"""Mode découpe : longue vidéo -> transcription Whisper -> parties verticales sous-titrées.

Deux stratégies :
  linéaire : toute la vidéo, coupée aux pauses de parole (série PARTIE X/N).
  best-of  : Claude lit le transcript et sélectionne les N meilleurs moments,
             chacun ouvert par un hook plein écran de 2 s.
Un zoom lent alterné (avant/arrière selon la partie) casse la monotonie des plans fixes.
"""
import os
from pathlib import Path

from .common import CONFIG, OUTPUT, ass_filter_path, claude_json, ffmpeg_exe, media_duration, run
from .subtitles import build_ass

PROMPT_MOMENTS = """Voici le transcript horodaté d'une vidéo (format [secondes] texte) :

{transcript}

Sélectionne les {n} moments les PLUS captivants (tension, révélation, punchline),
chacun d'environ {duree} secondes, sans chevauchement, dans l'ordre chronologique.
Pour chaque moment, écris un hook de 4 à 7 mots qui donne envie de rester.

Réponds UNIQUEMENT avec un tableau JSON (aucun texte autour) :
[{{"debut_sec": nombre, "fin_sec": nombre, "hook": "..."}}]"""

ZOOM_AVANT = ("zoompan=z='min(1+0.00028*on,1.10)':x='iw/2-(iw/zoom/2)'"
              ":y='ih/2-(ih/zoom/2)':d=1:s=1080x1920:fps=30")
ZOOM_ARRIERE = ("zoompan=z='max(1.10-0.00028*on,1.0)':x='iw/2-(iw/zoom/2)'"
                ":y='ih/2-(ih/zoom/2)':d=1:s=1080x1920:fps=30")


def transcribe(video: Path, total: float | None = None) -> list[dict]:
    """Transcrit la vidéo et retourne [{word, start, end}, ...] (import local : chargement lent).
    Cache sur disque à côté de la source : une vidéo n'est jamais transcrite deux fois."""
    import json

    cache = video.with_suffix(".words.json")
    if cache.exists():
        print("  transcription trouvée en cache ✓", flush=True)
        return json.loads(cache.read_text(encoding="utf-8"))
    from faster_whisper import WhisperModel

    model = WhisperModel(CONFIG["whisper_modele"], device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(video),
        language=CONFIG.get("langue_source") or None,
        word_timestamps=True,
        vad_filter=True,
        beam_size=1,  # ~2x plus rapide que le beam 5 par défaut, qualité quasi identique (V8.5)
        condition_on_previous_text=False,
    )
    total = total or info.duration
    words = []
    next_report = 0.0
    for seg in segments:
        if seg.end >= next_report:
            pct = min(100, seg.end / total * 100)
            print(f"  transcription : {seg.end/60:.0f}/{total/60:.0f} min ({pct:.0f}%)", flush=True)
            next_report = seg.end + 120  # un point de progression toutes les ~2 min d'audio
        for w in seg.words or []:
            words.append({"word": w.word.strip(), "start": w.start, "end": w.end})
    if not words:
        raise RuntimeError("Whisper n'a détecté aucune parole dans la vidéo")
    cache.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    return words


_FINS_PHRASE = (".", "!", "?", "…", ".»", "!»", "?»", ".\"", "!\"", "?\"")
_SILENCE_PHRASE = 0.45  # s : edge-tts insère ~0,8 s après un point (vs ≤0,3 s pour une virgule)


def fins_de_phrase(words: list[dict]) -> list[float]:
    """Temps (s) où une phrase se termine.
    1) Si les tokens portent la ponctuation forte (transcription Whisper) : on l'utilise.
    2) Sinon (voix edge-tts, tokens SANS ponctuation — c'est le cas par défaut) : une fin de
       phrase = un SILENCE marqué entre deux mots. edge-tts laisse ~0,8 s après un point contre
       ≤0,3 s pour une virgule et ~0,05 s entre deux mots. On prend un seuil ADAPTATIF (nettement
       au-dessus de l'inter-mot médian) pour rester fiable quelle que soit la vitesse de la voix,
       avec un plancher de sécurité. Sans ça, une phrase dont le point est peu marqué n'était pas
       détectée et la découpe tombait en plein milieu de phrase."""
    if not words:
        return []
    par_ponct = [w["end"] for w in words if w["word"].strip().endswith(_FINS_PHRASE)]
    if len(par_ponct) >= max(3, len(words) // 80):
        return par_ponct
    gaps = [(words[j + 1]["start"] - words[j]["end"], words[j]["end"])
            for j in range(len(words) - 1)]
    seuil = _SILENCE_PHRASE
    positifs = sorted(g for g, _ in gaps if g > 0)
    if positifs:
        median = positifs[len(positifs) // 2]
        seuil = max(_SILENCE_PHRASE, median * 2.2)  # une vraie pause dépasse largement l'inter-mot
    ends = [t for g, t in gaps if g >= seuil]
    ends.append(words[-1]["end"])   # la toute fin est toujours une fin de phrase
    return ends


def audio_polish(duree: float, pad_sec: float = 0.0,
                 fade_out: float = 0.40, fade_in: float = 0.15) -> list[str]:
    """Arguments ffmpeg `-af` qui suppriment toute coupure NETTE du son : bref fondu d'entrée
    (anti-clic au début d'une partie) + fondu de sortie sur les dernières fractions de seconde,
    puis silence optionnel `pad_sec` pour laisser jouer la carte de fin. Le fondu de sortie
    adoucit aussi une coupe qui tomberait malgré tout en plein mot. Partagé par le mode génération
    (parties de série) et le mode découpe."""
    fo = max(0.0, duree - fade_out)
    chaine = f"afade=t=in:st=0:d={fade_in:.2f},afade=t=out:st={fo:.2f}:d={fade_out:.2f}"
    if pad_sec > 0:
        chaine += f",apad=pad_dur={pad_sec:.2f}"
    return ["-af", chaine]


def _split_points(words: list[dict], total: float, target: float,
                  sentence_ends: list[float] | None = None,
                  overflow: float = 15.0) -> list[tuple[float, float]]:
    """Découpe en parties d'environ `target` s, en coupant DE PRÉFÉRENCE EN FIN DE PHRASE
    (jusqu'à `overflow` s au-delà de la cible pour terminer la phrase → ~1 min 10-15),
    jamais en plein milieu. Repli : fin de mot la plus proche si aucune phrase ne se termine
    dans la fenêtre. Absorbe un reliquat trop court dans la partie précédente."""
    if sentence_ends is None:
        sentence_ends = fins_de_phrase(words)
    parts, start = [], 0.0
    while start < total - 5:
        limit = start + target
        mini = start + min(22.0, target * 0.5)   # une partie fait au moins ~ce minimum
        haut = min(limit + overflow, total - 1.0)
        cut = min(limit, total)
        cands = [t for t in sentence_ends if mini <= t <= haut]
        if cands:  # 1) fin de phrase la plus proche de la cible
            cut = min(cands, key=lambda t: abs(t - limit))
        else:      # 2) repli : la PLUS LONGUE pause (respiration) de la fenêtre, jamais un mot au hasard
            best, best_gap = None, 0.0
            for j in range(len(words) - 1):
                t = words[j]["end"]
                if t <= mini:
                    continue
                if t > haut:
                    break
                gap = words[j + 1]["start"] - t
                if gap > best_gap:
                    best, best_gap = t, gap
            if best is not None:
                cut = best
        if total - cut < 12:            # reliquat trop court -> on finit ici
            parts.append((start, total))
            break
        parts.append((start, min(cut + 0.35, total)))
        start = cut + 0.05
    return parts or [(0.0, total)]


def choisir_moments(words: list[dict], n: int, duree_cible: float) -> list[dict]:
    """Claude sélectionne les n meilleurs passages du transcript."""
    lignes, tampon, debut_ligne = [], [], 0.0
    for w in words:
        if not tampon:
            debut_ligne = w["start"]
        tampon.append(w["word"])
        if len(tampon) >= 12:
            lignes.append(f"[{debut_ligne:.0f}] {' '.join(tampon)}")
            tampon = []
    if tampon:
        lignes.append(f"[{debut_ligne:.0f}] {' '.join(tampon)}")
    plan = claude_json(PROMPT_MOMENTS.format(
        transcript="\n".join(lignes), n=n, duree=int(duree_cible)), timeout=600)
    moments = []
    for m in plan[:n]:
        debut, fin = float(m["debut_sec"]), float(m["fin_sec"])
        if fin - debut < 8:
            continue
        # recale la fin sur la fin de mot la plus proche (coupe propre)
        fins = [w["end"] for w in words if abs(w["end"] - fin) < 6]
        if fins:
            fin = min(fins, key=lambda t: abs(t - fin))
        moments.append({"debut": debut, "fin": fin + 0.3, "hook": str(m.get("hook", "")).strip()})
    if not moments:
        raise RuntimeError("Claude n'a retenu aucun moment exploitable")
    return moments


OUTRO_SEC = 3.2  # carte de fin jouée APRÈS la dernière phrase (image gelée), sans la recouvrir


def _rendre_partie(video: Path, start: float, end: float, ass: Path, out: Path,
                   zoom_avant: bool, voile_debut: float | None = None) -> None:
    zoom = ZOOM_AVANT if zoom_avant else ZOOM_ARRIERE
    voile = ""
    if voile_debut is not None:  # gel de la dernière image + voile sombre pendant l'outro
        voile = (f"tpad=stop_mode=clone:stop_duration={OUTRO_SEC},"
                 f"drawbox=enable='gte(t,{voile_debut:.2f})':x=0:y=0:w=iw:h=ih:color=black@0.88:t=fill,")
        audio = audio_polish(end - start, pad_sec=OUTRO_SEC)  # fondu de sortie + silence sous la carte
    else:
        audio = audio_polish(end - start)  # dernière partie : fin en fondu, jamais net
    tmp = out.with_name(out.stem + ".rendering" + out.suffix)  # rendu atomique (anti-corruption)
    run(
        ffmpeg_exe(), "-y",
        "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(video),
        "-vf",
        # gel d'image (tpad) AVANT le zoompan : sinon zoompan corrompt la base de temps et la
        # durée explose (bug pré-existant : partie outro rendue à ~3075 s au lieu de ~9 s).
        "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale=1080:1920,setsar=1,"
        f"{voile}{zoom},ass='{ass_filter_path(ass)}'",
        "-c:v", "libx264", "-preset", "fast", "-crf", "21",
        *audio, "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        str(tmp),
    )
    os.replace(tmp, out)


def cut_video(video: Path, titre: str, style: str = "classique",
              watermark: str | None = None, best_of: int | None = None) -> list[dict]:
    """Retourne [{chemin, hook}, ...]. best_of=N -> les N meilleurs moments au lieu du linéaire."""
    total = media_duration(video)
    words = transcribe(video, total)
    fins = fins_de_phrase(words)
    outputs = []
    if best_of:
        moments = choisir_moments(words, best_of, float(CONFIG["duree_partie_sec"]))
        print(f"  {len(moments)} moments retenus…", flush=True)
        for n, m in enumerate(moments, 1):
            print(f"  rendu moment {n}/{len(moments)} : {m['hook']}", flush=True)
            start, end = m["debut"], m["fin"]
            part_words = [w for w in words if start <= w["start"] < end]
            resp = [f - start for f in fins if start < f <= end]
            ass = OUTPUT / f"{video.stem}_m{n}.ass"
            build_ass(part_words, ass, offset=-start, banner_end=end - start,
                      style=style, watermark=watermark, hook=m["hook"],
                      cues_duree=end - start, cues_seed=n, cues_respirations=resp,
                      progress_duree=end - start)
            out = OUTPUT / f"{video.stem}_moment{n}.mp4"
            _rendre_partie(video, start, end, ass, out, zoom_avant=n % 2 == 1)
            outputs.append({"chemin": out, "hook": m["hook"]})
    else:
        parts = _split_points(words, total, float(CONFIG["duree_partie_sec"]), sentence_ends=fins)
        print(f"  découpe en {len(parts)} parties…", flush=True)
        for n, (start, end) in enumerate(parts, 1):
            print(f"  rendu partie {n}/{len(parts)}…", flush=True)
            part_words = [w for w in words if start <= w["start"] < end]
            resp = [f - start for f in fins if start < f <= end]
            ass = OUTPUT / f"{video.stem}_p{n}.ass"
            duree_p = end - start
            outro = f"LA SUITE ARRIVE EN PARTIE {n + 1}|ABONNE-TOI POUR PAS LA RATER" if n < len(parts) else None
            voile_debut = duree_p if outro else None  # la carte joue APRÈS la phrase (image gelée)
            build_ass(part_words, ass, offset=-start,
                      banner=f"PARTIE {n}/{len(parts)}",
                      banner_end=duree_p + (OUTRO_SEC if outro else 0.0),
                      style=style, watermark=watermark, outro=outro,
                      outro_debut=voile_debut, cues_duree=duree_p, cues_seed=n,
                      cues_respirations=resp, progress_duree=duree_p)
            out = OUTPUT / f"{video.stem}_partie{n}.mp4"
            _rendre_partie(video, start, end, ass, out, zoom_avant=n % 2 == 1,
                           voile_debut=voile_debut)
            outputs.append({"chemin": out, "hook": None})
    return outputs
