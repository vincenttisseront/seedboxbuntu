"""Seerr depuis Internet : certificat propre au nom public sur le seul point d'entrée de Seerr, DuckDNS, refus des valeurs invalides."""
import os
import re
import sys
import tempfile
from pathlib import Path

import yaml

SRC = os.environ.get("SB_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root = Path(tempfile.mkdtemp(prefix="sbseerrpub"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st", "SB_SHARES": "mnt/shares"}.items():
    os.environ[key] = str(root / folder)
    (root / folder).mkdir(parents=True, exist_ok=True)
os.environ["SB_SHARE"] = SRC
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as ctl  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:240], flush=True)


def fails(function, *args):
    try:
        function(*args)
    except ctl.SeedboxError as error:
        return str(error)
    return None


TEMPLATE = (Path(SRC) / "templates/config/traefik/traefik.yml").read_text()


def traefik(env):
    values = ctl.template_values(env)
    text = re.sub(r"@@([A-Z_]+)@@", lambda m: str(values.get(m.group(1), "")), TEMPLATE)
    return yaml.safe_load(text)


base = {"DOMAIN": "192.168.1.20", "DOMAIN_MODE": "auto", "TZ": "Europe/Paris"}
off = traefik(base)
check("non publié : aucun résolveur, Seerr avec le certificat de l'appliance", "certificatesResolvers" not in off and off["entryPoints"]["seerr"]["http"]["tls"] == {})

http = traefik({**base, "SEERR_PUBLIC_HOST": "maseedbox.duckdns.org", "SEERR_PUBLIC_EMAIL": "moi@exemple.fr"})
seerr_tls = http["entryPoints"]["seerr"]["http"]["tls"]
check("publié : le point d'entrée de Seerr demande un certificat pour le nom public",
      seerr_tls.get("certResolver") == "seerr" and seerr_tls["domains"][0]["main"] == "maseedbox.duckdns.org", str(seerr_tls))
check("publié : la console (443) et Dockge gardent le certificat de l'appliance",
      http["entryPoints"]["websecure"]["http"]["tls"] == {} and http["entryPoints"]["dockge"]["http"]["tls"] == {})
resolver = http["certificatesResolvers"]["seerr"]["acme"]
check("sans jeton DuckDNS : défi HTTP par le port 80", resolver.get("httpChallenge") == {"entryPoint": "web"} and resolver["email"] == "moi@exemple.fr")

dns = traefik({**base, "SEERR_PUBLIC_HOST": "maseedbox.duckdns.org", "SEERR_PUBLIC_EMAIL": "moi@exemple.fr", "SEERR_PUBLIC_DNS": "duckdns"})
check("avec jeton DuckDNS : défi DNS, aucun autre port à ouvrir", dns["certificatesResolvers"]["seerr"]["acme"]["dnsChallenge"]["provider"] == "duckdns")

both = traefik({**base, "DOMAIN": "seedbox.exemple.fr", "DOMAIN_MODE": "custom", "ACME_EMAIL": "a@exemple.fr",
                "SEERR_PUBLIC_HOST": "maseedbox.duckdns.org", "SEERR_PUBLIC_EMAIL": "moi@exemple.fr"})
check("domaine Let's Encrypt de l'appliance + Seerr publié : deux résolveurs distincts",
      set(both["certificatesResolvers"]) == {"le", "seerr"} and both["entryPoints"]["websecure"]["http"]["tls"]["certResolver"] == "le")

DYN = (Path(SRC) / "templates/config/traefik/dynamic/seedbox.yml").read_text()


def dynamic(env):
    values = ctl.template_values(env)
    return yaml.safe_load(re.sub(r"@@([A-Z_]+)@@", lambda m: str(values.get(m.group(1), "")), DYN))["http"]["routers"]


routers = dynamic({**base, "SEERR_PUBLIC_HOST": "maseedbox.duckdns.org", "SEERR_PUBLIC_EMAIL": "moi@exemple.fr"})
check("nom public jamais renvoyé vers l'adresse principale sur le port de Seerr, mais toujours ailleurs",
      "!Host(`maseedbox.duckdns.org`)" in routers["other-hosts-seerr"]["rule"] and "maseedbox" not in routers["other-hosts"]["rule"]
      and routers["other-hosts-seerr"]["entryPoints"] == ["seerr"] and "seerr" not in routers["other-hosts"]["entryPoints"])
check("routes de la console : jamais sur le port de Seerr", all("seerr" not in r.get("entryPoints", []) for n, r in routers.items() if n != "other-hosts-seerr"))

# Seerr seul sur le point d'entrée publié : aucune autre route de la stack ne s'y accroche
compose = yaml.safe_load((Path(SRC) / "stack/docker-compose.yml").read_text())
on_seerr = [k for svc in compose["services"].values() for k, v in (svc.get("labels") or {}).items()
            if k.endswith(".entrypoints") and "seerr" in str(v)]
no_entry = [k for svc in compose["services"].values() for k in (svc.get("labels") or {})
            if re.match(r"traefik\.http\.routers\.[^.]+\.rule$", k) and k.replace(".rule", ".entrypoints") not in svc["labels"]]
check("port 5055 : seul le routeur de Seerr y est servi, et chaque routeur déclare son point d'entrée",
      on_seerr == ["traefik.http.routers.seerr.entrypoints"] and not no_entry, f"{on_seerr} {no_entry}")

# refus et mise en place
remote = ctl.remote()
ctl.render_templates = lambda: None
ctl.compose = lambda *a, **k: None
ctl.run = lambda *a, **k: None
ctl.provision_seerr = lambda: None
(ctl.ENV_FILE).parent.mkdir(parents=True, exist_ok=True)
ctl.ENV_FILE.write_text("DOMAIN=192.168.1.20\n")
check("nom invalide refusé", fails(remote.seerr_public_set, "pas un nom", "moi@exemple.fr") is not None)
check("e-mail invalide refusé", fails(remote.seerr_public_set, "maseedbox.duckdns.org", "moi") is not None)
check("jeton DuckDNS sur un autre domaine refusé", fails(remote.seerr_public_set, "seerr.exemple.fr", "moi@exemple.fr", "a" * 36) is not None)
calls = []
remote.ddns_get = lambda url: calls.append(url) or "OK"
remote.seerr_public_set("MaSeedbox.duckdns.org", "moi@exemple.fr", "0123456789abcdef-0123-4567-89ab")
env = ctl.env_read()
check("publié : nom enregistré en minuscules, défi DNS, jeton dans le fichier protégé et non dans .env",
      env.get("SEERR_PUBLIC_HOST") == "maseedbox.duckdns.org" and env.get("SEERR_PUBLIC_DNS") == "duckdns"
      and "DUCKDNS_TOKEN=0123456789abcdef-0123-4567-89ab" in remote.DNS_ENV.read_text() and "DUCKDNS" not in ctl.ENV_FILE.read_text()
      and oct(remote.DNS_ENV.stat().st_mode & 0o777) == "0o600")
check("DuckDNS : mise à jour du bon sous-domaine, adresse lue par DuckDNS", calls and "domains=maseedbox&" in calls[0] and calls[0].endswith("&ip="), str(calls))
remote.ddns_get = lambda url: "KO"
check("DuckDNS : refus signalé", remote.ddns_update(quiet=True)["ok"] is False and remote.seerr_public()["ddns"]["answer"] == "KO")
remote.seerr_public_disable()
check("arrêt : nom retiré, jeton DuckDNS effacé", not ctl.env_read().get("SEERR_PUBLIC_HOST") and "DUCKDNS" not in remote.DNS_ENV.read_text()
      and remote.ddns_update().get("skipped"))
print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
