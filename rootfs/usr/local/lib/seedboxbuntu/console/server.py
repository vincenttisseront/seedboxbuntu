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
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
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
    def do_GET(self):
        if not self.authorized():
            return
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/"):
            return self.send_file(path)
        try:
            self.route_get(path)
        except (ctl.SeedboxError, OSError, ValueError, KeyError) as error:
            self.send_json({"error": str(error)}, 400)

    def do_POST(self):
        if not self.authorized():
            return
        try:
            self.route_post(self.path.split("?", 1)[0], self.body())
        except (ctl.SeedboxError, OSError, ValueError, KeyError) as error:
            self.send_json({"error": str(error)}, 400)

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
            ctl.appliance().set_config(str(data.get("repo", "")), str(data.get("ref", "")))
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
