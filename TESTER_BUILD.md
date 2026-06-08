# Arena Coach Tester Build

This is a tester build, not an installer.

## What You Need

- Windows
- Python 3.10 or newer

If Python is missing:

```powershell
winget install Python.Python.3.12
```

Then close and reopen PowerShell.

## Setup

From the ArenaCoach folder:

```powershell
.\scripts\setup_windows.ps1
```

When setup finishes, launch Arena Coach by double-clicking:

- `run_arena_coach.pyw`

If you need the debug console version, use:

- `run_arena_coach_debug.bat`

## First Test Flow

1. Open Arena Coach.
2. Create your profile.
3. Click `Test Connection`.
4. Open Echo VR / Echo Arena.
5. Click `Test Connection` again.
6. Click `Start Logging`.
7. Play or enter a match.
8. Click `Stop Logging`.
9. Click `Process Match for Review`.
10. Walk through Guided Review and finalize the match.

## Send Data Back

Inside Arena Coach:

1. Open the `Settings` tab.
2. In `Data Sharing`, click `Export My Data`.
3. Arena Coach will create a zip in the `exports` folder.
4. Send that zip back to the developer.

Raw logs are optional during export. Leave them off unless the developer asks for them.

## Important Safety Notes

Do not delete these folders/files when updating Arena Coach:

- `data/`
- `logs/`
- `exports/`
- `imports/`
- `backups/`
- `arena_coach_config.json`

Those contain your local data.

## Quick Troubleshooting

- If setup fails, run `scripts\check_setup.ps1`
- If the app does not open, run `run_arena_coach_debug.bat`
- If something looks wrong, export your data and send the zip plus a screenshot to the developer
