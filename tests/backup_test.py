"""Sauvegarde : contenu, chiffrement, exclusions, refus d'archives piégées, restauration (sans Docker)."""
import os as _os
SRC = _os.environ.get("SB_SRC") or _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import io, os, subprocess, sys, tarfile, tempfile
from pathlib import Path
root = Path(tempfile.mkdtemp(prefix="sbbk"))
for k, d in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[k] = str(root / d)
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as ctl, backup as bk
bad = 0
def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:200])
B, E, S = ctl.BASE, ctl.ETC, ctl.STATE
for p, t in {B/".env": "SECRET=abc\n", B/"config/radarr/config.xml": "<a/>", B/"config/jellyfin/cache/x": "cache",
             B/"config/jellyfin/metadata/m": "meta", B/"config/sonarr/logs/l.txt": "log", E/"ca.key": "CAKEY", E/"shares.json": "{}",
             S/"apps.json": "{}", B/"docker-compose.local.yml": "x: 1\n", B/"downloads/film.mkv": "media"}.items():
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(t)
name = bk.create()
file = bk.find(name)
check("archive créée, mode 0600", file.exists() and oct(file.stat().st_mode)[-3:] == "600", name)
check("archive chiffrée (pas du gzip)", file.read_bytes()[:2] != b"\x1f\x8b" and file.read_bytes()[:8] == b"Salted__")
check("clé créée 0600", bk.KEY_FILE.exists() and oct(bk.KEY_FILE.stat().st_mode)[-3:] == "600")
with tempfile.TemporaryDirectory() as w:
    plain = bk.open_archive(file, bk.KEY_FILE.read_text(), w)
    names = tarfile.open(plain).getnames()
    has = lambda frag: any(frag in n for n in names)
    check("contient .env, config radarr, ca.key, apps.json", has("opt/seedbox/.env") and has("radarr/config.xml") and has("ca.key") and has("apps.json"))
    check("exclut caches, journaux, métadonnées Jellyfin", not has("jellyfin/cache") and not has("sonarr/logs") and not has("jellyfin/metadata"))
    check("exclut les médias et la clé de chiffrement", not has("film.mkv") and not has("backup.key"))
try:
    with tempfile.TemporaryDirectory() as w: bk.open_archive(file, "mauvaise-cle", w)
    check("mauvaise clé refusée", False)
except ctl.SeedboxError as e:
    check("mauvaise clé refusée", "clé" in str(e), str(e))
def forged(member, kind="file", link=""):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as t:
        info = tarfile.TarInfo(member)
        if kind == "sym": info.type = tarfile.SYMTYPE; info.linkname = link; t.addfile(info)
        else: data = b"x"; info.size = 1; t.addfile(info, io.BytesIO(data))
    path = Path(tempfile.mkdtemp()) / "seedbox-20200101-000000.tar.gz.enc"
    subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-pass", f"file:{bk.KEY_FILE}", "-out", str(path)], input=raw.getvalue(), check=True)
    return path
for label, member, kind, link in [("chemin hors périmètre (etc/passwd)", "etc/passwd", "file", ""), ("remontée ../", "../etc/passwd", "file", ""),
                                  ("chemin absolu", "/etc/passwd", "file", ""), ("lien symbolique vers l'extérieur", str((B/"config").relative_to("/"))+"/evil", "sym", "/etc/shadow")]:
    try:
        with tempfile.TemporaryDirectory() as w: bk.open_archive(forged(member, kind, link), bk.KEY_FILE.read_text(), w)
        check(f"archive piégée refusée : {label}", False)
    except ctl.SeedboxError as e:
        check(f"archive piégée refusée : {label}", True, str(e)[:60])
# rétention
for i in range(5):
    p = bk.LOCAL_DIR / f"seedbox-2020010{i+1}-000000.tar.gz.enc"; p.write_bytes(b"x"); os.utime(p, (1000+i, 1000+i))
bk.set_settings({"keep": 3})
bk.prune(bk.LOCAL_DIR, 3)
left = [e["name"] for e in bk.listing()]
check("rétention : 3 conservées", len([n for n in left if "-avant" not in n]) == 3, left)
# restauration (sans Docker)
(B/".env").write_text("SECRET=modifie\n"); (B/"config/radarr/config.xml").write_text("<changed/>")
calls = []
ctl.compose = lambda *a, **k: calls.append(a); ctl.ensure_env = lambda: None; ctl.apply_addresses = lambda: []
bk.restore(name)
check("restauration : .env et config remis", (B/".env").read_text() == "SECRET=abc\n" and (B/"config/radarr/config.xml").read_text() == "<a/>")
check("restauration : pile arrêtée puis sauvegarde « avant » faite", calls and any("-avant" in e["name"] for e in bk.listing()))
check("réglages : jours et heure validés", bk.validate({"days": "mon,wed", "time": "04:15"})["time"] == "04:15")
try: bk.validate({"keep": "x"}); check("keep invalide refusé", False)
except ctl.SeedboxError: check("keep invalide refusé", True)
print("ECHECS:", bad); sys.exit(1 if bad else 0)
