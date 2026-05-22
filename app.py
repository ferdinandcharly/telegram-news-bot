import os
import time
import json
import base64
import threading
import tempfile
from datetime import datetime, timedelta, date as dt_date
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, jsonify, send_from_directory, request, session, redirect
import requests as http
import bot

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

# ── Mémoire ───────────────────────────────────────────────────────────────────
alertes   = []
ALERTES_FILE = "alertes.json"

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

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
.brand{font-size:9px;letter-spacing:3px;color:#333;text-transform:uppercase;font-weight:600;text-align:center;margin-bottom:48px}
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
<title>{titre} — News Alert</title>
<style>{_CSS_AUTH}</style></head>
<body>
<div tabindex="0" style="position:fixed;opacity:0;pointer-events:none;width:0;height:0"></div>
<div class="brand">News Alert</div>
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
<title>Connexion — News Alert</title>
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
                      headers={"apikey": SUPABASE_KEY, "Content-Type": "application/json"},
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
        return jsonify({"ok": True}) if r.ok else jsonify({"erreur": "échec"}), 400
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


@app.route("/privacy")
def privacy():
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Politique de confidentialité — News Alert</title>
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
<li><strong>Telegram</strong> — alertes critiques (optionnel, uniquement si activé)</li>
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
<li>Demander l'export ou la suppression de tes données à : <a href="mailto:{os.getenv('CONTACT_EMAIL','ferdinandcharly@gmail.com')}">{os.getenv('CONTACT_EMAIL','ferdinandcharly@gmail.com')}</a></li>
</ul>

<h2>6. Cookies et sessions</h2>
<p>Un cookie de session est utilisé uniquement pour maintenir ta connexion (durée 30 jours). Aucun cookie publicitaire ou de tracking.</p>

<h2>7. Contact</h2>
<p>Pour toute question : <a href="mailto:{os.getenv('CONTACT_EMAIL','ferdinandcharly@gmail.com')}">{os.getenv('CONTACT_EMAIL','ferdinandcharly@gmail.com')}</a></p>
</body></html>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        pwd   = request.form.get("password", "")
        try:
            r = http.post(sb_auth("/token?grant_type=password"),
                          headers={"apikey": SUPABASE_KEY, "Content-Type": "application/json"},
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
        'Déjà un compte ? <a href="/login">Se connecter</a><br><a href="/privacy" style="color:#333">Politique de confidentialité</a>'
    )

@app.route("/register", methods=["GET", "POST"])
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
                          headers={"apikey": SUPABASE_KEY, "Content-Type": "application/json"},
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
<html lang="fr"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>News Alert</title>
<style>
:root, [data-theme="dark"] { --bg:#000; --text:#f0f0f0; --sub:#555; --line:#1a1a1a; --surface:#0e0e0e; }
[data-theme="dim"]   { --bg:#161b22; --text:#e6edf3; --sub:#8b949e; --line:#30363d; --surface:#1c2128; }
[data-theme="light"] { --bg:#fff; --text:#111; --sub:#888; --line:#e8e8e8; --surface:#f5f5f5; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text);
       font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       min-height: 100vh; padding-bottom: 48px; }

.hero { padding: 56px 28px 36px; border-bottom: 1px solid var(--line); }
.hero-brand { font-size: 11px; font-weight: 600; letter-spacing: 0.08em;
              text-transform: uppercase; color: var(--sub); margin-bottom: 20px; }
.hero h1 { font-size: 26px; font-weight: 700; line-height: 1.25;
           letter-spacing: -0.4px; margin-bottom: 10px; }
.hero p { font-size: 14px; color: var(--sub); line-height: 1.65; }

.step { padding: 32px 28px; border-bottom: 1px solid var(--line); }
.step-num { font-size: 11px; font-weight: 600; letter-spacing: 0.06em;
            text-transform: uppercase; color: var(--sub); margin-bottom: 8px; }
.step-title { font-size: 16px; font-weight: 600; margin-bottom: 6px; }
.step-sub { font-size: 13px; color: var(--sub); margin-bottom: 20px; line-height: 1.5; }

input[type=text] { width: 100%; padding: 14px 16px; background: var(--surface);
                   border: 1px solid var(--line); border-radius: 10px; color: var(--text);
                   font-size: 15px; outline: none; transition: border-color 0.15s; }
input[type=text]:focus { border-color: var(--sub); }

.topics { display: flex; flex-wrap: wrap; gap: 8px; }
.topic { padding: 9px 16px; border-radius: 8px; border: 1px solid var(--line);
         background: none; color: var(--sub); font-size: 14px; font-weight: 500;
         cursor: pointer; transition: all 0.15s; user-select: none; }
.topic.on { border-color: var(--text); color: var(--text); background: var(--surface); }

.themes { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.theme-card { border: 1px solid var(--line); border-radius: 10px; padding: 16px 10px;
              cursor: pointer; text-align: center; transition: all 0.15s; background: none; }
.theme-card.on { border-color: var(--text); background: var(--surface); }
.theme-swatch { height: 36px; border-radius: 6px; margin-bottom: 10px; }
.sw-dark  { background: #000; border: 1px solid #222; }
.sw-dim   { background: #161b22; border: 1px solid #30363d; }
.sw-light { background: #fff; border: 1px solid #e8e8e8; }
.theme-name { font-size: 12px; font-weight: 500; color: var(--sub); }
.theme-card.on .theme-name { color: var(--text); }

.heure-row { display: flex; align-items: center; justify-content: space-between;
             padding: 14px 16px; background: var(--surface);
             border: 1px solid var(--line); border-radius: 10px; }
.heure-label { font-size: 14px; color: var(--text); }
select { padding: 6px 10px; background: var(--bg); border: 1px solid var(--line);
         border-radius: 8px; color: var(--text); font-size: 14px; outline: none; }

.cta { padding: 28px; }
.btn-go { width: 100%; padding: 15px; background: var(--text); color: var(--bg); border: none;
          border-radius: 10px; font-size: 15px; font-weight: 600; cursor: pointer;
          letter-spacing: -0.1px; transition: opacity 0.15s; }
.btn-go:disabled { opacity: 0.25; cursor: default; }
.cancel { display: block; text-align: center; margin-top: 18px;
          font-size: 13px; color: var(--sub); text-decoration: none; }

.notif-card { display: flex; align-items: center; justify-content: space-between;
              gap: 16px; padding: 16px; background: var(--surface);
              border: 1px solid var(--line); border-radius: 10px; }
.notif-titre { font-size: 14px; font-weight: 600; margin-bottom: 4px; }
.notif-desc { font-size: 12px; color: var(--sub); line-height: 1.5; }
.btn-notif-ob { flex-shrink: 0; padding: 9px 18px; background: var(--text); color: var(--bg);
                border: none; border-radius: 8px; font-size: 13px; font-weight: 600;
                cursor: pointer; transition: opacity 0.15s; white-space: nowrap; }
.btn-notif-ob:disabled { opacity: 0.35; cursor: default; }
</style></head>
<body>

<div class="hero">
  <div class="hero-brand">News Alert</div>
  <h1>Personnalise<br>ton fil d'actu</h1>
  <p>Choisis tes sujets, reçois uniquement<br>les événements qui comptent vraiment.</p>
</div>

<div class="step">
  <div class="step-num">Étape 1</div>
  <div class="step-title">Comment t'appeler ?</div>
  <div class="step-sub">Optionnel — utilisé dans tes notifications matinales</div>
  <input type="text" id="display-name" placeholder="Prénom ou pseudo" maxlength="30"/>
</div>

<div class="step">
  <div class="step-num">Étape 2</div>
  <div class="step-title">Quels sujets t'intéressent ?</div>
  <div class="step-sub">Choisis au moins un domaine</div>
  <div class="topics">
    <button type="button" class="topic" data-d="🌍 Géopolitique">🌍 Géopolitique</button>
    <button type="button" class="topic" data-d="🔬 Science">🔬 Science</button>
    <button type="button" class="topic" data-d="💻 Tech & IA">💻 Tech &amp; IA</button>
    <button type="button" class="topic" data-d="💰 Finance">💰 Finance</button>
    <button type="button" class="topic" data-d="🌱 Environnement">🌱 Environnement</button>
    <button type="button" class="topic" data-d="⚽ Sport">⚽ Sport</button>
  </div>
</div>

<div class="step">
  <div class="step-num">Étape 3</div>
  <div class="step-title">Ton thème</div>
  <div class="step-sub">Tu pourras le changer dans les paramètres</div>
  <div class="themes">
    <button type="button" class="theme-card on" data-t="dark">
      <div class="theme-swatch sw-dark"></div>
      <div class="theme-name">Dark</div>
    </button>
    <button type="button" class="theme-card" data-t="dim">
      <div class="theme-swatch sw-dim"></div>
      <div class="theme-name">Dim</div>
    </button>
    <button type="button" class="theme-card" data-t="light">
      <div class="theme-swatch sw-light"></div>
      <div class="theme-name">Light</div>
    </button>
  </div>
</div>

<div class="step">
  <div class="step-num">Étape 4</div>
  <div class="step-title">Résumé matinal</div>
  <div class="step-sub">Reçois chaque matin une synthèse des événements de la nuit</div>
  <div class="heure-row">
    <span class="heure-label">Heure du résumé</span>
    <select id="select-heure-recap-ob">
      <option value="5">5h00</option>
      <option value="6">6h00</option>
      <option value="7">7h00</option>
      <option value="8" selected>8h00</option>
      <option value="9">9h00</option>
      <option value="10">10h00</option>
      <option value="11">11h00</option>
      <option value="12">12h00</option>
      <option value="18">18h00</option>
      <option value="20">20h00</option>
      <option value="21">21h00</option>
    </select>
  </div>
</div>

<div class="step">
  <div class="step-num">Étape 5</div>
  <div class="step-title">Notifications push</div>
  <div class="step-sub">Reçois une alerte immédiate sur ton téléphone pour les événements critiques</div>
  <div class="notif-card" id="notif-card">
    <div class="notif-info">
      <div class="notif-titre">Alertes en temps réel</div>
      <div class="notif-desc">Guerres, catastrophes, découvertes majeures — uniquement ce qui compte.</div>
    </div>
    <button type="button" class="btn-notif-ob" id="btn-notif-ob" onclick="demanderNotifs()">Activer</button>
  </div>
  <div id="notif-state" style="font-size:13px;color:var(--sub);margin-top:12px;display:none"></div>
</div>

<div class="cta">
  <button class="btn-go" id="btn-go" onclick="submit()">Commencer →</button>
  <a href="/cancel-register" class="cancel">Annuler et supprimer mon compte</a>
</div>

<script>
  document.querySelectorAll(".topic").forEach(b => {
    b.addEventListener("click", () => b.classList.toggle("on"));
  });

  document.querySelectorAll(".theme-card").forEach(b => {
    b.addEventListener("click", () => {
      document.querySelectorAll(".theme-card").forEach(x => x.classList.remove("on"));
      b.classList.add("on");
      document.documentElement.setAttribute("data-theme", b.dataset.t);
    });
  });

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
      const { key } = await fetch("/api/vapid-key").then(r => r.json());
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
    } catch(e) {
      btn.disabled = false;
      btn.textContent = "Activer";
      state.textContent = "Erreur — réessaie depuis les paramètres.";
      state.style.display = "block";
    }
  }

  // Vérifier si déjà accordé
  if (Notification.permission === "granted") {
    const btn = document.getElementById("btn-notif-ob");
    if (btn) { btn.textContent = "✓ Activées"; btn.disabled = true; }
  } else if (Notification.permission === "denied") {
    const btn = document.getElementById("btn-notif-ob");
    if (btn) { btn.textContent = "Bloquées"; btn.disabled = true; }
  }

  async function submit() {
    const domaines = [...document.querySelectorAll(".topic.on")].map(b => b.dataset.d);
    document.getElementById("btn-go").disabled = true;

    const theme        = document.querySelector(".theme-card.on")?.dataset.t || "dark";
    const display_name = document.getElementById("display-name").value.trim();
    const heure_recap  = parseInt(document.getElementById("select-heure-recap-ob").value);

    await fetch("/api/preferences", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ display_name, theme, domaines, heure_recap })
    });
    window.location.href = "/";
  }
</script>
</body></html>"""

@app.route("/onboarding")
def onboarding():
    if not session.get("access_token"):
        return redirect("/login")
    return _ONBOARDING_HTML

@app.route("/cancel-register")
def cancel_register():
    """Supprime le compte créé si l'utilisateur annule l'onboarding."""
    token   = session.get("access_token")
    user_id = session.get("user_id")
    if token and user_id:
        try:
            # supprimer via l'API admin Supabase (service key)
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
                      headers={"apikey": SUPABASE_KEY, "Content-Type": "application/json"},
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
               "/api/update-password", "/privacy", "/auth/google", "/auth/callback",
               "/api/oauth-session"]
    if request.path in exempts or request.path.startswith("/a/"):
        return
    if not session.get("access_token"):
        if request.path.startswith("/api"):
            return jsonify({"erreur": "non authentifié"}), 401
        return redirect("/login")


# ── Alertes (globales) ────────────────────────────────────────────────────────
def charger_alertes():
    if SUPABASE_URL:
        try:
            r = http.get(sb("alertes"), headers=SB_SERVICE,
                         params={"order": "date.desc", "limit": "500"}, timeout=10)
            if r.ok:
                return r.json()
        except Exception as e:
            print(f"Supabase charger_alertes : {e}")
    if os.path.exists(ALERTES_FILE):
        with open(ALERTES_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def sauver_alerte(alerte):
    if SUPABASE_URL:
        try:
            http.post(sb("alertes"),
                      headers={**SB_SERVICE, "Prefer": "resolution=merge-duplicates,return=minimal"},
                      json=alerte, timeout=10)
            return
        except Exception as e:
            print(f"Supabase sauver_alerte : {e}")
    with open(ALERTES_FILE, "w", encoding="utf-8") as f:
        json.dump(alertes, f, ensure_ascii=False, indent=2)


# ── Push notifications ────────────────────────────────────────────────────────
def envoyer_push(titre, body, url, niveau=3):
    if not VAPID_PRIVATE_FILE or not SUPABASE_URL:
        return
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
                    data=json.dumps({"title": titre, "body": body, "url": url}),
                    vapid_private_key=VAPID_PRIVATE_FILE,
                    vapid_claims={"sub": "mailto:ferdinandcharly@gmail.com"})
        except Exception as e:
            if hasattr(e, "response") and e.response and e.response.status_code in [404, 410]:
                http.delete(sb("user_subscriptions"), headers=SB_SERVICE,
                            params={"id": f"eq.{item.get('id')}"}, timeout=5)
            else:
                print(f"Push error : {e}")


# ── Callback bot ──────────────────────────────────────────────────────────────
def ajouter_alerte(domaine, titre, teaser, lien, description="", niveau=2):
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
        "date":        datetime.now().isoformat(),
        "niveau":      niveau,
    }
    alertes.insert(0, alerte)
    if len(alertes) > 500:
        alertes.pop()
    sauver_alerte(alerte)

    notif_url = f"{APP_URL}/#synthese/{alerte['id']}" if APP_URL else lien
    prefix = "🔴" if niveau >= 3 else "🟡"
    envoyer_push(titre=f"{prefix} {domaine[:25]} — {titre[:40]}",
                 body=(accroche or titre)[:120], url=notif_url, niveau=niveau)

bot.on_alerte = ajouter_alerte


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
    hdrs = user_headers()
    if not hdrs:
        return jsonify([])
    try:
        r = http.get(sb("user_sauvegardes"), headers=hdrs,
                     params={"select": "alerte_id"}, timeout=10)
        ids = {row["alerte_id"] for row in (r.json() if r.ok else [])}
        return jsonify([a for a in alertes if a["id"] in ids])
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500

@app.route("/api/sauvegardes/ids")
def api_sauvegardes_ids():
    hdrs = user_headers()
    if not hdrs:
        return jsonify([])
    try:
        r = http.get(sb("user_sauvegardes"), headers=hdrs,
                     params={"select": "alerte_id"}, timeout=10)
        return jsonify([row["alerte_id"] for row in (r.json() if r.ok else [])])
    except:
        return jsonify([])

@app.route("/api/sauvegardes/<alerte_id>", methods=["POST", "DELETE"])
def api_sauvegardes_toggle(alerte_id):
    hdrs = user_headers()
    if not hdrs:
        return jsonify({"erreur": "non authentifié"}), 401
    try:
        aid = int(alerte_id)
    except ValueError:
        return jsonify({"erreur": "id invalide"}), 400
    user_id = session.get("user_id")
    try:
        if request.method == "POST":
            http.post(sb("user_sauvegardes"),
                      headers={**hdrs, "Prefer": "resolution=merge-duplicates,return=minimal"},
                      json={"user_id": user_id, "alerte_id": aid}, timeout=10)
        else:
            http.delete(sb("user_sauvegardes"), headers=hdrs,
                        params={"user_id": f"eq.{user_id}", "alerte_id": f"eq.{aid}"},
                        timeout=10)
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

    return jsonify([c for c in all_corr if match(c)])


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

    if hdrs and user_id:
        def _get_saved():
            r = http.get(sb("user_sauvegardes"), headers=hdrs,
                         params={"select": "alerte_id"}, timeout=8)
            return [row["alerte_id"] for row in r.json()] if r.ok else []

        def _get_prefs():
            r = http.get(sb("user_preferences"), headers=hdrs,
                         params={"user_id": f"eq.{user_id}"}, timeout=8)
            return r.json()[0] if (r.ok and r.json()) else None

        with ThreadPoolExecutor(max_workers=2) as ex:
            f_ids   = ex.submit(_get_saved)
            f_prefs = ex.submit(_get_prefs)
            saved_ids = f_ids.result()
            prefs_row = f_prefs.result()

    prefs = {
        "display_name": prefs_row.get("display_name") or ""                if prefs_row else "",
        "theme":        prefs_row.get("theme")        or "dark"             if prefs_row else "dark",
        "domaines":     prefs_row.get("domaines")     or list(bot.FLUX.keys()) if prefs_row else list(bot.FLUX.keys()),
        "niveau_notif": prefs_row.get("niveau_notif") or 3                 if prefs_row else 3,
        "heure_recap":  8,
        "langue":       prefs_row.get("langue") or "multi"               if prefs_row else "multi",
    }

    # filtrer par domaines préférés
    domaines_actifs = prefs["domaines"]
    alertes_filtrees = [
        a for a in alertes
        if any(d in a.get("domaine", "") for d in domaines_actifs)
    ] if domaines_actifs else alertes

    return jsonify({
        "alertes":      alertes_filtrees,
        "saved_ids":    saved_ids,
        "preferences":  prefs,
        "email":        session.get("user_email", ""),
        "is_new_user":  prefs_row is None,
    })


# ── API préférences unifiées ──────────────────────────────────────────────────
@app.route("/api/preferences", methods=["POST"])
def api_preferences():
    hdrs    = user_headers()
    user_id = session.get("user_id")
    if not hdrs or not user_id:
        return jsonify({"erreur": "non authentifié"}), 401
    data  = request.get_json()
    prefs = {"user_id": user_id}
    for key in ("display_name", "theme", "domaines", "niveau_notif", "langue"):
        if key in data:
            prefs[key] = data[key]
    http.post(sb("user_preferences"),
              headers={**hdrs, "Prefer": "resolution=merge-duplicates,return=minimal"},
              json=prefs, timeout=10)
    return jsonify({"ok": True})


# ── API info utilisateur ──────────────────────────────────────────────────────
@app.route("/api/me")
def api_me():
    return jsonify({
        "email":   session.get("user_email", ""),
        "user_id": session.get("user_id", ""),
    })


# ── Fichiers statiques ────────────────────────────────────────────────────────
@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")

@app.route("/health")
def health():
    return "OK", 200


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

    titre   = alerte.get("titre", "")
    domaine = alerte.get("domaine", "")
    accroche = alerte.get("accroche", "")
    contexte = alerte.get("contexte", "")
    suite    = alerte.get("suite", "")
    lien     = alerte.get("lien", "")
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
<div class="cta"><a href="{app_url}">Ouvrir News Alert →</a></div>
<div class="footer">Partagé via News Alert</div>
</body></html>"""

@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


# ── Bot en arrière-plan ───────────────────────────────────────────────────────
_resumes_envoyes   = {}  # {user_id: date_string}
_telegram_envoye   = None

def envoyer_push_user(user_id, titre, body, url):
    """Envoie une push notification à un utilisateur spécifique."""
    if not VAPID_PRIVATE_FILE:
        return
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
                        data=json.dumps({"title": titre, "body": body, "url": url}),
                        vapid_private_key=VAPID_PRIVATE_FILE,
                        vapid_claims={"sub": "mailto:ferdinandcharly@gmail.com"})
            except Exception:
                pass
    except Exception as e:
        print(f"[Push user] {e}")

def generer_correlations(user_id=None, domaines_user=None, display_name=None):
    """Analyse les alertes des dernières 24h filtrées par domaines, génère les corrélations."""
    global _telegram_envoye

    depuis = (datetime.now() - timedelta(hours=24)).isoformat()
    # Lire depuis Supabase pour ne pas dépendre de la mémoire (réinitialisée au redémarrage)
    try:
        r = http.get(sb("alertes"), headers=SB_SERVICE,
                     params={"date": f"gte.{depuis}", "order": "date.desc", "limit": "200"}, timeout=10)
        alertes_24h = r.json() if r.ok and isinstance(r.json(), list) else []
    except Exception as e:
        print(f"[Corrélation] Erreur lecture Supabase : {e}")
        alertes_24h = [a for a in alertes if a.get("date", "") >= depuis]

    if domaines_user:
        mots = [d.split(" ", 1)[-1] for d in domaines_user]
        alertes_24h = [a for a in alertes_24h
                       if any(m in a.get("domaine", "") for m in mots)]

    print(f"[Corrélation] {len(alertes_24h)} alertes des 24h pour {user_id or 'global'}")
    if len(alertes_24h) < 2:
        print(f"[Corrélation] Pas assez d'alertes, abandon.")
        return

    alertes_compact = [
        {
            "id":      a["id"],
            "titre":   a["titre"],
            "domaine": a["domaine"],
            "accroche": a.get("accroche", ""),
            "contexte": a.get("contexte", ""),
        }
        for a in alertes_24h
    ]

    prompt = (
        "Tu es un analyste géopolitique, scientifique et économique senior. "
        "Voici les alertes d'actualité des dernières 24h :\n"
        + json.dumps(alertes_compact, ensure_ascii=False)
        + """

Identifie les groupes d'événements qui se répondent ou s'influencent mutuellement.
Pour chaque groupe de 2 alertes ou plus, génère une analyse structurée en français.

Critères pour former un groupe :
- Même crise ou conflit qui évolue
- Réaction en chaîne (décision A → conséquence B → réponse C)
- Même acteur impliqué dans plusieurs événements
- Tension entre deux infos contradictoires sur le même sujet

Pour chaque groupe, fournis :
- titre : formulation courte et percutante (max 10 mots)
- contexte : pourquoi ces événements sont liés (1-2 phrases)
- analyse : ce que ça signifie concrètement, l'enjeu réel (2-3 phrases)
- implication : ce qui pourrait se passer ensuite ou ce qu'on surveille (1-2 phrases)
- alertes_ids : liste des ids concernés
- domaines : liste des domaines impliqués

Réponds uniquement avec ce JSON, sans texte autour :
[{"titre":"...","contexte":"...","analyse":"...","implication":"...","alertes_ids":[id1,id2],"domaines":["🌍 Géopolitique"]}]

Si aucun groupe pertinent, réponds [].
Sois exigeant : préfère 2 corrélations solides à 5 superficielles."""
    )

    try:
        rep = bot.client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500, temperature=0.2,
        )
        contenu = rep.choices[0].message.content.strip()
        debut = contenu.find("[")
        fin   = contenu.rfind("]") + 1
        correlations = json.loads(contenu[debut:fin])
    except Exception as e:
        print(f"[Corrélation] Erreur Groq : {e}")
        return

    if not correlations:
        print(f"[Corrélation] Aucune corrélation pour {user_id or 'global'}")
        return

    # Sauvegarder dans Supabase (synthese = contexte + analyse + implication pour compat affichage)
    for i, c in enumerate(correlations):
        c["id"]      = int(datetime.now().timestamp() * 1000) + i
        c["date"]    = datetime.now().isoformat()
        c["synthese"] = f"{c.get('contexte', '')} {c.get('analyse', '')} {c.get('implication', '')}".strip()
        try:
            http.post(sb("correlations"),
                      headers={**SB_SERVICE, "Prefer": "resolution=merge-duplicates,return=minimal"},
                      json=c, timeout=10)
        except Exception as e:
            print(f"[Corrélation] Erreur sauvegarde : {e}")

    # Push à l'utilisateur spécifique
    if user_id and correlations:
        url    = f"{APP_URL}/#correlations" if APP_URL else "/"
        prenom = display_name.split()[0] if display_name else None
        salut  = f"Bonjour {prenom} — " if prenom else ""
        titres = " · ".join(c["titre"] for c in correlations[:2])
        envoyer_push_user(user_id,
                          f"🌅 {salut}{len(correlations)} corrélation(s) du jour",
                          titres[:120], url)

    # Telegram une seule fois par jour
    today = dt_date.today()
    if _telegram_envoye != today:
        _telegram_envoye = today
        date_str = datetime.now().strftime("%d/%m/%Y")
        msg = f"🌅 *Résumé matinal — {date_str}*\n_{len(alertes_24h)} alertes analysées_\n\n"
        for c in correlations[:5]:
            msg += (
                f"*{c['titre']}*\n"
                f"_{c.get('contexte', '')}_ \n"
                f"{c.get('analyse', '')}\n"
                f"👉 {c.get('implication', '')}\n\n"
            )
        bot.envoyer(msg)

    print(f"[Corrélation] {len(correlations)} corrélation(s) pour {user_id or 'global'}")


def check_resumes_matinaux(heure):
    """Envoie le résumé à tous les utilisateurs à 8h chaque matin."""
    if heure != 8 or not SUPABASE_URL:
        return
    today = dt_date.today().isoformat()
    # Vérifier qu'on n'a pas déjà envoyé les résumés ce matin
    if _resumes_envoyes.get("__global__") == today:
        return
    _resumes_envoyes["__global__"] = today
    print(f"[Resume] Génération des corrélations matinales ({today})")
    try:
        r = http.get(sb("user_preferences"), headers=SB_SERVICE,
                     params={"select": "user_id,domaines,display_name"}, timeout=5)
        users = r.json() if r.ok and isinstance(r.json(), list) else []
        print(f"[Resume] {len(users)} utilisateur(s) trouvé(s)")
        for u in users:
            uid = u.get("user_id")
            if uid and _resumes_envoyes.get(uid) != today:
                _resumes_envoyes[uid] = today
                generer_correlations(user_id=uid, domaines_user=u.get("domaines") or [],
                                     display_name=u.get("display_name"))
    except Exception as e:
        print(f"[Resume] Erreur : {e}")


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

def boucle():
    premiere_fois = not os.path.exists(bot.SEEN_FILE)
    bot.verifier(premiere_fois=premiere_fois)
    bot.envoyer(
        "🤖 *Bot d'actualités redémarré*\n"
        "Domaines : Géopolitique 🌍 | Science 🔬 | Tech 💻 | Finance 💰 | Environnement 🌱\n"
        "Fréquence : toutes les 15 min"
    )
    cycles = 0
    while True:
        time.sleep(bot.INTERVALLE)
        bot.verifier()
        cycles += 1

        # résumé matinal : vérifier chaque heure
        now = datetime.now()
        if now.minute < 15:  # fenêtre de 15 min par heure
            check_resumes_matinaux(now.hour)

        # nettoyage Supabase une fois par jour
        if cycles % 96 == 0:
            nettoyer_vieilles_alertes()


if __name__ == "__main__":
    init_vapid()
    alertes.extend(charger_alertes())
    t = threading.Thread(target=boucle, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
