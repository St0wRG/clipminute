"""Abonnements CLIPFORGE : niveaux Free / Pro / Studio et fonctions débloquées.

Source de vérité des offres et du verrouillage. Le paiement (Stripe) se branchera plus tard :
ici on gère seulement le NIVEAU d'un compte et ce qu'il autorise. `limite() == None` = illimité.
"""

TIERS = {
    "free": {
        "nom": "Free", "prix": 0, "prix_label": "0 €", "accent": "#9aa0b5",
        "accroche": "Pour tester et voir la magie opérer.",
        "cta": "Commencer gratuitement",
        "limites": {"clips_mois": 3, "profils": 1},
        "features": {
            "sans_filigrane": False, "series": False, "calendrier": False,
            "tendances": False, "publication": False, "archives_reelles": False,
            "youtube": False, "auto_replan": False, "rapports": False,
            "multi_plateformes": False, "priorite": False,
        },
    },
    "pro": {
        "nom": "Pro", "prix": 19, "prix_label": "19 €", "accent": "#25f4ee",
        "accroche": "Pour publier sérieusement et faire grossir ton compte.",
        "cta": "Passer à Pro", "populaire": True,
        "limites": {"clips_mois": 100, "profils": 3},
        "features": {
            "sans_filigrane": True, "series": True, "calendrier": True,
            "tendances": True, "publication": True, "archives_reelles": True,
            "youtube": False, "auto_replan": False, "rapports": False,
            "multi_plateformes": False, "priorite": False,
        },
    },
    "studio": {
        "nom": "Studio", "prix": 49, "prix_label": "49 €", "accent": "#fe2c55",
        "accroche": "L'usine complète, en pilote quasi automatique.",
        "cta": "Passer à Studio",
        "limites": {"clips_mois": None, "profils": None},
        "features": {
            "sans_filigrane": True, "series": True, "calendrier": True,
            "tendances": True, "publication": True, "archives_reelles": True,
            "youtube": True, "auto_replan": True, "rapports": True,
            "multi_plateformes": True, "priorite": True,
        },
    },
}
ORDRE = ["free", "pro", "studio"]
DEFAUT = "free"

# libellés affichés dans la grille comparative (ordre = ordre d'affichage)
LIGNES = [
    ("limite:clips_mois", "Clips par mois"),
    ("limite:profils", "Profils / chaînes"),
    ("series", "Séries multi-parties (moteur à abonnés)"),
    ("calendrier", "Calendrier éditorial visuel"),
    ("tendances", "Sujets tendance de la niche (temps réel)"),
    ("sans_filigrane", "Sans filigrane"),
    ("archives_reelles", "Fonds premium (archives réelles)"),
    ("publication", "Publication planifiée TikTok"),
    ("youtube", "Découpe de vidéos longues / YouTube"),
    ("auto_replan", "Calendrier rechargé automatiquement par l'IA"),
    ("rapports", "Rapports hebdo IA"),
    ("multi_plateformes", "Multi-plateformes"),
    ("priorite", "Génération prioritaire"),
]


def infos(tier: str) -> dict:
    return TIERS.get(tier or DEFAUT, TIERS[DEFAUT])


def feature(tier: str, cle: str) -> bool:
    """Le niveau `tier` autorise-t-il la fonction `cle` ? (True/False)"""
    return bool(infos(tier)["features"].get(cle, False))


def limite(tier: str, cle: str):
    """Limite numérique (`clips_mois`, `profils`) — None = illimité."""
    return infos(tier)["limites"].get(cle)


def valeur_ligne(tier: str, cle_ligne: str):
    """Valeur à afficher dans la grille comparative pour une ligne donnée.
    Retourne True/False (coche/croix) ou un texte (ex. '3', '100', 'Illimité')."""
    if cle_ligne.startswith("limite:"):
        v = limite(tier, cle_ligne.split(":", 1)[1])
        return "Illimité" if v is None else str(v)
    return feature(tier, cle_ligne)


def grille() -> list[dict]:
    """Grille comparative prête pour le template : [{cle, label, valeurs:{tier: v}}]."""
    return [{"cle": cle, "label": label,
             "valeurs": {t: valeur_ligne(t, cle) for t in ORDRE}}
            for cle, label in LIGNES]
