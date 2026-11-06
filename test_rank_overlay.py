"""Run: python test_rank_overlay.py (no Qt window opens)."""
import numpy as np

from rank_overlay import (accept, add_log, believable, games, looks_like, migrate, number, same, summary,
                          text_lines)

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
assert accept(L, 4025) and accept(L, 3975) and not accept(L, 4000)
assert not accept(L, 4400) and not accept([], 4025) and not accept(L, None)
# the medal snapshot is only kept with a believable MMR read: an automatic read of some other
# number (the Heroes page, with Axe where the medal goes) must not replace it
L = [{"t": 0, "mmr": 5404}]
assert not believable(L, 17, False) and not believable(L, None, False) and not believable([], 5404, False)
assert believable(L, 5404, False) and believable(L, 5380, False) and believable(L, 17, True)
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

# hotkeys: one letter or digit each, all different
assert ro.hotkey_letters({}) == {"hide": "R", "capture": "E", "history": "D"}
assert ro.hotkey_letters({"keys": {"history": "A"}})["history"] == "A"
assert ro.hotkey_problem({"hide": "R", "capture": "E", "history": "A"}) is None
assert ro.hotkey_problem({"hide": "R", "capture": "R", "history": "A"}) and ro.hotkey_problem({"hide": "", "capture": "E", "history": "A"})

# daily backups: one copy a day, the last 7 kept
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402
tmp = Path(tempfile.mkdtemp())
ro.SETTINGS, ro.BACKUP_DIR = tmp / "settings.json", tmp / "backups"
ro.SETTINGS.write_text("{}")
for day in range(1, 11):
    ro.backup(f"2026-11-{day:02d}")
    ro.backup(f"2026-11-{day:02d}")  # a second save that day changes nothing
assert sorted(f.name for f in ro.BACKUP_DIR.iterdir()) == [f"settings-2026-11-{d:02d}.json" for d in range(4, 11)]
print("ok")
