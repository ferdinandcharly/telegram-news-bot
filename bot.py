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
        "https://www.rfi.fr/fr/rss",
        "https://www.france24.com/fr/rss",
        "https://feeds.reuters.com/reuters/worldNews",
        "https://www.lemonde.fr/international/rss_full.xml",
    ],
    "🔬 Science": [
        "https://www.nasa.gov/rss/dyn/breaking_news.rss",
        "https://www.sciencedaily.com/rss/all.xml",
        "https://www.futura-sciences.com/rss/actualites.xml",
    ],
    "💻 Tech & IA": [
        "https://www.theverge.com/rss/index.xml",
        "https://arstechnica.com/feed/",
        "https://www.technologyreview.com/feed/",
        "https://www.wired.com/feed/rss",
    ],
    "💰 Finance": [
        "https://feeds.reuters.com/reuters/businessNews",
        "https://www.bfmtv.com/rss/economie/",
        "https://feeds.reuters.com/reuters/companyNews",
        "https://services.lesechos.fr/rss/les-echos-economie.xml",
    ],
    "🌱 Environnement": [
        "https://reporterre.net/spip.php?page=backend",
        "https://feeds.reuters.com/reuters/environment",
        "https://www.futura-sciences.com/planete/rss/actualites.xml",
        "https://www.lemonde.fr/planete/rss_full.xml",
    ],
    "⚽ Sport": [
        "https://www.lequipe.fr/rss/actu_rss.xml",
        "https://feeds.bbci.co.uk/sport/rss.xml",
        "https://feeds.reuters.com/reuters/sportsNews",
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


def _mots_cles(titre):
    """Extrait les mots significatifs d'un titre pour la déduplication."""
    bruit = {"le","la","les","un","une","des","de","du","en","au","aux","et","ou",
             "est","sont","a","ont","the","a","an","in","of","to","for","is","are"}
    return {m for m in titre.lower().split() if len(m) > 3 and m not in bruit}

# Titres récents pour déduplication (les 200 derniers)
_titres_recents = []

def est_doublon(titre):
    """Retourne True si un article très similaire a déjà été vu récemment."""
    mots = _mots_cles(titre)
    if not mots:
        return False
    for t_ancien in _titres_recents[-200:]:
        mots_ancien = _mots_cles(t_ancien)
        if not mots_ancien:
            continue
        communs = mots & mots_ancien
        # Doublon si >55% des mots clés sont partagés
        if len(communs) / max(len(mots), len(mots_ancien)) > 0.55:
            return True
    return False


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
                    "Évalue l'importance de cet événement sur 3 niveaux :\n"
                    "- niveau 3 (CRITIQUE) : guerre déclarée, catastrophe naturelle massive, "
                    "découverte scientifique historique mondiale, krach financier majeur, "
                    "catastrophe environnementale irréversible, percée technologique majeure "
                    "changeant définitivement un secteur. Événement qui fera la une mondiale.\n"
                    "- niveau 2 (IMPORTANT) : événement majeur et inhabituel qui change "
                    "significativement une situation — pas une mise à jour d'un événement existant. "
                    "Exemples : premier acte diplomatique d'ampleur, décision économique structurelle, "
                    "découverte scientifique solide publiée, incident grave documenté.\n"
                    "- niveau 0 : tout le reste — suivi d'un événement déjà connu, opinion, "
                    "analyse, rapport, nomination, conférence, sondage, produit, mise à jour, "
                    "rumeur, déclaration sans acte concret. Rejette au moins 95% des articles.\n"
                    "Si niveau vaut 2 ou 3, rédige un teaser en français. "
                    "Si le titre n'est pas en français, traduis-le dans titre_fr, sinon laisse titre_fr vide.\n"
                    "Réponds JSON uniquement, avec niveau valant 0, 2 ou 3 :\n"
                    "{\"niveau\": 0, \"titre_fr\": \"\", \"accroche\": \"\", \"contexte\": \"\", \"suite\": \"\"}\n"
                    "ou\n"
                    "{\"niveau\": 2, \"titre_fr\": \"...\", \"accroche\": \"...\", \"contexte\": \"...\", \"suite\": \"...\"}\n"
                    "ou\n"
                    "{\"niveau\": 3, \"titre_fr\": \"...\", \"accroche\": \"...\", \"contexte\": \"...\", \"suite\": \"...\"}"
                )
            }],
            max_tokens=420,
            temperature=0.1,
        )
        contenu = rep.choices[0].message.content.strip()
        # extraire le JSON même s'il y a du texte autour
        debut = contenu.find("{")
        fin = contenu.rfind("}") + 1
        data = json.loads(contenu[debut:fin])
        teaser = {
            "titre_fr": data.get("titre_fr", ""),
            "accroche": data.get("accroche", ""),
            "contexte": data.get("contexte", ""),
            "suite":    data.get("suite", ""),
        }
        return data.get("niveau", 0), teaser
    except Exception as e:
        print(f"  Erreur IA : {e}")
        return 0, {}


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

                    if est_doublon(titre):
                        print(f"  [Doublon] {titre[:60]}")
                        continue

                    _titres_recents.append(titre)

                    niveau, teaser = est_important(titre, resume, domaine)

                    if niveau >= 2:
                        accroche = teaser.get("accroche", "")
                        contexte = teaser.get("contexte", "")
                        suite    = teaser.get("suite", "")

                        # Telegram + push uniquement pour les critiques
                        if niveau >= 3:
                            prefixe = "🔴 CRITIQUE"
                            message = (
                                f"{prefixe} — {domaine}\n\n"
                                f"*{titre}*\n\n"
                                f"📌 {accroche}\n"
                                f"🔍 {contexte}\n"
                                f"👀 {suite}\n\n"
                                f"{lien}"
                            )
                            envoyer(message)

                        if on_alerte:
                            on_alerte(domaine, titre, teaser, lien, resume, niveau)
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
