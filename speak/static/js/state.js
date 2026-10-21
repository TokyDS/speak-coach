// Общее состояние страницы и блокировка кнопок, пока ждём сервер.

export const state = {
  session: null,          // {id, title, partnerLevel}
  busy: false,            // ждём ответа сервера
  generation: 0,          // растёт при уходе со страницы: опоздавшие ответы отбрасываются
};

/** Блокирует все элементы с атрибутом data-lock на время запроса. */
export function setBusy(busy, status) {
  state.busy = busy;
  for (const node of document.querySelectorAll("[data-lock]")) node.disabled = busy;
  for (const mic of document.querySelectorAll(".mic")) mic.classList.toggle("busy", busy);
  if (status) setStatus(status);
}

/** Статус в пульте видимой страницы. */
export function setStatus(text) {
  for (const node of document.querySelectorAll("main:not([hidden]) [data-status]")) node.textContent = text;
}

/** Уход со страницы: всё, что ещё в пути, относится к прошлому «поколению» и будет проигнорировано. */
export function nextGeneration() {
  state.generation += 1;
  setBusy(false);
  return state.generation;
}
