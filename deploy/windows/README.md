# Unattended start on Windows

The final PC signs in automatically to its single Windows account and the
system starts with that session. This folder registers that start for the
current user with standard Windows tools:

```text
Windows sign-in (automatic on the final PC)
 -> Task Scheduler task "Batcomputer" (at this user's sign-in)
 -> services/local-supervisor/autostart.py
      -> supervisor (run.py): Docker Desktop, Mosquitto, GPU detector, Frigate, services
      -> map window: Chrome app window in full screen, once the map answers
```

## Install

From the repository root, in the account that must run the system (no
administrator rights needed):

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\install-autostart.ps1
```

`-NoMapWindow` starts the system without opening the map window, and
`-Uninstall` removes the task and the shortcut. To test without signing out:
`Start-ScheduledTask -TaskName Batcomputer`.

The task:

- runs `python.exe` found on `PATH` at install time (the interpreter of the
  supervisor's own services);
- uses an interactive logon, because Docker Desktop and the map window need
  the user's desktop;
- has no execution time limit (Task Scheduler stops tasks after three days by
  default) and normal priority (the default, 7, is below normal and every
  child process would inherit it);
- ignores a second start while one is running.

## Map window

The map opens as a Chrome app window (no tabs or address bar) in full screen,
on the monitor view (`http://127.0.0.1:8091/?view=monitor`). It uses its own
Chrome profile under `runtime/map-window/chrome-profile`, so it is separate
from the user's Chrome and its flags apply even when that Chrome is open.

| Key | Action |
|---|---|
| `F11` | toggle full screen and a normal window |
| `Alt+F4` | close the map window; the system keeps running |
| `Ctrl+Alt+B` | open the map window again (Start Menu shortcut "Batcomputer mapa") |

Chrome's `--kiosk` mode was not used because it cannot be left with `F11`;
the owner wants to switch between the full-screen view and normal work on the
development PC (2026-10-01). Whether the final PC needs the locked kiosk mode
is decided with its installation (roadmap phase 5).

## Autostart behavior

`autostart.py` runs the supervisor in its own console window and:

- retries a failed start every 30 s (for example Docker Desktop not ready in
  time, exit code 3), so a slow boot does not leave the PC without a system;
- ends when the supervisor exits cleanly (`Ctrl+C` in its window);
- opens the map window once, when `http://127.0.0.1:8091/api/health` answers.

Its log is `runtime/local-supervisor/logs/autostart-YYYYMMDD.log`; the
supervisor's own output is in `runtime/local-supervisor/logs/<session>/supervisor.log`.
Both follow the seven-day retention. While it runs, the supervisor asks
Windows not to sleep (`SetThreadExecutionState`); no power setting changes,
and the display may still turn off.

Closing the console window ends the system without a clean stop. That is
safe: on the next start Frigate is restarted after the detector, and the
outboxes keep undelivered messages.

## Validation on the development PC (2026-10-01)

`Start-ScheduledTask` with Docker Desktop running and Frigate blind (started
by Docker before the detector): the supervisor started 4 s after the task,
restarted Frigate (236 `Model not ready` warnings in the 21 s before, none
after), and the map window opened 20 s after the task. Every process ran at
normal priority. `Ctrl+Alt+B` reopened the map window.

Real restart of the PC, signing in by hand (the development PC is shared and
its automatic sign-in belongs to another account, which was left untouched):

| Step | Local time | Since boot |
|---|---|---:|
| Windows boot | 10:34:48 | 0 s |
| Sign-in (by hand) | 10:35:17 | 29 s |
| `autostart.py` started by the task | 10:35:19 | 31 s |
| Docker Desktop answered (started by the supervisor, 9.6 s) | 10:35:34 | 46 s |
| Mosquitto listening | 10:35:42 | 54 s |
| GPU detector client listening | 10:35:48 | 60 s |
| Frigate restarted after the detector and answering | 10:36:12 | 84 s |
| All services started, map window open | 10:36:13 | 85 s |

From sign-in to the map: 56 s, with one autostart instance and no retry.
Fifteen minutes later Frigate had no `Model not ready` warnings, 33 ms per
inference and no skipped frames. On a cold Docker, restarting Frigate took
about 22 s, against about 7 s with Docker already running.
