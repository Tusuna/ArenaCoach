# Arena Coach

Standalone Echo Arena personal evaluation app.

Arena Coach is its own program. Schrodinger's Observer was used only as research and is not imported or required at runtime.

## What Works Now

- live Echo API capture to raw JSONL
- raw log parse/import to SQLite
- profile creation and active profile selection
- guided match review and finalization
- canonical players, aliases, known user IDs, and guest handling
- public/private/tournament match context
- private subtypes, AFK flags, round scores, and quality labels
- advanced inference and advanced player metrics
- Stats Preview, Advanced Summary, and Compare Players
- export/import/backup tools
- PySide6 desktop GUI and CLI

Current automated tests: `76`, passing.

## Main GUI Tabs

- Live Capture
- Match Review
- Match History
- Players
- Profile
- Stats Preview
- Advanced Summary
- Compare Players
- Settings
- Debug Logs

Top-level tabs stay fixed. Card-based tabs support saved order and saved heights.

## Fast Start

### Normal Windows tester flow

```powershell
.\scripts\check_setup.bat
```

If setup is needed:

```powershell
.\scripts\setup_windows.bat
```

Then launch:

- `run_arena_coach.pyw` for normal use
- `run_arena_coach_debug.bat` if you want a console/debug view

### Developer flow

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -B -m unittest discover -s tests
python -m arena_coach.gui.app
```

## Normal Match Workflow

1. Create or select your profile.
2. Open Echo VR.
3. Press `Test Connection`.
4. Press `Start Logging`.
5. Play the match.
6. Press `Stop Logging`.
7. Press `Process Match for Review`.
8. Confirm players, self, teams, AFK, and match context.
9. Finalize the match.
10. Press `Infer Selected Match` if you want advanced stats.
11. Review the results in Match History, Stats Preview, Advanced Summary, and Compare Players.

## Data Rules

- `userid` is the strong identity signal.
- Echo `playerid` is treated as local/session slot data, not stable identity.
- AFK is stored as suspected, not proven.
- Low-quality matches are kept in history but excluded from competitive stats by default.
- Advanced inferred events are stored separately from base normalized events.

## Export / Import / Backups

Arena Coach has tester-friendly sharing built in.

GUI:

- `Export My Data`: creates a clean zip in `exports/` and opens File Explorer to it
- `Import Shared Data`: lets you choose an Arena Coach zip and unpacks it into `imports/`
- `Backup Database Now`: creates a safety copy in `backups/`

CLI:

```powershell
python -m arena_coach.main data export
python -m arena_coach.main data import <export.zip>
python -m arena_coach.main data list-imports
python -m arena_coach.main data backup
```

External imports are unpacked separately and are not merged into your live database automatically.

## Useful Commands

```powershell
python -m arena_coach.main test-connection
python -m arena_coach.main start
python -m arena_coach.main parse-log <path>
python -m arena_coach.main import-log <path>
python -m arena_coach.main matches list
python -m arena_coach.main stats summary
python -m arena_coach.main advanced player <player_id>
python -B -m unittest discover -s tests
```

## Docs

- [TESTER_BUILD.md](./TESTER_BUILD.md)
- [docs/user_tutorial.md](./docs/user_tutorial.md)
- [docs/observer_research.md](./docs/observer_research.md)
- [docs/advanced_inference_research.md](./docs/advanced_inference_research.md)

## Known Limits

- AI Coach is not built yet.
- The final polished dashboard is not built yet.
- Advanced inferred events are heuristic, not absolute truth.
- AFK detection is suspicion, not proof.
- Some advanced scoring formulas still need more real competitive data for tuning.
