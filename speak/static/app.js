"use strict";
// Speak Up: запись → сервер (Whisper → Claude → Piper) → ответ голосом и разбор реплики.
// Весь текст от сервера вставляется через textContent — никакого innerHTML с чужими строками.

const MIN_RECORD_MS = 500;
const LEVEL_BARS = 40;
const $ = (id) => document.getElementById(id);

const state = {
  session: null,        // {id, title}
  busy: false,          // ждём ответа сервера
  starting: false,      // микрофон включается (в т.ч. висит запрос разрешения)
  stopWhenStarted: false, // пробел отпустили, пока микрофон включался
  recording: null,      // {target, startedAt}
  generation: 0,        // растёт при выходе из разговора: опоздавшие ответы старого разговора отбрасываются
  lastPartnerAudio: null,
};

// ---------- DOM-помощники ----------

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) if (child != null) node.append(child);
  return node;
}

function show(pageId) {
  for (const id of ["start", "talk", "summary"]) $(id).hidden = id !== pageId;
  window.scrollTo(0, 0);
}

let toastTimer = 0;
function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 6000);
}

function setStatus(text) { $("status").textContent = text; }

function setBusy(busy, status) {
  state.busy = busy;
  $("mic").classList.toggle("busy", busy);
  $("mic").disabled = busy;
  $("btn-hint").disabled = busy;
  $("btn-finish").disabled = busy;
  if (status) setStatus(status);
}

// ---------- сеть ----------

async function api(path, { json, blob } = {}) {
  const headers = { "X-Speak": "1" };
  let body;
  if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  if (blob) { headers["Content-Type"] = blob.type || "application/octet-stream"; body = blob; }
  const res = await fetch(path, { method: "POST", headers, body });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `ошибка сервера (${res.status})`);
  return res;
}

async function* ndjson(res) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buffer += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (line) yield JSON.parse(line);
    }
    if (done) return;
  }
}

// ---------- звук ----------

const player = new Audio();
function play(src) {
  if (!src) return;
  player.pause();
  player.src = src;
  player.play().catch(() => setStatus("Нажми «Повторить вопрос», чтобы услышать"));
}

const recorder = {
  stream: null, media: null, chunks: [], analyser: null, raf: 0, levels: [],

  async ensureStream() {
    if (this.stream) return;
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    const ctx = new AudioContext();
    this.analyser = ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    ctx.createMediaStreamSource(this.stream).connect(this.analyser);
  },

  mimeType() {
    for (const type of ["audio/ogg;codecs=opus", "audio/webm;codecs=opus", "audio/webm"]) {
      if (MediaRecorder.isTypeSupported(type)) return type;
    }
    return "";
  },

  async start() {
    await this.ensureStream();
    const mimeType = this.mimeType();
    // у каждой записи свой массив: поздние данные прошлой записи не попадут в новую
    const chunks = [];
    const media = new MediaRecorder(this.stream, mimeType ? { mimeType } : {});
    media.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    media.start();
    this.media = media;
    this.chunks = chunks;
    this.levels = [];
    this.drawLevel();
  },

  stop() {
    const media = this.media;
    const chunks = this.chunks;
    this.media = null;
    return new Promise((resolve) => {
      cancelAnimationFrame(this.raf);
      drawIdleLevel();
      if (!media || media.state === "inactive") { resolve(null); return; }
      media.onstop = () => resolve(new Blob(chunks, { type: media.mimeType || "audio/ogg" }));
      media.stop();
    });
  },

  drawLevel() {
    const data = new Uint8Array(this.analyser.fftSize);
    this.analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (const v of data) sum += ((v - 128) / 128) ** 2;
    this.levels.push(Math.min(1, Math.sqrt(sum / data.length) * 4));
    if (this.levels.length > LEVEL_BARS) this.levels.shift();
    drawBars(this.levels, cssVar("--onair"));
    this.raf = requestAnimationFrame(() => this.drawLevel());
  },
};

function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function drawBars(levels, color) {
  const canvas = $("level");
  const ctx = canvas.getContext("2d");
  const step = canvas.width / LEVEL_BARS;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = color;
  levels.forEach((level, i) => {
    const h = Math.max(2, level * canvas.height);
    ctx.fillRect(i * step + 1, (canvas.height - h) / 2, step - 2, h);
  });
}

function drawIdleLevel() { drawBars(new Array(LEVEL_BARS).fill(0), cssVar("--line")); }

// ---------- запись: реплика или повтор ----------

async function startRecording(target) {
  if (state.busy || state.recording || state.starting) return;
  const gen = state.generation;
  state.starting = true;
  state.stopWhenStarted = false;
  player.pause();
  try {
    await recorder.start();
  } catch (err) {
    toast("Нет доступа к микрофону. Разреши его в адресной строке Firefox и попробуй снова.");
    return;
  } finally {
    state.starting = false;
  }
  if (gen !== state.generation) { recorder.stop(); return; }  // пока включался микрофон, из разговора вышли
  state.recording = { target, startedAt: performance.now() };
  $("mic").classList.add("recording");
  if (target.button) target.button.textContent = "Стоп";
  setStatus(target.kind === "turn" ? "Говори… отпусти пробел, когда закончишь" : "Повтори фразу вслух…");
  if (state.stopWhenStarted) stopRecording();
}

function endRecordingUi(rec) {
  $("mic").classList.remove("recording");
  if (rec.target.button) rec.target.button.textContent = "Повторить вслух";
}

async function stopRecording() {
  if (state.starting) { state.stopWhenStarted = true; return; }
  const rec = state.recording;
  if (!rec) return;
  const gen = state.generation;
  state.recording = null;
  endRecordingUi(rec);
  setBusy(true);  // сразу: новая запись не начнётся, пока эта не отправлена
  try {
    const blob = await recorder.stop();
    if (gen !== state.generation) return;
    if (!blob || performance.now() - rec.startedAt < MIN_RECORD_MS) {
      setStatus("Слишком коротко — держи пробел, пока говоришь");
      return;
    }
    if (rec.target.kind === "turn") await sendTurn(blob, gen);
    else await sendRepeat(blob, rec.target, gen);
  } finally {
    if (gen === state.generation) setBusy(false);
  }
}

/** Выход из разговора: выключить запись и отвязаться от ответов, которые ещё в пути. */
function leaveConversation() {
  state.generation += 1;
  state.stopWhenStarted = true;
  if (state.recording) {
    endRecordingUi(state.recording);
    state.recording = null;
    recorder.stop();
  }
  player.pause();
  $("hint-box").hidden = true;
  setBusy(false);
}

function toggleRecording(target) {
  if (state.recording || state.starting) stopRecording();
  else startRecording(target);
}

// ---------- ход разговора ----------

function addPartnerLine(text, audio) {
  const hide = $("opt-hide").checked;
  const bubble = el("p", { class: "bubble" + (hide ? " hidden-text" : ""), text });
  const reveal = el("button", { type: "button", class: "chip-btn", text: "Показать текст",
    onclick: () => { bubble.classList.remove("hidden-text"); reveal.remove(); } });
  bubble.addEventListener("click", () => { bubble.classList.remove("hidden-text"); reveal.remove(); });
  const again = el("button", { type: "button", class: "chip-btn", text: "▶ Ещё раз", onclick: () => play(audio) });
  const line = el("li", { class: "line partner" },
    el("span", { class: "who" }, "Alex", again, hide ? reveal : null), bubble);
  $("transcript").append(line);
  line.scrollIntoView({ behavior: "smooth", block: "end" });
  state.lastPartnerAudio = audio;
}

function addPendingLine(cls, who, text) {
  const bubble = el("p", { class: "bubble pending", text });
  const whoEl = el("span", { class: "who", text: who });
  const line = el("li", { class: `line ${cls}` }, whoEl, bubble);
  $("transcript").append(line);
  line.scrollIntoView({ behavior: "smooth", block: "end" });
  return { line, bubble, whoEl };
}

async function sendTurn(blob, gen) {
  setStatus("Распознаю…");
  const mine = addPendingLine("learner", "Ты", "…");
  let partner = null;
  let finished = false;  // пришёл ли reply или empty — иначе поток оборвался
  try {
    const res = await api(`/api/session/${state.session.id}/turn`, { blob });
    for await (const event of ndjson(res)) {
      if (gen !== state.generation) return;
      finished = finished || event.type === "reply" || event.type === "empty";
      if (event.type === "empty") {
        mine.line.remove();
        setStatus("Не расслышал — попробуй ещё раз, чуть громче");
      } else if (event.type === "heard") {
        mine.bubble.classList.remove("pending");
        mine.bubble.textContent = event.text;
        mine.whoEl.append(el("span", { class: "wpm-tag", text: `${event.words} сл · ${event.wpm} сл/мин` }));
        partner = addPendingLine("partner", "Alex", "думает…");
        setStatus("Alex думает…");
      } else if (event.type === "error") {
        throw new Error(event.message);
      } else if (event.type === "reply") {
        partner?.line.remove();
        mine.line.append(renderReview(event.feedback, event.turn));
        addPartnerLine(event.reply, event.audio);
        play(event.audio);
        updateMeters(event.stats);
        setStatus("Твоя очередь — держи пробел и говори");
      }
    }
    if (!finished && gen === state.generation) throw new Error("Ответ не пришёл — связь с тренажёром прервалась");
  } catch (err) {
    if (gen !== state.generation) return;
    partner?.line.remove();
    if (mine.bubble.classList.contains("pending")) mine.line.remove();
    else mine.line.append(el("p", { class: "status", text: "Эта реплика не дошла до Alex — скажи её ещё раз." }));
    toast(err.message);
    setStatus("Попробуй ещё раз");
  }
}

function updateMeters(stats) {
  $("m-turns").textContent = stats.turns;
  $("m-words").textContent = stats.words;
  $("m-wpm").textContent = stats.wpm || "—";
}

// ---------- разбор реплики ----------

function renderProof(diff) {
  const p = el("p", { class: "proof" });
  diff.forEach((part, i) => {
    if (i) p.append(" ");
    const tag = part.op === "delete" ? "del" : part.op === "insert" ? "ins" : "span";
    p.append(el(tag, { text: part.text }));
  });
  return p;
}

function renderNative(feedback, turn) {
  const sameAsCorrected = feedback.natural.trim() === feedback.corrected.trim();
  const which = "natural";
  const scoreEl = el("p", { class: "score" });
  const repeatBtn = el("button", { type: "button", class: "ghost small", text: "Повторить вслух" });
  repeatBtn.addEventListener("click", () =>
    toggleRecording({ kind: "repeat", turn, which, button: repeatBtn, scoreEl }));
  const listenBtn = el("button", { type: "button", class: "ghost small", text: "Послушать",
    onclick: () => speak(feedback.natural, listenBtn) });
  return el("div", { class: "native" },
    el("span", { class: "label", text: sameAsCorrected && !feedback.ok ? "Правильно" : "Как сказал бы носитель" }),
    el("span", { class: "text", text: feedback.natural }),
    el("div", { class: "actions" }, listenBtn, repeatBtn),
    scoreEl);
}

function renderReview(feedback, turn) {
  const box = el("section", { class: "review", "aria-label": "Разбор реплики" });
  box.append(el("span", { class: `verdict-chip ${feedback.ok ? "ok" : "fix"}`,
    text: feedback.ok ? "Без ошибок" : "Можно лучше" }));
  if (!feedback.ok) box.append(renderProof(feedback.diff));
  if (!feedback.ok || feedback.natural.trim() !== feedback.corrected.trim()) box.append(renderNative(feedback, turn));
  if (feedback.notes.length) box.append(el("ul", { class: "notes" }, feedback.notes.map((n) => el("li", { text: n }))));
  if (feedback.phrases.length) box.append(renderPhrases(feedback.phrases));
  return box;
}

function renderPhrases(phrases) {
  return el("ul", { class: "phrases" }, phrases.map((p) =>
    el("li", {}, el("span", { class: "en", text: p.en }), el("span", { class: "ru", text: p.ru }))));
}

async function speak(text, button) {
  button.disabled = true;
  try {
    const data = await (await api("/api/speak", { json: { text, slow: $("opt-slow").checked } })).json();
    play(data.audio);
  } catch (err) {
    toast(err.message);
  } finally {
    button.disabled = false;
  }
}

async function sendRepeat(blob, target, gen) {
  setStatus("Сравниваю…");
  try {
    const path = `/api/session/${state.session.id}/repeat?turn=${target.turn}&kind=${target.which}`;
    const data = await (await api(path, { blob })).json();
    if (gen !== state.generation) return;
    target.scoreEl.replaceChildren(
      el("strong", { text: `${data.score}% ` }),
      ...data.words.flatMap((w) => [el("span", { class: w.ok ? "" : "miss", text: w.text }), " "]));
    setStatus(data.score >= 90 ? "Отлично получилось!" : "Пропущенные слова подчёркнуты — попробуй ещё");
  } catch (err) {
    if (gen === state.generation) toast(err.message);
  }
}

// ---------- подсказка, завершение ----------

async function showHint() {
  if (state.busy || state.recording || state.starting) return;
  const gen = state.generation;
  setBusy(true, "Подбираю, с чего начать…");
  try {
    const data = await (await api(`/api/session/${state.session.id}/hint`, { json: {} })).json();
    if (gen !== state.generation) return;
    $("hints").replaceChildren(...data.starters.map((s) =>
      el("li", {}, el("span", { class: "en", text: s.en }), el("span", { class: "ru", text: s.ru }))));
    $("hint-box").hidden = false;
    setStatus("Выбери начало и договори своими словами");
  } catch (err) {
    if (gen === state.generation) toast(err.message);
  } finally {
    if (gen === state.generation) setBusy(false);
  }
}

async function finish() {
  if (state.busy || state.starting) return;
  if (state.recording) leaveConversation();  // недоговорённую реплику не отправляем
  const gen = state.generation;
  setBusy(true, "Подвожу итоги…");
  try {
    const data = await (await api(`/api/session/${state.session.id}/finish`, { json: {} })).json();
    if (gen !== state.generation) return;
    if (!data.summary) { goStart(); return; }
    renderSummary(data.summary, data.stats);
    leaveConversation();
    show("summary");
  } catch (err) {
    if (gen !== state.generation) return;
    toast(`${err.message}. Разговор не потерян — нажми «Завершить» ещё раз.`);
    setBusy(false, "Итоги не получились — попробуй ещё раз");
  }
}

function renderSummary(summary, stats) {
  $("sum-title").textContent = state.session.title;
  const minutes = Math.max(1, Math.round(stats.seconds / 60));
  $("sum-stats").replaceChildren(...[
    ["реплик", stats.turns], ["слов сказано", stats.words],
    ["минут речи", stats.seconds < 60 ? `${stats.seconds} с` : minutes], ["слов в минуту", stats.wpm || "—"],
  ].map(([label, value]) => el("div", {}, el("dt", { text: label }), el("dd", { text: String(value) }))));
  $("sum-verdict").textContent = summary.verdict;
  $("sum-strengths").replaceChildren(...summary.strengths.map((s) => el("li", { text: s })));
  $("sum-focus").replaceChildren(...summary.focus.map((f) => el("li", {},
    el("span", { class: "topic", text: f.topic }),
    el("span", { class: "pair" }, el("del", { text: f.wrong }), " → ", el("span", { class: "right", text: f.right })))));
  $("sum-phrases").replaceChildren(...renderPhrases(summary.phrases).children);
}

// ---------- старт ----------

async function startSession(key) {
  show("talk");
  $("transcript").replaceChildren();
  $("hint-box").hidden = true;
  updateMeters({ turns: 0, words: 0, wpm: 0 });
  drawIdleLevel();
  const gen = state.generation;
  setBusy(true, "Alex подключается…");
  try {
    const data = await (await api("/api/session", { json: { scenario: key, slow: $("opt-slow").checked } })).json();
    if (gen !== state.generation) return;  // пока подключались, нажали «назад»
    state.session = { id: data.id, title: data.title };
    $("talk-title").textContent = data.title;
    addPartnerLine(data.opener, data.audio);
    play(data.audio);
    setStatus("Держи пробел и отвечай");
  } catch (err) {
    if (gen !== state.generation) return;
    toast(err.message);
    goStart();
  } finally {
    if (gen === state.generation) setBusy(false);
  }
}

function goStart() {
  leaveConversation();
  state.session = null;
  show("start");
  loadHistory();
}

async function loadHistory() {
  try {
    const res = await fetch("/api/history");
    const { sessions } = await res.json();
    $("history-block").hidden = sessions.length === 0;
    $("history").replaceChildren(...sessions.map(renderHistoryItem));
  } catch {
    $("history-block").hidden = true;
  }
}

function renderHistoryItem(item) {
  const date = new Date(item.started);
  const s = item.stats || {};
  const focus = (item.focus || []).map((f) => f.topic).join(" · ");
  return el("li", {},
    el("span", { class: "h-title", text: item.title }),
    el("span", { class: "h-stats", text: `${s.turns ?? 0} реплик\n${s.words ?? 0} слов · ${s.wpm || "—"} сл/мин` }),
    el("span", { class: "h-date", text: date.toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" }) }),
    focus ? el("span", { class: "h-focus", text: `Поработать: ${focus}` }) : null);
}

function renderScenarios() {
  $("scenarios").replaceChildren(...window.SCENARIOS.map((s) =>
    el("button", { type: "button", class: "scenario", onclick: () => startSession(s.key) },
      el("h3", { text: s.title }), el("span", { class: "blurb", text: s.blurb }))));
}

function restoreOptions() {
  for (const id of ["opt-slow", "opt-hide"]) {
    try { $(id).checked = localStorage.getItem(id) === "1"; } catch { /* хранилище недоступно — не страшно */ }
    $(id).addEventListener("change", (e) => {
      try { localStorage.setItem(id, e.target.checked ? "1" : "0"); } catch { /* см. выше */ }
    });
  }
}

// ---------- клавиатура и кнопки ----------

function talkActive() { return !$("talk").hidden && state.session; }

// Пробел — «рация». Если фокус на кнопке (пришли Tab'ом), пробел нажимает её, как обычно.
function spaceForTalk(e) {
  return e.code === "Space" && talkActive() && !e.target.closest?.("button, input, textarea, select");
}
document.addEventListener("keydown", (e) => {
  if (!spaceForTalk(e)) return;
  e.preventDefault();
  if (!e.repeat) startRecording({ kind: "turn" });
});
document.addEventListener("keyup", (e) => {
  if (!spaceForTalk(e)) return;
  e.preventDefault();
  stopRecording();
});
// После щелчка мышью снимаем фокус с кнопки, чтобы пробел снова работал как «рация»
// (e.detail === 0 — кнопку нажали с клавиатуры, там фокус нужен).
document.addEventListener("click", (e) => {
  if (e.detail > 0) e.target.closest?.("button")?.blur();
});

$("mic").addEventListener("click", () => toggleRecording({ kind: "turn" }));
$("btn-hint").addEventListener("click", showHint);
$("btn-hint-close").addEventListener("click", () => { $("hint-box").hidden = true; });
$("btn-replay").addEventListener("click", () => play(state.lastPartnerAudio));
$("btn-finish").addEventListener("click", finish);
$("btn-back").addEventListener("click", goStart);
$("btn-again").addEventListener("click", goStart);

renderScenarios();
restoreOptions();
loadHistory();
