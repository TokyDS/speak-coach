"""Разбор реплик, подсказки и итоги: разовые вызовы `claude -p` со строгой JSON-схемой ответа.

Работает через установленный Claude Code (твоя подписка) — API-ключ не нужен. Вызов урезан:
свой системный промпт, без инструментов, MCP и чужих настроек, без сохранения сессии.
Живой собеседник, который отвечает голосом, — в partner.py.
"""
from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .levels import LEVELS
from .scenarios import Scenario

CLAUDE_TIMEOUT = 120
HISTORY_LINES = 14
PARTNER_NAME = "Alex"
# Если в окружении есть ключ API, claude -p возьмёт его вместо подписки — и запросы станут платными.
_BILLING_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
CLI_FLAGS = ("--tools", "", "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence")

LEARNER = (
    "The learner is a Russian-speaking university student and .NET backend developer. "
    "They understand spoken English well (songs, films, YouTube) but have very little speaking practice, "
    "so they need confidence and lots of chances to talk. "
    "Anything you write in Russian addresses the learner informally, as «ты», and avoids gendered forms "
    "(«тебе удалось», not «ты смог/смогла»), since their gender is unknown."
)

_PHRASE = {"type": "object", "properties": {"en": {"type": "string"}, "ru": {"type": "string"}},
           "required": ["en", "ru"]}
_STRINGS = {"type": "array", "items": {"type": "string"}}
_LEVEL = {"type": "string", "enum": [*LEVELS, "unknown"]}

FEEDBACK_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "corrected": {"type": "string"},
        "natural": {"type": "string"},
        "natural_ru": {"type": "string"},
        "notes": _STRINGS,
        "phrases": {"type": "array", "items": _PHRASE},
        "level": _LEVEL,
    },
    "required": ["ok", "corrected", "natural", "natural_ru", "notes", "phrases", "level"],
}
_FEEDBACK_KEYS = tuple(FEEDBACK_SCHEMA["required"])

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
        "level": _LEVEL,
        "level_note": {"type": "string"},
    },
    "required": ["verdict", "strengths", "focus", "phrases", "level", "level_note"],
}


class CoachError(Exception):
    """Claude не ответил или ответил не по схеме."""


Runner = Callable[..., subprocess.CompletedProcess]


def subscription_env() -> dict[str, str]:
    """Окружение для claude без ключей API: платим только подпиской."""
    return {k: v for k, v in os.environ.items() if k not in _BILLING_ENV}


def ask_claude(system: str, prompt: str, schema: dict, model: str,
               run: Runner = subprocess.run) -> dict:
    cmd = ["claude", "-p", "--model", model, "--output-format", "json",
           "--system-prompt", system, "--json-schema", json.dumps(schema), *CLI_FLAGS]
    try:
        proc = run(cmd, input=prompt, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT, check=False,
                   env=subscription_env())
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


FEEDBACK_SYSTEM = f"""You are an English speaking coach. {LEARNER}

You get a practice conversation and the learner's LAST utterance. Their words come from speech recognition:
ignore punctuation, capitalisation, filler words (um, uh) and obvious recognition glitches.
- `corrected`: their utterance with grammar and word choice fixed, changing as little as possible.
  If it was already fine, repeat it unchanged and set `ok` to true.
- `natural`: how a native speaker would casually say the same thing (may equal `corrected`).
- `natural_ru`: a translation of `natural` entirely into natural Russian, with no English words left in it.
- `notes`: at most 3 short explanations IN RUSSIAN of real mistakes, most important first, each like
  «went, а не go: прошедшее время после yesterday». Empty list if there were no real mistakes.
- `phrases`: 0–2 useful English expressions that would help in this conversation; `ru` is a translation
  entirely in Russian, with no English words left in it.
- `level`: the CEFR level this utterance demonstrates (range of grammar and vocabulary, accuracy, how much
  they could express). Judge only what they actually produced. Use "unknown" if it is too short or simple to
  tell (e.g. "Yes, I do", "I don't know")."""


def feedback_prompt(history: Sequence[Line], utterance: str) -> str:
    return (f"Conversation so far:\n{format_history(history)}\n\n"
            f"The learner just said (speech recognition transcript):\n\"{utterance}\"")


def hint_system(level: str) -> str:
    return f"""You help an English learner who got stuck in a conversation. {LEARNER}
Give 3 different sentence starters (5–10 words each, {level} level, spoken style) the learner could use to begin
answering the partner's last question in their own way, each with a Russian translation in `ru`."""


SUMMARY_SYSTEM = f"""You are a kind, honest English speaking coach reviewing a practice conversation. {LEARNER}
Write everything for the learner IN RUSSIAN except English examples.
- `verdict`: 2–3 sentences on how the conversation went and one concrete thing to try next time.
- `strengths`: 1–3 specific things they did well.
- `focus`: up to 3 recurring mistake patterns; each with a short `topic` in Russian, a `wrong` example
  taken from their own words and the `right` version.
- `phrases`: 3–5 useful English phrases for this topic that they did not use yet; `ru` is a translation
  entirely in Russian, with no English words left in it.
- `level`: the CEFR level their speaking showed in this conversation overall ("unknown" if too little to tell).
- `level_note`: one or two encouraging sentences in Russian: what already fits this level and the single most
  useful thing that would move them to the next one."""


def summary_prompt(scenario: Scenario, history: Sequence[Line], corrections: Sequence[tuple[str, str]]) -> str:
    fixes = "\n".join(f"- said: \"{said}\" → better: \"{better}\"" for said, better in corrections) or "- none"
    return (f"Scenario: {scenario.title} ({scenario.role})\n\nConversation:\n{format_history(history)}\n\n"
            f"Corrections already shown during the conversation:\n{fixes}")


class Coach:
    def __init__(self, model: str, ask: Callable[..., dict] = ask_claude) -> None:
        self.model = model
        self._ask = ask

    def feedback(self, history: Sequence[Line], utterance: str) -> dict:
        data = self._ask(FEEDBACK_SYSTEM, feedback_prompt(history, utterance), FEEDBACK_SCHEMA, self.model)
        if any(key not in data for key in _FEEDBACK_KEYS):
            raise CoachError("разбор пришёл неполным")
        return data

    def hint(self, history: Sequence[Line], level: str) -> list[dict]:
        prompt = f"Conversation so far:\n{format_history(history)}"
        return self._ask(hint_system(level), prompt, HINT_SCHEMA, self.model).get("starters", [])

    def summary(self, scenario: Scenario, history: Sequence[Line],
                corrections: Sequence[tuple[str, str]]) -> dict:
        return self._ask(SUMMARY_SYSTEM, summary_prompt(scenario, history, corrections),
                         SUMMARY_SCHEMA, self.model)
