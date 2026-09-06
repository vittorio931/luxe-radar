"""Contrat statique de l’organisation Discord."""

from discord_server_setup import LUXURY_BRANDS, LUXURY_STRUCTURE, NIKE_RUNNING, SPORT_STRUCTURE, STARTER_MESSAGES, STRUCTURE, validate_structure
from discord_server_cleanup import KEEP_RADARS


def main():
    categories, channels = validate_structure()
    assert categories == 12 and channels == 74
    names = {name for _, items in STRUCTURE for name, _ in items}
    assert {"bienvenue", "reglement", "commandes-bot", "alertes-vinted", "suggestions"} <= names
    assert set(STARTER_MESSAGES) <= names
    assert {"vinted-cpu-amd", "vinted-cpu-intel", "ebay-cpu-amd", "ebay-cpu-intel", "guide-alertes"} <= names
    assert len(LUXURY_BRANDS) == 23 and len(LUXURY_STRUCTURE[0][1]) == 23
    assert {"luxe-louis-vuitton", "luxe-gucci", "luxe-dior", "luxe-moncler"} <= names
    assert len(NIKE_RUNNING) == 15 and len(SPORT_STRUCTURE) == 2
    assert {"outdoor-columbia", "nike-running-vaporfly", "nike-running-phenom-elite"} <= names
    assert len(KEEP_RADARS) == 12
    assert KEEP_RADARS <= names
    print("[OK] Structure Discord validée : 12 catégories, 74 salons dont Columbia et 15 Radars Nike Running")


if __name__ == "__main__":
    main()
