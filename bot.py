import os
import gc
import json
import time
import unicodedata
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
        "https://www.bfmtv.com/rss/economie/",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.lemonde.fr/economie/rss_full.xml",
        "https://www.franceinfo.fr/economie.rss",
    ],
    "🌱 Environnement": [
        "https://reporterre.net/spip.php?page=backend",
        "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        "https://www.futura-sciences.com/planete/rss/actualites.xml",
        "https://www.lemonde.fr/planete/rss_full.xml",
    ],
    "⚽ Sport": [
        "https://www.lequipe.fr/rss/actu_rss.xml",
        "https://feeds.bbci.co.uk/sport/rss.xml",
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
    "franceinfo.fr": "France Info",
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


# Domaines des sources déjà francophones : inutile de retraduire leur titre.
_DOMAINES_FR = {
    "rfi.fr", "france24.com", "lemonde.fr", "bfmtv.com", "franceinfo.fr",
    "futura-sciences.com", "reporterre.net", "lequipe.fr", "lesechos.fr",
}

def _est_source_francaise(lien):
    try:
        from urllib.parse import urlparse
        host = urlparse(lien).netloc.replace("www.", "")
        return any(d in host for d in _DOMAINES_FR)
    except:
        return False


def _extraire_image(article, lien):
    """Récupère une image pour l'article. Priorité au flux RSS (gratuit),
    sinon fallback sur la balise og:image de la page (1 requête HTTP)."""
    import re
    # 1) Depuis le flux RSS — aucune requête réseau supplémentaire
    try:
        for cle in ("media_content", "media_thumbnail"):
            medias = article.get(cle)
            if medias and medias[0].get("url"):
                return medias[0]["url"]
        for lst in (article.get("links", []), article.get("enclosures", [])):
            for enc in lst:
                if enc.get("type", "").startswith("image") and enc.get("href"):
                    return enc["href"]
        html = article.get("summary", "")
        if article.get("content"):
            html += article["content"][0].get("value", "")
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html)
        if m:
            return m.group(1)
    except Exception:
        pass
    # 2) Fallback : og:image de la page de l'article
    try:
        import requests
        r = requests.get(lien, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
        if r.ok:
            html = r.text[:200000]  # l'og:image est dans le <head>
            for pat in (
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
            ):
                m = re.search(pat, html, re.IGNORECASE)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return ""


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
                    "découverte scientifique historique mondiale, krach financier majeur systémique, "
                    "catastrophe environnementale irréversible, percée technologique majeure "
                    "changeant définitivement un secteur. Événement qui fera la une mondiale pendant des jours.\n"
                    "- niveau 2 (IMPORTANT) : rupture réelle et inhabituelle à portée internationale — "
                    "premier acte diplomatique d'ampleur, conflit armé qui éclate ou s'étend, "
                    "décision économique structurelle affectant plusieurs pays, "
                    "découverte scientifique solide et publiée, incident grave documenté à impact mondial.\n"
                    "- niveau 0 : TOUT le reste. En particulier, rejette sans exception : "
                    "politique intérieure d'un pays (débats parlementaires, nominations, sondages, élections locales), "
                    "résultats d'entreprises, fluctuations de marchés boursiers ordinaires, "
                    "déclarations politiques sans acte concret, mises à jour d'un événement déjà connu, "
                    "conférences, rapports, opinions, analyses, produits tech grand public. "
                    "Rejette au moins 95% des articles.\n\n"
                    "Si niveau vaut 2 ou 3, rédige un teaser en français.\n\n"
                    "Classe aussi l'article avec :\n"
                    "- portee : 'locale' | 'nationale' | 'regionale' | 'mondiale'\n"
                    "- theme_fin : une seule valeur parmi : 'politique-interieure', 'conflit', 'diplomatie', "
                    "'economie-macro', 'marche-finance', 'science', 'tech-ia', 'environnement', "
                    "'catastrophe', 'sport', 'societe'\n"
                    "- pays : si la portée est nationale, régionale ou locale, indique le pays "
                    "principal concerné en français (ex: France, États-Unis, Côte d'Ivoire, Sénégal). "
                    "Laisse vide (\"\") si la portée est mondiale.\n\n"
                    "Réponds JSON uniquement :\n"
                    "{\"niveau\": 0, \"portee\": \"nationale\", \"theme_fin\": \"politique-interieure\", "
                    "\"pays\": \"\", \"accroche\": \"\", \"contexte\": \"\", \"suite\": \"\"}"
                )
            }],
            max_tokens=480,
            temperature=0.1,
        )
        contenu = rep.choices[0].message.content.strip()
        debut = contenu.find("{")
        fin = contenu.rfind("}") + 1
        data = json.loads(contenu[debut:fin])
        teaser = {
            "titre_fr": "",  # rempli par traduire_titre() pour les sources non francophones
            "accroche": data.get("accroche", ""),
            "contexte": data.get("contexte", ""),
            "suite":    data.get("suite", ""),
            "portee":   data.get("portee", "mondiale"),
            "theme_fin": data.get("theme_fin", ""),
            "pays":     data.get("pays", ""),
        }
        return data.get("niveau", 0), teaser
    except Exception as e:
        print(f"  Erreur IA : {e}")
        return 0, {}


def traduire_titre(titre):
    """Traduit un titre d'article en français via un appel Groq dédié et minimal.
    Bien plus fiable que de le demander dans le gros prompt de filtrage.
    Appelé uniquement pour les articles gardés (niveau >= 2) de sources non francophones."""
    try:
        rep = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "user",
                "content": (
                    "Traduis ce titre d'actualité en français, de façon fidèle et naturelle. "
                    "Conserve les noms propres. Ne mets ni guillemets ni explication : "
                    "réponds uniquement par le titre traduit.\n\n"
                    f"Titre : {titre}"
                )
            }],
            max_tokens=120,
            temperature=0.2,
        )
        return rep.choices[0].message.content.strip().strip('"').strip()
    except Exception as e:
        print(f"  Erreur traduction : {e}")
        return ""


def _sans_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").lower()

# Pré-filtre Groq-free : on ne rejette QUE le bruit universel, jamais l'ambigu.
# Volontairement minimal — dans le moindre doute, l'article part au triage Groq.
_BRUIT_TITRE = ("horoscope", "sudoku", "mots croises", "mots fleches",
                "programme tv", "votre week-end", "recette")
_BRUIT_URL = ("/horoscope", "/meteo", "/cuisine", "/recette", "/people",
              "/loisirs", "/jeux/", "/mots-croises", "/television", "/tele/",
              "/programme-tv", "/sortir/")

def pre_filtre_rejette(titre, lien):
    """Rejet local (0 appel Groq) du bruit universel uniquement.
    Dans le doute → False : on laisse l'article passer au triage Groq."""
    t = _sans_accents(titre)
    if any(m in t for m in _BRUIT_TITRE):
        return True
    u = (lien or "").lower()
    if any(p in u for p in _BRUIT_URL):
        return True
    return False


# Termes sans ambiguïté : un article qui les contient est du sport,
# même s'il vient d'un flux d'actualité générale (BBC World, Le Monde…).
_SPORT_TITRE = (
    "fifa", "uefa", "ballon d'or", "coupe du monde", "world cup", "ligue des champions",
    "champions league", "premier league", "ligue 1", "la liga", "bundesliga", "serie a",
    "roland-garros", "roland garros", "wimbledon", "jeux olympiques", "olympic", "jo 2",
    "nba", "nfl", "tour de france", "grand prix", "formule 1", "formula 1", "six nations",
    "ballon d or", "champions cup", "europa league", "psg", "real madrid", "fc barcelone",
    "top 14", "roland‑garros",
)
_SPORT_URL = ("/sport/", "/sports/", "lequipe.fr", "/football/", "/rugby/", "/tennis/")


def reclasser_domaine(domaine, titre, lien):
    """Rebascule en Sport un article clairement sportif venu d'un flux généraliste.
    0 appel Groq : simple détection de mots-clés sans ambiguïté."""
    if "Sport" in domaine:
        return domaine
    t = _sans_accents(titre)
    u = (lien or "").lower()
    if any(m in t for m in _SPORT_TITRE) or any(p in u for p in _SPORT_URL):
        return "⚽ Sport"
    return domaine


def triage_groupe(lot):
    """Triage grossier et PERMISSIF de plusieurs articles en un seul appel Groq.
    `lot` : liste de dicts {domaine, titre, ...}.
    Retourne une liste de bool alignée sur `lot` (True = à analyser en détail).
    En cas d'erreur de parsing → tout à True : on n'écarte jamais par accident."""
    if not lot:
        return []
    liste = "\n".join(f"[{i}] ({c['domaine']}) {c['titre']}" for i, c in enumerate(lot))
    try:
        rep = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "user",
                "content": (
                    "Voici des titres d'actualité numérotés. Pour chacun, indique s'il PEUT "
                    "être un événement d'importance internationale : rupture géopolitique, "
                    "conflit armé, catastrophe, découverte scientifique majeure, décision "
                    "économique structurelle, percée technologique. Sois PERMISSIF : dans le "
                    "doute réponds 1. Réponds 0 uniquement pour le bruit évident (politique "
                    "intérieure routinière, sport, résultats d'entreprises ordinaires, faits "
                    "divers, people, opinions, conseils pratiques).\n\n"
                    f"{liste}\n\n"
                    "Réponds en JSON uniquement : un tableau d'objets "
                    "{\"i\": <numéro>, \"v\": 0 ou 1}."
                )
            }],
            max_tokens=400,
            temperature=0.1,
        )
        contenu = rep.choices[0].message.content.strip()
        debut = contenu.find("[")
        fin = contenu.rfind("]") + 1
        verdicts = json.loads(contenu[debut:fin])
        garde = [True] * len(lot)
        for v in verdicts:
            i = v.get("i")
            if isinstance(i, int) and 0 <= i < len(lot):
                garde[i] = bool(v.get("v", 1))
        return garde
    except Exception as e:
        print(f"  Erreur triage : {e}")
        return [True] * len(lot)


def verifier(premiere_fois=False):
    vus = charger_vus()
    nouveaux_ids = set()

    # ── Phase 1 : collecte + dédoublonnage + pré-filtre local (0 appel Groq) ──
    candidats = []  # {domaine, titre, resume, lien, article}
    for domaine, urls in FLUX.items():
        for url in urls:
            try:
                feed = feedparser.parse(url)
                entrees = feed.entries[:8]
                # On libère tout de suite le gros objet feed parsé (le flux complet
                # lemonde rss_full pèse plusieurs Mo) : on ne garde que les 8 entrées utiles.
                feed = None
                for article in entrees:
                    aid = article.get("id") or article.get("link", "")
                    if not aid or aid in vus:
                        continue
                    nouveaux_ids.add(aid)

                    if premiere_fois:
                        continue  # premier lancement : mémoriser sans alerter

                    titre  = article.get("title", "")
                    resume = article.get("summary", article.get("description", ""))
                    lien   = article.get("link", "")

                    # Doublon inter-sources (cross-cycle) → rattachement, aucun appel Groq
                    alerte_id_doublon = trouver_doublon(titre)
                    if alerte_id_doublon:
                        if on_doublon:
                            on_doublon(alerte_id_doublon, {"titre": titre, "url": lien, "nom": _nom_source(lien)})
                        continue

                    # Pré-filtre Groq-free : écarte le bruit universel évident
                    if pre_filtre_rejette(titre, lien):
                        continue

                    # Reclassement par contenu : un flux généraliste (BBC World…)
                    # publie parfois du sport → on le remet dans le bon domaine.
                    dom = reclasser_domaine(domaine, titre, lien)

                    candidats.append({"domaine": dom, "titre": titre,
                                      "resume": resume, "lien": lien, "article": article})
            except Exception as e:
                print(f"  Erreur flux {url[:50]} : {e}")

    # ── Phase 2 : triage groupé permissif (Groq, par paquets de 8) ────────────
    survivants = []
    for i in range(0, len(candidats), 8):
        lot = candidats[i:i + 8]
        for c, garde in zip(lot, triage_groupe(lot)):
            if garde:
                survivants.append(c)
        time.sleep(1)

    # ── Phase 3 : analyse complète + alerte des survivants uniquement ─────────
    alertes = 0
    for c in survivants:
        titre, resume, lien, domaine = c["titre"], c["resume"], c["lien"], c["domaine"]

        # Re-test doublon : un survivant peut dupliquer une alerte créée dans CE cycle
        alerte_id_doublon = trouver_doublon(titre)
        if alerte_id_doublon:
            if on_doublon:
                on_doublon(alerte_id_doublon, {"titre": titre, "url": lien, "nom": _nom_source(lien)})
            continue

        niveau, teaser = est_important(titre, resume, domaine)

        if niveau >= 2:
            # Traduction fiable du titre pour les sources non francophones.
            if not _est_source_francaise(lien):
                titre_fr = traduire_titre(titre)
                if titre_fr:
                    teaser["titre_fr"] = titre_fr
            image = _extraire_image(c["article"], lien)
            source = {"titre": titre, "url": lien, "nom": _nom_source(lien)}
            new_id = on_alerte(domaine, titre, teaser, lien, resume, niveau, source, image) if on_alerte else None
            if new_id:
                _titres_recents.append({"titre": titre, "alerte_id": new_id})
                if len(_titres_recents) > 200:
                    _titres_recents.pop(0)
            alertes += 1
            time.sleep(2)

    vus.update(nouveaux_ids)
    # Borne le set : il grossit sans fin tant que l'instance reste éveillée
    # (UptimeRobot la ping 24/7). On ne garde que les ~4000 IDs les plus récents.
    if len(vus) > 4000:
        vus = set(list(vus)[-4000:])
    sauver_vus(vus)

    # Libère la mémoire transitoire accumulée pendant le parsing RSS + appels Groq.
    gc.collect()

    h = datetime.now().strftime("%H:%M")
    if premiere_fois:
        print(f"[{h}] Démarrage — {len(nouveaux_ids)} articles mémorisés. Surveillance active.")
    else:
        print(f"[{h}] {len(nouveaux_ids)} nouveaux | {len(candidats)} après pré-filtre "
              f"| {len(survivants)} après triage | {alertes} alerte(s)")


def main():
    print("=== Bot d'actualités démarré ===")
    premiere_fois = not os.path.exists(SEEN_FILE)

    verifier(premiere_fois=premiere_fois)

    while True:
        time.sleep(INTERVALLE)
        verifier()


if __name__ == "__main__":
    main()
