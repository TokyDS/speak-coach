import json
import subprocess

import pytest

from speak import scenarios
from speak.coach import (FEEDBACK_SCHEMA, HISTORY_LINES, SUMMARY_SCHEMA, Coach, CoachError, Line, ask_claude,
                         feedback_prompt, format_history, hint_system, subscription_env)


def _proc(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_ask_claude_runs_stripped_down_cli_on_subscription(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    calls = {}

    def run(cmd, **kwargs):
        calls["cmd"], calls["kwargs"] = cmd, kwargs
        return _proc(json.dumps({"is_error": False, "structured_output": {"reply": "Hi"}}))

    result = ask_claude("SYS", "PROMPT", {"type": "object"}, "sonnet", run=run)

    assert result == {"reply": "Hi"}
    cmd = calls["cmd"]
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--no-session-persistence" in cmd and "--strict-mcp-config" in cmd
    assert calls["kwargs"]["input"] == "PROMPT"  # промпт идёт через stdin, а не argv
    env = calls["kwargs"]["env"]                 # ключи API не доходят до claude — платит подписка
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env
    assert "PATH" in env


def test_subscription_env_keeps_everything_else(monkeypatch):
    monkeypatch.setenv("SPEAK_SOMETHING", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk")
    env = subscription_env()
    assert env["SPEAK_SOMETHING"] == "1" and "ANTHROPIC_API_KEY" not in env


@pytest.mark.parametrize("proc, fragment", [
    (_proc("not json", 1, "boom"), "код 1"),
    (_proc(json.dumps({"is_error": True, "result": "rate limited"})), "rate limited"),
    (_proc(json.dumps({"is_error": False, "result": "text only"})), "не в том формате"),
])
def test_ask_claude_raises_readable_errors(proc, fragment):
    with pytest.raises(CoachError, match=fragment):
        ask_claude("s", "p", {}, "m", run=lambda *a, **k: proc)


@pytest.mark.parametrize("error, fragment", [
    (FileNotFoundError("claude"), "не найдена команда claude"),
    (subprocess.TimeoutExpired("claude", 1), "две минуты"),
])
def test_ask_claude_reports_missing_cli_and_timeout(error, fragment):
    def run(*args, **kwargs):
        raise error

    with pytest.raises(CoachError, match=fragment):
        ask_claude("s", "p", {}, "m", run=run)


def test_history_is_trimmed_to_recent_lines():
    lines = [Line("partner" if i % 2 else "learner", f"line {i}") for i in range(HISTORY_LINES + 6)]
    text = format_history(lines)
    assert "line 0" not in text
    assert text.splitlines()[-1] == f"Alex: line {HISTORY_LINES + 5}"


def test_feedback_prompt_and_schema_ask_for_level_and_translation():
    prompt = feedback_prompt([Line("partner", "Tell me about yourself.")], "I am backend developer")
    assert "Alex: Tell me about yourself." in prompt and '"I am backend developer"' in prompt
    assert {"level", "natural_ru"} <= set(FEEDBACK_SCHEMA["required"])
    assert "unknown" in FEEDBACK_SCHEMA["properties"]["level"]["enum"]
    assert {"level", "level_note"} <= set(SUMMARY_SCHEMA["required"])
    assert "A2 level" in hint_system("A2")


FULL = {"ok": True, "corrected": "Hi", "natural": "Hi", "natural_ru": "Привет", "notes": [], "phrases": [],
        "level": "unknown"}


def test_feedback_validates_keys():
    assert Coach("m", ask=lambda *a: FULL).feedback([], "Hi") == FULL
    with pytest.raises(CoachError, match="неполным"):
        Coach("m", ask=lambda *a: {"ok": True}).feedback([], "hello")


def test_coach_hint_and_summary_pass_through_model_and_level():
    seen = []

    def ask(system, prompt, schema, model):
        seen.append((system, model))
        return {"starters": [{"en": "Well, I think", "ru": "Ну, я думаю"}], "verdict": "ok"}

    coach = Coach("haiku", ask=ask)
    assert coach.hint([Line("partner", "Why?")], "B2")[0]["en"] == "Well, I think"
    assert coach.summary(scenarios.SCENARIOS[0], [], [("a", "b")])["verdict"] == "ok"
    assert all(model == "haiku" for _, model in seen)
    assert "B2 level" in seen[0][0]
