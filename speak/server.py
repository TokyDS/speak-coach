"""Локальный сервер тренажёра: только 127.0.0.1, страница и API разговора.

Защита от чужих сайтов: POST принимается только с заголовком X-Speak (браузер не пошлёт его
кросс-доменно без CORS, а CORS мы не разрешаем) и только с Host = localhost/127.0.0.1
(против DNS rebinding).

Ход разговора отдаётся потоком NDJSON: сначала «что услышали» (через пару секунд),
потом реплика собеседника с голосом и разбор — чтобы не ждать молча весь ответ Claude.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import audio, scenarios
from .coach import Coach, CoachError
from .session import Session, SessionStore, Turn, load_history, save, words_per_minute
from .textdiff import count_words, diff_words, repeat_score

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
DEFAULT_PORT = 8770
MAX_AUDIO = 12 * 1024 * 1024
MAX_JSON = 16 * 1024
MAX_SPEAK_CHARS = 600
SOCKET_TIMEOUT = 60
_PKG = Path(__file__).resolve().parent
_STATIC = {"app.js": "text/javascript", "app.css": "text/css"}
_ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
_SESSION_ROUTE = re.compile(r"^/api/session/([A-Za-z0-9_-]{8,32})/(turn|hint|repeat|finish)$")


@dataclass(frozen=True)
class App:
    transcribe: Callable  # (np.ndarray) -> str
    synthesize: Callable  # (text, slow) -> wav bytes
    coach: Coach
    store: SessionStore
    sessions_dir: Path


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def wav_data_url(wav: bytes) -> str:
    return "data:audio/wav;base64," + base64.b64encode(wav).decode("ascii")


def _json_for_script(data: object) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def render_index() -> bytes:
    html = (_PKG / "templates" / "index.html").read_text(encoding="utf-8")
    payload = _json_for_script([s.public() for s in scenarios.SCENARIOS])
    return html.replace("/*__SCENARIOS__*/[]", payload).encode("utf-8")


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "speak"
        timeout = SOCKET_TIMEOUT  # зависший клиент не держит поток вечно

        def log_message(self, fmt: str, *args) -> None:
            log.debug("%s " + fmt, self.address_string(), *args)

        # ---------- маршрутизация ----------

        def do_GET(self) -> None:
            if not self._host_ok():
                return
            path = urlparse(self.path).path
            if path == "/":
                self._send(HTTPStatus.OK, render_index(), "text/html; charset=utf-8")
            elif path.startswith("/static/") and path[8:] in _STATIC:
                body = (_PKG / "static" / path[8:]).read_bytes()
                self._send(HTTPStatus.OK, body, f"{_STATIC[path[8:]]}; charset=utf-8")
            elif path == "/api/history":
                self._json(HTTPStatus.OK, {"sessions": load_history(app.sessions_dir)})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "не найдено"})

        def do_POST(self) -> None:
            if not self._host_ok():
                return
            if self.headers.get("X-Speak") != "1":
                self._json(HTTPStatus.FORBIDDEN, {"error": "запрос не со страницы тренажёра"})
                return
            url = urlparse(self.path)
            try:
                self._route_post(url.path, parse_qs(url.query))
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc)})
            except (CoachError, audio.AudioError, FileNotFoundError) as exc:
                self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
            except Exception:
                log.exception("ошибка обработки %s", url.path)
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "внутренняя ошибка, подробности в терминале"})

        def _route_post(self, path: str, query: dict) -> None:
            if path == "/api/session":
                self._start_session()
                return
            if path == "/api/speak":
                self._speak()
                return
            match = _SESSION_ROUTE.match(path)
            if not match:
                raise ApiError(HTTPStatus.NOT_FOUND, "не найдено")
            session = app.store.get(match.group(1))
            if session is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "разговор не найден — начни новый")
            action = match.group(2)
            if action == "turn":
                self._turn(session)
            elif action == "hint":
                self._json(HTTPStatus.OK, {"starters": app.coach.hint(session.history())})
            elif action == "repeat":
                self._repeat(session, query)
            else:
                self._finish(session)

        # ---------- действия ----------

        def _start_session(self) -> None:
            body = self._read_json()
            try:
                scenario = scenarios.get(str(body.get("scenario", "")))
            except KeyError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from None
            slow = bool(body.get("slow"))
            opener = scenario.opener()
            session = app.store.create(scenario, slow, opener)
            self._json(HTTPStatus.OK, {"id": session.id, "title": scenario.title, "opener": opener,
                                       "audio": wav_data_url(app.synthesize(opener, slow))})

        def _speak(self) -> None:
            body = self._read_json()
            text = str(body.get("text", "")).strip()
            if not text or len(text) > MAX_SPEAK_CHARS:
                raise ApiError(HTTPStatus.BAD_REQUEST, "нужен текст до 600 символов")
            self._json(HTTPStatus.OK, {"audio": wav_data_url(app.synthesize(text, bool(body.get("slow"))))})

        def _turn(self, session: Session) -> None:
            samples = audio.decode(self._read_body(MAX_AUDIO))
            seconds = audio.duration(samples)
            heard = app.transcribe(samples)
            # после заголовков потока ответить обычным JSON уже нельзя — любые ошибки идут событием
            self._start_stream()
            try:
                self._stream_turn(session, heard, seconds)
            except (BrokenPipeError, ConnectionResetError):
                log.info("страница закрыла соединение, не дождавшись ответа")
            except Exception:
                log.exception("ошибка в ходе разговора")
                self._emit_quietly({"type": "error", "message": "внутренняя ошибка, подробности в терминале"})

        def _stream_turn(self, session: Session, heard: str, seconds: float) -> None:
            if not heard:
                self._emit({"type": "empty"})
                return
            words = count_words(heard)
            self._emit({"type": "heard", "text": heard, "seconds": round(seconds, 1),
                        "words": words, "wpm": words_per_minute(words, seconds)})
            try:
                result = app.coach.turn(session.scenario, session.history(), heard)
                reply_audio = app.synthesize(result["reply"], session.slow)
            except (CoachError, FileNotFoundError) as exc:
                self._emit({"type": "error", "message": str(exc)})
                return
            feedback = {**result["feedback"], "diff": diff_words(heard, result["feedback"]["corrected"])}
            updated = app.store.add_turn(session.id, Turn(heard, seconds, result["reply"], feedback))
            if updated is None:
                self._emit({"type": "error", "message": "разговор уже завершён — начни новый"})
                return
            self._emit({"type": "reply", "reply": result["reply"], "audio": wav_data_url(reply_audio),
                        "feedback": feedback, "turn": len(updated.turns) - 1, "stats": updated.stats()})

        def _repeat(self, session: Session, query: dict) -> None:
            target = self._repeat_target(session, query)
            heard = app.transcribe(audio.decode(self._read_body(MAX_AUDIO)))
            self._json(HTTPStatus.OK, {"heard": heard, **repeat_score(target, heard)})

        def _finish(self, session: Session) -> None:
            if not session.turns:
                app.store.pop(session.id)
                self._json(HTTPStatus.OK, {"summary": None, "stats": session.stats()})
                return
            # разговор удаляем только после сохранения: если Claude не ответит, можно нажать ещё раз
            summary = app.coach.summary(session.scenario, session.history(), session.corrections())
            path = save(session, summary, app.sessions_dir)
            app.store.pop(session.id)
            log.info("итоги сохранены: %s", path)
            self._json(HTTPStatus.OK, {"summary": summary, "stats": session.stats()})

        # ---------- ввод/вывод ----------

        def _repeat_target(self, session: Session, query: dict) -> str:
            kind = query.get("kind", ["natural"])[0]
            try:
                turn = session.turns[int(query.get("turn", ["-1"])[0])]
            except (ValueError, IndexError):
                raise ApiError(HTTPStatus.BAD_REQUEST, "нет такой реплики") from None
            if kind not in ("natural", "corrected"):
                raise ApiError(HTTPStatus.BAD_REQUEST, "kind: natural или corrected")
            return turn.feedback.get(kind, "")

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            if host in _ALLOWED_HOSTS:
                return True
            self._json(HTTPStatus.FORBIDDEN, {"error": "неверный Host"})
            return False

        def _read_body(self, limit: int) -> bytes:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ApiError(HTTPStatus.BAD_REQUEST, "неверный Content-Length") from None
            if length <= 0:
                raise ApiError(HTTPStatus.BAD_REQUEST, "пустой запрос")
            if length > limit:
                raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "слишком большой запрос")
            return self.rfile.read(length)

        def _read_json(self) -> dict:
            try:
                body = json.loads(self._read_body(MAX_JSON))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ApiError(HTTPStatus.BAD_REQUEST, "ожидался JSON") from None
            if not isinstance(body, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "ожидался JSON-объект")
            return body

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: HTTPStatus, data: object) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json")

        def _start_stream(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.close_connection = True  # конец потока = закрытие соединения

        def _emit(self, event: dict) -> None:
            self.wfile.write(json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n")
            self.wfile.flush()

        def _emit_quietly(self, event: dict) -> None:
            try:
                self._emit(event)
            except OSError:
                pass  # страница уже ушла — сообщать некому

    return Handler


def serve(app: App, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((HOST, port), make_handler(app))
