"""Dota Rank Tracker: a small always-on-top widget with your medal and last
5 ranked results. Click it for the full stats window. Ctrl+Shift+R hides
and shows the widget; Ctrl+Shift+M logs your MMR so each game gets its +/-.
It can also read your MMR off Dota's Profile → Stats page by itself (Windows OCR).
Data comes from OpenDota (needs Expose Public Match Data on in Dota)."""

import asyncio
import ctypes
import json
import os
import re
import sys
import threading
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QColor, QCursor, QDesktopServices, QFont, QFontMetrics, QIcon,
                           QImage, QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QInputDialog, QMenu, QSpinBox,
                               QSystemTrayIcon, QWidget)

API = "https://api.opendota.com/api"
ICONS = "https://www.opendota.com/assets/images/dota2/rank_icons"
RANKS = ["", "Herald", "Guardian", "Crusader", "Archon", "Legend", "Ancient", "Divine", "Immortal"]
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "DotaRankTracker"
SETTINGS = DATA_DIR / "settings.json"
# ponytail: fixed 3 min poll = 3 calls (refresh + 2 reads) -> ~60/hour, fine for a gaming
# session under OpenDota's 3000/day free limit; back off when idle if people run it 24/7
POLL_MS = 3 * 60 * 1000
RETYPE_MS = 10 * 60 * 1000  # no ranked game ends this fast, so a re-log this soon is a typo fix
HOTKEYS = {1: 0x52, 2: 0x4D}  # id -> key: Ctrl+Shift+R hide/show, Ctrl+Shift+M log MMR
READ_MS = 4000  # how often the MMR area is read while Dota is in front
MAX_JUMP = 300  # an auto-read further than this from the last log is a misread (~10 games)

try:  # Windows' own OCR, no extra install
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter
except ImportError:
    OcrEngine = None

# menu and dialogs in the widget's colours instead of the plain Windows look
STYLE = """
QMenu { background: #171b22; border: 1px solid #2c333e; border-radius: 10px; padding: 6px; color: #e6e8eb; font: 10pt "Segoe UI"; }
QMenu::item { padding: 7px 28px 7px 14px; border-radius: 6px; }
QMenu::item:selected { background: #2a303b; }
QMenu::separator { height: 1px; background: #2c333e; margin: 5px 8px; }
QDialog { background: #171b22; }
QDialog QLabel { color: #8b93a1; font: 10pt "Segoe UI"; }
QLineEdit, QSpinBox { background: #0b0d11; color: #e6e8eb; border: 1px solid #2c333e; border-radius: 8px;
                      padding: 8px 10px; font: 11pt "Segoe UI"; selection-background-color: #c23b2a; selection-color: #ffffff; }
QSpinBox { font: 600 20pt "Segoe UI"; }
QLineEdit:focus, QSpinBox:focus { border-color: #d9a441; }
QPushButton { background: transparent; color: #e6e8eb; border: 1px solid #2c333e; border-radius: 8px;
              padding: 7px 16px; font: 600 10pt "Segoe UI"; min-width: 64px; }
QPushButton:hover { border-color: #4a5260; }
QPushButton:default { background: #c23b2a; border-color: #c23b2a; }
QToolTip { background: #171b22; color: #e6e8eb; border: 1px solid #2c333e; padding: 6px; }
"""

WIN, LOSS, BG, LINE, TEXT, DIM, GOLD = (QColor("#4cc26b"), QColor("#e0533f"), QColor(14, 17, 22, 225),
                                        QColor("#2c333e"), QColor("#e6e8eb"), QColor("#8b93a1"), QColor("#d9a441"))


# --- settings / ids -------------------------------------------------------

def load_settings():
    try:
        s = json.loads(SETTINGS.read_text("utf-8"))
        if isinstance(s, dict):
            return s
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        pass
    # unreadable: keep a copy instead of silently overwriting the MMR log
    try:
        os.replace(SETTINGS, SETTINGS.with_name("settings.bad.json"))
    except OSError:
        pass
    return {}


def save_settings(s):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(s), "utf-8")
    os.replace(tmp, SETTINGS)  # atomic: a crash mid-save never leaves a half-written file


def parse_id(text):
    """Friend id, steamid64, or a Steam/OpenDota/Dotabuff/Stratz link -> friend id."""
    text = text.strip()
    big = re.search(r"7656\d{13}", text)
    if big:
        return str(int(big.group()) - 76561197960265728)
    m = re.search(r"(\d{3,10})/?$", text) or re.search(r"players/(\d+)", text)
    return m.group(1) if m else None


def rank_name(tier):
    if not tier:
        return "Uncalibrated"
    return "Immortal" if tier >= 80 else f"{RANKS[tier // 10]} {tier % 10}"


def won(m):
    return (m["player_slot"] < 128) == m["radiant_win"]


def mmr_deltas(log, matches):
    """Valve doesn't share per-game MMR, so we pair the MMR you log with the one
    ranked game that ended since your previous log. Returns ({match_id: delta},
    pending, pending_t) where pending is the latest delta OpenDota has no game for
    yet. Same rules as mmrInfo() in index.html."""
    by_match, pending, pending_t = {}, None, None
    for prev, cur in zip(log, log[1:]):
        games = [m for m in matches if prev["t"] < (m["start_time"] + m["duration"]) * 1000 < cur["t"]]
        if len(games) == 1:
            by_match[games[0]["match_id"]] = cur["mmr"] - prev["mmr"]
        elif not games and cur is log[-1] and cur["mmr"] != prev["mmr"]:
            pending, pending_t = cur["mmr"] - prev["mmr"], cur["t"]
    return by_match, pending, pending_t


def add_log(log, mmr, now_ms):
    """Append an MMR entry; a re-log within RETYPE_MS replaces the last one (typo fix)."""
    if log and now_ms - log[-1]["t"] < RETYPE_MS:
        log = log[:-1]
    return log + [{"t": now_ms, "mmr": mmr}]


def number(text):
    """The one number in OCR text ("4,002" or "MMR 4002" -> 4002). None if there is no
    number or several (in-game K/D/A "5/3/10", clock "12:34")."""
    found = re.findall(r"\d+(?:[,. ]\d{3})*", text)
    digits = re.sub(r"\D", "", found[0]) if len(found) == 1 else ""
    return int(digits) if 0 < len(digits) <= 5 else None  # MMR never has 6 digits


def accept(log, value, prev):
    """Log an auto-read only if two reads in a row agree, it changed, and it's a believable jump."""
    if value is None or value != prev or not log:
        return False
    return value != log[-1]["mmr"] and abs(value - log[-1]["mmr"]) <= MAX_JUMP


def session_summary(start, matches, log):
    """Ranked games that started at/after start (ms): (wins, losses, mmr total, games with a known +/-).
    Same rules as sessionSummary() in index.html."""
    by_match, pending, pending_t = mmr_deltas(log, matches)
    games = [m for m in matches if m["start_time"] * 1000 >= start]
    wins, n = sum(map(won, games)), len(games)
    known = [by_match[m["match_id"]] for m in games if m["match_id"] in by_match]
    if pending and pending_t >= start:  # a game you logged that OpenDota hasn't got yet
        n, wins = n + 1, wins + (pending > 0)
        known.append(pending)
    return wins, n - wins, sum(known), len(known)


# --- Windows bits (signatures declared; undeclared ctypes calls fail silently on 64-bit) ---

if os.name == "nt":
    U = ctypes.WinDLL("user32", use_last_error=True)
    U.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
    U.RegisterHotKey.restype = wintypes.BOOL
    U.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT)
    U.SetWindowPos.restype = wintypes.BOOL
    U.GetForegroundWindow.restype = wintypes.HWND
    U.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    U.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
    K = ctypes.WinDLL("kernel32", use_last_error=True)
    K.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    K.CreateMutexW.restype = wintypes.HANDLE
    try:
        DWM = ctypes.WinDLL("dwmapi")
        DWM.DwmSetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD, wintypes.LPCVOID, wintypes.DWORD)
    except OSError:
        DWM = None


def pin_topmost(hwnd):
    if os.name == "nt":  # HWND_TOPMOST, NOMOVE|NOSIZE|NOACTIVATE: stay on top without stealing focus
        U.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0010)


def dark_title_bar(hwnd):
    if os.name == "nt" and DWM:
        on = ctypes.c_int(1)
        DWM.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))


def hide_from_capture(hwnd):
    if os.name == "nt":  # WDA_EXCLUDEFROMCAPTURE: our MMR reads see Dota, not the widget on top of it
        U.SetWindowDisplayAffinity(hwnd, 0x11)


def dota_in_front():
    if os.name != "nt":
        return False
    buf = ctypes.create_unicode_buffer(64)
    U.GetWindowTextW(U.GetForegroundWindow(), buf, 64)
    return buf.value == "Dota 2"


def single_instance():
    if os.name != "nt":
        return True
    K.CreateMutexW(None, False, "Local\\DotaRankTracker")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


# --- OpenDota fetch, off the UI thread -------------------------------------

def get(path, method="GET"):
    req = urllib.request.Request(API + path, method=method, headers={"User-Agent": "DotaRankTracker"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"null")


_icons = {}  # medal pngs by url; only one fetch runs at a time


def icon(url):
    if url not in _icons:
        req = urllib.request.Request(url, headers={"User-Agent": "DotaRankTracker"})
        with urllib.request.urlopen(req, timeout=20) as r:
            _icons[url] = r.read()
    return _icons[url]


class Fetcher(QObject):
    done = Signal(object)

    def run(self, account, ask_refresh):
        threading.Thread(target=self._work, args=(account, ask_refresh), daemon=True).start()

    def _work(self, account, ask_refresh):
        try:
            if ask_refresh:  # tell OpenDota to pull our newest games from Steam
                get(f"/players/{account}/refresh", "POST")
                time.sleep(20)
            player = get(f"/players/{account}")
            matches = get(f"/players/{account}/matches?lobby_type=7&limit=40"
                          "&project=start_time&project=duration&project=hero_id")
            tier = player.get("rank_tier")
            icons = []
            if tier:
                icons.append(icon(f"{ICONS}/rank_icon_{tier // 10}.png"))
                if tier % 10:
                    icons.append(icon(f"{ICONS}/rank_star_{tier % 10}.png"))
            self.done.emit({"account": account, "player": player, "matches": matches, "icons": icons})
        except Exception as e:  # network down, OpenDota 5xx, etc. - keep the last good data
            self.done.emit({"account": account, "error": str(e)})


# --- reading your MMR off Dota's screen -------------------------------------

def grab(qt, area):
    """Screenshot of a global screen box [x, y, w, h] as a QImage."""
    x, y, w, h = area
    s = qt.screenAt(QPoint(x + w // 2, y + h // 2)) or qt.primaryScreen()
    g = s.geometry()
    return s.grabWindow(0, x - g.x(), y - g.y(), w, h).toImage()


async def _recognize(engine, bmp):
    return await engine.recognize_async(bmp)


def stretched(img):
    """Grey, darkest pixel -> black, brightest -> white."""
    g = img.convertToFormat(QImage.Format_Grayscale8)
    px = bytes(g.constBits())
    lo, hi = min(px), max(px)
    px = px.translate(bytes(max(0, min(255, (v - lo) * 255 // max(hi - lo, 1))) for v in range(256)))
    return QImage(px, g.width(), g.height(), g.bytesPerLine(), QImage.Format_Grayscale8).copy()


def ocr(engine, img):
    k = max(1, min(3, 1500 // max(img.width(), img.height(), 1)))  # small UI text reads better bigger
    img = img.scaled(img.width() * k, img.height() * k, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    img = img.convertToFormat(QImage.Format_ARGB32)  # = BGRA bytes on little-endian
    w = DataWriter()
    w.write_bytes(bytes(img.constBits()))
    bmp = SoftwareBitmap.create_copy_from_buffer(w.detach_buffer(), BitmapPixelFormat.BGRA8,
                                                 img.width(), img.height())
    return number(asyncio.run(_recognize(engine, bmp)).text)


def read_number(img):
    """The MMR in a screenshot via Windows OCR, or None. ~5 ms; safe off the UI thread."""
    try:
        engine = OcrEngine and OcrEngine.try_create_from_user_profile_languages()
        if not engine:
            return None
        # coloured text on dark can fail as-is but reads once stretched (and the other way round)
        v = ocr(engine, img)
        return v if v is not None else ocr(engine, stretched(img))
    except Exception:  # OCR language missing, odd image, ... - just no reading this time
        return None


class Reader(QObject):
    done = Signal(object)

    def run(self, img):
        threading.Thread(target=lambda: self.done.emit(read_number(img)), daemon=True).start()


class AreaPicker(QWidget):
    """A frozen screenshot of the screen under the mouse; drag a box around your MMR."""

    def __init__(self, qt, on_pick):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        screen = qt.screenAt(QCursor.pos()) or qt.primaryScreen()
        self.shot = screen.grabWindow(0)
        self.on_pick, self.start, self.box = on_pick, None, QRect()
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CrossCursor)

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.shot)
        dim, hole = QPainterPath(), QPainterPath()
        dim.addRect(QRectF(self.rect()))
        hole.addRect(QRectF(self.box))
        p.fillPath(dim.subtracted(hole), QColor(0, 0, 0, 150))
        p.setPen(QPen(GOLD, 2))
        p.drawRect(self.box)
        p.setFont(QFont("Segoe UI", 12, QFont.DemiBold))
        hint = "Drag a box around your MMR number  (Dota → Profile → Stats, top right)\nEsc to type it instead"
        r = p.fontMetrics().boundingRect(self.rect(), Qt.AlignHCenter, hint).adjusted(-18, -10, 18, 10)
        r.moveTop(40)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor("#171b22"))
        p.setPen(QPen(LINE, 1))
        p.drawRoundedRect(r, 10, 10)
        p.setPen(TEXT)
        p.drawText(r, Qt.AlignCenter, hint)

    def mousePressEvent(self, e):
        self.start = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self.start:
            self.box = QRect(self.start, e.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, _):
        if self.box.width() >= 8 and self.box.height() >= 6:
            self.close()
            g = self.geometry()
            self.on_pick([self.box.x() + g.x(), self.box.y() + g.y(), self.box.width(), self.box.height()])

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
            self.on_pick(None)


# --- the small widget ------------------------------------------------------

class Overlay(QWidget):
    W, H = 248, 78

    def __init__(self, app):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.app = app
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.PointingHandCursor)
        self.medal = None
        self.state = None  # last good data
        self.error = None
        self.new_match = None  # newest box gets a highlight ring until clicked
        self._press = None

    # click opens stats, drag moves
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = (e.globalPosition().toPoint(), self.pos(), False)
        elif e.button() == Qt.RightButton:
            self.app.menu.popup(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if not self._press:
            return
        start, origin, dragging = self._press
        delta = e.globalPosition().toPoint() - start
        if dragging or delta.manhattanLength() > 4:
            self._press = (start, origin, True)
            self.move(origin + delta)

    def mouseReleaseEvent(self, e):
        if self._press and e.button() == Qt.LeftButton:
            if self._press[2]:
                self.app.settings["pos"] = [self.x(), self.y()]
                save_settings(self.app.settings)
            else:
                self.new_match = None
                self.update()
                self.app.open_stats()
        self._press = None

    def nativeEvent(self, event_type, message):
        if os.name == "nt" and event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam in HOTKEYS:  # WM_HOTKEY
                # run after this native handler returns; a dialog's event loop must not nest in here
                QTimer.singleShot(0, self.app.toggle_overlay if msg.wParam == 1 else self.app.log_mmr)
                return True, 0
        return super().nativeEvent(event_type, message)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        card = QRectF(0.5, 0.5, self.W - 1, self.H - 1)
        p.setPen(QPen(LINE, 1))
        p.setBrush(BG)
        p.drawRoundedRect(card, 12, 12)

        # medal; leaderboard spot sits on it like in Dota
        lb = self.state and self.state["player"].get("leaderboard_rank")
        if self.medal:
            p.drawPixmap(QRectF(8, 5, 64, 64).toRect(), self.medal)
            if lb:
                p.setFont(self._font(8, QFont.Bold))
                for dx, dy, c in ((1, 1, QColor(0, 0, 0, 220)), (0, 0, GOLD)):  # shadow, then text
                    p.setPen(c)
                    p.drawText(QRectF(8 + dx, 50 + dy, 64, 14), Qt.AlignCenter, f"#{lb}")
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#1d222b"))
            p.drawEllipse(QRectF(14, 11, 52, 52))
            if self.state:  # loaded but no medal = uncalibrated
                p.setPen(DIM)
                p.setFont(self._font(18, QFont.Bold))
                p.drawText(QRectF(14, 11, 52, 52), Qt.AlignCenter, "?")

        x, right = 80, self.W - 12
        if not self.state:
            p.setPen(LOSS if self.error else DIM)
            p.setFont(self._font(9))
            p.drawText(QRectF(x, 0, right - x, self.H), Qt.AlignVCenter | Qt.TextWordWrap,
                       "Can't reach OpenDota" if self.error else "Loading rank…")
            return

        player, matches = self.state["player"], self.state["matches"]
        log = self.app.mmr_log()
        # top row: medal name on the left, session or today on the right
        p.setFont(self._font(11, QFont.Bold))
        p.setPen(TEXT)
        p.drawText(QRectF(x, 9, right - x, 20), Qt.AlignLeft | Qt.AlignVCenter, rank_name(player.get("rank_tier")))
        start = self.app.session_start()
        if start:
            w, l, mmr, known = session_summary(start, matches, log)
            parts = [(f"session {w}–{l}", DIM)] + ([(f" {mmr:+d}", WIN if mmr >= 0 else LOSS)] if known else [])
        else:
            midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
            today = [m for m in matches if m["start_time"] >= midnight]
            w = sum(map(won, today))
            parts = [(f"today {w}–{len(today) - w}", DIM)] if today else []
        tx = right
        for text, color in reversed(parts):  # right-aligned, drawn right to left
            p.setFont(self._font(8, QFont.DemiBold if color != DIM else QFont.Normal))
            tx -= QFontMetrics(p.font()).horizontalAdvance(text)
            p.setPen(color)
            p.drawText(QRectF(tx, 9, right - tx, 20), Qt.AlignLeft | Qt.AlignVCenter, text)

        # last 5: newest on the left. A game you logged MMR for but OpenDota
        # hasn't fetched yet goes first, drawn dashed.
        by_match, pending, _ = mmr_deltas(log, matches)
        boxes = ([{"pending": pending}] if pending else []) + matches
        size, gap, y = 24, 5, 34
        for i in range(5):
            r = QRectF(x + i * (size + gap), y, size, size)
            if i < len(boxes) and "pending" in boxes[i]:
                c = WIN if pending > 0 else LOSS
                p.setPen(QPen(c, 1.2, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
                p.setFont(self._font(9, QFont.Bold))
                p.drawText(r, Qt.AlignCenter, "W" if pending > 0 else "L")
                self._delta(p, r, pending)
            elif i < len(boxes):
                m = boxes[i]
                c = WIN if won(m) else LOSS
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(c.red(), c.green(), c.blue(), 50))
                p.drawRoundedRect(r, 5, 5)
                p.setPen(QPen(c, 1.2))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
                p.setPen(c)
                p.setFont(self._font(9, QFont.Bold))
                p.drawText(r, Qt.AlignCenter, "W" if won(m) else "L")
                self._delta(p, r, by_match.get(m["match_id"]))
                if m["match_id"] == self.new_match:
                    p.setPen(QPen(TEXT, 1.5))
                    p.drawRoundedRect(r.adjusted(-2.5, -2.5, 2.5, 2.5), 7, 7)
            else:  # not played yet
                p.setPen(QPen(QColor("#4a5260"), 1, Qt.DashLine))
                p.setBrush(QColor(255, 255, 255, 8))
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)

    def _delta(self, p, r, d):
        if d is None:
            return
        p.setPen(WIN if d >= 0 else LOSS)
        p.setFont(self._font(8, QFont.DemiBold))
        p.drawText(QRectF(r.x() - 6, r.bottom() + 1, r.width() + 12, 13), Qt.AlignCenter, f"{d:+d}")

    @staticmethod
    def _font(pt, weight=QFont.Normal):
        f = QFont("Segoe UI", pt)
        f.setWeight(weight)
        return f

    def set_data(self, data):
        if "error" in data:
            self.error = data["error"]
        else:
            old = self.state and self.state["matches"][:1]
            if old and data["matches"] and data["matches"][0]["match_id"] != old[0]["match_id"]:
                self.new_match = data["matches"][0]["match_id"]
            self.state, self.error = data, None
            self.medal = compose_medal(data["icons"])
            lb = data["player"].get("leaderboard_rank")
            hint = "" if data["matches"] else "\nNo ranked games found - is Expose Public Match Data on?"
            self.setToolTip(f"{rank_name(data['player'].get('rank_tier'))}{f' #{lb}' if lb else ''}\n"
                            "Click: full stats · Drag: move · Ctrl+Shift+R: hide · Ctrl+Shift+M: log MMR" + hint)
        self.update()


def compose_medal(layers):
    if not layers:
        return None
    out = QPixmap(128, 128)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    for raw in layers:
        pm = QPixmap()
        pm.loadFromData(raw)
        p.drawPixmap(out.rect(), pm)
    p.end()
    return out


# --- the big window: the dashboard page in an embedded browser -----------

class Page(QWebEnginePage):
    """Our own page talks back by logging '@app {json}' (e.g. session start/end)."""

    def __init__(self, profile, parent, on_message):
        super().__init__(profile, parent)
        self.on_message = on_message
        # links open in the normal browser instead of a window that never appears
        self.newWindowRequested.connect(lambda req: QDesktopServices.openUrl(req.requestedUrl()))

    def javaScriptConsoleMessage(self, level, message, line, source):
        if message.startswith("@app ") and source.startswith("file:"):
            try:
                self.on_message(json.loads(message[5:]))
            except ValueError:
                pass


class StatsWindow(QWebEngineView):
    def __init__(self, on_message):
        super().__init__()
        profile = QWebEngineProfile("DotaRankTracker", self)  # named = localStorage survives restarts
        profile.setPersistentStoragePath(str(DATA_DIR / "web"))
        page = Page(profile, self, on_message)
        page.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        page.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, False)
        self.setPage(page)
        self.setWindowTitle("Dota Rank Tracker")
        self.resize(1240, 880)
        self.page().setBackgroundColor(QColor("#0e1116"))
        self.account = None
        self.state = None  # (account, mmr log, session start) to mirror into the page
        self.loadFinished.connect(lambda ok: ok and self.state and self.push(*self.state))

    def push(self, account, log, session):
        """The widget owns the MMR log and session; mirror them into the page's storage."""
        self.state = (account, log, session)
        if account == self.account:
            self.page().runJavaScript(f"store.set('dota.mmr.{account}', {json.dumps(log)});"
                                      f"store.set('dota.session.{account}', {json.dumps(session)});"
                                      "if (data) render();")

    def open(self, account):
        if account != self.account:
            self.account = account
            url = QUrl.fromLocalFile(str(HERE / "index.html"))
            url.setQuery(f"id={account}&app=1")
            self.load(url)
        else:
            self.page().runJavaScript("load()")
        self.showNormal()
        dark_title_bar(int(self.winId()))
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):  # closing only hides; the widget keeps running
        e.ignore()
        self.hide()


# --- app glue --------------------------------------------------------------

class App:
    def __init__(self, qt):
        self.qt = qt
        self.settings = load_settings()
        self.overlay = Overlay(self)
        self.stats = None
        self.fetcher = Fetcher()
        self.fetcher.done.connect(self.on_data)
        self.busy = False
        self.asking = False
        self.reader = Reader()
        self.reader.done.connect(self.on_read)
        self.reading, self.last_read = False, None

        self.menu = QMenu()  # rounded corners need a see-through, frameless popup
        self.menu.setAttribute(Qt.WA_TranslucentBackground)
        self.menu.setWindowFlags(self.menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.toggle_act = QAction("Hide widget\tCtrl+Shift+R", self.menu, triggered=self.toggle_overlay)
        self.session_act = QAction("Start session", self.menu, triggered=self.toggle_session)
        self.menu.addAction(self.toggle_act)
        self.menu.addAction(QAction("Open full stats", self.menu, triggered=self.open_stats))
        self.menu.addAction(QAction("Log MMR…\tCtrl+Shift+M", self.menu, triggered=self.log_mmr))
        if OcrEngine:
            self.menu.addAction(QAction("Set MMR screen area…", self.menu, triggered=self.pick_area))
        self.menu.addAction(self.session_act)
        self.menu.addAction(QAction("Check for new games now", self.menu, triggered=lambda: self.poll(True)))
        self.menu.addAction(QAction("Change account…", self.menu, triggered=self.ask_account))
        self.menu.addSeparator()
        self.menu.addAction(QAction("Quit", self.menu, triggered=self.quit))
        self.menu.aboutToShow.connect(
            lambda: self.session_act.setText("End session" if self.session_start() else "Start session"))
        self.tray = QSystemTrayIcon(app_icon(), qt)
        self.tray.setToolTip("Dota Rank Tracker")
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(lambda r: r == QSystemTrayIcon.Trigger and self.toggle_overlay())
        self.tray.show()

        if not self.settings.get("account") and not self.ask_account():
            sys.exit(0)
        self.place()
        self.overlay.show()
        hwnd = int(self.overlay.winId())
        hide_from_capture(hwnd)
        taken = [chr(vk) for hid, vk in HOTKEYS.items()
                 if os.name == "nt" and not U.RegisterHotKey(hwnd, hid, 0x0002 | 0x0004 | 0x4000, vk)]
        if taken:
            self.tray.showMessage("Dota Rank Tracker",
                                  f"Ctrl+Shift+{'/'.join(taken)} is taken by another app; use the tray menu instead.")
        if self.settings.get("hidden"):
            self.overlay.hide()
            self.toggle_act.setText("Show widget\tCtrl+Shift+R")

        self.timer = QTimer(timeout=lambda: self.poll(True))
        self.timer.start(POLL_MS)
        # Dota borderless can push itself above us; re-assert topmost now and then
        self.top_timer = QTimer(timeout=lambda: self.overlay.isVisible() and pin_topmost(int(self.overlay.winId())))
        self.top_timer.start(5000)
        self.read_timer = QTimer(timeout=self.auto_read)
        self.read_timer.start(READ_MS)
        self.poll(False)

    def place(self):
        geo = self.qt.primaryScreen().availableGeometry()
        x, y = self.settings.get("pos") or (geo.right() - Overlay.W - 24, geo.top() + 24)
        # keep it on a visible screen if monitors changed
        if not any(s.availableGeometry().contains(QPoint(x + 20, y + 20)) for s in self.qt.screens()):
            x, y = geo.right() - Overlay.W - 24, geo.top() + 24
        self.overlay.move(x, y)

    def ask_account(self):
        d = dialog("Dota Rank Tracker", "Your Dota friend ID, or a Steam / OpenDota / Dotabuff link.\n"
                                        "Turn on Expose Public Match Data in Dota's settings first.")
        d.setTextValue(self.settings.get("account", ""))
        account = d.exec() and parse_id(d.textValue())
        if not account:
            return False
        self.settings["account"] = account
        save_settings(self.settings)
        self.overlay.state = None
        self.overlay.medal = None
        self.overlay.update()
        if self.stats and self.stats.isVisible():
            self.open_stats()
        self.poll(False)  # if a fetch for the old account is running, on_data re-polls
        return True

    def poll(self, ask_refresh):
        if self.busy or not self.settings.get("account"):
            return
        self.busy = True
        self.fetcher.run(self.settings["account"], ask_refresh)

    def on_data(self, data):
        self.busy = False
        if data["account"] != self.settings.get("account"):  # account changed mid-fetch
            self.poll(False)
            return
        self.overlay.set_data(data)
        if self.overlay.medal:
            self.tray.setIcon(QIcon(self.overlay.medal))

    # MMR log and session are per account
    def mmr_log(self):
        return self.settings.get("mmr", {}).get(self.settings.get("account"), [])

    def session_start(self):
        return self.settings.get("session", {}).get(self.settings.get("account"))

    def _changed(self):
        save_settings(self.settings)
        self.overlay.update()
        if self.stats:
            self.stats.push(self.settings["account"], self.mmr_log(), self.session_start())

    def save_mmr(self, value):
        self.settings.setdefault("mmr", {})[self.settings["account"]] = add_log(
            self.mmr_log(), value, int(time.time() * 1000))
        self._changed()

    def auto_read(self):
        area = self.settings.get("mmr_area")
        if area and OcrEngine and not self.reading and not self.asking and dota_in_front():
            self.reading = True
            self.reader.run(grab(self.qt, area))

    def on_read(self, value):
        self.reading = False
        if accept(self.mmr_log(), value, self.last_read):
            self.save_mmr(value)
        self.last_read = value

    def log_mmr(self):
        """Hotkey/menu: read the MMR area and confirm it, or pick the area first, or just type."""
        if self.asking:
            return
        area = self.settings.get("mmr_area")
        if not area and OcrEngine:
            self.pick_area()
        else:
            self.ask_mmr(area and read_number(grab(self.qt, area)))

    def pick_area(self):
        if self.asking:
            return
        self.asking = True
        self.picker = AreaPicker(self.qt, self.on_pick)
        self.picker.show()
        self.picker.activateWindow()

    def on_pick(self, area):
        self.asking = False
        if area:
            self.settings["mmr_area"] = area
            save_settings(self.settings)
        QTimer.singleShot(0, lambda: self.ask_mmr(area and read_number(grab(self.qt, area))))

    def ask_mmr(self, read):
        if self.asking:
            return
        self.asking = True
        try:
            log = self.mmr_log()
            d = dialog("Log MMR", "Read from your screen - press Enter if it's right" if read is not None
                       else "Your MMR now  (Dota → Profile → Stats)")
            d.setInputMode(QInputDialog.IntInput)
            d.setIntRange(0, 20000)
            d.setIntValue(read if read is not None else log[-1]["mmr"] if log else 3000)
            if spin := d.findChild(QSpinBox):
                spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
            ok = d.exec()
            value = d.intValue()
        finally:
            self.asking = False
        if ok:
            self.save_mmr(value)
            self.last_read = value

    def set_session(self, start):
        self.settings.setdefault("session", {})[self.settings["account"]] = start
        self._changed()

    def toggle_session(self):
        self.set_session(None if self.session_start() else int(time.time() * 1000))

    def on_page_message(self, msg):
        if "session" in msg and (msg["session"] is None or isinstance(msg["session"], (int, float))):
            self.set_session(msg["session"] and int(msg["session"]))

    def toggle_overlay(self):
        show = not self.overlay.isVisible()
        self.overlay.setVisible(show)
        if show:
            pin_topmost(int(self.overlay.winId()))
        self.settings["hidden"] = not show
        save_settings(self.settings)
        self.toggle_act.setText(("Hide" if show else "Show") + " widget\tCtrl+Shift+R")

    def open_stats(self):
        if not self.stats:
            self.stats = StatsWindow(self.on_page_message)
            self.stats.setWindowIcon(app_icon())
        account = self.settings["account"]
        self.stats.open(account)
        self.stats.push(account, self.mmr_log(), self.session_start())

    def quit(self):
        self.tray.hide()
        self.qt.exit(0)  # exit, not quit: the stats window ignores close


def dialog(title, label):
    d = QInputDialog(None, Qt.WindowStaysOnTopHint)
    d.setWindowTitle(title)
    d.setLabelText(label)
    d.setMinimumWidth(340)
    dark_title_bar(int(d.winId()))
    return d


def app_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(4, 4, 56, 56), 14, 14)
    p.fillPath(path, QColor("#c23b2a"))
    p.setPen(QColor("white"))
    p.setFont(QFont("Segoe UI", 26, QFont.Bold))
    p.drawText(pm.rect(), Qt.AlignCenter, "R")
    p.end()
    return QIcon(pm)


def main():
    if not single_instance():
        return
    qt = QApplication(sys.argv)
    qt.setQuitOnLastWindowClosed(False)
    qt.setApplicationName("Dota Rank Tracker")
    qt.setStyleSheet(STYLE)
    App(qt)
    sys.exit(qt.exec())


if __name__ == "__main__":
    main()
