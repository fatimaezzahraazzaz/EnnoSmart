#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Test du HTTPFetcher avec Playwright forcé.
Usage : python test_fetcher.py --url "https://doi.org/10.1109/maes.2021.3049857"
"""

import os
import sys
import argparse
from pathlib import Path

# FORCER PLAYWRIGHT (ignore .env)
os.environ["ENNOSMART_USE_CLOUDSCRAPER"] = "0"
os.environ["ENNOSMART_USE_PLAYWRIGHT"] = "1"
os.environ["ENNOSMART_HTTP_TIMEOUT"] = "90"

# Ajoute le chemin du projet pour importer les modules
sys.path.insert(0, str(Path(__file__).parent))

# Vérification que undetected-chromedriver est présent
try:
    import undetected_chromedriver as uc
    print("✅ undetected-chromedriver trouvé.")
except ImportError as e:
    print("❌ undetected-chromedriver manquant. Installe : pip install undetected-chromedriver")
    sys.exit(1)

# Import du fetcher centralisé
from services.http_client import GLOBAL_FETCHER

def main():
    parser = argparse.ArgumentParser(description="Test du HTTPFetcher sur une URL (Playwright forcé)")
    parser.add_argument("--url", required=True, help="URL à tester")
    parser.add_argument("--max-bytes", type=int, default=0,
                        help="Limite de téléchargement en octets (0 = illimité)")
    args = parser.parse_args()

    url = args.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    print(f"🔍 Test du fetcher sur : {url}")
    print(f"   Mode cloudscraper : {os.getenv('ENNOSMART_USE_CLOUDSCRAPER')}")
    print(f"   Mode playwright   : {os.getenv('ENNOSMART_USE_PLAYWRIGHT')}")
    print(f"   Timeout           : {os.getenv('ENNOSMART_HTTP_TIMEOUT')}s")
    print("-" * 60)

    ok, info, content = GLOBAL_FETCHER.fetch_bytes(
        url=url,
        headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        max_bytes=args.max_bytes or 0,
        referer="https://www.google.com/",
    )

    print("📦 Résultat de fetch_bytes :")
    print(f"   ok        : {ok}")
    print(f"   status    : {info.get('status')}")
    print(f"   final_url : {info.get('final_url', 'N/A')}")
    print(f"   http_status: {info.get('http_status', 'N/A')}")
    print(f"   content_type: {info.get('content_type', 'N/A')}")
    print(f"   bytes_downloaded: {info.get('content_bytes', 0)}")
    print(f"   elapsed   : {info.get('elapsed_seconds', 0)}s")
    if info.get("reason"):
        print(f"   reason    : {info.get('reason')}")

    # Si c'est un PDF, on signale
    if content and content[:5] == b"%PDF-":
        print("\n✅ Le contenu commence par %PDF- → c'est un PDF valide.")
    else:
        # Afficher un extrait du début en texte
        try:
            preview = content[:600].decode("utf-8", errors="ignore") if content else ""
            print("\n📄 Début du contenu (HTML/texte) :")
            print(preview[:400] + ("..." if len(preview) > 400 else ""))
        except Exception:
            pass

    # Test fetch_text pour extraire le titre
    if "text/html" in info.get("content_type", "").lower():
        ok2, info2, html = GLOBAL_FETCHER.fetch_text(
            url=url,
            headers={"Accept": "text/html,application/xhtml+xml"},
            max_chars=8000,
        )
        if ok2:
            import re
            title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            if title_match:
                print(f"\n   📄 Titre de la page : {title_match.group(1).strip()}")
            # Vérifier la présence de "captcha" ou "challenge"
            if "challenge" in html.lower() or "captcha" in html.lower():
                print("   ⚠️  La page contient un défi CAPTCHA ou Cloudflare.")
            else:
                print("   ✅ La page semble contenir du contenu (pas de challenge visible).")

    print("\n✅ Test terminé.")

    # Fermeture explicite du driver
    GLOBAL_FETCHER.close()

if __name__ == "__main__":
    main()