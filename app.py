import os
import gc
import re
import time
import json
import base64
import threading
import tempfile
import unicodedata
from datetime import datetime, timedelta, date as dt_date
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor

PARIS = ZoneInfo("Europe/Paris")
from flask import Flask, jsonify, send_from_directory, request, session, redirect
import requests as http
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import bot

# ── Monitoring (Sentry, optionnel) ──────────────────────────────────────────
# Actif seulement si SENTRY_DSN est défini. Sinon : no-op, aucun effet.
_SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if _SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=_SENTRY_DSN, traces_sample_rate=0.0, send_default_pii=False)
    except Exception as e:
        print(f"Sentry init échouée : {e}")

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

# Render/Cloud derrière un proxy → lire la vraie IP client (X-Forwarded-For)
# pour que le rate limiting compte par utilisateur et pas par proxy.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

# Rate limiting : protège login/register du brute force. Stockage mémoire
# (suffisant en mono-instance). Pas de limite globale par défaut.
limiter = Limiter(key_func=get_remote_address, app=app,
                  default_limits=[], storage_uri="memory://")

# Email autorisé à voir l'espace développeur (/api/admin/stats).
# Gating côté serveur uniquement : l'email vient de la session Supabase, non falsifiable côté client.
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "ferdinandcharly@gmail.com").strip().lower()

def _est_admin():
    email = session.get("user_email", "")
    return bool(email) and email.strip().lower() == ADMIN_EMAIL


# ── Pages d'erreur (HTML propre, ou JSON pour les routes /api) ───────────────
@app.errorhandler(429)
def _err_429(e):
    if request.path.startswith("/api"):
        return jsonify({"erreur": "trop de requêtes, réessaie plus tard"}), 429
    return _auth_page("Trop de tentatives", "Patiente une minute avant de réessayer.",
                      '<p class="err">Trop de tentatives rapprochées.</p>',
                      '<a href="/login">Retour à la connexion</a>'), 429

@app.errorhandler(404)
def _err_404(e):
    if request.path.startswith("/api"):
        return jsonify({"erreur": "introuvable"}), 404
    return _auth_page("Page introuvable", "Cette page n'existe pas.",
                      '<p class="sub">La page demandée est introuvable.</p>',
                      '<a href="/">Retour à l\'accueil</a>'), 404

@app.errorhandler(500)
def _err_500(e):
    if request.path.startswith("/api"):
        return jsonify({"erreur": "erreur serveur"}), 500
    return _auth_page("Erreur", "Une erreur est survenue.",
                      '<p class="err">Une erreur inattendue est survenue. Réessaie.</p>',
                      '<a href="/">Retour à l\'accueil</a>'), 500

# ── Mémoire ───────────────────────────────────────────────────────────────────
alertes   = []
ALERTES_FILE = "alertes.json"

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
# Clé anon (publique) pour l'authentification utilisateur. INDISPENSABLE pour que
# la confirmation par email s'applique : s'inscrire avec la clé service_role
# (SUPABASE_KEY) place Supabase en contexte admin et AUTO-CONFIRME le compte.
# Repli sur la service_role si non définie (comportement actuel, non corrigé).
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY") or SUPABASE_KEY

SB_SERVICE = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

def sb(table):
    return f"{SUPABASE_URL}/rest/v1/{table}"

def sb_auth(path):
    return f"{SUPABASE_URL}/auth/v1{path}"

def user_headers():
    token = session.get("access_token")
    if not token:
        return None
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# ── VAPID ─────────────────────────────────────────────────────────────────────
VAPID_PUBLIC       = os.getenv("VAPID_PUBLIC_KEY", "")
_VAPID_PRIVATE_B64 = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PRIVATE_FILE = None
APP_URL            = os.getenv("APP_URL", "").rstrip("/")

def init_vapid():
    global VAPID_PRIVATE_FILE
    if os.path.exists("vapid_private.pem"):
        VAPID_PRIVATE_FILE = "vapid_private.pem"
    elif _VAPID_PRIVATE_B64:
        try:
            pem = base64.urlsafe_b64decode(_VAPID_PRIVATE_B64 + "==")
            tmp = tempfile.NamedTemporaryFile(mode="wb", suffix=".pem", delete=False)
            tmp.write(pem); tmp.close()
            VAPID_PRIVATE_FILE = tmp.name
        except Exception as e:
            print(f"Erreur VAPID : {e}")


# ── Pages auth ────────────────────────────────────────────────────────────────
_CSS_AUTH = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d0d;color:#fff;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
     min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:0 32px}
.brand{display:flex;align-items:center;justify-content:center;gap:9px;font-size:9px;letter-spacing:3px;color:#333;text-transform:uppercase;font-weight:600;text-align:center;margin-bottom:48px}
.brand .brand-logo{width:24px;height:24px;border-radius:6px;display:block}
h1{font-size:24px;font-weight:700;color:#fff;text-align:center;letter-spacing:-0.5px;margin-bottom:4px}
.sub{font-size:12px;color:#444;text-align:center;margin-bottom:32px}
.form{width:100%;max-width:280px}
input{width:100%;padding:11px 14px;background:#111;border:0.5px solid #222;border-radius:10px;
      color:#fff;font-size:13px;outline:none;transition:border-color 0.2s;margin-bottom:8px;display:block}
input::placeholder{color:#3a3a3a}
input:focus{border-color:#3a3a3a}
button[type=submit]{width:100%;padding:12px;background:#fff;color:#000;border:none;
     border-radius:10px;font-size:13px;font-weight:600;cursor:pointer;margin-top:6px}
.btn-oauth{width:100%;padding:11px;background:#1a1a1a;color:#ccc;border:0.5px solid #2a2a2a;
     border-radius:10px;font-size:13px;font-weight:500;cursor:pointer;margin-top:8px;
     display:flex;align-items:center;justify-content:center;gap:10px;text-decoration:none}
.btn-oauth:hover{background:#222}
.divider{display:flex;align-items:center;gap:10px;margin:16px 0;color:#333;font-size:11px}
.divider::before,.divider::after{content:"";flex:1;height:0.5px;background:#1e1e1e}
.err{font-size:11px;color:#c0392b;text-align:center;margin-bottom:14px}
.ok{font-size:11px;color:#27ae60;text-align:center;margin-bottom:14px;padding:10px;background:#0a1f0a;border-radius:8px;border:0.5px solid #1a3a1a}
.lien{font-size:11px;color:#333;text-align:center;margin-top:22px;line-height:2}
.lien a{color:#555;text-decoration:none}
.lien a:hover{color:#888}
"""

def _auth_page(titre, sous_titre, contenu, liens=""):
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{titre} — Korrel</title>
<style>{_CSS_AUTH}</style></head>
<body>
<div tabindex="0" style="position:fixed;opacity:0;pointer-events:none;width:0;height:0"></div>
<div class="brand"><img class="brand-logo" src="/static/logo.png?v=20260620d" alt=""/>Korrel</div>
<h1>{titre}</h1>
<p class="sub">{sous_titre}</p>
<div class="form">{contenu}</div>
<p class="lien">{liens}</p>
<script>document.querySelector('[tabindex="0"]').focus();</script>
</body></html>"""


_GOOGLE_ICON = '<svg width="16" height="16" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.2l6.7-6.7C35.7 2.4 30.2 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.8 6C12.4 13 17.8 9.5 24 9.5z"/><path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v8.5h12.7c-.6 3-2.3 5.5-4.8 7.2l7.5 5.8c4.4-4.1 7.1-10.1 7.1-17z"/><path fill="#FBBC05" d="M10.5 28.7A14.5 14.5 0 0 1 9.5 24c0-1.6.3-3.2.8-4.7l-7.8-6A23.9 23.9 0 0 0 0 24c0 3.9.9 7.5 2.7 10.7l7.8-6z"/><path fill="#34A853" d="M24 48c6.2 0 11.4-2 15.2-5.5l-7.5-5.8c-2 1.4-4.6 2.3-7.7 2.3-6.2 0-11.5-4.2-13.4-9.8l-7.8 6C6.6 42.6 14.6 48 24 48z"/></svg>'

def _page_login(erreur=""):
    err = f'<p class="err">{erreur}</p>' if erreur else ""
    oauth = f'<a href="/auth/google" class="btn-oauth">{_GOOGLE_ICON} Continuer avec Google</a>'
    return _auth_page(
        "Connexion", "Ton fil d'actu filtré par IA.",
        f"""{err}<form method="POST">
<input type="email" name="email" placeholder="exemple@gmail.com" autocomplete="email"/>
<input type="password" name="password" placeholder="Mot de passe" autocomplete="current-password"/>
<div style="text-align:right;margin-bottom:10px;margin-top:-2px">
  <a href="/forgot-password" style="font-size:11px;color:#444;text-decoration:none">Mot de passe oublié ?</a>
</div>
<button type="submit">Continuer</button></form>
<div class="divider">ou</div>
{oauth}""",
        'Pas encore de compte ? <a href="/register">Inscrivez-vous</a>'
    )


@app.route("/auth/google")
def auth_google():
    redirect_to = f"{APP_URL}/auth/callback" if APP_URL else "/auth/callback"
    url = f"{SUPABASE_URL}/auth/v1/authorize?provider=google&redirect_to={redirect_to}"
    return redirect(url)


@app.route("/auth/callback")
def auth_callback():
    """Page de callback OAuth — le token est dans le fragment URL (#), traité en JS."""
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Connexion — Korrel</title>
<style>*{{margin:0;padding:0}}body{{background:#0d0d0d;color:#fff;font-family:system-ui,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;font-size:13px;color:#555}}</style>
</head><body>Connexion en cours…
<script>
(async () => {{
  const hash = Object.fromEntries(new URLSearchParams(location.hash.slice(1)));
  if (!hash.access_token) {{ location.href = '/login'; return; }}
  const r = await fetch('/api/oauth-session', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{access_token: hash.access_token, refresh_token: hash.refresh_token}})
  }});
  const d = await r.json();
  location.href = d.new_user ? '/onboarding' : '/';
}})();
</script></body></html>"""


@app.route("/api/oauth-session", methods=["POST"])
def api_oauth_session():
    data  = request.get_json()
    token = data.get("access_token", "")
    rt    = data.get("refresh_token", "")
    if not token:
        return jsonify({"erreur": "token manquant"}), 400
    try:
        r = http.get(sb_auth("/user"),
                     headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {token}"}, timeout=8)
        if not r.ok:
            return jsonify({"erreur": "token invalide"}), 401
        u = r.json()
        session.permanent       = True
        session["access_token"] = token
        session["refresh_token"]= rt
        session["user_id"]      = u["id"]
        session["user_email"]   = u["email"]
        # Vérifier si onboarding nécessaire
        r2 = http.get(sb("user_preferences"), headers=SB_SERVICE,
                      params={"user_id": f"eq.{u['id']}", "select": "user_id"}, timeout=5)
        new_user = r2.ok and not r2.json()
        return jsonify({"ok": True, "new_user": new_user})
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        try:
            http.post(sb_auth("/recover"),
                      headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
                      json={"email": email}, timeout=10)
        except Exception:
            pass
        return _auth_page(
            "Email envoyé", "Vérification en cours…",
            '<p class="ok">Si un compte existe pour cet email, tu recevras un lien de réinitialisation.</p>',
            '<a href="/login">Retour à la connexion</a>'
        )
    return _auth_page(
        "Mot de passe oublié", "Entre ton adresse email.",
        """<form method="POST">
<input type="email" name="email" placeholder="exemple@gmail.com" autocomplete="email"/>
<button type="submit">Envoyer le lien</button></form>""",
        '<a href="/login">Retour à la connexion</a>'
    )


@app.route("/reset-password")
def reset_password():
    return _auth_page(
        "Nouveau mot de passe", "Choisis un mot de passe sécurisé.",
        """<p class="err" id="err-msg" style="display:none"></p>
<p class="ok" id="ok-msg" style="display:none">Mot de passe mis à jour — <a href="/login" style="color:#27ae60">Se connecter</a></p>
<form id="form-reset">
<input type="password" id="new-pwd" placeholder="Nouveau mot de passe (6 min.)" autocomplete="new-password"/>
<input type="password" id="confirm-pwd" placeholder="Confirmer le mot de passe" autocomplete="new-password"/>
<button type="submit">Mettre à jour</button></form>
<script>
(async()=>{
  const hash = Object.fromEntries(new URLSearchParams(location.hash.slice(1)));
  const token = hash.access_token;
  if(!token){document.getElementById("err-msg").textContent="Lien invalide ou expiré.";document.getElementById("err-msg").style.display="block";}
  document.getElementById("form-reset").addEventListener("submit",async e=>{
    e.preventDefault();
    const pwd=document.getElementById("new-pwd").value;
    const cpwd=document.getElementById("confirm-pwd").value;
    const err=document.getElementById("err-msg");
    if(pwd.length<6){err.textContent="Mot de passe trop court.";err.style.display="block";return;}
    if(pwd!==cpwd){err.textContent="Les mots de passe ne correspondent pas.";err.style.display="block";return;}
    const r=await fetch("/api/update-password",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({token,password:pwd})});
    if(r.ok){document.getElementById("form-reset").style.display="none";document.getElementById("ok-msg").style.display="block";}
    else{err.textContent="Erreur — réessaie.";err.style.display="block";}
  });
})();
</script>""",
        '<a href="/login">Annuler</a>'
    )


@app.route("/api/update-password", methods=["POST"])
def api_update_password():
    data  = request.get_json()
    token = data.get("token", "")
    pwd   = data.get("password", "")
    if not token or len(pwd) < 6:
        return jsonify({"erreur": "invalide"}), 400
    try:
        r = http.put(sb_auth("/user"),
                     headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {token}",
                              "Content-Type": "application/json"},
                     json={"password": pwd}, timeout=10)
        return (jsonify({"ok": True}), 200) if r.ok else (jsonify({"erreur": "échec"}), 400)
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


@app.route("/privacy")
def privacy():
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Politique de confidentialité — Korrel</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d0d0d;color:#ccc;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
     max-width:680px;margin:0 auto;padding:48px 24px}}
h1{{font-size:22px;font-weight:700;color:#fff;margin-bottom:6px}}
.date{{font-size:12px;color:#555;margin-bottom:40px}}
h2{{font-size:15px;font-weight:600;color:#ddd;margin:32px 0 10px}}
p,li{{font-size:13px;line-height:1.75;color:#888;margin-bottom:8px}}
ul{{padding-left:18px}}
a{{color:#555}}
.back{{font-size:20px;color:#444;text-decoration:none;display:block;margin-bottom:32px;line-height:1}}
.back:hover{{color:#888}}
</style></head><body>
<a class="back" href="javascript:history.back()">←</a>
<h1>Politique de confidentialité</h1>
<p class="date">Dernière mise à jour : {datetime.now().strftime("%d/%m/%Y")}</p>

<h2>1. Données collectées</h2>
<ul>
<li><strong>Compte</strong> : adresse email et mot de passe (chiffré par Supabase Auth)</li>
<li><strong>Préférences</strong> : domaines d'intérêt, thème, langue d'affichage, nom d'affichage</li>
<li><strong>Articles sauvegardés</strong> : identifiants des articles que tu choisis de conserver</li>
<li><strong>Abonnements push</strong> : endpoint de notification pour l'envoi d'alertes (optionnel)</li>
</ul>

<h2>2. Utilisation des données</h2>
<ul>
<li>Personnaliser ton fil d'actualités selon tes domaines d'intérêt</li>
<li>T'envoyer des alertes et un résumé matinal selon tes préférences</li>
<li>Conserver tes articles sauvegardés</li>
</ul>
<p>Nous ne vendons ni ne partageons tes données avec des tiers à des fins commerciales.</p>

<h2>3. Services tiers</h2>
<ul>
<li><strong>Supabase</strong> — hébergement de la base de données et authentification (États-Unis / UE)</li>
<li><strong>Groq</strong> — analyse IA des articles (titres et résumés envoyés pour filtrage). Aucune donnée personnelle n'est transmise.</li>
<li><strong>Render</strong> — hébergement du serveur applicatif</li>
</ul>

<h2>4. Conservation des données</h2>
<ul>
<li>Articles non sauvegardés : supprimés après 3 jours</li>
<li>Articles sauvegardés : conservés 6 mois</li>
<li>Ton compte et tes préférences : conservés tant que ton compte est actif</li>
</ul>

<h2>5. Tes droits</h2>
<p>Tu peux à tout moment :</p>
<ul>
<li>Supprimer ton compte depuis les paramètres de l'app</li>
<li>Demander l'export ou la suppression de tes données à : <a href="mailto:{os.getenv('CONTACT_EMAIL','korrel.news@gmail.com')}">{os.getenv('CONTACT_EMAIL','korrel.news@gmail.com')}</a></li>
</ul>

<h2>6. Cookies et sessions</h2>
<p>Un cookie de session est utilisé uniquement pour maintenir ta connexion (durée 30 jours). Aucun cookie publicitaire ou de tracking.</p>

<h2>7. Contact</h2>
<p>Pour toute question : <a href="mailto:{os.getenv('CONTACT_EMAIL','korrel.news@gmail.com')}">{os.getenv('CONTACT_EMAIL','korrel.news@gmail.com')}</a></p>
</body></html>"""


@app.route("/terms")
def terms():
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Conditions d'utilisation — Korrel</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d0d0d;color:#ccc;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
     max-width:680px;margin:0 auto;padding:48px 24px}}
h1{{font-size:22px;font-weight:700;color:#fff;margin-bottom:6px}}
.date{{font-size:12px;color:#555;margin-bottom:40px}}
h2{{font-size:15px;font-weight:600;color:#ddd;margin:32px 0 10px}}
p,li{{font-size:13px;line-height:1.75;color:#888;margin-bottom:8px}}
ul{{padding-left:18px}}
a{{color:#555}}
.back{{font-size:20px;color:#444;text-decoration:none;display:block;margin-bottom:32px;line-height:1}}
.back:hover{{color:#888}}
</style></head><body>
<a class="back" href="javascript:history.back()">←</a>
<h1>Conditions d'utilisation</h1>
<p class="date">Dernière mise à jour : {datetime.now().strftime("%d/%m/%Y")}</p>

<h2>1. Service</h2>
<p>Korrel est un agrégateur d'actualités qui filtre et synthétise des articles de sources publiques à l'aide d'une IA, selon les centres d'intérêt que tu choisis. Le service est fourni « en l'état », sans garantie de disponibilité continue.</p>

<h2>2. Compte</h2>
<ul>
<li>Le service est réservé aux personnes de 15 ans et plus (ou disposant de l'accord d'un représentant légal).</li>
<li>Tu es responsable de la confidentialité de tes identifiants.</li>
<li>Un compte est strictement personnel.</li>
<li>Tu peux supprimer ton compte à tout moment depuis les paramètres.</li>
</ul>

<h2>3. Contenu et exactitude</h2>
<p>Les titres, résumés et corrélations sont générés automatiquement par IA et peuvent contenir des inexactitudes ou des erreurs. Ils ne constituent pas une information vérifiée, ni un conseil (financier, médical, juridique…). Vérifie toujours l'information à la source avant d'agir.</p>
<p>Les articles restent la propriété de leurs éditeurs respectifs ; Korrel renvoie vers les sources d'origine.</p>

<h2>4. Usage acceptable</h2>
<ul>
<li>Ne pas tenter de perturber, surcharger ou contourner la sécurité du service.</li>
<li>Ne pas réutiliser le contenu de manière automatisée sans autorisation.</li>
</ul>

<h2>5. Notifications</h2>
<p>Les notifications push sont optionnelles et désactivables à tout moment dans les paramètres de l'appareil ou de l'app.</p>

<h2>6. Responsabilité</h2>
<p>Korrel ne saurait être tenu responsable des décisions prises sur la base des informations affichées, ni des interruptions de service liées aux prestataires tiers (hébergement, sources, IA).</p>

<h2>7. Évolution</h2>
<p>Ces conditions peuvent évoluer. La date de dernière mise à jour figure en haut de page.</p>

<h2>8. Droit applicable</h2>
<p>Les présentes conditions sont régies par le droit français. À défaut de résolution amiable, tout litige relève de la compétence des tribunaux français.</p>

<h2>9. Contact</h2>
<p>Pour toute question : <a href="mailto:{os.getenv('CONTACT_EMAIL','korrel.news@gmail.com')}">{os.getenv('CONTACT_EMAIL','korrel.news@gmail.com')}</a></p>

<p style="margin-top:32px"><a href="/privacy">Politique de confidentialité →</a></p>
</body></html>"""


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute; 40 per hour", methods=["POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        pwd   = request.form.get("password", "")
        try:
            r = http.post(sb_auth("/token?grant_type=password"),
                          headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
                          json={"email": email, "password": pwd}, timeout=10)
            if r.ok:
                d = r.json()
                session.permanent        = True
                session["access_token"]  = d["access_token"]
                session["refresh_token"] = d.get("refresh_token")
                session["user_id"]       = d["user"]["id"]
                session["user_email"]    = d["user"]["email"]
                # vérifier si nouvel utilisateur
                r2 = http.get(sb("user_preferences"),
                              headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {d['access_token']}",
                                       "Content-Type": "application/json"},
                              params={"user_id": f"eq.{d['user']['id']}"}, timeout=5)
                if r2.ok and not r2.json():
                    return redirect("/onboarding")
                return redirect("/")
            return _page_login("Email ou mot de passe incorrect")
        except Exception as e:
            return _page_login(f"Erreur : {e}")
    return _page_login()

def _register_form(erreur=""):
    err = f'<p class="err">{erreur}</p>' if erreur else ""
    oauth = f'<a href="/auth/google" class="btn-oauth">{_GOOGLE_ICON} Continuer avec Google</a>'
    return _auth_page(
        "Créer un compte", "Ton fil d'actu filtré par IA.",
        f"""{err}<form method="POST">
<input type="email" name="email" placeholder="exemple@gmail.com" autocomplete="email"/>
<input type="password" name="password" placeholder="Mot de passe (6 min.)" autocomplete="new-password"/>
<input type="password" name="confirm" placeholder="Confirmer le mot de passe" autocomplete="new-password"/>
<button type="submit">Créer mon compte</button></form>
<div class="divider">ou</div>
{oauth}""",
        'Déjà un compte ? <a href="/login">Se connecter</a><br><a href="/privacy" style="color:#333">Confidentialité</a> · <a href="/terms" style="color:#333">Conditions d\'utilisation</a>'
    )

@app.route("/register", methods=["GET", "POST"])
@limiter.limit("6 per minute; 20 per hour", methods=["POST"])
def register():
    if request.method == "POST":
        email   = request.form.get("email", "").strip()
        pwd     = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if pwd != confirm:
            return _register_form("Les mots de passe ne correspondent pas")
        if len(pwd) < 6:
            return _register_form("Mot de passe trop court (6 min.)")
        try:
            r = http.post(sb_auth("/signup"),
                          headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
                          json={"email": email, "password": pwd}, timeout=10)
            if r.ok:
                d = r.json()
                if d.get("access_token"):
                    session.permanent        = True
                    session["access_token"]  = d["access_token"]
                    session["refresh_token"] = d.get("refresh_token")
                    session["user_id"]       = d["user"]["id"]
                    session["user_email"]    = d["user"]["email"]
                    return redirect("/onboarding")
                return _auth_page(
                    "Vérifie tes emails", "Un lien de confirmation t'a été envoyé.",
                    '<p class="ok">Clique sur le lien dans l\'email pour activer ton compte.</p>',
                    '<a href="/login">Se connecter</a>'
                )
            err = r.json().get("msg") or r.json().get("error_description") or "Erreur"
            return _register_form(err)
        except Exception as e:
            return _register_form(f"Erreur : {e}")
    return _register_form()

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

_ONBOARDING_HTML = """<!DOCTYPE html>
<html lang="fr" data-theme="dark"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"/>
<title>Korrel</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
:root, [data-theme="dark"]  { --bg:#000;     --text:#f0f0f0; --sub:#6a6a6a; --line:#1c1c1c; --surface:#0e0e0e; --accent:#d4691f; }
[data-theme="dim"]          { --bg:#161b22;  --text:#e6edf3; --sub:#8b949e; --line:#30363d; --surface:#1c2128; --accent:#e07b2e; }
[data-theme="light"]        { --bg:#fff;     --text:#111;    --sub:#999;    --line:#e8e8e8; --surface:#f6f6f6; --accent:#c85c12; }
html, body { height: 100%; }
body { background: var(--bg); color: var(--text);
       font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif;
       -webkit-tap-highlight-color: transparent; }

.wiz { display: flex; flex-direction: column; height: 100dvh; max-width: 520px; margin: 0 auto; }

.wiz-top { display: flex; align-items: center; gap: 14px;
           padding: calc(16px + env(safe-area-inset-top)) 24px 0; }
.back { width: 34px; height: 34px; flex-shrink: 0; border: none; background: none;
        color: var(--sub); font-size: 28px; line-height: 1; cursor: pointer; border-radius: 8px;
        display: flex; align-items: center; justify-content: center; transition: color 0.15s; }
.back:active { color: var(--text); }
.back.hide { visibility: hidden; }
.bar { flex: 1; height: 4px; background: var(--line); border-radius: 99px; overflow: hidden; }
.bar-fill { height: 100%; width: 0; background: var(--accent); border-radius: 99px;
            transition: width 0.35s cubic-bezier(.4,0,.2,1); }

.screens { flex: 1; overflow-y: auto; padding: 0 24px; }
.screen { display: none; min-height: 100%; flex-direction: column; justify-content: center; padding: 28px 0; }
.screen.on { display: flex; animation: fade 0.28s ease; }
@keyframes fade { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }

.s-kicker { font-size: 11px; font-weight: 600; letter-spacing: 0.09em; text-transform: uppercase;
            color: var(--accent); margin-bottom: 14px; }
.s-kicker.brand-kicker { display: flex; align-items: center; gap: 9px; }
.s-kicker .kicker-logo { width: 24px; height: 24px; border-radius: 6px; display: block; }
.s-title { font-size: 27px; font-weight: 700; line-height: 1.22; letter-spacing: -0.5px; margin-bottom: 12px; }
.s-sub { font-size: 14px; color: var(--sub); line-height: 1.6; margin-bottom: 28px; }

input[type=text] { width: 100%; padding: 15px 17px; background: var(--surface);
                   border: 1px solid var(--line); border-radius: 12px; color: var(--text);
                   font-size: 16px; outline: none; transition: border-color 0.15s; }
input[type=text]:focus { border-color: var(--accent); }

.choices { display: flex; flex-direction: column; gap: 10px; }
.choice { display: flex; align-items: center; gap: 13px; padding: 16px 18px;
          border: 1px solid var(--line); border-radius: 12px; background: var(--surface);
          cursor: pointer; transition: border-color 0.15s; }
.choice .dot { width: 20px; height: 20px; border-radius: 50%; border: 2px solid var(--line);
               flex-shrink: 0; position: relative; transition: border-color 0.15s; }
.choice.on { border-color: var(--accent); }
.choice.on .dot { border-color: var(--accent); }
.choice.on .dot::after { content: ""; position: absolute; inset: 3px; border-radius: 50%; background: var(--accent); }
.choice .lbl { font-size: 15px; font-weight: 500; }

.age-attest { display: flex; align-items: flex-start; gap: 11px; padding: 16px 18px;
              border: 1px solid var(--line); border-radius: 12px; background: var(--surface);
              cursor: pointer; font-size: 13px; color: var(--sub); line-height: 1.5; }
.age-attest input { width: 20px; height: 20px; flex-shrink: 0; margin-top: 1px; accent-color: var(--accent); }
.age-attest a { color: var(--text); text-decoration: underline; }

.chips { display: flex; flex-wrap: wrap; gap: 10px; }
.chip { padding: 11px 17px; border-radius: 10px; border: 1px solid var(--line);
        background: var(--surface); color: var(--sub); font-size: 14px; font-weight: 500;
        cursor: pointer; transition: border-color 0.15s, color 0.15s; user-select: none; }
.chip.on { border-color: var(--accent); color: var(--text); }

.themes { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.theme-card { border: 1px solid var(--line); border-radius: 12px; padding: 16px 10px;
              cursor: pointer; text-align: center; transition: border-color 0.15s; background: var(--surface); }
.theme-card.on { border-color: var(--accent); }
.theme-swatch { height: 38px; border-radius: 7px; margin-bottom: 10px; }
.sw-dark  { background: #000;    border: 1px solid #2a2a2a; }
.sw-dim   { background: #161b22; border: 1px solid #30363d; }
.sw-light { background: #fff;    border: 1px solid #e0e0e0; }
.theme-name { font-size: 12px; font-weight: 500; color: var(--sub); }
.theme-card.on .theme-name { color: var(--text); }

.avatar-pick { display: flex; align-items: center; gap: 18px; }
.ob-avatar { position: relative; width: 84px; height: 84px; border-radius: 50%; flex-shrink: 0;
             background: var(--surface); border: 1px solid var(--line); cursor: pointer;
             background-size: cover; background-position: center; color: var(--sub);
             display: flex; align-items: center; justify-content: center; }
.ob-avatar.has-photo svg { display: none; }
.ob-avatar-cam { position: absolute; right: 0; bottom: 0; width: 26px; height: 26px;
             border-radius: 50%; background: var(--accent); color: #fff;
             display: flex; align-items: center; justify-content: center; border: 2px solid var(--bg); }
.avatar-hint { font-size: 13px; color: var(--sub); line-height: 1.5; }

.ob-sel { position: relative; }
.ob-select { width: 100%; padding: 15px 42px 15px 17px; background: var(--surface);
             border: 1px solid var(--line); border-radius: 12px; color: var(--text);
             font-size: 16px; outline: none; cursor: pointer; appearance: none; -webkit-appearance: none; }
.ob-sel::after { content: ""; position: absolute; right: 18px; top: 50%; width: 9px; height: 9px;
             border-right: 2px solid var(--sub); border-bottom: 2px solid var(--sub);
             transform: translateY(-70%) rotate(45deg); pointer-events: none; }

.portees-section { display: flex; flex-direction: column; }
.portee-row { display: flex; align-items: center; justify-content: space-between;
              padding: 14px 0; border-bottom: 1px solid var(--line); gap: 12px; }
.portee-row:last-child { border-bottom: none; }
.portee-domain { font-size: 14px; color: var(--text); }
.portee-btns { display: flex; gap: 6px; flex-shrink: 0; }
.portee-btn { padding: 7px 13px; border-radius: 8px; border: 1px solid var(--line);
              background: none; color: var(--sub); font-size: 12px; font-weight: 500; cursor: pointer; transition: border-color 0.15s, color 0.15s; }
.portee-btn.on { border-color: var(--accent); color: var(--text); }

.notif-card { display: flex; align-items: center; justify-content: space-between; gap: 16px;
              padding: 16px; background: var(--surface); border: 1px solid var(--line); border-radius: 12px; }
.notif-titre { font-size: 14px; font-weight: 600; margin-bottom: 4px; }
.notif-desc { font-size: 12px; color: var(--sub); line-height: 1.5; }
.btn-notif-ob { flex-shrink: 0; padding: 10px 18px; background: var(--accent); color: #fff;
                border: none; border-radius: 9px; font-size: 13px; font-weight: 600; cursor: pointer;
                white-space: nowrap; transition: opacity 0.15s; }
.btn-notif-ob:disabled { opacity: 0.4; cursor: default; }

.opt-row { display: flex; align-items: center; justify-content: space-between; gap: 16px;
           padding: 14px 16px; background: var(--surface); border: 1px solid var(--line);
           border-radius: 12px; margin-top: 10px; }
.opt-titre { font-size: 14px; font-weight: 600; margin-bottom: 3px; }
.opt-desc { font-size: 12px; color: var(--sub); line-height: 1.45; }
.sw-toggle { position: relative; width: 44px; height: 26px; flex-shrink: 0; }
.sw-toggle input { opacity: 0; width: 0; height: 0; }
.sw-toggle .knob { position: absolute; inset: 0; background: var(--line); border-radius: 999px;
                   cursor: pointer; transition: background 0.15s; }
.sw-toggle .knob::before { content: ""; position: absolute; height: 20px; width: 20px; left: 3px; top: 3px;
                   background: var(--text); border-radius: 50%; transition: transform 0.15s; }
.sw-toggle input:checked + .knob { background: var(--accent); }
.sw-toggle input:checked + .knob::before { transform: translateX(18px); background: #fff; }

.wiz-foot { padding: 16px 24px calc(18px + env(safe-area-inset-bottom)); }
.next { width: 100%; padding: 16px; background: var(--text); color: var(--bg); border: none;
        border-radius: 12px; font-size: 15px; font-weight: 600; cursor: pointer; transition: opacity 0.15s; }
.next:disabled { opacity: 0.3; cursor: default; }
.cancel { display: block; text-align: center; margin-top: 14px; font-size: 13px;
          color: var(--sub); text-decoration: none; }
</style></head>
<body>

<div class="wiz">
  <div class="wiz-top">
    <button class="back hide" id="back" onclick="goPrev()" aria-label="Retour">&lsaquo;</button>
    <div class="bar"><div class="bar-fill" id="bar"></div></div>
  </div>

  <div class="screens" id="screens">

    <section class="screen on" data-next="Commencer">
      <div class="s-kicker brand-kicker"><img class="kicker-logo" src="/static/logo.png?v=20260620d" alt=""/>Korrel</div>
      <div class="s-title">Parle-nous de toi</div>
      <div class="s-sub">Quelques questions rapides pour personnaliser ton fil d'actualité. Ça prend moins d'une minute, et tu pourras tout changer plus tard.</div>
    </section>

    <section class="screen">
      <div class="s-kicker">Profil</div>
      <div class="s-title">Une photo ?</div>
      <div class="s-sub">Facultatif. Elle apparaît sur ta carte de compte.</div>
      <div class="avatar-pick">
        <button type="button" class="ob-avatar" id="ob-avatar"
                onclick="document.getElementById('ob-avatar-input').click()" aria-label="Ajouter une photo">
          <svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor"
               stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
            <circle cx="12" cy="13" r="4"/>
          </svg>
          <span class="ob-avatar-cam">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
                 stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
          </span>
        </button>
        <div class="avatar-hint">Touche le cercle<br>pour choisir une image</div>
      </div>
      <input type="file" id="ob-avatar-input" accept="image/*" style="display:none" onchange="changerPhotoOb(this)"/>
    </section>

    <section class="screen">
      <div class="s-kicker">Profil</div>
      <div class="s-title">Comment t'appeler ?</div>
      <div class="s-sub">Utilisé dans tes notifications et résumés. Facultatif.</div>
      <input type="text" id="display-name" placeholder="Prénom ou pseudo" maxlength="30" autocomplete="off"/>
    </section>

    <section class="screen">
      <div class="s-kicker">Profil</div>
      <div class="s-title">Tu es…</div>
      <div class="s-sub">Pour adapter le ton de tes résumés. Facultatif.</div>
      <div class="choices" id="genre-choices">
        <div class="choice" data-v="homme"><span class="dot"></span><span class="lbl">Homme</span></div>
        <div class="choice" data-v="femme"><span class="dot"></span><span class="lbl">Femme</span></div>
        <div class="choice" data-v="autre"><span class="dot"></span><span class="lbl">Autre</span></div>
        <div class="choice" data-v="non_precise"><span class="dot"></span><span class="lbl">Préfère ne pas dire</span></div>
      </div>
    </section>

    <section class="screen" data-require="domaines">
      <div class="s-kicker">Centres d'intérêt</div>
      <div class="s-title">Quels sujets suivre ?</div>
      <div class="s-sub">Choisis-en au moins un. Tu pourras en ajouter ou en retirer à tout moment.</div>
      <div class="chips" id="domaines-chips">
        <button type="button" class="chip" data-d="🌍 Géopolitique">🌍 Géopolitique</button>
        <button type="button" class="chip" data-d="🔬 Science">🔬 Science</button>
        <button type="button" class="chip" data-d="💻 Tech & IA">💻 Tech &amp; IA</button>
        <button type="button" class="chip" data-d="💰 Finance">💰 Finance</button>
        <button type="button" class="chip" data-d="🌱 Environnement">🌱 Environnement</button>
        <button type="button" class="chip" data-d="⚽ Sport">⚽ Sport</button>
      </div>
    </section>

    <section class="screen">
      <div class="s-kicker">Couverture</div>
      <div class="s-title">Mondial ou national ?</div>
      <div class="s-sub">Pour chaque domaine, indique si tu veux aussi l'actualité nationale de ton pays.</div>
      <div class="portees-section" id="portees-section"></div>
    </section>

    <section class="screen">
      <div class="s-kicker">Région</div>
      <div class="s-title">Ton pays</div>
      <div class="s-sub">Pour filtrer les actus nationales. Seule la France est entièrement supportée pour l'instant.</div>
      <div class="ob-sel">
        <select id="onb-pays" class="ob-select">
          <option value="France">France</option>
          <option value="Belgique">Belgique</option>
          <option value="Suisse">Suisse</option>
          <option value="Canada">Canada</option>
          <option value="Côte d'Ivoire">Côte d'Ivoire</option>
          <option value="Sénégal">Sénégal</option>
          <option value="Maroc">Maroc</option>
          <option value="Tunisie">Tunisie</option>
          <option value="Algérie">Algérie</option>
          <option value="RD Congo">RD Congo</option>
          <option value="tous">Tous les pays</option>
        </select>
      </div>
    </section>

    <section class="screen">
      <div class="s-kicker">Langue</div>
      <div class="s-title">Langue d'affichage</div>
      <div class="s-sub">Traduction par IA — peut contenir des inexactitudes. Modifiable à tout moment.</div>
      <div class="ob-sel">
        <select id="onb-langue" class="ob-select">
          <option value="multi">Original (langue de la source)</option>
          <option value="fr">Français</option>
          <option value="en">English</option>
          <option value="es">Español</option>
          <option value="de">Deutsch</option>
        </select>
      </div>
    </section>

    <section class="screen">
      <div class="s-kicker">Apparence</div>
      <div class="s-title">Choisis ton thème</div>
      <div class="s-sub">Modifiable dans les paramètres.</div>
      <div class="themes">
        <button type="button" class="theme-card on" data-t="dark">
          <div class="theme-swatch sw-dark"></div><div class="theme-name">Noir</div>
        </button>
        <button type="button" class="theme-card" data-t="dim">
          <div class="theme-swatch sw-dim"></div><div class="theme-name">Gris</div>
        </button>
        <button type="button" class="theme-card" data-t="light">
          <div class="theme-swatch sw-light"></div><div class="theme-name">Blanc</div>
        </button>
      </div>
    </section>

    <section class="screen">
      <div class="s-kicker">Alertes</div>
      <div class="s-title">Reste informé en temps réel</div>
      <div class="s-sub">Une notification immédiate pour les événements qui comptent vraiment.</div>
      <div class="notif-card">
        <div class="notif-info">
          <div class="notif-titre">Alertes en temps réel</div>
          <div class="notif-desc">Guerres, catastrophes, découvertes majeures — uniquement l'essentiel.</div>
        </div>
        <button type="button" class="btn-notif-ob" id="btn-notif-ob" onclick="demanderNotifs()">Activer</button>
      </div>
      <div id="notif-state" style="font-size:13px;color:var(--sub);margin-top:12px;display:none"></div>
      <div class="opt-row">
        <div>
          <div class="opt-titre">Inclure les importantes</div>
          <div class="opt-desc">Alertes 🟡 en plus des critiques 🔴</div>
        </div>
        <label class="sw-toggle"><input type="checkbox" id="onb-important"/><span class="knob"></span></label>
      </div>
      <div class="opt-row">
        <div>
          <div class="opt-titre">Corrélations du matin</div>
          <div class="opt-desc">Le récap quotidien des sujets liés</div>
        </div>
        <label class="sw-toggle"><input type="checkbox" id="onb-corr" checked/><span class="knob"></span></label>
      </div>
    </section>

    <section class="screen" data-next="Entrer dans Korrel" data-require="age">
      <div class="s-kicker">C'est prêt</div>
      <div class="s-title" id="done-title">Bienvenue !</div>
      <div class="s-sub">Ton fil est configuré. Tu peux tout ajuster dans les paramètres quand tu le souhaites.</div>
      <label class="age-attest">
        <input type="checkbox" id="onb-age" onchange="validate()"/>
        <span>Je certifie avoir au moins 15 ans et j'accepte les
          <a href="/terms" target="_blank">conditions d'utilisation</a> et la
          <a href="/privacy" target="_blank">politique de confidentialité</a>.</span>
      </label>
    </section>

  </div>

  <div class="wiz-foot">
    <button class="next" id="next-btn" onclick="goNext()">Suivant</button>
    <a href="/cancel-register" class="cancel">Annuler et supprimer mon compte</a>
  </div>
</div>

<script>
  let obAvatar = "";
  const screensEl = document.getElementById("screens");
  const screens   = [...document.querySelectorAll(".screen")];
  const backBtn   = document.getElementById("back");
  const nextBtn   = document.getElementById("next-btn");
  const bar       = document.getElementById("bar");
  const porteeSection = document.getElementById("portees-section");
  let i = 0;

  function render() {
    screens.forEach((s, n) => s.classList.toggle("on", n === i));
    bar.style.width = (i / (screens.length - 1)) * 100 + "%";
    backBtn.classList.toggle("hide", i === 0);
    const s = screens[i];
    nextBtn.textContent = s.dataset.next || "Suivant";
    if (porteeSection && s.contains(porteeSection)) updatePorteeRows();
    if (s.querySelector("#done-title")) {
      const nom = document.getElementById("display-name").value.trim();
      document.getElementById("done-title").textContent = nom ? ("Bienvenue, " + nom + " 👋") : "Bienvenue 👋";
    }
    validate();
    screensEl.scrollTop = 0;
    const t = s.querySelector("input[type=text]");
    if (t) setTimeout(() => t.focus(), 60);
  }

  function validate() {
    const s = screens[i];
    let ok = true;
    if (s.dataset.require === "domaines") {
      ok = document.querySelectorAll("#domaines-chips .chip.on").length > 0;
    } else if (s.dataset.require === "age") {
      ok = document.getElementById("onb-age").checked;
    }
    nextBtn.disabled = !ok;
  }

  function goNext() {
    if (nextBtn.disabled) return;
    if (i === screens.length - 1) { submit(); return; }
    i++; render();
  }
  function goPrev() { if (i > 0) { i--; render(); } }

  // Entrée = avancer (sur les écrans avec champ texte)
  document.addEventListener("keydown", e => {
    if (e.key === "Enter" && !nextBtn.disabled) { e.preventDefault(); goNext(); }
  });

  // Domaines (multi)
  document.querySelectorAll("#domaines-chips .chip").forEach(b => {
    b.addEventListener("click", () => { b.classList.toggle("on"); validate(); });
  });

  // Sexe (choix unique)
  document.querySelectorAll("#genre-choices .choice").forEach(c => {
    c.addEventListener("click", () => {
      document.querySelectorAll("#genre-choices .choice").forEach(x => x.classList.remove("on"));
      c.classList.add("on");
    });
  });

  // Thème (aperçu live)
  document.querySelectorAll(".theme-card").forEach(b => {
    b.addEventListener("click", () => {
      document.querySelectorAll(".theme-card").forEach(x => x.classList.remove("on"));
      b.classList.add("on");
      document.documentElement.setAttribute("data-theme", b.dataset.t);
    });
  });

  // Portées : une ligne par domaine sélectionné
  function updatePorteeRows() {
    const selected = [...document.querySelectorAll("#domaines-chips .chip.on")].map(b => b.dataset.d);
    porteeSection.querySelectorAll(".portee-row").forEach(row => {
      if (!selected.includes(row.dataset.domain)) row.remove();
    });
    selected.forEach(domain => {
      if (!porteeSection.querySelector('[data-domain="' + domain + '"]')) {
        const row = document.createElement("div");
        row.className = "portee-row";
        row.dataset.domain = domain;
        row.innerHTML = '<span class="portee-domain">' + domain + '</span>'
          + '<div class="portee-btns">'
          + '<button type="button" class="portee-btn on" data-p="mondiale">Mondial</button>'
          + '<button type="button" class="portee-btn" data-p="tout">+ National</button>'
          + '</div>';
        row.querySelectorAll(".portee-btn").forEach(btn => {
          btn.addEventListener("click", () => {
            row.querySelectorAll(".portee-btn").forEach(x => x.classList.remove("on"));
            btn.classList.add("on");
          });
        });
        porteeSection.appendChild(row);
      }
    });
    if (!selected.length) {
      porteeSection.innerHTML = '<div class="s-sub" style="margin:0">Aucun domaine sélectionné — reviens en arrière pour en choisir.</div>';
    }
  }

  // Photo : recadrage carré 256px JPEG
  async function changerPhotoOb(input) {
    const file = input.files && input.files[0];
    if (!file) return;
    const dataURL = await new Promise((res, rej) => {
      const fr = new FileReader();
      fr.onload = () => res(fr.result);
      fr.onerror = rej;
      fr.readAsDataURL(file);
    });
    const img = new Image();
    img.onload = () => {
      const T = 256;
      const cv = document.createElement("canvas");
      cv.width = cv.height = T;
      const ctx = cv.getContext("2d");
      const c = Math.min(img.width, img.height);
      ctx.drawImage(img, (img.width - c) / 2, (img.height - c) / 2, c, c, 0, 0, T, T);
      obAvatar = cv.toDataURL("image/jpeg", 0.82);
      const av = document.getElementById("ob-avatar");
      av.style.backgroundImage = "url('" + obAvatar + "')";
      av.classList.add("has-photo");
    };
    img.src = dataURL;
    input.value = "";
  }

  function urlB64ToUint8Array(b64) {
    const pad = "=".repeat((4 - b64.length % 4) % 4);
    const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return new Uint8Array([...raw].map(c => c.charCodeAt(0)));
  }

  async function demanderNotifs() {
    const btn   = document.getElementById("btn-notif-ob");
    const state = document.getElementById("notif-state");
    btn.disabled = true;
    btn.textContent = "…";
    try {
      const perm = await Notification.requestPermission();
      if (perm !== "granted") {
        state.textContent = "Notifications refusées — tu pourras les activer plus tard dans les paramètres.";
        state.style.display = "block";
        btn.textContent = "Refusé";
        return;
      }
      const reg = await navigator.serviceWorker.register("/sw.js");
      await navigator.serviceWorker.ready;
      const { key } = await fetch("/api/vapid-public").then(r => r.json());
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlB64ToUint8Array(key)
      });
      await fetch("/api/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sub)
      });
      btn.textContent = "✓ Activées";
      state.textContent = "Tu recevras les alertes critiques en temps réel.";
      state.style.display = "block";
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "Activer";
      state.textContent = "Erreur — réessaie depuis les paramètres.";
      state.style.display = "block";
    }
  }

  if (Notification.permission === "granted") {
    const b = document.getElementById("btn-notif-ob"); if (b) { b.textContent = "✓ Activées"; b.disabled = true; }
  } else if (Notification.permission === "denied") {
    const b = document.getElementById("btn-notif-ob"); if (b) { b.textContent = "Bloquées"; b.disabled = true; }
  }

  async function submit() {
    nextBtn.disabled = true;
    nextBtn.textContent = "…";
    const domaines = [...document.querySelectorAll("#domaines-chips .chip.on")].map(b => b.dataset.d);
    const portees  = {};
    document.querySelectorAll("#portees-section .portee-row").forEach(row => {
      portees[row.dataset.domain] = row.querySelector(".portee-btn.on")?.dataset.p || "mondiale";
    });
    const prefs = {
      display_name: document.getElementById("display-name").value.trim(),
      theme:        document.querySelector(".theme-card.on")?.dataset.t || "dark",
      domaines, portees,
      pays:         document.getElementById("onb-pays").value,
      langue:       document.getElementById("onb-langue").value,
      niveau_notif: document.getElementById("onb-important").checked ? 2 : 3,
      notif_correlations: document.getElementById("onb-corr").checked,
    };
    const genre = document.querySelector("#genre-choices .choice.on")?.dataset.v;
    if (genre) prefs.genre = genre;
    if (obAvatar) prefs.avatar = obAvatar;
    await fetch("/api/preferences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(prefs)
    });
    window.location.href = "/";
  }

  render();
</script>
</body></html>"""

@app.route("/onboarding")
def onboarding():
    if not session.get("access_token"):
        return redirect("/login")
    return _ONBOARDING_HTML

@app.route("/api/delete-account", methods=["POST"])
def api_delete_account():
    """Supprime définitivement le compte de l'utilisateur connecté."""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"erreur": "non authentifié"}), 401
    try:
        # Supprimer les données utilisateur de Supabase
        for table in ["user_sauvegardes", "user_preferences", "user_subscriptions"]:
            http.delete(sb(table), headers=SB_SERVICE,
                        params={"user_id": f"eq.{user_id}"}, timeout=8)
        # Supprimer le compte Auth Supabase
        http.delete(f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
                    headers={**SB_SERVICE, "Content-Type": "application/json"}, timeout=10)
        session.clear()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


@app.route("/cancel-register")
def cancel_register():
    """Supprime le compte créé si l'utilisateur annule l'onboarding."""
    token   = session.get("access_token")
    user_id = session.get("user_id")
    if token and user_id:
        try:
            http.delete(
                f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
                headers={**SB_SERVICE, "Content-Type": "application/json"},
                timeout=10
            )
        except Exception as e:
            print(f"Erreur suppression compte : {e}")
    session.clear()
    return redirect("/login")

@app.route("/api/refresh-token", methods=["POST"])
def refresh_token():
    rt = session.get("refresh_token")
    if not rt:
        return jsonify({"erreur": "pas de refresh token"}), 401
    try:
        r = http.post(sb_auth("/token?grant_type=refresh_token"),
                      headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
                      json={"refresh_token": rt}, timeout=10)
        if r.ok:
            d = r.json()
            session.permanent       = True
            session["access_token"] = d["access_token"]
            if d.get("refresh_token"):
                session["refresh_token"] = d["refresh_token"]
            return jsonify({"ok": True})
        return jsonify({"erreur": "refresh échoué"}), 401
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


@app.before_request
def check_auth():
    exempts = ["/health", "/sw.js", "/login", "/register", "/onboarding", "/cancel-register",
               "/api/refresh-token", "/forgot-password", "/reset-password",
               "/api/update-password", "/privacy", "/terms", "/auth/google", "/auth/callback",
               "/api/oauth-session", "/api/cron/recap"]
    # /static/ public : Chrome récupère manifest + icônes SANS cookie (fetch anonyme),
    # sinon ils sont redirigés vers /login et la PWA devient "non installable".
    if request.path in exempts or request.path.startswith("/a/") or request.path.startswith("/static/"):
        return
    if not session.get("access_token"):
        if request.path.startswith("/api"):
            return jsonify({"erreur": "non authentifié"}), 401
        return redirect("/login")


# ── Alertes (globales) ────────────────────────────────────────────────────────
# Mots-clés identifiant chaque domaine dans la colonne `domaine` (ex: "🌍 Géopolitique").
DOMAINES_CLES = ["Géo", "Science", "Tech", "Finance", "Environnement", "Sport"]

def _completer_domaines(liste, mini=6):
    """Aucun domaine ne doit disparaître du feed faute d'actu récente.
    Pour chaque domaine sous-représenté dans les 250 alertes récentes,
    on complète avec ses dernières alertes en date."""
    ids = {a.get("id") for a in liste}
    for cle in DOMAINES_CLES:
        present = sum(1 for a in liste if cle in (a.get("domaine") or ""))
        if present >= mini:
            continue
        try:
            r = http.get(sb("alertes"), headers=SB_SERVICE,
                         params={"order": "date.desc", "limit": str(mini),
                                 "domaine": f"ilike.*{cle}*"}, timeout=10)
            if r.ok:
                for a in r.json():
                    if a.get("id") not in ids:
                        liste.append(a)
                        ids.add(a.get("id"))
        except Exception as e:
            print(f"Supabase _completer_domaines {cle} : {e}")

def charger_alertes():
    if SUPABASE_URL:
        try:
            r = http.get(sb("alertes"), headers=SB_SERVICE,
                         params={"order": "date.desc", "limit": "300"}, timeout=10)
            if r.ok:
                liste = r.json()
                _completer_domaines(liste)
                return liste
        except Exception as e:
            print(f"Supabase charger_alertes : {e}")
    if os.path.exists(ALERTES_FILE):
        with open(ALERTES_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def sauver_alerte(alerte):
    if SUPABASE_URL:
        try:
            hdr = {**SB_SERVICE, "Prefer": "resolution=merge-duplicates,return=minimal"}
            r = http.post(sb("alertes"), headers=hdr, json=alerte, timeout=10)
            # Colonnes optionnelles pas encore créées → réessaie sans, pour ne pas perdre l'alerte
            cols_opt = ("image", "pays")
            if not r.ok and any(c in alerte for c in cols_opt):
                r = http.post(sb("alertes"), headers=hdr,
                              json={k: v for k, v in alerte.items() if k not in cols_opt}, timeout=10)
            if r.ok:
                return
            print(f"Supabase sauver_alerte HTTP {r.status_code} : {r.text[:200]}")
            return
        except Exception as e:
            print(f"Supabase sauver_alerte : {e}")
    with open(ALERTES_FILE, "w", encoding="utf-8") as f:
        json.dump(alertes, f, ensure_ascii=False, indent=2)


# ── Push notifications ────────────────────────────────────────────────────────
def envoyer_push(titre, body, url, niveau=3, tag=None):
    if not VAPID_PRIVATE_FILE or not SUPABASE_URL:
        return
    payload = {"title": titre, "body": body, "url": url}
    if tag:
        payload["tag"] = tag
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return
    try:
        r = http.get(sb("user_subscriptions"), headers=SB_SERVICE, timeout=5)
        subs = r.json() if r.ok else []
    except:
        return
    for item in subs:
        if niveau < item.get("niveau_min", 3):
            continue
        sub = item.get("subscription")
        if not sub:
            continue
        try:
            webpush(subscription_info=sub,
                    data=json.dumps(payload),
                    vapid_private_key=VAPID_PRIVATE_FILE,
                    vapid_claims={"sub": "mailto:korrel.news@gmail.com"})
        except Exception as e:
            if hasattr(e, "response") and e.response and e.response.status_code in [404, 410]:
                http.delete(sb("user_subscriptions"), headers=SB_SERVICE,
                            params={"id": f"eq.{item.get('id')}"}, timeout=5)
            else:
                print(f"Push error : {e}")


# ── Callbacks bot ─────────────────────────────────────────────────────────────
def ajouter_alerte(domaine, titre, teaser, lien, description="", niveau=2, source=None, image=""):
    accroche = teaser.get("accroche", "") if isinstance(teaser, dict) else teaser
    alerte = {
        "id":          int(datetime.now().timestamp() * 1000),
        "domaine":     domaine,
        "titre":       titre,
        "titre_fr":    teaser.get("titre_fr", "") if isinstance(teaser, dict) else "",
        "accroche":    accroche,
        "contexte":    teaser.get("contexte", "") if isinstance(teaser, dict) else "",
        "suite":       teaser.get("suite", "")    if isinstance(teaser, dict) else "",
        "description": description[:1200],
        "lien":        lien,
        "image":       image or "",
        "date":        datetime.now().isoformat(),
        "niveau":      niveau,
        "portee":      teaser.get("portee", "mondiale") if isinstance(teaser, dict) else "mondiale",
        "pays":        teaser.get("pays", "")           if isinstance(teaser, dict) else "",
        "theme_fin":   teaser.get("theme_fin", "")      if isinstance(teaser, dict) else "",
        "sources":     [source] if source else [],
    }
    alertes.insert(0, alerte)
    if len(alertes) > 250:
        alertes.pop()
    sauver_alerte(alerte)

    notif_url = f"{APP_URL}/#synthese/{alerte['id']}" if APP_URL else lien
    # Épuré : titre = titre de l'article ; corps = surtitre domaine + accroche.
    emoji    = "🔴" if niveau >= 3 else "🟡"
    dom      = domaine.split(" ", 1)[-1] if " " in domaine else domaine  # sans l'emoji du domaine
    surtitre = f"{emoji} {dom} · Critique" if niveau >= 3 else f"{emoji} {dom}"
    corps    = f"{surtitre}\n{(accroche or titre)[:140]}"
    envoyer_push(titre=titre[:120], body=corps, url=notif_url,
                 niveau=niveau, tag=f"alerte-{alerte['id']}")
    return alerte["id"]


def ajouter_source_alerte(alerte_id, source_dict):
    """Ajoute une source supplémentaire à une alerte existante (clustering multi-sources)."""
    try:
        r = http.get(sb("alertes"), headers=SB_SERVICE,
                     params={"id": f"eq.{alerte_id}", "select": "sources"}, timeout=5)
        if not r.ok or not r.json():
            return
        sources = r.json()[0].get("sources") or []
        if any(s.get("url") == source_dict.get("url") for s in sources):
            return
        sources.append(source_dict)
        http.patch(sb("alertes"), headers=SB_SERVICE,
                   params={"id": f"eq.{alerte_id}"},
                   json={"sources": sources}, timeout=5)
        for a in alertes:
            if a["id"] == alerte_id:
                a["sources"] = sources
                break
    except Exception as e:
        print(f"[Source] Erreur ajout source : {e}")


bot.on_alerte  = ajouter_alerte
bot.on_doublon = ajouter_source_alerte


# ── API alertes ───────────────────────────────────────────────────────────────
@app.route("/api/alertes")
def api_alertes():
    domaine = request.args.get("domaine")
    liste = alertes if not domaine or domaine == "Tout" else [
        a for a in alertes if domaine in a["domaine"]
    ]
    return jsonify(liste)

@app.route("/api/stats")
def api_stats():
    aujourd_hui = datetime.now().date().isoformat()
    return jsonify({
        "total":       len(alertes),
        "aujourd_hui": sum(1 for a in alertes if a["date"].startswith(aujourd_hui)),
    })

@app.route("/api/admin/stats")
def api_admin_stats():
    """Statistiques de pilotage, réservées à l'admin (voir ADMIN_EMAIL)."""
    if not _est_admin():
        return jsonify({"erreur": "accès refusé"}), 403

    s   = bot.STATS
    now = time.time()

    # ── Volume : répartition par domaine + fenêtres 24h / 48h ──
    par_domaine = {}
    for a in alertes:
        dom = a.get("domaine", "?")
        par_domaine[dom] = par_domaine.get(dom, 0) + 1

    def _recent(a, heures):
        try:
            d = datetime.fromisoformat(a["date"])
            if d.tzinfo is None:
                d = d.replace(tzinfo=PARIS)
            return (datetime.now(PARIS) - d).total_seconds() <= heures * 3600
        except Exception:
            return False
    h24 = sum(1 for a in alertes if _recent(a, 24))
    h48 = sum(1 for a in alertes if _recent(a, 48))

    # ── Corrélations : compte dédupliqué + date de la dernière génération ──
    corr_actives, corr_derniere = 0, None
    try:
        r = http.get(sb("correlations"), headers=SB_SERVICE,
                     params={"order": "date.desc", "limit": "30"}, timeout=8)
        if r.ok and r.json():
            brut = r.json()
            corr_actives = len(_dedup_correlations(
                [c for c in brut if len(set(c.get("alertes_ids") or [])) >= 2]))
            corr_derniere = brut[0].get("date")
    except Exception:
        pass

    # ── Abonnements push actifs ──
    push_n = 0
    try:
        r = http.get(sb("user_subscriptions"), headers=SB_SERVICE,
                     params={"select": "endpoint"}, timeout=8)
        if r.ok:
            push_n = len(r.json())
    except Exception:
        pass

    # ── Filtre 8b : taux de rejet + estimation grossière de tokens ──
    cand = s.get("candidats", 0)
    surv = s.get("survivants", 0)
    rejet_pct  = round(100 * (1 - surv / cand)) if cand else 0
    tokens_est = cand * 250  # ~titre+résumé+prompt par article, pour situer vs le plafond TPM

    return jsonify({
        "bot": {
            "dernier_cycle":  s.get("dernier_cycle"),
            "prochain_cycle": (s["dernier_cycle"] + bot.INTERVALLE) if s.get("dernier_cycle") else None,
            "uptime_s":       int(now - s.get("demarrage", now)),
            "cycles_total":   s.get("cycles_total", 0),
            "flux_total":     s.get("flux_total", 0),
            "flux_echecs":    s.get("flux_echecs", []),
        },
        "volume": {
            "total":         len(alertes),
            "h24":           h24,
            "h48":           h48,
            "par_domaine":   par_domaine,
            "corr_actives":  corr_actives,
            "corr_derniere": corr_derniere,
        },
        "filtre": {
            "candidats":   cand,
            "survivants":  surv,
            "alertes":     s.get("alertes", 0),
            "rejet_pct":   rejet_pct,
            "tokens_est":  tokens_est,
            "tpm_plafond": 12000,
        },
        "push": {"abonnements": push_n},
        "now":  now,
    })


# ── API synthèse ──────────────────────────────────────────────────────────────
@app.route("/api/synthese/<alerte_id>")
def api_synthese(alerte_id):
    try:
        alerte_id = int(alerte_id)
        alerte = next((a for a in alertes if a["id"] == alerte_id), None)
        if not alerte:
            return jsonify({"erreur": "introuvable"}), 404
        accroche = alerte.get("accroche") or alerte.get("resume", "")
        contexte_txt = "\n".join(filter(None, [
            f"Titre : {alerte.get('titre', '')}",
            f"Info : {accroche}",
            f"Contexte : {alerte.get('contexte', '')}",
            f"Suite : {alerte.get('suite', '')}",
            f"Description : {alerte.get('description', '')[:800]}",
        ]))
        rep = bot.client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": (
                "Tu es journaliste. Explique cet événement en français en 3 à 5 phrases "
                "claires, objectives et accessibles. Ne commence pas par 'Voici' ou 'Cet article'.\n\n"
                + contexte_txt
            )}],
            max_tokens=400, temperature=0.3,
        )
        return jsonify({"synthese": rep.choices[0].message.content.strip()})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"erreur": str(e)}), 500


# ── API sauvegardes (par utilisateur) ─────────────────────────────────────────
@app.route("/api/sauvegardes")
def api_sauvegardes_get():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify([])
    try:
        r = http.get(sb("user_sauvegardes"), headers=SB_SERVICE,
                     params={"user_id": f"eq.{user_id}", "select": "alerte_id"}, timeout=10)
        ids = [row["alerte_id"] for row in (r.json() if r.ok else [])]
        if not ids:
            return jsonify([])
        mem_map = {a["id"]: a for a in alertes if a["id"] in ids}
        manquants = [i for i in ids if i not in mem_map]
        if manquants:
            ids_str = ",".join(str(i) for i in manquants)
            r2 = http.get(sb("alertes"), headers=SB_SERVICE,
                          params={"id": f"in.({ids_str})"}, timeout=10)
            if r2.ok:
                for a in r2.json():
                    mem_map[a["id"]] = a
        return jsonify([mem_map[i] for i in ids if i in mem_map])
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500

@app.route("/api/sauvegardes/ids")
def api_sauvegardes_ids():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify([])
    try:
        r = http.get(sb("user_sauvegardes"), headers=SB_SERVICE,
                     params={"user_id": f"eq.{user_id}", "select": "alerte_id"}, timeout=10)
        return jsonify([row["alerte_id"] for row in (r.json() if r.ok else [])])
    except:
        return jsonify([])

@app.route("/api/sauvegardes/<alerte_id>", methods=["POST", "DELETE"])
def api_sauvegardes_toggle(alerte_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"erreur": "non authentifié"}), 401
    try:
        aid = int(alerte_id)
    except ValueError:
        return jsonify({"erreur": "id invalide"}), 400
    try:
        if request.method == "POST":
            r = http.post(sb("user_sauvegardes"),
                          headers={**SB_SERVICE, "Prefer": "resolution=merge-duplicates,return=minimal"},
                          json={"user_id": user_id, "alerte_id": aid}, timeout=10)
        else:
            r = http.delete(sb("user_sauvegardes"), headers=SB_SERVICE,
                            params={"user_id": f"eq.{user_id}", "alerte_id": f"eq.{aid}"},
                            timeout=10)
        if not r.ok:
            return jsonify({"erreur": f"Supabase {r.status_code}: {r.text[:200]}"}), 500
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


# ── API push subscriptions (par utilisateur) ──────────────────────────────────
@app.route("/api/subscribe", methods=["POST"])
def api_subscribe():
    hdrs = user_headers()
    if not hdrs:
        return jsonify({"erreur": "non authentifié"}), 401
    data    = request.get_json()
    sub     = data.get("subscription") or data
    niveau_min = int(data.get("niveau_min", 3))
    endpoint   = sub.get("endpoint", "")
    user_id    = session.get("user_id")
    try:
        http.post(sb("user_subscriptions"),
                  headers={**SB_SERVICE, "Prefer": "resolution=merge-duplicates,return=minimal"},
                  json={"user_id": user_id, "endpoint": endpoint,
                        "subscription": sub, "niveau_min": niveau_min},
                  timeout=10)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500

@app.route("/api/unsubscribe", methods=["POST"])
def api_unsubscribe():
    data = request.get_json()
    endpoint = (data.get("subscription") or data).get("endpoint", "")
    if endpoint:
        http.delete(sb("user_subscriptions"), headers=SB_SERVICE,
                    params={"endpoint": f"eq.{endpoint}"}, timeout=10)
    return jsonify({"ok": True})

@app.route("/api/vapid-public")
def api_vapid_public():
    return jsonify({"key": VAPID_PUBLIC})


# ── API notif manuelle ────────────────────────────────────────────────────────
# Mots vides ignorés pour comparer les sujets de deux corrélations.
_STOP_CORR = {
    "dans", "pour", "avec", "plus", "cette", "leur", "leurs", "entre", "contre",
    "selon", "vers", "sans", "sous", "apres", "avant", "face", "alors", "mais",
    "donc", "elle", "cela", "quoi", "tout", "tous", "etre", "sont", "aussi",
    "deux", "trois", "fait", "fois", "pres", "grand", "grande", "nouvelle",
    "nouveau", "depuis", "encore", "vont", "vers", "que", "qui", "des", "les",
    "une", "aux", "sur", "par", "son", "ses", "ont", "est",
}

def _corr_tokens(c):
    """Mots significatifs (≥4 lettres, sans accents) du titre + contexte d'une corrélation."""
    txt = f"{c.get('titre', '')} {c.get('contexte', '')}".lower()
    txt = unicodedata.normalize("NFD", txt)
    txt = "".join(ch for ch in txt if unicodedata.category(ch) != "Mn")
    return {m for m in re.findall(r"[a-z]{4,}", txt) if m not in _STOP_CORR}

def _dedup_correlations(corrs):
    """Regroupe les corrélations d'un même sujet et ne garde que la plus récente.
    `corrs` doit être trié par date décroissante. Deux corrélations sont jugées
    du même sujet si elles partagent des alertes_ids, ou ≥3 mots-clés significatifs."""
    gardees, signatures = [], []
    for c in corrs:
        toks = _corr_tokens(c)
        ids  = set(c.get("alertes_ids") or [])
        doublon = any(
            (ids and gids and (ids & gids)) or len(toks & gtoks) >= 3
            for gtoks, gids in signatures
        )
        if not doublon:
            gardees.append(c)
            signatures.append((toks, ids))
    return gardees

@app.route("/api/correlations")
def api_correlations():
    hdrs    = user_headers()
    user_id = session.get("user_id")

    # domaines préférés de l'utilisateur
    domaines_user = list(bot.FLUX.keys())
    if hdrs and user_id:
        try:
            r = http.get(sb("user_preferences"), headers=hdrs,
                         params={"user_id": f"eq.{user_id}", "select": "domaines"}, timeout=5)
            if r.ok and r.json():
                domaines_user = r.json()[0].get("domaines") or domaines_user
        except:
            pass

    # récupérer les 30 dernières corrélations
    try:
        r = http.get(sb("correlations"), headers=SB_SERVICE,
                     params={"order": "date.desc", "limit": "30"}, timeout=10)
        all_corr = r.json() if r.ok else []
    except:
        return jsonify([])

    # filtrer par domaines préférés
    mots_cles = [d.split(" ", 1)[-1] for d in domaines_user]  # sans l'emoji
    def match(c):
        c_dom = c.get("domaines") or []
        if not c_dom:
            return True
        return any(any(mk in cd for mk in mots_cles) for cd in c_dom)

    # all_corr est déjà trié par date décroissante → on garde la version la plus
    # récente de chaque sujet, on écarte les doublons accumulés sur 3 jours et les
    # corrélations à une seule alerte (anciennes lignes qui n'ont pas de sens).
    return jsonify(_dedup_correlations([
        c for c in all_corr
        if match(c) and len(set(c.get("alertes_ids") or [])) >= 2
    ]))


@app.route("/api/notifier/<alerte_id>", methods=["POST"])
def api_notifier(alerte_id):
    try:
        alerte = next((a for a in alertes if a["id"] == int(alerte_id)), None)
        if not alerte:
            return jsonify({"erreur": "introuvable"}), 404
        notif_url = f"{APP_URL}/#synthese/{alerte['id']}" if APP_URL else alerte["lien"]
        body = alerte.get("accroche") or alerte.get("resume", alerte["titre"])
        envoyer_push(titre=f"🟡 {alerte['domaine'][:25]} — {alerte['titre'][:40]}",
                     body=body[:120], url=notif_url, niveau=2)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


# ── API préférences utilisateur ───────────────────────────────────────────────
@app.route("/api/domaines", methods=["GET", "POST"])
def api_domaines():
    hdrs = user_headers()
    user_id = session.get("user_id")
    if request.method == "POST":
        data = request.get_json()
        domaines = data.get("domaines", [])
        if hdrs and user_id:
            http.post(sb("user_preferences"),
                      headers={**hdrs, "Prefer": "resolution=merge-duplicates,return=minimal"},
                      json={"user_id": user_id, "domaines": domaines}, timeout=10)
        return jsonify({"ok": True})
    if hdrs and user_id:
        r = http.get(sb("user_preferences"), headers=hdrs,
                     params={"user_id": f"eq.{user_id}", "select": "domaines"}, timeout=10)
        if r.ok and r.json():
            return jsonify(r.json()[0].get("domaines") or list(bot.FLUX.keys()))
    return jsonify(list(bot.FLUX.keys()))


# ── API init (1 seul appel au démarrage) ─────────────────────────────────────
@app.route("/api/init")
def api_init():
    hdrs    = user_headers()
    user_id = session.get("user_id")

    saved_ids = []
    prefs_row = None

    if user_id:
        def _get_saved():
            r = http.get(sb("user_sauvegardes"), headers=SB_SERVICE,
                         params={"user_id": f"eq.{user_id}", "select": "alerte_id"}, timeout=8)
            return [row["alerte_id"] for row in r.json()] if r.ok else []

        def _get_prefs():
            r = http.get(sb("user_preferences"), headers=SB_SERVICE,
                         params={"user_id": f"eq.{user_id}"}, timeout=8)
            return r.json()[0] if (r.ok and r.json()) else None

        with ThreadPoolExecutor(max_workers=2) as ex:
            f_ids   = ex.submit(_get_saved)
            f_prefs = ex.submit(_get_prefs)
            saved_ids = f_ids.result()
            prefs_row = f_prefs.result()

    prefs = {
        "display_name":  prefs_row.get("display_name") or ""                  if prefs_row else "",
        "theme":         prefs_row.get("theme")        or "dark"               if prefs_row else "dark",
        "domaines":      prefs_row.get("domaines")     or list(bot.FLUX.keys()) if prefs_row else list(bot.FLUX.keys()),
        "niveau_notif":  prefs_row.get("niveau_notif") or 3                   if prefs_row else 3,
        "heure_recap":   8,
        "langue":        prefs_row.get("langue") or "multi"                   if prefs_row else "multi",
        "portees":       prefs_row.get("portees") or {}                        if prefs_row else {},
        "pays":          prefs_row.get("pays") or "France"                     if prefs_row else "France",
        # activé par défaut ; ne devient False que si l'utilisateur a explicitement coupé
        "notif_correlations": (prefs_row.get("notif_correlations") if prefs_row else None) is not False,
        "avatar":        (prefs_row.get("avatar") or "") if prefs_row else "",
        "genre":         (prefs_row.get("genre") or "") if prefs_row else "",
    }

    # filtrer par domaines préférés
    domaines_actifs = prefs["domaines"]
    alertes_filtrees = [
        a for a in alertes
        if any(d in a.get("domaine", "") for d in domaines_actifs)
    ] if domaines_actifs else alertes

    # filtrer par portée (par domaine) et par pays (national/local de l'user uniquement)
    portees   = prefs["portees"]
    pays_user = (prefs["pays"] or "").strip().lower()
    def _garder(a):
        portee_art = a.get("portee", "")
        if not portee_art:
            return True  # article ancien sans tag → toujours visible
        # 1) préférence mondial vs national par domaine
        if portees:
            portee_pref = portees.get(a.get("domaine", ""), "tout")
            if portee_pref == "mondiale" and portee_art not in ("mondiale", "regionale"):
                return False
        # 2) une news nationale/locale n'est gardée que si elle concerne le pays de l'user
        if portee_art in ("nationale", "locale") and pays_user and pays_user != "tous":
            pays_art = (a.get("pays") or "").strip().lower()
            if pays_art and pays_art != pays_user:
                return False
        return True
    alertes_filtrees = [a for a in alertes_filtrees if _garder(a)]

    return jsonify({
        "alertes":      alertes_filtrees,
        "saved_ids":    saved_ids,
        "preferences":  prefs,
        "email":        session.get("user_email", ""),
        "is_new_user":  prefs_row is None,
        "is_admin":     _est_admin(),
    })


# ── API préférences unifiées ──────────────────────────────────────────────────
@app.route("/api/preferences", methods=["POST"])
def api_preferences():
    hdrs    = user_headers()
    user_id = session.get("user_id")
    if not hdrs or not user_id:
        return jsonify({"erreur": "non authentifié"}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"erreur": "JSON invalide"}), 400

    THEMES  = {"dark", "dim", "slate", "light"}
    LANGUES = {"multi", "fr", "en", "es", "de"}
    GENRES  = {"homme", "femme", "autre", "non_precise"}
    MAX_AVATAR = 400_000  # ~300 Ko de base64 (une photo 256px est très en dessous)

    # Validation par champ : on ignore une valeur invalide plutôt que de tout rejeter.
    prefs = {"user_id": user_id}
    for key in ("display_name", "theme", "domaines", "niveau_notif",
                "langue", "portees", "pays", "notif_correlations", "avatar"):
        if key not in data:
            continue
        v = data[key]
        if   key == "display_name" and isinstance(v, str):       prefs[key] = v.strip()[:40]
        elif key == "theme"        and v in THEMES:              prefs[key] = v
        elif key == "langue"       and v in LANGUES:             prefs[key] = v
        elif key == "niveau_notif" and v in (2, 3):             prefs[key] = v
        elif key == "pays"         and isinstance(v, str):       prefs[key] = v[:40]
        elif key == "domaines"     and isinstance(v, list):      prefs[key] = [str(d)[:40] for d in v][:20]
        elif key == "portees"      and isinstance(v, dict):      prefs[key] = v
        elif key == "notif_correlations" and isinstance(v, bool): prefs[key] = v
        elif key == "avatar"       and isinstance(v, str) and len(v) <= MAX_AVATAR:
            prefs[key] = v
    http.post(sb("user_preferences"),
              headers={**hdrs, "Prefer": "resolution=merge-duplicates,return=minimal"},
              json=prefs, timeout=10)
    # genre : colonne optionnelle. Enregistrée séparément pour qu'une colonne
    # absente ne fasse pas échouer l'enregistrement des autres préférences.
    genre = data.get("genre")
    if genre in GENRES:
        try:
            http.post(sb("user_preferences"),
                      headers={**hdrs, "Prefer": "resolution=merge-duplicates,return=minimal"},
                      json={"user_id": user_id, "genre": genre}, timeout=10)
        except Exception as e:
            print(f"Supabase genre (colonne absente ?) : {e}")
    return jsonify({"ok": True})


# ── API info utilisateur ──────────────────────────────────────────────────────
@app.route("/api/reset-my-password", methods=["POST"])
def api_reset_my_password():
    email = session.get("user_email", "")
    if not email:
        return jsonify({"erreur": "non authentifié"}), 401
    try:
        http.post(sb_auth("/recover"),
                  headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
                  json={"email": email}, timeout=10)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


@app.route("/api/me")
def api_me():
    return jsonify({
        "email":   session.get("user_email", ""),
        "user_id": session.get("user_id", ""),
    })


# ── Fichiers statiques ────────────────────────────────────────────────────────
@app.route("/sw.js")
def service_worker():
    resp = send_from_directory("static", "sw.js", mimetype="application/javascript")
    # Jamais de cache HTTP sur le service worker → une nouvelle version est prise
    # en compte au lancement suivant (sinon un SW bugué pourrait rester des heures).
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp

@app.route("/health")
def health():
    return "OK", 200


@app.route("/api/cron/recap")
def api_cron_recap():
    """Déclencheur externe des corrélations (réveille l'instance Render endormie).
    À appeler par un cron gratuit (ex: cron-job.org) avec ?token=CRON_SECRET :
    un cron à 8h (push matinal) et un à 18h avec &heure=18 (refresh silencieux).
    Le verrou _resumes_envoyes empêche tout double passage sur le même créneau."""
    secret = os.getenv("CRON_SECRET", "")
    if not secret or request.args.get("token") != secret:
        return "forbidden", 403
    # ?force=1 : relancer même si déjà fait aujourd'hui (utile pour tester)
    if request.args.get("force"):
        _resumes_envoyes.clear()
    # ?heure=8 (défaut) ou 18 : indépendant du fuseau horaire du serveur
    try:
        heure = int(request.args.get("heure", 8))
    except ValueError:
        heure = 8
    check_resumes_matinaux(heure if heure in (8, 18) else 8)
    return "ok", 200


@app.route("/a/<int:alerte_id>")
def partager_alerte(alerte_id):
    """Page publique de partage d'une alerte (sans login)."""
    alerte = next((a for a in alertes if a["id"] == alerte_id), None)
    if not alerte:
        # Chercher dans Supabase
        try:
            r = http.get(sb("alertes"), headers=SB_SERVICE,
                         params={"id": f"eq.{alerte_id}"}, timeout=8)
            data = r.json()
            alerte = data[0] if r.ok and data else None
        except Exception:
            alerte = None
    if not alerte:
        return "Alerte introuvable.", 404

    from markupsafe import escape
    titre   = escape(alerte.get("titre", ""))
    domaine = escape(alerte.get("domaine", ""))
    accroche = escape(alerte.get("accroche", ""))
    contexte = escape(alerte.get("contexte", ""))
    suite    = escape(alerte.get("suite", ""))
    lien     = escape(alerte.get("lien", ""))
    niveau   = alerte.get("niveau", 2)
    dot      = "🔴" if niveau >= 3 else "🟡"
    app_url  = APP_URL or request.host_url.rstrip("/")

    return f"""<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta property="og:title" content="{titre}"/>
<meta property="og:description" content="{accroche}"/>
<title>{titre}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0a0a;color:#f0f0f0;font-family:system-ui,sans-serif;min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:24px 20px 48px}}
.card{{max-width:560px;width:100%}}
.badge{{font-size:11px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:.06em;margin-bottom:16px}}
h1{{font-size:22px;font-weight:700;line-height:1.35;margin-bottom:20px}}
.section{{margin-top:18px;padding-top:18px;border-top:1px solid #1a1a1a}}
.label{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#444;margin-bottom:6px}}
.text{{font-size:15px;color:#ccc;line-height:1.65}}
.cta{{margin-top:36px;text-align:center}}
.cta a{{display:inline-block;padding:12px 24px;background:#f0f0f0;color:#000;border-radius:12px;font-size:15px;font-weight:600;text-decoration:none}}
.footer{{margin-top:20px;font-size:12px;color:#333;text-align:center}}
</style></head>
<body>
<div class="card">
  <div class="badge">{dot} {domaine}</div>
  <h1>{titre}</h1>
  {"<div class='section'><div class='label'>En bref</div><div class='text'>" + accroche + "</div></div>" if accroche else ""}
  {"<div class='section'><div class='label'>Contexte</div><div class='text'>" + contexte + "</div></div>" if contexte else ""}
  {"<div class='section'><div class='label'>À suivre</div><div class='text'>" + suite + "</div></div>" if suite else ""}
</div>
<div class="cta"><a href="{app_url}">Ouvrir Korrel →</a></div>
<div class="footer">Partagé via Korrel</div>
</body></html>"""

@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


# ── Bot en arrière-plan ───────────────────────────────────────────────────────
_resumes_envoyes = {}  # {user_id: date_string}

def envoyer_push_user(user_id, titre, body, url, tag=None):
    """Envoie une push notification à un utilisateur spécifique."""
    if not VAPID_PRIVATE_FILE:
        return
    payload = {"title": titre, "body": body, "url": url}
    if tag:
        payload["tag"] = tag
    try:
        from pywebpush import webpush
        r = http.get(sb("user_subscriptions"), headers=SB_SERVICE,
                     params={"user_id": f"eq.{user_id}"}, timeout=5)
        for item in (r.json() if r.ok else []):
            sub = item.get("subscription")
            if not sub:
                continue
            try:
                webpush(subscription_info=sub,
                        data=json.dumps(payload),
                        vapid_private_key=VAPID_PRIVATE_FILE,
                        vapid_claims={"sub": "mailto:korrel.news@gmail.com"})
            except Exception:
                pass
    except Exception as e:
        print(f"[Push user] {e}")

def generer_correlations():
    """Analyse TOUTES les alertes des dernières 48h et génère les corrélations (passe globale unique).

    Un seul appel Groq 70b par matin pour l'ensemble des utilisateurs : les corrélations
    sont stockées globalement puis filtrées par domaine au moment du push (cf. check_resumes_matinaux).
    Retourne la liste des corrélations générées (vide si rien)."""

    depuis = (datetime.now() - timedelta(hours=48)).isoformat()
    # Lire depuis Supabase pour ne pas dépendre de la mémoire (réinitialisée au redémarrage)
    try:
        r = http.get(sb("alertes"), headers=SB_SERVICE,
                     params={"date": f"gte.{depuis}", "order": "date.desc", "limit": "200"}, timeout=10)
        alertes_24h = r.json() if r.ok and isinstance(r.json(), list) else []
    except Exception as e:
        print(f"[Corrélation] Erreur lecture Supabase : {e}")
        alertes_24h = [a for a in alertes if a.get("date", "") >= depuis]

    print(f"[Corrélation] {len(alertes_24h)} alertes des 48h (global)")
    if len(alertes_24h) < 2:
        print(f"[Corrélation] Pas assez d'alertes, abandon.")
        return []

    # Plafond pour rester sous la limite Groq (12k TPM en free tier sur le 70b).
    # On garde les plus récentes et on allège chaque entrée (pas de contexte, accroche tronquée).
    MAX_ALERTES = 60
    alertes_24h = alertes_24h[:MAX_ALERTES]
    alertes_compact = [
        {
            "id":      a["id"],
            "titre":   a["titre"][:140],
            "domaine": a["domaine"],
            "accroche": (a.get("accroche") or "")[:160],
        }
        for a in alertes_24h
    ]

    # Corrélations publiées ces 3 derniers jours → permet de détecter les SUITES
    # (un sujet déjà couvert qui évolue) et de réécrire une version à jour plutôt
    # que d'empiler une nouvelle carte sur le même fil.
    depuis_corr = (datetime.now() - timedelta(days=3)).isoformat()
    try:
        rc = http.get(sb("correlations"), headers=SB_SERVICE,
                      params={"date": f"gte.{depuis_corr}", "order": "date.desc",
                              "limit": "15", "select": "id,titre,synthese"}, timeout=10)
        corr_passees = rc.json() if rc.ok and isinstance(rc.json(), list) else []
    except Exception:
        corr_passees = []
    ids_passees = {c["id"] for c in corr_passees}
    corr_passees_compact = [
        {"id": c["id"], "titre": c.get("titre", ""), "resume": (c.get("synthese") or "")[:130]}
        for c in corr_passees
    ]
    if corr_passees_compact:
        bloc_suites = (
            "\n\nCORRÉLATIONS DÉJÀ PUBLIÉES CES 3 DERNIERS JOURS (sujets en cours) :\n"
            + json.dumps(corr_passees_compact, ensure_ascii=False)
            + "\nSi un nouveau groupe PROLONGE l'un de ces sujets (le même fil qui évolue, "
              "une nouvelle avancée — PAS juste le même thème), écris une version ACTUALISÉE "
              "qui intègre la nouveauté, et ajoute le champ \"remplace\": <id de la corrélation prolongée>. "
              "Ne remplace QUE si c'est vraiment la suite du même événement ; sinon n'inclus pas \"remplace\"."
        )
    else:
        bloc_suites = ""

    prompt = (
        "Tu es un analyste géopolitique, scientifique et économique senior. "
        "Voici les alertes d'actualité des dernières 48h :\n"
        + json.dumps(alertes_compact, ensure_ascii=False)
        + bloc_suites
        + """

Identifie les groupes d'alertes RÉELLEMENT liées par un mécanisme concret
(cause→effet, même acteur, ressource ou marché commun, dynamique d'ensemble).
N'invente pas de lien thématique vague.

COMBINE EN PROFONDEUR : quand plusieurs alertes éclairent une MÊME situation
d'ensemble, fusionne-les dans UNE SEULE corrélation riche qui les tisse, au lieu
d'émettre une carte par alerte. Ex: « inflation au Japon » + « la BoJ relève ses
taux » + « le yen se renforce » → UNE corrélation « Bascule monétaire du Japon »
qui explique l'enchaînement (l'inflation force la banque centrale à durcir, ce
qui soutient le yen mais alourdit le coût de la dette publique). Vise 3-4 faits
par corrélation quand c'est possible : une analyse dense qui relie plusieurs
alertes vaut bien mieux que plusieurs cartes partielles sur le même sujet.

NE FAIS PAS de cartes séparées pour deux alertes qui décrivent le MÊME événement
sous deux angles (c'est un doublon, pas une corrélation) : fusionne-les, ou ignore
la redondante. Ex: « l'Ukraine développe un missile » + « l'Ukraine présente une
alternative au Patriot » = même fait → une seule mention.

Pour chaque groupe (2 alertes ou +), rédige en français, FACTUEL et SPÉCIFIQUE.

RÈGLES ABSOLUES :
- Nomme les acteurs précis : pays, entreprises, dirigeants, institutions.
- Cite les chiffres/dates des alertes (montants, %, échéances) quand ils existent.
- Les 3 champs doivent dire des choses DIFFÉRENTES. Ne répète JAMAIS dans
  « analyse » ou « implication » un fait déjà écrit dans « contexte ». Si tu te
  répètes, c'est que le groupe est trop faible : supprime-le.
- INTERDIT (recommence si tu l'écris) : « sera surveillé(e) de près », « il faudra
  surveiller l'évolution », « la prochaine étape sera surveillée », « cela pourrait
  avoir des implications », « les enjeux sont importants », « stabilité régionale »,
  « impact significatif », et toute phrase vraie pour n'importe quelle actu.
- Pas de méta-langage (« cet événement », « cette corrélation »).

Rôle PRÉCIS et NON REDONDANT de chaque champ :
- titre : le lien en max 8 mots, concret (ex: « Frappes mer Noire → pétrole +12% »).
- contexte : les FAITS. Plante le décor (la crise de fond, ex: « Dans le contexte du
  conflit Iran-Israël… ») puis les acteurs et ce qui s'est passé. Si la corrélation
  combine plusieurs alertes, réunis-en les faits clés ici. 2-4 phrases.
- analyse : le POURQUOI ça compte, SANS redire les faits. Ce qui est en jeu —
  ressources, argent, territoire, pouvoir : qui gagne, qui perd, par quel mécanisme.
- implication : une PRÉDICTION concrète. Nomme l'événement futur précis possible
  (« si X, alors Y »), une échéance, une décision attendue, un seuil chiffré.
  PAS « on surveillera » : dis CE QUI peut concrètement arriver.
- alertes_ids : liste des ids concernés.
- domaines : liste des domaines impliqués.

Exemple du niveau attendu (3 champs DISTINCTS) :
{"titre":"Sécheresse Panama → fret maritime +30%",
 "contexte":"Le canal de Panama limite les passages à 24/jour depuis octobre faute d'eau. Maersk et MSC reroutent via le cap Horn, +12 jours de trajet.",
 "analyse":"Le surcoût se répercute sur les prix des biens importés en Europe pour Noël ; les armateurs captent une marge record pendant que les exportateurs sud-américains perdent l'accès rapide à l'Asie.",
 "implication":"Si les pluies ne reviennent pas avant janvier, l'autorité du canal a prévenu qu'elle descendrait à 18 passages/jour — nouvelle hausse du fret à anticiper."}

Réponds uniquement avec ce JSON, sans texte autour ("remplace" est optionnel, cf. ci-dessus) :
[{"titre":"...","contexte":"...","analyse":"...","implication":"...","alertes_ids":[id1,id2],"domaines":["🌍 Géopolitique"]}]

Si aucun groupe n'a de lien mécanique solide entre événements distincts, réponds [].
Sois exigeant : 2 corrélations denses valent mieux que 5 creuses."""
    )

    try:
        rep = bot.client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2800, temperature=0.2,
        )
        contenu = rep.choices[0].message.content.strip()
        debut = contenu.find("[")
        fin   = contenu.rfind("]") + 1
        correlations = json.loads(contenu[debut:fin])
    except Exception as e:
        print(f"[Corrélation] Erreur Groq : {e}")
        return []

    if not correlations:
        print(f"[Corrélation] Aucune corrélation (global)")
        return []

    # Une corrélation relie AU MOINS 2 alertes distinctes : on écarte les groupes
    # à une seule source (ou zéro), qui n'ont aucun sens en tant que corrélation.
    correlations = [c for c in correlations
                    if len(set(c.get("alertes_ids") or [])) >= 2]
    if not correlations:
        print(f"[Corrélation] Aucune corrélation à ≥2 alertes (global)")
        return []

    # Sauvegarder dans Supabase. Colonnes : id, titre, synthese, alertes_ids, date, domaines,
    # contexte, analyse, implication. synthese garde la version fusionnée pour fallback d'affichage.
    for i, c in enumerate(correlations):
        c["id"]       = int(datetime.now().timestamp() * 1000) + i
        c["date"]     = datetime.now().isoformat()
        c["synthese"] = f"{c.get('contexte', '')} {c.get('analyse', '')} {c.get('implication', '')}".strip()

        # Suite d'un sujet déjà publié ? On ne valide que si l'id pointe vraiment
        # vers une corrélation récente (évite une suppression sur un id halluciné).
        try:
            remplace_id = int(c.get("remplace"))
        except (TypeError, ValueError):
            remplace_id = None
        if remplace_id not in ids_passees:
            remplace_id = None

        row = {
            "id":          c["id"],
            "date":        c["date"],
            "titre":       c.get("titre", ""),
            "synthese":    c["synthese"],
            "alertes_ids": c.get("alertes_ids", []),
            "domaines":    c.get("domaines", []),
            "contexte":    c.get("contexte", ""),
            "analyse":     c.get("analyse", ""),
            "implication": c.get("implication", ""),
            "maj":         bool(remplace_id),
        }
        hdr = {**SB_SERVICE, "Prefer": "resolution=merge-duplicates,return=minimal"}
        try:
            r = http.post(sb("correlations"), headers=hdr, json=row, timeout=10)
            # Colonne "maj" pas encore créée côté Supabase → réessaie sans, pour ne rien perdre.
            if not r.ok and "maj" in row:
                r = http.post(sb("correlations"), headers=hdr,
                              json={k: v for k, v in row.items() if k != "maj"}, timeout=10)
                c["maj"] = bool(remplace_id)  # garde le flag pour le push, même sans colonne
            # L'ancienne carte est remplacée : on la supprime après une insertion réussie.
            if remplace_id and r.ok:
                http.delete(sb("correlations"), headers=SB_SERVICE,
                            params={"id": f"eq.{remplace_id}"}, timeout=8)
        except Exception as e:
            print(f"[Corrélation] Erreur sauvegarde : {e}")

    print(f"[Corrélation] {len(correlations)} corrélation(s) générée(s) (global)")
    # Le prompt + la réponse 70b sont volumineux : on libère avant de rendre la main.
    del prompt, rep, contenu, alertes_compact, alertes_24h
    gc.collect()
    return correlations


def check_resumes_matinaux(heure):
    """Génère les corrélations 2×/jour (8h + 18h, heure de Paris).
    À 8h : génération + push matinal filtré par domaine. À 18h : régénération
    SILENCIEUSE (le contenu se rafraîchit avec l'actu du jour, sans notifier).
    Un verrou par créneau évite de tourner deux fois le même créneau."""
    if heure not in (8, 18) or not SUPABASE_URL:
        return
    today = datetime.now(PARIS).date().isoformat()
    cle = f"__global_{heure}__"
    # Vérifier qu'on n'a pas déjà tourné ce créneau aujourd'hui
    if _resumes_envoyes.get(cle) == today:
        return

    push_matinal = (heure == 8)
    label = "matinales (8h)" if push_matinal else "de l'après-midi (18h)"
    print(f"[Resume] Génération des corrélations {label} ({today})")
    correlations = generer_correlations()  # 1 seul appel Groq 70b pour tout le monde
    _resumes_envoyes[cle] = today
    # 18h : régénération silencieuse, on s'arrête avant le push.
    if not correlations or not push_matinal:
        return

    try:
        # select="*" : robuste si la colonne notif_correlations n'existe pas encore
        r = http.get(sb("user_preferences"), headers=SB_SERVICE,
                     params={"select": "*"}, timeout=5)
        users = r.json() if r.ok and isinstance(r.json(), list) else []
    except Exception as e:
        print(f"[Resume] Erreur lecture utilisateurs : {e}")
        return

    print(f"[Resume] {len(users)} utilisateur(s) — push filtré par domaine")
    url = f"{APP_URL}/#correlations" if APP_URL else "/"
    for u in users:
        uid = u.get("user_id")
        if not uid or _resumes_envoyes.get(uid) == today:
            continue
        _resumes_envoyes[uid] = today
        if u.get("notif_correlations") is False:   # l'utilisateur a coupé le récap
            continue

        # filtrer les corrélations globales par les domaines de l'utilisateur
        mots = [d.split(" ", 1)[-1] for d in (u.get("domaines") or [])]
        if mots:
            corr_user = [c for c in correlations
                         if not c.get("domaines")
                         or any(any(mk in cd for mk in mots) for cd in c["domaines"])]
        else:
            corr_user = correlations
        if not corr_user:
            continue

        # Digest : titre = nombre, corps = liste à puces des corrélations.
        n = len(corr_user)
        titre_notif = f"🔗 {n} corrélation{'s' if n > 1 else ''} du jour"
        puces = "\n".join(f"• {c['titre']}" for c in corr_user[:4])
        envoyer_push_user(uid, titre_notif, puces[:300], url, tag="corr-jour")


def nettoyer_vieilles_alertes():
    """Supprime les alertes non sauvegardées de plus de 3 jours, conserve les sauvegardées jusqu'à 30 jours."""
    if not SUPABASE_URL:
        return
    try:
        from datetime import timedelta
        # Récupérer tous les IDs sauvegardés par des utilisateurs
        r_saved = http.get(sb("user_sauvegardes"), headers=SB_SERVICE,
                           params={"select": "alerte_id"}, timeout=10)
        saved_ids = []
        if r_saved.ok:
            saved_ids = [str(row["alerte_id"]) for row in r_saved.json()]

        # Supprimer alertes non sauvegardées > 3 jours
        limite_non_sauvegardees = (datetime.now() - timedelta(days=3)).isoformat()
        params_non_sauvegardees = {"date": f"lt.{limite_non_sauvegardees}"}
        if saved_ids:
            params_non_sauvegardees["id"] = f"not.in.({','.join(saved_ids)})"
        r1 = http.delete(sb("alertes"), headers=SB_SERVICE,
                         params=params_non_sauvegardees, timeout=10)

        # Supprimer alertes sauvegardées > 6 mois
        limite_sauvegardees = (datetime.now() - timedelta(days=180)).isoformat()
        r2 = http.delete(sb("alertes"), headers=SB_SERVICE,
                         params={"date": f"lt.{limite_sauvegardees}"}, timeout=10)

        if r1.ok or r2.ok:
            print("Nettoyage Supabase : alertes non sauvegardées > 3j et sauvegardées > 6 mois supprimées")

        # Supprimer corrélations > 3 jours
        limite_corr = (datetime.now() - timedelta(days=3)).isoformat()
        r3 = http.delete(sb("correlations"), headers=SB_SERVICE,
                         params={"date": f"lt.{limite_corr}"}, timeout=10)
        if r3.ok:
            print("Nettoyage Supabase : corrélations > 3j supprimées")
    except Exception as e:
        print(f"Erreur nettoyage : {e}")

def _ram_mo():
    """RSS courant en Mo (Linux/Render). Renvoie None ailleurs (ex: Windows)."""
    try:
        with open("/proc/self/statm") as f:
            pages = int(f.read().split()[1])  # RSS en pages
        return pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except Exception:
        return None


def boucle():
    premiere_fois = not os.path.exists(bot.SEEN_FILE)
    bot.verifier(premiere_fois=premiere_fois)
    cycles = 0
    while True:
        time.sleep(bot.INTERVALLE)
        bot.verifier()
        cycles += 1

        ram = _ram_mo()
        if ram is not None:
            print(f"[RAM] {ram:.0f} Mo / 512")

        # résumé matinal : 8h heure de Paris (le serveur tourne en UTC)
        now = datetime.now(PARIS)
        if now.minute < 15:  # fenêtre de 15 min par heure
            check_resumes_matinaux(now.hour)

        # nettoyage Supabase une fois par jour
        if cycles % 96 == 0:
            nettoyer_vieilles_alertes()


if __name__ == "__main__":
    init_vapid()
    alertes.extend(charger_alertes())
    # RUN_BOT=0 sur l'instance web quand le bot tourne sur un worker dédié
    # (ex: worker.py sur un second PC) → évite deux bots en parallèle.
    if os.getenv("RUN_BOT", "1") != "0":
        t = threading.Thread(target=boucle, daemon=True)
        t.start()
    else:
        print("[BOT] désactivé sur cette instance (RUN_BOT=0) — worker externe")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
