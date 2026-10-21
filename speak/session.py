"""Разговор в памяти сервера, статистика речи и сохранённые разговоры.

Разговор пишется на диск после каждой реплики (а не только по «Завершить»), чтобы
закрытая вкладка не теряла ни прогресс, ни оценки уровня. Итоги дописываются в тот же файл.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from .coach import Line
from .levels import median_level
from .scenarios import Scenario
from .textdiff import count_words

log = logging.getLogger(__name__)

HISTORY_LIMIT = 8
MAX_SESSIONS = 20


@dataclass(frozen=True)
class Turn:
    heard: str
    seconds: float
    reply: str = ""
    feedback: dict = field(default_factory=dict)
    at: datetime = field(default_factory=datetime.now)

    @property
    def words(self) -> int:
        return count_words(self.heard)

    @property
    def level(self) -> str:
        return self.feedback.get("level", "unknown")

    def record(self) -> dict:
        return {"at": self.at.isoformat(timespec="seconds"), "heard": self.heard, "seconds": round(self.seconds, 1),
                "words": self.words, "ok": bool(self.feedback.get("ok")), "level": self.level}


def words_per_minute(words: int, seconds: float) -> int:
    return round(words * 60 / seconds) if seconds >= 1 else 0


@dataclass(frozen=True)
class Session:
    id: str
    scenario: Scenario
    slow: bool
    opener: str
    started: datetime
    partner_level: str = "B1"
    turns: tuple[Turn, ...] = ()

    @property
    def file_name(self) -> str:
        return f"{self.started:%Y%m%d-%H%M%S}-{self.scenario.key}.json"

    def with_turn(self, turn: Turn) -> Session:
        return replace(self, turns=(*self.turns, turn))

    def history(self) -> list[Line]:
        lines = [Line("partner", self.opener)]
        for turn in self.turns:
            lines.append(Line("learner", turn.heard))
            if turn.reply:
                lines.append(Line("partner", turn.reply))
        return lines

    def corrections(self) -> list[tuple[str, str]]:
        return [(t.heard, t.feedback["corrected"]) for t in self.turns
                if t.feedback.get("corrected") and not t.feedback.get("ok")]

    def stats(self) -> dict:
        words = sum(t.words for t in self.turns)
        seconds = sum(t.seconds for t in self.turns)
        return {
            "turns": len(self.turns),
            "words": words,
            "seconds": round(seconds),
            "wpm": words_per_minute(words, seconds),
            "clean": sum(1 for t in self.turns if t.feedback.get("ok")),
            "level": median_level([t.level for t in self.turns]),
        }


class SessionStore:
    """Активные разговоры. Старые вытесняются, чтобы память не росла бесконечно."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, scenario: Scenario, slow: bool, opener: str, partner_level: str = "B1") -> Session:
        session = Session(secrets.token_urlsafe(9), scenario, slow, opener, datetime.now(), partner_level)
        with self._lock:
            if len(self._sessions) >= MAX_SESSIONS:
                oldest = min(self._sessions.values(), key=lambda s: s.started)
                del self._sessions[oldest.id]
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def add_turn(self, session_id: str, turn: Turn) -> Session | None:
        """None — разговор успели завершить или вытеснить, пока Claude думал."""
        with self._lock:
            current = self._sessions.get(session_id)
            if current is None:
                return None
            session = current.with_turn(turn)
            self._sessions[session_id] = session
            return session

    def pop(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.pop(session_id, None)


def save(session: Session, summary: dict | None, directory: Path) -> Path:
    """Записывает разговор целиком (атомарно: сначала во временный файл, потом подмена)."""
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "started": session.started.isoformat(timespec="seconds"),
        "scenario": session.scenario.key,
        "title": session.scenario.title,
        "partner_level": session.partner_level,
        "stats": session.stats(),
        "summary": summary,
        "turns": [t.record() for t in session.turns],
        "transcript": [{"speaker": l.speaker, "text": l.text} for l in session.history()],
        "corrections": [{"said": s, "better": b} for s, b in session.corrections()],
    }
    path = directory / session.file_name
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_records(directory: Path, limit: int | None = None) -> list[dict]:
    """Сохранённые разговоры, новые первыми. Повреждённые файлы пропускаются."""
    if not directory.is_dir():
        return []
    paths = sorted(directory.glob("*.json"), reverse=True)
    records = []
    for path in paths[:limit] if limit else paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))  # ValueError: битый JSON или UTF-8
        except (OSError, ValueError) as exc:
            log.warning("пропускаю повреждённый файл %s: %s", path.name, exc)
            continue
        if not isinstance(record, dict):
            log.warning("пропускаю файл неожиданного вида: %s", path.name)
            continue
        records.append(record)
    return records


def history_item(record: dict) -> dict:
    summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
    return {
        "started": record.get("started", ""),
        "title": record.get("title", ""),
        "stats": record.get("stats", {}),
        "focus": summary.get("focus", []),
        "phrases": summary.get("phrases", []),
    }


def load_history(directory: Path, limit: int = HISTORY_LIMIT) -> list[dict]:
    return [history_item(r) for r in load_records(directory, limit)]


def assessed_levels(records: list[dict]) -> list[str]:
    """Оценки уровня всех реплик, от старых к новым."""
    levels = []
    for record in reversed(records):
        turns = record.get("turns")
        if isinstance(turns, list):
            levels.extend(t.get("level", "unknown") for t in turns if isinstance(t, dict))
    return levels
