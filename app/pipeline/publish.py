"""Publication et cycle de vie des clips.

Statuts d'un clip dans queue/ (champ "statut" du JSON) :
  en_attente      -> produit, aucune date de publication
  planifie        -> date dans "publication_prevue", le scheduler le publiera
  pret_a_publier  -> échéance atteinte mais AUCUN service API branché (rien de simulé)
  publie          -> parti sur la plateforme (nécessite un service configuré)
  echec           -> tentative de publication ratée (voir journal)

L'adaptateur API réel (Zernio/Postiz/...) se branche dans _envoyer_via_service()
dès qu'Alex fournit le service + la clé dans config.json.
"""
import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from . import journal
from .common import CONFIG, QUEUE


def _meta_path(fichier: str) -> Path:
    return (QUEUE / Path(fichier).name).with_suffix(".json")


def lire_meta(fichier: str) -> dict:
    return json.loads(_meta_path(fichier).read_text(encoding="utf-8"))


def ecrire_meta(fichier: str, meta: dict) -> None:
    _meta_path(fichier).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def publish(mp4: Path, caption: str, titre: str, profil: str = "principal",
            adn: dict | None = None) -> Path:
    """Dépose un clip produit dans la file avec ses métadonnées.
    `adn` = empreinte de production (hook, sujet, durée, sfx…) — la matière première
    de la boucle d'apprentissage (rapport hebdo : ADN des tops vs flops)."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = QUEUE / f"{stamp}_{mp4.name}"
    shutil.copy2(mp4, dest)
    meta = {
        "fichier": dest.name,
        "titre": titre,
        "caption": caption,
        "profil": profil,
        "cree_le": time.strftime("%Y-%m-%d %H:%M:%S"),
        "statut": "en_attente",
    }
    if adn:
        meta["adn"] = adn
    ecrire_meta(dest.name, meta)
    journal.log("clip_produit", fichier=dest.name, titre=titre, profil=profil)
    return dest


def marquer_a_publier(fichier: str) -> dict:
    """Semi-auto : le clip est prêt, en attente de publication manuelle par l'utilisateur."""
    meta = lire_meta(fichier)
    meta["statut"] = "pret_a_publier"
    ecrire_meta(fichier, meta)
    journal.log("clip_a_publier", fichier=fichier, titre=meta.get("titre", ""))
    return meta


def marquer_publie_manuel(fichier: str) -> dict:
    """L'utilisateur a publié ce clip lui-même depuis TikTok : on l'enregistre comme publié."""
    meta = lire_meta(fichier)
    meta["statut"] = "publie"
    meta["publie_le"] = time.strftime("%Y-%m-%d %H:%M:%S")
    meta["url"] = "publié manuellement"
    ecrire_meta(fichier, meta)
    journal.log("publie", fichier=fichier, url="manuel")
    return meta


def planifier(fichier: str, quand: str) -> dict:
    """Programme la publication (quand = 'YYYY-MM-DD HH:MM')."""
    datetime.strptime(quand, "%Y-%m-%d %H:%M")  # valide le format
    meta = lire_meta(fichier)
    meta["statut"] = "planifie"
    meta["publication_prevue"] = quand
    ecrire_meta(fichier, meta)
    journal.log("publication_planifiee", fichier=fichier, quand=quand)
    return meta


_EVENEMENTS_ENVOI = ("publie", "tiktok_draft", "tiktok_direct")  # TOUTES les voies comptent


def _posts_du_jour() -> int:
    """Envois du jour, toutes voies confondues (Zernio, brouillon natif, direct natif) —
    le plafond max_posts_jour protège chaque chemin, pas seulement l'ancien."""
    aujourdhui = time.strftime("%Y-%m-%d")
    return sum(1 for e in journal.recents(300)
               if e["evenement"] in _EVENEMENTS_ENVOI and e["quand"].startswith(aujourdhui))


def _dernier_envoi() -> datetime | None:
    """Horodatage du dernier envoi réel (toutes voies), ou None si aucun. Base du garde-fou
    anti-rafale : le plafond journalier laissait passer 9 posts en 52 min (incident du 18/07),
    exactement le motif que TikTok pénalise. Ici on connaît l'écart au dernier post."""
    for e in journal.recents(300):  # déjà trié du plus récent au plus ancien
        if e["evenement"] in _EVENEMENTS_ENVOI:
            try:
                return datetime.strptime(e["quand"], "%Y-%m-%d %H:%M:%S")
            except (ValueError, KeyError):
                continue
    return None


def _envoyer_via_service(meta: dict) -> str:
    """Envoie réellement le clip via le service API configuré. Retourne l'URL/id du post."""
    service = CONFIG["publication"].get("service")
    api_key = CONFIG["publication"].get("api_key")
    if not service or not api_key:
        raise ServiceManquant(
            "Aucun service de publication configuré (config.json -> publication.service/api_key). "
            "Le clip est marqué 'prêt à publier'."
        )
    if service == "tiktok":
        return _tiktok_publier(meta)
    if service == "zernio":
        return _zernio_publier(meta)
    raise ServiceManquant(f"Service '{service}' inconnu — adaptateur à brancher dans publish.py.")


def _tiktok_publier(meta: dict) -> str:
    """Publication NATIVE via l'app TikTok d'Alex (pipeline.tiktok) — remplace Zernio.
    publication.tiktok_mode : 'brouillon' (video.upload, marche dès la sandbox ; Alex finalise
    dans l'appli) ou 'direct' (video.publish — exige l'audit validé)."""
    from . import tiktok as tt

    pub = CONFIG["publication"]
    mp4 = QUEUE / meta["fichier"]
    mode = pub.get("tiktok_mode", "brouillon")
    try:
        if mode == "direct":
            pid = tt.publier_direct(mp4, meta.get("caption") or meta.get("titre", ""),
                                    pub.get("privacy_level", "SELF_ONLY"),
                                    duet=True, stitch=True, comment=True)
        else:
            pid = tt.publier_brouillon(mp4)
    except tt.ErreurTikTok as e:
        # quotas/anti-spam plateforme -> QuotaTikTok (reprogrammation, pas un échec brûlé)
        if e.code.startswith("spam_risk") or e.code == "reached_active_user_cap" or "quota" in e.code:
            raise QuotaTikTok(str(e))
        raise
    meta["tiktok_publish_id"] = pid
    cible = "profil (direct)" if mode == "direct" else "brouillons TikTok"
    return f"TikTok natif -> {cible} — publish_id {pid}"


def _zernio_publier(meta: dict, privacy: str | None = None) -> str:
    """Publie via Zernio : presign -> upload direct -> création du post TikTok."""
    import requests

    pub = CONFIG["publication"]
    base = "https://zernio.com/api/v1"
    headers = {"Authorization": f"Bearer {pub['api_key']}"}
    mp4 = QUEUE / meta["fichier"]

    r = requests.post(f"{base}/media/presign", headers=headers, timeout=30,
                      json={"filename": mp4.name, "contentType": "video/mp4",
                            "content_type": "video/mp4"})
    r.raise_for_status()
    pres = r.json()
    upload_url, public_url = pres["uploadUrl"], pres["publicUrl"]

    with open(mp4, "rb") as f:
        up = requests.put(upload_url, data=f,
                          headers={"Content-Type": "video/mp4"}, timeout=900)
    up.raise_for_status()

    # multi-plateformes (V5.1) : tous les comptes configurés dans publication.zernio_comptes
    comptes = pub.get("zernio_comptes") or {"tiktok": pub["zernio_account_id"]}
    plateformes = [{"platform": plateforme, "accountId": cid}
                   for plateforme, cid in comptes.items() if cid]
    corps = {
        "content": meta.get("caption") or meta.get("titre", ""),
        "mediaItems": [{"type": "video", "url": public_url}],
        "platforms": plateformes,
        "tiktokSettings": {
            "privacy_level": privacy or pub.get("privacy_level", "PUBLIC_TO_EVERYONE"),
            "allow_comment": True,
            "allow_duet": True,
            "allow_stitch": True,
            "content_preview_confirmed": True,
            "express_consent_given": True,
        },
        "publishNow": True,
    }
    r2 = requests.post(f"{base}/posts", headers=headers, json=corps, timeout=120)
    if r2.status_code >= 400:
        txt = r2.text[:400]
        bas = txt.lower()
        # quota TikTok journalier (app partagée) : inutile de réessayer tout de suite
        if any(m in bas for m in ("quota", "try again later", "rate limit",
                                  "too many", "daily active user")):
            raise QuotaTikTok(txt)
        raise RuntimeError(f"Zernio a refusé le post ({r2.status_code}) : {txt}")
    post = r2.json()
    post_id = (post.get("post") or {}).get("_id") or post.get("_id") or "?"
    meta["zernio_post_id"] = post_id
    return f"post Zernio {post_id} -> {', '.join(c['platform'] for c in plateformes)}"


class ServiceManquant(RuntimeError):
    pass


class QuotaTikTok(RuntimeError):
    """Quota journalier TikTok atteint (limite plateforme, pas une panne)."""


def _reprogrammer(fichier: str, meta: dict, heures: float) -> None:
    from datetime import datetime, timedelta
    quand = (datetime.now() + timedelta(hours=heures)).strftime("%Y-%m-%d %H:%M")
    meta["statut"] = "planifie"
    meta["publication_prevue"] = quand
    ecrire_meta(fichier, meta)


def publier(fichier: str) -> tuple[bool, str]:
    """Tente la publication immédiate. Retourne (succès, message)."""
    meta = lire_meta(fichier)
    if CONFIG["publication"].get("pause"):
        meta["statut"] = "pret_a_publier"
        ecrire_meta(fichier, meta)
        journal.log("publication_en_pause", fichier=fichier)
        return False, "Publication EN PAUSE (config) — clip marqué prêt à publier, rien n'est parti."
    maxi = int(CONFIG["publication"].get("max_posts_jour", 3))
    if _posts_du_jour() >= maxi:
        msg = f"Plafond de sécurité atteint ({maxi} posts/jour) — publication refusée."
        journal.log("echec_publication", fichier=fichier, raison=msg)
        return False, msg
    # garde-fou ANTI-RAFALE : jamais deux posts trop rapprochés (motif anti-spam TikTok).
    # Ne bloque pas — reprogramme juste après la fenêtre, le scheduler reprendra le clip.
    espacement = float(CONFIG["publication"].get("espacement_min_heures", 4))
    dernier = _dernier_envoi() if espacement > 0 else None
    if dernier is not None:
        ecoule = (datetime.now() - dernier).total_seconds() / 3600
        if ecoule < espacement:
            reste = round(espacement - ecoule, 2)
            _reprogrammer(fichier, meta, reste)
            journal.log("anti_rafale", fichier=fichier, ecoule_h=round(ecoule, 2),
                        espacement_h=espacement, reprogramme=meta["publication_prevue"])
            return False, (f"Anti-rafale : dernier post il y a {ecoule:.1f} h "
                           f"(min {espacement:.0f} h) — reprogrammé dans {reste:.1f} h.")
    try:
        url = _envoyer_via_service(meta)
    except ServiceManquant as e:
        meta["statut"] = "pret_a_publier"
        ecrire_meta(fichier, meta)
        journal.log("service_manquant", fichier=fichier, detail=str(e))
        return False, str(e)
    except QuotaTikTok as e:
        # quota journalier : on repousse après la remise à zéro (pas d'échec, pas de compteur brûlé)
        meta["reports_quota"] = int(meta.get("reports_quota", 0)) + 1
        if meta["reports_quota"] <= 3:
            heures = float(CONFIG["publication"].get("report_quota_heures", 11))
            _reprogrammer(fichier, meta, heures)
            journal.log("quota_tiktok", fichier=fichier, report=meta["reports_quota"],
                        nouvelle_tentative=meta["publication_prevue"], detail=str(e)[:200])
            return False, f"Quota TikTok atteint — reprogrammé dans {heures:.0f} h ({meta['publication_prevue']})."
        meta["statut"] = "pret_a_publier"
        ecrire_meta(fichier, meta)
        journal.log("quota_tiktok", fichier=fichier, report=meta["reports_quota"],
                    abandon="passage en publication manuelle", detail=str(e)[:200])
        return False, "Quota TikTok atteint 3× — clip laissé 'prêt à publier' (publication manuelle)."
    except Exception as e:
        # 3 tentatives avec reprise par le scheduler avant l'échec définitif (V10.4)
        meta["tentatives"] = int(meta.get("tentatives", 0)) + 1
        if meta["tentatives"] < 3 and meta.get("publication_prevue"):
            meta["statut"] = "planifie"
            ecrire_meta(fichier, meta)
            journal.log("echec_publication", fichier=fichier, raison=str(e)[:300],
                        tentative=meta["tentatives"], retentative="au prochain passage")
            return False, f"Échec (tentative {meta['tentatives']}/3, nouvelle tentative au prochain passage) : {e}"
        meta["statut"] = "echec"
        ecrire_meta(fichier, meta)
        journal.log("echec_publication", fichier=fichier, raison=str(e)[:300],
                    tentative=meta.get("tentatives", 1))
        return False, f"Échec de publication : {e}"
    meta["statut"] = "publie"
    meta["publie_le"] = time.strftime("%Y-%m-%d %H:%M:%S")
    meta["url"] = url
    ecrire_meta(fichier, meta)
    journal.log("publie", fichier=fichier, url=url)
    return True, url


def purger_publies(jours: int = 30) -> int:
    """Fait de la place disque : supprime mp4 + meta des clips publiés/envoyés en brouillon
    depuis plus de `jours` jours (l'historique durable vit dans journal.jsonl et stats.db)."""
    seuil = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - jours * 86400))
    n = 0
    for meta_path in QUEUE.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("statut") not in ("publie", "brouillon_tiktok"):
            continue
        quand = meta.get("publie_le") or meta.get("cree_le") or ""
        if not quand or quand > seuil:
            continue
        (QUEUE / meta["fichier"]).unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        journal.log("clip_purge", fichier=meta["fichier"], publie_le=quand)
        n += 1
    return n


def echeances() -> list[dict]:
    """Clips planifiés dont l'échéance est passée."""
    maintenant = time.strftime("%Y-%m-%d %H:%M")
    dus = []
    for meta_path in QUEUE.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("statut") == "planifie" and meta.get("publication_prevue", "9999") <= maintenant:
            dus.append(meta)
    return dus
