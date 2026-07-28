"""Comptes utilisateurs locaux du dashboard (inscription + connexion).

Stockage : comptes.json à la racine de l'app — mots de passe hachés (scrypt via werkzeug),
jamais en clair. Prévu pour un usage local mono/multi-utilisateur simple.
"""
import json
import time

from werkzeug.security import check_password_hash, generate_password_hash

from .common import APP

FICHIER = APP / "comptes.json"


def _charger() -> dict:
    if not FICHIER.exists():
        return {}
    try:
        return json.loads(FICHIER.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _sauver(comptes: dict) -> None:
    FICHIER.write_text(json.dumps(comptes, ensure_ascii=False, indent=2), encoding="utf-8")


def existe_au_moins_un() -> bool:
    return bool(_charger())


def creer(email: str, mot_de_passe: str) -> tuple[bool, str]:
    email = email.strip().lower()
    if "@" not in email or len(email) < 5:
        return False, "Adresse e-mail invalide."
    if len(mot_de_passe) < 8:
        return False, "Mot de passe trop court (8 caractères minimum)."
    comptes = _charger()
    if email in comptes:
        return False, "Un compte existe déjà avec cette adresse."
    comptes[email] = {
        "mdp": generate_password_hash(mot_de_passe),
        "cree_le": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tier": "free",  # tout nouveau compte démarre en Free (on attire, puis on convertit)
    }
    _sauver(comptes)
    return True, "Compte créé."


def verifier(email: str, mot_de_passe: str) -> bool:
    compte = _charger().get(email.strip().lower())
    return bool(compte and check_password_hash(compte["mdp"], mot_de_passe))


def tier(email: str) -> str:
    """Niveau d'abonnement d'un compte ('free' par défaut)."""
    compte = _charger().get((email or "").strip().lower())
    return (compte or {}).get("tier", "free")


def definir_tier(email: str, niveau: str) -> bool:
    """Change le niveau d'un compte (appelé après paiement Stripe validé, ou à la main)."""
    from .abonnement import TIERS

    email = (email or "").strip().lower()
    comptes = _charger()
    if email not in comptes or niveau not in TIERS:
        return False
    comptes[email]["tier"] = niveau
    _sauver(comptes)
    return True
