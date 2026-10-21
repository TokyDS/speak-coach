"""Поддельный процесс `claude -p --input-format stream-json` для тестов собеседника."""
import json
import queue
import subprocess


def delta(text: str) -> dict:
    return {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}}


def result(is_error: bool = False, text: str = "") -> dict:
    return {"type": "result", "is_error": is_error, "result": text}


class _Stdin:
    def __init__(self, proc: "FakeProc") -> None:
        self._proc = proc

    def write(self, data: str) -> None:
        if self._proc.dead:
            raise BrokenPipeError
        message = json.loads(data)["message"]["content"]
        self._proc.received.append(message)
        for event in self._proc.script(message):
            # None — конец stdout (процесс умер), строка — сырая строка вывода, dict — событие
            self._proc.out.put(event if event is None or isinstance(event, str) else json.dumps(event) + "\n")

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class FakeProc:
    """script(message) -> события ответа; None в событиях = процесс умер."""

    def __init__(self, script) -> None:
        self.script = script
        self.received: list[str] = []
        self.out: queue.Queue = queue.Queue()
        self.dead = False
        self.stdin = _Stdin(self)
        self.stderr = iter(["claude: что-то пошло не так\n"])
        self.cmd: list[str] = []
        self.env: dict = {}
        self.terminated = 0
        self.killed = False
        self.ignores_terminate = False   # «зависший» claude, которого берёт только kill
        self.keeps_running = False       # stdout закрылся, а процесс всё ещё жив

    @property
    def stdout(self):
        while True:
            line = self.out.get()
            if line is None:
                if not self.keeps_running:
                    self.dead = True
                return
            yield line

    def poll(self):
        return 1 if self.dead else None

    def terminate(self) -> None:
        self.terminated += 1
        if not self.ignores_terminate:
            self.dead = True
            self.out.put(None)

    def wait(self, timeout=None):
        if not self.dead:
            raise subprocess.TimeoutExpired("claude", timeout)
        return 1

    def kill(self) -> None:
        self.killed = True
        self.dead = True
        self.out.put(None)


def spawner(script):
    """Фабрика вместо subprocess.Popen; запоминает созданные процессы."""
    created: list[FakeProc] = []

    def spawn(cmd, **kwargs):
        proc = FakeProc(script)
        proc.cmd = cmd
        proc.env = kwargs.get("env", {})
        created.append(proc)
        return proc

    spawn.created = created
    return spawn
