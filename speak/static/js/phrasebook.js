// Тетрадь фраз: добавление из разговора и повторение вслух (видишь по-русски — говоришь по-английски).

import { getJson, postJson } from "./api.js";
import { $, el, plural, show, toast } from "./dom.js";
import { cancelRecording, drawIdleLevel, player, toggleRecording } from "./sound.js";
import { nextGeneration, setBusy, setStatus, state } from "./state.js";

const PASS_SCORE = 85;   // столько % слов фразы — и повтор засчитывается сам
const BOX_DAYS = [0, 1, 3, 7, 16, 35, 90];

let queue = [];
let position = 0;
let current = null;
let reviewedToday = 0;
let requeued = new Set();
let nextAction = () => {};

export async function addToPhrasebook(en, ru, source, button) {
  button.disabled = true;
  try {
    const data = await postJson("/api/phrasebook", { json: { en, ru, source } });
    button.textContent = data.created ? "✓ в тетради" : "✓ уже в тетради";
  } catch (err) {
    button.disabled = false;
    toast(err.message);
  }
}

// ---------- панель на старте ----------

export async function loadPhrasebookPanel() {
  try {
    const data = await getJson("/api/phrasebook");
    $("pb-due").textContent = data.due;
    $("btn-review").disabled = data.due === 0;
    $("btn-review").textContent = data.due ? `Повторить ${data.due}` : "Повторить";
    $("pb-text").textContent = data.total
      ? `${data.total} ${plural(data.total, "фраза", "фразы", "фраз")} в тетради, выучено ${data.learned}.`
      : "Добавляй исправления и полезные фразы из разговоров — тут их можно повторять вслух.";
  } catch (err) {
    $("pb-text").textContent = err.message;
  }
}

// ---------- страница тетради ----------

export function drillTarget() {
  const result = $("drill-result");
  if (!current || (!result.hidden && result.dataset.retry !== "1")) return null;
  return { mic: $("drill-mic"), canvas: $("drill-level"), onBlob: checkCard,
           statusText: "Говори… отпусти пробел, когда закончишь" };
}

export function initPhrasebook({ exit }) {
  $("btn-cards-back").addEventListener("click", () => { leaveCards(); exit(); });
  $("drill-mic").addEventListener("click", () => { const t = drillTarget(); if (t) toggleRecording(t); });
  $("drill-reveal").addEventListener("click", () => reveal());
  $("drill-accept").addEventListener("click", () => grade(true));
  // единственный обработчик «Дальше»: что он делает, решает nextAction (засчитать ошибку или просто дальше)
  $("drill-next").addEventListener("click", () => nextAction());
}

function showNext(action) {
  nextAction = action;
  $("drill-next").hidden = false;
  $("drill-next").textContent = "Дальше";
}

function leaveCards() {
  cancelRecording();
  nextGeneration();
  current = null;  // иначе пробел на скрытой карточке отправил бы ответ к старой фразе
}

export async function openCards(withDrill) {
  leaveCards();
  show("cards");
  drawIdleLevel($("drill-level"));
  reviewedToday = 0;
  requeued = new Set();
  try {
    const data = await getJson("/api/phrasebook");
    renderList(data.cards);
    queue = withDrill ? data.queue : [];
    position = 0;
    $("drill-done").hidden = true;
    if (withDrill && queue.length) showCard();
    else { $("drill").hidden = true; $("drill-count").textContent = `${data.total} ${plural(data.total, "фраза", "фразы", "фраз")}`; }
  } catch (err) {
    toast(err.message);
  }
}

function showCard() {
  current = queue[position];
  $("drill").hidden = false;
  $("drill-done").hidden = true;
  $("drill-count").textContent = `${position + 1} из ${queue.length}`;
  $("drill-ru").textContent = current.ru;
  $("drill-result").hidden = true;
  delete $("drill-result").dataset.retry;
  $("drill-reveal").hidden = false;
  $("drill-accept").hidden = true;
  $("drill-next").hidden = true;
  setStatus("Держи пробел и скажи по-английски");
}

function nextCard() {
  position += 1;
  if (position < queue.length) { showCard(); return; }
  current = null;
  $("drill").hidden = true;
  $("drill-done").hidden = false;
  $("drill-count").textContent = `повторено: ${reviewedToday}`;
  openCardsListOnly();
}

async function openCardsListOnly() {
  try {
    renderList((await getJson("/api/phrasebook")).cards);
  } catch (err) {
    toast(err.message);
  }
}

async function checkCard(blob, gen) {
  setStatus("Сравниваю…");
  try {
    const data = await postJson(`/api/phrasebook/${current.id}/check`, { blob });
    if (gen !== state.generation) return;
    showResult(data);
    if (data.score >= PASS_SCORE) await grade(true);
    else {
      $("drill-accept").hidden = false;
      showNext(() => grade(false));
      setStatus("Пропущенные слова подчёркнуты. Можно сказать ещё раз — держи пробел");
      $("drill-result").dataset.retry = "1";
    }
  } catch (err) {
    if (gen === state.generation) toast(err.message);
  }
}

function showResult(data) {
  const listen = el("button", { type: "button", class: "ghost small", text: "▶ Послушать" });
  listen.addEventListener("click", () => listenTo(current.en, listen));
  const words = data
    ? data.words.flatMap((w) => [el("span", { class: w.ok ? "" : "miss", text: w.text }), " "])
    : [el("span", { text: current.en })];
  $("drill-result").replaceChildren(
    data ? el("p", { class: "heard", text: `Услышал: «${data.heard || "…"}»` }) : null,
    el("p", { class: "drill-en" }, data ? el("strong", { text: `${data.score}% ` }) : null, ...words),
    listen);
  $("drill-result").hidden = false;
  $("drill-reveal").hidden = true;
}

function reveal() {
  showResult(null);
  showNext(() => grade(false));
  listenTo(current.en, null);
}

async function grade(correct) {
  if (!current) return;
  setBusy(true);
  try {
    const { card } = await postJson(`/api/phrasebook/${current.id}/grade`, { json: { correct } });
    reviewedToday += 1;
    delete $("drill-result").dataset.retry;
    if (correct) {
      const days = BOX_DAYS[card.box] ?? 1;
      setStatus(`Засчитано! Вернётся через ${days} ${plural(days, "день", "дня", "дней")}`);
      $("drill-accept").hidden = true;
      showNext(() => nextCard());
    } else {
      if (!requeued.has(card.id)) {  // ошибся — фраза ещё раз в конце очереди (один раз за подход)
        requeued.add(card.id);
        queue.push(card);
      }
      nextCard();
    }
  } catch (err) {
    toast(err.message);
  } finally {
    setBusy(false);
  }
}

async function listenTo(text, button) {
  if (button) button.disabled = true;
  try {
    const data = await postJson("/api/speak", { json: { text, slow: $("opt-slow").checked } });
    player.play(data.audio);
  } catch (err) {
    toast(err.message);
  } finally {
    if (button) button.disabled = false;
  }
}

function renderList(cards) {
  $("cards-empty").hidden = cards.length > 0;
  $("card-list").replaceChildren(...cards.map((card) => {
    const remove = el("button", { type: "button", class: "chip-btn danger", text: "Удалить" });
    remove.addEventListener("click", async () => {
      remove.disabled = true;
      try {
        await postJson(`/api/phrasebook/${card.id}/delete`, { json: {} });
        remove.closest("li").remove();
      } catch (err) {
        remove.disabled = false;
        toast(err.message);
      }
    });
    const status = card.learned ? "выучено" : card.is_due ? "на сегодня" : `повтор ${formatDue(card.due)}`;
    return el("li", {},
      el("span", { class: "en", text: card.en }),
      el("span", { class: "ru", text: card.ru }),
      el("span", { class: `box-tag${card.learned ? " learned" : ""}`, text: status }),
      remove);
  }));
}

function formatDue(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}
