import customtkinter as ctk

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

MARQUES = {
    "Nike": 20,
    "Adidas": 20,
    "Jordan": 30,
    "New Balance": 25,
    "Asics": 25,
    "Salomon": 30,
    "On": 30,
    "Under Armour": 15,
    "Essentials": 50,
    "Ralph Lauren": 20,
    "Lacoste": 20,
    "Fred Perry": 20,
    "Tommy Hilfiger": 20,
    "Polo Sport": 20,
    "Carhartt WIP": 20,
    "Patagonia": 25,
    "The North Face": 25,
    "Arc'teryx": 35,
    "Stone Island": 30,
    "C.P. Company": 30,
    "Stone Island Shadow Project": 40,
    "Diesel": 25,
    "Moncler": 40,
    "Canada Goose": 40,
    "Burberry": 40,
    "Palm Angels": 40,
    "Ami Paris": 40,
    "Jacquemus": 40,
    "Acne Studios": 40,
    "Off-White": 40,
    "Gucci": 50,
    "Prada": 50,
    "Balenciaga": 50,
    "Saint Laurent": 50,
    "Dior": 50,
    "Givenchy": 50,
    "Fendi": 50,
    "Valentino": 50,
    "Maison Margiela": 50,
    "Amiri": 50,
}

app = ctk.CTk()
app.title("LUXE RADAR")
app.geometry("900x650")

titre = ctk.CTkLabel(
    app,
    text="🛍️ LUXE RADAR",
    font=ctk.CTkFont(size=30, weight="bold")
)
titre.pack(pady=(25, 5))

sous_titre = ctk.CTkLabel(
    app,
    text="Radar de bonnes affaires Vinted",
    font=ctk.CTkFont(size=15)
)
sous_titre.pack(pady=(0, 20))

conteneur = ctk.CTkFrame(app)
conteneur.pack(fill="both", expand=True, padx=25, pady=10)

gauche = ctk.CTkFrame(conteneur)
gauche.pack(side="left", fill="both", expand=True, padx=(10, 5), pady=10)

ctk.CTkLabel(
    gauche,
    text="🏷️ Marques surveillées",
    font=ctk.CTkFont(size=18, weight="bold")
).pack(pady=15)

scroll = ctk.CTkScrollableFrame(gauche, width=350, height=400)
scroll.pack(fill="both", expand=True, padx=10, pady=10)

cases = {}

for marque, prix_max in MARQUES.items():
    variable = ctk.BooleanVar(value=True)

    case = ctk.CTkCheckBox(
        scroll,
        text=f"{marque}  ≤ {prix_max} €",
        variable=variable
    )

    case.pack(anchor="w", pady=5, padx=10)
    cases[marque] = variable

droite = ctk.CTkFrame(conteneur)
droite.pack(side="right", fill="both", expand=True, padx=(5, 10), pady=10)

ctk.CTkLabel(
    droite,
    text="⚙️ Configuration",
    font=ctk.CTkFont(size=18, weight="bold")
).pack(pady=15)

ctk.CTkLabel(
    droite,
    text="Prix maximum personnalisé"
).pack(pady=(20, 5))

prix = ctk.CTkEntry(
    droite,
    placeholder_text="Ex : 30"
)
prix.pack(padx=30, fill="x")

resultat = ctk.CTkTextbox(droite, height=220)
resultat.pack(fill="both", expand=True, padx=20, pady=25)


def lancer_radar():
    resultat.delete("1.0", "end")

    selection = [
        marque
        for marque, variable in cases.items()
        if variable.get()
    ]

    resultat.insert(
        "end",
        "🔎 RADAR PRÊT\n\n"
        f"Marques sélectionnées : {len(selection)}\n\n"
    )

    for marque in selection:
        resultat.insert(
            "end",
            f"• {marque} ≤ {MARQUES[marque]} €\n"
        )


bouton = ctk.CTkButton(
    droite,
    text="🔎 LANCER LE RADAR",
    height=45,
    font=ctk.CTkFont(size=16, weight="bold"),
    command=lancer_radar
)
bouton.pack(fill="x", padx=30, pady=(0, 20))

app.mainloop()