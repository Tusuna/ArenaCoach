# Observer Research for Arena Coach

Phase 0 findings for rebuilding the minimum Echo Arena capture/logging pipeline inside Arena Coach.

Primary rule for the rebuild: SchrodingersObserver is reference only. Arena Coach must not import Observer modules, run Observer executables, depend on Observer files at runtime, or write code inside the Observer folder.

## Reference Project Layout

Reference folder inspected:

```text
SchrodingersObserver/
  README.md
  API Examples/
    api_json.json
    pre_round_api_json.json
  Database_Files/
  Images/
  schrodingersObserver_GUI/
    schrodingersObserver.exe
    replay_api.exe
    evaluator.py
    SpeedReport_REV5.py
    live_update.py
    api_update.py
    read_replay.py
    database.py
    gamePlay.json
    set_rules.json
    example_vars.py
    Results/
```

The folder contains a packaged Windows Python app with many bundled dependencies. The authored Observer files relevant to Arena Coach are the Python files listed above, `gamePlay.json`, `set_rules.json`, README documentation, API examples, and sample output files in `Results/`.

The GUI Python source was not present. The GUI entry appears to be the compiled `schrodingersObserver_GUI/schrodingersObserver.exe`. Therefore, GUI button behavior below is partly from README documentation and partly inferred from the evaluation engine modules.

## Main Entry Points

- GUI app: `schrodingersObserver_GUI/schrodingersObserver.exe`
- Live evaluation engine: `schrodingersObserver_GUI/evaluator.py`
  - Instantiates the Echo VR API client at `evaluator.py:88`.
  - Polls live game state in `main()` at `evaluator.py:4003`.
  - Fetches snapshots with `echovr_api.fetch_state_data()` at `evaluator.py:4024`.
- Speed-only evaluator: `schrodingersObserver_GUI/SpeedReport_REV5.py`
  - Also instantiates the Echo VR API client at `SpeedReport_REV5.py:16`.
  - Fetches snapshots at `SpeedReport_REV5.py:1145`.
- Streaming output API: `schrodingersObserver_GUI/api_update.py`
  - Flask app that exposes the current processed Observer JSON on `/`.
  - Default port is `7777`.
- Live processed JSON updater: `schrodingersObserver_GUI/live_update.py`
  - Converts raw Echo VR snapshots plus Observer evaluation state into the streamed `live_json`.
- Replay reader: `schrodingersObserver_GUI/read_replay.py`
  - Opens `.echoreplay` zip files, extracts the contained text, and parses embedded JSON snapshots.
- Replay recorder: `schrodingersObserver_GUI/replay_api.exe`
  - Source is not present.
  - README documents HTTP control endpoints.

## Start Logging Flow

README behavior:

- The GUI `Start` button starts an active read.
- It attempts to connect to the Echo VR API.
- If connection fails, the GUI reports it in the Error tab.
- If enabled, replay recording is started separately and has priority over live evaluation.

Confirmed evaluation-engine flow in `evaluator.py`:

1. `main()` initializes `game_info`, sets up `logging.basicConfig(...)`, prepares file metadata, and enters an infinite poll loop.
2. Each loop calls `echovr_api.fetch_state_data()`.
3. Each snapshot is passed to `evaluate_structure(current_status, game_info)`.
4. During `playing` or `round_start`, Observer marks the game live.
5. When a new file is needed, `create_new_file()` creates the next dated file path under the configured results directory.
6. `write_file()` lazily writes the CSV header on first event:

```csv
Round,Time,Name,Catagory,Points,Comment
```

7. Events are derived by comparing consecutive API snapshots and internal state, then appended as CSV rows.

Arena Coach should rebuild this as:

- User clicks Start Logging.
- Arena Coach opens its own session object.
- Poll `http://<configured-host>:6721/` directly with Arena Coach-owned HTTP code.
- Write each raw snapshot as JSON Lines to `ArenaCoach/logs/raw/<session>.jsonl`.
- Add Arena Coach capture metadata to each line, especially wall-clock `captured_at`, monotonic sequence number, and source URL.
- Derive events later from the raw snapshot log. Do not depend on Observer's evaluator module.

## Stop Logging Flow

README behavior:

- The GUI `Stop` button can stop both live reads and replay reads.
- Evaluation files are also saved if the API disconnects, for example when the user leaves the lobby.
- Stopping can pause briefly if Observer multi-threading is enabled.

Confirmed evaluation-engine flow:

- On `ConnectionError` or JSON decode error, if `game_info["game_live"]` is true, Observer calls `log_player_performance(game_info)` before retrying.
- On `game_status == "pre_match"` after a live game, Observer marks the game no longer live and calls `log_player_performance(game_info)`.
- On `game_status == ""` or `post_match` after `round_over`, Observer logs end-of-round data.
- `log_player_performance()` joins the helper thread if needed, writes round summaries, stack logs, brawl logs, and final evaluation output.

Replay-control flow from README:

- `replay_api.exe` can run its own API, default port `7770`.
- `GET /run/?running=False` stops replay recording cleanly.
- `GET /end/?stop=True` breaks the replay into a new round.

Arena Coach should rebuild this as:

- User clicks Stop Logging.
- Set the Arena Coach capture loop stop event.
- Finish the JSONL file cleanly.
- Store session metadata: start time, stop time, detected players, detected teams, final score if available, game/session IDs, and raw snapshot count.
- Do not use Observer's replay API executable.

## Game Connection Method

Observer uses the Echo VR local HTTP API through the third-party `echovr_api` Python package:

```python
echovr_api = echovr_api.api.API(base_url="http://127.0.0.1:6721")
current_status = echovr_api.fetch_state_data()
```

The settings file `gamePlay.json` contains:

- `ip_address`: default `127.0.0.1`
- `api_port`: default `7777` for Observer's own processed streaming API, not the Echo VR game API
- `replay_port`: default `7770`
- `stream_api`: default `true`
- `stream_rate`: default `0.5`
- `fps`: default `30`
- `script_delay`: default `1`
- `retry_delay`: default `1000`

Important distinction:

- Echo VR game API: `http://127.0.0.1:6721/`
- Observer processed output API: Flask API on port `7777`
- Replay recorder control API: documented default port `7770`

Arena Coach should connect to the Echo VR game API directly. It should not read Observer's processed API, because that would make Arena Coach dependent on Observer at runtime.

## Data Source

Primary live data source:

- Repeated JSON snapshots from the Echo VR local API.
- Observer polls this source and compares snapshots to infer events.

Replay data source:

- `.echoreplay` files are zip files containing text with embedded JSON snapshots.
- `read_replay.py` extracts the zip to `usable_replay/`, reads each line, trims from the first `{` to a tab delimiter if present, then `json.loads(...)` each snapshot.

Arena Coach MVP should use live polling first and save raw snapshots. Replay ingestion can be added later by implementing Arena Coach's own `.echoreplay` reader based on the documented concept.

## Raw Echo API Fields Observed

The raw snapshots used by Observer include these top-level fields:

- `sessionid`
- `sessionip`
- `game_status`
- `game_clock_display`
- `game_clock`
- `match_type`
- `map_name`
- `client_name`
- `orange_points`
- `blue_points`
- `private_match`
- `tournament_match`
- `blue_team_restart_request`
- `orange_team_restart_request`
- `last_score`
- `teams`
- `disc`
- `possession`

`game_status` values referenced in code and samples:

- `pre_match`
- `round_start`
- `playing`
- `score`
- `round_over`
- `post_match`
- `sudden_death`
- `pre_sudden_death`
- `post_sudden_death`
- empty string

`disc` fields observed:

- `position`
- `forward`
- `left`
- `up`
- `velocity`
- `bounce_count`

`last_score` fields observed:

- `disc_speed`
- `team`
- `goal_type`
- `point_amount`
- `distance_thrown`
- `person_scored`
- `assist_scored`

Team fields observed:

- `teams[0]` is treated as Blue.
- `teams[1]` is treated as Orange.
- `teams[*].team` contains the team label, for example `BLUE TEAM`.
- `teams[*].players` contains active players when present.
- `teams[*].possession`
- `teams[*].stats`

Team stats observed:

- `points`
- `possession_time`
- `interceptions`
- `blocks`
- `steals`
- `catches`
- `passes`
- `saves`
- `goals`
- `stuns`
- `assists`
- `shots_taken`

Player fields observed:

- `name`
- `userid`
- `playerid`
- `number`
- `level`
- `ping`
- `stats`
- `stunned`
- `blocking`
- `invulnerable`
- `possession`
- `holding_left`
- `holding_right`
- `velocity`
- `head`
- `body`
- `lhand`
- `rhand`

Player stats observed:

- `possession_time`
- `points`
- `saves`
- `goals`
- `stuns`
- `passes`
- `catches`
- `steals`
- `blocks`
- `interceptions`
- `assists`
- `shots_taken`

Timestamp fields:

- Raw API provides game time as `game_clock` and display time as `game_clock_display`.
- Observer event logs use round number plus `game_clock_display` as `Time`.
- Observer output filenames include `date.today().strftime("%y_%m_%d_")`.
- No durable wall-clock event timestamp was found in Observer event rows.

Arena Coach should add wall-clock `captured_at` timestamps during capture while preserving API `game_clock` and `game_clock_display`.

## Observer Output Formats

### Evaluation Event Log

Sample file:

```text
schrodingersObserver_GUI/Results/25_11_11_Game_1_evaluation.csv
```

Header:

```csv
Round,Time,Name,Catagory,Points,Comment
```

Example rows:

```csv
1,00:16.99,defmytones,entry,0,defmytones entered game at 00:16.99 seconds
1,00:16.99,WALbigboy - (0),steals,+1,WALbigboy - (0) made a steal
1,00:15.94,ryann,shot,+1,ryann scored a 2 point goal from 4.517776 meters away at 16.676273 m/s on an open net.
```

Notes:

- The field is misspelled as `Catagory` in Observer.
- These rows are already processed evaluation events, not raw game snapshots.
- Events are score/evaluation-oriented and may contain derived coaching grades.

### Evaluation Result CSV

Sample file:

```text
schrodingersObserver_GUI/Results/25_11_11_Game_1_evaluation_result.csv
```

This is a round/category score summary by player and team. It is useful as a reference for later stats views, but not required for Arena Coach MVP raw capture.

### Round Average JSON

Sample file:

```text
schrodingersObserver_GUI/Results/25_11_11_Game_1_evaluation_result_rounds_plus_avg.json
```

This summarizes each player by category and round average.

### Stack Log

Sample file:

```text
schrodingersObserver_GUI/Results/25_11_11_Game_1_stack_log.csv
```

Header:

```csv
Name,Partner,Number of joust, Seconds,
```

### Brawl Log

Sample file:

```text
schrodingersObserver_GUI/Results/25_11_11_Game_1_brawl_log.csv
```

Header:

```csv
Name,Opponent,Stunned,Stunned By,Blocked,Blocked By,
```

### Speed Result

Sample file:

```text
schrodingersObserver_GUI/Results/25_11_11_Game_1_speed_result.csv
```

This records joust timing and velocity values per player.

### Processed Streaming JSON

`live_update.py` creates `live_json` with fields like:

- `sessionid`
- `sessionip`
- `game_clock_display`
- `game_clock`
- `last_score`
- `orange_score`
- `blue_score`
- `game_state`
- `blue_joust`
- `orange_joust`
- `disc`
- `poss`
- `team`
- `player_info`

`api_update.py` can expose this processed JSON over Flask. Arena Coach should not depend on this processed stream, but the shape is useful for designing Arena Coach's own session preview.

## Available Event Types

Observer's evaluation CSV categories found in sample output:

- `entry`
- `Exit`
- `shot`
- `steals`
- `stuns`
- `goalie`
- `possession`
- `poss_time`
- `clear`
- `change_time`
- `man_coverage`
- `lane_coverage`
- `stack_control`

Raw API stat counters can support normalized Arena Coach event types:

- `goal`: score change plus `last_score.person_scored`
- `assist`: `last_score.assist_scored` when not `[INVALID]`, plus assist stat deltas
- `save`: player `stats.saves` delta
- `stun`: player `stats.stuns` delta and/or `stunned` transitions
- `steal`: player `stats.steals` delta
- `pass`: player `stats.passes` delta
- `catch`: player `stats.catches` delta
- `shot`: player `stats.shots_taken` delta and score/miss logic
- `interception`: player `stats.interceptions` delta
- `possession`: player/team possession transitions, `player.possession`, `teams[*].possession`, top-level `possession`
- `score_update`: `blue_points` or `orange_points` changes
- `player_join`: player name/userid appears in team rosters
- `player_leave`: player disappears from team rosters
- `match_start`: `game_status` transition into `round_start` or `playing`
- `match_end`: `game_status` transition into `post_match` or return to `pre_match` after live play

Events that require Arena Coach-owned inference:

- `turnover`: possession changes between teams after one team had control.
- `clear`: Observer infers this from disc location, possession, bounce/recovery state, and team clear rating. Arena Coach can implement a simple first-pass heuristic later, then preserve uncertain cases as unknown or metadata.
- `block`: Observer tracks blocks as part of stuns/brawl info; Arena Coach may store as metadata or add a normalized type later.

Unknown events must be preserved. If a raw snapshot produces an unrecognized transition, Arena Coach should write an `unknown` normalized event with the raw JSON/text and metadata instead of dropping it.

## What Arena Coach Needs To Rebuild

Minimum standalone rebuild for the MVP:

1. Echo API client
   - Implement Arena Coach-owned HTTP polling for `http://<host>:6721/`.
   - Configurable host, port, timeout, and poll interval.
   - No runtime import of `echovr_api`.

2. Capture loop
   - Start/stop session from Arena Coach UI.
   - Background thread or async task controlled by Arena Coach.
   - One raw JSONL file per logging session under `ArenaCoach/logs/raw/`.
   - Each line should include:
     - `sequence`
     - `captured_at`
     - `source`
     - `snapshot`

3. Session metadata
   - `started_at`
   - `stopped_at`
   - `sessionid`
   - `sessionip`
   - `match_type`
   - `map_name`
   - final `blue_score`
   - final `orange_score`
   - detected teams
   - detected players with aliases, userids, playerids, observed teams
   - raw snapshot count
   - capture errors

4. Raw parser
   - Read Arena Coach JSONL snapshots.
   - Compare consecutive snapshots.
   - Emit normalized events:
     - `event_id`
     - `match_id`
     - `timestamp`
     - `event_type`
     - `actor_name`
     - `target_name`
     - `assist_name`
     - `team`
     - `value`
     - `raw_text`
     - `metadata_json`

5. Event derivation rules
   - Score changes from `blue_points`, `orange_points`, and `last_score`.
   - Player/team stat deltas from `teams[*].players[*].stats`.
   - Join/leave from roster diffs.
   - Start/end from `game_status` transitions.
   - Possession from player/team possession fields.
   - Preserve unknown transitions.

6. Manual identity mapping
   - Use player `name` as the initial alias.
   - Store `userid` and `playerid` as metadata when present, but do not rely only on them because aliases and local capture conditions may vary.

## What Not To Copy

Do not copy or depend on:

- `schrodingersObserver.exe`
- `replay_api.exe`
- Observer's bundled Python environment
- Observer's `database.py` or MySQL design
- Observer's `echovr_api` package
- Observer's generated `Results/` files at runtime
- Observer's scoring/evaluation engine as application code

Concepts that are safe to rebuild:

- Polling the Echo VR local API.
- Creating one log per session.
- Comparing consecutive snapshots to derive events.
- Preserving raw snapshots.
- Using event categories as parser inspiration.
- Using `last_score` and player stat deltas to infer goals, assists, saves, stuns, steals, passes, catches, interceptions, and shots.

## Open Questions For Later Phases

- The GUI source is missing, so exact internal Start/Stop button implementation cannot be copied or line-referenced. Arena Coach should implement its own UI flow.
- Observer replay recording source is missing. Arena Coach should prioritize live JSONL capture first.
- Echo Arena/Echo VR API availability depends on the user's local game/runtime setup. Arena Coach startup must not require it.
- Some high-level events like `clear`, `turnover`, and detailed pass quality require inference beyond simple stat deltas. Implement simple conservative rules first and store raw metadata for later refinement.
