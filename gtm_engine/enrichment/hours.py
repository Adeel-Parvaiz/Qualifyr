"""Parse OSM-format opening_hours strings into structured data.

OSM format examples:
  Mo-Fr 09:00-18:00; Sa 10:00-14:00
  Mo-Su 08:00-22:00
  24/7
  Mo-Fr 09:00-17:00; Sa 09:00-13:00; Su off
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_DAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
_DAY_INDEX = {d: i for i, d in enumerate(_DAYS)}

_TIME_RANGE = re.compile(r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})")
_DAY_RANGE = re.compile(r"(Mo|Tu|We|Th|Fr|Sa|Su)(?:\s*-\s*(Mo|Tu|We|Th|Fr|Sa|Su))?")


@dataclass
class ParsedHours:
    raw: str
    is_24_7: bool = False
    days_open: int = 0
    weekly_schedule: dict[str, list[str]] = field(default_factory=dict)


def _expand_day_range(start: str, end: str | None) -> list[str]:
    si = _DAY_INDEX.get(start, -1)
    if si < 0:
        return []
    if end is None:
        return [start]
    ei = _DAY_INDEX.get(end, -1)
    if ei < 0:
        return [start]
    if ei >= si:
        return list(_DAYS[si : ei + 1])
    return list(_DAYS[si:]) + list(_DAYS[: ei + 1])


def parse_opening_hours(raw: str | None) -> ParsedHours | None:
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    result = ParsedHours(raw=raw)

    if raw.lower() in ("24/7", "24hours", "mo-su 00:00-24:00"):
        result.is_24_7 = True
        result.days_open = 7
        for d in _DAYS:
            result.weekly_schedule[d] = ["00:00-24:00"]
        return result

    days_seen: set[str] = set()
    for rule in raw.split(";"):
        rule = rule.strip()
        if not rule:
            continue
        if "off" in rule.lower():
            off_days = _DAY_RANGE.findall(rule)
            for start, end in off_days:
                for d in _expand_day_range(start, end or None):
                    result.weekly_schedule.pop(d, None)
                    days_seen.discard(d)
            continue

        day_matches = _DAY_RANGE.findall(rule)
        time_matches = _TIME_RANGE.findall(rule)
        time_strs = [f"{o}-{c}" for o, c in time_matches] or ["open"]

        if day_matches:
            for start, end in day_matches:
                for d in _expand_day_range(start, end or None):
                    result.weekly_schedule.setdefault(d, []).extend(time_strs)
                    days_seen.add(d)
        elif time_matches:
            for d in _DAYS:
                result.weekly_schedule.setdefault(d, []).extend(time_strs)
                days_seen.add(d)

    result.days_open = len(days_seen)
    return result
