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
def _page_auth(sous_titre, form_html, erreur="", lien=""):
    err = f'<p class="err">{erreur}</p>' if erreur else ""
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>News Alert</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#000;color:#f0f0f0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}}
.box{{width:100%;max-width:360px;text-align:center}}
.brand{{font-size:13px;font-weight:600;color:#555;letter-spacing:0.04em;
        text-transform:uppercase;margin-bottom:40px}}
h1{{font-size:22px;font-weight:700;letter-spacing:-0.3px;margin-bottom:6px}}
.sub{{font-size:14px;color:#555;margin-bottom:32px;line-height:1.5}}
input{{width:100%;padding:14px 16px;background:#0e0e0e;border:1px solid #1e1e1e;
       border-radius:10px;color:#f0f0f0;font-size:15px;margin-bottom:10px;outline:none;
       transition:border-color 0.15s}}
input:focus{{border-color:#333}}
button[type=submit]{{width:100%;padding:14px;background:#f0f0f0;color:#000;border:none;
        border-radius:10px;font-size:15px;font-weight:600;cursor:pointer;margin-top:6px;
        letter-spacing:-0.1px}}
.err{{color:#ef4444;font-size:13px;margin-bottom:14px;padding:12px 14px;
      background:#1a0a0a;border:1px solid #3a1010;border-radius:8px}}
.lien{{font-size:13px;color:#444;margin-top:24px}}
.lien a{{color:#666;text-decoration:none}}
.lien a:hover{{color:#999}}
</style></head>
<body><div class="box">
<div class="brand">News Alert</div>
<h1>{sous_titre}</h1>
<p class="sub">Ton fil d'actu filtré par IA.</p>
{err}{form_html}
<p class="lien">{lien}</p>
</div></body></html>"""

_FORM_LOGIN = """<form method="POST">
<input type="email" name="email" placeholder="Adresse email" autocomplete="email"/>
<input type="password" name="password" placeholder="Mot de passe" autocomplete="current-password"/>
<button type="submit">Continuer</button>
</form>"""

_FORM_REGISTER = """<form method="POST">
<input type="email" name="email" placeholder="Adresse email" autocomplete="email"/>
<input type="password" name="password" placeholder="Mot de passe (6 caractères min.)" autocomplete="new-password"/>
<input type="password" name="confirm" placeholder="Confirmer le mot de passe" autocomplete="new-password"/>
<button type="submit">Créer mon compte</button>
</form>"""

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
            return _page_auth("Connexion", _FORM_LOGIN,
                              "Email ou mot de passe incorrect",
                              '<a href="/register">Créer un compte</a>')
        except Exception as e:
            return _page_auth("Connexion", _FORM_LOGIN, f"Erreur : {e}",
                              '<a href="/register">Créer un compte</a>')
    return _page_auth("Connexion", _FORM_LOGIN, "", '<a href="/register">Créer un compte</a>')

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email   = request.form.get("email", "").strip()
        pwd     = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if pwd != confirm:
            return _page_auth("Créer un compte", _FORM_REGISTER,
                              "Les mots de passe ne correspondent pas",
                              '<a href="/login">Se connecter</a>')
        if len(pwd) < 6:
            return _page_auth("Créer un compte", _FORM_REGISTER,
                              "Mot de passe trop court (6 min.)",
                              '<a href="/login">Se connecter</a>')
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
                return _page_auth("Vérifie tes emails",
                                  "<p style='color:#888;font-size:14px'>Lien de confirmation envoyé.</p>",
                                  "", '<a href="/login">Se connecter</a>')
            err = r.json().get("msg") or r.json().get("error_description") or "Erreur"
            return _page_auth("Créer un compte", _FORM_REGISTER, err,
                              '<a href="/login">Se connecter</a>')
        except Exception as e:
            return _page_auth("Créer un compte", _FORM_REGISTER, f"Erreur : {e}",
                              '<a href="/login">Se connecter</a>')
    return _page_auth("Créer un compte", _FORM_REGISTER, "", '<a href="/login">Se connecter</a>')

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
               "/api/refresh-token"]
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
        "heure_recap":  prefs_row.get("heure_recap")  or 8                 if prefs_row else 8,
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
    for key in ("display_name", "theme", "domaines", "niveau_notif", "heure_recap"):
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
    alertes_24h = [a for a in alertes if a.get("date", "") >= depuis]

    if domaines_user:
        mots = [d.split(" ", 1)[-1] for d in domaines_user]
        alertes_24h = [a for a in alertes_24h
                       if any(m in a.get("domaine", "") for m in mots)]

    if len(alertes_24h) < 2:
        print(f"[Corrélation] Pas assez d'alertes ({len(alertes_24h)}) pour {user_id or 'global'}")
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
    """Envoie le résumé aux utilisateurs qui ont choisi cette heure."""
    if not SUPABASE_URL:
        return
    today = dt_date.today().isoformat()
    try:
        # Récupérer aussi last_recap_date pour éviter les doublons au redémarrage
        r = http.get(sb("user_preferences"), headers=SB_SERVICE,
                     params={"heure_recap": f"eq.{heure}",
                             "select": "user_id,domaines,last_recap_date,display_name"}, timeout=5)
        for u in (r.json() if r.ok else []):
            uid  = u.get("user_id")
            last = u.get("last_recap_date", "")
            nom  = u.get("display_name") or None
            if uid and last != today and _resumes_envoyes.get(uid) != today:
                _resumes_envoyes[uid] = today
                http.patch(sb("user_preferences"), headers=SB_SERVICE,
                           params={"user_id": f"eq.{uid}"},
                           json={"last_recap_date": today}, timeout=5)
                generer_correlations(user_id=uid, domaines_user=u.get("domaines") or [],
                                     display_name=nom)
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
