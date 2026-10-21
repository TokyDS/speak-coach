import pytest

from fakes import delta, result, spawner
from speak import partner, scenarios
from speak.coach import CoachError, Line
from speak.partner import PartnerProcess, Partners, SentenceSplitter, partner_system, sentences

SCENARIO = scenarios.get("kculture")


def chatty(message):
    return [delta("Oh nice, "), delta("that sounds fun! I love "), delta("horror films too. "),
            delta("What was it about?"), result()]


def test_splitter_waits_for_whole_sentences_and_merges_short_ones():
    splitter = SentenceSplitter(min_chunk=24)
    assert splitter.feed("Oh nice! ") == []            # короткое — ждём продолжения
    assert splitter.feed("I love horror films too. What") == ["Oh nice! I love horror films too."]
    assert splitter.feed(" was it about?") == []        # конец без пробела — ещё может продолжиться
    assert splitter.flush() == ["What was it about?"]
    assert splitter.flush() == []


def test_sentences_streams_from_deltas():
    out = list(sentences(iter(["Hello there, my dear old friend. ", "How was ", "your weekend?"])))
    assert out == ["Hello there, my dear old friend.", "How was your weekend?"]


def test_partner_system_carries_level_scenario_and_opener():
    text = partner_system(SCENARIO, "A2", "Which group do you like?")
    assert "CEFR A2" in text and "very simple" in text
    assert SCENARIO.role in text
    assert 'opened the conversation with: "Which group do you like?"' in text


def test_process_streams_reply_without_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    spawn = spawner(chatty)
    process = PartnerProcess("SYS", "sonnet", spawn)
    text = "".join(process.reply("I watched a film"))
    assert text == "Oh nice, that sounds fun! I love horror films too. What was it about?"
    proc = spawn.created[0]
    assert proc.received == ["I watched a film"]
    assert "--input-format" in proc.cmd and "stream-json" in proc.cmd
    assert "ANTHROPIC_API_KEY" not in proc.env


def test_process_reports_claude_error_and_death():
    process = PartnerProcess("SYS", "m", spawner(lambda m: [result(True, "overloaded")]))
    with pytest.raises(CoachError, match="overloaded"):
        list(process.reply("hi"))
    dead = PartnerProcess("SYS", "m", spawner(lambda m: [None]))
    with pytest.raises(CoachError, match="отключился"):
        list(dead.reply("hi"))


def test_process_skips_noise_lines():
    noisy = lambda m: ["not json\n", "null\n", "[1, 2]\n", delta("Fine, and you?"), result()]  # noqa: E731
    process = PartnerProcess("SYS", "m", spawner(noisy))
    assert "".join(process.reply("hi")) == "Fine, and you?"


def test_abandoned_reply_is_drained_before_next_message():
    process = PartnerProcess("SYS", "m", spawner(chatty))
    stream = process.reply("first")
    next(stream)
    stream.close()  # страница ушла посреди ответа
    assert "".join(process.reply("second")).startswith("Oh nice")


def test_hung_process_is_actually_killed_after_timeout(monkeypatch):
    monkeypatch.setattr(partner, "REPLY_TIMEOUT", 0.05)
    spawn = spawner(lambda m: [])
    process = PartnerProcess("SYS", "m", spawn)
    proc = spawn.created[0]
    proc.ignores_terminate = True          # завис так, что terminate не помогает
    with pytest.raises(CoachError, match="не ответил"):
        list(process.reply("hi"))
    assert not process.alive
    assert proc.terminated == 1 and proc.killed and proc.dead


def test_dead_process_is_closed_when_replaced():
    spawn = spawner(chatty)
    partners = Partners("m", spawn)
    partners.warm("s1", "SYS")
    first = spawn.created[0]
    first.ignores_terminate = True
    first.keeps_running = True
    first.out.put(None)                    # stdout закрылся, а процесс висит и на terminate не реагирует
    list(partners.reply("s1", "SYS", [Line("partner", "Hi!")], "hello"))
    assert len(spawn.created) == 2 and first.killed


def test_finished_session_never_respawns():
    spawn = spawner(chatty)
    partners = Partners("m", spawn)
    partners.warm("s1", "SYS")
    partners.close("s1")                   # «Завершить», пока ответ ещё в пути
    with pytest.raises(CoachError, match="уже завершён"):
        list(partners.reply("s1", "SYS", [Line("partner", "Hi!")], "hello"))
    partners.warm("s1", "SYS")             # прогрев тоже не поднимает процесс
    assert len(spawn.created) == 1


def test_partners_reuse_process_and_send_only_new_utterance():
    spawn = spawner(chatty)
    partners = Partners("m", spawn)
    partners.warm("s1", "SYS")
    history = [Line("partner", "Hi!"), Line("learner", "hello"), Line("partner", "How are you?")]
    list(partners.reply("s1", "SYS", history, "fine"))
    assert len(spawn.created) == 1
    assert spawn.created[0].received == ["fine"]


def test_partners_respawn_with_history_and_evict_oldest():
    spawn = spawner(chatty)
    partners = Partners("m", spawn, max_live=1)
    history = [Line("partner", "Hi!"), Line("learner", "hello"), Line("partner", "How are you?")]
    list(partners.reply("s1", "SYS", history, "fine"))     # процесса не было — история в сообщении
    assert "conversation so far" in spawn.created[0].received[0]
    list(partners.reply("s2", "SYS", history[:1], "hey"))  # второй разговор вытесняет первый
    assert spawn.created[0].dead
    assert spawn.created[1].received == ["hey"]
    partners.close_all()
    assert spawn.created[1].dead


def test_partners_retry_once_when_process_dies_before_answering():
    calls = {"n": 0}

    def flaky(message):
        calls["n"] += 1
        return [None] if calls["n"] == 1 else chatty(message)

    spawn = spawner(flaky)
    partners = Partners("m", spawn)
    assert "".join(partners.reply("s1", "SYS", [Line("partner", "Hi!")], "hello")).startswith("Oh nice")
    assert len(spawn.created) == 2


def test_partners_give_up_after_second_failure():
    partners = Partners("m", spawner(lambda m: [None]))
    with pytest.raises(CoachError):
        list(partners.reply("s1", "SYS", [Line("partner", "Hi!")], "hello"))


def test_missing_cli_is_explained():
    def spawn(*args, **kwargs):
        raise FileNotFoundError("claude")

    partners = Partners("m", spawn)
    partners.warm("s1", "SYS")  # прогрев не падает, только пишет в лог
    with pytest.raises(CoachError, match="не найдена команда claude"):
        list(partners.reply("s1", "SYS", [], "hi"))
