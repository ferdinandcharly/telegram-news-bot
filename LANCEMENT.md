# Korrel — Feuille de route lancement

Document de suivi pour préparer la publication sur Android/iOS. Coche les cases au fur
et à mesure (`- [ ]` → `- [x]`). Les phases sont ordonnées : **commence par la Phase 1**
(gratuit + sans 2ᵉ PC), puis Phase 2 (2ᵉ PC), Phase 3 (payant), Phase 4 (Play Store),
Phase 5 (iOS — repoussée après le lancement Android).

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

> ⚠️ **Au 2026-07-03, les 2 actions manuelles Supabase ci-dessous ne sont toujours pas
> cochées.** Elles deviennent 🔴 bloquantes dès qu'un vrai public arrive — à faire avant
> toute publication store.

### Sécurité / comptes
- [ ] 🔴 **Activer la confirmation email** dans Supabase (Auth → Providers → Email → *Confirm email*).
      → **À FAIRE par toi dans le dashboard Supabase.** Le code gère déjà les 2 cas.
- [ ] 🔴 **Vérifier la RLS** sur `user_preferences`, `user_sauvegardes`, `user_subscriptions`.
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

> ⚠️ Cette phase devient **de facto obligatoire au moment de publier sur un store** :
> un cold start de 30-50 s sur une app installée = désinstallations + avis négatifs.

- [ ] 🔴 **Render payant** (~7 $/mois) : instance qui ne dort pas → fin du cold start (30-50 s).
- [ ] 🔴 **Domaine personnalisé** (~10 €/an) : quasi indispensable pour le TWA — lier
      `assetlinks.json` à `onrender.com` est fragile. *(Reclassé 🟡 → 🔴 pour le Play Store.)*
- [ ] 🟡 **Supabase Pro** (25 $/mois) : quand la DB approche 500 Mo ou pour les backups quotidiens serveur.
- [ ] 🟡 **Surveiller le quota Groq 12k TPM (free)** : filtre 8b + corrélations 70b partagés
      entre tous les users. OK jusqu'à ~20-50 users (corrélations globales), c'est la première
      limite qui craquera avec l'audience → tier payant Groq le moment venu.

---

## 🤖 Phase 4 — Play Store (si tu vises le store, pas juste l'install PWA)

> L'install via Chrome (« Ajouter à l'écran d'accueil ») marche **déjà** sans rien de tout ça.
> Effort estimé : quelques jours, aucune réécriture — la PWA est prête à ~80 %
> (manifest complet, SW durci, meta OK, `/api/delete-account` existe).

- [ ] 🔴 **Compte Play Console** (25 $ une fois).
- [ ] 🔴 Emballer la PWA en **TWA** via PWABuilder / Bubblewrap.
- [ ] 🔴 **`assetlinks.json`** servi sur `/.well-known/` — **absent aujourd'hui (vérifié 2026-07-03)**.
      Sans lui, barre d'URL visible dans l'app. Domaine perso requis (voir Phase 3).
- [ ] 🔴 **Formulaire Data Safety** (email + préférences + endpoints push à déclarer, lier la
      page confidentialité existante) + content rating + fiche store.
- [ ] 🔴 **Suppression de compte accessible HORS app** : Google exige une page web publique,
      en plus du bouton in-app (`/api/delete-account` existe déjà). *(Reclassé 🟠 → 🔴 : exigence store.)*
- [ ] 🟠 **Screenshots** : fiche store + champ `screenshots` du manifest — **absent aujourd'hui (vérifié 2026-07-03)**.
- [x] 🟠 **SW offline minimal** (vérifié par PWABuilder) — fait en Phase 1.

---

## 🍎 Phase 5 — iOS (repoussée après le lancement Android)

Deux voies, par ordre recommandé :

### 5a. PWA via Safari (gratuit, marche déjà)
- [x] 🟢 **Push web iOS** : fonctionne depuis iOS 16.4 pour les PWA installées sur l'écran
      d'accueil — le VAPID actuel est compatible. Meta iOS déjà en place
      (`apple-touch-icon`, `apple-mobile-web-app-*`).
- [ ] 🟠 **Page d'aide à l'installation** dans l'app (« Partager → Sur l'écran d'accueil ») :
      le geste est méconnu des utilisateurs iPhone.

### 5b. App Store (seulement si la traction le justifie)
- [ ] 🔴 **Compte développeur Apple** (99 $/an).
- [ ] 🔴 Emballage **Capacitor** + build Xcode → nécessite **un Mac** (ou service de build
      cloud depuis Windows — friction réelle).
- [ ] 🔴 **Risque guideline 4.2** (minimum functionality) : Apple rejette régulièrement les
      sites web emballés sans fonctionnalité native ajoutée. Prévoir un vrai plus natif
      avant de soumettre.
- [ ] 🟠 Data collection / privacy labels App Store + suppression de compte (exigée aussi par Apple).

---

## 📌 Ordre recommandé (bilan 2026-07-03)

1. **Phase 2** (worker 2ᵉ PC) + actions manuelles Supabase (confirmation email, RLS) — dès maintenant.
2. **Phase 3** : domaine perso + Render payant.
3. **Phase 4** : Play Store en TWA.
4. **Phase 5a** : iOS en PWA Safari avec page d'aide ; **5b** App Store seulement si la traction le justifie.

---

*Phase 1 (code) réalisée le 2026-06-19. Bilan cap Android/iOS le 2026-07-03. Restent côté
toi : confirmation email + RLS Supabase, `SENTRY_DSN`, et la Start Command Render `python3 serve.py`.*
