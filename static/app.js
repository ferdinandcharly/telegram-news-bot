  let filtreCourant = "Tout";
  let userDomaines  = [];
  let userLangue    = "multi";
  let userPortees   = {};
  let userPays      = "France";
  let sigFeed       = "";   // signature des alertes affichées (évite un re-render inutile)

  // empreinte du cache : si elle ne change pas, inutile de ré-afficher (clignotement)
  function signatureAlertes() {
    return alertesCache.map(a => `${a.id}:${a.titre_fr ? 1 : 0}`).join(",");
  }

  function filtrerParPrefs(alertes) {
    if (!userDomaines.length) return alertes;
    return alertes.filter(a =>
      userDomaines.some(d => (a.domaine || "").includes(d.replace(/^\S+\s/, "")))
    );
  }

  // Filtre portée (mondial/national par domaine) + pays (national d'un seul pays)
  function filtrerParPorteePays(alertes) {
    const pays = (userPays || "").toLowerCase();
    return alertes.filter(a => {
      const portee = a.portee || "";
      if (!portee) return true;  // alerte ancienne sans tag
      const pref = userPortees[a.domaine] || "tout";
      if (pref === "mondiale" && portee !== "mondiale" && portee !== "regionale") return false;
      if ((portee === "nationale" || portee === "locale") && pays && pays !== "tous") {
        const pa = (a.pays || "").toLowerCase();
        if (pa && pa !== pays) return false;
      }
      return true;
    });
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
    fermerDomaine();   // toujours revenir aux rails en quittant/rouvrant le feed
    ["feed","corr","saved","params"].forEach(p => {
      document.getElementById("page-" + p).style.display = "none";
    });
    document.getElementById("page-" + page).style.display = "block";
    // appel programmatique (ex: depuis une notif) : retrouver le bouton nav correspondant
    btn = btn || document.querySelector(`.nav-btn[onclick*="'${page}'"]`);
    document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("actif"));
    if (btn) btn.classList.add("actif");
    if (page === "feed")  marquerLus();
    if (page === "saved") chargerSauvegardes();
    if (page === "corr")  chargerCorrelations();
    // Remonte en haut de la page sélectionnée, avec défilement animé
    // (sauf si l'utilisateur a demandé moins d'animations)
    const doux = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
    window.scrollTo({ top: 0, behavior: doux });
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
    else { input.value = ""; afficherFeed(); }
  }

  // ── Filtres ─────────────────────────────────────────────────────────────
  function setFiltre(val, btn) {
    filtreCourant = val;
    document.querySelectorAll(".filtre-pill").forEach(b => b.classList.remove("actif"));
    btn.classList.add("actif");
    afficherFeed();
  }

  // ── Helpers ─────────────────────────────────────────────────────────────
  // Les dates serveur sont en UTC mais stockées sans fuseau ("...T18:00:00").
  // Sans 'Z', le navigateur les lit en heure locale → décalage (ex: +2h en France).
  // On force l'UTC quand aucun fuseau n'est présent.
  function parseDate(iso) {
    if (typeof iso === "string" && !/[zZ]|[+-]\d\d:?\d\d$/.test(iso)) iso += "Z";
    return new Date(iso);
  }

  function formatHeure(iso) {
    const diff = Math.floor((Date.now() - parseDate(iso)) / 60000);
    if (diff < 1)    return "À l'instant";
    if (diff < 60)   return diff + " min";
    if (diff < 1440) return Math.floor(diff / 60) + "h";
    return parseDate(iso).toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
  }

  const DOMAINES_MAP = [
    { key: "Géo",          cls: "geo",     label: "Géopolitique", icon: "🌍" },
    { key: "Science",      cls: "sci",     label: "Science",      icon: "🔬" },
    { key: "Tech",         cls: "tech",    label: "Tech & IA",    icon: "💻" },
    { key: "Finance",      cls: "finance", label: "Finance",      icon: "📈" },
    { key: "Environnement",cls: "env",     label: "Environnement",icon: "🌿" },
    { key: "Sport",        cls: "sport",   label: "Sport",        icon: "⚽" },
  ];

  function getDomaine(d) {
    return DOMAINES_MAP.find(m => d.includes(m.key)) || { cls: "geo", label: d };
  }

  const PORTEE_LABELS = {
    mondiale:   { icon: "earth-outline",    txt: "Mondial",  cls: "portee-mondiale"  },
    regionale:  { icon: "location-outline", txt: "Régional", cls: "portee-nationale" },
    nationale:  { icon: "location-outline", txt: "National", cls: "portee-nationale" },
    locale:     { icon: "location-outline", txt: "Local",    cls: "portee-nationale" },
  };

  function carteHTML(a, featured = false) {
    const dom         = getDomaine(a.domaine);
    const cls         = "carte-" + dom.cls;
    const saved       = savedIds.has(a.id);
    const niv         = a.niveau || 2;
    const sources     = a.sources || [];
    const critique    = niv >= 3 ? `<span class="badge-alerte">CRITIQUE</span>` : "";
    const clsCritique = niv >= 3 ? " carte-critique" : "";
    const sourceCount = sources.length > 1 ? `<span class="source-count">${sources.length} sources</span>` : "";
    const titre       = esc(userLangue === "fr" && a.titre_fr ? a.titre_fr : a.titre);
    const porteeInfo  = a.portee ? PORTEE_LABELS[a.portee] : null;

    // Surtitre épuré : uniquement la catégorie (+ badge critique)
    const meta = `<div class="carte-meta">
      <span class="source-dot"></span>
      <span class="meta-cat">${esc(dom.label)}</span>
      ${critique}
    </div>`;

    // Portée + heure descendent dans le pied de carte
    const porteeBit = porteeInfo
      ? `<span class="fm-portee ${porteeInfo.cls}"><ion-icon name="${porteeInfo.icon}"></ion-icon>${porteeInfo.txt}</span><span class="fm-sep">·</span>`
      : "";
    const footMeta = `<span class="carte-footmeta">${porteeBit}<span class="fm-time">${formatHeure(a.date)}</span></span>`;

    const actions = `<div class="carte-actions">
      <button class="btn-save ${saved ? "saved" : ""}" aria-label="${saved ? "Retirer des enregistrés" : "Enregistrer"}" onclick="event.stopPropagation(); toggleSave(${a.id}, this)">
        <ion-icon name="${saved ? "bookmark" : "bookmark-outline"}"></ion-icon>
      </button>
      <button class="btn-save" aria-label="Partager" onclick="event.stopPropagation(); partagerAlerte(${a.id})">
        <ion-icon name="share-outline"></ion-icon>
      </button>
    </div>`;

    const footer = `<div class="carte-footer">
      <div class="footer-left">${footMeta}${sourceCount}</div>
      ${actions}
    </div>`;

    const visuelHero = a.image
      ? `<img class="vignette-img-hero" src="${esc(a.image)}" loading="lazy" alt="" onerror="this.remove()"><div class="vignette-shade"></div>`
      : `<span class="vignette-icon-hero">${dom.icon || ""}</span>`;
    const visuelRow = a.image
      ? `<img class="vignette-img" src="${esc(a.image)}" loading="lazy" alt="" onerror="this.remove()">`
      : `<span class="vignette-icon">${dom.icon || ""}</span>`;

    if (featured) {
      return `
        <div class="carte ${cls} carte-hero${clsCritique}" onclick="ouvrirModal(${a.id})">
          <div class="carte-vignette-hero">
            ${visuelHero}
            <span class="vignette-badge">${esc(dom.label)}</span>
          </div>
          <div class="carte-body">
            ${meta}
            <div class="carte-titre">${titre}</div>
            ${a.accroche ? `<div class="carte-resume">${esc(a.accroche)}</div>` : ""}
            ${footer}
          </div>
        </div>`;
    }

    return `
      <div class="carte ${cls} carte-row${clsCritique}" onclick="ouvrirModal(${a.id})">
        <div class="carte-vignette">${visuelRow}</div>
        <div class="carte-body">
          ${meta}
          <div class="carte-titre">${titre}</div>
          ${a.accroche && dom.cls !== "sport" ? `<div class="carte-resume">${esc(a.accroche)}</div>` : ""}
          ${footer}
        </div>
      </div>`;
  }

  // ── Skeleton de chargement ────────────────────────────────────────────────
  function skeletonHTML(n = 6) {
    const card = `<div class="skel-card">
      <div class="skel-vignette"></div>
      <div class="skel-body">
        <div class="skel-line court"></div>
        <div class="skel-line long"></div>
        <div class="skel-line moyen"></div>
      </div>
    </div>`;
    return Array(n).fill(card).join("");
  }

  // ── Alertes ─────────────────────────────────────────────────────────────
  // Rendu du feed depuis le cache local (filtre + recherche), sans réseau.
  // Le feed est organisé en rails par domaine (carrousel circulaire).
  // En recherche ou avec un filtre de domaine précis, on retombe sur une liste plate.
  function afficherFeed() {
    fermerDomaine();
    const q = (document.getElementById("recherche")?.value || "").trim();
    if (filtreCourant === "Tout" && !q) { afficherRails(); return; }
    afficherListePlate(q);
  }

  // ── Vue liste plate (recherche / filtre domaine) ──
  function afficherListePlate(q) {
    let alertes = filtreCourant === "Tout"
      ? filtrerParPrefs(alertesCache)
      : alertesCache.filter(a => (a.domaine || "").includes(filtreCourant));
    alertes = filtrerParPorteePays(alertes);

    const ql = q.toLowerCase();
    if (ql) {
      alertes = alertes.filter(a =>
        (a.titre    || "").toLowerCase().includes(ql) ||
        (a.accroche || "").toLowerCase().includes(ql) ||
        (a.contexte || "").toLowerCase().includes(ql) ||
        (a.domaine  || "").toLowerCase().includes(ql)
      );
    }

    const feed = document.getElementById("feed");
    if (!alertes.length) {
      feed.innerHTML = `<div class="vide">${q ? "Aucun résultat pour « " + esc(q) + " »." : "Aucune alerte pour le moment.<br>Vérification toutes les 15 min."}</div>`;
      return;
    }
    const LIMITE_HERO = Date.now() - 48 * 3600 * 1000;
    const idx = alertes.findIndex(a => (a.niveau || 2) >= 3 && parseDate(a.date).getTime() >= LIMITE_HERO);
    if (idx > 0) alertes = [alertes[idx], ...alertes.slice(0, idx), ...alertes.slice(idx + 1)];
    feed.innerHTML = alertes.map((a, i) => carteHTML(a, i === 0)).join("");
  }

  // ── Tri / regroupement pour les rails ──
  function critiqueDuJour(a) {
    return (a.niveau || 2) >= 3 && parseDate(a.date).getTime() >= Date.now() - 48 * 3600 * 1000;
  }
  function trierArts(arts) {
    return [...arts].sort((x, y) => {
      const dc = (critiqueDuJour(y) ? 1 : 0) - (critiqueDuJour(x) ? 1 : 0);
      if (dc) return dc;                                  // critique du jour d'abord
      return parseDate(y.date) - parseDate(x.date);       // puis le plus récent
    });
  }
  function ordreDomaines() {
    const est_pref = m => userDomaines.some(d => d.includes(m.key));
    return [...DOMAINES_MAP.filter(est_pref), ...DOMAINES_MAP.filter(m => !est_pref(m))];
  }

  // ── Vue rails par domaine ──
  function afficherRails() {
    const feed = document.getElementById("feed");
    const alertes = filtrerParPorteePays(alertesCache);
    if (!alertes.length) {
      feed.innerHTML = `<div class="vide">Aucune alerte pour le moment.<br>Vérification toutes les 15 min.</div>`;
      return;
    }
    let html = "";
    // "À la une" : critiques du jour, tous domaines confondus
    const unes = trierArts(alertes.filter(critiqueDuJour)).slice(0, 10);
    if (unes.length) html += railHTML({ cls: "une", label: "À la une" }, unes, false, true);
    // Un rail par domaine, préférés en haut
    for (const m of ordreDomaines()) {
      const arts = alertes.filter(a => getDomaine(a.domaine || "").cls === m.cls);
      if (!arts.length) continue;
      html += railHTML(m, trierArts(arts).slice(0, 10), userDomaines.some(d => d.includes(m.key)), false);
    }
    feed.innerHTML = html;
    feed.querySelectorAll(".carousel").forEach(initCarousel);
  }

  function railHTML(m, arts, pref, isUne) {
    const accent = isUne ? "#ef4444" : `var(--${m.cls})`;
    const star = pref ? `<span class="rail-star">★</span>` : "";
    const foot = isUne ? "" :
      `<div class="rail-foot"><span class="rail-count"></span><span class="rail-all" onclick="ouvrirDomaine('${m.cls}')">Voir tout →</span></div>`;
    return `<div class="rail" style="--accent:${accent}">
      <div class="rail-head"><span class="rail-dot"></span><span class="rail-name">${esc(m.label)}</span>${star}</div>
      <div class="carousel" data-cur="0"><div class="stage">${arts.map(carteCarousel).join("")}</div></div>
      ${foot}
    </div>`;
  }

  function carteCarousel(a) {
    const dom  = getDomaine(a.domaine || "");
    const crit = (a.niveau || 2) >= 3;
    const titre = esc(userLangue === "fr" && a.titre_fr ? a.titre_fr : a.titre);
    const visuel = a.image
      ? `<img class="cc-img" src="${esc(a.image)}" loading="lazy" alt="" onerror="this.remove()">`
      : `<span class="cc-ic">${dom.icon || ""}</span>`;
    return `<div class="cc carte-${dom.cls} ${crit ? "crit" : ""}" data-id="${a.id}"
        style="--accent:var(--${dom.cls});--thumb:var(--thumb-${dom.cls})">
      <div class="cc-thumb">${visuel}${crit ? '<span class="cc-badge">CRITIQUE</span>' : ""}</div>
      <div class="cc-body">
        <div class="cc-cat"><span class="cc-d"></span>${esc(dom.label)}</div>
        <div class="cc-title">${titre}</div>
        <div class="cc-foot">${porteeHeureHTML(a)}</div>
      </div></div>`;
  }

  function carteGrille(a) {
    const dom  = getDomaine(a.domaine || "");
    const crit = (a.niveau || 2) >= 3;
    const titre = esc(userLangue === "fr" && a.titre_fr ? a.titre_fr : a.titre);
    const visuel = a.image
      ? `<img class="g-img" src="${esc(a.image)}" loading="lazy" alt="" onerror="this.remove()">`
      : `<span class="g-ic">${dom.icon || ""}</span>`;
    return `<div class="gcard carte-${dom.cls} ${crit ? "crit" : ""}" data-id="${a.id}"
        style="--accent:var(--${dom.cls});--thumb:var(--thumb-${dom.cls})" onclick="ouvrirModal(${a.id})">
      <div class="g-thumb">${visuel}${crit ? '<span class="cc-badge">CRITIQUE</span>' : ""}</div>
      <div class="g-body">
        <div class="gcat"><span class="cc-d"></span>${esc(dom.label)}</div>
        <div class="gtitle">${titre}</div>
        <div class="gfoot">${porteeHeureHTML(a)}</div>
      </div>
    </div>`;
  }

  // Portée (icône + libellé) · heure — réutilisé carrousel + grille
  function porteeHeureHTML(a) {
    const p = a.portee ? PORTEE_LABELS[a.portee] : null;
    const portee = p
      ? `<span class="fm-portee ${p.cls}"><ion-icon name="${p.icon}"></ion-icon>${p.txt}</span><span class="fm-sep">·</span>`
      : "";
    return `${portee}<span class="fm-time">${formatHeure(a.date)}</span>`;
  }

  // ── Mécanique du carrousel circulaire (placement en sinus) ──
  const C_STEP = Math.PI / 4, C_R = 86;
  function layoutCarousel(car) {
    const cards = [...car.querySelectorAll(".cc")], n = cards.length, cur = +car.dataset.cur;
    cards.forEach((card, i) => {
      let off = i - cur; if (off > n / 2) off -= n; if (off < -n / 2) off += n;
      const a = Math.abs(off), ang = off * C_STEP, depth = Math.cos(ang);
      const tx = Math.sin(ang) * C_R, sc = Math.max(0.5, 0.62 + 0.38 * depth);
      const op = a <= 2 ? 1 : (a <= 3 ? 0.28 : 0);   // cartes proches opaques
      card.style.transform = `translateX(calc(-50% + ${tx}px)) scale(${sc})`;
      card.style.opacity = op;
      card.style.zIndex = Math.round(depth * 10);   // sous la nav (z-index 50)
      card.style.pointerEvents = a > 2 ? "none" : "auto";
      card.classList.toggle("center", off === 0);
    });
  }
  function majCarousel(car) {
    const el = car.parentElement.querySelector(".rail-count");
    if (el) el.textContent = `${(+car.dataset.cur) + 1} / ${car.querySelectorAll(".cc").length}`;
  }
  function tourneCarousel(car, dir) {
    const n = car.querySelectorAll(".cc").length;
    car.dataset.cur = ((+car.dataset.cur) + dir + n) % n;
    layoutCarousel(car); majCarousel(car);
  }
  function initCarousel(car) {
    layoutCarousel(car); majCarousel(car);
    let x0 = null;
    car.addEventListener("pointerdown", e => { x0 = e.clientX; car._moved = false; });
    car.addEventListener("pointermove", e => { if (x0 !== null && Math.abs(e.clientX - x0) > 8) car._moved = true; });
    car.addEventListener("pointerup", e => {
      if (x0 === null) return; const dx = e.clientX - x0; x0 = null;
      if (Math.abs(dx) > 30) tourneCarousel(car, dx < 0 ? 1 : -1);
    });
    car.querySelectorAll(".cc").forEach((card, i) => {
      card.addEventListener("click", () => {
        if (car._moved) return;                                   // c'était un glissé
        if (card.classList.contains("center")) ouvrirModal(+card.dataset.id);
        else { car.dataset.cur = i; layoutCarousel(car); majCarousel(car); }
      });
    });
  }

  // ── Page domaine (« Voir tout ») ──
  function ouvrirDomaine(cls) {
    const m = DOMAINES_MAP.find(x => x.cls === cls); if (!m) return;
    const arts = trierArts(filtrerParPorteePays(alertesCache).filter(a => getDomaine(a.domaine || "").cls === cls));
    const dv = document.getElementById("feed-domain");
    dv.innerHTML = `
      <div class="dp-head">
        <button class="dp-back" onclick="fermerDomaine()">‹ Retour</button>
        <span class="dp-title">${esc(m.label)}</span>
        <span class="dp-sub">${arts.length} info${arts.length > 1 ? "s" : ""}</span>
      </div>
      <div class="dp-grid">${arts.map(carteGrille).join("") || '<div class="vide">Aucune info.</div>'}</div>`;
    document.getElementById("feed").style.display = "none";
    dv.style.display = "block";
    window.scrollTo(0, 0);
  }
  function fermerDomaine() {
    const dv = document.getElementById("feed-domain");
    if (dv) { dv.style.display = "none"; dv.innerHTML = ""; }
    const f = document.getElementById("feed");
    if (f) f.style.display = "";
  }

  // Rafraîchit le cache depuis le réseau puis ré-affiche.
  async function chargerAlertes() {
    try {
      alertesCache = await fetch("/api/alertes").then(r => r.json());
    } catch { /* garde le cache courant en cas d'échec réseau */ }
    afficherFeed();
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
    feed.innerHTML = skeletonHTML(3);
    const corrs = await fetch("/api/correlations").then(r => r.json()).catch(() => []);

    if (!corrs.length) {
      feed.innerHTML = `<div class="vide">Aucune corrélation pour le moment.<br>Générée chaque matin à 8h.</div>`;
      return;
    }

    feed.innerHTML = corrs.map(c => {
      const domaines = c.domaines || [];
      const nb  = (c.alertes_ids || []).length;
      const d   = c.date ? parseDate(c.date).toLocaleDateString("fr-FR", {day:"numeric",month:"short"}) : "";

      // accent = couleur du premier domaine impliqué
      const accent = domaines.length
        ? (CORR_DOMAIN_COLORS[domaines[0].replace(/^\S+\s/, "")] || "var(--text-secondary)")
        : "var(--text-secondary)";

      const domainsHtml = (domaines.map(dom => {
        const label = dom.replace(/^\S+\s/, "");
        const color = CORR_DOMAIN_COLORS[label] || "var(--text-secondary)";
        return `<span class="corr-domain-dot" style="color:${color}">${esc(label)}</span>`;
      }).join('<span class="corr-sep">·</span>')) || `<span class="corr-domain-dot">Général</span>`;

      // le « fil » : un nœud par étape, reliés verticalement
      const corps = c.contexte
        ? [["Contexte", c.contexte], ["Enjeux", c.analyse], ["À suivre", c.implication]]
            .filter(([, txt]) => txt)
            .map(([label, txt]) => `
              <div class="corr-step">
                <span class="corr-dot"></span>
                <div class="corr-label">${label}</div>
                <div class="corr-text">${esc(txt)}</div>
              </div>`).join("")
        : `<div class="corr-synthese">${esc(c.synthese || "")}</div>`;

      // liste (repliée) des articles liés, cliquables → fiche de l'article
      const ids = c.alertes_ids || [];
      const liensHtml = ids.map(id => {
        const a = alertesCache.find(x => x.id === id);
        if (!a) return `<div class="corr-lien-off">Article expiré</div>`;
        const dom = getDomaine(a.domaine);
        const t   = esc(userLangue === "fr" && a.titre_fr ? a.titre_fr : a.titre);
        return `<div class="corr-lien" onclick="ouvrirModal(${id})">
            <span class="corr-lien-dot" style="background:var(--${dom.cls})"></span>
            <span class="corr-lien-titre">${t}</span>
            <ion-icon name="chevron-forward-outline"></ion-icon>
          </div>`;
      }).join("");

      const foot = nb
        ? `<button class="corr-foot" onclick="toggleCorrLiens(this)">
             <ion-icon name="git-network-outline"></ion-icon>
             <span>${nb} alerte${nb > 1 ? "s" : ""} liée${nb > 1 ? "s" : ""}</span>
             <ion-icon name="chevron-down-outline" class="corr-foot-chevron"></ion-icon>
           </button>
           <div class="corr-liens">${liensHtml}</div>`
        : "";

      return `<div class="corr-card" style="--accent:${accent}">
        <div class="corr-card-head">
          ${domainsHtml}
          <span class="corr-sep">·</span>
          <span class="corr-date">${d}</span>
          ${c.maj ? '<span class="corr-maj">mis à jour</span>' : ''}
        </div>
        <div class="corr-titre">${esc(c.titre || "")}</div>
        <div class="corr-steps">${corps}</div>
        ${foot}
      </div>`;
    }).join("");
  }

  // Déplie / replie la liste des articles liés d'une corrélation
  function toggleCorrLiens(btn) {
    btn.classList.toggle("open");
    const liste = btn.nextElementSibling;
    if (liste && liste.classList.contains("corr-liens")) liste.classList.toggle("open");
  }

  // ── Sauvegardés ─────────────────────────────────────────────────────────
  function labelDate(iso) {
    if (!iso) return "Avant";
    const d = parseDate(iso);
    const now = new Date();
    const diff = Math.floor((now - d) / 86400000);
    if (diff === 0) return "Aujourd'hui";
    if (diff === 1) return "Hier";
    if (diff < 7)  return "Cette semaine";
    return d.toLocaleDateString("fr-FR", { day: "numeric", month: "long" });
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
    document.body.classList.add("modal-ouvert");

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
    afficherFeed();
  }

  function fermerModal(e) {
    if (e.target === document.getElementById("modal")) {
      document.getElementById("modal").classList.remove("visible");
      document.body.classList.remove("modal-ouvert");
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
    afficherFeed();
  }

  async function sauverPays(val) {
    userPays = val;
    await fetch("/api/preferences", { method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ pays: val }) });
    afficherFeed();
  }

  // ── Init ─────────────────────────────────────────────────────────────────
  async function init() {
    document.getElementById("feed").innerHTML = skeletonHTML();
    const data = await fetch("/api/init").then(r => r.json());

    // thème ("dim" est l'ancien nom de "slate")
    const theme = (data.preferences.theme || "dark").replace("dim", "slate");
    setTheme(theme, false);

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

    // portées par domaine + pays (filtrage national)
    userPortees = data.preferences.portees || {};
    userPays    = data.preferences.pays || "France";
    const selectPays = document.getElementById("select-pays");
    if (selectPays) selectPays.value = userPays;

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
    afficherFeed();
    sigFeed = signatureAlertes();
    chargerStats();
    majBadgeNonLus();
  }

  async function checkNotifHash() {
    const hash = window.location.hash;
    // notif de corrélation → page Corrélations
    if (hash.startsWith("#correlations") || hash.startsWith("#corr")) {
      history.replaceState(null, "", "/");
      afficherPage("corr");
      return;
    }
    const match = hash.match(/#synthese\/(\d+)/);
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
  // si l'app est déjà ouverte et qu'une notif change le hash
  window.addEventListener("hashchange", checkNotifHash);

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
    try {
      alertesCache = await fetch("/api/alertes").then(r => r.json());
    } catch { return; }
    // ne ré-afficher que si le contenu a réellement changé (sinon clignotement inutile)
    const sig = signatureAlertes();
    const domaineOuvert = document.getElementById("feed-domain")?.style.display === "block";
    const modaleOuverte = document.body.classList.contains("modal-ouvert");
    if (sig !== sigFeed && !domaineOuvert && !modaleOuverte) {
      sigFeed = sig;
      afficherFeed();
    }
    chargerStats();
    majBadgeNonLus();
  }, 30000);
