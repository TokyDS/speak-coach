"""Прогресс по дням: минуты речи, темп, доля реплик без ошибок, уровень, повторения тетради."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from . import levels
from .session import assessed_levels, words_per_minute

DAYS = 30


def _dated_turns(record: dict) -> list[tuple[str, dict]]:
    """(день, реплика) для реплик с корректной датой; испорченные записи пропускаются."""
    turns = record.get("turns")
    if not isinstance(turns, list):
        return []
    dated = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        try:
            dated.append((datetime.fromisoformat(str(turn.get("at", ""))).date().isoformat(), turn))
        except ValueError:
            continue
    return dated


def _turns_by_day(records: list[dict]) -> dict[str, list[dict]]:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        for day, turn in _dated_turns(record):
            by_day[day].append(turn)
    return by_day


def _day_stats(day: str, turns: list[dict], reviews: int) -> dict:
    seconds = sum(float(t.get("seconds", 0)) for t in turns)
    words = sum(int(t.get("words", 0)) for t in turns)
    return {
        "date": day,
        "turns": len(turns),
        "minutes": round(seconds / 60, 1),
        "words": words,
        "wpm": words_per_minute(words, seconds) or None,
        "clean_pct": round(100 * sum(1 for t in turns if t.get("ok")) / len(turns)) if turns else None,
        "level": levels.median_level([t.get("level", "unknown") for t in turns]),
        "reviews": reviews,
    }


def streak(active_days: set[str], today: date) -> int:
    """Дни подряд с занятиями. Сегодня ещё не занимался — серия не рвётся, считаем со вчера."""
    day = today if today.isoformat() in active_days else today - timedelta(days=1)
    count = 0
    while day.isoformat() in active_days:
        count += 1
        day -= timedelta(days=1)
    return count


def build(records: list[dict], review_log: list[dict], today: date) -> dict:
    by_day = _turns_by_day(records)
    reviews: dict[str, int] = defaultdict(int)
    for entry in review_log:
        reviews[str(entry.get("date", ""))] += 1
    window = [(today - timedelta(days=offset)).isoformat() for offset in range(DAYS - 1, -1, -1)]
    active = {d for d, turns in by_day.items() if turns} | {d for d, n in reviews.items() if n}
    all_turns = [t for turns in by_day.values() for t in turns]
    seconds = sum(float(t.get("seconds", 0)) for t in all_turns)
    week = {(today - timedelta(days=offset)).isoformat() for offset in range(7)}
    return {
        "days": [_day_stats(d, by_day.get(d, []), reviews.get(d, 0)) for d in window],
        "streak": streak(active, today),
        "level": levels.estimate(assessed_levels(records)),
        "totals": {
            "sessions": sum(1 for r in records if _dated_turns(r)),
            "turns": len(all_turns),
            "words": sum(int(t.get("words", 0)) for t in all_turns),
            "minutes": round(seconds / 60, 1),
            "week_minutes": round(sum(float(t.get("seconds", 0)) for d in week for t in by_day.get(d, [])) / 60, 1),
            "active_days": len(active),
        },
    }
