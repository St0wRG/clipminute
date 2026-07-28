"""Intégration native de l'API TikTok (Login Kit + Content Posting API).

Remplace Zernio par l'app développeur propre d'Alex (quota dédié).
- OAuth v2 : autorisation, échange du code, rafraîchissement du jeton.
- Content Posting : creator_info (conforme UX), upload en brouillon (video.upload)
  et direct post (video.publish), suivi du statut.

Aucune donnée simulée : tant que client_key/secret ne sont pas remplis, connecte() lève
une erreur explicite plutôt que de faire semblant.
"""
import json
import os
import time
from pathlib import Path

import requests

from .common import CONFIG as _CONFIG_COMMUN, data_root, recharger_config


class ErreurTikTok(RuntimeError):
    """Erreur API TikTok : message lisible + code d'origine (pour router quota/spam en amont)."""
    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code

AUTH = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
API = "https://open.tiktokapis.com/v2"
SCOPES = "user.info.basic,video.upload,video.publish"
def _chemin():
    _CONFIG_COMMUN.get("_")  # force la création du config.json du user (modèle) s'il manque
    return data_root() / "config.json"  # config isolée par utilisateur


def _cfg() -> dict:
    return json.loads(_chemin().read_text(encoding="utf-8"))


def _save(tt: dict) -> None:
    # écriture atomique : deux rafraîchissements de jeton concurrents ne peuvent pas
    # laisser un config.json à moitié écrit (fichier temporaire + os.replace)
    cfg = _cfg()
    cfg.setdefault("tiktok", {}).update(tt)
    p = _chemin()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    recharger_config()  # les jetons ont changé -> la config en cache du user est périmée


def configure() -> dict:
    tt = _cfg()["tiktok"]
    if not tt.get("client_key") or not tt.get("client_secret"):
        raise RuntimeError(
            "Identifiants TikTok manquants (config.json -> tiktok.client_key/client_secret). "
            "Colle-les depuis la section Credentials du portail développeur."
        )
    return tt


# ---------- OAuth ----------
def pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) pour TikTok. ⚠️ TikTok N'EST PAS conforme à la RFC 7636 :
    le challenge est l'encodage HEXADÉCIMAL de SHA256 (pas base64url). Vérifié dans leur doc
    « use hex encoding of SHA256 » — c'est LA cause du 'code verifier invalid' avec du base64url."""
    import hashlib
    import secrets
    verifier = secrets.token_urlsafe(64)
    challenge = hashlib.sha256(verifier.encode()).hexdigest()
    return verifier, challenge


def auth_url(state: str, code_challenge: str) -> str:
    tt = configure()
    from urllib.parse import urlencode
    q = urlencode({
        "client_key": tt["client_key"],
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": tt["redirect_uri"],
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    return f"{AUTH}?{q}"


def exchange_code(code: str, code_verifier: str) -> dict:
    tt = configure()
    data = {
        "client_key": tt["client_key"],
        "client_secret": tt["client_secret"],
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": tt["redirect_uri"],
        "code_verifier": code_verifier,
    }
    r = requests.post(TOKEN, timeout=30,
                      headers={"Content-Type": "application/x-www-form-urlencoded"}, data=data)
    d = r.json()
    if "access_token" not in d:
        raise RuntimeError(f"Échange du code échoué : {d}")
    _save({
        "access_token": d["access_token"],
        "refresh_token": d.get("refresh_token", ""),
        "open_id": d.get("open_id", ""),
        "expire_at": int(time.time()) + int(d.get("expires_in", 86400)) - 60,
    })
    return d


def _token() -> str:
    """Jeton d'accès valide (rafraîchi automatiquement si expiré)."""
    tt = configure()
    if not tt.get("access_token"):
        raise RuntimeError("Compte TikTok non connecté — lance la connexion depuis le dashboard.")
    if time.time() < tt.get("expire_at", 0):
        return tt["access_token"]
    # rafraîchir
    r = requests.post(TOKEN, timeout=30, headers={
        "Content-Type": "application/x-www-form-urlencoded"}, data={
        "client_key": tt["client_key"],
        "client_secret": tt["client_secret"],
        "grant_type": "refresh_token",
        "refresh_token": tt["refresh_token"],
    })
    d = r.json()
    if "access_token" not in d:
        raise RuntimeError(f"Rafraîchissement du jeton échoué (reconnecte-toi) : {d}")
    _save({
        "access_token": d["access_token"],
        "refresh_token": d.get("refresh_token", tt["refresh_token"]),
        "expire_at": int(time.time()) + int(d.get("expires_in", 86400)) - 60,
    })
    return d["access_token"]


def est_connecte() -> bool:
    try:
        return bool(_cfg()["tiktok"].get("access_token"))
    except Exception:
        return False


def deconnecter() -> None:
    _save({"access_token": "", "refresh_token": "", "open_id": "", "expire_at": 0})


# ---------- Infos compte / créateur ----------
def user_info() -> dict:
    tok = _token()
    r = requests.get(f"{API}/user/info/", timeout=20,
                     headers={"Authorization": f"Bearer {tok}"},
                     params={"fields": "open_id,union_id,avatar_url,display_name"})
    return (r.json().get("data") or {}).get("user") or {}


def creator_info() -> dict:
    """Renseignements exigés par l'UX TikTok AVANT tout post : options de confidentialité
    autorisées, interdictions duo/couture/commentaires, limites de durée, pseudo/avatar."""
    tok = _token()
    r = requests.post(f"{API}/post/publish/creator_info/query/", timeout=20,
                      headers={"Authorization": f"Bearer {tok}",
                               "Content-Type": "application/json; charset=UTF-8"})
    d = r.json()
    if (d.get("error") or {}).get("code") not in (None, "ok"):
        raise RuntimeError(f"creator_info a échoué : {d.get('error')}")
    return d.get("data") or {}


# ---------- Publication ----------
class _LecteurProgression:
    """Enveloppe un fichier pour signaler l'avancement de lecture (upload) sans changer
    le Content-Length (on garde un upload en un bloc, requis par TikTok)."""
    def __init__(self, f, total, cb):
        self.f, self.total, self.cb, self.lu = f, total, cb, 0

    def read(self, n=-1):
        chunk = self.f.read(n)
        if chunk:
            self.lu += len(chunk)
            self.cb(self.lu, self.total)
        return chunk

    def __len__(self):
        return self.total


def _upload_bytes(upload_url: str, mp4: Path, on_progress=None) -> None:
    taille = mp4.stat().st_size
    with open(mp4, "rb") as f:
        src = _LecteurProgression(f, taille, on_progress) if on_progress else f
        r = requests.put(upload_url, timeout=900, data=src, headers={
            "Content-Type": "video/mp4",
            "Content-Length": str(taille),
            "Content-Range": f"bytes 0-{taille - 1}/{taille}",
        })
    if r.status_code not in (200, 201, 206):
        raise RuntimeError(f"Upload des octets échoué ({r.status_code}) : {r.text[:300]}")


def _source_info(mp4: Path) -> dict:
    taille = mp4.stat().st_size
    return {"source": "FILE_UPLOAD", "video_size": taille,
            "chunk_size": taille, "total_chunk_count": 1}


# Traduction des codes d'erreur TikTok en messages clairs et actionnables (affichés dans la tâche).
_ERREURS_TT = {
    "spam_risk_too_many_pending_share":
        "Trop de brouillons en attente sur ton compte TikTok. Ouvre l'appli TikTok et "
        "finalise (publie) ou supprime les brouillons déjà envoyés, puis réessaie. "
        "TikTok bloque les nouveaux envois tant qu'il y en a trop en attente ; la limite "
        "se relâche aussi d'elle-même au bout d'un moment.",
    "spam_risk_user_banned_from_posting":
        "TikTok a temporairement bloqué la publication sur ce compte (anti-spam). Réessaie plus tard.",
    "reached_active_user_cap":
        "Quota d'utilisateurs actifs de l'app atteint (limite du mode non-audité). "
        "Ça se débloque avec l'audit validé.",
    "unaudited_client_can_only_post_to_private_accounts":
        "En mode non-audité, TikTok n'autorise l'envoi que vers des comptes privés.",
    "access_token_invalid":
        "Jeton TikTok invalide/expiré — reconnecte le compte dans Réglages.",
}


def _msg_erreur(err) -> str:
    """Message lisible depuis l'objet d'erreur TikTok (dict {code,message,...})."""
    if not isinstance(err, dict):
        return str(err)
    code = err.get("code") or ""
    if code in ("ok", "", None) and not err.get("message"):
        return str(err)
    return _ERREURS_TT.get(code) or err.get("message") or code or str(err)


def publier_brouillon(mp4: Path, on_progress=None) -> str:
    """video.upload : envoie la vidéo dans les brouillons TikTok (l'utilisateur finalise dans l'appli)."""
    tok = _token()
    r = requests.post(f"{API}/post/publish/inbox/video/init/", timeout=60,
                      headers={"Authorization": f"Bearer {tok}",
                               "Content-Type": "application/json; charset=UTF-8"},
                      json={"source_info": _source_info(mp4)})
    d = r.json()
    data = d.get("data") or {}
    if not data.get("upload_url"):
        err = d.get("error") if isinstance(d.get("error"), dict) else {}
        raise ErreurTikTok(_msg_erreur(err or d), code=err.get("code", ""))
    _upload_bytes(data["upload_url"], mp4, on_progress)
    return data.get("publish_id", "?")


def publier_direct(mp4: Path, titre: str, privacy: str,
                   duet: bool, stitch: bool, comment: bool, on_progress=None) -> str:
    """video.publish : poste directement sur le profil (confidentialité + options choisies par l'utilisateur)."""
    tok = _token()
    post_info = {
        "title": titre[:2200],
        "privacy_level": privacy,           # ex. PUBLIC_TO_EVERYONE / SELF_ONLY / MUTUAL_FOLLOW_FRIENDS
        "disable_duet": not duet,
        "disable_stitch": not stitch,
        "disable_comment": not comment,
        # miniature = frame du hook (grille du profil qui donne envie de binger)
        "video_cover_timestamp_ms": int((_cfg().get("publication") or {}).get("cover_ms", 1000)),
    }
    r = requests.post(f"{API}/post/publish/video/init/", timeout=60,
                      headers={"Authorization": f"Bearer {tok}",
                               "Content-Type": "application/json; charset=UTF-8"},
                      json={"post_info": post_info, "source_info": _source_info(mp4)})
    d = r.json()
    data = d.get("data") or {}
    if not data.get("upload_url"):
        err = d.get("error") if isinstance(d.get("error"), dict) else {}
        raise ErreurTikTok(_msg_erreur(err or d), code=err.get("code", ""))
    _upload_bytes(data["upload_url"], mp4, on_progress)
    return data.get("publish_id", "?")


def statut(publish_id: str) -> dict:
    tok = _token()
    r = requests.post(f"{API}/post/publish/status/fetch/", timeout=20,
                      headers={"Authorization": f"Bearer {tok}",
                               "Content-Type": "application/json; charset=UTF-8"},
                      json={"publish_id": publish_id})
    return (r.json().get("data") or {})
