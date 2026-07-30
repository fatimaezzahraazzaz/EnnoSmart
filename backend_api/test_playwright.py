import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
# Force l'utilisation de Playwright
os.environ["ENNOSMART_USE_PLAYWRIGHT"] = "1"
os.environ["ENNOSMART_USE_CLOUDSCRAPER"] = "0"

from services.http_client import GLOBAL_FETCHER

url = "https://doi.org/10.1109/maes.2021.3049857"
print(f"Test avec Playwright sur : {url}")
ok, info, content = GLOBAL_FETCHER.fetch_bytes(url, max_bytes=0)
print(f"ok={ok}, status={info.get('status')}")
if ok and content:
    print(f"Taille : {len(content)} octets")
    try:
        preview = content[:500].decode("utf-8", errors="ignore")
        print("Extrait :", preview[:200])
    except:
        pass
GLOBAL_FETCHER.close()