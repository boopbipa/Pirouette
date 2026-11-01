// Compatibilité avec les Mac restés sur un ancien macOS (l'app s'affiche avec le Safari du système) : ce que
// Safari 14 et 15 n'ont pas encore, ajouté ici avant le reste. Et si l'app ne démarre pas malgré tout, un message
// clair au lieu d'une page vide.
(function () {
  // .at(-1) : Safari 15.4
  for (const proto of [Array.prototype, String.prototype]) {
    if (!proto.at) {
      Object.defineProperty(proto, "at", {
        configurable: true, writable: true,
        value(i) { i = Math.trunc(i) || 0; if (i < 0) i += this.length; return i < 0 || i >= this.length ? undefined : this[i]; },
      });
    }
  }

  // <dialog> (fenêtres de confirmation, réglages, tutoriel…) : Safari 15.4. Une version simple : ouvrir, fermer,
  // valeur de retour, Échap, et un voile derrière.
  if (typeof HTMLDialogElement !== "function") {
    document.documentElement.classList.add("no-dialog");
    const patch = (d) => {
      if (d.showModal) return;
      d.returnValue = "";
      Object.defineProperty(d, "open", {
        get() { return this.hasAttribute("open"); },
        set(v) { if (v) this.setAttribute("open", ""); else this.removeAttribute("open"); },
      });
      d.show = d.showModal = function () { this.setAttribute("open", ""); this.classList.add("poly-modal"); };
      d.close = function (value) {
        if (!this.hasAttribute("open")) return;
        if (value !== undefined) this.returnValue = value;
        this.removeAttribute("open");
        this.classList.remove("poly-modal");
        this.dispatchEvent(new Event("close"));
      };
      let pressed = "";  // le bouton qui envoie le formulaire (event.submitter n'existe pas encore)
      d.addEventListener("click", (e) => {
        const b = e.target.closest && e.target.closest("button");
        if (b && (b.type || "submit") === "submit") pressed = b.value || "";
      }, true);
      d.addEventListener("submit", (e) => {
        if ((e.target.getAttribute("method") || "").toLowerCase() !== "dialog") return;
        e.preventDefault();
        d.close(pressed);
        pressed = "";
      });
    };
    const all = () => document.querySelectorAll("dialog").forEach(patch);
    all();
    new MutationObserver(all).observe(document.documentElement, { childList: true, subtree: true });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      const open = [...document.querySelectorAll("dialog[open]")].pop();
      if (!open) return;
      const cancel = new Event("cancel", { cancelable: true });
      if (open.dispatchEvent(cancel)) open.close();
    });
  }

  // L'app n'a pas démarré (une erreur au chargement) : on le dit, avec de quoi aider
  const errors = [];
  addEventListener("error", (e) => { errors.push(e.message || String(e.error || "erreur")); });
  addEventListener("load", () => setTimeout(() => {
    if (window.__pirouetteStarted || !errors.length) return;
    const box = document.createElement("div");
    box.className = "boot-error";
    box.innerHTML = "<b>Pirouette n'a pas pu s'afficher.</b><p>Ta version de macOS est peut-être trop ancienne pour cette version de "
      + "Pirouette : fais les mises à jour du Mac (menu Pomme › Réglages Système › Mise à jour de logiciels), puis rouvre "
      + "Pirouette. Si ça continue, envoie ce message à l'équipe :</p><code></code>";
    box.querySelector("code").textContent = errors.slice(0, 3).join(" · ") + " — " + navigator.userAgent;
    document.body.append(box);
    try { window.pywebview && window.pywebview.api && window.pywebview.api.ready(); } catch (_) { /* fenêtre déjà visible */ }
  }, 1500));
})();
