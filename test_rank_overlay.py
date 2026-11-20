"""Run: python test_rank_overlay.py (no Qt window opens)."""
import numpy as np
from unittest.mock import Mock, patch

from rank_overlay import (accept, add_log, believable, daily_results, games, looks_like, migrate, number,
                          same, summary, text_lines)

S = 1000  # ms per second
log = [{"t": 1000 * S, "mmr": 3000}, {"t": 2000 * S, "mmr": 3025}, {"t": 3000 * S, "mmr": 3001},
       {"t": 3500 * S, "mmr": 3001}, {"t": 4000 * S, "mmr": 3030}]

# every MMR change is a game; the first entry and a repeat of the same MMR are not
assert games(log) == [(2000 * S, 3025, 25), (3000 * S, 3001, -24), (4000 * S, 3030, 29)]
assert games([]) == [] and games(log[:1]) == []

# session/today: games captured at/after the start
assert summary(log, 0) == (2, 1, 30)
assert summary(log, 2500 * S) == (1, 1, 5)  # marked the 2nd game as the start
assert summary(log, 4000 * S) == (1, 0, 29)  # start exactly at a game counts it
assert summary(log, 5000 * S) == (0, 0, 0)  # started after the last game: nothing yet

# the history's period boxes: days run 6 am to 6 am; "7 days" = today and the 6 days before
import time  # noqa: E402
from rank_overlay import period_start  # noqa: E402
at = lambda d, h, m=0: int(time.mktime((2026, 11, d, h, m, 0, 0, 0, -1)) * 1000)  # noqa: E731
now = at(6, 15, 30)
assert period_start(0, now) == at(6, 6)  # today since 6 am
assert period_start(0, at(6, 3)) == at(5, 6)  # 3 am is still last night's day
assert period_start(7, now) == at(0, 6) == int(time.mktime((2026, 10, 31, 6, 0, 0, 0, 0, -1)) * 1000)
assert period_start(1, now) == period_start(0, now) and period_start(None, now) == 0
assert summary(log, period_start(None, now)) == summary(log, 0)
# a period the log doesn't reach back to shows "—" (not a number that's really a shorter period),
# and so does one with no games in it
from rank_overlay import covers, period_box  # noqa: E402
D = 86_400_000
six = [{"t": now - 6 * D + 3_600_000, "mmr": 5400}, {"t": now - 2 * D, "mmr": 5430}, {"t": now - 3_600_000, "mmr": 5405}]
# only periods the log covers can be picked; Today and All always can
assert [covers(six, d, now) for d in (0, 3, 7, 30, None)] == [True, True, False, False, True]
assert covers([], 0, now) and covers([], None, now) and not covers([], 7, now)
assert period_box(six, 30, now) == ("empty", "—", "6 days so far")
assert period_box(six, 7, now) == ("empty", "—", "6 days so far")
assert period_box(six, 3, now) == ("up", "+5", "1–1 · 50% wins · 2 games")
assert period_box(six, None, now) == ("up", "+5", "1–1 · 50% wins · 2 games")
assert period_box(six, 0, now) == ("down", "-25", "0–1 · 0% wins · 1 game")
assert period_box(six[:2], 0, now) == ("empty", "—", "no games")
assert period_box([], 7, now) == ("empty", "—", "no data") and period_box([], None, now) == ("empty", "—", "no games")
assert period_box([{"t": now - 3_600_000, "mmr": 1}], 7, now)[2] == "1 day so far"

# Daily totals use the game's capture time, not the baseline or calendar midnight.
daily_log = [{"t": at(5, 5, 59), "mmr": 5400}, {"t": at(5, 6), "mmr": 5425},
             {"t": at(6, 0), "mmr": 5400}, {"t": at(6, 5, 59), "mmr": 5425},
             {"t": at(6, 6), "mmr": 5400}, {"t": at(6, 7), "mmr": 5400},
             {"t": at(7, 3), "mmr": 5425}]
assert daily_results(daily_log) == [(at(6, 6), 1, 1, 0), (at(5, 6), 2, 1, 25)]
assert daily_results(daily_log, at(6, 6)) == [(at(6, 6), 1, 1, 0)]
assert daily_results(daily_log, at(7, 6)) == []
assert daily_results([]) == [] and daily_results(daily_log[:1]) == []
month_edge = [{"t": at(0, 23), "mmr": 4000}, {"t": at(1, 5, 59), "mmr": 4025},
              {"t": at(1, 6), "mmr": 4000}]
assert daily_results(month_edge) == [(at(1, 6), 0, 1, -25), (at(0, 6), 1, 0, 25)]

# a re-capture within 10 minutes replaces the last entry (misread fix), later ones append
assert add_log(log, 3010, 4000 * S + 60 * S)[-2:] == [log[3], {"t": 4060 * S, "mmr": 3010}]
assert len(add_log(log, 3010, 4000 * S + 11 * 60 * S)) == 6
assert add_log([], 3000, 5) == [{"t": 5, "mmr": 3000}]

# v0.1 settings kept the log per OpenDota account (and a short-lived rank number)
old = {"account": "1", "mmr": {"1": log}, "session": {"1": 7}, "mmr_area": [1, 2, 3, 4], "medal": 75}
assert migrate(old) == {"mmr": log, "session": 7, "mmr_area": [1, 2, 3, 4]}
assert migrate({"mmr": log, "session": None}) == {"mmr": log, "session": None}

# OCR text -> MMR: one number only (in-game K/D/A or the clock must not count)
assert number("4,002") == 4002 and number("MMR 4002") == 4002 and number("12 345") == 12345
assert number("513110") is None and number("5/3/10") is None and number("12:34") is None and number("") is None
# Dota writes "5,404"; OCR may give "5.404" and the label too
assert number("5,404 MMR") == 5404 and number("5.404") == 5404

# auto-read logs only a changed, believable reading, and never starts the log
L = [{"t": 0, "mmr": 4000}]
assert accept(L, 4025) and accept(L, 3975) and accept(L, 4100) and accept(L, 3900)
assert not accept(L, 4000) and not accept(L, 4101) and not accept(L, 3899)
assert not accept([], 4025) and not accept(L, None)
# the medal snapshot is only kept with a believable MMR read: an automatic read of some other
# number (the Heroes page, with Axe where the medal goes) must not replace it
L = [{"t": 0, "mmr": 5404}]
assert not believable(L, 17, False) and not believable(L, None, False) and not believable([], 5404, False)
assert believable(L, 5404, False) and believable(L, 5380, False)
assert believable(L, 5504, True) and believable(L, 5304, True)
assert not believable(L, 5505, True) and not believable(L, 5303, True)
assert believable([], 5404, True) and not believable([], 5404, False)
# the MMR box counts as "still" when its thumbnail barely changes
assert same(b"\x10\x20", b"\x12\x1e") and not same(b"\x10\x20", b"\x10\x40") and not same(b"\x10", None)
# the OCR finds each line of light text: here a number (rows 10-25) over a label (rows 30-37);
# a 2-row speck is not text
a = np.full((50, 80, 3), 20, np.uint8)
a[10:26, 15:60] = 230
a[30:38, 20:50] = 200
a[45:47, 5:8] = 255
assert text_lines(a) == [(10, 26, 15, 60), (30, 38, 20, 50)]
assert text_lines(np.full((20, 20, 3), 20, np.uint8)) == []

# the History shortcut only clicks when the first spot still looks like the Dota logo did
logo = np.random.default_rng(1).integers(0, 255, (48, 48, 3), dtype=np.uint8)
glow = np.clip(logo.astype(int) + 8, 0, 255).astype(np.uint8)  # the logo's animated glow
game = np.random.default_rng(2).integers(0, 255, (48, 48, 3), dtype=np.uint8)  # a match there
assert looks_like(logo, glow) and not looks_like(logo, game) and not looks_like(logo, logo[:40])

# Dota's game feed, in the order a real match sends it: hide once picks are done, act when the Ancient falls
import rank_overlay as ro  # noqa: E402


def msg(state, win="none", hero="npc_dota_hero_furion", match="9070038408", team="radiant"):
    return {"map": {"matchid": match, "game_state": "DOTA_GAMERULES_STATE_" + state, "win_team": win},
            "player": {"team_name": team}, "hero": {"name": hero} if hero else {}}


w = ro.GameWatcher()
seen = [w.feed(m) for m in (msg("HERO_SELECTION", hero=None), msg("STRATEGY_TIME"), msg("PRE_GAME"),
                            msg("GAME_IN_PROGRESS"), msg("GAME_IN_PROGRESS", "dire"), msg("POST_GAME", "dire"), {})]
assert [e for e in seen if e] == [("started", "npc_dota_hero_furion"), ("ended", "npc_dota_hero_furion")]
assert w.feed(msg("STRATEGY_TIME", match="0")) is None  # a demo
assert w.feed(msg("STRATEGY_TIME", match="1", team=None)) is None  # watching someone else's game
assert ro.hero_name("npc_dota_hero_furion") == "Nature's Prophet" and ro.hero_name("npc_dota_hero_dark_willow") == "Dark Willow"

# Keep duplicate suppression bounded, with separate hero state for each match.
assert w.feed(msg("GAME_IN_PROGRESS")) is None  # a late packet must not re-start an ended match
assert w.feed(msg("PRE_GAME", match="2", hero=None)) == ("started", None)
assert w.feed(msg("POST_GAME", "dire", match="2", hero=None)) == ("ended", None)
for match in range(3, ro.RECENT_MATCHES + 10):
    assert w.feed(msg("PRE_GAME", match=str(match))) == ("started", "npc_dota_hero_furion")
    assert w.feed(msg("POST_GAME", "dire", match=str(match))) == ("ended", "npc_dota_hero_furion")
assert len(w.matches) == ro.RECENT_MATCHES and "2" not in w.matches
assert w.feed(msg("POST_GAME", "dire", match=str(ro.RECENT_MATCHES + 9))) is None


def capture_app(entries):
    """Use real app callbacks without opening an overlay, writing settings or touching Dota."""
    app = object.__new__(ro.App)
    app.settings = {"mmr": entries, "mmr_area": [0, 0, 100, 30]}
    app.qt, app.overlay, app.tray, app.reader = Mock(), Mock(), Mock(), Mock()
    app.overlay.winId.return_value = 123
    app.asking = app.reading = False
    app.seen = app.read_seen = None
    app.read_retry_at = 0
    app.ocr_problem = None
    app.medal_img = Mock(return_value=None)
    app.save_mmr, app.set_medal = Mock(), Mock()
    return app


# Failed and implausible automatic reads retry the same screen, but never before the cooldown.
for result in (None, 17):
    app = capture_app([{"t": 0, "mmr": 5404}])
    with patch.object(ro, "grab"), patch.object(ro, "thumb", return_value=b"\x10\x20"), \
            patch.object(ro, "dota_in_front", return_value=True), \
            patch.object(ro.time, "monotonic", return_value=100) as clock, patch.object(ro, "note"):
        app.auto_read()  # first look is not stable yet
        app.reader.run.assert_not_called()
        app.auto_read()
        app.reader.run.assert_called_once()
        assert app.reading
        app.on_read(result, "medal", "auto")
        assert not app.reading and app.read_seen is None
        app.set_medal.assert_not_called()
        app.save_mmr.assert_not_called()
        clock.return_value = 100 + ro.READ_RETRY_S - 0.01
        app.auto_read()
        assert app.reader.run.call_count == 1
        clock.return_value = 100 + ro.READ_RETRY_S
        app.auto_read()
        assert app.reader.run.call_count == 2
        app.on_read(5429, None, "auto")
        app.save_mmr.assert_called_once_with(5429)
        app.auto_read()  # a successful unchanged image stays cached
        assert app.reader.run.call_count == 2

app = capture_app([])
with patch.object(ro, "grab") as screenshot:
    app.auto_read()  # never establish a baseline automatically
    screenshot.assert_not_called()
app.on_read(5404, None, "manual")
app.save_mmr.assert_called_once_with(5404)
app = capture_app([{"t": 0, "mmr": 5404}])
with patch.object(ro, "note"):
    app.on_read(17, "bad medal", "manual")
app.save_mmr.assert_not_called()
app.set_medal.assert_not_called()
app.tray.showMessage.assert_called_once()

# OCR errors remain distinct from a screenshot containing no number.
with patch.object(ro, "pixels"), patch.object(ro, "text_lines", return_value=[]):
    assert ro.read_number(None) is None
with patch.object(ro, "pixels", side_effect=ValueError("broken screenshot")):
    try:
        ro.read_number(None)
    except ValueError as exc:
        assert str(exc) == "broken screenshot"
    else:
        raise AssertionError("OCR processing errors must not become an ordinary no-number result")

reader = ro.Reader()
errors, readings = [], []
reader.error.connect(lambda message, mode: errors.append((message, mode)))
reader.done.connect(lambda value, medal, mode: readings.append((value, medal, mode)))


def inline_thread(*, target, daemon):
    return Mock(start=target)


with patch.object(ro.threading, "Thread", side_effect=inline_thread), \
        patch.object(ro, "read_number", side_effect=RuntimeError("broken model")), \
        patch.object(ro, "ocr_engine", side_effect=FileNotFoundError("missing model")):
    reader.run(None, None, "auto")
    reader.warm_up()
assert errors == [("RuntimeError: broken model", "auto"), ("FileNotFoundError: missing model", "startup")]
assert readings == []
with patch.object(ro.time, "monotonic", return_value=100), patch.object(ro, "note") as event_log:
    app.on_ocr_error("broken model", "auto")
    app.on_ocr_error("broken model", "auto")
    event_log.assert_called_once()
    assert app.read_seen is None and not app.reading
    assert app.read_retry_at == 100 + ro.READ_RETRY_S
    assert app.tray.showMessage.call_count == 2  # prior rejected manual read plus one OCR error

# After-game capture waits ten seconds, then waits for menus, or stops on capture/timeout.
app = capture_app([{"t": 0, "mmr": 5404}])
app.watcher, app.last_game = ro.GameWatcher(), None
app.after_timer, app.go_history = Mock(), Mock()
app.in_menus = Mock(return_value=False)
app.recording = None
app.settings.update(history_clicks=[[10, 10]], hidden=True)
with patch.object(ro.QTimer, "singleShot") as delayed, patch.object(ro.time, "time", return_value=100) as clock, \
        patch.object(ro, "START_SPOT") as spot, patch.object(ro, "note"), \
        patch.object(ro, "save_settings"), patch.object(ro, "pin_topmost"):
    spot.exists.return_value = True
    app.on_game(msg("PRE_GAME"))
    app.overlay.hide.assert_called_once()
    app.on_game(msg("POST_GAME", "dire"))
    app.on_game(msg("POST_GAME", "dire"))
    delayed.assert_called_once_with(10_000, app.after_game)
    app.after_game()
    app.after_timer.start.assert_called_once_with(2000)
    app.overlay.show.assert_called_once()
    assert not app.settings["hidden"]
    app.go_history.assert_not_called()
    app.in_menus.return_value = True
    app.asking = True
    app.try_after_game()
    app.go_history.assert_not_called()
    app.asking = False
    app.recording = []
    app.try_after_game()
    app.go_history.assert_not_called()
    app.recording = None
    app.try_after_game()
    app.go_history.assert_called_once_with([[10, 10]], ro.CLICK_GAP)
    app.after_timer.stop.assert_called_once()
    app.go_history.reset_mock()
    app.settings["mmr"] = [{"t": 100_001, "mmr": 5429}]
    app.try_after_game()
    app.go_history.assert_not_called()
    app.settings["mmr"] = [{"t": 0, "mmr": 5404}]
    clock.return_value = app.after_until + 1
    app.try_after_game()
    app.go_history.assert_not_called()
    assert app.after_timer.stop.call_count == 3

# History navigation's delayed medal capture must revalidate MMR, not save a bad page unconditionally.
app = capture_app([{"t": 0, "mmr": 5404}])
app.settings["medal_area"] = [100, 0, 100, 100]
app.in_menus = Mock(return_value=True)
app.medal_img.return_value = "bad medal"
app.reader.run.side_effect = lambda image, medal, mode: app.on_read(17, medal, mode)
with patch.object(ro, "QTimer") as timers, patch.object(ro.threading, "Thread") as thread, \
        patch.object(ro, "read_number", return_value=17), patch.object(ro, "grab"), patch.object(ro, "note"):
    thread.return_value.is_alive.return_value = False
    app.go_history([[10, 10]], ro.CLICK_GAP)
    check = timers.call_args.kwargs["timeout"]
    check()
    check()
    timers.singleShot.assert_called_once()
    timers.singleShot.call_args.args[1]()
    assert app.reader.run.call_count == 2
    app.set_medal.assert_not_called()
    app.save_mmr.assert_not_called()

# hotkeys: one letter or digit each, all different
assert ro.hotkey_letters({}) == {"hide": "R", "capture": "E", "history": "D"}
assert ro.hotkey_letters({"keys": {"history": "A"}})["history"] == "A"
assert ro.hotkey_problem({"hide": "R", "capture": "E", "history": "A"}) is None
assert ro.hotkey_problem({"hide": "R", "capture": "R", "history": "A"}) and ro.hotkey_problem({"hide": "", "capture": "E", "history": "A"})

# daily backups: one copy a day, the last 7 kept
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402
with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    with patch.object(ro, "SETTINGS", tmp / "settings.json"), patch.object(ro, "BACKUP_DIR", tmp / "backups"):
        ro.SETTINGS.write_text("{}")
        for day in range(1, 11):
            ro.backup(f"2026-11-{day:02d}")
            ro.backup(f"2026-11-{day:02d}")  # a second save that day changes nothing
        assert sorted(f.name for f in ro.BACKUP_DIR.iterdir()) == [
            f"settings-2026-11-{d:02d}.json" for d in range(4, 11)]
    with patch.object(ro, "_ocr", None), patch.object(ro, "MODEL", tmp / "missing.onnx"):
        try:
            ro.ocr_engine()
        except FileNotFoundError as exc:
            assert "OCR model not found" in str(exc)
        else:
            raise AssertionError("A missing OCR model must produce a diagnostic")
    bad_model = tmp / "broken.onnx"
    bad_model.write_bytes(b"not an ONNX model")
    with patch.object(ro, "_ocr", None), patch.object(ro, "MODEL", bad_model):
        try:
            ro.ocr_engine()
        except RuntimeError as exc:
            assert "Couldn't load OCR model" in str(exc)
        else:
            raise AssertionError("An invalid OCR model must produce a diagnostic")

# Verify the actual History tables, including period changes and edited/deleted captures, offscreen.
with patch.dict(ro.os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
    qt = ro.QApplication.instance() or ro.QApplication([])
qt.setStyleSheet(ro.STYLE)
app = capture_app(daily_log)
app.medal = None
app.portraits = Mock()
app.portraits.get.return_value = None
app.toggle_session = Mock()
with patch.object(ro.time, "time", return_value=at(7, 15) / 1000):
    history = ro.History(app)
    app._changed = Mock(side_effect=history.refresh)
    history.refresh()
    history.show()
    qt.processEvents()
    assert history.windowTitle() == ro.APP_TITLE
    assert history.tabs.tabText(1) == "By day"
    assert history.daily_table.rowCount() == 2
    assert [history.daily_table.item(0, c).text() for c in range(1, 5)] == ["1", "1", "50%", "+0"]
    assert [history.daily_table.item(1, c).text() for c in range(1, 5)] == ["2", "1", "67%", "+25"]
    assert history.table.rowCount() == len(daily_log)
    app.set_log(daily_log[:-1])
    assert [history.daily_table.item(0, c).text() for c in range(1, 5)] == ["0", "1", "0%", "-25"]
    app.set_log(daily_log[:-1] + [{"t": at(7, 3), "mmr": 5450}])
    assert history.daily_table.item(0, 4).text() == "+25"
    app.settings["period"] = 0
    history.refresh()
    assert history.daily_table.rowCount() == 0 and "No games" in history.daily_hint.text()
    history.close()

# The tray menu and tooltip identify the build without registering hotkeys or starting a game feed.
with patch.object(ro, "load_settings", return_value={}), patch.object(ro, "Overlay"), \
        patch.object(ro, "QSystemTrayIcon") as tray, patch.object(ro, "QTimer"), \
        patch.object(ro.Reader, "warm_up"), patch.object(ro.App, "place"), \
        patch.object(ro.App, "start_game_feed"), patch.object(ro.App, "register_hotkeys", return_value=[]), \
        patch.object(ro, "hide_from_capture"):
    app = ro.App(qt)
    header = app.menu.actions()[0]
    assert header.text() == f"Dota Rank Tracker v{ro.VERSION}" and not header.isEnabled()
    tray.return_value.setToolTip.assert_called_once_with(ro.APP_TITLE)
    assert any(action.text() == "History" for action in app.menu.actions())
    assert any(action.text() == "Quit" for action in app.menu.actions())
    app.menu.close()

# The widget and tray share one setting, and switching off cancels a pending menu wait.
app = capture_app(daily_log)
app.after_timer, app.auto_act = Mock(), ro.QAction(checkable=True, checked=True)
app.go_history = Mock()
app.overlay = ro.Overlay(app)
button = app.overlay.auto_button.geometry()
assert button.left() > 84 + 4 * (24 + 5) + 24 and button.center().y() in range(40, 64)
assert app.overlay.rect().contains(button) and app.overlay.width() < 284
app.qt = qt
area = qt.primaryScreen().availableGeometry()
app.settings["pos"] = [area.right() - 248 + 1, area.top() + 24]
app.place()
assert app.overlay.geometry().right() == area.right()
app.qt = Mock()
with patch.object(ro, "save_settings") as persist, patch.object(ro, "grab"):
    app.overlay.auto_button.click()
    assert app.overlay.auto_button.text() == "Auto" and not app.overlay.auto_button.isChecked()
    assert not app.auto_act.isChecked()
    assert app.settings["auto_after_game"] is False
    app.after_timer.stop.assert_called_once()
    app.try_after_game()  # a queued callback must not navigate after switching off
    app.go_history.assert_not_called()
    app.capture_mmr()
    app.reader.run.assert_called_once()
    app.auto_act.triggered.connect(app.set_auto)
    app.auto_act.trigger()
    assert app.overlay.auto_button.isChecked() and app.overlay.auto_button.text() == "Auto"
    assert app.settings["auto_after_game"] is True and persist.call_count == 2
app.overlay.close()
print("ok")
