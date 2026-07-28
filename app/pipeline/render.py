"""Assemblage final : fond + voix off (+ musique discrète + SFX aux moments clés) + sous-titres.

SFX (rétention) : whoosh aux changements de scène, boom sourd sur le hook, battements de cœur
sous la montée finale. Sons SYNTHÉTISÉS une fois via ffmpeg (lavfi) dans assets/sfx — aucun
téléchargement, aucune licence à gérer. Mixés bas sous la voix.
"""
import os
from pathlib import Path

from .common import APP, ass_filter_path, ffmpeg_exe, run

VOLUME_MUSIQUE = 0.12  # bien sous la voix
SFX_DIR = APP / "assets" / "sfx"
_VOL_SFX = {"whoosh": 0.22, "boom": 0.42, "heartbeat": 0.30}  # sous la voix, marge anti-écrêtage


def _assurer_sfx() -> dict[str, Path]:
    """Génère (une seule fois) les 3 SFX maison. Retourne {nom: chemin}."""
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    specs = {
        # souffle court : bruit rose passé au bande-passante + enveloppe
        "whoosh": ["-f", "lavfi", "-i", "anoisesrc=color=pink:duration=0.5:amplitude=0.7",
                   "-af", "bandpass=f=950:width_type=h:w=700,"
                          "afade=t=in:d=0.10,afade=t=out:st=0.18:d=0.32,volume=1.4"],
        # impact sourd : sinus grave avec décroissance rapide
        "boom": ["-f", "lavfi", "-i", "sine=frequency=52:duration=1.0",
                 "-af", "afade=t=in:d=0.005,afade=t=out:st=0.12:d=0.85,volume=2.2"],
        # deux battements de cœur : sinus 50 Hz haché par une enveloppe exponentielle
        "heartbeat": ["-f", "lavfi", "-i",
                      r"aevalsrc=exp(-16*mod(t\,0.62))*sin(2*PI*50*t):d=1.3",
                      "-af", "volume=2.0"],
    }
    chemins = {}
    for nom, args in specs.items():
        dest = SFX_DIR / f"{nom}.mp3"
        if not dest.exists():
            run(ffmpeg_exe(), "-y", *args, "-c:a", "libmp3lame", "-q:a", "4", str(dest))
        chemins[nom] = dest
    return chemins


def _voix_polish(voix_fin: float | None) -> str | None:
    """Chaîne de filtre appliquée à la voix : fondu d'entrée bref + ~0,7 s de silence de fin +
    fondu de sortie, pour qu'un clip UNIQUE ne se termine jamais sur une coupure nette. `voix_fin`
    = instant (s) de fin du dernier mot ; None -> pas de traitement (rétrocompatible)."""
    if voix_fin is None:
        return None
    return (f"afade=t=in:st=0:d=0.15,apad=pad_dur=0.7,"
            f"afade=t=out:st={max(0.0, voix_fin):.2f}:d=0.55")


def render(background: Path, audio: Path, ass: Path, out_path: Path,
           musique: Path | None = None,
           sfx_events: list[tuple[float, str]] | None = None,
           voix_fin: float | None = None) -> Path:
    """sfx_events : [(temps_s, 'whoosh'|'boom'|'heartbeat'), ...] mixés sous la voix.
    voix_fin : instant de fin du dernier mot -> fin en fondu (jamais de coupure nette)."""
    sfx_events = [(t, n) for (t, n) in (sfx_events or []) if n in _VOL_SFX and t >= 0]
    vpol = _voix_polish(voix_fin)
    # rendu ATOMIQUE : on écrit dans un fichier temporaire, renommé seulement à la fin.
    # Un crash (PC éteint) ne laisse donc jamais un .mp4 « final » corrompu (moov manquant).
    tmp = out_path.with_name(out_path.stem + ".rendering" + out_path.suffix)
    if not musique and not sfx_events:
        run(
            ffmpeg_exe(), "-y",
            "-i", str(background),
            "-i", str(audio),
            "-vf", f"ass='{ass_filter_path(ass)}'",
            *(["-af", vpol] if vpol else []),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "21",
            "-c:a", "aac", "-b:a", "160k",
            "-shortest", "-movflags", "+faststart",
            str(tmp),
        )
        os.replace(tmp, out_path)
        return out_path

    # graphe dynamique : voix + (musique en boucle) + (chaque SFX retardé à son instant)
    cmd = [ffmpeg_exe(), "-y", "-i", str(background), "-i", str(audio)]
    filtres = [f"[0:v]ass='{ass_filter_path(ass)}'[v]"]
    if vpol:  # la voix passe par le fondu avant d'entrer dans le mixage
        filtres.append(f"[1:a]{vpol}[vx]")
        amix_entrees = ["[vx]"]
    else:
        amix_entrees = ["[1:a]"]
    idx = 2
    if musique:
        cmd += ["-stream_loop", "-1", "-i", str(musique)]
        filtres.append(f"[{idx}:a]volume={VOLUME_MUSIQUE}[mus]")
        amix_entrees.append("[mus]")
        idx += 1
    if sfx_events:
        sons = _assurer_sfx()
        fichiers = sorted({n for _, n in sfx_events})
        entree_de = {}
        for nom in fichiers:
            cmd += ["-i", str(sons[nom])]
            entree_de[nom] = idx
            idx += 1
        for i, (t, nom) in enumerate(sfx_events):
            ms = max(0, int(t * 1000))
            # adelay exige un délai par canal ; 'all=1' n'existe pas partout -> ms|ms
            filtres.append(f"[{entree_de[nom]}:a]adelay={ms}|{ms},"
                           f"volume={_VOL_SFX[nom]}[sf{i}]")
            amix_entrees.append(f"[sf{i}]")
    filtres.append("".join(amix_entrees)
                   + f"amix=inputs={len(amix_entrees)}:duration=first:normalize=0[a]")
    cmd += ["-filter_complex", ";".join(filtres),
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "21",
            "-c:a", "aac", "-b:a", "160k",
            "-shortest", "-movflags", "+faststart",
            str(tmp)]
    run(*cmd)
    os.replace(tmp, out_path)   # rename atomique (voir plus haut)
    return out_path
