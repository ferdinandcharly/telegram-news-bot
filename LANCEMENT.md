# Korrel — Feuille de route lancement

Document de suivi pour préparer la publication sur Android. Coche les cases au fur
et à mesure (`- [ ]` → `- [x]`). Les phases sont ordonnées : **commence par la Phase 1**
(gratuit + sans 2ᵉ PC), puis Phase 2 (2ᵉ PC), Phase 3 (payant), Phase 4 (Play Store).

Légende sévérité : 🔴 bloquant lancement · 🟠 important avant un vrai public · 🟡 peut suivre après

---

## ✅ Déjà fait

- [x] Renommage + identité visuelle Korrel (icônes, logo, lockup feed)
- [x] Onboarding wizard (photo, nom, sexe, domaines, pays, langue, thème, notifs)
- [x] Corrélations : dédup par sujet + fusion en analyses plus poussées
- [x] Feed : tous les domaines visibles (préférés en haut), clic logo = remonter
- [x] `worker.py` prêt (bot découplé du web) — *activable Phase 2*
- [x] `backup.py` + tâche planifiée (export DB quotidien, local)
- [x] `/api/cron/recap` protégé par `CRON_SECRET`
- [x] Secrets non versionnés (`.gitignore` OK)

---

## 🟢 Phase 1 — Gratuit, sans 2ᵉ PC ✅ TERMINÉE (2026-06-19)

### Sécurité / comptes
- [ ] 🟠 **Activer la confirmation email** dans Supabase (Auth → Providers → Email → *Confirm email*).
      → **À FAIRE par toi dans le dashboard Supabase.** Le code gère déjà les 2 cas.
- [ ] 🟠 **Vérifier la RLS** sur `user_preferences`, `user_sauvegardes`, `user_subscriptions`.
      → **À FAIRE par toi** (SQL fourni dans le chat). Toute la sécu des données en dépend.
- [x] 🟠 **Rate limiting** login (10/min) + register (6/min) — Flask-Limiter + ProxyFix.
- [x] 🟡 **Validation d'entrées** `/api/preferences` (types, taille avatar, valeurs autorisées).

### Robustesse / visibilité
- [x] 🟠 **Monitoring Sentry** — câblé, *activé si* `SENTRY_DSN` est défini. → crée un projet Sentry (gratuit) et ajoute `SENTRY_DSN` dans l'env Render.
- [x] 🟠 **Serveur de prod** — `serve.py` (waitress, cross-platform). → sur Render, passer la **Start Command** à `python3 serve.py`.
- [x] 🟡 **Versions figées** dans `requirements.txt`.
- [x] 🟡 **`render.yaml`** ajouté (config reproductible).

### UI / expérience
- [x] 🟠 **Shell offline** (service worker) + repli si `init()` échoue (message « Pas de connexion »).
- [x] 🟡 **Toasts** d'erreur (ex: hors-ligne → « affichage en cache »).
- [x] 🟡 **Pages 404 / 500 / 429** (HTML propre, JSON pour `/api`).
- [x] 🟡 **Bouton rafraîchir** dans la barre du feed.
- [x] 🟡 **Accessibilité** : aria-labels (recherche, ✎/✓), labels d'inputs, activation clavier du logo, focus-visible global, tap 40px. *(Reste à vérifier à l'œil : contrastes des 3 thèmes sur device.)*

### Légal
- [x] 🟠 **Page CGU** `/terms` (+ lien depuis l'inscription, à côté de la confidentialité).

---

## 🖥️ Phase 2 — Nécessite le 2ᵉ PC (gratuit, juste le matériel)

- [ ] 🔴 Lancer **`worker.py`** 24/7 sur le PC + mettre **`RUN_BOT=0`** sur Render.
- [ ] 🟠 **Auto-démarrage au boot** (Planificateur de tâches Windows).
- [ ] 🟡 Supprimer **UptimeRobot + cron-job.org** (rustines devenues inutiles).

> ⚠️ Le PC garde le **bot** vivant, **pas le site web**. Le web sur Render dort toujours
> pour les visiteurs → voir Phase 3 pour éliminer le cold start côté utilisateurs.

---

## 💳 Phase 3 — Nécessite de payer

- [ ] 🔴 **Render payant** (~7 $/mois) : instance qui ne dort pas → fin du cold start (30-50 s).
- [ ] 🟡 **Supabase Pro** (25 $/mois) : quand la DB approche 500 Mo ou pour les backups quotidiens serveur.
- [ ] 🟡 **Domaine personnalisé** (~10 €/an) : plus propre que `onrender.com`, utile pour le TWA.

---

## 🤖 Phase 4 — Play Store (si tu vises le store, pas juste l'install PWA)

> L'install via Chrome (« Ajouter à l'écran d'accueil ») marche **déjà** sans rien de tout ça.

- [ ] 🔴 **Compte Play Console** (25 $ une fois).
- [ ] 🔴 Emballer la PWA en **TWA** via PWABuilder / Bubblewrap.
- [ ] 🔴 **`assetlinks.json`** servi sur `/.well-known/` (sinon barre d'URL visible). Domaine perso conseillé.
- [ ] 🔴 **Formulaire Data Safety** + content rating + fiche store.
- [ ] 🟠 **Suppression de compte** : voie web accessible (in-app `/api/delete-account` existe déjà).
- [ ] 🟠 **Screenshots** (fiche store + champ `screenshots` du manifest, absent aujourd'hui).
- [x] 🟠 **SW offline minimal** (vérifié par PWABuilder) — fait en Phase 1.

---

*Phase 1 (code) réalisée le 2026-06-19. Restent côté toi : confirmation email + RLS Supabase, `SENTRY_DSN`, et la Start Command Render `python3 serve.py`.*
