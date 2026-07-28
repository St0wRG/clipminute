"""Assemble le payload de l'installeur ClipMinute (app Desktop distribuée).

Usage :  venv\\Scripts\\python.exe installer\\build_installer.py
Produit : installer\\build\\payload\\  (ce que Inno Setup déploiera tel quel)

Étapes (voir installer/PLAN_BUILD.md) :
  1. Python 3.12 embeddable  ->  payload/python/  (+ patch ._pth, + pip, + deps)
  2. Code de l'app           ->  payload/app/     (SANS données perso, config ASSAINIE)
  3. ffmpeg essentials       ->  payload/ffmpeg/
  4. DLL MSVC app-local      ->  payload/python/
  5. launcher.pyw + icône    ->  payload/

Les téléchargements sont mis en cache dans installer/cache/ (relance rapide).
"""
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ICI = Path(__file__).resolve().parent          # .../app/installer
APP = ICI.parent                                # .../app
BUILD = ICI / "build"
PAYLOAD = BUILD / "payload"
CACHE = ICI / "cache"

PY_VERSION = "3.12.10"
VERSION = (Path(__file__).resolve().parents[1] / "version.txt").read_text(encoding="ascii").strip()
URL_PYTHON = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
URL_GETPIP = "https://bootstrap.pypa.io/get-pip.py"
URL_FFMPEG = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

# --- code de l'app : quoi embarquer -----------------------------------------
FICHIERS_RACINE = [
    "dashboard.py", "run_pipeline.py", "publier_tiktok.py", "scheduler.py",
    "version.txt",   # source de vérité du système de mise à jour
]
DOSSIERS = ["pipeline", "templates", "static"]  # code + JS partagé (barre de titre fenêtre)
# assets : on embarque le nécessaire produit, JAMAIS les caches ni le perso
ASSETS_OK = ["musiques", "sfx", "backgrounds"]  # backgrounds = dossier vide (fonds perso de l'utilisateur)
EXCLUS_PARTOUT = {"__pycache__", ".git"}


def telecharger(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [cache] {dest.name}")
        return dest
    print(f"  [dl] {url}")
    # curl.exe d'abord (magasin de certificats Windows/schannel : évite les soucis
    # de bundle CA Python, vécu avec gyan.dev) ; urllib en repli.
    r = subprocess.run(["curl.exe", "-fsSL", "--max-time", "600", "-o", str(dest), url],
                       capture_output=True, text=True)
    if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
        return dest
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)
    return dest


def run(cmd: list, **kw) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", **kw)
    if r.returncode != 0:
        raise RuntimeError(f"échec {cmd[0]} : {r.stderr[-2000:]}")


def etape_python() -> Path:
    """Python embeddable + pip + dépendances, dans payload/python/."""
    print("[1/5] Python embeddable + dépendances")
    py_dir = PAYLOAD / "python"
    zip_py = telecharger(URL_PYTHON, CACHE / f"python-{PY_VERSION}-embed-amd64.zip")
    if not (py_dir / "python.exe").exists():
        py_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_py) as z:
            z.extractall(py_dir)

    # patch ._pth : activer site + site-packages + ..\app  (ASCII sans BOM)
    pth = py_dir / "python312._pth"
    pth.write_text("python312.zip\n.\nLib\\site-packages\n..\\app\nimport site\n",
                   encoding="ascii")

    py = py_dir / "python.exe"
    # pip (l'embeddable n'a pas ensurepip)
    r = subprocess.run([str(py), "-m", "pip", "--version"], capture_output=True)
    if r.returncode != 0:
        getpip = telecharger(URL_GETPIP, CACHE / "get-pip.py")
        run([str(py), str(getpip), "--no-warn-script-location"])

    # requirements : gel du venv de dev, sans l'outillage
    gel = subprocess.run([str(APP / "venv/Scripts/python.exe"), "-m", "pip", "freeze"],
                         capture_output=True, text=True).stdout
    lignes = [l for l in gel.splitlines()
              if l and not l.startswith(("pip==", "setuptools==", "wheel==", "gunicorn=="))]
    lignes.append("pywebview==5.4")   # fenêtre native (WebView2) — pas dans le venv de dev
    req = BUILD / "requirements-dist.txt"
    req.write_text("\n".join(lignes) + "\n", encoding="ascii")
    print(f"  [pip] {len(lignes)} paquets vers l'embarqué (long la 1re fois)…")
    run([str(py), "-m", "pip", "install", "--no-warn-script-location",
         "--no-cache-dir", "-q", "-r", str(req)])
    # vérification d'import des modules critiques
    run([str(py), "-c", "import flask, faster_whisper, av, anthropic, edge_tts; print('ok')"])
    print("  imports critiques : OK")
    return py_dir


def config_assainie() -> dict:
    """config.json modèle SANS aucun secret/jeton personnel (build reproductible)."""
    cfg = json.loads((APP / "config.json").read_text(encoding="utf-8"))
    tt = cfg.get("tiktok")
    if isinstance(tt, dict):
        for k in ("access_token", "refresh_token", "open_id",
                  "expires_at", "refresh_expires_at", "client_key", "client_secret"):
            tt.pop(k, None)
    pub = cfg.get("publication")
    if isinstance(pub, dict):
        for k in ("api_key", "zernio_account_id", "zernio_comptes"):
            pub.pop(k, None)
    for k in ("anthropic_api_key", "pexels_api_key", "pixabay_api_key"):
        cfg.pop(k, None)
    return cfg


def etape_app() -> None:
    """Code de l'app -> payload/app/ (sans données perso)."""
    print("[2/5] Code de l'app (assaini)")
    dest = PAYLOAD / "app"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for f in FICHIERS_RACINE:
        shutil.copy2(APP / f, dest / f)
    for d in DOSSIERS:
        shutil.copytree(APP / d, dest / d,
                        ignore=shutil.ignore_patterns(*EXCLUS_PARTOUT))
    (dest / "assets").mkdir()
    for a in ASSETS_OK:
        src = APP / "assets" / a
        if src.exists():
            shutil.copytree(src, dest / "assets" / a,
                            ignore=shutil.ignore_patterns(*EXCLUS_PARTOUT))
        else:
            (dest / "assets" / a).mkdir()
    # config modèle assainie (UTF-8 sans BOM)
    (dest / "config.json").write_text(
        json.dumps(config_assainie(), ensure_ascii=False, indent=2), encoding="utf-8")
    # garde-fou : aucun secret ne doit partir dans le payload
    contenu = (dest / "config.json").read_text(encoding="utf-8")
    for interdit in ("sk-ant-", "access_token", "client_secret"):
        if interdit in contenu:
            raise RuntimeError(f"SECRET DÉTECTÉ dans le config embarqué ({interdit}) — build annulé")
    print("  config embarquée : assainie (aucun secret)")


def etape_ffmpeg() -> None:
    print("[3/5] ffmpeg essentials")
    dest = PAYLOAD / "ffmpeg"
    if (dest / "ffmpeg.exe").exists() and (dest / "ffprobe.exe").exists():
        print("  [cache] déjà en place")
        return
    zip_ff = telecharger(URL_FFMPEG, CACHE / "ffmpeg-release-essentials.zip")
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_ff) as z:
        for n in z.namelist():
            base = n.rsplit("/", 1)[-1]
            if base in ("ffmpeg.exe", "ffprobe.exe", "LICENSE"):
                with z.open(n) as src, open(dest / (base if base != "LICENSE" else "LICENCE-ffmpeg.txt"), "wb") as out:
                    shutil.copyfileobj(src, out)
    if not (dest / "ffmpeg.exe").exists():
        raise RuntimeError("ffmpeg.exe introuvable dans l'archive")
    print("  ffmpeg.exe + ffprobe.exe extraits")


def etape_dll_msvc() -> None:
    """DLL MSVC app-local (sinon : DLL load failed importing ctranslate2 sur PC propre)."""
    print("[4/5] DLL MSVC app-local")
    sys32 = Path("C:/Windows/System32")
    for dll in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        src = sys32 / dll
        if not src.exists():
            raise RuntimeError(f"{dll} introuvable dans System32")
        shutil.copy2(src, PAYLOAD / "python" / dll)
    print("  msvcp140 + vcruntime140(+_1) copiées à côté de python.exe")


LAUNCHER = '''"""ClipMinute — lanceur (ClipMinute.exe). Serveur local en thread + FENÊTRE NATIVE.

L'interface s'ouvre dans une vraie fenêtre d'application (pywebview / WebView2, moteur
fourni avec Windows) appartenant à NOTRE processus : icône ClipMinute dans la barre des
tâches, aucun navigateur visible. Fermer la fenêtre = quitter le programme (les rendus
en cours, lancés en sous-processus détachés, continuent). Replis si WebView2 manque :
fenêtre --app Edge/Chrome, puis navigateur par défaut.
"""
import ctypes, os, socket, subprocess, sys, threading, time, webbrowser

BASE = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(BASE).lower() == "python":   # lancé par python\\ClipMinute.exe
    BASE = os.path.dirname(BASE)
os.chdir(os.path.join(BASE, "app"))

FF = os.path.join(BASE, "ffmpeg")
os.environ["PATH"] = FF + os.pathsep + os.environ.get("PATH", "")
os.environ["PYTHONIOENCODING"] = "utf-8"

sys.path.insert(0, os.path.join(BASE, "app"))

PORT = 5877
URL = "http://127.0.0.1:%d/" % PORT

try:    # identité propre dans la barre des tâches (icône/groupement ClipMinute)
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ClipMinute.App")
except Exception:
    pass

import logging
logging.basicConfig(filename=os.path.join(BASE, "clipminute.log"), level=logging.WARNING)


class ApiFenetre:
    """Boutons de la barre de titre maison (frameless) — appelés depuis le JS des pages."""
    def __init__(self):
        self._agrandie = False

    def reduire(self):
        _FENETRE[0].minimize()

    def agrandir(self):
        (_FENETRE[0].restore if self._agrandie else _FENETRE[0].maximize)()
        self._agrandie = not self._agrandie

    def fermer(self):
        _FENETRE[0].destroy()


_FENETRE = []
_SPLASH_VU = threading.Event()   # posé quand le splash est réellement peint à l'écran


def _splash_html():
    """HTML du splash chargé EN MÉMOIRE. On n'utilise PLUS d'URL file:/// : WebView2
    la rend en page vide dans une fenêtre frameless -> le splash n'apparaissait jamais."""
    try:
        with open(os.path.join(BASE, "app", "static", "splash.html"), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        logging.exception("splash.html illisible")
        return None


def fenetre_native(url_initiale=None) -> bool:
    """Fenêtre d'application SANS cadre Windows (frameless) : l'app dessine sa propre
    barre de titre (static/fenetre.js). Bloque jusqu'à la fermeture.
    url_initiale=None -> on ouvre sur le splash (HTML en mémoire)."""
    try:
        import webview
        commun = dict(width=1280, height=860, min_size=(980, 640), frameless=True,
                      easy_drag=False, js_api=ApiFenetre(), text_select=True, zoomable=True)
        html = _splash_html() if url_initiale is None else None
        if html is not None:
            f = webview.create_window("ClipMinute", html=html, **commun)
        else:
            f = webview.create_window("ClipMinute", url_initiale or URL, **commun)
        _FENETRE.append(f)
        # le splash n'est VISIBLE qu'une fois WebView2 initialisé (2-3 s à froid) :
        # c'est de CE moment que part le minimum d'animation, sinon on bascule avant
        # que l'utilisateur ait rien vu.
        try:
            f.events.loaded += (lambda *a: _SPLASH_VU.set())
        except Exception:
            _SPLASH_VU.set()
        webview.start()
        return True
    except Exception:
        logging.exception("fenêtre native indisponible -> repli navigateur")
        return False


def fenetre_edge() -> bool:
    """Repli : fenêtre --app d'Edge/Chrome (sans barre d'adresse). Ne bloque pas."""
    candidats = [
        os.path.expandvars(r"%ProgramFiles(x86)%\\Microsoft\\Edge\\Application\\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\\Microsoft\\Edge\\Application\\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\\Google\\Chrome\\Application\\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\\Google\\Chrome\\Application\\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\\Google\\Chrome\\Application\\chrome.exe"),
    ]
    for exe in candidats:
        if os.path.exists(exe):
            subprocess.Popen([exe, "--app=" + URL,
                              "--user-data-dir=" + os.path.join(BASE, "fenetre"),
                              "--no-first-run", "--no-default-browser-check",
                              "--window-size=1280,860"])
            return True
    return False


def attendre_serveur(timeout_s: float = 25.0) -> bool:
    fin = time.time() + timeout_s
    while time.time() < fin:
        try:
            with socket.create_connection(("127.0.0.1", PORT), 0.25):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def ouvrir_ui(url_initiale=None):
    """Fenêtre native si possible ; sinon repli SANS fermer le serveur."""
    if fenetre_native(url_initiale):
        return                                   # fenêtre fermée -> on laisse sortir
    attendre_serveur()                           # les replis n'ont pas d'écran d'attente
    if not fenetre_edge():
        webbrowser.open(URL)
    while True:                                  # replis non bloquants : garder le serveur en vie
        time.sleep(3600)


def deja_lance():
    try:
        with socket.create_connection(("127.0.0.1", PORT), 0.4):
            return True
    except OSError:
        return False


def serveur():
    try:
        from dashboard import app
        app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)
    except Exception:
        logging.exception("échec du serveur")


def _basculer_quand_pret():
    """Splash -> app quand LES DEUX sont prêts : serveur en ligne ET splash réellement
    affiché depuis 2,4 s (WebView2 met 2-3 s à s'initialiser : compter depuis le
    lancement du processus ferait disparaître l'animation avant d'être vue)."""
    attendre_serveur(30.0)
    _SPLASH_VU.wait(20.0)
    time.sleep(2.4)
    try:
        _FENETRE[0].load_url(URL)
    except Exception:
        pass


if not deja_lance():                          # démarrage à froid : lancer le serveur
    threading.Thread(target=serveur, daemon=True).start()
threading.Thread(target=_basculer_quand_pret, daemon=True).start()
ouvrir_ui()                                   # splash (HTML en mémoire) à CHAQUE lancement
# fenêtre native fermée -> fin du programme : le serveur (thread daemon) s'arrête avec,
# le port est libéré ; les rendus en cours (sous-processus détachés) se terminent seuls.
'''


def etape_launcher_icone() -> None:
    print("[5/5] launcher + icône")
    (PAYLOAD / "launcher.pyw").write_text(LAUNCHER, encoding="utf-8")

    # icône ClipMinute : cadran sombre, aiguille "minute" lime — générée avec Pillow
    from PIL import Image, ImageDraw
    LIME, INK = (182, 255, 58, 255), (14, 17, 23, 255)
    tailles = [256, 128, 64, 48, 32, 16]
    im = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([8, 8, 248, 248], radius=56, fill=INK)
    d.ellipse([48, 48, 208, 208], outline=LIME, width=14)      # cadran
    d.line([128, 128, 128, 68], fill=LIME, width=16)           # aiguille minute (12 h)
    d.line([128, 128, 172, 150], fill=(234, 238, 244, 255), width=10)  # petite aiguille
    d.ellipse([118, 118, 138, 138], fill=LIME)                 # axe
    im.save(PAYLOAD / "clipminute.ico",
            sizes=[(t, t) for t in tailles])
    print("  launcher.pyw + clipminute.ico écrits")

    # ClipMinute.exe : copie de pythonw avec NOTRE icône et nos métadonnées (rcedit).
    # Placé DANS python\ (pythonw exige python312.dll à côté de lui). C'est lui que
    # visent les raccourcis -> barre des tâches et gestionnaire de tâches = ClipMinute.
    rcedit = telecharger(
        "https://github.com/electron/rcedit/releases/latest/download/rcedit-x64.exe",
        CACHE / "rcedit-x64.exe")
    stub = PAYLOAD / "python" / "ClipMinute.exe"
    shutil.copy2(PAYLOAD / "python" / "pythonw.exe", stub)
    run([str(rcedit), str(stub),
         "--set-icon", str(PAYLOAD / "clipminute.ico"),
         "--set-version-string", "ProductName", "ClipMinute",
         "--set-version-string", "FileDescription", "ClipMinute",
         "--set-version-string", "CompanyName", "ClipMinute",
         "--set-file-version", VERSION, "--set-product-version", VERSION])
    print("  ClipMinute.exe (icône + métadonnées) créé")


def main() -> int:
    print(f"Build du payload ClipMinute -> {PAYLOAD}")
    PAYLOAD.mkdir(parents=True, exist_ok=True)
    etape_python()
    etape_app()
    etape_ffmpeg()
    etape_dll_msvc()
    etape_launcher_icone()
    total = sum(f.stat().st_size for f in PAYLOAD.rglob("*") if f.is_file())
    print(f"\nPayload prêt : {total / 1048576:.0f} Mo — étape suivante : ISCC clipminute.iss")
    return 0


if __name__ == "__main__":
    sys.exit(main())
