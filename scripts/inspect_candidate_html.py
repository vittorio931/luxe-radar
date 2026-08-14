"""Inspecte les structures publiques des trois candidats HTTP exploitables."""

import re

import requests

from probe_priority_batch import TARGETS


def main():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
    })
    for name in ("AliExpress", "Depop", "SSENSE"):
        text = session.get(TARGETS[name], timeout=30).text
        print("\n===", name, len(text), "===")
        patterns = [
            r'href=["\']([^"\']*(?:product|products|item|items|prd)/[^"\']+)',
            r'"(?:title|name|productName)"\s*:\s*"([^"\\]{5,180})"',
            r'"(?:price|salePrice|currentPrice)"\s*:\s*(?:"([^"\\]+)"|([0-9.]+))',
            r'"(?:image|imageUrl|mainImage)"\s*:\s*"(https?[^"\\]+)',
        ]
        for pattern in patterns:
            matches = []
            for match in re.findall(pattern, text, re.I):
                value = " | ".join(x for x in match if x) if isinstance(match, tuple) else match
                value = value.replace("\\u002F", "/").replace("\\/", "/")
                if value not in matches:
                    matches.append(value)
            print("PATTERN", pattern[:35], "COUNT", len(matches))
            print(*matches[:12], sep="\n")


if __name__ == "__main__":
    main()
