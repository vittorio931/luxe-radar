from product_recognition import recognize


def check(query, title, marketplace, accepted, level=None):
    r = recognize(title, query, marketplace=marketplace)
    assert r.accepted is accepted, (query, title, marketplace, r)
    if level is not None:
        assert r.level == level, (query, title, marketplace, r)
    return r


def main():
    # Libellé exact "ensemble" : doit passer.
    check("ensemble Essentials", "Ensemble Essentials gris Taille M Neuf avec étiquette", "eBay", True, "fort")
    # Mais un coffret/disque Essentials ne doit pas être pris pour un vêtement.
    check("ensemble Essentials", "YOUN SUN NAH Essentials 3xCD BOX Set", "eBay", False, "rejet")
    # Une marketplace grossiste ne suffit pas à prouver la marque si le titre est générique.
    check("ensemble Essentials", "Mens casual hoodie pants tracksuit set", "DHgate", False, "rejet")
    check("ensemble Essentials", "ESSENTIALS hoodie pants tracksuit", "DHgate", True, "fort")
    # Un modèle demandé est obligatoire.
    check("pantalon Under Armour Hybrid", "Under Armour Challenger joggers black", "ASOS", False, "rejet")
    check("pantalon Under Armour Hybrid", "Under Armour Hybrid pants black", "ASOS", True, "fort")
    # Même logique pour Stone Island / Nike Trail sur titres de grossistes.
    check("Stone Island sweat", "Mens casual hooded sweatshirt", "DHgate", False, "rejet")
    check("Stone Island sweat", "Stone Island hooded sweatshirt", "DHgate", True, "fort")
    check("Nike Trail", "Trail running shoes breathable mesh", "DHgate", False, "rejet")
    check("Nike Trail", "Nike Trail running shoes breathable mesh", "DHgate", True, "fort")
    # Accessoire incompatible : ne doit plus passer grâce à marque + descripteur.
    check("Pantalon Nike Trail", "Nike Trail socks", "eBay", False, "rejet")
    print("OK - V2.8.7 reconnaissance stricte et anti-faux-positifs validée.")


if __name__ == "__main__":
    main()
