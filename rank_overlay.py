"""Dota Rank Tracker: a small always-on-top widget with your MMR and your last 5
games as W/L boxes with the +/- of each. Ctrl+Shift+E reads your MMR off Dota's
screen (PaddleOCR's text model); it is also read by itself whenever your Profile page is open.
Every MMR change is one game. Ctrl+Shift+R hides and shows the widget; click it
for the history, where you fix a misread or mark where a session started."""

import ctypes
import json
import os
import re
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QCursor, QFont, QFontMetrics, QIcon, QImage,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout, QHeaderView, QLabel, QMenu,
                               QPushButton, QStyleFactory, QSystemTrayIcon, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "DotaRankTracker"
SETTINGS = DATA_DIR / "settings.json"
MEDAL = DATA_DIR / "medal.png"  # snapshot of your medal from Dota's screen
START_SPOT = DATA_DIR / "start_spot.png"  # what the first History click hits (the Dota logo) in the menus
RETYPE_MS = 10 * 60 * 1000  # no ranked game ends this fast, so a re-capture this soon replaces the last one
HOTKEYS = {1: 0x52, 2: 0x45, 3: 0x44}  # id -> key: Ctrl+Shift+R hide/show, E capture MMR, D go to History
# s between replayed clicks for Dota's menu to switch pages: measured logo -> profile -> History,
# 0.4 worked 15/15 (~1.1 s to the MMR), 0.35 failed 2/12, 0.2 and under always failed
CLICK_GAP = 0.4
READ_MS = 4000  # how often the MMR box is looked at while Dota is in front
MAX_JUMP = 300  # an auto-read further than this from the last log is a misread (~10 games)

# menu and history window in the widget's colours instead of the plain Windows look
STYLE = """
QMenu { background: #171b22; border: 1px solid #2c333e; border-radius: 10px; padding: 6px; color: #e6e8eb; font: 10pt "Segoe UI"; }
QMenu::item { padding: 7px 28px 7px 14px; border-radius: 6px; }
QMenu::item:selected { background: #2a303b; }
QMenu::separator { height: 1px; background: #2c333e; margin: 5px 8px; }
QToolTip { background: #171b22; color: #e6e8eb; border: 1px solid #2c333e; padding: 6px; }
#history { background: #0e1116; }
#history QLabel { color: #e6e8eb; font: 10pt "Segoe UI"; }
#history QLabel#dim { color: #8b93a1; font: 9pt "Segoe UI"; }
#history QLabel#big { font: 700 26pt "Segoe UI"; }
#history QPushButton { background: #c23b2a; color: #ffffff; border: 0; border-radius: 8px; padding: 8px 16px;
                       font: 600 10pt "Segoe UI"; }
#history QPushButton:hover { background: #d4493a; }
QTableWidget { background: #0e1116; color: #e6e8eb; border: 1px solid #2c333e; border-radius: 8px;
               gridline-color: transparent; font: 10pt "Segoe UI"; selection-background-color: #2a303b; }
QTableWidget QLineEdit { background: #0b0d11; color: #e6e8eb; border: 1px solid #d9a441; padding: 2px 6px; }
QHeaderView::section { background: #0e1116; color: #8b93a1; border: 0; border-bottom: 1px solid #2c333e;
                       padding: 6px 4px; font: 9pt "Segoe UI"; }
"""

WIN, LOSS, BG, LINE, TEXT, DIM, GOLD = (QColor("#4cc26b"), QColor("#e0533f"), QColor(14, 17, 22, 225),
                                        QColor("#2c333e"), QColor("#e6e8eb"), QColor("#8b93a1"), QColor("#d9a441"))


# --- settings and the MMR log -----------------------------------------------

def load_settings():
    try:
        s = json.loads(SETTINGS.read_text("utf-8"))
        if isinstance(s, dict):
            return migrate(s)
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


def migrate(s):
    """v0.1 kept the log and session per OpenDota account; now there's just yours."""
    account = s.pop("account", None)
    s.pop("medal", None)  # was a rank number; the medal is a snapshot now
    if isinstance(s.get("mmr"), dict):
        s["mmr"] = s["mmr"].get(account) or next(iter(s["mmr"].values()), [])
    if isinstance(s.get("session"), dict):
        s["session"] = s["session"].get(account)
    return s


def save_settings(s):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(s), "utf-8")
    os.replace(tmp, SETTINGS)  # atomic: a crash mid-save never leaves a half-written file


def add_log(log, mmr, now_ms):
    """Append an MMR entry; a re-capture within RETYPE_MS replaces the last one (fixes a misread)."""
    if log and now_ms - log[-1]["t"] < RETYPE_MS:
        log = log[:-1]
    return log + [{"t": now_ms, "mmr": mmr}]


def games(log):
    """Every MMR change is a game: [(time, mmr after, +/-)], oldest first. Games you
    didn't capture in between end up as one combined change."""
    return [(cur["t"], cur["mmr"], cur["mmr"] - prev["mmr"])
            for prev, cur in zip(log, log[1:]) if cur["mmr"] != prev["mmr"]]


def summary(log, since):
    """(wins, losses, mmr total) of the games captured at/after `since` (ms)."""
    ds = [d for t, _, d in games(log) if t >= since]
    wins = sum(d > 0 for d in ds)
    return wins, len(ds) - wins, sum(ds)


def number(text):
    """The one number in OCR text ("4,002" or "MMR 4002" -> 4002). None if there is no
    number or several (in-game K/D/A "5/3/10", clock "12:34")."""
    found = re.findall(r"\d+(?:[,. ]\d{3})*", text)
    digits = re.sub(r"\D", "", found[0]) if len(found) == 1 else ""
    return int(digits) if 0 < len(digits) <= 5 else None  # MMR never has 6 digits


def accept(log, value):
    """Log an auto-read only if it changed and it's a believable jump. The first MMR
    always comes from a Ctrl+Shift+E capture, so an auto-read never starts the log."""
    if value is None or not log:
        return False
    return value != log[-1]["mmr"] and abs(value - log[-1]["mmr"]) <= MAX_JUMP


def same(a, b):
    """Two tiny greyscale thumbnails look alike (screen didn't change)."""
    return b is not None and max(abs(x - y) for x, y in zip(a, b)) <= 6


def looks_like(a, b):
    """Same picture, give or take an animated glow: the Dota logo spot across menu pages and
    hover differs by ~3-8 (mean per pixel), the same spot in a game by ~110."""
    return a.shape == b.shape and float(abs(a.astype("int16") - b.astype("int16")).mean()) < 30


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
    U.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
    U.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
    U.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    U.GetAsyncKeyState.restype = ctypes.c_short
    U.mouse_event.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t)
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


def spot(click):
    """The 48x48 box around a click."""
    return [click[0] - 24, click[1] - 24, 48, 48]


def replay(clicks, gap=CLICK_GAP):
    """Click each recorded spot in turn (menu navigation in Dota), then put the mouse back."""
    old = wintypes.POINT()
    U.GetCursorPos(ctypes.byref(old))
    for i, (x, y) in enumerate(clicks):
        if i:
            time.sleep(gap)
        U.SetCursorPos(x, y)
        time.sleep(0.02)
        U.mouse_event(0x0002, 0, 0, 0, 0)  # left down
        time.sleep(0.03)
        U.mouse_event(0x0004, 0, 0, 0, 0)  # left up
    U.SetCursorPos(old.x, old.y)


def single_instance():
    if os.name != "nt":
        return True
    K.CreateMutexW(None, False, "Local\\DotaRankTracker")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


# --- reading your MMR off Dota's screen -------------------------------------

def grab(qt, area):
    """Screenshot of a global screen box [x, y, w, h] as a QImage."""
    x, y, w, h = area
    s = qt.screenAt(QPoint(x + w // 2, y + h // 2)) or qt.primaryScreen()
    g = s.geometry()
    return s.grabWindow(0, x - g.x(), y - g.y(), w, h).toImage()


def thumb(img):
    return bytes(img.scaled(24, 12, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                 .convertToFormat(QImage.Format_Grayscale8).constBits())


# PaddleOCR's tiny text recognizer (Apache-2.0, via RapidOCR's ONNX export) run with onnxruntime.
# It read 1000/1000 MMRs drawn in Dota's own font and the real Profile box, ~2 ms each.
# We find the text lines ourselves instead of shipping a detector + OpenCV (~70 MB).
MODEL = HERE / "models" / "PP-OCRv6_rec_tiny.onnx"
_ocr, _ocr_lock = None, threading.Lock()


def ocr_engine():
    """(onnxruntime session, character list), loaded once."""
    global _ocr
    with _ocr_lock:
        if _ocr is None:
            import onnxruntime  # slow import (~1 s), so it's warmed up at start
            sess = onnxruntime.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])
            _ocr = sess, ["blank"] + sess.get_modelmeta().custom_metadata_map["character"].split("\n") + [" "]
        return _ocr


def pixels(img):
    import numpy as np
    img = img.convertToFormat(QImage.Format_RGB888)
    a = np.frombuffer(bytes(img.constBits()), np.uint8).reshape(img.height(), img.bytesPerLine())
    return a[:, :img.width() * 3].reshape(img.height(), img.width(), 3)


def text_lines(a):
    """Boxes (top, bottom, left, right) around each line of light text on Dota's dark UI."""
    g = a.mean(axis=2)
    ink = g > (g.min() + g.max()) / 2
    rows, out, y = ink.any(axis=1), [], 0
    while y < len(rows):
        if rows[y]:
            top = y
            while y < len(rows) and rows[y]:
                y += 1
            if y - top >= 4:  # thinner is a line or a speck, not text
                cols = ink[top:y].any(axis=0).nonzero()[0]
                out.append((top, y, cols[0], cols[-1] + 1))
        y += 1
    return out


def recognize(img, box):
    top, bottom, left, right = box
    pad = max(2, (bottom - top) // 4)
    x, y = max(0, left - pad), max(0, top - pad)
    line = img.copy(x, y, min(img.width(), right + pad) - x, min(img.height(), bottom + pad) - y)
    a = pixels(line.scaledToHeight(48, Qt.SmoothTransformation))[:, :, ::-1] / 127.5 - 1  # BGR in -1..1
    sess, chars = ocr_engine()
    idx = sess.run(None, {"x": a.transpose(2, 0, 1)[None].astype("float32")})[0][0].argmax(axis=1)
    return "".join(chars[i] for k, i in enumerate(idx) if i and (k == 0 or i != idx[k - 1]))  # CTC decode


def read_number(img):
    """The MMR in a screenshot, or None. Safe off the UI thread."""
    try:
        return number(" ".join(recognize(img, b) for b in text_lines(pixels(img))))
    except Exception:  # odd image, model missing, ... - just no reading this time
        return None


def cut_out(img):
    """The medal without Dota's near-black panel behind it: dark pixels fade to see-through."""
    import numpy as np
    a = pixels(img)
    alpha = np.clip((a.max(axis=2).astype(np.int16) - 22) * 6, 0, 255).astype(np.uint8)
    rgba = np.ascontiguousarray(np.dstack([a, alpha]))
    h, w = alpha.shape
    return QImage(rgba.data, w, h, w * 4, QImage.Format_RGBA8888).copy()


class Reader(QObject):
    done = Signal(object, object, str)  # MMR or None, medal snapshot taken with it, "auto" / "manual"

    def run(self, mmr_img, medal_img, mode):
        threading.Thread(target=lambda: self.done.emit(read_number(mmr_img), medal_img, mode), daemon=True).start()


class AreaPicker(QWidget):
    """A frozen screenshot of the screen under the mouse; drag a box around your MMR or medal."""

    def __init__(self, qt, on_pick, hint, shot=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        screen = qt.screenAt(QCursor.pos()) or qt.primaryScreen()
        self.shot = shot or screen.grabWindow(0)
        self.on_pick, self.hint, self.start, self.box = on_pick, hint, None, QRect()
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
        hint = self.hint
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
    W, H = 248, 90

    def __init__(self, app):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.app = app
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click: history · Drag: move · Ctrl+Shift+E: capture · Ctrl+Shift+D: History + capture · Ctrl+Shift+R: hide")
        self.note = None  # (text, colour) under the MMR for a moment after a capture
        self._press = None

    # click opens history, drag moves
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
                self.app.open_history()
        self._press = None

    def nativeEvent(self, event_type, message):
        if os.name == "nt" and event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam in HOTKEYS:  # WM_HOTKEY
                # run after this native handler returns, not nested inside it
                QTimer.singleShot(0, {1: self.app.toggle_overlay, 2: self.app.capture_mmr,
                                      3: self.app.history_shortcut}[msg.wParam])
                return True, 0
        return super().nativeEvent(event_type, message)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(LINE, 1))
        p.setBrush(BG)
        p.drawRoundedRect(QRectF(0.5, 0.5, self.W - 1, self.H - 1), 12, 12)

        # left: medal, and your MMR under it so a bad capture is easy to spot (fix it in the history)
        log = self.app.mmr_log()
        medal = self.app.medal
        if medal:
            m = medal.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap(8 + (64 - m.width()) // 2, 4 + (64 - m.height()) // 2, m)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#1d222b"))
            p.drawEllipse(QRectF(14, 10, 52, 52))
        text, color = self.note or ((f"{log[-1]['mmr']:,}", TEXT) if log else ("Ctrl+Shift+E", DIM))
        if self.note and self.note[1] == GOLD:  # just captured: the number itself, in gold
            text = f"{log[-1]['mmr']:,}" if log else text
        p.setFont(self._font(10 if log and text[0].isdigit() else 8, QFont.Bold))
        p.setPen(color)
        p.drawText(QRectF(0, 66, 80, 18), Qt.AlignCenter, text)

        # right, top: session (or today) W-L and MMR total
        x, right = 84, self.W - 12
        start = self.app.session_start()
        midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)) * 1000
        w, l, total = summary(log, start or midnight)
        parts = [(f"{'session' if start else 'today'} {w}–{l}", DIM)]
        if w + l:
            parts.append((f" {total:+d}", WIN if total >= 0 else LOSS))
        tx = x
        for text, color in parts:
            p.setFont(self._font(10, QFont.DemiBold if color != DIM else QFont.Normal))
            p.setPen(color)
            p.drawText(QRectF(tx, 12, right - tx, 20), Qt.AlignLeft | Qt.AlignVCenter, text)
            tx += QFontMetrics(p.font()).horizontalAdvance(text)

        # right: last 5 games, newest on the left
        last = games(log)[::-1][:5]
        size, gap, y = 24, 5, 40
        for i in range(5):
            r = QRectF(x + i * (size + gap), y, size, size)
            if i < len(last):
                d = last[i][2]
                c = WIN if d > 0 else LOSS
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(c.red(), c.green(), c.blue(), 50))
                p.drawRoundedRect(r, 5, 5)
                p.setPen(QPen(c, 1.2))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
                p.setFont(self._font(9, QFont.Bold))
                p.drawText(r, Qt.AlignCenter, "W" if d > 0 else "L")
                p.setFont(self._font(8, QFont.DemiBold))
                p.drawText(QRectF(r.x() - 6, r.bottom() + 1, r.width() + 12, 13), Qt.AlignCenter, f"{d:+d}")
            else:  # not played yet
                p.setPen(QPen(QColor("#4a5260"), 1, Qt.DashLine))
                p.setBrush(QColor(255, 255, 255, 8))
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)

    def flash(self, text, color):
        self.note = (text, color)
        self.update()
        QTimer.singleShot(3000, lambda: (setattr(self, "note", None), self.update()))

    @staticmethod
    def _font(pt, weight=QFont.Normal):
        f = QFont("Segoe UI", pt)
        f.setWeight(weight)
        return f


# --- the history window ------------------------------------------------------

class History(QWidget):
    """Every MMR you captured. Click a number to fix it, ⚑ to start the session at
    that game, × to delete it."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("history")
        self.setWindowTitle("Dota Rank Tracker")
        self.setWindowIcon(app_icon())
        self.resize(480, 600)
        self.big, self.session, self.hint = QLabel(), QLabel(), QLabel(
            "Click an MMR to fix a misread · ⚑ starts the session at that game · × deletes")
        self.big.setObjectName("big")
        self.hint.setObjectName("dim")
        self.hint.setWordWrap(True)
        self.session.setWordWrap(True)
        self.button = QPushButton(clicked=app.toggle_session)
        self.table = QTableWidget(0, 5)
        self.table.setStyle(QStyleFactory.create("Fusion"))  # whole-row tint, not Windows 11's per-cell boxes
        self.table.setHorizontalHeaderLabels(["When", "MMR", "+/-", "", ""])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.horizontalHeaderItem(0).setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.CurrentChanged | QAbstractItemView.DoubleClicked)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for c, wdt in ((1, 90), (2, 70), (3, 40), (4, 40)):
            h.setSectionResizeMode(c, QHeaderView.Fixed)
            self.table.setColumnWidth(c, wdt)
        self.table.itemChanged.connect(self.edited)
        self.table.cellClicked.connect(self.clicked)

        self.medal = QLabel()
        top = QHBoxLayout()
        top.addWidget(self.medal)
        top.addWidget(self.big)
        top.addStretch()
        top.addWidget(self.button, 0, Qt.AlignVCenter)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 18)
        lay.addLayout(top)
        lay.addWidget(self.session)
        lay.addWidget(self.hint)
        lay.addWidget(self.table)

    def refresh(self):
        log, start = self.app.mmr_log(), self.app.session_start()
        self.big.setText(f"{log[-1]['mmr']:,} MMR" if log else "No MMR yet")
        medal = self.app.medal
        self.medal.setVisible(bool(medal))
        if medal:
            self.medal.setPixmap(medal.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        if start:
            w, l, total = summary(log, start)
            self.session.setText(f"Session: {w}–{l}, <span style='color:{(WIN if total >= 0 else LOSS).name()}'>"
                                 f"{total:+d} MMR</span> · since {when(start)}")
        else:
            self.session.setText("<span style='color:#8b93a1'>No session running. Press ⚑ on a game to start "
                                 "one there, or Start session to start from now.</span>")
        self.button.setText("End session" if start else "Start session")

        t = self.table
        t.blockSignals(True)
        t.setRowCount(len(log))
        for row, i in enumerate(range(len(log) - 1, -1, -1)):  # newest first
            e = log[i]
            d = e["mmr"] - log[i - 1]["mmr"] if i else None
            in_session = start is not None and e["t"] >= start and d
            cells = [(when(e["t"]), DIM), (str(e["mmr"]), TEXT),
                     (f"{d:+d}" if d else "–", WIN if d and d > 0 else LOSS if d else DIM),
                     ("⚑", GOLD if e["t"] == start else DIM), ("×", DIM)]
            for c, (text, color) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(color)
                item.setData(Qt.UserRole, e["t"])
                item.setTextAlignment(Qt.AlignCenter if c else Qt.AlignLeft | Qt.AlignVCenter)
                if c != 1:
                    item.setFlags(Qt.ItemIsEnabled)
                if in_session:
                    item.setBackground(QColor(217, 164, 65, 22))
                if c == 3:
                    item.setToolTip("Start the session at this game" if d else "")
                    if not d:
                        item.setText("")
                if c == 4:
                    item.setToolTip("Delete this entry")
                t.setItem(row, c, item)
        t.blockSignals(False)

    def edited(self, item):
        v = item.text().strip().replace(",", "")
        log = self.app.mmr_log()
        if item.column() == 1 and v.isdigit() and int(v) < 20000:
            self.app.set_log([dict(e, mmr=int(v)) if e["t"] == item.data(Qt.UserRole) else e for e in log])
        else:
            QTimer.singleShot(0, self.refresh)  # not a number: put the old one back

    def clicked(self, row, col):
        item = self.table.item(row, col)
        t = item and item.data(Qt.UserRole)
        if col == 3 and item.text():
            self.app.set_session(t)
        elif col == 4:
            self.app.set_log([e for e in self.app.mmr_log() if e["t"] != t])


def when(ms):
    return time.strftime("%a %d %b, %H:%M", time.localtime(ms / 1000))


# --- app glue --------------------------------------------------------------

class App:
    def __init__(self, qt):
        self.qt = qt
        self.settings = load_settings()
        self.medal = QPixmap(str(MEDAL)) if MEDAL.exists() else None
        self.overlay = Overlay(self)
        self.history = None
        self.asking = False
        self.reader = Reader()
        self.reader.done.connect(self.on_read)
        self.reading = False  # an auto-read is running
        self.recording = None  # clicks while recording the way to History
        self.seen = self.read_seen = None  # thumbnails: last look at the MMR box, and last one OCR'd
        threading.Thread(target=ocr_engine, daemon=True).start()  # warm up so the first capture is quick

        self.menu = rounded_menu()
        self.toggle_act = QAction("Hide widget\tCtrl+Shift+R", self.menu, triggered=self.toggle_overlay)
        self.session_act = QAction("Start session", self.menu, triggered=self.toggle_session)
        self.menu.addAction(self.toggle_act)
        self.menu.addAction(QAction("Capture MMR\tCtrl+Shift+E", self.menu, triggered=self.capture_mmr))
        self.menu.addAction(QAction("Go to History + capture\tCtrl+Shift+D", self.menu,
                                    triggered=self.history_shortcut))
        self.menu.addAction(self.session_act)
        self.menu.addAction(QAction("History", self.menu, triggered=self.open_history))
        self.menu.addAction(QAction("Set MMR + medal screen areas…", self.menu, triggered=self.pick_area))
        self.menu.addAction(QAction("Re-record the way to History…", self.menu, triggered=self.start_recording))
        self.menu.addSeparator()
        self.menu.addAction(QAction("Quit", self.menu, triggered=self.quit))
        self.menu.aboutToShow.connect(self.update_menu)
        self.tray = QSystemTrayIcon(self.icon(), qt)
        self.tray.setToolTip("Dota Rank Tracker")
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(lambda r: r == QSystemTrayIcon.Trigger and self.toggle_overlay())
        self.tray.show()

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

        # Dota borderless can push itself above us; re-assert topmost now and then
        self.top_timer = QTimer(timeout=lambda: self.overlay.isVisible() and pin_topmost(int(self.overlay.winId())))
        self.top_timer.start(5000)
        self.read_timer = QTimer(timeout=self.auto_read)
        self.read_timer.start(READ_MS)

    def place(self):
        geo = self.qt.primaryScreen().availableGeometry()
        x, y = self.settings.get("pos") or (geo.right() - Overlay.W - 24, geo.top() + 24)
        # keep it on a visible screen if monitors changed
        if not any(s.availableGeometry().contains(QPoint(x + 20, y + 20)) for s in self.qt.screens()):
            x, y = geo.right() - Overlay.W - 24, geo.top() + 24
        self.overlay.move(x, y)


    def update_menu(self):
        self.session_act.setText("End session" if self.session_start() else "Start session")

    def icon(self):
        return QIcon(self.medal) if self.medal else app_icon()

    def set_medal(self, img):
        """Keep the medal snapshot taken with a good MMR read (so it's the History page, not a game)."""
        img = cut_out(img)
        self.medal = QPixmap.fromImage(img)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        img.save(str(MEDAL))
        self._changed()

    def mmr_log(self):
        return self.settings.get("mmr", [])

    def session_start(self):
        return self.settings.get("session")

    def _changed(self):
        save_settings(self.settings)
        self.overlay.update()
        self.tray.setIcon(self.icon())
        if self.history:
            self.history.refresh()

    def set_log(self, log):
        self.settings["mmr"] = sorted(log, key=lambda e: e["t"])
        self._changed()

    def save_mmr(self, value):
        self.set_log(add_log(self.mmr_log(), value, int(time.time() * 1000)))

    def auto_read(self):
        """While Dota is in front, OCR the MMR box once it holds still (a menu page, not the moving
        game world) and looks different from the last OCR. Costs nothing during a match."""
        area = self.settings.get("mmr_area")
        if not area or self.reading or self.asking or not dota_in_front():
            return
        img = grab(self.qt, area)
        t = thumb(img)
        still, self.seen = same(t, self.seen), t
        if still and not same(t, self.read_seen):
            self.read_seen, self.reading = t, True
            self.reader.run(img, self.medal_img(), "auto")

    def medal_img(self):
        area = self.settings.get("medal_area")
        return grab(self.qt, area) if area else None

    def capture_mmr(self, medal=True):
        """Ctrl+Shift+E: read the MMR (and medal) boxes and log, no questions. First time: pick the boxes."""
        if self.asking:
            return
        area = self.settings.get("mmr_area")
        if not area:
            return self.pick_area()
        self.overlay.flash("reading…", DIM)
        self.reader.run(grab(self.qt, area), self.medal_img() if medal else None, "manual")

    def on_read(self, value, medal_img, mode):
        if mode == "auto":
            self.reading = False
        if value is not None and medal_img is not None:
            self.set_medal(medal_img)
        manual = mode == "manual"
        log = self.mmr_log()
        if manual and value is None:
            self.overlay.flash("can't read", LOSS)
            self.tray.showMessage("Dota Rank Tracker", "Couldn't read a number in your MMR box. Open Dota's History "
                                  "page, or right-click the widget → Set MMR + medal screen areas.")
        elif manual or accept(log, value):
            if not log or value != log[-1]["mmr"]:
                self.save_mmr(value)
            if manual:
                self.overlay.flash("captured", GOLD)

    def pick_area(self):
        """Two boxes on one frozen screenshot: the MMR number, then the medal (Esc skips the medal)."""
        if self.asking:
            return
        self.asking = True
        self.picker = AreaPicker(self.qt, self.on_pick_mmr, "1/2  Drag a box around your MMR number\n"
                                 "(Dota → your profile → History, top right) · Esc to cancel")
        self.picker.show()
        self.picker.activateWindow()

    def on_pick_mmr(self, area):
        if not area:
            self.asking = False
            return
        self.settings["mmr_area"] = area
        self.picker = AreaPicker(self.qt, self.on_pick_medal, "2/2  Now drag a box tightly around your medal\n"
                                 "(left of your name) · Esc to skip", self.picker.shot)
        self.picker.show()
        self.picker.activateWindow()

    def on_pick_medal(self, area):
        self.asking = False
        if area:
            self.settings["medal_area"] = area
        save_settings(self.settings)
        QTimer.singleShot(300, self.capture_mmr)  # once the picker is off the screen

    # Ctrl+Shift+D: your recorded clicks to Dota's History page, then a capture
    def history_shortcut(self):
        if self.recording is not None:
            return self.stop_recording()
        clicks = self.settings.get("history_clicks")
        if not clicks:
            return self.start_recording()
        if not self.settings.get("mmr_area"):
            return self.pick_area()
        if not START_SPOT.exists():  # recorded before this check existed
            return self.start_recording()
        self.go_history(clicks, CLICK_GAP)

    def in_menus(self, clicks):
        """Dota in front and its logo where the first click goes, like when you recorded:
        so never click into a game."""
        return dota_in_front() and looks_like(pixels(grab(self.qt, spot(clicks[0]))),
                                              pixels(QImage(str(START_SPOT))))

    def go_history(self, clicks, gap, retry=True):
        """Replay the clicks, then capture as soon as the MMR shows. Not there in time
        (slower PC, slow page): once more with double the gap."""
        if not self.in_menus(clicks):
            return self.overlay.flash("not in menus", LOSS)
        self.overlay.flash("going to History…", DIM)
        clicking = threading.Thread(target=replay, args=(clicks, gap), daemon=True)
        clicking.start()
        start, last = time.time(), [None]

        def check():
            if clicking.is_alive():
                return
            value = read_number(grab(self.qt, self.settings["mmr_area"]))
            agreed, last[0] = value is not None and value == last[0], value  # not a frame mid-fade
            if agreed:
                self.go_timer.stop()
                self.capture_mmr(medal=False)  # the MMR right away; the medal is still fading in,
                QTimer.singleShot(1200, lambda: self.settings.get("medal_area") and  # so snap it once the
                                  self.set_medal(self.medal_img()))  # page has settled
            elif time.time() - start > len(clicks) * gap + 2:
                self.go_timer.stop()
                if retry:
                    self.go_history(clicks, gap * 2, False)
                else:
                    self.capture_mmr()  # says it can't read
        self.go_timer = QTimer(timeout=check)
        self.go_timer.start(50)

    def start_recording(self):
        self.recording, self.was_down = [], True
        self.rec_timer = QTimer(timeout=self.record_click)
        self.rec_timer.start(15)
        self.overlay.note = ("recording clicks", WIN)
        self.overlay.update()
        self.tray.showMessage("Recording your way to History", "In Dota, click the Dota logo (top left), then your "
                              "profile, then History. Then press Ctrl+Shift+D to save.")

    def record_click(self):
        down = bool(U.GetAsyncKeyState(0x01) & 0x8000)  # left mouse button
        if down and not self.was_down and dota_in_front():
            p = wintypes.POINT()
            U.GetCursorPos(ctypes.byref(p))
            self.recording.append([p.x, p.y])
        self.was_down = down

    def stop_recording(self):
        self.rec_timer.stop()
        clicks, self.recording = self.recording, None
        self.overlay.note = None
        if clicks:
            self.settings["history_clicks"] = clicks
            save_settings(self.settings)
            grab(self.qt, spot(clicks[0])).save(str(START_SPOT))  # the logo, to check before each replay
            self.overlay.flash(f"saved {len(clicks)} clicks", WIN)
            QTimer.singleShot(800, self.capture_mmr)
        else:
            self.overlay.flash("nothing recorded", LOSS)

    def set_session(self, start):
        self.settings["session"] = start
        self._changed()

    def toggle_session(self):
        self.set_session(None if self.session_start() else int(time.time() * 1000))

    def toggle_overlay(self):
        show = not self.overlay.isVisible()
        self.overlay.setVisible(show)
        if show:
            pin_topmost(int(self.overlay.winId()))
        self.settings["hidden"] = not show
        save_settings(self.settings)
        self.toggle_act.setText(("Hide" if show else "Show") + " widget\tCtrl+Shift+R")

    def open_history(self):
        if not self.history:
            self.history = History(self)
        self.history.refresh()
        self.history.showNormal()
        dark_title_bar(int(self.history.winId()))
        self.history.raise_()
        self.history.activateWindow()

    def quit(self):
        self.tray.hide()
        self.qt.exit(0)


def rounded_menu(title="", parent=None):
    m = QMenu(title, parent)  # rounded corners need a see-through, frameless popup
    m.setAttribute(Qt.WA_TranslucentBackground)
    m.setWindowFlags(m.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    return m


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
