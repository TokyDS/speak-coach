"""Собеседник и разбор ошибок: вызовы `claude -p` со строгой JSON-схемой ответа.

Работает через установленный Claude Code (твоя подписка) — API-ключ не нужен. Вызов урезан:
свой системный промпт, без инструментов, MCP и чужих настроек, без сохранения сессии.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .scenarios import Scenario

CLAUDE_TIMEOUT = 120
HISTORY_LINES = 14
PARTNER_NAME = "Alex"

_LEARNER = (
    "The learner is a Russian-speaking university student and .NET backend developer with B1 English. "
    "They understand spoken English well (songs, films, YouTube) but have very little speaking practice, "
    "so they need confidence and lots of chances to talk. "
    "Anything you write in Russian addresses the learner informally, as «ты»."
)

_PHRASE = {"type": "object", "properties": {"en": {"type": "string"}, "ru": {"type": "string"}},
           "required": ["en", "ru"]}
_STRINGS = {"type": "array", "items": {"type": "string"}}

TURN_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "feedback": {
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "corrected": {"type": "string"},
                "natural": {"type": "string"},
                "notes": _STRINGS,
                "phrases": {"type": "array", "items": _PHRASE},
            },
            "required": ["ok", "corrected", "natural", "notes", "phrases"],
        },
    },
    "required": ["reply", "feedback"],
}

_FEEDBACK_KEYS = tuple(TURN_SCHEMA["properties"]["feedback"]["required"])

HINT_SCHEMA = {
    "type": "object",
    "properties": {"starters": {"type": "array", "items": _PHRASE}},
    "required": ["starters"],
}

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string"},
        "strengths": _STRINGS,
        "focus": {"type": "array", "items": {
            "type": "object",
            "properties": {"topic": {"type": "string"}, "wrong": {"type": "string"}, "right": {"type": "string"}},
            "required": ["topic", "wrong", "right"]}},
        "phrases": {"type": "array", "items": _PHRASE},
    },
    "required": ["verdict", "strengths", "focus", "phrases"],
}


class CoachError(Exception):
    """Claude не ответил или ответил не по схеме."""


Runner = Callable[..., subprocess.CompletedProcess]


def ask_claude(system: str, prompt: str, schema: dict, model: str,
               run: Runner = subprocess.run) -> dict:
    cmd = ["claude", "-p", "--model", model, "--output-format", "json",
           "--system-prompt", system, "--json-schema", json.dumps(schema),
           "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence"]
    try:
        proc = run(cmd, input=prompt, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT, check=False)
    except FileNotFoundError as exc:
        raise CoachError("не найдена команда claude — установи Claude Code и войди в аккаунт") from exc
    except subprocess.TimeoutExpired as exc:
        raise CoachError("Claude не ответил за две минуты, попробуй ещё раз") from exc
    return _parse_cli_output(proc)


def _parse_cli_output(proc: subprocess.CompletedProcess) -> dict:
    try:
        envelope = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        detail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise CoachError(f"claude завершился с ошибкой (код {proc.returncode}): {detail}") from None
    if envelope.get("is_error"):
        raise CoachError(f"ошибка Claude: {str(envelope.get('result', ''))[:300]}")
    data = envelope.get("structured_output")
    if not isinstance(data, dict):
        raise CoachError("Claude ответил не в том формате, попробуй ещё раз")
    return data


@dataclass(frozen=True)
class Line:
    speaker: str   # "partner" | "learner"
    text: str


def format_history(lines: Sequence[Line]) -> str:
    recent = lines[-HISTORY_LINES:]
    return "\n".join(f"{PARTNER_NAME if l.speaker == 'partner' else 'Learner'}: {l.text}" for l in recent)


def turn_system(scenario: Scenario) -> str:
    return f"""You are {PARTNER_NAME}, an English conversation partner. {_LEARNER}

Scenario: {scenario.role}

You return two things.

`reply` — what you say next. It is read aloud by text-to-speech, so:
- 1 to 3 short sentences of natural spoken English at B1–B2 level; contractions are good.
- No markdown, lists, emoji, brackets or stage directions.
- React to what the learner said (show interest, add a little of your own opinion or experience),
  then ask exactly ONE open question that invites a longer answer. Stay in the scenario.
- Never correct mistakes inside `reply`; feedback is shown separately.
- If the learner's words are empty, cut off or make no sense, kindly ask them to say it again.
- If the learner asks you something, answer briefly before asking your question.

`feedback` — about the learner's LAST utterance only. Their words come from speech recognition:
ignore punctuation, capitalisation, filler words (um, uh) and obvious recognition glitches.
- `corrected`: their utterance with grammar and word choice fixed, changing as little as possible.
  If it was already fine, repeat it unchanged and set `ok` to true.
- `natural`: how a native speaker would casually say the same thing (may equal `corrected`).
- `notes`: at most 3 short explanations IN RUSSIAN of real mistakes, most important first, each like
  «went, а не go: прошедшее время после yesterday». Empty list if there were no real mistakes.
- `phrases`: 0–2 useful English expressions that would help in this conversation, with Russian translation."""


def turn_prompt(history: Sequence[Line], utterance: str) -> str:
    return (f"Conversation so far:\n{format_history(history)}\n\n"
            f"The learner just said (speech recognition transcript):\n\"{utterance}\"")


HINT_SYSTEM = f"""You help an English learner who got stuck in a conversation. {_LEARNER}
Give 3 different sentence starters (5–10 words each, B1 level, spoken style) the learner could use to begin
answering the partner's last question in their own way, each with a Russian translation in `ru`."""

SUMMARY_SYSTEM = f"""You are a kind, honest English speaking coach reviewing a practice conversation. {_LEARNER}
Write everything for the learner IN RUSSIAN except English examples.
- `verdict`: 2–3 sentences on how the conversation went and one concrete thing to try next time.
- `strengths`: 1–3 specific things they did well.
- `focus`: up to 3 recurring mistake patterns; each with a short `topic` in Russian, a `wrong` example
  taken from their own words and the `right` version.
- `phrases`: 3–5 useful English phrases for this topic that they did not use yet, with Russian translation."""


def summary_prompt(scenario: Scenario, history: Sequence[Line], corrections: Sequence[tuple[str, str]]) -> str:
    fixes = "\n".join(f"- said: \"{said}\" → better: \"{better}\"" for said, better in corrections) or "- none"
    return (f"Scenario: {scenario.title} ({scenario.role})\n\nConversation:\n{format_history(history)}\n\n"
            f"Corrections already shown during the conversation:\n{fixes}")


class Coach:
    def __init__(self, model: str, ask: Callable[..., dict] = ask_claude) -> None:
        self._model = model
        self._ask = ask

    def turn(self, scenario: Scenario, history: Sequence[Line], utterance: str) -> dict:
        data = self._ask(turn_system(scenario), turn_prompt(history, utterance), TURN_SCHEMA, self._model)
        if not str(data.get("reply", "")).strip():
            raise CoachError("собеседник промолчал, попробуй ещё раз")
        feedback = data.get("feedback")
        if not isinstance(feedback, dict) or any(key not in feedback for key in _FEEDBACK_KEYS):
            raise CoachError("разбор пришёл неполным, попробуй ещё раз")
        return data

    def hint(self, history: Sequence[Line]) -> list[dict]:
        prompt = f"Conversation so far:\n{format_history(history)}"
        return self._ask(HINT_SYSTEM, prompt, HINT_SCHEMA, self._model).get("starters", [])

    def summary(self, scenario: Scenario, history: Sequence[Line],
                corrections: Sequence[tuple[str, str]]) -> dict:
        return self._ask(SUMMARY_SYSTEM, summary_prompt(scenario, history, corrections),
                         SUMMARY_SCHEMA, self._model)
