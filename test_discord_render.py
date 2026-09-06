"""Contrat hors réseau de l'entrée Render du bot Discord."""

import os
from unittest.mock import patch

import discord_render


def main():
    with patch.dict(os.environ, {"PORT": "0"}):
        server = discord_render.start_health_server()
        try:
            assert server.server_address[1] > 0
        finally:
            server.shutdown()
            server.server_close()
    print("[OK] Endpoint de santé Render du bot Discord validé")


if __name__ == "__main__":
    main()
