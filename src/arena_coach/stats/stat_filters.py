"""Filter models for stats aggregation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Optional

from arena_coach.match_context import normalize_private_match_type


@dataclass(frozen=True)
class StatsFilter:
    finalized_only: bool = True
    competitive_only: bool = False
    include_low_quality: bool = True
    include_public: bool = True
    include_private: bool = True
    include_tournament: bool = True
    include_unknown: bool = True
    include_afk_players: bool = False
    include_guest_players: bool = False
    private_match_type: Optional[str] = None
    private_match_types: tuple[str, ...] = ()
    from_date: Optional[date] = None
    to_date: Optional[date] = None
    last_n: Optional[int] = None

    def with_updates(self, **kwargs) -> "StatsFilter":
        return replace(self, **kwargs)

    def selected_private_match_types(self) -> tuple[str, ...]:
        values: list[str] = []
        for value in self.private_match_types:
            normalized = normalize_private_match_type(value, allow_none=False)
            if normalized and normalized not in values:
                values.append(normalized)
        if values:
            return tuple(values)
        normalized_single = normalize_private_match_type(self.private_match_type, allow_none=True)
        return (normalized_single,) if normalized_single else ()

    def allows_classification(self, classification: str) -> bool:
        normalized = str(classification or "Unknown").strip().casefold()
        if normalized == "public":
            return self.include_public
        if normalized == "private":
            return self.include_private
        if normalized == "tournament":
            return self.include_tournament
        return self.include_unknown

    def allows_private_match_type(self, private_match_type: Optional[str], match_classification: str) -> bool:
        selected_types = self.selected_private_match_types()
        if not selected_types:
            return True
        if str(match_classification or "").casefold() != "private":
            return False
        normalized = normalize_private_match_type(private_match_type, allow_none=False)
        return normalized in selected_types
