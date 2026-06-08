# Advanced Inference Research

This document records what the current Arena Coach raw JSONL snapshots reliably contain, based on real captures in `logs/raw` from May 25-31, 2026.

## Snapshot Cadence

- Observed sample interval across recent real logs: about `0.5005s`
- Effective sample rate: about `2 snapshots/second`

This is good enough for conservative short-window inference, but not good enough for frame-perfect intent reconstruction.

## Confirmed Raw Fields

## Player fields consistently present

Observed on real logs:

- `head`
- `body`
- `lhand`
- `rhand`
- `velocity`
- `holding_left`
- `holding_right`
- `stunned`
- `blocking`
- `possession`
- `invulnerable`
- `level`
- `number`
- `ping`
- `userid`
- `playerid`
- `stats`

Notes:

- `head.position` and `body.position` use `position`
- `lhand` and `rhand` use `pos`
- `velocity` is a 3-vector
- `userid` is the stable identity signal
- `playerid` still appears session-local / slot-like and should not be used as identity evidence

## Disc fields consistently present

Observed on real logs:

- `position`
- `velocity`
- `forward`
- `left`
- `up`
- `bounce_count`

These were present in sampled snapshots from real logs and appear usable for coarse trajectory analysis.

## Score / possession / match context fields consistently present

- `possession`
- `teams[*].possession`
- `last_score`
- `game_clock`
- `game_clock_display`
- `game_status`
- `blue_points`
- `orange_points`
- `blue_round_score`
- `orange_round_score`
- `total_round_count`
- `match_type`
- `private_match`
- `tournament_match`

Also observed:

- `last_throw`
- `pause`
- `rules_changed_at`
- `rules_changed_by`
- shoulder button flags

## Field Quality Notes

## Team side / goal direction

Partially inferable.

What worked:

- In live snapshots, blue and orange team body-position centroids are often separated strongly on the `z` axis.
- In some matches, blue players cluster at negative `z` while orange players cluster at positive `z`.

What did not look perfectly reliable:

- Lobby / pre-match / casual private logs can contain weak or noisy team separation.
- Some captures start outside a stable live context.

Conclusion:

- Court orientation is not direct.
- Team half inference is reasonable only during live play when team centroids are clearly separated.
- Coverage, clear, and transition metrics should fall back to low confidence or skip when orientation is weak.

## Target data for stats

Current direct evidence:

- `last_score` directly names scorer and assist
- snapshot stat counters directly expose deltas for:
  - shots
  - saves
  - stuns
  - steals
  - interceptions
  - passes
  - catches
  - assists
  - goals
  - blocks

Current missing direct evidence:

- no reliable direct target for stun
- no reliable direct target for steal
- no reliable direct intended receiver for pass
- no guaranteed direct shooter/saver relationship beyond timing + geometry

Conclusion:

- shots/saves/stuns/steals/interceptions are mostly stat-delta based
- relationships between two players usually have to be inferred

## Positional Continuity Assessment

## Pass trajectory

- Disc position and velocity exist
- Player positions exist
- Sample rate is only about 2 Hz

Reliability:

- medium for coarse pass direction and likely receiver
- not reliable for exact throw intent in crowded play

## Catch attempt

- Teammate proximity near disc end point is usable
- catch stat delta helps

Reliability:

- medium when a likely receiver is clearly nearest
- low when multiple teammates are nearby

## Clear

- Requires half-court inference plus disc movement
- Disc vectors are available

Reliability:

- medium when team orientation is stable
- low / skip when orientation is unclear

## Coverage

- Requires scorer, assister, defenders, and lane geometry
- Coordinates exist, but sample rate is still coarse

Reliability:

- low to medium
- should stay neutral and explanation-based
- should avoid blame-heavy language

## Transition timing

- Player positions over time exist
- Possession changes exist

Reliability:

- medium for coarse "time to recover / time to push" windows
- not precise enough for frame-perfect movement judgments

## Shot / save relationship

- shots are visible via stat deltas
- saves / blocks are visible via stat deltas
- disc and player positions exist
- last_score directly identifies made goals

Reliability:

- high for "shot saved" when shot delta is followed closely by save delta and no goal
- medium for "stuffed / blocked shot" unless geometry is very clear

## Direct vs Inferred Reliability

## Direct

These are directly available or already directly derived from raw fields:

- scorer from `last_score.person_scored`
- assist from `last_score.assist_scored`
- shot stat delta
- save stat delta
- block stat delta
- steal stat delta
- interception stat delta
- pass stat delta
- catch stat delta
- possession flags
- player/body/disc coordinates

## High-confidence inferred

- `turnover` when possession changes to the opponent with a clear before/after possessor and no shot/goal/save sequence explaining it
- `shot_saved` when shot delta is followed by save delta inside a short window and no goal occurs
- `initiator` when pass -> assister catch/possession -> assist -> goal chain is clear

## Medium-confidence inferred

- `intercepted_pass`
- `missed_pass`
- `missed_catch`
- `blocked_shot`
- `stuffed_shot`
- `missed_shot`
- `clear` when orientation is stable
- `pass_to_covered_teammate`
- coarse transition timing

## Low-confidence / review-needed

- `shooter_uncovered`
- `lane_coverage_failure`
- any "coverage failure" language
- any event that depends on exact intended receiver

## Not currently reliable enough for strong claims

- exact intended receiver on every pass
- direct "who stunned me" without target data
- direct "who stole from me" without target data
- precise lane blame in crowded sequences
- exact team orientation in every private/lobby segment

## Phase 9 Guidance

Recommended implementation posture:

- keep basic events unchanged
- store all advanced outputs separately in `advanced_events`
- always attach:
  - confidence
  - confidence score
  - evidence
  - source sequences / source events
  - explanation
  - directness
- skip rather than overclaim when coordinates or possession continuity are weak
