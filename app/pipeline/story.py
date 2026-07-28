"""Écriture par Claude : scripts scénarisés (scènes visuelles) et plan éditorial."""
import time

from . import calendrier
from .common import claude_json

PROMPT_SCENES = """Tu écris un script de vidéo TikTok virale en français.
Ligne éditoriale du compte : {theme}
Sujet : {sujet}

Contraintes :
- {mots_min} à {mots_max} mots au total, langage parlé, phrases courtes et percutantes.
- COLD OPEN : la PREMIÈRE phrase démarre au moment le plus fort (une image, une sensation, un
  fait brut), JAMAIS une intro contextuelle datée. Pas "En 1959, au col Dyatlov…" mais
  "Ils ont déchiré leur tente de l'intérieur pour fuir dans la neige." 5 à 9 mots, crée un MANQUE.
- ACCROCHE 2e PERSONNE quand c'est possible : parle AU spectateur ("Ton cerveau te fait ça
  chaque nuit", "Tu l'as déjà ressentie sans savoir pourquoi") — l'auto-référence retient bien
  plus qu'un fait lointain. Pour un fait historique, entre par la sensation universelle avant le nom.
- "hook_ecran" : version ÉCRAN du hook, 4 à 7 mots MAJUSCULES d'impact (affichée plein
  écran les 2 premières secondes — ex. "ILS N'ONT JAMAIS ÉTÉ RETROUVÉS").
- "hook_ecran_b" : une DEUXIÈME version du hook écran avec un angle différent (émotion vs
  mystère, question vs affirmation) — on testera les deux (A/B).
- STRUCTURE EN BOUCLES OUVERTES : le hook promet une révélation → la tension MONTE →
  à MI-PARCOURS, relance une question encore plus forte que celle du hook → la réponse
  complète n'arrive que dans les 15 DERNIÈRES secondes (payoff final).
- SUSPENSE RÉPARTI (crucial) : si le script est long, il sera COUPÉ en parties d'environ 60 s
  publiées un jour sur l'autre. Place donc un pic de suspense (révélation partielle suivie d'une
  NOUVELLE question) à intervalles réguliers d'environ 55-60 secondes de récit — pour que
  n'importe quelle coupure tombe sur un cliffhanger, jamais sur une phrase anodine. Chaque scène
  se termine idéalement sur une micro-tension, jamais sur une info plate.
- La DERNIÈRE phrase est une chute/cliffhanger qui pousse à commenter ET peut s'enchaîner
  naturellement avec la première (le clip se regarde en boucle sans couture audible).
- Contenu factuel si le sujet s'y prête : rien d'inventé présenté comme vrai.
- Découpe le script en {scenes_min} à {scenes_max} SCÈNES visuelles COURTES et successives
  (une nouvelle image toutes les ~{sec} secondes → rythme visuel rapide, jamais de plan qui traîne).
- Pour chaque scène : "recherche" = requête de banque vidéo en ANGLAIS, 2 à 4 mots,
  concrète et filmable, VARIÉE d'une scène à l'autre (alterne plans larges, gros plans,
  objets, lieux, personnes — ex. "rainforest cabin night", "close up eyes fear",
  "old paper documents", "empty highway dusk"). Pas de noms propres.
- Pour chaque scène : "mots_choc" = 0 à 2 mots EXACTS de son texte à fort voltage
  (chiffres, lieux, mots d'émotion — ils seront surlignés en rouge à l'écran). Choisis
  peu et bien : maximum 1 mot choc toutes les ~2 phrases.
- Pour chaque scène : "archive" = si un VISUEL RÉEL de l'affaire existe très probablement
  (lieu précis, personne, document, objet célèbre), requête d'archive EN ANGLAIS AVEC le
  nom propre (ex. "Flannan Isles Lighthouse", "Isdal Woman sketch") — la vraie image sera
  montrée à l'écran (authenticité). Sinon null. Maximum 1 scène sur 3 en archive.

La "caption" est la bio TikTok du clip, construite pour l'algorithme :
- 1ère ligne : question ou affirmation choc qui POUSSE À COMMENTER (le commentaire est le signal n°1).
- 2ème ligne : une phrase naturelle contenant les MOTS-CLÉS du sujet (SEO TikTok : les gens cherchent ces mots).
- Dernière ligne : 4 à 5 hashtags — 2 larges (#pourtoi #fyp) + 2-3 de niche précis liés au sujet.
- 300 caractères maximum au total, ton direct, pas de pub.

Réponds UNIQUEMENT avec un objet JSON (aucun texte autour) :
{{"titre": "titre court", "caption": "la bio optimisée", "hook_ecran": "4-7 MOTS D'IMPACT",
  "hook_ecran_b": "VARIANTE ANGLE DIFFÉRENT",
  "scenes": [{{"texte": "…", "recherche": "…", "mots_choc": ["…"], "archive": null}}]}}"""

PROMPT_SEMAINE = """Tu es le rédacteur en chef d'un compte TikTok VIRAL en français.
Ligne éditoriale : {theme}
Thèmes récurrents : {themes}
{tendances}
Nous sommes le {aujourdhui}. Propose {n} sujets de clips (1 par jour à partir de demain).
OBJECTIF : faire GROSSIR le compte au maximum. Priorise ce qui se PARTAGE et se COMMENTE.
Chaque sujet doit être IRRÉSISTIBLE, pensé pour arrêter le doigt qui scrolle :
- un "curiosity gap" fort : il promet une révélation, un secret ou un twist qu'on DOIT connaître.
- ultra-spécifique et concret (un nom, un chiffre, un lieu, une date précise) — jamais générique.
- une tension émotionnelle nette : peur, malaise, injustice, incompréhension, fascination.
- formulé comme un TEASER de 12 à 22 mots qui donne déjà envie de commenter, pas un titre neutre.
- chaque jour une émotion et un angle DIFFÉRENTS (ne pas enchaîner deux sujets qui se ressemblent).
- évite le déjà-vu mille fois, sauf si tu proposes un angle réellement inédit.
MIX DE CROISSANCE (pondéré par ce qui marche déjà sur ce compte) :
- ~50 % de PHÉNOMÈNES UNIVERSELS / RELATABLE (corps, sommeil, mémoire, cerveau, comportement) —
  « ça m'arrive à MOI » = le plus gros moteur de partage et de tag. C'est ce qui a percé ici.
- ~20 % d'ARNAQUES du quotidien (protection des proches) = partage-alerte réflexe.
- ~15 % de SCIENCE contre-intuitive « mind-blow » = enregistrements.
- ~15 % de MYSTÈRES/faits étranges FRAIS et peu couverts en français (jamais les sur-traités).
- Formule les accroches à la 2e personne quand c'est possible (« ton cerveau… », « ça t'est déjà arrivé ? »).
- Quand une TENDANCE du jour ci-dessus colle HONNÊTEMENT à la niche, angle un sujet dessus pour surfer la vague (jamais de hors-sujet forcé).
Heures de pointe autorisées : {heures}.

Réponds UNIQUEMENT avec un tableau JSON (aucun texte autour) :
[{{"date": "YYYY-MM-DD", "heure": "HH:MM", "sujet": "..."}}]"""


PROMPT_COMMENTAIRE = """Tu écris un script de vidéo TikTok en français qui RÉPOND à ce commentaire d'un abonné :
« {commentaire} »
Ligne éditoriale du compte : {theme}

Le commentaire s'affiche déjà en haut de l'écran (bulle). Ta vidéo est LA réponse.
Contraintes :
- {mots_min} à {mots_max} mots, langage parlé, phrases courtes et percutantes.
- PREMIÈRE phrase : accroche directe qui rebondit sur le commentaire (« Alors ça, il faut que je te réponde… »).
- Apporte une vraie réponse/révélation ; tension qui monte ; rien d'inventé présenté comme vrai.
- Termine par une chute qui donne envie de commenter à son tour.
- Découpe en {scenes_min} à {scenes_max} SCÈNES visuelles courtes (une image toutes les ~{sec} s),
  "recherche" = requête banque vidéo en ANGLAIS (2-4 mots, filmable, variée), "mots_choc" = 0-2 mots forts.

La "caption" est la bio TikTok (≤300 car.) : 1re ligne = relance à commentaires, 2e = mots-clés, 4-5 hashtags.
Réponds UNIQUEMENT avec un objet JSON :
{{"titre": "titre court", "caption": "bio", "hook_ecran": "", "hook_ecran_b": "",
  "scenes": [{{"texte": "…", "recherche": "…", "mots_choc": ["…"]}}]}}"""


PROMPT_HOOKS = """Tu es expert des accroches TikTok virales en français.
Ligne éditoriale du compte : {theme}
Sujet du clip : {sujet}

Propose {n} ACCROCHES d'ouverture RADICALEMENT différentes pour ce même sujet — on va en
choisir une avant d'écrire le script. Chacune doit arrêter le doigt qui scrolle dès la 1re seconde.
Varie les ANGLES d'une accroche à l'autre (ne répète pas la même mécanique) :
- une qui mise sur l'ÉMOTION brute (peur, malaise, injustice) ;
- une à la 2e PERSONNE qui parle AU spectateur (« ton cerveau… », « ça t'est déjà arrivé… ») ;
- une MYSTÈRE / question ouverte qui crée un manque (« personne n'a jamais expliqué… ») ;
- ou un CHIFFRE/fait brut choc. Choisis les {n} angles les plus forts pour CE sujet.

Pour chaque accroche :
- "angle" : 1 à 2 mots qui nomment l'angle (ex. « Émotion », « 2e personne », « Mystère », « Chiffre choc »).
- "hook_parle" : la PREMIÈRE phrase dite à voix haute — un COLD OPEN de 5 à 12 mots qui démarre
  au moment le plus fort, langage parlé, crée un MANQUE. Jamais d'intro contextuelle datée.
- "hook_ecran" : version ÉCRAN, 4 à 7 mots MAJUSCULES d'impact (affichée plein écran 2 s).

Réponds UNIQUEMENT avec un tableau JSON (aucun texte autour) :
[{{"angle": "…", "hook_parle": "…", "hook_ecran": "…"}}]"""


def proposer_hooks(sujet: str, profil: dict, n: int = 3) -> list[dict]:
    """Propose {n} accroches d'angles différents pour un sujet — étape AVANT le rendu.
    Rapide (juste les accroches, pas le script). Renvoie [{angle, hook_parle, hook_ecran}]."""
    data = claude_json(PROMPT_HOOKS.format(
        sujet=sujet,
        theme=profil.get("theme", "contenu grand public varié"),
        n=n,
    ), timeout=180)
    if not isinstance(data, list):
        raise RuntimeError("Réponse de Claude inattendue (liste d'accroches attendue)")
    hooks = []
    for h in data:
        if not isinstance(h, dict):
            continue
        parle = str(h.get("hook_parle", "")).strip()
        ecran = str(h.get("hook_ecran", "")).strip()
        if not parle:
            continue
        hooks.append({
            "angle": str(h.get("angle", "")).strip() or "Accroche",
            "hook_parle": parle,
            "hook_ecran": (ecran or parle[:40]).upper(),
        })
    if not hooks:
        raise RuntimeError("Aucune accroche exploitable renvoyée par Claude")
    return hooks[:n]


def generer_reponse(commentaire: str, mots: int, profil: dict) -> dict:
    """Script qui répond à un commentaire (format viral 'réponse à @user')."""
    from .common import CONFIG

    sec = float(CONFIG.get("secondes_par_scene", 5))
    cible = max(4, min(32, round(max(20.0, mots / 2.6) / sec)))
    data = claude_json(PROMPT_COMMENTAIRE.format(
        commentaire=commentaire[:300],
        theme=profil.get("theme", "contenu grand public varié"),
        mots_min=mots - 20, mots_max=mots + 30,
        scenes_min=max(4, cible - 2), scenes_max=cible + 3, sec=int(sec),
    ), timeout=600)
    return _valider_scenes(data)


def _valider_scenes(data: dict) -> dict:
    """Valide la réponse de Claude et assemble le texte complet."""
    if not data.get("titre") or not data.get("scenes"):
        raise RuntimeError("Réponse de Claude incomplète (titre/scenes)")
    for s in data["scenes"]:
        if not s.get("texte") or not s.get("recherche"):
            raise RuntimeError(f"Scène invalide : {s}")
        if not isinstance(s.get("mots_choc"), list):
            s["mots_choc"] = []
        if not isinstance(s.get("archive"), str) or not s["archive"].strip():
            s["archive"] = None
    data["texte"] = " ".join(s["texte"].strip() for s in data["scenes"])
    data.setdefault("caption", "")
    data.setdefault("hook_ecran", "")
    data.setdefault("hook_ecran_b", "")
    data["mots_choc"] = [m for s in data["scenes"] for m in s["mots_choc"] if isinstance(m, str)]
    return data


def generate(sujet: str, mots: int, profil: dict, hook: dict | None = None) -> dict:
    """Script scénarisé : {titre, caption, scenes:[{texte, recherche}]}.
    Si `hook` (issu de proposer_hooks) est fourni, le script est écrit AUTOUR de cette
    accroche imposée (choisie par l'utilisateur avant le rendu).
    Garde anti-script-court : Claude rend parfois ~la moitié des mots demandés (vécu :
    520 demandés, 230 livrés -> 2 parties au lieu de 3+). Si < 85 % du minimum, on relance
    UNE fois avec consigne renforcée et on garde la meilleure version ; toujours court -> journalisé."""
    from . import journal
    from .common import CONFIG

    sec = float(CONFIG.get("secondes_par_scene", 5))
    duree_est = max(20.0, mots / 2.6)          # ~2,6 mots/s en français parlé
    cible = max(4, min(32, round(duree_est / sec)))  # 1 scène toutes les ~sec secondes, plafond 32
    scenes_min, scenes_max = max(4, cible - 2), cible + 3
    prompt = PROMPT_SCENES.format(
        sujet=sujet,
        theme=profil.get("theme", "contenu grand public varié"),
        mots_min=mots - 20, mots_max=mots + 30,
        scenes_min=scenes_min, scenes_max=scenes_max, sec=int(sec),
    )
    if hook and hook.get("hook_parle"):
        prompt += (
            "\n\n⚠️ ACCROCHE IMPOSÉE (choisie par l'utilisateur) : le COLD OPEN est FIXÉ."
            f"\n- La 1re phrase de la 1re scène DOIT être exactement (ou quasi) : « {hook['hook_parle']}. »"
            f"\n- \"hook_ecran\" DOIT être : « {hook.get('hook_ecran', '')} »."
            "\nÉcris tout le reste du script pour tenir la promesse de cette accroche (même angle, même tension)."
        )
    data = _valider_scenes(claude_json(prompt, timeout=600))
    mini = int((mots - 20) * 0.85)
    nb = len(data["texte"].split())
    if nb < mini:
        print(f"  script trop court ({nb} mots < {mini}) — nouvelle tentative…", flush=True)
        journal.log("script_court", sujet=sujet[:80], mots_obtenus=nb, mots_min=mini)
        relance = (prompt + f"\n\n⚠️ IMPÉRATIF : une tentative précédente ne faisait que {nb} mots"
                   f" — c'est BEAUCOUP trop court. Le script complet doit faire AU MOINS {mots - 20}"
                   " mots. Développe chaque scène et ajoute des scènes s'il le faut.")
        try:
            data2 = _valider_scenes(claude_json(relance, timeout=600))
            if len(data2["texte"].split()) > nb:
                data, nb = data2, len(data2["texte"].split())
        except RuntimeError:
            pass  # on garde la 1re version plutôt que d'échouer
        if nb < mini:
            journal.log("script_court_persistant", sujet=sujet[:80], mots_obtenus=nb, mots_min=mini)
    return data


PROMPT_GLISSANT = """Tu es le rédacteur en chef d'un compte TikTok VIRAL en français.
Ligne éditoriale : {theme}
Thèmes récurrents du compte : {themes}
{tendances}
Sujets DÉJÀ traités récemment — INTERDIT de les répéter ou de les reformuler :
{recents}

Je te donne {n} créneaux de publication PRÉCIS. Pour CHACUN, propose UN sujet de clip
d'environ 1 minute qui maximise l'engagement et le trafic : MIX DE CROISSANCE (~50 %
relatable universel qui fait se sentir concerné, ~20 % arnaques expliquées, ~15 % science
étonnante, ~15 % mystères/histoires vraies), d'actualité ou tendance quand c'est pertinent,
toujours avec un angle qui donne envie de commenter et partager.

Créneaux à remplir : {creneaux}

Réponds UNIQUEMENT avec un tableau JSON (aucun texte autour), un objet PAR créneau :
[{{"quand": "YYYY-MM-DD HH:MM", "sujet": "…"}}]"""


def sujets_pour_creneaux(profil: dict, creneaux: list[str], recents: list[str]) -> list[dict]:
    """Calendrier glissant : propose UN sujet engageant par créneau imposé (date+heure),
    anglé tendances niche, sans répéter les sujets récents. Renvoie [{quand, sujet}]."""
    from . import tendances
    trends = tendances.tendances_niche(12)
    bloc = ""
    if trends:
        bloc = ("Sujets qui montent en ce moment dans la niche :\n- " + "\n- ".join(trends)
                + "\nInspire-t'en quand ils collent à la ligne éditoriale (adapte en français), "
                  "sans forcer un hors-sujet.\n")
    plan = claude_json(PROMPT_GLISSANT.format(
        theme=profil.get("theme", "contenu grand public varié"),
        themes=" · ".join(profil.get("sujets", [])),
        tendances=bloc,
        recents="\n".join(f"- {s}" for s in recents[-40:]) or "- (aucun)",
        n=len(creneaux),
        creneaux=", ".join(creneaux),
    ), timeout=420)
    if not isinstance(plan, list):
        raise RuntimeError("Réponse invalide (liste attendue) pour le calendrier glissant")
    valides = []
    attendus = set(creneaux)
    for p in plan:
        if isinstance(p, dict) and p.get("quand") in attendus and str(p.get("sujet", "")).strip():
            valides.append({"quand": p["quand"], "sujet": str(p["sujet"]).strip()})
            attendus.discard(p["quand"])
    return valides


def planifier_semaine(profil: dict, jours: int = 7) -> list[dict]:
    """Remplit le calendrier éditorial avec un plan proposé par Claude, anglé sur les
    tendances de recherche FR du jour quand un angle de la niche s'y prête (4.1)."""
    from . import tendances
    from .common import CONFIG

    trends = tendances.tendances_niche(15)  # sujets qui montent DANS la niche (Reddit)
    bloc_tendances = ""
    if trends:
        bloc_tendances = (
            "Sujets qui montent en ce moment dans la niche (histoires vraies, mystères, science, "
            "arnaques) — souvent en avance sur TikTok :\n- " + "\n- ".join(trends)
            + "\nInspire-toi de ces sujets chauds quand ils collent à ta ligne éditoriale (traduis/"
              "adapte en français, angle grand public), sans jamais forcer un hors-sujet.\n"
        )
    plan = claude_json(PROMPT_SEMAINE.format(
        theme=profil.get("theme", "contenu grand public varié"),
        themes=" · ".join(profil["sujets"]),
        tendances=bloc_tendances,
        aujourdhui=time.strftime("%Y-%m-%d"),
        n=jours,
        heures=", ".join(CONFIG.get("heures_publication", ["18:00", "21:00", "23:00"])),
    ))
    if not isinstance(plan, list) or not plan:
        raise RuntimeError("Plan éditorial vide ou invalide")
    crees = []
    for slot in plan[:jours]:
        quand = f"{slot['date']} {slot['heure']}"
        time.strptime(quand, "%Y-%m-%d %H:%M")  # valide le format
        crees.append(calendrier.ajouter(quand, slot["sujet"], profil["nom"]))
    return crees
