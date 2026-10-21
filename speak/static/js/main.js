// Точка входа: стартовая страница, настройки, клавиатура и переходы между экранами.

import { $, currentPage, el, show, storageGet, storageSet } from "./dom.js";
import { drillTarget, initPhrasebook, loadPhrasebookPanel, openCards } from "./phrasebook.js";
import { initProgress, loadStartPanels, openProgress } from "./progress.js";
import { startRecording, stopRecording } from "./sound.js";
import { state } from "./state.js";
import { initTalk, leaveConversation, loadHistory, startSession, turnTarget } from "./talk.js";

const config = window.SPEAK;

function goStart() {
  leaveConversation();
  state.session = null;
  show("start");
  refreshStart();
}

function refreshStart() {
  loadStartPanels($("opt-level").value);
  loadPhrasebookPanel();
  loadHistory();
}

function sessionOptions() {
  return {
    slow: $("opt-slow").checked,
    difficulty: storageGet("difficulty", "same"),
    level: $("opt-level").value,
  };
}

function renderScenarios() {
  $("scenarios").replaceChildren(...config.scenarios.map((s) =>
    el("button", { type: "button", class: "scenario", onclick: () => startSession(s.key, sessionOptions()) },
      el("h3", { text: s.title }), el("span", { class: "blurb", text: s.blurb }))));
}

function initOptions() {
  for (const id of ["opt-slow", "opt-hide"]) {
    $(id).checked = storageGet(id, "0") === "1";
    $(id).addEventListener("change", (e) => storageSet(id, e.target.checked ? "1" : "0"));
  }
  const select = $("opt-level");
  select.append(...config.levels.map((level) => el("option", { value: level, text: level })));
  select.value = storageGet("opt-level", "auto");
  if (!select.value) select.value = "auto";
  select.addEventListener("change", () => { storageSet("opt-level", select.value); loadStartPanels(select.value); });

  const segmented = $("difficulty");
  const paint = (value) => {
    for (const button of segmented.querySelectorAll("button")) {
      button.setAttribute("aria-checked", String(button.dataset.value === value));
    }
  };
  paint(storageGet("difficulty", "same"));
  segmented.addEventListener("click", (e) => {
    const button = e.target.closest("button");
    if (!button) return;
    storageSet("difficulty", button.dataset.value);
    paint(button.dataset.value);
  });
}

// Пробел — «рация» на экранах разговора и тетради. Если фокус на кнопке (пришли Tab'ом),
// пробел нажимает её, как обычно.
function spaceTarget(e) {
  if (e.code !== "Space" || e.target.closest?.("button, input, textarea, select, summary")) return null;
  const page = currentPage();
  if (page === "talk" && state.session) return turnTarget();
  if (page === "cards") return drillTarget();
  return null;
}

function initKeyboard() {
  document.addEventListener("keydown", (e) => {
    const target = spaceTarget(e);
    if (!target) return;
    e.preventDefault();
    if (!e.repeat) startRecording(target);
  });
  document.addEventListener("keyup", (e) => {
    if (e.code !== "Space" || !["talk", "cards"].includes(currentPage())) return;
    if (e.target.closest?.("button, input, textarea, select, summary")) return;
    e.preventDefault();
    stopRecording();
  });
  // После щелчка мышью снимаем фокус с кнопки, чтобы пробел снова работал как «рация»
  // (e.detail === 0 — кнопку нажали с клавиатуры, там фокус нужен).
  document.addEventListener("click", (e) => {
    if (e.detail > 0) e.target.closest?.("button")?.blur();
  });
}

initTalk({ exit: goStart });
initPhrasebook({ exit: goStart });
initProgress({ exit: goStart });
$("btn-review").addEventListener("click", () => openCards(true));
$("btn-cards").addEventListener("click", () => openCards(false));
$("btn-progress").addEventListener("click", openProgress);
renderScenarios();
initOptions();
initKeyboard();
refreshStart();
