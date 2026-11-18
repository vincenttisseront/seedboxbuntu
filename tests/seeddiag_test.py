"""Diagnostic du partage : constats mécaniques sur les réglages et l'état de qBittorrent, remèdes en un clic bornés, prompt de l'IA."""
import json
import os
import sys
import tempfile
from pathlib import Path

SRC = os.environ.get("SB_SRC") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root = Path(tempfile.mkdtemp(prefix="sbdiag"))
for key, folder in {"SB_BASE": "opt/seedbox", "SB_ETC": "etc/sb", "SB_STATE": "var/sb", "SB_STORAGE": "mnt/st"}.items():
    os.environ[key] = str(root / folder)
    (root / folder).mkdir(parents=True, exist_ok=True)
os.environ["SB_SHARE"] = SRC
sys.path.insert(0, SRC + "/rootfs/usr/local/lib/seedboxbuntu")
import seedboxctl as ctl  # noqa: E402

diag = ctl.seeddiag()
llm = ctl.llm()
bad = 0


def check(label, ok, detail=""):
    global bad
    bad += not ok
    print(f"[{'OK' if ok else 'ECHEC'}] {label} {detail}"[:240], flush=True)


def torrent(state="stalledUP", **extra):
    return {"state": state, "progress": 1, "num_incomplete": 0, "num_leechs": 0, "up_limit": -1, "private": True, **extra}


def facts(prefs=None, transfer=None, torrents=None, mode="vpn", forwarded=None, bound=True):
    return {"prefs": {"listen_port": 6881, "queueing_enabled": False, "max_active_uploads": 3, "max_active_torrents": 5, "encryption": 0, **(prefs or {})},
            "transfer": {"connection_status": "connected", "up_rate_limit": 0, "dht_nodes": 300, "up_info_speed": 0, **(transfer or {})},
            "torrents": torrents if torrents is not None else [torrent("uploading", num_incomplete=2) for _ in range(5)], "mode": mode, "bound": bound, "version": "5.0.2",
            "vpn": {"provider": "protonvpn", "forwarding": True, "forwarded_port": forwarded}}


def ids(f):
    return [x["id"] for x in diag.diagnose(f)]


check("réglages sains : aucun défaut", ids(facts()) == ["ok"])
check("port d'écoute différent du port ouvert par le VPN -> critique, avec remède",
      diag.diagnose(facts(forwarded=51413))[0]["id"] == "port-forward" and diag.diagnose(facts(forwarded=51413))[0]["apply"]["prefs"] == {"listen_port": 51413})
check("pare-feu sans redirection de port VPN -> critique, pas de remède automatique",
      "no-forwarding" in ids(facts(transfer={"connection_status": "firewalled"})) and "apply" not in diag.diagnose(facts(transfer={"connection_status": "firewalled"}))[0])
check("pare-feu en accès direct -> redirection du port sur la box", "firewalled" in ids(facts(transfer={"connection_status": "firewalled"}, mode="direct")))
check("déconnecté -> critique", diag.diagnose(facts(transfer={"connection_status": "disconnected"}))[0]["id"] == "disconnected")
check("interface liée à tun0 en accès direct -> critique", diag.diagnose(facts(mode="direct", bound=True))[0]["id"] == "interface")
check("limite d'envoi basse -> remède « retirer la limite »", any(x["id"] == "up-limit" and x["apply"]["prefs"] == {"up_limit": 0}
      for x in diag.diagnose(facts(transfer={"up_rate_limit": 200_000}))))
check("mode tortue actif -> bascule", any(x["id"] == "alt-speed" and x["apply"].get("toggle_alt") for x in diag.diagnose(facts(transfer={"use_alt_speed_limits": True}, prefs={"alt_up_limit": 50_000}))))
check("limite par torrent repérée", "torrent-limits" in ids(facts(torrents=[torrent(up_limit=100_000)])))
check("file d'attente active avec torrents en attente -> avertissement",
      "queue" in ids(facts(prefs={"queueing_enabled": True}, torrents=[torrent("queuedUP"), torrent("uploading")])))
check("limite de partage atteinte, torrents à l'arrêt -> critique",
      diag.diagnose(facts(prefs={"max_ratio_enabled": True, "max_ratio": 2, "max_ratio_act": 0}, torrents=[torrent("pausedUP"), torrent()]))[0]["id"] == "share-limits")
check("mode anonyme et chiffrement obligatoire repérés", {"anonymous", "encryption"} <= set(ids(facts(prefs={"anonymous_mode": True, "encryption": 1}))))
check("la plupart des torrents sans téléchargeur -> constat « pas de demande », sans remède",
      "no-demand" in ids(facts(torrents=[torrent() for _ in range(5)])) and all("apply" not in x for x in diag.diagnose(facts(torrents=[torrent() for _ in range(5)]))))
check("les constats sont triés du plus grave au plus léger",
      [x["severity"] for x in diag.diagnose(facts(forwarded=51413, prefs={"anonymous_mode": True}, torrents=[torrent()]))] == sorted(
          [x["severity"] for x in diag.diagnose(facts(forwarded=51413, prefs={"anonymous_mode": True}, torrents=[torrent()]))], key=lambda s: diag.SEVERITY[s]))

sent = []
fake_post = lambda path, form: sent.append((path, form))
f_port = facts(forwarded=51413)
done = diag.apply("port-forward", f_port, fake_post)
check("remède appliqué : réglage écrit dans qBittorrent", sent == [("/app/setPreferences", {"json": json.dumps({"listen_port": 51413})})] and "51413" in done["applied"])
sent.clear()
diag.apply("alt-speed", facts(transfer={"use_alt_speed_limits": True}), fake_post)
check("mode tortue : bascule par l'API dédiée", sent == [("/transfer/toggleSpeedLimitsMode", {})])


def _raises(fn, *a):
    try:
        fn(*a)
    except ctl.SeedboxError as error:
        return str(error)
    return None


check("remède absent du diagnostic du moment -> refusé", "plus proposé" in (_raises(diag.apply, "port-forward", facts(), fake_post) or ""))
check("identifiant libre refusé (aucun réglage arbitraire)", _raises(diag.apply, "web_ui_password", facts(), fake_post) is not None)

result = diag.overview(facts(forwarded=51413, transfer={"connection_status": "firewalled"}))
prompt = llm.diag_prompt(result)
check("prompt de l'IA : constats et chiffres présents, aucun nom de torrent ni de tracker",
      "port d'écoute" in prompt and "[critique]" in prompt and "51413" in prompt and "tracker.exemple" not in prompt and "Question :" in prompt, prompt[:200])
check("résumé : états des torrents comptés", diag.overview(facts(torrents=[torrent("uploading"), torrent("stalledUP")]))["summary"]["states"] == {"uploading": 1, "stalledUP": 1})
print(f"échecs : {bad}")
sys.exit(1 if bad else 0)
