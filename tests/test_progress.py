from datetime import date

from speak import progress

TODAY = date(2026, 10, 21)


def turn(day: str, seconds: float, words: int, ok: bool, level: str) -> dict:
    return {"at": f"{day}T12:00:00", "seconds": seconds, "words": words, "ok": ok, "level": level}


RECORDS = [
    {"turns": [turn("2026-10-21", 30, 60, True, "B1"), turn("2026-10-21", 30, 40, False, "A2")]},
    {"turns": [turn("2026-10-20", 60, 100, True, "B1")] + [turn("2026-10-19", 6, 10, False, "B1")] * 5},
    {"turns": "broken"},
    {"turns": [{"at": "not a date"}, "junk"]},
    {},
]


def test_build_aggregates_days_streak_and_level():
    data = progress.build(RECORDS, [{"date": "2026-10-18", "correct": True}], TODAY)
    assert len(data["days"]) == progress.DAYS
    today = data["days"][-1]
    assert today == {"date": "2026-10-21", "turns": 2, "minutes": 1.0, "words": 100, "wpm": 100,
                     "clean_pct": 50, "level": "A2", "reviews": 0}
    assert data["days"][-4]["reviews"] == 1 and data["days"][-4]["turns"] == 0
    assert data["streak"] == 4              # 18 (повторение), 19, 20, 21
    assert data["level"]["level"] == "B1"  # 8 оценок: медиана
    assert data["totals"] == {"sessions": 2, "turns": 8, "words": 250, "minutes": 2.5,
                              "week_minutes": 2.5, "active_days": 4}


def test_empty_day_has_no_rates():
    day = progress.build([], [], TODAY)["days"][0]
    assert day["wpm"] is None and day["clean_pct"] is None and day["level"] is None


def test_streak_does_not_break_before_today_is_over():
    assert progress.streak({"2026-10-20", "2026-10-19"}, TODAY) == 2
    assert progress.streak({"2026-10-21"}, TODAY) == 1
    assert progress.streak({"2026-10-18"}, TODAY) == 0
