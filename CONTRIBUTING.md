# Contributing

Arena Coach is still in an active tester phase.

## Basic Rules

- Do not delete `data/`, `logs/`, `exports/`, `imports/`, `backups/`, or `arena_coach_config.json` during updates.
- Keep CLI workflows working when adding GUI features.
- Prefer backend/service changes over widget-local business logic.
- Preserve raw match logs.
- Treat `userid` as the strong identity signal.

## Dev Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -B -m unittest discover -s tests
python -m arena_coach.gui.app
```

## Testing

Run the full test suite before handing off changes:

```powershell
python -B -m unittest discover -s tests
```
