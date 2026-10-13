#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Inspecte le HTML récupéré par Playwright pour voir si le titre et l'abstract
sont réellement présents (et pas juste une page de challenge).
Usage : python inspect_html.py "https://ieeexplore.ieee.org/document/9374668/"
"""

import os
import sys
import re
from pathlib import Path

# Forcer Playwright
os.environ["ENNOSMART_USE_CLOUDSCRAPER"] = "0"
os.environ["ENNOSMART_USE_PLAYWRIGHT"] = "1"
os.environ["ENNOSMART_HTTP_TIMEOUT"] = "90"

sys.path.insert(0, str(Path(__file__).parent))

from services.http_client import GLOBAL_FETCHER

# Tentative d'import de BeautifulSoup pour une meilleure analyse
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    print("⚠️  BeautifulSoup non installé. Installe-le avec : pip install beautifulsoup4")
    print("   L'analyse se fera en mode texte brut (moins précis).\n")

def extract_title_abstract(html: str) -> tuple:
    """Extrait le titre et l'abstract du HTML."""
    title = None
    abstract = None

    if HAS_BS4:
        soup = BeautifulSoup(html, "html.parser")
        # Titre
        if soup.title:
            title = soup.title.string.strip() if soup.title.string else None

        # Abstract : on cherche dans les balises meta
        for meta in soup.find_all("meta"):
            name = meta.get("name", "").lower()
            if name in {"citation_abstract", "description", "dc.description", "og:description"}:
                content = meta.get("content")
                if content and len(content) > 50:
                    abstract = content.strip()
                    break

        # Si pas trouvé via meta, on cherche une div avec classe "abstract"
        if not abstract:
            for tag in soup.find_all(["div", "section", "p"]):
                classes = " ".join(tag.get("class", []))
                if "abstract" in classes.lower():
                    text = tag.get_text(" ", strip=True)
                    if len(text) > 100:
                        abstract = text
                        break

        # Si toujours rien, on cherche un paragraphe contenant "Abstract" ou "Résumé"
        if not abstract:
            for tag in soup.find_all("p"):
                text = tag.get_text(" ", strip=True)
                if re.search(r"\b(abstract|résumé|resume)\b", text, re.I) and len(text) > 100:
                    abstract = text
                    break

    else:
        # Mode fallback sans BeautifulSoup
        title_match = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
        if title_match:
            title = title_match.group(1).strip()

        abstract_match = re.search(
            r'<meta\s+name=["\'](?:citation_abstract|description|dc\.description)["\']\s+content=["\'](.*?)["\']',
            html,
            re.I | re.S,
        )
        if abstract_match:
            abstract = abstract_match.group(1).strip()

        if not abstract:
            # Cherche un bloc de texte contenant "abstract"
            parts = re.split(r"<p>|</p>|<div>|</div>", html)
            for part in parts:
                if re.search(r"\babstract\b", part, re.I) and len(part) > 100:
                    abstract = re.sub(r"<[^>]+>", "", part).strip()
                    break

    return title, abstract

def inspect_url(url: str):
    print(f"\n{'='*70}")
    print(f"🔍 Inspection de : {url}")
    print('-' * 70)

    ok, info, content = GLOBAL_FETCHER.fetch_bytes(
        url=url,
        headers={"Accept": "text/html,application/xhtml+xml"},
        max_bytes=0,  # pas de limite
        referer="https://www.google.com/",
    )

    if not ok:
        print(f"❌ Échec de la requête : {info.get('reason')}")
        return

    print(f"✅ Status : {info.get('status')} | HTTP {info.get('http_status')}")
    print(f"📦 Taille : {info.get('content_bytes', 0):,} octets")
    print(f"🔗 Final URL : {info.get('final_url')}")

    html = content.decode("utf-8", errors="ignore")

    # 1) Vérifier si la page est un PDF
    if html[:5] == "%PDF-":
        print("📄 C'est un PDF direct ! (extraction directe possible)")
        return

    # 2) Détection de challenge (rapide)
    low = html.lower()
    has_challenge = any(k in low for k in ["challenge", "captcha", "aws waf", "just a moment", "checking your browser"])
    print(f"⚠️  Page de challenge détectée : {has_challenge}")

    # 3) Extraction du titre et de l'abstract
    title, abstract = extract_title_abstract(html)

    if title:
        print(f"\n📌 Titre : {title[:200]}")
    else:
        print("\n📌 Titre : NON TROUVÉ")

    if abstract:
        print(f"\n📝 Abstract : {abstract[:400]}...")
        print(f"   (longueur : {len(abstract)} caractères)")
    else:
        print("\n📝 Abstract : NON TROUVÉ")

    # 4) Indicateurs de contenu
    indicators = {
        "article": "article" in low,
        "abstract": "abstract" in low or "résumé" in low,
        "keywords": "keywords" in low or "mots-clés" in low,
        "references": "references" in low or "bibliography" in low,
        "doi": "doi.org" in low or "10." in low and "doi" in low,
    }
    print("\n🔎 Indicateurs de contenu scientifique :")
    for key, present in indicators.items():
        print(f"   - {key}: {'✅' if present else '❌'}")

    # 5) Afficher les 500 premiers caractères significatifs (sans balises)
    clean_text = re.sub(r"<[^>]+>", " ", html)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    print(f"\n📄 Extrait texte (300 premiers caractères) :\n{clean_text[:300]}...")

def main():
    import sys
    if len(sys.argv) < 2:
        print("Usage : python inspect_html.py <URL>")
        print("Exemple : python inspect_html.py https://ieeexplore.ieee.org/document/9374668/")
        sys.exit(1)

    url = sys.argv[1]
    inspect_url(url)
    GLOBAL_FETCHER.close()

if __name__ == "__main__":
    main()