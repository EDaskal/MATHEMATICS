/* Γεννήτρια Διαγωνισμάτων — λογική οθόνης.
 * Επικοινωνεί με την Python μέσω `api.<μέθοδος>(...)` (pywebview ή τοπικός server). */
"use strict";

const LETTERS = ["Α", "Β", "Γ", "Δ", "Ε", "ΣΤ", "Ζ", "Η", "Θ", "Ι"];
const ROMAN = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"];
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
    const r = await fetch(`/api/${name}`, { method: "POST", body: JSON.stringify(args) });
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
};

/* ------------------------------------------------------------------ κατάσταση */
const S = {
  app: null,           // get_state()
  exam: { themes: [] },
  target: 0,           // θέμα όπου πάει η επικόλληση εικόνας
  lastBuild: null,
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

function switchTab(name) {
  for (const b of $$("#steps button")) b.classList.toggle("active", b.dataset.tab === name);
  for (const s of $$(".tab")) s.classList.toggle("active", s.id === `tab-${name}`);
  window.scrollTo(0, 0);
  if (name === "review") renderReview();
  if (name === "setup") renderThemes();
}

/* ------------------------------------------------------------------ Markdown + LaTeX προεπισκόπηση */
function renderMath(tex, display) {
  // η γραμματοσειρά του KaTeX δεν έχει όρθια ελληνικά: \mathrm{ημ} → \textrm{ημ} μόνο για την προεπισκόπηση
  tex = tex.replace(/\\mathrm\{([^{}]*[\u0370-\u03FF\u1F00-\u1FFF][^{}]*)\}/g, "\\textrm{$1}");
  try {
    return katex.renderToString(tex, { displayMode: display, throwOnError: false, strict: false, trust: false });
  } catch (e) {
    return `<span style="color:#b3261e">${escapeHtml(tex)}</span>`;
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function mdToHtml(text, refmap = {}) {
  const maths = [];
  let t = String(text || "");
  t = t.replace(/\$\$([\s\S]+?)\$\$/g, (_, g) => { maths.push(renderMath(g, true)); return `@@M${maths.length - 1}@@`; });
  t = t.replace(/\$([^$\n]+?)\$/g, (_, g) => { maths.push(renderMath(g, false)); return `@@M${maths.length - 1}@@`; });
  t = t.replace(/\[\[([^\]]+)\]\]/g, (_, g) => refmap[g.trim()] || `(${g.trim()})`);
  let html = marked.parse(t, { breaks: false, gfm: true });
  html = html.replace(/@@M(\d+)@@/g, (_, i) => maths[+i]);
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
  tr.textContent = `Μονάδες: ${sumItems} / 100 · Έτοιμα θέματα: ${done}/${all}` + (pend ? ` · σε εξέλιξη: ${pend}` : "");
  tr.className = "total " + (sumItems === 100 && done === all ? "good" : "bad");
}

/* ================================================================== ΒΗΜΑ 1: Διαγώνισμα */
function readDetails() {
  S.exam.title = $("#f-title").value.trim();
  S.exam.subtitle = $("#f-subtitle").value.trim();
  S.exam.date = $("#f-date").value.trim();
  S.exam.footer = $("#f-footer").value;
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
  S.target = Math.min(S.target, n - 1);
  await autoThemeTotals();
  for (const t of cur) await syncItemPoints(t, true);
  renderThemes();
}

function renderThemes() {
  const box = $("#themes");
  box.innerHTML = "";
  S.exam.themes.forEach((t, i) => {
    const input = el("input", { type: "file", accept: "image/*", multiple: true, hidden: true,
      onchange: async (e) => { await addFiles(i, e.target.files); e.target.value = ""; } });
    const drop = el("div", { class: "drop", onclick: () => input.click() },
      el("div", { text: "Σύρετε εικόνες εδώ ή πατήστε για επιλογή" }),
      el("div", { class: "small", text: i === S.target ? "Ctrl+V: επικόλληση screenshot σε αυτό το θέμα" : "" }));
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", async (e) => {
      e.preventDefault(); drop.classList.remove("over");
      await addFiles(i, e.dataTransfer.files);
    });

    const thumbs = el("div", { class: "thumbs" });
    t.exercises.forEach((ex, k) => {
      const badge = { new: ["", "νέα"], pending: ["run", "διαβάζεται…"], done: ["ok", "έτοιμη"], error: ["err", "σφάλμα"] }[ex.status];
      thumbs.append(el("div", { class: "thumb" },
        el("div", {},
          el("img", { src: ex.dataUrl, alt: "", onclick: (e) => { e.stopPropagation(); lightbox(ex.dataUrl); } }),
          el("div", {}, el("span", { class: `badge ${badge[0]}`, text: badge[1] }),
            t.exercises.length > 1 ? el("span", { class: "small muted", text: `  →  ${LETTERS[i]}${k + 1}` }) : null)),
        el("div", { class: "tools" },
          el("button", { title: "Πάνω", text: "▲", onclick: (e) => { e.stopPropagation(); moveEx(i, k, -1); } }),
          el("button", { title: "Κάτω", text: "▼", onclick: (e) => { e.stopPropagation(); moveEx(i, k, 1); } }),
          el("button", { title: "Αφαίρεση", text: "✕", onclick: (e) => { e.stopPropagation(); removeEx(i, k); } }))));
    });

    const pts = el("input", { type: "number", min: 0, max: 100, value: t.pointsTotal,
      oninput: async (e) => { t.pointsTotal = +e.target.value || 0; await syncItemPoints(t, true); updateTotals(); } });
    const card = el("div", { class: "theme" + (i === S.target ? " target" : ""), onclick: () => { if (S.target !== i) { S.target = i; renderThemes(); } } },
      el("div", { class: "theme-head" },
        el("h3", { text: `ΘΕΜΑ ${LETTERS[i] || i + 1}` }),
        el("label", {}, "Μονάδες", pts)),
      thumbs, drop, input,
      t.exercises.length > 1 ? el("div", { class: "small muted", text: "Πολλές εικόνες: κάθε εικόνα γίνεται ξεχωριστό ερώτημα (π.χ. Γ1, Γ2)." }) : null);
    box.append(card);
  });
  updateTotals();
}

async function addFiles(themeIdx, files) {
  const list = Array.from(files || []).filter((f) => f.type.startsWith("image/"));
  if (!list.length) { toast("Επιλέξτε αρχεία εικόνας (PNG, JPG).", true); return; }
  for (const f of list) await addImage(themeIdx, await fileToDataUrl(f));
}

function fileToDataUrl(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result);
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}

async function addImage(themeIdx, dataUrl) {
  const t = S.exam.themes[themeIdx];
  try {
    const res = await api.add_image(dataUrl);
    if (!res.ok) { toast(res.error, true); return; }
    t.exercises.push({ id: newId(), imageId: res.image_id, dataUrl, status: "new", data: null, error: null, fig: null });
    await syncItemPoints(t);
    renderThemes();
  } catch (e) { toast(String(e.message || e), true); }
}

async function moveEx(i, k, d) {
  const exs = S.exam.themes[i].exercises;
  const j = k + d;
  if (j < 0 || j >= exs.length) return;
  [exs[k], exs[j]] = [exs[j], exs[k]];
  renderThemes();
}

async function removeEx(i, k) {
  const t = S.exam.themes[i];
  t.exercises.splice(k, 1);
  await syncItemPoints(t);
  renderThemes();
}

document.addEventListener("paste", async (e) => {
  if (!$("#tab-setup").classList.contains("active")) return;
  const items = Array.from(e.clipboardData?.items || []).filter((it) => it.type.startsWith("image/"));
  if (!items.length) return;
  e.preventDefault();
  for (const it of items) {
    const f = it.getAsFile();
    if (f) await addImage(S.target, await fileToDataUrl(f));
  }
  toast(`Η εικόνα μπήκε στο ΘΕΜΑ ${LETTERS[S.target]}.`);
});

async function startTranscription(exList) {
  const jobs = exList.filter((ex) => ex.imageId).map((ex) => ({ job_id: ex.id, image_id: ex.imageId }));
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
  if (!all.length) { toast("Προσθέστε τουλάχιστον μία εικόνα θέματος.", true); return; }
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
      if (value === "original" && !st.crop) st.crop = await api.crop_figure(ex.id, ex.imageId, fig.crop);
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
  box.append(el("details", {},
    el("summary", { class: "small", text: "Επεξεργασία σχήματος (για προχωρημένους)" }),
    el("p", { class: "small muted", text: "Η περιγραφή σε JSON. Αλλάξτε συντεταγμένες/ετικέτες και πατήστε «Επανασχεδίαση»." }),
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
      el("button", { class: "btn small", text: "Εφαρμογή", onclick: async () => {
        const vals = cropInp.value.split(",").map((v) => parseFloat(v));
        if (vals.length === 4 && vals.every((v) => !isNaN(v))) { fig.crop = vals; }
        st.crop = await api.crop_figure(ex.id, ex.imageId, fig.crop);
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
  const orig = el("div", { class: "orig" },
    el("img", { src: ex.dataUrl, alt: "πρωτότυπο", onclick: () => lightbox(ex.dataUrl) }),
    el("div", { class: "src small muted", text: ex.data?.source_label ? `Άσκηση βιβλίου: ${ex.data.source_label}` : "" }));
  const editor = el("div", { class: "editor" });
  const card = el("div", { class: "ex", id: `ex-${ex.id}` }, orig, editor);

  if (ex.status === "pending" || ex.status === "new") {
    editor.append(el("div", { class: "pending" }, el("div", { class: "spinner" }),
      ex.status === "new" ? "Αναμονή…" : "Ο Claude διαβάζει την εικόνα… (συνήθως 15–60 δευτερόλεπτα)"));
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
  const itemsNode = single
    ? renderItems(d.items, 1, (p) => labeler(exIdx, p), [], refmap, rerender)
    : renderItems(d.items, 2, (p) => labeler(exIdx, p), [], refmap, rerender);
  editor.append(itemsNode);
  if (!d.items.length) {
    editor.append(el("button", { class: "btn small", text: "+ ερώτημα", onclick: () => { d.items.push({ label: "", text: "", choices: null, items: [] }); rerender(); } }));
  }
  if (d.closing) editor.append(el("div", { class: "field-label", text: "Κείμενο στο τέλος" }), textPair(d.closing, (v) => { d.closing = v; }, refmap));
  editor.append(el("div", { class: "row" },
    el("div", { class: "spacer" }),
    el("button", { class: "btn small ghost", text: "↻ Ξαναδιάβασε την εικόνα", onclick: () => {
      if (confirm("Η μεταγραφή θα γίνει από την αρχή και οι αλλαγές σας σε αυτό το θέμα θα χαθούν. Συνέχεια;")) startTranscription([ex]);
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
          const chosen = ex.fig.mode === "original" ? ex.fig.crop : ex.fig.render;
          if (chosen && !chosen.error) fr = { png: chosen.png, svg: chosen.svg, width_cm: chosen.width_cm };
        }
        return { stem: ex.data.stem, items: ex.data.items, closing: ex.data.closing, figure_render: fr };
      }),
    }));
  return { title: S.exam.title, subtitle: S.exam.subtitle, date: S.exam.date, footer: S.exam.footer, themes };
}

async function onBuild() {
  const exs = allExercises();
  const pending = exs.filter((e) => e.status === "pending").length;
  const failed = exs.filter((e) => e.status === "error" || e.status === "new").length;
  if (pending) { toast("Περιμένετε να ολοκληρωθεί η μεταγραφή όλων των θεμάτων.", true); return; }
  if (failed && !confirm(`${failed} εικόνα(ες) δεν έχουν μεταγραφεί και θα παραλειφθούν. Συνέχεια;`)) return;
  const sum = S.exam.themes.reduce((a, t) => a + t.itemPoints.reduce((x, y) => x + (+y || 0), 0), 0);
  if (sum !== 100 && !confirm(`Το σύνολο των μονάδων είναι ${sum}, όχι 100. Δημιουργία παρ' όλα αυτά;`)) return;
  const figErr = exs.some((ex) => ex.status === "done" && ex.data.figure && ex.fig &&
    !((ex.fig.mode === "original" ? ex.fig.crop : ex.fig.render) || {}).png);
  if (figErr && !confirm("Κάποιο σχήμα δεν σχεδιάστηκε και θα λείπει από το Word. Συνέχεια;")) return;

  const btn = $("#btn-build");
  btn.disabled = true;
  btn.textContent = "Δημιουργία…";
  try {
    const res = await api.build(examPayload());
    S.lastBuild = res;
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
    const lab = el("label", { class: s.provider === k ? "sel" : "" },
      el("input", { type: "radio", name: "prov", value: k, checked: s.provider === k, onchange: () => {
        s.provider = k; fillSettings();
      } }), v);
    pg.append(lab);
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
  $("#s-template").textContent = s.template_path || "Ενσωματωμένο πρότυπο";
  $("#s-outdir").textContent = s.output_dir;
  $("#s-sublevel").value = s.sublevel_style;
  $("#s-align").value = s.points_align;
  $("#s-rules-status").textContent = st.rules_custom ? "Χρησιμοποιούνται δικοί σας κανόνες." : "Χρησιμοποιούνται οι αρχικοί κανόνες.";
  $("#s-version").textContent = `Έκδοση ${st.version}`;
  updateTemplateHint();
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
  };
}

async function saveSettings(close = true) {
  S.app = await api.save_settings(collectSettings());
  fillSettings();
  if (close) $("#settings").hidden = true;
  if ($("#tab-review").classList.contains("active")) renderReview();
}

function updateTemplateHint() {
  const s = S.app.settings;
  const h = $("#template-hint");
  if (!s.template_path) {
    h.className = "hint-box warn";
    h.innerHTML = "";
    h.append("Χρησιμοποιείται το ενσωματωμένο πρότυπο. Για τη δική σας μορφή (κεφαλίδα, υποσέλιδο, γραμματοσειρές) ",
      el("a", { href: "#", text: "επιλέξτε ένα δικό σας διαγώνισμα", onclick: async (e) => { e.preventDefault(); S.app = await api.choose_template(); fillSettings(); } }),
      " ως πρότυπο.");
  } else {
    h.className = "hint-box" + (S.app.template_exists ? "" : " warn");
    h.textContent = S.app.template_exists ? `Πρότυπο: ${s.template_path}` : `Το πρότυπο δεν βρέθηκε: ${s.template_path}`;
  }
}

function bindSettings() {
  $("#btn-settings").onclick = () => { fillSettings(); $("#settings").hidden = false; };
  $("[data-close]", $("#settings")).onclick = () => { $("#settings").hidden = true; };
  $("#settings").addEventListener("click", (e) => { if (e.target.id === "settings") $("#settings").hidden = true; });
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
}

/* ================================================================== εκκίνηση */
async function main() {
  await initBridge();
  S.app = await api.get_state();
  const s = S.app.settings;
  $("#f-title").value = s.last_title || "";
  $("#f-subtitle").value = s.last_subtitle || "";
  $("#f-footer").value = s.footer_text || "";
  $("#btn-today").onclick = () => { $("#f-date").value = S.app.today; };
  $("#f-nthemes").onchange = (e) => setThemeCount(+e.target.value);
  $("#btn-transcribe").onclick = onTranscribeClick;
  $("#btn-back").onclick = () => switchTab("setup");
  $("#btn-build").onclick = onBuild;
  $("#lightbox").onclick = () => { $("#lightbox").hidden = true; };
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { $("#lightbox").hidden = true; $("#settings").hidden = true; }
  });
  for (const b of $$("#steps button")) b.onclick = () => { if (b.dataset.tab === "done") renderDone(); switchTab(b.dataset.tab); };
  bindSettings();
  updateTemplateHint();
  await setThemeCount(+$("#f-nthemes").value);
}

window.addEventListener("DOMContentLoaded", () => {
  main().catch((e) => { console.error(e); toast("Σφάλμα εκκίνησης: " + (e.message || e), true, 10000); });
});
