// Pirouette sur le téléphone et la tablette (iPhone, iPad, Android…) : tes quiz et tes flashcards, copiés en ligne par ton ordinateur (voir app/sync.py).
// Cette page lit la copie, la garde sur l'appareil (elle marche hors connexion) et envoie chaque réponse en ligne :
// le Mac la reprend à sa prochaine synchro. Rien ne s'écrit jamais dans les quiz ou les cartes depuis ici.
"use strict";

const CONFIG = window.PIROUETTE_CONFIG || {};
const BLANK = "_____";
const $ = (sel) => document.querySelector(sel);
const app = $("#app");

// ---------- Petit rangement local (IndexedDB : de la place pour des milliers de cartes) ----------
const kv = (() => {
  let db;
  const open = () => db || (db = new Promise((resolve, reject) => {
    const req = indexedDB.open("pirouette", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("kv");
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  }));
  const run = async (mode, fn) => {
    const store = (await open()).transaction("kv", mode).objectStore("kv");
    return new Promise((resolve, reject) => {
      const req = fn(store);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  };
  return {
    get: (key) => run("readonly", (s) => s.get(key)),
    set: (key, value) => run("readwrite", (s) => s.put(value, key)),
    del: (key) => run("readwrite", (s) => s.delete(key)),
  };
})();

// ---------- Dates à l'heure locale (comme sur le Mac) ----------
const pad = (n) => String(n).padStart(2, "0");
const dayOf = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const stamp = (d = new Date()) => `${dayOf(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
const parseDay = (s) => { if (!s) return null; const [y, m, d] = s.slice(0, 10).split("-").map(Number); return y ? new Date(y, m - 1, d) : null; };
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const daysBetween = (a, b) => Math.round((b - a) / 86400000);
const today = () => parseDay(dayOf(new Date()));

// ---------- Répétition espacée : la même que sur le Mac (app/srs.py) ----------
const SRS = { START: 2.5, MIN: 1.3, MAX: 3.2, MAX_INTERVAL: 365, LEGACY: 3 };
const isNew = (card) => !card.reviews;
function dueDate(card, now) {
  const due = parseDay(card.due);
  if (due) return due;
  if (card.status === "known") return addDays(parseDay(card.last_reviewed) || now, SRS.LEGACY);
  return now;
}
const isDue = (card, now) => dueDate(card, now) <= now;
function intervals(card) {
  let interval = card.interval;
  if (interval == null) interval = card.status === "known" ? SRS.LEGACY : 0;
  const ease = card.ease ?? SRS.START;
  let good, hard, easy;
  if (interval <= 0) [good, hard, easy] = [1, 1, 4];
  else if (interval < 3) [good, hard, easy] = [3, 2, 5];
  else {
    good = Math.round(interval * ease);
    hard = Math.max(interval + 1, Math.round(interval * 1.2));
    easy = Math.round(interval * ease * 1.3);
  }
  const cap = (n) => Math.max(1, Math.min(SRS.MAX_INTERVAL, n));
  return { again: 0, hard: cap(hard), good: cap(Math.max(good, hard + 1)), easy: cap(Math.max(easy, good + 2)) };
}
function examCap(days, now, exam) {
  if (!exam || days <= 0) return days;
  const left = daysBetween(now, exam);
  if (left <= 0) return left > -7 ? 1 : days;
  if (left <= 14) return Math.min(days, Math.max(1, Math.round(left / 3)));
  return Math.min(days, left - 1);
}
function schedule(card, rating, at, exam) {
  const when = new Date(at);
  const now = parseDay(dayOf(when));
  const days = examCap(intervals(card)[rating], now, exam ? parseDay(exam) : null);
  let ease = card.ease ?? SRS.START;
  if (rating === "again") { ease -= 0.2; card.lapses = (card.lapses || 0) + (isNew(card) ? 0 : 1); }
  else if (rating === "hard") ease -= 0.15;
  else if (rating === "easy") ease += 0.15;
  Object.assign(card, {
    status: rating === "again" ? "review" : "known", interval: days,
    ease: Math.round(Math.min(SRS.MAX, Math.max(SRS.MIN, ease)) * 100) / 100,
    due: dayOf(addDays(now, days)), reviews: (card.reviews || 0) + 1, last_reviewed: at, last_rating: rating,
  });
}

// ---------- Réponses tapées : justes à quelques détails près (comme sur le Mac) ----------
function normalize(s) {
  return String(s).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ")
    .replace(/\b(le|la|les|l|un|une|des|du|de|d|the|a|an)\b/g, " ").replace(/\s+/g, " ").trim();
}
function editDistance(a, b) {
  let row = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const next = [i];
    for (let j = 1; j <= b.length; j++) next[j] = Math.min(row[j] + 1, next[j - 1] + 1, row[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    row = next;
  }
  return row[b.length];
}
function typedIsRight(given, expected) {
  const a = normalize(given), b = normalize(expected);
  return a === b || (b.length >= 5 && editDistance(a, b) <= Math.floor(b.length / 6));
}

// ---------- Texte ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const rich = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>");
const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;
const shuffle = (list) => { const a = [...list]; for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : "10000000-1000-4000-8000-100000000000".replace(/[018]/g,
  (c) => (c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4)))).toString(16)));
const COLORS = ["#B4532A", "#5B7A99", "#6E8B6A", "#8E7499", "#A88A5C", "#4F8A8B", "#A0606E", "#8A8A45", "#5F649C", "#7D6B5D"];
const colorOf = (course) => COLORS[(Number.isInteger(course?.color) ? course.color : 0) % COLORS.length];
function toast(text) {
  const box = $("#toast");
  box.textContent = text;
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { box.hidden = true; }, 3200);
}
const ICON = {
  back: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  close: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  sync: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16m0 4v-4h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

// ---------- En ligne (Supabase, sans bibliothèque) ----------
const S = { session: null, owner: null, docs: null, hashes: {}, outbox: [], log: [], remote: [], lastSync: null, syncing: false };

async function request(path, { method = "GET", body, auth = true, headers = {}, params } = {}) {
  if (!CONFIG.url || !CONFIG.key) throw new Error("Cette page n'est pas encore branchée au service en ligne.");
  const url = new URL(CONFIG.url.replace(/\/$/, "") + path);
  for (const [k, v] of Object.entries(params || {})) url.searchParams.set(k, v);
  const h = { apikey: CONFIG.key, "Content-Type": "application/json", ...headers };
  if (auth) h.Authorization = `Bearer ${await token()}`;
  let res;
  try {
    res = await fetch(url, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw Object.assign(new Error("Pas de connexion"), { offline: true });
  }
  const text = await res.text();
  const data = text ? (() => { try { return JSON.parse(text); } catch { return text; } })() : null;
  if (!res.ok) {
    const message = (data && (data.message || data.msg || data.error_description)) || `Erreur ${res.status}`;
    throw Object.assign(new Error(message), { status: res.status });
  }
  return data;
}
async function keepSession(data) {
  S.session = { access_token: data.access_token, refresh_token: data.refresh_token,
    expires_at: Math.floor(Date.now() / 1000) + (data.expires_in || 3600), user_id: data.user?.id || S.session?.user_id };
  await kv.set("session", S.session);
}
async function token() {
  if (!S.session) throw new Error("Pas encore jumelé");
  if (S.session.expires_at - 60 > Date.now() / 1000) return S.session.access_token;
  try {
    await keepSession(await request("/auth/v1/token?grant_type=refresh_token", { method: "POST", auth: false,
      body: { refresh_token: S.session.refresh_token } }));
  } catch (err) {
    if (err.status === 400 || err.status === 401) await forget("Ta connexion a expiré : jumelle à nouveau cet appareil depuis ton ordinateur.");
    throw err;
  }
  return S.session.access_token;
}
function deviceName() {
  const ua = navigator.userAgent;
  if (/iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "iPad";
  if (/iPhone/.test(ua)) return "iPhone";
  if (/Android/.test(ua)) return "Android";
  return "Navigateur";
}
async function pair(code) {
  if (!S.session) await keepSession(await request("/auth/v1/signup", { method: "POST", auth: false, body: { data: {} } }));
  S.owner = await request("/rest/v1/rpc/sync_pair", { method: "POST", body: { pair_code: code.trim().toUpperCase(), device_name: deviceName() } });
  await kv.set("owner", S.owner);
  await syncNow({ quiet: true });
}
async function forget(message) {
  for (const key of ["session", "owner", "docs", "hashes", "remote", "outbox", "log", "lastSync"]) await kv.del(key);
  Object.assign(S, { session: null, owner: null, docs: null, hashes: {}, outbox: [], log: [], remote: [], lastSync: null });
  if (message) toast(message);
  route();
}

// Synchro : d'abord envoyer les réponses en attente, puis relire ce qui a changé sur le Mac
async function syncNow({ quiet = false } = {}) {
  if (!S.owner || S.syncing) return;
  S.syncing = true;
  document.body.classList.add("syncing");
  try {
    if (S.outbox.length) {
      await request("/rest/v1/sync_events", { method: "POST", params: { on_conflict: "id" },
        headers: { Prefer: "resolution=ignore-duplicates,return=minimal" },
        body: S.outbox.map((e) => ({ id: e.id, owner: S.owner, kind: e.kind, payload: e.payload })) });
      S.outbox = [];
      await kv.set("outbox", S.outbox);
    }
    const list = await request("/rest/v1/sync_docs", { params: { select: "kind,id,hash" } });
    const docs = S.docs || { meta: null, deck: {}, quiz: {} };
    const wanted = list.filter((r) => S.hashes[`${r.kind}:${r.id}`] !== r.hash);
    for (const kind of ["meta", "deck", "quiz"]) {
      const ids = wanted.filter((r) => r.kind === kind).map((r) => r.id);
      for (let i = 0; i < ids.length; i += 30) {
        const rows = await request("/rest/v1/sync_docs", { params: { select: "kind,id,hash,data", kind: `eq.${kind}`,
          id: `in.(${ids.slice(i, i + 30).join(",")})` } });
        for (const row of rows) {
          if (kind === "meta") docs.meta = row.data; else docs[kind][row.id] = row.data;
          S.hashes[`${kind}:${row.id}`] = row.hash;
        }
      }
    }
    const present = new Set(list.map((r) => `${r.kind}:${r.id}`));
    for (const kind of ["deck", "quiz"]) {
      for (const id of Object.keys(docs[kind])) {
        if (!present.has(`${kind}:${id}`)) { delete docs[kind][id]; delete S.hashes[`${kind}:${id}`]; }
      }
    }
    S.remote = await request("/rest/v1/sync_events", { params: { select: "id,kind,payload" } });
    S.docs = docs;
    S.lastSync = new Date().toISOString();
    await Promise.all([kv.set("docs", docs), kv.set("hashes", S.hashes), kv.set("remote", S.remote), kv.set("lastSync", S.lastSync)]);
    view = null;
    if (!quiet && !session) route();
  } catch (err) {
    if (!err.offline && !quiet) toast(err.message);
    if (err.status === 401 || err.status === 403) await forget("Cet appareil n'est plus jumelé : refais-le depuis ton ordinateur.");
  } finally {
    S.syncing = false;
    document.body.classList.remove("syncing");
    refreshSyncLine();
  }
}

// Une réponse : gardée ici (rejouée tant que le Mac ne l'a pas reprise), et envoyée dès qu'il y a du réseau
async function addEvent(kind, payload) {
  const event = { id: uuid(), kind, payload: { ...payload, at: stamp() } };
  S.outbox.push(event);
  S.log.push(event);
  const since = stamp(addDays(new Date(), -4));
  S.log = S.log.filter((e) => e.payload.at >= since);
  view = null;
  await Promise.all([kv.set("outbox", S.outbox), kv.set("log", S.log)]);
  clearTimeout(addEvent.timer);
  addEvent.timer = setTimeout(() => syncNow({ quiet: true }), 1500);
}

// La copie du Mac + les réponses pas encore reprises par le Mac (les siennes et celles de l'autre appareil)
let view = null;
function current() {
  if (view) return view;
  const v = structuredClone(S.docs || { meta: null, deck: {}, quiz: {} });
  const seen = new Set();
  const events = [...S.remote, ...S.log, ...S.outbox].filter((e) => !seen.has(e.id) && seen.add(e.id))
    .sort((a, b) => String(a.payload.at).localeCompare(String(b.payload.at)));
  const exams = Object.fromEntries((v.meta?.courses || []).map((c) => [c.id, c.exam]));
  for (const { kind, payload: p } of events) {
    if (kind === "card") {
      const card = v.deck[p.course_id]?.cards.find((c) => c.id === p.card_id);
      if (card && (card.last_reviewed || "") < p.at) schedule(card, p.rating, p.at, exams[p.course_id]);
    } else if (kind === "answers") {
      const quiz = v.quiz[p.quiz_id];
      if (!quiz) continue;
      const stats = quiz.stats || (quiz.stats = {});
      for (const a of p.answers || []) {
        const stat = stats[a.index] || (stats[a.index] = { right: 0, wrong: 0 });
        if ((stat.date || "") >= p.at) continue;
        stat[a.correct ? "right" : "wrong"] += 1;
        Object.assign(stat, { last: a.correct, date: p.at });
      }
    } else if (kind === "attempt") {
      const quiz = v.quiz[p.quiz_id];
      if (quiz && !(quiz.attempts || []).some((t) => t.date === p.at)) (quiz.attempts || (quiz.attempts = [])).push({ date: p.at, score: p.score, total: p.total });
    }
  }
  // Nouvelles cartes déjà vues aujourd'hui (sur le Mac, ou ici en attendant le Mac) : pour le quota du jour
  const day = dayOf(new Date());
  v.newToday = Object.values(v.meta?.activity?.[day] || {}).reduce((n, c) => n + (c.new || 0), 0)
    + [...S.remote, ...S.outbox].filter((e) => e.kind === "card" && e.payload.new && e.payload.at.startsWith(day)).length;
  return (view = v);
}

// ---------- Ce qu'il y a à réviser ----------
function courses() {
  const v = current();
  const archived = new Set((v.meta?.folders || []).filter((f) => f.archived).map((f) => f.id));
  return (v.meta?.courses || []).map((c) => ({ ...c, archived: archived.has(c.folder_id) }));
}
function todayCards(courseIds) {
  const v = current(), now = today();
  const due = [], fresh = [];
  let needed = 0;
  for (const course of courses()) {
    if (courseIds ? !courseIds.includes(course.id) : course.archived) continue;
    const cards = v.deck[course.id]?.cards || [];
    const examDays = course.exam ? daysBetween(now, parseDay(course.exam)) : null;
    const mine = [];
    for (const card of cards) {
      const item = { course, card, exam: examDays > 0 ? examDays : 9999 };
      if (isNew(card) && card.status !== "known") mine.push(item);
      else if (isDue(card, now)) due.push(item);
    }
    fresh.push(...mine);
    if (examDays > 0 && examDays <= 90 && mine.length) needed += Math.ceil(mine.length / Math.max(1, examDays - 2));
  }
  due.sort((a, b) => dueDate(a.card, now) - dueDate(b.card, now));
  fresh.sort((a, b) => a.exam - b.exam);
  const quota = Math.max(0, Math.max(v.meta?.new_per_day ?? 20, needed) - v.newToday);
  return [...due, ...fresh.slice(0, quota)];
}
function quizzesOf(courseId) {
  return Object.values(current().quiz).filter((q) => q.course_id === courseId)
    .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
}
function mastery(courseId) {
  const cards = current().deck[courseId]?.cards || [];
  const known = cards.filter((c) => c.status === "known").length;
  return { cards: cards.length, known, pct: cards.length ? Math.round((100 * known) / cards.length) : 0 };
}

// ---------- Écrans ----------
let session = null;  // séance en cours (cartes ou quiz)

function route() {
  if (!CONFIG.url) return renderMissing();
  if (!S.owner) return renderPair();
  if (session) return;  // une séance ne se coupe pas toute seule
  const [, page, id] = location.hash.replace(/^#\/?/, "#/").split("/");
  if (page === "m" && id) return renderSubject(decodeURIComponent(id));
  renderHome();
}
window.addEventListener("hashchange", () => { if (session) endSession(false); route(); });

function renderMissing() {
  app.innerHTML = `<section class="center-page"><div class="logo-tile">${logoSvg()}</div>
    <h1>Pirouette</h1><p class="muted">Cette version n'est pas branchée au service en ligne.</p></section>`;
}

function logoSvg() {
  return '<img src="icon.svg" alt="" width="92" height="92">';
}

function renderPair() {
  const fromLink = (location.hash.match(/jumeler=([A-Z0-9-]+)/i) || [])[1] || "";
  const ios = /iPhone|iPad/.test(navigator.userAgent) || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);
  const installed = navigator.standalone || matchMedia("(display-mode: standalone)").matches;
  const pretty = fromLink.replace(/-/g, "").replace(/^(.{4})(.+)$/, "$1-$2").toUpperCase();
  app.innerHTML = `<section class="center-page pair">
    <div class="logo-tile">${logoSvg()}</div>
    <h1>Pirouette</h1>
    <p class="lead">Tes quiz et tes flashcards, aussi ici.</p>
    ${ios && !installed ? `<div class="install">
      <b>D'abord, ajoute Pirouette à ton écran d'accueil</b>
      <ol><li>Touche <span class="share-ic" aria-label="Partager">${shareIcon()}</span> en bas de Safari</li>
      <li>Puis « Sur l'écran d'accueil »</li>
      <li>Ouvre Pirouette depuis l'écran d'accueil et tape ce code${pretty ? " :" : ""}</li></ol>
      ${pretty ? `<div class="big-code">${esc(pretty)}</div>` : ""}
      <button class="link" type="button" id="here">Ou continuer dans Safari</button>
    </div>` : ""}
    ${/Android/.test(navigator.userAgent) && !installed ? `<p class="muted small android-tip">Astuce : menu <b>⋮</b> › <b>Installer l'application</b>
      pour avoir Pirouette sur ton écran d'accueil.</p>` : ""}
    <form class="pair-form" id="pair-form" ${ios && !installed ? "hidden" : ""}>
      <label for="code">Le code affiché sur ton ordinateur</label>
      <input id="code" autocomplete="one-time-code" autocapitalize="characters" spellcheck="false" placeholder="ABCD-1234" value="${esc(pretty)}" maxlength="12">
      <button class="primary" type="submit">Jumeler</button>
      <p class="muted small">Sur ton ordinateur : Pirouette › Réglages › Téléphone et tablette › Ajouter un téléphone ou une tablette.</p>
    </form>
  </section>`;
  $("#here")?.addEventListener("click", () => { $(".install").hidden = true; $("#pair-form").hidden = false; });
  $("#pair-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = e.target.querySelector("button");
    button.disabled = true;
    button.textContent = "Un instant…";
    try {
      await pair($("#code").value);
      history.replaceState(null, "", location.pathname);
      toast("C'est jumelé !");
      route();
    } catch (err) {
      toast(err.offline ? "Pas de connexion à internet." : err.message);
      button.disabled = false;
      button.textContent = "Jumeler";
    }
  });
}
const shareIcon = () => '<svg viewBox="0 0 24 24"><path d="M12 15V3m0 0L8 7m4-4 4 4M6 11H5v10h14V11h-1" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';

function hello(name) {
  const h = new Date().getHours();
  const word = h >= 18 || h < 4 ? "Bonsoir" : "Bonjour";
  return name ? `${word}, ${esc(name.split(" ")[0])}` : word;
}
function syncLine() {
  if (!S.lastSync) return "Pas encore synchronisé";
  const minutes = Math.round((Date.now() - new Date(S.lastSync)) / 60000);
  const when = minutes < 1 ? "à l'instant" : minutes < 60 ? `il y a ${minutes} min` : minutes < 1440 ? `il y a ${Math.round(minutes / 60)} h`
    : `il y a ${plural(Math.round(minutes / 1440), "jour", "jours")}`;
  const waiting = S.outbox.length ? ` · ${plural(S.outbox.length, "réponse", "réponses")} à envoyer` : "";
  return `Synchronisé ${when}${waiting}`;
}
function refreshSyncLine() {
  const line = $("#sync-line");
  if (line) line.textContent = syncLine();
}

function renderHome() {
  const v = current();
  if (!v.meta) {
    app.innerHTML = `<section class="center-page"><div class="logo-tile">${logoSvg()}</div><h1>Presque prêt</h1>
      <p class="muted">Ouvre Pirouette sur ton ordinateur : il envoie tes quiz et tes cartes dans un instant.</p>
      <button class="primary" type="button" id="retry">Réessayer</button></section>`;
    $("#retry").addEventListener("click", () => syncNow());
    return;
  }
  const due = todayCards();
  const list = courses();
  const folders = v.meta.folders || [];
  const groups = [...folders.filter((f) => !f.archived).map((f) => ({ name: f.name, items: list.filter((c) => c.folder_id === f.id) })),
    { name: folders.length ? "Sans semestre" : "", items: list.filter((c) => !folders.some((f) => f.id === c.folder_id)) }]
    .filter((g) => g.items.length);
  const archived = list.filter((c) => c.archived);
  const row = (c) => {
    const n = todayCards([c.id]).length, q = quizzesOf(c.id).length, m = mastery(c.id);
    return `<a class="row" href="#/m/${encodeURIComponent(c.id)}" style="--c:${colorOf(c)}">
      <span class="ring" style="--m:${m.pct}%"><i></i></span>
      <span class="row-text"><b>${esc(c.name)}</b><small>${n ? `<em>${n}</em> à revoir` : m.cards ? "À jour" : ""}${q ? `${n || m.cards ? " · " : ""}${plural(q, "quiz", "quiz")}` : ""}</small></span>
      ${ICON.chevron}</a>`;
  };
  app.innerHTML = `<header class="top"><h1>${hello(v.meta.name)}</h1>
      <button class="icon-btn" type="button" id="sync" aria-label="Synchroniser">${ICON.sync}</button></header>
    <section class="today ${due.length ? "" : "done"}">
      <div><b>${due.length ? `${plural(due.length, "carte", "cartes")} aujourd'hui` : "Tout est à jour aujourd'hui"}</b>
      <small>${due.length ? `environ ${Math.max(1, Math.round(due.length * 0.2))} min` : "Reviens demain, ou fais un quiz"}</small></div>
      ${due.length ? `<button class="primary" type="button" id="go">Commencer</button>` : ""}
    </section>
    ${groups.map((g) => `${g.name ? `<h2>${esc(g.name)}</h2>` : `<h2>Matières</h2>`}<nav class="list">${g.items.map(row).join("")}</nav>`).join("")}
    ${archived.length ? `<details class="archived"><summary>Semestres archivés · ${archived.length}</summary><nav class="list">${archived.map(row).join("")}</nav></details>` : ""}
    ${list.length ? "" : `<p class="muted center">Pas encore de matière : crée tes quiz et tes cartes sur ton ordinateur.</p>`}
    <footer class="foot"><span id="sync-line">${syncLine()}</span> · <button class="link" type="button" id="unpair">Dissocier</button></footer>`;
  $("#go")?.addEventListener("click", () => startCards(due, "Révision du jour"));
  $("#sync").addEventListener("click", () => syncNow());
  $("#unpair").addEventListener("click", async () => {
    if (!confirm("Dissocier cet appareil de ton ordinateur ? Tes réponses pas encore envoyées seraient perdues.")) return;
    try { await request("/rest/v1/sync_devices", { method: "DELETE", params: { device: `eq.${S.session.user_id}` } }); } catch {}
    forget("Cet appareil n'est plus jumelé.");
  });
}

function renderSubject(id) {
  const course = courses().find((c) => c.id === id);
  if (!course) { location.hash = "#/"; return; }
  const due = todayCards([id]);
  const all = (current().deck[id]?.cards || []).map((card) => ({ course, card }));
  const quizzes = quizzesOf(id);
  const m = mastery(id);
  app.innerHTML = `<header class="top sub"><a class="icon-btn" href="#/" aria-label="Retour">${ICON.back}</a></header>
    <h1 class="subject" style="--c:${colorOf(course)}"><span class="dot"></span>${esc(course.name)}</h1>
    ${m.cards ? `<p class="muted">${m.pct} % des cartes sues${course.exam ? ` · partiels le ${new Date(parseDay(course.exam)).toLocaleDateString("fr-FR", { day: "numeric", month: "long" })}` : ""}</p>` : ""}
    ${all.length ? `<h2>Flashcards</h2><nav class="list">
      <button class="row" type="button" id="due" ${due.length ? "" : "disabled"}><span class="row-text"><b>Révision du jour</b><small>${due.length ? `<em>${due.length}</em> à revoir` : "Rien à revoir aujourd'hui"}</small></span>${ICON.chevron}</button>
      <button class="row" type="button" id="all"><span class="row-text"><b>Toutes les cartes</b><small>${plural(all.length, "carte", "cartes")}, dans le désordre</small></span>${ICON.chevron}</button>
    </nav>` : ""}
    ${quizzes.length ? `<h2>Quiz</h2><nav class="list">${quizzes.map((q) => {
      const last = (q.attempts || []).at(-1);
      return `<button class="row" type="button" data-quiz="${esc(q.id)}"><span class="row-text"><b>${esc(q.title || "Quiz")}</b>
        <small>${plural(q.questions.length, "question", "questions")}${last ? ` · dernier : ${last.score}/${last.total}` : ""}</small></span>${ICON.chevron}</button>`;
    }).join("")}</nav>` : ""}
    ${!all.length && !quizzes.length ? `<p class="muted center">Rien dans cette matière pour l'instant.</p>` : ""}`;
  $("#due")?.addEventListener("click", () => startCards(due, course.name));
  $("#all")?.addEventListener("click", () => startCards(shuffle(all), course.name, { free: true }));
  app.querySelectorAll("[data-quiz]").forEach((b) => b.addEventListener("click", () => startQuiz(current().quiz[b.dataset.quiz])));
}

// ---------- Séance de flashcards ----------
function startCards(items, title, { free = false } = {}) {
  if (!items.length) return;
  session = { type: "cards", items: items.slice(0, 200), index: 0, ok: 0, title, free, back: location.hash || "#/" };
  drawCard();
}
function sessionHead() {
  const s = session, total = s.type === "cards" ? s.items.length : s.questions.length;
  return `<header class="session-top"><button class="icon-btn" type="button" id="quit" aria-label="Arrêter">${ICON.close}</button>
    <div class="bar"><i style="width:${(100 * s.index) / total}%"></i></div><span class="count">${Math.min(s.index + 1, total)}/${total}</span></header>`;
}
function drawCard() {
  const s = session;
  if (s.index >= s.items.length) return finish();
  const { course, card } = s.items[s.index];
  app.innerHTML = `${sessionHead()}
    <div class="stage"><div class="flash" id="flash" style="--c:${colorOf(course)}" role="button" tabindex="0" aria-label="Retourner la carte">
      <div class="face front"><span class="tag"><i></i>${esc(course.name)}</span><div class="txt">${rich(card.front)}</div><small class="hint">Touche pour voir la réponse</small></div>
      <div class="face back"><span class="tag"><i></i>${esc(course.name)}</span><div class="txt">${rich(card.back)}</div></div>
    </div></div>
    <div class="rates" id="rates" hidden>
      <button class="rate again" type="button" data-rate="again">Je ne savais pas</button>
      <button class="rate good" type="button" data-rate="good">Je savais</button>
    </div>`;
  $("#quit").addEventListener("click", () => endSession(true));
  const flash = $("#flash");
  // Une touche retourne la carte ; une fois la réponse vue, on peut la retourner autant qu'on veut, et noter
  let revealed = false, moved = 0;
  const back = "rotateY(180deg)";
  flash.addEventListener("click", () => {
    if (moved > 10) return;
    flash.classList.toggle("flipped");
    revealed = true;
    $("#rates").hidden = false;
    flash.querySelector(".hint").textContent = "Touche pour retourner la carte";
  });
  app.querySelectorAll("[data-rate]").forEach((b) => b.addEventListener("click", () => rate(b.dataset.rate)));
  // Glisser : à droite « je savais », à gauche « je ne savais pas » (une fois la réponse vue)
  let x0 = null;
  flash.addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; moved = 0; }, { passive: true });
  flash.addEventListener("touchmove", (e) => {
    if (x0 === null || !revealed) return;
    const dx = e.touches[0].clientX - x0;
    moved = Math.abs(dx);
    flash.style.transform = `translateX(${dx}px) rotate(${dx / 25}deg) ${flash.classList.contains("flipped") ? back : ""}`;
    flash.dataset.lean = dx > 40 ? "good" : dx < -40 ? "again" : "";
  }, { passive: true });
  flash.addEventListener("touchend", (e) => {
    if (x0 === null) return;
    const dx = e.changedTouches[0].clientX - x0;
    x0 = null;
    if (revealed && Math.abs(dx) > 90) return rate(dx > 0 ? "good" : "again");
    flash.style.transform = "";
    flash.dataset.lean = "";
  });
}
function rate(rating) {
  const s = session, { course, card } = s.items[s.index];
  if (!s.free || isDue(card, today()) || isNew(card)) {
    addEvent("card", { course_id: course.id, card_id: card.id, rating, new: isNew(card) });
  }
  s.ok += rating === "good";
  s.index += 1;
  drawCard();
}

// ---------- Séance de quiz ----------
function startQuiz(quiz) {
  if (!quiz?.questions?.length) return;
  const questions = quiz.questions.map((q, index) => ({ ...q, index,
    choices: q.type === "vrai_faux" ? q.choices : shuffle(q.choices || []) }));
  session = { type: "quiz", quiz, questions: shuffle(questions).slice(0, 30), index: 0, ok: 0, back: location.hash || "#/" };
  drawQuestion();
}
const typed = (q) => q.type === "reponse_courte" || q.type === "texte_a_trous" || !q.choices?.length;
function drawQuestion() {
  const s = session;
  if (s.index >= s.questions.length) return finish();
  const q = s.questions[s.index];
  const stem = q.type === "texte_a_trous" ? esc(q.question).replace(BLANK, '<span class="blank">______</span>') : rich(q.question);
  app.innerHTML = `${sessionHead()}
    <section class="question"><p class="q">${stem}</p>
      ${typed(q) ? `<form id="typed" class="typed"><input id="answer" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="Ta réponse">
          <button class="primary" type="submit">Valider</button><button class="link" type="button" id="dunno">Je ne sais pas</button></form>`
        : `<div class="choices">${q.choices.map((c, i) => `<button class="choice" type="button" data-i="${i}">${rich(c)}</button>`).join("")}</div>`}
      <div class="feedback" id="feedback" hidden></div></section>`;
  $("#quit").addEventListener("click", () => endSession(true));
  if (typed(q)) {
    $("#typed").addEventListener("submit", (e) => {
      e.preventDefault();
      const given = $("#answer").value.trim();
      if (!given) return;
      $("#answer").disabled = true;
      e.target.querySelectorAll("button").forEach((b) => (b.hidden = true));
      if (typedIsRight(given, q.answer)) return answered(q, true);
      // Pas tout à fait la même réponse : on montre la réponse attendue, et chacun se corrige
      showFeedback(q, null, `<div class="self"><button class="primary" type="button" id="self-ok">J'avais bon</button>
        <button class="ghost" type="button" id="self-ko">J'avais faux</button></div>`);
      $("#self-ok").addEventListener("click", () => answered(q, true, { shown: true }));
      $("#self-ko").addEventListener("click", () => answered(q, false, { shown: true }));
    });
    $("#dunno").addEventListener("click", () => { $("#answer").disabled = true; answered(q, false); });
    setTimeout(() => $("#answer")?.focus(), 50);
  } else {
    app.querySelectorAll(".choice").forEach((b) => b.addEventListener("click", () => {
      const given = q.choices[Number(b.dataset.i)];
      const correct = normalize(given) === normalize(q.answer);
      app.querySelectorAll(".choice").forEach((x) => {
        x.disabled = true;
        if (normalize(q.choices[Number(x.dataset.i)]) === normalize(q.answer)) x.classList.add("right");
      });
      if (!correct) b.classList.add("wrong");
      answered(q, correct);
    }));
  }
}
function showFeedback(q, correct, extra = "") {
  const fb = $("#feedback");
  fb.className = `feedback ${correct === null ? "neutral" : correct ? "ok" : "ko"}`;
  fb.innerHTML = `${correct === null ? `<p><b>Réponse attendue :</b> ${rich(q.answer)}</p>`
      : correct ? "<p><b>Bonne réponse !</b></p>" : `<p><b>Pas tout à fait.</b>${typed(q) || q.type === "vrai_faux" ? ` La réponse : ${rich(q.answer)}` : ""}</p>`}
    ${q.explanation ? `<p class="expl">${rich(q.explanation)}</p>` : ""}${extra}`;
  fb.hidden = false;
  fb.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function answered(q, correct, { shown = false } = {}) {
  const s = session;
  s.ok += correct;
  s.answered = true;
  addEvent("answers", { quiz_id: s.quiz.id, answers: [{ index: q.index, correct }] });
  if (shown) { s.index += 1; return drawQuestion(); }
  showFeedback(q, correct, `<button class="primary next" type="button" id="next">${s.index + 1 < s.questions.length ? "Suivante" : "Voir le score"}</button>`);
  $("#next").addEventListener("click", () => { s.index += 1; drawQuestion(); });
}

// ---------- Fin de séance ----------
function finish() {
  const s = session;
  const total = s.type === "cards" ? s.items.length : s.questions.length;
  if (s.type === "quiz") addEvent("attempt", { quiz_id: s.quiz.id, score: s.ok, total });
  const pct = total ? Math.round((100 * s.ok) / total) : 0;
  app.innerHTML = `<section class="center-page finish"><div class="score-ring" style="--m:${pct}%"><span>${s.ok}<small>/${total}</small></span></div>
    <h1>${pct >= 80 ? "Bravo !" : pct >= 50 ? "Bien joué" : "Ça viendra"}</h1>
    <p class="muted">${s.type === "cards" ? `${plural(s.ok, "carte sue", "cartes sues")} sur ${total}` : `${plural(s.ok, "bonne réponse", "bonnes réponses")} sur ${total}`}</p>
    <button class="primary" type="button" id="back">Terminer</button></section>`;
  $("#back").addEventListener("click", () => endSession(false));
}
function endSession(ask) {
  if (!session) return;
  const started = session.index > 0 || session.answered;
  if (ask && started && session.index < (session.items || session.questions).length) {
    const text = session.type === "quiz"
      ? "Quitter le quiz ? Ta progression dans ce quiz sera perdue (tes réponses déjà données comptent quand même pour tes révisions)."
      : "Arrêter la séance ? Les cartes déjà notées sont gardées.";
    if (!confirm(text)) return;
  }
  const back = session.back;
  session = null;
  view = null;
  if (location.hash !== back) location.hash = back; else route();
}

// ---------- Démarrage ----------
(async () => {
  try {
    [S.session, S.owner, S.docs, S.hashes, S.outbox, S.log, S.remote, S.lastSync] = await Promise.all(
      ["session", "owner", "docs", "hashes", "outbox", "log", "remote", "lastSync"].map((k) => kv.get(k)));
  } catch {}
  S.hashes ||= {}; S.outbox ||= []; S.log ||= []; S.remote ||= [];
  route();
  if (S.owner) syncNow();
  // De retour sur l'app (ou du réseau qui revient) : on se remet à jour
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && S.owner) syncNow({ quiet: !!session }); });
  addEventListener("online", () => S.owner && syncNow({ quiet: true }));
  setInterval(refreshSyncLine, 30000);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
})();
