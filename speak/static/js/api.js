// Запросы к локальному серверу. POST несёт X-Speak — сервер без него отказывает (защита от чужих сайтов).

export async function post(path, { json, blob } = {}) {
  const headers = { "X-Speak": "1" };
  let body;
  if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  if (blob) { headers["Content-Type"] = blob.type || "application/octet-stream"; body = blob; }
  const res = await fetch(path, { method: "POST", headers, body });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `ошибка сервера (${res.status})`);
  return res;
}

export async function postJson(path, options) {
  return (await post(path, options)).json();
}

export async function getJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `ошибка сервера (${res.status})`);
  return res.json();
}

/** Поток NDJSON: по объекту на строку, по мере прихода. */
export async function* ndjson(res) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finished = false;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (line) yield JSON.parse(line);
      }
      if (done) { finished = true; return; }
    }
  } finally {
    // цикл бросили на середине (ушли со страницы) — закрываем поток, и сервер перестаёт озвучивать впустую
    if (!finished) reader.cancel().catch(() => {});
  }
}
