// Прогресс: уровень и серия на старте, графики за 30 дней на отдельной странице.

import { getJson } from "./api.js";
import { $, el, plural, show, svg, toast } from "./dom.js";

const LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"];
const PAD = { top: 12, right: 12, bottom: 24, left: 40 };
const HEIGHT = 170;
let lastData = null;

function shortDate(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

// ---------- старт: уровень и серия ----------

export async function loadStartPanels(manualLevel) {
  try {
    const data = await getJson("/api/progress");
    renderLevelPanel(data.level, manualLevel);
    renderStreakPanel(data);
  } catch (err) {
    $("lvl-text").textContent = err.message;
  }
}

function renderLevelPanel(level, manualLevel) {
  const fallback = window.SPEAK.default_level;
  if (manualLevel !== "auto") {
    $("lvl-code").textContent = manualLevel;
    $("lvl-text").textContent = level.level
      ? `Выбран вручную. По твоим репликам сейчас выходит ${level.level}.`
      : "Выбран вручную. Оценка по репликам появится после нескольких ответов.";
    $("lvl-bar").style.width = "0%";
    return;
  }
  if (!level.level) {
    $("lvl-code").textContent = fallback;
    $("lvl-text").textContent = `Ещё ${level.need} ${plural(level.need, "развёрнутая реплика", "развёрнутые реплики", "развёрнутых реплик")} — и я оценю уровень. Пока считаю ${fallback}.`;
    $("lvl-bar").style.width = `${Math.round(100 * level.samples / (level.samples + level.need))}%`;
    return;
  }
  $("lvl-code").textContent = level.level;
  $("lvl-text").textContent = level.next
    ? `По ${level.samples} последним репликам. До ${level.next}: уже ${level.next_share}% реплик на уровне ${level.next}.`
    : `По ${level.samples} последним репликам — это верх шкалы.`;
  $("lvl-bar").style.width = `${level.next ? level.next_share : 100}%`;
}

function renderStreakPanel(data) {
  $("pr-streak").textContent = data.streak;
  $("pr-streak-word").textContent = ` ${plural(data.streak, "день", "дня", "дней")} подряд`;
  const week = data.totals.week_minutes;
  const weekText = week >= 1 ? `${week} мин` : `${Math.round(week * 60)} с`;
  $("pr-text").textContent = data.totals.turns
    ? `Твоя речь за неделю: ${weekText}. Всего реплик: ${data.totals.turns}.`
    : "Минуты речи за неделю появятся после первого разговора.";
  const recent = data.days.slice(-14);
  const max = Math.max(...recent.map((d) => d.minutes), 0.1);
  const spark = $("pr-spark");
  const step = 140 / recent.length;
  spark.replaceChildren(...recent.map((d, i) => {
    const h = d.minutes ? Math.max(2, (d.minutes / max) * 26) : 1;
    return svg("rect", { x: (i * step + 1).toFixed(1), y: (28 - h).toFixed(1), width: (step - 2).toFixed(1),
                         height: h.toFixed(1), rx: 1, class: d.minutes ? "spark-bar" : "spark-empty" });
  }));
}

// ---------- страница прогресса ----------

export async function openProgress() {
  show("progress");
  try {
    lastData = await getJson("/api/progress");
    renderProgress(lastData);
  } catch (err) {
    toast(err.message);
  }
}

export function initProgress({ exit }) {
  $("btn-progress-back").addEventListener("click", exit);
  new ResizeObserver(() => { if (lastData && !$("progress").hidden) renderCharts(lastData.days); })
    .observe($("ch-minutes"));
}

function renderProgress(data) {
  const lvl = data.level;
  $("pg-tiles").replaceChildren(...[
    ["дней подряд", data.streak],
    ["минут речи", data.totals.minutes],
    ["реплик", data.totals.turns],
    ["дней с практикой", data.totals.active_days],
    ["уровень", lvl.level ? `${lvl.level}` : "—", lvl.level ? `по ${lvl.samples} репликам` : `нужно ещё ${lvl.need}`],
  ].map(([label, value, note]) => el("div", {}, el("dt", { text: label }), el("dd", { text: String(value) }),
                                     note ? el("span", { class: "tile-note", text: note }) : null)));
  renderCharts(data.days);
  renderTable(data.days);
}

function renderCharts(days) {
  chart($("ch-minutes"), days, { kind: "bar", value: (d) => d.minutes || null, format: (v) => `${v} мин` });
  chart($("ch-wpm"), days, { kind: "line", value: (d) => d.wpm, format: (v) => `${v} сл/мин` });
  chart($("ch-clean"), days, { kind: "line", value: (d) => d.clean_pct, max: 100, format: (v) => `${v}%` });
  chart($("ch-level"), days, { kind: "line", value: (d) => (d.level ? LEVELS.indexOf(d.level) : null),
                               categories: LEVELS, format: (v) => LEVELS[v] });
}

function niceMax(value) {
  if (value <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(value));
  return [1, 2, 2.5, 5, 10].map((m) => m * pow).find((m) => m >= value);
}

/** Один график — одна серия: столбики или линия с разрывами в дни без практики. */
function chart(box, days, { kind, value, format, max, categories }) {
  const values = days.map(value);
  const present = values.filter((v) => v != null);
  if (!present.length) {
    box.replaceChildren(el("p", { class: "chart-empty", text: "Пока нет данных — поговори хотя бы раз." }));
    return;
  }
  const width = Math.max(280, box.clientWidth);
  const plotW = width - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const yMax = categories ? categories.length - 1 : (max ?? niceMax(Math.max(...present)));
  const yMin = 0;
  const band = plotW / days.length;
  const x = (i) => PAD.left + band * i + band / 2;
  const y = (v) => PAD.top + plotH - ((v - yMin) / (yMax - yMin || 1)) * plotH;
  const root = svg("svg", { viewBox: `0 0 ${width} ${HEIGHT}`, width, height: HEIGHT, role: "img",
                            "aria-label": box.previousElementSibling?.textContent || "" });

  const ticks = categories ? categories.map((_, i) => i) : [0, yMax / 2, yMax];
  for (const t of ticks) {
    root.append(svg("line", { x1: PAD.left, x2: width - PAD.right, y1: y(t), y2: y(t), class: "grid" }));
    const label = svg("text", { x: PAD.left - 8, y: y(t) + 4, class: "axis", "text-anchor": "end" });
    label.textContent = categories ? categories[t] : String(Math.round(t * 10) / 10);
    root.append(label);
  }
  for (const i of [0, Math.floor(days.length / 2), days.length - 1]) {
    const label = svg("text", { x: x(i), y: HEIGHT - 6, class: "axis", "text-anchor": i === 0 ? "start" : i === days.length - 1 ? "end" : "middle" });
    label.textContent = shortDate(days[i].date);
    root.append(label);
  }

  if (kind === "bar") drawBars(root, values, x, y, band, yMin);
  else drawLine(root, values, x, y);
  addHover(root, days, values, x, band, format, kind === "line" ? { top: PAD.top, bottom: PAD.top + plotH } : null);
  box.replaceChildren(root);
}

function drawBars(root, values, x, y, band, yMin) {
  const w = Math.min(14, band - 2);
  values.forEach((v, i) => {
    if (v == null) return;
    const top = y(v), base = y(yMin), h = Math.max(2, base - top), r = Math.min(4, w / 2, h);
    const left = x(i) - w / 2;
    // скруглены только верхние углы — столбик «стоит» на базовой линии
    const d = `M${left},${base} V${base - h + r} Q${left},${base - h} ${left + r},${base - h} `
            + `H${left + w - r} Q${left + w},${base - h} ${left + w},${base - h + r} V${base} Z`;
    root.append(svg("path", { d, class: "bar" }));
  });
}

function drawLine(root, values, x, y) {
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v == null) { pen = false; return; }
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
    pen = true;
  });
  root.append(svg("path", { d, class: "line" }));
  const last = values.reduce((acc, v, i) => (v != null ? i : acc), -1);
  values.forEach((v, i) => {
    if (v != null) root.append(svg("circle", { cx: x(i), cy: y(v), r: i === last ? 5 : 4, class: i === last ? "dot last" : "dot" }));
  });
}

function addHover(root, days, values, x, band, format, crosshair) {
  const guide = crosshair ? svg("line", { y1: crosshair.top, y2: crosshair.bottom, class: "crosshair", visibility: "hidden" }) : null;
  if (guide) root.append(guide);
  const tip = $("tooltip");
  days.forEach((day, i) => {
    const hit = svg("rect", { x: x(i) - band / 2, y: 0, width: band, height: HEIGHT, class: "hit" });
    hit.addEventListener("pointerenter", () => {
      const v = values[i];
      tip.textContent = `${shortDate(day.date)}: ${v == null ? "нет практики" : format(v)}`;
      tip.hidden = false;
      if (guide) { guide.setAttribute("x1", x(i)); guide.setAttribute("x2", x(i)); guide.setAttribute("visibility", "visible"); }
    });
    hit.addEventListener("pointermove", (e) => {
      tip.style.left = `${Math.min(e.clientX + 12, window.innerWidth - tip.offsetWidth - 8)}px`;
      tip.style.top = `${e.clientY - 36}px`;
    });
    hit.addEventListener("pointerleave", () => {
      tip.hidden = true;
      guide?.setAttribute("visibility", "hidden");
    });
    root.append(hit);
  });
}

function renderTable(days) {
  const active = days.filter((d) => d.turns || d.reviews).reverse();
  const head = el("tr", {}, ...["Дата", "Реплик", "Минут речи", "Темп", "Без ошибок", "Уровень", "Повторений"]
    .map((h) => el("th", { scope: "col", text: h })));
  const rows = active.map((d) => el("tr", {}, ...[shortDate(d.date), d.turns, d.minutes, d.wpm ?? "—",
    d.clean_pct == null ? "—" : `${d.clean_pct}%`, d.level ?? "—", d.reviews].map((v) => el("td", { text: String(v) }))));
  $("pg-table").replaceChildren(el("thead", {}, head),
    el("tbody", {}, rows.length ? rows : [el("tr", {}, el("td", { colspan: 7, text: "Пока нет занятий за 30 дней" }))]));
}
