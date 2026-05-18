import os
import json
import time
import feedparser
import requests
from groq import Groq
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

GROQ_KEY = os.getenv("GROQ_API_KEY")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID")

SEEN_FILE = "vus.json"
INTERVALLE = 900  # 15 minutes

FLUX = {
    "🌍 Géopolitique": [
        "http://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.lemonde.fr/international/rss_full.xml",
        "https://www.rfi.fr/fr/rss",
    ],
    "🔬 Science": [
        "https://www.nasa.gov/rss/dyn/breaking_news.rss",
        "https://www.sciencedaily.com/rss/all.xml",
        "https://www.futura-sciences.com/rss/actualites.xml",
    ],
    "💻 Tech & IA": [
        "https://www.wired.com/feed/rss",
        "https://www.technologyreview.com/feed/",
    ],
    "💰 Finance": [
        "https://feeds.reuters.com/reuters/businessNews",
        "https://services.lesechos.fr/rss/les-echos-economie.xml",
    ],
    "🌱 Environnement": [
        "https://www.lemonde.fr/planete/rss_full.xml",
        "https://reporterre.net/spip.php?page=backend",
    ],
}

client = Groq(api_key=GROQ_KEY)

# Callback optionnel pour sauvegarder les alertes (utilisé par app.py)
on_alerte = None


def charger_vus():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def sauver_vus(vus):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(vus), f)


def est_important(titre, resume, domaine):
    try:
        rep = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "user",
                "content": (
                    f"Domaine : {domaine}\n"
                    f"Titre : {titre}\n"
                    f"Résumé : {resume[:400]}\n\n"
                    "Est-ce un événement VRAIMENT MAJEUR qui changera le cours des choses ? "
                    "Exemples acceptés : guerre déclarée, catastrophe naturelle massive, "
                    "découverte scientifique historique, rupture diplomatique majeure, "
                    "krach financier, catastrophe environnementale irréversible, "
                    "percée technologique qui redéfinit un secteur entier. "
                    "Exemples REFUSÉS : nouveau produit, mise à jour, partenariat, rapport, "
                    "nomination, conférence, sondage, tendance, article d'opinion. "
                    "Règle : si on peut l'ignorer sans conséquence, ce n'est pas important. "
                    "Rejette au moins 95% des articles.\n"
                    "Si important, rédige un teaser en 3 points courts en français.\n"
                    "Réponds JSON uniquement : "
                    "{\"important\": true/false, "
                    "\"accroche\": \"ce qui s'est passé en 1 phrase\", "
                    "\"contexte\": \"pourquoi c'est important en 1 phrase\", "
                    "\"suite\": \"ce qu'il faut surveiller en 1 phrase\"}"
                )
            }],
            max_tokens=250,
            temperature=0.1,
        )
        contenu = rep.choices[0].message.content.strip()
        # extraire le JSON même s'il y a du texte autour
        debut = contenu.find("{")
        fin = contenu.rfind("}") + 1
        data = json.loads(contenu[debut:fin])
        teaser = {
            "accroche": data.get("accroche", ""),
            "contexte": data.get("contexte", ""),
            "suite": data.get("suite", ""),
        }
        return data.get("important", False), teaser
    except Exception as e:
        print(f"  Erreur IA : {e}")
        return False, {}


def envoyer(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"  Erreur Telegram : {e}")


def verifier(premiere_fois=False):
    vus = charger_vus()
    nouveaux_ids = set()
    alertes = 0

    for domaine, urls in FLUX.items():
        for url in urls:
            try:
                feed = feedparser.parse(url)
                for article in feed.entries[:8]:
                    aid = article.get("id") or article.get("link", "")
                    if not aid or aid in vus:
                        continue
                    nouveaux_ids.add(aid)

                    if premiere_fois:
                        continue  # premier lancement : mémoriser sans alerter

                    titre  = article.get("title", "")
                    resume = article.get("summary", article.get("description", ""))
                    lien   = article.get("link", "")

                    important, teaser = est_important(titre, resume, domaine)

                    if important:
                        accroche = teaser.get("accroche", "")
                        contexte = teaser.get("contexte", "")
                        suite    = teaser.get("suite", "")
                        message = (
                            f"{domaine}\n\n"
                            f"*{titre}*\n\n"
                            f"📌 {accroche}\n"
                            f"🔍 {contexte}\n"
                            f"👀 {suite}\n\n"
                            f"{lien}"
                        )
                        envoyer(message)
                        if on_alerte:
                            on_alerte(domaine, titre, teaser, lien, resume)
                        alertes += 1
                        time.sleep(2)

            except Exception as e:
                print(f"  Erreur flux {url[:50]} : {e}")

    vus.update(nouveaux_ids)
    sauver_vus(vus)

    h = datetime.now().strftime("%H:%M")
    if premiere_fois:
        print(f"[{h}] Démarrage — {len(nouveaux_ids)} articles mémorisés. Surveillance active.")
    else:
        print(f"[{h}] {len(nouveaux_ids)} nouveaux articles analysés | {alertes} alerte(s) envoyée(s)")


def main():
    print("=== Bot d'actualités Telegram ===")
    premiere_fois = not os.path.exists(SEEN_FILE)

    verifier(premiere_fois=premiere_fois)

    envoyer(
        "🤖 *Bot d'actualités démarré*\n"
        "Domaines : Géopolitique 🌍 | Science 🔬\n"
        "Fréquence : toutes les 15 min"
    )

    while True:
        time.sleep(INTERVALLE)
        verifier()


if __name__ == "__main__":
    main()
