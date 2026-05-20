import os
import time
import json
import base64
import threading
import tempfile
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, jsonify, send_from_directory, request, session, redirect
import requests as http
import bot

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

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
      display:flex;align-items:center;justify-content:center;min-height:100vh}}
.box{{width:100%;max-width:340px;padding:0 24px;text-align:center}}
h1{{font-size:20px;font-weight:700;margin-bottom:6px}}
.sub{{font-size:13px;color:#666;margin-bottom:28px}}
input{{width:100%;padding:13px 16px;background:#111;border:1px solid #222;border-radius:10px;
       color:#f0f0f0;font-size:15px;margin-bottom:10px;outline:none}}
input:focus{{border-color:#444}}
button{{width:100%;padding:13px;background:#f0f0f0;color:#000;border:none;
        border-radius:10px;font-size:15px;font-weight:600;cursor:pointer;margin-top:4px}}
.err{{color:#ef4444;font-size:13px;margin-bottom:10px}}
.lien{{font-size:13px;color:#555;margin-top:20px}}
.lien a{{color:#888;text-decoration:none}}
</style></head>
<body><div class="box">
<h1>News Alert</h1>
<p class="sub">{sous_titre}</p>
{err}{form_html}
<p class="lien">{lien}</p>
</div></body></html>"""

_FORM_LOGIN = """<form method="POST">
<input type="email" name="email" placeholder="Email" autocomplete="email"/>
<input type="password" name="password" placeholder="Mot de passe"/>
<button type="submit">Se connecter</button></form>"""

_FORM_REGISTER = """<form method="POST">
<input type="email" name="email" placeholder="Email" autocomplete="email"/>
<input type="password" name="password" placeholder="Mot de passe (6 min.)"/>
<input type="password" name="confirm" placeholder="Confirmer le mot de passe"/>
<button type="submit">Créer mon compte</button></form>"""

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
<title>News Alert — Bienvenue</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#000;color:#f0f0f0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      min-height:100vh;padding:40px 24px 60px}}
h1{{font-size:22px;font-weight:700;margin-bottom:6px}}
.sub{{font-size:14px;color:#666;margin-bottom:32px}}
.section{{margin-bottom:28px}}
.section-title{{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;
                color:#555;margin-bottom:12px}}
input[type=text]{{width:100%;padding:13px 16px;background:#111;border:1px solid #222;
                  border-radius:10px;color:#f0f0f0;font-size:15px;outline:none}}
input[type=text]:focus{{border-color:#444}}
.domains{{display:flex;flex-wrap:wrap;gap:8px}}
.domain-btn{{padding:8px 16px;border-radius:20px;border:1px solid #222;background:none;
             color:#666;font-size:13px;font-weight:500;cursor:pointer;transition:all 0.15s}}
.domain-btn.on{{border-color:#f0f0f0;color:#f0f0f0;background:#111}}
.themes{{display:flex;gap:10px}}
.theme-btn{{flex:1;padding:14px 8px;border-radius:12px;border:2px solid #222;
            background:none;cursor:pointer;display:flex;flex-direction:column;
            align-items:center;gap:6px;transition:all 0.15s}}
.theme-btn.on{{border-color:#f0f0f0}}
.theme-preview{{width:100%;height:28px;border-radius:6px}}
.t-dark{{background:#000}}
.t-dim{{background:#161b22}}
.t-light{{background:#fff;border:1px solid #ddd}}
.theme-label{{font-size:12px;color:#888}}
.theme-btn.on .theme-label{{color:#f0f0f0}}
button[type=submit]{{width:100%;padding:14px;background:#f0f0f0;color:#000;border:none;
                     border-radius:12px;font-size:15px;font-weight:600;cursor:pointer;
                     margin-top:16px}}
</style></head>
<body>
<h1>Bienvenue 👋</h1>
<p class="sub">Configure ton expérience en quelques secondes</p>
<form id="form">
  <div class="section">
    <div class="section-title">Comment t'appeler ?</div>
    <input type="text" id="display-name" placeholder="Ton prénom ou pseudo"/>
  </div>
  <div class="section">
    <div class="section-title">Sujets qui t'intéressent</div>
    <div class="domains" id="domains">
      <button type="button" class="domain-btn on" data-d="🌍 Géopolitique">🌍 Géopolitique</button>
      <button type="button" class="domain-btn on" data-d="🔬 Science">🔬 Science</button>
      <button type="button" class="domain-btn on" data-d="💻 Tech &amp; IA">💻 Tech &amp; IA</button>
      <button type="button" class="domain-btn on" data-d="💰 Finance">💰 Finance</button>
      <button type="button" class="domain-btn on" data-d="🌱 Environnement">🌱 Environnement</button>
      <button type="button" class="domain-btn on" data-d="⚽ Sport">⚽ Sport</button>
    </div>
  </div>
  <div class="section">
    <div class="section-title">Thème</div>
    <div class="themes">
      <button type="button" class="theme-btn on" data-t="dark">
        <div class="theme-preview t-dark"></div>
        <span class="theme-label">Dark</span>
      </button>
      <button type="button" class="theme-btn" data-t="dim">
        <div class="theme-preview t-dim"></div>
        <span class="theme-label">Dim</span>
      </button>
      <button type="button" class="theme-btn" data-t="light">
        <div class="theme-preview t-light"></div>
        <span class="theme-label">Light</span>
      </button>
    </div>
  </div>
  <button type="submit">C'est parti →</button>
</form>
<script>
  document.querySelectorAll(".domain-btn").forEach(b =>
    b.addEventListener("click", () => b.classList.toggle("on"))
  );
  document.querySelectorAll(".theme-btn").forEach(b =>
    b.addEventListener("click", () => {
      document.querySelectorAll(".theme-btn").forEach(x => x.classList.remove("on"));
      b.classList.add("on");
    })
  );
  document.getElementById("form").addEventListener("submit", async e => {
    e.preventDefault();
    const domaines = [...document.querySelectorAll(".domain-btn.on")].map(b => b.dataset.d);
    const theme    = document.querySelector(".theme-btn.on")?.dataset.t || "dark";
    const display_name = document.getElementById("display-name").value.trim();
    await fetch("/api/preferences", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{ display_name, theme, domaines }})
    }});
    window.location.href = "/";
  }};
</script>
</body></html>"""

@app.route("/onboarding")
def onboarding():
    if not session.get("access_token"):
        return redirect("/login")
    return _ONBOARDING_HTML

@app.before_request
def check_auth():
    exempts = ["/health", "/sw.js", "/login", "/register", "/onboarding"]
    if request.path in exempts:
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
                         params={"order": "date.desc", "limit": "200"}, timeout=10)
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
    if len(alertes) > 200:
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
        "display_name": prefs_row.get("display_name") or "" if prefs_row else "",
        "theme":        prefs_row.get("theme")        or "dark" if prefs_row else "dark",
        "domaines":     prefs_row.get("domaines")     or list(bot.FLUX.keys()) if prefs_row else list(bot.FLUX.keys()),
    }

    return jsonify({
        "alertes":      alertes,
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
    for key in ("display_name", "theme", "domaines"):
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

@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


# ── Bot en arrière-plan ───────────────────────────────────────────────────────
def boucle():
    premiere_fois = not os.path.exists(bot.SEEN_FILE)
    bot.verifier(premiere_fois=premiere_fois)
    bot.envoyer(
        "🤖 *Bot d'actualités redémarré*\n"
        "Domaines : Géopolitique 🌍 | Science 🔬 | Tech 💻 | Finance 💰 | Environnement 🌱\n"
        "Fréquence : toutes les 15 min"
    )
    while True:
        time.sleep(bot.INTERVALLE)
        bot.verifier()


if __name__ == "__main__":
    init_vapid()
    alertes.extend(charger_alertes())
    t = threading.Thread(target=boucle, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
