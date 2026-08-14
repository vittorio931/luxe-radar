"""Probe conforme et borné du premier lot prioritaire de sites."""

import json
import re
from pathlib import Path

import requests


TARGETS = {
    "DHgate": "https://www.dhgate.com/wholesale/search.do?searchkey=Nike%20Trail",
    "AliExpress": "https://www.aliexpress.com/w/wholesale-nike-trail.html",
    "Alibaba": "https://www.alibaba.com/trade/search?SearchText=Nike+Trail",
    "GOAT": "https://www.goat.com/search?query=Nike%20Trail",
    "StockX": "https://stockx.com/search?s=nike%20trail",
    "Depop": "https://www.depop.com/search/?q=nike%20trail",
    "ASOS": "https://www.asos.com/search/?q=nike%20trail",
    "Zalando": "https://www.zalando.fr/catalogue/?q=nike%20trail",
    "Farfetch": "https://www.farfetch.com/fr/shopping/men/search/items.aspx?q=nike%20trail",
    "SSENSE": "https://www.ssense.com/en-fr/men?q=nike%20trail",
}


def main():
    report = []
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
    })
    for name, url in TARGETS.items():
        row = {"name": name, "url": url}
        try:
            response = session.get(url, timeout=25, allow_redirects=True)
            text = response.text
            low = text.casefold()
            row.update({
                "status_code": response.status_code,
                "final_url": response.url,
                "bytes": len(response.content),
                "query_visible": "nike" in low and "trail" in low,
                "prices": len(re.findall(r"(?:€|£|\$|eur|usd|gbp)\s*\d|\d[\d., ]*\s*(?:€|£|\$|eur|usd|gbp)", low)),
                "product_links": len(set(re.findall(r'href=["\']([^"\']*(?:product|products|item|items|prd)/[^"\']+)', text, re.I))),
                "blocked": response.status_code in {401, 403, 429} or any(
                    marker in low for marker in ("captcha", "cf-chl", "access denied", "verify you are human")
                ),
            })
        except requests.RequestException as exc:
            row.update({"error": type(exc).__name__, "blocked": False})
        report.append(row)
        print(json.dumps(row, ensure_ascii=False))
    output = Path(__file__).resolve().parents[1] / ".luxe_radar" / "priority_batch_probe.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
