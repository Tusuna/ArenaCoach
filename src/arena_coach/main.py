"""Command-line entry point for Arena Coach."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Optional, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arena_coach import __app_name__
from arena_coach.config import AppConfig, ConfigError, load_config
from arena_coach.database import connect_database, initialize_database
from arena_coach.log_importer import apply_afk_detection_to_match, import_raw_log
from arena_coach.logging_system.capture_session import CaptureSession
from arena_coach.logging_system.echo_api_client import EchoApiClient
from arena_coach.match_context import PRIVATE_MATCH_TYPES, normalize_private_match_type, private_match_type_label, round_record_warning
from arena_coach.match_mapping import MappingError
from arena_coach import match_mapping
from arena_coach.models import ConnectionStatus, SessionMetadata
from arena_coach.inference import AdvancedInferenceService
from arena_coach.parsing.event_deriver import derive_events
from arena_coach.parsing.raw_log_reader import read_raw_log
from arena_coach.repositories import matches_repo, players_repo, profiles_repo
from arena_coach.services.match_display import build_match_display_name
from arena_coach.services.data_exchange_service import DataExchangeService
from arena_coach.services.stats_service import DatabaseStatsService
from arena_coach.stats.stat_filters import StatsFilter


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    initialize_database(config.database_path)

    command = args.command or "status"
    try:
        if command == "status":
            return run_status(config)
        if command == "test-connection":
            return run_test_connection(config)
        if command == "start":
            return run_start(config, args.duration_seconds)
        if command == "parse-log":
            return run_parse_log(args.path, args.print_events)
        if command == "import-log":
            return run_import_log(config, args.path)
        if command == "profile":
            return run_profile_command(config, args)
        if command == "players":
            return run_players_command(config, args)
        if command == "matches":
            return run_matches_command(config, args)
        if command == "stats":
            return run_stats_command(config, args)
        if command == "infer":
            return run_infer_command(config, args)
        if command == "advanced":
            return run_advanced_command(config, args)
        if command == "data":
            return run_data_command(config, args)
        if command == "gui":
            return run_gui()
    except MappingError as exc:
        print(f"Mapping error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"Database error: {exc}", file=sys.stderr)
        return 1

    parser.print_help()
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arena-coach", description="Arena Coach CLI")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional path to a local Arena Coach config JSON file.",
    )

    commands = parser.add_subparsers(dest="command")

    commands.add_parser("status", help="Show startup status and Echo API availability.")
    commands.add_parser("test-connection", help="Test the Echo API connection.")
    commands.add_parser("gui", help="Launch the PySide6 desktop GUI.")
    data = commands.add_parser("data", help="Export, import, and back up Arena Coach data.")
    data_commands = data.add_subparsers(dest="data_command")
    data_export = data_commands.add_parser("export", help="Create a tester export zip.")
    data_export.add_argument("--include-raw-logs", action="store_true")
    data_export.add_argument("--include-debug-logs", action="store_true")
    data_export.add_argument("--exclude-unfinalized", action="store_true")
    data_export.add_argument("--exclude-advanced-events", action="store_true")
    data_import = data_commands.add_parser("import", help="Extract an external Arena Coach export zip.")
    data_import.add_argument("path", type=Path)
    data_commands.add_parser("list-imports", help="List extracted external tester imports.")
    data_backup = data_commands.add_parser("backup", help="Create a manual database backup.")
    data_backup.add_argument("--reason", default="manual")

    start = commands.add_parser("start", help="Start raw Echo API snapshot logging.")
    start.add_argument(
        "--duration-seconds",
        type=float,
        default=None,
        help="Stop capture automatically after this many seconds.",
    )

    parse_log = commands.add_parser("parse-log", help="Parse a raw JSONL log without saving it.")
    parse_log.add_argument("path", type=Path, help="Raw JSONL log path.")
    parse_log.add_argument("--print-events", action="store_true", help="Print the first 50 normalized events.")

    import_log = commands.add_parser("import-log", help="Import a raw JSONL log as an unfinalized match.")
    import_log.add_argument("path", type=Path, help="Raw JSONL log path.")

    profile = commands.add_parser("profile", help="Manage local user profiles.")
    profile_commands = profile.add_subparsers(dest="profile_command")
    profile_commands.add_parser("list", help="List local profiles.")
    profile_commands.add_parser("show", help="Show the active profile.")

    profile_create = profile_commands.add_parser("create", help="Create a local profile.")
    profile_create.add_argument("--display-name", required=True)
    profile_create.add_argument("--echo-name", default=None)

    profile_set_active = profile_commands.add_parser("set-active", help="Set the active local profile.")
    profile_set_active.add_argument("profile_id", type=int)

    profile_update = profile_commands.add_parser("update", help="Update the active local profile.")
    profile_update.add_argument("--display-name", default=None)
    profile_update.add_argument("--echo-name", default=None)

    players = commands.add_parser("players", help="Manage canonical players and aliases.")
    players_commands = players.add_subparsers(dest="players_command")
    players_commands.add_parser("list", help="List canonical players.")

    players_create = players_commands.add_parser("create", help="Create a canonical player.")
    players_create.add_argument("--name", required=True)

    players_aliases = players_commands.add_parser("aliases", help="List aliases for a canonical player.")
    players_aliases.add_argument("player_id", type=int)

    matches = commands.add_parser("matches", help="Review and finalize imported matches.")
    matches_commands = matches.add_subparsers(dest="matches_command")
    matches_commands.add_parser("list", help="List imported matches.")

    matches_show = matches_commands.add_parser("show", help="Show a match summary.")
    matches_show.add_argument("match_id", type=int)

    matches_review = matches_commands.add_parser("review", help="Review mapping state for a match.")
    matches_review.add_argument("match_id", type=int)

    matches_map = matches_commands.add_parser("map-player", help="Map a match alias to a canonical player.")
    matches_map.add_argument("match_id", type=int)
    matches_map.add_argument("--alias", required=True)
    matches_map.add_argument("--player-id", required=True, type=int)

    matches_create = matches_commands.add_parser(
        "create-player-from-alias",
        help="Create a canonical player from a match alias and map it.",
    )
    matches_create.add_argument("match_id", type=int)
    matches_create.add_argument("--alias", required=True)
    matches_create.add_argument("--canonical-name", required=True)

    matches_mark_self = matches_commands.add_parser("mark-self", help="Mark exactly one match alias as self.")
    matches_mark_self.add_argument("match_id", type=int)
    matches_mark_self.add_argument("--alias", required=True)

    matches_set_team = matches_commands.add_parser("set-team", help="Correct a match alias team.")
    matches_set_team.add_argument("match_id", type=int)
    matches_set_team.add_argument("--alias", required=True)
    matches_set_team.add_argument("--team", required=True, choices=sorted(match_mapping.VALID_TEAMS))

    matches_confirm_guest = matches_commands.add_parser(
        "confirm-guest",
        help="Confirm a match alias as guest/unmapped for now.",
    )
    matches_confirm_guest.add_argument("match_id", type=int)
    matches_confirm_guest.add_argument("--alias", required=True)

    matches_finalize = matches_commands.add_parser("finalize", help="Finalize a reviewed match.")
    matches_finalize.add_argument("match_id", type=int)

    matches_afk = matches_commands.add_parser("detect-afk", help="Refresh AFK markers from a match raw log.")
    matches_afk.add_argument("match_id", type=int)

    matches_private_type = matches_commands.add_parser(
        "set-private-type",
        help="Set the private match subtype for a reviewed match.",
    )
    matches_private_type.add_argument("match_id", type=int)
    matches_private_type.add_argument("--type", required=True, choices=PRIVATE_MATCH_TYPES)

    stats = commands.add_parser("stats", help="Show aggregated stats from finalized matches.")
    stats_commands = stats.add_subparsers(dest="stats_command")

    stats_summary = stats_commands.add_parser("summary", help="Show active profile summary stats.")
    _add_stats_filter_arguments(stats_summary)

    stats_trends = stats_commands.add_parser("trends", help="Show last-5 vs previous-5 trend stats.")
    _add_stats_filter_arguments(stats_trends)

    stats_matchups = stats_commands.add_parser("matchups", help="Show player-vs-player matchup stats.")
    _add_stats_filter_arguments(stats_matchups)

    stats_teammates = stats_commands.add_parser("teammates", help="Show teammate synergy stats.")
    _add_stats_filter_arguments(stats_teammates)

    stats_quality = stats_commands.add_parser("quality", help="Show match quality classification.")
    _add_stats_filter_arguments(stats_quality)

    stats_player = stats_commands.add_parser("player", help="Show aggregate stats for one canonical player.")
    stats_player.add_argument("player_id", type=int)
    _add_stats_filter_arguments(stats_player)

    infer = commands.add_parser("infer", help="Run advanced inference using raw snapshots and base events.")
    infer_commands = infer.add_subparsers(dest="infer_command")
    infer_match = infer_commands.add_parser("match", help="Infer advanced events for one match.")
    infer_match.add_argument("match_id", type=int)
    infer_match.add_argument("--force", action="store_true", help="Replace existing advanced events for this match.")
    infer_latest = infer_commands.add_parser("latest", help="Infer advanced events for the latest match.")
    infer_latest.add_argument("--force", action="store_true", help="Replace existing advanced events for the latest match.")
    infer_all = infer_commands.add_parser("all-finalized", help="Infer advanced events for all finalized matches.")
    infer_all.add_argument("--force", action="store_true", help="Replace existing advanced events for finalized matches.")

    advanced = commands.add_parser("advanced", help="Inspect advanced inferred events.")
    advanced_commands = advanced.add_subparsers(dest="advanced_command")
    advanced_infer = advanced_commands.add_parser("infer", help="Infer advanced events for one match.")
    advanced_infer.add_argument("match_id", type=int)
    advanced_infer.add_argument("--force", action="store_true")

    advanced_summary = advanced_commands.add_parser("summary", help="Show advanced-event summary for a match.")
    advanced_summary.add_argument("match_id", type=int)
    _add_advanced_filter_arguments(advanced_summary)

    advanced_timeline = advanced_commands.add_parser("timeline", help="Show advanced-event timeline for a match.")
    advanced_timeline.add_argument("match_id", type=int)
    _add_advanced_filter_arguments(advanced_timeline)

    advanced_player = advanced_commands.add_parser("player", help="Show advanced inferred events for one player.")
    advanced_player.add_argument("player_id", type=int)
    _add_advanced_filter_arguments(advanced_player)

    advanced_all = advanced_commands.add_parser("all-finalized", help="Infer advanced events for all finalized matches.")
    advanced_all.add_argument("--force", action="store_true")

    return parser


def run_status(config: AppConfig) -> int:
    print_startup(config)
    status = build_client(config).test_connection()
    print(format_connection_status(status))
    return 0


def run_test_connection(config: AppConfig) -> int:
    print_startup(config)
    status = build_client(config).test_connection()
    print(format_connection_status(status))
    if status.ok and status.snapshot_keys:
        print(f"Snapshot keys: {', '.join(status.snapshot_keys)}")
    return 0


def run_start(config: AppConfig, duration_seconds: Optional[float]) -> int:
    print_startup(config)
    client = build_client(config)
    status = client.test_connection()
    print(format_connection_status(status))
    if not status.ok:
        print("Starting capture anyway; Arena Coach will preserve valid snapshots if the API becomes available.")

    session = CaptureSession(
        client=client,
        raw_log_dir=config.raw_log_dir,
        poll_interval_seconds=config.poll_interval_seconds,
    )
    metadata = session.start()
    print("Logging started")
    print(f"raw log file: {metadata.raw_log_path}")
    print(f"metadata file: {session.metadata_path}")

    started = time.monotonic()
    try:
        while session.is_running:
            if duration_seconds is not None and time.monotonic() - started >= duration_seconds:
                break
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("Stop requested")
    finally:
        metadata = session.stop()
        print_stop_summary(metadata)

    return 0


def run_parse_log(path: Path, print_events: bool = False) -> int:
    read_result = read_raw_log(path)
    derived = derive_events(read_result.records)

    print(f"raw file path: {Path(path).resolve()}")
    print(f"valid snapshots: {read_result.summary.valid_snapshots}")
    print(f"invalid lines: {read_result.summary.invalid_lines}")
    print(f"detected sessionid: {derived.detected_sessionid or 'none'}")
    print(f"detected map: {derived.detected_map_name or 'none'}")
    print(f"detected players: {_format_names(derived.detected_player_list())}")
    print(f"detected teams: {_format_teams(derived.detected_team_list())}")
    print(f"final score: blue={derived.latest_blue_score}, orange={derived.latest_orange_score}")
    print(f"normalized events: {len(derived.events)}")
    print_event_counts(derived.event_counts())

    if print_events:
        print("first events:")
        for event in derived.events[:50]:
            actor = f" actor={event.actor_name}" if event.actor_name else ""
            target = f" target={event.target_name}" if event.target_name else ""
            team = f" team={event.team}" if event.team else ""
            value = f" value={event.value:g}" if event.value is not None else ""
            print(f"  seq={event.sequence} type={event.event_type}{actor}{target}{team}{value}")

    return 0


def run_import_log(config: AppConfig, path: Path) -> int:
    result = import_raw_log(path, config.database_path)
    print(f"created match id: {result.match_id}")
    print(f"raw log path: {result.raw_log_path}")
    print(f"detected players: {_format_names(result.detected_players)}")
    print(f"detected teams: {_format_teams(result.detected_teams)}")
    if result.private_match_type:
        print(f"private match type: {private_match_type_label(result.private_match_type)}")
    print(f"score: blue={result.blue_score}, orange={result.orange_score}")
    if result.total_rounds_played:
        print(
            "round record: "
            f"blue={result.blue_round_wins}, orange={result.orange_round_wins}, total_rounds={result.total_rounds_played}"
        )
        print(f"points carry over: {_yes_no_unknown(result.points_carry_over)}")
    print_event_counts(result.event_counts)
    print(f"events saved: {result.events_saved}")
    print(f"match players saved: {result.match_players_saved}")
    print(f"match player stats saved: {result.match_player_stats_saved}")
    print(f"finalized: {str(result.finalized).lower()}")
    return 0


def run_gui() -> int:
    try:
        from arena_coach.gui.app import main as gui_main
    except ModuleNotFoundError as exc:
        if exc.name == "PySide6":
            print("PySide6 is not installed. Run: pip install -r requirements.txt", file=sys.stderr)
            return 1
        raise
    return gui_main()


def run_data_command(config: AppConfig, args: argparse.Namespace) -> int:
    service = DataExchangeService(config)
    if not args.data_command:
        print("data command required", file=sys.stderr)
        return 2
    if args.data_command == "export":
        result = service.export_data(
            include_raw_logs=bool(args.include_raw_logs),
            include_debug_logs=bool(args.include_debug_logs),
            include_unfinalized_matches=not bool(args.exclude_unfinalized),
            include_advanced_events=not bool(args.exclude_advanced_events),
        )
        print(f"export created: {result['export_path']}")
        print("manifest:")
        print(json.dumps(result["manifest"], indent=2))
        return 0
    if args.data_command == "import":
        result = service.import_data(args.path)
        print(f"imported to: {result['import_dir']}")
        print(f"database path: {result['database_path']}")
        print("manifest:")
        print(json.dumps(result["manifest"], indent=2))
        return 0
    if args.data_command == "list-imports":
        rows = service.list_imports()
        if not rows:
            print("imports: none")
            return 0
        print("imports:")
        for row in rows:
            manifest = row.get("manifest") or {}
            print(
                f"  {row['name']} | profile={manifest.get('profile_display_name', 'unknown')} | "
                f"created_at={manifest.get('created_at', 'unknown')} | db={row['database_path']}"
            )
        return 0
    if args.data_command == "backup":
        result = service.backup_database(reason=str(args.reason or "manual"))
        print(f"backup created: {result['backup_path']}")
        print(f"size bytes: {result['size_bytes']}")
        return 0
    print("data command required", file=sys.stderr)
    return 2


def run_stats_command(config: AppConfig, args: argparse.Namespace) -> int:
    if not args.stats_command:
        print("stats command required", file=sys.stderr)
        return 2

    service = DatabaseStatsService(config.database_path)
    filters = _build_stats_filters(args)

    if args.stats_command == "summary":
        _print_stats_summary(service.summary(filters), filters)
        return 0
    if args.stats_command == "trends":
        _print_trends(service.trends(filters), filters)
        return 0
    if args.stats_command == "matchups":
        _print_matchups(service.matchups(filters), filters)
        return 0
    if args.stats_command == "teammates":
        _print_teammates(service.teammates(filters), filters)
        return 0
    if args.stats_command == "quality":
        quality_filters = filters.with_updates(finalized_only=False)
        _print_quality(service.quality(quality_filters), quality_filters)
        return 0
    if args.stats_command == "player":
        _print_player_summary(service.player(args.player_id, filters), filters)
        return 0

    print("stats command required", file=sys.stderr)
    return 2


def run_infer_command(config: AppConfig, args: argparse.Namespace) -> int:
    service = AdvancedInferenceService(config.database_path)
    if not args.infer_command:
        print("infer command required", file=sys.stderr)
        return 2
    if args.infer_command == "match":
        result = service.infer_match(args.match_id, force=args.force)
        _print_infer_result(result)
        return 0
    if args.infer_command == "latest":
        result = service.infer_latest(force=args.force)
        _print_infer_result(result)
        return 0
    if args.infer_command == "all-finalized":
        payload = service.infer_all_finalized(force=args.force)
        print(f"matches processed: {payload['matches_processed']}")
        print(f"advanced events saved: {payload['total_advanced_events']}")
        for row in payload["matches"]:
            print(f"  match #{row['match_id']}: {sum(row['event_counts'].values())} events")
        return 0
    print("infer command required", file=sys.stderr)
    return 2


def run_advanced_command(config: AppConfig, args: argparse.Namespace) -> int:
    service = AdvancedInferenceService(config.database_path)
    if not args.advanced_command:
        print("advanced command required", file=sys.stderr)
        return 2
    if args.advanced_command == "infer":
        result = service.infer_match(args.match_id, force=args.force)
        _print_infer_result(result)
        return 0
    if args.advanced_command == "summary":
        payload = service.summary(
            args.match_id,
            min_confidence=args.min_confidence,
            event_type=args.event_type,
            player_id=args.player_id,
            include_low_confidence=args.include_low_confidence,
        )
        _print_advanced_summary(payload, args.match_id)
        return 0
    if args.advanced_command == "timeline":
        rows = service.timeline(
            args.match_id,
            min_confidence=args.min_confidence,
            event_type=args.event_type,
            player_id=args.player_id,
            include_low_confidence=args.include_low_confidence,
        )
        _print_advanced_timeline(rows)
        return 0
    if args.advanced_command == "player":
        payload = service.player(
            args.player_id,
            min_confidence=args.min_confidence,
            event_type=args.event_type,
            include_low_confidence=args.include_low_confidence,
        )
        _print_advanced_player(payload)
        return 0
    if args.advanced_command == "all-finalized":
        payload = service.infer_all_finalized(force=args.force)
        print(f"matches processed: {payload['matches_processed']}")
        print(f"advanced events saved: {payload['total_advanced_events']}")
        return 0
    print("advanced command required", file=sys.stderr)
    return 2


def run_profile_command(config: AppConfig, args: argparse.Namespace) -> int:
    if not args.profile_command:
        print("profile command required", file=sys.stderr)
        return 2

    connection = connect_database(config.database_path)
    try:
        with connection:
            if args.profile_command == "list":
                profiles = profiles_repo.list_profiles(connection)
                active_id = profiles_repo.get_active_profile_id(connection)
                print("profiles:")
                if not profiles:
                    print("  none")
                for profile in profiles:
                    active = " active" if active_id == int(profile["id"]) else ""
                    echo_name = profile["primary_echo_name"] or "none"
                    print(f"  #{profile['id']} {profile['display_name']} echo={echo_name}{active}")
                return 0

            if args.profile_command == "create":
                profile_id = profiles_repo.create_profile(connection, args.display_name, args.echo_name)
                print(f"created profile id: {profile_id}")
                print(f"set active with: python -m arena_coach.main profile set-active {profile_id}")
                return 0

            if args.profile_command == "set-active":
                if not profiles_repo.set_active_profile(connection, args.profile_id):
                    print(f"profile id {args.profile_id} does not exist", file=sys.stderr)
                    return 1
                print(f"active profile set: {args.profile_id}")
                return 0

            if args.profile_command == "show":
                return _print_active_profile(connection)

            if args.profile_command == "update":
                active = profiles_repo.get_active_profile(connection)
                if active is None:
                    print("No active profile. Create one with profile create.", file=sys.stderr)
                    return 1
                if args.display_name is None and args.echo_name is None:
                    print("profile update requires --display-name or --echo-name", file=sys.stderr)
                    return 2
                profiles_repo.update_profile(
                    connection,
                    int(active["id"]),
                    display_name=args.display_name,
                    primary_echo_name=args.echo_name,
                )
                updated = profiles_repo.get_profile(connection, int(active["id"]))
                print(f"updated profile id: {updated['id']}")
                print(f"display name: {updated['display_name']}")
                print(f"primary echo name: {updated['primary_echo_name'] or 'none'}")
                return 0
    finally:
        connection.close()

    return 2


def run_players_command(config: AppConfig, args: argparse.Namespace) -> int:
    if not args.players_command:
        print("players command required", file=sys.stderr)
        return 2

    connection = connect_database(config.database_path)
    try:
        with connection:
            if args.players_command == "list":
                players = players_repo.list_players(connection)
                print("players:")
                if not players:
                    print("  none")
                for player in players:
                    print(f"  #{player['id']} {player['canonical_name']} aliases={player['alias_count']}")
                return 0

            if args.players_command == "create":
                player_id = players_repo.create_player(connection, args.name)
                print(f"created player id: {player_id}")
                return 0

            if args.players_command == "aliases":
                player = players_repo.get_player(connection, args.player_id)
                if player is None:
                    print(f"player id {args.player_id} does not exist", file=sys.stderr)
                    return 1
                aliases = players_repo.list_aliases(connection, args.player_id)
                print(f"aliases for #{player['id']} {player['canonical_name']}:")
                if not aliases:
                    print("  none")
                for alias in aliases:
                    userid = alias["userid"] or "none"
                    playerid = alias["playerid"] or "none"
                    print(f"  #{alias['id']} {alias['alias_name']} userid={userid} playerid={playerid}")
                return 0
    finally:
        connection.close()

    return 2


def run_matches_command(config: AppConfig, args: argparse.Namespace) -> int:
    if not args.matches_command:
        print("matches command required", file=sys.stderr)
        return 2

    connection = connect_database(config.database_path)
    try:
        with connection:
            if args.matches_command == "list":
                return _print_matches_list(connection)
            if args.matches_command == "show":
                return _print_match_show(connection, args.match_id)
            if args.matches_command == "review":
                return _print_match_review(connection, args.match_id)
            if args.matches_command == "map-player":
                match_mapping.map_match_alias(connection, args.match_id, args.alias, args.player_id)
                print(f"mapped {args.alias} to player id {args.player_id}")
                return 0
            if args.matches_command == "create-player-from-alias":
                player_id = match_mapping.create_player_from_alias(
                    connection,
                    args.match_id,
                    args.alias,
                    args.canonical_name,
                )
                print(f"created player id: {player_id}")
                print(f"mapped {args.alias} to player id {player_id}")
                return 0
            if args.matches_command == "mark-self":
                match_mapping.mark_self(connection, args.match_id, args.alias)
                print(f"marked self: {args.alias}")
                return 0
            if args.matches_command == "set-team":
                match_mapping.set_team(connection, args.match_id, args.alias, args.team)
                print(f"team set: {args.alias} -> {args.team}")
                return 0
            if args.matches_command == "confirm-guest":
                match_mapping.confirm_guest(connection, args.match_id, args.alias)
                print(f"confirmed guest/unmapped: {args.alias}")
                return 0
            if args.matches_command == "finalize":
                result = match_mapping.finalize_match(connection, args.match_id)
                print(f"finalized match id: {result.match_id}")
                print(f"active profile id: {result.user_profile_id}")
                print(f"user team: {result.user_team or 'none'}")
                print(f"result: {result.result or 'unknown'}")
                print(f"match_player_stats updated: {result.stats_rows_updated}")
                print(f"event player fields updated: {result.event_roles_updated}")
                return 0
            if args.matches_command == "detect-afk":
                match = matches_repo.get_match(connection, args.match_id)
                if match is None:
                    print(f"match id {args.match_id} does not exist", file=sys.stderr)
                    return 1
                if not match["raw_log_path"]:
                    print(f"match id {args.match_id} does not have a raw log path", file=sys.stderr)
                    return 1
                result = apply_afk_detection_to_match(args.match_id, Path(match["raw_log_path"]), config.database_path)
                print(f"AFK detection refreshed for match id: {result['match_id']}")
                print(f"stats rows updated: {result['updated_stats']}")
                print(f"suspected AFK: {_format_alias_list(result['suspected_afk'])}")
                return 0
            if args.matches_command == "set-private-type":
                match = matches_repo.get_match(connection, args.match_id)
                if match is None:
                    print(f"match id {args.match_id} does not exist", file=sys.stderr)
                    return 1
                if str(match["match_classification"] or "").casefold() != "private":
                    print("Only private matches can use private subtypes.", file=sys.stderr)
                    return 1
                private_type = normalize_private_match_type(args.type, allow_none=False)
                display_name = match["display_name"]
                if not display_name:
                    display_name = _display_name_for_match_row(match, private_match_type=private_type)
                else:
                    display_name = _display_name_for_match_row(match, private_match_type=private_type)
                matches_repo.update_match_context(
                    connection,
                    args.match_id,
                    private_match_type=private_type,
                    display_name=display_name,
                )
                print(f"private match type set: {private_match_type_label(private_type)}")
                return 0
    finally:
        connection.close()

    return 2


def build_client(config: AppConfig) -> EchoApiClient:
    return EchoApiClient(
        host=config.echo_api_host,
        port=config.echo_api_port,
        timeout=config.request_timeout_seconds,
        path=config.echo_api_path,
    )


def print_startup(config: AppConfig) -> None:
    print(f"{__app_name__} started")
    print(f"config loaded: {config.config_path}")
    print(f"database path: {config.database_path}")
    print(f"raw log path: {config.raw_log_dir}")

    connection = connect_database(config.database_path)
    try:
        active = profiles_repo.get_active_profile(connection)
    finally:
        connection.close()

    if active is None:
        print("No active profile. Create one with profile create.")
    else:
        echo_name = active["primary_echo_name"] or "none"
        print(f"active profile: #{active['id']} {active['display_name']} (echo: {echo_name})")


def format_connection_status(status: ConnectionStatus) -> str:
    if status.ok:
        latency = f", {status.latency_ms} ms" if status.latency_ms is not None else ""
        return f"Echo API connection status: available ({status.source}{latency})"
    return f"Echo API connection status: unavailable ({status.error})"


def print_stop_summary(metadata: SessionMetadata) -> None:
    print("Logging stopped")
    print(f"snapshots captured: {metadata.snapshot_count}")
    print(f"errors recorded: {metadata.error_count}")
    print(f"latest sessionid: {metadata.latest_sessionid or 'none'}")
    print(f"latest game status: {metadata.latest_game_status or 'none'}")
    print(f"latest score: blue={metadata.latest_blue_score}, orange={metadata.latest_orange_score}")


def print_event_counts(event_counts: dict[str, int]) -> None:
    print("event counts:")
    if not event_counts:
        print("  none")
        return
    for event_type, count in event_counts.items():
        print(f"  {event_type}: {count}")


def _add_stats_filter_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--competitive-only", action="store_true", help="Use only competitive-eligible matches.")
    parser.add_argument("--include-low-quality", action="store_true", help="Include low-quality matches.")
    parser.add_argument("--include-private", action="store_true", help="Include private matches even with a narrower scope.")
    parser.add_argument("--include-tournament", action="store_true", help="Include tournament matches even with a narrower scope.")
    parser.add_argument("--include-unknown", action="store_true", help="Include unknown-classification matches.")
    parser.add_argument("--public-only", action="store_true", help="Use public matches only unless other include flags are added.")
    parser.add_argument("--private-only", action="store_true", help="Use private matches only unless other include flags are added.")
    parser.add_argument("--tournament-only", action="store_true", help="Use tournament matches only unless other include flags are added.")
    parser.add_argument("--include-guests", action="store_true", help="Include guest or unmapped players where supported.")
    parser.add_argument("--include-afk", action="store_true", help="Include suspected AFK players where supported.")
    parser.add_argument("--private-type", type=str, default=None, choices=PRIVATE_MATCH_TYPES, help="Filter private matches by subtype.")
    parser.add_argument("--last", type=int, default=None, help="Limit to the most recent N matches.")
    parser.add_argument("--from-date", type=str, default=None, help="Inclusive start date in YYYY-MM-DD format.")
    parser.add_argument("--to-date", type=str, default=None, help="Inclusive end date in YYYY-MM-DD format.")


def _add_advanced_filter_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--min-confidence", choices=("high", "medium", "low"), default="medium")
    parser.add_argument("--event-type", default=None)
    parser.add_argument("--player-id", type=int, default=None)
    parser.add_argument("--include-low-confidence", action="store_true")


def _build_stats_filters(args: argparse.Namespace) -> StatsFilter:
    include_public = True
    include_private = True
    include_tournament = True
    include_unknown = True

    if getattr(args, "public_only", False):
        include_public, include_private, include_tournament, include_unknown = True, False, False, False
    if getattr(args, "private_only", False):
        include_public, include_private, include_tournament, include_unknown = False, True, False, False
    if getattr(args, "tournament_only", False):
        include_public, include_private, include_tournament, include_unknown = False, False, True, False

    if getattr(args, "include_private", False):
        include_private = True
    if getattr(args, "include_tournament", False):
        include_tournament = True
    if getattr(args, "include_unknown", False):
        include_unknown = True

    return StatsFilter(
        competitive_only=bool(getattr(args, "competitive_only", False)),
        include_low_quality=True if not getattr(args, "competitive_only", False) else bool(getattr(args, "include_low_quality", False)),
        include_public=include_public,
        include_private=include_private,
        include_tournament=include_tournament,
        include_unknown=include_unknown,
        include_guest_players=bool(getattr(args, "include_guests", False)),
        include_afk_players=bool(getattr(args, "include_afk", False)),
        private_match_type=normalize_private_match_type(getattr(args, "private_type", None), allow_none=True),
        from_date=_parse_date(getattr(args, "from_date", None)),
        to_date=_parse_date(getattr(args, "to_date", None)),
        last_n=getattr(args, "last", None),
    )


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD.") from exc


def _print_stats_summary(summary: dict[str, object], filters: StatsFilter) -> None:
    print("Stats Summary")
    _print_filter_summary(filters)
    active_profile = summary.get("active_profile") or {}
    if active_profile:
        print(f"active profile: {active_profile.get('display_name')} ({active_profile.get('primary_echo_name') or 'no Echo name'})")
    print(f"matches played: {summary.get('matches_played', 0)}")
    print(f"competitive eligible matches: {summary.get('competitive_eligible_matches', 0)}")
    print(f"excluded low-quality count: {summary.get('excluded_low_quality_count', 0)}")
    print(
        "record: "
        f"{summary.get('wins', 0)}-{summary.get('losses', 0)}-{summary.get('ties', 0)} "
        f"(win rate {float(summary.get('win_rate', 0.0)):.2f}%)"
    )
    print(f"shot efficiency: {float(summary.get('shot_efficiency', 0.0)):.3f}")
    playstyle = summary.get("playstyle") or {}
    print(f"playstyle: {playstyle.get('label', 'Unknown')}")
    if playstyle.get("explanation"):
        print(f"playstyle note: {playstyle['explanation']}")
    if summary.get("low_sample_warning"):
        print(f"warning: {summary['low_sample_warning']}")
    print("")
    print("averages:")
    averages = summary.get("averages") or {}
    for stat_name in (
        "points",
        "goals",
        "assists",
        "saves",
        "stuns",
        "steals",
        "shots",
        "passes",
        "catches",
        "interceptions",
        "blocks",
        "turnovers",
        "possession_time",
    ):
        print(f"  {stat_name}: {float(averages.get(stat_name, 0.0)):.2f}")
    print("")
    breakdowns = summary.get("breakdowns") or {}
    rows = []
    for label, values in breakdowns.items():
        rows.append(
            [
                label,
                values.get("matches_played", 0),
                values.get("wins", 0),
                values.get("losses", 0),
                values.get("ties", 0),
                f"{float(values.get('win_rate', 0.0)):.2f}%",
                f"{float((values.get('averages') or {}).get('points', 0.0)):.2f}",
                f"{float((values.get('averages') or {}).get('goals', 0.0)):.2f}",
                f"{float((values.get('averages') or {}).get('assists', 0.0)):.2f}",
            ]
        )
    print("breakdowns:")
    _print_table(["slice", "matches", "wins", "losses", "ties", "win%", "avg pts", "avg goals", "avg ast"], rows)


def _print_trends(payload: dict[str, object], filters: StatsFilter) -> None:
    print("Trend Stats")
    _print_filter_summary(filters)
    print(f"matches considered: {payload.get('match_count', 0)}")
    rows = []
    for metric in payload.get("metrics") or []:
        rows.append(
            [
                metric.get("stat_name"),
                f"{float(metric.get('previous_average', 0.0)):.2f}",
                f"{float(metric.get('last_average', 0.0)):.2f}",
                f"{float(metric.get('delta', 0.0)):+.2f}",
                metric.get("direction"),
            ]
        )
    _print_table(["stat", "prev 5", "last 5", "delta", "direction"], rows)


def _print_matchups(payload: dict[str, object], filters: StatsFilter) -> None:
    print("Matchups")
    _print_filter_summary(filters)
    top_rivals = payload.get("top_rivals") or []
    if top_rivals:
        print("top rivals:")
        for rival in top_rivals:
            print(
                f"  {rival['display_name']} | matches={rival['matches_against']} "
                f"| win%={float(rival['win_rate_against']):.2f} | opp pts={float((rival['opponent_totals'] or {}).get('points', 0.0)):.1f}"
            )
        print("")
    rows = []
    for row in payload.get("rows") or []:
        rows.append(
            [
                row.get("display_name"),
                row.get("matches_against", 0),
                f"{float(row.get('win_rate_against', 0.0)):.2f}%",
                f"{float((row.get('user_totals') or {}).get('goals', 0.0)):.1f}",
                f"{float((row.get('user_totals') or {}).get('assists', 0.0)):.1f}",
                f"{float((row.get('opponent_totals') or {}).get('goals', 0.0)):.1f}",
                f"{float((row.get('opponent_totals') or {}).get('stuns', 0.0)):.1f}",
                row.get("direct_stuns_against_user", 0),
                row.get("direct_steals_against_user", 0),
            ]
        )
    _print_table(
        ["opponent", "matches", "win%", "user g", "user a", "opp g", "opp stuns", "direct stuns", "direct steals"],
        rows,
    )


def _print_teammates(payload: dict[str, object], filters: StatsFilter) -> None:
    print("Teammates")
    _print_filter_summary(filters)
    best = payload.get("best_teammates") or []
    if best:
        print("best teammate candidates:")
        for teammate in best:
            print(
                f"  {teammate['display_name']} | matches={teammate['matches_together']} "
                f"| win%={float(teammate['win_rate_together']):.2f} | confidence={teammate['confidence']}"
            )
        print("")
    rows = []
    for row in payload.get("rows") or []:
        rows.append(
            [
                row.get("display_name"),
                row.get("matches_together", 0),
                f"{float(row.get('win_rate_together', 0.0)):.2f}%",
                f"{float((row.get('user_averages') or {}).get('points', 0.0)):.2f}",
                f"{float((row.get('user_averages') or {}).get('goals', 0.0)):.2f}",
                f"{float((row.get('teammate_averages') or {}).get('points', 0.0)):.2f}",
                f"{float(row.get('team_score_average', 0.0)):.2f}",
                row.get("confidence"),
            ]
        )
    _print_table(["teammate", "matches", "win%", "my pts", "my goals", "their pts", "team score", "confidence"], rows)


def _print_quality(payload: dict[str, object], filters: StatsFilter) -> None:
    print("Match Quality")
    _print_filter_summary(filters)
    counts = payload.get("counts") or {}
    for label in ("Competitive Eligible", "AFK Affected", "Low Quality", "Unreviewed"):
        print(f"{label}: {counts.get(label, 0)}")
    print("")
    rows = []
    for row in payload.get("matches") or []:
        rows.append(
            [
                row.get("match_id"),
                row.get("display_name"),
                row.get("quality_label"),
                f"B {row.get('blue_score', 0)} / O {row.get('orange_score', 0)}",
                (
                    f"B {row.get('blue_round_wins', 0)} / O {row.get('orange_round_wins', 0)}"
                    if int(row.get("total_rounds_played", 0) or 0) > 1
                    or int(row.get("blue_round_wins", 0) or 0) + int(row.get("orange_round_wins", 0) or 0) > 1
                    else "-"
                ),
                row.get("total_rounds_played", 0),
                _yes_no_unknown(_optional_bool(row.get("points_carry_over"))),
                row.get("active_non_afk_player_count"),
                row.get("suspected_afk_count"),
                row.get("mapped_player_count"),
                row.get("round_warning") or "-",
                ", ".join(row.get("quality_reasons") or []) or "-",
            ]
        )
    _print_table(
        ["id", "match", "quality", "points", "rounds", "total rounds", "carry", "active non-afk", "afk", "mapped", "round warning", "reasons"],
        rows,
    )


def _print_infer_result(result: Any) -> None:
    print(f"match id: {result.match_id}")
    print(f"raw log path: {result.raw_log_path or 'none'}")
    print(f"deleted existing advanced events: {result.deleted_existing_events}")
    print(f"advanced events saved: {result.advanced_events_saved}")
    if hasattr(result, "deleted_existing_metrics"):
        print(f"deleted existing advanced player metrics: {result.deleted_existing_metrics}")
    if hasattr(result, "advanced_player_metrics_saved"):
        print(f"advanced player metrics saved: {result.advanced_player_metrics_saved}")
    if result.orientation:
        print(
            "orientation: "
            f"axis={result.orientation.get('axis') or 'unknown'} "
            f"blue_side={result.orientation.get('blue_side') or 'unknown'} "
            f"orange_side={result.orientation.get('orange_side') or 'unknown'} "
            f"confidence={result.orientation.get('confidence') or 'unknown'}"
        )
    print_event_counts(result.event_counts)


def _print_advanced_summary(payload: dict[str, Any], match_id: int) -> None:
    print(f"Advanced Summary - Match #{match_id}")
    print(f"match: {((payload.get('match') or {}).get('display_name')) or 'unknown'}")
    counts = payload.get("counts") or {}
    if not counts:
        print("advanced events: none")
        return
    print("advanced event counts:")
    for event_type, count in counts.items():
        print(f"  {event_type}: {count}")
    print("")
    rows = []
    for row in payload.get("player_breakdown") or []:
        counts_map = row.get("counts") or {}
        rows.append(
            [
                row.get("canonical_name") or row.get("alias"),
                row.get("team") or "unknown",
                counts_map.get("turnover", 0),
                counts_map.get("interception", 0),
                counts_map.get("missed_shot", 0),
                counts_map.get("shot_saved", 0),
                counts_map.get("initiator", 0),
                counts_map.get("shooter_uncovered", 0) + counts_map.get("lane_coverage_failure", 0),
            ]
        )
    print("player breakdown:")
    _print_table(
        ["player", "team", "turnovers", "interceptions", "missed shots", "shots saved", "initiators", "coverage gaps"],
        rows,
    )
    metric_rows = payload.get("player_metrics") or []
    if metric_rows:
        print("")
        print("observer-style player metrics:")
        print("  open pass = times the player was a viable pass option while their own team had possession")
        print("  lane block = times the player blocked a passing lane while defending")
        print("  open 2s / open 3s = 2-point or 3-point goals scored on an open net")
        print("  guarded 2s / guarded 3s = 2-point or 3-point goals scored with a goalie present")
        _print_table(
            [
                "player",
                "team",
                "passes",
                "catches",
                "open pass",
                "lane block",
                "turnovers",
                "intercepts",
                "open 2s",
                "open 3s",
                "guarded 2s",
                "guarded 3s",
            ],
            [
                [
                    row.get("match_alias"),
                    row.get("team"),
                    row.get("completed_passes", 0),
                    row.get("inferred_catches", 0),
                    row.get("open_for_pass_samples", 0),
                    row.get("lane_blocks", 0),
                    row.get("inferred_turnovers", 0),
                    row.get("inferred_interceptions", 0),
                    row.get("goals_2_open_net", 0),
                    row.get("goals_3_open_net", 0),
                    row.get("goals_2_guarded", 0),
                    row.get("goals_3_guarded", 0),
                ]
                for row in metric_rows
            ],
        )


def _print_advanced_timeline(rows: list[dict[str, Any]]) -> None:
    print("Advanced Timeline")
    table_rows = []
    for row in rows:
        table_rows.append(
            [
                row.get("start_game_clock") if row.get("start_game_clock") is not None else row.get("start_sequence"),
                row.get("event_type"),
                row.get("actor_alias") or "",
                row.get("target_alias") or "",
                row.get("confidence"),
                row.get("directness"),
                row.get("explanation") or "",
            ]
        )
    _print_table(["clock/seq", "type", "actor", "target", "confidence", "directness", "explanation"], table_rows)


def _print_advanced_player(payload: dict[str, Any]) -> None:
    print(f"Advanced Player Summary - Player #{payload.get('player_id')}")
    print(f"matches represented: {payload.get('matches', 0)}")
    print("event counts:")
    for event_type, count in (payload.get("event_counts") or {}).items():
        print(f"  {event_type}: {count}")
    print("")
    _print_advanced_timeline(payload.get("timeline") or [])


def _print_player_summary(summary: dict[str, object], filters: StatsFilter) -> None:
    print("Player Summary")
    _print_filter_summary(filters)
    print(f"player: {summary.get('display_name')} (#{summary.get('player_id')})")
    print(f"matches: {summary.get('matches', 0)}")
    print(
        "record: "
        f"{summary.get('wins', 0)}-{summary.get('losses', 0)}-{summary.get('ties', 0)} "
        f"(win rate {float(summary.get('win_rate', 0.0)):.2f}%)"
    )
    print(f"with active user: {summary.get('with_user_matches', 0)}")
    print(f"against active user: {summary.get('against_user_matches', 0)}")
    print(f"afk matches: {summary.get('afk_matches', 0)}")
    print(f"shot efficiency: {float(summary.get('shot_efficiency', 0.0)):.3f}")
    print("")
    averages = summary.get("averages") or {}
    rows = [[stat_name, f"{float(value):.2f}"] for stat_name, value in averages.items()]
    _print_table(["stat", "average"], rows)


def _print_filter_summary(filters: StatsFilter) -> None:
    scope = []
    if filters.include_public:
        scope.append("public")
    if filters.include_private:
        scope.append("private")
    if filters.include_tournament:
        scope.append("tournament")
    if filters.include_unknown:
        scope.append("unknown")
    lines = [
        f"scope: {', '.join(scope) if scope else 'none'}",
        f"competitive only: {'yes' if filters.competitive_only else 'no'}",
        f"include low quality: {'yes' if filters.include_low_quality else 'no'}",
        f"include AFK players: {'yes' if filters.include_afk_players else 'no'}",
        f"include guests: {'yes' if filters.include_guest_players else 'no'}",
    ]
    if filters.last_n is not None:
        lines.append(f"last matches: {filters.last_n}")
    selected_private_types = filters.selected_private_match_types()
    if selected_private_types:
        lines.append(
            "private subtype: "
            + ", ".join(private_match_type_label(private_type) for private_type in selected_private_types)
        )
    if filters.from_date:
        lines.append(f"from date: {filters.from_date.isoformat()}")
    if filters.to_date:
        lines.append(f"to date: {filters.to_date.isoformat()}")
    for line in lines:
        print(line)
    print("")


def _print_table(headers: list[str], rows: list[list[object]]) -> None:
    if not rows:
        print("  none")
        return
    widths = [len(header) for header in headers]
    string_rows = []
    for row in rows:
        values = ["" if value is None else str(value) for value in row]
        string_rows.append(values)
        for index, value in enumerate(values):
            widths[index] = max(widths[index], len(value))
    header_line = "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    divider = "  ".join("-" * widths[index] for index in range(len(headers)))
    print(header_line)
    print(divider)
    for row in string_rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def _print_active_profile(connection: sqlite3.Connection) -> int:
    active = profiles_repo.get_active_profile(connection)
    if active is None:
        print("No active profile. Create one with profile create.")
        return 1
    print(f"profile id: {active['id']}")
    print(f"display name: {active['display_name']}")
    print(f"primary echo name: {active['primary_echo_name'] or 'none'}")
    print(f"created at: {active['created_at']}")
    return 0


def _print_matches_list(connection: sqlite3.Connection) -> int:
    matches = matches_repo.list_matches(connection)
    print("matches:")
    if not matches:
        print("  none")
        return 0
    for match in matches:
        score = _format_score(match)
        finalized = "true" if int(match["finalized"] or 0) else "false"
        started_at = match["started_at"] or match["created_at"]
        result = match["result"] or "unknown"
        summary = f"  #{match['id']} {started_at} map={match['map_name'] or 'none'} score={score} finalized={finalized} result={result}"
        if str(match["match_classification"] or "").casefold() == "private":
            summary += f" private_type={private_match_type_label(match['private_match_type'])}"
        round_text = _round_record_text(match)
        if round_text:
            summary += f" rounds={round_text}"
        print(summary)
    return 0


def _print_match_show(connection: sqlite3.Connection, match_id: int) -> int:
    match = matches_repo.get_match(connection, match_id)
    if match is None:
        print(f"match id {match_id} does not exist", file=sys.stderr)
        return 1

    players = matches_repo.get_match_players(connection, match_id)
    print_match_header(match)
    print(f"detected players: {', '.join(player['match_alias'] for player in players) if players else 'none'}")
    print_event_counts({row["event_type"]: row["count"] for row in matches_repo.get_event_counts(connection, match_id)})
    return 0


def _print_match_review(connection: sqlite3.Connection, match_id: int) -> int:
    match = matches_repo.get_match(connection, match_id)
    if match is None:
        print(f"match id {match_id} does not exist", file=sys.stderr)
        return 1

    print_match_header(match)
    active = profiles_repo.get_active_profile(connection)
    if active is not None and active["primary_echo_name"]:
        print(f"active profile echo name: {active['primary_echo_name']}")
    elif active is None:
        print("No active profile. Create one with profile create.")

    players = matches_repo.get_match_players(connection, match_id)
    print("detected players:")
    if not players:
        print("  none")
    for player in players:
        mapped = f"#{player['player_id']} {player['canonical_name']}" if player["player_id"] else "unmapped"
        confirmed = "yes" if int(player["confirmed"] or 0) else "no"
        is_user = "yes" if int(player["is_user"] or 0) else "no"
        userid = player["userid"] or "none"
        playerid = player["playerid"] or "none"
        print(
            f"  {player['match_alias']} | userid={userid} | playerid={playerid} | "
            f"team={player['team'] or 'none'} | mapped={mapped} | confirmed={confirmed} | self={is_user}"
        )

        if _matches_active_echo_name(active, player["match_alias"]):
            print("    self suggestion: active profile echo name matches this alias.")

        suggestions = players_repo.suggest_players_for_alias(
            connection,
            player["match_alias"],
            userid=player["userid"],
            playerid=player["playerid"],
        )
        if suggestions:
            suggestion_text = [
                f"#{item['player_id']} {item['canonical_name']} ({item['reason']}, {item['confidence']:.2f})"
                for item in suggestions
            ]
            print(f"    player suggestions: {', '.join(suggestion_text)}")

    print("event counts:")
    event_counts = matches_repo.get_event_counts(connection, match_id)
    if not event_counts:
        print("  none")
    for row in event_counts:
        print(f"  {row['event_type']}: {row['count']}")

    print("basic stats:")
    stat_rows = matches_repo.get_match_player_stats(connection, match_id)
    if not stat_rows:
        print("  none")
    for stat in stat_rows:
        afk = _afk_marker(stat["metadata_json"])
        print(
            f"  {stat['match_alias']} team={stat['team'] or 'none'} "
            f"pts={stat['points']} g={stat['goals']} a={stat['assists']} saves={stat['saves']} "
            f"stuns={stat['stuns']} steals={stat['steals']} shots={stat['shots']} "
            f"passes={stat['passes']} catches={stat['catches']} blocks={stat['blocks']} ints={stat['interceptions']}{afk}"
        )

    return 0


def print_match_header(match: sqlite3.Row) -> None:
    print(f"match id: {match['id']}")
    print(f"date/time: {match['started_at'] or match['created_at'] or 'unknown'}")
    print(f"type: {_match_type_text(match)}")
    print(f"map: {match['map_name'] or 'none'}")
    print(f"score: {_format_score(match)}")
    round_text = _round_record_text(match)
    if round_text:
        print(f"round record: {round_text}")
        print(f"total rounds: {int(match['total_rounds_played'] or 0)}")
        print(f"points carry over: {_yes_no_unknown(_optional_bool(match['points_carry_over']) if 'points_carry_over' in match.keys() else None)}")
    warning = round_record_warning(
        {
            "blue_score": match["blue_score"],
            "orange_score": match["orange_score"],
            "blue_round_wins": match["blue_round_wins"] if "blue_round_wins" in match.keys() else 0,
            "orange_round_wins": match["orange_round_wins"] if "orange_round_wins" in match.keys() else 0,
        }
    )
    if warning:
        print(f"warning: {warning}")
    print(f"result: {match['result'] or 'unknown'}")
    print(f"user team: {match['user_team'] or 'none'}")
    print(f"raw log path: {match['raw_log_path'] or 'none'}")
    print(f"finalized: {'true' if int(match['finalized'] or 0) else 'false'}")


def _format_names(players: list[dict]) -> str:
    names = [str(player.get("name")) for player in players if player.get("name")]
    return ", ".join(names) if names else "none"


def _format_teams(teams: list[dict]) -> str:
    labels = []
    for team in teams:
        color = team.get("team")
        label = team.get("label")
        if label and label != color:
            labels.append(f"{color} ({label})")
        else:
            labels.append(str(color))
    return ", ".join(labels) if labels else "none"


def _format_score(match: sqlite3.Row) -> str:
    return f"blue={match['blue_score']}, orange={match['orange_score']}"


def _round_record_text(match: sqlite3.Row | dict[str, object]) -> str:
    try:
        blue_rounds = int((match["blue_round_wins"] if "blue_round_wins" in match.keys() else 0) or 0)
        orange_rounds = int((match["orange_round_wins"] if "orange_round_wins" in match.keys() else 0) or 0)
        total_rounds = int((match["total_rounds_played"] if "total_rounds_played" in match.keys() else 0) or 0)
    except (TypeError, ValueError, KeyError):
        return ""
    if total_rounds <= 1 and blue_rounds + orange_rounds <= 1:
        return ""
    return f"blue={blue_rounds}, orange={orange_rounds}"


def _match_type_text(match: sqlite3.Row) -> str:
    classification = str(match["match_classification"] or "Unknown")
    if classification.casefold() != "private":
        return classification
    return f"{classification} {private_match_type_label(match['private_match_type'])}"


def _yes_no_unknown(value: Optional[bool]) -> str:
    if value is None:
        return "Unknown"
    return "Yes" if value else "No"


def _optional_bool(value: object) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        text = str(value).strip().casefold()
        if text in {"true", "yes", "1"}:
            return True
        if text in {"false", "no", "0"}:
            return False
        return None


def _display_name_for_match_row(match: sqlite3.Row, *, private_match_type: Optional[str] = None) -> str:
    return build_match_display_name(
        {
            "finalized": bool(match["finalized"]),
            "match_classification": match["match_classification"],
            "private_match_type": private_match_type if private_match_type is not None else match["private_match_type"],
            "match_type": match["match_type"],
            "started_at": match["started_at"] or match["created_at"],
            "blue_score": match["blue_score"],
            "orange_score": match["orange_score"],
            "blue_round_wins": match["blue_round_wins"] if "blue_round_wins" in match.keys() else 0,
            "orange_round_wins": match["orange_round_wins"] if "orange_round_wins" in match.keys() else 0,
            "total_rounds_played": match["total_rounds_played"] if "total_rounds_played" in match.keys() else 0,
            "user_team": match["user_team"],
            "result": match["result"],
        }
    )


def _format_alias_list(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def _afk_marker(metadata_json: str | None) -> str:
    if not metadata_json:
        return ""
    try:
        metadata = json.loads(metadata_json)
    except json.JSONDecodeError:
        return ""
    afk = metadata.get("afk_detection") if isinstance(metadata, dict) else None
    if not isinstance(afk, dict) or not afk.get("suspected"):
        return ""
    reasons = ",".join(afk.get("reasons") or [])
    confidence = afk.get("confidence", 0)
    return f" AFK=suspected({confidence:.2f}; {reasons})"


def _matches_active_echo_name(active: Optional[sqlite3.Row], alias: str) -> bool:
    if active is None or not active["primary_echo_name"]:
        return False
    return str(active["primary_echo_name"]).casefold() == str(alias).casefold()


if __name__ == "__main__":
    raise SystemExit(main())
