// DOM-помощники. Весь текст от сервера вставляется через textContent — никакого innerHTML с чужими строками.

export const $ = (id) => document.getElementById(id);

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else if (value !== false && value != null) node.setAttribute(key, value);
  }
  for (const child of children.flat()) if (child != null && child !== false) node.append(child);
  return node;
}

const SVG_NS = "http://www.w3.org/2000/svg";
export function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

export const PAGES = ["start", "talk", "summary", "cards", "progress"];

export function show(pageId) {
  for (const id of PAGES) $(id).hidden = id !== pageId;
  window.scrollTo(0, 0);
}

export function currentPage() {
  return PAGES.find((id) => !$(id).hidden);
}

let toastTimer = 0;
export function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 6000);
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Склонение: plural(5, "день", "дня", "дней") → "дней". */
export function plural(n, one, few, many) {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

export function storageGet(key, fallback) {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}

export function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* хранилище недоступно — настройка просто не запомнится */ }
}
