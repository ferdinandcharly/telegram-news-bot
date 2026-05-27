import os
import json
import time
import feedparser
from groq import Groq
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

GROQ_KEY = os.getenv("GROQ_API_KEY")

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

# Callbacks optionnels (patchés par app.py)
on_alerte  = None  # (domaine, titre, teaser, lien, resume, niveau) → alerte_id
on_doublon = None  # (alerte_id, source_dict) → None

_SOURCE_NAMES = {
    "bbci.co.uk": "BBC", "bbc.co.uk": "BBC",
    "rfi.fr": "RFI", "france24.com": "France 24",
    "reuters.com": "Reuters", "lemonde.fr": "Le Monde",
    "nasa.gov": "NASA", "sciencedaily.com": "Science Daily",
    "futura-sciences.com": "Futura", "theverge.com": "The Verge",
    "arstechnica.com": "Ars Technica", "technologyreview.com": "MIT Tech Review",
    "wired.com": "Wired", "bfmtv.com": "BFM", "lesechos.fr": "Les Échos",
    "reporterre.net": "Reporterre", "lequipe.fr": "L'Équipe",
}

def _nom_source(url):
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.replace("www.", "")
        for domain, name in _SOURCE_NAMES.items():
            if domain in host:
                return name
        return host.split(".")[0].capitalize()
    except:
        return "Source"


def charger_vus():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def sauver_vus(vus):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(vus), f)


def _mots_cles(titre):
    bruit = {"le","la","les","un","une","des","de","du","en","au","aux","et","ou",
             "est","sont","a","ont","the","a","an","in","of","to","for","is","are"}
    return {m for m in titre.lower().split() if len(m) > 3 and m not in bruit}

# Titres récents pour clustering (les 200 derniers articles sauvegardés)
# Chaque entrée : {"titre": str, "alerte_id": int}
_titres_recents = []

def trouver_doublon(titre):
    """Retourne alerte_id si un article similaire a déjà été sauvegardé, sinon None."""
    mots = _mots_cles(titre)
    if not mots:
        return None
    for item in _titres_recents[-200:]:
        mots_ancien = _mots_cles(item["titre"])
        if not mots_ancien:
            continue
        communs = mots & mots_ancien
        if len(communs) / max(len(mots), len(mots_ancien)) > 0.55:
            return item["alerte_id"]
    return None


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

                    alerte_id_doublon = trouver_doublon(titre)
                    if alerte_id_doublon:
                        if on_doublon:
                            on_doublon(alerte_id_doublon, {"titre": titre, "url": lien, "nom": _nom_source(lien)})
                        continue

                    niveau, teaser = est_important(titre, resume, domaine)

                    if niveau >= 2:
                        source = {"titre": titre, "url": lien, "nom": _nom_source(lien)}
                        new_id = on_alerte(domaine, titre, teaser, lien, resume, niveau, source) if on_alerte else None
                        if new_id:
                            _titres_recents.append({"titre": titre, "alerte_id": new_id})
                            if len(_titres_recents) > 200:
                                _titres_recents.pop(0)
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
    print("=== Bot d'actualités démarré ===")
    premiere_fois = not os.path.exists(SEEN_FILE)

    verifier(premiere_fois=premiere_fois)

    while True:
        time.sleep(INTERVALLE)
        verifier()


if __name__ == "__main__":
    main()
