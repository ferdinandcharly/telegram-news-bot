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

## 🟢 Phase 1 — Gratuit, sans 2ᵉ PC (À FAIRE EN PREMIER)

Tout ici est du code ou de la config gratuite. Rien à payer, rien à brancher.

### Sécurité / comptes
- [ ] 🟠 **Activer la confirmation email** dans Supabase (Auth → Email → confirm email).
      *Pourquoi : sans ça, n'importe qui crée des comptes avec des emails bidons. Le code gère déjà les 2 cas.*
- [ ] 🟠 **Vérifier la RLS** active sur `user_preferences`, `user_sauvegardes`, `user_subscriptions`.
      *Pourquoi : toute la sécurité des données utilisateurs en dépend.*
- [ ] 🟠 **Rate limiting** sur `/login` et `/register` (Flask-Limiter).
      *Pourquoi : empêche brute force + création massive de comptes.*
- [ ] 🟡 **Validation d'entrées** côté serveur sur `/api/preferences` (taille avatar, types).

### Robustesse / visibilité
- [ ] 🟠 **Monitoring d'erreurs** via Sentry (offre gratuite).
      *Pourquoi : sinon tu es aveugle sur les crashs quand de vrais users arrivent.*
- [ ] 🟠 **Serveur de prod** `gunicorn app:app` au lieu de `app.run()`.
      *⚠️ 1 seul worker tant que le bot tourne dans le web (sinon bots en double). Devient propre après Phase 2.*
- [ ] 🟡 **Figer les versions** dans `requirements.txt` (`flask==x.y`, etc.).
- [ ] 🟡 **`render.yaml`** dans le repo (config de déploiement reproductible).

### UI / expérience
- [ ] 🟠 **Shell offline minimal** dans le service worker + repli si `init()` échoue.
      *Pourquoi : aujourd'hui la PWA ouverte sans réseau = écran blanc.*
- [ ] 🟡 **Messages d'erreur** (toasts) quand save / préférences échouent.
- [ ] 🟡 **Page 404 / 500** personnalisée.
- [ ] 🟡 **Action “rafraîchir”** manuelle évidente dans le feed.
- [ ] 🟡 **Accessibilité** : passe rapide contrastes (3 thèmes) + tailles de tap.

### Légal
- [ ] 🟠 **Page CGU / Conditions d'utilisation** (la politique de confidentialité existe déjà).

---

## 🖥️ Phase 2 — Nécessite le 2ᵉ PC (gratuit, juste le matériel)

- [ ] 🔴 Lancer **`worker.py`** 24/7 sur le PC + mettre **`RUN_BOT=0`** sur Render.
      *Effet : notifications/polling fiables sans dépendre du réveil de Render.*
- [ ] 🟠 **Auto-démarrage au boot** (Planificateur de tâches Windows).
- [ ] 🟡 Supprimer **UptimeRobot + cron-job.org** (rustines devenues inutiles).

> ⚠️ Le PC garde le **bot** vivant, **pas le site web**. Le web sur Render dort toujours
> pour les visiteurs → voir Phase 3 pour éliminer le cold start côté utilisateurs.

---

## 💳 Phase 3 — Nécessite de payer

- [ ] 🔴 **Render payant** (~7 $/mois) : instance qui ne dort pas → fin du cold start (30-50 s)
      pour les visiteurs. *Le gros levier confort pour un vrai public.*
- [ ] 🟡 **Supabase Pro** (25 $/mois) : seulement quand la DB approche 500 Mo ou pour les
      backups quotidiens côté serveur. Pas avant.
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
- [ ] 🟠 **SW offline minimal** (vérifié par PWABuilder) — déjà couvert en Phase 1.

---

*Mis à jour le 2026-06-19.*
