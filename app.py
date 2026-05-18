import os
import time
import json
import threading
from datetime import datetime
from flask import Flask, jsonify, send_from_directory, request
import bot

app = Flask(__name__, template_folder="templates")

alertes = []
ALERTES_FILE = "alertes.json"
DOMAINES_FILE = "domaines.json"

DOMAINES_DEFAUT = list(bot.FLUX.keys())


def charger_alertes():
    if os.path.exists(ALERTES_FILE):
        with open(ALERTES_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def sauver_alertes_fichier():
    with open(ALERTES_FILE, "w", encoding="utf-8") as f:
        json.dump(alertes, f, ensure_ascii=False, indent=2)


def ajouter_alerte(domaine, titre, resume, lien):
    alerte = {
        "id": len(alertes),
        "domaine": domaine,
        "titre": titre,
        "resume": resume,
        "lien": lien,
        "date": datetime.now().isoformat(),
    }
    alertes.insert(0, alerte)
    if len(alertes) > 200:
        alertes.pop()
    sauver_alertes_fichier()


def charger_domaines():
    if os.path.exists(DOMAINES_FILE):
        with open(DOMAINES_FILE, encoding="utf-8") as f:
            return json.load(f)
    return DOMAINES_DEFAUT


def sauver_domaines(domaines):
    with open(DOMAINES_FILE, "w", encoding="utf-8") as f:
        json.dump(domaines, f, ensure_ascii=False)


# Brancher le callback du bot
bot.on_alerte = ajouter_alerte


# ── API ──────────────────────────────────────────────────────────────────────

@app.route("/api/alertes")
def api_alertes():
    domaine = request.args.get("domaine")
    if domaine and domaine != "Tout":
        filtrees = [a for a in alertes if domaine in a["domaine"]]
        return jsonify(filtrees)
    return jsonify(alertes)


@app.route("/api/stats")
def api_stats():
    aujourd_hui = datetime.now().date().isoformat()
    alertes_jour = [a for a in alertes if a["date"].startswith(aujourd_hui)]
    return jsonify({
        "total": len(alertes),
        "aujourd_hui": len(alertes_jour),
        "domaines": {d: sum(1 for a in alertes if d in a["domaine"]) for d in DOMAINES_DEFAUT},
    })


@app.route("/api/domaines", methods=["GET", "POST"])
def api_domaines():
    if request.method == "POST":
        data = request.get_json()
        nouveaux = data.get("domaines", [])
        # Mettre à jour les flux actifs dans le bot
        bot.FLUX = {k: v for k, v in bot.FLUX.items() if k in nouveaux}
        sauver_domaines(nouveaux)
        return jsonify({"ok": True})
    return jsonify(charger_domaines())


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
        "Domaines : Géopolitique 🌍 | Science 🔬\n"
        "Fréquence : toutes les 15 min"
    )
    while True:
        time.sleep(bot.INTERVALLE)
        bot.verifier()


if __name__ == "__main__":
    alertes.extend(charger_alertes())
    t = threading.Thread(target=boucle, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
