"""Journal d'exploitation (journal.jsonl) : qui/quoi/quand/résultat, une ligne JSON par événement."""
import json
import time

from .common import data_root


def _journal():
    return data_root() / "journal.jsonl"  # isolé par utilisateur


def _lu_fichier():
    return data_root() / "journal_lu.txt"


def log(evenement: str, **details) -> dict:
    entree = {"quand": time.strftime("%Y-%m-%d %H:%M:%S"), "evenement": evenement, **details}
    with open(_journal(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entree, ensure_ascii=False) + "\n")
    print(f"[journal] {evenement} {details}", flush=True)
    return entree


def recents(n: int = 80) -> list[dict]:
    j = _journal()
    if not j.exists():
        return []
    lignes = j.read_text(encoding="utf-8").strip().splitlines()[-n:]
    out = []
    for ligne in lignes:
        try:
            out.append(json.loads(ligne))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))


GRAVES = {"echec_publication", "erreur_scheduler", "erreur_generation", "service_manquant",
          "quota_tiktok"}


def marquer_lu() -> None:
    """Marque toutes les alertes actuelles comme lues (le bandeau se vide)."""
    _lu_fichier().write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")


def _lu_depuis() -> str:
    try:
        return _lu_fichier().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def alertes(n: int = 20) -> list[dict]:
    """Alertes non lues qui méritent l'attention d'Alex."""
    seuil = _lu_depuis()
    return [e for e in recents(300)
            if e["evenement"] in GRAVES and e["quand"] > seuil][:n]
