"""Statistiques de publication (V4.5) : rapatriées depuis l'API Zernio, stockées en SQLite.

Sans posts publiés, la synchronisation retourne honnêtement 0 — aucune donnée inventée.
"""
import sqlite3
import time
from pathlib import Path

from .common import CONFIG, QUEUE, data_root


def _connexion() -> sqlite3.Connection:
    con = sqlite3.connect(str(data_root() / "stats.db"))  # DB isolée par utilisateur
    con.execute("""CREATE TABLE IF NOT EXISTS stats_posts (
        post_id TEXT PRIMARY KEY,
        fichier TEXT, titre TEXT, publie_le TEXT, url TEXT,
        vues INTEGER DEFAULT 0, impressions INTEGER DEFAULT 0,
        likes INTEGER DEFAULT 0, commentaires INTEGER DEFAULT 0,
        partages INTEGER DEFAULT 0, engagement REAL DEFAULT 0,
        maj TEXT)""")
    # nos propres relevés quotidiens du compte : le graphique ne dépend plus de personne
    con.execute("""CREATE TABLE IF NOT EXISTS stats_compte (
        date TEXT PRIMARY KEY,
        abonnes INTEGER, likes INTEGER, videos INTEGER,
        source TEXT, maj TEXT)""")
    return con


def _snapshot_compte(donnees: dict, source: str) -> None:
    """Enregistre le relevé du jour (écrase le précédent du même jour)."""
    con = _connexion()
    con.execute(
        """INSERT INTO stats_compte (date, abonnes, likes, videos, source, maj)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(date) DO UPDATE SET abonnes=excluded.abonnes,
             likes=excluded.likes, videos=excluded.videos,
             source=excluded.source, maj=excluded.maj""",
        (time.strftime("%Y-%m-%d"), donnees["abonnes"], donnees["likes"],
         donnees["videos"], source, time.strftime("%H:%M:%S")),
    )
    con.commit()
    con.close()


def _profil_tiktok_public() -> dict | None:
    """Compteurs lus directement sur la page publique TikTok du compte
    (indépendant de Zernio). Le JSON embarqué contient followerCount/heartCount/videoCount."""
    import json as _json
    import re

    import requests

    pseudo = None
    for p in CONFIG.get("profils", {}).values():
        if p.get("pseudo"):
            pseudo = p["pseudo"].lstrip("@")
            break
    if not pseudo:
        return None
    try:
        r = requests.get(
            f"https://www.tiktok.com/@{pseudo}",
            headers={"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/126.0 Safari/537.36"),
                     "Accept-Language": "fr-FR,fr;q=0.9"},
            timeout=20,
        )
        r.raise_for_status()
        m = re.search(
            r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
            r.text, re.DOTALL)
        if not m:
            return None
        data = _json.loads(m.group(1))
        user = (data.get("__DEFAULT_SCOPE__", {})
                    .get("webapp.user-detail", {})
                    .get("userInfo", {}))
        s = user.get("stats") or {}
        if not s:
            return None
        return {"abonnes": int(s.get("followerCount", 0)),
                "likes": int(s.get("heartCount", s.get("heart", 0))),
                "videos": int(s.get("videoCount", 0)),
                "abonnes_gagnes_30j": 0}
    except Exception:
        return None


def _clips_publies() -> dict[str, dict]:
    """zernio_post_id -> meta des clips publiés (pour relier stats et fichiers locaux)."""
    import json

    liens = {}
    for meta_path in QUEUE.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if meta.get("zernio_post_id"):
            liens[meta["zernio_post_id"]] = meta
    return liens


def synchroniser() -> int:
    """Stats par vidéo. Voie NATIVE d'abord (Display API video.list — s'active dès que le
    scope est accordé par l'audit) ; repli Zernio si le service historique est configuré."""
    n = synchroniser_tiktok()
    if n:
        return n
    if CONFIG["publication"].get("service") == "zernio" and CONFIG["publication"].get("api_key"):
        return _synchroniser_zernio()
    return 0


def _lier_fichier_local(titre_tiktok: str, liens_titres: dict[str, str]) -> str | None:
    """Relie une vidéo TikTok à un clip local par son titre (meilleur effort)."""
    t = (titre_tiktok or "").lower()
    for titre_local, fichier in liens_titres.items():
        if titre_local and titre_local in t:
            return fichier
    return None


def synchroniser_tiktok() -> int:
    """API native `/v2/video/list/` : vues/likes/commentaires/partages par vidéo du compte.
    Requiert le scope `video.list` (post-audit) — sans lui : échec PROPRE journalisé, 0.
    Retourne le nb de vidéos mises à jour."""
    import json as _json

    import requests

    from . import journal
    from . import tiktok as tt

    try:
        tok = tt._token()
    except Exception:
        return 0  # compte non connecté : rien à faire
    # titres locaux (minuscules) -> fichier, pour relier stats et clips
    liens_titres = {}
    for meta_path in QUEUE.glob("*.json"):
        try:
            meta = _json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("titre"):
                liens_titres[meta["titre"].lower()] = meta["fichier"]
        except (OSError, ValueError):
            continue
    con = _connexion()
    n, cursor = 0, None
    for _ in range(5):  # pagination : 20/page, plafond 100 vidéos
        corps = {"max_count": 20}
        if cursor:
            corps["cursor"] = cursor
        r = requests.post(
            "https://open.tiktokapis.com/v2/video/list/",
            params={"fields": "id,title,create_time,view_count,like_count,"
                              "comment_count,share_count"},
            headers={"Authorization": f"Bearer {tok}",
                     "Content-Type": "application/json; charset=UTF-8"},
            json=corps, timeout=30)
        d = r.json()
        err = d.get("error") or {}
        if err.get("code") not in (None, "", "ok"):
            # scope absent tant que l'audit n'est pas validé : on le note et on sort proprement
            journal.log("stats_tiktok_indispo", code=err.get("code", "?"),
                        detail=str(err.get("message", ""))[:150])
            break
        data = d.get("data") or {}
        for v in data.get("videos", []):
            vid = str(v.get("id", ""))
            if not vid:
                continue
            publie_le = time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.localtime(int(v.get("create_time", 0)) or None))
            con.execute(
                """INSERT INTO stats_posts (post_id, fichier, titre, publie_le, url,
                     vues, impressions, likes, commentaires, partages, engagement, maj)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(post_id) DO UPDATE SET
                     vues=excluded.vues, likes=excluded.likes,
                     commentaires=excluded.commentaires, partages=excluded.partages,
                     maj=excluded.maj""",
                (f"tt_{vid}", _lier_fichier_local(v.get("title"), liens_titres),
                 (v.get("title") or "")[:80], publie_le, None,
                 int(v.get("view_count") or 0), 0,
                 int(v.get("like_count") or 0), int(v.get("comment_count") or 0),
                 int(v.get("share_count") or 0), 0.0,
                 time.strftime("%Y-%m-%d %H:%M:%S")))
            n += 1
        if not data.get("has_more"):
            break
        cursor = data.get("cursor")
    con.commit()
    con.close()
    return n


def _synchroniser_zernio() -> int:
    """(historique) Rapatrie les stats depuis Zernio. Conservé comme repli."""
    import requests

    pub = CONFIG["publication"]
    r = requests.get(
        "https://zernio.com/api/v1/analytics",
        headers={"Authorization": f"Bearer {pub['api_key']}"},
        params={"platform": "tiktok", "limit": 100, "sortBy": "date", "order": "desc"},
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    posts = data.get("posts") or data.get("data") or []
    liens = _clips_publies()
    con = _connexion()
    n = 0
    for p in posts:
        pid = p.get("postId") or p.get("_id")
        if not pid:
            continue
        a = p.get("analytics") or {}
        local = liens.get(pid, {})
        con.execute(
            """INSERT INTO stats_posts (post_id, fichier, titre, publie_le, url,
                 vues, impressions, likes, commentaires, partages, engagement, maj)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(post_id) DO UPDATE SET
                 vues=excluded.vues, impressions=excluded.impressions,
                 likes=excluded.likes, commentaires=excluded.commentaires,
                 partages=excluded.partages, engagement=excluded.engagement,
                 maj=excluded.maj""",
            (pid, local.get("fichier"), local.get("titre") or (p.get("content") or "")[:60],
             p.get("publishedAt"), p.get("platformPostUrl"),
             int(a.get("views") or 0), int(a.get("impressions") or 0),
             int(a.get("likes") or 0), int(a.get("comments") or 0),
             int(a.get("shares") or 0), float(a.get("engagementRate") or 0),
             time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        n += 1
    con.commit()
    con.close()
    return n


def toutes() -> list[dict]:
    con = _connexion()
    cur = con.execute("SELECT * FROM stats_posts ORDER BY publie_le DESC")
    colonnes = [c[0] for c in cur.description]
    lignes = [dict(zip(colonnes, l)) for l in cur.fetchall()]
    con.close()
    return lignes


def totaux() -> dict:
    con = _connexion()
    cur = con.execute("""SELECT COUNT(*), COALESCE(SUM(vues),0), COALESCE(SUM(likes),0),
                                COALESCE(SUM(commentaires),0), COALESCE(AVG(engagement),0)
                         FROM stats_posts""")
    n, vues, likes, comm, eng = cur.fetchone()
    con.close()
    return {"posts": n, "vues": vues, "likes": likes, "commentaires": comm,
            "engagement_moyen": round(eng or 0, 2)}


def par_fichier() -> dict[str, dict]:
    return {s["fichier"]: s for s in toutes() if s.get("fichier")}


_UA_WEB = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
           "Accept-Language": "fr-FR,fr;q=0.9"}


def _pseudo() -> str | None:
    for p in CONFIG.get("profils", {}).values():
        if p.get("pseudo"):
            return p["pseudo"].lstrip("@")
    return None


def _vues_embed(pseudo: str) -> dict[str, int]:
    """{video_id: vues} depuis la page d'EMBED publique de TikTok — elle pré-rend la grille
    (≈10 dernières vidéos) AVEC les compteurs, contrairement à la page de profil."""
    import json as _json
    import re

    import requests

    r = requests.get(f"https://www.tiktok.com/embed/@{pseudo}", headers=_UA_WEB, timeout=20)
    m = re.search(r'id="__FRONTITY_CONNECT_STATE__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
    if not m:
        return {}
    out: dict[str, int] = {}

    def walk(o):
        if isinstance(o, dict):
            if "playCount" in o and str(o.get("id", "")).isdigit():
                out[str(o["id"])] = int(o.get("playCount") or 0)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    try:
        walk(_json.loads(m.group(1)))
    except _json.JSONDecodeError:
        return {}
    return out


def _vues_video(pseudo: str, video_id: str) -> int | None:
    """Vues d'UNE vidéo depuis sa page publique (pour les anciennes, hors grille d'embed)."""
    import json as _json
    import re

    import requests

    try:
        r = requests.get(f"https://www.tiktok.com/@{pseudo}/video/{video_id}",
                         headers=_UA_WEB, timeout=20)
        m = re.search(r'id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
                      r.text, re.DOTALL)
        if m:
            stats_v = (_json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {})
                       .get("webapp.video-detail", {}).get("itemInfo", {})
                       .get("itemStruct", {}).get("stats", {}))
            if stats_v.get("playCount") is not None:
                return int(stats_v["playCount"])
        m2 = re.search(r'"playCount":(\d+)', r.text)
        return int(m2.group(1)) if m2 else None
    except Exception:
        return None


_CACHE_VUES: dict = {"quand": 0.0}


def rafraichir_vues_publiques(complet: bool = False) -> dict:
    """Met à jour les vues PAR VIDÉO depuis les pages publiques (embed + pages vidéo).
    Écrit dans les MÊMES lignes `tt_<id>` que video.list (post-audit) → convergence sans
    doublon. `complet=True` (tick quotidien) va aussi chercher les vidéos hors embed."""
    pseudo = _pseudo()
    if not pseudo:
        return {"vues": 0, "videos": 0}
    con = _connexion()
    # migration : les lignes Zernio dont l'URL contient l'id vidéo deviennent tt_<id>
    import re
    for pid, url in con.execute("SELECT post_id, url FROM stats_posts "
                                "WHERE post_id NOT LIKE 'tt_%' AND url LIKE '%/video/%'").fetchall():
        m = re.search(r"/video/(\d+)", url or "")
        if not m:
            continue
        cible = f"tt_{m.group(1)}"
        deja = con.execute("SELECT 1 FROM stats_posts WHERE post_id=?", (cible,)).fetchone()
        if deja:
            con.execute("DELETE FROM stats_posts WHERE post_id=?", (pid,))
        else:
            con.execute("UPDATE stats_posts SET post_id=? WHERE post_id=?", (cible, pid))
    maintenant = time.strftime("%Y-%m-%d %H:%M:%S")
    vus = _vues_embed(pseudo)
    for vid, vues in vus.items():
        con.execute(
            """INSERT INTO stats_posts (post_id, vues, maj) VALUES (?,?,?)
               ON CONFLICT(post_id) DO UPDATE SET vues=excluded.vues, maj=excluded.maj""",
            (f"tt_{vid}", vues, maintenant))
    if complet:  # vidéos connues mais absentes de la grille d'embed (les plus anciennes)
        connus = [r[0][3:] for r in con.execute(
            "SELECT post_id FROM stats_posts WHERE post_id LIKE 'tt_%'").fetchall()]
        for vid in [v for v in connus if v not in vus][:12]:
            v = _vues_video(pseudo, vid)
            if v is not None:
                con.execute("UPDATE stats_posts SET vues=?, maj=? WHERE post_id=?",
                            (v, maintenant, f"tt_{vid}"))
            time.sleep(0.8)  # politesse
    con.commit()
    vues_tot, n = con.execute(
        "SELECT COALESCE(SUM(vues),0), COUNT(*) FROM stats_posts WHERE post_id LIKE 'tt_%'").fetchone()
    con.close()
    _CACHE_VUES["quand"] = time.time()
    return {"vues": int(vues_tot), "videos": int(n)}


def vues_suivies() -> dict:
    """Vues cumulées du COMPTE : somme des vues par vidéo (lignes natives `tt_<id>`,
    alimentées par l'embed public — et par video.list après l'audit). Rafraîchi ≤ 1 fois
    par 10 min. Repli : anciennes lignes de suivi si aucune ligne native."""
    if time.time() - _CACHE_VUES["quand"] > 600:
        try:
            rafraichir_vues_publiques(complet=False)
        except Exception:
            pass  # réseau KO : on sert le dernier état connu
    con = _connexion()
    vues, posts, maj = con.execute(
        "SELECT COALESCE(SUM(vues),0), COUNT(*), MAX(maj) FROM stats_posts "
        "WHERE post_id LIKE 'tt_%'").fetchone()
    if not posts:  # repli : ancien suivi (Zernio) si rien de natif
        vues, posts, maj = con.execute(
            "SELECT COALESCE(SUM(vues),0), COUNT(*), MAX(maj) FROM stats_posts").fetchone()
    con.close()
    cpt = compte() or {}
    return {"vues": int(vues), "posts_suivis": int(posts),
            "videos_compte": max(int(cpt.get("videos") or 0), int(posts)),
            "maj": (maj or "")[5:16]}


_CACHE_SERIE: dict = {"quand": 0.0, "donnees": None}


def serie_quotidienne(jours: int = 30) -> list[dict]:
    """Série quotidienne [{date, abonnes, vues, likes}] construite depuis NOS relevés
    locaux (stats_compte) — indépendante de Zernio. Les vues/jour viennent de
    daily-metrics Zernio en meilleur effort (échec ignoré). Cache 5 min."""
    import requests

    if time.time() - _CACHE_SERIE["quand"] < 300 and _CACHE_SERIE["donnees"] is not None:
        return _CACHE_SERIE["donnees"]

    compte()  # garantit le relevé du jour dans stats_compte

    par_date: dict[str, dict] = {}
    for i in range(jours + 1):
        d = time.strftime("%Y-%m-%d", time.localtime(time.time() - (jours - i) * 86400))
        par_date[d] = {"date": d, "abonnes": None, "vues": 0, "likes": 0}

    con = _connexion()
    for d, ab in con.execute("SELECT date, abonnes FROM stats_compte"):
        if d in par_date:
            par_date[d]["abonnes"] = ab
    con.close()

    pub = CONFIG["publication"]
    if pub.get("api_key"):
        try:  # meilleur effort : un échec ici ne casse plus jamais la courbe abonnés
            depuis = time.strftime("%Y-%m-%d", time.localtime(time.time() - jours * 86400))
            r = requests.get(
                "https://zernio.com/api/v1/analytics/daily-metrics",
                headers={"Authorization": f"Bearer {pub['api_key']}"}, timeout=20,
                params={"platform": "tiktok", "fromDate": depuis, "attribution": "received"},
            )
            r.raise_for_status()
            for jour in r.json().get("dailyData", []):
                d = str(jour.get("date", ""))[:10]
                if d in par_date:
                    m = jour.get("metrics", {})
                    par_date[d]["vues"] = int(m.get("views") or 0)
                    par_date[d]["likes"] = int(m.get("likes") or 0)
        except Exception:
            pass

    # abonnés : propage la dernière valeur connue (jours sans relevé)
    courant = 0
    for d in sorted(par_date):
        if par_date[d]["abonnes"] is None:
            par_date[d]["abonnes"] = courant
        else:
            courant = par_date[d]["abonnes"]
    donnees = [par_date[d] for d in sorted(par_date)]
    _CACHE_SERIE["quand"] = time.time()
    _CACHE_SERIE["donnees"] = donnees
    return donnees


_CACHE_COMPTE: dict = {"quand": 0.0, "donnees": None}


def compte() -> dict | None:
    """Compteurs TikTok du compte, fiables par couches :
    1) page publique TikTok (temps réel, source officielle — ⚠️ ne compte QUE les vidéos
       publiques : les privées/en modération n'y figurent pas)  2) Zernio (repli historique,
    snapshot possiblement figé)  3) dernier relevé local. Chaque lecture fraîche est archivée."""
    import requests

    if time.time() - _CACHE_COMPTE["quand"] < 60:
        return _CACHE_COMPTE["donnees"]

    donnees = _profil_tiktok_public()
    source = "tiktok_public"

    if donnees is None:  # repli : Zernio (peut retarder — service abandonné)
        pub = CONFIG["publication"]
        if pub.get("api_key") and pub.get("zernio_account_id"):
            try:
                r = requests.get(
                    "https://zernio.com/api/v1/analytics/tiktok/account-insights",
                    headers={"Authorization": f"Bearer {pub['api_key']}"},
                    params={"accountId": pub["zernio_account_id"], "metricType": "total_value"},
                    timeout=15,
                )
                r.raise_for_status()
                m = r.json().get("metrics", {})
                donnees = {
                    "abonnes": m.get("follower_count", {}).get("total", 0),
                    "likes": m.get("likes_count", {}).get("total", 0),
                    "videos": m.get("video_count", {}).get("total", 0),
                    "abonnes_gagnes_30j": m.get("followers_gained", {}).get("total", 0),
                }
                source = "zernio"
            except Exception:
                donnees = None

    if donnees is not None:
        # le compteur videoCount de TikTok retarde (liste ≠ compteur) : dès que video.list
        # est actif (post-audit), le décompte RÉEL de la liste prime sur le compteur paresseux
        con = _connexion()
        n_natif = con.execute(
            "SELECT COUNT(*) FROM stats_posts WHERE post_id LIKE 'tt_%'").fetchone()[0]
        con.close()
        if n_natif > donnees.get("videos", 0):
            donnees["videos"] = n_natif
        donnees["releve_a"] = time.strftime("%H:%M")  # fraîcheur visible dans l'UI
        donnees["source"] = source
        _snapshot_compte(donnees, source)
        # delta 30 j depuis NOS relevés (plus fiable que le champ Zernio)
        con = _connexion()
        row = con.execute(
            "SELECT abonnes FROM stats_compte WHERE date <= date('now','-30 day') "
            "ORDER BY date DESC LIMIT 1").fetchone()
        base = row[0] if row else con.execute(
            "SELECT abonnes FROM stats_compte ORDER BY date ASC LIMIT 1").fetchone()
        con.close()
        if base is not None:
            base_val = base if isinstance(base, int) else base[0]
            donnees["abonnes_gagnes_30j"] = donnees["abonnes"] - base_val
    else:
        # dernier relevé local : jamais d'écran vide
        con = _connexion()
        row = con.execute("SELECT abonnes, likes, videos FROM stats_compte "
                          "ORDER BY date DESC LIMIT 1").fetchone()
        con.close()
        if row:
            donnees = {"abonnes": row[0], "likes": row[1], "videos": row[2],
                       "abonnes_gagnes_30j": 0}
    _CACHE_COMPTE["quand"] = time.time()
    _CACHE_COMPTE["donnees"] = donnees
    return donnees
