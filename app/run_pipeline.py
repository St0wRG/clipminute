"""CLIPFORGE — pipeline de clips TikTok sous-titrés.

Modes :
  genere           : script IA -> voix off -> sous-titres -> rendu 9:16
  decoupe          : longue vidéo -> Whisper -> parties sous-titrées (linéaire ou --best-of N)
  planifie-semaine : Claude remplit le calendrier éditorial (7 jours par défaut)

Exemples :
  python run_pipeline.py --mode genere --profil principal --sujet "..." [--mots 175]
  python run_pipeline.py --mode decoupe --video x.mp4 --titre "..." [--duree 60] [--best-of 3]
  python run_pipeline.py --mode planifie-semaine [--profil principal] [--jours 7]
"""
import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path

from pipeline import background, publish, render, story, subtitles, tts
from pipeline.common import APP, CONFIG, OUTPUT, QUEUE, get_profil

MUSIQUES = APP / "assets" / "musiques"  # partagé (assets non isolés) — pas OUTPUT.parent (= data/<uid>)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "clip"


DUREE_MAX_UN_SEUL_CLIP = 75  # au-delà, découpe automatique en parties de ~1 min


def _scenes_chronometrees(scenes: list[dict], words: list[dict], total: float) -> list[dict]:
    """Répartit la durée totale entre les scènes au prorata de leurs mots."""
    compte = [max(1, len(s["texte"].split())) for s in scenes]
    total_mots = sum(compte)
    timed, index, t_debut = [], 0, 0.0
    for i, s in enumerate(scenes):
        index += compte[i]
        if i == len(scenes) - 1:
            t_fin = total
        else:
            w = words[min(int(index / total_mots * len(words)), len(words) - 1)]
            t_fin = w["end"]
        timed.append({"recherche": s["recherche"], "duree": max(2.0, t_fin - t_debut),
                      "texte": s.get("texte", ""), "archive": s.get("archive")})
        t_debut = t_fin
    return timed


OUTRO_SEC = 3.2  # carte "LA SUITE ARRIVE" jouée APRÈS la dernière phrase (image gelée), sans la recouvrir


def _extraire_partie(full: Path, start: float, end: float, n: int, total_parts: int,
                     out: Path) -> Path:
    """Coupe une partie du rendu final, grave le bandeau PARTIE X/N + (sauf dernière) une outro.
    L'outro n'écrase plus la fin de la phrase : la dernière image est gelée (tpad) et la carte
    s'affiche pendant ce gel, une fois la phrase terminée."""
    from pipeline.clipper import audio_polish
    from pipeline.common import ass_filter_path, ffmpeg_exe, run

    duree = end - start
    outro = f"LA SUITE ARRIVE EN PARTIE {n + 1}|ABONNE-TOI POUR PAS LA RATER" if n < total_parts else None
    fin_totale = duree + OUTRO_SEC if outro else duree
    banner_ass = out.with_suffix(".ass")
    subtitles.build_ass([], banner_ass, banner=f"PARTIE {n}/{total_parts}",
                        banner_end=fin_totale, outro=outro,
                        outro_debut=duree if outro else None,
                        progress_duree=duree)  # barre de progression PAR PARTIE
    vf = ""
    if outro:  # gel de la dernière image + voile sombre pendant l'outro
        vf = (f"tpad=stop_mode=clone:stop_duration={OUTRO_SEC},"
              f"drawbox=enable='gte(t,{duree:.2f})':x=0:y=0:w=iw:h=ih:color=black@0.88:t=fill,")
        audio = audio_polish(duree, pad_sec=OUTRO_SEC) + ["-c:a", "aac", "-b:a", "160k"]
    else:  # dernière partie : fondu de sortie, plus jamais de coupure nette
        audio = audio_polish(duree) + ["-c:a", "aac", "-b:a", "160k"]
    vf += f"ass='{ass_filter_path(banner_ass)}'"
    tmp = out.with_name(out.stem + ".rendering" + out.suffix)  # rendu atomique (anti-corruption)
    run(
        ffmpeg_exe(),
        "-y", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(full),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "21",
        *audio, "-movflags", "+faststart",
        str(tmp),
    )
    os.replace(tmp, out)
    return out


def mode_genere(sujet: str | None, texte: str | None, mots: int | None = None,
                profil_nom: str | None = None, commentaire: str | None = None,
                commentaire_auteur: str | None = None, une_partie: bool = False,
                hook: dict | None = None) -> Path:
    profil = get_profil(profil_nom)
    reponse_a = None
    if commentaire:  # format « réponse à un commentaire » : la bulle EST le hook
        reponse_a = (commentaire_auteur or "commentaire", commentaire)
        print(f"[1/5] Script Claude — réponse au commentaire de {reponse_a[0]}")
        data = story.generer_reponse(commentaire, mots or int(profil.get("mots_par_clip", 175)), profil)
        print(f"  {len(data['scenes'])} scènes visuelles")
    elif texte:
        data = {"titre": texte[:40], "texte": texte, "caption": "",
                "scenes": [{"texte": texte,
                            "recherche": random.choice(profil.get("pexels_recherches", ["satisfying"]))}]}
    else:
        sujet = sujet or random.choice(profil["sujets"])
        if hook and hook.get("angle"):
            print(f"[1/5] Script Claude — sujet : {sujet} (accroche imposée : {hook['angle']})")
        else:
            print(f"[1/5] Script Claude — sujet : {sujet}")
        data = story.generate(sujet, mots or int(profil.get("mots_par_clip", 175)), profil, hook=hook)
        print(f"  {len(data['scenes'])} scènes visuelles")
    stem = f"{time.strftime('%Y%m%d_%H%M%S')}_{_slug(data['titre'])}"

    print("[2/5] Voix off edge-tts…")
    mp3 = OUTPUT / f"{stem}.mp3"
    words = tts.synthesize(data["texte"], profil["voix"], profil["vitesse_voix"], mp3)
    duration = words[-1]["end"] + 1.0

    print("[3/5] Sous-titres ASS…")
    from pipeline import journal
    from pipeline.clipper import fins_de_phrase
    respirations = fins_de_phrase(words)  # incitations calées sur les fins de phrase
    # A/B de hooks : on alterne la variante A/B selon la parité des clips produits aujourd'hui,
    # et l'ADN retient laquelle a été utilisée -> le rapport hebdo apprendra quel angle gagne.
    aujourdhui = time.strftime("%Y-%m-%d")
    nb_jour = sum(1 for e in journal.recents(200)
                  if e["evenement"] == "clip_produit" and e["quand"].startswith(aujourdhui))
    variante = "B" if (nb_jour % 2 == 1 and data.get("hook_ecran_b")) else "A"
    hook_choisi = data.get("hook_ecran_b") if variante == "B" else data.get("hook_ecran")
    if hook and hook.get("hook_ecran"):  # accroche choisie par l'utilisateur : on l'impose à l'écran
        hook_choisi, variante = hook["hook_ecran"], "choisi"
    if reponse_a:                    # en mode réponse, la bulle du commentaire EST le hook
        hook_choisi, variante = None, "reponse"
    ass = OUTPUT / f"{stem}.ass"
    subtitles.build_ass(words, ass,
                        style=profil.get("style_soustitres", "classique"),
                        watermark=profil.get("pseudo") or None,
                        hook=hook_choisi or None,              # hook plein écran 0-2 s
                        mots_choc=data.get("mots_choc"),       # rouge TikTok + punch
                        cues_duree=duration, cues_respirations=respirations,
                        commentaire=reponse_a,                 # bulle « réponse à un commentaire »
                        progress_duree=duration if duration <= DUREE_MAX_UN_SEUL_CLIP else None)

    print(f"[4/5] Fond vidéo ({duration:.0f} s)…")
    scenes_timed = _scenes_chronometrees(data["scenes"], words, duration)
    credits_images: list[str] = []
    if len(scenes_timed) > 1:
        bg = background.fond_scenes(scenes_timed, OUTPUT / f"{stem}_bg.mp4",
                                    profil.get("pexels_recherches"), credits=credits_images)
    else:
        bg = background.make_background(duration, OUTPUT / f"{stem}_bg.mp4",
                                        profil.get("pexels_recherches"))

    print("[5/5] Rendu final…")
    caption = data["caption"]
    if credits_images:  # attribution des archives réelles (licences CC)
        caption = f"{caption}\n📷 {' · '.join(credits_images[:3])}".strip()
    dossier_mus = MUSIQUES
    ambiance = profil.get("musique_ambiance")  # ex. "sombre" -> ne prend que sombre_*.mp3
    musiques = sorted(dossier_mus.glob(f"{ambiance}_*.mp3")) if ambiance else []
    musiques = musiques or sorted(dossier_mus.glob("*.mp3"))
    musique = random.choice(musiques) if musiques else None
    if musique:
        nom = musique.stem
        if ambiance and nom.startswith(f"{ambiance}_"):
            nom = nom[len(ambiance) + 1:]
        caption = f"{caption}\n♪ {nom.replace('_', ' ')} — Kevin MacLeod (incompetech.com) CC BY 4.0".strip()
    # SFX rétention : boom sur le hook, whoosh aux changements de scène, cœur qui bat sur la montée finale
    sfx = [(0.12, "boom"), (max(0.0, duration * 0.82), "heartbeat")]
    t, dernier_whoosh = 0.0, -10.0
    for s in scenes_timed[:-1]:
        t += s["duree"]
        if t - dernier_whoosh >= 2.5 and t < duration - 4:
            sfx.append((max(0.0, t - 0.15), "whoosh"))
            dernier_whoosh = t
    full = render.render(bg, mp3, ass, OUTPUT / f"{stem}.mp4", musique, sfx_events=sfx,
                         voix_fin=words[-1]["end"])  # fin en fondu (clip unique + dernière partie)

    # ADN du clip : l'empreinte de production que le rapport hebdo croisera avec les stats
    adn = {
        "sujet": (sujet or "")[:120], "duree_s": round(duration, 1),
        "hook_variante": variante, "hook": (hook_choisi or "")[:80],
        "hook_autre": (data.get("hook_ecran") if variante == "B" else data.get("hook_ecran_b") or "")[:80],
        "mots": len(data["texte"].split()), "scenes": len(data["scenes"]),
        "mots_choc": len(data.get("mots_choc") or []),
        "style": profil.get("style_soustitres", "classique"),
        "musique": musique.stem if musique else None, "sfx": bool(sfx),
        "heure_production": time.strftime("%H:%M"),
    }

    if une_partie or duration <= DUREE_MAX_UN_SEUL_CLIP:
        # une_partie : génération d'un créneau du calendrier = 1 clip, jamais de découpe en série
        # (même si le script déborde un peu de 75 s), pour que 1 créneau = 1 vidéo.
        dest = publish.publish(full, caption, data["titre"], profil["nom"], adn=adn)
        # Recette de re-roll : on GARDE voix+sous-titres ~24 h pour « autres fonds »
        # (rejouer uniquement les vidéos de fond sans re-payer la synthèse vocale).
        from pipeline import nettoyage
        recette = {
            "stem": stem, "scenes_timed": scenes_timed, "duration": round(duration, 2),
            "musique_stem": musique.stem if musique else None,
            "sfx": [[round(t, 2), n] for (t, n) in sfx], "voix_fin": words[-1]["end"],
            "pexels_recherches": profil.get("pexels_recherches"),
            "sujet": (sujet or "")[:200], "cree_ts": time.time(),
        }
        meta = publish.lire_meta(dest.name)
        meta["reroll"] = recette
        publish.ecrire_meta(dest.name, meta)
        nettoyage.garder_voix_pour_reroll(stem)  # garde {stem}.mp3 + .ass, purge le reste
        print(f"OK -> {dest}")
        return dest

    # histoire longue -> série de parties d'environ 1 minute, coupées EN FIN DE PHRASE
    from pipeline.clipper import _split_points
    parts = _split_points(words, duration, 60.0)
    print(f"Histoire longue ({duration:.0f} s) -> {len(parts)} parties…")
    dest = None
    for n, (start, end) in enumerate(parts, 1):
        out = OUTPUT / f"{stem}_partie{n}.mp4"
        print(f"  partie {n}/{len(parts)}…")
        _extraire_partie(full, start, end, n, len(parts), out)
        teaser = "⚠️ La suite va te choquer — abonne-toi pour la partie suivante.\n" if n < len(parts) else ""
        cap = f"{data['titre']} — Partie {n}/{len(parts)} 👀\n{teaser}{caption}"
        dest = publish.publish(out, cap, f"{data['titre']} ({n}/{len(parts)})", profil["nom"],
                               adn={**adn, "partie": n, "parties_total": len(parts),
                                    "duree_s": round(end - start, 1)})
    from pipeline import nettoyage
    nettoyage.purger_intermediaires(stem)  # les livrables sont en file : output/ redevient propre
    print(f"OK -> {len(parts)} parties dans la file")
    return dest


def mode_reroll_fonds(fichier_queue: str) -> Path:
    """Re-roll « autres fonds » : garde voix + sous-titres, régénère UNIQUEMENT les vidéos
    de fond (Pexels/archives), re-rend, et remplace le clip en file (atomique).
    La recette est dans la meta du clip (valable ~24 h, voir garder_voix_pour_reroll)."""
    from pipeline import journal, nettoyage
    meta = publish.lire_meta(fichier_queue)
    r = meta.get("reroll")
    if not r:
        raise RuntimeError("Ce clip n'a pas de recette de re-roll (trop ancien, série, ou déjà publié).")
    stem = r["stem"]
    mp3, ass = OUTPUT / f"{stem}.mp3", OUTPUT / f"{stem}.ass"
    if not mp3.exists() or not ass.exists():
        raise RuntimeError("Voix/sous-titres purgés (clip de plus de 24 h) — regénère le clip.")
    duration = float(r["duration"])
    print(f"[1/2] Nouveau fond vidéo ({duration:.0f} s)…")
    scenes = r.get("scenes_timed") or []
    bg = OUTPUT / f"{stem}_bg.mp4"
    if len(scenes) > 1:
        bg = background.fond_scenes(scenes, bg, r.get("pexels_recherches"))
    else:
        bg = background.make_background(duration, bg, r.get("pexels_recherches"))
    musique = None
    if r.get("musique_stem"):
        cand = sorted(MUSIQUES.glob(r["musique_stem"] + ".mp3"))
        musique = cand[0] if cand else None
    sfx = [(float(t), n) for (t, n) in (r.get("sfx") or [])]
    print("[2/2] Rendu final…")
    neuf = OUTPUT / f"{stem}_reroll.mp4"
    render.render(bg, mp3, ass, neuf, musique, sfx_events=sfx, voix_fin=r.get("voix_fin"))
    dest = QUEUE / Path(fichier_queue).name
    os.replace(str(neuf), str(dest))          # remplace le clip en file (même disque -> atomique)
    nettoyage.garder_voix_pour_reroll(stem)   # garde voix+ass pour un nouveau re-roll, purge le bg
    journal.log("reroll_fonds", fichier=fichier_queue, stem=stem)
    print(f"OK -> fond régénéré : {dest.name}")
    return dest


def mode_decoupe(video: Path, titre: str, duree: int | None = None,
                 best_of: int | None = None, profil_nom: str | None = None) -> list[Path]:
    from pipeline import clipper  # import tardif : faster-whisper est lourd

    profil = get_profil(profil_nom)
    if duree:
        CONFIG["duree_partie_sec"] = duree
    print(f"Découpe de {video.name} (transcription Whisper, patience en CPU)…")
    parts = clipper.cut_video(
        video, titre,
        style=profil.get("style_soustitres", "classique"),
        watermark=profil.get("pseudo") or None,
        best_of=best_of,
    )
    dests = []
    for i, p in enumerate(parts, 1):
        if p["hook"]:
            caption = f"{titre} — {p['hook']} 👀 #fyp #pourtoi"
        else:
            caption = f"{titre} — Partie {i}/{len(parts)} 👀 #partie{i} #fyp #pourtoi"
        dests.append(publish.publish(p["chemin"], caption, titre, profil["nom"]))
    print(f"OK -> {len(dests)} clips dans la file")
    return dests


def mode_planifie_semaine(profil_nom: str | None, jours: int) -> None:
    profil = get_profil(profil_nom)
    print(f"Plan éditorial {jours} jours pour le profil « {profil['nom']} »…")
    crees = story.planifier_semaine(profil, jours)
    for c in crees:
        print(f"  {c['quand']} — {c['sujet']}")
    print(f"OK -> {len(crees)} créneaux ajoutés au calendrier")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["genere", "decoupe", "planifie-semaine", "reroll-fonds"], required=True)
    ap.add_argument("--fichier", help="mode reroll-fonds : nom du clip en file à re-rendre")
    ap.add_argument("--profil")
    ap.add_argument("--sujet")
    ap.add_argument("--texte", help="script fourni directement (bypass Claude)")
    ap.add_argument("--video", help="vidéo source pour le mode decoupe")
    ap.add_argument("--url", help="mode decoupe : lien YouTube à télécharger comme source")
    ap.add_argument("--titre", default="Clip")
    ap.add_argument("--duree", type=int, help="durée cible d'une partie en secondes (mode decoupe)")
    ap.add_argument("--best-of", type=int, dest="best_of",
                    help="mode decoupe : ne garder que les N meilleurs moments (choisis par Claude)")
    ap.add_argument("--mots", type=int, help="longueur du script en mots (mode genere, ~175 = 1 min)")
    ap.add_argument("--commentaire", help="mode genere : génère une vidéo RÉPONSE à ce commentaire")
    ap.add_argument("--commentaire-auteur", dest="commentaire_auteur", help="pseudo de l'auteur du commentaire")
    ap.add_argument("--creneau", help="id du créneau du calendrier à générer (met à jour son statut)")
    ap.add_argument("--une-partie", dest="une_partie", action="store_true",
                    help="force 1 seul clip (pas de découpe en série) — utilisé par le calendrier")
    ap.add_argument("--hook", help="mode genere : accroche imposée (JSON {angle,hook_parle,hook_ecran})")
    ap.add_argument("--user", help="e-mail du compte : isole toutes les données dans data/<uid>/")
    ap.add_argument("--jours", type=int, default=7)
    args = ap.parse_args()
    from pipeline.common import set_user
    set_user(args.user)  # AVANT tout accès aux données : on cible le dossier de CET utilisateur

    if args.mode == "genere":
        # génération liée à un créneau : on met à jour son statut (genere/erreur) pour que
        # le dashboard et le scheduler restent cohérents (bouton « générer » par créneau).
        hook = None
        if args.hook:
            import json as _json
            try:
                hook = _json.loads(args.hook)
            except (ValueError, TypeError):
                hook = None
        try:
            mode_genere(args.sujet, args.texte, args.mots, args.profil,
                        args.commentaire, args.commentaire_auteur,
                        une_partie=args.une_partie, hook=hook)
            if args.creneau:
                from pipeline import calendrier
                calendrier.maj_statut(args.creneau, "genere")
        except Exception:
            if args.creneau:
                from pipeline import calendrier
                calendrier.maj_statut(args.creneau, "erreur")
            raise
    elif args.mode == "decoupe":
        if args.url:
            from pipeline import telecharger
            print(f"Téléchargement de {args.url}…")
            video, titre_yt = telecharger.depuis_url(args.url)
            titre = args.titre if args.titre != "Clip" else titre_yt
            mode_decoupe(video, titre, args.duree, args.best_of, args.profil)
        elif args.video:
            mode_decoupe(Path(args.video), args.titre, args.duree, args.best_of, args.profil)
        else:
            ap.error("--video ou --url est requis en mode decoupe")
    elif args.mode == "reroll-fonds":
        if not args.fichier:
            ap.error("--fichier est requis en mode reroll-fonds")
        mode_reroll_fonds(args.fichier)
    else:
        mode_planifie_semaine(args.profil, args.jours)
    return 0


if __name__ == "__main__":
    sys.exit(main())
