const $ = (sel) => document.querySelector(sel);
const TYPE_LABELS = { qcm: "QCM", vrai_faux: "Vrai / Faux", reponse_courte: "Réponse courte", texte_a_trous: "Texte à trous" };
const BLANK = "_____";
// Petites icônes dessinées (pas d'emoji dans l'app).
const svgIcon = (path) => `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
const ICON_EDIT = svgIcon('<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>');
const ICON_CALENDAR = svgIcon('<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>');
const ICON_ALERT = svgIcon('<path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H10l-4.5 4v-4A1.5 1.5 0 0 1 4 14.5z"/><path d="M12 7.5v3.5M12 13.6h.01"/>');
const ICON_SHARE = svgIcon('<path d="M12 15V4M8 8l4-4 4 4"/><path d="M5 12v6.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V12"/>');
const ICON_FILE = svgIcon('<path d="M14 3H6.5A1.5 1.5 0 0 0 5 4.5v15A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>');
const ICON_TRASH = svgIcon('<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>');

const state = {
  config: null,
  configTimer: null,
  course: null,     // cours affiché
  quiz: null,       // quiz complet en cours
  questions: [],    // questions de la session en cours
  index: 0,
  results: [],      // { question, given, correct }
  answered: false,  // la question affichée a-t-elle été validée ?
  fullRun: false,   // session sur toutes les questions (compte dans l'historique des scores)
  deck: null,       // paquet de flashcards du cours affiché
  newCards: null,   // cartes qui viennent d'être ajoutées (repérées dans la grille)
  tab: null,        // entrée du cours affichée : quiz, cartes ou fichiers
  createKind: null, // page de création : "quiz" ou "cartes"
  keepSelection: false, // garder les chapitres cochés en ouvrant la page de création
  cards: null,      // session de flashcards en cours
  profile: null,    // prénom affiché sur l'accueil
  excluded: {},     // par cours : chapitres décochés (tout le reste est coché, y compris les nouveaux)
  detecting: false, // l'IA est en train de repérer des chapitres
};

// Suivi des créations en arrière-plan (voir plus bas) : déclaré ici, la navigation s'en sert dès le démarrage.
const jobsState = { list: [], timer: null, open: false, keepOpen: false, seen: new Set() };

// ---------- Navigation ----------
function show(view) {
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  document.body.classList.toggle("on-home", view === "home");  // l'accueil a déjà son grand logo au centre
  window.scrollTo(0, 0);
}

// #/ accueil · #/cours mes cours · #/cours/<id> un cours · #/cours/<id>/quiz|cartes|fichiers une partie du cours
// #/cours/<id>/nouveau/quiz|cartes création · #/reviser (choix) · #/reviser/tout|dossier/<id>|cours/<id> · #/reglages
async function route() {
  const hash = location.hash;
  // Le suivi des créations se replie quand on change de page (sauf juste après avoir lancé une création).
  if (jobsState.keepOpen) jobsState.keepOpen = false;
  else foldJobs();
  const match = hash.match(/^#\/cours\/([0-9a-f]{12})(?:\/(nouveau\/)?([a-z]+))?/);
  // Lien actif du menu du haut
  const section = !hash || hash === "#/" ? "accueil"
    : hash.startsWith("#/cours") || hash.startsWith("#/nouveau-cours") ? "cours" : hash.slice(2).split("/")[0];
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("on", a.dataset.nav === section));
  try {
    if (match && match[2]) await openCreate(match[1], match[3] === "quiz" ? "quiz" : "cartes");
    else if (match) await openCourse(match[1], match[3]);
    else if (hash.startsWith("#/reglages")) await openSettings();
    else if (hash.match(/^#\/reviser\/(tout|dossier|cours)/)) await openReviewScope(...hash.slice(10).split("/"));
    else if (hash.startsWith("#/reviser")) await openReview();
    else if (hash.startsWith("#/nouveau-cours")) {
      await loadCourses();
      history.replaceState(null, "", "#/cours");
      openNewCourse();
    }
    else if (hash.startsWith("#/cours")) await loadCourses();
    else await openHome();
  } catch (err) {
    alert(err.message);
    location.hash = "#/cours";
  }
}

// ---------- Fil d'Ariane : où je suis, et un clic pour remonter ----------
// crumbs : [{ label, href }] ; le dernier est la page affichée (pas de lien).
function setCrumbs(crumbs = []) {
  const nav = $("#crumbs");
  nav.hidden = crumbs.length < 2;
  nav.innerHTML = crumbs.map((c, i) => i < crumbs.length - 1
    ? `<a href="${c.href}">${escapeHtml(c.label)}</a><span class="sep" aria-hidden="true">›</span>`
    : `<span class="here" aria-current="page">${escapeHtml(c.label)}</span>`).join("");
}
const COURSES_CRUMB = { label: "Mes cours", href: "#/cours" };
const TAB_NAMES = { chapitres: "Chapitres", quiz: "Quiz", cartes: "Flashcards", fichiers: "Fichiers" };
function courseCrumbs(course, tab = null, ...more) {
  const list = [COURSES_CRUMB, { label: course.name, href: `#/cours/${course.id}` }];
  if (tab) list.push({ label: TAB_NAMES[tab], href: `#/cours/${course.id}/${tab}` });
  return [...list, ...more.map((label) => ({ label }))];
}
window.addEventListener("hashchange", route);

async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Erreur ${response.status}`);
  }
  return response.json();
}
const jsonBody = (method, data) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

// ---------- Accueil ----------
// Une phrase d'accueil tirée au hasard à chaque ouverture ; certaines utilisent le prénom, d'autres l'heure.
const GREETINGS = [
  () => "On révise ?",
  (n) => n && `On révise, ${n} ?`,
  () => "On s'y remet ?",
  () => "Une petite pirouette dans tes cours ?",
  () => "Qu'est-ce qu'on apprend aujourd'hui ?",
  () => "Un petit quiz pour s'échauffer ?",
  () => "Allez, dix minutes de révision ?",
  () => "Quel cours au programme ?",
  () => "Tes flashcards t'attendent",
  () => "On fait travailler la mémoire ?",
  () => "Pas à pas, on y arrive",
  () => "On teste ce qui est resté ?",
  () => "Un cours, un quiz, et hop",
  () => "La mémoire, ça s'entraîne",
  () => "On révise tranquillement ?",
  (n) => n && `À toi de jouer, ${n}`,
  (n) => n && `Te revoilà, ${n} !`,
  () => "Une révision rapide ?",
  () => "On attaque quel chapitre ?",
  () => "Petite séance de révision ?",
  () => "Quelques cartes avec le café ?",
  () => "Petit rappel, grande mémoire",
];
const TIMED_GREETINGS = [
  { from: 5, to: 11, say: (n) => (n ? `Bonjour ${n}, on révise ?` : "Bonjour, on révise ?") },
  { from: 5, to: 11, say: () => "Un quiz avec le café ?" },
  { from: 12, to: 18, say: () => "Une révision pour la pause ?" },
  { from: 18, to: 22, say: (n) => (n ? `Bonsoir ${n}, on révise ?` : "Bonsoir, on révise ?") },
  { from: 18, to: 22, say: () => "Une dernière révision ce soir ?" },
  { from: 22, to: 29, say: () => "Encore debout ? Une dernière carte, alors." },
  { from: 22, to: 29, say: () => "Révision de minuit ?" },
];

function pickGreeting(name) {
  const hour = new Date().getHours();
  const h = hour < 5 ? hour + 24 : hour;
  const timed = TIMED_GREETINGS.filter((g) => h >= g.from && h < g.to).map((g) => g.say);
  const pool = Math.random() < 0.3 && timed.length ? timed : GREETINGS;
  const options = pool.map((f) => f(name)).filter(Boolean);
  let last = null;
  try { last = sessionStorage.getItem("pirouette.greeting"); } catch {}
  const choices = options.length > 1 ? options.filter((o) => o !== last) : options;
  const greeting = choices[Math.floor(Math.random() * choices.length)];
  try { sessionStorage.setItem("pirouette.greeting", greeting); } catch {}
  return greeting;
}

const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

function renderStats(stats) {
  const sub = $("#hero-sub");
  if (!stats.courses) {
    sub.textContent = "Bienvenue ! Trois étapes pour bien démarrer :";
    return;
  }
  sub.innerHTML = stats.cards_today
    ? `<b>${plural(stats.cards_today, "carte t'attend", "cartes t'attendent")}</b> aujourd'hui.`
    : stats.decks ? "Tes cartes sont à jour pour aujourd'hui." : "Prêt pour un quiz ou quelques flashcards ?";
}

// Premier lancement : trois étapes (l'IA, un semestre, un premier cours), cochées au fur et à mesure.
function renderGuide(config, folders, stats) {
  const steps = [
    { done: config.local.available || config.claude.available, title: "Installe l'IA locale",
      text: "Elle lit tes cours et écrit les quiz, sur ton Mac. Quelques clics dans Réglages.", action: `<a class="button primary small" href="#/reglages">Ouvrir les réglages</a>` },
    { done: folders.length > 0, title: "Crée ton semestre",
      text: "Tes cours s'y rangent, avec la date de tes partiels et ton plan de révision.", action: `<button class="primary small" type="button" data-guide="semestre">Créer un semestre</button>` },
    { done: stats.courses > 0, title: "Importe ton premier cours",
      text: "Un PDF, un Word, un Pages… Pirouette repère les chapitres et prépare quiz et flashcards.", action: `<button class="primary small" type="button" data-guide="cours">Importer un cours</button>` },
  ];
  // Seulement au premier lancement : dès qu'un cours existe, le guide laisse la place au bouton Réviser.
  const current = stats.courses ? -1 : steps.findIndex((st) => !st.done);
  $("#home-guide").hidden = current === -1;
  $("#home-guide").innerHTML = steps.map((st, i) => `
    <li class="guide-step${st.done ? " done" : ""}${i === current ? " current" : ""}">
      <span class="guide-num">${st.done ? "✓" : i + 1}</span>
      <div><strong>${st.title}</strong><small class="muted">${st.text}</small>${i === current ? `<div class="guide-action">${st.action}</div>` : ""}</div>
    </li>`).join("");
  return current !== -1;
}
$("#home-guide").addEventListener("click", async (e) => {
  const step = e.target.closest("[data-guide]")?.dataset.guide;
  if (step === "semestre") {
    const name = await askText("Nom du semestre", "", "ex. Semestre 1, L2 S3…");
    if (name) await api("/api/folders", jsonBody("POST", { name }));
    openHome();
  }
  if (step === "cours") {
    const folders = (await api("/api/folders")).filter((f) => !f.archived);
    openNewCourse({ folder: folders[folders.length - 1]?.id || "" });
  }
});

async function openHome() {
  const [profile, stats, config, folders] = await Promise.all([api("/api/profile"), api("/api/stats"), api("/api/config"), api("/api/folders")]);
  const guiding = renderGuide(config, folders, stats);
  renderHomeHeat(guiding);
  $("#setup-banner").hidden = guiding || config.local.available || config.claude.available;
  $(".home-actions").hidden = guiding;
  state.course = null;
  state.profile = profile;
  state.stats = stats;
  $("#greeting").textContent = pickGreeting(profile.name);
  $("#name-prompt").hidden = profile.asked;
  renderStats(stats);
  const start = $("#home-start");
  start.textContent = !stats.courses ? "Créer mon premier cours" : "Réviser";
  const exam = stats.next_exam;
  $("#home-exam").hidden = !exam;
  if (exam) $("#home-exam").innerHTML = examSentence(exam);
  setCrumbs();
  show("home");
}

$("#home-start").addEventListener("click", () => {
  const stats = state.stats || {};
  if (!stats.courses) return go("#/nouveau-cours");
  go("#/reviser");
});

async function loadCourses() {
  const [courses, orphans, folders] = await Promise.all([
    api("/api/courses"), api("/api/quizzes?orphans=true"), api("/api/folders"),
  ]);
  state.course = null;
  setCrumbs();

  $("#course-count").textContent = courses.length ? `· ${courses.length}` : "";
  $("#courses-empty").hidden = courses.length > 0;
  renderFolders(courses, folders);

  $("#orphans").hidden = orphans.length === 0;
  $("#orphan-list").innerHTML = orphans.map((q) => quizItem(q, null, null, { play: true })).join("");
  show("courses");
}

// Mes cours (l'atelier) : ce que contient chaque cours. Réviser montre la même grille, mais où tu en es.
function courseCard(c) {
  const chapters = c.files.reduce((n, f) => n + (f.chapters?.length || 0), 0);
  const parts = [plural(c.files.length, "fichier", "fichiers"), chapters ? plural(chapters, "chapitre", "chapitres") : "",
    `${c.quiz_count} quiz`, plural(c.card_count || 0, "carte", "cartes")].filter(Boolean);
  return `
    <a class="course-card" href="#/cours/${c.id}" draggable="false" data-course-card="${c.id}">
      <span class="course-main">
        <strong>${escapeHtml(c.name)}</strong>
        <small>${parts.join(" · ")}</small>
      </span>
      <span class="course-edit" title="Régler et modifier ce cours">${ICON_EDIT}</span>
    </a>`;
}

// ---------- Dossiers de cours (un semestre, une UE…) ----------
// Dossiers repliés : une préférence de cet ordinateur (les dossiers archivés sont repliés par défaut).
function folderOpen(folder) {
  try {
    const saved = JSON.parse(localStorage.getItem("pirouette.folders") || "{}");
    if (folder.id in saved) return saved[folder.id];
  } catch {}
  return !folder.archived;
}
function rememberFolder(id, open) {
  try {
    const saved = JSON.parse(localStorage.getItem("pirouette.folders") || "{}");
    saved[id] = open;
    localStorage.setItem("pirouette.folders", JSON.stringify(saved));
  } catch {}
}

const FOLDER_ICON = `<svg class="folder-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>`;

function folderHtml(folder, courses) {
  return `
    <details class="folder${folder.archived ? " is-archived" : ""}" data-folder="${folder.id}" ${folderOpen(folder) ? "open" : ""}>
      <summary class="folder-head drop-zone" data-drop-folder="${folder.id}">
        ${FOLDER_ICON}<strong>${escapeHtml(folder.name)}</strong>
        <small class="muted">${plural(courses.length, "cours", "cours")}${folder.exam_week && !folder.archived ? ` · ${examLabel(folder.exam_week)}` : ""}</small>
        <span class="folder-actions">
          <button class="icon" type="button" data-folder-rename="${folder.id}" title="Renommer le semestre" aria-label="Renommer le semestre">${ICON_EDIT}</button>
          <button class="icon" type="button" data-folder-exam="${folder.id}" title="Semaine des partiels" aria-label="Semaine des partiels">${ICON_CALENDAR}</button>
          <button class="icon" type="button" data-folder-export="${folder.id}" title="Exporter tous les quiz et flashcards du semestre" aria-label="Exporter le semestre">${ICON_SHARE}</button>
          <button class="ghost small" type="button" data-folder-archive="${folder.id}">${folder.archived ? "Désarchiver" : "Archiver"}</button>
          <button class="icon" type="button" data-folder-delete="${folder.id}" title="Supprimer le semestre (les cours sont gardés)" aria-label="Supprimer le semestre">${ICON_TRASH}</button>
        </span>
      </summary>
      <div class="course-grid drop-zone" data-drop-folder="${folder.id}">
        ${courses.length ? courses.map(courseCard).join("")
          : `<p class="folder-empty muted small-text">Semestre vide : glisse un cours ici.</p>`}
      </div>
    </details>`;
}

function renderFolders(courses, folders) {
  state.folders = folders;
  const known = new Set(folders.map((f) => f.id));
  const inFolder = (f) => courses.filter((c) => c.folder_id === f.id);
  $("#course-list").innerHTML = courses.filter((c) => !known.has(c.folder_id)).map(courseCard).join("");
  $("#folder-list").innerHTML = folders.filter((f) => !f.archived).map((f) => folderHtml(f, inFolder(f))).join("");
  const archived = folders.filter((f) => f.archived);
  $("#archived").hidden = !archived.length;
  $("#archived-list").innerHTML = archived.map((f) => folderHtml(f, inFolder(f))).join("");
  $("#folders-help").hidden = !folders.length || !courses.length;
}

$("#view-courses").addEventListener("toggle", (e) => {
  if (e.target.matches?.("details[data-folder]")) rememberFolder(e.target.dataset.folder, e.target.open);
}, true);

$("#new-folder").addEventListener("click", async () => {
  const name = await askText("Nom du semestre", "", "ex. Semestre 1, L2 S3…");
  if (!name) return;
  await api("/api/folders", jsonBody("POST", { name }));
  loadCourses();
});

$("#view-courses").addEventListener("click", async (e) => {
  const button = e.target.closest("[data-folder-rename], [data-folder-archive], [data-folder-delete], [data-folder-exam], [data-folder-export]");
  if (!button) return;
  e.preventDefault();  // un bouton dans le titre du dossier ne le replie pas
  const { folderRename, folderArchive, folderDelete, folderExam, folderExport } = button.dataset;
  if (folderExport) return exportItem("folder", folderExport, "#courses-status");
  const folder = state.folders.find((f) => f.id === (folderRename || folderArchive || folderDelete || folderExam));
  if (folderExam) return openExamDialog({ folder });
  try {
    if (folderRename) {
      const name = await askText("Nouveau nom du semestre", folder.name);
      if (!name) return;
      await api(`/api/folders/${folder.id}`, jsonBody("PATCH", { name }));
    }
    if (folderArchive) {
      rememberFolder(folder.id, folder.archived);  // désarchivé : ouvert ; archivé : replié
      await api(`/api/folders/${folder.id}`, jsonBody("PATCH", { archived: !folder.archived }));
    }
    if (folderDelete) {
      if (!confirm(`Supprimer le semestre « ${folder.name} » ? Ses cours ne sont pas supprimés : ils reviennent dans « Mes cours ».`)) return;
      await api(`/api/folders/${folder.id}`, { method: "DELETE" });
    }
    loadCourses();
  } catch (err) {
    alert(err.message);
  }
});

// Glisser un cours sur un dossier (ou sur « Mes cours » pour le sortir de son dossier).
// Fait à la main (souris / doigt) : le glisser-déposer du navigateur ne marche pas dans la fenêtre de l'app Mac.
const drag = { card: null, ghost: null, zone: null, startX: 0, startY: 0, moved: false };

$("#view-courses").addEventListener("pointerdown", (e) => {
  const card = e.target.closest("[data-course-card]");
  if (!card || e.button !== 0) return;
  Object.assign(drag, { card, zone: null, startX: e.clientX, startY: e.clientY, moved: false });
});

document.addEventListener("pointermove", (e) => {
  if (!drag.card) return;
  if (!drag.moved) {
    if (Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) < 6) return;
    drag.moved = true;
    const rect = drag.card.getBoundingClientRect();
    drag.ghost = drag.card.cloneNode(true);
    drag.ghost.classList.add("drag-ghost");
    drag.ghost.style.width = `${rect.width}px`;
    drag.offsetX = drag.startX - rect.left;
    drag.offsetY = drag.startY - rect.top;
    document.body.appendChild(drag.ghost);
    drag.card.classList.add("drag-source");
    document.body.classList.add("dragging-course");
  }
  e.preventDefault();
  drag.ghost.style.transform = `translate(${e.clientX - drag.offsetX}px, ${e.clientY - drag.offsetY}px)`;
  const zone = document.elementFromPoint(e.clientX, e.clientY)?.closest("[data-drop-folder]") || null;
  if (zone !== drag.zone) {
    drag.zone?.classList.remove("drop-over");
    zone?.classList.add("drop-over");
    drag.zone = zone;
  }
});

async function endDrag() {
  const { card, ghost, zone, moved } = drag;
  drag.card = null;
  if (!moved) return;
  ghost?.remove();
  card.classList.remove("drag-source");
  zone?.classList.remove("drop-over");
  document.body.classList.remove("dragging-course");
  // Le lâcher ne doit pas ouvrir le cours (le clic qui suit tout de suite est ignoré).
  drag.justDropped = true;
  setTimeout(() => { drag.justDropped = false; }, 80);
  if (!zone) return;
  const folderId = zone.dataset.dropFolder || null;
  const course = card.dataset.courseCard;
  try {
    await api(`/api/courses/${course}/folder`, jsonBody("PUT", { folder_id: folderId }));
    if (folderId) rememberFolder(folderId, true);
  } catch (err) {
    alert(err.message);
  }
  loadCourses();
}
document.addEventListener("click", (e) => {
  if (drag.justDropped) { e.preventDefault(); e.stopPropagation(); }
}, true);
document.addEventListener("pointerup", endDrag);
document.addEventListener("pointercancel", endDrag);
// Pas de glisser natif des liens (il prendrait le dessus sur le nôtre).
$("#view-courses").addEventListener("dragstart", (e) => { if (e.target.closest?.("[data-course-card]")) e.preventDefault(); });

// Petite question avec un champ texte (remplace prompt(), absent de certaines fenêtres d'app).
function askText(title, value = "", placeholder = "") {
  const dialog = $("#ask-dialog");
  $("#ask-title").textContent = title;
  $("#ask-input").value = value;
  $("#ask-input").placeholder = placeholder;
  dialog.returnValue = "";
  dialog.showModal();
  $("#ask-input").select();
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => {
      const text = $("#ask-input").value.trim();
      resolve(dialog.returnValue === "ok" && text ? text : null);
    }, { once: true });
  });
}
$("#ask-cancel").addEventListener("click", () => $("#ask-dialog").close(""));

async function saveName(name) {
  state.profile = await api("/api/profile", jsonBody("PUT", { name }));
  $("#name-prompt").hidden = true;
  $("#greeting").textContent = pickGreeting(state.profile.name);
}

$("#name-prompt").addEventListener("submit", (e) => {
  e.preventDefault();
  saveName($("#name-input").value);
});
$("#name-skip").addEventListener("click", () => saveName(""));
// ---------- Réglages ----------
const isDesktop = () => Boolean(window.pywebview?.api);

async function openSettings() {
  const settings = await api("/api/settings");
  state.course = null;
  $("#settings-name").value = settings.name;
  const claude = settings.claude;
  $("#settings-key-state").innerHTML = claude.configured
    ? `<span class="ok-text">✓ Clé enregistrée</span> <span class="muted">(${escapeHtml(claude.hint)} · modèle ${escapeHtml(claude.model)})</span>`
    : `<span class="muted">Aucune clé pour l'instant.</span>`;
  $("#settings-key-test").hidden = !claude.configured;
  $("#settings-key-remove").hidden = !claude.saved_in_app;
  $("#settings-key-form").hidden = !settings.claude_enabled;  // Claude est mis de côté pour l'instant
  $("#local-thinking").checked = settings.local_thinking;
  renderDefinitionRule(settings.definition_rule);
  renderReminder(settings);
  $("#calib-result").hidden = $("#calib-status").hidden = true;
  $("#local-thinking-status").hidden = true;
  $("#settings-data-dir").textContent = settings.data_dir;
  renderBackup(settings);
  loadClaudeApp();
  $("#settings-import").hidden = !settings.desktop;
  ["#settings-name-status", "#settings-key-status", "#settings-import-status"].forEach((id) => ($(id).hidden = true));

  state.config = null;  // la page d'un cours relira l'état des moteurs
  $("#app-version").textContent = settings.version;
  $("#update-status").hidden = true;
  setCrumbs();
  show("settings");
  openAppearance(settings.appearance);
  await refreshLocalAi();
}

$("#local-thinking").addEventListener("change", async (e) => {
  try {
    await api("/api/settings", jsonBody("PUT", { local_thinking: e.target.checked }));
    setStatus("#local-thinking-status", e.target.checked
      ? "Réflexion activée : les modèles comme qwen3 réfléchiront avant de répondre (plus long)."
      : "Réflexion désactivée : les modèles comme qwen3 répondront directement (plus rapide).", true);
  } catch (err) {
    e.target.checked = !e.target.checked;
    setStatus("#local-thinking-status", err.message, false);
  }
});

// ---------- L'app Claude : Pirouette comme outil de Claude (MCP) ----------
function renderClaudeApp(info) {
  $("#claude-app-install").hidden = info.installed || !info.supported;
  $("#claude-app-remove").hidden = !info.installed;
  $("#claude-app-state").innerHTML = !info.supported
    ? "Disponible dans l'app Pirouette pour Mac."
    : info.installed
      ? "<b>Branchée.</b> Il n'y a plus qu'à demander à Claude, dans n'importe quelle conversation."
      : info.claude_found ? "Pas encore branchée."
        : `L'app Claude n'est pas encore installée sur ce Mac : <a href="https://claude.ai/download" target="_blank" rel="noopener">claude.ai/download</a>.`;
}
async function loadClaudeApp() {
  renderClaudeApp(await api("/api/claude-app").catch(() => ({ supported: false })));
}
$("#claude-app-install").addEventListener("click", async () => {
  try {
    renderClaudeApp(await api("/api/claude-app", { method: "POST" }));
    setStatus("#claude-app-status", "C'est branché. Quitte l'app Claude (⌘Q) et rouvre-la, puis pose-lui simplement ta demande.", true);
  } catch (err) {
    setStatus("#claude-app-status", err.message, false);
  }
});
$("#claude-app-remove").addEventListener("click", async () => {
  try {
    renderClaudeApp(await api("/api/claude-app", { method: "DELETE" }));
    setStatus("#claude-app-status", "Débranchée. Rouvre l'app Claude pour qu'elle en tienne compte.", true);
  } catch (err) {
    setStatus("#claude-app-status", err.message, false);
  }
});

// ---------- Sauvegardes : copie de tout + CSV, à l'ouverture, chaque semaine ou jamais ----------
function renderBackup(settings) {
  $("#backup-mode").value = settings.backup_mode;
  $("#backup-dir").textContent = settings.backup_dir.replace(/^\/Users\/[^/]+/, "~");
  $("#backup-last").textContent = settings.last_backup
    ? `Dernière sauvegarde : ${formatDate(settings.last_backup)}.` : "Pas encore de sauvegarde.";
  $("#backup-open").hidden = !settings.desktop;
}
$("#backup-mode").addEventListener("change", async (e) => {
  renderBackup(await api("/api/settings", jsonBody("PUT", { backup_mode: e.target.value })));
});
$("#backup-now").addEventListener("click", async () => {
  $("#backup-now").disabled = true;
  try {
    const result = await api("/api/backup", { method: "POST" });
    renderBackup(result);
    setStatus("#backup-status", "Sauvegarde faite.", true);
  } catch (err) {
    setStatus("#backup-status", err.message, false);
  }
  $("#backup-now").disabled = false;
});
$("#backup-open").addEventListener("click", () => api("/api/backup/open", { method: "POST" }).catch(() => {}));

// ---------- Révisions : rappel quotidien, nouvelles cartes par jour ----------
function renderReminder(settings) {
  $("#reminder-on").checked = Boolean(settings.reminder_time);
  $("#reminder-on").disabled = !settings.reminder_supported;
  $("#reminder-time").value = settings.reminder_time || "19:00";
  $("#reminder-time-row").hidden = !settings.reminder_time;
  $("#reminder-help").textContent = settings.reminder_supported
    ? "Une notification du Mac à l'heure choisie, même app fermée : les jours de séance de ton plan de révision (sans plan, s'il y a des cartes du jour)."
    : "Disponible dans l'app Mac (Pirouette.app).";
  $("#new-per-day").value = settings.new_per_day;
  $("#quiz-size").value = settings.quiz_size;
  $("#revision-status").hidden = true;
}

async function saveReminder() {
  const time = $("#reminder-on").checked ? $("#reminder-time").value || "19:00" : "";
  try {
    const settings = await api("/api/settings", jsonBody("PUT", { reminder_time: time }));
    renderReminder(settings);
    setStatus("#revision-status", time
      ? `C'est noté : rappel à ${time.replace(":", " h ")} les jours de séance (ou de cartes à réviser). La première fois, macOS peut demander d'autoriser les notifications (de « Éditeur de script »).`
      : "Rappel arrêté.", true);
  } catch (err) {
    $("#reminder-on").checked = !$("#reminder-on").checked;
    setStatus("#revision-status", err.message, false);
  }
}
$("#reminder-on").addEventListener("change", saveReminder);
$("#reminder-time").addEventListener("change", saveReminder);
$("#reminder-test").addEventListener("click", async () => {
  try {
    const result = await api("/api/reminder/test", { method: "POST" });
    setStatus("#revision-status", result.shown ? `Notification envoyée : « ${result.message} »`
      : "La notification n'a pas pu s'afficher : vérifie Réglages Système → Notifications.", result.shown);
  } catch (err) {
    setStatus("#revision-status", err.message, false);
  }
});
$("#quiz-size").addEventListener("change", async (e) => {
  const settings = await api("/api/settings", jsonBody("PUT", { quiz_size: Number(e.target.value) || 0 }));
  e.target.value = state.quizSize = settings.quiz_size;
  setStatus("#revision-status", settings.quiz_size
    ? `Un quiz de plus de ${settings.quiz_size} questions en tire ${settings.quiz_size} à chaque lancement.`
    : "Chaque quiz se fait en entier.", true);
});
$("#new-per-day").addEventListener("change", async (e) => {
  const settings = await api("/api/settings", jsonBody("PUT", { new_per_day: Number(e.target.value) || 0 }));
  e.target.value = settings.new_per_day;
  setStatus("#revision-status", `${plural(settings.new_per_day, "nouvelle carte", "nouvelles cartes")} au plus chaque jour dans la révision du jour.`, true);
});

// ---------- Tes définitions : calibrer le repérage ----------
const RULE_NAMES = {
  gras_italique: "un titre en gras et italique, puis sa définition",
  gras: "un titre en gras, puis sa définition",
  italique: "un titre en italique, puis sa définition",
  deux_points: "« Terme : définition »",
  auto: "automatique (Pirouette devine d'après chaque fichier)",
  aucune: "désactivé",
};

function renderDefinitionRule(rule) {
  $("#definitions-current").innerHTML = rule === "auto"
    ? `<span class="muted">Repérage : ${RULE_NAMES.auto}.</span>`
    : `<span class="ok-text">✓ Tes définitions : ${escapeHtml(RULE_NAMES[rule] || rule)}.</span>`;
  $("#calib-auto").hidden = rule === "auto";
}

// Un passage collé (depuis Pages, Word…) garde sa mise en forme : on la traduit en **gras** / *italique*.
function pastedToMarkdown(root) {
  const lines = [[]];
  const newLine = () => lines.push([]);
  const walk = (node) => {
    for (const child of node.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) {
        if (!child.textContent.trim() && child.textContent.includes("\n")) continue;  // indentation du HTML collé
        const style = getComputedStyle(child.parentElement);
        lines[lines.length - 1].push({ text: child.textContent, bold: Number(style.fontWeight) >= 600, italic: style.fontStyle === "italic" });
      } else if (child.nodeName === "BR") {
        newLine();
      } else if (child.nodeType === Node.ELEMENT_NODE) {
        const block = /^(DIV|P|LI|H[1-6]|TR|UL|OL|TABLE)$/.test(child.nodeName);
        if (block && lines[lines.length - 1].length) newLine();
        walk(child);
        if (block) newLine();
      }
    }
  };
  walk(root);
  const wrap = (text, bold, italic) => {
    const marker = bold && italic ? "***" : bold ? "**" : italic ? "*" : "";
    const core = text.trim();
    return marker && core ? text.replace(core, `${marker}${core}${marker}`) : text;
  };
  return lines.map((segments) => {
    const visible = segments.filter((seg) => seg.text.trim());
    if (visible.length && visible.every((seg) => seg.bold === visible[0].bold && seg.italic === visible[0].italic)) {
      return wrap(segments.map((seg) => seg.text).join(""), visible[0].bold, visible[0].italic);
    }
    return segments.map((seg) => wrap(seg.text, seg.bold, seg.italic)).join("");
  }).join("\n").replace(/\n{3,}/g, "\n\n");
}

async function analyseDefinitions(form) {
  setStatus("#calib-status", "Analyse du passage…", true);
  try {
    const result = await api("/api/definitions/analyse", { method: "POST", body: form });
    state.calibration = result;
    const select = $("#calib-rule");
    select.innerHTML = result.rules.map((r) => `<option value="${r.id}">${escapeHtml(r.label)} · ${
      r.definitions.length} trouvée${r.definitions.length > 1 ? "s" : ""}</option>`).join("");
    select.value = result.best || result.rules[0].id;
    renderCalibrationList();
    $("#calib-result").hidden = false;
    setStatus("#calib-status", result.best
      ? "Vérifie la liste ci-dessus : si ce sont bien tes définitions, valide."
      : "Aucune définition repérée dans ce passage : essaie un passage plus long, avec au moins deux définitions.", Boolean(result.best));
  } catch (err) {
    setStatus("#calib-status", err.message, false);
  }
}

function renderCalibrationList() {
  const rule = state.calibration.rules.find((r) => r.id === $("#calib-rule").value);
  $("#calib-list").innerHTML = rule.definitions.length
    ? rule.definitions.slice(0, 12).map((d) => `<li><strong>${escapeHtml(d.term)}</strong>
        <span class="muted">${escapeHtml(d.definition.length > 180 ? d.definition.slice(0, 180) + "…" : d.definition)}</span></li>`).join("")
    : `<li class="muted">Rien trouvé avec cette règle.</li>`;
  $("#calib-save").disabled = !rule.definitions.length;
}

$("#calib-file").addEventListener("change", (e) => {
  if (!e.target.files.length) return;
  const form = new FormData();
  form.append("file", e.target.files[0]);
  e.target.value = "";
  analyseDefinitions(form);
});
$("#calib-analyse").addEventListener("click", () => {
  const form = new FormData();
  form.append("text", pastedToMarkdown($("#calib-paste")));
  analyseDefinitions(form);
});
$("#calib-rule").addEventListener("change", renderCalibrationList);
$("#calib-save").addEventListener("click", async () => {
  const settings = await api("/api/settings", jsonBody("PUT", { definition_rule: $("#calib-rule").value }));
  renderDefinitionRule(settings.definition_rule);
  $("#calib-result").hidden = true;
  setStatus("#calib-status", "C'est noté : Pirouette repérera tes définitions de cette façon dans tous tes cours.", true);
});
$("#calib-auto").addEventListener("click", async () => {
  const settings = await api("/api/settings", jsonBody("PUT", { definition_rule: "auto" }));
  renderDefinitionRule(settings.definition_rule);
  setStatus("#calib-status", "Repérage automatique rétabli.", true);
});

// ---------- Couleur de l'app (roue chromatique) ----------
const DEFAULT_ACCENT = "#B4532A";
const PRESETS = [
  ["Terre cuite", "#B4532A"], ["Chocolat", "#6B4430"], ["Pétrole", "#0E6E7E"], ["Sapin", "#1E6B52"], ["Cobalt", "#2F4BE0"],
  ["Moutarde", "#E0A100"], ["Framboise", "#D12F62"], ["Orange", "#F2541B"],
];
const PAPER = "#FAF8F3";
const NIGHT = "#1D1512";

const hexToRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const rgbToHex = (rgb) => "#" + rgb.map((c) => Math.round(Math.min(255, Math.max(0, c))).toString(16).padStart(2, "0")).join("").toUpperCase();

function hsvToRgb(h, s, v) {
  const f = (n) => {
    const k = (n + h / 60) % 6;
    return v - v * s * Math.max(0, Math.min(k, 4 - k, 1));
  };
  return [f(5) * 255, f(3) * 255, f(1) * 255];
}
function rgbToHsv([r, g, b]) {
  [r, g, b] = [r / 255, g / 255, b / 255];
  const max = Math.max(r, g, b), d = max - Math.min(r, g, b);
  let h = 0;
  if (d) h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [(h * 60 + 360) % 360, max ? d / max : 0, max];
}
function luminance(hex) {
  const lin = hexToRgb(hex).map((c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}
function contrast(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((m, n) => n - m);
  return (x + 0.05) / (y + 0.05);
}
const mix = (a, b, t) => rgbToHex(hexToRgb(a).map((c, i) => c * t + hexToRgb(b)[i] * (1 - t)));

// Assombrit (vers le noir) ou éclaircit (vers le blanc) jusqu'au contraste voulu avec le fond.
function untilContrast(hex, background, target, towards) {
  let color = hex;
  for (let t = 0; contrast(color, background) < target && t <= 1; t += 0.04) color = mix(towards, hex, t);
  return color;
}

// À partir d'une seule couleur, toutes les teintes utilisées par l'app (modes clair et sombre).
function derivePalette(hex) {
  return {
    accent: hex,
    ink: untilContrast(hex, PAPER, 4.5, "#000000"),       // texte lisible sur le papier
    soft: mix(hex, PAPER, 0.12),                           // fonds légers (pastilles, badges)
    d_accent: untilContrast(hex, NIGHT, 3.2, "#FFFFFF"),
    d_ink: untilContrast(hex, NIGHT, 5.5, "#FFFFFF"),
    d_soft: mix(hex, NIGHT, 0.22),
  };
}

// Même résultat que appearance_css() côté serveur.
function paletteCss(p) {
  if (!p) return "";
  return `:root{--accent:${p.accent};--accent-ink:${p.ink};--accent-soft:${p.soft}}`
    + `@media (prefers-color-scheme: dark){:root{--accent:${p.d_accent};--accent-ink:${p.d_ink};--accent-soft:${p.d_soft}}}`;
}

const picker = { h: 0, s: 0, v: 1, saved: DEFAULT_ACCENT, ready: false };

function setupPicker() {
  if (picker.ready) return;
  picker.ready = true;
  const canvas = $("#color-wheel");
  const ratio = window.devicePixelRatio || 1;
  canvas.width = canvas.height = 220 * ratio;
  $("#color-presets").innerHTML = PRESETS.map(([name, hex]) =>
    `<button type="button" class="preset" data-hex="${hex}" title="${name}" aria-label="${name}" style="background:${hex}"></button>`).join("");

  const pick = (e) => {
    const rect = canvas.getBoundingClientRect();
    const dx = e.clientX - rect.left - rect.width / 2;
    const dy = e.clientY - rect.top - rect.height / 2;
    picker.h = (Math.atan2(dy, dx) * 180 / Math.PI + 360) % 360;
    picker.s = Math.min(1, Math.hypot(dx, dy) / (rect.width / 2));
    updatePicker();
  };
  canvas.addEventListener("pointerdown", (e) => { canvas.setPointerCapture(e.pointerId); pick(e); });
  canvas.addEventListener("pointermove", (e) => { if (canvas.hasPointerCapture(e.pointerId)) pick(e); });
  $("#color-value").addEventListener("input", (e) => { picker.v = e.target.value / 100; drawWheel(); updatePicker(); });
  $("#color-hex").addEventListener("input", (e) => {
    const value = e.target.value.trim().replace(/^([^#])/, "#$1");
    if (/^#[0-9a-fA-F]{6}$/.test(value)) setPickerColor(value.toUpperCase(), { keepHex: true });
  });
  $("#color-presets").addEventListener("click", (e) => {
    const hex = e.target.closest("[data-hex]")?.dataset.hex;
    if (hex) setPickerColor(hex);
  });
  $("#color-save").addEventListener("click", saveAppearance);
  $("#color-reset").addEventListener("click", resetAppearance);
}

function drawWheel() {
  const canvas = $("#color-wheel");
  const ctx = canvas.getContext("2d");
  const size = canvas.width, r = size / 2;
  const image = ctx.createImageData(size, size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = x - r + 0.5, dy = y - r + 0.5, dist = Math.hypot(dx, dy);
      if (dist > r) continue;
      const [red, green, blue] = hsvToRgb((Math.atan2(dy, dx) * 180 / Math.PI + 360) % 360, dist / r, picker.v);
      const i = (y * size + x) * 4;
      image.data[i] = red; image.data[i + 1] = green; image.data[i + 2] = blue;
      image.data[i + 3] = dist > r - 1 ? (r - dist) * 255 : 255;  // bord adouci
    }
  }
  ctx.putImageData(image, 0, 0);
}

function currentHex() {
  return rgbToHex(hsvToRgb(picker.h, picker.s, picker.v));
}

function setPickerColor(hex, { keepHex = false } = {}) {
  [picker.h, picker.s, picker.v] = rgbToHsv(hexToRgb(hex));
  $("#color-value").value = Math.round(Math.max(0.3, picker.v) * 100);
  drawWheel();
  updatePicker({ keepHex, hex });
}

function updatePicker({ keepHex = false, hex = null } = {}) {
  hex = hex || currentHex();
  const angle = picker.h * Math.PI / 180;
  const handle = $("#wheel-handle");
  handle.style.left = `${110 + Math.cos(angle) * picker.s * 110}px`;
  handle.style.top = `${110 + Math.sin(angle) * picker.s * 110}px`;
  handle.style.background = hex;
  $("#hex-swatch").style.background = hex;
  if (!keepHex) $("#color-hex").value = hex;
  document.querySelectorAll(".preset").forEach((b) => b.classList.toggle("selected", b.dataset.hex === hex));
  const p = derivePalette(hex);
  const apply = (el, accent, ink, soft) => {
    el.style.setProperty("--accent", accent);
    el.style.setProperty("--accent-ink", ink);
    el.style.setProperty("--accent-soft", soft);
  };
  apply($("#preview-light"), p.accent, p.ink, p.soft);
  apply($("#preview-dark"), p.d_accent, p.d_ink, p.d_soft);
  picker.current = hex;
  $("#color-save").disabled = hex === picker.saved;
}

function openAppearance(saved) {
  setupPicker();
  picker.saved = saved?.accent || DEFAULT_ACCENT;
  $("#color-status").hidden = true;
  setPickerColor(picker.saved);
}

async function saveAppearance() {
  const palette = derivePalette(picker.current);
  const result = await api("/api/appearance", jsonBody("PUT", { palette }));
  $("#appearance-style").textContent = result.css;
  picker.saved = picker.current;
  $("#color-save").disabled = true;
  setStatus("#color-status", "Couleur appliquée à toute l'app.", true);
}

async function resetAppearance() {
  const result = await api("/api/appearance", jsonBody("PUT", { palette: null }));
  $("#appearance-style").textContent = result.css;
  picker.saved = DEFAULT_ACCENT;
  setPickerColor(DEFAULT_ACCENT);
  setStatus("#color-status", "Couleur d'origine (terre cuite) rétablie.", true);
}

// ---------- Assistant IA locale ----------
// Étapes : 1. Ollama installé et ouvert → 2. un modèle téléchargé → 3. prêt.
// Tant qu'Ollama n'est pas ouvert, on revérifie toutes les 3 s (l'utilisateur est en train de l'installer).
const aiSetup = { timer: null, downloading: false };

async function refreshLocalAi() {
  clearTimeout(aiSetup.timer);
  if ($("#view-settings").hidden) return;
  const status = await api("/api/ollama/status");
  renderLocalAi(status);
  if (!status.running) aiSetup.timer = setTimeout(refreshLocalAi, 3000);
}

function setStep(selector, step) {
  $(selector).classList.toggle("active", step === "active");
  $(selector).classList.toggle("done", step === "done");
}

function renderLocalAi(st) {
  const hasModel = st.models.length > 0;

  if (st.running) {
    setStep("#step-install", "done");
    $("#step-install-body").innerHTML = `<p class="muted">Ollama est ouvert.</p>`;
  } else {
    setStep("#step-install", "active");
    $("#step-install-body").innerHTML = (st.installed
      ? (st.can_open
        ? `<p>Ollama est installé mais pas ouvert.</p>
           <div class="actions"><button class="primary small" type="button" id="ollama-open">Ouvrir Ollama</button></div>`
        : `<p>Ollama est installé mais pas ouvert : lance-le depuis tes Applications.</p>`)
      : `<p>Ollama est l'app gratuite qui fait tourner l'IA sur ton ordinateur.</p>
         <div class="actions"><a class="button primary small" href="https://ollama.com/download" target="_blank" rel="noopener">Télécharger Ollama ↗</a></div>
         <p class="muted small-text">Ouvre le fichier téléchargé, glisse Ollama dans Applications, puis lance-le.</p>`)
      + `<p class="muted small-text waiting">En attente d'Ollama… Pirouette le détectera toute seule.</p>`;
  }

  if (!st.running) {
    setStep("#step-model", "");
    $("#step-model-body").innerHTML = "";
  } else if (!aiSetup.downloading) {
    setStep("#step-model", hasModel ? "done" : "active");
    if (!st.enough_ram) {
      $("#step-model-body").innerHTML = `<p class="warn">Cet ordinateur a ${st.ram_gb} Go de mémoire : c'est trop juste pour une IA locale
        (il faut au moins 8 Go) : la génération risque d'être très lente.</p>`;
    } else {
      const ram = st.ram_gb ? ` (${String(st.ram_gb).replace(".", ",")} Go de mémoire)` : "";
      const recommendedMissing = st.options.find((o) => o.name === st.recommended && !o.installed);
      const choice = recommendedMissing
        || st.options.find((o) => !o.installed && o.min_ram_gb <= (st.ram_gb || 16) + 1);
      $("#step-model-body").innerHTML = `
        ${hasModel ? `<p class="muted">Installé : ${st.models.map(escapeHtml).join(", ")}.</p>` : `<p>Choisis un modèle (un seul suffit) :</p>`}
        ${hasModel && recommendedMissing ? `<p class="setup-hint">Conseillé pour ton Mac : <strong>${escapeHtml(st.recommended)}</strong>
          (${String(recommendedMissing.size_gb).replace(".", ",")} Go), plus récent et meilleur en français. Choisis-le ci-dessous puis « Télécharger ».</p>` : ""}
        <details ${hasModel && !recommendedMissing ? "" : "open"}><summary class="small-text">${hasModel ? "Télécharger un autre modèle" : "Modèles disponibles"}</summary>
        <div class="model-options">${st.options.map((o) => `
          <label class="model-option">
            <input type="radio" name="ai-model" value="${o.name}" ${o.installed ? "disabled" : ""} ${choice && o.name === choice.name ? "checked" : ""}>
            <span><strong>${o.label} <small>· ${o.name} · ${String(o.size_gb).replace(".", ",")} Go</small></strong>
              <small>${o.name === st.recommended ? `<b class="ok-text">Conseillé pour cet ordinateur${ram}.</b> ` : ""}${o.installed ? "✓ Déjà installé. " : ""}${o.note}</small></span>
          </label>`).join("")}</div>
        <div class="actions"><button class="primary small" type="button" id="model-download" ${choice ? "" : "disabled"}>Télécharger le modèle</button></div>
        </details>
        <div class="download" id="model-download-progress" hidden>
          <div class="progress"><div></div></div><small></small>
        </div>`;
    }
  }

  renderContextStep(st);
  setStep("#step-ready", st.running && hasModel ? "done" : "");
  $("#step-ready-body").innerHTML = st.running && hasModel
    ? `<p class="ok-text">L'IA locale est prête : dans un cours, clique sur + pour créer un quiz ou des flashcards.</p>`
    : "";
}

// Étape 3 : la mémoire de lecture (contexte), réglée d'office selon la mémoire du Mac et modifiable.
const formatTokens = (n) => n.toLocaleString("fr-FR");
const pagesFor = (tokens) => Math.round((tokens * 1.5) / 1800);  // ~1 800 caractères par page de cours

function renderContextStep(st) {
  const ready = st.running && st.models.length > 0;
  setStep("#step-context", ready ? "done" : "");
  if (!ready) { $("#step-context-body").innerHTML = ""; return; }
  const ctx = st.context;
  const ram = st.ram_gb ? `${String(st.ram_gb).replace(".", ",")} Go de mémoire` : "cet ordinateur";
  $("#step-context-body").innerHTML = `
    <p>La quantité de cours que l'IA lit d'un coup. Plus elle est grande, moins Pirouette découpe ton cours
      (questions mieux réparties), mais plus le Mac utilise de mémoire. Pas besoin de Terminal : Pirouette
      l'applique à chaque demande.</p>
    <label class="field context-field">Mémoire de lecture
      <select id="context-select">
        <option value="0" ${ctx.chosen ? "" : "selected"}>Automatique : ${formatTokens(ctx.auto)} tokens (conseillé pour ${ram})</option>
        ${ctx.options.map((o) => `<option value="${o.tokens}" ${ctx.chosen === o.tokens ? "selected" : ""}>
          ${formatTokens(o.tokens)} tokens · environ ${pagesFor(o.tokens)} pages d'un coup${o.too_big ? " · trop pour cette mémoire" : ""}</option>`).join("")}
      </select>
    </label>
    <p class="muted small-text">Utilisé en ce moment : ${formatTokens(ctx.used)} tokens, soit environ ${pagesFor(ctx.used)} pages de cours par passage.</p>
    <p class="status" id="context-status" hidden></p>`;
}

document.addEventListener("change", async (e) => {
  if (e.target.id !== "context-select") return;
  try {
    await api("/api/settings", jsonBody("PUT", { local_context: Number(e.target.value) }));
    const st = await api("/api/ollama/status");
    renderContextStep(st);
    setStatus("#context-status", `Enregistré : ${formatTokens(st.context.used)} tokens.`, true);
  } catch (err) {
    setStatus("#context-status", err.message, false);
  }
});

document.addEventListener("click", async (e) => {
  if (e.target.id === "ollama-open") {
    e.target.disabled = true;
    e.target.textContent = "Ouverture…";
    await api("/api/ollama/open", { method: "POST" });
    setTimeout(refreshLocalAi, 2000);
  }
  if (e.target.id === "model-download") {
    const model = document.querySelector("input[name=ai-model]:checked")?.value;
    if (model) downloadModel(model);
  }
});

async function downloadModel(model) {
  aiSetup.downloading = true;
  document.querySelectorAll("input[name=ai-model], #model-download").forEach((el) => (el.disabled = true));
  const box = $("#model-download-progress");
  const bar = box.querySelector(".progress div");
  const text = box.querySelector("small");
  box.hidden = false;
  text.textContent = "Préparation du téléchargement…";
  const gb = (bytes) => (bytes / 1e9).toFixed(1).replace(".", ",");  // même unité que les tailles annoncées
  try {
    const response = await fetch("/api/ollama/pull", jsonBody("POST", { model }));
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `Erreur ${response.status}`);
    for await (const event of ndjson(response)) {
      if (event.type === "progress") {
        if (event.percent !== null) {
          bar.style.width = `${event.percent}%`;
          text.textContent = `${gb(event.completed)} / ${gb(event.total)} Go · ${Math.floor(event.percent)} % — garde Pirouette ouverte`;
        } else {
          text.textContent = event.status === "verifying sha256 digest" ? "Vérification…" : "Préparation…";
        }
      }
      if (event.type === "error") throw new Error(event.message);
      if (event.type === "done") {
        bar.style.width = "100%";
        text.textContent = "Téléchargement terminé !";
      }
    }
  } catch (err) {
    text.textContent = err.message;
    text.className = "error-text";
  } finally {
    aiSetup.downloading = false;
    state.config = null;
    setTimeout(refreshLocalAi, 1200);
  }
}

// Lit une réponse en NDJSON (une ligne JSON par événement), au fur et à mesure qu'elle arrive.
async function* ndjson(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) if (line.trim()) yield JSON.parse(line);
  }
}

function setStatus(selector, message, ok) {
  const el = $(selector);
  el.textContent = message;
  el.className = `status ${ok ? "ok" : "ko"}`;
  el.hidden = !message;
}

$("#settings-name-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const settings = await api("/api/settings", jsonBody("PUT", { name: $("#settings-name").value }));
  state.profile = { name: settings.name, asked: true };
  setStatus("#settings-name-status", settings.name ? `Enchanté, ${settings.name} !` : "Prénom retiré.", true);
});

$("#settings-key-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const key = $("#settings-key").value.trim();
  if (!key) return;
  try {
    await api("/api/settings", jsonBody("PUT", { api_key: key }));
    $("#settings-key").value = "";
    await openSettings();
    await testKey();
  } catch (err) {
    setStatus("#settings-key-status", err.message, false);
  }
});

async function testKey() {
  setStatus("#settings-key-status", "Vérification…", true);
  try {
    setStatus("#settings-key-status", (await api("/api/settings/test-claude", { method: "POST" })).message, true);
  } catch (err) {
    setStatus("#settings-key-status", err.message, false);
  }
}
$("#settings-key-test").addEventListener("click", testKey);

$("#settings-key-remove").addEventListener("click", async () => {
  if (!confirm("Supprimer la clé API enregistrée dans Pirouette ?")) return;
  await api("/api/settings", jsonBody("PUT", { api_key: "" }));
  await openSettings();
  setStatus("#settings-key-status", "Clé supprimée.", true);
});

$("#settings-import-btn").addEventListener("click", async () => {
  const result = await window.pywebview.api.import_legacy_data();
  if (result.message) setStatus("#settings-import-status", result.message, result.ok);
});

// ---------- Nouveau cours : une petite fenêtre, on y glisse son fichier ----------
const newCourse = { files: [] };

async function openNewCourse({ folder = "" } = {}) {
  newCourse.files = [];
  $("#new-course-name").value = "";
  $("#new-course-status").hidden = true;
  $("#new-course-create").disabled = false;
  renderNewCourseFiles();
  const folders = (await api("/api/folders")).filter((f) => !f.archived);
  $("#new-course-folder-field").hidden = !folders.length;
  $("#new-course-folder").innerHTML = `<option value="">Sans semestre</option>`
    + folders.map((f) => `<option value="${f.id}">${escapeHtml(f.name)}</option>`).join("");
  $("#new-course-folder").value = folders.some((f) => f.id === folder) ? folder : "";
  $("#new-course-dialog").showModal();
  $("#new-course-name").focus();
}

function renderNewCourseFiles() {
  const files = newCourse.files;
  $("#new-course-drop").classList.toggle("has-files", files.length > 0);
  $("#new-course-drop-title").textContent = files.length
    ? files.map((f) => f.name).join(", ") : "Glisse ton cours ici";
  $("#new-course-drop-sub").textContent = files.length
    ? "Clique pour en choisir d'autres" : "ou clique pour choisir · PDF, Word, PowerPoint, Pages, Keynote, texte";
}

function addNewCourseFiles(fileList) {
  newCourse.files = [...fileList];
  // Pas encore de nom : celui du premier fichier, sans l'extension.
  if (!$("#new-course-name").value.trim() && newCourse.files.length) {
    $("#new-course-name").value = newCourse.files[0].name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim();
  }
  renderNewCourseFiles();
}

$("#new-course-btn").addEventListener("click", () => openNewCourse());
$("#new-course-cancel").addEventListener("click", () => $("#new-course-dialog").close());
$("#new-course-files").addEventListener("change", (e) => { addNewCourseFiles(e.target.files); e.target.value = ""; });
const courseDrop = $("#new-course-drop");
["dragenter", "dragover"].forEach((ev) => courseDrop.addEventListener(ev, (e) => { e.preventDefault(); courseDrop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => courseDrop.addEventListener(ev, (e) => { e.preventDefault(); courseDrop.classList.remove("over"); }));
courseDrop.addEventListener("drop", (e) => addNewCourseFiles(e.dataTransfer.files));

$("#new-course-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("#new-course-name").value.trim();
  if (!name) {
    setStatus("#new-course-status", "Donne un nom au cours (ou glisse un fichier : son nom sera repris).", false);
    return $("#new-course-name").focus();
  }
  $("#new-course-create").disabled = true;
  try {
    const course = await api("/api/courses", jsonBody("POST", { name }));
    const folder = $("#new-course-folder").value;
    if (folder) await api(`/api/courses/${course.id}/folder`, jsonBody("PUT", { folder_id: folder }));
    $("#new-course-dialog").close();
    const files = newCourse.files;
    if (!files.length) return go(`#/cours/${course.id}`);
    // Avec un fichier : on ouvre la page Fichiers du cours, où l'on suit la lecture puis le repérage des chapitres.
    history.pushState(null, "", `#/cours/${course.id}/fichiers`);
    document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("on", a.dataset.nav === "cours"));
    await openCourse(course.id, "fichiers");
    await uploadFiles(files);
  } catch (err) {
    setStatus("#new-course-status", err.message, false);
    $("#new-course-create").disabled = false;
  }
});

// ---------- Un cours ----------
// #/cours/<id>/quiz | cartes | fichiers : les trois entrées du cours ; #/cours/<id>/nouveau/quiz | cartes : création.
const TABS = ["chapitres", "quiz", "cartes", "fichiers"];

function courseHash(tab) {
  return `#/cours/${state.course.id}${tab ? `/${tab}` : ""}`;
}

// Va à une adresse ; si c'est déjà l'adresse affichée (retour depuis un quiz…), réaffiche quand même.
function go(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

async function loadCourse(id) {
  const course = await api(`/api/courses/${id}`);
  if (state.course?.id !== id) {
    $("#chapters-status").hidden = $("#upload-status").hidden = $("#cards-status").hidden = true;
    state.deck = null;
    state.newCards = null;
  }
  state.course = course;
  if (!state.config) await loadConfig();
  return course;
}

async function openCourse(id, tab, { keepScroll = false } = {}) {
  if (state.course?.id !== id || !tab) state.chapterFilter = null;
  const course = await loadCourse(id);
  // Une seule page, en arbre : le fichier, puis chapitres, quiz et flashcards à déplier. `tab` ouvre un dépliant.
  if (!TABS.includes(tab)) tab = null;
  state.tab = tab;
  $("#course-name").textContent = course.name;
  setCrumbs(courseCrumbs(course));
  renderFolderPick(course);
  const chip = $("#course-exam-chip");
  chip.hidden = !course.exam || course.exam.days <= -7;
  if (course.exam) chip.innerHTML = `${ICON_CALENDAR}<span>${examLabel(course.exam.date)}</span>`;
  renderTiles(course);
  // Cours tout neuf, sans fichier ni carte : on invite d'abord à importer le cours.
  const empty = !course.files.length && !course.quizzes.length && !course.cards.total;
  $("#start-drop").hidden = !empty;
  $("#course-tree").hidden = empty;
  renderCourseRevise(course);
  renderQuizPanel(course);
  renderFiles(course);
  const questions = course.quizzes.reduce((n, q) => n + q.count, 0);
  $("#fold-quiz-count").textContent = course.quizzes.length
    ? `· ${plural(course.quizzes.length, "quiz", "quiz")}, ${plural(questions, "question", "questions")}` : "· aucun pour l'instant";
  await loadDeck();

  const scroll = window.scrollY;
  show("course");
  $("#course-overview").hidden = false;
  if (tab) $(`#panel-${tab}`).open = true;
  if (keepScroll) window.scrollTo(0, scroll);
  else if (tab) $(`#panel-${tab}`).scrollIntoView({ block: "start" });
  renderPrepare(course, tab);
  renderNews(course);
  // Nouvelle version d'un fichier : questions et cartes à vérifier.
  $("#outdated-note").hidden = !course.outdated;
  $("#outdated-text").textContent = `Ton cours a changé : ${plural(course.outdated, "question ou carte ne correspond", "questions ou cartes ne correspondent")} plus au cours.`;
}

// ---- Nouvelle version d'un fichier : les passages nouveaux, pour des questions et des cartes sur eux seulement ----
async function renderNews(course) {
  const { units } = await api(`/api/courses/${course.id}/news`).catch(() => ({ units: [] }));
  if (state.course?.id !== course.id) return;
  state.news = units;
  $("#news-card").hidden = !units.length;
  $("#news-list").innerHTML = units.map((u) => `<li><strong>${escapeHtml(u.title)}</strong>
    <small class="muted">≈ ${u.chars.toLocaleString("fr-FR")} caractères nouveaux → ${u.questions} questions, ${u.cards} cartes</small></li>`).join("");
  const config = state.config || await api("/api/config").catch(() => null);
  $("#news-go").hidden = !config?.local?.available;
  state.newsModel = config?.local?.default_model || "";
}
function newsRequest() {
  const c = state.course;
  const parts = state.news.map((u) => `--- Chapitre « ${u.title} » (clé ${u.key}) ---\n${u.text}`).join("\n\n");
  return `Pirouette : mon cours « ${c.name} » (identifiant ${c.id}) a une nouvelle version. Voici UNIQUEMENT les passages `
    + `nouveaux, chapitre par chapitre. Crée des questions et des flashcards seulement sur eux (ne relis pas le cours) : `
    + `pour chaque chapitre, pirouette_creer_quiz puis pirouette_ajouter_cartes avec sa clé dans « chapitres » `
    + `(la « source » est une phrase de ces passages, recopiée mot pour mot). Réponds ensuite par un résumé court.\n\n${parts}`;
}
$("#news-go").addEventListener("click", async () => {
  const form = new FormData();
  form.append("provider", "local");
  form.append("model", state.newsModel || "");
  try {
    await api(`/api/courses/${state.course.id}/news/create`, { method: "POST", body: form });
    $("#news-card").hidden = true;
    jobsState.open = true;
    refreshJobs();
  } catch (err) {
    setStatus("#news-status", err.message, false);
  }
});
$("#news-copy").addEventListener("click", async () => {
  await copyText(newsRequest());
  setStatus("#news-status", "Demande copiée : colle-la dans l'app Claude. Une fois faite, clique sur « Ignorer » pour ranger cet encart.", true);
});
$("#news-dismiss").addEventListener("click", async () => {
  await api(`/api/courses/${state.course.id}/news/dismiss`, { method: "POST" }).catch(() => {});
  $("#news-card").hidden = true;
});

// Menu « ••• » du cours : renommer, supprimer.
$("#course-more").addEventListener("click", () => {
  const menu = $("#course-more-menu");
  menu.hidden = !menu.hidden;
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".more")) $("#course-more-menu").hidden = true;
});

async function renderFolderPick(course) {
  const folders = await api("/api/folders");
  state.folders = folders;
  renderCourseRevise(course);  // le bouton « Réviser » mène au semestre du cours
  const select = $("#course-folder");
  select.innerHTML = `<option value="">Sans semestre</option>`
    + folders.map((f) => `<option value="${f.id}">${escapeHtml(f.name)}${f.archived ? " (archivé)" : ""}</option>`).join("")
    + `<option value="__new">+ Nouveau semestre…</option>`;
  select.value = folders.some((f) => f.id === course.folder_id) ? course.folder_id : "";
}

$("#course-folder").addEventListener("change", async (e) => {
  let folderId = e.target.value || null;
  if (folderId === "__new") {
    const name = await askText("Nom du semestre", "", "ex. Semestre 1, L2 S3…");
    if (!name) return renderFolderPick(state.course);
    folderId = (await api("/api/folders", jsonBody("POST", { name }))).id;
  }
  state.course = { ...state.course, ...(await api(`/api/courses/${state.course.id}/folder`, jsonBody("PUT", { folder_id: folderId }))) };
  renderFolderPick(state.course);
});

// Recharge le cours affiché sans changer d'onglet ni de position.
const refreshCourse = () => openCourse(state.course.id, state.tab, { keepScroll: true });

// Page du cours : ses chapitres, avec ce qu'ils contiennent, « Créer » et « … » (voir, exporter).
async function renderTiles(course) {
  const data = (await api(`/api/mastery?course=${course.id}`).catch(() => []))[0] || { chapters: [] };
  if (state.course?.id !== course.id) return;
  state.courseChapters = data.chapters;
  const fileOf = (ch) => course.files.find((f) => ch.key === f.id || ch.key.startsWith(`${f.id}-`));
  const several = course.files.length > 1;
  $("#fold-chapitres-count").textContent = data.chapters.length ? `· ${data.chapters.length}` : "";
  $("#chapter-list").innerHTML = data.chapters.map((ch, i) => {
    // Plusieurs fichiers : les chapitres de chacun sous son nom
    const f = fileOf(ch);
    const header = several && f && (i === 0 || fileOf(data.chapters[i - 1]) !== f)
      ? `<li class="chapter-group">${ICON_FILE}${escapeHtml(f.name)}</li>` : "";
    const contents = [ch.questions ? plural(ch.questions, "question", "questions") : "", ch.cards ? plural(ch.cards, "carte", "cartes") : ""]
      .filter(Boolean).join(" · ") || "Rien encore";
    return `${header}<li class="chapter-item">
      <span class="chapter-name"><strong>${escapeHtml(ch.single ? "Tout le cours" : ch.title)}</strong><small class="muted">${contents}</small></span>
      <span class="chapter-actions">
        <button class="primary small" type="button" data-chapter-create="${i}">Créer</button>
        <button class="icon more-btn" type="button" data-chapter-more="${i}" aria-label="Plus d'actions">•••</button>
      </span></li>`;
  }).join("") || `<li class="empty muted">Ajoute un fichier pour voir ses chapitres.</li>`;
}

// Le fichier d'un groupe de chapitres : nouvelle version (même nom : elle remplace l'ancienne) ou redécoupage
document.addEventListener("click", async (e) => {
  const replace = e.target.closest("[data-replace-file]")?.dataset.replaceFile;
  if (replace) {
    state.replacing = replace;
    $("#replace-input").value = "";
    return $("#replace-input").click();
  }
});
$("#replace-input").addEventListener("change", async () => {
  const picked = $("#replace-input").files[0];
  const old = state.course?.files.find((f) => f.id === state.replacing);
  if (!picked || !old) return;
  const ext = (name) => (name.match(/\.[^.]+$/)?.[0] || "").toLowerCase();
  if (ext(picked.name) === ext(old.name)) {
    // Même format : envoyé sous le nom de l'ancien fichier, il le remplace (nouvelle version)
    return uploadFiles([new File([picked], old.name, { type: picked.type })]);
  }
  // Autre format (PDF au lieu de Keynote…) : on ajoute le nouveau fichier puis on retire l'ancien
  if (!confirm(`Remplacer « ${old.name} » par « ${picked.name} » ? Tes quiz et tes cartes sont gardés.`)) return;
  await uploadFiles([picked]);
  await api(`/api/courses/${state.course.id}/files/${old.id}`, { method: "DELETE" }).catch(() => {});
  refreshCourse();
});

// « Créer » et « … » d'un chapitre : un petit menu sous le bouton.
$("#chapter-list").addEventListener("click", (e) => {
  const button = e.target.closest("[data-chapter-create], [data-chapter-more]");
  if (!button) return;
  const i = button.dataset.chapterCreate ?? button.dataset.chapterMore;
  const ch = state.courseChapters[i];
  const items = button.dataset.chapterCreate !== undefined
    ? [["quiz", "Un quiz"], ["cartes", "Des flashcards"]]
    : [["see-quiz", "Voir les questions", !ch.questions], ["see-cards", "Voir les cartes", !ch.cards],
       ["export", "Exporter le quiz", !ch.quizzes.length]];
  openChapterMenu(button, items.map(([action, label, off]) => `<button type="button" role="menuitem" data-chapter-action="${action}"
    data-i="${i}" ${off ? "disabled" : ""}><strong>${label}</strong></button>`).join(""));
});
function openChapterMenu(anchor, html) {
  const menu = $("#chapter-menu");
  menu.innerHTML = html;
  menu.hidden = false;
  const box = anchor.getBoundingClientRect();
  menu.style.top = `${box.bottom + window.scrollY + 6}px`;
  menu.style.left = `${Math.max(12, box.right + window.scrollX - 220)}px`;
}
document.addEventListener("click", (e) => {
  if (!e.target.closest("#chapter-menu, [data-chapter-create], [data-chapter-more]")) $("#chapter-menu").hidden = true;
});
$("#chapter-menu").addEventListener("click", (e) => {
  const item = e.target.closest("[data-chapter-action]");
  if (!item) return;
  $("#chapter-menu").hidden = true;
  const ch = state.courseChapters[item.dataset.i];
  const action = item.dataset.chapterAction;
  if (action === "quiz" || action === "cartes") {
    // Page de création avec seulement ce chapitre coché.
    excludedSet().clear();
    if (!ch.single) chapterUnits().forEach((u) => { if (u.key !== ch.key) excludedSet().add(u.key); });
    state.keepSelection = true;
    return go(`#/cours/${state.course.id}/nouveau/${action}`);
  }
  if (action === "export") return exportItem("quiz", ch.quizzes[0].id, "#course-chapters-status");
  state.chapterFilter = ch.single ? null : ch.title;
  // Même page : on filtre sur ce chapitre et on ouvre le dépliant
  const tab = action === "see-quiz" ? "quiz" : "cartes";
  history.replaceState(null, "", courseHash(tab));
  state.tab = tab;
  renderQuizPanel(state.course);
  if (state.deck) renderCardGrid();
  $(`#panel-${tab}`).open = true;
  $(`#panel-${tab}`).scrollIntoView({ behavior: "smooth", block: "start" });
});

// Filtre « un chapitre » (depuis « Voir les questions / les cartes » d'un chapitre). « Chapitre 1 — X » = « Chapitre 1 : X ».
const titleKey = (t) => String(t).toLowerCase().match(/[\p{L}\p{N}]+/gu)?.join(" ") || "";
const inChapter = (scope) => !state.chapterFilter || (scope || []).some((t) => titleKey(t) === titleKey(state.chapterFilter));
function chapterChip() {
  return state.chapterFilter ? `<p class="chapter-chip">Chapitre : <b>${escapeHtml(state.chapterFilter)}</b>
    <button class="link-button" type="button" data-clear-chapter>Tout afficher</button></p>` : "";
}
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-clear-chapter]")) { state.chapterFilter = null; renderQuizPanel(state.course); if (state.deck) renderCardGrid(); }
});

function renderQuizPanel(course) {
  const quizzes = course.quizzes.filter((q) => inChapter(q.scope));
  $("#quiz-chip").innerHTML = chapterChip();
  $("#quiz-list").innerHTML = quizzes.length
    ? quizzes.map((q) => quizItem(q, course, course.quizzes.length - course.quizzes.indexOf(q))).join("")
    : `<li class="empty muted">Aucun quiz ${state.chapterFilter ? "sur ce chapitre" : "pour l'instant"} : clique sur « + Nouveau quiz ».</li>`;
}

// Le(s) fichier(s) du cours, à la racine de l'arbre : nouvelle version, redécoupage des chapitres, retrait.
function renderFiles(course) {
  $("#source-names").innerHTML = course.files.map((f) => `<span class="source-name">${ICON_FILE}<strong>${escapeHtml(f.name)}</strong></span>`).join("")
    || `<span class="muted">Aucun fichier</span>`;
  $("#file-list").innerHTML = course.files.length ? course.files.map((f) => {
    const chapters = f.chapters || [];
    const found = chapters.length
      ? `${plural(chapters.length, "chapitre", "chapitres")} ${f.chapters_by === "ai" ? "repérés par l'IA" : "repérés automatiquement"}`
      : f.chapters_by === "ai" ? "L'IA n'a pas trouvé de chapitres" : "Pas de chapitres repérés";
    return `
    <li class="file-item source-file">
      ${ICON_FILE}
      <div class="file-main">
        <strong>${escapeHtml(f.name)}</strong>
        <small class="muted">${found} · ${f.revisions > 1 ? `version ${f.revisions}, ` : ""}mis à jour ${formatDate(f.updated_at)} · ${formatSize(f.size)}</small>
        ${f.definitions ? `<small class="muted"><button class="link-button inline" data-show-defs="${f.id}">${
          plural(f.definitions, "définition repérée", "définitions repérées")}</button></small>` : ""}
      </div>
      <div class="file-actions">
        <button class="ghost small" type="button" data-replace-file="${f.id}" title="Dépose la version à jour : les chapitres sont redécoupés, tes quiz et cartes gardés">Nouvelle version</button>
        <button class="ghost small" type="button" data-detect-file="${f.id}" ${state.detecting ? "disabled" : ""} title="${
          f.chapters_by === "ai" ? "Relancer l'IA pour repérer les chapitres" : "Repérer les chapitres avec l'IA"}">Redécouper</button>
        <button class="icon" data-remove-file="${f.id}" aria-label="Retirer ${escapeHtml(f.name)}" title="Retirer du cours">✕</button>
      </div>
    </li>`;
  }).join("") : `<li class="empty muted">Aucun fichier : dépose ton cours ci-dessous.</li>`;
}

// « Quiz 3 · Titre » ; un quiz renommé s'affiche exactement avec le nom choisi.
const quizLabel = (q, number = null) => (number && !q.custom_title ? `Quiz ${number} · ${q.title}` : q.title);

// Dans un cours, on gère ses quiz (aperçu des questions, renommer, supprimer) ; on les passe dans Réviser (`play`).
function quizItem(q, course, number = null, { play = false } = {}) {
  const outdated = course && q.course_version < course.version;
  const best = q.best_score ? `Meilleur score ${q.best_score.score}/${q.best_score.total}` : "Pas encore fait";
  const label = quizLabel(q, number);
  return `
    <li class="quiz-item${play ? " play" : ""}">
      <button class="quiz-open" ${play ? `data-open-quiz="${q.id}"` : `data-preview-quiz="${q.id}" aria-expanded="false"`}>
        <strong>${escapeHtml(label)}</strong>
        <small class="muted">${q.count} questions${play && q.course_name ? ` · ${escapeHtml(q.course_name)}` : ` · ${q.difficulty || "moyen"} · ${q.provider === "claude-app" ? "App Claude" : q.provider === "claude" ? "Claude" : "Local"} · ${formatDate(q.created_at)}`}</small>
        ${scopeNote(q.scope)}
        <small><span class="badge">${best}</span>${q.attempts > 1 ? ` <span class="muted">${q.attempts} essais</span>` : ""}
          ${outdated ? ` <span class="badge warn-badge" title="Le cours a été modifié depuis la création de ce quiz">cours mis à jour depuis</span>` : ""}</small>
      </button>
      ${play ? `<button class="primary small" data-open-quiz="${q.id}">Faire le quiz</button>` : `
      <button class="icon" data-export-quiz="${q.id}" aria-label="Exporter ce quiz (fichier texte)" title="Exporter (fichier texte à envoyer)">${ICON_SHARE}</button>
      <button class="icon" data-rename-quiz="${q.id}" data-title="${escapeHtml(label)}" aria-label="Renommer ce quiz" title="Renommer">${ICON_EDIT}</button>
      <button class="icon" data-delete-quiz="${q.id}" aria-label="Supprimer ce quiz" title="Supprimer">${ICON_TRASH}</button>`}
      ${play ? "" : `<div class="quiz-preview" id="preview-${q.id}" hidden></div>`}
    </li>`;
}

// ---------- Paquets : tous les quiz et flashcards d'un cours ou d'un semestre (.zip), à réimporter ----------
$("#course-export").addEventListener("click", () => {
  $("#course-more-menu").hidden = true;
  exportItem("course", state.course.id, "#course-status");
});
$("#pack-import-btn").addEventListener("click", () => { $("#pack-input").value = ""; $("#pack-input").click(); });
$("#pack-input").addEventListener("change", async () => {
  const file = $("#pack-input").files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    state.pack = await api("/api/pack/preview", { method: "POST", body: form });
  } catch (err) {
    return setStatus("#courses-status", err.message, false);
  }
  const options = (row) => [
    ...state.pack.existing.map((c) => `<option value="${c.id}" ${row.match?.id === c.id ? "selected" : ""}>${escapeHtml(c.name)}</option>`),
    `<option value="new" ${row.match ? "" : "selected"}>Créer le cours « ${escapeHtml(row.name)} »</option>`,
    `<option value="">Ne pas importer</option>`].join("");
  $("#pack-rows").innerHTML = state.pack.courses.map((row, i) => `
    <li class="pack-row">
      <span class="pack-name"><strong>${escapeHtml(row.name)}</strong>
        <small class="muted">${[row.quizzes ? plural(row.quizzes, "quiz", "quiz") : "", row.cards ? plural(row.cards, "carte", "cartes") : ""].filter(Boolean).join(" · ")}${row.folder ? ` · ${escapeHtml(row.folder)}` : ""}</small></span>
      <span class="pack-arrow" aria-hidden="true">→</span>
      <select class="chip" data-pack-target="${i}" aria-label="Où importer ${escapeHtml(row.name)}">${options(row)}</select>
      ${row.match ? `<small class="pack-found">retrouvé</small>` : `<small class="pack-new">à rattacher</small>`}
    </li>`).join("");
  $("#pack-error").hidden = true;
  $("#pack-dialog").showModal();
});
$("#pack-rows").addEventListener("change", (e) => {
  const select = e.target.closest("[data-pack-target]");
  const badge = select?.parentElement.querySelector(".pack-found, .pack-new");
  if (badge) badge.remove();
});
$("#pack-cancel").addEventListener("click", () => $("#pack-dialog").close());
$("#pack-go").addEventListener("click", async () => {
  const targets = [...document.querySelectorAll("[data-pack-target]")].map((s) => s.value);
  $("#pack-go").disabled = true;
  try {
    const done = await api("/api/pack/import", jsonBody("POST", { token: state.pack.token, targets }));
    $("#pack-dialog").close();
    const parts = [done.questions ? plural(done.questions, "question", "questions") : "", done.cards ? plural(done.cards, "carte", "cartes") : ""].filter(Boolean);
    setStatus("#courses-status", parts.length
      ? `Importé : ${parts.join(" et ")} dans ${plural(done.courses, "cours", "cours")}${done.created ? ` (dont ${plural(done.created, "nouveau cours", "nouveaux cours")})` : ""}.`
      : "Rien de nouveau : ces quiz et ces cartes étaient déjà là.", true);
    loadCourses();
  } catch (err) {
    $("#pack-error").textContent = err.message;
    $("#pack-error").hidden = false;
  } finally {
    $("#pack-go").disabled = false;
  }
});

// ---------- S'échanger quiz et flashcards : fichier texte à envoyer (Messages, mail…), à importer ailleurs ----------
async function exportItem(kind, id, statusSelector) {
  try {
    if (state.desktop) {
      const { name } = await api(`/api/export/${kind}/${id}`, { method: "POST" });
      setStatus(statusSelector, kind === "course" || kind === "folder"
        ? `Enregistré dans Téléchargements : « ${name} ». Pour le réimporter (sur ce Mac ou un autre) : Mes cours → Importer.`
        : `Enregistré dans Téléchargements : « ${name} ». Envoie-le (Messages, mail…) : il s'importe dans Pirouette${kind === "cards" ? ", Anki ou Quizlet" : ""}.`, true);
    } else {
      location.href = `/api/export/${kind}/${id}`;
    }
  } catch (err) {
    setStatus(statusSelector, err.message, false);
  }
}
document.addEventListener("click", (e) => {
  const button = e.target.closest("[data-export-quiz]");
  if (button) exportItem("quiz", button.dataset.exportQuiz, "#quiz-status");
  const importer = e.target.closest("[data-import]");
  if (importer) {
    state.importFrom = importer.closest("#panel-cartes") ? "cartes" : "quiz";
    $("#import-input").click();
  }
});
$("#cards-export").addEventListener("click", () => exportItem("cards", state.course.id, "#cards-status"));
$("#import-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  const onCards = state.importFrom === "cartes";
  const status = onCards ? "#cards-status" : "#quiz-status";
  const form = new FormData();
  form.append("file", file);
  try {
    const r = await api(`/api/courses/${state.course.id}/import`, { method: "POST", body: form });
    const skipped = r.skipped ? ` (${r.skipped} ignorée${r.skipped > 1 ? "s" : ""} : illisible${r.skipped > 1 ? "s" : ""} ou déjà là)` : "";
    state.deck = null;
    await refreshCourse();
    $(r.kind === "cards" ? "#panel-cartes" : "#panel-quiz").open = true;  // des cartes importées depuis « Quiz » : on les montre
    setStatus(r.kind === "cards" ? "#cards-status" : "#quiz-status", r.kind === "cards"
      ? `${plural(r.added, "carte ajoutée", "cartes ajoutées")}${skipped}.`
      : r.merged ? `${plural(r.added, "question ajoutée", "questions ajoutées")} au quiz « ${r.title} » (même chapitre)${skipped}.`
        : `Quiz « ${r.title} » importé : ${plural(r.added, "question", "questions")}${skipped}.`, true);
  } catch (err) {
    setStatus(status, err.message, false);
  }
});

// Aperçu d'un quiz dans le cours : ses questions et leurs réponses, sans le passer.
document.addEventListener("click", async (e) => {
  const button = e.target.closest("[data-preview-quiz]");
  if (!button) return;
  const box = $(`#preview-${button.dataset.previewQuiz}`);
  const open = box.hidden;
  button.setAttribute("aria-expanded", String(open));
  box.hidden = !open;
  if (!open || box.dataset.loaded) return;
  const quiz = await api(`/api/quizzes/${button.dataset.previewQuiz}`).catch(() => null);
  if (!quiz) return;
  box.dataset.loaded = "1";
  box.innerHTML = `<ol>${quiz.questions.map((q) => `<li><span>${escapeHtml(plainMd(q.question))}</span>
      <small class="muted">${escapeHtml(plainMd(String(q.answer)))}</small></li>`).join("")}</ol>
    ${quiz.course_id ? `<p class="muted small-text">Pour passer ce quiz : <a href="#/reviser/cours/${quiz.course_id}">Réviser ce cours →</a></p>` : ""}`;
});

document.addEventListener("click", async (e) => {
  const target = e.target.closest("[data-open-quiz], [data-rename-quiz], [data-delete-quiz], [data-remove-file], [data-create], [data-file-create]");
  if (!target) return;
  const { openQuiz, renameQuiz, deleteQuiz, removeFile, create, fileCreate, file } = target.dataset;
  try {
    if (openQuiz) {
      const back = !$("#view-review-scope").hidden ? location.hash : null;
      startQuiz(await api(`/api/quizzes/${openQuiz}`), undefined, { back });
    }
    if (renameQuiz) {
      const title = await askText("Nouveau nom du quiz", target.dataset.title);
      if (title) {
        await api(`/api/quizzes/${renameQuiz}`, jsonBody("PATCH", { title }));
        state.course && !$("#view-course").hidden ? refreshCourse() : loadCourses();
      }
    }
    if (deleteQuiz && confirm("Supprimer ce quiz ?")) {
      await api(`/api/quizzes/${deleteQuiz}`, { method: "DELETE" });
      state.course && !$("#view-course").hidden ? refreshCourse() : loadCourses();
    }
    if (removeFile && confirm("Retirer ce fichier du cours ?")) {
      await api(`/api/courses/${state.course.id}/files/${removeFile}`, { method: "DELETE" });
      refreshCourse();
    }
    if (create === "cartes") return openCardsMenu(target);
    if (create) {
      if (!state.course.files.length) return go(courseHash("fichiers"));
      go(courseHash(`nouveau/${create}`));
    }
    if (fileCreate) {
      // Quiz ou flashcards sur ce fichier seulement : tout le reste est décoché.
      const excluded = excludedSet();
      excluded.clear();
      chapterUnits().filter((u) => u.file !== file).forEach((u) => excluded.add(u.key));
      state.keepSelection = true;
      go(courseHash(`nouveau/${fileCreate}`));
    }
  } catch (err) {
    alert(err.message);
  }
});

$("#rename-course").addEventListener("click", async () => {
  $("#course-more-menu").hidden = true;
  const name = await askText("Nouveau nom du cours", state.course.name);
  if (!name) return;
  await api(`/api/courses/${state.course.id}`, jsonBody("PATCH", { name }));
  refreshCourse();
});

// Repartir de zéro (après avoir beaucoup créé, modifié, supprimé des quiz) : le contenu reste, le suivi repart à zéro.
$("#reset-stats").addEventListener("click", async () => {
  $("#course-more-menu").hidden = true;
  if (!confirm(`Réinitialiser les statistiques de « ${state.course.name} » ?\n\nLes scores des quiz, les réussites et erreurs par question, `
    + "la progression des flashcards (elles redeviennent nouvelles), l'historique des révisions et les notes des partiels "
    + "de ce cours sont remis à zéro. Les quiz, les cartes et les fichiers sont gardés.")) return;
  const { quizzes, cards } = await api(`/api/courses/${state.course.id}/reset-stats`, { method: "POST" });
  state.deck = null;
  await refreshCourse();
  alert(`C'est fait : ${plural(quizzes, "quiz", "quiz")} et ${plural(cards, "carte", "cartes")} repartent de zéro.`);
});

$("#delete-course").addEventListener("click", async () => {
  $("#course-more-menu").hidden = true;
  if (!confirm(`Supprimer le cours « ${state.course.name} », ses fichiers, ses quiz et ses flashcards ?`)) return;
  await api(`/api/courses/${state.course.id}`, { method: "DELETE" });
  location.hash = "#/cours";
});

// ---------- Dépôt des fichiers ----------
const dropzone = $("#dropzone");
["dragenter", "dragover"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove("over"); }));
dropzone.addEventListener("drop", (e) => uploadFiles(e.dataTransfer.files));
const startDrop = $("#start-drop");
["dragenter", "dragover"].forEach((ev) =>
  startDrop.addEventListener(ev, (e) => { e.preventDefault(); startDrop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) =>
  startDrop.addEventListener(ev, (e) => { e.preventDefault(); startDrop.classList.remove("over"); }));
startDrop.addEventListener("drop", (e) => uploadFiles(e.dataTransfer.files));
$("#file-input").addEventListener("change", (e) => { uploadFiles(e.target.files); e.target.value = ""; });

async function uploadFiles(fileList) {
  const files = [...fileList];  // copie : la liste du champ est vidée juste après l'appel
  if (!files.length) return;
  if (state.tab !== "fichiers") await openCourse(state.course.id, "fichiers");
  if (location.hash !== courseHash("fichiers")) history.replaceState(null, "", courseHash("fichiers"));
  const form = new FormData();
  for (const file of files) form.append("files", file);
  const status = $("#upload-status");
  status.hidden = false;
  status.className = "status";
  status.textContent = "Lecture du cours…";
  try {
    const { results, course } = await api(`/api/courses/${state.course.id}/files`, { method: "POST", body: form });
    await refreshCourse();
    status.textContent = Object.entries(results)
      .map(([name, r]) => `${r === "updated" ? "Nouvelle version enregistrée" : "Ajouté"} : ${name}`).join(" · ");
    status.className = "status ok";
    // Puis l'IA repère les chapitres des fichiers déposés (si un moteur est prêt).
    const names = Object.keys(results).map((n) => n.toLowerCase());
    const ids = course.files.filter((f) => names.includes(f.name.toLowerCase())).map((f) => f.id);
    await detectChapters(ids, { quiet: true });
    // Chapitres repérés : Pirouette propose de tout préparer (un quiz et des flashcards par chapitre).
    state.offerPrepare = state.course.id;
    await refreshCourse();
  } catch (err) {
    status.textContent = err.message;
    status.className = "status ko";
  }
}

// ---------- Chapitres ----------
// Chaque fichier est découpé en chapitres (clé « <fichier>-<n> ») ; un fichier sans chapitre compte en entier
// (clé « <fichier> »). Le choix se fait sur la page de création d'un quiz ou de flashcards.
function chapterUnits(course = state.course) {
  return course.files.flatMap((f) => f.chapters?.length
    ? f.chapters.map((c, i) => ({ key: `${f.id}-${i}`, file: f.id, title: c.title }))
    : [{ key: f.id, file: f.id, title: f.name }]);
}

function excludedSet() {
  const id = state.course.id;
  return (state.excluded[id] ||= new Set());
}

const isChecked = (key) => !excludedSet().has(key);
const hasSelection = () => chapterUnits().some((u) => isChecked(u.key));

// Ce qu'on envoie au serveur : "" = tout le cours ; sinon les fichiers entiers et les chapitres cochés.
function selectionParam() {
  const units = chapterUnits();
  if (units.every((u) => isChecked(u.key))) return "";
  const keys = [];
  for (const f of state.course.files) {
    const own = units.filter((u) => u.file === f.id);
    if (own.every((u) => isChecked(u.key))) keys.push(f.id);
    else keys.push(...own.filter((u) => isChecked(u.key)).map((u) => u.key));
  }
  return keys.join(",");
}

function renderChapterPicker(course) {
  const known = new Set(chapterUnits(course).map((u) => u.key));
  for (const key of [...excludedSet()]) if (!known.has(key)) excludedSet().delete(key);  // fichier modifié

  $("#chapter-groups").innerHTML = course.files.map((f) => {
    const chapters = f.chapters || [];
    const body = chapters.length
      ? `<ul class="chapter-list">${chapters.map((c, i) => `
          <li><label><input type="checkbox" data-chapter="${f.id}-${i}" ${isChecked(`${f.id}-${i}`) ? "checked" : ""}>
            <span>${escapeHtml(c.title)}</span><small class="muted">${formatWords(c.chars)}</small></label></li>`).join("")}</ul>`
      : `<p class="chapter-note muted">Pas de chapitres repérés : le fichier est pris en entier.</p>`;
    return `<div class="chapter-group">
      <div class="chapter-file"><label><input type="checkbox" data-chapter-file="${f.id}"> ${escapeHtml(f.name)}</label></div>
      ${body}</div>`;
  }).join("");
  syncFileBoxes();
  renderScope();
}

// Case du fichier : cochée, décochée ou « à moitié » selon ses chapitres.
function syncFileBoxes() {
  for (const box of document.querySelectorAll("[data-chapter-file]")) {
    const own = chapterUnits().filter((u) => u.file === box.dataset.chapterFile);
    const checked = own.filter((u) => isChecked(u.key)).length;
    box.checked = checked === own.length;
    box.indeterminate = checked > 0 && checked < own.length;
  }
}

function renderScope() {
  const units = chapterUnits();
  const chosen = units.filter((u) => isChecked(u.key));
  for (const el of document.querySelectorAll("[data-scope]")) {
    el.classList.toggle("none", !chosen.length);
    if (!chosen.length) el.innerHTML = "<strong>Rien de coché</strong> : coche au moins un chapitre.";
    else if (chosen.length === units.length) el.innerHTML = "Porte sur <strong>tout le cours</strong>.";
    else el.innerHTML = `Porte sur <strong>${chosen.length} partie${chosen.length > 1 ? "s" : ""} sur ${units.length}</strong> : ${chosen.map((u) => escapeHtml(u.title)).join(" · ")}`;
  }
  updateQuizMode();
}

// Un quiz par chapitre (le choix par défaut dès que plusieurs chapitres sont cochés) ou un seul quiz.
// Nombre de cartes : tout le chapitre (autant que nécessaire) ou un nombre précis
const coverCards = () => $("#cards-size-mode").value === "cover" && !$("#focus").value.trim();
$("#cards-size-mode").addEventListener("change", () => {
  $("#cards-count-field").hidden = $("#cards-size-mode").value === "cover";
  renderAppRequest();
});

// Taille du quiz : tout le chapitre (banque de questions, taille calculée par Pirouette) ou un nombre précis
const coverMode = () => $("#quiz-size-mode").value === "cover" && !$("#focus").value.trim();
$("#quiz-size-mode").addEventListener("change", () => {
  $("#num-questions-field").hidden = $("#quiz-size-mode").value === "cover";
  updateQuizMode();
  updateManualHint();
});

function perChapter() {
  return state.createKind === "quiz" && !$("#quiz-mode-row").hidden && state.quizMode !== "single";
}
function updateQuizMode() {
  if (!state.course || state.createKind !== "quiz") return;
  const chosen = chapterUnits().filter((u) => isChecked(u.key)).length;
  $("#quiz-mode-row").hidden = chosen < 2;
  document.querySelectorAll("[data-quiz-mode]").forEach((b) => b.classList.toggle("active", b.dataset.quizMode === (state.quizMode || "chapters")));
  const each = perChapter();
  $("#num-questions-label").textContent = each ? "Questions par quiz" : "Nombre de questions";
  $("#manual-field").hidden = each;
  $("#quiz-mode-hint").textContent = each
    ? `${chosen} quiz${coverMode() ? " qui couvrent chacun tout leur chapitre" : ` de ${Number($("#num-questions").value) || 10} questions`}, un par chapitre coché, créés l'un après l'autre en arrière-plan.`
    : "Un seul quiz sur tous les chapitres cochés.";
  $("#create-btn").textContent = each ? `Créer les ${chosen} quiz` : "Générer le quiz";
}
document.querySelectorAll("[data-quiz-mode]").forEach((b) => b.addEventListener("click", () => {
  state.quizMode = b.dataset.quizMode;
  updateQuizMode();
}));
$("#num-questions").addEventListener("input", updateQuizMode);

function scopeNote(scope) {
  return scope?.length ? `<small class="muted">Sur : ${scope.map(escapeHtml).join(" · ")}</small>` : "";
}

function formatWords(chars) {
  const words = Math.max(1, Math.round((chars || 0) / 6 / 10) * 10);
  return `≈ ${words.toLocaleString("fr-FR")} mots`;
}

document.addEventListener("change", (e) => {
  const key = e.target.dataset?.chapter;
  const file = e.target.dataset?.chapterFile;
  if (!key && !file) return;
  const excluded = excludedSet();
  const keys = key ? [key] : chapterUnits().filter((u) => u.file === file).map((u) => u.key);
  for (const k of keys) e.target.checked ? excluded.delete(k) : excluded.add(k);
  if (file) document.querySelectorAll(`[data-chapter^="${file}-"]`).forEach((b) => { b.checked = e.target.checked; });
  syncFileBoxes();
  renderScope();
});

$("#chapters-all").addEventListener("click", () => { excludedSet().clear(); renderChapterPicker(state.course); });
$("#chapters-none").addEventListener("click", () => {
  chapterUnits().forEach((u) => excludedSet().add(u.key));
  renderChapterPicker(state.course);
});

document.addEventListener("click", (e) => {
  const id = e.target.closest("[data-detect-file]")?.dataset.detectFile;
  if (id) detectChapters([id]);
});

// L'IA choisie (moteur par défaut, ou le dernier utilisé) repère les chapitres, fichier par fichier.
async function detectChapters(fileIds, { quiet = false } = {}) {
  if (!fileIds.length || state.detecting) return;
  if (!state.config) await loadConfig();
  const provider = selectedProvider();
  const status = $("#chapters-status");
  if (!state.config[provider]?.available) {
    if (quiet) return;  // pas d'IA prête : on garde le repérage automatique
    status.hidden = false;
    status.className = "status ko";
    status.innerHTML = `L'IA « ${provider === "claude" ? "Claude" : "locale"} » n'est pas prête : <a href="#/reglages">configure-la dans Réglages</a>.`;
    return;
  }
  const courseId = state.course.id;
  state.detecting = true;
  renderFiles(state.course);
  status.hidden = false;
  const found = [];
  try {
    for (const id of fileIds) {
      const f = state.course.files.find((x) => x.id === id);
      if (!f) continue;
      status.className = "status";
      status.textContent = `L'IA repère les chapitres de « ${f.name} »…`;
      const result = await api(`/api/courses/${courseId}/files/${id}/chapters`, { method: "POST", body: engineForm() });
      found.push(`${f.name} : ${result.found ? `${result.found} chapitres` : "pas de chapitres"}`);
      if (state.course?.id !== courseId) return;  // on a changé de cours entre-temps
      state.course.files = result.course.files;
      renderFiles(state.course);
      renderTiles(state.course);
    }
    status.className = "status ok";
    status.textContent = `Chapitres repérés · ${found.join(" · ")}`;
  } catch (err) {
    status.className = "status ko";
    status.textContent = `Repérage par l'IA impossible (${err.message}). Le découpage automatique est gardé.`;
  } finally {
    state.detecting = false;
    if (state.course?.id === courseId) renderFiles(state.course);
  }
}

// ---------- Moteurs ----------
async function loadConfig({ keepSelection = false } = {}) {
  const config = await api("/api/config");
  state.config = config;

  const localStatus = $("#local-status");
  localStatus.classList.toggle("warn", !config.local.available);
  if (config.local.available) {
    localStatus.textContent = `Sur ton Mac, hors ligne, gratuit · ${config.local.models.length} modèle(s)`;
  } else if (config.local.running) {
    localStatus.innerHTML = `Aucun modèle installé : <a href="#/reglages">télécharge-le dans Réglages</a>`;
  } else {
    localStatus.innerHTML = `Ollama n'est pas ouvert ou pas installé : <a href="#/reglages">configurer l'IA locale</a>`;
  }
  const claudeOn = config.claude.enabled;
  // Deux choix : l'app Claude (par défaut) et l'IA locale ; Claude par clé API seulement s'il est activé.
  document.querySelector("input[name=provider][value=claude]").closest(".engine").hidden = !claudeOn;
  $("#engine-title").textContent = "Moteur IA";
  $("#engine-note").hidden = true;
  $("#engine-note").innerHTML = config.local.available
    ? `Sur ton Mac, hors ligne et gratuite · ${config.local.models.length} modèle(s)`
    : localStatus.innerHTML;
  $("#engine-note").classList.toggle("warn", !config.local.available);
  const claudeStatus = $("#claude-status");
  claudeStatus.classList.toggle("warn", !config.claude.available);
  claudeStatus.innerHTML = config.claude.available
    ? `Meilleure qualité · ${escapeHtml(config.claude.default_model)}`
    : `Ajoute ta clé API dans <a href="#/reglages">Réglages</a>`;

  const select = $("#local-model");
  const previous = select.value;
  const models = config.local.models.length ? config.local.models : [config.local.default_model];
  select.innerHTML = models.map((m) => `<option>${escapeHtml(m)}</option>`).join("");
  if (keepSelection && models.includes(previous)) select.value = previous;
  else if (models.includes(config.local.default_model)) select.value = config.local.default_model;

  // Tant qu'Ollama n'est pas prêt, on revérifie toutes les 5 s (inutile de recharger la page).
  clearTimeout(state.configTimer);
  if (!config.local.available) state.configTimer = setTimeout(() => loadConfig({ keepSelection: true }), 5000);
  if (keepSelection) return;

  // Dernier moteur choisi (ou celui par défaut).
  let saved = null;
  try { saved = localStorage.getItem("pirouette.engine"); } catch {}
  const provider = ["local", "claude", "app"].includes(saved) && (saved !== "claude" || claudeOn) ? saved : "app";
  document.querySelector(`input[name=provider][value=${provider}]`).checked = true;
  api("/api/claude-app").then((info) => {
    $("#app-status").innerHTML = info.installed ? "Avec ton abonnement Claude · branchée"
      : `À brancher d'abord dans <a href="#/reglages">Réglages</a>`;
    $("#app-status").classList.toggle("warn", !info.installed);
  }).catch(() => {});
  updateProviderUi();
}

document.querySelectorAll("input[name=provider]").forEach((r) => r.addEventListener("change", updateProviderUi));
function updateProviderUi() {
  const provider = selectedProvider();
  try { localStorage.setItem("pirouette.engine", provider); } catch {}
  $("#local-model-field").hidden = provider !== "local";
  // L'app Claude : Pirouette ne peut pas la piloter ; on prépare la demande à y coller.
  const viaApp = provider === "app";
  $("#app-request").hidden = !viaApp;
  $("#create-btn").hidden = viaApp;
  document.querySelector(".heat-note").hidden = provider !== "local";
  if (viaApp) renderAppRequest();
}

// Demande pour l'app Claude. Elle donne l'identifiant du cours et les clés des chapitres : Claude n'a pas à lister
// les cours ni à lire tout le cours, ce qui économise beaucoup de tokens (donc du forfait).
function appRequest() {
  const course = state.course;
  const units = chapterUnits(course);
  const chosen = units.filter((u) => isChecked(u.key));
  const whole = !chosen.length || chosen.length === units.length;
  const where = whole ? `tout le cours « ${course.name} »`
    : `${chosen.length > 1 ? "les chapitres" : "le chapitre"} ${chosen.map((u) => `« ${u.title} »`).join(", ")} du cours « ${course.name} »`;
  const focus = $("#focus").value.trim();
  const theme = focus ? `, uniquement sur le thème « ${focus} »` : "";
  const each = state.createKind === "quiz" && perChapter() && (chosen.length || units.length) > 1;
  const listed = chosen.length ? chosen : units;
  const ids = `Cours : ${course.id} · chapitres : ${whole && !each ? "tout le cours" : listed.map((u) => `${u.key} (${u.title})`).join(", ")}`;
  const read = each ? "lis chaque chapitre (pirouette_lire avec sa clé)"
    : whole ? "lis le cours (pirouette_lire)" : "lis seulement ces chapitres (pirouette_lire)";
  const brief = "Réponds ensuite par un résumé court.";
  if (state.createKind !== "quiz") {
    const howMany = coverCards() ? "des flashcards qui couvrent tout le texte (une par définition et par notion importante, de 10 à 50 selon la longueur)"
      : `${$("#cards-count").value || 20} flashcards`;
    return `Pirouette : ajoute ${howMany} sur ${where}${theme}.\n${ids}\n`
      + `Va droit au but : ne liste pas les cours, ${read}, puis enregistre les cartes (pirouette_ajouter_cartes). ${brief}`;
  }
  const names = { qcm: "QCM", vrai_faux: "vrai/faux", reponse_courte: "réponse courte", texte_a_trous: "texte à trous" };
  const types = [...document.querySelectorAll("input[name=types]:checked")].map((b) => names[b.value]);
  const n = $("#num-questions").value || 10;
  const size = coverMode()
    ? (each ? "un quiz par chapitre qui couvre tout le chapitre" : "un quiz qui couvre tout le texte")
      + " : chaque définition et chaque notion importante a sa question, du début à la fin (de 10 à 40 questions selon la longueur)"
    : each ? `un quiz de ${n} questions par chapitre` : `un quiz de ${n} questions`;
  return `Pirouette : crée ${size} sur ${where}${theme}`
    + (types.length ? ` (${types.join(", ")})` : "") + `.\n${ids}\n`
    + `Va droit au but : ne liste pas les cours, ${read}, puis enregistre ${each
      ? "un quiz par chapitre (pirouette_creer_quiz, avec la clé du chapitre dans « chapitres »)" : "le quiz (pirouette_creer_quiz)"}. ${brief}`;
}
// La suite, à coller dans la même conversation : les flashcards des mêmes chapitres, sans relire le cours.
function appFollowUp() {
  const units = chapterUnits(state.course);
  const chosen = units.filter((u) => isChecked(u.key));
  const listed = chosen.length ? chosen : units;
  return `Dans cette même conversation, ajoute aussi 15 flashcards sur ces mêmes chapitres (${listed.map((u) => u.key).join(", ")}). `
    + "Tu as déjà lu le texte : ne le relis pas. Enregistre-les avec pirouette_ajouter_cartes, puis réponds par un résumé court.";
}
function renderAppRequest() {
  if ($("#app-request").hidden) return;
  $("#app-request-text").textContent = appRequest();
  $("#app-request-next").hidden = state.createKind !== "quiz";
}
// La demande suit les réglages de la page (nombre, types, chapitres, thème).
["#num-questions", "#cards-count", "#focus"].forEach((id) => $(id).addEventListener("input", renderAppRequest));
$("#focus").addEventListener("input", () => {
  // Un thème précis : un petit quiz ciblé, pas tout le chapitre
  $("#num-questions-field").hidden = coverMode();
  $("#quiz-size-mode").disabled = Boolean($("#focus").value.trim());
  $("#cards-count-field").hidden = coverCards();
  $("#cards-size-mode").disabled = Boolean($("#focus").value.trim());
});
$("#view-create").addEventListener("change", renderAppRequest);
$("#view-create").addEventListener("click", () => setTimeout(renderAppRequest, 0));
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = Object.assign(document.createElement("textarea"), { value: text });
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
}
$("#app-request-next").addEventListener("click", async () => {
  await copyText(appFollowUp());
  setStatus("#app-request-status", "Suite copiée. Colle-la dans la même conversation, une fois le quiz enregistré.", true);
});
$("#app-request-copy").addEventListener("click", async () => {
  const text = appRequest();
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = Object.assign(document.createElement("textarea"), { value: text });
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  setStatus("#app-request-status", "Copié. Colle-la dans l'app Claude (⌘V).", true);
});
const selectedProvider = () => document.querySelector("input[name=provider]:checked")?.value || "local";

// ---------- Créer un quiz ou des flashcards ----------
async function openCreate(id, kind) {
  const course = await loadCourse(id);
  if (!course.files.length) return go(courseHash("fichiers"));
  if (!state.keepSelection) excludedSet().clear();  // par défaut : tout le cours
  state.keepSelection = false;
  state.createKind = kind;
  const isQuiz = kind === "quiz";
  $("#create-title").textContent = isQuiz ? "Nouveau quiz" : "Ajouter des flashcards";
  setCrumbs(courseCrumbs(course, isQuiz ? "quiz" : "cartes", isQuiz ? "Nouveau quiz" : "Ajouter des cartes"));
  $("#create-quiz-options").hidden = !isQuiz;
  $("#create-cards-options").hidden = isQuiz;
  $("#manual-field").hidden = !isQuiz;
  $("#focus").value = $("#manual").value = "";
  $("#focus-hint").textContent = isQuiz
    ? "Pirouette ne garde que les passages du cours qui en parlent, et l'IA ne pose de questions que là-dessus."
    : "Pirouette ne garde que les passages du cours qui en parlent, et l'IA ne fait de cartes que là-dessus.";
  updateManualHint();
  $("#create-btn").textContent = isQuiz ? "Générer le quiz" : "Créer les cartes";
  state.quizMode = null;
  showError("#create-error", "");
  renderChapterPicker(course);
  show("create");
  updateProviderUi();  // la demande pour l'app Claude suit ce qu'on crée (quiz ou cartes)
}

function engineForm() {
  const form = new FormData();
  const provider = selectedProvider();
  form.append("provider", provider);
  form.append("model", provider === "local" ? $("#local-model").value : "");
  form.append("language", $("#language").value);
  form.append("chapters", selectionParam());
  return form;
}

// Lance une génération côté serveur, affiche sa progression et renvoie le résultat.
async function runJob(path, form, title) {
  $("#loading-title").textContent = title;
  $("#progress-log").innerHTML = "";
  show("loading");
  state.loadingAbort = new AbortController();
  let response;
  try {
    response = await fetch(`/api/courses/${state.course.id}/${path}`, { method: "POST", body: form, signal: state.loadingAbort.signal });
  } catch (err) {
    if (err.name === "AbortError") throw new Error("Création annulée.");
    throw err;
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Erreur ${response.status}`);
  }
  try {
    for await (const event of ndjson(response)) {
      if (event.type === "progress") logProgress(event.message);
      if (event.type === "error") throw new Error(event.message);
      if (event.type === "done") return event.result;
    }
  } catch (err) {
    if (err.name === "AbortError") throw new Error("Création annulée.");
    throw err;
  }
  throw new Error("La génération s'est interrompue.");
}

$("#loading-cancel").addEventListener("click", () => state.loadingAbort?.abort());
$("#create-btn").addEventListener("click", () => (state.createKind === "quiz" ? generateQuiz() : createCards()));

async function generateQuiz() {
  const types = [...document.querySelectorAll("input[name=types]:checked")].map((c) => c.value);
  if (!types.length) return showError("#create-error", "Choisis au moins un type de question.");
  if (!hasSelection()) return showError("#create-error", "Coche au moins un chapitre.");
  const form = engineForm();
  form.append("num_questions", coverMode() ? "0" : $("#num-questions").value);
  form.append("difficulty", $("#difficulty").value);
  form.append("types", types.join(","));
  form.append("course_share", $("#course-share").value);
  form.append("focus", $("#focus").value);
  form.append("manual", perChapter() ? "" : $("#manual").value);
  form.append("per_chapter", perChapter() ? "1" : "");
  showError("#create-error", "");
  // La création se fait en arrière-plan : on peut faire un autre quiz en attendant (suivi en bas à gauche).
  try {
    const { jobs } = await api(`/api/courses/${state.course.id}/quizzes/background`, { method: "POST", body: form });
    jobsState.open = true;
    jobsState.keepOpen = true;
    await refreshJobs();
    go(courseHash("quiz"));
    const status = $("#cards-status");
    status.hidden = true;
  } catch (err) {
    showError("#create-error", err.message);
  }
}

async function createCards() {
  if (!hasSelection()) return showError("#create-error", "Coche au moins un chapitre.");
  const form = engineForm();
  form.append("count", coverCards() ? "0" : $("#cards-count").value);
  form.append("focus", $("#focus").value);
  showError("#create-error", "");
  try {
    const deck = await runJob("cards", form, "Création des flashcards…");
    state.deck = deck;
    state.newCards = new Set(deck.cards.slice(-deck.added).map((c) => c.id));
    await openCourse(state.course.id, "cartes");
    history.replaceState(null, "", courseHash("cartes"));
    const status = $("#cards-status");
    status.textContent = `${deck.added} carte${deck.added > 1 ? "s" : ""} ajoutée${deck.added > 1 ? "s" : ""} au paquet.`;
    status.hidden = false;
  } catch (err) {
    backToCreate(err.message);
  }
}

const manualLines = () => $("#manual").value.split("\n").map((l) => l.replace(/^\s*(?:[-•*]|\d{1,2}[.)])\s*/, "").trim()).filter((l) => l.length >= 4);

function updateManualHint() {
  const mine = manualLines().length;
  const total = coverMode() ? mine : Math.max(Number($("#num-questions").value) || 0, mine);
  $("#manual-hint").textContent = mine && coverMode()
    ? `Ton quiz : ${plural(mine, "question à toi", "questions à toi")}, puis celles de l'IA pour couvrir tout le chapitre. L'IA écrit les réponses et les propositions à partir du cours.`
    : mine
    ? `Ton quiz : ${plural(mine, "question à toi", "questions à toi")}${total > mine ? ` + ${total - mine} créée${total - mine > 1 ? "s" : ""} par l'IA` : ""} (règle le nombre de questions plus haut). L'IA écrit les réponses et les propositions à partir du cours.`
    : "L'IA cherche la réponse dans ton cours et écrit les propositions. Une question sans réponse dans le cours est écartée.";
}
$("#manual").addEventListener("input", updateManualHint);
$("#num-questions").addEventListener("input", updateManualHint);

function backToCreate(message) {
  show("create");
  showError("#create-error", message);
  $("#create-error").scrollIntoView({ block: "center" });
}

function logProgress(message) {
  const li = document.createElement("li");
  li.textContent = message;
  $("#progress-log").appendChild(li);
}

function showError(selector, message) {
  const el = $(selector);
  el.textContent = message;
  el.hidden = !message;
}

// ---------- Flashcards : toutes les cartes sous les yeux ----------
async function loadDeck() {
  if (!state.deck || state.deck.course_id !== state.course.id) {
    state.deck = await api(`/api/courses/${state.course.id}/cards`);
    state.deck.course_id = state.course.id;
  }
  renderCardGrid();
}

const cardIsKnown = (card) => card.status === "known";

// Toutes les cartes du cours, à regarder librement : un clic retourne la carte. La révision se fait dans Réviser.
function renderCardGrid() {
  const cards = state.deck.cards;
  const none = !cards.length;
  $("#cards-help").hidden = $("#cards-foot").hidden = none;
  $("#fold-cards-count").textContent = none ? "· aucune pour l'instant" : `· ${cards.length}`;
  $("#cards-empty").hidden = !none;
  $("#cards-empty").textContent = "Pas encore de flashcards pour ce cours : clique sur « + Ajouter des cartes ».";
  $("#cards-chip").innerHTML = chapterChip();
  $("#card-grid").innerHTML = cards.filter((c) => inChapter(c.scope)).map((c) => fcardHtml(c, { removable: true })).join("");
  $("#cards-flip-all").textContent = "Voir les réponses";
  $("#cards-flip-all").hidden = none;
}

function fcardHtml(c, { removable = false } = {}) {
  const isNew = state.newCards?.has(c.id);
  if (removable) return binderCardHtml(c, isNew);
  return `
    <div class="fcard" data-card="${c.id}" role="button" tabindex="0" aria-pressed="false" title="Cliquer pour retourner la carte">
      <div class="fcard-inner">
        <div class="fcard-face fcard-front"><p>${escapeHtml(c.front)}</p></div>
        <div class="fcard-face fcard-back"><small>${escapeHtml(c.front)}</small><p>${escapeHtml(c.back)}</p></div>
      </div>
      <span class="fcard-mark">${isNew ? "Nouvelle" : ""}</span>
    </div>`;
}

// Le classeur du cours : chaque carte comme une carte à collectionner (rareté = ta progression), à retourner
// librement, avec « Modifier » et « Supprimer » toujours visibles dessous.
function binderCardHtml(c, isNew) {
  const rarity = cardRarity(c);
  const set = c.scope?.[0] || "";
  return `
    <div class="binder-slot">
      <div class="fcard binder ${rarity.cls}" data-card="${c.id}" role="button" tabindex="0" aria-pressed="false" title="Cliquer pour retourner la carte">
        <div class="fcard-inner">
          <div class="fcard-face fcard-front"><div class="binder-body">
            <span class="binder-head"><span class="binder-set">${escapeHtml(set)}</span><span class="binder-rarity">${isNew ? "Nouvelle !" : rarity.label}</span></span>
            <p>${escapeHtml(c.front)}</p>
          </div></div>
          <div class="fcard-face fcard-back"><div class="binder-body">
            <small>${escapeHtml(c.front)}</small><p>${escapeHtml(c.back)}</p>
          </div></div>
        </div>
      </div>
      <div class="binder-tools">
        <button class="link-button inline" type="button" data-edit-card="${c.id}">${ICON_EDIT} Modifier</button>
        <button class="link-button inline danger" type="button" data-delete-card="${c.id}">Supprimer</button>
      </div>
    </div>`;
}

function flipGridCard(el) {
  el.classList.toggle("flipped");
  el.setAttribute("aria-pressed", String(el.classList.contains("flipped")));
}

$("#card-grid").addEventListener("click", async (e) => {
  const edit = e.target.closest("[data-edit-card]");
  if (edit) return openCardDialog(state.deck.cards.find((c) => c.id === edit.dataset.editCard));
  const del = e.target.closest("[data-delete-card]");
  if (del) {
    if (!confirm("Supprimer cette carte ?")) return;
    state.deck = await api(`/api/courses/${state.course.id}/cards/${del.dataset.deleteCard}`, { method: "DELETE" });
    state.deck.course_id = state.course.id;
    return refreshCourse();
  }
  const el = e.target.closest("[data-card]");
  if (el) flipGridCard(el);
});
// Lire librement : toutes les cartes côté réponse, ou toutes côté question
$("#cards-flip-all").addEventListener("click", () => {
  const cards = [...document.querySelectorAll("#card-grid [data-card]")];
  const show = !cards.every((el) => el.classList.contains("flipped"));
  cards.forEach((el) => { el.classList.toggle("flipped", show); el.setAttribute("aria-pressed", String(show)); });
  $("#cards-flip-all").textContent = show ? "Voir les questions" : "Voir les réponses";
});
$("#card-grid").addEventListener("keydown", (e) => {
  if (e.target.closest("button")) return;
  const el = e.target.closest("[data-card]");
  if (el && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); flipGridCard(el); }
});

$("#cards-clear").addEventListener("click", async () => {
  if (!confirm("Effacer toutes les flashcards de ce cours (et leur suivi) ?")) return;
  await api(`/api/courses/${state.course.id}/cards`, { method: "DELETE" });
  state.deck = null;
  refreshCourse();
});

// ---------- « + » des flashcards : générer avec l'IA ou écrire une carte ----------
function openCardsMenu(button) {
  const menu = $("#cards-menu");
  if (!menu.hidden && menu.anchor === button) return closeCardsMenu();
  menu.anchor = button;
  menu.hidden = false;
  const rect = button.getBoundingClientRect();
  const left = Math.min(window.innerWidth - menu.offsetWidth - 12, Math.max(12, rect.right - menu.offsetWidth));
  menu.style.left = `${left + window.scrollX}px`;
  menu.style.top = `${rect.bottom + window.scrollY + 6}px`;
  menu.querySelector("button").focus();
}
function closeCardsMenu() { $("#cards-menu").hidden = true; }
document.addEventListener("click", (e) => {
  if (!$("#cards-menu").hidden && !e.target.closest("#cards-menu, [data-create=cartes]")) closeCardsMenu();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeCardsMenu(); });
$("#cards-menu").addEventListener("click", async (e) => {
  const choice = e.target.closest("[data-cards-choice]")?.dataset.cardsChoice;
  if (!choice) return;
  closeCardsMenu();
  if (choice === "ai") {
    if (!state.course.files.length) return go(courseHash("fichiers"));
    return go(courseHash("nouveau/cartes"));
  }
  $("#panel-cartes").open = true;
  openCardDialog();
});

// ---------- Écrire ou corriger une carte ----------
function openCardDialog(card = null, { onSaved = null } = {}) {
  state.editing = { card, onSaved };
  $("#card-dialog-title").textContent = card ? "Modifier la carte" : "Nouvelle carte";
  $("#card-front-input").value = card?.front || "";
  $("#card-back-input").value = card?.back || "";
  $("#card-save").textContent = card ? "Enregistrer" : "Ajouter la carte";
  $("#card-save-next").hidden = Boolean(card);
  $("#card-dialog-status").hidden = true;
  $("#card-dialog").showModal();
  $("#card-front-input").focus();
}

async function saveCardDialog(keepOpen) {
  const { card, onSaved } = state.editing;
  const body = { front: $("#card-front-input").value, back: $("#card-back-input").value };
  try {
    if (card) {
      Object.assign(card, await api(`/api/courses/${state.course.id}/cards/${card.id}`, jsonBody("PATCH", body)));
    } else {
      const result = await api(`/api/courses/${state.course.id}/cards/manual`, jsonBody("POST", body));
      state.deck?.cards.push(result.card);
      state.newCards = new Set([...(state.newCards || []), result.card.id]);
      state.course.cards.total += 1;
      state.course.cards.review += 1;
      if (keepOpen) {
        $("#card-front-input").value = $("#card-back-input").value = "";
        $("#card-front-input").focus();
        setStatus("#card-dialog-status", result.similar
          ? `Carte ajoutée. Elle ressemble à « ${result.similar.front} » : supprime l'une des deux si c'est la même.`
          : "Carte ajoutée. À la suivante !", !result.similar);
      } else if (result.similar) {
        alert(`Carte ajoutée. Elle ressemble à une carte existante : « ${result.similar.front} ».`);
      }
    }
  } catch (err) {
    return setStatus("#card-dialog-status", err.message, false);
  }
  if (!keepOpen) $("#card-dialog").close();
  if (onSaved) onSaved();
  else if (state.deck && !$("#view-course").hidden) {
    renderTiles(state.course);
    renderCardGrid();
  }
}

$("#card-form").addEventListener("submit", (e) => { e.preventDefault(); saveCardDialog(false); });
$("#card-save-next").addEventListener("click", () => saveCardDialog(true));
$("#card-cancel").addEventListener("click", () => $("#card-dialog").close());
$("#card-write").addEventListener("click", () => openCardDialog());

// ---------- Séance de révision : flashcards, et questions de quiz mélangées ----------
// Une séance = une liste d'éléments { kind: "card" | "question", … } venant de /api/session.
// Les cartes se notent À revoir / Difficile / Bien / Facile : Pirouette en déduit quand les reposer.
const RATING_LABELS = { again: "À revoir", hard: "Difficile", good: "Bien", easy: "Facile" };

function cardMode() {
  try { return localStorage.getItem("pirouette.cardMode") === "type" ? "type" : "flip"; } catch { return "flip"; }
}
function setCardMode(mode) {
  try { localStorage.setItem("pirouette.cardMode", mode); } catch {}
  document.querySelectorAll("[data-card-mode]").forEach((b) => {
    b.classList.toggle("active", b.dataset.cardMode === mode);
    b.setAttribute("aria-checked", b.dataset.cardMode === mode);
  });
}
document.addEventListener("click", (e) => {
  const button = e.target.closest("[data-card-mode]");
  if (!button) return;
  setCardMode(button.dataset.cardMode);
  // En pleine séance : la carte affichée passe tout de suite dans le nouveau mode (si on n'a pas encore répondu).
  const s = state.session;
  if (s && !$("#view-cards").hidden && s.items[s.index]?.kind === "card" && !s.revealed) showCard();
});
function mixQuestions() {
  try { return localStorage.getItem("pirouette.mixQuestions") !== "0"; } catch { return true; }
}

// Lance une séance. `params` : mode (today / weak / cards), course, folder, filter.
async function startSession(params, { title, back }) {
  const query = new URLSearchParams({ questions: mixQuestions() ? "1" : "0", ...params });
  if (params.mode === "cards") query.set("questions", "0");
  const data = await api(`/api/session?${query}`);
  if (!data.items.length) {
    alert(params.mode === "weak" ? "Aucune carte difficile pour l'instant : continue comme ça !"
      : params.mode === "today" ? "Rien à réviser aujourd'hui : tout est à jour." : "Aucune carte à réviser ici.");
    return;
  }
  runSession(data.items, { title, back, params });
}

function runSession(items, { title, back, params = null }) {
  foldJobs();
  state.session = { items, index: 0, flipped: false, results: [], title, back, params };
  $("#session-correction").hidden = true;
  $("#cards-title").textContent = title;
  setCrumbs(sessionCrumbs(back, title));
  $("#cards-done").hidden = true;
  setCardMode(cardMode());
  show("cards");
  showItem();
}

function showItem() {
  const s = state.session;
  const item = s.items[s.index];
  s.flipped = false;   // la carte montre sa réponse
  s.revealed = false;  // la réponse a été vue : on peut noter (et retourner la carte autant qu'on veut)
  s.checked = null;
  $("#cards-counter").textContent = `${s.index + 1} / ${s.items.length}`;
  $("#cards-bar").style.width = `${(s.index / s.items.length) * 100}%`;
  const several = new Set(s.items.map((i) => i.course_id)).size > 1;
  $("#session-origin").textContent = item.kind === "question"
    ? `Question de quiz · ${several ? `${item.course_name} · ` : ""}${item.quiz_title}` : several ? item.course_name : "";
  $("#card-stage").hidden = item.kind !== "card";
  $("#question-stage").hidden = item.kind !== "question";
  $("#session-mode").hidden = item.kind !== "card";
  if (item.kind === "card") showCard();
  else showSessionQuestion(item);
}

function showCard() {
  const s = state.session;
  const { card } = s.items[s.index];
  const typing = cardMode() === "type";
  $("#flashcard").classList.remove("flipped");
  dressCard(card, s);
  $("#card-front").textContent = card.front;
  $("#card-back").textContent = card.back;
  $("#card-back-question").textContent = card.front;
  $("#card-source").innerHTML = card.source ? `Dans ton cours : « ${escapeHtml(plainMd(card.source))} »` : "";
  $("#card-front-hint").textContent = typing ? "Écris ta réponse ci-dessous" : "Clique ou appuie sur Espace pour retourner";
  $("#typed-area").hidden = !typing;
  $("#typed-answer").value = "";
  $("#typed-answer").disabled = false;
  $("#typed-result").hidden = true;
  $("#ratings").hidden = true;
  $("#card-keys-text").textContent = typing ? "Entrée pour vérifier, puis 1, 2 ou 3 pour répondre"
    : "Espace pour retourner la carte, puis 1, 2 ou 3 pour répondre";
  (typing ? $("#typed-answer") : $("#flashcard")).focus();
}

// ---- La carte à collectionner : rareté (d'après ta progression), numéro, pile, tirage ----
function cardRarity(card) {
  if (card.status === "known" || (card.interval || 0) >= 21) return { cls: "holo", label: "★ Acquise" };
  if ((card.reviews || 0) > 0) return { cls: "rare", label: "◆◆ En cours" };
  return { cls: "", label: "◇ Nouvelle" };
}

function dressCard(card, s) {
  const el = $("#flashcard");
  const rarity = cardRarity(card);
  el.classList.remove("rare", "holo", "fly-again", "fly-hard", "fly-good");
  if (rarity.cls) el.classList.add(rarity.cls);
  const item = s.items[s.index];
  const set = card.scope?.[0] || item.course_name || "Flashcard";
  const pad = (n) => String(n).padStart(3, "0");
  document.querySelectorAll("[data-card-set]").forEach((e) => { e.textContent = set; });
  document.querySelectorAll("[data-card-rarity]").forEach((e) => { e.textContent = rarity.label; });
  document.querySelectorAll("[data-card-number]").forEach((e) => { e.textContent = `${pad(s.index + 1)}/${pad(s.items.length)}`; });
  const size = (text) => (text.length > 160 ? "very-long" : text.length > 70 ? "long" : "");
  document.querySelector(".flashcard-front .tcg-art").className = `tcg-art ${size(card.front)}`;
  document.querySelector(".flashcard-back .tcg-art").className = `tcg-art tcg-answer ${size(card.back)}`;
  // Cartes restantes dans la pile, derrière (au plus 3 dessinées)
  const left = s.items.slice(s.index + 1).filter((i) => i.kind === "card").length;
  $("#tcg-pile").dataset.left = String(Math.min(left, 3));
  el.classList.remove("dealing");
  void el.offsetWidth;  // relance l'animation de tirage
  el.classList.add("dealing");
}
$("#flashcard").addEventListener("animationend", (e) => {
  if (e.animationName === "tcg-deal") $("#flashcard").classList.remove("dealing");
});

// Relief : la carte s'incline sous la souris, avec un reflet (holographique pour les cartes acquises)
$("#flashcard").addEventListener("pointermove", (e) => {
  if (e.pointerType === "touch" || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const el = $("#flashcard");
  const box = el.getBoundingClientRect();
  const x = (e.clientX - box.left) / box.width;
  const y = (e.clientY - box.top) / box.height;
  el.classList.add("tilting");
  el.style.setProperty("--ry", `${(x - 0.5) * 16}deg`);
  el.style.setProperty("--rx", `${(0.5 - y) * 14}deg`);
  el.style.setProperty("--mx", `${x * 100}%`);
  el.style.setProperty("--my", `${y * 100}%`);
});
$("#flashcard").addEventListener("pointerleave", () => {
  const el = $("#flashcard");
  el.classList.remove("tilting");
  el.style.setProperty("--rx", "0deg");
  el.style.setProperty("--ry", "0deg");
});

function showCardToast(text) {
  const toast = $("#tcg-toast");
  toast.hidden = true;
  toast.textContent = text;
  void toast.offsetWidth;
  toast.hidden = false;
  clearTimeout(showCardToast.timer);
  showCardToast.timer = setTimeout(() => { toast.hidden = true; }, 1700);
}

// Retourne la carte ; un nouveau clic la remet côté question (et ainsi de suite).
function flipCard() {
  const s = state.session;
  if (!s || s.items[s.index]?.kind !== "card") return;
  if (cardMode() === "type" && !s.checked) return checkTyped();
  s.flipped = !s.flipped;
  $("#flashcard").classList.toggle("flipped", s.flipped);
  if (!s.revealed) {
    s.revealed = true;
    showRatings();
  }
}

function showRatings(suggested = null) {
  document.querySelectorAll("#ratings [data-rating]").forEach((b) => b.classList.toggle("suggested", b.dataset.rating === suggested));
  $("#ratings").hidden = false;
}

// ---- Mode « j'écris la réponse » : comparaison souple avec le verso ----
const FILLER_WORDS = new Set(("le la les l un une des du de d au aux a à en et ou est sont qui que qu ce ces cette se "
  + "sa son ses leur leurs par pour sur dans avec il elle ils elles on ne pas plus y the of and to in is").split(" "));

function answerWords(text) {
  return normalize(text).split(" ").filter((w) => w.length > 1 && !FILLER_WORDS.has(w));
}
const stem = (w) => w.slice(0, 6);

// Part des mots importants de la réponse attendue qu'on retrouve dans la réponse écrite (au début de mot près).
function compareAnswer(given, expected) {
  const said = new Set(answerWords(given).map(stem));
  const wanted = [...new Set(answerWords(expected))];
  if (!wanted.length) return { score: looselyEqual(given, expected) ? 1 : 0, found: new Set() };
  const found = new Set(wanted.filter((w) => said.has(stem(w))));
  return { score: found.size / wanted.length, found };
}

function highlightFound(expected, found) {
  const stems = new Set([...found].map(stem));
  return escapeHtml(expected).replace(/[\p{L}\p{N}]+/gu, (word) => {
    const key = normalize(word);
    return key.length > 1 && stems.has(stem(key)) ? `<mark>${word}</mark>` : word;
  });
}

function checkTyped() {
  const s = state.session;
  const given = $("#typed-answer").value.trim();
  if (!given) return $("#typed-answer").focus();
  const { card } = s.items[s.index];
  const { score, found } = compareAnswer(given, card.back);
  const verdict = score >= 0.7 ? "right" : score >= 0.4 ? "close" : "wrong";
  s.checked = { given, verdict };
  s.flipped = s.revealed = true;
  $("#typed-answer").disabled = true;
  $("#flashcard").classList.add("flipped");
  $("#card-back").innerHTML = highlightFound(card.back, found);
  const box = $("#typed-result");
  box.className = `typed-result ${verdict}`;
  box.innerHTML = `<strong>${{ right: "✓ C'est juste !", close: "≈ Presque", wrong: "✗ Pas tout à fait" }[verdict]}</strong>
    <span class="muted">${Math.round(score * 100)} % des mots importants de la réponse (surlignés sur la carte). À toi de juger :</span>`;
  box.hidden = false;
  showRatings({ right: "good", close: "hard", wrong: "again" }[verdict]);
  $("#ratings .suggested")?.focus();
}

$("#typed-check").addEventListener("click", checkTyped);
$("#typed-answer").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); e.stopPropagation(); checkTyped(); }
});

function rateCard(rating) {
  const s = state.session;
  const item = s?.items[s.index];
  if (!item || item.kind !== "card" || !s.revealed) return;
  if (s.leaving) return;
  const { card } = item;
  const before = cardRarity(card).cls;
  s.results.push({ kind: "card", item, rating, given: s.checked?.given || "" });
  api(`/api/courses/${item.course_id}/cards/${card.id}/review`, jsonBody("POST", { rating }))
    .then((updated) => {
      Object.assign(card, updated);
      const own = state.deck?.course_id === item.course_id && state.deck.cards.find((c) => c.id === card.id);
      if (own && own !== card) Object.assign(own, updated);
      if (before !== "holo" && cardRarity(card).cls === "holo") showCardToast("★ Carte acquise !");
    })
    .catch(() => {});
  // La carte part (à gauche : ratée, en bas : à moitié, à droite : sue), puis la suivante est tirée de la pile
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return nextItem();
  s.leaving = true;
  const el = $("#flashcard");
  el.classList.remove("tilting", "dealing");
  el.classList.add(`fly-${rating}`);
  setTimeout(() => {
    s.leaving = false;
    el.classList.remove(`fly-${rating}`);
    if (state.session === s) nextItem();
  }, 360);
}
$("#ratings").addEventListener("click", (e) => {
  const button = e.target.closest("[data-rating]");
  if (button) rateCard(button.dataset.rating);
});

function nextItem() {
  const s = state.session;
  s.index += 1;
  if (s.index < s.items.length) showItem();
  else finishSession();
}

// ---- Questions de quiz dans la séance ----
function showSessionQuestion(item) {
  const q = item.question;
  $("#sq-type").textContent = (TYPE_LABELS[q.type] || q.type) + (q.kind === "cours" ? " · Question de cours" : "");
  $("#sq-text").textContent = q.question;
  showFigure("#sq-figure", q, item.course_id);
  $("#sq-feedback").hidden = true;
  $("#sq-validate").hidden = false;
  $("#sq-next").hidden = true;
  const area = $("#sq-answers");
  if (q.type === "texte_a_trous") {
    $("#sq-text").innerHTML = clozeHtml(q, "sq-short");
    area.innerHTML = "";
    $("#sq-short").focus();
  } else if (!q.choices?.length) {
    area.innerHTML = `<textarea id="sq-short" rows="2" placeholder="Ta réponse…"></textarea>`;
    $("#sq-short").focus();
  } else {
    area.innerHTML = q.choices.map((c, i) => `
      <label class="choice"><input type="radio" name="sq-choice" value="${i}"><span>${escapeHtml(c)}</span></label>`).join("");
  }
}

function recordSessionAnswer(item, given, correct) {
  state.session.results.push({ kind: "question", item, given, correct });
  api(`/api/quizzes/${item.quiz_id}/answers`, jsonBody("POST", { answers: [{ index: item.index, correct }] })).catch(() => {});
}

const SQ_DELETE_ITEM = `<button type="button" class="danger" data-sq-delete><strong>Supprimer cette question</strong><small>Hors sujet : elle ne sera plus posée</small></button>`;
const SQ_DELETE = `<div class="question-tools end">${qtoolsHtml([SQ_DELETE_ITEM])}</div>`;

$("#sq-feedback").addEventListener("click", async (e) => {
  if (!e.target.closest("[data-sq-delete]")) return;
  const s = state.session;
  const item = s.items[s.index];
  if (!(await deleteQuestion(item.quiz_id, item.index))) return;
  // Sa réponse ne compte pas ; les questions suivantes du même quiz remontent d'un rang.
  if (s.results.at(-1)?.item === item) s.results.pop();
  s.items.forEach((other) => { if (other.kind === "question" && other.quiz_id === item.quiz_id && other.index > item.index) other.index -= 1; });
  nextItem();
});

function sessionFeedback(q, correct, verdict = "") {
  const fb = $("#sq-feedback");
  fb.className = `feedback ${correct ? "ok" : "ko"}`;
  fb.innerHTML = `${verdict}${q.explanation ? `<p>${escapeHtml(cleanExplanation(q.explanation))}</p>` : ""}${sourceHtml(q)}${keyTermsHtml(q)}${SQ_DELETE}`;
  fb.hidden = false;
  $("#sq-validate").hidden = true;
  $("#sq-next").hidden = false;
  $("#sq-next").focus();
}

function validateSessionQuestion() {
  const s = state.session;
  const item = s.items[s.index];
  const q = item.question;
  if (!q.choices?.length) {
    const input = $("#sq-short");
    const given = input.value.trim();
    if (!given) return;
    input.disabled = true;
    if (typedIsRight(given, q.answer)) {
      recordSessionAnswer(item, given, true);
      return sessionFeedback(q, true, `<p><strong>Bonne réponse !</strong></p>`);
    }
    const fb = $("#sq-feedback");
    fb.className = "feedback neutral";
    fb.innerHTML = `<p><strong>Réponse attendue :</strong> ${escapeHtml(q.answer)}</p>
      ${q.explanation ? `<p>${escapeHtml(cleanExplanation(q.explanation))}</p>` : ""}${sourceHtml(q)}
      <div class="actions"><button class="primary" id="sq-right">J'avais bon</button><button class="ghost" id="sq-wrong">J'avais faux</button></div>${SQ_DELETE}`;
    fb.hidden = false;
    $("#sq-validate").hidden = true;
    const grade = (correct) => { recordSessionAnswer(item, given, correct); nextItem(); };
    $("#sq-right").onclick = () => grade(true);
    $("#sq-wrong").onclick = () => grade(false);
    return;
  }
  const picked = document.querySelector("input[name=sq-choice]:checked");
  if (!picked) return;
  const given = q.choices[Number(picked.value)];
  const correct = given === q.answer;
  document.querySelectorAll("#sq-answers .choice").forEach((label, i) => {
    label.querySelector("input").disabled = true;
    if (q.choices[i] === q.answer) label.classList.add("right");
    else if (i === Number(picked.value)) label.classList.add("wrong");
  });
  recordSessionAnswer(item, given, correct);
  sessionFeedback(q, correct);
}
$("#sq-validate").addEventListener("click", validateSessionQuestion);
$("#sq-next").addEventListener("click", nextItem);

const RATING_NAMES = { again: "Je ne savais pas", hard: "À moitié", good: "Je savais", easy: "Je savais" };
const isMistake = (r) => (r.kind === "card" ? r.rating !== "good" && r.rating !== "easy" : !r.correct);

function finishSession() {
  const s = state.session;
  const cards = s.results.filter((r) => r.kind === "card");
  const questions = s.results.filter((r) => r.kind === "question");
  const count = (rating) => cards.filter((r) => r.rating === rating).length;
  const good = questions.filter((r) => r.correct).length;
  const parts = [];
  if (cards.length) {
    parts.push([`<span class="ok-text">${plural(count("good") + count("easy"), "carte sue", "cartes sues")}</span>`,
      count("hard") ? `${count("hard")} à moitié` : "",
      count("again") ? `<span class="ko-text">${plural(count("again"), "pas sue", "pas sues")}</span>` : ""].filter(Boolean).join(" · "));
  }
  if (questions.length) parts.push(`${plural(good, "bonne réponse", "bonnes réponses")} sur ${questions.length}`);
  $("#cards-bar").style.width = "100%";
  $("#cards-summary").innerHTML = parts.join("<br>");
  $("#session-correction-btn").hidden = !s.results.length;
  $("#card-stage").hidden = $("#question-stage").hidden = $("#session-mode").hidden = true;
  $("#session-correction").hidden = true;
  $("#session-origin").textContent = "";
  $("#cards-done").hidden = false;
}

// Correction de la séance : tout, ou seulement les erreurs (cartes pas ou à moitié sues, questions ratées).
function showSessionCorrection(filter = "all") {
  const s = state.session;
  s.correctionFilter = filter;
  document.querySelectorAll("[data-correction]").forEach((b) => b.classList.toggle("active", b.dataset.correction === filter));
  const shown = s.results.filter((r) => filter === "all" || isMistake(r));
  $("#session-review").innerHTML = shown.length ? shown.map((r) => {
    const ok = !isMistake(r);
    if (r.kind === "card") {
      const { card } = r.item;
      return `<li class="${ok ? "ok" : r.rating === "hard" ? "half" : "ko"}">
        <p class="q">${escapeHtml(card.front)}</p>
        ${r.given ? `<p class="${ok ? "given-ok" : "given-ko"}">Ta réponse : ${escapeHtml(r.given)}</p>` : ""}
        <p>Réponse : <strong>${escapeHtml(card.back)}</strong></p>
        <p class="muted small-text">${RATING_NAMES[r.rating]}</p>
        <p class="question-tools">${explainButton({ course_id: r.item.course_id, question: card.front, expected: card.back, given: r.given, source: card.source })}</p>
      </li>`;
    }
    const q = r.item.question;
    return `<li class="${ok ? "ok" : "ko"}">
      <p class="q">${escapeHtml(q.question)}</p>
      <p class="${ok ? "given-ok" : "given-ko"}">${ok ? "✓" : "✗"} Ta réponse : ${escapeHtml(r.given || "")}</p>
      ${ok ? "" : `<p>Bonne réponse : <strong>${escapeHtml(q.answer)}</strong></p>`}
      ${q.explanation ? `<p class="muted">${escapeHtml(cleanExplanation(q.explanation))}</p>` : ""}
      ${sourceHtml(q)}
      <p class="question-tools">${explainButton({ course_id: r.item.course_id, question: q.question, expected: q.answer, given: r.given, source: q.source })}</p>
    </li>`;
  }).join("") : `<li class="empty muted">Aucune erreur : tout était juste.</li>`;
  $("#session-redo").hidden = !s.results.some(isMistake);
  $("#cards-done").hidden = true;
  $("#session-correction").hidden = false;
  window.scrollTo(0, 0);
}

$("#session-correction-btn").addEventListener("click", () => showSessionCorrection("all"));
document.querySelectorAll("[data-correction]").forEach((b) => b.addEventListener("click", () => showSessionCorrection(b.dataset.correction)));
$("#session-redo").addEventListener("click", () => {
  const s = state.session;
  const items = s.results.filter(isMistake).map((r) => r.item);
  runSession(shuffle(items), { title: `${s.title} · mes erreurs`, back: s.back });
});
$("#session-correction-done").addEventListener("click", (e) => { state.sessionBack = state.session?.back; leaveSession(e); });

const leaveSession = (e) => {
  e?.preventDefault();
  state.session = null;
  state.deck = null;  // les cartes ont changé : relues depuis le serveur
  go(state.sessionBack || "#/reviser");
};
// Fil d'Ariane d'une séance : depuis un cours (Mes cours › Cours › Flashcards › …), l'accueil ou Réviser.
function sessionCrumbs(back, title) {
  const inCourse = back.match(/^#\/cours\/([0-9a-f]{12})(?:\/([a-z]+))?/);
  if (inCourse && state.course?.id === inCourse[1]) {
    return courseCrumbs(state.course, TABS.includes(inCourse[2]) ? inCourse[2] : null, title);
  }
  if (back.startsWith("#/reviser/") && state.reviewScope) {
    return [{ label: "Réviser", href: "#/reviser" }, { label: state.reviewScope.name, href: back }, { label: title }];
  }
  return [back === "#/" ? { label: "Accueil", href: "#/" } : { label: "Réviser", href: "#/reviser" }, { label: title }];
}
$("#session-done-back").addEventListener("click", (e) => { state.sessionBack = state.session?.back; leaveSession(e); });

$("#flashcard").addEventListener("click", () => {
  if (!window.getSelection().toString()) flipCard();
});
$("#card-edit-current").addEventListener("click", () => {
  const s = state.session;
  const item = s.items[s.index];
  state.course = state.course?.id === item.course_id ? state.course : { id: item.course_id, cards: { total: 0, known: 0, review: 0 } };
  openCardDialog(item.card, { onSaved: () => {
    $("#card-front").textContent = $("#card-back-question").textContent = item.card.front;
    $("#card-back").textContent = item.card.back;
  } });
});

// Une carte inutile (hors sujet, simple info d'organisation du cours…) : on la supprime sans quitter la séance.
$("#card-delete-current").addEventListener("click", async () => {
  const s = state.session;
  const item = s.items[s.index];
  if (!confirm("Supprimer cette carte ? Elle disparaît du cours et de tes révisions.")) return;
  try {
    await api(`/api/courses/${item.course_id}/cards/${item.card.id}`, { method: "DELETE" });
  } catch (err) {
    return alert(err.message);
  }
  state.deck = null;
  s.items.splice(s.index, 1);
  if (s.index < s.items.length) showItem();
  else if (s.results.length) finishSession();
  else { state.sessionBack = s.back; leaveSession(); }
});

// Le cours sert à ranger et fabriquer ; on s'entraîne dans Réviser (un lien y mène).
function renderCourseRevise(course) {
  const { today } = course.revision;
  $("#course-revise").hidden = !course.cards.total && !course.quizzes.length;
  // Vers l'espace Réviser : celui du semestre du cours (plan, rétroplanning, suivi), sinon celui du cours
  const folder = state.folders?.find((f) => f.id === course.folder_id);
  $("#course-revise-text").innerHTML = today ? `À réviser aujourd'hui : <b>${today}</b> carte${today > 1 ? "s" : ""}` : "Rien à réviser aujourd'hui : tout est à jour.";
  $("#course-revise-link").href = folder ? `#/reviser/dossier/${folder.id}` : `#/reviser/cours/${course.id}`;
  $("#course-revise-link").textContent = folder ? `Réviser ${folder.name} →` : "Réviser →";
}

// ---------- Réviser : révision du jour, points faibles et suivi ----------
// Page Réviser : d'abord quoi réviser (tout, un dossier, un cours)…
async function openReview() {
  state.course = null;
  state.newCards = null;
  const [data, folders] = await Promise.all([api("/api/progress"), api("/api/folders")]);
  const courses = data.courses;
  // Même grille que Mes cours, mais chaque carte dit où tu en es : maîtrise (cartes sues) et ce qui est à revoir.
  const card = (c) => {
    const mastery = c.cards ? Math.round((100 * c.known) / c.cards) : 0;
    const status = c.today ? `<b>${c.today}</b> à revoir aujourd'hui` : c.cards ? "À jour" : "Pas encore de cartes";
    return `
    <a class="course-card revise-course" href="#/reviser/cours/${c.id}">
      <span class="course-main">
        <strong>${escapeHtml(c.name)}</strong>
        <small>${status}${c.weak ? ` · ${plural(c.weak, "point faible", "points faibles")}` : ""}</small>
      </span>
      <span class="revise-mastery" title="${c.cards ? `${c.known} cartes sues sur ${c.cards}` : "Pas encore de cartes"}">${c.cards ? `${mastery} %<small>maîtrise</small>` : ""}</span>
      <span class="revise-band" style="--m:${mastery}%" aria-hidden="true"></span>
    </a>`;
  };
  const group = (folder, list) => {
    if (!list.length) return "";
    const today = list.reduce((n, c) => n + c.today, 0);
    const exam = folder?.exam_week && !folder.archived ? ` · ${examLabel(folder.exam_week)}` : "";
    return `
    <details class="folder revise-folder" data-folder="${folder?.id || ""}" ${!folder || folderOpen(folder) ? "open" : ""}>
      <summary class="folder-head">
        ${FOLDER_ICON}<strong>${escapeHtml(folder ? folder.name : "Sans semestre")}</strong>
        <small class="muted">${plural(list.length, "cours", "cours")}${exam}${today ? ` · ${today} à revoir` : ""}</small>
        ${folder && list.length > 1 ? `<a class="button primary small folder-revise" href="#/reviser/dossier/${folder.id}">Réviser le semestre</a>` : ""}
      </summary>
      <div class="course-grid">${list.map(card).join("")}</div>
    </details>`;
  };
  const known = new Set(folders.map((f) => f.id));
  const archived = folders.filter((f) => f.archived);
  $("#review-pick").innerHTML = !courses.length
    ? `<p class="empty-state muted">Pas encore de cours à réviser : crée un cours dans « Mes cours ».</p>`
    : folders.filter((f) => !f.archived).map((f) => group(f, courses.filter((c) => c.folder_id === f.id))).join("")
      + group(null, courses.filter((c) => !known.has(c.folder_id)))
      + (archived.length ? `<details class="pick-archived"><summary>Archivés</summary>${
        archived.map((f) => group(f, courses.filter((c) => c.folder_id === f.id))).join("")}</details>` : "");
  setCrumbs();
  show("review");
}
// Un semestre replié ou ouvert dans Réviser l'est aussi dans Mes cours (même préférence)
$("#review-pick").addEventListener("toggle", (e) => {
  const folder = e.target.closest?.("details.revise-folder");
  if (folder?.dataset.folder) rememberFolder(folder.dataset.folder, folder.open);
}, true);

// … puis, pour ce choix, trois onglets : Réviser (plan, séance, quiz, partiel), Rétroplanning et Suivi.
const SCOPE_TABS = { "": "Réviser", planning: "Rétroplanning", suivi: "Suivi" };

async function openReviewScope(kind, id = "", sub = "") {
  if (kind === "cours" && sub === "partiel") return openPartielPrep(id);
  state.course = null;
  const folders = await api("/api/folders");
  let params = {}, name = "Tous mes cours", owner = null, parent = null;
  if (kind === "dossier") {
    params = { folder: id };
    name = folders.find((f) => f.id === id)?.name || "Semestre";
    owner = { folder: id };
  } else if (kind === "cours") {
    params = { course: id };
    const course = await api(`/api/courses/${id}`);
    name = course.name;
    parent = folders.find((f) => f.id === course.folder_id) || null;
    owner = parent ? null : { course: id };  // un cours rangé dans un semestre suit le plan du semestre
  }
  const base = `#/reviser/${kind}${id ? `/${id}` : ""}`;
  const tab = sub in SCOPE_TABS ? sub : "";
  state.reviewScope = { params, name, hash: location.hash, base, single: kind === "cours", owner, parent };
  $("#review-scope-title").textContent = name;
  document.querySelectorAll("[data-scope-tab]").forEach((a) => {
    const t = a.dataset.scopeTab;
    a.href = base + (t ? `/${t}` : "");
    a.classList.toggle("active", t === tab);
    a.setAttribute("aria-selected", String(t === tab));
  });
  $("#scope-tabs").hidden = !owner && !parent;  // « Tous mes cours » : pas de plan
  $("#scope-main").hidden = tab !== "";
  $("#scope-planning").hidden = tab !== "planning";
  $("#scope-suivi").hidden = tab !== "suivi";
  setCardMode(cardMode());
  $("#mix-questions").checked = mixQuestions();
  setCrumbs([{ label: "Réviser", href: "#/reviser" }, ...(tab ? [{ label: name, href: base }, { label: SCOPE_TABS[tab] }] : [{ label: name }])]);
  show("review-scope");
  if (tab === "planning") await renderRetro();
  else if (tab === "suivi") await renderProgress();
  else await renderScopeMain();
}

const reviewScope = () => state.reviewScope?.params || {};
const ownerQuery = () => new URLSearchParams(state.reviewScope?.owner || {});
const RHYTHM_TEXT = { 1: "une séance par jour", 2: "une séance tous les 2 jours", 3: "une séance tous les 3 jours", 7: "une séance par semaine" };
const dayShort = (iso) => new Date(`${iso}T00:00`).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
function dayWhen(iso) {
  const days = daysUntil(iso);
  return days === 0 ? "aujourd'hui" : days === 1 ? "demain" : dayShort(iso);
}

// ---- Onglet Réviser : plan (ou révision du jour), là où l'on bloque, quiz, partiel ----
async function renderScopeMain() {
  const scope = state.reviewScope;
  const [data, quizzes, plan] = await Promise.all([
    api(`/api/progress?${new URLSearchParams(reviewScope())}`),
    api("/api/quizzes"),
    scope.owner ? api(`/api/plan?${ownerQuery()}`) : null,
  ]);
  const scoped = data.courses.filter((c) => (reviewScope().course ? c.id === reviewScope().course
    : reviewScope().folder ? c.folder_id === reviewScope().folder : true));
  const ids = new Set(scoped.map((c) => c.id));
  state.scopePlan = plan;
  $("#review-exam").hidden = !data.exam || data.exam.days <= -7;
  if (data.exam) $("#review-exam").innerHTML = examSentence(data.exam);

  // Plan de révision
  $("#plan-card").hidden = !plan;
  $("#today-card").hidden = Boolean(plan?.plan);
  if (plan) renderPlan(plan);
  const { today } = data;
  $("#today-summary").innerHTML = today.cards || today.questions
    ? `<b>${today.cards}</b> carte${today.cards > 1 ? "s" : ""}${today.questions ? ` · <b>${today.questions}</b> question${today.questions > 1 ? "s" : ""} de quiz` : ""}`
    : "Tout est à jour";
  $("#start-today").disabled = !today.cards && !(today.questions && mixQuestions());
  $("#today-note").innerHTML = scope.parent
    ? `Le plan de révision se règle sur le semestre : <a href="#/reviser/dossier/${scope.parent.id}">${escapeHtml(scope.parent.name)} →</a>`
    : "Les cartes reviennent au bon moment, avec quelques questions de tes quiz.";

  // Là où tu bloques
  const hard = data.hard;
  $("#hard-card").hidden = !hard.length;
  $("#hard-count").textContent = hard.length ? `· ${plural(hard.length, "carte", "cartes")}` : "";
  $("#hard-list").innerHTML = hard.map((h) => `<li><span>${escapeHtml(h.front)}</span>
    <small class="muted">${h.lapses ? `oubliée ${plural(h.lapses, "fois", "fois")}` : "ratée la dernière fois"}${
      scope.single ? "" : ` · ${escapeHtml(h.course)}`}</small></li>`).join("");

  // Par chapitre (quiz, cartes, tout mélangé) ; les quiz qui ne tiennent pas à un chapitre, à part.
  const byChapter = await renderMastery();
  const placed = new Set(byChapter.flatMap((c) => c.chapters.flatMap((ch) => ch.quizzes.map((q) => q.id))));
  const names = Object.fromEntries(scoped.map((c) => [c.id, c.name]));
  // Mode « Quiz » : tous les quiz du cours ou du semestre (ceux d'un chapitre d'abord)
  const all = quizzes.filter((q) => ids.has(q.course_id))
    .sort((a, b) => Number(!placed.has(a.id)) - Number(!placed.has(b.id)))
    .map((q) => ({ ...q, course_name: scope.single ? "" : names[q.course_id] }));
  $("#scope-quizzes").innerHTML = all.length ? all.map((q) => quizItem(q, null, null, { play: true })).join("")
    : `<li class="empty muted">Pas encore de quiz : crée-les depuis la page du cours (Mes cours).</li>`;
  $("#mode-quiz-note").textContent = plural(all.length, "quiz", "quiz");
  $("#mode-jour-note").textContent = today.cards || today.questions ? `${today.cards + today.questions} à revoir` : "à jour";
  $("#mode-chapitres-note").textContent = plural(byChapter.reduce((n, c) => n + c.chapters.length, 0), "chapitre", "chapitres");

  // Mode partiel : sur un cours. Depuis un semestre, on choisit le cours.
  const single = Boolean(scope.single);
  $("#partiel-pick").hidden = single;
  if (!single) {
    const pick = $("#partiel-course");
    const previous = pick.value;
    pick.innerHTML = scoped.map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
    if (scoped.some((c) => c.id === previous)) pick.value = previous;
  }
  $("#start-partiel").disabled = !single && !scoped.length;
}

const MASTERY_NAMES = { acquis: "Acquis", en_cours: "En cours", fragile: "Fragile", a_voir: "À voir" };
async function renderMastery() {
  const data = await api(`/api/mastery?${new URLSearchParams(reviewScope())}`).catch(() => []);
  const withChapters = data.filter((c) => c.chapters.length);
  $("#mastery-card").hidden = false;
  const several = withChapters.length > 1;
  state.chapterRows = {};
  $("#mastery-list").innerHTML = !withChapters.length ? `<p class="muted">Pas encore de chapitres : importe ton cours depuis Mes cours.</p>` : withChapters.map((c) => `
    ${several ? `<h3>${escapeHtml(c.course)}</h3>` : ""}
    <ul class="mastery-rows">${c.chapters.map((ch, i) => {
      // Plusieurs fichiers (« PARTIE 2 » dans chacun) : le nom du fichier sépare les groupes.
      const files = new Set(c.chapters.map((x) => x.file));
      const header = files.size > 1 && (i === 0 || c.chapters[i - 1].file !== ch.file)
        ? `<li class="mastery-file">${escapeHtml(ch.file.replace(/\.[^.]+$/, ""))}</li>` : "";
      const id = `${c.course_id}:${ch.key}`;
      state.chapterRows[id] = { ...ch, course_id: c.course_id, course: c.course };
      const detail = [ch.cards ? `${ch.solid}/${ch.cards} cartes ancrées` : "", ch.weak ? `${ch.weak} difficile${ch.weak > 1 ? "s" : ""}` : "",
        ch.quiz !== null ? `meilleur score ${ch.quiz} %` : ""].filter(Boolean).join(" · ");
      return `${header}<li class="chapter-row" title="${escapeHtml(detail || "Pas encore révisé")}">
        <span class="mastery-title">${escapeHtml(ch.single ? c.course : ch.title)}</span>
        <span class="mastery-bar"><span class="lvl ${ch.level}" style="width:${Math.max(ch.score, ch.level === "a_voir" ? 0 : 6)}%"></span></span>
        <span class="mastery-level ${ch.level}">${MASTERY_NAMES[ch.level]}</span>
        <span class="chapter-actions">
          <button class="ghost small" type="button" data-chapter-quiz="${escapeHtml(id)}" ${ch.questions ? "" : "disabled"}>Quiz${ch.questions ? ` (${ch.questions})` : ""}</button>
          <button class="ghost small" type="button" data-chapter-cards="${escapeHtml(id)}" ${ch.cards ? "" : "disabled"}>Cartes${ch.cards ? ` (${ch.cards})` : ""}</button>
          <button class="primary small" type="button" data-chapter-mix="${escapeHtml(id)}" ${ch.cards || ch.questions ? "" : "disabled"}>Tout</button>
        </span></li>`;
    }).join("")}</ul>`).join("");
  return data;
}

// Réviser un chapitre : son quiz (s'il y en a plusieurs, on choisit), ses cartes, ou tout mélangé.
$("#mastery-list").addEventListener("click", async (e) => {
  const button = e.target.closest("[data-chapter-quiz], [data-chapter-cards], [data-chapter-mix]");
  if (!button) return;
  const { chapterQuiz, chapterCards, chapterMix } = button.dataset;
  const ch = state.chapterRows[chapterQuiz || chapterCards || chapterMix];
  const title = ch.single ? ch.course : ch.title;
  if (chapterQuiz) {
    let quiz = ch.quizzes[0];
    if (ch.quizzes.length > 1) {
      const pick = await chooseQuiz(ch.quizzes);
      if (!pick) return;
      quiz = pick;
    }
    return startQuiz(await api(`/api/quizzes/${quiz.id}`), undefined, { back: state.reviewScope.hash });
  }
  startSession({ mode: "chapter", course: ch.course_id, chapter: ch.title, questions: chapterMix ? "1" : "0" },
    { title: `${title}${chapterMix ? "" : " · cartes"}`, back: state.reviewScope.hash });
});
function chooseQuiz(quizzes) {
  return new Promise((resolve) => {
    const dialog = $("#choose-quiz-dialog");
    $("#choose-quiz-list").innerHTML = quizzes.map((q, i) => `<button type="button" class="choose-quiz" data-i="${i}">
      <strong>${escapeHtml(q.title)}</strong><small class="muted">${q.count} questions${q.best !== null ? ` · meilleur score ${q.best} %` : ""}</small></button>`).join("");
    dialog.onclick = (e) => {
      const b = e.target.closest("[data-i]");
      if (b || e.target.closest("#choose-quiz-cancel")) { dialog.close(); resolve(b ? quizzes[b.dataset.i] : null); }
    };
    dialog.showModal();
  });
}

function renderPlan(data) {
  const { plan, status } = data;
  $("#plan-setup").hidden = Boolean(plan);
  $("#plan-view").hidden = !plan;
  $("#plan-cancel").hidden = true;
  if (!plan) {
    $("#plan-save").textContent = "Lancer mon plan";
    return;
  }
  $("#plan-rhythm").textContent = `${RHYTHM_TEXT[plan.every]} · ${plan.minutes} min`;
  $("#plan-next").innerHTML = status.done_today
    ? `Séance faite. Prochaine séance : <b>${dayWhen(status.next)}</b>.`
    : plan.every > 1 && status.deadline !== status.next
      ? `<b>Séance à faire</b>, d'ici ${dayWhen(status.deadline)} · ${plural(data.session.cards, "carte", "cartes")} environ.`
      : `<b>Séance à faire aujourd'hui</b> · ${plural(data.session.cards, "carte", "cartes")} environ.`;
  $("#plan-regular").textContent = (status.total ? `${status.kept} séance${status.kept > 1 ? "s" : ""} tenue${status.kept > 1 ? "s" : ""} sur ${status.total}. ` : "")
    + status.message;
  $("#plan-start").textContent = status.done_today ? "Refaire une séance" : "Commencer la séance";
  $("#plan-start").className = status.done_today ? "ghost" : "primary";
}

$("#plan-save").addEventListener("click", async () => {
  const data = await api("/api/plan", jsonBody("PUT", {
    ...state.reviewScope.owner, every: Number($("#plan-every").value), minutes: Number($("#plan-minutes").value) }));
  state.scopePlan = data;
  renderPlan(data);
  $("#today-card").hidden = true;
});
$("#plan-edit").addEventListener("click", () => {
  const { plan } = state.scopePlan;
  $("#plan-every").value = plan.every;
  $("#plan-minutes").value = plan.minutes;
  $("#plan-setup").hidden = false;
  $("#plan-view").hidden = true;
  $("#plan-save").textContent = "Enregistrer";
  $("#plan-cancel").hidden = false;
});
$("#plan-cancel").addEventListener("click", () => renderPlan(state.scopePlan));
$("#plan-start").addEventListener("click", () => startSession(
  { mode: "plan", ...reviewScope(), minutes: state.scopePlan.plan.minutes },
  { title: "Séance du plan", back: state.reviewScope.hash }));

// ---- Onglet Rétroplanning ----
const RETRO_KINDS = { learn: "Chapitres", review: "Révision", consolidate: "Consolidation", blanc: "Partiel blanc" };

async function renderRetro() {
  const scope = state.reviewScope;
  $("#retro-elsewhere").hidden = Boolean(scope.owner);
  $("#retro-setup").hidden = $("#retro-view").hidden = true;
  if (!scope.owner) {
    $("#retro-elsewhere").innerHTML = `Le rétroplanning se construit sur le semestre de ce cours :
      <a href="#/reviser/dossier/${scope.parent.id}/planning">${escapeHtml(scope.parent.name)} →</a>`;
    return;
  }
  const [data, plan] = await Promise.all([api(`/api/retro?${ownerQuery()}`), api(`/api/plan?${ownerQuery()}`)]);
  state.retro = data;
  if (plan.plan) $("#retro-every").value = plan.plan.every;
  $("#review-exam").hidden = true;
  if (!data.retro) {
    $("#retro-setup").hidden = false;
    $("#retro-error").hidden = true;
    const future = data.exam && daysUntil(data.exam) > 0;
    $("#retro-exam").value = future ? data.exam : "";
    $("#retro-intro").textContent = future
      ? `Partiels le ${dayLong(data.exam)}, dans ${plural(daysUntil(data.exam), "jour", "jours")}. Pirouette répartit tes chapitres sur les séances, garde du temps pour consolider, puis finit par des partiels blancs.`
      : "Indique quand commence ta semaine de partiels : Pirouette répartit tes chapitres sur les séances d'ici là, garde du temps pour consolider, puis finit par des partiels blancs.";
    return;
  }
  const { retro } = data;
  $("#retro-view").hidden = false;
  const left = retro.sessions.filter((s) => !s.done && !s.past).length;
  $("#retro-summary").innerHTML = `<b>${examLabel(retro.exam)}</b> · ${RHYTHM_TEXT[retro.every]} · ${plural(left, "séance restante", "séances restantes")}`;
  $("#retro-list").innerHTML = retro.sessions.map((s) => retroItem(s)).join("");
}

function retroItem(s) {
  const when = s.today ? "Aujourd'hui" : daysUntil(s.date) === 1 ? "Demain" : dayShort(s.date);
  let body = "";
  if (s.kind === "learn") {
    body = s.units.map((u) => `<div class="retro-unit"><span>${escapeHtml(u.title)}${
      state.reviewScope.single ? "" : ` <small class="muted">· ${escapeHtml(u.course)}</small>`}</span>
      <span class="retro-actions">${u.quiz_id
        ? `<button class="${u.quiz_done ? "ghost" : "primary"} small" data-open-quiz="${u.quiz_id}">${u.quiz_done ? "Refaire le quiz" : "Quiz"}</button>`
        : `<button class="ghost small" data-retro-make="${u.course_id}" data-key="${u.key}" data-title="${escapeHtml(u.title)}">Créer le quiz</button>`}
        <button class="ghost small" data-retro-cards="${u.course_id}" data-title="${escapeHtml(u.title)}">Cartes</button></span></div>`).join("");
  } else if (s.kind === "blanc") {
    body = `<div class="retro-unit"><span>${escapeHtml(s.course)}</span><span class="retro-actions">
      <a class="button ghost small" href="#/reviser/cours/${s.course_id}/partiel">Préparer le partiel</a></span></div>`;
  } else {
    body = `<div class="retro-unit"><span class="muted">${s.kind === "consolidate"
      ? "Tes erreurs et les cartes où tu bloques." : "Les cartes du jour et quelques questions de tes quiz."}</span>
      <span class="retro-actions"><button class="ghost small" data-retro-start="${s.kind}">Commencer</button></span></div>`;
  }
  return `<li class="retro-item${s.done ? " done" : ""}${s.today ? " today" : ""}${s.past && !s.done ? " missed" : ""}">
    <label class="retro-check" title="Séance faite"><input type="checkbox" data-retro-check="${s.date}" ${s.done ? "checked" : ""}></label>
    <div class="retro-body"><div class="retro-when"><strong>${when}</strong><span class="muted">${RETRO_KINDS[s.kind]}</span></div>${body}</div>
  </li>`;
}

$("#retro-build").addEventListener("click", () => buildRetro(true));
$("#retro-rebuild").addEventListener("click", () => {
  if (confirm("Recalculer le rétroplanning à partir d'aujourd'hui ? Les séances faites sont gardées.")) buildRetro(false);
});
async function buildRetro(withDate) {
  try {
    await api("/api/retro", jsonBody("POST", { ...state.reviewScope.owner, every: Number($("#retro-every").value),
      exam: withDate ? $("#retro-exam").value : "" }));
  } catch (err) {
    return showError("#retro-error", err.message);
  }
  renderRetro();
}
$("#retro-delete").addEventListener("click", async () => {
  if (!confirm("Supprimer le rétroplanning ?")) return;
  await api(`/api/retro?${ownerQuery()}`, { method: "DELETE" });
  renderRetro();
});
$("#retro-list").addEventListener("change", async (e) => {
  const box = e.target.closest("[data-retro-check]");
  if (!box) return;
  await api("/api/retro/session", jsonBody("PUT", { ...state.reviewScope.owner, date: box.dataset.retroCheck, done: box.checked }));
  renderRetro();
});
$("#retro-list").addEventListener("click", async (e) => {
  const cards = e.target.closest("[data-retro-cards]");
  if (cards) {
    return startSession({ mode: "chapter", course: cards.dataset.retroCards, chapter: cards.dataset.title, questions: "0" },
      { title: cards.dataset.title, back: state.reviewScope.hash });
  }
  const start = e.target.closest("[data-retro-start]");
  if (start) {
    const mode = start.dataset.retroStart === "consolidate" ? "weak" : "today";
    return startSession({ mode, ...reviewScope() },
      { title: mode === "weak" ? "Consolidation" : "Révision", back: state.reviewScope.hash });
  }
  const make = e.target.closest("[data-retro-make]");
  if (make) {
    make.disabled = true;
    const form = new FormData();
    form.append("provider", "local");
    form.append("num_questions", "0");  // tout le chapitre
    form.append("chapters", make.dataset.key);
    try {
      await api(`/api/courses/${make.dataset.retroMake}/quizzes/background`, { method: "POST", body: form });
      make.textContent = "Quiz en préparation…";
      jobsState.keepOpen = true;
      refreshJobs();
    } catch (err) {
      make.disabled = false;
      alert(err.message);
    }
  }
});

// Calendrier : une colonne par semaine (lundi en haut), plus foncé = plus de révisions.
function heatmapHtml(days) {
  const first = new Date(days[0].date);
  const pad = (first.getDay() + 6) % 7;
  const max = Math.max(1, ...days.map((d) => d.cards + d.questions));
  const level = (n) => (!n ? 0 : Math.min(4, Math.ceil((4 * n) / max)));
  return Array(pad).fill(`<span class="heat off"></span>`).join("") + days.map((d) => {
    const n = d.cards + d.questions;
    const label = `${new Date(d.date).toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" })} : `
      + (n ? `${plural(d.cards, "carte", "cartes")}, ${plural(d.questions, "question", "questions")}` : "rien");
    return `<span class="heat l${level(n)}" title="${label}"></span>`;
  }).join("");
}

// Accueil : les jours où tu as révisé (12 dernières semaines), discret, sous le bouton
async function renderHomeHeat(guiding) {
  const box = $("#home-heat");
  const data = guiding ? null : await api("/api/progress").catch(() => null);
  box.hidden = !data?.active_days;
  if (box.hidden) return;
  $("#home-heat-note").textContent = `· ${plural(data.active_days, "jour", "jours")} en 12 semaines`;
  $("#home-heatmap").innerHTML = heatmapHtml(data.days);
}

// Réviser : quatre modes (du jour, par chapitre, quiz, partiel) ; le dernier choisi est gardé
function setScopeMode(mode) {
  $("#scope-main").dataset.mode = mode;
  document.querySelectorAll("[data-scope-mode]").forEach((b) => {
    b.classList.toggle("active", b.dataset.scopeMode === mode);
    b.setAttribute("aria-selected", String(b.dataset.scopeMode === mode));
  });
  try { localStorage.setItem("pirouette.scopeMode", mode); } catch {}
}
$("#scope-modes").addEventListener("click", (e) => {
  const button = e.target.closest("[data-scope-mode]");
  if (button) setScopeMode(button.dataset.scopeMode);
});
try { setScopeMode(localStorage.getItem("pirouette.scopeMode") || "jour"); } catch { setScopeMode("jour"); }

// ---- Onglet Suivi ----
async function renderProgress() {
  const data = await api(`/api/progress?${new URLSearchParams(reviewScope())}`);
  const { week } = data;
  const pctText = (v) => (v === null ? "—" : `${v} %`);
  $("#review-exam").hidden = !data.exam || data.exam.days <= -7;
  if (data.exam) $("#review-exam").innerHTML = examSentence(data.exam);
  $("#pg-cards").textContent = week.cards;
  $("#pg-cards-success").textContent = pctText(week.cards_success);
  $("#pg-questions-success").textContent = pctText(week.questions_success);
  $("#progress-note").textContent = `· ${plural(data.active_days, "jour", "jours")} de révision`;

  $("#heatmap").innerHTML = heatmapHtml(data.days);

  const top = Math.max(1, ...data.forecast);
  const dayName = (i) => (i === 0 ? "Auj." : i === 1 ? "Dem." : new Date(Date.now() + i * 86400000).toLocaleDateString("fr-FR", { weekday: "short" }));
  $("#forecast").innerHTML = data.forecast.map((n, i) => `
    <div class="fc-col" title="${plural(n, "carte", "cartes")}"><span class="fc-num">${n || ""}</span>
      <span class="fc-bar" style="height:${Math.round((n / top) * 100)}%"></span><small>${dayName(i)}</small></div>`).join("");

  const pct = (v) => (v === null ? `<span class="muted">—</span>` : `${v} %`);
  const scope = reviewScope();
  const rows = data.courses.filter((c) => (scope.course ? c.id === scope.course : scope.folder ? c.folder_id === scope.folder : true));
  $("#course-progress-box").hidden = Boolean(state.reviewScope?.single);
  $("#course-progress").innerHTML = rows.length ? rows.map((c) => `
    <tr><td><a href="#/cours/${c.id}">${escapeHtml(c.name)}</a></td>
      <td>${c.cards ? `${c.known} / ${c.cards}` : `<span class="muted">—</span>`}</td>
      <td>${c.today || `<span class="muted">0</span>`}</td>
      <td>${c.weak ? `<b class="ko-text">${c.weak}</b>` : `<span class="muted">0</span>`}</td>
      <td>${pct(c.cards_success)}</td><td>${pct(c.questions_success)}</td>
      <td>${c.last ? formatDay(c.last) : `<span class="muted">jamais</span>`}</td></tr>`).join("")
    : `<tr><td colspan="7" class="muted">Aucun cours.</td></tr>`;
}

$("#mix-questions").addEventListener("change", (e) => {
  try { localStorage.setItem("pirouette.mixQuestions", e.target.checked ? "1" : "0"); } catch {}
  renderScopeMain();
});
$("#start-today").addEventListener("click", () => startSession({ mode: "today", ...reviewScope() },
  { title: "Révision du jour", back: state.reviewScope.hash }));
$("#start-partiel").addEventListener("click", () => {
  const id = reviewScope().course || $("#partiel-course").value;
  if (id) go(`#/reviser/cours/${id}/partiel`);
});
$("#start-weak").addEventListener("click", () => startSession({ mode: "weak", ...reviewScope() },
  { title: "Là où tu bloques", back: state.reviewScope.hash }));

// ---------- Déroulé du quiz ----------
// Pendant un quiz ou une révision, le suivi des créations se replie (il ne cache plus les boutons).
function foldJobs() {
  if (jobsState.open) { jobsState.open = false; renderJobs(); }
}

// `back` : la page d'où l'on lance le quiz (Réviser › un semestre ou un cours) ; sinon le cours du quiz.
// Un grand quiz (banque de questions) : Pirouette en tire quelques-unes à chaque lancement, d'abord celles
// ratées la dernière fois et celles jamais posées, puis les plus anciennes.
api("/api/settings").then((s) => { state.quizSize = s.quiz_size; }).catch(() => {});
function drawQuestions(quiz) {
  const all = quiz.questions;
  const size = state.quizSize ?? 10;
  if (!size || all.length <= size + 2) return all;
  const stats = quiz.stats || {};
  const priority = (i) => {
    const stat = stats[i];
    if (!stat) return 2;
    if (stat.last === false) return 3;
    const days = stat.date ? (Date.now() - Date.parse(stat.date)) / 864e5 : 30;
    return Math.min(days / 14, 1.5);
  };
  const picked = all.map((q, i) => [priority(i) + Math.random() * 0.6, q]).sort((a, b) => b[0] - a[0])
    .slice(0, size).map(([, q]) => q);
  return shuffle(picked);
}

function startQuiz(quiz, questions, { back = state.quizBack } = {}) {
  foldJobs();
  const drawn = !questions;
  questions = questions || drawQuestions(quiz);
  state.quizBack = back || null;
  state.quiz = quiz;
  state.questions = questions;
  // Les scores comptent pour un passage complet ou un tirage (pas pour « refaire mes erreurs »)
  state.fullRun = drawn || questions.length === quiz.questions.length;
  const partial = drawn && questions.length < quiz.questions.length;
  $("#quiz-draw").hidden = !partial;
  $("#quiz-draw").innerHTML = partial
    ? `${questions.length} questions tirées parmi les ${quiz.questions.length} de ce quiz, d'abord celles ratées ou jamais vues. `
      + `<a href="#" id="quiz-draw-all">Faire les ${quiz.questions.length}</a>` : "";
  state.index = 0;
  state.results = [];
  $("#quiz-title").textContent = quiz.title;
  // Tes questions dont la réponse n'est pas dans le cours : signalées au premier passage.
  const note = $("#quiz-note");
  note.hidden = !(quiz.dropped?.length && !quiz.attempts?.length);
  note.textContent = quiz.dropped?.length
    ? `Réponse introuvable dans le cours, question écartée : ${quiz.dropped.map((q) => `« ${q} »`).join(", ")}.` : "";
  const inCourse = quiz.course_id && state.course?.id === quiz.course_id;
  if (state.quizBack) setCrumbs(sessionCrumbs(state.quizBack, quiz.title));
  else setCrumbs(inCourse ? courseCrumbs(state.course, "quiz", quiz.title)
    : quiz.course_id ? [COURSES_CRUMB, { label: quiz.course_name || "Cours", href: `#/cours/${quiz.course_id}/quiz` }, { label: quiz.title }]
    : [COURSES_CRUMB, { label: quiz.title }]);
  $("#back-course-btn").textContent = state.quizBack ? "Retour à Réviser" : quiz.course_id ? "Retour au cours" : "Mes cours";
  show("quiz");
  renderQuestion();
}

$("#quiz-draw").addEventListener("click", (e) => {
  if (e.target.id !== "quiz-draw-all") return;
  e.preventDefault();
  startQuiz(state.quiz, state.quiz.questions);
});

function backToCourse() {
  if (state.quizBack) return go(state.quizBack);
  go(state.quiz?.course_id ? `#/cours/${state.quiz.course_id}/quiz` : "#/cours");
}
$("#back-course-btn").addEventListener("click", backToCourse);

// Figure du cours affichée sous la question (quiz écrits par Claude à partir d'un schéma)
function showFigure(id, q, courseId) {
  const img = $(id);
  img.classList.remove("zoomed");
  img.hidden = !(q.figure && courseId);
  if (!img.hidden) img.src = `/api/courses/${courseId}/figure?ref=${encodeURIComponent(q.figure)}`;
  else img.removeAttribute("src");
}

document.querySelectorAll(".q-figure").forEach((img) => img.addEventListener("click", () => img.classList.toggle("zoomed")));

function renderQuestion() {
  const q = state.questions[state.index];
  const total = state.questions.length;
  state.answered = false;
  $("#question-tools").hidden = true;
  $("#quiz-counter").textContent = `${state.index + 1} / ${total}`;
  if (state.index > 0) $("#quiz-note").hidden = true;
  $("#progress-bar").style.width = `${(state.index / total) * 100}%`;
  $("#question-type").textContent = (TYPE_LABELS[q.type] || q.type) + (q.kind === "cours" ? " · Question de cours" : "")
    + (q.manual ? " · Ta question" : "");
  $("#question-text").textContent = q.question;
  showFigure("#question-figure", q, state.quiz?.course_id);
  $("#feedback").hidden = true;
  $("#validate-btn").hidden = false;
  $("#next-btn").hidden = true;

  const area = $("#answer-area");
  if (q.type === "texte_a_trous") {
    $("#question-text").innerHTML = clozeHtml(q, "short-answer");
    area.innerHTML = "";
    $("#short-answer").focus();
  } else if (q.type === "reponse_courte") {
    area.innerHTML = `<textarea id="short-answer" rows="2" placeholder="Ta réponse…"></textarea>`;
    $("#short-answer").focus();
  } else {
    area.innerHTML = q.choices.map((c, i) => `
      <label class="choice"><input type="radio" name="choice" value="${i}">
      <span>${escapeHtml(c)}</span></label>`).join("");
  }
  $("#prev-btn").hidden = state.index === 0;
  // Question déjà faite (retour en arrière) : on la revoit avec sa correction.
  const done = state.results.find((r) => r.question === q);
  if (done) showAnswered(q, done);
}

function showAnswered(q, result) {
  state.answered = true;
  const input = $("#short-answer");
  if (input) { input.value = result.given; input.disabled = true; }
  document.querySelectorAll(".choice").forEach((label, i) => {
    const radio = label.querySelector("input");
    radio.disabled = true;
    radio.checked = q.choices[i] === result.given;
    if (q.choices[i] === q.answer) label.classList.add("right");
    else if (q.choices[i] === result.given) label.classList.add("wrong");
  });
  const fb = $("#feedback");
  fb.className = `feedback ${result.correct ? "ok" : "ko"}`;
  fb.innerHTML = `${q.choices?.length ? "" : `<p><strong>${result.correct ? "Bonne réponse" : "Réponse attendue"} :</strong> ${escapeHtml(q.answer)}</p>`}
    ${q.explanation ? `<p>${escapeHtml(cleanExplanation(q.explanation))}</p>` : ""}${sourceHtml(q)}${keyTermsHtml(q)}`;
  fb.hidden = false;
  showReportButton(q, result.given);
  $("#validate-btn").hidden = true;
  $("#next-btn").hidden = false;
  const last = state.index + 1 >= state.questions.length;
  $("#next-btn").textContent = last ? "Voir le résultat" : "Question suivante";
}

$("#prev-btn").addEventListener("click", () => {
  if (state.index === 0) return;
  state.index -= 1;
  renderQuestion();
});

function sessionKeys(e) {
  const s = state.session;
  const item = s?.items[s.index];
  if (!item || !$("#cards-done").hidden || e.metaKey || e.ctrlKey) return;
  if (item.kind === "card") {
    if (e.target.id === "typed-answer") return;
    if ((e.key === " " || (e.key === "Enter" && !s.revealed)) && !e.target.closest?.("button")) { e.preventDefault(); flipCard(); }
    else if (s.revealed && /^[1-3]$/.test(e.key)) { e.preventDefault(); rateCard(["again", "hard", "good"][e.key - 1]); }
    return;
  }
  if (/^[1-9]$/.test(e.key) && e.target.tagName !== "TEXTAREA" && !$("#sq-validate").hidden) {
    document.querySelectorAll("input[name=sq-choice]")[e.key - 1]?.click();
  } else if (e.key === "Enter" && !e.shiftKey && !e.target.closest?.("button")) {
    e.preventDefault();
    if (!$("#sq-validate").hidden) validateSessionQuestion();
    else if (!$("#sq-next").hidden) nextItem();
  }
}

$("#validate-btn").addEventListener("click", validate);
$("#next-btn").addEventListener("click", next);
document.addEventListener("keydown", (e) => {
  if (document.querySelector("dialog[open]")) return;  // on écrit dans une fenêtre (carte, signalement…)
  if (!$("#view-cards").hidden) return sessionKeys(e);
  if ($("#view-quiz").hidden || e.key !== "Enter" || e.shiftKey) return;
  e.preventDefault();
  if (!$("#validate-btn").hidden) validate();
  else if (!$("#next-btn").hidden) next();
});

// Texte à trous : la phrase du cours avec un champ à la place du trou.
function clozeHtml(q, inputId) {
  const [before, after = ""] = q.question.split(BLANK);
  const width = Math.max(6, Math.min(28, q.answer.length + 3));
  return `<span class="cloze">${escapeHtml(before)}<input type="text" id="${inputId}" class="cloze-input" style="width:${width}ch"
    autocomplete="off" spellcheck="false" aria-label="Mot manquant">${escapeHtml(after)}</span>`;
}

// Réponse tapée juste : identique à quelques détails près (accents, articles, majuscules, faute de frappe).
function typedIsRight(given, expected) {
  if (looselyEqual(given, expected)) return true;
  const a = normalize(given), b = normalize(expected);
  return b.length >= 5 && editDistance(a, b) <= Math.floor(b.length / 6);
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

function validate() {
  const q = state.questions[state.index];
  if (q.type === "reponse_courte" || q.type === "texte_a_trous") return validateShort(q);

  const picked = document.querySelector("input[name=choice]:checked");
  if (!picked) return;
  const given = q.choices[Number(picked.value)];
  const correct = given === q.answer;
  document.querySelectorAll(".choice").forEach((label, i) => {
    label.querySelector("input").disabled = true;
    if (q.choices[i] === q.answer) label.classList.add("right");
    else if (i === Number(picked.value)) label.classList.add("wrong");
  });
  record(q, given, correct);
}

function validateShort(q) {
  const input = $("#short-answer");
  const given = input.value.trim();
  if (!given) return;
  input.disabled = true;
  if (typedIsRight(given, q.answer)) return record(q, given, true);

  // Réponse libre : on montre la réponse attendue et l'utilisateur s'auto-évalue.
  state.answered = true;
  showReportButton(q, given);
  const fb = $("#feedback");
  fb.className = "feedback neutral";
  fb.innerHTML = `<p><strong>Réponse attendue :</strong> ${escapeHtml(q.answer)}</p>
    ${q.explanation ? `<p>${escapeHtml(cleanExplanation(q.explanation))}</p>` : ""}
    ${sourceHtml(q)}
    ${keyTermsHtml(q)}
    <div class="actions"><button class="primary" id="self-right">J'avais bon</button>
    <button class="ghost" id="self-wrong">J'avais faux</button></div>
`;
  fb.hidden = false;
  $("#validate-btn").hidden = true;
  // La réponse attendue et l'explication sont déjà affichées : on passe directement à la suite.
  const selfGrade = (correct) => { saveAnswer(q, correct); state.results.push({ question: q, given, correct }); next(); };
  $("#self-right").onclick = () => selfGrade(true);
  $("#self-wrong").onclick = () => selfGrade(false);
}

// Chaque réponse compte pour le suivi et les points faibles (même si on ne va pas au bout du quiz).
function saveAnswer(q, correct) {
  const index = state.quiz.questions.indexOf(q);
  if (index >= 0) api(`/api/quizzes/${state.quiz.id}/answers`, jsonBody("POST", { answers: [{ index, correct }] })).catch(() => {});
}

function record(q, given, correct) {
  saveAnswer(q, correct);
  state.answered = true;
  showReportButton(q, given);
  state.results.push({ question: q, given, correct });
  const fb = $("#feedback");
  fb.className = `feedback ${correct ? "ok" : "ko"}`;
  // QCM et vrai/faux : la bonne réponse est déjà surlignée en vert, inutile de la répéter.
  const verdict = q.type === "reponse_courte" || q.type === "texte_a_trous"
    ? `<p><strong>Bonne réponse !</strong>${q.type === "texte_a_trous" && given !== q.answer ? ` (${escapeHtml(q.answer)})` : ""}</p>` : "";
  fb.innerHTML = `${verdict}
    ${q.explanation ? `<p>${escapeHtml(cleanExplanation(q.explanation))}</p>` : ""}
    ${sourceHtml(q)}
    ${keyTermsHtml(q)}
`;
  fb.hidden = false;
  $("#validate-btn").hidden = true;
  $("#next-btn").hidden = false;
  $("#next-btn").textContent = state.index + 1 < state.questions.length ? "Question suivante" : "Voir le résultat";
  $("#next-btn").focus();
}

// La phrase du cours d'où vient la question (vérifiée à la création du quiz).
// Sans les marques de gras / italique du cours (« ***GABA*** » → « GABA »).
function plainMd(text) {
  return String(text ?? "").replace(/\*{2,3}(?=\S)(.+?)(?<=\S)\*{2,3}/g, "$1").replace(/(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])/g, "$1");
}

// Explications déjà enregistrées : sans « Le cours précise que… » (on sait que tout vient du cours).
function cleanExplanation(text) {
  const raw = plainMd(text);
  const rest = raw.replace(/^(?:(?:selon|d['’]après|dans) (?:le|ton|ce) cours,?\s*|(?:le|ton|ce) cours (?:précise|indique|explique|dit|stipule|mentionne|affirme|souligne|décrit|définit|rappelle|montre|note)(?: bien)? (?:que |qu['’]|:\s*)?)/i, "").trim();
  return rest && rest !== raw ? rest[0].toUpperCase() + rest.slice(1) : raw;
}

function sourceHtml(item) {
  return item?.source ? `<p class="source"><span>Dans ton cours</span>« ${escapeHtml(plainMd(item.source))} »</p>` : "";
}

function keyTermsHtml(q) {
  if (!q.key_terms?.length) return "";
  return `<details class="key-terms"><summary>Mots-clés (${q.key_terms.length})</summary><dl>
    ${q.key_terms.map((t) => `<dt>${escapeHtml(t.term)}</dt><dd>${escapeHtml(t.definition)}</dd>`).join("")}
  </dl></details>`;
}

function next() {
  state.index += 1;
  if (state.index < state.questions.length) renderQuestion();
  else showResults();
}

// ---------- Résultats ----------
function showResults() {
  state.resultsFilter = "all";
  $("#retry-btn").textContent = isBank(state.quiz) ? "Nouveau tirage" : "Refaire le quiz";
  renderResults();
  show("results");
  // Seules les sessions complètes comptent dans les scores du quiz.
  const good = state.results.filter((r) => r.correct).length;
  if (state.fullRun && state.results.length) {
    api(`/api/quizzes/${state.quiz.id}/attempts`, jsonBody("POST", { score: good, total: state.results.length })).catch(() => {});
  }
}

// Correction : toutes les questions, ou seulement les erreurs.
function renderResults() {
  const good = state.results.filter((r) => r.correct).length;
  const total = state.results.length;
  $("#score").textContent = total ? `${good} / ${total} (${Math.round((good / total) * 100)} %)` : "—";
  $("#retry-wrong-btn").hidden = good === total;
  const filter = state.resultsFilter || "all";
  document.querySelectorAll("[data-results]").forEach((b) => b.classList.toggle("active", b.dataset.results === filter));
  const shown = state.results.filter((r) => filter === "all" || !r.correct);
  $("#review").innerHTML = shown.length ? shown.map((r) => `
    <li class="${r.correct ? "ok" : "ko"}">
      <p class="q">${escapeHtml(r.question.question)}</p>
      <p class="${r.correct ? "given-ok" : "given-ko"}">${r.correct ? "✓" : "✗"} Ta réponse : ${escapeHtml(r.given)}</p>
      ${r.correct ? "" : `<p>Bonne réponse : <strong>${escapeHtml(r.question.answer)}</strong></p>`}
      ${r.question.explanation ? `<p class="muted">${escapeHtml(cleanExplanation(r.question.explanation))}</p>` : ""}
      ${sourceHtml(r.question)}
      ${keyTermsHtml(r.question)}
      <div class="question-tools">
        ${explainButton({ course_id: state.quiz.course_id, question: r.question.question, expected: r.question.answer, given: r.given, source: r.question.source })}
        ${qtoolsHtml([
          `<button type="button" data-report="${state.results.indexOf(r)}"><strong>Signaler une erreur</strong><small>La question ou sa réponse est incorrecte</small></button>`,
          `<button type="button" class="danger" data-delete-result="${state.results.indexOf(r)}"><strong>Supprimer cette question</strong><small>Hors sujet : elle ne sera plus posée</small></button>`])}
      </div>
    </li>`).join("") : `<li class="empty muted">Aucune erreur : tout était juste.</li>`;
}

document.querySelectorAll("[data-results]").forEach((b) => b.addEventListener("click", () => {
  state.resultsFilter = b.dataset.results;
  renderResults();
}));

// Supprimer une question jugée hors sujet (dans le quiz, la correction ou une séance de révision).
async function deleteQuestion(quizId, index) {
  if (!confirm("Supprimer cette question du quiz ? Elle ne sera plus jamais posée.")) return false;
  await api(`/api/quizzes/${quizId}/questions/${index}`, { method: "DELETE" });
  return true;
}

$("#review").addEventListener("click", async (e) => {
  const at = e.target.closest("[data-delete-result]")?.dataset.deleteResult;
  if (at === undefined) return;
  const result = state.results[at];
  const index = state.quiz.questions.indexOf(result.question);
  if (!(await deleteQuestion(state.quiz.id, index))) return;
  state.quiz.questions.splice(index, 1);
  state.results.splice(at, 1);
  renderResults();
});

$("#delete-q-btn").addEventListener("click", async () => {
  const q = state.questions[state.index];
  const index = state.quiz.questions.indexOf(q);
  if (!(await deleteQuestion(state.quiz.id, index))) return;
  state.quiz.questions.splice(index, 1);
  // (La séance peut utiliser la liste du quiz elle-même : ne pas retirer deux fois.)
  if (state.questions !== state.quiz.questions) state.questions.splice(state.index, 1);
  // La réponse à cette question ne compte pas.
  if (state.results.at(-1)?.question === q) state.results.pop();
  if (state.index < state.questions.length) renderQuestion();
  else if (state.results.length) showResults();
  else backToCourse();
});

const isBank = (quiz) => Boolean(state.quizSize) && quiz.questions.length > state.quizSize + 2;
$("#retry-btn").addEventListener("click", () => startQuiz(state.quiz, isBank(state.quiz) ? undefined : shuffle(state.quiz.questions)));
$("#retry-wrong-btn").addEventListener("click", () =>
  startQuiz(state.quiz, state.results.filter((r) => !r.correct).map((r) => r.question)));

// ---------- Définitions repérées dans un fichier ----------
document.addEventListener("click", async (e) => {
  const id = e.target.closest("[data-show-defs]")?.dataset.showDefs;
  if (!id) return;
  const file = state.course.files.find((f) => f.id === id);
  const definitions = await api(`/api/courses/${state.course.id}/files/${id}/definitions`);
  $("#defs-title").textContent = `Définitions repérées · ${file?.name || ""}`;
  $("#defs-dialog-list").innerHTML = definitions.map((d) => `<li><strong>${escapeHtml(d.term)}</strong>
    <span class="muted">${escapeHtml(d.definition)}</span></li>`).join("");
  $("#defs-dialog").showModal();
});
$("#defs-close").addEventListener("click", () => $("#defs-dialog").close());

// ---------- Signaler une question incorrecte ----------
// Le signalement est gardé dans l'app, puis Pirouette ouvre l'app Mail avec le message prêt à partir.
function showReportButton(q, given) {
  state.reporting = { question: q, given };
  $("#question-tools").hidden = false;
  $("#question-tools .qtools-menu").hidden = true;
}

function openReport(question, given) {
  state.reporting = { question, given };
  const q = question;
  $("#report-question").textContent = q.question;
  $("#report-choices").innerHTML = (q.choices?.length ? q.choices : [q.answer]).map((c) => `
    <li class="${c === q.answer ? "is-answer" : ""}">${c === q.answer ? "✓ " : ""}${escapeHtml(c)}${
      c === given && c !== q.answer ? ` <span class="muted">(ta réponse)</span>` : ""}</li>`).join("");
  $("#report-given").textContent = given && !q.choices?.length ? `Ta réponse : ${given}` : "";
  $("#report-message").value = "";
  $("#report-status").hidden = true;
  $("#report-send").disabled = false;
  $("#report-dialog").showModal();
  $("#report-message").focus();
}

$("#report-btn").addEventListener("click", () => openReport(state.reporting.question, state.reporting.given));
$("#review").addEventListener("click", (e) => {
  const index = e.target.closest("[data-report]")?.dataset.report;
  if (index !== undefined) openReport(state.results[index].question, state.results[index].given);
});
$("#report-cancel").addEventListener("click", () => $("#report-dialog").close());

$("#report-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const { question, given } = state.reporting;
  $("#report-send").disabled = true;
  try {
    const result = await api("/api/feedback", jsonBody("POST", {
      quiz_id: state.quiz.id, index: state.quiz.questions.indexOf(question), given, message: $("#report-message").value,
    }));
    if (!result.opened) window.location.href = result.mailto;  // navigateur : ouvre l'app de mail
    setStatus("#report-status", "Merci ! Ton app Mail s'ouvre avec le message prêt : il ne reste qu'à cliquer sur Envoyer.", true);
    setTimeout(() => $("#report-dialog").close(), 4000);
  } catch (err) {
    setStatus("#report-status", err.message, false);
    $("#report-send").disabled = false;
  }
});

// ---------- Utilitaires ----------
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function normalize(s) {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\b(le|la|les|l|un|une|des|du|de|d|the|a|an)\b/g, " ")
    .replace(/\s+/g, " ").trim();
}
function looselyEqual(a, b) {
  return normalize(a) === normalize(b);
}
function shuffle(list) {
  const copy = [...list];
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}
function formatSize(bytes) {
  return bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} Mo` : `${Math.ceil(bytes / 1e3)} Ko`;
}
function formatDate(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("fr-FR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

// « aujourd'hui », « hier », « il y a 3 j », sinon la date courte.
function formatDay(iso) {
  if (!iso) return "";
  const day = new Date(iso);
  const days = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(iso).setHours(0, 0, 0, 0)) / 86400000);
  if (days <= 0) return "aujourd'hui";
  if (days === 1) return "hier";
  if (days < 7) return `il y a ${days} j`;
  return day.toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
}

route();

// ---------- Menus déroulants aux couleurs de l'app ----------
// Chaque <select> est doublé d'un bouton et d'une liste stylés ; le <select> (caché) garde la valeur, les
// options et l'événement « change » : le reste du code n'a rien à changer. Les nouveaux <select> sont pris en
// charge automatiquement.
const CHEVRON = `<svg class="dd-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>`;
const DD_ICONS = { folder: FOLDER_ICON.replace('class="folder-ico"', 'class="dd-icon"') };
const valueProp = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
const indexProp = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "selectedIndex");

function enhanceSelect(select) {
  if (select.dataset.dd) return;
  select.dataset.dd = "1";
  const box = document.createElement("div");
  box.className = `dd ${select.className}`.trim();
  box.innerHTML = `<button type="button" class="dd-btn" aria-haspopup="listbox" aria-expanded="false">
      ${DD_ICONS[select.dataset.icon] || ""}<span class="dd-label"></span>${CHEVRON}</button>
    <div class="dd-list" role="listbox" hidden></div>`;
  select.after(box);
  box.prepend(select);
  select.hidden = true;
  select.tabIndex = -1;
  const button = box.querySelector(".dd-btn");
  const list = box.querySelector(".dd-list");
  if (select.getAttribute("aria-label")) button.setAttribute("aria-label", select.getAttribute("aria-label"));
  if (select.id) document.querySelectorAll(`label[for="${select.id}"]`).forEach((l) => l.addEventListener("click", (e) => { e.preventDefault(); button.focus(); }));

  const update = () => {
    const option = select.options[select.selectedIndex];
    box.querySelector(".dd-label").textContent = option ? option.textContent : "";
    button.disabled = select.disabled;
  };
  // Valeur changée par le code (select.value = …) : le bouton suit.
  Object.defineProperty(select, "value", { get() { return valueProp.get.call(this); }, set(v) { valueProp.set.call(this, v); update(); } });
  Object.defineProperty(select, "selectedIndex", { get() { return indexProp.get.call(this); }, set(v) { indexProp.set.call(this, v); update(); } });
  new MutationObserver(update).observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled"] });

  let active = -1;
  const items = () => [...list.querySelectorAll(".dd-option:not([aria-disabled=true])")];
  const setActive = (i) => {
    const all = items();
    active = Math.max(0, Math.min(all.length - 1, i));
    all.forEach((el, k) => el.classList.toggle("active", k === active));
    all[active]?.scrollIntoView({ block: "nearest" });
  };
  const open = () => {
    closeAllDropdowns(box);
    list.innerHTML = [...select.children].map((node) => node.tagName === "OPTGROUP"
      ? `<div class="dd-group">${escapeHtml(node.label)}</div>${[...node.children].map(optionHtml).join("")}`
      : optionHtml(node)).join("");
    list.hidden = false;
    button.setAttribute("aria-expanded", "true");
    box.classList.add("open");
    // Pas la place en dessous : la liste s'ouvre vers le haut.
    const rect = button.getBoundingClientRect();
    box.classList.toggle("up", window.innerHeight - rect.bottom < Math.min(320, list.scrollHeight) + 16 && rect.top > window.innerHeight - rect.bottom);
    setActive(items().findIndex((el) => el.dataset.index === String(select.selectedIndex)));
  };
  const close = () => {
    list.hidden = true;
    button.setAttribute("aria-expanded", "false");
    box.classList.remove("open", "up");
  };
  const choose = (index) => {
    close();
    button.focus();
    if (Number(index) === select.selectedIndex) return;
    indexProp.set.call(select, Number(index));
    update();
    select.dispatchEvent(new Event("change", { bubbles: true }));
  };
  box.closeDropdown = close;
  button.addEventListener("click", () => (list.hidden ? open() : close()));
  list.addEventListener("click", (e) => {
    const option = e.target.closest(".dd-option");
    if (option && option.getAttribute("aria-disabled") !== "true") choose(option.dataset.index);
  });
  box.addEventListener("keydown", (e) => {
    if (list.hidden) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key) && e.target === button) { e.preventDefault(); open(); }
      return;
    }
    if (e.key === "ArrowDown") { e.preventDefault(); setActive(active + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(active - 1); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); const el = items()[active]; if (el) choose(el.dataset.index); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); button.focus(); }
    else if (e.key === "Tab") close();
    else if (e.key.length === 1) {
      const next = items().findIndex((el, k) => k > active && normalize(el.textContent).startsWith(normalize(e.key)));
      if (next >= 0) setActive(next);
    }
  });
  update();

  function optionHtml(option) {
    const index = [...select.options].indexOf(option);
    const selected = index === select.selectedIndex;
    return `<div class="dd-option${selected ? " selected" : ""}" role="option" data-index="${index}" aria-selected="${selected}"
      ${option.disabled ? 'aria-disabled="true"' : ""}>${escapeHtml(option.textContent)}</div>`;
  }
}

function closeAllDropdowns(except = null) {
  document.querySelectorAll(".dd.open").forEach((box) => box !== except && box.closeDropdown());
}
document.addEventListener("click", (e) => { if (!e.target.closest(".dd")) closeAllDropdowns(); });
document.querySelectorAll("select").forEach(enhanceSelect);
new MutationObserver((changes) => {
  for (const change of changes) {
    change.addedNodes.forEach((node) => {
      if (node.nodeType !== 1) return;
      if (node.tagName === "SELECT") enhanceSelect(node);
      else node.querySelectorAll?.("select").forEach(enhanceSelect);
    });
  }
}).observe(document.body, { childList: true, subtree: true });

// ---------- Feedback : une idée, un bug… ----------
$("#feedback-btn").addEventListener("click", () => {
  $("#feedback-message").value = "";
  $("#feedback-status").hidden = true;
  $("#feedback-send").disabled = false;
  $("#feedback-dialog").showModal();
  $("#feedback-message").focus();
});
$("#feedback-cancel").addEventListener("click", () => $("#feedback-dialog").close());
$("#feedback-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const where = [...$("#crumbs").querySelectorAll("a, .here")].map((el) => el.textContent).join(" › ")
    || document.querySelector("[data-nav].on")?.textContent || "";
  $("#feedback-send").disabled = true;
  try {
    const result = await api("/api/feedback/general", jsonBody("POST", {
      kind: $("#feedback-kind").value, message: $("#feedback-message").value, page: where,
    }));
    if (!result.opened) window.location.href = result.mailto;
    setStatus("#feedback-status", "Merci ! Ton app Mail s'ouvre avec le message prêt : il ne reste qu'à cliquer sur Envoyer.", true);
    setTimeout(() => $("#feedback-dialog").close(), 4000);
  } catch (err) {
    setStatus("#feedback-status", err.message, false);
    $("#feedback-send").disabled = false;
  }
});

// ---------- Mode partiel ----------
// Préparation (nombre de questions et de flashcards, chronomètre), puis l'épreuve en conditions d'examen :
// rien n'est corrigé avant « Rendre ma copie ». Les réponses écrites se corrigent à la main ou par l'IA, puis note /20.
const PARTIEL_DEFAULTS = { questions: 20, cards: 10 };

async function openPartielPrep(courseId) {
  const [course, info] = await Promise.all([api(`/api/courses/${courseId}`),
    api(`/api/courses/${courseId}/partiel?questions=0&cards=0`)]);
  state.partielCourse = course;
  state.course = null;
  $("#partiel-course-name").textContent = course.name;
  const { questions, cards } = info.available;
  $("#partiel-questions").value = Math.min(PARTIEL_DEFAULTS.questions, questions);
  $("#partiel-cards").value = Math.min(PARTIEL_DEFAULTS.cards, cards);
  $("#partiel-questions-max").textContent = `(${questions} disponible${questions > 1 ? "s" : ""})`;
  $("#partiel-cards-max").textContent = `(${cards} disponible${cards > 1 ? "s" : ""})`;
  $("#partiel-questions").max = questions;
  $("#partiel-cards").max = cards;
  $("#partiel-timer").value = "none";
  $("#partiel-minutes-field").hidden = true;
  showError("#partiel-error", questions + cards ? "" : "Ce cours n'a encore ni quiz ni flashcards : crée-en d'abord.");
  $("#partiel-go").disabled = !(questions + cards);
  const history = info.history.slice().reverse();
  $("#partiel-history").hidden = !history.length;
  $("#partiel-history-list").innerHTML = history.map((h) => `<li><strong>${formatScore(h.score)} / 20</strong>
    <span class="muted">${formatDay(h.date)} · ${plural(h.total, "question", "questions")}${h.duration ? ` · ${formatDuration(h.duration)}` : ""}</span></li>`).join("");
  setCrumbs([{ label: "Réviser", href: "#/reviser" }, { label: course.name, href: `#/reviser/cours/${course.id}` }, { label: "Partiel" }]);
  show("partiel");
  updatePartielMinutes();
}

const formatScore = (n) => String(Math.round(n * 10) / 10).replace(".", ",");
function formatDuration(seconds) {
  const m = Math.floor(seconds / 60), sec = seconds % 60;
  return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}` : `${m} min ${String(sec).padStart(2, "0")}`;
}
const clock = (seconds) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

// Durée proposée pour le compte à rebours : environ une minute par élément.
function updatePartielMinutes() {
  if (!$("#partiel-minutes").dataset.touched) {
    $("#partiel-minutes").value = Math.max(5, (Number($("#partiel-questions").value) || 0) + (Number($("#partiel-cards").value) || 0));
  }
}
document.querySelectorAll("[data-count-for]").forEach((chips) => chips.addEventListener("click", (e) => {
  const button = e.target.closest("button");
  if (!button) return;
  const input = $(`#${chips.dataset.countFor}`);
  input.value = Math.min(Number(button.textContent), Number(input.max) || 200);
  updatePartielMinutes();
}));
["#partiel-questions", "#partiel-cards"].forEach((id) => $(id).addEventListener("input", updatePartielMinutes));
$("#partiel-minutes").addEventListener("input", (e) => { e.target.dataset.touched = "1"; });
$("#partiel-timer").addEventListener("change", (e) => { $("#partiel-minutes-field").hidden = e.target.value !== "down"; });

$("#partiel-go").addEventListener("click", async () => {
  const course = state.partielCourse;
  const questions = Number($("#partiel-questions").value) || 0;
  const cards = Number($("#partiel-cards").value) || 0;
  if (!questions && !cards) return showError("#partiel-error", "Choisis au moins une question ou une flashcard.");
  const data = await api(`/api/courses/${course.id}/partiel?questions=${questions}&cards=${cards}`);
  if (!data.items.length) return showError("#partiel-error", "Rien à mettre dans ce partiel pour l'instant.");
  startExam(course, data.items, { timer: $("#partiel-timer").value, minutes: Number($("#partiel-minutes").value) || 30 });
});

function startExam(course, items, { timer, minutes }) {
  foldJobs();
  clearInterval(state.exam?.tick);
  state.exam = { course, items, answers: items.map(() => ""), doubts: items.map(() => false), index: 0,
                 timer, limit: timer === "down" ? minutes * 60 : null, started: Date.now(), running: true, hideClock: false };
  $("#exam-title").textContent = `Partiel · ${course.name}`;
  $("#exam-run").hidden = false;
  $("#exam-grading").hidden = $("#exam-result").hidden = true;
  $("#exam-timer").hidden = timer === "none";
  $("#exam-clock").hidden = false;
  $("#exam-timer-toggle").textContent = "Masquer";
  if (timer !== "none") {
    state.exam.tick = setInterval(updateExamClock, 1000);
    updateExamClock();
  }
  setCrumbs([{ label: "Réviser", href: "#/reviser" }, { label: course.name, href: `#/reviser/cours/${course.id}` },
    { label: "Partiel", href: `#/reviser/cours/${course.id}/partiel` }, { label: "Épreuve" }]);
  show("exam");
  showExamItem();
}

const examElapsed = () => Math.round((Date.now() - state.exam.started) / 1000);
function updateExamClock() {
  const exam = state.exam;
  if (!exam?.running) return;
  const elapsed = examElapsed();
  if (exam.limit) {
    const left = Math.max(0, exam.limit - elapsed);
    $("#exam-clock").textContent = `Il reste ${clock(left)}`;
    $("#exam-timer").classList.toggle("urgent", left <= 60);
    if (!left) submitExam(true);
  } else {
    $("#exam-clock").textContent = clock(elapsed);
  }
}
$("#exam-timer-toggle").addEventListener("click", () => {
  const hidden = !$("#exam-clock").hidden;
  $("#exam-clock").hidden = hidden;
  $("#exam-timer-toggle").textContent = hidden ? "Afficher le chrono" : "Masquer";
});

function renderExamNav() {
  const exam = state.exam;
  $("#exam-nav").innerHTML = exam.items.map((_, i) => {
    const classes = [i === exam.index ? "current" : "", exam.answers[i] !== "" ? "done" : "", exam.doubts[i] ? "doubt" : ""].join(" ");
    return `<button type="button" class="${classes.trim()}" data-exam-go="${i}" aria-label="Question ${i + 1}${exam.doubts[i] ? " (je doute)" : ""}">${i + 1}</button>`;
  }).join("");
}
$("#exam-nav").addEventListener("click", (e) => {
  const i = e.target.closest("[data-exam-go]")?.dataset.examGo;
  if (i !== undefined) { state.exam.index = Number(i); showExamItem(); }
});

function showExamItem() {
  const exam = state.exam;
  const item = exam.items[exam.index];
  const saved = exam.answers[exam.index];
  const total = exam.items.length;
  const area = $("#exam-answer");
  if (item.kind === "card") {
    $("#exam-figure").hidden = true;
    $("#exam-tag").textContent = `${exam.index + 1} / ${total} · Flashcard`;
    $("#exam-text").textContent = item.card.front;
    area.innerHTML = `<textarea id="exam-input" rows="3" placeholder="Ta réponse…"></textarea>`;
  } else {
    const q = item.question;
    $("#exam-tag").textContent = `${exam.index + 1} / ${total} · ${TYPE_LABELS[q.type] || q.type}`;
    showFigure("#exam-figure", q, exam.course.id);
    if (q.type === "texte_a_trous") {
      $("#exam-text").innerHTML = clozeHtml(q, "exam-input");
      area.innerHTML = "";
    } else if (!q.choices?.length) {
      $("#exam-text").textContent = q.question;
      area.innerHTML = `<textarea id="exam-input" rows="2" placeholder="Ta réponse…"></textarea>`;
    } else {
      $("#exam-text").textContent = q.question;
      area.innerHTML = q.choices.map((c, i) => `<label class="choice"><input type="radio" name="exam-choice" value="${i}"
        ${saved === c ? "checked" : ""}><span>${escapeHtml(c)}</span></label>`).join("");
    }
  }
  const input = $("#exam-input");
  if (input) {
    input.value = saved;
    input.addEventListener("input", () => { exam.answers[exam.index] = input.value; renderExamNav(); });
    input.focus();
  }
  $("#exam-doubt").classList.toggle("active", exam.doubts[exam.index]);
  $("#exam-doubt").setAttribute("aria-pressed", exam.doubts[exam.index]);
  $("#exam-prev").disabled = exam.index === 0;
  $("#exam-next").disabled = exam.index === total - 1;
  renderExamNav();
}
$("#exam-answer").addEventListener("change", (e) => {
  if (e.target.name !== "exam-choice") return;
  const exam = state.exam;
  exam.answers[exam.index] = exam.items[exam.index].question.choices[Number(e.target.value)];
  renderExamNav();
});
$("#exam-prev").addEventListener("click", () => { state.exam.index -= 1; showExamItem(); });
$("#exam-next").addEventListener("click", () => { state.exam.index += 1; showExamItem(); });
$("#exam-doubt").addEventListener("click", () => {
  const exam = state.exam;
  exam.doubts[exam.index] = !exam.doubts[exam.index];
  showExamItem();
});
$("#exam-submit").addEventListener("click", () => submitExam(false));

// On quitte un quiz, une révision ou un partiel en cours (menu, fil d'Ariane) : on demande d'abord.
function leaveQuestion() {
  if (state.exam?.running && !$("#view-exam").hidden) return "Tu es sûr de vouloir quitter le partiel ? Tes réponses seront perdues.";
  if (!$("#view-quiz").hidden) return "Tu es sûr de vouloir quitter le quiz ?";
  if (!$("#view-cards").hidden && $("#cards-done").hidden) return "Tu es sûr de vouloir quitter la révision ?";
  return null;
}
document.addEventListener("click", (e) => {
  const link = e.target.closest("a[href^='#/']");
  if (!link || e.defaultPrevented) return;
  const question = leaveQuestion();
  if (question && !confirm(question)) return e.preventDefault();
  // Quiz ou révision lancés depuis une page : son lien a la même adresse, le navigateur ne ferait rien.
  if (link.getAttribute("href") === location.hash) {
    e.preventDefault();
    route();
  }
}, true);

// ---- Rendre la copie : correction automatique, puis réponses écrites ----
function submitExam(timeUp) {
  const exam = state.exam;
  if (!exam.running) return;
  if (!timeUp) {
    const empty = exam.answers.filter((a) => !String(a).trim()).length;
    const doubts = exam.doubts.filter(Boolean).length;
    const warn = [empty ? plural(empty, "question sans réponse", "questions sans réponse") : "",
      doubts ? plural(doubts, "question marquée « Je doute »", "questions marquées « Je doute »") : ""].filter(Boolean).join(" et ");
    if (!confirm(`Rendre ta copie ?${warn ? ` Il reste ${warn}.` : ""}`)) return;
  }
  exam.running = false;
  exam.duration = examElapsed();
  clearInterval(exam.tick);
  exam.results = exam.items.map((item, i) => {
    const given = String(exam.answers[i] || "").trim();
    if (!given) return { verdict: "faux", by: "auto" };
    if (item.kind === "question") {
      const q = item.question;
      if (q.choices?.length) return { verdict: given === q.answer ? "juste" : "faux", by: "auto" };
      if (typedIsRight(given, q.answer)) return { verdict: "juste", by: "auto" };
    }
    return { verdict: null, by: null };  // réponse écrite : à corriger
  });
  const written = exam.results.filter((r) => r.verdict === null).length;
  $("#exam-run").hidden = true;
  if (!written) return showExamResult();
  $("#exam-grading-text").textContent = `${timeUp ? "Temps écoulé. " : ""}${plural(written, "réponse écrite", "réponses écrites")} à corriger (flashcards et réponses courtes). `
    + "L'IA juge l'idée, pas la formulation ; tu pourras contester chaque verdict. Ou corrige toi-même en comparant avec la réponse attendue.";
  $("#exam-grading-choice").hidden = false;
  $("#exam-grading-status").hidden = $("#exam-manual").hidden = $("#exam-manual-done").hidden = true;
  $("#exam-grading").hidden = false;
  window.scrollTo(0, 0);
}

const examPrompt = (item) => (item.kind === "card" ? item.card.front : item.question.question);
const examExpected = (item) => (item.kind === "card" ? item.card.back : item.question.answer);
const examSource = (item) => (item.kind === "card" ? item.card.source : item.question.source) || "";

$("#exam-grade-ai").addEventListener("click", async () => {
  const exam = state.exam;
  const pending = exam.results.map((r, i) => (r.verdict === null ? i : -1)).filter((i) => i >= 0);
  $("#exam-grading-choice").hidden = true;
  setStatus("#exam-grading-status", `L'IA corrige ${plural(pending.length, "réponse", "réponses")}… (quelques secondes par réponse)`, true);
  try {
    const { grades } = await api("/api/partiel/grade", jsonBody("POST", {
      provider: "local", model: state.config?.local?.default_model || "",
      items: pending.map((i) => ({ question: examPrompt(exam.items[i]), expected: examExpected(exam.items[i]),
        given: exam.answers[i], source: examSource(exam.items[i]) })),
    }));
    pending.forEach((i, k) => { if (grades[k]) exam.results[i] = { verdict: grades[k].verdict, reason: grades[k].reason, by: "ai" }; });
  } catch (err) {
    setStatus("#exam-grading-status", `L'IA n'a pas pu corriger (${err.message}). Corrige toi-même ci-dessous.`, false);
  }
  if (exam.results.some((r) => r.verdict === null)) return showManualGrading();
  showExamResult();
});
$("#exam-grade-self").addEventListener("click", showManualGrading);

// Correction à la main : réponse écrite et réponse attendue côte à côte.
function showManualGrading() {
  const exam = state.exam;
  $("#exam-grading-choice").hidden = true;
  const pending = exam.results.map((r, i) => (r.verdict === null ? i : -1)).filter((i) => i >= 0);
  $("#exam-manual").innerHTML = pending.map((i) => `
    <li data-grade-item="${i}">
      <p class="q">${escapeHtml(examPrompt(exam.items[i]))}</p>
      <p>Ta réponse : <strong>${escapeHtml(exam.answers[i])}</strong></p>
      <p class="muted">Réponse attendue : ${escapeHtml(examExpected(exam.items[i]))}</p>
      <div class="filters verdicts">
        <button type="button" data-verdict="juste">Juste</button>
        <button type="button" data-verdict="partiel">À moitié</button>
        <button type="button" data-verdict="faux">Faux</button>
      </div>
    </li>`).join("");
  $("#exam-manual").hidden = false;
  $("#exam-manual-done").hidden = false;
  updateManualDone();
}
$("#exam-manual").addEventListener("click", (e) => {
  const button = e.target.closest("[data-verdict]");
  if (!button) return;
  const li = button.closest("[data-grade-item]");
  state.exam.results[li.dataset.gradeItem] = { verdict: button.dataset.verdict, by: "you" };
  li.querySelectorAll("[data-verdict]").forEach((b) => b.classList.toggle("active", b === button));
  updateManualDone();
});
function updateManualDone() {
  const left = state.exam.results.filter((r) => r.verdict === null).length;
  $("#exam-manual-done").disabled = left > 0;
  $("#exam-manual-done").textContent = left ? `Encore ${left} à corriger` : "Voir ma note";
}
$("#exam-manual-done").addEventListener("click", showExamResult);

// ---- La note sur 20 et la correction ----
const VERDICT_POINTS = { juste: 1, partiel: 0.5, faux: 0 };
const VERDICT_NAMES = { juste: "Juste", partiel: "À moitié", faux: "Faux" };

function examScore() {
  const exam = state.exam;
  const points = exam.results.reduce((n, r) => n + VERDICT_POINTS[r.verdict], 0);
  return { points, total: exam.items.length, score: Math.round((points / exam.items.length) * 200) / 10 };
}

async function showExamResult() {
  const exam = state.exam;
  $("#exam-grading").hidden = true;
  $("#exam-result").hidden = false;
  exam.filter = "all";
  renderExamResult();
  window.scrollTo(0, 0);
  if (exam.recorded) return;
  exam.recorded = true;
  // Le suivi : réponses aux questions de quiz, et les flashcards (juste = sue, à moitié, faux = à revoir).
  const byQuiz = {};
  exam.items.forEach((item, i) => {
    const verdict = exam.results[i].verdict;
    if (item.kind === "question") (byQuiz[item.quiz_id] ||= []).push({ index: item.index, correct: verdict === "juste" });
    else api(`/api/courses/${item.course_id}/cards/${item.card.id}/review`,
      jsonBody("POST", { rating: { juste: "good", partiel: "hard", faux: "again" }[verdict] })).catch(() => {});
  });
  Object.entries(byQuiz).forEach(([quizId, answers]) => api(`/api/quizzes/${quizId}/answers`, jsonBody("POST", { answers })).catch(() => {}));
  const { score, points, total } = examScore();
  try {
    exam.saved = await api(`/api/courses/${exam.course.id}/partiels`, jsonBody("POST", { score, points, total, duration: exam.duration }));
  } catch {}
}

function renderExamResult() {
  const exam = state.exam;
  const { score } = examScore();
  const count = (v) => exam.results.filter((r) => r.verdict === v).length;
  $("#exam-score").textContent = `${formatScore(score)} / 20`;
  $("#exam-summary").innerHTML = [`<span class="ok-text">${count("juste")} juste${count("juste") > 1 ? "s" : ""}</span>`,
    count("partiel") ? `${count("partiel")} à moitié` : "", `<span class="ko-text">${count("faux")} faux</span>`,
    `<span class="muted">en ${formatDuration(exam.duration)}</span>`].filter(Boolean).join(" · ");
  document.querySelectorAll("[data-exam-filter]").forEach((b) => b.classList.toggle("active", b.dataset.examFilter === exam.filter));
  const rows = exam.items.map((item, i) => ({ item, i, r: exam.results[i] }))
    .filter(({ r }) => exam.filter === "all" || r.verdict !== "juste");
  $("#exam-review").innerHTML = rows.length ? rows.map(({ item, i, r }) => {
    const given = String(exam.answers[i] || "").trim();
    const q = item.kind === "question" ? item.question : null;
    const contestable = r.by === "ai" || r.by === "you";
    return `<li class="${r.verdict === "juste" ? "ok" : r.verdict === "partiel" ? "half" : "ko"}" value="${i + 1}">
      <p class="q">${escapeHtml(examPrompt(item))}${item.kind === "card" ? ` <span class="muted small-text">· flashcard</span>` : ""}</p>
      <p class="${r.verdict === "juste" ? "given-ok" : "given-ko"}">${VERDICT_NAMES[r.verdict]} · Ta réponse : ${given ? escapeHtml(given) : "<em>pas de réponse</em>"}</p>
      ${r.verdict === "juste" && q?.choices?.length ? "" : `<p>Réponse attendue : <strong>${escapeHtml(examExpected(item))}</strong></p>`}
      ${r.reason ? `<p class="ai-reason"><span>Correction de l'IA</span>${escapeHtml(r.reason)}</p>` : ""}
      ${q?.explanation ? `<p class="muted">${escapeHtml(cleanExplanation(q.explanation))}</p>` : ""}
      ${sourceHtml(q || item.card)}
      <p class="question-tools">${explainButton({ course_id: exam.course.id, question: examPrompt(item), expected: examExpected(item), given, source: examSource(item) })}</p>
      ${contestable ? `<div class="contest"><span class="muted small-text">${r.by === "ai" ? "Pas d'accord ?" : "Changer :"}</span>
        <div class="filters verdicts">${["juste", "partiel", "faux"].map((v) =>
          `<button type="button" data-contest="${i}" data-verdict="${v}" class="${r.verdict === v ? "active" : ""}">${VERDICT_NAMES[v]}</button>`).join("")}</div></div>` : ""}
    </li>`;
  }).join("") : `<li class="empty muted">Aucune erreur : tout était juste.</li>`;
  $("#exam-redo").hidden = !exam.results.some((r) => r.verdict !== "juste");
}

document.querySelectorAll("[data-exam-filter]").forEach((b) => b.addEventListener("click", () => {
  state.exam.filter = b.dataset.examFilter;
  renderExamResult();
}));
$("#exam-review").addEventListener("click", (e) => {
  const button = e.target.closest("[data-contest]");
  if (!button) return;
  const exam = state.exam;
  const result = exam.results[button.dataset.contest];
  if (result.verdict === button.dataset.verdict) return;
  result.verdict = button.dataset.verdict;
  result.contested = true;
  renderExamResult();
  const { score, points, total } = examScore();
  if (exam.saved) api(`/api/courses/${exam.course.id}/partiels`, jsonBody("POST", { id: exam.saved.id, score, points, total })).catch(() => {});
});
$("#exam-redo").addEventListener("click", () => {
  const exam = state.exam;
  const items = exam.items.filter((_, i) => exam.results[i].verdict !== "juste");
  runSession(shuffle(items), { title: "Mes erreurs du partiel", back: `#/reviser/cours/${exam.course.id}` });
});
$("#exam-done").addEventListener("click", () => go(`#/reviser/cours/${state.exam.course.id}`));

// Clavier pendant l'épreuve : ← / → pour changer de question, 1 à 4 pour choisir une proposition.
document.addEventListener("keydown", (e) => {
  if ($("#view-exam").hidden || $("#exam-run").hidden || !state.exam?.running || document.querySelector("dialog[open]")) return;
  if (["TEXTAREA", "INPUT"].includes(e.target.tagName) && e.target.type !== "radio") return;
  if (e.key === "ArrowRight" && !$("#exam-next").disabled) { e.preventDefault(); $("#exam-next").click(); }
  else if (e.key === "ArrowLeft" && !$("#exam-prev").disabled) { e.preventDefault(); $("#exam-prev").click(); }
  else if (/^[1-9]$/.test(e.key)) document.querySelectorAll("input[name=exam-choice]")[e.key - 1]?.click();
});

// ---------- Explique-moi : l'IA réexplique à partir du passage du cours ----------
const explainPayloads = new Map();
let explainCounter = 0;
function explainButton(payload) {
  const id = String(++explainCounter);
  explainPayloads.set(id, payload);
  return `<button class="link-button explain-btn" type="button" data-explain="${id}">Explique-moi</button>`;
}

document.addEventListener("click", async (e) => {
  const button = e.target.closest("[data-explain]");
  if (!button) return;
  const payload = explainPayloads.get(button.dataset.explain);
  const holder = button.closest("li, .question-tools") || button.parentElement;
  let box = holder.querySelector(".ai-explain");
  if (box && !box.classList.contains("loading")) return box.scrollIntoView({ block: "nearest" });
  if (!box) {
    box = document.createElement("div");
    box.className = "ai-explain loading";
    (button.closest(".question-tools") || button).after(box);
  }
  box.innerHTML = `<span>L'IA explique</span><p class="muted">Pirouette relit le passage du cours…</p>`;
  button.disabled = true;
  try {
    const { explanation } = await api("/api/explain", jsonBody("POST", {
      ...payload, model: state.config?.local?.default_model || "",
    }));
    box.innerHTML = `<span>L'IA explique</span><p>${escapeHtml(explanation)}</p>`;
    box.classList.remove("loading");
  } catch (err) {
    box.innerHTML = `<span>L'IA explique</span><p class="error-text">${escapeHtml(err.message)}</p>`;
    box.classList.remove("loading");
    button.disabled = false;
    box.remove();
    alert(err.message);
  }
});

// ---------- Cours mis à jour : questions et cartes qui ne correspondent plus ----------
async function openOutdated() {
  const data = await api(`/api/courses/${state.course.id}/outdated`);
  state.outdated = data;
  const row = (key, title, sub) => `<li><label><input type="checkbox" data-outdated="${escapeHtml(key)}" checked>
    <span><strong>${escapeHtml(title)}</strong><small class="muted">${escapeHtml(sub)}</small></span></label></li>`;
  $("#outdated-list").innerHTML = data.questions.map((q) => row(q.key, q.question, `Question · ${q.quiz_title}`)).join("")
    + data.cards.map((c) => row(c.key, c.front, "Flashcard")).join("");
  $("#outdated-dialog").showModal();
}
$("#outdated-open").addEventListener("click", openOutdated);
$("#outdated-cancel").addEventListener("click", () => $("#outdated-dialog").close());

async function resolveOutdated(removeChecked) {
  const { questions, cards } = state.outdated;
  const checked = new Set([...document.querySelectorAll("[data-outdated]")].filter((c) => removeChecked && c.checked).map((c) => c.dataset.outdated));
  const body = {
    questions: questions.filter((q) => checked.has(q.key)).map((q) => ({ quiz_id: q.quiz_id, index: q.index })),
    cards: cards.filter((c) => checked.has(c.key)).map((c) => c.id),
    keep: [...questions, ...cards].filter((x) => !checked.has(x.key)).map((x) => x.key),
  };
  await api(`/api/courses/${state.course.id}/outdated`, jsonBody("POST", body));
  $("#outdated-dialog").close();
  state.deck = null;
  refreshCourse();
}
$("#outdated-form").addEventListener("submit", (e) => { e.preventDefault(); resolveOutdated(true); });
$("#outdated-keep").addEventListener("click", () => resolveOutdated(false));

// ---------- Semaine des partiels ----------
const dayLong = (iso) => new Date(`${iso}T00:00`).toLocaleDateString("fr-FR", { day: "numeric", month: "long" });
function daysUntil(iso) {
  return Math.round((new Date(`${iso}T00:00`) - new Date().setHours(0, 0, 0, 0)) / 86400000);
}
// « Partiels le 15 décembre · J-23 »
function examLabel(iso) {
  const days = daysUntil(iso);
  if (days <= 0 && days > -7) return "Partiels cette semaine";
  if (days <= -7) return `Partiels passés (${dayLong(iso)})`;
  return `Partiels le ${dayLong(iso)} · J-${days}`;
}
function examSentence(exam, name = "") {
  const who = name ? ` <span class="muted">(${escapeHtml(name)})</span>` : "";
  if (exam.days <= 0) return `<b>Semaine de partiels en cours</b>${who} : les cartes reviennent chaque jour.`;
  return `<b>Partiels dans ${plural(exam.days, "jour", "jours")}</b>, le ${dayLong(exam.date)}${who}. Les révisions s'organisent pour que tout soit revu avant.`;
}

function openExamDialog({ folder = null, course = null }) {
  state.examTarget = { folder, course };
  const own = folder ? folder.exam_week : course.exam_week;
  $("#exam-date").value = own || "";
  const inherited = course && !own && course.exam?.from === "dossier" ? course.exam.date : null;
  $("#exam-inherit").hidden = !inherited;
  if (inherited) $("#exam-inherit").textContent = `Pour l'instant, ce cours suit la date de son semestre : ${dayLong(inherited)}. Une date ici la remplace pour ce cours seulement.`;
  $("#exam-dialog-text").textContent = (folder
    ? `Premier jour de la semaine de partiels pour tous les cours du semestre « ${folder.name} ». `
    : "Indique le premier jour de ta semaine de partiels. ")
    + "Les révisions s'organisent pour que tout soit revu avant, de plus en plus souvent à l'approche, puis chaque jour pendant la semaine.";
  $("#exam-clear").hidden = !own;
  $("#exam-status").hidden = true;
  $("#exam-dialog").showModal();
}

async function saveExam(day) {
  const { folder, course } = state.examTarget;
  try {
    if (folder) await api(`/api/folders/${folder.id}`, jsonBody("PATCH", { exam_week: day }));
    else await api(`/api/courses/${course.id}/exam`, jsonBody("PUT", { date: day }));
    $("#exam-dialog").close();
    if (folder) loadCourses();
    else refreshCourse();
  } catch (err) {
    setStatus("#exam-status", err.message, false);
  }
}
$("#exam-form").addEventListener("submit", (e) => {
  e.preventDefault();
  if (!$("#exam-date").value) return setStatus("#exam-status", "Choisis une date.", false);
  saveExam($("#exam-date").value);
});
$("#exam-clear").addEventListener("click", () => saveExam(""));
$("#exam-cancel").addEventListener("click", () => $("#exam-dialog").close());
$("#course-exam").addEventListener("click", () => {
  $("#course-more-menu").hidden = true;
  openExamDialog({ course: state.course });
});
$("#course-exam-chip").addEventListener("click", () => openExamDialog({ course: state.course }));

// ---------- Mises à jour de l'app ----------
// Au lancement : s'il existe une version plus récente, une petite fenêtre propose de l'installer en un clic.
const UPDATE_ERRORS = {
  offline: "Impossible de vérifier : pas de connexion à Internet ?",
  private: "Les versions sont publiées sur un dépôt GitHub privé : Pirouette ne peut pas les voir toute seule. "
    + "Rends le dépôt public (ou publie les versions ailleurs) pour activer les mises à jour automatiques.",
  unavailable: "GitHub ne répond pas pour l'instant. Réessaie plus tard.",
};

async function checkUpdate({ quiet = false } = {}) {
  let info;
  try {
    info = await api("/api/update");
  } catch (err) {
    if (!quiet) setStatus("#update-status", err.message, false);
    return;
  }
  state.update = info;
  state.desktop = Boolean(info.desktop);
  if (info.ready) {
    if (!quiet) setStatus("#update-status", "La mise à jour est prête : quitte Pirouette puis rouvre-la.", true);
    return showUpdateReady({ popup: !quiet });
  }
  if (!quiet) {
    if (info.error) setStatus("#update-status", UPDATE_ERRORS[info.error] || info.error, false);
    else setStatus("#update-status", info.available ? `La version ${info.latest} est disponible.` : "Tu as la dernière version.", true);
  }
  if (info.available) showUpdateToast(info);
}

function showUpdateToast(info) {
  let dismissed = null;
  try { dismissed = sessionStorage.getItem("pirouette.updateLater"); } catch {}
  if (dismissed === info.latest && $("#view-settings").hidden) return;
  $("#update-text").innerHTML = `<b>Pirouette ${escapeHtml(info.latest)}</b> est disponible.`
    + (info.can_install ? " Tes cours et tes cartes sont gardés." : "");
  $("#update-go").textContent = info.can_install ? "Mettre à jour" : "Télécharger";
  $("#update-go").disabled = false;
  $("#update-go").hidden = false;
  $("#update-later").hidden = false;
  $("#update-later").textContent = "Plus tard";
  $("#update-progress").hidden = true;
  $("#update-toast").hidden = false;
}

$("#update-later").addEventListener("click", () => {
  try { sessionStorage.setItem("pirouette.updateLater", state.update?.latest || ""); } catch {}
  $("#update-toast").hidden = true;
});

// Quitter Pirouette (pour que la mise à jour s'installe). Le serveur de l'app arrête le programme lui-même :
// fermer la fenêtre depuis l'interface pouvait laisser l'app bloquée (il fallait forcer à quitter).
function quitApp() {
  $("#update-text").textContent = "Pirouette se ferme…";
  fetch("/api/quit", { method: "POST" }).catch(() => {});
  setTimeout(() => window.pywebview?.api?.quit?.(), 300);
}

// La nouvelle version est téléchargée : on ne relance pas l'app nous-mêmes (source de bugs). Une fenêtre invite
// à quitter puis rouvrir ; « Plus tard » laisse un rappel discret en bas de l'écran.
function showUpdateReady({ popup = true } = {}) {
  $("#update-text").innerHTML = "<b>La mise à jour est prête.</b> Quitte Pirouette (⌘Q) puis rouvre-la.";
  $("#update-progress").hidden = true;
  $("#update-go").hidden = !state.desktop;
  $("#update-go").disabled = false;
  $("#update-go").textContent = "Quitter Pirouette";
  $("#update-later").hidden = false;
  $("#update-later").textContent = "Plus tard";
  $("#update-toast").hidden = popup;
  if (!popup) return;
  $("#update-dialog-text b").textContent = state.update?.latest || "";
  $("#update-dialog-quit").hidden = !state.desktop;
  if (!$("#update-dialog").open) $("#update-dialog").showModal();
}
$("#update-dialog-quit").addEventListener("click", quitApp);
$("#update-dialog-later").addEventListener("click", () => {
  $("#update-dialog").close();
  $("#update-toast").hidden = false;
});

$("#update-go").addEventListener("click", async () => {
  const info = state.update;
  if (info.ready) {
    quitApp();
    return;
  }
  if (!info.can_install) {
    window.open(info.page, "_blank");
    return;
  }
  $("#update-go").disabled = true;
  $("#update-later").hidden = true;
  $("#update-progress").hidden = false;
  $("#update-text").textContent = "Téléchargement de la nouvelle version…";
  try {
    const response = await fetch("/api/update/install", jsonBody("POST", { url: info.url }));
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `Erreur ${response.status}`);
    for await (const event of ndjson(response)) {
      if (event.type === "progress") $("#update-bar").style.width = `${event.percent}%`;
      if (event.type === "status") $("#update-text").textContent = event.message;
      if (event.type === "error") throw new Error(event.message);
      if (event.type === "done") {
        info.ready = true;
        return showUpdateReady();
      }
    }
  } catch (err) {
    $("#update-text").textContent = err.message;
    $("#update-go").disabled = false;
    $("#update-go").textContent = "Réessayer";
    $("#update-later").hidden = false;
  }
});

$("#update-check").addEventListener("click", () => checkUpdate());
setTimeout(() => checkUpdate({ quiet: true }), 2500);
api("/api/settings").then((settings) => { state.desktop = Boolean(settings.desktop); }).catch(() => {});

// Menu « Mise à jour » de la barre des menus du Mac (voir desktop.py) : il appelle window.pirouetteMenu(…).
function updateNotice(html, { install = false } = {}) {
  $("#update-text").innerHTML = html;
  $("#update-progress").hidden = true;
  $("#update-go").hidden = !install;
  $("#update-go").disabled = false;
  $("#update-go").textContent = state.update?.can_install ? "Mettre à jour" : "Télécharger";
  $("#update-later").hidden = false;
  $("#update-later").textContent = install ? "Plus tard" : "OK";
  $("#update-toast").hidden = false;
}

window.pirouetteMenu = async (action) => {
  const version = (await api("/api/settings").catch(() => ({}))).version || "";
  if (action === "version") {
    return updateNotice(`Tu utilises <b>Pirouette ${escapeHtml(version)}</b>.`);
  }
  updateNotice("Recherche d'une mise à jour…");
  $("#update-later").hidden = true;
  let info;
  try {
    info = await api("/api/update");
  } catch (err) {
    return updateNotice(escapeHtml(err.message));
  }
  state.update = info;
  state.desktop = Boolean(info.desktop);
  if (info.ready) return showUpdateReady();
  if (info.error) return updateNotice(escapeHtml(UPDATE_ERRORS[info.error] || info.error));
  if (!info.available) return updateNotice(`Tu as la dernière version : <b>Pirouette ${escapeHtml(info.current)}</b>.`);
  updateNotice(`<b>Pirouette ${escapeHtml(info.latest)}</b> est disponible (tu as la ${escapeHtml(info.current)}). Tes cours et tes cartes sont gardés.`,
    { install: true });
  if (action === "install" && info.can_install) $("#update-go").click();
};

// ---------- Suivi des quiz créés en arrière-plan (en bas à gauche, sur toutes les pages) ----------

async function refreshJobs() {
  let list;
  try { list = await api("/api/jobs"); } catch { return; }
  const finishedNow = list.filter((j) => j.status === "done" && !jobsState.seen.has(j.id) && jobsState.list.some((o) => o.id === j.id && o.status !== "done"));
  list.filter((j) => j.status === "done").forEach((j) => jobsState.seen.add(j.id));
  jobsState.list = list;
  renderJobs();
  // Un quiz vient d'être prêt : la liste du cours affiché se met à jour.
  if (finishedNow.length && state.course && !$("#view-course").hidden && finishedNow.some((j) => j.course_id === state.course.id)) {
    state.deck = null;
    refreshCourse();
  }
  const busy = list.some((j) => j.status === "queued" || j.status === "running");
  clearTimeout(jobsState.timer);
  if (busy) jobsState.timer = setTimeout(refreshJobs, 1500);
}

function renderJobs() {
  const list = jobsState.list;
  $("#jobs").hidden = !list.length;
  if (!list.length) return;
  const busy = list.filter((j) => j.status === "queued" || j.status === "running").length;
  const done = list.filter((j) => j.status === "done").length;
  const paused = list.filter((j) => j.status === "paused").length;
  const waiting = paused ? ` · ${paused} à décider` : "";
  $("#jobs-title").textContent = busy
    ? `Création en cours · ${busy} restant${busy > 1 ? "s" : ""}${done ? ` · ${done} prêt${done > 1 ? "s" : ""}` : ""}${waiting}`
    : `${done || !paused ? `${done} prêt${done > 1 ? "s" : ""}` : ""}${done ? waiting : waiting.slice(3)}${list.some((j) => j.status === "error") ? " · erreur" : ""}${
      list.some((j) => j.status === "cancelled") ? " · annulé" : ""}`;
  $("#jobs-heat").hidden = !busy || !jobsState.open;
  $("#jobs-foot").hidden = busy < 2 || !jobsState.open;
  $("#jobs-dot").className = `jobs-dot ${busy ? "busy" : paused ? "wait" : "ready"}`;
  $("#jobs").classList.toggle("open", jobsState.open);
  $("#jobs-list").innerHTML = list.map((j) => `
    <li class="job ${j.status}">
      <span class="job-state" aria-hidden="true"></span>
      <span class="job-main">
        <strong>${j.kind === "cards" ? "Flashcards · " : "Quiz · "}${escapeHtml(j.status === "done" ? j.result.title : j.label)}</strong>
        <small class="muted" title="${escapeHtml(j.message)}">${j.status === "paused" ? "" : `${escapeHtml(j.course_name)} · `}${escapeHtml(j.status === "queued" ? "en attente" : j.message)}</small>
      </span>
      ${j.status === "done" && j.kind !== "cards" ? `<button class="primary small" type="button" data-job-start="${j.result.quiz_id}" data-job="${j.id}">Commencer</button>` : ""}
      ${j.status === "done" && j.kind === "cards" ? `<button class="ghost small" type="button" data-job-cards="${j.course_id}" data-job="${j.id}">Voir</button>` : ""}
      ${j.status === "queued" || j.status === "running" ? `<button class="ghost small" type="button" data-job-cancel="${j.id}">Annuler</button>` : ""}
      ${j.status === "paused" ? `<button class="primary small" type="button" data-job-retry="${j.id}" data-variant="${j.kind === "cards" ? "" : "facile"}"
        title="${j.kind === "cards" ? "Relancer la création" : "Créer des questions plus faciles et les ajouter au quiz du chapitre"}">${j.kind === "cards" ? "Réessayer" : "Plus faciles"}</button>` : ""}
      ${["done", "error", "cancelled", "paused"].includes(j.status) ? `<button class="icon" type="button" data-job-dismiss="${j.id}" aria-label="Retirer de la liste" title="Retirer">✕</button>` : ""}
    </li>`).join("");
}

$("#jobs-head").addEventListener("click", () => { jobsState.open = !jobsState.open; renderJobs(); });
$("#jobs-list").addEventListener("click", async (e) => {
  const cards = e.target.closest("[data-job-cards]");
  if (cards) {
    await api(`/api/jobs/${cards.dataset.job}`, { method: "DELETE" }).catch(() => {});
    state.deck = null;
    go(`#/cours/${cards.dataset.jobCards}/cartes`);
    return refreshJobs();
  }
  const retry = e.target.closest("[data-job-retry]");
  if (retry) {
    retry.disabled = true;
    await api(`/api/jobs/${retry.dataset.jobRetry}/retry`, jsonBody("POST", { variant: retry.dataset.variant })).catch((err) => alert(err.message));
    return refreshJobs();
  }
  const cancel = e.target.closest("[data-job-cancel]")?.dataset.jobCancel;
  if (cancel) {
    await api(`/api/jobs/${cancel}/cancel`, { method: "POST" }).catch(() => {});
    return refreshJobs();
  }
  const start = e.target.closest("[data-job-start]");
  const dismiss = e.target.closest("[data-job-dismiss]")?.dataset.jobDismiss;
  if (start) {
    const quiz = await api(`/api/quizzes/${start.dataset.jobStart}`).catch((err) => alert(err.message));
    if (!quiz) return;
    await api(`/api/jobs/${start.dataset.job}`, { method: "DELETE" }).catch(() => {});
    if (quiz.course_id && state.course?.id !== quiz.course_id) state.course = await api(`/api/courses/${quiz.course_id}`);
    startQuiz(quiz, undefined, { back: null });
    refreshJobs();
  }
  if (dismiss) {
    await api(`/api/jobs/${dismiss}`, { method: "DELETE" }).catch(() => {});
    refreshJobs();
  }
});
$("#jobs-cancel-all").addEventListener("click", async () => {
  if (!confirm("Annuler toutes les créations en cours et en attente ? Rien de ce qui n'est pas terminé ne sera gardé.")) return;
  await api("/api/jobs/cancel", { method: "POST" }).catch(() => {});
  refreshJobs();
});
refreshJobs();

// ---------- « Un problème avec cette question ? » : signaler ou supprimer, rangés derrière une petite bulle ----------
function qtoolsHtml(items) {
  return `<span class="qtools">
    <button class="icon qtools-btn" type="button" aria-label="Un problème avec cette question ?" title="Un problème avec cette question ?">${ICON_ALERT}</button>
    <span class="plus-menu qtools-menu" hidden>${items.join("")}</span>
  </span>`;
}
document.addEventListener("click", (e) => {
  const toggle = e.target.closest(".qtools-btn");
  document.querySelectorAll(".qtools-menu").forEach((menu) => {
    if (!toggle || menu !== toggle.nextElementSibling) menu.hidden = true;
  });
  if (toggle) {
    const menu = toggle.nextElementSibling;
    menu.hidden = !menu.hidden;
  }
}, true);

// ---------- Au dépôt d'un cours : tout préparer (un quiz et des flashcards par chapitre) ----------
function prepareDismissed(id) {
  try { return JSON.parse(localStorage.getItem("pirouette.prepareLater") || "[]").includes(id); } catch { return false; }
}
// Case décochée : son nombre (questions ou cartes) est grisé.
[["#prepare-quizzes", "#prepare-nq"], ["#prepare-cards", "#prepare-nc"]].forEach(([box, count]) =>
  $(box).addEventListener("change", () => { $(count).disabled = !$(box).checked; }));

function renderPrepare(course, tab) {
  const units = course.files.length ? chapterUnits(course) : [];
  const fresh = !course.quizzes.length && !course.cards.total && !prepareDismissed(course.id);
  const show = units.length > 0 && (tab === null || tab === "fichiers") && (state.offerPrepare === course.id || fresh);
  $("#prepare-panel").hidden = !show;
  if (!show) return;
  $("#prepare-text").textContent = units.length > 1
    ? `${units.length} chapitres repérés. Pirouette peut tout créer maintenant, chapitre par chapitre :`
    : "Pirouette peut tout créer maintenant pour ce cours :";
  showError("#prepare-error", "");
}
$("#prepare-later").addEventListener("click", () => {
  try {
    const list = JSON.parse(localStorage.getItem("pirouette.prepareLater") || "[]");
    localStorage.setItem("pirouette.prepareLater", JSON.stringify([...list, state.course.id]));
  } catch {}
  state.offerPrepare = null;
  $("#prepare-panel").hidden = true;
});
$("#prepare-go").addEventListener("click", async () => {
  const quizzes = $("#prepare-quizzes").checked, cards = $("#prepare-cards").checked;
  if (!quizzes && !cards) return showError("#prepare-error", "Coche au moins les quiz ou les flashcards.");
  const form = new FormData();
  form.append("provider", "local");
  form.append("model", state.config?.local?.default_model || "");
  form.append("quizzes", quizzes ? "1" : "");
  form.append("cards", cards ? "1" : "");
  form.append("num_questions", $("#prepare-nq").value || "0");
  form.append("cards_count", $("#prepare-nc").value || "0");
  try {
    await api(`/api/courses/${state.course.id}/prepare`, { method: "POST", body: form });
    state.offerPrepare = null;
    $("#prepare-panel").hidden = true;
    jobsState.open = true;
    refreshJobs();
  } catch (err) {
    showError("#prepare-error", err.message);
  }
});
