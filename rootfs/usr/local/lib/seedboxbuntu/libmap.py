"""SeedBoxBuntu - carte des bibliothèques par partage, et versions multiples d'un même titre.

Carte : pour chaque volume (le disque local et chaque partage réseau), le nombre de fichiers et de titres, l'espace pris,
la répartition par résolution et par codec, le débit moyen (Mo par minute de vidéo) et l'espace libre du volume.
Versions multiples : un titre présent en plusieurs fichiers (deux éditions, 1080p et 4K d'un film ; plusieurs fichiers pour
un même épisode) ou enregistré deux fois dans Radarr. Le gain annoncé est celui de la version la plus lourde moins la
plus légère à garder ; rien n'est supprimé ici : la page propose, vous décidez dans Radarr ou Sonarr.
Limites assumées : tout vient du relevé (libscan) ; deux titres sont jugés identiques par leur nom et leur année, pas par leur
contenu ; la durée manque tant que les applications n'ont pas analysé le fichier (le débit moyen l'ignore alors).
    seedboxctl libmap show | versions
"""
import re

import seedboxctl as ctl

SHOWN = 40


def say(message):
    print(f"[seedboxctl] {message}", flush=True)


def _bucket(resolution):
    return {0: "inconnue", 2160: "4K", 1080: "1080p", 720: "720p"}.get(resolution) or ("480p ou moins" if 0 < resolution < 720 else f"{resolution}p")


def _codec(name):
    name = (name or "").lower()
    if any(x in name for x in ("265", "hevc")):
        return "x265 (HEVC)"
    if any(x in name for x in ("264", "avc")):
        return "x264 (AVC)"
    if "av1" in name:
        return "AV1"
    return name.upper() if name else "inconnu"


def build(records, space=None):
    """Carte par volume à partir des enregistrements du relevé."""
    space = space or {}
    volumes = {}
    for r in records:
        v = volumes.setdefault(r["volume"], {"volume": r["volume"], "files": 0, "size": 0, "titles": set(), "resolutions": {}, "codecs": {},
                                              "bytes_timed": 0, "seconds_timed": 0})
        v["files"] += 1
        v["size"] += r["size"]
        v["titles"].add((r["app"], r["id"]))
        label = _bucket(r["resolution"])
        entry = v["resolutions"].setdefault(label, {"files": 0, "size": 0})
        entry["files"] += 1
        entry["size"] += r["size"]
        label = _codec(r["codec"])
        entry = v["codecs"].setdefault(label, {"files": 0, "size": 0})
        entry["files"] += 1
        entry["size"] += r["size"]
        if r.get("seconds", 0) >= 600:
            v["bytes_timed"] += r["size"]
            v["seconds_timed"] += r["seconds"]
    result = []
    for name, v in sorted(volumes.items(), key=lambda kv: -kv[1]["size"]):
        total, free = space.get(name, (None, None))
        result.append({"volume": name, "files": v["files"], "titles": len(v["titles"]), "size": v["size"],
                       "mb_per_min": round(v["bytes_timed"] / (1 << 20) / (v["seconds_timed"] / 60), 1) if v["seconds_timed"] else None,
                       "total": total, "free": free,
                       "resolutions": [{"label": k, **e} for k, e in sorted(v["resolutions"].items(), key=lambda kv: -kv[1]["size"])],
                       "codecs": [{"label": k, **e} for k, e in sorted(v["codecs"].items(), key=lambda kv: -kv[1]["size"])]})
    return result


def versions(records):
    """Titres en plusieurs versions : [{title, kind, files:[...], saving}], les plus gros gains d'abord."""
    groups = {}
    for r in records:
        if r["app"] == "radarr":
            groups.setdefault((r["title"].strip().lower(), r.get("year", 0)), []).append(r)
    found = []
    for rows in groups.values():
        if len({(r["id"], r["file_id"]) for r in rows}) > 1:       # deux fichiers, ou deux fiches Radarr du même titre
            found.append(_entry("film", rows))
    found.extend(_episode_versions(records))
    found.sort(key=lambda e: -e["saving"])
    return found


def _episode_versions(records):
    """Un épisode en deux fichiers : même série, même saison, même numéro lu dans le nom du fichier (SxxEyy)."""
    groups = {}
    for r in records:
        if r["app"] != "sonarr":
            continue
        match = re.search(r"[Ss](\d{1,2})[Ee](\d{1,3})", r["path"].rsplit("/", 1)[-1])
        if match:
            groups.setdefault((r["id"], int(match.group(1)), int(match.group(2))), []).append(r)
    return [_entry("épisode", rows) for rows in groups.values() if len(rows) > 1]


def _entry(kind, rows):
    rows = sorted(rows, key=lambda r: -r["size"])
    return {"kind": kind, "title": rows[0]["title"], "year": rows[0].get("year", 0), "saving": sum(r["size"] for r in rows[1:]),
            "files": [{"path": r["path"], "size": r["size"], "quality": r["quality"], "volume": r["volume"], "app": r["app"]} for r in rows]}


def overview():
    libscan = ctl.libscan()
    data = libscan.load()
    records = data["records"]
    found = versions(records)
    return {"at": data.get("at", 0), "errors": data.get("errors", {}), "files": len(records),
            "volumes": build(records, libscan.volumes_space()),
            "versions": found[:SHOWN], "versions_total": len(found), "versions_saving": sum(e["saving"] for e in found)}
