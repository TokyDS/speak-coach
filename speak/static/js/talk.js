// Экран разговора: реплика → «что услышал» → голос Alex по предложениям → разбор и уровень.

import { getJson, ndjson, post, postJson } from "./api.js";
import { $, el, plural, show, toast } from "./dom.js";
import { addToPhrasebook } from "./phrasebook.js";
import { cancelRecording, drawIdleLevel, isRecording, player, toggleRecording } from "./sound.js";
import { nextGeneration, setBusy, setStatus, state } from "./state.js";

let onExit = () => {};
let lastPartnerAudio = [];
let recordings = [];      // blob-URL своих реплик: для «твой вариант»; освобождаем при выходе

export function initTalk({ exit }) {
  onExit = exit;
  $("mic").addEventListener("click", () => toggleRecording(turnTarget()));
  $("btn-hint").addEventListener("click", showHint);
  $("btn-hint-close").addEventListener("click", () => { $("hint-box").hidden = true; });
  $("btn-replay").addEventListener("click", () => player.play(lastPartnerAudio));
  $("btn-finish").addEventListener("click", finish);
  $("btn-back").addEventListener("click", () => { leaveConversation(); onExit(); });
  $("btn-again").addEventListener("click", () => onExit());
}

export function turnTarget() {
  return { mic: $("mic"), canvas: $("level"), onBlob: sendTurn,
           statusText: "Говори… отпусти пробел, когда закончишь" };
}

// ---------- начало и конец ----------

export async function startSession(key, options) {
  leaveConversation();
  show("talk");
  $("transcript").replaceChildren();
  updateMeters({ turns: 0, words: 0, wpm: 0, level: null });
  drawIdleLevel($("level"));
  const gen = state.generation;
  setBusy(true, "Alex подключается…");
  try {
    const data = await postJson("/api/session", { json: { scenario: key, ...options } });
    if (gen !== state.generation) return;  // пока подключались, нажали «назад»
    state.session = { id: data.id, title: data.title, partnerLevel: data.partner_level };
    $("talk-title").textContent = data.title;
    $("talk-level").textContent = `Alex говорит на ${data.partner_level}`;
    $("talk-level").title = data.estimated
      ? `Твой уровень по последним репликам — ${data.learner_level}`
      : `Твой уровень пока ${data.learner_level} (выбран вручную или по умолчанию)`;
    const line = partnerLine();
    line.add(data.opener, data.audio);
    player.play(data.audio);
    setStatus("Держи пробел и отвечай");
  } catch (err) {
    if (gen !== state.generation) return;
    toast(err.message);
    onExit();
  } finally {
    if (gen === state.generation) setBusy(false);
  }
}

/** Выход из разговора: выключить запись и отвязаться от ответов, которые ещё в пути. */
export function leaveConversation() {
  cancelRecording();
  nextGeneration();
  $("hint-box").hidden = true;
  recordings.forEach((url) => URL.revokeObjectURL(url));
  recordings = [];
}

// ---------- реплики ----------

function scrollToEnd(node) {
  node.scrollIntoView({ behavior: "smooth", block: "end" });
}

/** Реплика Alex, которая наполняется по предложениям. */
function partnerLine() {
  const hide = $("opt-hide").checked;
  const audios = [];
  const bubble = el("p", { class: "bubble" + (hide ? " hidden-text" : "") });
  const reveal = el("button", { type: "button", class: "chip-btn", text: "Показать текст",
    onclick: () => { bubble.classList.remove("hidden-text"); reveal.remove(); } });
  bubble.addEventListener("click", () => { bubble.classList.remove("hidden-text"); reveal.remove(); });
  const again = el("button", { type: "button", class: "chip-btn", text: "▶ Ещё раз", onclick: () => player.play(audios) });
  const line = el("li", { class: "line partner" }, el("span", { class: "who" }, "Alex", again, hide ? reveal : null), bubble);
  $("transcript").append(line);
  lastPartnerAudio = audios;
  return {
    line,
    add(text, audio) {
      bubble.textContent = bubble.textContent ? `${bubble.textContent} ${text}` : text;
      audios.push(audio);
      scrollToEnd(line);
    },
  };
}

function pendingLine(cls, who, text) {
  const bubble = el("p", { class: "bubble pending", text });
  const whoEl = el("span", { class: "who", text: who });
  const line = el("li", { class: `line ${cls}` }, whoEl, bubble);
  $("transcript").append(line);
  scrollToEnd(line);
  return { line, bubble, whoEl };
}

/** Твоя реплика: неуверенно распознанные слова — кнопки «сравнить произношение». */
function fillHeard(mine, event, recordingUrl) {
  mine.bubble.classList.remove("pending");
  mine.whoEl.append(el("span", { class: "wpm-tag", text: `${event.words} сл · ${event.wpm} сл/мин` }));
  const unsure = event.tokens.filter((t) => t.unsure);
  if (!unsure.length) { mine.bubble.textContent = event.text; return; }
  const panel = el("div", { class: "pron", hidden: true });
  mine.bubble.replaceChildren(...event.tokens.flatMap((token, i) => [i ? " " : "",
    token.unsure
      ? el("button", { type: "button", class: "unsure", text: token.text, title: "Распознано неуверенно — сравнить",
                       onclick: () => showPronunciation(panel, token, recordingUrl) })
      : token.text]));
  mine.line.append(el("p", { class: "pron-hint", text: "Подчёркнутые слова распознались неуверенно — нажми, чтобы сравнить произношение" }), panel);
}

function showPronunciation(panel, token, recordingUrl) {
  const listen = el("button", { type: "button", class: "ghost small", text: "▶ Как правильно" });
  listen.addEventListener("click", () => speak(token.text, listen));
  panel.replaceChildren(
    el("p", {}, el("strong", { text: `«${token.text}» ` }),
      "— Whisper сомневался в этом слове. Возможно, его стоит произнести чётче."),
    el("div", { class: "actions" },
      el("button", { type: "button", class: "ghost small", text: "▶ Твой вариант",
                     onclick: () => player.playSlice(recordingUrl, token.start, token.end) }),
      listen));
  panel.hidden = false;
}

async function sendTurn(blob, gen) {
  setStatus("Распознаю…");
  const recordingUrl = URL.createObjectURL(blob);
  recordings.push(recordingUrl);
  const mine = pendingLine("learner", "Ты", "…");
  let thinking = null;
  let partner = null;
  let finished = false;   // пришёл ли feedback или empty — иначе поток оборвался
  try {
    const res = await post(`/api/session/${state.session.id}/turn`, { blob });
    for await (const event of ndjson(res)) {
      if (gen !== state.generation) return;
      if (event.type === "empty") {
        finished = true;
        mine.line.remove();
        setStatus("Не расслышал — попробуй ещё раз, чуть громче");
      } else if (event.type === "heard") {
        fillHeard(mine, event, recordingUrl);
        thinking = pendingLine("partner", "Alex", "думает…");
        setStatus("Alex отвечает…");
      } else if (event.type === "say") {
        thinking?.line.remove();
        thinking = null;
        partner = partner || partnerLine();
        partner.add(event.text, event.audio);
        player.enqueue(event.audio);
      } else if (event.type === "feedback") {
        finished = true;
        renderFeedback(mine.line, event);
        updateMeters(event.stats);
        setStatus("Твоя очередь — держи пробел и говори");
      } else if (event.type === "error") {
        throw new Error(event.message);
      }
    }
    if (!finished) throw new Error("Ответ не пришёл — связь с тренажёром прервалась");
  } catch (err) {
    if (gen !== state.generation) return;
    thinking?.line.remove();
    if (mine.bubble.classList.contains("pending")) mine.line.remove();
    else if (!partner) mine.line.append(el("p", { class: "status", text: "Эта реплика не дошла до Alex — скажи её ещё раз." }));
    else partner.line.append(el("p", { class: "status", text: "Ответ оборвался — эта реплика не засчитана." }));
    toast(err.message);
    setStatus("Попробуй ещё раз");
  }
}

function updateMeters(stats) {
  $("m-turns").textContent = stats.turns;
  $("m-words").textContent = stats.words;
  $("m-wpm").textContent = stats.wpm || "—";
  $("m-level").textContent = stats.level || "—";
}

// ---------- разбор ----------

function renderFeedback(line, event) {
  if (!event.feedback) {
    line.append(el("p", { class: "status", text: `${event.error || "Разбора нет"}. Разговор продолжается.` }));
    return;
  }
  line.append(renderReview(event.feedback, event.turn));
}

function renderProof(diff) {
  const p = el("p", { class: "proof" });
  diff.forEach((part, i) => {
    if (i) p.append(" ");
    const tag = part.op === "delete" ? "del" : part.op === "insert" ? "ins" : "span";
    p.append(el(tag, { text: part.text }));
  });
  return p;
}

function levelChip(level) {
  if (!level || level === "unknown") {
    return el("span", { class: "level-chip muted", text: "уровень: коротко, не оценить",
                        title: "По короткой реплике уровень не определить — отвечай развёрнуто" });
  }
  return el("span", { class: "level-chip", text: `уровень фразы: ${level}` });
}

function renderNative(feedback, turn) {
  const scoreEl = el("p", { class: "score" });
  const repeatBtn = el("button", { type: "button", class: "ghost small", text: "Повторить вслух" });
  repeatBtn.addEventListener("click", () => toggleRecording({
    mic: $("mic"), canvas: $("level"), button: repeatBtn, buttonLabel: "Повторить вслух",
    statusText: "Повтори фразу вслух…", onBlob: (blob, gen) => sendRepeat(blob, gen, turn, scoreEl) }));
  const listenBtn = el("button", { type: "button", class: "ghost small", text: "Послушать" });
  listenBtn.addEventListener("click", () => speak(feedback.natural, listenBtn));
  const saveBtn = el("button", { type: "button", class: "ghost small", text: "В тетрадь" });
  saveBtn.addEventListener("click", () => addToPhrasebook(feedback.natural, feedback.natural_ru, "correction", saveBtn));
  const sameAsCorrected = feedback.natural.trim() === feedback.corrected.trim();
  return el("div", { class: "native" },
    el("span", { class: "label", text: sameAsCorrected && !feedback.ok ? "Правильно" : "Как сказал бы носитель" }),
    el("span", { class: "text", text: feedback.natural }),
    feedback.natural_ru ? el("span", { class: "label", text: feedback.natural_ru }) : null,
    el("div", { class: "actions" }, listenBtn, repeatBtn, saveBtn),
    scoreEl);
}

function renderReview(feedback, turn) {
  const box = el("section", { class: "review", "aria-label": "Разбор реплики" });
  box.append(el("div", { class: "review-head" },
    el("span", { class: `verdict-chip ${feedback.ok ? "ok" : "fix"}`, text: feedback.ok ? "Без ошибок" : "Можно лучше" }),
    levelChip(feedback.level)));
  if (!feedback.ok) box.append(renderProof(feedback.diff));
  if (!feedback.ok || feedback.natural.trim() !== feedback.corrected.trim()) box.append(renderNative(feedback, turn));
  if (feedback.notes.length) box.append(el("ul", { class: "notes" }, feedback.notes.map((n) => el("li", { text: n }))));
  if (feedback.phrases.length) box.append(renderPhrases(feedback.phrases, "phrase"));
  return box;
}

export function renderPhrases(phrases, source) {
  return el("ul", { class: "phrases" }, phrases.map((p) => {
    const add = el("button", { type: "button", class: "chip-btn", text: "+ в тетрадь" });
    add.addEventListener("click", () => addToPhrasebook(p.en, p.ru, source, add));
    return el("li", {}, el("span", { class: "en", text: p.en }), el("span", { class: "ru", text: p.ru }), add);
  }));
}

async function speak(text, button) {
  button.disabled = true;
  try {
    const data = await postJson("/api/speak", { json: { text, slow: $("opt-slow").checked } });
    player.play(data.audio);
  } catch (err) {
    toast(err.message);
  } finally {
    button.disabled = false;
  }
}

async function sendRepeat(blob, gen, turn, scoreEl) {
  setStatus("Сравниваю…");
  try {
    const data = await postJson(`/api/session/${state.session.id}/repeat?turn=${turn}&kind=natural`, { blob });
    if (gen !== state.generation) return;
    scoreEl.replaceChildren(scoreLine(data));
    setStatus(data.score >= 90 ? "Отлично получилось!" : "Пропущенные слова подчёркнуты — попробуй ещё");
  } catch (err) {
    if (gen === state.generation) toast(err.message);
  }
}

export function scoreLine(data) {
  return el("span", {}, el("strong", { text: `${data.score}% ` }),
    ...data.words.flatMap((w) => [el("span", { class: w.ok ? "" : "miss", text: w.text }), " "]));
}

// ---------- подсказка, итоги ----------

async function showHint() {
  if (state.busy) return;
  const gen = state.generation;
  setBusy(true, "Подбираю, с чего начать…");
  try {
    const data = await postJson(`/api/session/${state.session.id}/hint`, { json: {} });
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
  if (state.busy) return;
  if (isRecording()) {  // недоговорённую реплику не отправляем — даже если микрофон ещё включался
    cancelRecording();
    nextGeneration();
  }
  const gen = state.generation;
  setBusy(true, "Подвожу итоги…");
  try {
    const data = await postJson(`/api/session/${state.session.id}/finish`, { json: {} });
    if (gen !== state.generation) return;
    leaveConversation();
    if (!data.summary) { onExit(); return; }
    renderSummary(data.summary, data.stats);
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
  const known = summary.level && summary.level !== "unknown";
  $("sum-level").textContent = known ? summary.level : "?";
  $("sum-level-note").textContent = summary.level_note;
  $("sum-verdict").textContent = summary.verdict;
  $("sum-strengths").replaceChildren(...summary.strengths.map((s) => el("li", { text: s })));
  $("sum-focus").replaceChildren(...summary.focus.map((f) => el("li", {},
    el("span", { class: "topic", text: f.topic }),
    el("span", { class: "pair" }, el("del", { text: f.wrong }), " → ", el("span", { class: "right", text: f.right })))));
  $("sum-phrases").replaceChildren(...renderPhrases(summary.phrases, "summary").children);
  $("btn-add-all").onclick = async (e) => {
    for (const button of $("sum-phrases").querySelectorAll(".chip-btn:not(:disabled)")) button.click();
    e.currentTarget.disabled = true;
  };
  $("btn-add-all").disabled = summary.phrases.length === 0;
}

export async function loadHistory() {
  try {
    const { sessions } = await getJson("/api/history");
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
    el("span", { class: "h-stats", text: `${s.turns ?? 0} ${plural(s.turns ?? 0, "реплика", "реплики", "реплик")} · ${s.level || "—"}\n`
                                         + `${s.words ?? 0} ${plural(s.words ?? 0, "слово", "слова", "слов")} · ${s.wpm || "—"} сл/мин` }),
    el("span", { class: "h-date", text: date.toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" }) }),
    focus ? el("span", { class: "h-focus", text: `Поработать: ${focus}` }) : null);
}
