"""SeedBoxBuntu - notifications push dans le navigateur (Web Push, RFC 8030, 8291 et 8292), sans service tiers de notre part.

Chaque appareil qui active les notifications dans la console enregistre un abonnement (adresse du service de push de son
navigateur et deux clés). Les alertes sont chiffrées pour cet appareil (aes128gcm) et signées avec une clé VAPID propre au
serveur, puis envoyées au service de push du navigateur (Google, Mozilla, Apple, Microsoft) ; ce service ne voit que du
texte chiffré. Une alerte arrive donc même console fermée, sans ntfy.
Limites : il faut HTTPS (la console l'est), un navigateur compatible, et le module python3-cryptography sur le serveur.
Seules les adresses des services de push connus sont acceptées (le serveur n'envoie jamais vers une adresse choisie par un utilisateur).
    seedboxctl push show | test | send TITRE MESSAGE
"""
import base64
import json
import os
import re
import time
import urllib.parse
import urllib.request

import seedboxctl as ctl

SUBS_FILE = ctl.STATE / "webpush-subs.json"
KEY_FILE = ctl.ETC / "webpush-vapid.pem"
ALLOWED_SUFFIXES = (".googleapis.com", ".push.services.mozilla.com", ".push.apple.com", ".notify.windows.com", ".push.microsoft.com")
MAX_PER_USER = 5
MAX_TOTAL = 200
RECORD_SIZE = 4096
LOGIN = re.compile(r"^[a-z][a-z0-9_.-]{1,30}$")
B64 = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


def b64e(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64d(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def crypto():
    """Modules de python3-cryptography, ou SeedboxError claire s'ils manquent."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError:
        raise ctl.SeedboxError("le paquet python3-cryptography est absent : sudo apt install python3-cryptography")
    return {"hashes": hashes, "serialization": serialization, "ec": ec, "dss": decode_dss_signature, "AESGCM": AESGCM, "HKDF": HKDF}


def available():
    try:
        crypto()
        return True
    except ctl.SeedboxError:
        return False


# --- clés VAPID -----------------------------------------------------------------------------------
def vapid_key():
    c = crypto()
    if KEY_FILE.exists():
        return c["serialization"].load_pem_private_key(KEY_FILE.read_bytes(), None)
    key = c["ec"].generate_private_key(c["ec"].SECP256R1())
    pem = key.private_bytes(c["serialization"].Encoding.PEM, c["serialization"].PrivateFormat.PKCS8, c["serialization"].NoEncryption())
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(pem)
    return key


def public_key(key=None):
    c = crypto()
    key = key or vapid_key()
    return b64e(key.public_key().public_bytes(c["serialization"].Encoding.X962, c["serialization"].PublicFormat.UncompressedPoint))


def vapid_header(endpoint, key=None, now=None, subject=None):
    """En-tête Authorization « vapid t=JWT, k=clé » (RFC 8292) pour l'origine de l'adresse d'envoi."""
    c = crypto()
    key = key or vapid_key()
    parts = urllib.parse.urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int((time.time() if now is None else now) + 12 * 3600),
              "sub": subject or subject_default()}
    head = b64e(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64e(json.dumps(claims, separators=(",", ":")).encode())
    signing = f"{head}.{body}".encode()
    der = key.sign(signing, c["ec"].ECDSA(c["hashes"].SHA256()))
    r, s = c["dss"](der)
    signature = b64e(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={head}.{body}.{signature}, k={public_key(key)}"


def subject_default():
    env = ctl.env_read()
    email = env.get("ACME_EMAIL", "")
    return f"mailto:{email}" if email and "@" in email else "mailto:seedbox@seedboxbuntu.invalid"


# --- chiffrement (RFC 8291) ---------------------------------------------------------------------------
def encrypt(payload, p256dh, auth, as_private=None, salt=None):
    """Corps aes128gcm d'un message pour un abonnement (clés en base64 URL)."""
    c = crypto()
    ec, hashes, hkdf = c["ec"], c["hashes"], c["HKDF"]
    ua_public_bytes, auth_secret = b64d(p256dh), b64d(auth)
    if len(ua_public_bytes) != 65 or ua_public_bytes[0] != 4 or len(auth_secret) != 16:
        raise ctl.SeedboxError("clés d'abonnement invalides")
    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public_bytes)
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    as_public = as_private.public_key().public_bytes(c["serialization"].Encoding.X962, c["serialization"].PublicFormat.UncompressedPoint)
    shared = as_private.exchange(ec.ECDH(), ua_public)
    salt = salt or os.urandom(16)
    ikm = hkdf(algorithm=hashes.SHA256(), length=32, salt=auth_secret, info=b"WebPush: info\x00" + ua_public_bytes + as_public).derive(shared)
    cek = hkdf(algorithm=hashes.SHA256(), length=16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = hkdf(algorithm=hashes.SHA256(), length=12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    data = payload.encode() if isinstance(payload, str) else payload
    if len(data) > RECORD_SIZE - 17 - 1:
        raise ctl.SeedboxError("message trop long pour un seul enregistrement push")
    ciphertext = c["AESGCM"](cek).encrypt(nonce, data + b"\x02", None)
    return salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public + ciphertext


# --- abonnements --------------------------------------------------------------------------------------
def endpoint_ok(endpoint):
    parts = urllib.parse.urlsplit(endpoint or "")
    host = (parts.hostname or "").lower()
    return (parts.scheme == "https" and len(endpoint) <= 600 and not parts.username and parts.port in (None, 443)
            and any(host.endswith(suffix) or host == suffix[1:] for suffix in ALLOWED_SUFFIXES))


def _read():
    try:
        value = json.loads(SUBS_FILE.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data):
    SUBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = SUBS_FILE.with_name(SUBS_FILE.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(data))
    os.replace(temp, SUBS_FILE)


def subscribe(user, subscription):
    if not LOGIN.match(user or ""):
        raise ctl.SeedboxError("compte inconnu")
    endpoint = (subscription or {}).get("endpoint", "")
    keys = (subscription or {}).get("keys") or {}
    if not endpoint_ok(endpoint):
        raise ctl.SeedboxError("service de notifications non reconnu")
    for name in ("p256dh", "auth"):
        if not isinstance(keys.get(name), str) or not B64.match(keys[name]) or len(keys[name]) > 120:
            raise ctl.SeedboxError("clés d'abonnement invalides")
    encrypt("x", keys["p256dh"], keys["auth"])                         # les clés doivent permettre de chiffrer
    data = _read()
    mine = [s for s in data.get(user, []) if s["endpoint"] != endpoint]
    if sum(len(v) for v in data.values()) - len(data.get(user, [])) + len(mine) >= MAX_TOTAL:
        raise ctl.SeedboxError("trop d'appareils enregistrés")
    mine.append({"endpoint": endpoint, "p256dh": keys["p256dh"], "auth": keys["auth"], "added": int(time.time())})
    data[user] = mine[-MAX_PER_USER:]
    _write(data)
    return len(data[user])


def unsubscribe(user, endpoint):
    data = _read()
    before = len(data.get(user, []))
    data[user] = [s for s in data.get(user, []) if s["endpoint"] != endpoint]
    if not data[user]:
        data.pop(user)
    _write(data)
    return before - len(data.get(user, []))


def count(user=None):
    data = _read()
    return len(data.get(user, [])) if user else sum(len(v) for v in data.values())


def has_subscribers():
    return available() and count() > 0


def status(user):
    ok = available()
    return {"available": ok, "key": public_key() if ok else "", "mine": count(user), "total": count()}


# --- envoi ----------------------------------------------------------------------------------------------
def post(endpoint, body, headers, timeout=15):
    """Envoi HTTP ; renvoie le code. Remplaçable dans les tests."""
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def send(title, message, users=None, url="/console/#alerts", poster=None):
    """Envoie à tous les appareils (ou à ceux des comptes donnés). Renvoie {envoyés, retirés, erreurs}. Un abonnement
    périmé (404, 410) est supprimé."""
    poster = poster or post
    key = vapid_key()
    payload = json.dumps({"title": str(title)[:100], "body": str(message)[:300], "url": url, "tag": "seedbox-alert"}, ensure_ascii=False)
    data = _read()
    sent = removed = 0
    errors = []
    for user, subs in list(data.items()):
        if users is not None and user not in users:
            continue
        keep = []
        for sub in subs:
            try:
                body = encrypt(payload, sub["p256dh"], sub["auth"])
                code = poster(sub["endpoint"], body, {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                                                      "TTL": "86400", "Urgency": "high", "Authorization": vapid_header(sub["endpoint"], key)})
            except (OSError, ctl.SeedboxError) as error:
                errors.append(str(error)[:80])
                keep.append(sub)
                continue
            if code in (404, 410):
                removed += 1
            elif code >= 400:
                errors.append(f"HTTP {code}")
                keep.append(sub)
            else:
                sent += 1
                keep.append(sub)
        data[user] = keep
    data = {u: s for u, s in data.items() if s}
    _write(data)
    return {"sent": sent, "removed": removed, "errors": errors[:5]}
