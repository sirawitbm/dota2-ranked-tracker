"""Run: python test_rank_overlay.py (no Qt window opens)."""
from rank_overlay import add_log, mmr_deltas, parse_id, rank_name, session_summary, number, accept, same

assert parse_id("https://steamcommunity.com/profiles/76561197960278073") == "12345"
assert parse_id("https://www.dotabuff.com/players/1234567") == "1234567"
assert parse_id(" 1234567 ") == "1234567" and parse_id("hello") is None
assert rank_name(54) == "Legend 4" and rank_name(80) == "Immortal" and rank_name(None) == "Uncalibrated"

S = 1000  # ms per second; match times are in seconds, log times in ms


def game(mid, end, win=True):  # ranked game that ended at `end` seconds
    return {"match_id": mid, "start_time": end - 100, "duration": 100, "player_slot": 0, "radiant_win": win}


log = [{"t": 1000 * S, "mmr": 3000}, {"t": 2000 * S, "mmr": 3025}, {"t": 3000 * S, "mmr": 3001}]
assert mmr_deltas(log, [game(1, 1500)]) == ({1: 25}, [(3000 * S, -24)])  # 2nd game not on OpenDota yet
assert mmr_deltas(log, [game(2, 2500, False), game(1, 1500)]) == ({1: 25, 2: -24}, [])
assert mmr_deltas(log, [game(1, 1500), game(3, 1600)]) == ({}, [(3000 * S, -24)])  # two games in one gap: can't split
assert mmr_deltas([], []) == ({}, [])
# OpenDota never got the 1st game: its change must not vanish once a newer game arrives
assert mmr_deltas(log, [game(2, 2500, False)]) == ({2: -24}, [(2000 * S, 25)])
assert session_summary(0, [game(2, 2500, False)], log) == (1, 1, 1, 2)

# a re-log within 10 minutes replaces the last entry (typo fix), later ones append
assert add_log(log, 3010, 3000 * S + 60 * S)[-2:] == [log[1], {"t": 3060 * S, "mmr": 3010}]
assert len(add_log(log, 3010, 3000 * S + 11 * 60 * S)) == 4
assert add_log([], 3000, 5) == [{"t": 5, "mmr": 3000}]

# session from game 1 on: game 1 (+25 W) and the pending loss (-24) count
assert session_summary(1400 * S, [game(1, 1500)], log) == (1, 1, 1, 2)
# session started after both games: nothing yet
assert session_summary(3500 * S, [game(2, 2500, False), game(1, 1500)], log) == (0, 0, 0, 0)
# marking the 2nd game as start counts only it
assert session_summary(2400 * S, [game(2, 2500, False), game(1, 1500)], log) == (0, 1, -24, 1)
# games with no logged MMR still count for W-L, just not for the MMR total
assert session_summary(0, [game(5, 4000), game(2, 2500, False), game(1, 1500)], log) == (2, 1, 1, 2)
# OCR text -> MMR: one number only (in-game K/D/A or the clock must not count)
assert number("4,002") == 4002 and number("MMR 4002") == 4002 and number("12 345") == 12345
assert number("513110") is None and number("5/3/10") is None and number("12:34") is None and number("") is None
# auto-read logs only a changed, believable reading, and never starts the log
L = [{"t": 0, "mmr": 4000}]
assert accept(L, 4025) and accept(L, 3975) and not accept(L, 4000)
assert not accept(L, 4400) and not accept([], 4025) and not accept(L, None)
# the MMR box counts as "still" when its thumbnail barely changes
assert same(b" ", b"") and not same(b" ", b"@") and not same(b"", None)
# Dota writes "5,404"; OCR may give "5.404" and the label too
assert number("5,404 MMR") == 5404 and number("5.404") == 5404
print("ok")
