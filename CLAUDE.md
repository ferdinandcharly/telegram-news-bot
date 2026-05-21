# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Lancer le projet

```bash
py app.py
```

Pas de build, pas de lint, pas de tests automatisés. Les fichiers `test_alertes.py` et `test_telegram.py` sont des scripts manuels one-shot.

Pour tester en local, copier `.env.example` → `.env` avec les vraies clés (voir `.env` ignoré par git).

## Déploiement

Branche active : `multi-users`. La branche `main` est l'ancienne version stable sans auth.

```bash
git add <fichiers>
git commit -m "..."
git push origin multi-users
```

Render détecte le push et redémarre automatiquement. Start command Render : `python3 app.py`.

## Architecture

```
bot.py          ← RSS polling (15 min) + filtre IA Groq + Telegram
app.py          ← Flask server + toute la logique API + lance bot en thread
templates/index.html  ← SPA mobile complète (HTML/CSS/JS vanilla, un seul fichier)
static/sw.js    ← Service Worker pour push notifications VAPID
static/manifest.json  ← PWA manifest
```

### Flux de données

```
RSS feeds → bot.verifier() → est_doublon() → est_important() [Groq 8b]
    → on_alerte() callback → app.py → Supabase alertes table + push notifs

Chaque matin à l'heure choisie par user :
check_resumes_matinaux() → generer_correlations() [Groq 70b] → Supabase correlations
```

### Supabase (REST API directe — pas le SDK Python)

Le SDK `supabase-py` n'est pas utilisé (échoue à l'install sur Windows à cause de pyiceberg). Tous les appels Supabase passent par `requests` via les helpers `sb(table)` et `sb_auth(path)`.

Deux niveaux d'accès :
- `SB_SERVICE` (service role key) — lecture/écriture globale, utilisé pour les opérations serveur
- `user_headers()` — token JWT de l'utilisateur connecté, utilisé pour les opérations RLS

Tables Supabase :
- `alertes` — articles filtrés (globale, pas de user_id)
- `correlations` — synthèses IA quotidiennes (globale)
- `user_preferences` — thème, domaines, heure_recap, display_name, last_recap_date (PK: user_id)
- `user_sauvegardes` — (user_id, alerte_id) avec RLS
- `user_subscriptions` — endpoints push VAPID par user

### Authentification

Flask session cookie (30 jours, permanent). Supabase Auth via `/auth/v1/token`. Le token access_token expire après 1h → refresh automatique côté JS toutes les 50 min via `/api/refresh-token` et sur `visibilitychange`.

`check_auth()` (`@app.before_request`) protège toutes les routes sauf : `/health`, `/login`, `/register`, `/onboarding`, `/cancel-register`, `/api/refresh-token`, et tout chemin commençant par `/a/` (pages de partage publiques).

### Filtre IA (bot.py)

Deux étapes avant de sauvegarder un article :
1. `est_doublon(titre)` — skip si >55% de mots-clés communs avec un titre récent (évite les doublons inter-sources)
2. `est_important(titre, resume, domaine)` → Groq llama-3.1-8b-instant, retourne `(niveau, teaser)`
   - niveau 0 : rejeté (~95% des articles)
   - niveau 2 : IMPORTANT → sauvegardé + push
   - niveau 3 : CRITIQUE → sauvegardé + push + Telegram

### Corrélations

`generer_correlations()` utilise llama-3.3-70b-versatile (plus lent, bien meilleur pour l'analyse). Retourne un tableau JSON avec `contexte`, `analyse`, `implication` par groupe d'alertes liées. Stockées 3 jours. Déclenchées par `check_resumes_matinaux(heure)` dans la boucle principale.

### SPA front-end (index.html)

Tout est dans un seul fichier. Variables globales clés :
- `alertesCache` — toutes les alertes en mémoire
- `userDomaines` — domaines préférés, mis à jour depuis `/api/init` et sur changement de settings
- `savedIds` — Set des IDs sauvegardés

`filtrerParPrefs()` est appliqué côté client à chaque `chargerAlertes()`. Le filtrage par domaine est client-side uniquement — `/api/alertes` retourne tout.

`/api/init` est le seul appel au démarrage : charge alertes + préférences + sauvegardes en parallèle (ThreadPoolExecutor).

## Variables d'environnement requises

```
GROQ_API_KEY
TELEGRAM_TOKEN
TELEGRAM_CHAT_ID
VAPID_PUBLIC_KEY
VAPID_PRIVATE_KEY      # clé privée VAPID encodée en base64
APP_URL                # URL publique (ex: https://telegram-news-bot2.onrender.com)
SUPABASE_URL
SUPABASE_KEY           # service role key (pas la anon key)
FLASK_SECRET_KEY
APP_PASSWORD           # non utilisé actuellement
```

## Conventions

- Commentaires en français
- Pas de librairie supabase-py — uniquement `requests`
- Le bot tourne dans un thread daemon lancé par `app.py`, pas indépendamment
- `on_alerte` dans bot.py est un callback optionnel patché par app.py au démarrage
