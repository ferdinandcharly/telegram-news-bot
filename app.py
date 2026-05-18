import os
import time
import threading
from flask import Flask
import bot

app = Flask(__name__)

@app.route("/")
@app.route("/health")
def health():
    return "Bot actif ✅", 200

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
    t = threading.Thread(target=boucle, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
