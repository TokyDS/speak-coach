import http.client
import json
import shutil
import threading
from datetime import date

import pytest

from speak.coach import Coach, CoachError
from speak.phrasebook import Phrasebook
from speak.server import App, serve
from speak.session import SessionStore
from speak.voice import Transcript, Word

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="нужен ffmpeg")

FEEDBACK = {"ok": False, "corrected": "I went to the cinema.", "natural": "I went to the movies.",
            "natural_ru": "Я ходил в кино.", "notes": ["went — прошедшее время"], "phrases": [], "level": "A2"}
REPLY = ["Oh nice, that sounds like ", "a fun evening! ", "What did you watch?"]


class FakeCoach(Coach):
    def __init__(self) -> None:
        super().__init__("fake")
        self.feedback_fails = False
        self.summary_fails = False

    def feedback(self, history, utterance):
        if self.feedback_fails:
            raise CoachError("Claude недоступен")
        return FEEDBACK

    def hint(self, history, level):
        return [{"en": f"Honestly ({level}), I", "ru": "Честно говоря, я"}]

    def summary(self, scenario, history, corrections):
        if self.summary_fails:
            raise CoachError("Claude не ответил")
        return {"verdict": "Хорошо", "strengths": [], "focus": [], "phrases": [], "level": "A2", "level_note": "…"}


class FakePartners:
    def __init__(self) -> None:
        self.mode = "ok"
        self.warmed: list[tuple[str, str]] = []
        self.closed: list[str] = []
        self.on_reply = None

    def warm(self, session_id, system):
        self.warmed.append((session_id, system))

    def reply(self, session_id, system, history, utterance):
        if self.on_reply:
            self.on_reply()
        if self.mode == "fail":
            raise CoachError("собеседник отключился")
        if self.mode == "crash":
            raise RuntimeError("неожиданный сбой")
        if self.mode == "silent":
            return
        yield from REPLY

    def close(self, session_id):
        self.closed.append(session_id)

    def close_all(self):
        pass


@pytest.fixture
def server(tmp_path):
    state = {"transcript": Transcript("I go to cinema", (Word("I", 0.0, 0.2, 0.99), Word("go", 0.2, 0.4, 0.3),
                                                         Word("to", 0.4, 0.5, 0.9), Word("cinema", 0.5, 1.0, 0.95)))}
    app = App(transcribe=lambda samples: state["transcript"], synthesize=lambda text, slow=False: b"RIFFfake",
              coach=FakeCoach(), partners=FakePartners(), store=SessionStore(),
              sessions_dir=tmp_path / "sessions", phrasebook=Phrasebook(tmp_path / "phrasebook.json"))
    httpd = serve(app, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
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


def start_session(httpd, **options):
    status, data = request(httpd, "POST", "/api/session", {"scenario": "smalltalk", **options})
    assert status == 200, data
    return json.loads(data)


def events(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.decode().splitlines() if line]


# ---------- страница и защита ----------

def test_index_embeds_config(server):
    httpd, _, _ = server
    status, data = request(httpd, "GET", "/")
    assert status == 200
    assert b'"key": "smalltalk"' in data and b'"levels": ["A2", "B1", "B2", "C1"]' in data
    assert b"/*__CONFIG__*/" not in data


def test_static_whitelist(server):
    httpd, _, _ = server
    assert request(httpd, "GET", "/static/js/main.js")[0] == 200
    assert request(httpd, "GET", "/static/app.css")[0] == 200
    assert request(httpd, "GET", "/static/../server.py")[0] == 404
    assert request(httpd, "GET", "/static/js/../../server.py")[0] == 404
    assert request(httpd, "GET", "/nope")[0] == 404


def test_rejects_foreign_host_and_missing_header(server):
    httpd, _, _ = server
    assert request(httpd, "GET", "/", headers={"Host": "evil.example"})[0] == 403
    status, _ = request(httpd, "POST", "/api/session", {"scenario": "smalltalk"}, headers={"X-Speak": "0"})
    assert status == 403


# ---------- уровни при старте ----------

def test_session_start_warms_partner_at_default_level(server):
    httpd, _, app = server
    session = start_session(httpd)
    assert session["audio"].startswith("data:audio/wav;base64,")
    assert session["partner_level"] == "B1" and session["learner_level"] == "B1" and not session["estimated"]
    sid, system = app.partners.warmed[0]
    assert sid == session["id"] and "CEFR B1" in system and session["opener"] in system


def test_session_level_follows_manual_choice_and_difficulty(server):
    httpd, _, _ = server
    assert start_session(httpd, level="B2", difficulty="harder")["partner_level"] == "C1"
    assert start_session(httpd, level="auto", difficulty="easier")["partner_level"] == "A2"
    assert request(httpd, "POST", "/api/session", {"scenario": "smalltalk", "difficulty": "max"})[0] == 400
    assert request(httpd, "POST", "/api/session", {"scenario": "nope"})[0] == 400


def test_session_level_uses_estimate_from_saved_turns(server):
    httpd, _, app = server
    app.sessions_dir.mkdir()
    turns = [{"at": "2026-10-20T10:00:00", "level": "B2", "seconds": 3, "words": 9, "ok": True}] * 6
    (app.sessions_dir / "20261020-100000-smalltalk.json").write_text(json.dumps({"turns": turns}), encoding="utf-8")
    session = start_session(httpd)
    assert session["learner_level"] == "B2" and session["estimated"] and session["partner_level"] == "B2"


# ---------- ход разговора ----------

@needs_ffmpeg
def test_turn_streams_heard_sentences_then_feedback_and_autosaves(server, tone_wav):
    httpd, _, app = server
    sid = start_session(httpd)["id"]
    status, data = request(httpd, "POST", f"/api/session/{sid}/turn", tone_wav, {"Content-Type": "audio/wav"})
    assert status == 200
    stream = events(data)
    heard, says, feedback = stream[0], stream[1:-1], stream[-1]
    assert heard["type"] == "heard" and heard["text"] == "I go to cinema" and heard["wpm"] == 240
    assert [t["text"] for t in heard["tokens"] if t["unsure"]] == ["go"]
    assert [s["text"] for s in says] == ["Oh nice, that sounds like a fun evening!", "What did you watch?"]
    assert all(s["audio"].startswith("data:audio/wav") for s in says)
    assert feedback["type"] == "feedback" and feedback["error"] is None and feedback["turn"] == 0
    assert {"text": "went", "op": "insert"} in feedback["feedback"]["diff"]
    assert feedback["stats"]["level"] == "A2"
    saved = json.loads(next(app.sessions_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert saved["summary"] is None and saved["turns"][0]["level"] == "A2"
    assert app.store.get(sid).turns[0].reply == "Oh nice, that sounds like a fun evening! What did you watch?"


@needs_ffmpeg
def test_feedback_failure_keeps_conversation_going(server, tone_wav):
    httpd, _, app = server
    app.coach.feedback_fails = True
    sid = start_session(httpd)["id"]
    stream = events(request(httpd, "POST", f"/api/session/{sid}/turn", tone_wav)[1])
    assert [e["type"] for e in stream] == ["heard", "say", "say", "feedback"]
    assert stream[-1]["feedback"] is None and "Claude недоступен" in stream[-1]["error"]
    assert len(app.store.get(sid).turns) == 1


@needs_ffmpeg
@pytest.mark.parametrize("mode, message", [("fail", "собеседник отключился"),
                                           ("silent", "собеседник промолчал, попробуй ещё раз"),
                                           ("crash", "внутренняя ошибка, подробности в терминале")])
def test_partner_problems_become_error_events(server, tone_wav, mode, message):
    httpd, _, app = server
    app.partners.mode = mode
    sid = start_session(httpd)["id"]
    status, data = request(httpd, "POST", f"/api/session/{sid}/turn", tone_wav)
    stream = events(data)  # каждая строка — валидный JSON, без второго HTTP-ответа внутри
    assert status == 200 and stream[0]["type"] == "heard"
    assert stream[-1] == {"type": "error", "message": message}
    assert app.store.get(sid).turns == ()


@needs_ffmpeg
def test_turn_with_silence_emits_empty(server, tone_wav):
    httpd, state, _ = server
    state["transcript"] = Transcript("")
    sid = start_session(httpd)["id"]
    assert events(request(httpd, "POST", f"/api/session/{sid}/turn", tone_wav)[1]) == [{"type": "empty"}]


@needs_ffmpeg
def test_turn_for_session_finished_meanwhile_reports_error(server, tone_wav):
    httpd, _, app = server
    sid = start_session(httpd)["id"]
    app.partners.on_reply = lambda: app.store.pop(sid)  # «Завершить» в другой вкладке, пока Alex отвечал
    stream = events(request(httpd, "POST", f"/api/session/{sid}/turn", tone_wav)[1])
    assert stream[-1] == {"type": "error", "message": "разговор уже завершён — начни новый"}


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
    state["transcript"] = Transcript("I went to movies")
    status, data = request(httpd, "POST", f"/api/session/{sid}/repeat?turn=0&kind=natural", tone_wav)
    assert status == 200 and json.loads(data)["score"] == 80
    assert request(httpd, "POST", f"/api/session/{sid}/repeat?turn=5", tone_wav)[0] == 400
    assert request(httpd, "POST", f"/api/session/{sid}/repeat?turn=0&kind=x", tone_wav)[0] == 400


def test_hint_uses_partner_level_and_speak(server):
    httpd, _, _ = server
    sid = start_session(httpd, level="C1")["id"]
    status, data = request(httpd, "POST", f"/api/session/{sid}/hint", {})
    assert status == 200 and json.loads(data)["starters"][0]["en"] == "Honestly (C1), I"
    status, data = request(httpd, "POST", "/api/speak", {"text": "Hello there"})
    assert status == 200 and json.loads(data)["audio"].startswith("data:audio/wav")
    assert request(httpd, "POST", "/api/speak", {"text": ""})[0] == 400


# ---------- завершение ----------

@needs_ffmpeg
def test_finish_saves_summary_closes_partner_and_shows_in_history(server, tone_wav):
    httpd, _, app = server
    sid = start_session(httpd)["id"]
    request(httpd, "POST", f"/api/session/{sid}/turn", tone_wav)
    status, data = request(httpd, "POST", f"/api/session/{sid}/finish", {})
    assert status == 200 and json.loads(data)["summary"]["level"] == "A2"
    assert sid in app.partners.closed
    files = list(app.sessions_dir.glob("*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text(encoding="utf-8"))["summary"]["verdict"] == "Хорошо"
    sessions = json.loads(request(httpd, "GET", "/api/history")[1])["sessions"]
    assert sessions[0]["stats"]["turns"] == 1


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
    assert not app.sessions_dir.exists() and sid in app.partners.closed


# ---------- тетрадь и прогресс ----------

@needs_ffmpeg
def test_phrasebook_add_check_grade_delete(server, tone_wav):
    httpd, state, _ = server
    status, data = request(httpd, "POST", "/api/phrasebook",
                           {"en": "I went to the cinema", "ru": "Я ходил в кино", "source": "correction"})
    card = json.loads(data)["card"]
    assert status == 200 and json.loads(data)["created"] and card["is_due"]
    state["transcript"] = Transcript("I went to the cinema")
    status, data = request(httpd, "POST", f"/api/phrasebook/{card['id']}/check", tone_wav)
    assert status == 200 and json.loads(data)["score"] == 100
    status, data = request(httpd, "POST", f"/api/phrasebook/{card['id']}/grade", {"correct": True})
    assert json.loads(data)["card"]["box"] == 1
    assert request(httpd, "POST", f"/api/phrasebook/{card['id']}/grade", {"correct": "yes"})[0] == 400
    overview = json.loads(request(httpd, "GET", "/api/phrasebook")[1])
    assert overview["total"] == 1 and overview["due"] == 0
    assert request(httpd, "POST", f"/api/phrasebook/{card['id']}/delete", {})[0] == 200
    assert request(httpd, "POST", f"/api/phrasebook/{card['id']}/delete", {})[0] == 400
    assert request(httpd, "POST", "/api/phrasebook", {"en": "", "ru": "", "source": "phrase"})[0] == 400


@needs_ffmpeg
def test_progress_counts_today(server, tone_wav):
    httpd, _, _ = server
    sid = start_session(httpd)["id"]
    request(httpd, "POST", f"/api/session/{sid}/turn", tone_wav)
    data = json.loads(request(httpd, "GET", "/api/progress")[1])
    today = data["days"][-1]
    assert today["date"] == date.today().isoformat() and today["turns"] == 1 and today["level"] == "A2"
    assert data["streak"] == 1 and data["level"]["need"] == 4


def test_corrupt_phrasebook_is_reported_not_crashed(server):
    httpd, _, app = server
    app.phrasebook._path.write_text("{broken", encoding="utf-8")
    status, data = request(httpd, "GET", "/api/phrasebook")
    assert status == 500 and "повреждён" in json.loads(data)["error"]


def test_rejects_oversized_and_malformed_json(server):
    httpd, _, _ = server
    assert request(httpd, "POST", "/api/session", b"[1,2]", {"Content-Type": "application/json"})[0] == 400
    assert request(httpd, "POST", "/api/session", b"{bad", {"Content-Type": "application/json"})[0] == 400
    assert request(httpd, "POST", "/api/session", b"x" * 20000)[0] == 413
