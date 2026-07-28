"""Téléchargement d'une vidéo (YouTube ou autre) via yt-dlp, pour le mode découpe.

⚠️ Rappel : le téléchargement de contenus protégés reste soumis au droit d'auteur —
le choix des liens relève de la responsabilité de l'utilisateur.
"""
from pathlib import Path

from .common import data_root, ffmpeg_exe


def _progression(d: dict) -> None:
    if d.get("status") == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        if total and d.get("downloaded_bytes", 0) % (20 * 1024 * 1024) < 1024 * 1024:
            pct = d["downloaded_bytes"] / total * 100
            print(f"  téléchargement : {pct:.0f}%", flush=True)
    elif d.get("status") == "finished":
        print("  téléchargement terminé, préparation…", flush=True)


def _valider_url(url: str) -> None:
    """Anti-SSRF (audit sécurité) : n'accepte que http(s) vers une adresse PUBLIQUE.
    Sans ce filtre, un lien collé (ou suggéré par un contenu tiers) pourrait viser
    le réseau local — http://192.168.1.1/…, http://127.0.0.1:5877/… — ou file://."""
    import ipaddress
    import socket as _sock
    from urllib.parse import urlparse

    u = urlparse((url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Lien invalide : seuls les liens http(s) sont acceptés.")
    try:
        infos = _sock.getaddrinfo(u.hostname, None)
    except OSError as e:
        raise ValueError(f"Nom de domaine introuvable : {u.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ValueError("Lien refusé : il pointe vers une adresse interne "
                             "(réseau local), ce qui n'est pas autorisé.")


def depuis_url(url: str) -> tuple[Path, str]:
    """Télécharge la vidéo en mp4 (max 1080p) dans sources/. Retourne (chemin, titre)."""
    import yt_dlp

    _valider_url(url)
    sources = data_root() / "sources"  # isolé par utilisateur
    sources.mkdir(parents=True, exist_ok=True)
    options = {
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(sources / "%(id)s.%(ext)s"),
        "ffmpeg_location": str(Path(ffmpeg_exe()).parent),
        "progress_hooks": [_progression],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
    chemin = sources / f"{info['id']}.mp4"   # (était SOURCES : reliquat pré-multi-comptes)
    if not chemin.exists():
        raise RuntimeError(f"Téléchargement échoué pour {url}")
    titre = info.get("title") or "Clip"
    print(f"  vidéo : {titre} ({info.get('duration', 0) // 60} min)", flush=True)
    return chemin, titre
