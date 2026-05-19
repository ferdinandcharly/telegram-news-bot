import os
import time
import json
import base64
import threading
import tempfile
from datetime import datetime
from flask import Flask, jsonify, send_from_directory, request
import bot

app = Flask(__name__, template_folder="templates", static_folder="static")

# ── Données en mémoire ────────────────────────────────────────────────────────
alertes          = []
sauvegardes      = set()
subscriptions_push = []

ALERTES_FILE       = "alertes.json"
SAUVEGARDES_FILE   = "sauvegardes.json"
SUBSCRIPTIONS_FILE = "subscriptions.json"
DOMAINES_DEFAUT    = list(bot.FLUX.keys())

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

def envoyer_push(titre, body, url):
    if not VAPID_PRIVATE_FILE or not subscriptions_push:
        return
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return
    morts = []
    for sub in subscriptions_push:
        try:
            webpush(
                subscription_info=sub,
                data=json.dumps({"title": titre, "body": body, "url": url}),
                vapid_private_key=VAPID_PRIVATE_FILE,
                vapid_claims={"sub": "mailto:ferdinandcharly@gmail.com"},
            )
        except Exception as e:
            if hasattr(e, "response") and e.response and e.response.status_code in [404, 410]:
                morts.append(sub)
            else:
                print(f"Push error : {e}")
    for sub in morts:
        subscriptions_push.remove(sub)
    if morts:
        sauver_subscriptions()


# ── Persistance ───────────────────────────────────────────────────────────────

def charger_alertes():
    if os.path.exists(ALERTES_FILE):
        with open(ALERTES_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def sauver_alertes_fichier():
    with open(ALERTES_FILE, "w", encoding="utf-8") as f:
        json.dump(alertes, f, ensure_ascii=False, indent=2)

def charger_sauvegardes():
    if os.path.exists(SAUVEGARDES_FILE):
        with open(SAUVEGARDES_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def sauver_sauvegardes_fichier():
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
    sauver_alertes_fichier()

    # push auto uniquement pour les critiques (niveau 3)
    if niveau >= 3:
        body = accroche or titre
        notif_url = f"{APP_URL}/#synthese/{alerte['id']}" if APP_URL else lien
        envoyer_push(titre=f"🔴 {domaine} — {titre[:50]}", body=body[:120], url=notif_url)

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
    else:
        sauvegardes.discard(aid)
    sauver_sauvegardes_fichier()
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
    sub = request.get_json()
    if sub and sub not in subscriptions_push:
        subscriptions_push.append(sub)
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
    sauvegardes.update(charger_sauvegardes())
    subscriptions_push.extend(charger_subscriptions())
    t = threading.Thread(target=boucle, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
