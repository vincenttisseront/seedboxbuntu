"""SeedBoxBuntu - sous-titres manquants.

Liste les titres dont AUCUN fichier n'a de sous-titres dans une des langues voulues (français et anglais par défaut) alors que la
langue parlée n'en fait pas partie : quelqu'un qui ne comprend que le français regarde un film uniquement en japonais sans
sous-titres. Un titre dont une piste audio est déjà dans une langue voulue n'est pas signalé.
Source : l'analyse des fichiers par Radarr et Sonarr (champs « langues audio » et « sous-titres » du relevé des bibliothèques).
Lecture seule : la console ne va pas chercher les sous-titres elle-même ; la liste sert à les chercher avec l'outil de votre choix
(Jellyfin, un plugin de sous-titres...).
Limites assumées : seuls les sous-titres INTÉGRÉS au fichier sont vus par Radarr et Sonarr ; des fichiers .srt à côté du film ne
comptent pas : un titre pourvu de .srt peut donc être signalé à tort. Un fichier non encore analysé (champs vides) n'est pas signalé.
    seedboxctl subsgap show | set langs fra eng...
"""
import json
import os
import re

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "subsgap.json"
DEFAULTS = {"langs": ["fra", "eng"]}
LANG = re.compile(r"^[a-z]{3}$")
SHOWN = 100
ALIASES = {"fr": "fra", "fre": "fra", "en": "eng", "de": "deu", "ger": "deu", "es": "spa", "it": "ita", "ja": "jpn", "ko": "kor", "pt": "por",
           "ru": "rus", "zh": "zho", "chi": "zho", "ar": "ara", "nl": "nld", "dut": "nld", "pl": "pol", "tr": "tur", "cs": "ces", "cze": "ces",
           "el": "ell", "gre": "ell", "ro": "ron", "rum": "ron"}


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def settings():
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        stored = {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def save(values):
    cfg = settings()
    for key, value in values.items():
        if key != "langs":
            raise ctl.SeedboxError(f"réglage inconnu : {key}")
        if not isinstance(value, list) or not 1 <= len(value) <= 8 or not all(isinstance(v, str) and LANG.match(v) for v in value):
            raise ctl.SeedboxError("langues : codes de 3 lettres (fra, eng...), 1 à 8")
        cfg[key] = value
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cfg))
    os.chmod(SETTINGS_FILE, 0o644)
    return cfg


def codes(text):
    """« English / French », « eng / fra » ou « fr,en » -> ensemble de codes à 3 lettres."""
    slim = ctl.mediaslim()
    out = set()
    for part in re.split(r"[/,]", str(text or "")):
        part = part.strip().lower()
        if not part:
            continue
        code = slim._lang_code(part)
        out.add(ALIASES.get(code, code) if code else ALIASES.get(part, ""))
    out.discard("")
    return out


def gaps(records, cfg=None):
    """[{app, id, title, size, audio, subs, missing}] : titres sans sous-titres voulus ni audio voulu."""
    cfg = cfg or settings()
    wanted = set(cfg["langs"])
    titles = {}
    for r in records:
        if not r.get("audio"):                           # fichier pas encore analysé : on ne dit rien
            continue
        t = titles.setdefault((r["app"], r["id"]), {"app": r["app"], "id": r["id"], "title": r["title"], "size": 0, "audio": set(), "subs": set(), "files": 0})
        t["size"] += r["size"]
        t["files"] += 1
        t["audio"] |= codes(r["audio"])
        t["subs"] |= codes(r.get("subs", ""))
    found = []
    for t in titles.values():
        if t["audio"] & wanted or t["subs"] & wanted:
            continue
        found.append({**t, "audio": sorted(t["audio"]), "subs": sorted(t["subs"]), "missing": sorted(wanted)})
    return sorted(found, key=lambda t: -t["size"])


def overview(records=None):
    records = ctl.libscan().records() if records is None else records
    found = gaps(records)
    return {"settings": settings(), "total": len(found), "titles": found[:SHOWN], "analysed": sum(1 for r in records if r.get("audio")), "files": len(records)}
