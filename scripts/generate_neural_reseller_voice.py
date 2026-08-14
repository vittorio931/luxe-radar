import asyncio
from pathlib import Path

import edge_tts

ROOT = Path(__file__).resolve().parents[1]
TEXT = """Quand on fait de la revente, le plus long, ce n'est pas toujours de vendre. C'est de trouver la bonne pièce, au bon prix, avant les autres.

LUXE RADAR réunit les annonces de Vinted, eBay, Grailed et soixante-sept behaviour dans une seule recherche. Tu indiques un article, ton budget, et le Radar classe les résultats les plus pertinents en premier, puis charge la suite automatiquement.

Tu peux mettre les meilleures annonces en favoris, créer des alertes, comparer plusieurs opportunités et suivre les variations de prix.

Dans le Studio, calcule ta marge après les frais, vérifie ton budget et utilise la checklist avant achat. Dans le Portfolio, enregistre ton stock, ton prix d'achat, ton prix de vente et ton bénéfice réel.

Moins de temps à ouvrir dix onglets. Plus de temps pour analyser, négocier et vendre.

Commence gratuitement avec LUXE RADAR. Trouve avant les autres."""


async def main():
    output = ROOT / "media" / "luxe_radar_reseller_voix_neurale.mp3"
    communicate = edge_tts.Communicate(
        TEXT,
        voice="fr-FR-RemyMultilingualNeural",
        rate="-7%",
        pitch="-2Hz",
        volume="+0%",
    )
    await communicate.save(str(output))
    print(output)


if __name__ == "__main__":
    asyncio.run(main())
