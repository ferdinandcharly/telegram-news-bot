"""Serveur de production (waitress) — remplace `python app.py` (serveur de dev Flask).

    py serve.py

Sur Render : mettre la Start Command sur `python3 serve.py`.
Reprend l'init de app.py (VAPID, cache alertes, bot en thread si RUN_BOT != 0)
puis sert l'app via waitress, un vrai serveur WSGI mono-process (cross-platform,
contrairement a gunicorn qui ne tourne pas sous Windows).

Note : tant que le bot tourne dans ce process, rester en mono-process. Quand le
bot sera deporte sur worker.py (RUN_BOT=0 ici), on pourra paralleliser sans risque.
"""
import os
import threading

from waitress import serve

import app

if __name__ == "__main__":
    app.init_vapid()
    app.alertes.extend(app.charger_alertes())
    if os.getenv("RUN_BOT", "1") != "0":
        threading.Thread(target=app.boucle, daemon=True).start()
    else:
        print("[PROD] bot desactive (RUN_BOT=0) — worker externe")
    port = int(os.environ.get("PORT", 5000))
    print(f"[PROD] waitress en ecoute sur :{port}")
    serve(app.app, host="0.0.0.0", port=port, threads=8)
