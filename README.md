# Dota 2 Ranked Tracker

A small always-on-top widget for Dota 2 ranked: your medal, your last 5 games as W/L boxes, and the **MMR +/- of each game** right after it ends. Click it to open a big stats window.

![widget](docs/widget.png)

## Download

Get `DotaRankTracker.exe` from the [Releases](../../releases) page and run it. No install needed.

Windows may say "Windows protected your PC" because the exe isn't signed. Click **More info → Run anyway**.

## First run

1. In Dota: **Settings → Options → Social → Expose Public Match Data**: turn it **on**. Without this, nothing shows up.
2. Start the app and paste your **Dota friend ID** (Dota → your profile). A Steam profile link or a Dotabuff/OpenDota/Stratz link also works.
3. The widget appears top-right. Drag it wherever you like.
4. Play Dota in **Borderless Window** mode. Overlays can't show on top of exclusive fullscreen.

## Using it

| Do this | What happens |
|---|---|
| **Click** the widget | Opens the full stats window |
| **Drag** the widget | Moves it (position is remembered) |
| **Right-click** the widget or the tray icon | Menu: capture MMR, set MMR screen area, start/end session, check for new games, change account, quit |
| **Ctrl+Shift+R** | Hide / show the widget |
| **Ctrl+Shift+E** | Capture your MMR off Dota's screen |

### MMR +/- per game

Valve doesn't let outside apps see MMR changes. That's why Dota Plus is the only place that shows them, and it often lags. This app reads your MMR straight off Dota's screen instead, with [RapidOCR](https://github.com/RapidAI/RapidOCR). It's an offline text reader built into the app, and it got 400 out of 400 test numbers right in Dota's own font.

1. **Once:** open **Dota → Profile** (Stats or History). Your MMR is in the top right. Press **Ctrl+Shift+E** and drag a box around the number. That's the whole setup.
2. **After each ranked game:** open Profile again. The app logs your new MMR by itself within a few seconds, or press **Ctrl+Shift+E** to capture it right away. You never type or confirm anything.
3. The widget shows your current MMR under the medal, so you can see that it read the right number. The +/- appears under that game. If OpenDota hasn't picked up the game yet, it shows as a dashed box until it arrives.

**Wrong number?** Open the big window → **MMR history**. Click the number to fix it, or press **×** to delete it.

Changed resolution, or the number moved? Right-click → **Set MMR screen area…**. The app only reads the box when the screen there is standing still (a menu, not the game), and an automatic read only counts if it's within 300 of your last MMR. The widget doesn't show up in screenshots, so it can sit on top of the number. If you play two games without logging, they can't be split. They still count for W–L, but get no +/-.

### Sessions

- **Start session** (big window, or the right-click menu) begins counting from now.
- Forgot to start one? In the big window's **Match history**, click **⚑** next to the first game of your session. It starts from that game.
- While a session is on, the widget shows `session 3–1 +52` and the big window shows W–L, MMR total and net wins.

### What the big window shows

Medal, leaderboard spot, OpenDota's MMR estimate, ranked W–L (all time, last 20, today, last 7 days), streak, average medal of your lobbies, KDA, GPM/XPM, hero damage, game length, your last 2 games, ranked form chart, lobby medal over time, full match history (hero, result, MMR +/-, KDA, GPM/XPM, last hits, damage, party, lobby medal, links to OpenDota/Stratz), MMR history, win rate by role/party/side, heroes and the people you queue with.

`index.html` also works on its own: double-click it to open it in a browser.

## Where data comes from

- Games and rank: [OpenDota](https://www.opendota.com) API (free, public data only). The app asks OpenDota to fetch your newest games every 3 minutes.
- Your MMR: read on your own PC from Dota's screen (only the box you picked). No game memory, no Steam login.
- Your settings and MMR log: `%APPDATA%\DotaRankTracker\` on your own PC. Nothing is uploaded anywhere.

## Build it yourself

```powershell
pip install -r requirements.txt pyinstaller
python test_rank_overlay.py
.\build.ps1          # -> dist\DotaRankTracker.exe
```

Pushing a tag like `v0.1.0` builds the exe on GitHub and attaches it to a release.
