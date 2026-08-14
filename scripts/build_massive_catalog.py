"""Construit le catalogue massif depuis un annuaire public puis vérifie les domaines."""

from __future__ import annotations

import concurrent.futures
import json
import re
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "marketplaces" / "sites.json"
SOURCE_API = "https://fromthelabels.com/api/brands"
SOURCE_PAGE = "https://fromthelabels.com/brands"
PAGE_SIZE = 100
MAX_WORKERS = 32
TIMEOUT = 8
USER_AGENT = "LUXE-RADAR-Catalog-Validator/1.0"
ALLOWED_CATEGORY_SLUGS = {"clothing-brands", "footwear-brands", "swimwear-brands"}
EXCLUDED_HOSTS = {
    "facebook.com", "instagram.com", "linkedin.com", "pinterest.com", "tiktok.com",
    "twitter.com", "x.com", "youtube.com", "fromthelabels.com",
}

COUNTRY_CODES = {
    "united states": "US", "usa": "US", "united kingdom": "GB", "uk": "GB",
    "england": "GB", "scotland": "GB", "wales": "GB", "australia": "AU",
    "canada": "CA", "france": "FR", "italy": "IT", "spain": "ES",
    "germany": "DE", "netherlands": "NL", "belgium": "BE", "portugal": "PT",
    "denmark": "DK", "sweden": "SE", "norway": "NO", "finland": "FI",
    "switzerland": "CH", "austria": "AT", "ireland": "IE", "india": "IN",
    "japan": "JP", "south korea": "KR", "korea": "KR", "china": "CN",
    "hong kong": "HK", "new zealand": "NZ", "brazil": "BR", "mexico": "MX",
    "south africa": "ZA", "singapore": "SG", "indonesia": "ID", "philippines": "PH",
    "turkey": "TR", "greece": "GR", "poland": "PL", "czech republic": "CZ",
    "romania": "RO", "ukraine": "UA", "israel": "IL", "united arab emirates": "AE",
}

CURRENCIES = {
    "US": "USD", "GB": "GBP", "AU": "AUD", "CA": "CAD", "FR": "EUR",
    "IT": "EUR", "ES": "EUR", "DE": "EUR", "NL": "EUR", "BE": "EUR",
    "PT": "EUR", "AT": "EUR", "IE": "EUR", "GR": "EUR", "FI": "EUR",
    "DK": "DKK", "SE": "SEK", "NO": "NOK", "CH": "CHF", "IN": "INR",
    "JP": "JPY", "KR": "KRW", "CN": "CNY", "HK": "HKD", "NZ": "NZD",
    "BR": "BRL", "MX": "MXN", "ZA": "ZAR", "SG": "SGD", "ID": "IDR",
    "PH": "PHP", "TR": "TRY", "PL": "PLN", "CZ": "CZK", "RO": "RON",
    "UA": "UAH", "IL": "ILS", "AE": "AED",
}


def canonical_url(value):
    value = str(value or "").strip()
    if not value:
        return None
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().removeprefix("www.").strip(".")
    if not host or "." not in host or host in EXCLUDED_HOSTS or any(host.endswith("." + x) for x in EXCLUDED_HOSTS):
        return None
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    return "https://" + host, host


def country_from_location(location):
    low = str(location or "").casefold()
    for label, code in sorted(COUNTRY_CODES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(r"\b" + re.escape(label) + r"\b", low):
            return code
    return ""


def fetch_candidates():
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    candidates = []
    offset = 0
    while True:
        response = session.get(SOURCE_API, params={"limit": PAGE_SIZE, "offset": offset}, timeout=30)
        response.raise_for_status()
        batch = response.json().get("data") or []
        if not batch:
            break
        candidates.extend(batch)
        offset += len(batch)
        print(f"SOURCE {offset}")
        if len(batch) < PAGE_SIZE:
            break
    return candidates


def verify(candidate):
    normalized = canonical_url(candidate.get("websiteUrl"))
    if not normalized:
        return None
    url, host = normalized
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        dns_ok = True
    except OSError:
        return None
    status_code = None
    final_url = url
    note = "Domaine DNS vérifié. Recherche produit non testée."
    status = "to_test"
    try:
        response = requests.get(
            url, timeout=TIMEOUT, allow_redirects=True, stream=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        )
        status_code = response.status_code
        final = canonical_url(response.url)
        if final:
            final_url, final_host = final
            if final_host != host:
                host = final_host
        sample = next(response.iter_content(8192), b"").lower()
        response.close()
        challenged = any(marker in sample for marker in (b"captcha", b"cf-chl", b"access denied"))
        if status_code in {401, 403, 429} or challenged:
            status = "blocked"
            note = f"Domaine vérifié ; accès HTTP {status_code or 'challenge'}. Aucun contournement tenté."
        elif 200 <= status_code < 400:
            status = "non_implemented"
            note = f"Domaine et page publique vérifiés (HTTP {status_code}). Recherche produit non implémentée."
        else:
            note = f"Domaine DNS vérifié ; HTTP {status_code}. Recherche produit à tester."
    except requests.RequestException as exc:
        note = f"Domaine DNS vérifié ; HTTP non concluant ({type(exc).__name__}). Recherche produit à tester."
    return candidate, host, final_url, status, status_code, note, dns_ok


def category_for(candidate):
    slug = str((candidate.get("category") or {}).get("slug") or "")
    return {
        "clothing-brands": "Vêtements / marques",
        "footwear-brands": "Chaussures / sneakers",
        "swimwear-brands": "Mode / swimwear",
    }.get(slug, "Mode / autres")


def make_site(result):
    candidate, host, url, status, status_code, note, _ = result
    country = country_from_location(candidate.get("location"))
    supports = False
    return {
        "name": str(candidate.get("name") or host).strip(),
        "url": url,
        "base_url": url,
        "domain": host,
        "category": category_for(candidate),
        "country": country,
        "currency": CURRENCIES.get(country, ""),
        "enabled": False,
        "status": status,
        "connector_type": "unimplemented",
        "supports_search": supports,
        "supports_price": supports,
        "supports_image": supports,
        "supports_reference": supports,
        "capabilities": {"search": supports, "price": supports, "image": supports, "reference": supports},
        "notes": note,
        "verification": {
            "source": SOURCE_PAGE,
            "source_verified": bool(candidate.get("verified")),
            "http_status": status_code,
        },
    }


def main():
    old = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    protected = [
        site for site in old.get("sites", [])
        if site.get("enabled") or site.get("connector_type") == "dedicated"
    ]
    raw = fetch_candidates()
    relevant = [item for item in raw if str((item.get("category") or {}).get("slug")) in ALLOWED_CATEGORY_SLUGS]
    print("RELEVANT", len(relevant))
    verified = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for index, result in enumerate(pool.map(verify, relevant), 1):
            if result:
                verified.append(result)
            if index % 100 == 0:
                print(f"VERIFIED_PROGRESS {index}/{len(relevant)} KEPT {len(verified)}")

    by_domain = {}
    for result in verified:
        site = make_site(result)
        by_domain.setdefault(site["domain"], site)
    for site in protected:
        normalized = canonical_url(site.get("url") or site.get("base_url"))
        if not normalized:
            continue
        url, host = normalized
        protected_site = dict(site)
        protected_site.update({"url": url, "base_url": url, "domain": host})
        protected_site.setdefault("notes", "Connecteur dédié existant conservé.")
        protected_site.setdefault("country", "")
        protected_site.setdefault("currency", "")
        if site.get("enabled"):
            protected_site.update({
                "enabled": True, "status": "active", "supports_search": True,
                "supports_price": True, "supports_image": True,
                "supports_reference": bool((site.get("capabilities") or {}).get("reference")),
            })
        by_domain[host] = protected_site

    sites = sorted(by_domain.values(), key=lambda site: (not site.get("enabled"), site["category"], site["name"].casefold()))
    if len(sites) < 1000:
        raise SystemExit(f"Catalogue insuffisant après vérification : {len(sites)}")
    output = {
        "version": 3,
        "managed_by": "LUXE RADAR MANAGER",
        "generated_from": SOURCE_PAGE,
        "generation_policy": "Domaines officiels issus d'un annuaire public mode, dédupliqués et vérifiés DNS/HTTP. Aucun site importé n'est activé.",
        "sites": sites,
    }
    CATALOG_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = {}
    for site in sites:
        counts[site["status"]] = counts.get(site["status"], 0) + 1
    print("TOTAL_SITES", len(sites))
    for status, count in sorted(counts.items()):
        print(status.upper(), count)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
