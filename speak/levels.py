"""Уровни CEFR: на каком уровне говорит собеседник и какой уровень сейчас у тебя.

Одна реплика — шумная оценка (короткое «Yes, I do» не покажет уровень), поэтому
твой уровень — медиана по последним оценённым репликам, а не последняя оценка.
"""
from __future__ import annotations

from collections.abc import Sequence

LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")
PARTNER_LEVELS = ("A2", "B1", "B2", "C1")
DEFAULT_LEVEL = "B1"   # самооценка пользователя, пока реплик мало
WINDOW = 30            # сколько последних оценок учитывать
MIN_SAMPLES = 5        # меньше — уровень ещё не называем
DIFFICULTY_SHIFT = {"easier": -1, "same": 0, "harder": 1}

PARTNER_STYLE = {
    "A2": "Use very simple, high-frequency words and short sentences (5–10 words), mostly present and past simple. "
          "No idioms or phrasal verbs. Speak clearly and a little slowly.",
    "B1": "Use everyday vocabulary and clear sentences with simple linking words (because, so, but, when). "
          "Avoid idioms; if you use a phrasal verb, make its meaning obvious from context.",
    "B2": "Speak natural conversational English: some phrasal verbs and common idioms, varied tenses, "
          "sentences of normal length, the way a friendly native speaker talks.",
    "C1": "Speak fully natural, fast-paced native English with idioms, nuance, humour and complex sentences, "
          "as you would with a fluent friend.",
}


def is_level(value: object) -> bool:
    return value in LEVELS


def shift(level: str, steps: int) -> str:
    """Сдвиг уровня собеседника в пределах A2–C1."""
    base = level if level in PARTNER_LEVELS else ("A2" if LEVELS.index(level) < 1 else "C1")
    index = min(max(PARTNER_LEVELS.index(base) + steps, 0), len(PARTNER_LEVELS) - 1)
    return PARTNER_LEVELS[index]


def partner_level(learner_level: str, difficulty: str) -> str:
    return shift(learner_level, DIFFICULTY_SHIFT.get(difficulty, 0))


def estimate(assessed: Sequence[str]) -> dict:
    """{level|None, samples, next, next_share}: next_share — % недавних реплик уже на следующем уровне."""
    known = [level for level in assessed if level in LEVELS][-WINDOW:]
    if len(known) < MIN_SAMPLES:
        return {"level": None, "samples": len(known), "next": None, "next_share": 0, "need": MIN_SAMPLES - len(known)}
    ranks = sorted(LEVELS.index(level) for level in known)
    current = LEVELS[ranks[(len(ranks) - 1) // 2]]
    nxt = LEVELS[LEVELS.index(current) + 1] if current != LEVELS[-1] else None
    share = round(100 * sum(1 for r in ranks if nxt and r >= LEVELS.index(nxt)) / len(ranks)) if nxt else 100
    return {"level": current, "samples": len(known), "next": nxt, "next_share": share, "need": 0}


def median_level(assessed: Sequence[str]) -> str | None:
    known = sorted(LEVELS.index(level) for level in assessed if level in LEVELS)
    return LEVELS[known[(len(known) - 1) // 2]] if known else None
