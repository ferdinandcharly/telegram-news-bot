  let filtreCourant = "Tout";
  let userDomaines  = [];
  let userLangue    = "multi";

  function filtrerParPrefs(alertes) {
    if (!userDomaines.length) return alertes;
    return alertes.filter(a =>
      userDomaines.some(d => (a.domaine || "").includes(d.replace(/^\S+\s/, "")))
    );
  }

  function esc(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  let savedIds = new Set();
  let alerteModal = null;

  // ── Badge non-lus ────────────────────────────────────────────────────────
  function majBadgeNonLus() {
    const lastVisit = localStorage.getItem("lastVisit") || "1970-01-01";
    const nonLus = alertesCache.filter(a => (a.date || "") > lastVisit).length;
    const badge = document.getElementById("badge-nonlus");
    if (!badge) return;
    if (nonLus > 0) {
      badge.textContent = nonLus > 99 ? "99+" : nonLus;
      badge.style.display = "flex";
    } else {
      badge.style.display = "none";
    }
  }

  function marquerLus() {
    localStorage.setItem("lastVisit", new Date().toISOString());
    const badge = document.getElementById("badge-nonlus");
    if (badge) badge.style.display = "none";
  }

  // ── Navigation ──────────────────────────────────────────────────────────
  function afficherPage(page, btn) {
    document.activeElement?.blur();
    ["feed","corr","saved","params"].forEach(p => {
      document.getElementById("page-" + p).style.display = "none";
    });
    document.getElementById("page-" + page).style.display = "block";
    document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("actif"));
    btn.classList.add("actif");
    if (page === "feed")  marquerLus();
    if (page === "saved") chargerSauvegardes();
    if (page === "corr")  chargerCorrelations();
  }

  // ── Recherche ────────────────────────────────────────────────────────────
  function toggleRecherche() {
    const zone = document.getElementById("zone-recherche");
    const input = document.getElementById("recherche");
    const btn = document.getElementById("btn-recherche");
    const open = zone.style.display === "none";
    zone.style.display = open ? "block" : "none";
    btn.classList.toggle("actif", open);
    if (open) { input.focus(); }
    else { input.value = ""; chargerAlertes(); }
  }

  // ── Filtres ─────────────────────────────────────────────────────────────
  function setFiltreTout() {
    filtreCourant = "Tout";
    document.getElementById("btn-tout").classList.add("actif");
    document.getElementById("filtre-select").classList.remove("actif");
    document.getElementById("filtre-select").value = "";
    chargerAlertes();
  }

  function setFiltreSelect(sel) {
    if (!sel.value) { setFiltreTout(); return; }
    filtreCourant = sel.value;
    document.getElementById("btn-tout").classList.remove("actif");
    sel.classList.add("actif");
    chargerAlertes();
  }

  // ── Helpers ─────────────────────────────────────────────────────────────
  function formatHeure(iso) {
    const diff = Math.floor((Date.now() - new Date(iso)) / 60000);
    if (diff < 1)    return "À l'instant";
    if (diff < 60)   return diff + " min";
    if (diff < 1440) return Math.floor(diff / 60) + "h";
    return new Date(iso).toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
  }

  const DOMAINES_MAP = [
    { key: "Géo",          cls: "geo",     label: "Géopolitique" },
    { key: "Science",      cls: "sci",     label: "Science"      },
    { key: "Tech",         cls: "tech",    label: "Tech & IA"    },
    { key: "Finance",      cls: "finance", label: "Finance"      },
    { key: "Environnement",cls: "env",     label: "Environnement"},
    { key: "Sport",        cls: "sport",   label: "Sport"        },
  ];

  function getDomaine(d) {
    return DOMAINES_MAP.find(m => d.includes(m.key)) || { cls: "geo", label: d };
  }

  function carteHTML(a, featured = false) {
    const dom     = getDomaine(a.domaine);
    const cls     = "carte-" + dom.cls;
    const saved       = savedIds.has(a.id);
    const niv         = a.niveau || 2;
    const sources     = a.sources || [];
    const critique    = niv >= 3 ? `<span class="badge-alerte">CRITIQUE</span>` : "";
    const clsCritique = niv >= 3 ? " carte-critique" : "";
    const sourceCount = sources.length > 1
      ? `<span class="source-count">${sources.length} sources</span>` : "";

    return `
      <div class="carte ${cls}${featured ? " carte-featured" : ""}${clsCritique}" onclick="ouvrirModal(${a.id})">
        <div class="carte-source-row">
          <span class="source-dot"></span>
          <span class="source-name">${esc(dom.label)}</span>
          <span class="source-sep">·</span>
          <span class="source-time">${formatHeure(a.date)}</span>
          ${critique}
        </div>
        <div class="carte-titre">${esc(userLangue === "fr" && a.titre_fr ? a.titre_fr : a.titre)}</div>
        ${a.accroche && dom.cls !== "sport" ? `<div class="carte-resume">${esc(a.accroche)}</div>` : ""}
        <div class="carte-footer">
          ${sourceCount}
          <div class="carte-actions">
            <button class="btn-save ${saved ? "saved" : ""}"
              onclick="event.stopPropagation(); toggleSave(${a.id}, this)">
              <ion-icon name="${saved ? "bookmark" : "bookmark-outline"}"></ion-icon>
            </button>
            <button class="btn-save"
              onclick="event.stopPropagation(); partagerAlerte(${a.id})">
              <ion-icon name="share-outline"></ion-icon>
            </button>
          </div>
        </div>
      </div>`;
  }

  // ── Alertes ─────────────────────────────────────────────────────────────
  async function chargerAlertes() {
    const url = filtreCourant === "Tout"
      ? "/api/alertes"
      : `/api/alertes?domaine=${encodeURIComponent(filtreCourant)}`;
    const raw  = await fetch(url).then(r => r.json());
    let alertes = filtreCourant === "Tout" ? filtrerParPrefs(raw) : raw;

    const q = (document.getElementById("recherche")?.value || "").trim().toLowerCase();
    if (q) {
      alertes = alertes.filter(a =>
        (a.titre    || "").toLowerCase().includes(q) ||
        (a.accroche || "").toLowerCase().includes(q) ||
        (a.contexte || "").toLowerCase().includes(q) ||
        (a.domaine  || "").toLowerCase().includes(q)
      );
    }

    const feed = document.getElementById("feed");
    if (!alertes.length) {
      feed.innerHTML = `<div class="vide">${q ? "Aucun résultat pour « " + esc(q) + " »." : "Aucune alerte pour le moment.<br>Vérification toutes les 15 min."}</div>`;
    } else {
      let html = "";
      alertes.forEach((a, i) => {
        html += carteHTML(a, i === 0);
      });
      feed.innerHTML = html;
    }
  }

  async function chargerStats() {
    const s = await fetch("/api/stats").then(r => r.json());
    document.getElementById("stat-total").textContent = s.total;
    document.getElementById("stat-jour").textContent  = s.aujourd_hui;
  }

  // ── Corrélations ────────────────────────────────────────────────────────
  const CORR_DOMAIN_COLORS = {
    "Géopolitique": "var(--geo)", "Science": "var(--sci)", "Tech": "var(--tech)",
    "Finance": "var(--finance)", "Environnement": "var(--env)", "Sport": "var(--sport)"
  };

  async function chargerCorrelations() {
    const feed = document.getElementById("feed-corr");
    feed.innerHTML = `<div class="vide">Chargement...</div>`;
    const corrs = await fetch("/api/correlations").then(r => r.json()).catch(() => []);

    if (!corrs.length) {
      feed.innerHTML = `<div class="vide">Aucune corrélation pour le moment.<br>Générée chaque matin à 8h.</div>`;
      return;
    }

    feed.innerHTML = corrs.map(c => {
      const domaines = c.domaines || [];
      const nb  = (c.alertes_ids || []).length;
      const d   = c.date ? new Date(c.date).toLocaleDateString("fr-FR", {day:"numeric",month:"short"}) : "";

      const domainsHtml = domaines.map(d => {
        const label = d.replace(/^\S+\s/, "");
        const color = CORR_DOMAIN_COLORS[label] || "var(--sub)";
        return `<span class="corr-domain-dot" style="color:${color}">${esc(label)}</span>`;
      }).join("") || `<span class="corr-domain-dot">Général</span>`;

      const corps = c.contexte
        ? `<div class="corr-section">
             <span class="corr-label">Pourquoi c'est lié</span>
             <span class="corr-text">${esc(c.contexte)}</span>
           </div>
           <div class="corr-section">
             <span class="corr-label">Ce que ça signifie</span>
             <span class="corr-text">${esc(c.analyse || "")}</span>
           </div>
           <div class="corr-section">
             <span class="corr-label">À surveiller</span>
             <span class="corr-text">${esc(c.implication || "")}</span>
           </div>`
        : `<div class="corr-section"><span class="corr-text">${esc(c.synthese || "")}</span></div>`;

      return `<div class="corr-card">
        <div class="corr-card-head">
          <div class="corr-card-meta">
            <div class="corr-domains">${domainsHtml}</div>
            <div style="display:flex;align-items:center;gap:8px">
              <span class="corr-nb">${nb} alerte${nb > 1 ? "s" : ""}</span>
              <span class="corr-date">${d}</span>
            </div>
          </div>
          <div class="corr-titre">${esc(c.titre || "")}</div>
        </div>
        <div class="corr-body">${corps}</div>
      </div>`;
    }).join("");
  }

  // ── Sauvegardés ─────────────────────────────────────────────────────────
  function labelDate(iso) {
    if (!iso) return "Avant";
    const d = new Date(iso);
    const now = new Date();
    const diff = Math.floor((now - d) / 86400000);
    if (diff === 0) return "Aujourd'hui";
    if (diff === 1) return "Hier";
    if (diff < 7)  return "Cette semaine";
    return d.toLocaleDateString("fr-FR", { day: "numeric", month: "long" });
  }

  function savedCarteHTML(a) {
    const dom   = getDomaine(a.domaine);
    const cls   = "carte-" + dom.cls;
    const saved = savedIds.has(a.id);
    const colorMap = { geo:"var(--geo)", sci:"var(--sci)", tech:"var(--tech)",
                       finance:"var(--finance)", env:"var(--env)", sport:"var(--sport)" };
    const color = colorMap[dom.cls] || "var(--sub)";
    return `<div class="saved-card ${cls}" onclick="ouvrirModal(${a.id})">
      <div class="saved-card-meta">
        <span class="saved-dot" style="background:${color}"></span>
        <span class="saved-domaine" style="color:${color}">${esc(dom.label)}</span>
        <span class="saved-time">${formatHeure(a.date)}</span>
      </div>
      <div class="saved-titre">${esc(userLangue === "fr" && a.titre_fr ? a.titre_fr : a.titre)}</div>
      ${a.accroche ? `<div class="saved-resume">${esc(a.accroche)}</div>` : ""}
      <div class="saved-actions">
        <button class="btn-save saved" onclick="event.stopPropagation(); toggleSave(${a.id}, this)">
          <ion-icon name="bookmark"></ion-icon>
        </button>
        <button class="btn-save" onclick="event.stopPropagation(); partagerAlerte(${a.id})">
          <ion-icon name="share-outline"></ion-icon>
        </button>
      </div>
    </div>`;
  }

  async function chargerSauvegardes() {
    const data = await fetch("/api/sauvegardes").then(r => r.json());
    const feed = document.getElementById("feed-saved");
    if (!data.length) {
      feed.innerHTML = `<div class="vide">Aucun article enregistré.<br>Appuie sur <ion-icon name="bookmark-outline" style="vertical-align:middle"></ion-icon> pour en conserver.</div>`;
      return;
    }
    let html = "";
    let lastLabel = null;
    data.forEach(a => {
      const label = labelDate(a.date);
      if (label !== lastLabel) {
        html += `<div class="saved-group-label">${label}</div>`;
        lastLabel = label;
      }
      html += carteHTML(a);
    });
    feed.innerHTML = html;
  }

  // ── Scroll hide filtres ───────────────────────────────────────────────────
  (() => {
    let lastY = 0;
    let ticking = false;
    window.addEventListener("scroll", () => {
      if (!ticking) {
        requestAnimationFrame(() => {
          const barre = document.getElementById("barre-filtres");
          if (!barre) { ticking = false; return; }
          const curr = window.scrollY;
          if (curr > lastY && curr > 60) {
            barre.classList.add("masque");
          } else {
            barre.classList.remove("masque");
          }
          lastY = curr;
          ticking = false;
        });
        ticking = true;
      }
    }, { passive: true });
  })();

  function partagerAlerte(id) {
    const url = `${location.origin}/a/${id}`;
    if (navigator.share) {
      navigator.share({ title: "News Alert", url });
    } else {
      navigator.clipboard.writeText(url).then(() => {
        const btn = event.target;
        const old = btn.textContent;
        btn.textContent = "✓";
        setTimeout(() => btn.textContent = old, 1500);
      });
    }
  }

  async function toggleSave(id, btn) {
    const estSaved = savedIds.has(id);
    const method   = estSaved ? "DELETE" : "POST";
    try {
      const r = await fetch(`/api/sauvegardes/${id}`, { method });
      if (!r.ok) return;
    } catch { return; }
    if (estSaved) { savedIds.delete(id); btn.innerHTML = '<ion-icon name="bookmark-outline"></ion-icon>'; btn.classList.remove("saved"); }
    else          { savedIds.add(id);    btn.innerHTML = '<ion-icon name="bookmark"></ion-icon>';         btn.classList.add("saved"); }
  }

  // ── Modal synthèse ───────────────────────────────────────────────────────
  let alertesCache = [];

  async function ouvrirModal(id) {
    alerteModal = alertesCache.find(a => a.id === id);
    if (!alerteModal) return;

    const dom = getDomaine(alerteModal.domaine);
    document.getElementById("modal-domaine").textContent = dom.label;
    document.getElementById("modal-domaine").className   = "modal-domaine modal-" + dom.cls;
    document.getElementById("modal-lien").href = esc(alerteModal.lien);
    document.getElementById("modal-titre").textContent    = alerteModal.titre;
    document.getElementById("modal-lien").href            = alerteModal.lien;
    document.getElementById("modal-synthese").innerHTML   =
      `<div class="loading">Génération en cours <div class="loading-dots"><span></span><span></span><span></span></div></div>`;

    const saved = savedIds.has(id);
    const saveBtn = document.getElementById("modal-save-btn");
    saveBtn.textContent = saved ? "Retirer des sauvegardés" : "Sauvegarder";
    saveBtn.className   = "btn-secondary" + (saved ? " saved" : "");

    document.getElementById("modal").classList.add("visible");

    try {
      const data = await fetch(`/api/synthese/${id}`).then(r => r.json());
      document.getElementById("modal-synthese").textContent = data.synthese || "Synthèse indisponible.";
    } catch {
      document.getElementById("modal-synthese").textContent = "Erreur lors de la génération.";
    }
  }

  async function toggleSaveModal() {
    if (!alerteModal) return;
    const id      = alerteModal.id;
    const estSaved = savedIds.has(id);
    const method   = estSaved ? "DELETE" : "POST";
    try {
      const r = await fetch(`/api/sauvegardes/${id}`, { method });
      if (!r.ok) return;
    } catch { return; }
    if (estSaved) { savedIds.delete(id); }
    else          { savedIds.add(id); }
    const saveBtn = document.getElementById("modal-save-btn");
    const nowSaved = savedIds.has(id);
    saveBtn.textContent = nowSaved ? "Retirer des sauvegardés" : "Sauvegarder";
    saveBtn.className   = "btn-secondary" + (nowSaved ? " saved" : "");
    chargerAlertes();
  }

  function fermerModal(e) {
    if (e.target === document.getElementById("modal")) {
      document.getElementById("modal").classList.remove("visible");
    }
  }

  // ── Paramètres domaines ──────────────────────────────────────────────────
  async function sauverDomaines() {
    const map = [
      ["toggle-geo",     "🌍 Géopolitique"],
      ["toggle-sci",     "🔬 Science"],
      ["toggle-tech",    "💻 Tech & IA"],
      ["toggle-finance", "💰 Finance"],
      ["toggle-env",     "🌱 Environnement"],
      ["toggle-sport",   "⚽ Sport"],
    ];
    const domaines = map
      .filter(([id]) => document.getElementById(id)?.checked)
      .map(([, label]) => label);
    userDomaines = domaines; // mise à jour locale immédiate

    await fetch("/api/preferences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domaines }),
    });
  }

  // ── Push notifications ───────────────────────────────────────────────────
  let VAPID_PUBLIC = "";

  function urlB64ToUint8Array(b64) {
    const pad = "=".repeat((4 - b64.length % 4) % 4);
    const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return new Uint8Array([...raw].map(c => c.charCodeAt(0)));
  }

  function getNiveauMin() {
    return document.getElementById("toggle-notif-important").checked ? 2 : 3;
  }

  async function abonner(niveauMin) {
    if (!VAPID_PUBLIC) {
      const data = await fetch("/api/vapid-public").then(r => r.json());
      VAPID_PUBLIC = data.key;
    }
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlB64ToUint8Array(VAPID_PUBLIC),
    });
    await fetch("/api/subscribe", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ subscription: sub.toJSON(), niveau_min: niveauMin })
    });
    return sub;
  }

  async function toggleNotifications() {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      document.getElementById("notif-status").textContent = "Non supportées par ce navigateur";
      return;
    }
    const btn = document.getElementById("btn-notif");
    if (btn.classList.contains("active")) {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        await fetch("/api/unsubscribe", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(sub.toJSON()) });
        await sub.unsubscribe();
      }
      btn.textContent = "Activer";
      btn.classList.remove("active");
      document.getElementById("notif-status").textContent = "Non activées";
      document.getElementById("row-notif-important").style.display = "none";
      return;
    }
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      document.getElementById("notif-status").textContent = "Permission refusée";
      return;
    }
    try {
      await abonner(getNiveauMin());
      btn.textContent = "Activées ✓";
      btn.classList.add("active");
      document.getElementById("notif-status").textContent = "Activées sur cet appareil";
      document.getElementById("row-notif-important").style.display = "flex";
    } catch (e) {
      document.getElementById("notif-status").textContent = "Erreur : " + e.message;
    }
  }

  async function updateNiveauNotif() {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (!sub) return;
    const niveauMin = getNiveauMin();
    await abonner(niveauMin);
    // sauvegarder aussi dans les préférences pour rechargement
    await fetch("/api/preferences", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ niveau_notif: niveauMin })
    });
  }

  async function initNotifications() {
    if (!("serviceWorker" in navigator)) return;
    await navigator.serviceWorker.register("/sw.js");
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub && Notification.permission === "granted") {
      const btn = document.getElementById("btn-notif");
      btn.textContent = "Activées ✓";
      btn.classList.add("active");
      document.getElementById("notif-status").textContent = "Activées sur cet appareil";
      document.getElementById("row-notif-important").style.display = "flex";
    }
  }


  // ── Thème ─────────────────────────────────────────────────────────────────
  function setTheme(t, save = true) {
    document.documentElement.setAttribute("data-theme", t);
    document.querySelectorAll(".btn-theme").forEach(b => {
      b.classList.toggle("actif", b.dataset.t === t);
    });
    if (save) {
      fetch("/api/preferences", { method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ theme: t }) });
    }
  }

  function toggleEditNom() {
    const texte  = document.getElementById("dn-texte");
    const input  = document.getElementById("dn-input");
    const btnEdit = document.getElementById("dn-btn-edit");
    const btnSave = document.getElementById("dn-btn-save");
    input.value = texte.textContent === "—" ? "" : texte.textContent;
    texte.style.display  = "none";
    btnEdit.style.display = "none";
    input.style.display  = "inline-block";
    btnSave.style.display = "inline-block";
    input.focus();
  }

  async function sauverNomEdit() {
    const input  = document.getElementById("dn-input");
    const texte  = document.getElementById("dn-texte");
    const btnEdit = document.getElementById("dn-btn-edit");
    const btnSave = document.getElementById("dn-btn-save");
    const val = input.value.trim();
    if (!val) return;
    await fetch("/api/preferences", { method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ display_name: val }) });
    texte.textContent = val;
    input.style.display  = "none";
    btnSave.style.display = "none";
    texte.style.display  = "inline";
    btnEdit.style.display = "inline-block";
  }

  function demanderSuppression() {
    document.getElementById("row-delete-compte").style.display = "none";
    const row = document.getElementById("row-confirm-delete");
    row.style.display = "flex";
    row.style.flexDirection = "column";
  }

  function annulerSuppression() {
    document.getElementById("row-confirm-delete").style.display = "none";
    document.getElementById("row-delete-compte").style.display = "flex";
  }

  async function confirmerSuppression() {
    const btn = document.querySelector("#row-confirm-delete button");
    btn.textContent = "Suppression…";
    btn.disabled = true;
    try {
      const r = await fetch("/api/delete-account", { method: "POST" });
      if (r.ok) { window.location.href = "/login"; }
      else { btn.textContent = "Erreur — réessaie"; btn.disabled = false; }
    } catch { btn.textContent = "Erreur — réessaie"; btn.disabled = false; }
  }

  async function resetMotDePasse() {
    const btn = document.getElementById("btn-reset-pwd");
    btn.textContent = "Envoi…";
    btn.disabled = true;
    try {
      await fetch("/api/reset-my-password", { method: "POST" });
      btn.textContent = "Email envoyé ✓";
    } catch {
      btn.textContent = "Erreur — réessaie";
      btn.disabled = false;
    }
  }

  async function sauverLangue(val) {
    userLangue = val;
    await fetch("/api/preferences", { method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ langue: val }) });
    chargerAlertes();
  }

  // ── Init ─────────────────────────────────────────────────────────────────
  async function init() {
    const data = await fetch("/api/init").then(r => r.json());

    // thème
    setTheme(data.preferences.theme || "dark", false);

    // nom d'affichage
    const dn = data.preferences.display_name;
    const dnTexte = document.getElementById("dn-texte");
    // email réel toujours affiché dans le bloc Compte
    document.getElementById("user-email").textContent = data.email || "—";
    // nom d'affichage dans le header et les champs
    if (dn) {
      if (dnTexte) dnTexte.textContent = dn;
    } else {
      if (dnTexte) dnTexte.textContent = data.email;
    }

    // langue
    userLangue = data.preferences.langue || "multi";
    const selectLangue = document.getElementById("select-langue");
    if (selectLangue) selectLangue.value = userLangue;

    // domaines préférés
    userDomaines = data.preferences.domaines || [];
    const map = [
      ["toggle-geo",     "🌍 Géopolitique"],
      ["toggle-sci",     "🔬 Science"],
      ["toggle-tech",    "💻 Tech & IA"],
      ["toggle-finance", "💰 Finance"],
      ["toggle-env",     "🌱 Environnement"],
      ["toggle-sport",   "⚽ Sport"],
    ];
    map.forEach(([id, label]) => {
      const el = document.getElementById(id);
      if (el) el.checked = data.preferences.domaines.includes(label);
    });

    // niveau notif important
    const niveauNotif = data.preferences.niveau_notif || 3;
    const toggleImp = document.getElementById("toggle-notif-important");
    if (toggleImp) toggleImp.checked = niveauNotif <= 2;

    // alertes & sauvegardes
    savedIds     = new Set(data.saved_ids);
    alertesCache = data.alertes;
    chargerAlertes();
    chargerStats();
    majBadgeNonLus();
  }

  async function checkNotifHash() {
    const match = window.location.hash.match(/#synthese\/(\d+)/);
    if (!match) return;
    const id = parseInt(match[1]);
    history.replaceState(null, "", "/");
    // attendre que les alertes soient chargées
    let tentatives = 0;
    while (!alertesCache.length && tentatives < 10) {
      await new Promise(r => setTimeout(r, 300));
      tentatives++;
    }
    ouvrirModal(id);
  }

  async function refreshToken() {
    try { await fetch("/api/refresh-token", { method: "POST" }); } catch {}
  }

  // Rafraîchir le token Supabase toutes les 50 min
  setInterval(refreshToken, 50 * 60 * 1000);
  // Rafraîchir au retour sur l'app (après mise en veille / changement d'onglet)
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refreshToken();
  });

  init().then(checkNotifHash);
  initNotifications();
  setInterval(async () => {
    const data = await fetch("/api/alertes").then(r => r.json());
    alertesCache = data;
    chargerAlertes();
    chargerStats();
    majBadgeNonLus();
  }, 30000);
