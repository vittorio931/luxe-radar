"""Contrats de persistance et de détection du worker Vinted."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
from unittest.mock import patch
from threading import Event

import vinted_watch


def main():
    offer = {
        "marketplace": "Vinted", "titre": "Polo Ralph Lauren bleu",
        "prix": 18, "devise": "EUR",
        "marque": "Ralph Lauren", "taille": "M", "etat": "Très bon état",
        "score": 94, "image": "https://images.example.test/polo.jpg",
        "lien": "https://www.vinted.fr/items/123-polo-ralph-lauren",
    }
    with TemporaryDirectory() as folder:
        path = Path(folder) / "watch.sqlite3"
        uid = "test-user"
        watch = vinted_watch.save_watch(uid, {
            "query": "Polo Ralph Lauren", "price_min": 5,
            "price_max": 50, "interval": 1,
        }, path)
        assert watch["active"] and watch["interval"] == 30
        notified = []
        notification_ready = Event()
        def notifier(owner, detected_offer, query):
            notified.append((owner, detected_offer, query))
            notification_ready.set()
        worker = vinted_watch.WatchWorker(lambda criteria: [offer], path=path, notifier=notifier)
        worker.run_due()
        assert notification_ready.wait(2)
        assert notified[0][0] == uid and notified[0][1]["titre"] == offer["titre"]
        assert notified[0][2] == "Polo Ralph Lauren"
        alerts = vinted_watch.alerts(uid, path=path)
        assert len(alerts) == 1 and alerts[0]["titre"] == offer["titre"] and alerts[0]["unread"]
        # Le même article ne crée jamais une seconde alerte.
        with vinted_watch._LOCK, vinted_watch._connect(path) as db:
            db.execute("UPDATE vinted_watches SET next_run=0")
            db.commit()
        worker.run_due()
        assert len(vinted_watch.alerts(uid, path=path)) == 1
        assert vinted_watch.alerts(uid, mark_read=True, path=path)[0]["unread"]
        assert not vinted_watch.alerts(uid, path=path)[0]["unread"]
        assert vinted_watch.set_active(uid, False, path)
        assert vinted_watch.get_watch(uid, path)["active"] is False
        captured = {}
        class Response:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *args): return False
        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["body"] = request.data.decode("utf-8")
            return Response()
        with patch.dict(os.environ, {"LUXE_RADAR_DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test/token"}), \
             patch.object(vinted_watch.urllib.request, "urlopen", side_effect=fake_urlopen):
            assert vinted_watch.send_discord_offer(offer, "Polo Ralph Lauren")
        assert "with_components=true" in captured["url"]
        payload = json.loads(captured["body"])
        embed = payload["embeds"][0]
        assert payload["components"][0]["components"][0]["label"] == "⚡ Acheter sur Vinted"
        assert payload["components"][0]["components"][0]["style"] == 5
        assert embed["thumbnail"]["url"] == offer["image"]
        assert {field["name"] for field in embed["fields"]} >= {"💶 Prix", "🏷️ Marque", "📏 Taille", "✨ État", "🔎 Recherche", "🎯 Score"}
    print("[OK] Surveillance Vinted persistante, dédoublonnée et alertes validées")


if __name__ == "__main__":
    main()
