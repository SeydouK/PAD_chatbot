"""Collecte hors ligne d'un site web vers des fichiers texte pour le RAG.

Usage (depuis le dossier pad-chatbot) :
    pip install requests beautifulsoup4 lxml pypdf
    python scrape_site.py https://www.portdakar.sn --max-pages 150
    python scrape_site.py https://www.portdakar.sn --dry-run        # liste sans écrire

Ce que fait le script :
  * lit robots.txt et respecte ses interdictions (et son Crawl-delay) ;
  * part du sitemap.xml s'il existe, sinon de la page d'accueil, et suit les liens du MÊME domaine ;
  * attend --delai secondes entre deux requêtes (poli : le site n'est pas surchargé) ;
  * extrait le texte principal (retire menus, pieds de page, scripts) et lit les PDF ;
  * retire les lignes répétées sur beaucoup de pages (bandeaux, mentions légales) ;
  * écrit docs/web/<nom>.txt avec l'URL en 1re ligne, que rag.py affiche comme source.
Relancer le script remplace les fichiers ; les pages disparues du site sont supprimées.
"""
import argparse
import hashlib
import io
import re
import sys
import time
import unicodedata
import urllib.robotparser
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urldefrag, urlparse

import requests
from bs4 import BeautifulSoup

AGENT = "PAD-chatbot-indexeur/1.0 (collecte pour assistant documentaire interne)"
EXT_IGNOREES = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip", ".rar", ".mp3", ".mp4",
                ".avi", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".css", ".js", ".ico")
BALISES_BRUIT = ["script", "style", "noscript", "nav", "header", "footer", "aside", "form",
                 "iframe", "svg", "button", "select"]
ZONES_PRINCIPALES = ["main", "article", "[role=main]", "#content", "#main", ".content", ".region-content"]
MIN_MOTS_PAGE = 40
TAILLE_MAX = 15_000_000   # 15 Mo : au-delà, on ignore le fichier


def normaliser(url: str) -> str:
    url, _ = urldefrag(url)
    p = urlparse(url)
    return p._replace(path=p.path or "/").geturl()


def meme_site(url: str, racine: str) -> bool:
    a, b = urlparse(url).netloc.lower(), urlparse(racine).netloc.lower()
    return a.removeprefix("www.") == b.removeprefix("www.")


def nom_fichier(url: str) -> str:
    p = urlparse(url)
    base = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFD", (p.path + ("?" + p.query if p.query else "")).lower())
                  .encode("ascii", "ignore").decode()).strip("-") or "accueil"
    return f"{base[:60]}-{hashlib.sha1(url.encode()).hexdigest()[:6]}.txt"


class Collecteur:
    def __init__(self, racine, delai, max_pages, sortie, dry_run, verifier_ssl=True):
        self.racine = racine.rstrip("/")
        self.delai, self.max_pages, self.sortie, self.dry_run = delai, max_pages, Path(sortie), dry_run
        self.s = requests.Session()
        self.s.headers["User-Agent"] = AGENT
        self.s.verify = verifier_ssl
        if not verifier_ssl:
            import urllib3
            urllib3.disable_warnings()
            print("ATTENTION : vérification SSL désactivée (--insecure).")
        self.robots = urllib.robotparser.RobotFileParser()
        self.dernier = 0.0
        self.pages = {}   # url -> (titre, texte)

    # ---------- réseau poli ----------
    def get(self, url):
        attente = self.delai - (time.time() - self.dernier)
        if attente > 0:
            time.sleep(attente)
        self.dernier = time.time()
        try:
            r = self.s.get(url, timeout=20, stream=True, allow_redirects=True)
            if not meme_site(r.url, self.racine):       # redirigé hors du site : on ignore
                return None
            contenu = r.raw.read(TAILLE_MAX + 1, decode_content=True)
            if r.status_code != 200 or len(contenu) > TAILLE_MAX:
                print(f"  ignoré ({r.status_code}) {url}")
                return None
            r.content_brut = contenu
            return r, contenu
        except requests.RequestException as e:
            print(f"  erreur réseau {url} : {e.__class__.__name__}: {str(e)[:300]}")
            return None

    def lire_robots(self):
        r = self.get(self.racine + "/robots.txt")
        if r:
            self.robots.parse(r[1].decode("utf-8", "replace").splitlines())
            cd = self.robots.crawl_delay(AGENT)
            if cd:
                self.delai = max(self.delai, float(cd))
            print(f"robots.txt lu (délai effectif {self.delai}s)")
        else:
            self.robots.parse([])   # pas de robots.txt : tout est permis
            print("pas de robots.txt accessible : aucune restriction déclarée")
        self.sitemaps = self.robots.site_maps() or [self.racine + "/sitemap.xml"]

    def autorise(self, url):
        return self.robots.can_fetch(AGENT, url)

    # ---------- découverte ----------
    def urls_sitemap(self):
        trouvees, a_voir, vus = [], list(self.sitemaps), set()
        while a_voir and len(vus) < 30:
            sm = a_voir.pop(0)
            if sm in vus:
                continue
            vus.add(sm)
            r = self.get(sm)
            if not r:
                continue
            for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", r[1].decode("utf-8", "replace"), re.S):
                (a_voir if loc.lower().endswith(".xml") else trouvees).append(loc)
        return trouvees

    # ---------- extraction ----------
    @staticmethod
    def texte_html(html: bytes):
        soup = BeautifulSoup(html, "lxml")
        if not soup.find("a", href=True):      # lxml peut abandonner sur du HTML mal formé (Drupal 7)
            soup = BeautifulSoup(html, "html.parser")
        titre = (soup.title.get_text(" ", strip=True) if soup.title else "") or ""
        liens = [a["href"] for a in soup.find_all("a", href=True)]
        for t in soup(BALISES_BRUIT):
            t.decompose()
        zone = None
        for sel in ZONES_PRINCIPALES:
            zone = soup.select_one(sel)
            if zone and len(zone.get_text(" ", strip=True).split()) >= MIN_MOTS_PAGE:
                break
            zone = None
        zone = zone or soup.body or soup
        lignes = []
        for el in zone.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "th", "blockquote"]):
            if el.find(["p", "li", "td", "th"]):       # conteneur : ses enfants seront lus
                continue
            t = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
            if t:
                lignes.append(t)
        return titre, lignes, liens

    @staticmethod
    def texte_pdf(contenu: bytes):
        from pypdf import PdfReader
        lecteur = PdfReader(io.BytesIO(contenu))
        blocs = []
        for n, page in enumerate(lecteur.pages, 1):
            t = (page.extract_text() or "").strip()
            if t:
                blocs.append(f"[Page {n}]\n{t}")
        return "\n\n".join(blocs)

    # ---------- exploration ----------
    def collecter(self):
        self.lire_robots()
        file = [self.racine + "/"] + self.urls_sitemap()
        vus, pdfs = set(), []
        print(f"{len(file)} URL de départ")
        while file and len(self.pages) < self.max_pages:
            url = normaliser(file.pop(0))
            if url in vus or not meme_site(url, self.racine) or url.lower().endswith(EXT_IGNOREES):
                continue
            vus.add(url)
            if not self.autorise(url):
                print(f"  interdit par robots.txt : {url}")
                continue
            rep = self.get(url)
            if not rep:
                continue
            r, contenu = rep
            type_ = r.headers.get("Content-Type", "").lower()
            if "pdf" in type_ or url.lower().endswith(".pdf"):
                try:
                    texte = self.texte_pdf(contenu)
                except Exception as e:  # PDF scanné, protégé ou corrompu
                    print(f"  PDF illisible {url} : {e.__class__.__name__}")
                    continue
                if len(texte.split()) >= MIN_MOTS_PAGE:
                    self.pages[url] = (url.rsplit("/", 1)[-1], texte, True)
                    print(f"  [{len(self.pages)}] PDF {url}")
                continue
            if "html" not in type_:
                continue
            titre, lignes, liens = self.texte_html(contenu)
            for h in liens:
                suivant = normaliser(urljoin(url, h))
                if suivant.startswith("http") and suivant not in vus:
                    file.append(suivant)
            if sum(len(l.split()) for l in lignes) >= MIN_MOTS_PAGE:
                self.pages[url] = (titre, lignes, False)
                print(f"  [{len(self.pages)}] {url}")
        return self.pages

    # ---------- nettoyage + écriture ----------
    def ecrire(self):
        # lignes qui reviennent sur plus de 30 % des pages HTML : bandeaux, mentions légales...
        html = [p for p in self.pages.values() if not p[2]]
        compte = Counter(l for _, lignes, _ in html for l in set(lignes))
        limite = max(3, int(0.3 * len(html)))
        repetees = {l for l, n in compte.items() if n > limite}

        a_ecrire = {}
        for url, (titre, contenu, est_pdf) in self.pages.items():
            if est_pdf:
                corps = contenu
            else:
                lignes, vues = [], set()
                for l in contenu:
                    if l in repetees or l in vues:
                        continue
                    vues.add(l)
                    lignes.append(l if re.search(r"[.!?:;]$", l) or len(l.split()) > 12 else l + ".")
                corps = "\n\n".join(lignes)
            if len(corps.split()) >= MIN_MOTS_PAGE:
                a_ecrire[nom_fichier(url)] = f"URL: {url}\nTITRE: {titre}\n\n{corps}\n"

        print(f"\n{len(a_ecrire)} fichier(s) à écrire ({len(repetees)} ligne(s) répétée(s) retirée(s)) dans {self.sortie}")
        if self.dry_run:
            return
        self.sortie.mkdir(parents=True, exist_ok=True)
        for nom, txt in a_ecrire.items():
            (self.sortie / nom).write_text(txt, encoding="utf-8")
        for ancien in self.sortie.glob("*.txt"):          # page disparue du site
            if ancien.name not in a_ecrire:
                ancien.unlink()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="racine du site, ex. https://www.portdakar.sn")
    ap.add_argument("--max-pages", type=int, default=150)
    ap.add_argument("--delai", type=float, default=1.5, help="secondes entre deux requêtes")
    ap.add_argument("--sortie", default="docs/web")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--insecure", action="store_true", help="ne pas vérifier le certificat SSL (dernier recours)")
    a = ap.parse_args()
    c = Collecteur(a.url, a.delai, a.max_pages, a.sortie, a.dry_run, not a.insecure)
    if not c.collecter():
        sys.exit("Aucune page exploitable : rien n'a été modifié.")
    c.ecrire()


if __name__ == "__main__":
    main()
