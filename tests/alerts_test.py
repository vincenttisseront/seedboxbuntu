"""Alertes : validation, masquage des secrets, canaux (ntfy, webhook, e-mail), anti-rebond, rétablissement."""
import os as _os
SRC = _os.environ.get("SB_SRC") or _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import json, os, socket, sys, tempfile, threading, http.server
from pathlib import Path
root = Path(tempfile.mkdtemp(prefix="sbal"))
for k, d in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[k] = str(root / d); (root / d).mkdir(parents=True, exist_ok=True)
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as ctl, alerts as al
bad = 0
def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:190])
got = []
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        got.append((self.path, dict(self.headers), body)); self.send_response(200); self.end_headers()
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]
mails = []
def smtp():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1); smtp.port = s.getsockname()[1]; ready.set()
    c, _ = s.accept(); f = c.makefile("rwb"); f.write(b"220 test\r\n"); f.flush(); data = False; buf = []
    while True:
        line = f.readline()
        if not line: break
        if data:
            if line == b".\r\n": data = False; f.write(b"250 ok\r\n"); f.flush(); continue
            buf.append(line); continue
        cmd = line[:4].upper()
        if cmd in (b"EHLO", b"HELO"): f.write(b"250 test\r\n")
        elif cmd == b"DATA": data = True; f.write(b"354 go\r\n")
        elif cmd == b"QUIT": f.write(b"221 bye\r\n"); f.flush(); break
        else: f.write(b"250 ok\r\n")
        f.flush()
    mails.append(b"".join(buf).decode()); c.close()
ready = threading.Event(); threading.Thread(target=smtp, daemon=True).start(); ready.wait()
# validation
for label, vals in [("webhook Discord invalide refusé", {"discord": {"webhook": "http://evil.example/x"}}), ("sujet ntfy invalide refusé", {"ntfy": {"topic": "a b/c"}}),
                    ("adresse e-mail invalide refusée", {"email": {"to": "pas-un-mail"}}), ("URL file:// refusée", {"webhook": {"url": "file:///etc/passwd"}})]:
    try: al.validate(vals); check(label, False)
    except ctl.SeedboxError: check(label, True)
al.save({"ntfy": {"url": f"http://127.0.0.1:{port}", "topic": "sb-test", "token": "tok123"}, "webhook": {"url": f"http://127.0.0.1:{port}/hook", "secret": "s3cret"},
         "email": {"host": "127.0.0.1", "port": smtp.port, "security": "none", "sender": "sb@example.com", "to": "admin@example.com"}})
check("réglages en 0600", oct(al.SETTINGS_FILE.stat().st_mode)[-3:] == "600")
m = al.masked()
check("secrets masqués vers la console", m["ntfy"]["token"] == al.MASK and m["webhook"]["secret"] == al.MASK and "tok123" not in json.dumps(al.overview()))
al.save(m)   # renvoi du masque : secrets conservés
check("masque renvoyé = secret inchangé", al.settings()["ntfy"]["token"] == "tok123")
r = al.notify("Disque presque plein", "91 % utilisés", "critical")
check("trois canaux répondent", r == {"ntfy": None, "webhook": None, "email": None}, r)
n = next(x for x in got if x[0] == "/sb-test")
check("ntfy : sujet, titre, priorité, jeton, corps", n[1].get("Title") == "Disque presque plein" and n[1].get("Priority") == "5" and n[1].get("Authorization") == "Bearer tok123" and n[2] == "91 % utilisés".encode())
w = next(x for x in got if x[0] == "/hook"); body = json.loads(w[2])
check("webhook : JSON et secret", body["title"] == "Disque presque plein" and body["level"] == "critical" and {k.lower(): v for k, v in w[1].items()}.get("x-seedbox-secret") == "s3cret")
check("e-mail reçu", mails and "Disque presque plein" in mails[0] and "admin@example.com" in mails[0])
# anti-rebond
sent = []
al.notify = lambda title, msg, level="warning", force=False: sent.append((title, level))
state = {"v": {"x": ("Service x arrêté", "détail", "critical")}}
al.findings = lambda cfg=None: state["v"]
check("1er constat : rien d'envoyé", al.check() == 0 and not sent)
check("2e constat : alerte envoyée", al.check() == 1 and sent[-1][0] == "Service x arrêté")
check("3e constat : pas de doublon", al.check() == 0 and len(sent) == 1)
st = al.load_state(); st["x"]["notified"] -= 7 * 3600; al.STATE_FILE.write_text(json.dumps(st))
check("rappel après 6 h", al.check() == 1 and len(sent) == 2)
state["v"] = {}
check("rétabli annoncé", al.check() == 1 and sent[-1][0].startswith("Rétabli"))
check("plus rien en état", al.load_state() == {})
os.remove(al.SETTINGS_FILE); al.notify = lambda *a, **k: {}
try: al.test(); check("test sans canal : erreur claire", False)
except ctl.SeedboxError as e: check("test sans canal : erreur claire", "canal" in str(e))
print("ECHECS:", bad); sys.exit(1 if bad else 0)
