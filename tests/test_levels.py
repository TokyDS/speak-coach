import pytest

from speak import levels


@pytest.mark.parametrize("base, difficulty, expected", [
    ("B1", "same", "B1"), ("B1", "easier", "A2"), ("B1", "harder", "B2"),
    ("A2", "easier", "A2"),            # ниже A2 собеседник не опускается
    ("C1", "harder", "C1"),            # и выше C1 не поднимается
    ("A1", "same", "A2"), ("C2", "same", "C1"),
    ("B2", "nonsense", "B2"),
])
def test_partner_level(base, difficulty, expected):
    assert levels.partner_level(base, difficulty) == expected


def test_estimate_needs_enough_assessed_turns():
    est = levels.estimate(["B1", "unknown", "B1", "unknown"])
    assert est == {"level": None, "samples": 2, "next": None, "next_share": 0, "need": 3}


def test_estimate_is_median_of_recent_and_shows_progress_to_next():
    old = ["A2"] * 40                              # давние реплики вне окна не тянут вниз
    recent = ["B1"] * 20 + ["B2"] * 6 + ["A2"] * 4
    est = levels.estimate(old + recent)
    assert est["level"] == "B1"
    assert est["samples"] == levels.WINDOW
    assert est["next"] == "B2" and est["next_share"] == 20


def test_estimate_at_top_of_scale():
    est = levels.estimate(["C2"] * 6)
    assert est["level"] == "C2" and est["next"] is None and est["next_share"] == 100


def test_median_level():
    assert levels.median_level(["A2", "B2", "unknown", "B1"]) == "B1"
    assert levels.median_level(["unknown"]) is None
    assert levels.is_level("B2") and not levels.is_level("auto")


def test_every_partner_level_has_a_style():
    assert set(levels.PARTNER_STYLE) == set(levels.PARTNER_LEVELS)
