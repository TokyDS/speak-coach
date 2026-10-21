import json
import subprocess

import pytest

from speak import scenarios
from speak.coach import (HISTORY_LINES, Coach, CoachError, Line, ask_claude, format_history,
                         turn_prompt, turn_system)


def _proc(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_ask_claude_runs_stripped_down_cli_and_returns_structured_output():
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


@pytest.mark.parametrize("proc, fragment", [
    (_proc("not json", 1, "boom"), "код 1"),
    (_proc(json.dumps({"is_error": True, "result": "rate limited"})), "rate limited"),
    (_proc(json.dumps({"is_error": False, "result": "text only"})), "не в том формате"),
])
def test_ask_claude_raises_readable_errors(proc, fragment):
    with pytest.raises(CoachError, match=fragment):
        ask_claude("s", "p", {}, "m", run=lambda *a, **k: proc)


def test_ask_claude_reports_missing_cli():
    def run(*args, **kwargs):
        raise FileNotFoundError("claude")

    with pytest.raises(CoachError, match="не найдена команда claude"):
        ask_claude("s", "p", {}, "m", run=run)


def test_ask_claude_reports_timeout():
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired("claude", 1)

    with pytest.raises(CoachError, match="две минуты"):
        ask_claude("s", "p", {}, "m", run=run)


def test_history_is_trimmed_to_recent_lines():
    lines = [Line("partner" if i % 2 else "learner", f"line {i}") for i in range(HISTORY_LINES + 6)]
    text = format_history(lines)
    assert "line 0" not in text
    assert text.splitlines()[-1] == f"Alex: line {HISTORY_LINES + 5}"


def test_turn_prompts_carry_scenario_and_utterance():
    scenario = scenarios.get("job_interview")
    assert scenario.role in turn_system(scenario)
    prompt = turn_prompt([Line("partner", "Tell me about yourself.")], "I am backend developer")
    assert "Alex: Tell me about yourself." in prompt
    assert '"I am backend developer"' in prompt


def test_coach_rejects_empty_reply():
    coach = Coach("m", ask=lambda *a: {"reply": "  ", "feedback": {}})
    with pytest.raises(CoachError):
        coach.turn(scenarios.SCENARIOS[0], [], "hello")


def test_coach_rejects_incomplete_feedback():
    coach = Coach("m", ask=lambda *a: {"reply": "Cool!", "feedback": {"ok": True}})
    with pytest.raises(CoachError, match="неполным"):
        coach.turn(scenarios.SCENARIOS[0], [], "hello")


def test_coach_hint_and_summary_pass_through():
    seen = []

    def ask(system, prompt, schema, model):
        seen.append((schema, model))
        return {"starters": [{"en": "Well, I think", "ru": "Ну, я думаю"}], "verdict": "ok"}

    coach = Coach("haiku", ask=ask)
    assert coach.hint([Line("partner", "Why?")])[0]["en"] == "Well, I think"
    assert coach.summary(scenarios.SCENARIOS[0], [], [("a", "b")])["verdict"] == "ok"
    assert all(model == "haiku" for _, model in seen)
