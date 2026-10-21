import json
import random

import pytest

from speak import scenarios
from speak.session import (MAX_SESSIONS, SessionStore, Turn, assessed_levels, load_history, load_records, save,
                           words_per_minute)

SCENARIO = scenarios.get("smalltalk")


def _turn(heard: str, seconds: float, ok: bool, level: str = "B1") -> Turn:
    feedback = {"ok": ok, "corrected": heard if ok else heard + " fixed", "natural": heard, "level": level}
    return Turn(heard, seconds, "Nice! And then?", feedback)


def test_words_per_minute():
    assert words_per_minute(30, 15) == 120
    assert words_per_minute(5, 0.5) == 0


def test_store_appends_turns_without_mutating_old_snapshot():
    store = SessionStore()
    session = store.create(SCENARIO, slow=False, opener="Hey!")
    updated = store.add_turn(session.id, _turn("I am fine", 2, ok=True))
    assert session.turns == ()
    assert len(updated.turns) == 1
    assert store.get(session.id) is updated


def test_history_and_corrections_and_stats():
    store = SessionStore()
    s = store.create(SCENARIO, slow=False, opener="Hey!")
    store.add_turn(s.id, _turn("I go home", 3, ok=False))
    s = store.add_turn(s.id, _turn("It was great", 3, ok=True))

    assert [l.speaker for l in s.history()] == ["partner", "learner", "partner", "learner", "partner"]
    assert s.corrections() == [("I go home", "I go home fixed")]
    assert s.stats() == {"turns": 2, "words": 6, "seconds": 6, "wpm": 60, "clean": 1, "level": "B1"}


def test_store_evicts_oldest_session():
    store = SessionStore()
    first = store.create(SCENARIO, False, "a")
    for _ in range(MAX_SESSIONS):
        store.create(SCENARIO, False, "b")
    assert store.get(first.id) is None


def test_save_and_load_history_round_trip(tmp_path):
    store = SessionStore()
    s = store.create(SCENARIO, False, "Hey!")
    s = store.add_turn(s.id, _turn("I go home", 3, ok=False))
    summary = {"verdict": "ok", "focus": [{"topic": "Прошедшее время", "wrong": "go", "right": "went"}],
               "phrases": [{"en": "to be honest", "ru": "честно говоря"}], "strengths": []}

    path = save(s, summary, tmp_path / "sessions")
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["scenario"] == "smalltalk"
    assert record["corrections"] == [{"said": "I go home", "better": "I go home fixed"}]

    (tmp_path / "sessions" / "00000000-broken.json").write_text("{oops", encoding="utf-8")
    history = load_history(tmp_path / "sessions")
    assert len(history) == 1
    assert history[0]["focus"][0]["topic"] == "Прошедшее время"


def test_autosave_overwrites_one_file_and_feeds_level_estimate(tmp_path):
    store = SessionStore()
    s = store.create(SCENARIO, False, "Hey!", partner_level="A2")
    s = store.add_turn(s.id, _turn("I go home", 3, ok=False, level="A2"))
    save(s, None, tmp_path)                                    # после первой реплики
    s = store.add_turn(s.id, _turn("It was a long and tiring day", 4, ok=True, level="B1"))
    save(s, None, tmp_path)                                    # после второй — тот же файл
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1 and not list(tmp_path.glob("*.tmp"))
    record = json.loads(files[0].read_text(encoding="utf-8"))
    assert record["summary"] is None and record["partner_level"] == "A2"
    assert [t["level"] for t in record["turns"]] == ["A2", "B1"]
    assert record["turns"][1]["words"] == 7 and record["turns"][1]["ok"] is True
    assert assessed_levels(load_records(tmp_path)) == ["A2", "B1"]


def test_assessed_levels_go_from_old_to_new():
    records = [{"turns": [{"level": "B2"}]}, {"turns": [{"level": "A2"}, "junk"]}, {"turns": "broken"}]
    assert assessed_levels(records) == ["A2", "B2"]   # records — новые первыми


def test_load_history_skips_odd_files(tmp_path):
    (tmp_path / "1-list.json").write_text("[1, 2]", encoding="utf-8")
    (tmp_path / "2-null-summary.json").write_text('{"title": "Ок", "summary": null}', encoding="utf-8")
    (tmp_path / "3-bad-utf8.json").write_bytes(b"\xff\xfe{")
    history = load_history(tmp_path)
    assert [h["title"] for h in history] == ["Ок"]
    assert history[0]["focus"] == []


def test_add_turn_to_finished_session_returns_none():
    store = SessionStore()
    s = store.create(SCENARIO, False, "Hey!")
    store.pop(s.id)
    assert store.add_turn(s.id, _turn("hi", 1, ok=True)) is None


def test_load_history_of_missing_dir_is_empty(tmp_path):
    assert load_history(tmp_path / "nope") == []


def test_scenarios_are_complete_and_unique():
    keys = [s.key for s in scenarios.SCENARIOS]
    assert len(keys) == len(set(keys))
    for s in scenarios.SCENARIOS:
        assert s.title and s.blurb and s.role and s.openers
        assert s.opener(random.Random(0)) in s.openers
    with pytest.raises(KeyError):
        scenarios.get("nope")
