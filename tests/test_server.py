import http.client
import json
import shutil
import threading

import pytest

from speak.coach import Coach, CoachError
from speak.server import App, serve
from speak.session import SessionStore

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="нужен ффмпег")

TURN_RESULT = {
    "reply": "Oh nice! What did you watch?",
    "feedback": {"ok": False, "corrected": "I went to the cinema.", "natural": "I went to the movies.",
                 "notes": ["went — прошедшее время"], "phrases": []},
}


class FakeCoach(Coach):
    def __init__(self, fail: bool = False) -> None:
        super().__init__("fake")
        self.fail = fail
        self.crash = False
        self.summary_fails = False

    def turn(self, scenario, history, utterance):
        if self.fail:
            raise CoachError("Claude недоступен")
        if self.crash:
            raise RuntimeError("неожиданный сбой")
        return TURN_RESULT

    def hint(self, history):
        return [{"en": "Honestly, I", "ru": "Честно говоря, я"}]

    def summary(self, scenario, history, corrections):
        if self.summary_fails:
            raise CoachError("Claude не ответил")
        return {"verdict": "Хорошо", "strengths": [], "focus": [], "phrases": []}


@pytest.fixture
def server(tmp_path):
    state = {"heard": "I go to cinema"}
    app = App(transcribe=lambda samples: state["heard"], synthesize=lambda text, slow=False: b"RIFFfake",
              coach=FakeCoach(), store=SessionStore(), sessions_dir=tmp_path / "sessions")
    httpd = serve(app, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd, state, app
    httpd.shutdown()
    httpd.server_close()


def request(httpd, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
    hdrs = {"Host": "127.0.0.1", "X-Speak": "1", **(headers or {})}
    if isinstance(body, dict):
        body = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    conn.request(method, path, body=body, headers=hdrs)
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res.status, data


def start_session(httpd):
    status, data = request(httpd, "POST", "/api/session", {"scenario": "smalltalk"})
    assert status == 200
    return json.loads(data)


def events(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.decode().splitlines() if line]


def test_index_embeds_scenarios(server):
    httpd, _, _ = server
    status, data = request(httpd, "GET", "/")
    assert status == 200
    assert b'"key": "smalltalk"' in data
    assert b"/*__SCENARIOS__*/" not in data


def test_static_files_and_unknown_paths(server):
    httpd, _, _ = server
    assert request(httpd, "GET", "/static/app.js")[0] == 200
    assert request(httpd, "GET", "/static/../server.py")[0] == 404
    assert request(httpd, "GET", "/nope")[0] == 404


def test_rejects_foreign_host_and_missing_header(server):
    httpd, _, _ = server
    assert request(httpd, "GET", "/", headers={"Host": "evil.example"})[0] == 403
    status, _ = request(httpd, "POST", "/api/session", {"scenario": "smalltalk"}, headers={"X-Speak": "0"})
    assert status == 403


def test_session_start_returns_opener_with_audio(server):
    httpd, _, _ = server
    session = start_session(httpd)
    assert session["opener"]
    assert session["audio"].startswith("data:audio/wav;base64,")
    assert request(httpd, "POST", "/api/session", {"scenario": "nope"})[0] == 400


@needs_ffmpeg
def test_turn_streams_heard_then_reply_and_counts_stats(server, tone_wav):
    httpd, _, _ = server
    sid = start_session(httpd)["id"]
    status, data = request(httpd, "POST", f"/api/session/{sid}/turn", tone_wav, {"Content-Type": "audio/wav"})
    assert status == 200
    heard, reply = events(data)
    assert heard == {"type": "heard", "text": "I go to cinema", "seconds": 1.0, "words": 4, "wpm": 240}
    assert reply["type"] == "reply" and reply["turn"] == 0
    assert {"text": "went", "op": "insert"} in reply["feedback"]["diff"]
    assert reply["stats"]["turns"] == 1


@needs_ffmpeg
def test_turn_with_silence_emits_empty(server, tone_wav):
    httpd, state, _ = server
    state["heard"] = ""
    sid = start_session(httpd)["id"]
    _, data = request(httpd, "POST", f"/api/session/{sid}/turn", tone_wav)
    assert events(data) == [{"type": "empty"}]


@needs_ffmpeg
def test_turn_reports_coach_failure_in_stream(server, tone_wav):
    httpd, _, app = server
    app.coach.fail = True
    sid = start_session(httpd)["id"]
    _, data = request(httpd, "POST", f"/api/session/{sid}/turn", tone_wav)
    assert events(data)[-1] == {"type": "error", "message": "Claude недоступен"}
    assert app.store.get(sid).turns == ()


@needs_ffmpeg
def test_unexpected_failure_mid_stream_becomes_error_event(server, tone_wav):
    httpd, _, app = server
    app.coach.crash = True
    sid = start_session(httpd)["id"]
    status, data = request(httpd, "POST", f"/api/session/{sid}/turn", tone_wav)
    assert status == 200
    stream = events(data)  # каждая строка — валидный JSON, без второго HTTP-ответа внутри
    assert stream[0]["type"] == "heard"
    assert stream[-1]["type"] == "error"


@needs_ffmpeg
def test_turn_for_session_finished_meanwhile_reports_error(server, tone_wav):
    httpd, _, app = server
    sid = start_session(httpd)["id"]
    original = app.coach.turn

    def finish_then_answer(*args):
        app.store.pop(sid)  # «Завершить» в другой вкладке, пока Claude думал
        return original(*args)

    app.coach.turn = finish_then_answer
    _, data = request(httpd, "POST", f"/api/session/{sid}/turn", tone_wav)
    assert events(data)[-1] == {"type": "error", "message": "разговор уже завершён — начни новый"}


def test_turn_rejects_bad_audio_and_unknown_session(server):
    httpd, _, _ = server
    sid = start_session(httpd)["id"]
    status, data = request(httpd, "POST", f"/api/session/{sid}/turn", b"garbage")
    assert status == 502 and "error" in json.loads(data)
    assert request(httpd, "POST", "/api/session/unknown-session/turn", b"x")[0] == 404


@needs_ffmpeg
def test_repeat_scores_against_natural_version(server, tone_wav):
    httpd, state, _ = server
    sid = start_session(httpd)["id"]
    request(httpd, "POST", f"/api/session/{sid}/turn", tone_wav)
    state["heard"] = "I went to movies"
    status, data = request(httpd, "POST", f"/api/session/{sid}/repeat?turn=0&kind=natural", tone_wav)
    result = json.loads(data)
    assert status == 200 and result["score"] == 80
    assert request(httpd, "POST", f"/api/session/{sid}/repeat?turn=5", tone_wav)[0] == 400


def test_hint_and_speak(server):
    httpd, _, _ = server
    sid = start_session(httpd)["id"]
    status, data = request(httpd, "POST", f"/api/session/{sid}/hint", {})
    assert status == 200 and json.loads(data)["starters"][0]["en"] == "Honestly, I"
    status, data = request(httpd, "POST", "/api/speak", {"text": "Hello there"})
    assert status == 200 and json.loads(data)["audio"].startswith("data:audio/wav")
    assert request(httpd, "POST", "/api/speak", {"text": ""})[0] == 400


@needs_ffmpeg
def test_finish_saves_summary_and_shows_in_history(server, tone_wav):
    httpd, _, app = server
    sid = start_session(httpd)["id"]
    request(httpd, "POST", f"/api/session/{sid}/turn", tone_wav)
    status, data = request(httpd, "POST", f"/api/session/{sid}/finish", {})
    assert status == 200 and json.loads(data)["summary"]["verdict"] == "Хорошо"
    assert len(list(app.sessions_dir.glob("*.json"))) == 1
    _, data = request(httpd, "GET", "/api/history")
    assert json.loads(data)["sessions"][0]["stats"]["turns"] == 1


@needs_ffmpeg
def test_failed_summary_keeps_conversation_for_retry(server, tone_wav):
    httpd, _, app = server
    sid = start_session(httpd)["id"]
    request(httpd, "POST", f"/api/session/{sid}/turn", tone_wav)
    app.coach.summary_fails = True
    assert request(httpd, "POST", f"/api/session/{sid}/finish", {})[0] == 502
    assert app.store.get(sid) is not None
    app.coach.summary_fails = False
    assert request(httpd, "POST", f"/api/session/{sid}/finish", {})[0] == 200
    assert app.store.get(sid) is None


def test_finish_without_turns_saves_nothing(server):
    httpd, _, app = server
    sid = start_session(httpd)["id"]
    _, data = request(httpd, "POST", f"/api/session/{sid}/finish", {})
    assert json.loads(data)["summary"] is None
    assert not app.sessions_dir.exists()


def test_rejects_oversized_and_malformed_json(server):
    httpd, _, _ = server
    assert request(httpd, "POST", "/api/session", b"[1,2]", {"Content-Type": "application/json"})[0] == 400
    assert request(httpd, "POST", "/api/session", b"{bad", {"Content-Type": "application/json"})[0] == 400
    assert request(httpd, "POST", "/api/session", b"x" * 20000)[0] == 413
