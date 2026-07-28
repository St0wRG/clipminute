"""Sujets qui montent — pour angler le plan éditorial.

Source NICHE (défaut, la bonne pour ce compte) : flux RSS « top de la semaine » de subreddits
mystères / true-crime / science / arnaques. Fiable, sans clé, souvent EN AVANCE sur TikTok, et
bien plus pertinent que des tendances généralistes.

Contrainte Reddit : l'API .json non authentifiée est bloquée (403), et le RSS rate-limite dès la
2e requête rapprochée (429). Donc UN seul subreddit par appel. On accumule un cache PAR subreddit :
chaque appel rafraîchit le pilier le plus périmé, et on renvoie un mélange entrelacé de tous les
piliers en cache. Le scheduler appelle ça à chaque tick -> variété complète en quelques ticks.

Repli : Google Trends FR (RSS public) si la niche ne donne rien. Jamais bloquant.
"""
import html
import json
import re
import time

from .common import APP

CACHE = APP / "assets" / "tendances.json"              # Google Trends (repli)
CACHE_NICHE = APP / "assets" / "tendances_niche.json"  # {sub: {"quand": ts, "titres": [...]}}
TTL_NICHE = 6 * 3600
GARDE_NICHE = 3 * 86400  # on garde un pilier dans le mélange jusqu'à 3 j (le temps qu'il tourne)
# subreddits alignés sur la niche, ordonnés pour panacher les piliers dès les 1res requêtes
SUBREDDITS = ["UnresolvedMysteries", "todayilearned", "Scams", "Damnthatsinteresting",
              "creepy", "interestingasfuck", "TrueCrime", "Glitch_in_the_Matrix"]
_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")}


def _nettoyer_titre(t: str) -> str:
    t = re.sub(r"\s+", " ", html.unescape(t or "").strip())
    t = re.sub(r"^(TIL that |TIL |Til |LPT:?\s*)", "", t)  # préfixes reddit courants
    return t[:120]


def _fetch_sub_rss(sub: str) -> list[str]:
    """Titres du top de la semaine d'un subreddit via RSS (1 requête). [] si KO/rate-limité."""
    try:
        import requests

        r = requests.get(f"https://www.reddit.com/r/{sub}/top/.rss",
                         params={"t": "week", "limit": 8}, headers=_UA, timeout=12)
        if r.status_code != 200:
            return []
        titres = [_nettoyer_titre(t) for t in re.findall(r"<title>([^<]+)</title>", r.text)[1:]]
        return [t for t in titres if len(t) > 12]
    except Exception:
        return []


def _cache_subs() -> dict:
    try:
        d = json.loads(CACHE_NICHE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def tendances_niche(n: int = 12) -> list[str]:
    """Mélange entrelacé des sujets qui montent dans la niche. Rafraîchit 1 pilier par appel."""
    cache = _cache_subs()
    maintenant = time.time()
    # pilier à rafraîchir = le plus périmé (les absents comptent comme quand=0)
    cible = min(SUBREDDITS, key=lambda s: cache.get(s, {}).get("quand", 0))
    if maintenant - cache.get(cible, {}).get("quand", 0) > TTL_NICHE:
        titres = _fetch_sub_rss(cible)
        if titres:
            cache[cible] = {"quand": maintenant, "titres": titres}
            try:
                CACHE_NICHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
    # mélange entrelacé (round-robin) de tous les piliers assez récents -> variété
    par_sub = [c["titres"] for c in cache.values()
               if maintenant - c.get("quand", 0) < GARDE_NICHE and c.get("titres")]
    if not par_sub:
        return tendances_du_jour(n)  # repli Google si la niche ne donne encore rien
    vus, out = set(), []
    for rang in range(8):
        for titres in par_sub:
            if rang < len(titres):
                t = titres[rang]
                k = t.lower()[:40]
                if k not in vus:
                    vus.add(k)
                    out.append(t)
    return out[:n]


def tendances_du_jour(n: int = 15) -> list[str]:
    """Repli : les ~n recherches qui montent en France aujourd'hui (Google Trends RSS). [] si KO."""
    try:
        if CACHE.exists():
            d = json.loads(CACHE.read_text(encoding="utf-8"))
            if time.time() - float(d.get("quand", 0)) < 86400 and d.get("titres"):
                return d["titres"][:n]
    except (OSError, ValueError):
        pass
    titres: list[str] = []
    try:
        import requests

        r = requests.get("https://trends.google.com/trending/rss?geo=FR",
                         headers=_UA, timeout=15)
        r.raise_for_status()
        titres = [t.strip() for t in re.findall(r"<title>([^<]{3,80})</title>", r.text)[1:]
                  if t.strip()]
    except Exception:
        titres = []
    if titres:
        try:
            CACHE.write_text(json.dumps({"quand": time.time(), "titres": titres},
                                        ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return titres[:n]
    try:
        return json.loads(CACHE.read_text(encoding="utf-8")).get("titres", [])[:n]
    except (OSError, ValueError):
        return []
