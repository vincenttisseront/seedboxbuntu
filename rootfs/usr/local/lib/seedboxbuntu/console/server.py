#!/usr/bin/env python3
"""SeedBoxBuntu - console d'administration web.

Service de l'hôte, publié uniquement à travers Traefik et Authelia. Il écoute sur la
passerelle du réseau Docker : les autres conteneurs peuvent atteindre ce port, c'est
pourquoi chaque requête doit présenter le secret ajouté par Traefik. Le nom de
l'utilisateur vient des en-têtes posés par Authelia ; le groupe admins est exigé.

Bibliothèque standard de Python 3 uniquement : rien à installer.
"""
import hmac
import http.server
import json
import os
import re
import socketserver
import subprocess
import sys
import shutil
import threading
import urllib.parse
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import seedboxctl as ctl  # noqa: E402

LISTEN = (os.environ.get("SB_CONSOLE_HOST", "172.30.0.1"), int(os.environ.get("SB_CONSOLE_PORT", "8090")))
WEB = HERE / "web"
SEEDBOXCTL = HERE.parent / "seedboxctl.py"
MAX_BODY = 64 * 1024
JOBS = {}
JOBS_LOCK = threading.Lock()
PUBLIC_JOBS = set()                      # tâches lancées par une personne invitée : seul leur état est visible publiquement
ATTEMPTS = {}                            # adresse -> instants des essais d'invitation (limite de débit)
ATTEMPTS_LOCK = threading.Lock()
PUBLIC_FILES = {"app.css", "invite.js", "logo.svg", "favicon.ico", "icon-192.png"}
ATTEMPT_LIMIT, ATTEMPT_WINDOW = 12, 600   # essais par adresse et par 10 minutes
MAX_PUBLIC_JOBS = 3                       # créations de compte simultanées au plus
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2",
         ".webmanifest": "application/manifest+json"}


def start_job(title, argv, stdin=None):
    """Tâche longue dans un processus séparé : la console reste disponible et suit le journal."""
    identifier = uuid.uuid4().hex[:12]
    job = {"id": identifier, "title": title, "state": "running", "log": "", "started": time.time()}
    with JOBS_LOCK:
        JOBS[identifier] = job
        for key in sorted(JOBS, key=lambda k: JOBS[k]["started"])[:-30]:
            del JOBS[key]

    def worker():
        process = subprocess.Popen(
            [sys.executable, str(SEEDBOXCTL), *argv], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, env=dict(os.environ, PYTHONUNBUFFERED="1"))
        if stdin is not None:
            process.stdin.write(stdin + "\n")
        process.stdin.close()
        for line in process.stdout:
            job["log"] = (job["log"] + line)[-20000:]
        job["state"] = "ok" if process.wait() == 0 else "error"

    threading.Thread(target=worker, daemon=True).start()
    return identifier


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "SeedBoxBuntu"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, form, *args):
        sys.stderr.write("%s %s\n" % (self.headers.get("Remote-User", "-"), form % args))

    # --- réponses ---------------------------------------------------------------
    def send_json(self, payload, status=200):
        self.last_status = status
        self.last_payload = payload if isinstance(payload, dict) else {}
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, kind, filename):
        body = text.encode()
        self.last_status = 200
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        target = (WEB / path.lstrip("/")).resolve()
        if path == "/":
            target = WEB / "index.html"
        if not target.is_file() or WEB.resolve() not in target.parents:
            return self.send_json({"error": "introuvable"}, 404)
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # --- contrôle d'accès ---------------------------------------------------------
    def authorized(self):
        expected = ctl.env_read().get("CONSOLE_PROXY_SECRET", "")
        given = self.headers.get("X-Seedbox-Proxy-Secret", "")
        if not expected or not hmac.compare_digest(expected, given):
            self.send_json({"error": "accès direct refusé"}, 403)
            return False
        groups = [g.strip() for g in self.headers.get("Remote-Groups", "").split(",")]
        if "admins" not in groups:
            self.send_json({"error": "réservé aux administrateurs"}, 403)
            return False
        return True

    def body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ctl.SeedboxError("requête trop volumineuse")
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            # Un formulaire d'un autre site ne peut pas envoyer ce type sans contrôle CORS
            raise ctl.SeedboxError("type de contenu refusé")
        return json.loads(self.rfile.read(length) or b"{}")

    # --- routes -------------------------------------------------------------------
    # --- partie publique : invitations (Traefik sans Authelia) -----------------------------------------------------
    def public_request(self):
        """Requête arrivée par la route publique /invite : elle porte le secret public, jamais celui de l'administration."""
        return "X-Seedbox-Public-Secret" in self.headers

    def public_authorized(self):
        expected = ctl.env_read().get("CONSOLE_PROXY_SECRET", "")
        if not expected or not hmac.compare_digest(expected, self.headers.get("X-Seedbox-Public-Secret", "")):
            self.send_json({"error": "accès direct refusé"}, 403)
            return False
        return True

    def client_address_text(self):
        forwarded = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        return forwarded or self.client_address[0]

    def throttled(self, weight=1):
        """Au plus ATTEMPT_LIMIT essais par adresse et par ATTEMPT_WINDOW secondes."""
        now = time.time()
        with ATTEMPTS_LOCK:
            for key in [k for k, v in ATTEMPTS.items() if not v or now - v[-1] > ATTEMPT_WINDOW]:
                del ATTEMPTS[key]
            history = [t for t in ATTEMPTS.get(self.client_address_text(), []) if now - t < ATTEMPT_WINDOW]
            if len(history) + weight > ATTEMPT_LIMIT:
                ATTEMPTS[self.client_address_text()] = history
                return True
            ATTEMPTS[self.client_address_text()] = history + [now] * weight
        return False

    def send_public_file(self, name):
        target = (WEB / name).resolve()
        font = name.startswith("fonts/") and name.endswith(".woff2") and (WEB / "fonts").resolve() in target.parents
        if not (name in PUBLIC_FILES or font) or not target.is_file():
            return self.send_json({"error": "introuvable"}, 404)
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def send_invite_page(self):
        body = (WEB / "invite.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def public_get(self, path):
        if not self.public_authorized():
            return
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if path == "/invite/api/info":
            if self.throttled():
                return self.send_json({"error": "trop d'essais, réessayez dans quelques minutes"}, 429)
            return self.send_json(ctl.guests().info(query.get("token", [""])[0]))
        if path == "/invite/api/job":
            identifier = query.get("id", [""])[0]
            job = JOBS.get(identifier)
            if not job or identifier not in PUBLIC_JOBS:
                return self.send_json({"error": "introuvable"}, 404)
            return self.send_json({"state": job["state"]})            # jamais le journal : il peut contenir des détails internes
        match = re.fullmatch(r"/invite/static/((?:fonts/)?[A-Za-z0-9._-]+)", path)
        if match:
            return self.send_public_file(match.group(1))
        if re.fullmatch(r"/invite/[A-Za-z0-9_-]{0,64}/?", path):
            return self.send_invite_page()
        self.send_json({"error": "introuvable"}, 404)

    def public_post(self, path):
        if not self.public_authorized():
            return
        if path != "/invite/api/accept":
            return self.send_json({"error": "introuvable"}, 404)
        if self.throttled(2):
            return self.send_json({"error": "trop d'essais, réessayez dans quelques minutes"}, 429)
        data = self.body()
        token, login, password = str(data.get("token", "")), str(data.get("login", "")).strip().lower(), str(data.get("password", ""))
        with JOBS_LOCK:
            running = sum(1 for i in PUBLIC_JOBS if JOBS.get(i, {}).get("state") == "running")
        if running >= MAX_PUBLIC_JOBS:
            return self.send_json({"error": "plusieurs comptes sont en cours de création, réessayez dans une minute"}, 429)
        try:
            invite_id = ctl.guests().reserve(token, login, password)
        except ctl.SeedboxError as error:
            return self.send_json({"error": str(error)}, 400)
        identifier = start_job(f"Compte invité {login}", ["guests", "redeem", invite_id, login], password)
        PUBLIC_JOBS.add(identifier)
        try:
            ctl.audit().record(login, self.client_address_text(), "compte créé par invitation", "invitation=" + str(invite_id), "invitation")
        except Exception as error:  # noqa: BLE001
            sys.stderr.write("audit : %s\n" % error)
        self.send_json({"job": identifier})

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/invite" or path.startswith("/invite/"):
            return self.public_get(path)
        if self.public_request():                    # un en-tête public n'ouvre jamais l'administration
            return self.send_json({"error": "accès refusé"}, 403)
        if not self.authorized():
            return
        if not path.startswith("/api/"):
            return self.send_file(path)
        try:
            self.route_get(path)
        except (ctl.SeedboxError, OSError, ValueError, KeyError) as error:
            self.send_json({"error": str(error)}, 400)

    def do_POST(self):
        self.last_status, self.last_payload = 0, {}
        path = self.path.split("?", 1)[0]
        if path == "/invite" or path.startswith("/invite/"):
            try:
                return self.public_post(path)
            except (ctl.SeedboxError, OSError, ValueError, KeyError) as error:
                return self.send_json({"error": "requête invalide"}, 400)
        if self.public_request():
            return self.send_json({"error": "accès refusé"}, 403)
        if not self.authorized():
            return
        data = {}
        try:
            data = self.body()
            self.route_post(path, data)
        except (ctl.SeedboxError, OSError, ValueError, KeyError) as error:
            self.send_json({"error": str(error)}, 400)
        self.audit_request(path, data)

    def audit_request(self, path, data):
        """Chaque action de l'administration entre dans le journal d'audit (sans secret). Une panne du journal ne bloque rien."""
        try:
            job = JOBS.get(self.last_payload.get("job", ""), {})
            ctl.audit().request_event(self.headers.get("Remote-User", ""), self.client_address_text(), path,
                                      data if isinstance(data, dict) else {}, self.last_status, job.get("title", ""))
        except Exception as error:  # noqa: BLE001 - le journal ne doit jamais casser une action
            sys.stderr.write("audit : %s\n" % error)

    def route_get(self, path):
        if path == "/api/session":
            return self.send_json({"user": self.headers.get("Remote-User", ""), "version": ctl.version()})
        if path == "/api/status":
            return self.send_json(ctl.status())
        if path == "/api/credentials":
            return self.send_json(ctl.credentials())
        if path == "/api/users":
            return self.send_json({"users": ctl.user_list()})
        if path == "/api/trackers":
            return self.send_json(ctl.trackers_overview())
        if path == "/api/vpn":
            return self.send_json(ctl.vpn_overview())
        if path == "/api/catalog":
            return self.send_json(ctl.apps().listing())
        if path == "/api/versions":
            return self.send_json(ctl.versions().listing())
        if path == "/api/auth":
            return self.send_json(ctl.auth_overview())
        if path == "/api/remote":
            return self.send_json(ctl.remote().overview())
        if path == "/api/system":
            return self.send_json(ctl.sysinfo().overview())
        if path == "/api/system/containers":
            return self.send_json({"containers": ctl.sysinfo().containers()})
        if path == "/api/system/sources":
            return self.send_json(ctl.sysinfo().log_sources())
        if path == "/api/system/logs":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            lines = int(query.get("lines", ["200"])[0] or 200)
            return self.send_json(ctl.sysinfo().logs(query.get("source", [""])[0], lines))
        if path == "/api/alerts":
            return self.send_json(ctl.alerts().overview())
        if path == "/api/backup":
            return self.send_json(ctl.backup().overview())
        if path == "/api/guests":
            return self.send_json(ctl.guests().overview())
        if path == "/api/audit":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            one = lambda key, default="": query.get(key, [default])[0]  # noqa: E731
            return self.send_json(ctl.audit().search(one("q")[:100], one("user")[:64], one("source")[:20], one("outcome")[:10],
                                                    int(one("since", "0") or 0), int(one("until", "0") or 0),
                                                    int(one("limit", "100") or 100), int(one("offset", "0") or 0)))
        if path == "/api/import":
            return self.send_json(ctl.importer().overview())
        if path == "/api/audit/status":
            return self.send_json(ctl.audit().overview())
        if path == "/api/audit/export":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            fmt = query.get("format", ["csv"])[0]
            if fmt not in ("csv", "json"):
                raise ctl.SeedboxError("format inconnu")
            body, kind = ctl.audit().export(fmt, q=query.get("q", [""])[0][:100])
            return self.send_text(body, kind, f"audit.{fmt}")
        if path == "/api/logs/search":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            sources = [s for s in query.get("sources", [""])[0].split(",") if s] or None
            return self.send_json(ctl.audit().search_logs(query.get("q", [""])[0][:200], query.get("since", ["1h"])[0], sources,
                                                         query.get("regex", ["0"])[0] == "1", int(query.get("limit", ["300"])[0] or 300)))
        if path == "/api/logs/export":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            sources = [s for s in query.get("sources", [""])[0].split(",") if s] or None
            text = ctl.audit().export_logs(query=query.get("q", [""])[0][:200], since=query.get("since", ["1h"])[0], sources=sources,
                                           regex=query.get("regex", ["0"])[0] == "1")
            return self.send_text(text, "text/plain; charset=utf-8", "journaux.txt")
        if path == "/api/gpu":
            return self.send_json(ctl.gpu().overview())
        if path == "/api/cleanup":
            return self.send_json(ctl.cleanup().overview())
        if path == "/api/metrics":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            return self.send_json(ctl.metrics().history(query.get("range", ["24h"])[0]))
        if path == "/api/offsite":
            return self.send_json(ctl.offsite().overview())
        if path == "/api/backup/key":
            return self.send_json({"key": ctl.backup().key()})
        if path == "/api/backup/download":
            return self.send_backup()
        if path == "/api/steps":
            return self.send_json(ctl.steps_status())
        if path == "/api/check":
            items = ctl.health().run_checks()
            return self.send_json({"items": items, "summary": ctl.health().summary(items)})
        if path == "/api/p2p":
            return self.send_json(ctl.p2p_status())
        if path == "/api/shares":
            return self.send_json(ctl.shares().overview())
        if path == "/api/appliance":
            return self.send_json(ctl.appliance().overview())
        if path == "/api/settings/updates":
            return self.send_json(ctl.maintenance().updates_overview())
        if path == "/api/security":
            return self.send_json(ctl.maintenance().security_overview())
        if path == "/api/ca.crt":
            return self.send_certificate()
        match = re.fullmatch(r"/api/jobs/([0-9a-f]{12})", path)
        if match and match.group(1) in JOBS:
            return self.send_json(JOBS[match.group(1)])
        self.send_json({"error": "route inconnue"}, 404)

    def send_backup(self):
        """Archive chiffrée : téléchargeable pour la garder hors du serveur."""
        name = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("name", [""])[0]
        target = ctl.backup().find(name)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(target, "rb") as handle:
            shutil.copyfileobj(handle, self.wfile)

    def send_certificate(self):
        body = (ctl.CONFIG / "traefik" / "certs" / "ca.crt").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-x509-ca-cert")
        self.send_header("Content-Disposition", 'attachment; filename="seedboxbuntu-ca.crt"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def route_post(self, path, data):
        if path == "/api/users":
            ctl.validate_account(data["login"], data["password"])
            return self.send_json({"job": start_job(f"Création du compte {data['login']}", [
                "user", "add", data["login"], "--role", data.get("role", "user")], data["password"])})
        match = re.fullmatch(r"/api/users/([a-z0-9_.-]+)/password", path)
        if match:
            ctl.validate_account(match.group(1), data["password"])
            return self.send_json({"job": start_job(f"Mot de passe de {match.group(1)}",
                                                    ["user", "passwd", match.group(1)], data["password"])})
        match = re.fullmatch(r"/api/users/([a-z0-9_.-]+)/role", path)
        if match:
            if data.get("role") not in ctl.ROLES:
                raise ctl.SeedboxError("rôle invalide")
            return self.send_json({"job": start_job(f"Rôle de {match.group(1)}", ["user", "role", match.group(1), data["role"]])})
        match = re.fullmatch(r"/api/users/([a-z0-9_.-]+)/delete", path)
        if match:
            return self.send_json({"job": start_job(f"Suppression de {match.group(1)}", ["user", "del", match.group(1)])})
        match = re.fullmatch(r"/api/rotate/(qbittorrent|autobrr|radarr|sonarr|prowlarr)", path)
        if match:
            return self.send_json({"job": start_job(f"Renouvellement : {match.group(1)}", ["rotate", match.group(1)])})
        if path == "/api/auth/2fa":
            if data.get("mode") not in ctl.AUTH_MODES:
                raise ctl.SeedboxError("mode inconnu")
            return self.send_json({"job": start_job("Double authentification", ["auth", "2fa", data["mode"]])})
        if path == "/api/remote/hosts":
            return self.send_json({"job": start_job("Adresses supplémentaires", ["remote", "hosts", data.get("value", "")])})
        if path == "/api/remote/tailscale":
            if data.get("mode") == "disable":
                return self.send_json({"job": start_job("Désactivation de Tailscale", ["remote", "tailscale", "disable"])})
            module = ctl.remote()
            if not module.HOSTNAME.match(data.get("hostname", "seedbox")):
                raise ctl.SeedboxError("nom Tailscale invalide")
            key = data.get("key", "").strip()
            if key and not module.AUTHKEY.match(key):
                raise ctl.SeedboxError("clé d'authentification invalide (tskey-...)")
            argv = ["remote", "tailscale", "set", "--hostname", data.get("hostname", "seedbox")] + (["--key-stdin"] if key else [])
            return self.send_json({"job": start_job("Activation de Tailscale", argv, key or None)})
        if path == "/api/remote/dns":
            if data.get("mode") == "disable":
                return self.send_json({"job": start_job("Let's Encrypt : défi HTTP", ["remote", "dns", "disable"])})
            if not ctl.remote().PROVIDER.match(data.get("provider", "")):
                raise ctl.SeedboxError("fournisseur invalide")
            return self.send_json({"job": start_job("Let's Encrypt par DNS", ["remote", "dns", "set", "--provider", data["provider"]],
                                                    data.get("variables", ""))})
        if path == "/api/alerts/settings":
            ctl.alerts().validate(data)
            return self.send_json({"job": start_job("Réglages des alertes", ["alerts", "set"], json.dumps(data))})
        if path == "/api/alerts/test":
            return self.send_json({"job": start_job("Envoi d'une alerte de test", ["alerts", "test"])})
        if path == "/api/backup/create":
            return self.send_json({"job": start_job("Sauvegarde de la configuration", ["backup", "create"])})
        if path == "/api/backup/delete":
            ctl.backup().find(data.get("name", ""))
            return self.send_json({"job": start_job("Suppression de la sauvegarde", ["backup", "delete", data["name"]])})
        if path == "/api/backup/restore":
            ctl.backup().find(data.get("name", ""))
            return self.send_json({"job": start_job("Restauration de la sauvegarde", ["backup", "restore", data["name"]])})
        if path == "/api/backup/settings":
            module = ctl.backup()
            values = {k: v for k, v in data.items() if k in module.DEFAULTS}
            module.validate(values)
            argv = ["backup", "set"]
            for key, value in values.items():
                argv += [f"--{key}", "true" if value is True else "false" if value is False else str(value)]
            return self.send_json({"job": start_job("Réglages de la sauvegarde", argv)})
        if path == "/api/offsite/settings":
            module = ctl.offsite()
            module.validate(data)
            return self.send_json({"job": start_job("Sauvegarde hors serveur : réglages", ["offsite", "set"], json.dumps(data))})
        match = re.fullmatch(r"/api/offsite/(connection|push|restore-test|clear)", path)
        if match:
            labels = {"connection": "Essai de la destination distante", "push": "Envoi de la dernière sauvegarde hors du serveur",
                      "restore-test": "Test de restauration", "clear": "Destination distante retirée"}
            argv = ["offsite", match.group(1)] + (["--local"] if match.group(1) == "restore-test" and data.get("local") else [])
            return self.send_json({"job": start_job(labels[match.group(1)], argv)})
        if path == "/api/import/scan":
            return self.send_json({"job": start_job("Recherche dans le dossier d'import", ["import", "scan"])})
        if path == "/api/import/run":
            module = ctl.importer()
            ids = data.get("ids")
            if not isinstance(ids, list) or not ids or len(ids) > 8 or any(not module.ID_RE.match(str(i)) for i in ids):
                raise ctl.SeedboxError("élément à importer invalide")
            pairs = module.clean_map(data.get("map"))
            argv = ["import", "run", *ids]
            for old_path, new_path in pairs:
                argv += ["--map", f"{old_path}={new_path}"]
            if data.get("dry_run"):
                argv.append("--dry-run")
            return self.send_json({"job": start_job("Import (simulation)" if data.get("dry_run") else "Import d'une autre seedbox", argv)})
        if path == "/api/import/clear":
            return self.send_json({"job": start_job("Dossier d'import vidé", ["import", "clear"])})
        if path == "/api/guests/invite":
            module = ctl.guests()
            created = module.create(str(data.get("label", "")), data.get("days", 7), data.get("uses", 1), data.get("profile"),
                                    self.headers.get("Remote-User", ""))
            return self.send_json(created)
        if path == "/api/guests/revoke":
            if not ctl.guests().ID_RE.match(str(data.get("id", ""))):
                raise ctl.SeedboxError("invitation invalide")
            ctl.guests().revoke(data["id"])
            return self.send_json(ctl.guests().overview())
        if path == "/api/guests/profile":
            login = str(data.get("login", ""))
            if not ctl.VALID_LOGIN.match(login):
                raise ctl.SeedboxError("compte invalide")
            profile = ctl.guests().clean_profile(data.get("profile"))
            return self.send_json({"job": start_job(f"Limites de {login}", ["guests", "profile", login], json.dumps(profile))})
        if path == "/api/guests/extend":
            login = str(data.get("login", ""))
            if not ctl.VALID_LOGIN.match(login):
                raise ctl.SeedboxError("compte invalide")
            days = int(data.get("days", 0))
            if not 0 <= days <= 365:
                raise ctl.SeedboxError("prolongation : 0 à 365 jours")
            return self.send_json({"job": start_job(f"Échéance de {login}", ["guests", "extend", login, str(days)])})
        if path == "/api/gpu/settings":
            if data.get("mode") not in ctl.gpu().MODES:
                raise ctl.SeedboxError("mode inconnu")
            return self.send_json({"job": start_job("Transcodage matériel", ["gpu", "set", "--mode", data["mode"]])})
        if path == "/api/gpu/test":
            return self.send_json({"job": start_job("Essai d'encodage matériel", ["gpu", "test"])})
        match = re.fullmatch(r"/api/cleanup/(scan|trash|restore|empty)", path)
        if match:
            action = match.group(1)
            if action == "scan":
                return self.send_json({"job": start_job("Analyse de l'espace récupérable", ["cleanup", "scan"])})
            if action == "empty":
                return self.send_json({"job": start_job("Vidage de la corbeille", ["cleanup", "empty"])})
            ids = data.get("ids")
            if not isinstance(ids, list) or not ids or len(ids) > 1000 or not all(isinstance(i, str) and ctl.cleanup().ID_RE.match(i) for i in ids):
                raise ctl.SeedboxError("sélection invalide")
            label = "Mise à la corbeille" if action == "trash" else "Restauration depuis la corbeille"
            return self.send_json({"job": start_job(label, ["cleanup", action, *ids])})
        if path == "/api/steps/retry":
            return self.send_json({"job": start_job("Nouvel essai des étapes en échec", ["steps", "retry"])})
        if path == "/api/steps/ignore":
            if data.get("label") not in ctl.steps_load():
                raise ctl.SeedboxError("étape inconnue")
            return self.send_json({"job": start_job("Étape ignorée", ["steps", "ignore", data["label"]])})
        if path == "/api/steps/skip-upgrade":
            return self.send_json({"job": start_job("Abandon de la mise à jour", ["steps", "skip-upgrade"])})
        if path == "/api/catalog/refresh":
            return self.send_json({"job": start_job("Actualisation du catalogue d'applications", ["apps", "refresh"])})
        match = re.fullmatch(r"/api/catalog/(install|remove)", path)
        if match:
            ident = data.get("id", "")
            if not ctl.apps().ID.match(ident):
                raise ctl.SeedboxError("identifiant d'application invalide")
            if match.group(1) == "install":
                return self.send_json({"job": start_job(f"Installation de {ident}", ["apps", "install", ident])})
            argv = ["apps", "remove", ident] + (["--purge"] if data.get("purge") else [])
            return self.send_json({"job": start_job(f"Retrait de {ident}", argv)})
        if path == "/api/catalog/pack":
            ident = data.get("id", "")
            if not ctl.apps().ID.match(ident):
                raise ctl.SeedboxError("pack invalide")
            return self.send_json({"job": start_job(f"Pack {ident}", ["apps", "pack", "install", ident])})
        if path == "/api/catalog/preset":
            value = data.get("value", "")
            if value not in ctl.apps().RECYCLARR_PRESETS:
                raise ctl.SeedboxError("préréglage inconnu")
            return self.send_json({"job": start_job("Préréglage de qualité de Recyclarr", ["apps", "preset", value])})
        match = re.fullmatch(r"/api/versions/(update|rollback|unpin)", path)
        if match:
            service = data.get("service", "")
            if not ctl.versions().SERVICE.match(service):
                raise ctl.SeedboxError("service invalide")
            action = match.group(1)
            if action == "rollback":
                target = data.get("to", "")
                if not re.fullmatch(r"[A-Za-z0-9._-]{0,64}", target):
                    raise ctl.SeedboxError("version invalide")
                argv = ["versions", "rollback", service] + (["--to", target] if target else [])
                return self.send_json({"job": start_job(f"Retour arrière de {service}", argv)})
            title = {"update": "Mise à jour de", "unpin": "Mises à jour automatiques de"}[action]
            return self.send_json({"job": start_job(f"{title} {service}", ["versions", action, service])})
        if path == "/api/catalog/url":
            url = (data.get("url") or "").strip()
            if url and not re.fullmatch(r"https://[A-Za-z0-9._~:/?#\[\]@!&'()*+,;=%-]+", url):
                raise ctl.SeedboxError("adresse invalide : https://... attendu")
            return self.send_json({"job": start_job("Adresse du catalogue", ["apps", "url", url])})
        if path == "/api/domain":
            argv = ["domain", data.get("domain", "")]
            if data.get("email"):
                argv += ["--email", data["email"]]
            return self.send_json({"job": start_job("Changement d'adresse", argv)})
        if path == "/api/vpn":
            argv = ["vpn", "set", "--provider", data["provider"],
                    "--addresses", data.get("addresses", ""), "--countries", data.get("countries", "")]
            if data.get("port_forwarding"):
                argv.append("--port-forwarding")
            return self.send_json({"job": start_job("Configuration du VPN", argv, data["private_key"])})
        if path == "/api/vpn/disable":
            return self.send_json({"job": start_job("Retour à l'accès direct (sans VPN)", ["vpn", "direct"])})
        if path == "/api/trackers/sync":
            return self.send_json({"job": start_job("Synchronisation des trackers", ["trackers", "sync"])})
        if path == "/api/trackers/rule":
            argv = ["trackers", "rule", data["keyword"], "--min-seed-hours", str(int(data.get("min_seed_hours", 0))),
                    "--max-ratio", str(float(data.get("max_ratio", -1)))]
            return self.send_json({"job": start_job(f"Règle du tracker {data['keyword']}", argv)})
        if path == "/api/p2p/token":
            ctl.p2p_set_token(data["token"])
            return self.send_json({"ok": True})
        if path == "/api/p2p/update":
            return self.send_json({"job": start_job("Mise à jour de p2p-aggregator", ["p2p", "update"])})
        if path == "/api/p2p/session":
            return self.send_json({"job": start_job("Activation de tracker-session", ["p2p", "session"])})
        if path == "/api/appliance/check":
            ctl.appliance().check()
            return self.send_json(ctl.appliance().overview())
        if path == "/api/appliance/source":
            ctl.appliance().set_config(str(data.get("repo", "")), str(data.get("ref", "")), data.get("channel"),
                                       data.get("guard"), data.get("require_signed"), data.get("auto") or None)
            return self.send_json(ctl.appliance().overview())
        if path == "/api/appliance/signer":
            ctl.appliance().set_signers(str(data.get("key", "")))
            return self.send_json(ctl.appliance().overview())
        if path == "/api/appliance/apply":
            ctl.appliance().launch("apply", str(data.get("ref", "")) or None, bool(data.get("force")))
            return self.send_json(ctl.appliance().overview())
        if path == "/api/appliance/rollback":
            ctl.appliance().launch("rollback")
            return self.send_json(ctl.appliance().overview())
        if path == "/api/settings/updates":
            return self.updates_save(data)
        if path == "/api/updates/run":
            if data.get("what") not in ("security", "apps"):
                raise ctl.SeedboxError("mise à jour inconnue")
            label = {"security": "Mises à jour de sécurité du système", "apps": "Mises à jour des applications"}[data["what"]]
            return self.send_json({"job": start_job(label, ["updates", "run", data["what"]])})
        if path == "/api/security/apply":
            return self.send_json({"job": start_job("Durcissement de l'hôte", ["security", "apply"])})
        if path == "/api/security/ports":
            ports = ctl.maintenance().parse_ports(str(data.get("ports", "")))
            text = " ".join([str(p) for p in ports["tcp"]] + [f"{p}/udp" for p in ports["udp"]])
            return self.send_json({"job": start_job("Ports ouverts supplémentaires", ["security", "ports", text or " "])})
        if path == "/api/deploy":
            return self.send_json({"job": start_job("Relance du déploiement", ["deploy"])})
        if path == "/api/shares":
            return self.share_add(data)
        if path == "/api/shares/role":
            role, share = data["role"], data.get("share", "")
            if role not in ctl.shares().ROLES or (share and not ctl.shares().NAME.match(share)):
                raise ctl.SeedboxError("rôle ou partage invalide")
            argv = ["share", "role", role] + ([share, "--path", ctl.shares().check_subdir(data.get("path", "") or
                                                ctl.shares().ROLES[role][1])] if share else [])
            return self.send_json({"job": start_job(f"Emplacement : {ctl.shares().ROLES[role][0]}", argv)})
        match = re.fullmatch(r"/api/shares/([a-z][a-z0-9]{0,19})/(mount|umount|remove|credentials)", path)
        if match:
            name, action = match.groups()
            if action == "credentials":
                argv = ["share", "passwd", name, "--username", ctl.shares().check_line("identifiant", data["username"]),
                        "--domain", data.get("domain", "")]
                return self.send_json({"job": start_job(f"Identifiants de {name}", argv, data.get("password", ""))})
            labels = {"mount": "Montage", "umount": "Démontage", "remove": "Suppression"}
            return self.send_json({"job": start_job(f"{labels[action]} de {name}", ["share", action, name])})
        match = re.fullmatch(r"/api/services/([a-z0-9-]+)/restart", path)
        if match:
            known = {a["service"] for a in ctl.all_apps()}
            if match.group(1) not in known:
                raise ctl.SeedboxError("service inconnu")
            service = ctl.qbit_service() if match.group(1) == "qbittorrent" else match.group(1)
            ctl.compose("restart", service, capture=True)
            return self.send_json({"ok": True})
        self.send_json({"error": "route inconnue"}, 404)


    def updates_save(self, data):
        """Validation avant la tâche : une saisie fausse est refusée tout de suite, rien n'est modifié."""
        module = ctl.maintenance()
        argv = ["updates", "set"]
        for section in ("security", "apps"):
            values = {k: v for k, v in (data.get(section) or {}).items() if k in module.DEFAULTS[section]}
            module.validate(section, values)
            for key, value in values.items():
                if isinstance(value, bool):
                    value = "true" if value else "false"
                elif isinstance(value, list):
                    value = ",".join(str(v) for v in value)
                argv += [f"--{section}-{key.replace('_', '-')}", str(value)]
        return self.send_json({"job": start_job("Planification des mises à jour", argv)})

    def share_add(self, data):
        """Validation complète avant la tâche : la console répond tout de suite si une saisie est fausse."""
        module = ctl.shares()
        kind, name = data.get("kind", ""), data.get("name", "")
        if not module.NAME.match(name):
            raise ctl.SeedboxError("nom invalide : minuscules et chiffres, 20 caractères au plus")
        source = {"smb": module.parse_smb, "nfs": module.parse_nfs}.get(kind, lambda v: v.strip())(data.get("source", ""))
        argv = ["share", "add", name, "--kind", kind, "--source", source, "--version", data.get("version", "")]
        password = None
        if kind == "smb" and data.get("username"):
            argv += ["--username", module.check_line("identifiant", data["username"]), "--domain", data.get("domain", "")]
            password = module.check_line("mot de passe", data.get("password", ""), 256)
        return self.send_json({"job": start_job(f"Ajout du partage {name}", argv, password)})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    server = Server(LISTEN, Handler)
    print(f"Console SeedBoxBuntu sur http://{LISTEN[0]}:{LISTEN[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
