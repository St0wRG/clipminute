"""Calendrier éditorial (calendrier.json) : créneaux de génération à venir.

Créneau : {"id", "quand": "YYYY-MM-DD HH:MM", "profil", "sujet", "statut": "prevu|genere|erreur"}
Le scheduler génère le clip du créneau à l'échéance puis le passe en file de publication.
"""
import json
import time
import uuid

from . import journal
from .common import data_root


def _fichier():
    return data_root() / "calendrier.json"  # isolé par utilisateur


def charger() -> list[dict]:
    f = _fichier()
    if not f.exists():
        return []
    return json.loads(f.read_text(encoding="utf-8"))


def sauver(creneaux: list[dict]) -> None:
    creneaux.sort(key=lambda c: c["quand"])
    _fichier().write_text(json.dumps(creneaux, ensure_ascii=False, indent=2), encoding="utf-8")


def ajouter(quand: str, sujet: str, profil: str) -> dict:
    creneau = {"id": uuid.uuid4().hex[:8], "quand": quand, "profil": profil,
               "sujet": sujet, "statut": "prevu"}
    creneaux = charger()
    creneaux.append(creneau)
    sauver(creneaux)
    journal.log("creneau_ajoute", id=creneau["id"], prevu_pour=quand, sujet=sujet, profil=profil)
    return creneau


def supprimer(creneau_id: str) -> None:
    sauver([c for c in charger() if c["id"] != creneau_id])


def maj_statut(creneau_id: str, statut: str) -> None:
    creneaux = charger()
    for c in creneaux:
        if c["id"] == creneau_id:
            c["statut"] = statut
            c["maj_ts"] = int(time.time())  # horodatage : détection des créneaux zombies
    sauver(creneaux)


def dus() -> list[dict]:
    maintenant = time.strftime("%Y-%m-%d %H:%M")
    return [c for c in charger() if c["statut"] == "prevu" and c["quand"] <= maintenant]
