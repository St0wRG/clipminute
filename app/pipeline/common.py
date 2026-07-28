"""Chemins, config et localisation des binaires."""
import contextvars
import glob
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
BACKGROUNDS = APP / "assets" / "backgrounds"   # partagé (cache de fonds, non isolé)

# ═══════════ Multi-utilisateurs : données isolées par compte dans data/<uid>/ ═══════════
_CTX = contextvars.ContextVar("clipforge_uid", default="_defaut")
_config_cache: dict[str, dict] = {}


def uid_de(email: str | None) -> str:
    e = (email or "").strip().lower()
    return hashlib.sha1(e.encode("utf-8")).hexdigest()[:16] if e else "_defaut"


def set_user(email: str | None) -> None:
    """Fixe l'utilisateur courant (par e-mail) : toutes les données (clips, calendrier, config,
    journal, stats, sources) sont dès lors lues/écrites dans SON dossier isolé data/<uid>/."""
    _CTX.set(uid_de(email))


def current_uid() -> str:
    return _CTX.get()


def data_root() -> Path:
    """Dossier isolé de l'utilisateur courant (créé au besoin)."""
    d = APP / "data" / _CTX.get()
    d.mkdir(parents=True, exist_ok=True)
    return d


class _PathProxy:
    """queue/ et output/ résolus dans le dossier de l'utilisateur COURANT à chaque accès."""
    __slots__ = ("_sub",)

    def __init__(self, sub: str):
        self._sub = sub

    def _p(self) -> Path:
        p = data_root() / self._sub
        p.mkdir(parents=True, exist_ok=True)
        return p

    def __truediv__(self, other):
        return self._p() / other

    def __fspath__(self):
        return str(self._p())

    def __str__(self):
        return str(self._p())

    def __repr__(self):
        return f"<PathProxy data/{_CTX.get()}/{self._sub}>"

    def glob(self, pat):
        return self._p().glob(pat)

    def iterdir(self):
        return self._p().iterdir()

    def exists(self):
        return self._p().exists()

    def mkdir(self, *a, **k):
        return self._p()  # déjà créé par _p()

    @property
    def parent(self):
        return self._p().parent

    @property
    def name(self):
        return self._p().name


OUTPUT = _PathProxy("output")
QUEUE = _PathProxy("queue")


def _modele_config() -> dict:
    """Config par défaut d'un NOUVEL utilisateur : le modèle de l'app, SANS les jetons perso —
    chaque compte connecte SON propre TikTok, ne voit jamais les clés/jetons d'un autre."""
    cfg = json.loads((APP / "config.json").read_text(encoding="utf-8"))
    tt = cfg.get("tiktok")
    if isinstance(tt, dict):
        for k in ("access_token", "refresh_token", "open_id", "expires_at", "refresh_expires_at"):
            tt.pop(k, None)
    pub = cfg.get("publication")
    if isinstance(pub, dict):
        for k in ("api_key", "zernio_account_id", "zernio_comptes"):
            pub.pop(k, None)
    # clés API personnelles : jamais héritées par un nouveau compte
    for k in ("anthropic_api_key", "pexels_api_key", "pixabay_api_key"):
        cfg.pop(k, None)
    return cfg


def sauver_config() -> None:
    """Persiste la config du user courant dans son dossier (écriture atomique)."""
    uid = _CTX.get()
    if uid not in _config_cache:
        return
    p = data_root() / "config.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_config_cache[uid], ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def recharger_config() -> None:
    _config_cache.pop(_CTX.get(), None)


class _ConfigProxy:
    """Config du user courant (data/<uid>/config.json), chargée à la demande et mise en cache."""

    def _d(self) -> dict:
        uid = _CTX.get()
        if uid not in _config_cache:
            p = data_root() / "config.json"
            if p.exists():
                _config_cache[uid] = json.loads(p.read_text(encoding="utf-8"))
            else:
                _config_cache[uid] = _modele_config()
                sauver_config()
        return _config_cache[uid]

    def __getitem__(self, k):
        return self._d()[k]

    def __setitem__(self, k, v):
        self._d()[k] = v

    def __delitem__(self, k):
        del self._d()[k]

    def pop(self, k, *défaut):
        return self._d().pop(k, *défaut)

    def __contains__(self, k):
        return k in self._d()

    def __iter__(self):
        return iter(self._d())

    def get(self, k, d=None):
        return self._d().get(k, d)

    def setdefault(self, k, d):
        return self._d().setdefault(k, d)

    def keys(self):
        return self._d().keys()

    def items(self):
        return self._d().items()

    def values(self):
        return self._d().values()


CONFIG = _ConfigProxy()


def get_profil(nom: str | None = None) -> dict:
    """Retourne le profil demandé (ou celui par défaut), avec son nom inclus."""
    profils = CONFIG["profils"]
    nom = nom or CONFIG["profil_defaut"]
    if nom not in profils:
        raise RuntimeError(f"Profil inconnu : {nom} (disponibles : {', '.join(profils)})")
    return {"nom": nom, **profils[nom]}


def _find_winget_exe(name: str) -> str | None:
    # 1) build embarqué (installation distribuée ClipMinute : {install}\ffmpeg\, frère
    #    du dossier app\ — et variante APP\ffmpeg\bin\ si un jour rangé dans l'app)
    for embarque in (APP.parent / "ffmpeg" / f"{name}.exe",
                     APP / "ffmpeg" / "bin" / f"{name}.exe"):
        if embarque.exists():
            return str(embarque)
    p = shutil.which(name)
    if p:
        return p
    links = os.path.expandvars(rf"%LOCALAPPDATA%\Microsoft\WinGet\Links\{name}.exe")
    if os.path.exists(links):
        return links
    hits = glob.glob(
        os.path.expandvars(rf"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\bin\{name}.exe"),
        recursive=True,
    )
    return hits[0] if hits else None


def ffmpeg_exe() -> str:
    p = _find_winget_exe("ffmpeg")
    if not p:
        raise RuntimeError("ffmpeg introuvable — installer via: winget install -e --id Gyan.FFmpeg")
    return p


def ffprobe_exe() -> str:
    p = _find_winget_exe("ffprobe")
    if not p:
        raise RuntimeError("ffprobe introuvable")
    return p


def claude_exe() -> str:
    for name in ("claude", "claude.cmd", "claude.exe"):
        p = shutil.which(name)
        if p:
            return p
    # CLI embarquée dans l'app desktop Claude (dossier versionné -> prendre la plus récente).
    # L'environnement du Planificateur de tâches peut être amputé (%APPDATA%, USERPROFILE...) :
    # on dérive donc AUSSI la racine utilisateur depuis le chemin de l'app (C:\Users\<x>\MERIDIAN\...).
    racine_utilisateur = APP.parents[1]  # ...\MERIDIAN\90_PROJETS\CLIPFORGE -> C:\Users\Gaming
    while racine_utilisateur.parent.name.lower() != "users" and racine_utilisateur.parent != racine_utilisateur:
        racine_utilisateur = racine_utilisateur.parent
    racines = [
        os.path.expandvars(r"%APPDATA%\Claude\claude-code"),
        str(Path.home() / "AppData" / "Roaming" / "Claude" / "claude-code"),
        str(racine_utilisateur / "AppData" / "Roaming" / "Claude" / "claude-code"),
    ]
    # App desktop MSIX (Windows Store) : AppData\Roaming est VIRTUALISÉ — les processus
    # hors conteneur (Planificateur de tâches !) doivent lire le vrai chemin dans Packages.
    racines += glob.glob(str(racine_utilisateur / "AppData" / "Local" / "Packages"
                             / "Claude_*" / "LocalCache" / "Roaming" / "Claude" / "claude-code"))
    for racine in racines:
        hits = glob.glob(os.path.join(racine, "*", "claude.exe"))
        if hits:
            return max(hits)
    detail = " | ".join(f"{r} (dossier existe: {os.path.isdir(r)})" for r in racines)
    raise RuntimeError(f"CLI claude introuvable. Racines testées : {detail}")


def version_app() -> str:
    """Version installée de ClipMinute (version.txt à la racine de l'app)."""
    try:
        return (APP / "version.txt").read_text(encoding="ascii").strip()
    except OSError:
        return "0.0.0"


def _cle_anthropic() -> str:
    """Clé API Anthropic de l'utilisateur courant : Réglages (config isolée) ou variable
    d'environnement. Vide si absente."""
    return (CONFIG.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def _claude_brut(prompt: str, timeout: int) -> str:
    """Texte brut de Claude — 2 voies, dans cet ordre :
    1) clé API renseignée -> SDK anthropic direct (cas de l'app DISTRIBUÉE : chaque
       utilisateur met SA clé dans Réglages ; pas besoin de la CLI Claude Code) ;
    2) sinon CLI `claude -p` (poste du développeur, abonnement Claude ; via stdin car
       la ligne de commande Windows est limitée en longueur).
    """
    cle = _cle_anthropic()
    if cle:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("Le module 'anthropic' n'est pas installé "
                               "(pip install anthropic) — génération impossible.")
        client = anthropic.Anthropic(api_key=cle, timeout=float(timeout))
        try:
            rep = client.messages.create(
                model=CONFIG.get("anthropic_model") or "claude-sonnet-5",
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError:
            raise RuntimeError("Clé API Anthropic refusée — vérifie-la dans Réglages.")
        except anthropic.APIStatusError as e:
            raise RuntimeError(f"L'API Anthropic a répondu une erreur ({e.status_code}) — "
                               "réessaie dans un instant.")
        return "".join(b.text for b in rep.content if getattr(b, "type", "") == "text").strip()

    try:
        exe = claude_exe()
    except RuntimeError:
        raise RuntimeError("Aucune voie de génération : renseigne ta clé API Anthropic "
                           "dans Réglages (ou installe la CLI Claude Code).")
    r = subprocess.run(
        [exe, "-p"],
        input=prompt,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(f"claude -p a échoué : {r.stderr[-1000:]}")
    return r.stdout.strip()


def claude_json(prompt: str, timeout: int = 300) -> dict | list:
    """Réponse de Claude -> premier objet/tableau JSON extrait."""
    import re

    texte = _claude_brut(prompt, timeout)
    m = re.search(r"[\[{].*[\]}]", texte, re.DOTALL)
    if not m:
        raise RuntimeError(f"Pas de JSON dans la réponse de Claude : {texte[:500]}")
    return json.loads(m.group(0))


def claude_texte(prompt: str, timeout: int = 300) -> str:
    """Réponse texte brute de Claude."""
    return _claude_brut(prompt, timeout)


def run(*cmd: str) -> None:
    """Lance une commande, échoue bruyamment avec la sortie d'erreur."""
    r = subprocess.run(list(cmd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"Commande échouée ({cmd[0]}):\n{r.stderr[-3000:]}")


def media_duration(path: Path) -> float:
    r = subprocess.run(
        [ffprobe_exe(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip())


def ass_filter_path(path: Path) -> str:
    """Échappe un chemin Windows pour le filtre ass= de ffmpeg."""
    return str(path).replace("\\", "/").replace(":", "\\:")
