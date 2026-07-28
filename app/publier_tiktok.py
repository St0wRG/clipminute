"""Envoi d'un clip vers TikTok en tâche de fond (lancé par le dashboard).

Affiche la progression de l'upload (% dans le log → jauge de la tâche), puis suit
le traitement TikTok jusqu'à SEND_TO_USER_INBOX / PUBLISH_COMPLETE.
"""
import argparse
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))

from pipeline import journal, publish  # noqa: E402
from pipeline import tiktok as tt  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fichier", required=True)
    ap.add_argument("--user", help="e-mail du compte (isolation des données)")
    ap.add_argument("--mode", default="draft", choices=["draft", "direct"])
    ap.add_argument("--titre", default="")
    ap.add_argument("--privacy", default="SELF_ONLY")
    ap.add_argument("--duet", action="store_true")
    ap.add_argument("--stitch", action="store_true")
    ap.add_argument("--comment", action="store_true")
    a = ap.parse_args()
    from pipeline.common import set_user
    set_user(a.user)  # isole les données sur CE compte avant tout accès

    # même garde-fou que la voie automatique : le plafond quotidien s'applique à TOUS les chemins
    from pipeline.common import CONFIG
    maxi = int(CONFIG["publication"].get("max_posts_jour", 3))
    if publish._posts_du_jour() >= maxi:
        journal.log("echec_publication", fichier=a.fichier,
                    raison=f"plafond {maxi} envois/jour atteint", via="tiktok")
        print(f"ERREUR : plafond de sécurité atteint ({maxi} envois TikTok aujourd'hui). "
              "C'est ce qui protège du blocage anti-spam — réessaie demain "
              "(ou monte max_posts_jour dans Réglages en connaissance de cause).", flush=True)
        return 1

    mp4 = APP / "queue" / a.fichier
    mo = mp4.stat().st_size / 1048576
    print(f"Envoi vers TikTok : {a.fichier} ({mo:.1f} Mo)…", flush=True)

    dernier = [-5]

    def progression(lu, total):
        pct = int(lu * 100 / total) if total else 0
        if pct >= dernier[0] + 5:  # ne loguer que tous les 5 %
            dernier[0] = pct
            print(f"  upload en cours ({pct}%)", flush=True)

    try:
        if a.mode == "direct":
            pid = tt.publier_direct(mp4, a.titre, a.privacy, a.duet, a.stitch, a.comment, progression)
        else:
            pid = tt.publier_brouillon(mp4, progression)
    except Exception as e:
        journal.log("echec_publication", fichier=a.fichier, raison=str(e)[:300], via="tiktok")
        print(f"ERREUR : {e}", flush=True)
        return 1

    print(f"  upload terminé (100%) — traitement TikTok (id {pid})…", flush=True)
    statut_final = None
    for _ in range(24):
        time.sleep(5)
        try:
            s = tt.statut(pid).get("status")
        except Exception:
            continue
        print(f"  statut : {s}", flush=True)
        if s in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
            statut_final = s
            break
        if s == "FAILED":
            journal.log("echec_publication", fichier=a.fichier, raison="statut FAILED", via="tiktok")
            print("ERREUR : TikTok a rejeté la vidéo (FAILED)", flush=True)
            return 1

    meta = publish.lire_meta(a.fichier)
    meta["statut"] = "publie" if a.mode == "direct" else "brouillon_tiktok"
    meta["tiktok_publish_id"] = pid
    publish.ecrire_meta(a.fichier, meta)
    from pipeline import nettoyage
    nettoyage.liberer_espace(a.fichier)  # le clip est parti : on libère le disque
    journal.log("tiktok_" + a.mode, fichier=a.fichier, publish_id=pid, statut=statut_final)
    cible = "profil TikTok" if a.mode == "direct" else "brouillons TikTok (ouvre l'appli pour finaliser)"
    print(f"OK -> envoyé dans tes {cible}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
