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
      headers: { "X-Api-Token": new URLSearchParams(location.search).get("k") || "" } });
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
      const events = await api._poll();
      for (const ev of events || []) window.__onPyEvent(ev);
    } catch (_) { /* server έκλεισε */ }
    await sleep(700);
  }
}

window.__onPyEvent = (payload) => {
  if (payload.event === "transcribed") onTranscribed(payload.data);
  if (payload.event === "corpus_transcribed") onCorpusTranscribed(payload.data);
  if (payload.event === "phone_image") onPhoneImage(payload.data);
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
  btn.textContent = labels[theme] || labels.system;
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

function mdToHtml(text, refmap = {}) {
  const maths = [];
  let t = String(text || "").replace(/[ ]*(?:\t|⇥)[ ]*/g, " @@TAB@@ ");
  t = t.replace(/\$\$([\s\S]+?)\$\$/g, (_, g) => { maths.push(renderMath(g, true)); return `@@M${maths.length - 1}@@`; });
  t = t.replace(/\$([^$\n]+?)\$/g, (_, g) => { maths.push(renderMath(g, false)); return `@@M${maths.length - 1}@@`; });
  t = t.replace(/\[\[([^\]]+)\]\]/g, (_, g) => refmap[g.trim()] || `(${g.trim()})`);
  let html = marked.parse(t, { breaks: false, gfm: true });
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

/** Ζώνη εικόνων: σύρσιμο, κλικ για επιλογή αρχείου, και στόχος για Ctrl+V. */
function dropZone(tIdx, eIdx, label, hint) {
  const input = el("input", { type: "file", accept: "image/*", multiple: true, hidden: true,
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
  const list = Array.from(files || []).filter((f) => f.type.startsWith("image/"));
  if (!list.length) { toast("Επιλέξτε αρχεία εικόνας (PNG, JPG).", true); return; }
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
  if (st.mode === "redraw" && current && current.warnings && current.warnings.length) {
    box.append(el("div", { class: "fig-warn" }, ...current.warnings.map((w) => el("div", { text: "⚠ " + w }))));
  }
  box.append(el("div", { class: "small muted", text: fig.description }));

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
  $("#about-changelog").innerHTML = marked.parse(a.changelog || "_Δεν βρέθηκε το ιστορικό αλλαγών._");
  $("#about-update").innerHTML = "";
  openModal("#about");
}

async function showGuide() {
  const a = await loadAbout();
  $("#guide-body").innerHTML = marked.parse(a.guide || "_Δεν βρέθηκε ο οδηγός χρήσης._");
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
    if (manual) $("#about-update").replaceChildren(el("div", { class: "error-box", text: r.error }));
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

function updateCard(r) {
  const size = r.size ? ` · ${(r.size / 1048576).toFixed(0)} MB` : "";
  return el("div", { class: "update-card" },
    el("h3", { text: `Νέα έκδοση ${r.latest}` + (r.published ? ` (${r.published})` : "") }),
    el("div", { class: "notes-md", html: marked.parse(r.notes || "") }),
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
  await api.install_update(r.url);
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
S.corpus = { path: "", name: "", outline: [], sel: -1, images: [], exs: [], jobs: {}, manual: false, opened: false };

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
  renderCorpus();
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
  const input = el("input", { type: "file", accept: "image/*", multiple: true, hidden: true,
    onchange: async (ev) => { await corpusAddFiles(ev.target.files); ev.target.value = ""; } });
  const drop = el("div", { class: "drop active", tabindex: 0, onclick: () => input.click() },
    el("div", { class: "drop-title", text: "Σύρετε εδώ όσες εικόνες θέλετε (φωτογραφίες ή screenshots)" }),
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
  const list = Array.from(files || []).filter((f) => f.type.startsWith("image/"));
  if (!list.length) { toast("Επιλέξτε αρχεία εικόνας (PNG, JPG).", true); return; }
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
  C.images.forEach((im, i) => {
    const b = badge[im.status] || badge.new;
    const job = Object.values(C.jobs).find((j) => j.imageIds[0] === im.id);
    const nEx = job ? C.exs.filter((x) => x.jobId === job.id).length : 0;
    box.append(el("div", { class: "cthumb" + (im.join ? " joined" : "") },
      el("img", { src: im.dataUrl, alt: "", onclick: () => lightbox(im.dataUrl) }),
      el("div", { class: "nm", title: im.name, text: `${i + 1}. ${im.name}` }),
      el("div", { class: "tools" },
        el("span", { class: `badge ${b[0]}`, title: im.error || "", text: im.status === "done" && job ? `${nEx} ασκ.` : b[1] }),
        el("div", { class: "spacer" }),
        i > 0 ? el("button", { class: im.join ? "on" : "", title: "Η εικόνα είναι συνέχεια (επόμενη σελίδα) της προηγούμενης", text: "⤒ Συνέχεια",
          onclick: () => { corpusMarkChanged(im); corpusMarkChanged(C.images[i - 1]); im.join = !im.join; renderCorpus(); } }) : null,
        el("button", { title: "Πιο μπροστά", text: "◀", onclick: () => corpusMoveImage(i, -1) }),
        el("button", { title: "Πιο πίσω", text: "▶", onclick: () => corpusMoveImage(i, 1) }),
        el("button", { title: "Αφαίρεση", text: "✕", onclick: () => corpusRemoveImage(i) }))));
  });
  const n = C.images.length;
  const todo = C.images.filter((im) => im.status === "new" || im.status === "error").length;
  $("#c-img-total").textContent = n ? `${n} εικόνες` + (todo && todo < n ? ` · ${todo} για μεταγραφή` : "") : "";
  $("#c-sortbar").hidden = n < 2;
  $("#c-transcribe").disabled = !todo;
}

function corpusMoveImage(i, d) {
  const imgs = S.corpus.images;
  const j = i + d;
  if (j < 0 || j >= imgs.length) return;
  for (const im of [imgs[i], imgs[j]]) if (im.join || imgs[imgs.indexOf(im) + 1]?.join) corpusMarkChanged(im);
  [imgs[i], imgs[j]] = [imgs[j], imgs[i]];
  if (imgs[0]) imgs[0].join = false;
  corpusResort();
  renderCorpus();
}

function corpusRemoveImage(i) {
  const im = S.corpus.images[i];
  const job = Object.values(S.corpus.jobs).find((j) => j.imageIds.includes(im.id));
  if (job && S.corpus.exs.some((x) => x.jobId === job.id) && !confirm("Να αφαιρεθεί η εικόνα και οι ασκήσεις που διαβάστηκαν από αυτήν;")) return;
  if (job) dropJob(job.id);
  S.corpus.images.splice(i, 1);
  if (S.corpus.images[i]) S.corpus.images[i].join = S.corpus.images[i].join && i > 0;
  if (S.corpus.images[0]) S.corpus.images[0].join = false;
  renderCorpus();
}

function corpusSort(by) {
  const imgs = S.corpus.images;
  if (imgs.some((im) => im.join) && !confirm("Η ταξινόμηση θα αφαιρέσει τις ενώσεις «Συνέχεια». Συνέχεια;")) return;
  for (const im of imgs) { if (im.join) corpusMarkChanged(im); im.join = false; }
  imgs.sort(by === "time" ? (a, b) => a.time - b.time || natCmp(a.name, b.name) : (a, b) => natCmp(a.name, b.name));
  corpusResort();
  renderCorpus();
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
  $("#c-ex-total").textContent = C.exs.length && sec ? `${corpusExLabel(0)} – ${corpusExLabel(C.exs.length - 1)} στο «${sec.display}»` : "";
  $("#c-insert").textContent = sec ? `Εισαγωγή στο «${sec.display}»` : "Εισαγωγή στο Word";
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
  box.append(el("div", { class: "small muted", text: fig.description }));
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
    el("div", { class: "spacer" }),
    el("button", { class: "icon-mini", title: "Άσκηση πιο πάνω", text: "▲", onclick: () => move(-1) }),
    el("button", { class: "icon-mini", title: "Άσκηση πιο κάτω", text: "▼", onclick: () => move(1) }),
    el("button", { class: "icon-mini danger", title: "Να μη μπει αυτή η άσκηση", text: "✕", onclick: () => {
      if (!confirm("Να αφαιρεθεί αυτή η άσκηση από την εισαγωγή;")) return;
      C.exs.splice(C.exs.indexOf(ex), 1); renderCorpusReview(); renderCorpusImages();
    } })));
  if (d.uncertain && d.uncertain.length) {
    editor.append(el("div", { class: "notes" }, el("b", { text: "Χρειάζεται έλεγχος:" }),
      el("ul", {}, ...d.uncertain.map((u) => el("li", { text: u })))));
  }
  editor.append(el("div", { class: "field-label", text: "Εκφώνηση" }), textPair(d.stem, (v) => { d.stem = v; }, {}, 3));
  if (d.figure && ex.fig) editor.append(corpusFigureBox(ex));
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
    return { stem: ex.data.stem, items: ex.data.items, closing: ex.data.closing, figure_render: fr };
  });
}

async function corpusInsert() {
  const C = S.corpus;
  const sec = corpusSection();
  if (!C.path) { toast("Επιλέξτε πρώτα το αρχείο Word (βήμα 1).", true); return; }
  if (!sec) { toast("Επιλέξτε στη λίστα του βήματος 1 την ενότητα όπου θα μπουν οι ασκήσεις.", true); window.scrollTo(0, 0); return; }
  if (!C.exs.length) { toast("Δεν υπάρχουν ασκήσεις για εισαγωγή.", true); return; }
  const pending = C.images.filter((im) => im.status === "pending").length;
  if (pending) { toast("Περιμένετε να ολοκληρωθεί η μεταγραφή όλων των εικόνων.", true); return; }
  const failed = C.images.filter((im) => im.status === "error" || im.status === "new").length;
  if (failed && !confirm(`${failed} εικόνα(ες) δεν έχουν μεταγραφεί και θα παραλειφθούν. Συνέχεια;`)) return;
  if (!confirm(`Θα μπουν ${C.exs.length} ασκήσεις (${corpusExLabel(0)} – ${corpusExLabel(C.exs.length - 1)}) στο τέλος της ενότητας «${sec.display}».\n\nΒεβαιωθείτε ότι το αρχείο είναι κλειστό στο Word. Συνέχεια;`)) return;
  const btn = $("#c-insert");
  btn.disabled = true;
  btn.textContent = "Εισαγωγή…";
  let res;
  try {
    res = await api.corpus_insert(C.path, sec.key, corpusPayload());
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
      el("h2", { style: "margin:0", text: `Προστέθηκαν ${res.inserted} ασκήσεις (${res.first_label} – ${res.last_label}) στο «${sec.display}»` })),
    el("div", { class: "path", text: res.path }),
    el("div", { class: "row" },
      el("button", { class: "btn primary", text: "Άνοιγμα στο Word", onclick: () => api.open_file(res.path) }),
      el("button", { class: "btn", text: "Άνοιγμα φακέλου", onclick: () => api.open_folder(res.path) })),
    res.backup ? el("p", { class: "muted small", text: `Αντίγραφο του αρχείου πριν από την αλλαγή: ${res.backup}` }) : null,
    res.removed_placeholders ? el("p", { class: "muted small", text: `Αφαιρέθηκαν ${res.removed_placeholders} κενές αριθμημένες γραμμές που υπήρχαν στην ενότητα.` }) : null,
    res.warnings && res.warnings.length ? el("div", { class: "notes" }, el("b", { text: "Σημειώσεις:" }), el("ul", {}, ...res.warnings.map((w) => el("li", { text: w })))) : null,
    el("p", { class: "muted small", text: "Για την επόμενη ενότητα: επιλέξτε τη στη λίστα του βήματος 1 και προσθέστε νέες εικόνες." }),
  ].filter(Boolean));
  // νέα παρτίδα: οι ασκήσεις μπήκαν στο Word
  Object.assign(C, { images: [], exs: [], jobs: {}, manual: false, outline: res.outline || C.outline });
  renderCorpus();
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
    Object.assign(S.corpus, { images: [], exs: [], jobs: {}, manual: false });
    renderCorpus();
  };
  $("#c-transcribe").onclick = () => corpusTranscribe();
  $("#c-insert").onclick = corpusInsert;
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
  updateTemplateHint();
  await setThemeCount(+$("#f-nthemes").value);
  if (s.check_updates) setTimeout(() => checkUpdates(false), 1500);
}

window.addEventListener("DOMContentLoaded", () => {
  main().catch((e) => { console.error(e); toast("Σφάλμα εκκίνησης: " + (e.message || e), true, 10000); });
});
