/* Γεννήτρια Διαγωνισμάτων — λογική οθόνης.
 * Επικοινωνεί με την Python μέσω `api.<μέθοδος>(...)` (pywebview ή τοπικός server).
 *
 * Μοντέλο δεδομένων:
 *   S.exam.themes[] = { pointsTotal, itemPoints[], manualPoints, exercises[] }
 *   exercise        = { id, pages[{imageId, dataUrl}], status, data, error, fig }
 *   Μια άσκηση μπορεί να έχει πολλές σελίδες (φωτογραφίες) που διαβάζονται μαζί. */
"use strict";

const LETTERS = ["Α", "Β", "Γ", "Δ", "Ε", "ΣΤ", "Ζ", "Η", "Θ", "Ι"];
const ROMAN = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"];
// Εφεδρικές λίστες όταν δεν γίνεται ανάγνωση των εγκατεστημένων γραμματοσειρών
const FONTS = ["Cambria", "Calibri", "Times New Roman", "Arial", "Georgia", "Book Antiqua", "Palatino Linotype", "Garamond", "Segoe UI", "Tahoma"];
const MATH_FONTS = ["Cambria Math"];
let FONT_INFO = null; // αποτέλεσμα list_fonts()
const SIZES = [10, 10.5, 11, 11.5, 12, 13, 14];
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let uid = 0;
const newId = () => `j${Date.now().toString(36)}${(uid++).toString(36)}`;

/* ------------------------------------------------------------------ γέφυρα προς Python */
let MODE = "http";
const api = new Proxy({}, {
  get: (_, name) => async (...args) => {
    if (MODE === "pywebview") return window.pywebview.api[name](...args);
    const r = await fetch(`/api/${name}`, { method: "POST", body: JSON.stringify(args),
      headers: { "Content-Type": "application/json", "X-Api-Token": new URLSearchParams(location.search).get("k") || "" } });
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || "Σφάλμα");
    return j.result;
  },
});

async function initBridge() {
  if (window.pywebview && window.pywebview.api && Object.keys(window.pywebview.api).length) { MODE = "pywebview"; return; }
  const ready = await Promise.race([
    new Promise((r) => window.addEventListener("pywebviewready", () => r(true), { once: true })),
    sleep(location.protocol.startsWith("http") ? 1200 : 8000).then(() => false),
  ]);
  MODE = ready ? "pywebview" : "http";
  if (MODE === "http") pollEvents();
}

async function pollEvents() {
  for (;;) {
    try {
      const events = await api.poll_events();
      for (const ev of events || []) window.__onPyEvent(ev);
    } catch (_) { /* server έκλεισε */ }
    await sleep(700);
  }
}

window.__onPyEvent = (payload) => {
  if (payload.event === "transcribed") onTranscribed(payload.data);
  if (payload.event === "corpus_transcribed") onCorpusTranscribed(payload.data);
  if (payload.event === "phone_image") onPhoneImage(payload.data);
  if (payload.event === "phone_pdf") onPhonePdf(payload.data);
  if (payload.event === "usage") renderUsage(payload.data);
  if (payload.event === "update_progress") onUpdateProgress(payload.data);
};

/* ------------------------------------------------------------------ κατάσταση */
const S = {
  app: null,                       // get_state()
  exam: { themes: [] },
  target: { t: 0, e: -1 },         // πού πάει η επικόλληση (Ctrl+V): θέμα t, άσκηση e (-1 = νέα άσκηση)
  lastBuild: null,
  update: null,                    // αποτέλεσμα check_updates
};

function newTheme(total) {
  return { pointsTotal: total, exercises: [], itemPoints: [], manualPoints: false };
}

function allExercises() {
  return S.exam.themes.flatMap((t) => t.exercises);
}

function findExercise(id) {
  for (const t of S.exam.themes) for (const ex of t.exercises) if (ex.id === id) return { t, ex };
  return null;
}

/* ------------------------------------------------------------------ βοηθητικά UI */
function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "value") e.value = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return e;
}

let toastTimer = null;
function toast(msg, isErr = false, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

function lightbox(src) {
  const lb = $("#lightbox");
  $("img", lb).src = src;
  lb.hidden = false;
}

function autoGrow(ta) {
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight + 2, 420) + "px";
}

function openModal(id) { $(id).hidden = false; }
function closeModals() { for (const m of $$(".modal")) m.hidden = true; $("#lightbox").hidden = true; }

function switchTab(name) {
  for (const b of $$("#steps button")) b.classList.toggle("active", b.dataset.tab === name);
  for (const s of $$(".tab")) s.classList.toggle("active", s.id === `tab-${name}`);
  window.scrollTo(0, 0);
  if (name === "review") renderReview();
  if (name === "setup") renderThemes();
}

/* ------------------------------------------------------------------ θέμα εμφάνισης */
function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.dataset.theme = theme;
  else delete root.dataset.theme;
  const btn = $("#btn-theme");
  const labels = { light: "☀ Φωτεινό", dark: "☾ Σκοτεινό", system: "◐ Αυτόματο" };
  const [icon, ...rest] = (labels[theme] || labels.system).split(" ");
  btn.replaceChildren(icon, " ", el("span", { class: "lbl", text: rest.join(" ") }));  // σε στενές οθόνες μόνο το εικονίδιο
  btn.title = "Εμφάνιση: " + ({ light: "φωτεινή", dark: "σκοτεινή", system: "όπως τα Windows" }[theme] || "όπως τα Windows") + " — πατήστε για αλλαγή";
}

async function cycleTheme() {
  const order = ["system", "light", "dark"];
  const cur = S.app.settings.theme || "system";
  const next = order[(order.indexOf(cur) + 1) % order.length];
  S.app = await api.save_settings({ theme: next });
  applyTheme(next);
}

/* ------------------------------------------------------------------ Markdown + LaTeX προεπισκόπηση */
function renderMath(tex, display) {
  // η γραμματοσειρά του KaTeX δεν έχει όρθια ελληνικά: \mathrm{ημ} → \textrm{ημ} μόνο για την προεπισκόπηση
  tex = tex.replace(/\\mathrm\{([^{}]*[Ͱ-Ͽἀ-῿][^{}]*)\}/g, "\\textrm{$1}");
  try {
    return katex.renderToString(tex, { displayMode: display, throwOnError: false, strict: false, trust: false });
  } catch (e) {
    return `<span style="color:var(--warn)">${escapeHtml(tex)}</span>`;
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/** Ασφάλεια: HTML που προέρχεται από μεταγραφές, αρχεία Word/PDF ή το Internet δεν πρέπει να τρέξει κώδικα
 *  στην οθόνη. Αφαιρεί επικίνδυνα στοιχεία (script, iframe, …), χειριστές συμβάντων (onerror=…) και
 *  συνδέσμους javascript:. Το <template> δεν εκτελεί/φορτώνει τίποτα όσο γίνεται ο καθαρισμός. */
const SAFE_TAGS = new Set(["P", "BR", "B", "STRONG", "I", "EM", "U", "S", "DEL", "SUB", "SUP", "CODE", "PRE", "SPAN", "DIV",
  "UL", "OL", "LI", "H1", "H2", "H3", "H4", "H5", "H6", "BLOCKQUOTE", "HR", "TABLE", "THEAD", "TBODY", "TR", "TH", "TD",
  "A", "IMG", "KBD", "SMALL", "MARK", "DETAILS", "SUMMARY", "INPUT"]);
function sanitizeHtml(html) {
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  for (const node of [...tpl.content.querySelectorAll("*")]) {
    if (!SAFE_TAGS.has(node.tagName)) { node.replaceWith(document.createTextNode(node.textContent || "")); continue; }
    for (const at of [...node.attributes]) {
      const n = at.name.toLowerCase(), v = at.value.trim().toLowerCase();
      const okUrl = !v || v.startsWith("http://") || v.startsWith("https://") || v.startsWith("#") || v.startsWith("data:image/");
      if (n.startsWith("on") || n === "style" || n === "srcdoc" || ((n === "href" || n === "src" || n === "xlink:href") && !okUrl)) node.removeAttribute(at.name);
    }
    if (node.tagName === "INPUT" && node.type !== "checkbox") node.remove();
    // εξωτερικοί σύνδεσμοι: στον browser, ποτέ μέσα στο παράθυρο της εφαρμογής
    if (node.tagName === "A" && /^https?:/i.test(node.getAttribute("href") || "")) { node.setAttribute("target", "_blank"); node.setAttribute("rel", "noopener noreferrer"); }
  }
  return tpl.innerHTML;
}
const safeMarkdown = (md) => sanitizeHtml(marked.parse(String(md || ""), { breaks: false, gfm: true }));

function mdToHtml(text, refmap = {}) {
  const maths = [];
  let t = String(text || "").replace(/[ ]*(?:\t|⇥)[ ]*/g, " @@TAB@@ ");
  t = t.replace(/\$\$([\s\S]+?)\$\$/g, (_, g) => { maths.push(renderMath(g, true)); return `@@M${maths.length - 1}@@`; });
  t = t.replace(/\$([^$\n]+?)\$/g, (_, g) => { maths.push(renderMath(g, false)); return `@@M${maths.length - 1}@@`; });
  // κείμενο ασκήσεων: κανένα «ωμό» HTML (π.χ. από φωτογραφία/PDF με κρυφές οδηγίες ή από αρχείο Word)
  t = t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  t = t.replace(/\[\[([^\]]+)\]\]/g, (_, g) => escapeHtml(refmap[g.trim()] || `(${g.trim()})`));
  let html = sanitizeHtml(marked.parse(t, { breaks: false, gfm: true }));
  html = html.replace(/@@M(\d+)@@/g, (_, i) => maths[+i]);
  html = html.replace(/ ?@@TAB@@ ?/g, '<span class="coltab" title="στήλη (στηλοθέτης στο Word)"></span>');
  return html;
}

/* ------------------------------------------------------------------ αρίθμηση */
function level1Count(theme) {
  const exs = theme.exercises;
  if (exs.length === 1) return Math.max(1, (exs[0].data?.items || []).length);
  return exs.length;
}

function labelsFor(theme, tIdx) {
  // επιστρέφει συνάρτηση (exIdx, path[]) → ετικέτα όπως θα φανεί στο Word
  const L = LETTERS[tIdx] || String(tIdx + 1);
  const single = theme.exercises.length === 1;
  const style = S.app?.settings?.sublevel_style || "roman";
  return (exIdx, path) => {
    if (single) {
      if (path.length === 1) return `${L}${path[0] + 1}.`;
      if (path.length === 2) return style === "roman" ? `${ROMAN[path[1]] || path[1] + 1})` : "•";
      return "•";
    }
    if (path.length === 0) return `${L}${exIdx + 1}.`;
    if (path.length === 1) return style === "roman" ? `${ROMAN[path[0]] || path[0] + 1})` : "•";
    return "•";
  };
}

function refmapFor(theme, tIdx, ex) {
  const L = LETTERS[tIdx] || String(tIdx + 1);
  const single = theme.exercises.length === 1;
  const style = S.app?.settings?.sublevel_style || "roman";
  const m = {};
  const items = ex.data?.items || [];
  items.forEach((it, i) => {
    if (single) m[it.label] = m[it.label] || `${L}${i + 1}`;
    else m[it.label] = style === "roman" ? `(${ROMAN[i]})` : `(${it.label})`;
    if (single) (it.items || []).forEach((sub, j) => {
      if (!(sub.label in m)) m[sub.label] = style === "roman" ? `(${ROMAN[j]})` : `(${sub.label})`;
    });
  });
  return m;
}

/* ------------------------------------------------------------------ μονάδες */
async function autoThemeTotals() {
  const n = S.exam.themes.length;
  const vals = await api.propose_points(n);
  S.exam.themes.forEach((t, i) => { t.pointsTotal = vals[i]; t.manualPoints = false; });
}

async function syncItemPoints(theme, force = false) {
  const n = level1Count(theme);
  if (force || !theme.manualPoints || theme.itemPoints.length !== n) {
    theme.itemPoints = await api.split_points(theme.pointsTotal, n);
    theme.manualPoints = false;
  }
}

function updateTotals() {
  const sumThemes = S.exam.themes.reduce((a, t) => a + (+t.pointsTotal || 0), 0);
  const ts = $("#total-setup");
  ts.textContent = `Σύνολο μονάδων: ${sumThemes} / 100`;
  ts.className = "total " + (sumThemes === 100 ? "good" : "bad");

  const sumItems = S.exam.themes.reduce((a, t) => a + t.itemPoints.reduce((x, y) => x + (+y || 0), 0), 0);
  const tr = $("#total-review");
  const done = allExercises().filter((e) => e.status === "done").length;
  const all = allExercises().length;
  const pend = allExercises().filter((e) => e.status === "pending").length;
  tr.textContent = `Μονάδες: ${sumItems} / 100 · Έτοιμες ασκήσεις: ${done}/${all}` + (pend ? ` · σε εξέλιξη: ${pend}` : "");
  tr.className = "total " + (sumItems === 100 && done === all ? "good" : "bad");
}

/* ================================================================== ΒΗΜΑ 1: Διαγώνισμα */
function readDetails() {
  S.exam.title = $("#f-title").value.trim();
  S.exam.subtitle = $("#f-subtitle").value.trim();
  S.exam.date = $("#f-date").value.trim();
  S.exam.class_name = $("#f-class").value.trim();
  S.exam.editor = $("#f-editor").value.trim();
}

async function setThemeCount(n) {
  const cur = S.exam.themes;
  while (cur.length < n) cur.push(newTheme(0));
  if (cur.length > n) {
    const dropped = cur.slice(n).some((t) => t.exercises.length);
    if (dropped && !confirm("Τα θέματα που αφαιρούνται έχουν εικόνες. Να αφαιρεθούν;")) {
      $("#f-nthemes").value = String(cur.length);
      return;
    }
    cur.length = n;
  }
  if (S.target.t >= n) S.target = { t: n - 1, e: -1 };
  await autoThemeTotals();
  for (const t of cur) await syncItemPoints(t, true);
  renderThemes();
}

function isTarget(t, e) { return S.target.t === t && S.target.e === e; }

function setTarget(t, e) {
  if (!isTarget(t, e)) { S.target = { t, e }; renderThemes(); }
}

/* ================================================================== ΚΙΝΗΤΟ (QR) */
S.phone = { active: false, count: 0, url: "" };

function phoneTargetLabel() {
  if (S.mode === "corpus") {
    const sec = corpusSection();
    return sec ? `Corpus · ${sec.display}` : "Corpus · (επιλέξτε ενότητα στην εφαρμογή)";
  }
  const L = LETTERS[S.target.t] || "";
  return `Διαγώνισμα · ΘΕΜΑ ${L}`;
}

function phoneSync() {
  const lab = phoneTargetLabel();
  if ($("#phone-target")) $("#phone-target").textContent = lab;
  for (const id of ["#c-phone", "#e-phone"]) {
    const b = $(id);
    if (!b) continue;
    b.classList.toggle("on", S.phone.active);
    b.textContent = S.phone.active ? `📱 Κινητό: σύνδεση ανοιχτή · ${S.phone.count} φωτογραφίες` : (id === "#c-phone" ? "📱 Από το κινητό (QR)" : "📱 Φωτογραφίες από το κινητό (QR)");
  }
  if (S.phone.active) api.phone_set_target(lab).catch(() => {});
}

function showQr(res) {
  $("#phone-qr").innerHTML = res.qr;
  $("#phone-url").textContent = res.url;
  S.phone.url = res.url;
}

async function phoneOpen() {
  if (!S.phone.active) {
    let res;
    try { res = await api.phone_start(); } catch (e) { res = { ok: false, error: String(e.message || e) }; }
    if (!res.ok) { toast(res.error, true, 9000); return; }
    S.phone.active = true;
    S.phone.count = res.count || 0;
    showQr(res);
    const sel = $("#phone-urls");
    sel.innerHTML = "";
    const choices = res.choices || (res.urls || []).map((u) => ({ url: u, label: u }));
    sel.hidden = false;
    $("#phone-urls-wrap").hidden = choices.length < 2;
    for (const c of choices) sel.append(el("option", { value: c.url, text: `Δίκτυο: ${c.label}` }));
    sel.onchange = async () => showQr(await api.phone_qr(sel.value));
  }
  $("#phone-count").textContent = S.phone.count;
  phoneSync();
  openModal("#phone");
}

async function phoneStop() {
  await api.phone_stop();
  S.phone.active = false;
  $("#phone").hidden = true;
  phoneSync();
  toast("Η σύνδεση με το κινητό έκλεισε. Το παλιό QR δεν ισχύει πια.");
}

async function onPhoneImage({ image_id, data_url, name, join }) {
  S.phone.count++;
  $("#phone-count").textContent = S.phone.count;
  if (S.mode === "corpus") {
    const C = S.corpus;
    const dup = C.images.find((im) => im.dataUrl === data_url);
    if (dup) { toast(`Η φωτογραφία «${name}» είναι ίδια με «${dup.name}» και παραλείφθηκε.`); phoneSync(); return; }
    const last = C.images[C.images.length - 1];
    if (join && last) corpusMarkChanged(last);
    C.images.push({ id: newId(), imageId: image_id, dataUrl: data_url, name, time: Date.now(),
      join: !!(join && last), status: "new", error: null });
    renderCorpusImages();
    toast(join && last ? `📱 «${name}»: συνέχεια της προηγούμενης φωτογραφίας` : `📱 Νέα φωτογραφία: «${name}»`);
  } else {
    const t = S.target.t;
    const th = S.exam.themes[t];
    if (!th) return;
    let e = -1;
    if (join && th.exercises.length) e = S.target.e >= 0 && th.exercises[S.target.e] ? S.target.e : th.exercises.length - 1;
    await addPageId(t, e, image_id, data_url);
    toast(e >= 0 ? `📱 Συνέχεια άσκησης στο ΘΕΜΑ ${LETTERS[t]}` : `📱 Νέα άσκηση στο ΘΕΜΑ ${LETTERS[t]}`);
  }
  phoneSync();
}

/* ================================================================== ΧΡΗΣΗ ΣΥΝΔΡΟΜΗΣ CLAUDE (1.7.2) */
const USAGE_WIN = {
  five_hour: { short: "5ωρο", long: "Τρέχον 5ωρο παράθυρο" },
  seven_day: { short: "Εβδ.", long: "Εβδομάδα — όλα τα μοντέλα" },
  seven_day_opus: { short: "Opus", long: "Εβδομάδα — μόνο Opus" },
  seven_day_sonnet: { short: "Sonnet", long: "Εβδομάδα — μόνο Sonnet" },
};
const USAGE_ORDER = ["five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet"];
S.usage = null;
let usageBusy = false;

const nf0 = new Intl.NumberFormat("el-GR", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("el-GR", { maximumFractionDigits: 1 });
const usd = new Intl.NumberFormat("el-GR", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Ποσοστό χρήσης ενός παραθύρου τώρα· αν πέρασε η ώρα μηδενισμού, θεωρείται 0. */
function usageNow(w, nowSec) {
  const reset = w.resets_at && w.resets_at <= nowSec;
  // πάνω από 100% (π.χ. 1,02) σημαίνει ότι το όριο έχει ήδη εξαντληθεί: εμφανίζεται 100%
  return { pct: reset ? 0 : Math.min(100, Math.round((w.utilization || 0) * 100)), reset };
}
function usageLevel(pct) { return pct >= 90 ? "high" : pct >= 70 ? "mid" : ""; }
function usageBar(pct) {
  return el("span", { class: "ubar " + usageLevel(pct) }, el("i", { style: `width:${Math.max(0, Math.min(100, pct))}%` }));
}

function fmtDuration(sec) {
  sec = Math.max(0, Math.round(sec));
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  if (d) return `${d} ημ. ${h} ώρ.`;
  if (h) return `${h} ώρ. ${m}′`;
  if (m) return `${m}′`;
  return `${sec}″`;
}
function fmtWhen(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const hm = d.toLocaleTimeString("el-GR", { hour: "2-digit", minute: "2-digit", hour12: false });
  const day0 = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diff = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()) - day0) / 86400000);
  if (diff === 0) return `σήμερα ${hm}`;
  if (diff === 1) return `αύριο ${hm}`;
  return `${d.toLocaleDateString("el-GR", { weekday: "short", day: "numeric", month: "numeric" })} ${hm}`;
}
function fmtAgo(sec) { return sec < 60 ? "μόλις τώρα" : `πριν ${fmtDuration(sec)}`; }

function usageWindows(lim) {
  const ws = (lim && lim.windows) || {};
  const names = Object.keys(ws).sort((a, b) => (USAGE_ORDER.indexOf(a) + 1 || 99) - (USAGE_ORDER.indexOf(b) + 1 || 99));
  return names.map((n) => ({ name: n, ...ws[n], label: USAGE_WIN[n] || { short: n, long: n } }));
}

function renderUsageChip(st) {
  const chip = $("#usage-chip");
  if (!chip) return;
  const nowSec = Date.now() / 1000;
  chip.innerHTML = "";
  chip.className = "usage-chip";
  if (st.provider === "api") {
    const t = st.today || {};
    chip.append(el("span", { text: `API · σήμερα ${t.images || 0} ${t.images === 1 ? "εικόνα" : "εικόνες"}` }));
    chip.title = "Σύνδεση με κλειδί API: χρέωση ανά χρήση (Claude Console). Πατήστε για λεπτομέρειες.";
    chip.hidden = false;
    return;
  }
  chip.append(el("span", { class: "uc-brand", text: "Claude" }));
  const wins = usageWindows(st.limits).filter((w) => w.name === "five_hour" || w.name === "seven_day");
  if (!wins.length) {
    chip.append(el("span", { class: "uc-spin", text: st.refreshing || usageBusy ? "ενημέρωση…" : "χρήση: —" }));
  }
  for (const w of wins) {
    const { pct } = usageNow(w, nowSec);
    chip.append(el("span", { class: "uc-w" }, el("span", { class: "uc-k", text: w.label.short }), usageBar(pct),
      el("span", { class: usageLevel(pct) ? `pct-${usageLevel(pct)}` : "", text: `${pct}%` })));
  }
  const blocked = st.limits && st.limits.status === "rejected" && wins.some((w) => !usageNow(w, nowSec).reset && w.utilization >= 1);
  if (blocked) chip.classList.add("blocked");
  if (st.updated_at && nowSec - st.updated_at > 3600) chip.classList.add("stale");
  const parts = wins.map((w) => { const u = usageNow(w, nowSec); return `${w.label.long}: ${u.pct}%` + (w.resets_at ? ` (μηδενίζεται ${fmtWhen(w.resets_at)})` : ""); });
  chip.title = (parts.join("\n") || "Χρήση συνδρομής Claude") + (st.updated_at ? `\nΕνημέρωση: ${fmtAgo(nowSec - st.updated_at)}` : "") + "\nΠατήστε για λεπτομέρειες";
  chip.hidden = false;
}

function renderUsageModal(st) {
  const nowSec = Date.now() / 1000;
  const lim = st.limits;
  const wins = usageWindows(lim);
  const box = $("#usage-windows");
  box.innerHTML = "";
  const status = $("#usage-status");
  status.className = "usage-status";
  if (st.provider === "api") {
    status.classList.add("none");
    status.textContent = "Σύνδεση με κλειδί API: δεν υπάρχουν όρια συνδρομής. Η χρέωση γίνεται ανά χρήση — δείτε Claude Console → Usage / Billing.";
  } else if (!lim || !wins.length) {
    status.classList.add("none");
    status.textContent = usageBusy || st.refreshing ? "Ενημέρωση…" : "Δεν υπάρχουν ακόμη στοιχεία. Πατήστε «↻ Ανανέωση τώρα» ή κάντε μια ανάγνωση.";
  } else {
    const live = wins.filter((w) => !usageNow(w, nowSec).reset);
    const full = live.filter((w) => w.utilization >= 1);
    if (lim.status === "rejected" && full.length) {
      status.classList.add("bad");
      const until = Math.max(...full.map((w) => w.resets_at || 0));
      status.textContent = `⛔ Εξαντλήθηκε το όριο (${full.map((w) => w.label.long).join(", ")}). Ξαναδουλεύει: ${until ? fmtWhen(until) : "αργότερα"}`;
    } else if (lim.status === "allowed_warning" || live.some((w) => w.utilization >= 0.9)) {
      status.classList.add("warn");
      const which = (USAGE_WIN[lim.limit_type] || {}).long;
      status.textContent = `⚠ Πλησιάζετε το όριο${which ? ` (${which})` : ""}. Οι αναγνώσεις σταματούν όταν φτάσει στο 100% και ξαναρχίζουν με τον μηδενισμό.`;
    } else {
      status.textContent = "✓ Υπάρχει διαθέσιμο υπόλοιπο.";
    }
  }
  for (const w of wins) {
    const u = usageNow(w, nowSec);
    const left = Math.max(0, 100 - u.pct);
    const resetTxt = !w.resets_at ? "" : u.reset ? "μηδενίστηκε (θα ενημερωθεί με την επόμενη ανάγνωση)"
      : `μηδενίζεται ${fmtWhen(w.resets_at)} (σε ${fmtDuration(w.resets_at - nowSec)})`;
    box.append(el("div", { class: "uwin" },
      el("div", { class: "uwin-top" }, el("b", { text: w.label.long }), el("span", { class: "uwin-pct", text: `${u.pct}%` })),
      usageBar(u.pct),
      el("div", { class: "uwin-sub" }, el("span", { text: `χρησιμοποιήθηκε ${u.pct}% · απομένει ${left}%` }), el("span", { text: resetTxt }))));
  }
  if (lim && st.provider !== "api") {
    let ov = "";
    if (lim.using_overage) ov = "⚠ Αυτή τη στιγμή χρησιμοποιείται «επιπλέον χρήση» πέρα από τη συνδρομή (χρεώνεται χωριστά).";
    else if (lim.overage_status === "allowed" || lim.overage_status === "allowed_warning") ov = "Επιπλέον χρήση πέρα από τη συνδρομή: ενεργή — όταν τελειώσει το όριο, η χρήση συνεχίζει με επιπλέον χρέωση.";
    else if (lim.overage_status) ov = "Επιπλέον χρήση πέρα από τη συνδρομή: όχι ενεργή — όταν τελειώσει το όριο, οι αναγνώσεις περιμένουν τον μηδενισμό (καμία επιπλέον χρέωση).";
    if (ov) box.append(el("div", { class: "small muted", text: ov }));
  }
  // εκτίμηση ανά εικόνα
  const est = $("#usage-estimate");
  est.innerHTML = "";
  const w5 = wins.find((w) => w.name === "five_hour");
  if (st.provider !== "api" && st.per_image && st.per_image > 0) {
    const per = st.per_image * 100;
    let txt = `Εκτίμηση: κάθε εικόνα καταναλώνει περίπου ${nf1.format(per)}% του 5ώρου (από ${st.per_image_samples} εικόνες)`;
    if (w5) {
      const u = usageNow(w5, nowSec);
      const n = Math.floor((100 - u.pct) / per);
      txt += `· με το τωρινό υπόλοιπο χωρούν περίπου ${nf0.format(Math.max(0, n))} ακόμη εικόνες σε αυτό το 5ωρο`;
    }
    est.append(el("div", { text: txt + "." }),
      el("div", { class: "small muted", text: "Η εκτίμηση αλλάζει με το μοντέλο και το μέγεθος των ασκήσεων, και βγαίνει μεγαλύτερη αν γίνεται ταυτόχρονα χρήση του Claude και αλλού." }));
  } else if (st.provider !== "api") {
    est.append(el("div", { class: "small muted", text: "Η εκτίμηση «πόσες εικόνες χωρούν ακόμη» εμφανίζεται μετά από μερικές αναγνώσεις (τουλάχιστον 3 εικόνες)." }));
  }
  // πίνακας κατανάλωσης
  const cols = [["Αυτή η συνεδρία", st.session || {}], ["Σήμερα", st.today || {}], ["7 ημέρες", st.week || {}]];
  const rows = [
    ["Αναγνώσεις (κλήσεις)", (t) => nf0.format(t.calls || 0)],
    ["Εικόνες", (t) => nf0.format(t.images || 0)],
    ["Tokens εισόδου", (t) => nf0.format((t.input_tokens || 0) + (t.cache_read || 0) + (t.cache_write || 0))],
    ["Tokens εξόδου", (t) => nf0.format(t.output_tokens || 0)],
    ["Χρόνος ανάγνωσης", (t) => (t.seconds ? fmtDuration(t.seconds) : "—")],
  ];
  if (st.provider !== "api") rows.push(["Αντίστοιχο κόστος API*", (t) => usd.format(t.cost_usd || 0)]);
  if (cols.some(([, t]) => t.errors)) rows.push(["Αποτυχίες", (t) => nf0.format(t.errors || 0)]);
  const tbl = $("#usage-table");
  tbl.innerHTML = "";
  tbl.append(el("thead", {}, el("tr", {}, el("th", { text: "" }), ...cols.map(([h]) => el("th", { text: h })))),
    el("tbody", {}, ...rows.map(([label, f]) => el("tr", {}, el("td", { text: label }), ...cols.map(([, t]) => el("td", { text: f(t) }))))));
  const foot = [];
  if (st.model) foot.push(`Μοντέλο τελευταίας ανάγνωσης: ${st.model}`);
  if (st.updated_at && st.provider !== "api") {
    const d = new Date(st.updated_at * 1000).toLocaleTimeString("el-GR", { hour: "2-digit", minute: "2-digit", hour12: false });
    foot.push(`Όρια ενημερώθηκαν: ${d} (${fmtAgo(nowSec - st.updated_at)})`);
  }
  if (st.provider !== "api") foot.push("*Δεν χρεώνεται με τη συνδρομή — δείχνει τι θα κόστιζε με κλειδί API.");
  $("#usage-foot").textContent = foot.join(" · ");
  $("#usage-auto").checked = !!st.auto;
  $("#usage-auto").disabled = st.provider === "api";
  $("#usage-refresh").disabled = st.provider === "api" || usageBusy || !!st.refreshing;
  $("#usage-refresh").textContent = usageBusy || st.refreshing ? "Ενημέρωση…" : "↻ Ανανέωση τώρα";
}

function renderUsage(st) {
  if (!st) return;
  S.usage = st;
  renderUsageChip(st);
  if (!$("#usage").hidden) renderUsageModal(st);
}

async function usageRefresh(manual) {
  if (usageBusy) return;
  usageBusy = true;
  if (S.usage) renderUsage(S.usage);
  let res;
  try { res = await api.usage_refresh(); } catch (e) { res = { ok: false, error: String(e.message || e) }; }
  usageBusy = false;
  if (res.state) renderUsage(res.state); else if (S.usage) renderUsage(S.usage);
  if (!res.ok && manual) toast(res.error || "Δεν ήρθαν στοιχεία χρήσης.", true, 6000);
}

async function usageInit() {
  $("#usage-chip").onclick = () => { if (S.usage) renderUsageModal(S.usage); openModal("#usage");
    if (S.usage && S.usage.provider !== "api" && (!S.usage.updated_at || Date.now() / 1000 - S.usage.updated_at > 1800)) usageRefresh(false); };
  $("#usage-refresh").onclick = () => usageRefresh(true);
  $("#usage-auto").onchange = async (e) => { S.app = await api.save_settings({ usage_check: e.target.checked }); if (S.usage) S.usage.auto = e.target.checked; };
  let st;
  try { st = await api.usage_state(); } catch (_) { return; }
  renderUsage(st);
  setInterval(() => { if (S.usage) renderUsage(S.usage); }, 60000);  // σχετικοί χρόνοι, μηδενισμοί
  const stale = !st.updated_at || Date.now() / 1000 - st.updated_at > 600;
  if (st.provider === "agent_sdk" && st.auto && stale) setTimeout(() => usageRefresh(false), 2500);
}

/* ================================================================== PDF: επιλογή σελίδων */
const PDF_MAX = 60 * 1024 * 1024;
const PDF = { queue: [], cur: null, sel: new Set() };
const isPdfFile = (f) => !!f && (f.type === "application/pdf" || /\.pdf$/i.test(f.name || ""));

/** PDF από τον υπολογιστή (επιλογή αρχείου ή σύρσιμο). t = θέμα του διαγωνίσματος (αν ισχύει). */
async function addPdfFile(file, t = null) {
  if (file.size > PDF_MAX) { toast(`«${file.name}»: το PDF είναι πολύ μεγάλο (έως 60 MB).`, true); return; }
  toast(`Άνοιγμα του «${file.name}»…`, false, 8000);
  try {
    const res = await api.add_pdf(await fileToDataUrl(file), file.name);
    if (!res.ok) { toast(`${file.name}: ${res.error}`, true, 6000); return; }
    toast(`«${file.name}»: διαλέξτε σελίδες`);
    pdfEnqueue(res, t);
  } catch (e) { toast(String(e.message || e), true); }
}

function onPhonePdf(data) {
  S.phone.count++;
  if ($("#phone-count")) $("#phone-count").textContent = S.phone.count;
  toast(`📱 PDF «${data.name}» (${data.total} σελ.): διαλέξτε ποιες σελίδες θα μπουν`, false, 6000);
  pdfEnqueue(data, null);
  phoneSync();
}

function pdfEnqueue(doc, t) {
  PDF.queue.push({ ...doc, t });
  if (!PDF.cur || $("#pdfpick").hidden) pdfNext();  // (Esc κλείνει το παράθυρο = ακύρωση)
}

function pdfNext() {
  PDF.cur = PDF.queue.shift() || null;
  if (!PDF.cur) { $("#pdfpick").hidden = true; return; }
  const d = PDF.cur;
  const n = d.pages.length;
  PDF.sel = new Set(n <= 10 ? d.pages.map((p) => p.n) : []);
  $("#pdfpick-title").textContent = `📄 «${d.name}» — ${d.total} ${d.total === 1 ? "σελίδα" : "σελίδες"}`;
  const corpus = S.mode === "corpus";
  const dest = corpus ? phoneTargetLabel() : `Διαγώνισμα · ΘΕΜΑ ${LETTERS[d.t ?? S.target.t] || ""}`;
  $("#pdfpick-dest").innerHTML = "";
  $("#pdfpick-dest").append("Προορισμός: ", el("b", { text: dest }),
    d.truncated ? el("span", { class: "warn-text", text: ` · Εμφανίζονται οι πρώτες ${n} σελίδες (όριο). Για τις υπόλοιπες, χωρίστε το PDF.` }) : "");
  $("#pdfpick-range").value = "";
  $("#pdfpick-join").checked = false;
  $("#pdfpick-join-text").textContent = corpus
    ? "Οι σελίδες είναι συνέχεια η μία της άλλης (ασκήσεις που περνούν στην επόμενη σελίδα)"
    : "Όλες οι επιλεγμένες σελίδες είναι μία άσκηση (η ίδια άσκηση συνεχίζει)";
  $("#pdfpick-note").textContent = corpus
    ? "Κάθε σελίδα διαβάζεται χωριστά και μπορεί να έχει πολλές ασκήσεις. Η «Συνέχεια» ορίζεται και αργότερα σε κάθε σελίδα, με το κουμπί «⤒ Συνέχεια»."
    : "Στο διαγώνισμα κάθε σελίδα γίνεται μία άσκηση. Αν μια σελίδα έχει πολλές ασκήσεις, προτιμήστε screenshot μόνο της άσκησης (ή το Corpus, που τις διαβάζει όλες).";
  pdfRender();
  openModal("#pdfpick");
}

function pdfRender() {
  const d = PDF.cur;
  if (!d) return;
  const grid = $("#pdfpick-grid");
  grid.innerHTML = "";
  for (const p of d.pages) {
    const on = PDF.sel.has(p.n);
    const card = el("div", { class: "pdfpage" + (on ? " on" : ""), role: "button", tabindex: 0, title: on ? "Επιλεγμένη — πατήστε για αφαίρεση" : "Πατήστε για επιλογή",
      onclick: () => { if (PDF.sel.has(p.n)) PDF.sel.delete(p.n); else PDF.sel.add(p.n); $("#pdfpick-range").value = ""; pdfRender(); } },
      el("img", { src: p.thumb, alt: `σελίδα ${p.n}` }),
      el("button", { class: "zoom", title: "Μεγέθυνση", text: "🔍", onclick: async (ev) => {
        ev.stopPropagation();
        const r = await api.pdf_preview(d.pdf_id, p.n);
        if (r.ok) lightbox(r.data_url); else toast(r.error, true);
      } }),
      el("div", { class: "pn" }, el("span", { text: `σελ. ${p.n}` }), el("span", { text: on ? "✓" : "" })));
    grid.append(card);
  }
  const k = PDF.sel.size;
  $("#pdfpick-info").textContent = `${k} από ${d.pages.length} επιλεγμένες`;
  $("#pdfpick-ok").disabled = k === 0;
  $("#pdfpick-ok").textContent = k === 1 ? "Προσθήκη 1 σελίδας" : `Προσθήκη ${k} σελίδων`;
}

/** «1-3, 5, 8-» → σύνολο αριθμών σελίδων (μέσα στο 1..max). */
function parsePageRange(text, max) {
  const out = new Set();
  for (const part of String(text || "").split(/[,;\s]+/)) {
    const m = part.match(/^(\d*)\s*[-–]\s*(\d*)$/) || part.match(/^(\d+)$/);
    if (!m) continue;
    let a = parseInt(m[1], 10), b = m[2] === undefined ? a : parseInt(m[2], 10);
    if (isNaN(a)) a = 1;
    if (isNaN(b)) b = max;
    if (a > b) [a, b] = [b, a];
    for (let i = Math.max(1, a); i <= Math.min(max, b); i++) out.add(i);
  }
  return out;
}

async function pdfConfirm() {
  const d = PDF.cur;
  if (!d || !PDF.sel.size) return;
  const pages = [...PDF.sel].sort((a, b) => a - b);
  const join = $("#pdfpick-join").checked;
  $("#pdfpick-ok").disabled = true;
  let res;
  try { res = await api.pdf_take(d.pdf_id, pages); } catch (e) { res = { ok: false, error: String(e.message || e) }; }
  if (!res.ok) { toast(res.error, true); $("#pdfpick-ok").disabled = false; return; }
  if (S.mode === "corpus") {
    const C = S.corpus;
    const skipped = [];
    let added = 0;
    res.pages.forEach((p, i) => {
      const dup = C.images.find((im) => im.dataUrl === p.data_url);
      if (dup) { skipped.push(`σελ. ${p.n}`); return; }
      C.images.push({ id: newId(), imageId: p.image_id, dataUrl: p.data_url, name: p.name, time: Date.now() + i,
        join: join && added > 0, status: "new", error: null });
      added++;
    });
    renderCorpusImages();
    toast(skipped.length ? `Προστέθηκαν ${added} σελίδες· παραλείφθηκαν όσες υπήρχαν ήδη (${skipped.join(", ")}).`
      : `Προστέθηκαν ${added} σελίδες από το «${d.name}».`, false, 5000);
  } else {
    const t = d.t ?? S.target.t;
    if (!S.exam.themes[t]) { toast("Δεν βρέθηκε το θέμα του διαγωνίσματος.", true); return; }
    for (let i = 0; i < res.pages.length; i++) {
      const p = res.pages[i];
      const e = join && i > 0 ? S.exam.themes[t].exercises.length - 1 : -1;
      await addPageId(t, e, p.image_id, p.data_url);
    }
    toast(join ? `Μία άσκηση με ${res.pages.length} σελίδες στο ΘΕΜΑ ${LETTERS[t]}`
      : `${res.pages.length} ${res.pages.length === 1 ? "νέα άσκηση" : "νέες ασκήσεις"} στο ΘΕΜΑ ${LETTERS[t]}`, false, 5000);
  }
  pdfNext();
}

function pdfCancel() {
  pdfNext();
}

function initPdfPick() {
  $("#pdfpick-all").onclick = () => { PDF.sel = new Set(PDF.cur.pages.map((p) => p.n)); $("#pdfpick-range").value = ""; pdfRender(); };
  $("#pdfpick-none").onclick = () => { PDF.sel = new Set(); $("#pdfpick-range").value = ""; pdfRender(); };
  $("#pdfpick-range").oninput = (e) => {
    if (!PDF.cur) return;
    if (!e.target.value.trim()) return;
    PDF.sel = parsePageRange(e.target.value, PDF.cur.pages.length);
    const v = e.target.value; pdfRender(); $("#pdfpick-range").value = v;
    const first = $("#pdfpick-grid .pdfpage.on");
    if (first) first.scrollIntoView({ block: "nearest" });
  };
  $("#pdfpick-ok").onclick = pdfConfirm;
  $("#pdfpick-cancel").onclick = pdfCancel;
  $("#pdfpick-x").onclick = pdfCancel;
  // κλικ έξω από το παράθυρο ή Esc = ακύρωση αυτού του PDF (και εμφάνιση του επόμενου, αν περιμένει)
  $("#pdfpick").addEventListener("click", (e) => { if (e.target === $("#pdfpick")) setTimeout(pdfCancel, 0); });
  window.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !PDF.cur || $("#pdfpick").hidden) return;
    if (!$("#lightbox").hidden) {  // πρώτα κλείνει μόνο η μεγέθυνση
      $("#lightbox").hidden = true; e.stopImmediatePropagation(); e.preventDefault(); return;
    }
    setTimeout(pdfCancel, 0);
  }, true);
}

/** Ζώνη εικόνων: σύρσιμο, κλικ για επιλογή αρχείου, και στόχος για Ctrl+V. */
function dropZone(tIdx, eIdx, label, hint) {
  const input = el("input", { type: "file", accept: "image/*,application/pdf,.pdf", multiple: true, hidden: true,
    onchange: async (ev) => { await addFiles(tIdx, eIdx, ev.target.files); ev.target.value = ""; } });
  const active = isTarget(tIdx, eIdx);
  const drop = el("div", { class: "drop" + (active ? " active" : ""), tabindex: 0,
    onclick: (ev) => { ev.stopPropagation(); setTarget(tIdx, eIdx); input.click(); } },
    el("div", { class: "drop-title", text: label }),
    el("div", { class: "small", text: active ? "Ctrl+V: επικόλληση screenshot εδώ" : hint }));
  drop.addEventListener("dragover", (ev) => { ev.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", async (ev) => {
    ev.preventDefault(); drop.classList.remove("over");
    await addFiles(tIdx, eIdx, ev.dataTransfer.files);
  });
  return el("div", {}, drop, input);
}

function pageThumb(tIdx, eIdx, pIdx, page, nPages) {
  return el("div", { class: "thumb" },
    el("div", {},
      nPages > 1 ? el("div", { class: "page-lab", text: `Σελίδα ${pIdx + 1}` }) : null,
      el("img", { src: page.dataUrl, alt: "", onclick: (e) => { e.stopPropagation(); lightbox(page.dataUrl); } })),
    el("div", { class: "tools" },
      el("button", { title: "Προηγούμενη σελίδα", text: "▲", onclick: (e) => { e.stopPropagation(); movePage(tIdx, eIdx, pIdx, -1); } }),
      el("button", { title: "Επόμενη σελίδα", text: "▼", onclick: (e) => { e.stopPropagation(); movePage(tIdx, eIdx, pIdx, 1); } }),
      el("button", { title: "Αφαίρεση σελίδας", text: "✕", onclick: (e) => { e.stopPropagation(); removePage(tIdx, eIdx, pIdx); } })));
}

function renderThemes() {
  if (S.phone && S.phone.active) phoneSync();
  const box = $("#themes");
  box.innerHTML = "";
  S.exam.themes.forEach((t, i) => {
    const L = LETTERS[i] || i + 1;
    const pts = el("input", { type: "number", min: 0, max: 100, value: t.pointsTotal,
      onclick: (e) => e.stopPropagation(),
      oninput: async (e) => { t.pointsTotal = +e.target.value || 0; await syncItemPoints(t, true); updateTotals(); } });
    const card = el("div", { class: "theme" + (S.target.t === i ? " target" : "") },
      el("div", { class: "theme-head" },
        el("h3", { text: `ΘΕΜΑ ${L}` }),
        el("label", {}, "Μονάδες", pts)));

    t.exercises.forEach((ex, k) => {
      const badge = { new: ["", "νέα"], pending: ["run", "διαβάζεται…"], done: ["ok", "έτοιμη"], error: ["err", "σφάλμα"] }[ex.status];
      const exBox = el("div", { class: "exblock" + (isTarget(i, k) ? " target" : "") },
        el("div", { class: "exblock-head" },
          el("span", { class: "exlab", text: t.exercises.length > 1 ? `Άσκηση ${L}${k + 1}` : "Άσκηση" }),
          el("span", { class: `badge ${badge[0]}`, text: badge[1] }),
          ex.pages.length > 1 ? el("span", { class: "small muted", text: `${ex.pages.length} σελίδες` }) : null,
          el("div", { class: "spacer" }),
          t.exercises.length > 1 ? el("button", { class: "icon-mini", title: "Άσκηση πιο πάνω", text: "▲", onclick: () => moveEx(i, k, -1) }) : null,
          t.exercises.length > 1 ? el("button", { class: "icon-mini", title: "Άσκηση πιο κάτω", text: "▼", onclick: () => moveEx(i, k, 1) }) : null,
          el("button", { class: "icon-mini danger", title: "Αφαίρεση άσκησης", text: "✕", onclick: () => removeEx(i, k) })),
        el("div", { class: "thumbs" }, ...ex.pages.map((p, pIdx) => pageThumb(i, k, pIdx, p, ex.pages.length))),
        dropZone(i, k, "+ Συνέχεια (επόμενη σελίδα)", "Αν η άσκηση συνεχίζει σε επόμενη σελίδα"));
      card.append(exBox);
    });

    if (!t.exercises.length) {
      card.append(dropZone(i, -1, "Φωτογραφία άσκησης", "Σύρετε εικόνα εδώ ή πατήστε για επιλογή"));
    } else {
      card.append(el("div", { class: "add-ex" },
        isTarget(i, -1)
          ? dropZone(i, -1, "+ Νέα άσκηση στο ίδιο θέμα", "Γίνεται ξεχωριστό ερώτημα (π.χ. Γ2)")
          : el("button", { class: "btn small ghost", text: "+ Νέα άσκηση στο ίδιο θέμα",
            title: "Προσθέτει δεύτερη άσκηση στο θέμα· γίνονται Γ1, Γ2…", onclick: () => setTarget(i, -1) })));
    }
    box.append(card);
  });
  updateTotals();
}

async function addFiles(tIdx, eIdx, files) {
  const all = Array.from(files || []);
  const pdfs = all.filter(isPdfFile);
  const list = all.filter((f) => f.type.startsWith("image/"));
  if (!list.length && !pdfs.length) { toast("Επιλέξτε αρχεία εικόνας (PNG, JPG) ή PDF.", true); return; }
  for (const f of pdfs) await addPdfFile(f, tIdx);
  for (const f of list) {
    await addPage(tIdx, eIdx, await fileToDataUrl(f));
    // πολλά αρχεία σε «νέα άσκηση»: το πρώτο φτιάχνει την άσκηση, τα υπόλοιπα γίνονται συνέχειά της
    if (eIdx === -1) eIdx = S.exam.themes[tIdx].exercises.length - 1;
  }
}

function fileToDataUrl(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result);
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}

/** Προσθήκη σελίδας: eIdx = -1 → νέα άσκηση, αλλιώς συνέχεια της άσκησης eIdx. */
async function addPage(tIdx, eIdx, dataUrl) {
  try {
    const res = await api.add_image(dataUrl);
    if (!res.ok) { toast(res.error, true); return; }
    await addPageId(tIdx, eIdx, res.image_id, dataUrl);
  } catch (e) { toast(String(e.message || e), true); }
}

/** Όπως το addPage, για εικόνα που έχει ήδη καταχωριστεί (π.χ. ήρθε από το κινητό). */
async function addPageId(tIdx, eIdx, imageId, dataUrl) {
  const t = S.exam.themes[tIdx];
  if (!t) return;
  try {
    const page = { imageId, dataUrl };
    if (eIdx < 0 || !t.exercises[eIdx]) {
      t.exercises.push({ id: newId(), pages: [page], status: "new", data: null, error: null, fig: null });
      S.target = { t: tIdx, e: t.exercises.length - 1 };
    } else {
      const ex = t.exercises[eIdx];
      ex.pages.push(page);
      markChanged(ex);
    }
    await syncItemPoints(t);
    renderThemes();
  } catch (e) { toast(String(e.message || e), true); }
}

/** Αλλαγή σελίδων σε άσκηση που είχε ήδη μεταγραφεί → χρειάζεται νέα μεταγραφή. */
function markChanged(ex) {
  if (ex.status === "done" || ex.status === "error") {
    ex.status = "new";
    ex.data = null;
    ex.fig = null;
  }
}

function movePage(t, e, p, d) {
  const ex = S.exam.themes[t].exercises[e];
  const j = p + d;
  if (j < 0 || j >= ex.pages.length) return;
  [ex.pages[p], ex.pages[j]] = [ex.pages[j], ex.pages[p]];
  markChanged(ex);
  renderThemes();
}

async function removePage(t, e, p) {
  const th = S.exam.themes[t];
  const ex = th.exercises[e];
  ex.pages.splice(p, 1);
  if (!ex.pages.length) th.exercises.splice(e, 1);
  else markChanged(ex);
  S.target = { t, e: -1 };
  await syncItemPoints(th);
  renderThemes();
}

function moveEx(i, k, d) {
  const exs = S.exam.themes[i].exercises;
  const j = k + d;
  if (j < 0 || j >= exs.length) return;
  [exs[k], exs[j]] = [exs[j], exs[k]];
  renderThemes();
}

async function removeEx(i, k) {
  const t = S.exam.themes[i];
  if (t.exercises[k].status === "done" && !confirm("Να αφαιρεθεί η άσκηση και η μεταγραφή της;")) return;
  t.exercises.splice(k, 1);
  S.target = { t: i, e: -1 };
  await syncItemPoints(t);
  renderThemes();
}

document.addEventListener("paste", async (e) => {
  if (S.mode === "corpus" || !$("#tab-setup").classList.contains("active")) return;
  const items = Array.from(e.clipboardData?.items || []).filter((it) => it.type.startsWith("image/"));
  if (!items.length) return;
  e.preventDefault();
  let { t, e: ex } = S.target;
  for (const it of items) {
    const f = it.getAsFile();
    if (!f) continue;
    await addPage(t, ex, await fileToDataUrl(f));
    if (ex === -1) ex = S.exam.themes[t].exercises.length - 1;
  }
  toast(`Η εικόνα μπήκε στο ΘΕΜΑ ${LETTERS[t]}.`);
});

/* ---------- πεδία με αποθηκευμένη λίστα (Τάξη, Επιμέλεια) */
function fillList(inputSel, listName) {
  const values = S.app.settings[listName] || [];
  const dl = $(`#${listName}-list`);
  dl.innerHTML = values.map((v) => `<option value="${escapeHtml(v)}">`).join("");
  const mgr = $(`#${listName}-manage`);
  mgr.innerHTML = "";
  if (!values.length) {
    mgr.append(el("div", { class: "small muted", text: "Η λίστα είναι κενή. Κάθε νέα τιμή που χρησιμοποιείτε αποθηκεύεται αυτόματα." }));
  }
  for (const v of values) {
    mgr.append(el("div", { class: "list-row" },
      el("button", { class: "linklike", text: v, title: "Επιλογή", onclick: () => { $(inputSel).value = v; mgr.hidden = true; } }),
      el("button", { class: "icon-mini danger", text: "✕", title: "Διαγραφή από τη λίστα", onclick: async () => {
        S.app = await api.forget_value(listName, v); fillList(inputSel, listName);
      } })));
  }
}

async function saveListValue(listName, inputSel) {
  const v = $(inputSel).value.trim();
  if (!v) return;
  const list = S.app.settings[listName] || [];
  if (list.includes(v)) { toast("Υπάρχει ήδη στη λίστα."); return; }
  S.app = await api.save_settings({ [listName]: [...list, v] });
  fillList(inputSel, listName);
  toast("Αποθηκεύτηκε στη λίστα.");
}

async function startTranscription(exList) {
  const jobs = exList.filter((ex) => ex.pages.length).map((ex) => ({ job_id: ex.id, image_ids: ex.pages.map((p) => p.imageId) }));
  if (!jobs.length) return;
  exList.forEach((ex) => { ex.status = "pending"; ex.error = null; });
  try {
    const res = await api.transcribe(jobs);
    if (!res.ok) throw new Error(res.error);
  } catch (e) {
    exList.forEach((ex) => { ex.status = "error"; ex.error = String(e.message || e); });
  }
  renderReview();
}

async function onTranscribeClick() {
  readDetails();
  const all = allExercises();
  if (!all.length) { toast("Προσθέστε τουλάχιστον μία εικόνα άσκησης.", true); return; }
  const empty = S.exam.themes.map((t, i) => (t.exercises.length ? null : LETTERS[i])).filter(Boolean);
  if (empty.length && !confirm(`Τα θέματα ${empty.join(", ")} δεν έχουν εικόνα και θα παραλειφθούν. Συνέχεια;`)) return;
  const todo = all.filter((ex) => ex.status === "new" || ex.status === "error");
  switchTab("review");
  if (todo.length) await startTranscription(todo);
}

/* ================================================================== ΒΗΜΑ 2: Έλεγχος */
async function onTranscribed({ job_id, result, error }) {
  const f = findExercise(job_id);
  if (!f) return;
  const { t, ex } = f;
  if (error || !result) {
    ex.status = "error";
    ex.error = error || "Άγνωστο σφάλμα.";
  } else {
    ex.status = "done";
    ex.data = result;
    ex.fig = result.figure ? { mode: "redraw", render: result.figure_render || null, crop: null } : null;
    delete result.figure_render;
  }
  await syncItemPoints(t);
  if ($("#tab-review").classList.contains("active")) {
    const card = document.getElementById(`ex-${job_id}`);
    if (card) card.replaceWith(renderExercise(t, S.exam.themes.indexOf(t), ex));
    renderPointsRow(t, S.exam.themes.indexOf(t));
  }
  updateTotals();
}

function renderReview() {
  const box = $("#review");
  box.innerHTML = "";
  S.exam.themes.forEach((t, i) => {
    if (!t.exercises.length) return;
    const sec = el("div", { class: "rtheme", id: `rtheme-${i}` },
      el("h2", {}, `ΘΕΜΑ ${LETTERS[i]}`, el("span", { class: "muted small", text: `${t.pointsTotal} μονάδες` })));
    t.exercises.forEach((ex) => sec.append(renderExercise(t, i, ex)));
    sec.append(el("div", { id: `points-${i}` }));
    box.append(sec);
    renderPointsRow(t, i);
  });
  if (!box.children.length) box.append(el("div", { class: "card muted", text: "Δεν υπάρχουν θέματα ακόμα. Προσθέστε εικόνες στο βήμα 1." }));
  updateTotals();
}

function renderPointsRow(t, i) {
  const holder = document.getElementById(`points-${i}`);
  if (!holder) return;
  const L = LETTERS[i];
  const n = level1Count(t);
  const sum = t.itemPoints.reduce((a, b) => a + (+b || 0), 0);
  const row = el("div", { class: "points-row" }, el("span", { class: "plab", text: "Μονάδες:" }));
  t.itemPoints.forEach((v, k) => {
    row.append(el("span", { class: "muted", text: `${L}${k + 1}` }),
      el("input", { type: "number", min: 0, value: v, oninput: (e) => {
        t.itemPoints[k] = +e.target.value || 0; t.manualPoints = true;
        const s = t.itemPoints.reduce((a, b) => a + (+b || 0), 0);
        $(".psum", row).textContent = `= ${s} / ${t.pointsTotal}`;
        $(".psum", row).style.color = s === t.pointsTotal ? "var(--ok)" : "var(--warn)";
        updateTotals();
      } }));
    if (k < n - 1) row.append(el("span", { text: "+" }));
  });
  row.append(el("span", { class: "psum", text: `= ${sum} / ${t.pointsTotal}`, style: `color:${sum === t.pointsTotal ? "var(--ok)" : "var(--warn)"}` }),
    el("div", { class: "spacer" }),
    el("button", { class: "btn small", text: "Αυτόματη κατανομή", onclick: async () => { await syncItemPoints(t, true); renderPointsRow(t, i); updateTotals(); } }));
  holder.replaceChildren(row);
}

function textPair(value, onChange, refmap, rows = 2) {
  const prev = el("div", { class: "preview" });
  const ta = el("textarea", { rows, spellcheck: "false" });
  ta.value = value || "";
  const update = () => { prev.innerHTML = mdToHtml(ta.value, refmap); };
  ta.addEventListener("input", () => { onChange(ta.value); update(); autoGrow(ta); });
  update();
  requestAnimationFrame(() => autoGrow(ta));
  return el("div", { class: "pair" }, ta, prev);
}

function choicesEditor(item, rerender) {
  const ch = item.choices;
  const box = el("div", { class: "choices" });
  ch.options.forEach((opt, i) => {
    const inp = el("input", { type: "text", value: opt, oninput: (e) => { ch.options[i] = e.target.value; prev(); } });
    box.append(el("span", { class: "cl", text: `${LETTERS[i]}:` }), inp);
  });
  const cols = el("select", { onchange: (e) => { ch.columns = +e.target.value; prev(); } },
    el("option", { value: 2, text: "2 στήλες" }), el("option", { value: 4, text: "4 στήλες" }));
  cols.value = String(ch.columns);
  const preview = el("div", { class: "preview" });
  function prev() {
    const c = ch.columns >= 4 ? 4 : 2;
    let html = "<table>";
    ch.options.forEach((o, i) => {
      if (i % c === 0) html += "<tr>";
      html += `<td><b>${LETTERS[i]}:</b> ${mdToHtml(o).replace(/^<p>|<\/p>\s*$/g, "")}</td>`;
      if (i % c === c - 1) html += "</tr>";
    });
    preview.innerHTML = html + "</table>";
  }
  prev();
  return el("div", {},
    el("div", { class: "row" }, el("span", { class: "field-label", text: "Επιλογές" }), cols,
      el("button", { class: "btn small ghost", text: "Αφαίρεση επιλογών", onclick: () => { item.choices = null; rerender(); } })),
    el("div", { class: "pair" }, box, preview));
}

function renderItems(items, depth, labelOf, pathPrefix, refmap, rerender) {
  const wrap = el("div", { class: depth > 1 ? "sub" : "" });
  items.forEach((it, idx) => {
    const path = [...pathPrefix, idx];
    const lab = labelOf(path);
    const head = el("div", { class: "item-head" },
      el("span", { class: "lab", text: lab }),
      el("span", { class: "orig-lab", text: it.label ? `(βιβλίο: ${it.label})` : "" }),
      el("div", { class: "spacer" }),
      !it.choices ? el("button", { class: "btn small ghost", text: "+ επιλογές Α–Δ", title: "Ερώτηση πολλαπλής επιλογής",
        onclick: () => { it.choices = { columns: 2, options: ["", "", "", ""] }; rerender(); } }) : null,
      depth < 3 ? el("button", { class: "btn small ghost", text: "+ υποερώτημα",
        onclick: () => { (it.items = it.items || []).push({ label: "", text: "", choices: null, items: [] }); rerender(); } }) : null,
      el("button", { class: "btn small ghost", text: "+ ερώτημα μετά",
        onclick: () => { items.splice(idx + 1, 0, { label: "", text: "", choices: null, items: [] }); rerender(); } }),
      el("button", { class: "btn small ghost danger", text: "✕", title: "Διαγραφή ερωτήματος",
        onclick: () => { if (confirm("Διαγραφή αυτού του ερωτήματος;")) { items.splice(idx, 1); rerender(); } } }));
    const node = el("div", { class: "item" }, head, textPair(it.text, (v) => { it.text = v; }, refmap));
    if (it.choices) node.append(choicesEditor(it, rerender));
    if (it.items && it.items.length) node.append(renderItems(it.items, depth + 1, labelOf, path, refmap, rerender));
    wrap.append(node);
  });
  return wrap;
}

function figurePage(ex) {
  const p = Math.min(Math.max(0, ex.data.figure.crop_page || 0), ex.pages.length - 1);
  return ex.pages[p];
}

function figLabelRow(fig, st, key, rerender) {
  // Μέγεθος γραμμάτων του επανασχεδιασμένου σχήματος (ανεξάρτητο από τις Ρυθμίσεις, όπως στο πρωτότυπο).
  const r = st.render;
  if (st.mode !== "redraw" || !r || r.error || !r.label_pt) return null;
  const apply = async (pt) => {
    let o;
    try { o = JSON.parse(fig.spec_json); } catch (_) { toast("Η περιγραφή του σχήματος δεν είναι έγκυρη.", true); return; }
    if (pt == null) delete o.label_pt; else o.label_pt = Math.round(pt * 2) / 2;
    fig.spec_json = JSON.stringify(o);
    st.render = await api.render_figure(key, fig.spec_json);
    rerender();
  };
  const how = { manual: "δικό σας μέγεθος", original: "όπως στο πρωτότυπο", auto: "αυτόματο" }[r.label_mode] || "";
  const pt = String(r.label_pt).replace(".", ",");
  return el("div", { class: "row fig-size" },
    el("span", { class: "small", text: "Γράμματα σχήματος:" }),
    el("button", { class: "btn small", text: "Α−", title: "Μικρότερα γράμματα στο σχήμα",
      disabled: r.label_pt <= 6, onclick: () => apply(Math.max(6, r.label_pt - 1)) }),
    el("span", { class: "small", text: `${pt} pt` }),
    el("button", { class: "btn small", text: "Α+", title: "Μεγαλύτερα γράμματα στο σχήμα",
      disabled: r.label_pt >= 20, onclick: () => apply(Math.min(20, r.label_pt + 1)) }),
    el("span", { class: "small muted", text: how }),
    r.label_mode === "manual" ? el("button", { class: "btn small ghost", text: "Επαναφορά", title: "Μέγεθος όπως στο πρωτότυπο",
      onclick: () => apply(null) }) : null);
}

function exPlainData(d) {
  return { stem: d.stem || "", items: d.items || [], closing: d.closing || "" };
}

async function figureFromImage(ex, ids, request, rerender, btn) {
  // Σχήμα από τις εικόνες της άσκησης (π.χ. από τη λύση), ή νέος έλεγχος του υπάρχοντος με πλέγμα.
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Ο Claude κοιτάζει το σχήμα… (περίπου ½–1 λεπτό)";
  try {
    const r = request === null
      ? await api.recheck_figure(ex.id, ids, ex.data.figure, exPlainData(ex.data))
      : await api.read_figure(ex.id, ids, request, exPlainData(ex.data));
    if (r.error) { toast(r.error, true); btn.disabled = false; btn.textContent = old; return; }
    ex.data.figure = r.figure;
    ex.fig = { mode: "redraw", render: r.render, crop: null };
    toast(request === null ? "Ο έλεγχος του σχήματος ολοκληρώθηκε." : "Το σχήμα προστέθηκε.");
  } catch (e) {
    toast(String(e), true);
  }
  rerender();
}

function figureCheckInfo(fig) {
  if (!fig || !fig.checked) return null;
  return el("div", { class: "small fig-checked", title: fig.check_notes || "" },
    el("b", { text: "✓ Ελέγχθηκε σε πλέγμα." }), " ",
    el("span", { class: "muted", text: (fig.check_notes || "").slice(0, 400) }));
}

function figureToolsRow(ex, ids, rerender) {
  // Για άσκηση ΜΕ σχήμα: νέος έλεγχος, ή άλλο σχήμα από την εικόνα (π.χ. από τη λύση).
  const req = el("input", { type: "text", placeholder: "Ποιο σχήμα; π.χ. «το σχήμα της λύσης» (προαιρετικό)" });
  const again = el("button", { class: "btn small", text: "🔍 Νέος έλεγχος με πλέγμα",
    title: "Ο Claude ξανακοιτά το σχήμα πάνω σε πλέγμα και διορθώνει θέσεις, φορές βελών και χρώματα",
    onclick: () => figureFromImage(ex, ids, null, rerender, again) });
  const other = el("button", { class: "btn small ghost", text: "Άλλο σχήμα από την εικόνα",
    onclick: () => { if (confirm("Το τωρινό σχήμα θα αντικατασταθεί. Συνέχεια;")) figureFromImage(ex, ids, req.value, rerender, other); } });
  return el("div", { class: "fig-tools" }, el("div", { class: "row" }, again), el("div", { class: "row" }, req, other));
}

function noFigureBox(ex, ids, rerender) {
  // Η άσκηση δεν έχει σχήμα (ή το σχήμα ήταν στη λύση και παραλείφθηκε): προσθήκη κατ' απαίτηση.
  const req = el("input", { type: "text", placeholder: "Ποιο σχήμα; π.χ. «το σχήμα της λύσης» (προαιρετικό)" });
  const add = el("button", { class: "btn small", text: "＋ Σχήμα από την εικόνα",
    title: "Ο Claude βρίσκει το σχήμα στην εικόνα — και μέσα στη λύση — και το ξανασχεδιάζει",
    onclick: () => figureFromImage(ex, ids, req.value, rerender, add) });
  return el("details", { class: "fig-add" },
    el("summary", { class: "small", text: "＋ Προσθήκη σχήματος (π.χ. από τη λύση)" }),
    el("p", { class: "small muted", text: "Η ανάγνωση κρατά μόνο την εκφώνηση. Αν το σχήμα που χρειάζεστε είναι μέσα στη λύση του βιβλίου ή δεν διαβάστηκε, ζητήστε το εδώ." }),
    el("div", { class: "row" }, req, add));
}

function figureBox(t, tIdx, ex) {
  const fig = ex.data.figure;
  const st = ex.fig;
  const box = el("div", { class: "figure-box" });
  const current = st.mode === "original" ? st.crop : st.render;
  const img = current && current.png_data ? el("img", { class: "fig-img", src: current.png_data, onclick: () => lightbox(current.png_data) }) : null;
  const rerender = () => { const c = document.getElementById(`ex-${ex.id}`); if (c) c.replaceWith(renderExercise(t, tIdx, ex)); };

  const radio = (value, text) => el("label", {},
    el("input", { type: "radio", name: `fig-${ex.id}`, value, checked: st.mode === value, onchange: async () => {
      st.mode = value;
      if (value === "original" && !st.crop) st.crop = await api.crop_figure(ex.id, figurePage(ex).imageId, fig.crop);
      rerender();
    } }), text);

  box.append(el("div", { class: "row" }, el("span", { class: "field-label", text: "Σχήμα" }),
    fig.confidence === "low" ? el("span", { class: "badge err", text: "αβέβαιη ανάγνωση — ελέγξτε το" }) : null));
  box.append(el("div", { class: "radio-row" }, radio("redraw", "Επανασχεδιασμένο (επεξεργάσιμο στο Word)"), radio("original", "Πρωτότυπη εικόνα (περικοπή)")));
  if (img) box.append(img);
  if (current && current.error) box.append(el("div", { class: "fig-warn", text: current.error }));
  const sizeRow = figLabelRow(fig, st, ex.id, rerender);
  if (sizeRow) box.append(sizeRow);
  if (st.mode === "redraw" && current && current.warnings && current.warnings.length) {
    box.append(el("div", { class: "fig-warn" }, ...current.warnings.map((w) => el("div", { text: "⚠ " + w }))));
  }
  box.append(el("div", { class: "small muted", text: fig.description }));
  const chk = figureCheckInfo(fig);
  if (chk) box.append(chk);
  box.append(figureToolsRow(ex, ex.pages.map((p) => p.imageId), rerender));

  const spec = el("textarea", { rows: 8, spellcheck: "false" });
  spec.value = prettyJson(fig.spec_json);
  const cropInp = el("input", { type: "text", value: (fig.crop || []).map((v) => (+v).toFixed(2)).join(", ") });
  const pageSel = el("select", {}, ...ex.pages.map((_, i) => el("option", { value: i, text: `σελίδα ${i + 1}` })));
  pageSel.value = String(fig.crop_page || 0);
  box.append(el("details", {},
    el("summary", { class: "small", text: "Επεξεργασία σχήματος (για προχωρημένους)" }),
    el("p", { class: "small muted", text: "Η περιγραφή του σχήματος σε JSON. Αλλάξτε συντεταγμένες ή ετικέτες και πατήστε «Επανασχεδίαση»." }),
    spec,
    el("div", { class: "row" },
      el("button", { class: "btn small", text: "Επανασχεδίαση", onclick: async () => {
        fig.spec_json = spec.value;
        st.render = await api.render_figure(ex.id, spec.value);
        st.mode = "redraw";
        rerender();
      } })),
    el("div", { class: "row" },
      el("span", { class: "small", text: "Περικοπή πρωτοτύπου [x0, y0, x1, y1] (0–1):" }), cropInp,
      ex.pages.length > 1 ? pageSel : null,
      el("button", { class: "btn small", text: "Εφαρμογή", onclick: async () => {
        const vals = cropInp.value.split(",").map((v) => parseFloat(v));
        if (vals.length === 4 && vals.every((v) => !isNaN(v))) fig.crop = vals;
        fig.crop_page = +pageSel.value || 0;
        st.crop = await api.crop_figure(ex.id, figurePage(ex).imageId, fig.crop);
        st.mode = "original";
        rerender();
      } }))));
  return box;
}

function prettyJson(s) {
  try { return JSON.stringify(JSON.parse(s), null, 1); } catch (_) { return s; }
}

function renderExercise(t, tIdx, ex) {
  const exIdx = t.exercises.indexOf(ex);
  const many = ex.pages.length > 1;
  const orig = el("div", { class: "orig" },
    ...ex.pages.map((p, i) => el("div", { class: "orig-page" },
      many ? el("div", { class: "page-lab", text: `Σελίδα ${i + 1} από ${ex.pages.length}` }) : null,
      el("img", { src: p.dataUrl, alt: `σελίδα ${i + 1}`, onclick: () => lightbox(p.dataUrl) }))),
    el("div", { class: "src small muted", text: ex.data?.source_label ? `Άσκηση βιβλίου: ${ex.data.source_label}` : "" }));
  const editor = el("div", { class: "editor" });
  const card = el("div", { class: "ex", id: `ex-${ex.id}` }, orig, editor);

  if (ex.status === "pending" || ex.status === "new") {
    editor.append(el("div", { class: "pending" }, el("div", { class: "spinner" }),
      ex.status === "new" ? "Αναμονή…" : `Ο Claude διαβάζει ${many ? `τις ${ex.pages.length} σελίδες` : "την εικόνα"}… (συνήθως 15–60 δευτερόλεπτα)`));
    return card;
  }
  if (ex.status === "error") {
    editor.append(el("div", { class: "error-box", text: ex.error || "Σφάλμα" }),
      el("div", { class: "row" }, el("button", { class: "btn", text: "Δοκιμή ξανά", onclick: () => startTranscription([ex]) })));
    return card;
  }

  const d = ex.data;
  const rerender = () => {
    const c = document.getElementById(`ex-${ex.id}`);
    if (c) c.replaceWith(renderExercise(t, tIdx, ex));
    syncItemPoints(t).then(() => { renderPointsRow(t, tIdx); updateTotals(); });
  };
  const labeler = labelsFor(t, tIdx);
  const refmap = refmapFor(t, tIdx, ex);
  const single = t.exercises.length === 1;

  if (d.uncertain && d.uncertain.length) {
    editor.append(el("div", { class: "notes" }, el("b", { text: "Χρειάζεται έλεγχος:" }),
      el("ul", {}, ...d.uncertain.map((u) => el("li", { text: u })))));
  }
  editor.append(el("div", { class: "field-label", text: single ? "Εκφώνηση" : `Εκφώνηση ${labeler(exIdx, [])}` }),
    textPair(d.stem, (v) => { d.stem = v; }, refmap, 3));
  if (d.figure && ex.fig) editor.append(figureBox(t, tIdx, ex));
  else editor.append(noFigureBox(ex, ex.pages.map((p) => p.imageId), rerender));
  editor.append(el("div", { class: "field-label", text: "Ερωτήματα" }));
  editor.append(single
    ? renderItems(d.items, 1, (p) => labeler(exIdx, p), [], refmap, rerender)
    : renderItems(d.items, 2, (p) => labeler(exIdx, p), [], refmap, rerender));
  if (!d.items.length) {
    editor.append(el("button", { class: "btn small", text: "+ ερώτημα", onclick: () => { d.items.push({ label: "", text: "", choices: null, items: [] }); rerender(); } }));
  }
  if (d.closing) editor.append(el("div", { class: "field-label", text: "Κείμενο στο τέλος" }), textPair(d.closing, (v) => { d.closing = v; }, refmap));
  editor.append(el("div", { class: "row" },
    el("div", { class: "spacer" }),
    el("button", { class: "btn small ghost", text: many ? "↻ Ξαναδιάβασε τις σελίδες" : "↻ Ξαναδιάβασε την εικόνα", onclick: () => {
      if (confirm("Η μεταγραφή θα γίνει από την αρχή και οι αλλαγές σας σε αυτή την άσκηση θα χαθούν. Συνέχεια;")) startTranscription([ex]);
    } })));
  return card;
}

/* ================================================================== ΒΗΜΑ 3: Word */
function examPayload() {
  readDetails();
  const themes = S.exam.themes
    .filter((t) => t.exercises.some((ex) => ex.status === "done"))
    .map((t) => ({
      points: t.itemPoints,
      exercises: t.exercises.filter((ex) => ex.status === "done").map((ex) => {
        let fr = null;
        if (ex.data.figure && ex.fig) {
          if (ex.fig.mode === "original") {
            const c = ex.fig.crop;
            if (c && !c.error) fr = { mode: "original", png: c.png, svg: null, width_cm: c.width_cm };
          } else {
            const r = ex.fig.render || {};
            fr = { mode: "redraw", spec_json: ex.data.figure.spec_json, png: r.png, svg: r.svg, width_cm: r.width_cm };
          }
        }
        return { stem: ex.data.stem, items: ex.data.items, closing: ex.data.closing, figure_render: fr };
      }),
    }));
  return {
    title: S.exam.title, subtitle: S.exam.subtitle, date: S.exam.date,
    class_name: S.exam.class_name, editor: S.exam.editor, themes,
  };
}

async function onBuild() {
  const exs = allExercises();
  const pending = exs.filter((e) => e.status === "pending").length;
  const failed = exs.filter((e) => e.status === "error" || e.status === "new").length;
  if (pending) { toast("Περιμένετε να ολοκληρωθεί η μεταγραφή όλων των ασκήσεων.", true); return; }
  if (failed && !confirm(`${failed} άσκηση(εις) δεν έχουν μεταγραφεί και θα παραλειφθούν. Συνέχεια;`)) return;
  const sum = S.exam.themes.reduce((a, t) => a + t.itemPoints.reduce((x, y) => x + (+y || 0), 0), 0);
  if (sum !== 100 && !confirm(`Το σύνολο των μονάδων είναι ${sum}, όχι 100. Δημιουργία παρ' όλα αυτά;`)) return;
  readDetails();
  if ((!S.exam.class_name || !S.exam.editor) &&
      !confirm("Δεν έχετε συμπληρώσει " + [!S.exam.class_name && "Τάξη", !S.exam.editor && "Επιμέλεια"].filter(Boolean).join(" και ") +
        " (βήμα 1). Το υποσέλιδο θα είναι ελλιπές. Συνέχεια;")) return;

  const btn = $("#btn-build");
  btn.disabled = true;
  btn.textContent = "Δημιουργία…";
  try {
    const res = await api.build(examPayload());
    S.lastBuild = res;
    if (res.state) { S.app = res.state; refreshLists(); }
    renderDone();
    switchTab("done");
  } catch (e) {
    toast(String(e.message || e), true, 8000);
  } finally {
    btn.disabled = false;
    btn.textContent = "Δημιουργία Word";
  }
}

function renderDone() {
  const box = $("#done");
  box.innerHTML = "";
  const r = S.lastBuild;
  if (!r) { box.append(el("p", { class: "muted", text: "Δεν έχει δημιουργηθεί ακόμα αρχείο." })); return; }
  if (!r.ok) {
    box.append(el("h2", { text: "Δεν δημιουργήθηκε το αρχείο" }), el("div", { class: "error-box", text: r.error }));
    return;
  }
  box.append(...[
    el("div", { class: "row" }, el("span", { class: "okmark", text: "✓" }), el("h2", { style: "margin:0", text: "Το διαγώνισμα είναι έτοιμο" })),
    el("div", { class: "path", text: r.path }),
    el("div", { class: "row" },
      el("button", { class: "btn primary", text: "Άνοιγμα στο Word", onclick: () => api.open_file(r.path) }),
      el("button", { class: "btn", text: "Άνοιγμα φακέλου", onclick: () => api.open_folder(r.path) })),
    el("p", { class: "muted small", text: "Ανοίξτε το αρχείο στο Word: οι προεπισκοπήσεις των Windows και το LibreOffice δεν δείχνουν τις εξισώσεις, ενώ το αρχείο είναι σωστό. Στα σχήματα, δεξί κλικ → «Μετατροπή σε σχήμα» για επεξεργασία." }),
    r.warnings && r.warnings.length ? el("div", { class: "notes" }, el("b", { text: "Σημειώσεις:" }), el("ul", {}, ...r.warnings.map((w) => el("li", { text: w })))) : null,
    el("div", { class: "row" },
      el("button", { class: "btn", text: "← Πίσω στον έλεγχο", onclick: () => switchTab("review") }),
      el("div", { class: "spacer" }),
      el("button", { class: "btn ghost", text: "Νέο διαγώνισμα", onclick: newExam }))].filter(Boolean));
}

async function newExam() {
  if (!confirm("Να ξεκινήσει νέο διαγώνισμα; Οι εικόνες και οι μεταγραφές του τρέχοντος θα χαθούν.")) return;
  const n = S.exam.themes.length || 4;
  S.exam = { themes: [] };
  S.target = { t: 0, e: -1 };
  $("#f-date").value = "";
  await setThemeCount(n);
  switchTab("setup");
}

/* ================================================================== Ρυθμίσεις */
function fillSettings() {
  const st = S.app;
  const s = st.settings;
  const pg = $("#s-provider");
  pg.innerHTML = "";
  for (const [k, v] of Object.entries(st.providers)) {
    pg.append(el("label", { class: s.provider === k ? "sel" : "" },
      el("input", { type: "radio", name: "prov", value: k, checked: s.provider === k, onchange: () => { s.provider = k; fillSettings(); } }), v));
  }
  $("#s-agent").hidden = s.provider !== "agent_sdk";
  $("#s-api").hidden = s.provider !== "api";
  const os = $("#s-oauth-status");
  os.textContent = st.has_oauth ? "✓ Υπάρχει αποθηκευμένο token συνδρομής." : "Δεν έχει αποθηκευτεί token. (Αν στον υπολογιστή υπάρχει ήδη σύνδεση Claude Code, θα χρησιμοποιηθεί αυτή.)";
  os.className = "status-line " + (st.has_oauth ? "ok" : "no");
  const ks = $("#s-key-status");
  ks.textContent = st.has_api_key ? "✓ Υπάρχει αποθηκευμένο κλειδί API." : "Δεν έχει αποθηκευτεί κλειδί API.";
  ks.className = "status-line " + (st.has_api_key ? "ok" : "no");
  $("#s-model").value = s.model;
  $("#models").innerHTML = st.models.map((m) => `<option value="${escapeHtml(m)}">`).join("");
  $("#s-parallel").value = s.max_parallel;
  const al = $("#s-aliases");
  al.innerHTML = "";
  for (const [k, v] of Object.entries(s.api_aliases || {})) {
    al.append(el("span", { text: k }), el("input", { type: "text", value: v, "data-alias": k }));
  }
  $("#s-template").textContent = s.template_path || "Ενσωματωμένο πρότυπο της εφαρμογής";
  $("#s-outdir").textContent = s.output_dir;
  $("#s-sublevel").value = s.sublevel_style;
  $("#s-align").value = s.points_align;
  $("#s-font").value = s.font_name;
  $("#s-math-font").value = s.math_font || "Cambria Math";
  fillSizeSelect($("#s-size"), +s.font_size || 12);
  fillSizeSelect($("#s-math-size"), +s.math_size || 12);
  $("#s-fig-colors").value = s.figure_colors === "bw" ? "bw" : "original";
  $("#s-fig-check").checked = s.figure_check !== false;
  fillFontLists();
  if (!FONT_INFO) loadFonts(false);
  $("#s-theme").value = s.theme || "system";
  $("#s-check-updates").checked = !!s.check_updates;
  $("#s-repo").value = s.update_repo || "";
  $("#s-rules-status").textContent = st.rules_custom ? "Χρησιμοποιούνται δικοί σας κανόνες." : "Χρησιμοποιούνται οι αρχικοί κανόνες.";
  $("#s-version").textContent = `Έκδοση ${st.version}`;
  updateTemplateHint();
}

/* ---------- γραμματοσειρές */
function fillSizeSelect(sel, value) {
  sel.innerHTML = "";
  const sizes = SIZES.includes(value) ? SIZES : [...SIZES, value].sort((a, b) => a - b);
  for (const z of sizes) sel.append(el("option", { value: z, text: String(z).replace(".", ",") }));
  sel.value = String(value);
}

async function loadFonts(refresh) {
  const st = $("#s-fonts-status");
  st.textContent = refresh ? "Ανάγνωση γραμματοσειρών…" : "Φόρτωση λίστας γραμματοσειρών…";
  try {
    FONT_INFO = await api.list_fonts(!!refresh);
  } catch (e) {
    FONT_INFO = null;
  }
  fillFontLists();
}

function fillFontLists() {
  const st = $("#s-fonts-status");
  const text = FONT_INFO?.text?.length ? FONT_INFO.text : FONTS.map((n) => ({ name: n, greek: true }));
  const math = FONT_INFO?.math?.length ? FONT_INFO.math : MATH_FONTS;
  $("#fonts").innerHTML = text.map((f) => `<option value="${escapeHtml(f.name)}"${f.greek ? "" : ' label="χωρίς ελληνικά"'}>`).join("");
  $("#math-fonts").innerHTML = math.map((n) => `<option value="${escapeHtml(n)}">`).join("");
  if (FONT_INFO) {
    st.textContent = `Βρέθηκαν ${text.length} γραμματοσειρές, από τις οποίες ${math.length} μαθηματικών.`;
  } else if (st.textContent.startsWith("Φόρτωση") || st.textContent.startsWith("Ανάγνωση")) {
    // περιμένουμε
  } else {
    st.textContent = "";
  }
  const rec = FONT_INFO?.recommended_math || [];
  const installed = new Set(math.map((n) => n.toLowerCase()));
  $("#s-math-recommend-list").innerHTML = rec.length
    ? "<table><tr><th>Γραμματοσειρά</th><th>Ταιριάζει με κείμενο σε</th><th></th></tr>" +
      rec.map((r) => `<tr><td>${escapeHtml(r.name)}</td><td>${escapeHtml(r.pair)}</td><td>${installed.has(r.name.toLowerCase()) ? "✓ εγκατεστημένη" : escapeHtml(r.note)}</td></tr>`).join("") + "</table>"
    : "";
  checkFontWarnings();
}

function checkFontWarnings() {
  const txt = $("#s-font").value.trim();
  const mth = $("#s-math-font").value.trim();
  const tw = $("#s-font-warn");
  const mw = $("#s-math-warn");
  tw.textContent = "";
  mw.textContent = "";
  if (!FONT_INFO) return;
  const tinfo = FONT_INFO.text.find((f) => f.name.toLowerCase() === txt.toLowerCase());
  const isMath = FONT_INFO.math.some((n) => n.toLowerCase() === txt.toLowerCase());
  if (txt && !tinfo) tw.textContent = "⚠ Δεν είναι εγκατεστημένη σε αυτόν τον υπολογιστή· το Word θα χρησιμοποιήσει άλλη.";
  else if (tinfo && !tinfo.greek) tw.textContent = "⚠ Δεν υποστηρίζει ελληνικά — θα εμφανιστούν λάθος χαρακτήρες.";
  else if (isMath) tw.textContent = "⚠ Γραμματοσειρά μαθηματικών στο απλό κείμενο: το Word αφήνει πολύ μεγάλα κενά ανάμεσα στις γραμμές. Προτείνεται η αντίστοιχη γραμματοσειρά κειμένου (π.χ. Cambria).";
  if (mth && !FONT_INFO.math.some((n) => n.toLowerCase() === mth.toLowerCase())) {
    mw.textContent = FONT_INFO.text.some((f) => f.name.toLowerCase() === mth.toLowerCase())
      ? "⚠ Δεν είναι γραμματοσειρά μαθηματικών· το Word θα την αγνοήσει στις εξισώσεις και θα χρησιμοποιήσει Cambria Math."
      : "⚠ Δεν είναι εγκατεστημένη σε αυτόν τον υπολογιστή· το Word θα χρησιμοποιήσει Cambria Math.";
  }
}

function collectSettings() {
  const s = S.app.settings;
  const aliases = {};
  for (const inp of $$("#s-aliases input")) aliases[inp.dataset.alias] = inp.value.trim();
  return {
    provider: s.provider,
    model: $("#s-model").value.trim() || "opus",
    max_parallel: +$("#s-parallel").value || 3,
    api_aliases: aliases,
    sublevel_style: $("#s-sublevel").value,
    points_align: $("#s-align").value,
    font_name: $("#s-font").value.trim() || "Cambria",
    font_size: +$("#s-size").value || 12,
    math_font: $("#s-math-font").value.trim() || "Cambria Math",
    math_size: +$("#s-math-size").value || 12,
    figure_colors: $("#s-fig-colors").value,
    figure_check: $("#s-fig-check").checked,
    theme: $("#s-theme").value,
    check_updates: $("#s-check-updates").checked,
    update_repo: $("#s-repo").value.trim() || "EDaskal/MATHEMATICS",
  };
}

async function saveSettings(close = true) {
  S.app = await api.save_settings(collectSettings());
  applyTheme(S.app.settings.theme);
  fillSettings();
  if (close) $("#settings").hidden = true;
  if ($("#tab-review").classList.contains("active")) renderReview();
  api.usage_state().then(renderUsage).catch(() => {});  // π.χ. αλλαγή συνδρομή ↔ κλειδί API
}

function updateTemplateHint() {
  const s = S.app.settings;
  const h = $("#template-hint");
  const fontInfo = `Γραμματοσειρές: κείμενο ${s.font_name} ${String(s.font_size).replace(".", ",")}, μαθηματικά ${s.math_font} ${String(s.math_size).replace(".", ",")} (αλλάζουν από τις Ρυθμίσεις).`;
  if (!s.template_path) {
    h.className = "hint-box warn";
    h.innerHTML = "";
    h.append("Χρησιμοποιείται το ενσωματωμένο πρότυπο. Για τα περιθώρια και τη μορφή των δικών σας διαγωνισμάτων ",
      el("a", { href: "#", text: "επιλέξτε ένα δικό σας διαγώνισμα", onclick: async (e) => { e.preventDefault(); S.app = await api.choose_template(); fillSettings(); } }),
      " ως πρότυπο. ", el("br"), fontInfo);
  } else {
    h.className = "hint-box" + (S.app.template_exists ? "" : " warn");
    h.textContent = (S.app.template_exists ? `Πρότυπο: ${s.template_path}` : `Το πρότυπο δεν βρέθηκε: ${s.template_path}`) + " · " + fontInfo;
  }
}

function bindSettings() {
  $("#btn-settings").onclick = () => { fillSettings(); openModal("#settings"); };
  $("#s-save").onclick = () => saveSettings(true);
  $("#s-setup-token").onclick = async () => {
    const r = await api.launch_setup_token();
    toast(r.ok ? "Ακολουθήστε τις οδηγίες στο νέο παράθυρο και αντιγράψτε το token που θα εμφανιστεί." : r.error, !r.ok, 7000);
  };
  const secret = async (kind, inputSel, value) => {
    const r = await api.set_secret(kind, value);
    if (!r.ok) { toast(r.error, true); return; }
    S.app = r.state;
    $(inputSel).value = "";
    fillSettings();
    toast(value ? "Αποθηκεύτηκε." : "Διαγράφηκε.");
  };
  $("#s-oauth-save").onclick = () => { const v = $("#s-oauth").value.trim(); if (v) secret("oauth", "#s-oauth", v); };
  $("#s-oauth-del").onclick = () => { if (confirm("Διαγραφή του token συνδρομής;")) secret("oauth", "#s-oauth", ""); };
  $("#s-key-save").onclick = () => { const v = $("#s-key").value.trim(); if (v) secret("api_key", "#s-key", v); };
  $("#s-key-del").onclick = () => { if (confirm("Διαγραφή του κλειδιού API;")) secret("api_key", "#s-key", ""); };
  $("#s-test").onclick = async () => {
    await saveSettings(false);
    const out = $("#s-test-result");
    out.textContent = "Δοκιμή…";
    out.style.color = "";
    const r = await api.test_connection();
    out.textContent = r.ok ? "✓ " + r.message : "✕ " + r.error;
    out.style.color = r.ok ? "var(--ok)" : "var(--warn)";
  };
  $("#s-template-choose").onclick = async () => { S.app = await api.choose_template(); fillSettings(); };
  $("#s-template-reset").onclick = async () => { S.app = await api.reset_template(); fillSettings(); };
  $("#s-outdir-choose").onclick = async () => { S.app = await api.choose_output_dir(); fillSettings(); };
  $("#s-rules-open").onclick = async () => { await api.open_rules(); S.app = await api.get_state(); fillSettings(); };
  $("#s-rules-reset").onclick = async () => {
    if (!confirm("Επαναφορά των αρχικών κανόνων; Οι αλλαγές σας στους κανόνες θα χαθούν.")) return;
    await api.reset_rules(); S.app = await api.get_state(); fillSettings();
  };
  $("#s-theme").onchange = (e) => applyTheme(e.target.value);
  $("#s-font").addEventListener("input", checkFontWarnings);
  $("#s-math-font").addEventListener("input", checkFontWarnings);
  $("#s-fonts-refresh").onclick = () => loadFonts(true);
  $("#s-check-now").onclick = () => checkUpdates(true);
}

/* ================================================================== Σχετικά, Τι νέο, Οδηγός, Ενημερώσεις */
let ABOUT = null;
async function loadAbout() {
  if (!ABOUT) ABOUT = await api.get_about();
  return ABOUT;
}

async function showAbout() {
  const a = await loadAbout();
  const b = a.build || {};
  $("#about-version").textContent = `Έκδοση ${a.version}`;
  const rows = [
    ["Έκδοση", a.version],
    ["Build", b.run ? `#${b.run}` : "τοπική έκδοση (όχι από GitHub)"],
    ["Ημερομηνία build", b.date || "—"],
    ["Commit", b.commit ? b.commit.slice(0, 7) : "—"],
    ["Ενημερώσεις από", `github.com/${a.repo}`],
  ];
  const kv = $("#about-kv");
  kv.innerHTML = "";
  for (const [k, v] of rows) kv.append(el("span", { text: k }), el("span", { text: v }));
  $("#about-changelog").innerHTML = safeMarkdown(a.changelog || "_Δεν βρέθηκε το ιστορικό αλλαγών._");
  $("#about-update").innerHTML = "";
  openModal("#about");
}

async function showGuide() {
  const a = await loadAbout();
  $("#guide-body").innerHTML = safeMarkdown(a.guide || "_Δεν βρέθηκε ο οδηγός χρήσης._");
  openModal("#guide");
}

async function checkUpdates(manual = false) {
  const target = manual ? $("#about-update") : null;
  if (manual) {
    if ($("#about").hidden) await showAbout();
    $("#about-update").innerHTML = "";
    $("#about-update").append(el("div", { class: "muted", text: "Έλεγχος για νέα έκδοση…" }));
  }
  let r;
  try { r = await api.check_updates(); } catch (e) { r = { ok: false, error: String(e.message || e) }; }
  S.update = r;
  if (!r.ok) {
    if (manual) $("#about-update").replaceChildren(updateErrorBox(r));
    return;
  }
  if (r.newer) {
    showUpdateBanner(r);
    if (manual) $("#about-update").replaceChildren(updateCard(r));
  } else if (manual) {
    $("#about-update").replaceChildren(el("div", { class: "okline", text: `✓ Έχετε την τελευταία έκδοση (${r.current}).` }));
  }
  void target;
}

function updateErrorBox(r) {
  // 1.7.7: η πραγματική αιτία + λεπτομέρειες + χειροκίνητη λήψη από τη σελίδα του GitHub
  const diag = el("div", { class: "small upd-diag", hidden: true });
  const runDiag = async () => {
    diag.hidden = false;
    diag.replaceChildren(el("div", { class: "muted", text: "Δοκιμή σύνδεσης με κάθε τρόπο…" }));
    let d;
    try { d = await api.update_diagnose(); } catch (e) { d = { ok: false, error: String(e) }; }
    const rows = (d.results || []).map((x) => el("div", {}, el("b", { text: (x.ok ? "✓ " : "✗ ") + x.method + ": " }), x.detail));
    diag.replaceChildren(...[
      r.detail ? el("div", { class: "muted", text: "Σφάλμα: " + r.detail }) : null,
      ...rows,
      d.ok === false ? el("div", { text: d.error || "" }) : null,
      el("div", { class: "muted", text: "Στείλτε μια φωτογραφία αυτού του πλαισίου αν χρειαστείτε βοήθεια." })].filter(Boolean));
  };
  return el("div", { class: "error-box" },
    el("div", { text: r.error }),
    el("div", { class: "row upd-err-actions" },
      el("button", { class: "btn small", text: "Άνοιγμα σελίδας λήψης", title: "Κατεβάστε και τρέξτε το Diagonismata-Setup από τη σελίδα του GitHub",
        onclick: () => api.open_releases_page() }),
      el("button", { class: "btn small ghost", text: "Λεπτομέρειες", onclick: runDiag })),
    diag);
}

function updateCard(r) {
  const size = r.size ? ` · ${(r.size / 1048576).toFixed(0)} MB` : "";
  return el("div", { class: "update-card" },
    el("h3", { text: `Νέα έκδοση ${r.latest}` + (r.published ? ` (${r.published})` : "") }),
    el("div", { class: "notes-md", html: safeMarkdown(r.notes || "") }),
    el("div", { class: "row" },
      r.url ? el("button", { class: "btn primary", text: `Λήψη και εγκατάσταση${size}`, onclick: () => installUpdate(r) })
        : el("span", { class: "muted small", text: "Η έκδοση δεν έχει ακόμα αρχείο εγκατάστασης. Δοκιμάστε σε λίγα λεπτά." }),
      el("div", { class: "spacer" }),
      el("span", { class: "small muted", text: `Τρέχουσα: ${r.current}` })),
    el("div", { class: "progress", id: "upd-progress", hidden: true }, el("div", { class: "bar" }), el("span", { class: "small" })));
}

function showUpdateBanner(r) {
  const b = $("#update-banner");
  b.innerHTML = "";
  b.append(el("span", { html: `<b>Υπάρχει νέα έκδοση ${escapeHtml(r.latest)}</b> (έχετε ${escapeHtml(r.current)}).` }),
    el("div", { class: "spacer" }),
    el("button", { class: "btn small", text: "Τι νέο & εγκατάσταση", onclick: async () => { await showAbout(); $("#about-update").replaceChildren(updateCard(r)); } }),
    el("button", { class: "icon-mini", text: "✕", title: "Απόκρυψη", onclick: () => { b.hidden = true; } }));
  b.hidden = false;
}

async function installUpdate(r) {
  if (!confirm(`Θα κατέβει και θα εγκατασταθεί η έκδοση ${r.latest}. Η εφαρμογή θα κλείσει και θα ξανανοίξει μόνη της. Βεβαιωθείτε ότι δεν έχετε αποθηκευμένη δουλειά στην οθόνη. Συνέχεια;`)) return;
  const p = $("#upd-progress");
  if (p) p.hidden = false;
  const res = await api.install_update(r.url);
  if (res && res.ok === false) { if (p) p.hidden = true; toast(res.error, true, 6000); }
}

function onUpdateProgress(d) {
  const p = $("#upd-progress");
  if (!p) return;
  const label = $("span", p);
  const bar = $(".bar", p);
  if (d.error) { label.textContent = "✕ " + d.error; label.style.color = "var(--warn)"; return; }
  if (d.stage === "install") { bar.style.width = "100%"; label.textContent = "Εγκατάσταση… η εφαρμογή θα κλείσει και θα ξανανοίξει."; return; }
  const pct = d.total ? Math.round((100 * d.done) / d.total) : 0;
  bar.style.width = pct + "%";
  label.textContent = d.total ? `Λήψη ${pct}%` : `Λήψη ${(d.done / 1048576).toFixed(0)} MB`;
}


/* ================================================================== CORPUS: ασκήσεις σε υπάρχον Word */
S.mode = "exam";
S.examTab = "setup";
S.corpus = { path: "", name: "", outline: [], sel: -1, images: [], exs: [], jobs: {}, manual: false, opened: false, dup: null, docEx: null };

function setMode(mode) {
  S.mode = mode;
  document.body.classList.toggle("mode-corpus", mode === "corpus");
  for (const b of $$("#modes button")) b.classList.toggle("active", b.dataset.mode === mode);
  if (mode === "corpus") {
    const cur = $(".tab.active");
    if (cur && cur.id !== "tab-corpus") S.examTab = cur.id.replace("tab-", "");
    for (const s of $$(".tab")) s.classList.toggle("active", s.id === "tab-corpus");
    if (!S.corpus.opened && S.app.settings.corpus_path) corpusOpen(S.app.settings.corpus_path, true);
    S.corpus.opened = true;
    renderCorpus();
  } else {
    switchTab(S.examTab || "setup");
  }
  phoneSync();
  window.scrollTo(0, 0);
}

function fmtNum(n, fmt) {
  const letters = (k) => { const a = "abcdefghijklmnopqrstuvwxyz"; return a[(k - 1) % 26].repeat(Math.floor((k - 1) / 26) + 1); };
  const roman = (k) => { let o = ""; for (const [v, r] of [[1000, "m"], [900, "cm"], [500, "d"], [400, "cd"], [100, "c"], [90, "xc"], [50, "l"], [40, "xl"], [10, "x"], [9, "ix"], [5, "v"], [4, "iv"], [1, "i"]]) while (k >= v) { o += r; k -= v; } return o; };
  switch (fmt) {
    case "lowerLetter": return letters(n);
    case "upperLetter": return letters(n).toUpperCase();
    case "lowerRoman": return roman(n);
    case "upperRoman": return roman(n).toUpperCase();
    default: return String(n);
  }
}

function corpusSection() { return S.corpus.outline[S.corpus.sel] || null; }

function corpusExLabel(i) {
  const plan = corpusPlan();
  if (plan && plan.labels[i]) return plan.labels[i];
  const sec = corpusSection();
  if (!sec) return `${i + 1}.`;
  const n = (sec.start || 1) + sec.exercises + i;
  return (sec.pattern || "%1.").replace("%1", fmtNum(n, sec.formats?.[0]));
}

function corpusItemLabel(path) {
  const sec = corpusSection();
  const fmts = sec?.formats || ["decimal", "lowerLetter", "lowerRoman", "decimal"];
  const d = path.length; // 1 = ερώτημα, 2 = υποερώτημα
  return fmtNum(path[path.length - 1] + 1, fmts[Math.min(d, 3)]) + ".";
}

async function corpusOpen(path, quiet = false) {
  const res = path ? await api.corpus_open(path) : await api.corpus_choose_file();
  if (!res.ok) {
    if (!res.cancelled && !quiet) toast(res.error, true, 7000);
    renderCorpus();
    return;
  }
  const prevKey = corpusSection()?.key;
  Object.assign(S.corpus, { path: res.path, name: res.name, outline: res.outline });
  S.corpus.sel = prevKey ? res.outline.findIndex((h) => h.key.title === prevKey.title && h.key.nth === prevKey.nth) : -1;
  S.app.settings.corpus_path = res.path;
  if (S.audit.path !== res.path) Object.assign(S.audit, { path: res.path, res: null });
  renderCorpus();
  renderAudit();
  corpusCheckDuplicates(0);
  loadDocEx();
}

function renderCorpus() {
  const C = S.corpus;
  $("#c-path").textContent = C.path ? C.path : "Δεν έχει επιλεγεί αρχείο.";
  $("#c-reload").disabled = !C.path;
  $("#c-openword").disabled = !C.path;
  const ol = $("#c-outline");
  ol.innerHTML = "";
  if (C.path && !C.outline.length) {
    ol.append(el("div", { class: "ol-row muted", text: "Δεν βρέθηκαν κεφάλαια/ενότητες. Χρειάζονται τίτλοι με στυλ «Επικεφαλίδα» ή επίπεδο διάρθρωσης." }));
  }
  C.outline.forEach((h, i) => {
    const cnt = h.exercises ? `${h.exercises} ασκήσεις · τελευταία ${h.last_label}` : (h.depth > 0 ? "καμία άσκηση ακόμα" : "");
    ol.append(el("div", { class: `ol-row d${Math.min(h.depth, 2)}` + (i === C.sel ? " sel" : ""), title: "Επιλογή ενότητας για τις νέες ασκήσεις",
      onclick: () => { C.sel = i; renderCorpus(); } },
      el("span", { text: h.display }), el("span", { class: "cnt", text: cnt })));
  });
  if (S.phone.active) phoneSync();
  const sec = corpusSection();
  $("#c-images-title").textContent = sec ? `2. Εικόνες ασκήσεων για «${sec.display}»` : "2. Εικόνες ασκήσεων";
  renderCorpusImages();
  renderCorpusReview();
}

/* ---------- εικόνες */
function corpusDrop() {
  const input = el("input", { type: "file", accept: "image/*,application/pdf,.pdf", multiple: true, hidden: true,
    onchange: async (ev) => { await corpusAddFiles(ev.target.files); ev.target.value = ""; } });
  const drop = el("div", { class: "drop active", tabindex: 0, onclick: () => input.click() },
    el("div", { class: "drop-title", text: "Σύρετε εδώ όσες εικόνες θέλετε (φωτογραφίες, screenshots ή PDF)" }),
    el("div", { class: "small", text: "ή πατήστε για επιλογή αρχείων · Ctrl+V για επικόλληση screenshot" }));
  drop.addEventListener("dragover", (ev) => { ev.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", async (ev) => { ev.preventDefault(); drop.classList.remove("over"); await corpusAddFiles(ev.dataTransfer.files); });
  return el("div", {}, drop, input);
}

// ταξινόμηση όπως τα ονόματα αρχείων (χωρίς την κατάληξη): 1.6.1 → 1.6.1.1 → 1.6.2 → 1.6.10
const stemOf = (n) => String(n || "").replace(/\.[A-Za-z0-9]{2,5}$/, "");
const natCmp = (a, b) => stemOf(a).localeCompare(stemOf(b), undefined, { numeric: true, sensitivity: "base" });

async function corpusAddFiles(files, fromPaste = false) {
  const all = Array.from(files || []);
  const pdfs = all.filter(isPdfFile);
  const list = all.filter((f) => f.type.startsWith("image/"));
  if (!list.length && !pdfs.length) { toast("Επιλέξτε αρχεία εικόνας (PNG, JPG) ή PDF.", true); return; }
  for (const f of pdfs) await addPdfFile(f);
  if (!list.length) return;
  if (!fromPaste) list.sort((a, b) => natCmp(a.name, b.name));
  const skipped = [];
  let added = 0;
  for (const f of list) {
    const dataUrl = await fileToDataUrl(f);
    const dup = S.corpus.images.find((im) => im.dataUrl === dataUrl);
    if (dup) { skipped.push(`«${f.name}» (ίδια με «${dup.name}»)`); continue; }
    try {
      const res = await api.add_image(dataUrl);
      if (!res.ok) { toast(`${f.name}: ${res.error}`, true); continue; }
      S.corpus.images.push({ id: newId(), imageId: res.image_id, dataUrl, name: fromPaste ? `Επικόλληση ${S.corpus.images.length + 1}` : f.name,
        time: f.lastModified || Date.now(), join: false, status: "new", error: null });
      added++;
    } catch (e) { toast(String(e.message || e), true); }
  }
  renderCorpusImages();
  if (skipped.length) toast(`Παραλείφθηκαν διπλές εικόνες: ${skipped.join(", ")}`, false, 7000);
  else if (added) toast(`Προστέθηκαν ${added} εικόνες.`);
}

function corpusGroups() {
  // ομάδες εικόνων: μια εικόνα με «Συνέχεια» ενώνεται με την προηγούμενη (σελίδες της ίδιας άσκησης)
  const groups = [];
  S.corpus.images.forEach((im, i) => {
    if (im.join && i > 0 && groups.length) groups[groups.length - 1].push(im);
    else groups.push([im]);
  });
  return groups;
}

/** Ομάδες ως διαστήματα [αρχή, τέλος): σελίδες δεμένες με «Συνέχεια» (ίδια άσκηση σε πολλές σελίδες). */
function corpusGroupRanges() {
  const imgs = S.corpus.images;
  if (imgs[0]) imgs[0].join = false;  // η πρώτη εικόνα δεν μπορεί να είναι «συνέχεια»
  const out = [];
  imgs.forEach((im, i) => {
    if (i > 0 && im.join && out.length) out[out.length - 1][1] = i + 1;
    else out.push([i, i + 1]);
  });
  return out;
}

function corpusMarkChanged(im) {
  // αλλαγή σε ομάδα που είχε μεταγραφεί: οι ασκήσεις της αφαιρούνται και ξαναδιαβάζονται
  const job = Object.values(S.corpus.jobs).find((j) => j.imageIds.includes(im.id));
  if (job) dropJob(job.id);
}

function dropJob(jobId) {
  const job = S.corpus.jobs[jobId];
  if (!job) return;
  S.corpus.exs = S.corpus.exs.filter((x) => x.jobId !== jobId);
  for (const im of S.corpus.images) if (job.imageIds.includes(im.id)) { im.status = "new"; im.error = null; }
  delete S.corpus.jobs[jobId];
}

function renderCorpusImages() {
  const C = S.corpus;
  $("#c-drop").replaceChildren(corpusDrop());
  const box = $("#c-thumbs");
  box.innerHTML = "";
  const badge = { new: ["", "νέα"], pending: ["run", "διαβάζεται…"], done: ["ok", "έτοιμη"], error: ["err", "σφάλμα"] };
  const ranges = corpusGroupRanges();
  const groupOf = [];
  ranges.forEach((r, g) => { for (let k = r[0]; k < r[1]; k++) groupOf[k] = g; });
  C.images.forEach((im, i) => {
    const g = groupOf[i], [gs, ge] = ranges[g], size = ge - gs;
    const what = size > 1 ? `όλη την ομάδα (${size} σελίδες)` : "την εικόνα";
    const b = badge[im.status] || badge.new;
    const job = Object.values(C.jobs).find((j) => j.imageIds[0] === im.id);
    const nEx = job ? C.exs.filter((x) => x.jobId === job.id).length : 0;
    box.append(el("div", { class: "cthumb" + (im.join ? " joined" : "") + (size > 1 ? " grp" : "") + (size > 1 && i === gs ? " grp-first" : "") },
      el("img", { src: im.dataUrl, alt: "", onclick: () => lightbox(im.dataUrl) }),
      size > 1 ? el("div", { class: "grp-tag", title: "Σελίδες δεμένες με «Συνέχεια»: διαβάζονται μαζί και μετακινούνται μαζί",
        text: `🔗 Ομάδα · σελ. ${i - gs + 1}/${size}` }) : null,
      el("div", { class: "nm", title: im.name, text: `${i + 1}. ${im.name}` }),
      el("div", { class: "tools" },
        el("span", { class: `badge ${b[0]}`, title: im.error || "", text: im.status === "done" && job ? `${nEx} ασκ.` : b[1] }),
        el("div", { class: "spacer" }),
        i > 0 ? el("button", { class: im.join ? "on" : "", title: "Η εικόνα είναι συνέχεια (επόμενη σελίδα) της προηγούμενης", text: "⤒ Συνέχεια",
          onclick: () => { corpusMarkChanged(im); corpusMarkChanged(C.images[i - 1]); im.join = !im.join; renderCorpus(); } }) : null,
        size > 1 ? el("button", { class: "in-grp", title: "Μέσα στην ομάδα: η σελίδα πάει μία θέση νωρίτερα (η ομάδα θα ξαναδιαβαστεί)",
          text: "‹", disabled: i === gs, onclick: () => corpusMoveInGroup(i, -1) }) : null,
        size > 1 ? el("button", { class: "in-grp", title: "Μέσα στην ομάδα: η σελίδα πάει μία θέση αργότερα (η ομάδα θα ξαναδιαβαστεί)",
          text: "›", disabled: i === ge - 1, onclick: () => corpusMoveInGroup(i, 1) }) : null,
        el("button", { title: `Πιο μπροστά: μετακινεί ${what}· προσπερνά ολόκληρες τις ομάδες`, text: "◀", disabled: g === 0,
          onclick: () => corpusMoveImage(i, -1) }),
        el("button", { title: `Πιο πίσω: μετακινεί ${what}· προσπερνά ολόκληρες τις ομάδες`, text: "▶", disabled: g === ranges.length - 1,
          onclick: () => corpusMoveImage(i, 1) }),
        el("button", { title: "Αφαίρεση", text: "✕", onclick: () => corpusRemoveImage(i) }))));
  });
  const n = C.images.length;
  const todo = C.images.filter((im) => im.status === "new" || im.status === "error").length;
  $("#c-img-total").textContent = n ? `${n} εικόνες` + (todo && todo < n ? ` · ${todo} για μεταγραφή` : "") : "";
  $("#c-sortbar").hidden = n < 2;
  $("#c-transcribe").disabled = !todo;
}

/** ◀ ▶: μετακινείται ολόκληρη η ομάδα της εικόνας (ή η μόνη εικόνα) κατά μία θέση ομάδας. Έτσι μια ομάδα
 *  «Συνέχεια» μένει ενωμένη με την ίδια σειρά σελίδων, και ό,τι μετακινείται δίπλα της την προσπερνά ολόκληρη.
 *  Οι ομάδες δεν αλλάζουν περιεχόμενο, άρα όσες έχουν ήδη διαβαστεί δεν χρειάζεται να ξαναδιαβαστούν. */
function corpusMoveImage(i, d) {
  const imgs = S.corpus.images;
  const ranges = corpusGroupRanges();
  const g = ranges.findIndex(([s, e]) => i >= s && i < e);
  const h = g + (d < 0 ? -1 : 1);
  if (g < 0 || h < 0 || h >= ranges.length) return;
  const [a, b] = d < 0 ? [ranges[h], ranges[g]] : [ranges[g], ranges[h]];  // a αμέσως πριν από b
  const first = imgs.slice(a[0], a[1]);
  const second = imgs.slice(b[0], b[1]);
  imgs.splice(a[0], b[1] - a[0], ...second, ...first);
  second[0].join = false;
  first[0].join = false;
  corpusResort();
  renderCorpus();
}

/** ‹ ›: αλλαγή σειράς σελίδας ΜΟΝΟ μέσα στην ομάδα της (ποτέ έξω από αυτήν). Οι σελίδες μιας ομάδας
 *  διαβάζονται μαζί και με τη σειρά τους, άρα αν η ομάδα έχει ήδη διαβαστεί, ξαναδιαβάζεται (με ερώτηση). */
function corpusMoveInGroup(i, d) {
  const C = S.corpus;
  const imgs = C.images;
  const r = corpusGroupRanges().find(([s, e]) => i >= s && i < e);
  const j = i + d;
  if (!r || r[1] - r[0] < 2 || j < r[0] || j >= r[1]) return;
  const job = Object.values(C.jobs).find((jb) => jb.imageIds.includes(imgs[i].id));
  if (job) {
    const nEx = C.exs.filter((x) => x.jobId === job.id).length;
    if (!confirm(`Η αλλαγή σειράς μέσα στην ομάδα σημαίνει ότι η ομάδα θα ξαναδιαβαστεί` +
      (nEx === 1 ? " (η άσκησή της θα αφαιρεθεί μέχρι τη νέα ανάγνωση και τυχόν διορθώσεις σας σε αυτήν θα χαθούν)"
        : nEx ? ` (οι ${nEx} ασκήσεις της θα αφαιρεθούν μέχρι τη νέα ανάγνωση και τυχόν διορθώσεις σας σε αυτές θα χαθούν)` : "") +
      `. Συνέχεια;`)) return;
    dropJob(job.id);
  }
  [imgs[i], imgs[j]] = [imgs[j], imgs[i]];
  for (let k = r[0]; k < r[1]; k++) imgs[k].join = k > r[0];  // η ομάδα μένει ίδια, μόνο η σειρά αλλάζει
  corpusResort();
  renderCorpus();
  if (job) toast("Η σειρά άλλαξε. Πατήστε «Μεταγραφή →» για να ξαναδιαβαστεί η ομάδα.", false, 5000);
}

function corpusRemoveImage(i) {
  const im = S.corpus.images[i];
  const job = Object.values(S.corpus.jobs).find((j) => j.imageIds.includes(im.id));
  if (job && S.corpus.exs.some((x) => x.jobId === job.id) && !confirm("Να αφαιρεθεί η εικόνα και οι ασκήσεις που διαβάστηκαν από αυτήν;")) return;
  if (job) dropJob(job.id);
  S.corpus.images.splice(i, 1);
  // αν αφαιρέθηκε η πρώτη σελίδα μιας ομάδας, η επόμενη γίνεται η αρχή της (δεν κολλά στην προηγούμενη ομάδα)
  if (S.corpus.images[i] && !im.join) S.corpus.images[i].join = false;
  if (S.corpus.images[0]) S.corpus.images[0].join = false;
  renderCorpus();
}

/** Ταξινόμηση με τις ομάδες «Συνέχεια» ενωμένες: ταξινομούνται οι ομάδες (με την πρώτη τους σελίδα)
 *  και οι σελίδες κάθε ομάδας κρατούν τη σειρά τους. */
function corpusSort(by) {
  const imgs = S.corpus.images;
  const cmp = by === "time" ? (a, b) => a.time - b.time || natCmp(a.name, b.name) : (a, b) => natCmp(a.name, b.name);
  const groups = corpusGroupRanges().map(([s, e]) => imgs.slice(s, e));
  groups.sort((x, y) => cmp(x[0], y[0]));
  imgs.splice(0, imgs.length, ...groups.flat());
  for (const grp of groups) grp[0].join = false;
  corpusResort();
  renderCorpus();
  if (groups.some((grp) => grp.length > 1)) toast("Οι ομάδες «Συνέχεια» έμειναν ενωμένες, με την ίδια σειρά σελίδων.");
}

/** Οι ασκήσεις ακολουθούν τη σειρά των εικόνων (εκτός αν έχουν μετακινηθεί με το χέρι). */
function corpusResort() {
  if (S.corpus.manual) return;
  const pos = {};
  S.corpus.images.forEach((im, i) => { pos[im.id] = i; });
  const jobPos = (x) => pos[S.corpus.jobs[x.jobId]?.imageIds[0]] ?? 1e9;
  S.corpus.exs.sort((a, b) => jobPos(a) - jobPos(b) || a.k - b.k);
}

/* ---------- μεταγραφή */
async function corpusTranscribe(groups) {
  const C = S.corpus;
  if (!groups) groups = corpusGroups().filter((g) => g.some((im) => im.status === "new" || im.status === "error"));
  if (!groups.length) { toast("Δεν υπάρχουν νέες εικόνες για μεταγραφή."); return; }
  const jobs = [];
  for (const g of groups) {
    const old = Object.values(C.jobs).find((j) => j.imageIds[0] === g[0].id);
    if (old) dropJob(old.id);
    const id = newId();
    C.jobs[id] = { id, imageIds: g.map((im) => im.id) };
    g.forEach((im) => { im.status = "pending"; im.error = null; });
    jobs.push({ job_id: id, image_ids: g.map((im) => im.imageId) });
  }
  renderCorpus();
  try {
    const res = await api.corpus_transcribe(jobs);
    if (!res.ok) throw new Error(res.error);
  } catch (e) {
    for (const g of groups) g.forEach((im) => { im.status = "error"; im.error = String(e.message || e); });
    toast(String(e.message || e), true, 7000);
  }
  renderCorpus();
}

function onCorpusTranscribed({ job_id, result, error }) {
  const C = S.corpus;
  const job = C.jobs[job_id];
  if (!job) return;
  const imgs = C.images.filter((im) => job.imageIds.includes(im.id));
  if (error || !result) {
    imgs.forEach((im) => { im.status = "error"; im.error = error || "Άγνωστο σφάλμα."; });
    toast(`Σφάλμα στην εικόνα «${imgs[0]?.name || ""}»: ${error}`, true, 7000);
  } else {
    imgs.forEach((im) => { im.status = "done"; });
    result.forEach((data, k) => {
      const fr = data.figure_render || null;
      delete data.figure_render;
      C.exs.push({ id: newId(), jobId: job_id, k, data, fig: data.figure ? { mode: "redraw", render: fr, crop: null } : null });
    });
    if (!result.length) toast(`Στην εικόνα «${imgs[0]?.name || ""}» δεν βρέθηκε άσκηση.`, true, 6000);
    corpusResort();
    corpusCheckDuplicates();
  }
  renderCorpusImages();
  renderCorpusReview();
}

/* ---------- έλεγχος */
function renderCorpusReview() {
  const C = S.corpus;
  const card = $("#c-review-card");
  const pending = C.images.filter((im) => im.status === "pending").length;
  card.hidden = !C.exs.length && !pending;
  const box = $("#c-review");
  box.innerHTML = "";
  C.exs.forEach((ex, i) => box.append(renderCorpusExercise(ex, i)));
  if (pending) box.append(el("div", { class: "pending" }, el("div", { class: "spinner" }),
    `Ο Claude διαβάζει ${pending} ${pending === 1 ? "εικόνα" : "εικόνες"}… (συνήθως 10–60 δευτερόλεπτα η καθεμία, παράλληλα)`));
  const sec = corpusSection();
  $("#c-review-title").textContent = `3. Έλεγχος — ${C.exs.length} ${C.exs.length === 1 ? "άσκηση" : "ασκήσεις"}`;
  const placed = C.exs.filter((x) => x.place).length;
  $("#c-ex-total").textContent = !C.exs.length ? "" : placed
    ? `${C.exs.length - placed} στο τέλος ${sec ? `του «${sec.display}»` : "της ενότητας"} · ${placed} σε θέση που ορίσατε (📍)`
    : (sec ? `${corpusExLabel(0)} – ${corpusExLabel(C.exs.length - 1)} στο «${sec.display}»` : "");
  $("#c-insert").textContent = placed === C.exs.length && placed ? "Εισαγωγή στο Word" : (sec ? `Εισαγωγή στο «${sec.display}»` : "Εισαγωγή στο Word");
  renderPlanNote();
  renderDupStatus();
}

function corpusFigureBox(ex) {
  const st = ex.fig;
  const fig = ex.data.figure;
  const job = S.corpus.jobs[ex.jobId];
  const imgs = S.corpus.images.filter((im) => job?.imageIds.includes(im.id));
  const page = imgs[Math.min(Math.max(0, fig.crop_page || 0), imgs.length - 1)];
  const box = el("div", { class: "figure-box" });
  const current = st.mode === "original" ? st.crop : st.mode === "redraw" ? st.render : null;
  const rerender = () => { const c = document.getElementById(`cx-${ex.id}`); if (c) c.replaceWith(renderCorpusExercise(ex, S.corpus.exs.indexOf(ex))); };
  const radio = (value, text) => el("label", {},
    el("input", { type: "radio", name: `cfig-${ex.id}`, value, checked: st.mode === value, onchange: async () => {
      st.mode = value;
      if (value === "original" && !st.crop && page) st.crop = await api.crop_figure(ex.id, page.imageId, fig.crop);
      rerender();
    } }), text);
  box.append(el("div", { class: "row" }, el("span", { class: "field-label", text: "Σχήμα" }),
    fig.confidence === "low" ? el("span", { class: "badge err", text: "αβέβαιη ανάγνωση — ελέγξτε το" }) : null));
  box.append(el("div", { class: "radio-row" }, radio("redraw", "Επανασχεδιασμένο"), radio("original", "Πρωτότυπη εικόνα (περικοπή)"), radio("none", "Χωρίς σχήμα")));
  if (current && current.png_data) box.append(el("img", { class: "fig-img", src: current.png_data, onclick: () => lightbox(current.png_data) }));
  if (current && current.error) box.append(el("div", { class: "fig-warn", text: current.error }));
  const sizeRow = figLabelRow(fig, st, ex.id, rerender);
  if (sizeRow) box.append(sizeRow);
  box.append(el("div", { class: "small muted", text: fig.description }));
  const chk = figureCheckInfo(fig);
  if (chk) box.append(chk);
  box.append(figureToolsRow(ex, imgs.map((im) => im.imageId), rerender));
  return box;
}

function renderCorpusExercise(ex, i) {
  const C = S.corpus;
  const job = C.jobs[ex.jobId];
  const imgs = C.images.filter((im) => job?.imageIds.includes(im.id));
  const orig = el("div", { class: "orig" },
    ...imgs.map((im) => el("div", { class: "orig-page" },
      el("div", { class: "page-lab", text: im.name }),
      el("img", { src: im.dataUrl, alt: im.name, onclick: () => lightbox(im.dataUrl) }))),
    el("div", { class: "src small muted", text: ex.data.source_label ? `Άσκηση βιβλίου: ${ex.data.source_label}` : "" }));
  const editor = el("div", { class: "editor" });
  const card = el("div", { class: "ex", id: `cx-${ex.id}` }, orig, editor);
  const d = ex.data;
  const rerender = () => { const c = document.getElementById(`cx-${ex.id}`); if (c) c.replaceWith(renderCorpusExercise(ex, C.exs.indexOf(ex))); };
  const move = (dd) => {
    const j = i + dd;
    if (j < 0 || j >= C.exs.length) return;
    [C.exs[i], C.exs[j]] = [C.exs[j], C.exs[i]];
    C.manual = true;
    renderCorpusReview();
  };
  editor.append(el("div", { class: "cx-head" },
    el("span", { class: "cx-num", text: corpusExLabel(i) }),
    el("button", { class: "btn small ghost place-btn" + (ex.place ? " set" : ""), title: "Πού θα μπει η άσκηση στο Word",
      text: ex.place ? `📍 ${ex.place.side === "after" ? "μετά την" : "πριν από την"} ${ex.place.label}` : "📍 Στο τέλος της ενότητας",
      onclick: () => openPlaceForNew(ex) }),
    el("div", { class: "spacer" }),
    el("button", { class: "icon-mini", title: "Άσκηση πιο πάνω", text: "▲", onclick: () => move(-1) }),
    el("button", { class: "icon-mini", title: "Άσκηση πιο κάτω", text: "▼", onclick: () => move(1) }),
    el("button", { class: "icon-mini danger", title: "Να μη μπει αυτή η άσκηση", text: "✕", onclick: () => {
      if (!confirm("Να αφαιρεθεί αυτή η άσκηση από την εισαγωγή;")) return;
      corpusDropExercise(ex);
    } })));
  const dups = dupMatches(ex);
  if (dups.length) {
    const top = dups[0].level;
    card.classList.add(`dup-${top}`);
    editor.querySelector(".cx-num").after(el("span", { class: `dup-badge ${top}`, text: DUP_TEXT[top] }));
    const box = el("div", { class: `dup-box ${top}` });
    for (const m of dups) {
      box.append(el("div", { class: "dup-row" },
        el("span", { class: "dot", text: DUP_DOT[m.level] }),
        el("span", { html: dupDescribe(m) }),
        el("button", { class: "btn small", text: "Σύγκριση", onclick: () => dupCompare(ex, m) }),
        m.kind === "doc" && m.fp ? el("button", { class: "btn small ghost", title: `Να μπει αμέσως μετά την ${m.label}, ώστε να είναι μαζί`,
          text: "📍 Δίπλα της", onclick: () => { ex.place = { side: "after", fp: m.fp, label: m.label, section: m.section }; renderCorpusReview(); } }) : null));
    }
    if (top === "same") box.append(el("div", { class: "small muted", text: "Η άσκηση θα μπει κανονικά στο Word. Αν δεν τη θέλετε, πατήστε «Σύγκριση» → «Να μη μπει» ή το ✕ πάνω δεξιά." }));
    editor.append(box);
  }
  if (d.uncertain && d.uncertain.length) {
    editor.append(el("div", { class: "notes" }, el("b", { text: "Χρειάζεται έλεγχος:" }),
      el("ul", {}, ...d.uncertain.map((u) => el("li", { text: u })))));
  }
  editor.append(el("div", { class: "field-label", text: "Εκφώνηση" }), textPair(d.stem, (v) => { d.stem = v; }, {}, 3));
  if (d.figure && ex.fig) editor.append(corpusFigureBox(ex));
  else {
    const job = C.jobs[ex.jobId];
    const ids = C.images.filter((im) => job?.imageIds.includes(im.id)).map((im) => im.imageId);
    if (ids.length) editor.append(noFigureBox(ex, ids, rerender));
  }
  editor.append(el("div", { class: "field-label", text: "Ερωτήματα" }));
  editor.append(renderItems(d.items, 1, corpusItemLabel, [], {}, rerender));
  editor.append(el("button", { class: "btn small ghost", text: "+ ερώτημα", onclick: () => { d.items.push({ label: "", text: "", choices: null, items: [] }); rerender(); } }));
  if (d.closing) editor.append(el("div", { class: "field-label", text: "Κείμενο στο τέλος" }), textPair(d.closing, (v) => { d.closing = v; }, {}));
  editor.append(el("div", { class: "row" }, el("div", { class: "spacer" }),
    el("button", { class: "btn small ghost", text: "↻ Ξαναδιάβασε την εικόνα", onclick: () => {
      if (!confirm("Η εικόνα θα διαβαστεί από την αρχή και οι αλλαγές σε όλες τις ασκήσεις της θα χαθούν. Συνέχεια;")) return;
      corpusTranscribe([imgs]);
    } })));
  return card;
}

/* ---------- έλεγχος για ίδιες ασκήσεις στο έγγραφο */
const DUP_TEXT = { same: "Υπάρχει ήδη", part: "Ίδιο ερώτημα", numbers: "Ίδια, άλλα νούμερα", similar: "Παρόμοια" };
const DUP_DOT = { same: "🔴", part: "🟠", numbers: "🟠", similar: "🟡" };
let dupTimer = null;
let dupSeq = 0;

function dupMatches(ex) { return S.corpus.dup?.matches?.[ex.id] || []; }

function corpusDropExercise(ex) {
  const C = S.corpus;
  const i = C.exs.indexOf(ex);
  if (i < 0) return;
  C.exs.splice(i, 1);
  renderCorpusReview();
  renderCorpusImages();
  corpusCheckDuplicates();
}

function corpusCheckDuplicates(delay = 400) {
  clearTimeout(dupTimer);
  dupTimer = setTimeout(runDupCheck, delay);
}

async function runDupCheck() {
  const C = S.corpus;
  const seq = ++dupSeq;
  const ids = C.exs.map((x) => x.id);
  if (!C.path || !ids.length) { C.dup = null; renderDupStatus(); return; }
  C.dup = { ...(C.dup || {}), state: "checking" };
  renderDupStatus();
  let res;
  try {
    res = await api.corpus_check(C.path, C.exs.map((x) => x.data), $("#c-dup-sens").value);
  } catch (e) {
    res = { ok: false, error: String(e.message || e) };
  }
  if (seq !== dupSeq) return; // στο μεταξύ ξεκίνησε νεότερος έλεγχος
  const matches = {};
  if (res.ok) {
    ids.forEach((id, i) => {
      matches[id] = (res.matches[i] || []).map((m) => (m.kind === "batch" ? { ...m, otherId: ids[m.index] } : m))
        .filter((m) => m.kind !== "batch" || C.exs.some((x) => x.id === m.otherId));
    });
  }
  C.dup = { state: res.ok ? (res.off ? "off" : "done") : "error", error: res.error, matches,
    existing: res.existing || 0, unread: res.unread || 0 };
  renderCorpusReview();
}

function renderDupStatus() {
  const C = S.corpus;
  const st = $("#c-dup-status");
  if (!st) return;
  const d = C.dup;
  st.innerHTML = "";
  if (!C.path) { st.textContent = "Ο έλεγχος για ίδιες ασκήσεις γίνεται όταν επιλεγεί το αρχείο Word (βήμα 1)."; return; }
  if (!d) { st.textContent = ""; return; }
  if (d.state === "checking") { st.append(el("span", { class: "spinner" }), `Έλεγχος αν οι ασκήσεις υπάρχουν ήδη στο «${C.name}»…`); return; }
  if (d.state === "off") { st.textContent = "Ο έλεγχος για ίδιες ασκήσεις είναι ανενεργός."; return; }
  if (d.state === "error") { st.append(el("span", { style: "color:var(--warn)", text: `Ο έλεγχος για ίδιες ασκήσεις δεν έγινε: ${d.error || ""}` })); return; }
  const tops = C.exs.map((x) => (dupMatches(x)[0] || {}).level).filter(Boolean);
  const nSame = tops.filter((t) => t === "same").length;
  const nOther = tops.length - nSame;
  const checked = `ελέγχθηκαν ${d.existing} ασκήσεις του εγγράφου`;
  if (!tops.length) {
    st.append(el("span", { html: `✓ Καμία νέα άσκηση δεν υπάρχει ήδη στο έγγραφο <span class="muted">(${checked})</span>` }));
  } else {
    const parts = [];
    if (nSame) parts.push(`<b class="same">🔴 ${nSame} ${nSame === 1 ? "υπάρχει" : "υπάρχουν"} ήδη</b>`);
    if (nOther) parts.push(`<b class="warn">${nOther} με ομοιότητα</b>`);
    st.append(el("span", { html: `${parts.join(" · ")} — δείτε τις σημειωμένες ασκήσεις <span class="muted">(${checked})</span>` }));
  }
  if (d.unread) st.append(el("span", { class: "muted", text: ` · ${d.unread} εξισώσεις του εγγράφου δεν διαβάστηκαν (παλιός τύπος εξισώσεων)` }));
}

function dupWhere(m) {
  if (m.kind === "doc") return `<b>${escapeHtml(m.label || "(χωρίς αριθμό)")}</b> <span class="muted">(${escapeHtml(m.section || "")})</span>`;
  const C = S.corpus;
  const j = C.exs.findIndex((x) => x.id === m.otherId);
  const job = j >= 0 ? C.jobs[C.exs[j].jobId] : null;
  const img = job ? C.images.find((im) => im.id === job.imageIds[0]) : null;
  return `τη νέα άσκηση <b>${escapeHtml(j >= 0 ? corpusExLabel(j) : "")}</b>` + (img ? ` <span class="muted">(εικόνα «${escapeHtml(img.name)}»)</span>` : "");
}

function dupDescribe(m) {
  const pct = Math.floor((m.score || 0) * 100);
  const where = dupWhere(m);
  if (m.level === "same") {
    const lead = m.kind === "doc" ? "Υπάρχει ήδη στο έγγραφο:" : "Ίδια με";
    return `${lead} ${where}` + (pct < 100 ? ` — ίδια κατά ${pct}%` : "");
  }
  if (m.level === "part") {
    const path = String(m.part || "0").split(".").map(Number);
    return `Το ερώτημα <b>${escapeHtml(corpusItemLabel(path))}</b> υπάρχει ήδη στην ${where}` + (m.other_part ? `, ερώτημα <b>${escapeHtml(m.other_part)}</b>` : "");
  }
  if (m.level === "numbers") return `Ίδια διατύπωση με άλλα νούμερα: ${where}`;
  return `Παρόμοια (${pct}%) με ${where}`;
}

function dupPartsFromData(d) {
  const box = el("div");
  box.append(el("div", { class: "dp l0" }, el("div", { html: mdToHtml(d.stem || "") })));
  const walk = (items, path) => (items || []).forEach((it, k) => {
    const p = [...path, k];
    box.append(el("div", { class: `dp l${Math.min(p.length, 2)}` }, el("span", { class: "lab", text: corpusItemLabel(p) }),
      el("div", { html: mdToHtml(it.text || "") })));
    walk(it.items, p);
  });
  walk(d.items, []);
  if (d.closing) box.append(el("div", { class: "dp l0" }, el("div", { html: mdToHtml(d.closing) })));
  return box;
}

function dupPartsFromDoc(parts) {
  const box = el("div");
  for (const p of parts || []) {
    box.append(el("div", { class: `dp l${Math.min(p.level || 0, 2)}` }, p.label ? el("span", { class: "lab", text: p.label }) : null,
      el("div", { html: mdToHtml(p.md || "") })));
  }
  return box;
}

function dupCompare(ex, m) {
  const C = S.corpus;
  const i = C.exs.indexOf(ex);
  $("#dup-title").textContent = `${DUP_DOT[m.level]} ${DUP_TEXT[m.level]}` + (m.score < 1 ? ` (ομοιότητα ${Math.floor(m.score * 100)}%)` : "");
  if (m.kind === "doc") {
    $("#dup-left-head").textContent = `Στο έγγραφο: ${m.label} — ${m.section}`;
    $("#dup-left").replaceChildren(dupPartsFromDoc(m.parts));
  } else {
    const other = C.exs.find((x) => x.id === m.otherId);
    $("#dup-left-head").textContent = `Νέα άσκηση ${corpusExLabel(C.exs.indexOf(other))}`;
    $("#dup-left").replaceChildren(other ? dupPartsFromData(other.data) : el("div"));
  }
  $("#dup-right-head").textContent = `Νέα άσκηση ${corpusExLabel(i)} (από τη φωτογραφία)`;
  $("#dup-right").replaceChildren(dupPartsFromData(ex.data));
  const diffs = $("#dup-diffs");
  diffs.innerHTML = "";
  if (m.diffs && m.diffs.length) {
    diffs.append(el("b", { text: "Πού διαφέρουν:" }));
    const t = el("table", {}, el("tr", {}, el("th", { text: m.kind === "doc" ? "Στο έγγραφο" : "Η άλλη νέα άσκηση" }), el("th", { text: "Στη νέα άσκηση" })));
    for (const d of m.diffs) t.append(el("tr", {}, el("td", { class: "old", text: d.old || "—" }), el("td", { class: "new", text: d.new || "—" })));
    diffs.append(t);
  } else if (m.level === "same") {
    diffs.append(el("div", { class: "muted", text: "Το κείμενο και οι εξισώσεις ταυτίζονται." }));
  } else if (m.level === "part") {
    diffs.append(el("div", { class: "muted", text: `Το ερώτημα ${corpusItemLabel(String(m.part || "0").split(".").map(Number))} της νέας άσκησης ταυτίζεται με το ερώτημα ${m.other_part || ""} της ${m.label}.` }));
  }
  $("#dup-skip").hidden = false;
  $("#dup-skip").onclick = () => { $("#dup").hidden = true; corpusDropExercise(ex); toast(`Η άσκηση αφαιρέθηκε από την εισαγωγή.`); };
  $("#dup").hidden = false;
}

/* ---------- έλεγχος ολόκληρου αρχείου (συλλογή και, προαιρετικά, δεύτερο αρχείο) */
S.audit = { path: "", other: null, res: null, running: false };

function auditOpen() {
  if (!S.corpus.path) { toast("Επιλέξτε πρώτα το αρχείο Word της συλλογής.", true); return; }
  const card = $("#c-audit-card");
  card.hidden = false;
  if (S.audit.path !== S.corpus.path) Object.assign(S.audit, { path: S.corpus.path, res: null });
  const st = S.app?.settings?.dup_check;
  if (!S.audit.res) $("#c-audit-sens").value = ["low", "normal", "high"].includes(st) ? st : "normal";
  renderAudit();
  card.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function auditChooseOther() {
  const r = await api.corpus_choose_other();
  if (!r.ok) return;
  S.audit.other = { path: r.path, name: r.name };
  S.audit.res = null;
  renderAudit();
}

async function auditRun() {
  const A = S.audit;
  if (!S.corpus.path || A.running) return;
  A.running = true;
  A.res = null;
  renderAudit();
  let res;
  try {
    res = await api.corpus_audit(S.corpus.path, A.other ? A.other.path : null, $("#c-audit-sens").value);
  } catch (e) {
    res = { ok: false, error: String(e.message || e) };
  }
  A.running = false;
  A.res = res;
  A.path = S.corpus.path;
  if (res.ok) {
    $("#c-audit-f-level").value = "";
    $("#c-audit-f-scope").value = "";
    $("#c-audit-f-sec").value = "";
  }
  renderAudit();
}

function auditEx(pr, side) {
  const r = S.audit.res;
  if (side === "a") return r.a.exercises[pr.a];
  return (pr.cross ? r.b : r.a).exercises[pr.b];
}

function auditWhere(pr, side) {
  const ex = auditEx(pr, side);
  const file = side === "b" && pr.cross ? `«${S.audit.res.b.name}» ` : "";
  return `${escapeHtml(file)}<b>${escapeHtml(ex?.label || "(χωρίς αριθμό)")}</b> <span class="where">(${escapeHtml(ex?.section || "")})</span>`;
}

function auditFiltered() {
  const r = S.audit.res;
  if (!r || !r.ok) return [];
  const lv = $("#c-audit-f-level").value;
  const sc = $("#c-audit-f-scope").value;
  const sec = $("#c-audit-f-sec").value;
  return r.pairs.filter((pr) => (!lv || pr.level === lv)
    && (!sc || (sc === "cross") === !!pr.cross)
    && (!sec || auditEx(pr, "a")?.section === sec || (!pr.cross && auditEx(pr, "b")?.section === sec)));
}

const AUDIT_SHOW = 300;

function renderAudit() {
  const A = S.audit;
  if (!$("#c-audit-card")) return;
  $("#c-audit-a").textContent = S.corpus.path ? S.corpus.name || S.corpus.path : "(δεν έχει επιλεγεί αρχείο)";
  $("#c-audit-b").hidden = !A.other;
  $("#c-audit-b-clear").hidden = !A.other;
  $("#c-audit-b").textContent = A.other ? A.other.name : "";
  $("#c-audit-other").textContent = A.other ? "Άλλο αρχείο…" : "Επιλογή αρχείου… (προαιρετικά)";
  $("#c-audit-run").disabled = A.running || !S.corpus.path;
  const bar = $("#c-audit-status-bar");
  const st = $("#c-audit-status");
  const list = $("#c-audit-list");
  const filters = $("#c-audit-filters");
  list.innerHTML = "";
  st.innerHTML = "";
  $("#c-audit-numbering")?.remove();
  if (A.running) {
    bar.hidden = false;
    filters.hidden = true;
    st.append(el("span", { class: "spinner" }), "Διαβάζονται και συγκρίνονται όλες οι ασκήσεις… (λίγα δευτερόλεπτα)");
    return;
  }
  const r = A.res;
  if (!r) { bar.hidden = true; filters.hidden = true; return; }
  bar.hidden = false;
  if (!r.ok) { filters.hidden = true; st.append(el("span", { style: "color:var(--warn)", text: r.error || "Ο έλεγχος δεν έγινε." })); return; }
  const numbering = r.numbering && r.numbering.length
    ? el("div", { class: "small", style: "color:var(--warn)", text: `⚠ Αρίθμηση: ${r.numbering.length} σημεία όπου δεν είναι συνεχόμενη (π.χ. ${r.numbering.slice(0, 2).join("· ")}). Ελέγξτε τα στο Word.` })
    : el("div", { class: "small muted", text: "✓ Αρίθμηση: συνεχόμενη σε όλες τις ενότητες." });
  numbering.id = "c-audit-numbering";
  bar.append(numbering);
  const n = (lv, cross) => r.pairs.filter((p) => p.level === lv && (cross === undefined || !!p.cross === cross)).length;
  const files = `${r.a.exercises.length} ασκήσεις στη συλλογή` + (r.b ? `, ${r.b.exercises.length} στο «${r.b.name}»` : "");
  if (!r.pairs.length) {
    st.append(el("span", { html: `✓ Δεν βρέθηκαν ίδιες ή παρόμοιες ασκήσεις <span class="muted">(${escapeHtml(files)})</span>` }));
    filters.hidden = true;
    return;
  }
  const parts = [];
  if (n("same")) parts.push(`<b class="same">🔴 ${n("same")} ίδιες</b>`);
  if (n("numbers")) parts.push(`<b class="warn">🟠 ${n("numbers")} με άλλα νούμερα</b>`);
  if (n("part")) parts.push(`<b class="warn">🟠 ${n("part")} με ίδιο ερώτημα</b>`);
  if (n("similar")) parts.push(`🟡 ${n("similar")} παρόμοιες`);
  let html = `Βρέθηκαν ${r.total} ζεύγη: ${parts.join(" · ")}`;
  if (r.b) {
    const cross = r.pairs.filter((p) => p.cross).length;
    html += ` — <b>${cross}</b> από το «${escapeHtml(r.b.name)}» υπάρχουν/μοιάζουν με ασκήσεις της συλλογής`;
  }
  html += ` <span class="muted">(${escapeHtml(files)})</span>`;
  st.append(el("span", { html }));
  if (r.a.unread || r.b?.unread) st.append(el("span", { class: "muted", text: ` · ${(r.a.unread || 0) + (r.b?.unread || 0)} εξισώσεις δεν διαβάστηκαν (παλιός τύπος)` }));
  if (r.truncated) st.append(el("span", { class: "muted", text: ` · εμφανίζονται τα ${r.pairs.length} σοβαρότερα· δοκιμάστε μικρότερη αυστηρότητα` }));
  // φίλτρα
  filters.hidden = false;
  $("#c-audit-f-scope").hidden = !r.b;
  const secSel = $("#c-audit-f-sec");
  const cur = secSel.value;
  const secs = [...new Set(r.pairs.flatMap((p) => [auditEx(p, "a")?.section, p.cross ? null : auditEx(p, "b")?.section]).filter(Boolean))];
  secSel.innerHTML = "";
  secSel.append(el("option", { value: "", text: "Όλες οι ενότητες" }), ...secs.map((x) => el("option", { value: x, text: x })));
  secSel.value = secs.includes(cur) ? cur : "";
  const shown = auditFiltered();
  for (const pr of shown.slice(0, AUDIT_SHOW)) {
    const pct = Math.floor(pr.score * 100);
    let text;
    if (pr.level === "part") {
      text = `Ερώτημα <b>${escapeHtml(pr.part || "")}</b> της ${auditWhere(pr, "b")} = ερώτημα <b>${escapeHtml(pr.other_part || "")}</b> της ${auditWhere(pr, "a")}`;
    } else {
      text = pr.cross ? `${auditWhere(pr, "b")} ↔ συλλογή: ${auditWhere(pr, "a")}` : `${auditWhere(pr, "a")} ↔ ${auditWhere(pr, "b")}`;
    }
    const lab = { same: "Ίδιες", numbers: "Ίδιες, άλλα νούμερα", part: "Ίδιο ερώτημα", similar: `Παρόμοιες ${pct}%` }[pr.level];
    list.append(el("div", { class: `audit-row ${pr.level}` },
      el("span", { class: "dot", text: DUP_DOT[pr.level] }),
      el("span", { class: `dup-badge ${pr.level}`, text: lab + (pr.level === "same" && pct < 100 ? ` ${pct}%` : "") }),
      el("span", { class: "apair", html: text }),
      pr.cross ? el("span", { class: "tag", text: "με το άλλο αρχείο" }) : null,
      el("button", { class: "btn small", text: "Σύγκριση", onclick: () => auditCompare(pr) }),
      pr.cross ? null : (pr.b === pr.a + 1 && auditEx(pr, "a")?.section === auditEx(pr, "b")?.section)
        ? el("span", { class: "tag", text: "ήδη η μία μετά την άλλη" })
        : el("button", { class: "btn small ghost", text: "↕ Μετακίνηση…", title: "Να μπουν η μία δίπλα στην άλλη",
          onclick: () => openPlaceForMove([auditEx(pr, "a"), auditEx(pr, "b")], 1) })));
  }
  if (!shown.length) list.append(el("div", { class: "muted small", text: "Καμία με αυτά τα φίλτρα." }));
  if (shown.length > AUDIT_SHOW) list.append(el("div", { class: "muted small", text: `…και ${shown.length - AUDIT_SHOW} ακόμα. Περιορίστε με τα φίλτρα.` }));
}

function auditCompare(pr) {
  const r = S.audit.res;
  const a = auditEx(pr, "a");
  const b = auditEx(pr, "b");
  const pct = Math.floor(pr.score * 100);
  $("#dup-title").textContent = `${DUP_DOT[pr.level]} ${DUP_TEXT[pr.level]}` + (pr.score < 1 ? ` (ομοιότητα ${pct}%)` : "");
  const nameB = pr.cross ? `«${r.b.name}»` : "Συλλογή";
  $("#dup-left-head").textContent = `Συλλογή: ${a.label} — ${a.section}`;
  $("#dup-left").replaceChildren(dupPartsFromDoc(a.parts));
  $("#dup-right-head").textContent = `${nameB}: ${b.label} — ${b.section}`;
  $("#dup-right").replaceChildren(dupPartsFromDoc(b.parts));
  const diffs = $("#dup-diffs");
  diffs.innerHTML = "";
  if (pr.diffs && pr.diffs.length) {
    diffs.append(el("b", { text: "Πού διαφέρουν:" }));
    const t = el("table", {}, el("tr", {}, el("th", { text: `Αριστερά (${a.label})` }), el("th", { text: `Δεξιά (${b.label})` })));
    for (const d of pr.diffs) t.append(el("tr", {}, el("td", { class: "old", text: d.old || "—" }), el("td", { class: "new", text: d.new || "—" })));
    diffs.append(t);
  } else if (pr.level === "part") {
    diffs.append(el("div", { class: "muted", text: `Το ερώτημα ${pr.part} της ${b.label} ταυτίζεται με το ερώτημα ${pr.other_part} της ${a.label}.` }));
  } else {
    diffs.append(el("div", { class: "muted", text: "Το κείμενο και οι εξισώσεις ταυτίζονται." }));
  }
  $("#dup-skip").hidden = true;
  $("#dup").hidden = false;
}

/* ---------- θέση νέων ασκήσεων και μετακίνηση ασκήσεων μέσα στο Word */
async function loadDocEx() {
  const C = S.corpus;
  if (!C.path) { C.docEx = null; return; }
  let res;
  try { res = await api.corpus_exercises(C.path); } catch (e) { res = { ok: false }; }
  if (!res.ok) { C.docEx = null; return; }
  C.docEx = res;
  // οι θέσεις που έχουν οριστεί ακολουθούν την άσκηση (με το περιεχόμενό της), ακόμα κι αν άλλαξε ο αριθμός της
  let lost = 0;
  for (const ex of C.exs) {
    if (!ex.place) continue;
    const f = res.exercises.filter((e) => e.fp === ex.place.fp);
    const hit = f.find((e) => e.label === ex.place.label) || f[0];
    if (hit) Object.assign(ex.place, { label: hit.label, section: hit.section });
    else { ex.place = null; lost += 1; }
  }
  if (lost) toast(`${lost} θέσεις που είχατε ορίσει δεν ισχύουν πια (άλλαξε το αρχείο) και οι ασκήσεις πάνε στο τέλος της ενότητας.`, true, 8000);
  renderCorpusReview();
}

function secInfo(display) { return S.corpus.outline.find((h) => h.display === display) || null; }

function labelAt(display, k) {
  const o = secInfo(display);
  return (o?.pattern || "%1.").replace("%1", fmtNum((o?.start || 1) + k, o?.formats?.[0]));
}

/** Πώς θα είναι η αρίθμηση μετά την εισαγωγή/μετακίνηση (προεπισκόπηση· ο τελικός έλεγχος γίνεται στο Word). */
function planLayout(newItems, move) {
  const D = S.corpus.docEx;
  if (!D) return null;
  const secs = new Map();
  for (const h of S.corpus.outline) if (!secs.has(h.display)) secs.set(h.display, []);
  for (const e of D.exercises) { if (!secs.has(e.section)) secs.set(e.section, []); secs.get(e.section).push({ old: e.label, fp: e.fp, doc: true }); }
  const find = (fp, label) => {
    for (const [sec, arr] of secs) {
      let i = arr.findIndex((x) => x.doc && x.fp === fp && x.old === label);
      if (i < 0) i = arr.findIndex((x) => x.doc && x.fp === fp);
      if (i >= 0) return [sec, arr, i];
    }
    return null;
  };
  let moved = null;
  if (move) {
    const f = find(move.fp, move.label);
    const a = find(move.anchor.fp, move.anchor.label);
    if (f && a && f[2] !== undefined) {
      moved = f[1].splice(f[2], 1)[0];
      moved.moved = true;
      const a2 = find(move.anchor.fp, move.anchor.label);
      a2[1].splice(a2[2] + (move.side === "after" ? 1 : 0), 0, moved);
    }
  }
  const labels = [];
  newItems.forEach((n, k) => {
    const item = { nw: k };
    if (n.place) {
      const f = find(n.place.fp, n.place.label);
      if (!f) return;
      const key = `${n.place.side}|${n.place.fp}`;
      item.key = key;
      let i = f[2] + (n.place.side === "after" ? 1 : 0);
      if (n.place.side === "after") while (i < f[1].length && f[1][i].key === key) i += 1;
      f[1].splice(i, 0, item);
    } else if (n.section) {
      if (!secs.has(n.section)) secs.set(n.section, []);
      secs.get(n.section).push(item);
    }
  });
  const changes = [];
  for (const [sec, arr] of secs) {
    arr.forEach((it, k) => {
      it.label = labelAt(sec, k);
      it.sec = sec;
      if (it.nw !== undefined) labels[it.nw] = it.label;
      else if (it.doc && !it.moved && it.old !== it.label) changes.push(it);
    });
  }
  return { labels, notes: compressChanges(changes), moved };
}

function compressChanges(list) {
  const num = (l) => { const m = /(\d+)\.?$/.exec(l || ""); return m ? +m[1] : NaN; };
  const out = [];
  let run = [];
  const flush = () => {
    if (!run.length) return;
    const a = run[0], b = run[run.length - 1];
    out.push(run.length === 1 ? `${a.old} → ${a.label}` : `${a.old}–${b.old} → ${a.label}–${b.label} (${run.length} ασκήσεις)`);
  };
  for (const c of list) {
    const p = run[run.length - 1];
    if (p && p.sec === c.sec && num(c.old) === num(p.old) + 1 && num(c.label) === num(p.label) + 1) run.push(c);
    else { flush(); run = [c]; }
  }
  flush();
  return out;
}

let planCache = null;
function corpusPlan() {
  const C = S.corpus;
  if (!C.docEx || !C.exs.length) return null;
  const sec = corpusSection();
  const key = JSON.stringify([C.docEx.mtime, sec?.display, C.exs.map((x) => [x.id, x.place?.side, x.place?.fp])]);
  if (planCache && planCache.key === key) return planCache.plan;
  const plan = planLayout(C.exs.map((x) => ({ place: x.place, section: sec?.display })), null);
  planCache = { key, plan };
  return plan;
}

function renderPlanNote() {
  const box = $("#c-plan");
  const plan = corpusPlan();
  const placed = S.corpus.exs.filter((x) => x.place);
  if (!plan || !placed.length) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = "";
  box.append(el("b", { text: "📍 Νέες ασκήσεις σε συγκεκριμένη θέση:" }),
    el("ul", {}, ...placed.map((x) => el("li", { text: `${corpusExLabel(S.corpus.exs.indexOf(x))}: ${x.place.side === "after" ? "μετά την" : "πριν από την"} ${x.place.label} (${x.place.section})` }))));
  if (plan.notes.length) {
    box.append(el("b", { text: "Θα αλλάξουν αριθμό (αυτόματα, από την αρίθμηση του Word):" }),
      el("ul", {}, ...plan.notes.slice(0, 8).map((t) => el("li", { text: t }))));
  }
}

const PLACE = { mode: "new", ex: null, cands: null, srcIdx: 0 };
function placeMode() { return ($$('input[name="place-mode"]').find((r) => r.checked) || {}).value || "end"; }
function setPlaceMode(v) { for (const r of $$('input[name="place-mode"]')) r.checked = r.value === v; $("#place-pick").hidden = v === "end"; }
function normTxt(t) { return String(t || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase(); }

function fillPlaceAnchors(selectIdx) {
  const D = S.corpus.docEx;
  const sel = $("#place-anchor");
  const keep = selectIdx !== undefined ? String(selectIdx) : sel.value;
  const q = normTxt($("#place-filter").value.trim());
  sel.innerHTML = "";
  if (!D) return;
  const src = placeSource();
  let group = null, cur = "";
  D.exercises.forEach((e, i) => {
    if (src && src.fp === e.fp && src.label === e.label) return; // όχι δίπλα στον εαυτό της
    if (q && !normTxt(e.label).startsWith(q) && !normTxt(e.snippet).includes(q) && !normTxt(e.section).includes(q)) return;
    if (e.section !== cur) { cur = e.section; group = el("optgroup", { label: e.section }); sel.append(group); }
    group.append(el("option", { value: String(i), text: `${e.label} — ${e.snippet}` }));
  });
  if ([...sel.options].some((o) => o.value === keep)) sel.value = keep;
  renderPlacePreview();
}

function placeSource() {
  if (PLACE.mode !== "move") return null;
  if (PLACE.cands) return PLACE.cands[PLACE.srcIdx];
  const D = S.corpus.docEx;
  const v = $("#place-what-sel")?.value;
  return D && v !== undefined && v !== "" ? D.exercises[+v] : null;
}

function placeAnchor() {
  const D = S.corpus.docEx;
  const v = $("#place-anchor").value;
  return D && v !== "" ? D.exercises[+v] : null;
}

function renderPlacePreview() {
  const box = $("#place-preview");
  box.innerHTML = "";
  $("#place-ok").disabled = false;
  const mode = placeMode();
  const anchor = placeAnchor();
  const C = S.corpus;
  if (PLACE.mode === "new") {
    const items = C.exs.map((x) => ({ place: x.place, section: corpusSection()?.display }));
    const k = C.exs.indexOf(PLACE.ex);
    if (mode === "end") items[k].place = null;
    else if (anchor) items[k].place = { side: mode, fp: anchor.fp, label: anchor.label };
    else { box.textContent = "Διαλέξτε άσκηση από τη λίστα."; return; }
    if (mode === "end" && !corpusSection()) { box.textContent = "Διαλέξτε πρώτα ενότητα στο βήμα 1."; return; }
    const plan = planLayout(items, null);
    if (!plan) return;
    box.append(el("div", { html: `Η νέα άσκηση θα πάρει τον αριθμό <b>${escapeHtml(plan.labels[k] || "")}</b>.` }));
    if (plan.notes.length) box.append(el("div", { text: "Θα αλλάξουν αριθμό:" }), el("ul", {}, ...plan.notes.slice(0, 6).map((t) => el("li", { text: t }))));
  } else {
    const src = placeSource();
    $("#place-ok").disabled = false;
    if (!src || !anchor) { box.textContent = "Διαλέξτε ποια άσκηση μετακινείται και δίπλα σε ποια."; return; }
    const exs = S.corpus.docEx.exercises;
    const si = exs.findIndex((e) => e.fp === src.fp && e.label === src.label);
    const ai = exs.findIndex((e) => e.fp === anchor.fp && e.label === anchor.label);
    if (exs[si]?.section === exs[ai]?.section && ((mode !== "before" && si === ai + 1) || (mode === "before" && si === ai - 1))) {
      box.textContent = `Η ${src.label} είναι ήδη ${mode === "before" ? "πριν από" : "μετά από"} την ${anchor.label}.`;
      $("#place-ok").disabled = true;
      return;
    }
    const plan = planLayout([], { fp: src.fp, label: src.label, side: mode === "before" ? "before" : "after", anchor: { fp: anchor.fp, label: anchor.label } });
    if (!plan || !plan.moved) return;
    box.append(el("div", { html: `Η <b>${escapeHtml(src.label)}</b> θα γίνει <b>${escapeHtml(plan.moved.label)}</b> (${escapeHtml(plan.moved.sec)}).` }));
    if (plan.notes.length) box.append(el("div", { text: "Θα αλλάξουν αριθμό:" }), el("ul", {}, ...plan.notes.slice(0, 6).map((t) => el("li", { text: t }))));
    box.append(el("div", { class: "muted small", text: "Πριν από την αλλαγή κρατιέται αντίγραφο ασφαλείας και γίνονται έλεγχοι ότι δεν χάθηκε τίποτα. Κλείστε το αρχείο στο Word." }));
  }
}

async function openPlaceForNew(ex) {
  const C = S.corpus;
  if (!C.path) { toast("Επιλέξτε πρώτα το αρχείο Word (βήμα 1).", true); return; }
  if (!C.docEx) await loadDocEx();
  if (!C.docEx) { toast("Δεν διαβάστηκαν οι ασκήσεις του αρχείου.", true); return; }
  Object.assign(PLACE, { mode: "new", ex, cands: null });
  const i = C.exs.indexOf(ex);
  $("#place-title").textContent = `Θέση της νέας άσκησης ${corpusExLabel(i)}`;
  $("#place-what").innerHTML = "";
  $("#place-end-wrap").hidden = false;
  const sec = corpusSection();
  $("#place-end-text").textContent = sec ? `Στο τέλος της ενότητας «${sec.display}» (όπως όλες)` : "Στο τέλος της ενότητας του βήματος 1";
  const sug = $("#place-suggest");
  sug.innerHTML = "";
  for (const m of dupMatches(ex).filter((m) => m.kind === "doc" && m.fp)) {
    sug.append(el("button", { class: "btn small", text: `${DUP_DOT[m.level]} Μετά την ${m.label} (${DUP_TEXT[m.level].toLowerCase()})`, onclick: () => {
      const idx = C.docEx.exercises.findIndex((e) => e.fp === m.fp && e.label === m.label);
      setPlaceMode("after");
      $("#place-filter").value = "";
      fillPlaceAnchors(idx);
    } }));
  }
  $("#place-filter").value = "";
  setPlaceMode(ex.place ? ex.place.side : "end");
  const cur = ex.place ? C.docEx.exercises.findIndex((e) => e.fp === ex.place.fp && e.label === ex.place.label) : undefined;
  fillPlaceAnchors(cur);
  $("#place-ok").textContent = "Εφαρμογή";
  $("#place").hidden = false;
}

async function openPlaceForMove(cands, srcIdx) {
  const C = S.corpus;
  if (!C.path) { toast("Επιλέξτε πρώτα το αρχείο Word.", true); return; }
  await loadDocEx();
  if (!C.docEx) { toast("Δεν διαβάστηκαν οι ασκήσεις του αρχείου.", true); return; }
  Object.assign(PLACE, { mode: "move", ex: null, cands: cands ? cands.map((c) => ({ fp: c.fp, label: c.label, section: c.section })) : null, srcIdx });
  $("#place-title").textContent = "Μετακίνηση άσκησης μέσα στο Word";
  const what = $("#place-what");
  what.innerHTML = "";
  what.append(el("b", { text: "Ποια άσκηση μετακινείται:" }));
  if (PLACE.cands) {
    PLACE.cands.forEach((c, k) => {
      what.append(el("label", { class: "what-opt" },
        el("input", { type: "radio", name: "place-src", checked: k === srcIdx, onchange: () => {
          PLACE.srcIdx = k;
          const other = PLACE.cands[1 - k];
          fillPlaceAnchors(C.docEx.exercises.findIndex((e) => e.fp === other.fp && e.label === other.label));
        } }),
        el("span", { html: `<b>${escapeHtml(c.label)}</b> <span class="muted">(${escapeHtml(c.section)})</span>` })));
    });
  } else {
    const sel = el("select", { id: "place-what-sel", onchange: () => fillPlaceAnchors() });
    let group = null, cur = "";
    C.docEx.exercises.forEach((e, i) => {
      if (e.section !== cur) { cur = e.section; group = el("optgroup", { label: e.section }); sel.append(group); }
      group.append(el("option", { value: String(i), text: `${e.label} — ${e.snippet}` }));
    });
    what.append(sel);
  }
  $("#place-end-wrap").hidden = true;
  $("#place-suggest").innerHTML = "";
  $("#place-filter").value = "";
  setPlaceMode("after");
  let anchorIdx;
  if (PLACE.cands) {
    const other = PLACE.cands[1 - srcIdx];
    anchorIdx = C.docEx.exercises.findIndex((e) => e.fp === other.fp && e.label === other.label);
  }
  fillPlaceAnchors(anchorIdx);
  $("#place-ok").textContent = "Μετακίνηση στο Word";
  $("#place").hidden = false;
}

async function placeApply() {
  const mode = placeMode();
  const anchor = placeAnchor();
  if (PLACE.mode === "new") {
    if (mode === "end") PLACE.ex.place = null;
    else if (!anchor) { toast("Διαλέξτε άσκηση από τη λίστα.", true); return; }
    else PLACE.ex.place = { side: mode, fp: anchor.fp, label: anchor.label, section: anchor.section };
    $("#place").hidden = true;
    renderCorpusReview();
    return;
  }
  const src = placeSource();
  if (!src || !anchor) { toast("Διαλέξτε ποια άσκηση μετακινείται και δίπλα σε ποια.", true); return; }
  const side = mode === "before" ? "before" : "after";
  if (!confirm(`Η ${src.label} θα μπει ${side === "after" ? "μετά από" : "πριν από"} την ${anchor.label} στο Word.\n\nΒεβαιωθείτε ότι το αρχείο είναι κλειστό στο Word. Συνέχεια;`)) return;
  const btn = $("#place-ok");
  btn.disabled = true;
  btn.textContent = "Μετακίνηση…";
  let res;
  try {
    res = await api.corpus_move(S.corpus.path, { fp: src.fp, label: src.label }, { fp: anchor.fp, label: anchor.label }, side);
  } catch (e) {
    res = { ok: false, error: String(e.message || e) };
  } finally {
    btn.disabled = false;
    btn.textContent = "Μετακίνηση στο Word";
  }
  $("#place").hidden = true;
  await afterDocChange(res, "move");
}

function changeReport(res, kind) {
  const box = el("div", { class: "move-res" + (res.ok ? "" : " err") });
  if (!res.ok) {
    box.append(el("b", { text: kind === "undo" ? "Η αναίρεση δεν έγινε" : "Η μετακίνηση δεν έγινε" }), el("div", { text: res.error || "" }));
    return box;
  }
  if (kind === "undo") {
    box.append(el("b", { text: "✓ Αναιρέθηκε η τελευταία αλλαγή" }),
      el("div", { class: "small muted", text: `Το αρχείο επανήλθε από το αντίγραφο ${res.restored}. Η κατάσταση πριν από την αναίρεση κρατήθηκε στο ${res.backup}.` }));
    return box;
  }
  box.append(el("b", { text: `✓ Η ${res.moved_from} μετακινήθηκε ${res.side === "after" ? "μετά από" : "πριν από"} την ${res.anchor} και έγινε ${res.moved_to}` }));
  if (res.renumbered && res.renumbered.length) box.append(el("div", { text: "Νέοι αριθμοί (αυτόματα από το Word):" }), el("ul", {}, ...res.renumbered.slice(0, 10).map((t) => el("li", { text: t }))));
  if (res.warnings && res.warnings.length) box.append(el("div", { class: "notes" }, el("b", { text: "Ελέγξτε:" }), el("ul", {}, ...res.warnings.map((w) => el("li", { text: w })))));
  const det = el("details", {}, el("summary", { text: "Έλεγχοι που έγιναν" }), el("ul", {}, ...(res.checks || []).map((t) => el("li", { text: "✓ " + t }))));
  box.append(det);
  const row = el("div", { class: "row" });
  if (res.backup && res.sha) {
    row.append(el("button", { class: "btn small", text: "↶ Αναίρεση", onclick: async () => {
      if (!confirm("Να επανέλθει το αρχείο όπως ήταν πριν από αυτή τη μετακίνηση;")) return;
      let r;
      try { r = await api.corpus_undo(S.corpus.path, res.backup, res.sha); } catch (e) { r = { ok: false, error: String(e.message || e) }; }
      await afterDocChange(r, "undo");
    } }));
  }
  row.append(el("button", { class: "btn small ghost", text: "Άνοιγμα στο Word", onclick: () => api.open_file(S.corpus.path) }));
  box.append(row);
  if (res.backup) box.append(el("div", { class: "muted small", text: `Αντίγραφο του αρχείου πριν από την αλλαγή: ${res.backup}` }));
  return box;
}

async function afterDocChange(res, kind) {
  const holder = $("#c-audit-moveres");
  $("#c-audit-card").hidden = false;
  holder.replaceChildren(changeReport(res, kind));
  holder.scrollIntoView({ behavior: "smooth", block: "nearest" });
  if (!res.ok) return;
  toast(kind === "undo" ? "Η αλλαγή αναιρέθηκε." : `Η ${res.moved_from} έγινε ${res.moved_to}.`);
  await corpusOpen(S.corpus.path, true);
  if (S.audit.res && S.audit.res.ok) auditRun();
}

/* ---------- εισαγωγή */
function corpusPayload() {
  return S.corpus.exs.map((ex) => {
    let fr = null;
    if (ex.data.figure && ex.fig) {
      if (ex.fig.mode === "original") {
        const c = ex.fig.crop;
        if (c && !c.error) fr = { mode: "original", png: c.png, svg: null, width_cm: c.width_cm };
      } else if (ex.fig.mode === "redraw") {
        const r = ex.fig.render || {};
        fr = { mode: "redraw", spec_json: ex.data.figure.spec_json, png: r.png, svg: r.svg, width_cm: r.width_cm };
      }
    }
    const place = ex.place ? { side: ex.place.side, fp: ex.place.fp, label: ex.place.label } : null;
    return { stem: ex.data.stem, items: ex.data.items, closing: ex.data.closing, figure_render: fr, place };
  });
}

async function corpusInsert() {
  const C = S.corpus;
  const sec = corpusSection();
  if (!C.path) { toast("Επιλέξτε πρώτα το αρχείο Word (βήμα 1).", true); return; }
  if (!C.exs.length) { toast("Δεν υπάρχουν ασκήσεις για εισαγωγή.", true); return; }
  const unplaced = C.exs.filter((x) => !x.place).length;
  if (!sec && unplaced) { toast("Επιλέξτε στη λίστα του βήματος 1 την ενότητα όπου θα μπουν οι ασκήσεις (ή ορίστε θέση 📍 σε κάθε άσκηση).", true); window.scrollTo(0, 0); return; }
  const pending = C.images.filter((im) => im.status === "pending").length;
  if (pending) { toast("Περιμένετε να ολοκληρωθεί η μεταγραφή όλων των εικόνων.", true); return; }
  const failed = C.images.filter((im) => im.status === "error" || im.status === "new").length;
  if (failed && !confirm(`${failed} εικόνα(ες) δεν έχουν μεταγραφεί και θα παραλειφθούν. Συνέχεια;`)) return;
  const nSame = C.exs.filter((x) => dupMatches(x).some((m) => m.level === "same" && m.kind === "doc")).length;
  const sameNote = nSame ? `\n\nΠροσοχή: ${nSame === 1 ? "1 άσκηση υπάρχει" : `${nSame} ασκήσεις υπάρχουν`} ήδη στο έγγραφο (κόκκινη ένδειξη) και θα ${nSame === 1 ? "μπει" : "μπουν"} ξανά.` : "";
  const placedN = C.exs.length - unplaced;
  const plan = corpusPlan();
  const where = placedN
    ? (unplaced ? `${unplaced} στο τέλος της ενότητας «${sec.display}» και ${placedN} στις θέσεις που ορίσατε (📍)` : "στις θέσεις που ορίσατε (📍)")
    : `(${corpusExLabel(0)} – ${corpusExLabel(C.exs.length - 1)}) στο τέλος της ενότητας «${sec.display}»`;
  const renum = plan && plan.notes.length ? `\n\nΘα αλλάξουν αριθμό (αυτόματα): ${plan.notes.slice(0, 4).join(" · ")}${plan.notes.length > 4 ? " …" : ""}` : "";
  if (!confirm(`Θα μπουν ${C.exs.length} ασκήσεις ${where}.${renum}${sameNote}\n\nΒεβαιωθείτε ότι το αρχείο είναι κλειστό στο Word. Συνέχεια;`)) return;
  const btn = $("#c-insert");
  btn.disabled = true;
  btn.textContent = "Εισαγωγή…";
  let res;
  try {
    res = await api.corpus_insert(C.path, sec ? sec.key : null, corpusPayload());
  } catch (e) {
    res = { ok: false, error: String(e.message || e) };
  } finally {
    btn.disabled = false;
  }
  const done = $("#c-done");
  done.hidden = false;
  done.innerHTML = "";
  if (!res.ok) {
    done.append(el("h2", { text: "Η εισαγωγή δεν έγινε" }), el("div", { class: "error-box", text: res.error }),
      el("p", { class: "muted small", text: "Το αρχείο σας δεν άλλαξε. Οι ασκήσεις παραμένουν εδώ· διορθώστε το πρόβλημα και πατήστε ξανά «Εισαγωγή»." }));
    renderCorpusReview();
    done.scrollIntoView({ behavior: "smooth" });
    return;
  }
  done.append(...[
    el("div", { class: "row" }, el("span", { class: "okmark", text: "✓" }),
      el("h2", { style: "margin:0", text: placedN
        ? `Προστέθηκαν ${res.inserted} ασκήσεις: ${(res.labels || []).join(", ")}`
        : `Προστέθηκαν ${res.inserted} ασκήσεις (${res.first_label} – ${res.last_label}) στο «${sec.display}»` })),
    el("div", { class: "path", text: res.path }),
    el("div", { class: "row" },
      el("button", { class: "btn primary", text: "Άνοιγμα στο Word", onclick: () => api.open_file(res.path) }),
      el("button", { class: "btn", text: "Άνοιγμα φακέλου", onclick: () => api.open_folder(res.path) })),
    res.backup ? el("p", { class: "muted small", text: `Αντίγραφο του αρχείου πριν από την αλλαγή: ${res.backup}` }) : null,
    res.removed_placeholders ? el("p", { class: "muted small", text: `Αφαιρέθηκαν ${res.removed_placeholders} κενές αριθμημένες γραμμές που υπήρχαν στην ενότητα.` }) : null,
    res.renumbered && res.renumbered.length ? el("div", { class: "small" }, el("b", { text: "Άλλαξαν αριθμό (αυτόματα από το Word):" }),
      el("ul", {}, ...res.renumbered.slice(0, 10).map((t) => el("li", { text: t })))) : null,
    res.warnings && res.warnings.length ? el("div", { class: "notes" }, el("b", { text: "Σημειώσεις:" }), el("ul", {}, ...res.warnings.map((w) => el("li", { text: w })))) : null,
    res.checks && res.checks.length ? el("details", { class: "small" }, el("summary", { text: "Έλεγχοι που έγιναν πριν από την αποθήκευση" }),
      el("ul", {}, ...res.checks.map((t) => el("li", { text: "✓ " + t })))) : null,
    res.backup && res.sha ? el("div", { class: "row" }, el("button", { class: "btn small", text: "↶ Αναίρεση της εισαγωγής", onclick: async () => {
      if (!confirm("Να επανέλθει το αρχείο όπως ήταν πριν από την εισαγωγή; (Οι ασκήσεις δεν θα υπάρχουν πια ούτε εδώ.)")) return;
      let r;
      try { r = await api.corpus_undo(C.path, res.backup, res.sha); } catch (e) { r = { ok: false, error: String(e.message || e) }; }
      if (r.ok) { toast("Η εισαγωγή αναιρέθηκε."); done.hidden = true; await corpusOpen(C.path, true); } else toast(r.error, true, 8000);
    } })) : null,
    el("p", { class: "muted small", text: "Για την επόμενη ενότητα: επιλέξτε τη στη λίστα του βήματος 1 και προσθέστε νέες εικόνες." }),
  ].filter(Boolean));
  // νέα παρτίδα: οι ασκήσεις μπήκαν στο Word
  Object.assign(C, { images: [], exs: [], jobs: {}, manual: false, outline: res.outline || C.outline, dup: null });
  renderCorpus();
  loadDocEx();
  if (S.audit.res) S.audit.res = null, renderAudit();
  done.scrollIntoView({ behavior: "smooth" });
}

function bindCorpus() {
  for (const b of $$("#modes button")) b.onclick = () => setMode(b.dataset.mode);
  $("#c-phone").onclick = phoneOpen;
  $("#e-phone").onclick = phoneOpen;
  $("#phone-stop").onclick = phoneStop;
  $("#c-choose").onclick = () => corpusOpen("");
  $("#c-reload").onclick = () => corpusOpen(S.corpus.path);
  $("#c-openword").onclick = () => S.corpus.path && api.open_file(S.corpus.path);
  $("#c-sort-name").onclick = () => corpusSort("name");
  $("#c-sort-time").onclick = () => corpusSort("time");
  $("#c-clear").onclick = () => {
    if (!S.corpus.images.length || !confirm("Να αφαιρεθούν όλες οι εικόνες και οι μεταγραφές τους;")) return;
    Object.assign(S.corpus, { images: [], exs: [], jobs: {}, manual: false, dup: null });
    renderCorpus();
  };
  $("#c-transcribe").onclick = () => corpusTranscribe();
  $("#c-insert").onclick = corpusInsert;
  $("#c-dup-sens").value = S.app?.settings?.dup_check || "normal";
  $("#c-dup-sens").onchange = (e) => { if (S.app?.settings) S.app.settings.dup_check = e.target.value; corpusCheckDuplicates(0); };
  $("#c-dup-recheck").onclick = () => corpusCheckDuplicates(0);
  $("#c-audit-open").onclick = auditOpen;
  $("#c-audit-close").onclick = () => { $("#c-audit-card").hidden = true; };
  $("#c-audit-other").onclick = auditChooseOther;
  $("#c-audit-b-clear").onclick = () => { S.audit.other = null; S.audit.res = null; renderAudit(); };
  $("#c-audit-run").onclick = auditRun;
  $("#c-audit-move").onclick = () => openPlaceForMove(null, 0);
  $("#place-ok").onclick = placeApply;
  $("#place-filter").oninput = () => fillPlaceAnchors();
  $("#place-anchor").onchange = () => { if (placeMode() === "end") setPlaceMode("after"); renderPlacePreview(); };
  for (const r of $$('input[name="place-mode"]')) r.onchange = () => { $("#place-pick").hidden = placeMode() === "end"; renderPlacePreview(); };
  $("#c-audit-sens").onchange = () => { if (S.audit.res) auditRun(); };
  for (const id of ["#c-audit-f-level", "#c-audit-f-scope", "#c-audit-f-sec"]) $(id).onchange = renderAudit;
  document.addEventListener("paste", async (e) => {
    if (S.mode !== "corpus") return;
    const items = Array.from(e.clipboardData?.items || []).filter((it) => it.type.startsWith("image/"));
    if (!items.length) return;
    e.preventDefault();
    await corpusAddFiles(items.map((it) => it.getAsFile()).filter(Boolean), true);
  });
}

/* ================================================================== εκκίνηση */
function refreshLists() {
  fillList("#f-class", "classes");
  fillList("#f-editor", "editors");
}

async function main() {
  await initBridge();
  S.app = await api.get_state();
  const s = S.app.settings;
  applyTheme(s.theme || "system");
  $("#app-version").textContent = `v${S.app.version}`;
  $("#f-title").value = s.last_title || "";
  $("#f-subtitle").value = s.last_subtitle || "";
  $("#f-class").value = s.last_class || "";
  $("#f-editor").value = s.last_editor || "";
  refreshLists();
  $("#btn-today").onclick = () => { $("#f-date").value = S.app.today; };
  $("#f-nthemes").onchange = (e) => setThemeCount(+e.target.value);
  $("#btn-transcribe").onclick = onTranscribeClick;
  $("#btn-back").onclick = () => switchTab("setup");
  $("#btn-build").onclick = onBuild;
  $("#btn-theme").onclick = cycleTheme;
  $("#btn-about").onclick = showAbout;
  $("#btn-guide").onclick = showGuide;
  $("#about-check").onclick = () => checkUpdates(true);
  for (const [name, input] of [["classes", "#f-class"], ["editors", "#f-editor"]]) {
    $(`#${name}-save`).onclick = () => saveListValue(name, input);
    $(`#${name}-toggle`).onclick = () => { const m = $(`#${name}-manage`); m.hidden = !m.hidden; };
  }
  $("#lightbox").onclick = () => { $("#lightbox").hidden = true; };
  for (const m of $$(".modal")) {
    m.addEventListener("click", (e) => { if (e.target === m) m.hidden = true; });
    for (const c of $$("[data-close]", m)) c.onclick = () => { m.hidden = true; };
  }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModals(); });
  for (const b of $$("#steps button")) b.onclick = () => { if (b.dataset.tab === "done") renderDone(); switchTab(b.dataset.tab); };
  bindSettings();
  bindCorpus();
  initPdfPick();
  updateTemplateHint();
  await setThemeCount(+$("#f-nthemes").value);
  if (s.check_updates) setTimeout(() => checkUpdates(false), 1500);
  usageInit();
}

window.addEventListener("DOMContentLoaded", () => {
  main().catch((e) => { console.error(e); toast("Σφάλμα εκκίνησης: " + (e.message || e), true, 10000); });
});
