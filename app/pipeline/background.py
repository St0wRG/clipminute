"""Fond vidéo 1080x1920 — multi-sources avec SÉLECTION AU MEILLEUR SCORE CONTEXTUEL.

Ordre par scène :
  1) "archive" (histoires vraies) : VRAIE image de l'affaire via Wikimedia Commons (sans clé),
     animée en Ken Burns — l'authenticité bat le stock générique.
  2) Vidéos stock : candidats Pexels + Pixabay fusionnés, chacun SCORÉ contre la requête ET le
     texte de la scène (recouvrement lexical du slug/tags) + durée suffisante + résolution.
     Le meilleur gagne (fini le random parmi 20) ; jamais deux fois le même plan par rendu.
  3) Photo stock (Pixabay) animée en Ken Burns si aucune vidéo ne colle.
  4) mp4 locaux assets/backgrounds  5) cache disque  6) dégradé animé — jamais bloquant.
Caches : résultats de recherche 1 h (quotas) + fichiers téléchargés (purgés par nettoyage.py).
"""
import random
import re
import time
from pathlib import Path

from .common import APP, BACKGROUNDS, CONFIG, ffmpeg_exe, run

PEXELS_CACHE = APP / "assets" / "backgrounds_pexels"

_CACHE_RECHERCHES: dict[str, tuple[float, list]] = {}
TTL_RECHERCHE = 3600.0
_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")}

KEN_BURNS = ("zoompan=z='min(1+0.0009*on,1.13)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
             ":d=1:s=1080x1920:fps=30")


# ---------- utilitaires score ----------
def _mots(s: str) -> set[str]:
    return set(re.findall(r"[a-zà-ÿ0-9]{3,}", (s or "").lower()))


def _score(requete: str, contexte: str, descriptif: str,
           duree_ok: bool, haute_res: bool) -> float:
    """Plus le descriptif du candidat recouvre la requête (fort) et le texte de la scène
    (faible), plus il colle au contexte. Bonus durée/résolution."""
    d = _mots(descriptif)
    if not d:
        return 0.0
    s = 3.0 * len(_mots(requete) & d) + 0.5 * len(_mots(contexte) & d)
    if duree_ok:
        s += 1.0
    if haute_res:
        s += 0.5
    return s


def _cache_recherche(cle: str, fabrique):
    t, val = _CACHE_RECHERCHES.get(cle, (0.0, None))
    if val is not None and time.time() - t < TTL_RECHERCHE:
        return val
    val = fabrique()
    _CACHE_RECHERCHES[cle] = (time.time(), val)
    return val


# ---------- candidats vidéo (Pexels + Pixabay), normalisés ----------
def _candidats_pexels(query: str) -> list[dict]:
    key = CONFIG.get("pexels_api_key")
    if not key:
        return []

    def _fetch():
        import requests

        r = requests.get("https://api.pexels.com/videos/search",
                         headers={"Authorization": key},
                         params={"query": query, "orientation": "portrait", "per_page": 20},
                         timeout=30)
        r.raise_for_status()
        out = []
        for v in r.json().get("videos", []):
            files = [f for f in v.get("video_files", [])
                     if f.get("file_type") == "video/mp4" and (f.get("height") or 0) >= 1080]
            if not files:
                continue
            best = min(files, key=lambda f: f["height"])
            # le slug de l'URL décrit le contenu : "aerial-view-of-a-lighthouse-on-a-cliff"
            slug = (v.get("url") or "").rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
            out.append({"cid": f"px{v['id']}", "descriptif": slug,
                        "duree": float(v.get("duration") or 0),
                        "haute_res": best["height"] >= 1440, "lien": best["link"]})
        return out

    try:
        return _cache_recherche(f"px:{query}", _fetch)
    except Exception:
        return []


def _candidats_pixabay(query: str) -> list[dict]:
    key = CONFIG.get("pixabay_api_key")
    if not key:
        return []

    def _fetch():
        import requests

        r = requests.get("https://pixabay.com/api/videos/",
                         params={"key": key, "q": query, "per_page": 20, "safesearch": "true"},
                         timeout=30)
        r.raise_for_status()
        out = []
        for v in r.json().get("hits", []):
            rendus = v.get("videos") or {}
            best = None
            for taille in ("large", "medium"):
                x = rendus.get(taille)
                if x and (x.get("height", 0) >= 1080 or x.get("width", 0) >= 1080):
                    best = x
                    break
            if not best:
                continue
            out.append({"cid": f"pb{v['id']}", "descriptif": v.get("tags", ""),
                        "duree": float(v.get("duration") or 0),
                        "haute_res": best.get("height", 0) >= 1440, "lien": best["url"]})
        return out

    try:
        return _cache_recherche(f"pb:{query}", _fetch)
    except Exception:
        return []


def _telecharger(lien: str, dest: Path) -> Path | None:
    import requests

    try:
        PEXELS_CACHE.mkdir(parents=True, exist_ok=True)
        with requests.get(lien, stream=True, timeout=120, headers=_UA) as dl:
            dl.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in dl.iter_content(1024 * 512):
                    f.write(chunk)
        return dest
    except Exception:
        dest.unlink(missing_ok=True)
        return None


def choisir_fond_video(query: str, duree: float, exclus: set,
                       contexte: str = "") -> Path | None:
    """Le candidat (Pexels ∪ Pixabay) au MEILLEUR score contextuel, hors déjà-utilisés."""
    cands = _candidats_pexels(query) + _candidats_pixabay(query)
    cands = [c for c in cands if c["cid"] not in exclus]
    if not cands:
        return None
    for c in cands:
        c["score"] = _score(query, contexte, c["descriptif"], c["duree"] >= duree, c["haute_res"])
    cands.sort(key=lambda c: c["score"], reverse=True)
    for c in cands[:5]:  # meilleur d'abord ; on descend si le téléchargement échoue
        dest = PEXELS_CACHE / f"{c['cid']}.mp4"
        if dest.exists() or _telecharger(c["lien"], dest):
            exclus.add(c["cid"])
            return dest
    return None


# ---------- archives réelles (Wikimedia Commons, sans clé) ----------
def _photo_archive(requete: str) -> tuple[Path, str] | None:
    """(fichier, crédit court) — la vraie image de l'affaire, choisie par pertinence Commons."""
    import html as _html

    import requests

    def _fetch():
        r = requests.get("https://commons.wikimedia.org/w/api.php", params={
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": requete, "gsrlimit": 6, "gsrnamespace": 6,
            "prop": "imageinfo", "iiprop": "url|extmetadata|mime", "iiurlwidth": 1440,
        }, headers=_UA, timeout=20)
        r.raise_for_status()
        return list(((r.json().get("query") or {}).get("pages") or {}).values())

    try:
        pages = _cache_recherche(f"wm:{requete}", _fetch)
    except Exception:
        return None
    for p in sorted(pages, key=lambda x: x.get("index", 99)):  # ordre de pertinence Commons
        ii = (p.get("imageinfo") or [{}])[0]
        if not str(ii.get("mime", "")).startswith("image/"):
            continue
        lien = ii.get("thumburl") or ii.get("url")
        if not lien:
            continue
        dest = PEXELS_CACHE / f"wm{p['pageid']}.jpg"
        if dest.exists() or _telecharger(lien, dest):
            meta = ii.get("extmetadata") or {}
            lic = (meta.get("LicenseShortName") or {}).get("value", "libre")
            artiste = re.sub(r"<[^>]+>", "", (meta.get("Artist") or {}).get("value", "")).strip()
            credit = f"{artiste[:40]} ({lic})" if artiste else f"Wikimedia Commons ({lic})"
            return dest, credit
    return None


def _candidat_photo_pixabay(query: str) -> str | None:
    key = CONFIG.get("pixabay_api_key")
    if not key:
        return None

    def _fetch():
        import requests

        r = requests.get("https://pixabay.com/api/", params={
            "key": key, "q": query, "per_page": 10, "orientation": "vertical",
            "image_type": "photo", "safesearch": "true", "min_height": 1280}, timeout=20)
        r.raise_for_status()
        return [h.get("largeImageURL") for h in r.json().get("hits", []) if h.get("largeImageURL")]

    try:
        liens = _cache_recherche(f"pbph:{query}", _fetch)
        return liens[0] if liens else None
    except Exception:
        return None


def _prepare_photo(img: Path, duration: float, out_path: Path) -> Path:
    """Photo -> segment vidéo 9:16 animé (Ken Burns) : une image fixe qui vit."""
    run(ffmpeg_exe(), "-y", "-loop", "1", "-framerate", "30", "-i", str(img),
        "-t", f"{duration:.2f}",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,"
               f"crop=1080:1920,{KEN_BURNS}",
        "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "21", str(out_path))
    return out_path


# ---------- assemblage ----------
def _prepare(src: Path, duration: float, out_path: Path) -> Path:
    """Boucle/coupe la source à la durée voulue, recadrée en 9:16, sans audio."""
    run(
        ffmpeg_exe(), "-y", "-stream_loop", "-1", "-i", str(src),
        "-t", f"{duration:.2f}",
        "-vf", "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale=1080:1920,setsar=1",
        "-an", "-r", "30", "-c:v", "libx264", "-preset", "fast", "-crf", "21",
        str(out_path),
    )
    return out_path


def _segment_degrade(duree: float, seg: Path) -> Path:
    run(ffmpeg_exe(), "-y", "-f", "lavfi",
        "-i", f"gradients=size=1080x1920:speed=0.02:nb_colors=3:duration={duree:.2f}",
        "-r", "30", "-c:v", "libx264", "-preset", "fast", "-crf", "23", str(seg))
    return seg


def fond_scenes(scenes: list[dict], out_path: Path,
                recherches_secours: list[str] | None = None,
                credits: list[str] | None = None) -> Path:
    """Fond qui suit l'histoire : un segment par scène [{recherche, duree, texte?, archive?}].
    Les crédits des archives réelles utilisées sont ajoutés à `credits` (pour la caption)."""
    segments = []
    utilises: set = set()
    for i, scene in enumerate(scenes):
        seg = out_path.parent / f"{out_path.stem}_s{i}.mp4"
        fait, etiquette = False, scene["recherche"]
        if scene.get("archive"):  # 1) la VRAIE image de l'affaire
            ph = _photo_archive(scene["archive"])
            if ph:
                _prepare_photo(ph[0], scene["duree"], seg)
                if credits is not None and ph[1] not in credits:
                    credits.append(ph[1])
                fait, etiquette = True, f"ARCHIVE {scene['archive']}"
        if not fait:  # 2) meilleure vidéo stock (Pexels ∪ Pixabay, score contextuel)
            src = (choisir_fond_video(scene["recherche"], scene["duree"], utilises,
                                      scene.get("texte", ""))
                   or choisir_fond_video(random.choice(recherches_secours or ["dark ambient"]),
                                         scene["duree"], utilises))
            if src:
                _prepare(src, scene["duree"], seg)
                fait = True
        if not fait:  # 3) photo stock animée
            lien = _candidat_photo_pixabay(scene["recherche"])
            if lien:
                dest = PEXELS_CACHE / f"pbph{abs(hash(lien)) % 10**8}.jpg"
                if dest.exists() or _telecharger(lien, dest):
                    _prepare_photo(dest, scene["duree"], seg)
                    fait, etiquette = True, f"photo {scene['recherche']}"
        if not fait:  # 4) cache local / 5) dégradé
            caches = [p for p in PEXELS_CACHE.glob("*.mp4") if p.stem not in utilises]
            if caches:
                src = random.choice(caches)
                utilises.add(src.stem)
                _prepare(src, scene["duree"], seg)
            else:
                _segment_degrade(scene["duree"], seg)
        segments.append(seg)
        print(f"  scène {i + 1}/{len(scenes)} : {etiquette} ({scene['duree']:.0f} s)", flush=True)
    liste = out_path.parent / f"{out_path.stem}_concat.txt"
    liste.write_text(
        "".join(f"file '{str(s).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n"
                for s in segments),
        encoding="utf-8",
    )
    run(ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", str(liste),
        "-c", "copy", str(out_path))
    for s in segments:
        s.unlink(missing_ok=True)
    liste.unlink(missing_ok=True)
    return out_path


def make_background(duration: float, out_path: Path, recherches: list[str] | None = None) -> Path:
    local = sorted(BACKGROUNDS.glob("*.mp4"))
    if local:
        return _prepare(random.choice(local), duration, out_path)
    src = choisir_fond_video(random.choice(recherches or ["satisfying", "nature"]),
                             duration, set())
    if src:
        return _prepare(src, duration, out_path)
    return _segment_degrade(duration, out_path)
