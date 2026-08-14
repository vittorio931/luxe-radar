from playwright.sync_api import sync_playwright

URL = "https://www.vinted.fr/catalog?search_text=Stone%20Island&price_to=30&currency=EUR&page=1"

with sync_playwright() as p:

    browser = p.chromium.launch(headless=False)
    page = browser.new_page()

    print("🔎 Ouverture de Vinted...")

    page.goto(
        URL,
        wait_until="domcontentloaded",
        timeout=30000
    )

    page.wait_for_timeout(5000)

    liens = page.locator('a[href*="/items/"]')

    print(f"\n📦 Liens trouvés : {liens.count()}")

    if liens.count() > 0:

        premier = liens.nth(0)

        print("\n🔬 TEST DU PREMIER ARTICLE")

        print("\n--- TEXTE DU LIEN ---")
        print(premier.inner_text(timeout=3000))

        print("\n--- TEXTE DU PARENT ---")
        print(
            premier.locator("xpath=..").inner_text(
                timeout=3000
            )
        )

        print("\n--- TEXTE DU GRAND-PARENT ---")
        print(
            premier.locator("xpath=../..").inner_text(
                timeout=3000
            )
        )

    else:
        print("❌ Aucun lien d'annonce trouvé.")

    input("\nAppuie sur Entrée pour fermer...")

    browser.close()