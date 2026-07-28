"""CLIPFORGE — scheduler : le battement de cœur de l'usine autonome.

Lancé toutes les 30 min par le Planificateur de tâches Windows (voir installer_planification.ps1).
1. Génère les clips des créneaux éditoriaux arrivés à échéance (calendrier.json),
   puis les planifie pour publication immédiate.
2. Publie les clips planifiés dont l'heure est passée (ou les marque « prêt à publier »
   tant qu'aucun service API n'est branché — rien n'est simulé).
Tout est tracé dans journal.jsonl.
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

# Le Planificateur de tâches Windows lance parfois les processus avec un environnement
# amputé (USERPROFILE/APPDATA absents) : on reconstruit le minimum vital depuis notre chemin.
_racine = APP_DIR
while _racine.parent.name.lower() != "users" and _racine.parent != _racine:
    _racine = _racine.parent
os.environ.setdefault("USERPROFILE", str(_racine))
os.environ.setdefault("HOME", str(_racine))
os.environ.setdefault("APPDATA", str(_racine / "AppData" / "Roaming"))
os.environ.setdefault("LOCALAPPDATA", str(_racine / "AppData" / "Local"))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from pipeline import calendrier, journal, publish, stats, story  # noqa: E402
from pipeline.common import CONFIG, claude_texte, get_profil, sauver_config  # noqa: E402

RAPPORTS = APP_DIR / "rapports"


DELAI_ZOMBIE = 2 * 3600  # un créneau bloqué en "generation" plus de 2 h = crash


def reveiller_zombies() -> None:
    """Un crash pendant la génération laissait le créneau en 'generation' pour toujours.
    Ici : zombie > 2 h -> retenté UNE fois (puis 'erreur') ; créneau 'erreur' -> retenté UNE fois."""
    creneaux = calendrier.charger()
    modifie, maintenant = False, time.time()
    for c in creneaux:
        reprises = int(c.get("reprises", 0))
        if c["statut"] == "generation" and maintenant - c.get("maj_ts", 0) > DELAI_ZOMBIE:
            c["reprises"], c["statut"] = reprises + 1, ("prevu" if reprises < 1 else "erreur")
            c["maj_ts"] = int(maintenant)
            journal.log("creneau_zombie", id=c["id"], sujet=c["sujet"][:60],
                        reprise=c["reprises"], nouveau_statut=c["statut"])
            modifie = True
        elif c["statut"] == "erreur" and reprises < 1:
            c["reprises"], c["statut"], c["maj_ts"] = reprises + 1, "prevu", int(maintenant)
            journal.log("creneau_retente", id=c["id"], sujet=c["sujet"][:60])
            modifie = True
    if modifie:
        calendrier.sauver(creneaux)


def traiter_creneaux() -> None:
    # MODE MANUEL par défaut (generation_auto = false) : le scheduler NE génère RIEN tout seul.
    # Seul le bouton « générer » du calendrier produit des clips. Évite que l'usine sorte des
    # vidéos non demandées (vieux créneaux échus du plan, sujets auto-inventés).
    if not CONFIG.get("generation_auto"):
        return
    for c in calendrier.dus():
        # verrou anti-concurrence : on marque le créneau AVANT de générer,
        # pour qu'un second scheduler (manuel + tâche Windows) ne le reprenne pas
        calendrier.maj_statut(c["id"], "generation")
        journal.log("creneau_du", id=c["id"], sujet=c["sujet"])
        try:
            from run_pipeline import mode_genere
            # 1 créneau = 1 clip (~1 min), comme le bouton « générer » du calendrier
            dest = mode_genere(c["sujet"], None, int(CONFIG.get("mots_par_clip_calendrier", 185)),
                               c["profil"], une_partie=True)
            calendrier.maj_statut(c["id"], "genere")
            if CONFIG["publication"].get("auto_publier"):
                # heure prévue déjà passée -> publication immédiate
                publish.planifier(dest.name, time.strftime("%Y-%m-%d %H:%M"))
            else:
                # semi-auto : le clip attend une publication manuelle
                publish.marquer_a_publier(dest.name)
        except Exception as e:
            calendrier.maj_statut(c["id"], "erreur")
            journal.log("erreur_generation", id=c["id"], sujet=c["sujet"], erreur=str(e)[:300])


def traiter_publications() -> None:
    if not CONFIG["publication"].get("auto_publier"):
        return  # semi-auto : aucune publication automatique
    for meta in publish.echeances():
        ok, msg = publish.publier(meta["fichier"])
        print(f"  publication {meta['fichier']} -> {'OK' if ok else msg}", flush=True)


def _planning() -> tuple[int, list[str]]:
    """Cadence de publication effective : CONFIG['planning'] (adapté par l'IA) sinon
    les valeurs historiques. Toujours bornée : 1-3/jour, heures triées."""
    plan = CONFIG.get("planning") or {}
    par_jour = max(1, min(3, int(plan.get("par_jour", 2) or 2)))
    heures = plan.get("heures") or CONFIG.get("heures_publication") or ["12:30", "20:00"]
    return par_jour, sorted(heures)[:par_jour]


def completer_calendrier() -> None:
    """CALENDRIER GLISSANT : maintient un horizon de 7 jours toujours rempli — quand un
    jour passe, le jour J+7 se remplit tout seul (par_jour sujets aux heures du planning),
    sujets engageants anglés tendances, sans répéter les récents. Ne génère AUCUN clip :
    ça propose des sujets (bouton « générer »), donc indépendant de generation_auto."""
    if not CONFIG.get("replan_auto", True):
        return
    par_jour, heures = _planning()
    creneaux = calendrier.charger()
    occupes: dict[str, set] = {}
    for c in creneaux:                       # tous statuts : un jour traité reste occupé
        occupes.setdefault(c["quand"][:10], set()).add(c["quand"][11:16])
    libres, maintenant = [], time.time()
    for j in range(7):                       # horizon glissant : aujourd'hui -> J+6
        d = time.strftime("%Y-%m-%d", time.localtime(maintenant + j * 86400))
        pris = occupes.get(d, set())
        if len(pris) >= par_jour:
            continue
        for h in heures:
            if h in pris or len(pris) + len([x for x in libres if x.startswith(d)]) >= par_jour:
                continue
            if time.mktime(time.strptime(f"{d} {h}", "%Y-%m-%d %H:%M")) <= maintenant:
                continue                     # créneau du jour déjà passé : on ne remplit pas le passé
            libres.append(f"{d} {h}")
    if not libres:
        return
    profil = get_profil(None)
    recents = [c["sujet"] for c in creneaux[-40:]]
    props = story.sujets_pour_creneaux(profil, libres, recents)
    for p in props:
        calendrier.ajouter(p["quand"], p["sujet"], profil["nom"])
    journal.log("calendrier_glissant", ajoutes=len(props),
                creneaux=" · ".join(p["quand"] for p in props)[:200])


def adapter_planning() -> None:
    """1×/semaine : l'IA analyse les stats réelles et peut ajuster la cadence (par_jour)
    et les heures — TOUJOURS bornées par les garde-fous : 1 à 3/jour, heures entre 11:00
    et 22:30, écart mini = publication.espacement_min_heures (anti-rafale/anti-spam).
    Proposition invalide -> on garde le planning actuel (et on le journalise)."""
    if not CONFIG.get("replan_auto", True):
        return
    plan = dict(CONFIG.get("planning") or {})
    if time.time() - float(plan.get("analyse_le") or 0) < 7 * 86400:
        return
    par_jour, heures = _planning()
    serie = stats.serie_quotidienne(30)
    posts = [{"quand": p.get("publie_le", ""), "vues": p.get("vues", 0),
              "likes": p.get("likes", 0)} for p in stats.toutes()[:30]]
    reponse = claude_texte(
        "Tu pilotes la cadence de publication d'un compte TikTok francophone en croissance.\n"
        f"Cadence actuelle : {par_jour}/jour aux heures {heures}.\n"
        f"Évolution du compte sur 30 jours (abonnés/vues/likes par jour) : {json.dumps(serie[-30:], ensure_ascii=False)}\n"
        f"Derniers posts (heure de publication -> performance) : {json.dumps(posts, ensure_ascii=False)}\n"
        "Si (et SEULEMENT si) les données suggèrent clairement mieux, propose une nouvelle cadence.\n"
        "Contraintes STRICTES : 1 à 3 posts/jour, heures entre 11:00 et 22:30, écart >= 4 h entre posts.\n"
        'Réponds UNIQUEMENT en JSON : {"garder": true} OU '
        '{"garder": false, "par_jour": N, "heures": ["HH:MM", …], "justification": "…"}',
        timeout=300)
    try:
        d = json.loads(reponse[reponse.index("{"):reponse.rindex("}") + 1])
    except (ValueError, TypeError):
        d = {"garder": True}
    plan["analyse_le"] = time.time()
    ecart_min = float(CONFIG["publication"].get("espacement_min_heures", 4))
    if not d.get("garder"):
        try:  # garde-fous durs : toute sortie de piste = proposition rejetée
            pj = int(d["par_jour"])
            hs = sorted(str(h) for h in d["heures"])
            assert 1 <= pj <= 3 and len(hs) == pj
            minutes = []
            for h in hs:
                t = time.strptime(h, "%H:%M")
                minutes.append(t.tm_hour * 60 + t.tm_min)
                assert 11 * 60 <= minutes[-1] <= 22 * 60 + 30
            assert all(b - a >= ecart_min * 60 for a, b in zip(minutes, minutes[1:]))
            plan.update(par_jour=pj, heures=hs,
                        justification=str(d.get("justification", ""))[:300])
            journal.log("planning_adapte", par_jour=pj, heures=" ".join(hs),
                        justification=plan["justification"][:160])
        except (KeyError, ValueError, AssertionError, TypeError):
            journal.log("planning_refuse", proposition=str(d)[:200],
                        raison="hors garde-fous — planning conservé")
    CONFIG["planning"] = plan
    sauver_config()


def auto_replan() -> None:
    """Recharge automatiquement le CALENDRIER en SUJETS (pas en clips) quand la file
    éditoriale est vide — c.-à-d. quand tous les créneaux programmés ont été traités.
    Les sujets sont anglés sur les tendances du jour et optimisés pour la croissance.
    Indépendant de generation_auto : ça ne génère AUCUN clip, ça propose seulement des
    sujets à générer à la main (bouton « générer »). Réglable via replan_auto (défaut true)."""
    if not CONFIG.get("replan_auto", True):
        return
    # file éditoriale = créneaux encore À FAIRE (à générer / en cours / en erreur)
    a_faire = [c for c in calendrier.charger()
               if c["statut"] in ("prevu", "generation", "erreur")]
    if a_faire:  # il reste des sujets à traiter -> on ne recharge pas encore
        return
    journal.log("replan_auto", declencheur="file editoriale vide", trend_aware=True)
    from run_pipeline import mode_planifie_semaine
    mode_planifie_semaine(None, 7)  # story.planifier_semaine angle déjà sur les tendances


def sync_stats() -> None:
    """Relevé quotidien du compte (chaque tick, ~1 requête) + stats posts et ménage (06:00-06:29)."""
    stats.compte()  # archive le relevé du jour dans stats_compte (source de nos graphiques)
    try:  # accumule 1 pilier de tendances niche par tick -> variété complète en quelques ticks
        from pipeline import tendances
        tendances.tendances_niche()
    except Exception:
        pass
    heure = time.strftime("%H:%M")
    if not ("06:00" <= heure < "06:30"):
        return
    n = stats.synchroniser()
    journal.log("stats_sync", posts=n)
    purges = publish.purger_publies(30)  # queue/ ne grossit plus indéfiniment (35 Mo/clip)
    if purges:
        print(f"  ménage : {purges} clip(s) publié(s) > 30 j purgés", flush=True)
    from pipeline import nettoyage
    nettoyage.purger_cache_fonds(7)  # fonds téléchargés > 7 j : retéléchargeables


# Auto-tuning (V-Sprint3) : SEULES ces clés peuvent être modifiées par le rapport hebdo,
# dans ces bornes — le rédacteur en chef propose, le garde-fou dispose.
TUNING_AUTORISE = {
    "heures_publication": lambda v: (isinstance(v, list) and 1 <= len(v) <= 4
                                     and all(isinstance(h, str) and len(h) == 5
                                             and h[2] == ":" for h in v)),
    "mots_par_clip": lambda v: isinstance(v, int) and 300 <= v <= 800,
    "secondes_par_scene": lambda v: isinstance(v, (int, float)) and 3 <= v <= 8,
}


def _appliquer_decisions(decisions: list) -> int:
    """Applique les décisions du rapport (liste blanche + bornes), tout est journalisé."""
    import json as _json

    from pipeline.common import data_root, recharger_config
    chemin_cfg = data_root() / "config.json"  # config de l'utilisateur courant
    cfg = _json.loads(chemin_cfg.read_text(encoding="utf-8"))
    n = 0
    for d in decisions[:3]:
        cle, valeur = d.get("cle"), d.get("valeur")
        if cle not in TUNING_AUTORISE or not TUNING_AUTORISE[cle](valeur):
            journal.log("auto_tuning_refuse", cle=str(cle)[:40], raison="hors liste blanche/bornes")
            continue
        if cle == "mots_par_clip":  # clé de profil, pas globale
            avant = cfg["profils"][cfg["profil_defaut"]].get(cle)
            cfg["profils"][cfg["profil_defaut"]][cle] = valeur
        else:
            avant = cfg.get(cle)
            cfg[cle] = valeur
        journal.log("auto_tuning", cle=cle, avant=avant, apres=valeur,
                    raison=str(d.get("raison", ""))[:200])
        n += 1
    if n:
        tmp = chemin_cfg.with_suffix(".json.tmp")
        tmp.write_text(_json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, chemin_cfg)
        recharger_config()  # la config en cache du user est périmée après auto-tuning
    return n


def _adn_clips() -> list[dict]:
    """ADN des clips produits (queue/) croisé avec leurs stats quand elles existent."""
    import json as _json

    from pipeline.common import QUEUE
    par_fic = stats.par_fichier()
    lignes = []
    for meta_path in sorted(QUEUE.glob("*.json"))[-30:]:
        try:
            meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not meta.get("adn"):
            continue
        s = par_fic.get(meta["fichier"], {})
        lignes.append({"titre": meta.get("titre", "")[:60], "statut": meta.get("statut"),
                       **meta["adn"],
                       "vues": s.get("vues"), "likes": s.get("likes"),
                       "partages": s.get("partages")})
    return lignes


def rapport_hebdo(force: bool = False) -> None:
    """Revue du dimanche (V10.3 + Sprint 3) : rapport écrit par Claude, et ses décisions
    APPLIQUÉES automatiquement (liste blanche bornée) — l'usine s'auto-optimise."""
    if time.strftime("%w") != "0" and not force:
        return
    RAPPORTS.mkdir(exist_ok=True)
    chemin = RAPPORTS / f"rapport_{time.strftime('%Y-%m-%d')}.md"
    if chemin.exists() and not force:
        return
    totaux = stats.totaux()
    lignes_stats = stats.toutes()[:15]
    evenements = journal.recents(60)
    creneaux = [c for c in calendrier.charger() if c["statut"] == "prevu"][:10]
    adn = _adn_clips()
    profil = get_profil(None)
    prompt = f"""Tu es le rédacteur en chef du média TikTok "{profil.get('pseudo', '')}".
Ligne éditoriale : {profil.get('theme', '')}
Écris le rapport hebdomadaire du dimanche en français, format markdown, sections :
1. Chiffres de la semaine  2. Top / flop des clips (croise l'ADN de production avec les stats :
   quel style de hook, quelle variante A/B, quelle durée, quels sujets performent ?)
3. Incidents d'exploitation  4. Semaine à venir
5. Décisions (max 3) : celles qui relèvent des réglages autorisés seront APPLIQUÉES automatiquement.
Si les données sont insuffisantes pour décider, dis-le honnêtement et ne décide RIEN.

RÉGLAGES AUTORISÉS (uniquement) : heures_publication (liste de "HH:MM", 1-4 créneaux) ·
mots_par_clip (300-800) · secondes_par_scene (3-8).
TERMINE ta réponse par un bloc JSON strict (même vide) :
```json
{{"decisions": [{{"cle": "…", "valeur": …, "raison": "…"}}]}}
```

DONNÉES BRUTES :
Totaux : {totaux}
Derniers posts avec stats : {lignes_stats}
ADN des clips produits : {adn}
Journal d'exploitation récent : {[{k: v for k, v in e.items()} for e in evenements[:40]]}
Créneaux à venir : {[(c['quand'], c['sujet'][:80]) for c in creneaux]}"""
    contenu = claude_texte(prompt, timeout=600)
    chemin.write_text(contenu, encoding="utf-8")
    journal.log("rapport_hebdo", fichier=chemin.name)
    # extraction + application des décisions (dernier bloc JSON de la réponse)
    import json as _json
    import re as _re
    blocs = _re.findall(r"\{[^{}]*\"decisions\"\s*:\s*\[.*?\]\s*\}", contenu, _re.DOTALL)
    if blocs:
        try:
            decisions = _json.loads(blocs[-1]).get("decisions", [])
            n = _appliquer_decisions(decisions)
            print(f"  rapport hebdo : {n} décision(s) appliquée(s)", flush=True)
        except (_json.JSONDecodeError, TypeError) as e:
            journal.log("auto_tuning_refuse", raison=f"JSON illisible : {e}")


def menage_reroll() -> None:
    """Purge la voix/sous-titres gardés pour le re-roll « autres fonds » au-delà de 24 h."""
    from pipeline import nettoyage
    nettoyage.purger_reroll_expires(24)


def _tick_utilisateur() -> int:
    """Un cycle complet pour l'utilisateur COURANT (contexte déjà positionné)."""
    journal.log("scheduler_tick")
    code = 0
    for etape in (reveiller_zombies, traiter_creneaux, traiter_publications,
                  completer_calendrier, adapter_planning,
                  sync_stats, menage_reroll, rapport_hebdo):
        try:
            etape()
        except Exception:
            journal.log("erreur_scheduler", etape=etape.__name__,
                        erreur=traceback.format_exc()[-500:])
            code = 1
    return code


def main() -> int:
    """Multi-utilisateurs : le scheduler traite CHAQUE compte dans son dossier isolé."""
    from pipeline import comptes
    from pipeline.common import set_user
    comptes_liste = list(comptes._charger().keys()) or [None]  # au moins le contexte par défaut
    code = 0
    for email in comptes_liste:
        set_user(email)
        code |= _tick_utilisateur()
    return code


if __name__ == "__main__":
    sys.exit(main())
