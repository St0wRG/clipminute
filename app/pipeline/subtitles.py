"""Sous-titres ASS style TikTok.

Styles disponibles :
  classique : groupes de 1-3 mots, blanc, effet pop.
  karaoke   : même groupes, mais le mot en cours de prononciation est surligné en jaune.
Options : banner (bandeau haut type "PARTIE 2/5"), watermark (pseudo en bas),
hook (texte d'accroche plein écran pendant les 2 premières secondes).
"""
from pathlib import Path

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Mot,Arial Black,88,&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,-1,0,0,0,100,100,0,0,1,9,2,2,60,60,760,1
Style: Bandeau,Arial Black,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,-1,0,0,0,100,100,0,0,1,7,2,8,60,60,120,1
Style: Watermark,Arial,40,&H60FFFFFF,&H60FFFFFF,&H60000000,&H00000000,-1,0,0,0,100,100,1,0,1,3,0,2,60,60,50,1
Style: Hook,Arial Black,96,&H0000E5FF,&H0000E5FF,&H00000000,&H78000000,-1,0,0,0,100,100,0,0,1,10,2,5,80,80,0,1
Style: Outro,Arial Black,80,&H0000E5FF,&H0000E5FF,&H00000000,&HB4000000,-1,0,0,0,100,100,0,0,1,9,3,5,70,70,0,1
Style: Voile,Arial,100,&H1E0A0A0A,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Coeur,Arial,100,&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1
Style: Cue,Arial Black,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,8,40,40,0,1
Style: Barre,Arial,100,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Commentaire,Arial,46,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,1,0,0,7,70,70,360,1
Style: CommentAuteur,Arial,34,&H00888888,&H00888888,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,1,0,0,7,86,70,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

POP = r"{\fad(50,30)\t(0,90,\fscx112\fscy112)\t(90,180,\fscx100\fscy100)}"
POP_CHOC = r"{\fad(50,30)\t(0,90,\fscx122\fscy122)\t(90,200,\fscx100\fscy100)}"  # punch renforcé
JAUNE = r"{\1c&H00E5FF&}"   # BGR : jaune vif
BLANC = r"{\1c&HFFFFFF&}"
ROUGE = r"{\1c&H552CFE&}"   # BGR : rouge TikTok #FE2C55 (mots choc)

MAX_WORDS = 3
MAX_GAP = 0.6  # au-delà de cette pause, on coupe le groupe


def _set_chocs(mots_choc) -> set[str]:
    """Normalise la liste de mots choc pour une comparaison insensible à la casse/ponctuation."""
    return {m.strip().strip(".,!?…:;\"'").lower() for m in (mots_choc or []) if m and m.strip()}


def _est_choc(word: str, chocs: set[str]) -> bool:
    return word.strip().strip(".,!?…:;\"'").lower() in chocs

# --- Micro-incitations d'engagement (psychologie TikTok) ---
# Cœur "like" (chemin vectoriel ~100x92, pointe en bas), rempli de rouge TikTok #FE2C55.
import random as _rng_mod  # noqa: E402

_HEART = ("m 50 90 b 12 56 0 33 0 21 b 0 6 15 0 26 0 b 39 0 50 12 50 23 "
          "b 50 12 61 0 74 0 b 85 0 100 6 100 21 b 100 33 88 56 50 90")
_ROUGE_TT = r"&H552CFE&"  # #FE2C55 en BGR


def _cue_coeur(debut: float) -> str:
    """Cœur like TikTok : surgit petit et blanc, rebondit, se remplit de rouge, disparaît."""
    fin = debut + 1.7
    tags = (r"\an5\pos(946,1210)\bord5\3c&HFFFFFF&\shad0\1c&HFFFFFF&\fscx10\fscy10"
            r"\t(0,140,\fscx102\fscy102)\t(140,300,\fscx86\fscy86)"   # rebond
            r"\t(70,340,\1c" + _ROUGE_TT + r")"                        # remplissage rouge
            r"\t(1300,1700,\alpha&HFF&)")
    return f"Dialogue: 3,{_ts(debut)},{_ts(fin)},Coeur,,0,0,0,,{{{tags}\\p1}}{_HEART}{{\\p0}}\n"


def _cue_texte(debut: float, texte: str, couleur: str) -> str:
    """Pastille d'incitation (Enregistre / Abonne-toi) en haut, hors de la zone sous-titres."""
    fin = debut + 2.1
    tags = (r"\an8\pos(540,300)\1c" + couleur + r"\3c&H000000&\bord6\shad2"
            r"\fscx55\fscy55\t(0,150,\fscx106\fscy106)\t(150,320,\fscx100\fscy100)"
            r"\t(1650,2100,\alpha&HFF&)")
    return f"Dialogue: 3,{_ts(debut)},{_ts(fin)},Cue,,0,0,0,,{{{tags}}}{_clean(texte).upper()}\n"


def engagement_cues(duration: float, seed: int | None = None,
                    respirations: list[float] | None = None) -> list[str]:
    """Incitations réparties : cœur like (fréquent), Enregistre (signal algo le plus fort),
    Abonne-toi (1 seul par clip). Évite le hook et l'outro. Si `respirations` (fins de
    phrase) est fourni, chaque incitation est CALÉE sur la respiration la plus proche
    (jamais par-dessus un mot important)."""
    if duration < 12:
        return []
    rng = _rng_mod.Random(seed)
    a, b = 3.5, max(8.0, duration - 5.0)
    if b <= a:
        return []
    n = max(2, int((b - a) // 20) + 1)
    seg = (b - a) / n
    sac = ["coeur", "coeur", "enregistre", "coeur", "abonne"]
    rng.shuffle(sac)
    lignes, places = [], []
    for i in range(n):
        t0 = a + i * seg + rng.uniform(0.1, max(0.2, seg - 2.2))
        if respirations:  # caler sur la fin de phrase la plus proche (±2,5 s)
            proches = [r for r in respirations if abs(r - t0) <= 2.5 and a <= r <= b]
            if proches:
                t0 = min(proches, key=lambda r: abs(r - t0)) + 0.12
        if any(abs(t0 - p) < 1.8 for p in places):  # deux incitations collées = bruit
            continue
        places.append(t0)
        kind = sac[i % len(sac)]
        if kind == "coeur":
            lignes.append(_cue_coeur(t0))
        elif kind == "enregistre":
            lignes.append(_cue_texte(t0, "Enregistre pour + tard", r"&H00E5FF&"))
        else:
            lignes.append(_cue_texte(t0, "+ Abonne-toi", _ROUGE_TT))
    return lignes


def _barre_progression(duree: float) -> str:
    """Barre de rétention : fin liseré rouge TikTok en haut qui avance de 0 à 100 %
    sur la durée (le viewer qui voit la fin approcher reste). Dessin étiré par \\fscx."""
    tags = (r"\an7\pos(0,0)\1c" + _ROUGE_TT + r"\bord0\shad0\fscy100\fscx0"
            rf"\t(0,{int(duree * 1000)},\fscx100)")
    return (f"Dialogue: 4,{_ts(0)},{_ts(duree)},Barre,,0,0,0,,"
            f"{{{tags}\\p1}}m 0 0 l 1080 0 l 1080 9 l 0 9{{\\p0}}\n")


def _ts(t: float) -> str:
    t = max(t, 0.0)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"


def _clean(s: str) -> str:
    return s.replace("{", "").replace("}", "").replace("\n", " ")


def _groups(words: list[dict]) -> list[list[dict]]:
    groups, cur = [], []
    for w in words:
        if cur and (len(cur) >= MAX_WORDS or w["start"] - cur[-1]["end"] > MAX_GAP):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def _group_end(groups: list[list[dict]], i: int) -> float:
    g = groups[i]
    if i + 1 < len(groups):
        return min(g[-1]["end"] + 0.35, groups[i + 1][0]["start"])
    return g[-1]["end"] + 0.5


def _events_classique(groups, chocs: set[str] | None = None) -> list[tuple[float, float, str]]:
    chocs = chocs or set()
    events = []
    for i, g in enumerate(groups):
        avec_choc = any(_est_choc(w["word"], chocs) for w in g)
        morceaux = [(ROUGE if _est_choc(w["word"], chocs) else BLANC)
                    + _clean(w["word"].strip()).upper() for w in g]
        pop = POP_CHOC if avec_choc else POP  # zoom punch renforcé sur les groupes choc
        events.append((g[0]["start"], _group_end(groups, i), pop + " ".join(morceaux)))
    return events


def _events_karaoke(groups, chocs: set[str] | None = None) -> list[tuple[float, float, str]]:
    """Un événement par mot : mot courant en jaune, mots choc en rouge TikTok, le reste en blanc."""
    chocs = chocs or set()
    events = []
    for i, g in enumerate(groups):
        fin_groupe = _group_end(groups, i)
        for j, w in enumerate(g):
            debut = w["start"]
            fin = g[j + 1]["start"] if j + 1 < len(g) else fin_groupe
            if fin <= debut:
                fin = debut + 0.05
            morceaux = []
            for k, w2 in enumerate(g):
                mot = _clean(w2["word"].strip()).upper()
                if k == j:
                    couleur = JAUNE          # le mot prononcé reste jaune (repère de lecture)
                elif _est_choc(w2["word"], chocs):
                    couleur = ROUGE          # mot choc : rouge TikTok
                else:
                    couleur = BLANC
                morceaux.append(couleur + mot)
            events.append((debut, fin, " ".join(morceaux)))
    return events


def _bulle_commentaire(auteur: str, texte: str, duree: float) -> list[str]:
    """Bulle blanche façon commentaire épinglé TikTok, en haut, tout le clip (format réponse).
    Rectangle blanc arrondi (dessin) + pseudo gris + texte noir — le hook devient LA question."""
    texte = _clean(texte)[:150]
    auteur = _clean(auteur)[:24] or "commentaire"
    # découpe en lignes de ~34 caractères (largeur bulle ~800 px utiles à Fontsize 46)
    mots, lignes, cur = texte.split(), [], ""
    for m in mots:
        if len(cur) + len(m) + 1 > 34 and cur:
            lignes.append(cur); cur = m
        else:
            cur = f"{cur} {m}".strip()
    if cur:
        lignes.append(cur)
    lignes = lignes[:4]
    corps = r"\N".join(lignes)
    x, y = 70, 300                       # coin haut-gauche de la bulle
    h = 62 + len(lignes) * 52            # hauteur = en-tête pseudo + lignes + marge
    fond = (rf"{{\an7\pos({x},{y})\1c&HFFFFFF&\bord0\shad4\4c&H000000&\p1}}"
            f"m 0 20 b 0 0 20 0 20 0 l 920 0 b 940 0 940 0 940 20 l 940 {h - 20} "
            f"b 940 {h} 940 {h} 920 {h} l 20 {h} b 0 {h} 0 {h} 0 {h - 20}" + r"{\p0}")
    # texte positionné explicitement (an7 + \pos : rendu déterministe, indépendant des marges)
    return [
        f"Dialogue: 1,{_ts(0)},{_ts(duree)},Commentaire,,0,0,0,,{fond}\n",
        f"Dialogue: 2,{_ts(0)},{_ts(duree)},CommentAuteur,,0,0,0,,"
        rf"{{\an7\pos({x + 34},{y + 16})\fad(150,0)}}▸ {auteur}" + "\n",
        f"Dialogue: 2,{_ts(0)},{_ts(duree)},Commentaire,,0,0,0,,"
        rf"{{\an7\pos({x + 26},{y + 62})\fad(150,0)}}{corps}" + "\n",
    ]


def build_ass(words: list[dict], ass_path: Path, offset: float = 0.0,
              banner: str | None = None, banner_end: float | None = None,
              style: str = "classique", watermark: str | None = None,
              hook: str | None = None, outro: str | None = None,
              outro_debut: float | None = None, cues_duree: float | None = None,
              cues_seed: int | None = None,
              cues_respirations: list[float] | None = None,
              mots_choc: list[str] | None = None,
              progress_duree: float | None = None,
              commentaire: tuple[str, str] | None = None) -> None:
    """Écrit le fichier ASS. offset décale tous les temps (pour les clips découpés).
    outro : carte plein écran affichée de outro_debut à la fin (ex. 'PARTIE 2 ARRIVE').
    cues_duree : si défini, insère les micro-incitations (calées sur cues_respirations si fourni).
    mots_choc : mots surlignés en rouge TikTok + punch renforcé.
    progress_duree : si défini, barre de progression 0→100 % sur cette durée."""
    lines = [HEADER]
    if cues_duree:
        lines.extend(engagement_cues(cues_duree, cues_seed, cues_respirations))
    if progress_duree and progress_duree > 3:
        lines.append(_barre_progression(progress_duree))
    if commentaire:  # format « réponse à un commentaire » : bulle en haut tout le clip
        lines.extend(_bulle_commentaire(commentaire[0], commentaire[1],
                                        banner_end or (words[-1]["end"] + offset if words else 60.0)))
    fin_totale = banner_end if banner_end else (words[-1]["end"] + offset + 1.0 if words else 60.0)
    if outro:
        debut = outro_debut if outro_debut is not None else max(0.0, fin_totale - 3.0)
        texte = _clean(outro).upper().replace("|", r"\N")
        # le voile sombre plein écran est appliqué en amont par ffmpeg (drawbox) ; ici, le texte.
        lines.append(
            f"Dialogue: 2,{_ts(debut)},{_ts(fin_totale)},Outro,,0,0,0,,"
            rf"{{\fad(200,0)\t(0,400,\fscx108\fscy108)\t(400,700,\fscx100\fscy100)}}{texte}" + "\n"
        )
    if banner:
        lines.append(f"Dialogue: 0,{_ts(0)},{_ts(fin_totale)},Bandeau,,0,0,0,,{_clean(banner)}\n")
    if watermark:
        lines.append(f"Dialogue: 0,{_ts(0)},{_ts(fin_totale)},Watermark,,0,0,0,,{_clean(watermark)}\n")
    if hook:
        h = _clean(hook).upper()
        lines.append(
            f"Dialogue: 1,{_ts(0)},{_ts(2.2)},Hook,,0,0,0,,"
            rf"{{\fad(120,180)}}{h}" + "\n"
        )
    faiseur = _events_karaoke if style == "karaoke" else _events_classique
    for debut, fin, texte in faiseur(_groups(words), _set_chocs(mots_choc)):
        # pendant le hook, on masque les sous-titres normaux pour ne pas superposer
        if hook and debut + offset < 2.2:
            continue
        lines.append(f"Dialogue: 0,{_ts(debut + offset)},{_ts(fin + offset)},Mot,,0,0,0,,{texte}\n")
    ass_path.write_text("".join(lines), encoding="utf-8")
