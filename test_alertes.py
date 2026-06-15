import app

app.alertes.extend(app.charger_alertes())

app.ajouter_alerte(
    "Test",
    "Titre de test CRITIQUE",
    {"accroche": "Ceci est un test.", "contexte": "Verification du systeme.", "suite": "Rien a surveiller."},
    "https://example.com",
    "description test",
    3
)
print("Alerte critique ajoutee")

app.ajouter_alerte(
    "Test",
    "Titre de test IMPORTANT",
    {"accroche": "Evenement notable.", "contexte": "Contexte.", "suite": "Suite."},
    "https://example.com",
    "description test",
    2
)
print("Alerte importante ajoutee")
