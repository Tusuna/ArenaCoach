# Arena Coach User Guide

This guide explains how to use Arena Coach as a player or tester. It focuses on what the app does, what the buttons mean, and how stats are produced at a practical level.

## 1. What Arena Coach Does

Arena Coach records Echo API snapshots, saves raw match logs, turns those logs into reviewable matches, lets you confirm who each player was, and then builds personal and match-level stats from finalized data.

The normal workflow is:

1. Create or select your profile.
2. Test the Echo connection.
3. Start Logging before a match.
4. Stop Logging after the match.
5. Process Match for Review.
6. Confirm players, teams, self, AFK, and match context.
7. Finalize Match.
8. Infer Selected Match when you want advanced stats.
9. Review results in Match History, Stats Preview, Advanced Summary, and Compare Players.

## 2. Main Tabs

### Live Capture

Use this tab when recording a new match.

- `Test Connection`: checks whether Arena Coach can see the local Echo API.
- `Start Logging`: begins recording raw snapshots.
- `Stop Logging`: ends the capture session.
- `Process Match for Review`: parses the newest raw log, saves it if needed, and opens review.
- `Advanced: Preview Latest Log`: technical preview only.
- `Advanced: Import Latest Log`: saves the newest raw log directly to match history.

### Match Review

Use this tab to confirm player identity and match context before trusting the stats.

Guided Review walks through:

1. Match Summary
2. Identify Yourself
3. Assign Players
4. Team Confirmation
5. Finalize Checklist

For each detected player you can:

- link them to an existing saved player
- create a new saved player
- mark them as guest/unknown
- mark one player as yourself
- correct their team
- mark them as AFK if needed

### Match History

Use this tab to inspect stored matches.

It shows:

- score
- round record
- quality labels
- private subtype
- team scoreboards
- round summaries
- event timeline
- advanced match detail if inference has been run

### Players

Use this tab to manage your local player database.

You can:

- search players by canonical name or alias
- create a new player
- edit a saved player name or notes
- add aliases
- add known user IDs through alias entry

This is the best place to clean up names after several matches.

### Profile

Use this tab to manage your local user profile.

Your profile stores:

- display name
- main Echo name
- active profile selection

The main Echo name helps Arena Coach suggest which detected player was you.

### Stats Preview

This is the quick high-level view.

It shows:

- finalized match counts
- competitive-eligible counts
- win/loss/tie summary
- trends
- rivals
- teammates
- playstyle guess
- quality warnings

### Advanced Summary

This is your detailed local-user breakdown.

It focuses on the current self player for the active profile and shows:

- category radar / grade summary
- Shooting
- Speed
- Possession
- Offense
- Defense
- Passing
- transition timing
- event totals
- recent advanced matches

The category cards below the radar show the actual inputs used to build the score.

### Compare Players

This lets you compare any two saved players side by side.

Use it to compare:

- category scores
- core averages
- core totals
- advanced category detail
- shared match context

If numbers differ from another tab, check the filters. Comparison and Advanced Summary each have their own filter state.

### Settings

Use this tab to manage:

- Echo API host, port, and path
- poll interval
- request timeout
- raw log directory
- guided review mode
- export/import options
- backups

Leave Echo API settings alone unless you really need to change them.

### Debug Logs

This is the technical activity log for the current app session.

Use it when something feels wrong and you need the message trail.

## 3. Actions Sidebar

The left sidebar gives you the fastest common actions:

- `Test Connection`
- `Start Logging`
- `Stop Logging`
- `Process Latest Match`
- `Advanced: Preview Latest Log`
- `Advanced: Import Latest Log`
- `Review Selected Match`
- `Finalize Selected Match`
- `Infer Selected Match`
- `Export My Data`
- `Import Shared Data`
- `Show Tutorial`

`Export My Data` creates a clean zip in `exports/` and opens File Explorer with that file selected.

`Import Shared Data` opens a file picker so you can choose another Arena Coach export zip. The import is unpacked into `imports/` and does not merge automatically into your live database.

## 4. How to Get Advanced Stats

Advanced stats are not automatic just because a match was captured.

To get advanced stats for a match:

1. Capture the match.
2. Process it.
3. Complete review.
4. Finalize the match.
5. Select the match.
6. Press `Infer Selected Match`.

After that, advanced views can use inferred events and advanced player metrics from that match.

## 5. What the Stats Mean

Arena Coach uses two layers of match data.

### Base stats

These come from Echo snapshots and direct match counters where available:

- points
- goals
- assists
- saves
- stuns
- steals
- passes
- catches
- shots
- interceptions
- blocks
- possession time

### Advanced stats

These are inferred from snapshot-to-snapshot changes and match context. Examples include:

- guarded 2s / guarded 3s
- open 2s / open 3s
- missed shots
- shots saved by goalie
- clears
- turnovers
- inferred interceptions
- open-for-pass samples
- passes to open receiver
- passes to covered receiver
- lane blocks
- lane coverage failures
- goalie coverage
- average time to offense
- average time to defense

These advanced stats are best treated as strong analysis signals, not absolute ground truth.

## 6. Category Scores

Advanced Summary groups advanced data into six categories:

- `Shooting`
- `Speed`
- `Possession`
- `Offense`
- `Defense`
- `Passing`

These scores are built from filtered match samples. The cards directly under each category show the inputs and rates used to create that score.

Important:

- Confidence filters matter.
- Match-type filters matter.
- AFK inclusion matters.
- Competitive-only filtering matters.

If the score changes, usually the sample or filters changed.

## 7. Match Quality and AFK

Arena Coach keeps all matches, but not every match is treated as competitive-quality.

Common reasons a match is marked low quality:

- fewer than 6 active non-AFK players
- no confirmed self player
- missing or strange final score
- team switch issues
- unfinalized review

AFK is stored as suspected, not proven.

Use the AFK toggle when a player was present but not meaningfully participating. This keeps the history but prevents those rows from distorting competitive evaluation by default.

## 8. Match Types

Arena Coach tracks:

- Public
- Private
- Tournament
- Unknown

Private matches can also be tagged as:

- PUG
- Scrimmage
- Official
- Casual
- Unknown

This changes filtering and context later. It does not delete or hide the match.

## 9. Round Scores vs Total Points

Multi-round private matches can store both:

- total points
- round record

Those are not always the same story if points carry over between rounds.

Arena Coach may show both:

- `Points: Blue 106 - Orange 107`
- `Rounds: Blue 5 - Orange 4`

If those tell different winner stories, Arena Coach will warn you.

## 10. Updating Players Correctly

Best practice:

1. Create canonical players only when you actually know who someone is.
2. Add aliases when they change names.
3. Keep known user IDs attached to the right player.
4. Use Guest/Unknown instead of guessing.
5. Clean up the Players tab after several matches if needed.

User ID is the strong identity signal.

Echo `playerid` is not treated as stable identity.

## 11. Exports, Imports, and Backups

### Export

Use `Export My Data` when:

- sharing your current state for debugging
- sending your match database back for planning
- freezing a clean snapshot before a big change

Exports can include:

- database copy
- raw logs
- debug logs
- unfinalized matches
- advanced analysis

### Import

Imports are unpacked into `imports/`.

They are kept separate so you can inspect them safely instead of silently merging them into your main data.

### Backups

Use backups before:

- updating the app
- large player cleanups
- testing risky data changes

## 12. Best Short Workflow

If you just want the cleanest normal use path:

1. Open Arena Coach.
2. Confirm the active profile.
3. Open Echo and press `Test Connection`.
4. Press `Start Logging`.
5. Play the match.
6. Press `Stop Logging`.
7. Press `Process Match for Review`.
8. Finish Guided Review.
9. Press `Finalize Match`.
10. Press `Infer Selected Match`.
11. Check Match History, Stats Preview, and Advanced Summary.

## 13. Known Limits

- Advanced inferred events are still inference, not direct game truth.
- Some direct personal target claims still depend on reliable target linkage.
- AFK is suspicion, not proof.
- Low sample advanced scores can swing hard.
- Public, private, and tournament context is useful, but more real edge cases still help tune it.
