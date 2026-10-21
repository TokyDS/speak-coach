"""Тетрадь фраз с интервальным повторением (коробки Лейтнера).

Фраза попадает в тетрадь из разбора реплики или итогов. При повторении видишь русский
вариант и говоришь по-английски: верно — фраза уходит в следующую коробку и вернётся
позже (1, 3, 7, 16, 35, 90 дней); ошибся — возвращается в первую и будет снова сегодня.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from pathlib import Path

from .textdiff import tokenize

log = logging.getLogger(__name__)

INTERVALS = (0, 1, 3, 7, 16, 35, 90)   # дней до следующего повторения в коробке N
LEARNED_BOX = 4                        # с этой коробки фраза считается выученной
MAX_TEXT = 300
MAX_LOG = 5000
SOURCES = ("correction", "phrase", "summary")


class PhrasebookError(Exception):
    """Фразу не удалось добавить или найти."""


@dataclass(frozen=True)
class Card:
    id: str
    en: str
    ru: str
    source: str
    added: str          # ISO-дата
    box: int = 0
    due: str = ""       # ISO-дата следующего повторения
    reviews: int = 0
    lapses: int = 0

    def public(self, today: date) -> dict:
        return {**asdict(self), "is_due": self.due <= today.isoformat(), "learned": self.box >= LEARNED_BOX}


def _key(text: str) -> str:
    return " ".join(t.norm for t in tokenize(text))


class Phrasebook:
    def __init__(self, path: Path, today: Callable[[], date] = date.today) -> None:
        self._path = path
        self._today = today
        self._lock = threading.Lock()

    # ---------- хранение ----------

    def _load(self) -> tuple[list[Card], list[dict]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            cards = [Card(**c) for c in raw.get("cards", []) if isinstance(c, dict)]
            review_log = [e for e in raw.get("log", []) if isinstance(e, dict)]
        except FileNotFoundError:
            return [], []
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            log.error("тетрадь %s не читается: %s", self._path, exc)
            raise PhrasebookError("файл тетради повреждён — подробности в терминале") from exc
        return cards, review_log

    def _save(self, cards: list[Card], review_log: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        data = {"cards": [asdict(c) for c in cards], "log": review_log[-MAX_LOG:]}
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self._path)

    # ---------- операции ----------

    def add(self, en: str, ru: str, source: str) -> tuple[Card, bool]:
        """(карточка, создана ли): одинаковая английская фраза второй раз не добавляется."""
        en, ru = en.strip(), ru.strip()
        if not en or not ru or len(en) > MAX_TEXT or len(ru) > MAX_TEXT:
            raise PhrasebookError("нужны английская и русская фразы до 300 символов")
        if source not in SOURCES:
            raise PhrasebookError(f"неизвестный источник: {source}")
        with self._lock:
            cards, review_log = self._load()
            for card in cards:
                if _key(card.en) == _key(en):
                    return card, False
            today = self._today().isoformat()
            card = Card(secrets.token_hex(6), en, ru, source, today, due=today)
            self._save([*cards, card], review_log)
            return card, True

    def grade(self, card_id: str, correct: bool) -> Card:
        with self._lock:
            cards, review_log = self._load()
            card = self._find(cards, card_id)
            today = self._today()
            box = min(card.box + 1, len(INTERVALS) - 1) if correct else 0
            updated = replace(card, box=box, due=(today + timedelta(days=INTERVALS[box])).isoformat(),
                              reviews=card.reviews + 1, lapses=card.lapses + (0 if correct else 1))
            entry = {"date": today.isoformat(), "card": card_id, "correct": correct}
            self._save([updated if c.id == card_id else c for c in cards], [*review_log, entry])
            return updated

    def delete(self, card_id: str) -> None:
        with self._lock:
            cards, review_log = self._load()
            self._find(cards, card_id)
            self._save([c for c in cards if c.id != card_id], review_log)

    def get(self, card_id: str) -> Card:
        with self._lock:
            return self._find(self._load()[0], card_id)

    def overview(self) -> dict:
        today = self._today()
        with self._lock:
            cards, _ = self._load()
        due = sorted((c for c in cards if c.due <= today.isoformat()), key=lambda c: (c.box, c.due))
        return {
            "total": len(cards),
            "due": len(due),
            "learned": sum(1 for c in cards if c.box >= LEARNED_BOX),
            "queue": [c.public(today) for c in due],
            "cards": [c.public(today) for c in sorted(cards, key=lambda c: c.added, reverse=True)],
        }

    def review_log(self) -> list[dict]:
        with self._lock:
            return self._load()[1]

    @staticmethod
    def _find(cards: list[Card], card_id: str) -> Card:
        for card in cards:
            if card.id == card_id:
                return card
        raise PhrasebookError("такой фразы нет в тетради")
