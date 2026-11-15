"""Notifications push : chiffrement RFC 8291 (vecteur de l'annexe A), signature VAPID, abonnements, liste blanche des
adresses d'envoi, envoi et nettoyage des abonnements périmés (sans réseau)."""
import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(os.environ.get("SB_SRC") or Path(__file__).resolve().parents[1])
root = Path(tempfile.mkdtemp(prefix="sbpush"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
sys.path.insert(0, str(SRC / "rootfs/usr/local/lib/seedboxbuntu"))
import seedboxctl as ctl  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:220], flush=True)


def raises(function, *args):
    try:
        function(*args)
    except ctl.SeedboxError as error:
        return str(error) or "erreur"
    return None


for folder in (ctl.STATE, ctl.ETC, ctl.BASE):
    folder.mkdir(parents=True, exist_ok=True)
wp = ctl.webpush()
if not wp.available():
    print("push_test : ignoré (python3-cryptography absent)")
    sys.exit(0)
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: E402

PUBLIC = serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint

# --- 1. chiffrement : vecteur de l'annexe A de la RFC 8291 -----------------------------------------------------
AS_PRIVATE = ec.derive_private_key(int.from_bytes(wp.b64d("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
body = wp.encrypt("When I grow up, I want to be a watermelon",
                  "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4", "BTBZMqHH6r4Tts7J_aSIgg",
                  as_private=AS_PRIVATE, salt=wp.b64d("DGv6ra1nlYgDCS1FRnbzlw"))
EXPECTED = ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")
check("RFC 8291 annexe A : corps chiffré identique au vecteur", wp.b64e(body) == EXPECTED, wp.b64e(body)[:60])


def decrypt(body, ua_private, auth_secret):
    """Déchiffrement côté navigateur, écrit d'après la RFC (indépendant du code testé)."""
    salt, size, key_len = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    as_public = body[21:21 + key_len]
    ua_public = ua_private.public_key().public_bytes(*PUBLIC)
    shared = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = HKDF(hashes.SHA256(), 32, auth_secret, b"WebPush: info\x00" + ua_public + as_public).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    plain = AESGCM(cek).decrypt(nonce, body[21 + key_len:], None)
    return plain.rstrip(b"\x00")[:-1], size


device = ec.generate_private_key(ec.SECP256R1())
auth = os.urandom(16)
p256dh, auth_text = wp.b64e(device.public_key().public_bytes(*PUBLIC)), wp.b64e(auth)
message, size = decrypt(wp.encrypt("Radarr ne répond plus ✓", p256dh, auth_text), device, auth)
check("chiffrement : l'appareil retrouve le message (accents compris)", message.decode() == "Radarr ne répond plus ✓" and size == 4096, message)
check("chiffrement : deux envois du même texte ne se ressemblent pas", wp.encrypt("x", p256dh, auth_text) != wp.encrypt("x", p256dh, auth_text))
check("chiffrement : clés invalides et message trop long refusés", raises(wp.encrypt, "x", "AAAA", auth_text) and raises(wp.encrypt, "x" * 5000, p256dh, auth_text))

# --- 2. clé et signature VAPID ---------------------------------------------------------------------------------
key = wp.vapid_key()
mode = stat.S_IMODE(wp.KEY_FILE.stat().st_mode)
check("VAPID : clé créée en 0600 et stable d'un appel à l'autre", mode == 0o600 and wp.public_key() == wp.public_key(wp.vapid_key()), oct(mode))
header = wp.vapid_header("https://fcm.googleapis.com/fcm/send/abc", key, now=1_800_000_000, subject="mailto:a@b.fr")
token = header.split("t=")[1].split(",")[0]
head_text, body_text, sig_text = token.split(".")
claims = json.loads(wp.b64d(body_text))
check("VAPID : audience = origine du service, expiration < 24 h, sujet", claims["aud"] == "https://fcm.googleapis.com" and claims["exp"] - 1_800_000_000 <= 24 * 3600 and claims["sub"] == "mailto:a@b.fr", claims)
signature = wp.b64d(sig_text)
try:
    key.public_key().verify(encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")), f"{head_text}.{body_text}".encode(), ec.ECDSA(hashes.SHA256()))
    check("VAPID : signature ES256 valide avec la clé publique annoncée", header.endswith("k=" + wp.public_key(key)))
except Exception as error:  # noqa: BLE001
    check("VAPID : signature ES256 valide avec la clé publique annoncée", False, error)

# --- 3. liste blanche des adresses d'envoi ---------------------------------------------------------------------
GOOD = ["https://fcm.googleapis.com/fcm/send/abc", "https://updates.push.services.mozilla.com/wpush/v2/x", "https://web.push.apple.com/Q1", "https://wns2-par02p.notify.windows.com/w/?token=x"]
EVIL = ["http://fcm.googleapis.com/x", "https://fcm.googleapis.com.evil.example/x", "https://evil.example/fcm.googleapis.com", "https://user@fcm.googleapis.com/x",
        "https://fcm.googleapis.com:8443/x", "https://127.0.0.1/x", "https://169.254.169.254/latest", "file:///etc/passwd", "", "https://evilgoogleapis.com/x"]
check("adresses : services de push connus acceptés", all(wp.endpoint_ok(e) for e in GOOD), [e for e in GOOD if not wp.endpoint_ok(e)])
check("adresses : SSRF (http, IP, sous-domaine piégé, identifiants, port) refusées", not any(wp.endpoint_ok(e) for e in EVIL), [e for e in EVIL if wp.endpoint_ok(e)])

# --- 4. abonnements --------------------------------------------------------------------------------------------
sub = lambda endpoint, **over: {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth_text, **over}}
check("abonnement : appareil enregistré, fichier en 0600", wp.subscribe("marie", sub(GOOD[0])) == 1 and stat.S_IMODE(wp.SUBS_FILE.stat().st_mode) == 0o600)
check("abonnement : le même appareil ne s'ajoute pas deux fois", wp.subscribe("marie", sub(GOOD[0])) == 1 and wp.count("marie") == 1)
check("abonnement : adresse hors liste, clés invalides, compte invalide refusés",
      raises(wp.subscribe, "marie", sub(EVIL[1])) and raises(wp.subscribe, "marie", sub(GOOD[1], p256dh="AAAA")) and raises(wp.subscribe, "marie", sub(GOOD[1], auth="@@@"))
      and raises(wp.subscribe, "../x", sub(GOOD[1])) and raises(wp.subscribe, "marie", {}) and wp.count("marie") == 1)
for i in range(8):
    wp.subscribe("marie", sub(f"https://fcm.googleapis.com/fcm/send/{i}"))
check("abonnement : au plus 5 appareils par compte", wp.count("marie") == wp.MAX_PER_USER, wp.count("marie"))
wp.subscribe("paul", sub(GOOD[2]))
st = wp.status("paul")
check("état : disponibilité, clé publique pour le navigateur, mes appareils et total", st["available"] and st["key"] == wp.public_key() and st["mine"] == 1 and st["total"] == 6, st)

# --- 5. envoi --------------------------------------------------------------------------------------------------
calls = []


def poster(endpoint, body, headers, timeout=15):
    calls.append((endpoint, headers))
    return {"3": 410, "4": 404, "5": 500}.get(endpoint.rsplit("/", 1)[-1], 201)


result = wp.send("Radarr ne répond plus", "depuis 12 minutes", poster=poster)
check("envoi : tous les appareils, abonnements périmés (404, 410) retirés, erreur 500 conservée et comptée",
      result["sent"] == 3 and result["removed"] == 2 and len(result["errors"]) == 1 and wp.count("marie") == 3, result)
check("envoi : en-têtes TTL, chiffrement et signature VAPID", all(h["Content-Encoding"] == "aes128gcm" and h["Authorization"].startswith("vapid t=") and h["TTL"] for _, h in calls))
calls.clear()
wp.send("Test", "ciblé", users=["paul"], poster=poster)
check("envoi : ciblé sur un compte", len(calls) == 1 and calls[0][0] == GOOD[2])
wp.unsubscribe("paul", GOOD[2])
check("désabonnement : appareil retiré, compte vide supprimé du fichier", wp.count("paul") == 0 and "paul" not in json.loads(wp.SUBS_FILE.read_text()))
check("canal d'alerte : actif seulement s'il existe des abonnés", ctl.alerts()._push_configured(ctl.alerts().settings()) is True)
for i in range(8):
    wp.unsubscribe("marie", f"https://fcm.googleapis.com/fcm/send/{i}")
wp.unsubscribe("marie", GOOD[0])
check("canal d'alerte : inactif sans abonné", ctl.alerts()._push_configured(ctl.alerts().settings()) is False)

print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
