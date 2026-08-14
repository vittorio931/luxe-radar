from flask import Flask, render_template, request
from radar_engine import rechercher_vinted

app = Flask(__name__)


@app.route("/", methods=["GET", "POST"])
def accueil():

    annonces = []
    recherche = ""
    erreur = ""

    if request.method == "POST":

        marque = request.form.get(
            "marque",
            ""
        ).strip()

        prix = request.form.get(
            "prix",
            ""
        ).strip()

        if not marque:
            erreur = "Entre une marque."

        elif not prix:
            erreur = "Entre un prix maximum."

        else:

            try:

                prix_max = float(prix)

                recherche = (
                    f"{marque} ≤ "
                    f"{prix_max:.2f} €"
                )

                annonces = rechercher_vinted(
                    marque,
                    prix_max,
                    limite=10
                )

            except ValueError:

                erreur = (
                    "Le prix doit être un nombre."
                )

            except Exception as e:

                erreur = (
                    f"Erreur : {e}"
                )

    marques = [
        "Stone Island",
        "Nike",
        "Adidas",
        "Puma",
        "New Balance"
    ]

    return render_template(
        "index.html",
        annonces=annonces,
        recherche=recherche,
        erreur=erreur,
        marques=marques
    )


if __name__ == "__main__":

    app.run(
        debug=False,
        host="127.0.0.1",
        port=5000
    )
