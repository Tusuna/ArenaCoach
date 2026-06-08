"""Reader for Arena Coach raw JSONL capture files."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class RawSnapshotRecord:
    line_number: int
    sequence: Optional[int]
    captured_at: Optional[str]
    source: Optional[str]
    snapshot: Dict[str, Any]
    raw_line: str


@dataclass
class RawLogInvalidLine:
    line_number: int
    error: str
    raw_line: str


@dataclass
class RawLogSummary:
    total_lines: int = 0
    valid_snapshots: int = 0
    invalid_lines: int = 0
    first_captured_at: Optional[str] = None
    last_captured_at: Optional[str] = None


@dataclass
class RawLogReadResult:
    path: Path
    records: List[RawSnapshotRecord] = field(default_factory=list)
    invalid_lines: List[RawLogInvalidLine] = field(default_factory=list)
    summary: RawLogSummary = field(default_factory=RawLogSummary)


def read_raw_log(path: Path) -> RawLogReadResult:
    raw_path = Path(path)
    result = RawLogReadResult(path=raw_path)

    with raw_path.open("r", encoding="utf-8") as file:
        for line_number, raw_line in enumerate(file, start=1):
            result.summary.total_lines += 1
            stripped = raw_line.strip()
            if not stripped:
                continue

            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                _add_invalid(result, line_number, f"Malformed JSON: {exc.msg}", raw_line)
                continue

            if not isinstance(payload, dict):
                _add_invalid(result, line_number, "Line JSON must be an object", raw_line)
                continue

            snapshot = payload.get("snapshot")
            if not isinstance(snapshot, dict):
                _add_invalid(result, line_number, "Line does not contain a snapshot object", raw_line)
                continue

            record = RawSnapshotRecord(
                line_number=line_number,
                sequence=_safe_int(payload.get("sequence")),
                captured_at=_safe_str(payload.get("captured_at")),
                source=_safe_str(payload.get("source")),
                snapshot=snapshot,
                raw_line=stripped,
            )
            result.records.append(record)
            result.summary.valid_snapshots += 1
            if record.captured_at:
                if result.summary.first_captured_at is None:
                    result.summary.first_captured_at = record.captured_at
                result.summary.last_captured_at = record.captured_at

    return result


def _add_invalid(result: RawLogReadResult, line_number: int, error: str, raw_line: str) -> None:
    result.invalid_lines.append(
        RawLogInvalidLine(
            line_number=line_number,
            error=error,
            raw_line=raw_line.rstrip("\n"),
        )
    )
    result.summary.invalid_lines += 1


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)
