"""SeedBoxBuntu - export et import des réglages de la console, sans restaurer toute une sauvegarde.

Un fichier JSON lisible reprend : alertes (canaux et seuils), rapport hebdomadaire, sauvegarde automatique (jours, heure,
nombre de copies), fenêtre de maintenance, ressources par conteneur, analyse de vulnérabilités, activité par personne,
langue des messages envoyés et préférences d'affichage de chaque compte. Il sert à préparer une autre seedbox ou à
conserver sa configuration à part.
Ne sont jamais exportés : mots de passe, clés et jetons des comptes et des applications, la clé de chiffrement des
sauvegardes, les partages réseau et les identifiants VPN, les abonnements push, la copie hors serveur. Les secrets des canaux
d'alerte (jeton ntfy, webhook Discord, mot de passe du courrier) ne sortent que si on les demande (--secrets), et
le fichier est alors à protéger comme un mot de passe.
L'import vérifie chaque section avec les règles de la console elle-même ; « à blanc », il dit seulement ce qui changerait.
    seedboxctl config export [--secrets] | import [--apply]
"""
import json
import time

import seedboxctl as ctl

FORMAT = "seedboxbuntu-config"
VERSION = 1
MAX_BYTES = 256 * 1024
SECRET_MASK = "********"


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


# Chaque section : (libellé, lire(secrets), appliquer(valeur), comparer(valeur) -> valeur normalisée courante)
def _alerts_view(cfg):
    """Réglages d'alertes sans secret : le sujet ntfy en est un (qui le connaît peut lire et écrire les messages)."""
    out = ctl.alerts().masked(cfg)
    if out["ntfy"].get("topic"):
        out["ntfy"]["topic"] = SECRET_MASK
    return out


def _alerts_get(secrets):
    module = ctl.alerts()
    return module.settings() if secrets else _alerts_view(module.settings())


def _backup_get(secrets):
    cfg = dict(ctl.backup().settings())
    cfg.pop("destination", None)                               # nom de partage propre à chaque serveur
    return cfg


def _backup_set(value):
    value = {k: v for k, v in value.items() if k != "destination"}
    return ctl.backup().set_settings(value)


def _prefs_get(secrets):
    return json.loads(json.dumps(ctl.notifications()._read(ctl.notifications().PREFS_FILE, {})))


def _prefs_set(value):
    module = ctl.notifications()
    if not isinstance(value, dict) or len(value) > module.MAX_USERS:
        raise ctl.SeedboxError("préférences : forme invalide")
    for user, prefs in value.items():
        module.prefs_set(user, prefs)


SECTIONS = {
    "alerts": ("Alertes", _alerts_get, lambda v: ctl.alerts().save(v)),
    "report": ("Rapport hebdomadaire", lambda s: ctl.weekly_report().settings(), lambda v: ctl.weekly_report().save(v)),
    "backup": ("Sauvegarde automatique", _backup_get, _backup_set),
    "window": ("Fenêtre de maintenance", lambda s: ctl.maintwindow().settings(), lambda v: ctl.maintwindow().save(v)),
    "resources": ("Ressources par conteneur", lambda s: ctl.resources().settings(), lambda v: ctl.resources().save(v)),
    "vulnscan": ("Analyse de vulnérabilités", lambda s: ctl.vulnscan().settings(), lambda v: ctl.vulnscan().save(v)),
    "activity": ("Activité par personne", lambda s: ctl.activity().settings(), lambda v: ctl.activity().save(v)),
    "language": ("Langue des messages envoyés", lambda s: {"notify": ctl.texts().language()}, lambda v: ctl.texts().save(v.get("notify", "fr"))),
    "ui_prefs": ("Préférences d'affichage par compte", _prefs_get, _prefs_set),
}


def export(secrets=False):
    sections = {}
    for key, (_, getter, _) in SECTIONS.items():
        try:
            sections[key] = getter(secrets)
        except Exception as error:  # noqa: BLE001 - une section illisible n'empêche pas les autres
            say(f"export : {key} ignoré ({str(error)[:80]})")
    return {"format": FORMAT, "version": VERSION, "created": int(time.time()), "secrets": bool(secrets),
            "from": ctl.version(), "sections": sections}


def parse(text):
    if len(text or "") > MAX_BYTES:
        raise ctl.SeedboxError("fichier trop volumineux pour une configuration de console")
    try:
        bundle = json.loads(text)
    except ValueError:
        raise ctl.SeedboxError("ce fichier n'est pas du JSON")
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        raise ctl.SeedboxError("ce fichier n'est pas une configuration SeedBoxBuntu")
    if bundle.get("version") != VERSION:
        raise ctl.SeedboxError(f"version de fichier non prise en charge ({bundle.get('version')})")
    if not isinstance(bundle.get("sections"), dict):
        raise ctl.SeedboxError("aucune section dans le fichier")
    return bundle


def _same(a, b):
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def plan(bundle):
    """Pour chaque section connue du fichier : état « inchangé », « modifié » ou « refusé » (avec la raison). Rien n'est écrit."""
    rows = []
    for key, value in bundle["sections"].items():
        if key not in SECTIONS:
            rows.append({"section": key, "title": key, "state": "ignorée", "detail": "section inconnue de cette version"})
            continue
        title, getter, _ = SECTIONS[key]
        try:
            current = getter(False)
            incoming = value
            if key == "alerts":                                # même normalisation que l'enregistrement : secrets masqués
                incoming = _alerts_view(ctl.alerts().validate(value))
            elif key == "ui_prefs" and isinstance(value, dict):
                module = ctl.notifications()
                incoming = {u: {**current.get(u, {}), **module.clean(p)} for u, p in value.items()}
                incoming = {**current, **incoming}
            elif isinstance(current, dict) and isinstance(value, dict):
                incoming = {**current, **value}
            rows.append({"section": key, "title": title, "state": "inchangée" if _same(current, incoming) else "modifiée", "detail": ""})
        except ctl.SeedboxError as error:
            rows.append({"section": key, "title": title, "state": "refusée", "detail": str(error)})
        except Exception as error:  # noqa: BLE001
            rows.append({"section": key, "title": title, "state": "refusée", "detail": str(error)[:120]})
    return rows


def apply(bundle, only=None):
    """Applique les sections modifiées et valides. Renvoie le plan avec l'état final de chaque section."""
    rows = plan(bundle)
    for row in rows:
        key = row["section"]
        if row["state"] != "modifiée" or (only and key not in only):
            continue
        try:
            SECTIONS[key][2](bundle["sections"][key])
            row["state"] = "appliquée"
        except ctl.SeedboxError as error:
            row["state"], row["detail"] = "refusée", str(error)
    changed = [r["title"] for r in rows if r["state"] == "appliquée"]
    if changed:
        ctl.notifications().record("config", "Configuration importée", ", ".join(changed)[:250], "info", "")
        say("configuration importée : " + ", ".join(changed))
    return rows
