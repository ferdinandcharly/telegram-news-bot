"""Sauvegarde temporaire de la base Supabase via l'API REST (service role).

Exporte chaque table en JSON dans backups/AAAA-MM-JJ_HHMM/. Aucun pg_dump ni
mot de passe Postgres requis : utilise SUPABASE_URL + SUPABASE_KEY du .env.

    py backup.py

Automatisable via le Planificateur de taches Windows (ex: tous les jours).
Conserve les GARDER dernieres sauvegardes, supprime les plus anciennes.
"""
import os
import sys
import json
import shutil
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()
URL = os.getenv("SUPABASE_URL")
KEY = os.getenv("SUPABASE_KEY")           # service role (lecture globale)

TABLES = ["alertes", "correlations", "user_preferences",
          "user_sauvegardes", "user_subscriptions"]
GARDER  = 14                              # nombre de sauvegardes a conserver
PAGE    = 1000                            # taille de page REST (max Supabase)
RACINE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")

if not URL or not KEY:
    sys.exit("SUPABASE_URL / SUPABASE_KEY manquants dans .env")

HDR = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}


def dump_table(table):
    """Recupere toutes les lignes d'une table (pagination par Range)."""
    rows, offset = [], 0
    while True:
        r = requests.get(
            f"{URL}/rest/v1/{table}",
            headers={**HDR, "Range-Unit": "items",
                     "Range": f"{offset}-{offset + PAGE - 1}"},
            params={"select": "*"}, timeout=60,
        )
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows


def purger_anciennes():
    if not os.path.isdir(RACINE):
        return
    dossiers = sorted(d for d in os.listdir(RACINE)
                      if os.path.isdir(os.path.join(RACINE, d)))
    for vieux in dossiers[:-GARDER] if len(dossiers) > GARDER else []:
        shutil.rmtree(os.path.join(RACINE, vieux), ignore_errors=True)
        print(f"  supprime ancienne sauvegarde : {vieux}")


def main():
    horodatage = datetime.now().strftime("%Y-%m-%d_%H%M")
    dest = os.path.join(RACINE, horodatage)
    os.makedirs(dest, exist_ok=True)
    print(f"[BACKUP] -> {dest}")

    manifeste = {"date": datetime.now().isoformat(), "tables": {}}
    total = 0
    for table in TABLES:
        try:
            rows = dump_table(table)
        except Exception as e:
            print(f"  ERREUR {table} : {e}")
            manifeste["tables"][table] = f"erreur: {e}"
            continue
        with open(os.path.join(dest, f"{table}.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        manifeste["tables"][table] = len(rows)
        total += len(rows)
        print(f"  {table}: {len(rows)} lignes")

    with open(os.path.join(dest, "_manifeste.json"), "w", encoding="utf-8") as f:
        json.dump(manifeste, f, ensure_ascii=False, indent=2)

    purger_anciennes()
    print(f"[BACKUP] termine — {total} lignes au total")


if __name__ == "__main__":
    main()
