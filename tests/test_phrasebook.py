import json
from datetime import date, timedelta

import pytest

from speak.phrasebook import INTERVALS, LEARNED_BOX, Phrasebook, PhrasebookError


class Clock:
    def __init__(self) -> None:
        self.today = date(2026, 10, 21)

    def __call__(self) -> date:
        return self.today


@pytest.fixture
def book(tmp_path):
    clock = Clock()
    return Phrasebook(tmp_path / "phrasebook.json", today=clock), clock


def test_add_dedupes_by_words_not_punctuation(book):
    pb, _ = book
    card, created = pb.add("I went to the cinema.", "Я ходил в кино.", "correction")
    assert created and card.box == 0 and card.due == "2026-10-21"
    same, created_again = pb.add("i went to the CINEMA", "другой перевод", "phrase")
    assert not created_again and same.id == card.id
    assert pb.overview()["total"] == 1


@pytest.mark.parametrize("en, ru, source", [("", "x", "phrase"), ("x", "", "phrase"),
                                            ("x" * 301, "y", "phrase"), ("x", "y", "hacker")])
def test_add_validates_input(book, en, ru, source):
    with pytest.raises(PhrasebookError):
        book[0].add(en, ru, source)


def test_correct_answers_move_card_through_boxes(book):
    pb, clock = book
    card, _ = pb.add("to be honest", "честно говоря", "phrase")
    for box in range(1, LEARNED_BOX + 1):
        card = pb.grade(card.id, correct=True)
        assert card.box == box
        assert card.due == (clock.today + timedelta(days=INTERVALS[box])).isoformat()
    overview = pb.overview()
    assert overview["learned"] == 1 and overview["due"] == 0
    assert overview["cards"][0]["learned"]


def test_mistake_sends_card_back_to_today(book):
    pb, _ = book
    card, _ = pb.add("to be honest", "честно говоря", "phrase")
    pb.grade(card.id, correct=True)
    card = pb.grade(card.id, correct=False)
    assert card.box == 0 and card.due == "2026-10-21" and card.lapses == 1 and card.reviews == 2
    assert [e["correct"] for e in pb.review_log()] == [True, False]


def test_due_queue_follows_the_calendar(book):
    pb, clock = book
    card, _ = pb.add("no way", "да ладно", "phrase")
    pb.grade(card.id, correct=True)                 # следующий раз — завтра
    assert pb.overview()["due"] == 0
    clock.today += timedelta(days=1)
    queue = pb.overview()["queue"]
    assert [c["en"] for c in queue] == ["no way"] and queue[0]["is_due"]


def test_delete_and_unknown_card(book):
    pb, _ = book
    card, _ = pb.add("no way", "да ладно", "phrase")
    pb.delete(card.id)
    assert pb.overview()["total"] == 0
    with pytest.raises(PhrasebookError, match="нет в тетради"):
        pb.grade(card.id, correct=True)
    with pytest.raises(PhrasebookError):
        pb.get("missing")


def test_file_survives_restart_and_reports_corruption(tmp_path):
    path = tmp_path / "phrasebook.json"
    Phrasebook(path).add("cheers", "спасибо", "phrase")
    assert Phrasebook(path).overview()["total"] == 1
    path.write_text('{"cards": [{"unexpected": 1}]}', encoding="utf-8")
    with pytest.raises(PhrasebookError, match="повреждён"):
        Phrasebook(path).overview()
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(PhrasebookError):
        Phrasebook(path).overview()


def test_saved_file_is_plain_json(book, tmp_path):
    pb, _ = book
    pb.add("cheers", "спасибо", "summary")
    data = json.loads((tmp_path / "phrasebook.json").read_text(encoding="utf-8"))
    assert data["cards"][0]["source"] == "summary" and data["log"] == []
