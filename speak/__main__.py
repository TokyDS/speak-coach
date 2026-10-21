"""Команды: speak setup | speak check | speak serve."""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import threading
import webbrowser

from .coach import Coach
from .config import Settings, load_settings
from .partner import Partners
from .phrasebook import Phrasebook
from .server import DEFAULT_PORT, HOST, App, serve, shutdown_workers
from .session import SessionStore
from .voice import Speaker, Transcriber

log = logging.getLogger("speak")


def cmd_setup(settings: Settings) -> int:
    from faster_whisper import WhisperModel
    from piper.download_voices import download_voice

    settings.voices_dir.mkdir(parents=True, exist_ok=True)
    if settings.voice_path.exists():
        log.info("голос %s уже скачан", settings.voice)
    else:
        log.info("скачиваю голос %s (~60 МБ)…", settings.voice)
        download_voice(settings.voice, settings.voices_dir)
    log.info("скачиваю/проверяю Whisper %s (до ~500 МБ при первом запуске)…", settings.whisper_model)
    WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
    log.info("готово. Запуск: speak serve")
    return 0


def cmd_check(settings: Settings) -> int:
    checks = {
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "claude (Claude Code)": shutil.which("claude") is not None,
        f"голос {settings.voice}": settings.voice_path.exists(),
    }
    for name, ok in checks.items():
        log.info("%s %s", "✓" if ok else "✗", name)
    if not all(checks.values()):
        log.info("не хватает компонентов: ffmpeg — sudo dnf install ffmpeg; голос — speak setup")
    return 0 if all(checks.values()) else 1


def cmd_serve(settings: Settings, port: int, open_browser: bool) -> int:
    if not settings.voice_path.exists():
        log.error("нет голоса %s — сначала запусти: speak setup", settings.voice)
        return 1
    transcriber = Transcriber(settings.whisper_model)
    speaker = Speaker(settings.voice_path)
    partners = Partners(settings.claude_model)
    app = App(transcribe=transcriber.transcribe, synthesize=speaker.synthesize,
              coach=Coach(settings.claude_model), partners=partners, store=SessionStore(),
              sessions_dir=settings.sessions_dir, phrasebook=Phrasebook(settings.phrasebook_path))
    try:
        httpd = serve(app, port)
    except OSError as exc:
        log.error("не удалось занять порт %s (%s) — уже запущен? Или выбери другой: speak serve --port %s",
                  port, exc.strerror, port + 1)
        return 1
    url = f"http://{HOST}:{port}/"
    log.info("тренажёр: %s  (Ctrl+C — выход)", url)
    log.info("модели: Claude %s, Whisper %s, голос %s",
             settings.claude_model, settings.whisper_model, settings.voice)
    # прогреваем модели в фоне, чтобы первая реплика не ждала их загрузки
    threading.Thread(target=_warm_up, args=(transcriber, speaker), daemon=True).start()
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("пока!")
    finally:
        httpd.server_close()
        partners.close_all()
        shutdown_workers()
    return 0


def _warm_up(transcriber: Transcriber, speaker: Speaker) -> None:
    import numpy as np

    try:
        transcriber.transcribe(np.zeros(1600, dtype=np.float32))
        speaker.synthesize("Hi.")
        log.info("модели загружены, можно говорить")
    except Exception as exc:  # прогрев необязателен: ошибка всплывёт и при первом запросе
        log.warning("прогрев не удался: %s", exc)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(prog="speak", description="Тренажёр разговорного английского")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="скачать голос и модель распознавания")
    sub.add_parser("check", help="проверить, всё ли установлено")
    p_serve = sub.add_parser("serve", help="запустить тренажёр в браузере")
    p_serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    p_serve.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.cmd == "setup":
        return cmd_setup(settings)
    if args.cmd == "check":
        return cmd_check(settings)
    return cmd_serve(settings, args.port, not args.no_browser)


if __name__ == "__main__":
    sys.exit(main())
