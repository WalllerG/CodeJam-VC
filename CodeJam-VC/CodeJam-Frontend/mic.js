"use strict";

// VoiceCal schedule builder: record or type -> review the parsed events -> add them to Google Calendar.

const MAX_RECORDING_MS = 60_000;
const MIN_RECORDING_MS = 700;
const REQUEST_TIMEOUT_MS = 180_000; // Whisper on a CPU can be slow for long recordings
const MAX_EVENTS_PER_REQUEST = 10;
const MAX_SCHEDULE_SIZE = 30;
const STORAGE_KEY = "voicecal.schedule.v1";
const TIME_ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone || "America/Toronto";
const DEFAULT_SUBTITLE = "Press the microphone button to start recording";
// Chrome/Firefox record webm, Safari only mp4
const RECORDING_FORMATS = [
  ["audio/webm;codecs=opus", ".webm"],
  ["audio/webm", ".webm"],
  ["audio/mp4", ".m4a"],
  ["audio/ogg;codecs=opus", ".ogg"],
];

const SIGN_IN_FLAG = "voicecal.signingIn";
const AUTH_ERRORS = {
  denied: "You cancelled the Google sign-in, so nothing was added.",
  expired: "The sign-in took too long or was started in another tab. Please try again.",
  calendar_scope: "VoiceCal needs permission to add events. Sign in again and tick the Google Calendar box.",
  failed: "Google sign-in didn't work. Please try again.",
};

const account = window.VoiceCalAccount;
const $ = (id) => document.getElementById(id);
const els = {
  subtitle: $("subtitle"),
  ring: $("recordRing"),
  recordBtn: $("recordBtn"),
  recDot: $("recDot"),
  timer: $("timer"),
  cancelBtn: $("cancelBtn"),
  hint: $("hint"),
  typeForm: $("typeForm"),
  textInput: $("textInput"),
  typeSubmit: $("typeSubmit"),
  transcript: $("transcript"),
  transcriptText: $("transcriptText"),
  notices: $("notices"),
  count: $("count"),
  addManual: $("addManual"),
  eventList: $("eventList"),
  emptyState: $("emptyState"),
  clearBtn: $("clearBtn"),
  saveBtn: $("saveBtn"),
  saveLabel: $("saveLabel"),
  toast: $("toast"),
};

let schedule = loadSchedule();
let busy = false;
let saving = false;
let micAvailable = true;
let nextId = 1;

const rec = {
  recorder: null,
  stream: null,
  chunks: [],
  startedAt: 0,
  cancelled: false,
  timerId: null,
  autoStopId: null,
  audioContext: null,
  meterFrame: null,
};

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value == null) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key in el && typeof value !== "string") el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child != null) el.append(child);
  }
  return el;
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

const pad = (n) => String(n).padStart(2, "0");
const localDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const localTime = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function addDays(dateValue, days) {
  const d = new Date(`${dateValue}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function newId() {
  return `ev-${Date.now().toString(36)}-${nextId++}`;
}

function endsNextDay(item) {
  return Boolean(item.start && item.end && item.end <= item.start);
}

function toPayload(item) {
  const endDate = endsNextDay(item) ? addDays(item.date, 1) : item.date;
  return {
    activity: item.activity.trim(),
    location: item.location.trim() || null,
    start: `${item.date}T${item.start}`,
    end: `${endDate}T${item.end}`,
  };
}

function validate(item) {
  const errors = {};
  if (!item.activity.trim()) errors.activity = "Give the event a name.";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(item.date)) errors.date = "Pick a date.";
  if (!/^\d{2}:\d{2}$/.test(item.start)) errors.start = "Pick a start time.";
  if (!/^\d{2}:\d{2}$/.test(item.end)) errors.end = "Pick an end time.";
  else if (item.end === item.start) errors.end = "The end time must differ from the start time.";
  return errors;
}

// ---------------------------------------------------------------------------
// Schedule state
// ---------------------------------------------------------------------------

function loadSchedule() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    if (!Array.isArray(saved)) return [];
    const now = new Date(); // computed inline: this runs before the date helpers below are defined
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    // Drop abandoned drafts: unsaved cards that never got a name and whose day is already over
    return saved.filter((item) => item && typeof item.id === "string"
      && !(item.status !== "created" && !String(item.activity || "").trim() && item.date && item.date < today));
  } catch {
    return [];
  }
}

function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(schedule));
  } catch {
    // private mode or storage full: the schedule still works for this visit
  }
}

function fromServerEvent(event) {
  return {
    id: newId(),
    activity: event.activity || "",
    location: event.location || "",
    date: (event.start || "").slice(0, 10),
    start: (event.start || "").slice(11, 16),
    end: (event.end || "").slice(11, 16),
    warnings: Array.isArray(event.warnings) ? event.warnings : [],
    status: "pending",
    message: "",
    link: "",
  };
}

function addEvents(events) {
  const room = MAX_SCHEDULE_SIZE - schedule.length;
  const added = events.slice(0, Math.max(0, room)).map(fromServerEvent);
  if (events.length > added.length) {
    showNotice(`Your list holds up to ${MAX_SCHEDULE_SIZE} events. Add or clear some before adding more.`, "warn");
  }
  schedule.push(...added);
  persist();
  render();
  return added;
}

function pendingItems() {
  return schedule.filter((item) => item.status !== "created");
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function render() {
  els.eventList.replaceChildren(...schedule.map(renderCard));
  updateChrome();
}

function updateChrome() {
  const pending = pendingItems().length;
  els.count.textContent = String(schedule.length);
  els.emptyState.hidden = schedule.length > 0;
  const needsSignIn = account.state.configured && !account.state.signedIn;
  els.clearBtn.disabled = schedule.length === 0 || saving;
  els.saveBtn.disabled = pending === 0 || saving || busy || !account.state.online || !account.state.configured;
  if (saving) els.saveLabel.textContent = "Adding…";
  else if (needsSignIn) els.saveLabel.textContent = "Sign in to add to Google Calendar";
  else els.saveLabel.textContent = pending > 0 ? `Add ${plural(pending, "event")} to Google Calendar` : "Add to Google Calendar";
  els.addManual.disabled = schedule.length >= MAX_SCHEDULE_SIZE;
}

function renderCard(item) {
  const locked = item.status === "created";
  const errors = item.showErrors ? validate(item) : {};

  const field = (label, key, type, extra = {}) =>
    h("label", { class: `field ${extra.class || ""}` },
      h("span", { class: "field-label" }, label, extra.badge || null),
      h("input", {
        type,
        value: item[key],
        disabled: locked,
        placeholder: extra.placeholder || null,
        maxlength: type === "text" ? "200" : null,
        "aria-invalid": errors[key] ? "true" : null,
        title: errors[key] || null,
        oninput: (e) => updateItem(item, key, e.target.value),
      }));

  const nextDayBadge = h("span", { class: "next-day", hidden: !endsNextDay(item) }, "+1 day");

  const card = h("article", { class: "event-card", dataset: { id: item.id, status: item.status } },
    h("div", { class: "event-top" },
      h("input", {
        class: "event-title",
        type: "text",
        value: item.activity,
        maxlength: "200",
        placeholder: "Event name",
        "aria-label": "Event name",
        disabled: locked,
        "aria-invalid": errors.activity ? "true" : null,
        oninput: (e) => updateItem(item, "activity", e.target.value),
      }),
      h("button", {
        class: "icon-btn",
        type: "button",
        title: locked ? "Remove from this list (stays on your calendar)" : "Remove event",
        "aria-label": "Remove event",
        onclick: () => removeItem(item),
      }, icon("x"))),
    h("div", { class: "event-grid" },
      field("Date", "date", "date", { class: "field-date" }),
      field("Start", "start", "time"),
      field("End", "end", "time", { badge: nextDayBadge }),
      field("Location", "location", "text", { class: "field-wide", placeholder: "Optional" })),
    item.warnings.length > 0 && item.status === "pending"
      ? h("ul", { class: "event-warnings" }, item.warnings.map((w) => h("li", {}, icon("alert"), h("span", {}, w))))
      : null,
    renderStatus(item));
  return card;
}

function renderStatus(item) {
  if (item.status === "created") {
    return h("div", { class: "event-status", dataset: { tone: "success" } }, icon("check"), "Added to your calendar",
      item.link ? h("a", { href: item.link, target: "_blank", rel: "noopener" }, "Open", icon("external")) : null);
  }
  if (item.status === "duplicate") {
    return h("div", { class: "event-status", dataset: { tone: "warn" } }, icon("alert"),
      item.message || "Already on your calendar.",
      item.link ? h("a", { href: item.link, target: "_blank", rel: "noopener" }, "View", icon("external")) : null);
  }
  if (item.status === "error") {
    return h("div", { class: "event-status", dataset: { tone: "error" } }, icon("alert"), item.message || "Couldn't add this event.");
  }
  return null;
}

function updateItem(item, key, value) {
  item[key] = value;
  if (item.status === "error" || item.status === "duplicate") {
    item.status = "pending";
    item.message = "";
  }
  persist();

  // Update the card in place so the input keeps focus while typing
  const card = els.eventList.querySelector(`[data-id="${item.id}"]`);
  if (card) {
    card.dataset.status = item.status;
    card.querySelector(".event-status")?.remove();
    card.querySelector(".next-day").hidden = !endsNextDay(item);
    if (item.showErrors) {
      const errors = validate(item);
      card.querySelector(".event-title").setAttribute("aria-invalid", errors.activity ? "true" : "false");
      const inputs = card.querySelectorAll(".event-grid input");
      ["date", "start", "end"].forEach((k, i) => inputs[i].setAttribute("aria-invalid", errors[k] ? "true" : "false"));
    }
  }
  updateChrome();
}

function removeItem(item) {
  schedule = schedule.filter((other) => other !== item);
  persist();
  render();
}

function addManualEvent() {
  const start = new Date();
  start.setMinutes(0, 0, 0);
  start.setHours(start.getHours() + 1);
  const end = new Date(start.getTime() + 60 * 60 * 1000);
  const item = {
    id: newId(),
    activity: "",
    location: "",
    date: localDate(start),
    start: localTime(start),
    end: localTime(end),
    warnings: [],
    status: "pending",
    message: "",
    link: "",
  };
  schedule.push(item);
  persist();
  render();
  els.eventList.querySelector(`[data-id="${item.id}"] .event-title`)?.focus();
}

// ---------------------------------------------------------------------------
// Notices and toast
// ---------------------------------------------------------------------------

function clearNotices() {
  els.notices.replaceChildren();
}

function showNotice(message, tone = "warn") {
  els.notices.append(h("div", { class: "notice", dataset: { tone } }, icon("alert"), h("span", {}, message)));
}

let toastTimer = null;
function showToast(message, tone = "success") {
  els.toast.textContent = message;
  els.toast.dataset.tone = tone;
  els.toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => els.toast.classList.remove("show"), 2800);
}

// ---------------------------------------------------------------------------
// Server calls
// ---------------------------------------------------------------------------

function errorDetail(data) {
  const detail = data && data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg).filter(Boolean).join(" ");
  return "";
}

async function request(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let res;
  try {
    res = await fetch(url, { ...options, signal: controller.signal });
  } catch (err) {
    if (err.name === "AbortError") throw new Error("The server took too long to respond. Please try again.");
    throw new Error("Can't reach the VoiceCal server. Make sure it's running (uvicorn demo:app --reload).");
  } finally {
    clearTimeout(timeout);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    // non-JSON error page
  }
  if (!res.ok) {
    const error = new Error(errorDetail(data) || `Something went wrong on the server (${res.status}).`);
    error.status = res.status;
    throw error;
  }
  return data;
}

function applyServerState(state) {
  const health = state.health;
  if (health && !health.speech_installed) {
    disableMic("Voice input isn't set up on the server yet — type your events below instead.");
  } else if (health && health.ffmpeg_installed === false) {
    disableMic("The server needs ffmpeg to understand recordings (winget install ffmpeg) — type your events below for now.");
  }
  updateChrome();
}

async function runParse(send, label) {
  if (busy) return;
  busy = true;
  clearNotices();
  setMicState("processing");
  els.subtitle.textContent = label;
  els.textInput.disabled = els.typeSubmit.disabled = true;
  updateChrome();
  try {
    handleParsed(await send());
  } catch (err) {
    els.subtitle.textContent = DEFAULT_SUBTITLE;
    showNotice(err.message, "error");
  } finally {
    busy = false;
    setMicState("idle");
    els.textInput.disabled = els.typeSubmit.disabled = false;
    updateChrome();
  }
}

function handleParsed(data) {
  const text = (data.text || "").trim();
  els.transcript.hidden = !text;
  els.transcriptText.textContent = text;

  (data.warnings || []).forEach((w) => showNotice(w, "warn"));
  const added = addEvents(data.events || []);
  if (added.length === 0) {
    els.subtitle.textContent = "Nothing to add yet — try again";
    return;
  }

  const stacked = els.eventList.getBoundingClientRect().top > els.recordBtn.getBoundingClientRect().bottom;
  els.subtitle.textContent = `Added ${plural(added.length, "event")} — review ${stacked ? "below" : "on the right"}`;
  const firstCard = els.eventList.querySelector(`[data-id="${added[0].id}"]`);
  if (stacked && firstCard) firstCard.scrollIntoView({ behavior: "smooth", block: "center" });
}

function parseText(text) {
  const trimmed = text.trim();
  if (!trimmed) {
    els.textInput.focus();
    return;
  }
  runParse(() => request("/api/parse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: trimmed, timezone: TIME_ZONE }),
  }), "Reading your schedule…").then(() => {
    if (!els.notices.querySelector('[data-tone="error"]')) els.textInput.value = "";
  });
}

async function saveSchedule() {
  if (saving || busy) return;
  const pending = pendingItems();
  if (pending.length === 0) return;

  if (!account.state.signedIn) {
    // The schedule is already saved in this browser, so it is still here after Google sends the user back
    try {
      sessionStorage.setItem(SIGN_IN_FLAG, "1");
    } catch {
      // only used for the welcome-back message
    }
    account.signIn();
    return;
  }

  const invalid = pending.filter((item) => Object.keys(validate(item)).length > 0);
  if (invalid.length) {
    invalid.forEach((item) => (item.showErrors = true));
    render();
    clearNotices();
    showNotice(`Fix the highlighted fields on ${plural(invalid.length, "event")} first.`, "error");
    els.eventList.querySelector('[aria-invalid="true"]')?.focus();
    return;
  }

  saving = true;
  clearNotices();
  updateChrome();
  let created = 0;
  let failed = 0;
  let duplicates = 0;
  try {
    // The server accepts a limited batch size, so send long lists in chunks
    for (let i = 0; i < pending.length; i += MAX_EVENTS_PER_REQUEST) {
      const batch = pending.slice(i, i + MAX_EVENTS_PER_REQUEST);
      const data = await request("/api/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ events: batch.map(toPayload), timezone: TIME_ZONE, skip_duplicates: true }),
      });
      for (const result of data.results || []) {
        const item = batch[result.index];
        if (!item) continue;
        item.status = result.status;
        item.message = result.message || "";
        item.link = result.link || "";
        if (result.status === "created") created++;
        else if (result.status === "duplicate") duplicates++;
        else failed++;
      }
    }
  } catch (err) {
    showNotice(err.message, "error");
    if (err.status === 401) account.refresh(); // signed out or access revoked: show the sign-in button again
  } finally {
    saving = false;
    pending.forEach((item) => delete item.showErrors);
    persist();
    render();
  }

  if (created) {
    showToast(created === 1 ? "✔ Event added to your Google Calendar" : `✔ ${created} events added to your Google Calendar`);
  }
  if (duplicates) showNotice(`${plural(duplicates, "event")} ${duplicates === 1 ? "was" : "were"} already on your calendar, so I skipped ${duplicates === 1 ? "it" : "them"}.`, "warn");
  if (failed) showNotice(`${plural(failed, "event")} couldn't be added — see the messages on ${failed === 1 ? "it" : "them"}.`, "error");
}

function clearSchedule() {
  const unsaved = pendingItems().length;
  if (unsaved && !window.confirm(`Remove ${plural(unsaved, "unsaved event")} from the list?`)) return;
  schedule = [];
  persist();
  render();
  clearNotices();
}

// ---------------------------------------------------------------------------
// Recording
// ---------------------------------------------------------------------------

function canRecord() {
  return Boolean(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
}

function disableMic(message) {
  micAvailable = false;
  els.recordBtn.disabled = true;
  els.recordBtn.setAttribute("aria-label", "Recording unavailable");
  els.subtitle.textContent = message;
  els.hint.hidden = true;
}

function setMicState(state) {
  els.recordBtn.dataset.state = state;
  els.recordBtn.disabled = !micAvailable || state === "processing";
  els.recordBtn.setAttribute("aria-label", state === "recording" ? "Stop recording" : "Start recording");
  const recording = state === "recording";
  els.recDot.hidden = els.timer.hidden = els.cancelBtn.hidden = !recording;
  if (state === "idle" && micAvailable && !busy && els.subtitle.textContent === "Listening…") {
    els.subtitle.textContent = DEFAULT_SUBTITLE;
  }
}

function micErrorMessage(err) {
  switch (err && err.name) {
    case "NotAllowedError":
    case "SecurityError":
      return "Microphone access is blocked. Allow it from the icon in your address bar, then try again.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "No microphone was found. Plug one in or type your events instead.";
    case "NotReadableError":
      return "Your microphone is being used by another app. Close it and try again.";
    default:
      return "Couldn't start recording. You can type your events instead.";
  }
}

function pickFormat() {
  for (const [mimeType, ext] of RECORDING_FORMATS) {
    if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(mimeType)) return [mimeType, ext];
  }
  return ["", ".webm"];
}

async function startRecording() {
  if (busy || saving || rec.recorder || !micAvailable) return;
  clearNotices();
  if (!canRecord()) {
    showNotice("This browser can't record audio here (it needs https or localhost). Type your events instead.", "error");
    return;
  }

  try {
    rec.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
  } catch (err) {
    showNotice(micErrorMessage(err), "error");
    return;
  }

  const [mimeType, ext] = pickFormat();
  try {
    rec.recorder = new MediaRecorder(rec.stream, mimeType ? { mimeType } : undefined);
  } catch {
    rec.recorder = new MediaRecorder(rec.stream);
  }

  const recorder = rec.recorder;
  rec.chunks = [];
  rec.cancelled = false;
  recorder.ondataavailable = (e) => {
    if (e.data && e.data.size) rec.chunks.push(e.data);
  };
  recorder.onstop = () => onRecordingStopped(ext, recorder.mimeType || mimeType);
  recorder.onerror = () => {
    showNotice("Recording failed. Please try again.", "error");
    stopRecording(true);
  };
  // Microphone unplugged mid-recording
  rec.stream.getAudioTracks().forEach((track) => (track.onended = () => stopRecording()));

  recorder.start(250);
  rec.startedAt = performance.now();
  setMicState("recording");
  els.subtitle.textContent = "Listening…";
  updateTimer();
  rec.timerId = setInterval(updateTimer, 250);
  rec.autoStopId = setTimeout(() => {
    showNotice("Reached the 60 second limit, so I stopped recording.", "warn");
    stopRecording();
  }, MAX_RECORDING_MS);
  startMeter(rec.stream);
}

function stopRecording(cancel = false) {
  if (!rec.recorder || rec.recorder.state === "inactive") return;
  rec.cancelled = cancel;
  rec.recorder.stop();
}

function cleanupRecording() {
  clearInterval(rec.timerId);
  clearTimeout(rec.autoStopId);
  cancelAnimationFrame(rec.meterFrame);
  if (rec.stream) rec.stream.getTracks().forEach((track) => track.stop()); // turns the mic indicator off
  if (rec.audioContext) rec.audioContext.close().catch(() => {});
  els.ring.style.setProperty("--level", "0");
  rec.recorder = rec.stream = rec.audioContext = null;
}

async function onRecordingStopped(ext, mimeType) {
  const duration = performance.now() - rec.startedAt;
  const chunks = rec.chunks;
  rec.chunks = [];
  cleanupRecording();
  setMicState("idle");

  if (rec.cancelled) {
    els.subtitle.textContent = DEFAULT_SUBTITLE;
    return;
  }
  if (duration < MIN_RECORDING_MS || chunks.length === 0) {
    els.subtitle.textContent = DEFAULT_SUBTITLE;
    showNotice("That was too short — tap once, speak, then tap again to finish.", "warn");
    return;
  }

  const blob = new Blob(chunks, { type: (mimeType || "audio/webm").split(";")[0] });
  const form = new FormData();
  form.append("file", blob, `recording${ext}`);
  form.append("timezone", TIME_ZONE);
  await runParse(() => request("/api/transcribe", { method: "POST", body: form }), "Transcribing…");
}

function updateTimer() {
  const seconds = Math.floor((performance.now() - rec.startedAt) / 1000);
  els.timer.textContent = `${Math.floor(seconds / 60)}:${pad(seconds % 60)}`;
}

function startMeter(stream) {
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtx) return;
  try {
    rec.audioContext = new AudioCtx();
    const analyser = rec.audioContext.createAnalyser();
    analyser.fftSize = 512;
    rec.audioContext.createMediaStreamSource(stream).connect(analyser);
    const samples = new Uint8Array(analyser.fftSize);
    const tick = () => {
      analyser.getByteTimeDomainData(samples);
      let sum = 0;
      for (const s of samples) sum += ((s - 128) / 128) ** 2;
      const level = Math.min(1, Math.sqrt(sum / samples.length) * 5);
      els.ring.style.setProperty("--level", level.toFixed(3));
      rec.meterFrame = requestAnimationFrame(tick);
    };
    tick();
  } catch {
    // the level meter is decoration; recording still works without it
  }
}

function toggleRecording() {
  if (rec.recorder) stopRecording();
  else startRecording();
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

els.recordBtn.addEventListener("click", toggleRecording);
els.cancelBtn.addEventListener("click", () => stopRecording(true));
els.addManual.addEventListener("click", addManualEvent);
els.clearBtn.addEventListener("click", clearSchedule);
els.saveBtn.addEventListener("click", saveSchedule);
els.typeForm.addEventListener("submit", (e) => {
  e.preventDefault();
  parseText(els.textInput.value);
});

document.addEventListener("keydown", (e) => {
  const tag = e.target && e.target.tagName;
  if (["INPUT", "TEXTAREA", "SELECT", "BUTTON", "A"].includes(tag) || e.target.isContentEditable) return;
  if (e.code === "Space" && !e.repeat) {
    e.preventDefault();
    toggleRecording();
  } else if (e.key === "Escape" && rec.recorder) {
    stopRecording(true);
  }
});

window.addEventListener("beforeunload", (e) => {
  if (rec.recorder || saving) e.preventDefault();
});

if (!canRecord()) {
  disableMic(window.isSecureContext
    ? "This browser can't record audio — type your events below instead."
    : "Recording needs https or localhost — type your events below instead.");
}

render();
account.onChange(applyServerState);
applyServerState(account.state);

const params = new URLSearchParams(window.location.search);
if (params.has("text") || params.has("auth_error")) {
  history.replaceState(null, "", window.location.pathname);
}

// Coming back from Google's sign-in page
const authError = params.get("auth_error");
let returningFromSignIn = false;
try {
  returningFromSignIn = sessionStorage.getItem(SIGN_IN_FLAG) === "1";
  sessionStorage.removeItem(SIGN_IN_FLAG);
} catch {
  // storage unavailable: skip the welcome-back message
}
if (authError) {
  showNotice(AUTH_ERRORS[authError] || AUTH_ERRORS.failed, "error");
} else if (returningFromSignIn) {
  account.ready.then((state) => {
    if (state.signedIn && pendingItems().length) {
      showToast(`✔ Signed in as ${state.email || "your Google account"}`);
      els.subtitle.textContent = "You're signed in — press “Add to Google Calendar” to save your events";
    }
  });
}

// Example links on the home page open this page with ?text=...
const prefill = params.get("text");
if (prefill) {
  els.textInput.value = prefill;
  parseText(prefill);
}
