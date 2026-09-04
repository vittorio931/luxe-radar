import json
from unittest.mock import patch

from scripts import audit_public_search


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class FakeOpener:
    def open(self, url, timeout=120):
        if "/search?" in url:
            return FakeResponse(b'<script>{"token":"0123456789abcdef0123456789abcdef"}</script>')
        payload = {
            "total": 12,
            "pending": False,
            "source_counts": {"eBay": 10, "Vinted": 2, "ASOS": 0},
            "completed_sources": ["eBay", "Vinted"],
            "failed_sources": [],
            "skipped_sources": ["Grailed"],
        }
        return FakeResponse(json.dumps(payload).encode())


def main():
    with patch("urllib.request.build_opener", return_value=FakeOpener()):
        row = audit_public_search.audit("https://example.test", "Fourteen", 1)
    assert row["total"] == 12
    assert row["sources_with_results"] == 2
    assert row["source_counts"] == {"eBay": 10, "Vinted": 2}
    assert row["skipped"] == ["Grailed"]
    print("OK - audit public mesure délai, volume et sources réelles")


if __name__ == "__main__":
    main()
