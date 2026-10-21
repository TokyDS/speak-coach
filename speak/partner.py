"""Живой собеседник: один процесс `claude -p` в режиме stream-json на весь разговор.

Разовый вызов CLI тратит ~2–3 с только на запуск. Постоянный процесс принимает реплики
по одной и отдаёт ответ по словам, поэтому первое предложение можно озвучить, пока
дописывается остальное: Alex начинает говорить через 1–3 с вместо ~5.

Процессы тяжёлые (~150 МБ), поэтому живыми держим только последние MAX_LIVE разговоров;
остальные при следующей реплике поднимаются заново с историей разговора в первом сообщении.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import subprocess
import threading
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator, Sequence

from .coach import CLI_FLAGS, LEARNER, PARTNER_NAME, CoachError, Line, format_history, subscription_env
from .levels import PARTNER_STYLE
from .scenarios import Scenario

log = logging.getLogger(__name__)

REPLY_TIMEOUT = 90       # сек. без единого события от claude — считаем, что он завис
CLOSE_TIMEOUT = 3        # сек. на мирное завершение процесса, потом kill
MAX_LIVE = 2
MIN_CHUNK = 24           # короткое «Oh nice!» озвучиваем вместе со следующим предложением
_EOF = object()
_SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*(?=\s)")

Spawn = Callable[..., subprocess.Popen]


def partner_system(scenario: Scenario, level: str, opener: str) -> str:
    return f"""You are {PARTNER_NAME}, an English conversation partner in a voice chat. {LEARNER}

Scenario: {scenario.role}

Language level: speak at CEFR {level}. {PARTNER_STYLE[level]}

Every message you receive is what the learner just said, transcribed by speech recognition, so ignore
punctuation and obvious recognition glitches. Your reply is read aloud by text-to-speech:
- 1 to 3 short sentences of spoken English; contractions are good.
- No markdown, lists, emoji, brackets or stage directions.
- React to what the learner said (show interest, add a little of your own opinion or experience),
  then ask exactly ONE open question that invites a longer answer. Stay in the scenario.
- Never correct the learner's mistakes; a separate coach does that.
- If the learner's words are empty, cut off or make no sense, kindly ask them to say it again.
- If the learner asks you something, answer briefly before asking your question.

You already opened the conversation with: "{opener}\""""


class SentenceSplitter:
    """Режет поток текста на предложения для озвучки по мере поступления."""

    def __init__(self, min_chunk: int = MIN_CHUNK) -> None:
        self._buffer = ""
        self._min_chunk = min_chunk

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        chunks = []
        start = 0
        for match in _SENTENCE_END.finditer(self._buffer):
            if match.end() - start >= self._min_chunk:
                chunks.append(self._buffer[start:match.end()].strip())
                start = match.end()
        self._buffer = self._buffer[start:]
        return [c for c in chunks if c]

    def flush(self) -> list[str]:
        rest, self._buffer = self._buffer.strip(), ""
        return [rest] if rest else []


def sentences(deltas: Iterator[str]) -> Iterator[str]:
    splitter = SentenceSplitter()
    for delta in deltas:
        yield from splitter.feed(delta)
    yield from splitter.flush()


class PartnerProcess:
    def __init__(self, system: str, model: str, spawn: Spawn = subprocess.Popen) -> None:
        cmd = ["claude", "-p", "--model", model, "--system-prompt", system, *CLI_FLAGS,
               "--input-format", "stream-json", "--output-format", "stream-json",
               "--include-partial-messages", "--verbose"]
        try:
            self._proc = spawn(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, bufsize=1, env=subscription_env())
        except FileNotFoundError as exc:
            raise CoachError("не найдена команда claude — установи Claude Code и войди в аккаунт") from exc
        self._events: queue.Queue = queue.Queue()
        self._ended = False   # stdout закрылся или claude завис — процесс больше не ответит
        self._stderr: deque[str] = deque(maxlen=20)
        self._lock = threading.Lock()
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self) -> None:
        try:
            for line in self._proc.stdout:
                self._events.put(line)
        except (OSError, ValueError) as exc:
            log.debug("stdout claude закрылся с ошибкой: %s", exc)
        finally:
            self._events.put(_EOF)  # конец потока отмечаем всегда — иначе читатель ждал бы таймаут

    def _pump_stderr(self) -> None:
        try:
            for line in self._proc.stderr:
                self._stderr.append(line.rstrip())
        except (OSError, ValueError):
            pass

    @property
    def alive(self) -> bool:
        return not self._ended and self._proc.poll() is None

    def reply(self, message: str) -> Iterator[str]:
        """Отдаёт ответ кусками текста. Генератор надо дочитать или закрыть — тогда хвост ответа вычитается."""
        with self._lock:
            payload = {"type": "user", "message": {"role": "user", "content": message}}
            try:
                self._proc.stdin.write(json.dumps(payload) + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise CoachError(f"собеседник отключился: {self._last_error()}") from exc
            finished = False
            try:
                for event in self._read_events():
                    if event.get("type") == "result":
                        finished = True
                        if event.get("is_error"):
                            raise CoachError(f"ошибка Claude: {str(event.get('result', ''))[:300]}")
                        return
                    text = _text_delta(event)
                    if text:
                        yield text
            finally:
                if not finished:
                    self._drain()

    def _read_events(self) -> Iterator[dict]:
        while True:
            if self._ended:  # поток уже закрыт — новых событий не будет, не ждём таймаут
                raise CoachError(f"собеседник отключился: {self._last_error()}")
            try:
                line = self._events.get(timeout=REPLY_TIMEOUT)
            except queue.Empty:
                self._ended = True
                self.close()
                raise CoachError("собеседник не ответил за полторы минуты") from None
            if line is _EOF:
                self._ended = True
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                log.debug("не JSON от claude: %s", line[:200])
                continue
            if isinstance(event, dict):
                yield event

    def _drain(self) -> None:
        """Ответ прервали на середине: дочитываем до конца, чтобы хвост не попал в следующую реплику."""
        try:
            for event in self._read_events():
                if event.get("type") == "result":
                    return
        except CoachError:
            pass

    def _last_error(self) -> str:
        return (" ".join(self._stderr) or "процесс claude завершился")[-300:]

    def close(self) -> None:
        """Гасим процесс, даже если он завис (alive уже False): terminate, ждём, иначе kill — без зомби."""
        self._ended = True
        if self._proc.poll() is not None:
            return
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        self._proc.terminate()
        try:
            self._proc.wait(timeout=CLOSE_TIMEOUT)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=CLOSE_TIMEOUT)


def _text_delta(event: dict) -> str:
    if event.get("type") != "stream_event":
        return ""
    delta = event.get("event", {}).get("delta", {})
    return delta.get("text", "") if delta.get("type") == "text_delta" else ""


class Partners:
    """Процессы собеседников по разговорам: не больше MAX_LIVE живых, остальные поднимаются заново."""

    def __init__(self, model: str, spawn: Spawn = subprocess.Popen, max_live: int = MAX_LIVE) -> None:
        self._model = model
        self._spawn = spawn
        self._max_live = max_live
        self._live: OrderedDict[str, PartnerProcess] = OrderedDict()
        self._finished: set[str] = set()   # завершённые разговоры: для них процесс больше не поднимаем
        self._lock = threading.Lock()

    def warm(self, session_id: str, system: str) -> None:
        """Запустить процесс заранее — пока звучит первая фраза, CLI успеет загрузиться."""
        try:
            self._get(session_id, system)
        except CoachError as exc:
            log.warning("не удалось заранее запустить собеседника: %s", exc)

    def reply(self, session_id: str, system: str, history: Sequence[Line], utterance: str) -> Iterator[str]:
        """Если процесс упал до первого слова ответа — поднимаем новый и пробуем ещё раз."""
        for attempt in (1, 2):
            remembers = self._has_live(session_id)  # живой процесс помнит разговор сам
            process = self._get(session_id, system)
            message = utterance if remembers or len(history) <= 1 else _with_history(history, utterance)
            started = False
            try:
                for text in process.reply(message):
                    started = True
                    yield text
                return
            except CoachError as exc:
                if started or attempt == 2:
                    raise
                log.warning("собеседник упал, поднимаю заново: %s", exc)
                self._discard(session_id)

    def close(self, session_id: str) -> None:
        """Разговор завершён: гасим процесс и больше не поднимаем его для этого разговора."""
        with self._lock:
            self._finished.add(session_id)
        self._discard(session_id)

    def _discard(self, session_id: str) -> None:
        with self._lock:
            process = self._live.pop(session_id, None)
        if process:
            process.close()

    def close_all(self) -> None:
        with self._lock:
            processes, self._live = list(self._live.values()), OrderedDict()
        for process in processes:
            process.close()

    def _has_live(self, session_id: str) -> bool:
        with self._lock:
            process = self._live.get(session_id)
            return process is not None and process.alive

    def _get(self, session_id: str, system: str) -> PartnerProcess:
        evicted = []
        with self._lock:
            if session_id in self._finished:
                raise CoachError("разговор уже завершён — начни новый")
            process = self._live.get(session_id)
            if process is None or not process.alive:
                if process is not None:
                    evicted.append(process)  # мёртвый или зависший — тоже гасим, иначе он утечёт
                process = PartnerProcess(system, self._model, self._spawn)
                self._live[session_id] = process
            self._live.move_to_end(session_id)
            while len(self._live) > self._max_live:
                evicted.append(self._live.popitem(last=False)[1])
        for old in evicted:
            old.close()
        return process


def _with_history(history: Sequence[Line], utterance: str) -> str:
    """Процесс подняли заново посреди разговора — напоминаем, о чём говорили."""
    return (f"(We were interrupted. The conversation so far:\n{format_history(history)}\n"
            f"Continue naturally.)\n\n{utterance}")
