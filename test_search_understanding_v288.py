from search_understanding import understand_query, suggest_queries


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    u = understand_query("pantalon hunder armour hybryd")
    check("Under Armour" in u.canonical, u)
    check("Hybrid" in u.canonical, u)
    check(u.brand == "Under Armour" and u.model == "Hybrid", u)

    s = understand_query("sweet stone iland")
    check("Stone Island" in s.canonical, s)
    check("sweat" in s.canonical.casefold(), s)

    m = understand_query("nike miller")
    check("Nike" in m.canonical and "Miler" in m.canonical, m)

    e = understand_query("essantials ensemble")
    check("Essentials" in e.canonical, e)

    # Ne pas corriger "sweet" sans contexte de marque mode connue.
    safe = understand_query("Sweet Protection helmet")
    check("sweat protection" not in safe.canonical.casefold(), safe)

    ref = understand_query("DM4652-040")
    check(ref.canonical == "DM4652-040" and not ref.corrected, ref)

    nike = [x["value"] for x in suggest_queries("nike phe", 8)]
    check(any("Nike Phenom Elite" == x for x in nike), nike)

    ua = [x["value"] for x in suggest_queries("under armour hy", 8)]
    check(any("Under Armour Hybrid" == x for x in ua), ua)

    stone = [x["value"] for x in suggest_queries("stone isl swe", 8)]
    check(any("Stone Island Sweat" == x for x in stone), stone)

    print("V2.8.8 search understanding: OK")


if __name__ == "__main__":
    main()
