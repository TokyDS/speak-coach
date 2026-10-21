// Звук: очередь воспроизведения (ответ собеседника приходит по предложениям), запись с микрофона,
// индикатор громкости и машина состояний «рации» (Пробел / кнопка).

import { cssVar, toast } from "./dom.js";
import { setBusy, setStatus, state } from "./state.js";

const MIN_RECORD_MS = 500;
const LEVEL_BARS = 40;

// ---------- воспроизведение ----------

const audio = new Audio();
const clip = new Audio();       // отрывок своей записи (для произношения)
let queue = [];
let clipTimer = 0;

audio.addEventListener("ended", () => playNext());

function playNext() {
  const src = queue.shift();
  if (!src) return;
  audio.src = src;
  audio.play().catch(() => setStatus("Нажми «Повторить вопрос», чтобы услышать"));
}

export const player = {
  /** Сыграть с начала (прерывает текущее). */
  play(sources) {
    this.stop();
    queue = [...[sources].flat().filter(Boolean)];
    playNext();
  },
  /** Добавить в очередь — заиграет, когда закончится текущее. */
  enqueue(src) {
    queue.push(src);
    if (audio.paused || audio.ended) playNext();
  },
  stop() {
    queue = [];
    audio.pause();
    clip.pause();
    clearTimeout(clipTimer);
  },
  /** Отрывок записи с start по end секунд. */
  playSlice(url, start, end) {
    this.stop();
    clip.src = url;
    clip.currentTime = Math.max(0, start - 0.15);
    clip.play().catch(() => toast("Не получилось проиграть запись"));
    clipTimer = setTimeout(() => clip.pause(), (end - start + 0.3) * 1000);
  },
};

// ---------- запись ----------

const recorder = {
  stream: null, media: null, chunks: [], analyser: null, raf: 0, levels: [], canvas: null,

  async ensureStream() {
    if (this.stream) return;
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    const ctx = new AudioContext();
    this.analyser = ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    ctx.createMediaStreamSource(this.stream).connect(this.analyser);
  },

  mimeType() {
    for (const type of ["audio/ogg;codecs=opus", "audio/webm;codecs=opus", "audio/webm", "audio/mp4"]) {
      if (MediaRecorder.isTypeSupported(type)) return type;
    }
    return "";
  },

  async start(canvas) {
    await this.ensureStream();
    const mimeType = this.mimeType();
    // у каждой записи свой массив: поздние данные прошлой записи не попадут в новую
    const chunks = [];
    const media = new MediaRecorder(this.stream, mimeType ? { mimeType } : {});
    media.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    media.start();
    this.media = media;
    this.chunks = chunks;
    this.canvas = canvas;
    this.levels = [];
    this.drawLevel();
  },

  stop() {
    const media = this.media;
    const chunks = this.chunks;
    this.media = null;
    return new Promise((resolve) => {
      cancelAnimationFrame(this.raf);
      drawIdleLevel(this.canvas);
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
    drawBars(this.canvas, this.levels, cssVar("--onair"));
    this.raf = requestAnimationFrame(() => this.drawLevel());
  },
};

export function drawBars(canvas, levels, color) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const step = canvas.width / LEVEL_BARS;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = color;
  levels.forEach((level, i) => {
    const h = Math.max(2, level * canvas.height);
    ctx.fillRect(i * step + 1, (canvas.height - h) / 2, step - 2, h);
  });
}

export function drawIdleLevel(canvas) {
  drawBars(canvas, new Array(LEVEL_BARS).fill(0), cssVar("--line"));
}

// ---------- «рация» ----------
// target: {mic, canvas, button?, buttonLabel?, statusText, onBlob(blob, gen)}

const mic = { starting: false, stopWhenStarted: false, recording: null };

export function isRecording() {
  return Boolean(mic.recording || mic.starting);
}

export async function startRecording(target) {
  if (state.busy || isRecording()) return;
  const gen = state.generation;
  mic.starting = true;
  mic.stopWhenStarted = false;
  player.stop();
  try {
    await recorder.start(target.canvas);
  } catch {
    toast("Нет доступа к микрофону. Разреши его в адресной строке Firefox и попробуй снова.");
    return;
  } finally {
    mic.starting = false;
  }
  if (gen !== state.generation) { recorder.stop(); return; }  // пока включался микрофон, ушли со страницы
  mic.recording = { target, startedAt: performance.now() };
  target.mic?.classList.add("recording");
  if (target.button) target.button.textContent = "Стоп";
  setStatus(target.statusText || "Говори… отпусти пробел, когда закончишь");
  if (mic.stopWhenStarted) stopRecording();
}

function endRecordingUi(rec) {
  rec.target.mic?.classList.remove("recording");
  if (rec.target.button) rec.target.button.textContent = rec.target.buttonLabel;
}

export async function stopRecording() {
  if (mic.starting) { mic.stopWhenStarted = true; return; }
  const rec = mic.recording;
  if (!rec) return;
  const gen = state.generation;
  mic.recording = null;
  endRecordingUi(rec);
  setBusy(true);  // сразу: новая запись не начнётся, пока эта не отправлена
  try {
    const blob = await recorder.stop();
    if (gen !== state.generation) return;
    if (!blob || performance.now() - rec.startedAt < MIN_RECORD_MS) {
      setStatus("Слишком коротко — держи пробел, пока говоришь");
      return;
    }
    await rec.target.onBlob(blob, gen);
  } finally {
    if (gen === state.generation) setBusy(false);
  }
}

export function toggleRecording(target) {
  if (isRecording()) stopRecording();
  else startRecording(target);
}

/** Уход со страницы: выключить запись без отправки. */
export function cancelRecording() {
  mic.stopWhenStarted = true;
  if (mic.recording) {
    endRecordingUi(mic.recording);
    mic.recording = null;
    recorder.stop();
  }
  player.stop();
}
