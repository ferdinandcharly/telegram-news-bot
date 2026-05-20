import os
import time
import json
import base64
import threading
import tempfile
from datetime import datetime
from flask import Flask, jsonify, send_from_directory, request, session, redirect
import bot

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")

# ── Données en mémoire ────────────────────────────────────────────────────────
alertes            = []
sauvegardes        = set()
subscriptions_push = []

ALERTES_FILE       = "alertes.json"
SAUVEGARDES_FILE   = "sauvegardes.json"
SUBSCRIPTIONS_FILE = "subscriptions.json"
DOMAINES_DEFAUT    = list(bot.FLUX.keys())

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

SB_HEADERS = {}

def init_supabase():
    global SB_HEADERS
    if SUPABASE_URL and SUPABASE_KEY:
        SB_HEADERS = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
        }
        print("Supabase configuré.")

# ── VAPID (push notifications) ────────────────────────────────────────────────
VAPID_PUBLIC  = os.getenv("VAPID_PUBLIC_KEY", "")
_VAPID_PRIVATE_B64 = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PRIVATE_FILE = None
APP_URL = os.getenv("APP_URL", "").rstrip("/")

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
            print(f"Erreur décodage VAPID : {e}")

def envoyer_push(titre, body, url, niveau=3):
    if not VAPID_PRIVATE_FILE or not subscriptions_push:
        return
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return
    morts = []
    for item in subscriptions_push:
        sub = item.get("sub", item) if isinstance(item, dict) else item
        niveau_min = item.get("niveau_min", 3) if isinstance(item, dict) else 3
        if niveau < niveau_min:
            continue
        try:
            webpush(
                subscription_info=sub,
                data=json.dumps({"title": titre, "body": body, "url": url}),
                vapid_private_key=VAPID_PRIVATE_FILE,
                vapid_claims={"sub": "mailto:ferdinandcharly@gmail.com"},
            )
        except Exception as e:
            if hasattr(e, "response") and e.response and e.response.status_code in [404, 410]:
                morts.append(item)
            else:
                print(f"Push error : {e}")
    for item in morts:
        subscriptions_push.remove(item)
    if morts:
        sauver_subscriptions()


# ── Persistance ───────────────────────────────────────────────────────────────

def sb_url(table):
    return f"{SUPABASE_URL}/rest/v1/{table}"

def charger_alertes():
    if SB_HEADERS:
        try:
            r = requests.get(
                sb_url("alertes"),
                headers={**SB_HEADERS, "Prefer": ""},
                params={"order": "date.desc", "limit": "200"},
                timeout=10
            )
            if r.ok:
                return r.json()
        except Exception as e:
            print(f"Supabase charger_alertes : {e}")
    if os.path.exists(ALERTES_FILE):
        with open(ALERTES_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def sauver_alerte(alerte):
    if SB_HEADERS:
        try:
            requests.post(
                sb_url("alertes"),
                headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
                json=alerte,
                timeout=10
            )
            return
        except Exception as e:
            print(f"Supabase sauver_alerte : {e}")
    with open(ALERTES_FILE, "w", encoding="utf-8") as f:
        json.dump(alertes, f, ensure_ascii=False, indent=2)

def charger_sauvegardes():
    if SB_HEADERS:
        try:
            r = requests.get(sb_url("sauvegardes"), headers=SB_HEADERS, timeout=10)
            if r.ok:
                return set(row["alerte_id"] for row in r.json())
        except Exception as e:
            print(f"Supabase charger_sauvegardes : {e}")
    if os.path.exists(SAUVEGARDES_FILE):
        with open(SAUVEGARDES_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def sauver_sauvegarde(alerte_id, ajouter=True):
    if SB_HEADERS:
        try:
            if ajouter:
                requests.post(
                    sb_url("sauvegardes"),
                    headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
                    json={"alerte_id": alerte_id},
                    timeout=10
                )
            else:
                requests.delete(
                    sb_url("sauvegardes"),
                    headers=SB_HEADERS,
                    params={"alerte_id": f"eq.{alerte_id}"},
                    timeout=10
                )
            return
        except Exception as e:
            print(f"Supabase sauver_sauvegarde : {e}")
    with open(SAUVEGARDES_FILE, "w", encoding="utf-8") as f:
        json.dump(list(sauvegardes), f)

def charger_subscriptions():
    if os.path.exists(SUBSCRIPTIONS_FILE):
        with open(SUBSCRIPTIONS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def sauver_subscriptions():
    with open(SUBSCRIPTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(subscriptions_push, f)


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

    # push filtré par préférence de chaque abonné
    body = accroche or titre
    notif_url = f"{APP_URL}/#synthese/{alerte['id']}" if APP_URL else lien
    prefix = "🔴" if niveau >= 3 else "🟡"
    envoyer_push(titre=f"{prefix} {domaine[:25]} — {titre[:40]}", body=body[:120], url=notif_url, niveau=niveau)

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
            max_tokens=400,
            temperature=0.3,
        )
        return jsonify({"synthese": rep.choices[0].message.content.strip()})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"erreur": str(e)}), 500


# ── API sauvegardes ───────────────────────────────────────────────────────────

@app.route("/api/sauvegardes")
def api_sauvegardes_get():
    return jsonify([a for a in alertes if a["id"] in sauvegardes])

@app.route("/api/sauvegardes/<alerte_id>", methods=["POST", "DELETE"])
def api_sauvegardes_toggle(alerte_id):
    try:
        aid = int(alerte_id)
    except ValueError:
        return jsonify({"erreur": "id invalide"}), 400
    if request.method == "POST":
        sauvegardes.add(aid)
        sauver_sauvegarde(aid, ajouter=True)
    else:
        sauvegardes.discard(aid)
        sauver_sauvegarde(aid, ajouter=False)
    return jsonify({"ok": True})

@app.route("/api/sauvegardes/ids")
def api_sauvegardes_ids():
    return jsonify(list(sauvegardes))

@app.route("/api/notifier/<alerte_id>", methods=["POST"])
def api_notifier(alerte_id):
    try:
        alerte = next((a for a in alertes if a["id"] == int(alerte_id)), None)
        if not alerte:
            return jsonify({"erreur": "introuvable"}), 404
        notif_url = f"{APP_URL}/#synthese/{alerte['id']}" if APP_URL else alerte["lien"]
        body = alerte.get("accroche") or alerte.get("resume", alerte["titre"])
        envoyer_push(
            titre=f"🟡 {alerte['domaine']} — {alerte['titre'][:50]}",
            body=body[:120],
            url=notif_url
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"erreur": str(e)}), 500


# ── API push subscriptions ────────────────────────────────────────────────────

@app.route("/api/subscribe", methods=["POST"])
def api_subscribe():
    data = request.get_json()
    if not data:
        return jsonify({"erreur": "données manquantes"}), 400
    sub = data.get("subscription") or data
    niveau_min = int(data.get("niveau_min", 3))
    endpoint = sub.get("endpoint", "")
    # remplacer si déjà abonné (mise à jour préférence)
    subscriptions_push[:] = [
        i for i in subscriptions_push
        if (i.get("sub", i) if isinstance(i, dict) else i).get("endpoint") != endpoint
    ]
    subscriptions_push.append({"sub": sub, "niveau_min": niveau_min})
    sauver_subscriptions()
    return jsonify({"ok": True})

@app.route("/api/unsubscribe", methods=["POST"])
def api_unsubscribe():
    sub = request.get_json()
    if sub in subscriptions_push:
        subscriptions_push.remove(sub)
        sauver_subscriptions()
    return jsonify({"ok": True})

@app.route("/api/vapid-public")
def api_vapid_public():
    return jsonify({"key": VAPID_PUBLIC})


# ── Fichiers statiques & pages ────────────────────────────────────────────────

@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js",
                               mimetype="application/javascript")

_LOGIN_HTML = """<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>News Alert</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#000;color:#f0f0f0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.box{{width:100%;max-width:320px;padding:0 24px;text-align:center}}
h1{{font-size:20px;font-weight:700;margin-bottom:8px}}
p{{font-size:13px;color:#666;margin-bottom:32px}}
input{{width:100%;padding:14px 16px;background:#111;border:1px solid #222;border-radius:12px;color:#f0f0f0;font-size:16px;margin-bottom:12px;outline:none}}
button{{width:100%;padding:14px;background:#f0f0f0;color:#000;border:none;border-radius:12px;font-size:15px;font-weight:600;cursor:pointer}}
.err{{color:#ef4444;font-size:13px;margin-bottom:12px}}
</style></head>
<body><div class="box">
<h1>News Alert</h1>
<p>Accès privé</p>
{error}
<form method="POST" action="/login">
<input type="password" name="password" placeholder="Mot de passe" autofocus/>
<button type="submit">Entrer</button>
</form>
</div></body></html>"""

@app.before_request
def check_auth():
    if not APP_PASSWORD:
        return
    exempts = ["/health", "/sw.js", "/login"]
    if request.path in exempts:
        return
    if not session.get("authenticated"):
        if request.path.startswith("/api"):
            return jsonify({"erreur": "non authentifié"}), 401
        return redirect("/login")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == APP_PASSWORD:
            session["authenticated"] = True
            return redirect("/")
        return _LOGIN_HTML.format(error='<p class="err">Mot de passe incorrect</p>')
    return _LOGIN_HTML.format(error="")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

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
    init_supabase()
    init_vapid()
    alertes.extend(charger_alertes())
    sauvegardes.update(charger_sauvegardes())
    subscriptions_push.extend(charger_subscriptions())
    t = threading.Thread(target=boucle, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
