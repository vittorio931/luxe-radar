
from flask import Flask, render_template, request
from radar_engine import rechercher_vinted
app = Flask(__name__)

MARQUES = [
    ("Nike", 20),
    ("Adidas", 20),
    ("Jordan", 30),
    ("New Balance", 25),
    ("Asics", 25),
    ("Salomon", 30),
    ("On", 30),
    ("Under Armour", 15),
    ("Ralph Lauren", 20),
    ("Lacoste", 20),
    ("Fred Perry", 20),
    ("Tommy Hilfiger", 20),
    ("Carhartt WIP", 20),
    ("Patagonia", 25),
    ("The North Face", 25),
    ("Arc'teryx", 35),
    ("Stone Island", 30),
    ("C.P. Company", 30),
    ("Diesel", 25),
    ("Moncler", 40),
    ("Canada Goose", 40),
    ("Burberry", 40),
    ("Palm Angels", 40),
    ("Ami Paris", 40),
    ("Jacquemus", 40),
    ("Acne Studios", 40),
    ("Off-White", 40),
    ("Gucci", 50),
    ("Prada", 50),
    ("Balenciaga", 50),
    ("Saint Laurent", 50),
    ("Dior", 50),
    ("Givenchy", 50),
    ("Fendi", 50),
    ("Valentino", 50),
    ("Maison Margiela", 50),
    ("Amiri", 50),
]


@app.route("/", methods=["GET", "POST"])
def accueil():
    annonces = []
    recherche = None
    erreur = None

    if request.method == "POST":
        recherche = request.form.get("marque", "").strip()
        prix = request.form.get("prix", "").strip()

        if recherche and prix:
            try:
                prix = float(prix)

                if prix <= 0:
                    erreur = "Le prix doit être supérieur à 0."
                else:
                    annonces = rechercher_vinted(
    recherche,
    prix,
    limite=10,
)

            except ValueError:
                erreur = "Prix invalide."

            except Exception as e:
                erreur = f"Erreur : {e}"

        else:
            erreur = "Indique une marque et un prix maximum."

    return render_template(
        "index.html",
        marques=MARQUES,
        annonces=annonces,
        recherche=recherche,
        erreur=erreur,
    )


if __name__ == "__main__":
    app.run(
        debug=False,
        use_reloader=False,
        host="127.0.0.1",
        port=5000,
    )

