"""Nettoyage disque — la génération télécharge et produit des Go régénérables.

Deux mécanismes :
  - `purger_intermediaires(stem)` : en FIN de génération, supprime les fichiers de travail
    d'output/ (mp3, ass, fonds, rendu complet, parties) — les livrables sont dans queue/.
    C'est l'absence de cette purge qui a rempli le disque (5,2 Go le 2026-07-18).
  - `liberer_espace(fichier_queue)` : au clic « J'ai publié » (ou envoi TikTok réussi),
    supprime les restes du clip + les fonds téléchargés (cache) vieux de > 7 jours.
Tout est journalisé, rien de non-régénérable n'est touché.
"""
import json
import re
import time
from pathlib import Path

from . import journal
from .common import APP, OUTPUT

CACHE_FONDS = APP / "assets" / "backgrounds_pexels"  # partagé (cache de fonds)


def _taille(fichiers) -> float:
    total = 0
    for f in fichiers:
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return total / 1048576


def purger_intermediaires(stem: str) -> float:
    """Supprime tous les fichiers de travail output/{stem}* . Retourne les Mo libérés."""
    cibles = list(OUTPUT.glob(f"{stem}*"))
    mo = _taille(cibles)
    for f in cibles:
        f.unlink(missing_ok=True)
    if cibles:
        journal.log("nettoyage_intermediaires", stem=stem, fichiers=len(cibles), mo=round(mo, 1))
    return mo


def garder_voix_pour_reroll(stem: str) -> float:
    """Comme purger_intermediaires MAIS conserve la voix ({stem}.mp3) et les sous-titres
    ({stem}.ass) — nécessaires au re-roll « autres fonds ». Retourne les Mo libérés
    (fonds, segments, rendu complet : tout le régénérable)."""
    garder = {f"{stem}.mp3", f"{stem}.ass"}
    cibles = [f for f in OUTPUT.glob(f"{stem}*") if f.name not in garder]
    mo = _taille(cibles)
    for f in cibles:
        f.unlink(missing_ok=True)
    if cibles:
        journal.log("reroll_voix_conservee", stem=stem, purges=len(cibles), mo=round(mo, 1))
    return mo


def purger_reroll_expires(heures: float = 24) -> float:
    """Passé le délai, supprime la voix/sous-titres gardés pour le re-roll et retire la
    recette de la meta du clip (le bouton « autres fonds » disparaît) → libère le disque."""
    from .common import QUEUE  # proxy multi-comptes : dossier de l'utilisateur courant
    seuil = time.time() - heures * 3600
    mo = 0.0
    for meta_path in QUEUE.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        r = meta.get("reroll")
        if not r or r.get("cree_ts", 0) > seuil:
            continue
        stem = r.get("stem", "")
        if stem:
            mo += purger_intermediaires(stem)  # retire les mp3/ass restants
        meta.pop("reroll", None)
        try:
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    if mo:
        journal.log("reroll_expire_purge", mo=round(mo, 1), heures=heures)
    return mo


def purger_cache_fonds(age_jours: int = 7) -> float:
    """Supprime les fonds téléchargés (Pexels/Pixabay/Commons) non utilisés depuis N jours.
    Retéléchargeables à la demande — on garde les récents (réutilisés entre scènes)."""
    seuil = time.time() - age_jours * 86400
    vieux = [f for f in CACHE_FONDS.glob("*") if f.is_file() and f.stat().st_mtime < seuil]
    mo = _taille(vieux)
    for f in vieux:
        f.unlink(missing_ok=True)
    if vieux:
        journal.log("nettoyage_cache_fonds", fichiers=len(vieux), mo=round(mo, 1),
                    age_jours=age_jours)
    return mo


def liberer_espace(fichier_queue: str, age_cache_jours: int = 7) -> dict:
    """Au clic « J'ai publié » / envoi TikTok réussi : restes d'output du clip + cache vieux."""
    base = Path(fichier_queue).name
    mo = 0.0
    m = re.match(r"\d{8}_\d{6}_(.+)\.mp4$", base)  # retire l'horodatage de mise en file
    if m:
        racine = re.sub(r"_partie\d+$", "", m.group(1))
        mo += purger_intermediaires(racine)
    mo += purger_cache_fonds(age_cache_jours)
    return {"mo_liberes": round(mo, 1)}
