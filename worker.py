"""Worker autonome — fait tourner UNIQUEMENT le bot, sans serveur web.

Polling RSS + filtre IA Groq + sauvegarde Supabase + push notifications +
résumés matinaux. Conçu pour tourner 24/7 sur une machine dédiée (ex: un
second PC) pendant que le site web reste hébergé sur Render.

    py worker.py

Toute la logique est réutilisée depuis app.py : la simple importation câble
le callback (bot.on_alerte = ajouter_alerte, au niveau module). Le serveur
Flask n'est PAS démarré ici.

IMPORTANT : pour éviter d'avoir deux bots en parallèle (alertes et push en
double), mettre RUN_BOT=0 dans les variables d'environnement de l'instance
web Render dès que ce worker tourne.

Le worker n'a besoin d'aucune connexion entrante (tout est sortant : RSS,
Groq, Supabase, push), donc aucun port à ouvrir ni HTTPS à configurer.
Il lui faut le même .env que le web (GROQ_API_KEY, SUPABASE_*, VAPID_*,
TELEGRAM_*, APP_URL).
"""
import time
import traceback

import app  # câble bot.on_alerte / bot.on_doublon à l'import (niveau module)


def main():
    print("[WORKER] démarrage du bot autonome (aucun serveur web)")
    app.init_vapid()
    app.alertes.extend(app.charger_alertes())

    # Supervision : si la boucle plante sur une exception non gérée, on la
    # relance au lieu de laisser le worker mourir en silence.
    while True:
        try:
            app.boucle()  # bloquant : tourne indéfiniment
        except KeyboardInterrupt:
            print("[WORKER] arrêt demandé.")
            break
        except Exception:
            traceback.print_exc()
            print("[WORKER] boucle plantée — redémarrage dans 60 s")
            time.sleep(60)


if __name__ == "__main__":
    main()
