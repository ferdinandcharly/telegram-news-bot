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


# Directives de Design UI/UX (Style Minimaliste & Pro)

Tu dois impérativement respecter ces règles de design pour TOUTES les modifications d'interface (HTML, CSS, Tailwind) que tu effectues sur l'application. Nous visons un rendu professionnel, sobre et moderne (style Vercel, Linear ou Stripe), et non une démo technologique "cliché IA".

## ⚠️ CONTRAINTES STRICTES (INTERDICTION DE MODIFIER)

- **Système de Thèmes Tri-couleur :** L'application possède un sélecteur de thème avec trois états : Noir, Gris, et Blanc. Tu ne dois **jamais** modifier, supprimer ou casser la logique de changement de thème existante. 
- **Adaptabilité :** Lorsque tu améliores le visuel d'un composant, assure-tot qu'il utilise bien les variables ou les classes CSS dynamiques liées à ces trois thèmes pour que le rendu reste parfait qu'on soit en mode Noir, Gris ou Blanc.
- **Logique des fichiers :** Ne modifie pas les fichiers de configuration globale des thèmes (comme `tailwind.config.js` ou tes fichiers de contextes/state pour le thème) sans mon autorisation explicite.

## 1. Palette de Couleurs & Contraste
- **Pas de dégradés flashy :** Interdiction d'utiliser des dégradés violet/bleu/rose typiques des outils IA.
- **Base Neutre :** Utilise une charte de gris très propre pour le fond et les cartes. 
  - *Mode Clair :* Fond blanc (`bg-white`) ou gris très clair (`bg-slate-50`), textes en gris foncé (`text-slate-900`).
  - *Mode Sombre :* Fond noir pur (`bg-black`) ou zinc très sombre (`bg-zinc-950`), bordures fines (`border-zinc-800`).
- **Couleur d'accent unique :** Utilise une seule couleur pour les actions principales (ex: un bleu roi propre `bg-blue-600`, ou du noir pur `bg-black text-white`).

## 2. Espacement et Mise en page (Layout)
- **Le "White Space" est une fonctionnalité :** Augmente les marges et les paddings. Laisse respirer les éléments (`p-6` ou `p-8` sur les cartes, `space-y-6` entre les sections).
- **Grille et Alignement :** Tout doit être parfaitement aligné sur une grille propre. Pas de centrage intempestif. Les textes et formulaires sont alignés à gauche.
- **Bordures vs Ombres :** Privilégie des bordures très fines (`border border-slate-200` ou `border-zinc-800`) plutôt que des ombres portées massives et floues.

## 3. Composants et Typographie
- **Typographie :** Utilise des polices d'écriture sans-serif modernes (Inter, Geist, ou la police système par défaut). Pas de texte en gras partout, joue plutôt sur la taille (`text-sm` vs `text-lg`) et l'opacité (`text-slate-500` pour les sous-titres).
- **Boutons & Inputs :** Des angles légèrement arrondis (`rounded-md` ou `rounded-lg`), jamais de pilules parfaites (`rounded-full`) sauf pour des badges spécifiques. Les champs de texte doivent avoir une bordure fine et un fond neutre.
- **Icônes :** Utilise uniquement des icônes minimalistes (comme Lucide React ou Heroicons). Une icône ne doit jamais être plus grosse que le texte qui l'accompagne.