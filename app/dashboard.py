"""CLIPFORGE — dashboard local (Flask).

Lancement : venv\\Scripts\\python.exe dashboard.py  ->  http://127.0.0.1:5877
Historique des clips, génération manuelle, upload + découpe de longues vidéos,
suivi des tâches (les jobs tournent en sous-processus de run_pipeline.py).
"""
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from flask import (Flask, Response, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))
import secrets as _secrets  # noqa: E402

from pipeline import abonnement, calendrier, comptes, journal, publish, stats  # noqa: E402
from pipeline import tiktok as tiktok_api  # noqa: E402

_OAUTH_STATE = {"v": ""}
from pipeline.common import (APP, CONFIG, QUEUE, current_uid, data_root,  # noqa: E402
                             ffmpeg_exe, sauver_config, set_user)

JOBS_DIR = APP / "jobs"   # PARTAGÉ : logs des sous-processus (pas du contenu utilisateur)
# python pour les sous-processus, dans l'ordre :
# 1) venv du projet (poste de dev) ; 2) python.exe voisin de l'interpréteur courant
# (app DISTRIBUÉE : python embarqué, aucun "python" dans le PATH) ; 3) interpréteur courant.
_PY_VENV = APP / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if _PY_VENV.exists():
    PY = _PY_VENV
elif os.name == "nt" and Path(sys.executable).with_name("python.exe").exists():
    PY = Path(sys.executable).with_name("python.exe")
else:
    PY = Path(sys.executable)
JOBS_DIR.mkdir(exist_ok=True)


def _sources():
    d = data_root() / "sources"; d.mkdir(parents=True, exist_ok=True); return d  # isolé /user


def _thumbs():
    d = data_root() / "thumbs"; d.mkdir(parents=True, exist_ok=True); return d  # isolé /user

app = Flask(__name__, template_folder=str(APP_DIR / "templates"))
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 ** 3  # reportages longs (jusqu'à 8 Go)

# clé de session persistée (les sessions survivent aux redémarrages)
_CLE = APP / ".secret_key"
if not _CLE.exists():
    _CLE.write_text(_secrets.token_hex(32), encoding="utf-8")
app.secret_key = _CLE.read_text(encoding="utf-8").strip()

# Durcissement du cookie de session — INCONDITIONNEL (audit sécu) : l'app desktop est
# elle aussi exposée aux pages web du navigateur de l'utilisateur.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,        # inaccessible au JS (limite le vol par XSS)
    SESSION_COOKIE_SAMESITE="Strict",    # jamais envoyé sur une navigation cross-site
)
if os.environ.get("CLIPFORGE_PUBLIC"):   # variante hébergée derrière nginx/HTTPS
    from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config.update(SESSION_COOKIE_SECURE=True, PREFERRED_URL_SCHEME="https")

# --- Garde anti DNS-rebinding / CSRF (audit sécurité) -------------------------------
# Menace réelle : une page web quelconque ouverte dans le navigateur de l'utilisateur
# peut viser http://127.0.0.1:5877. Deux défenses complémentaires :
#  1) Host : on n'accepte QUE les hôtes loopback attendus -> un domaine attaquant
#     re-résolu vers 127.0.0.1 (DNS-rebinding) est rejeté avant toute logique métier.
#  2) Origin/Referer : toute requête MUTANTE doit venir de notre propre origine.
PORT_LOCAL = 5877
HOTES_AUTORISES = {f"127.0.0.1:{PORT_LOCAL}", f"localhost:{PORT_LOCAL}",
                   "127.0.0.1", "localhost"}
ORIGINES_AUTORISEES = {f"http://127.0.0.1:{PORT_LOCAL}", f"http://localhost:{PORT_LOCAL}"}
METHODES_MUTANTES = {"POST", "PUT", "PATCH", "DELETE"}


@app.before_request
def _garde_reseau():
    if os.environ.get("CLIPFORGE_PUBLIC"):      # derrière nginx : l'hôte est le domaine
        return None
    hote = (request.host or "").lower()
    if hote not in HOTES_AUTORISES:
        return "Hôte non autorisé.", 403        # DNS-rebinding bloqué
    if request.method in METHODES_MUTANTES:
        origine = request.headers.get("Origin")
        if origine is None:                     # pas d'Origin : on retombe sur Referer
            ref = request.headers.get("Referer", "")
            origine = "/".join(ref.split("/")[:3]) if ref else None
        if origine is not None and origine not in ORIGINES_AUTORISEES:
            journal.log("csrf_bloque", chemin=request.path, origine=origine[:120])
            return jsonify({"erreur": "Requête refusée (origine non autorisée)."}), 403
    return None

# --- Authentification : tout le dashboard est protégé sauf les pages publiques ---
PUBLIC = {"page_connexion", "faire_connexion", "page_inscription", "faire_inscription",
          "deconnexion", "static", "tiktok_callback", "connexion_rapide", "oublier_profil"}

# Profil mémorisé (écran « clique sur ton avatar ») : le DERNIER compte connecté sur
# CETTE machine — email uniquement, jamais le mot de passe. Modèle « appareil de
# confiance » (type Netflix/Windows) : le clic reconnecte sans mot de passe en local.
_PROFIL_RECENT = APP / "profil_recent.json"


def _retenir_profil(email: str) -> None:
    import hashlib as _hl
    teinte = int(_hl.sha1(email.encode()).hexdigest()[:6], 16) % 360
    _PROFIL_RECENT.write_text(json.dumps({
        "email": email, "initiale": (email[:1] or "?").upper(), "teinte": teinte,
        "quand": time.time()}, ensure_ascii=False), encoding="utf-8")


def _profil_recent() -> dict | None:
    try:
        p = json.loads(_PROFIL_RECENT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return p if p.get("email") in comptes._charger() else None


@app.before_request
def _exiger_connexion():
    if request.endpoint in PUBLIC or (request.endpoint or "").startswith("static"):
        return None
    if not session.get("user"):
        # si aucun compte n'existe encore, on force l'inscription
        cible = "page_inscription" if not comptes.existe_au_moins_un() else "page_connexion"
        if request.path.startswith("/api/"):
            return jsonify({"erreur": "Non connecté."}), 401
        return redirect(url_for(cible))
    set_user(session["user"])  # contexte multi-utilisateur : toutes les données ciblent ce compte
    return None


@app.get("/inscription")
def page_inscription():
    if session.get("user"):
        return redirect("/")
    return render_template("connexion.html", mode="inscription", action="/inscription")


@app.post("/inscription")
def faire_inscription():
    email = request.form.get("email", "")
    ok, msg = comptes.creer(email, request.form.get("mdp", ""))
    if not ok:
        return render_template("connexion.html", mode="inscription", action="/inscription",
                               erreur=msg, email=email)
    session["user"] = email.strip().lower()
    _retenir_profil(session["user"])
    journal.log("compte_cree", email=session["user"])
    return redirect("/")


@app.get("/connexion")
def page_connexion():
    if session.get("user"):
        return redirect("/")
    if not comptes.existe_au_moins_un():
        return redirect("/inscription")
    return render_template("connexion.html", mode="connexion", action="/connexion",
                           profil=_profil_recent())


@app.post("/connexion-rapide")
def connexion_rapide():
    """Clic sur l'avatar mémorisé -> reconnexion sans mot de passe (machine de confiance).
    N'accepte QUE l'email du profil mémorisé (écrit après une vraie connexion ici)."""
    email = request.form.get("email", "")
    p = _profil_recent()
    if not p or p["email"] != email:
        return "Profil non reconnu — connecte-toi avec ton mot de passe.", 403
    session["user"] = email
    set_user(email)
    journal.log("connexion_rapide")
    return redirect("/")


@app.post("/oublier-profil")
def oublier_profil():
    """« Ce n'est pas moi » : oublie l'avatar mémorisé et montre le formulaire classique."""
    try:
        _PROFIL_RECENT.unlink()
    except OSError:
        pass
    return redirect("/connexion")


@app.post("/connexion")
def faire_connexion():
    email = request.form.get("email", "")
    if comptes.verifier(email, request.form.get("mdp", "")):
        session["user"] = email.strip().lower()
        _retenir_profil(session["user"])
        return redirect("/")
    return render_template("connexion.html", mode="connexion", action="/connexion",
                           erreur="E-mail ou mot de passe incorrect.", email=email)


@app.get("/deconnexion")
def deconnexion():
    session.pop("user", None)
    return redirect("/connexion")

REGISTRY = JOBS_DIR / "registry.json"
JOBS: dict[str, dict] = {}


def _sauver_registre() -> None:
    """Persiste les jobs (V5.6) : la liste survit aux redémarrages du dashboard."""
    donnees = {jid: {"label": j["label"], "pid": j["pid"], "debut": j["debut"],
                     "log": j["log"].name, "uid": j.get("uid", "_defaut")}
               for jid, j in sorted(JOBS.items())[-30:]}
    REGISTRY.write_text(json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")


def _charger_registre() -> None:
    if not REGISTRY.exists():
        return
    try:
        donnees = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    for jid, j in donnees.items():
        JOBS[jid] = {"label": j["label"], "pid": j["pid"], "debut": j["debut"],
                     "log": JOBS_DIR / j["log"], "proc": None, "uid": j.get("uid", "_defaut")}


def _start_job(args: list[str], label: str, script: str = "run_pipeline.py") -> str:
    jid = time.strftime("%H%M%S") + uuid.uuid4().hex[:4]
    log_path = JOBS_DIR / f"{jid}.log"
    log_file = open(log_path, "w", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    # Détacher le job pour qu'un redémarrage/crash du dashboard ne le tue plus.
    # Windows : DETACHED_PROCESS + BREAKAWAY_FROM_JOB (le serveur peut être dans un Job Object).
    # Linux/mac (VPS) : start_new_session=True (setsid) -> même effet de détachement.
    if os.name == "nt":
        base = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS
        kw_detache = {"creationflags": base | 0x01000000}       # + CREATE_BREAKAWAY_FROM_JOB
        kw_repli = {"creationflags": base}                      # si le Job interdit le breakaway
    else:
        kw_detache = {"start_new_session": True}
        kw_repli = {"start_new_session": True}
    # --user : le sous-processus écrit dans les données de CET utilisateur (isolation)
    commande = [str(PY), "-u", script, *args, "--user", session.get("user", "")]
    try:
        proc = subprocess.Popen(
            commande, cwd=str(APP), stdout=log_file, stderr=subprocess.STDOUT, env=env,
            **kw_detache,
        )
    except OSError:
        proc = subprocess.Popen(
            commande, cwd=str(APP), stdout=log_file, stderr=subprocess.STDOUT, env=env,
            **kw_repli,
        )
    JOBS[jid] = {"proc": proc, "pid": proc.pid, "log": log_path,
                 "label": label, "debut": time.time(), "uid": current_uid()}
    _sauver_registre()
    return jid


def _vivant(j: dict) -> bool | None:
    """True/False = en vie/mort ; None si le code retour direct est disponible."""
    if j.get("proc") is not None:
        return None
    import psutil
    try:
        p = psutil.Process(j["pid"])
        return p.is_running() and "python" in p.name().lower()
    except psutil.NoSuchProcess:
        return False


def _actif(j: dict) -> bool:
    if j.get("proc") is not None:
        return j["proc"].poll() is None
    return bool(_vivant(j))


def _job_state(jid: str, j: dict) -> dict:
    tail, pct, lines = "", None, []
    try:
        lines = j["log"].read_text(encoding="utf-8", errors="replace").strip().splitlines()
        tail = lines[-1] if lines else ""
        for line in reversed(lines):
            m = re.search(r"\((\d+)%\)", line)
            if m:
                pct = int(m.group(1))
                break
    except OSError:
        pass
    vie = _vivant(j)
    if vie is None:
        code = j["proc"].poll()
        statut = "en_cours" if code is None else ("termine" if code == 0 else "erreur")
    elif vie:
        statut = "en_cours"
    else:
        # job d'une session précédente : on juge sur la fin du log
        statut = "termine" if any(ligne.startswith("OK ->") for ligne in lines[-3:]) else "erreur"
    if statut != "en_cours" and any(ligne.startswith("ANNULEE") for ligne in lines[-3:]):
        statut = "annulee"
    if statut == "en_cours":
        ecoule = int(time.time() - j["debut"])
    else:
        # tâche finie : on FIGE la durée à la dernière écriture du log (fin réelle)
        try:
            ecoule = max(0, int(j["log"].stat().st_mtime - j["debut"]))
        except OSError:
            ecoule = 0
    return {
        "id": jid, "label": j["label"], "statut": statut,
        "ecoule": ecoule, "log": tail, "progression": pct,
    }


_charger_registre()


def _tier() -> str:
    return comptes.tier(session.get("user", ""))


def _clips_ce_mois() -> int:
    mois = time.strftime("%Y-%m")
    return sum(1 for e in journal.recents(600)
               if e["evenement"] == "clip_produit" and str(e.get("quand", "")).startswith(mois))


def _quota_clips_ok() -> tuple[bool, str]:
    t = _tier()
    lim = abonnement.limite(t, "clips_mois")
    if lim is not None and _clips_ce_mois() >= lim:
        return False, (f"Limite {abonnement.infos(t)['nom']} atteinte : {lim} clips ce mois-ci. "
                       "Passe à Pro ou Studio pour continuer à générer.")
    return True, ""


@app.get("/api/abonnement")
def api_abonnement():
    t = _tier()
    return jsonify({"tier": t, "infos": abonnement.infos(t),
                    "clips_mois": _clips_ce_mois(), "limite_clips": abonnement.limite(t, "clips_mois"),
                    "tiers": abonnement.TIERS, "ordre": abonnement.ORDRE, "grille": abonnement.grille()})


@app.get("/offres")
def page_offres():
    return render_template("offres.html", tier=_tier(),
                           tiers=abonnement.TIERS, ordre=abonnement.ORDRE, grille=abonnement.grille())


@app.post("/api/abonnement/definir")
def api_abonnement_definir():
    """Aperçu/test des niveaux (phase vitrine, sans paiement) : bascule le niveau du compte courant.
    Quand Stripe sera branché, le vrai upgrade passera par la validation du paiement, pas par ici."""
    # ⚠️ Aperçu réservé au DÉVELOPPEMENT : dans le produit distribué, cet endpoint
    # permettrait de s'auto-attribuer n'importe quel niveau. Le vrai upgrade passera
    # par la validation serveur d'un paiement (Stripe), jamais par un appel client.
    if not os.environ.get("CLIPMINUTE_DEV"):
        return jsonify({"erreur": "Changement de niveau indisponible."}), 403
    niveau = request.get_json(force=True).get("tier", "")
    if not comptes.definir_tier(session.get("user", ""), niveau):
        return jsonify({"erreur": "Niveau invalide."}), 400
    journal.log("abonnement_change", tier=niveau, mode="apercu")
    return jsonify({"ok": True, "tier": niveau})


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/generer")
def api_generer():
    ok, msg = _quota_clips_ok()
    if not ok:
        return jsonify({"erreur": msg, "upgrade": True}), 402
    data = request.get_json(force=True)
    args = ["--mode", "genere"]
    sujet = (data.get("sujet") or "").strip()
    commentaire = (data.get("commentaire") or "").strip()
    if commentaire:  # format « réponse à un commentaire »
        args += ["--commentaire", commentaire]
        if (data.get("commentaire_auteur") or "").strip():
            args += ["--commentaire-auteur", data["commentaire_auteur"].strip()]
        label = f"Réponse — {commentaire[:40]}"
    else:
        if sujet:
            args += ["--sujet", sujet]
        label = f"Clip IA — {sujet or 'sujet aléatoire'}"
    if data.get("mots"):
        args += ["--mots", str(int(data["mots"]))]
    if data.get("profil"):
        args += ["--profil", data["profil"]]
    if data.get("hook") and not commentaire:  # accroche choisie avant rendu (Sprint 2)
        args += ["--hook", json.dumps(data["hook"], ensure_ascii=False)]
    return jsonify({"job": _start_job(args, label)})


@app.post("/api/hooks")
def api_hooks():
    """Propose 3 accroches d'angles différents pour un sujet — étape AVANT le rendu.
    Synchrone (Claude ~30-60 s) : le front affiche un chargement puis les cartes."""
    from pipeline import story
    data = request.get_json(force=True)
    sujet = (data.get("sujet") or "").strip()
    if not sujet:
        return jsonify({"erreur": "Donne un sujet pour proposer des accroches."}), 400
    nom = data.get("profil") or CONFIG["profil_defaut"]
    profil = CONFIG["profils"].get(nom) or next(iter(CONFIG["profils"].values()))
    try:
        hooks = story.proposer_hooks(sujet, profil, n=3)
    except Exception as e:
        journal.log("hooks_echec", sujet=sujet[:80], raison=str(e)[:200])
        return jsonify({"erreur": "Impossible de proposer des accroches, réessaie."}), 500
    journal.log("hooks_proposes", sujet=sujet[:80], nombre=len(hooks))
    return jsonify({"hooks": hooks})


@app.get("/api/tendances")
def api_tendances():
    from pipeline import tendances
    return jsonify({"tendances": tendances.tendances_niche(12)})  # sujets niche (Reddit)


@app.get("/api/config")
def api_config():
    return jsonify({
        "profils": list(CONFIG["profils"].keys()),
        "themes": {nom: p.get("theme", "") for nom, p in CONFIG["profils"].items()},
        "profil_defaut": CONFIG["profil_defaut"],
        "service_publication": CONFIG["publication"].get("service"),
        "max_posts_jour": CONFIG["publication"].get("max_posts_jour", 3),
        "auto_publier": bool(CONFIG["publication"].get("auto_publier")),
    })


@app.post("/api/profil")
def api_profil():
    d = request.get_json(force=True)
    nom = d.get("profil")
    if nom not in CONFIG["profils"]:
        return jsonify({"erreur": f"Profil inconnu : {nom}"}), 400
    theme = (d.get("theme") or "").strip()
    if not theme:
        return jsonify({"erreur": "Thème vide."}), 400
    CONFIG["profils"][nom]["theme"] = theme
    sauver_config()  # persiste dans data/<uid>/config.json
    return jsonify({"ok": True})


@app.get("/api/kpi")
def api_kpi():
    statuts = {}
    for meta_path in QUEUE.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        statuts[meta.get("statut", "?")] = statuts.get(meta.get("statut", "?"), 0) + 1
    creneaux = [c for c in calendrier.charger() if c["statut"] == "prevu"]
    aujourdhui = time.strftime("%Y-%m-%d")
    posts_jour = sum(1 for e in journal.recents(300)
                     if e["evenement"] == "publie" and e["quand"].startswith(aujourdhui))
    actifs = sum(1 for j in JOBS.values() if j.get("uid") == current_uid() and _actif(j))
    return jsonify({
        "pause": bool(CONFIG["publication"].get("pause")),
        "compte": stats.compte(),
        "vues": stats.vues_suivies(),  # {vues, posts_suivis, videos_compte, maj} — tuile honnête
        "produits": sum(statuts.values()),
        "en_attente": statuts.get("en_attente", 0) + statuts.get("pret_a_publier", 0),
        "planifies": statuts.get("planifie", 0),
        "publies": statuts.get("publie", 0),
        "posts_jour": posts_jour,
        "max_posts_jour": CONFIG["publication"].get("max_posts_jour", 3),
        "creneaux": len(creneaux),
        "prochain_creneau": creneaux[0]["quand"] if creneaux else None,
        "taches_actives": actifs,
    })


@app.get("/api/calendrier")
def api_calendrier():
    return jsonify(calendrier.charger())


@app.post("/api/calendrier")
def api_calendrier_ajout():
    d = request.get_json(force=True)
    try:
        time.strptime(d["quand"], "%Y-%m-%d %H:%M")
    except (KeyError, ValueError):
        return jsonify({"erreur": "Date attendue au format YYYY-MM-DD HH:MM"}), 400
    c = calendrier.ajouter(d["quand"], d.get("sujet", "").strip() or "sujet aléatoire",
                           d.get("profil") or CONFIG["profil_defaut"])
    return jsonify(c)


@app.post("/api/calendrier/supprimer")
def api_calendrier_suppr():
    calendrier.supprimer(request.get_json(force=True).get("id", ""))
    return jsonify({"ok": True})


@app.post("/api/calendrier/statut")
def api_calendrier_statut():
    """Marquer un créneau comme « fait » (déjà géré/généré à la main) sans rien produire,
    ou le rétablir en « prévu ». Un créneau « fait » n'est plus proposé à la génération."""
    d = request.get_json(force=True)
    cid, statut = d.get("id", ""), d.get("statut", "")
    if statut not in ("fait", "prevu"):
        return jsonify({"erreur": "Statut invalide."}), 400
    if not any(c["id"] == cid for c in calendrier.charger()):
        return jsonify({"erreur": "Créneau introuvable."}), 404
    calendrier.maj_statut(cid, statut)
    return jsonify({"ok": True})


@app.post("/api/calendrier/generer")
def api_calendrier_generer():
    """Génère à la demande le clip d'un créneau (bouton « générer » du calendrier).
    Passe le créneau en 'generation' (le scheduler ne le reprend plus, dus() = 'prevu'),
    le job met le statut à jour en fin (genere/erreur) via run_pipeline --creneau."""
    cid = request.get_json(force=True).get("id", "")
    c = next((x for x in calendrier.charger() if x["id"] == cid), None)
    if not c:
        return jsonify({"erreur": "Créneau introuvable."}), 404
    if c["statut"] == "generation":
        return jsonify({"erreur": "Génération déjà en cours pour ce créneau."}), 409
    ok, msg = _quota_clips_ok()
    if not ok:
        return jsonify({"erreur": msg, "upgrade": True}), 402
    calendrier.maj_statut(cid, "generation")
    # 1 créneau = 1 clip (~1 min) : longueur courte + « une partie » -> jamais de découpe en série.
    # Une série est déjà découpée en créneaux séparés (1 partie/jour), donc chacun fait 1 clip.
    mots = int(CONFIG.get("mots_par_clip_calendrier", 175))
    args = ["--mode", "genere", "--sujet", c["sujet"],
            "--profil", c.get("profil") or CONFIG["profil_defaut"], "--creneau", cid,
            "--mots", str(mots), "--une-partie"]
    hook = request.get_json(force=True).get("hook")
    if hook:  # accroche choisie avant rendu (Sprint 2)
        args += ["--hook", json.dumps(hook, ensure_ascii=False)]
    return jsonify({"job": _start_job(args, f"Créneau — {c['sujet'][:40]}")})


@app.post("/api/calendrier/generer-serie")
def api_calendrier_generer_serie():
    """Génère en un clic TOUTES les parties « à générer » d'une série (chacune reste 1 clip).
    Elles se posteront ensuite 1 par jour, mais tu les as prêtes d'un coup pour les relire."""
    serie = (request.get_json(force=True).get("serie") or "").strip()
    if not serie:
        return jsonify({"erreur": "Série non précisée."}), 400
    mots = str(int(CONFIG.get("mots_par_clip_calendrier", 175)))
    parts = sorted((c for c in calendrier.charger()
                    if c.get("serie") == serie and c.get("statut") == "prevu"),
                   key=lambda c: c.get("quand", ""))
    if not parts:
        return jsonify({"erreur": "Aucune partie à générer pour cette série."}), 404
    if not abonnement.feature(_tier(), "series"):
        return jsonify({"erreur": "Les séries multi-parties sont réservées aux niveaux Pro et Studio.",
                        "upgrade": True}), 402
    ok, msg = _quota_clips_ok()
    if not ok:
        return jsonify({"erreur": msg, "upgrade": True}), 402
    jobs = []
    for c in parts:
        calendrier.maj_statut(c["id"], "generation")
        args = ["--mode", "genere", "--sujet", c["sujet"],
                "--profil", c.get("profil") or CONFIG["profil_defaut"], "--creneau", c["id"],
                "--mots", mots, "--une-partie"]
        jobs.append(_start_job(args, f"Série {serie} — {c['quand'][5:10]}"))
    return jsonify({"jobs": jobs, "parties": len(jobs)})


@app.post("/api/planifier-semaine")
def api_planifier_semaine():
    d = request.get_json(force=True)
    profil = d.get("profil") or CONFIG["profil_defaut"]
    args = ["--mode", "planifie-semaine", "--profil", profil]
    return jsonify({"job": _start_job(args, f"Plan éditorial 7 jours — {profil}")})


@app.post("/api/publier")
def api_publier():
    fichier = Path(request.get_json(force=True).get("fichier", "")).name
    ok, msg = publish.publier(fichier)
    return jsonify({"ok": ok, "message": msg})


@app.post("/api/planifier")
def api_planifier():
    d = request.get_json(force=True)
    try:
        meta = publish.planifier(Path(d["fichier"]).name, d["quand"])
    except (KeyError, ValueError):
        return jsonify({"erreur": "Date attendue au format YYYY-MM-DD HH:MM"}), 400
    return jsonify(meta)


@app.get("/api/journal")
def api_journal():
    return jsonify({"recents": journal.recents(40), "alertes": journal.alertes()})


@app.get("/journal")
def page_journal():
    return render_template("journal.html")


@app.get("/api/journal/complet")
def api_journal_complet():
    n = min(int(request.args.get("n", 200)), 1000)
    evenements = journal.recents(n)
    if request.args.get("filtre") == "erreurs":
        evenements = [e for e in evenements if e["evenement"] in journal.GRAVES]
    return jsonify(evenements)


@app.post("/api/journal/lu")
def api_journal_lu():
    journal.marquer_lu()
    return jsonify({"ok": True})


@app.post("/api/jobs/annuler")
def api_job_annuler():
    jid = request.get_json(force=True).get("id", "")
    j = JOBS.get(jid)
    if not j or j.get("uid") != current_uid():  # on ne touche que SES tâches
        return jsonify({"erreur": "Tâche inconnue."}), 404
    if not _actif(j):
        return jsonify({"erreur": "Cette tâche n'est plus en cours."}), 400
    import psutil
    try:
        p = psutil.Process(j["pid"])
        for enfant in p.children(recursive=True):
            enfant.kill()
        p.kill()
    except psutil.NoSuchProcess:
        pass
    with open(j["log"], "a", encoding="utf-8") as f:
        f.write("\nANNULEE par l'utilisateur depuis le dashboard\n")
    journal.log("tache_annulee", id=jid, label=j["label"])
    return jsonify({"ok": True})


@app.post("/api/jobs/supprimer")
def api_job_supprimer():
    """Retire une tâche TERMINÉE de la liste (et supprime son log)."""
    jid = request.get_json(force=True).get("id", "")
    j = JOBS.get(jid)
    if not j or j.get("uid") != current_uid():  # on ne touche que SES tâches
        return jsonify({"erreur": "Tâche inconnue."}), 404
    if _actif(j):
        return jsonify({"erreur": "Tâche en cours — annule-la d'abord."}), 400
    try:
        j["log"].unlink(missing_ok=True)
    except OSError:
        pass
    JOBS.pop(jid, None)
    _sauver_registre()
    return jsonify({"ok": True})


def _maj_config_fichier(mutation) -> None:
    """Applique une mutation à la config de l'utilisateur courant (proxy) et la persiste."""
    mutation(CONFIG)   # CONFIG = proxy sur data/<uid>/config.json (mis en cache)
    sauver_config()


@app.post("/api/pause")
def api_pause():
    etat = bool(request.get_json(force=True).get("pause"))

    def mut(cfg):
        cfg["publication"]["pause"] = etat
    _maj_config_fichier(mut)
    journal.log("pause_publication" if etat else "reprise_publication")
    return jsonify({"pause": etat})


@app.get("/api/stats")
def api_stats():
    return jsonify({"totaux": stats.totaux(), "par_fichier": stats.par_fichier()})


@app.get("/api/stats/serie")
def api_stats_serie():
    return jsonify(stats.serie_quotidienne(30))


@app.post("/api/stats/sync")
def api_stats_sync():
    try:
        n = stats.synchroniser()
    except Exception as e:
        return jsonify({"erreur": f"Synchronisation impossible : {e}"}), 502
    journal.log("stats_sync", posts=n, source="manuel")
    return jsonify({"posts": n})


@app.post("/api/profil/nouveau")
def api_profil_nouveau():
    d = request.get_json(force=True)
    nom = re.sub(r"[^a-z0-9_-]", "", (d.get("nom") or "").lower().replace(" ", "-"))
    if not nom:
        return jsonify({"erreur": "Nom de profil invalide."}), 400
    if nom in CONFIG["profils"]:
        return jsonify({"erreur": f"Le profil '{nom}' existe déjà."}), 400
    modele = dict(CONFIG["profils"][CONFIG["profil_defaut"]])
    modele["theme"] = (d.get("theme") or "").strip() or modele.get("theme", "")
    modele["pseudo"] = (d.get("pseudo") or "").strip()

    def mut(cfg):
        cfg["profils"][nom] = modele
    _maj_config_fichier(mut)
    journal.log("profil_cree", profil=nom)
    return jsonify({"ok": True, "profil": nom})


@app.post("/api/caption")
def api_caption():
    d = request.get_json(force=True)
    fichier = Path(d.get("fichier", "")).name
    try:
        meta = publish.lire_meta(fichier)
    except FileNotFoundError:
        return jsonify({"erreur": "Clip introuvable."}), 404
    meta["caption"] = (d.get("caption") or "").strip()
    publish.ecrire_meta(fichier, meta)
    journal.log("bio_modifiee", fichier=fichier)
    return jsonify({"ok": True})


CLES_GLOBALES = ["duree_partie_sec", "whisper_modele", "langue_source", "heures_publication"]
CLES_PUBLICATION = ["max_posts_jour", "privacy_level", "pause"]
CLES_PROFIL = ["theme", "voix", "vitesse_voix", "mots_par_clip", "style_soustitres",
               "pseudo", "sujets", "pexels_recherches"]


@app.get("/reglages")
def page_reglages():
    return render_template("reglages.html")


@app.get("/api/reglages")
def api_reglages():
    def masque(cle):
        return f"…{cle[-4:]}" if cle else ""
    return jsonify({
        "global": {c: CONFIG.get(c) for c in CLES_GLOBALES},
        "publication": {c: CONFIG["publication"].get(c) for c in CLES_PUBLICATION},
        "cles": {"pexels": masque(CONFIG.get("pexels_api_key") or ""),
                 "zernio": masque(CONFIG["publication"].get("api_key") or ""),
                 "anthropic": masque(CONFIG.get("anthropic_api_key") or "")},
        "tiktok": {"client_key": CONFIG.get("tiktok", {}).get("client_key", ""),
                   "client_secret": masque(CONFIG.get("tiktok", {}).get("client_secret") or ""),
                   "mode": CONFIG.get("tiktok", {}).get("mode", "sandbox")},
        "profils": {nom: {c: p.get(c) for c in CLES_PROFIL}
                    for nom, p in CONFIG["profils"].items()},
        "profil_defaut": CONFIG["profil_defaut"],
    })


@app.post("/api/reglages")
def api_reglages_post():
    d = request.get_json(force=True)

    def mut(cfg):
        for c, v in (d.get("global") or {}).items():
            if c in CLES_GLOBALES:
                cfg[c] = v
        for c, v in (d.get("publication") or {}).items():
            if c in CLES_PUBLICATION:
                cfg["publication"][c] = v
        for nom, champs in (d.get("profils") or {}).items():
            if nom in cfg["profils"]:
                for c, v in champs.items():
                    if c in CLES_PROFIL:
                        cfg["profils"][nom][c] = v
        for service, cle in (d.get("cles") or {}).items():
            if cle and not cle.startswith("…"):
                if service == "pexels":
                    cfg["pexels_api_key"] = cle
                elif service == "zernio":
                    cfg["publication"]["api_key"] = cle
                elif service == "anthropic":
                    cfg["anthropic_api_key"] = cle
        tt = d.get("tiktok") or {}
        cfg.setdefault("tiktok", {})
        if tt.get("client_key"):
            cfg["tiktok"]["client_key"] = tt["client_key"].strip()
        if tt.get("client_secret") and not tt["client_secret"].startswith("…"):
            cfg["tiktok"]["client_secret"] = tt["client_secret"].strip()
        if tt.get("mode"):
            cfg["tiktok"]["mode"] = tt["mode"]
    _maj_config_fichier(mut)
    journal.log("reglages_modifies")
    return jsonify({"ok": True})


# ---------- mise à jour à distance ----------
_MAJ_CACHE = {"quand": 0.0, "info": None}
# URL pérenne : GitHub Pages redirigera vers clipminute.app quand le domaine sera branché.
# ⚠️ SÉCURITÉ : constante FIGÉE — surtout pas surchargeable par la config (une écriture
# dans config.json détournerait sinon le canal de mise à jour = exécution de code).
MAJ_URL = "https://st0wrg.github.io/clipminute/version.json"
# Domaines dont on accepte de télécharger ET D'EXÉCUTER un installeur (épinglage).
DOMAINES_MAJ = {"github.com", "objects.githubusercontent.com",
                "st0wrg.github.io", "clipminute.app", "www.clipminute.app"}


def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except ValueError:
        return (0,)


@app.get("/api/maj")
def api_maj():
    """Compare la version installée au manifeste en ligne (cache 6 h)."""
    import requests as _rq
    from pipeline.common import version_app
    actuelle = version_app()
    if time.time() - _MAJ_CACHE["quand"] > 6 * 3600:
        try:
            r = _rq.get(MAJ_URL, timeout=6)   # URL figée : pas d'override par config
            _MAJ_CACHE["info"] = r.json() if r.ok else None
        except Exception:
            _MAJ_CACHE["info"] = None       # hors-ligne / manifeste absent : pas d'alerte
        _MAJ_CACHE["quand"] = time.time()
    info = _MAJ_CACHE["info"] or {}
    dispo = info.get("version", "")
    return jsonify({
        "actuelle": actuelle,
        "dispo": dispo,
        "maj": bool(dispo) and _version_tuple(dispo) > _version_tuple(actuelle),
        "notes": info.get("notes", ""),
        "url": info.get("url", ""),
    })


@app.post("/api/maj/installer")
def api_maj_installer():
    """Télécharge le nouvel installeur et le lance : Inno ferme l'app, met à jour
    par-dessus (même AppId) en PRÉSERVANT les données, puis propose de relancer.

    SÉCURITÉ (audit) — on exécute un binaire, donc chaîne de confiance stricte :
      1) l'URL doit être https ET sur un DOMAINE ÉPINGLÉ (pas d'URL arbitraire) ;
      2) le manifeste doit fournir un SHA-256 ; le fichier téléchargé est vérifié
         AVANT exécution, sinon il est supprimé et rien n'est lancé ;
      3) le nom de fichier local est dérivé d'une version assainie (pas de traversée).
    """
    import hashlib
    import re as _re
    import tempfile
    import requests as _rq

    info = _MAJ_CACHE["info"] or {}
    url, attendu = info.get("url", ""), (info.get("sha256") or "").strip().lower()
    hote = urlparse(url).hostname or ""
    if not (url.startswith("https://") and url.endswith(".exe")
            and hote in DOMAINES_MAJ):
        journal.log("maj_refusee", raison="url non épinglée", url=url[:160])
        return jsonify({"erreur": "Mise à jour refusée : source non reconnue."}), 400
    if not _re.fullmatch(r"[0-9a-f]{64}", attendu):
        journal.log("maj_refusee", raison="sha256 absent/invalide", url=url[:160])
        return jsonify({"erreur": "Mise à jour refusée : empreinte de sécurité manquante."}), 400
    version = info.get("version", "maj")
    if not _re.fullmatch(r"[0-9]+(\.[0-9]+){0,3}", str(version)):
        version = "maj"                       # jamais de version brute dans un chemin
    dest = Path(tempfile.gettempdir()) / f"ClipMinute-Setup-{version}.exe"
    try:
        calcul = hashlib.sha256()
        with _rq.get(url, stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for morceau in r.iter_content(1024 * 512):
                    calcul.update(morceau)
                    f.write(morceau)
    except Exception as e:
        return jsonify({"erreur": f"Téléchargement impossible : {e}"}), 502
    if calcul.hexdigest() != attendu:         # binaire altéré / MITM / manifeste falsifié
        try:
            dest.unlink()
        except OSError:
            pass
        journal.log("maj_refusee", raison="empreinte SHA-256 incorrecte", version=version)
        return jsonify({"erreur": "Mise à jour refusée : le fichier téléchargé ne "
                                  "correspond pas à l'empreinte officielle."}), 502
    journal.log("maj_lancee", version=version, sha256=attendu[:16])
    # détaché : l'installeur survit à la fermeture de l'app (qu'il va lui-même provoquer)
    subprocess.Popen([str(dest), "/SP-"],
                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008
                     if os.name == "nt" else 0)
    return jsonify({"ok": True})


@app.get("/api/tiktok/etat")
def api_tiktok_etat():
    tt = CONFIG.get("tiktok", {})
    info = {"configure": bool(tt.get("client_key") and tt.get("client_secret")),
            "connecte": tiktok_api.est_connecte(), "mode": tt.get("mode", "sandbox"),
            "compte": None}
    if info["connecte"]:
        try:
            u = tiktok_api.user_info()
            info["compte"] = {"pseudo": u.get("display_name"), "avatar": u.get("avatar_url")}
        except Exception as e:
            info["erreur"] = str(e)[:200]
    return jsonify(info)


@app.get("/tiktok/connect")
def tiktok_connect():
    state = _secrets.token_urlsafe(16)
    verifier, challenge = tiktok_api.pkce_pair()
    # état + verifier dans la session (cookie signé) : survivent aux redémarrages/multi-process
    session["tt_state"] = state
    session["tt_verifier"] = verifier
    try:
        return redirect(tiktok_api.auth_url(state, challenge))
    except Exception as e:
        return f"<pre style='padding:24px;font-family:sans-serif'>Connexion impossible : {e}</pre>", 400


# OAuth via le NAVIGATEUR SYSTÈME (fenêtre native) : Google interdit sa connexion dans
# les webviews embarquées (écran blanc) → règle desktop RFC 8252 : l'autorisation se fait
# dans le vrai navigateur, le callback localhost ramène le résultat. L'état est stocké
# côté serveur et LIÉ AU COMPTE (le navigateur externe n'a pas notre cookie de session).
_OAUTH_EXTERNE: dict[str, dict] = {}


@app.post("/api/tiktok/connect-navigateur")
def api_tiktok_connect_navigateur():
    import webbrowser
    state = _secrets.token_urlsafe(16)
    verifier, challenge = tiktok_api.pkce_pair()
    _OAUTH_EXTERNE[state] = {"verifier": verifier, "user": session["user"],
                             "quand": time.time()}
    for s in [s for s, v in _OAUTH_EXTERNE.items() if time.time() - v["quand"] > 600]:
        del _OAUTH_EXTERNE[s]            # états périmés (10 min)
    try:
        webbrowser.open(tiktok_api.auth_url(state, challenge))
    except Exception as e:
        return jsonify({"erreur": f"Impossible d'ouvrir le navigateur : {e}"}), 500
    return jsonify({"ok": True})


@app.get("/callback/")
def tiktok_callback():
    code = request.args.get("code", "")
    state = request.args.get("state", "")
    err = request.args.get("error")
    if err:
        return f"<h2>Autorisation refusée</h2><p>{err} — {request.args.get('error_description','')}</p>"
    externe = _OAUTH_EXTERNE.pop(state, None) if state else None
    if externe and time.time() - externe["quand"] <= 600:
        # flux navigateur système : pas de cookie ici → l'état porte le compte cible
        set_user(externe["user"])
        _v = externe["verifier"]
    elif code and state == session.get("tt_state"):
        # flux navigateur de l'app : le cookie de session identifie le compte.
        # ⚠️ endpoint PUBLIC → before_request ne fait PAS set_user : on le fait ICI,
        # sinon les jetons partiraient dans _defaut (bug latent depuis le multi-comptes).
        if not session.get("user"):
            return "<h2>Session expirée</h2><p>Reconnecte-toi au dashboard puis relance.</p>", 400
        set_user(session["user"])
        _v = session.get("tt_verifier", "")
    else:
        return "<h2>État invalide</h2><p>Relance la connexion depuis le dashboard.</p>", 400
    try:
        tiktok_api.exchange_code(code, _v)
        session.pop("tt_verifier", None)
        session.pop("tt_state", None)
        journal.log("tiktok_connecte")
    except Exception as e:
        return f"<pre style='padding:24px'>Échec de connexion : {e}</pre>", 400
    if externe:
        return ("<h2 style='font-family:sans-serif'>✅ Compte TikTok connecté</h2>"
                "<p style='font-family:sans-serif'>Tu peux fermer cet onglet et "
                "<b>revenir dans la fenêtre ClipMinute</b>.</p>")
    return ("<h2 style='font-family:sans-serif'>✅ Compte TikTok connecté</h2>"
            "<p style='font-family:sans-serif'>Tu peux fermer cet onglet et revenir au dashboard.</p>"
            "<script>setTimeout(function(){window.location='/'},1500)</script>")


@app.post("/api/tiktok/deconnecter")
def api_tiktok_deconnecter():
    tiktok_api.deconnecter()
    return jsonify({"ok": True})


@app.get("/api/tiktok/creator")
def api_tiktok_creator():
    try:
        return jsonify(tiktok_api.creator_info())
    except Exception as e:
        return jsonify({"erreur": str(e)[:300]}), 502


@app.post("/api/tiktok/publier")
def api_tiktok_publier():
    """L'upload TikTok (lent) tourne en TÂCHE DE FOND : le popup se ferme aussitôt,
    l'envoi s'affiche dans « Tâches » avec sa progression."""
    d = request.get_json(force=True)
    fichier = Path(d.get("fichier", "")).name
    if not (QUEUE / fichier).exists():
        return jsonify({"erreur": "Clip introuvable."}), 404
    mode = "direct" if d.get("mode") == "direct" else "draft"
    args = ["--fichier", fichier, "--mode", mode, "--titre", d.get("titre") or ""]
    if mode == "direct":
        args += ["--privacy", d.get("privacy") or "SELF_ONLY"]
        if d.get("duet"):
            args.append("--duet")
        if d.get("stitch"):
            args.append("--stitch")
        if d.get("comment", True):
            args.append("--comment")
    label = (f"TikTok — {'publication' if mode == 'direct' else 'brouillon'} — "
             f"{fichier.split('_', 2)[-1][:32]}")
    return jsonify({"job": _start_job(args, label, script="publier_tiktok.py")})


@app.get("/api/rapports")
def api_rapports():
    dossier = APP / "rapports"
    if not dossier.exists():
        return jsonify([])
    return jsonify(sorted((p.name for p in dossier.glob("*.md")), reverse=True))


@app.get("/rapports/<nom>")
def rapport(nom):
    p = APP / "rapports" / Path(nom).name
    if not p.exists():
        return "Introuvable", 404
    # Servi en TEXTE BRUT : un rapport peut contenir du contenu tiers (titres de clips) ;
    # l'injecter dans du HTML permettrait un XSS dans la session de l'utilisateur.
    return Response(p.read_text(encoding="utf-8"),
                    mimetype="text/plain; charset=utf-8")


@app.post("/api/decouper")
def api_decouper():
    if not abonnement.feature(_tier(), "youtube"):
        return jsonify({"erreur": "La découpe de vidéos longues / YouTube est réservée au niveau Studio.",
                        "upgrade": True}), 402
    f = request.files.get("video")
    url = (request.form.get("url") or "").strip()
    if (not f or not f.filename) and not url:
        return jsonify({"erreur": "Fournis un fichier vidéo OU un lien YouTube."}), 400
    duree = int(request.form.get("duree") or 60)
    if f and f.filename:
        titre = (request.form.get("titre") or Path(f.filename).stem).strip()
        dest = _sources() / f"{time.strftime('%Y%m%d_%H%M%S')}_{Path(f.filename).name}"
        f.save(dest)
        args = ["--mode", "decoupe", "--video", str(dest), "--titre", titre, "--duree", str(duree)]
        source = Path(f.filename).name
    else:
        titre = (request.form.get("titre") or "").strip()
        args = ["--mode", "decoupe", "--url", url, "--duree", str(duree)]
        if titre:
            args += ["--titre", titre]
        source = url
    best_of = request.form.get("best_of")
    if best_of:
        args += ["--best-of", str(int(best_of))]
        label = f"Découpe — {source} ({best_of} meilleurs moments)"
    else:
        label = f"Découpe — {source} (parties de {duree} s)"
    return jsonify({"job": _start_job(args, label)})


@app.get("/api/jobs")
def api_jobs():
    return jsonify([_job_state(jid, j) for jid, j in sorted(JOBS.items(), reverse=True)
                    if j.get("uid") == current_uid()])  # seulement SES tâches


@app.get("/api/historique")
def api_historique():
    items = []
    for meta_path in QUEUE.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        mp4 = meta_path.with_suffix(".mp4")
        if not mp4.exists():
            continue
        meta["taille_mo"] = round(mp4.stat().st_size / 1024 / 1024, 1)
        # re-roll : drapeau léger + sujet, sans renvoyer la grosse recette au front
        reroll = meta.get("reroll") or {}
        meta["reroll_dispo"] = bool(reroll)
        meta["sujet_reroll"] = reroll.get("sujet") or (meta.get("adn") or {}).get("sujet") or ""
        meta.pop("reroll", None)
        items.append(meta)
    items.sort(key=lambda m: m.get("cree_le", ""), reverse=True)
    return jsonify(items)


@app.post("/api/reroll/fonds")
def api_reroll_fonds():
    """Re-roll « autres fonds » : régénère les vidéos de fond en gardant voix + sous-titres.
    Lance un job court (pas de synthèse vocale) qui remplace le clip en file."""
    nom = Path(request.get_json(force=True).get("fichier", "")).name
    if not nom.endswith(".mp4"):
        return jsonify({"erreur": "Nom de fichier invalide."}), 400
    try:
        meta = publish.lire_meta(nom)
    except (OSError, json.JSONDecodeError):
        return jsonify({"erreur": "Clip introuvable."}), 404
    if not meta.get("reroll"):
        return jsonify({"erreur": "Fonds non rejouables : la recette a expiré (> 24 h) ou c'est une série. Regénère le clip."}), 409
    args = ["--mode", "reroll-fonds", "--fichier", nom]
    return jsonify({"job": _start_job(args, f"Autres fonds — {meta.get('titre', nom)[:38]}")})


@app.post("/api/supprimer")
def api_supprimer():
    nom = Path(request.get_json(force=True).get("fichier", "")).name
    if not nom.endswith(".mp4"):
        return jsonify({"erreur": "Nom de fichier invalide."}), 400
    for p in (QUEUE / nom, (QUEUE / nom).with_suffix(".json"), _thumbs() / (nom + ".jpg")):
        if p.exists():
            p.unlink()
    return jsonify({"ok": True})


@app.get("/media/<nom>")
def media(nom):
    p = QUEUE / Path(nom).name
    if not p.exists():
        return "Introuvable", 404
    return send_file(p, conditional=True)


@app.get("/telecharger/<nom>")
def telecharger(nom):
    p = QUEUE / Path(nom).name
    if not p.exists():
        return "Introuvable", 404
    return send_file(p, as_attachment=True, download_name=Path(nom).name)


@app.post("/api/marquer-publie")
def api_marquer_publie():
    fichier = Path(request.get_json(force=True).get("fichier", "")).name
    try:
        publish.marquer_publie_manuel(fichier)
    except FileNotFoundError:
        return jsonify({"erreur": "Clip introuvable."}), 404
    from pipeline import nettoyage
    lib = nettoyage.liberer_espace(fichier)  # demande Alex : le clic libère le disque
    return jsonify({"ok": True, **lib})


@app.get("/thumb/<nom>")
def thumb(nom):
    nom = Path(nom).name
    jpg = _thumbs() / (nom + ".jpg")
    src = QUEUE / nom
    if not jpg.exists():
        if not src.exists():
            return "Introuvable", 404
        subprocess.run(
            [ffmpeg_exe(), "-y", "-ss", "1", "-i", str(src), "-frames:v", "1",
             "-vf", "scale=270:-2", str(jpg)],
            capture_output=True,
        )
    if not jpg.exists():
        return "Miniature impossible", 500
    return send_file(jpg)


if __name__ == "__main__":
    # Verrou anti-double-instance : si le port 5877 répond déjà, un autre dashboard
    # tourne. On s'arrête au lieu d'ouvrir une 2e instance qui écrirait sur les MÊMES
    # bases SQLite (cause de stats qui « clignotent »/semblent réinitialisées).
    import socket
    _sonde = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _sonde.settimeout(1.5)
    _occupe = _sonde.connect_ex(("127.0.0.1", 5877)) == 0
    _sonde.close()
    if _occupe:
        print("CLIPFORGE dashboard déjà en service sur http://127.0.0.1:5877 — "
              "ce lancement en double s'arrête (évite deux instances sur la même base).")
        raise SystemExit(0)
    print("CLIPFORGE dashboard -> http://127.0.0.1:5877")
    app.run(host="127.0.0.1", port=5877, threaded=True)
