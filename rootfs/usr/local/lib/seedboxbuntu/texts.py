"""SeedBoxBuntu - langue des messages envoyés (alertes, rapport) : français d'origine, anglais en option.

La console traduit son propre affichage dans le navigateur ; les messages que le serveur envoie lui-même (ntfy, Discord,
webhook, e-mail) sont traduits ici, au moment de l'envoi, avec des phrases exactes puis des modèles à trous. Une phrase
inconnue reste en français : jamais un texte tronqué. Réglage : /etc/seedboxbuntu/language.json.
"""
import json
import os
import re

import seedboxctl as ctl

SETTINGS_FILE = ctl.ETC / "language.json"
LANGS = ("fr", "en")

EXACT = {
    "Test SeedBoxBuntu": "SeedBoxBuntu test",
    "Les alertes fonctionnent : ce message est un essai.": "Alerts are working: this message is a test.",
    "Sauvegarde échouée": "Backup failed",
    "Certificat bientôt expiré": "Certificate expiring soon",
    "Mise à jour de l'appliance bloquée": "Appliance update blocked",
    "Mise à jour de l'appliance annulée automatiquement": "Appliance update cancelled automatically",
    "Mise à jour automatique de l'appliance en échec": "Automatic appliance update failed",
    "Appliance mise à jour": "Appliance updated",
    "Test de restauration échoué": "Restore test failed",
    "Le problème signalé n'est plus constaté.": "The reported problem is no longer seen.",
    "Elle échoue depuis plus de 15 minutes : voir Applications > Configuration et mises à jour.":
        "It has been failing for over 15 minutes: see Applications > Configuration and updates.",
    "Rapport hebdomadaire : tout va bien": "Weekly report: all good",
    "Rapport hebdomadaire : points à regarder": "Weekly report: items to look at",
    "Rapport hebdomadaire : action nécessaire": "Weekly report: action needed",
    "À REGARDER": "TO LOOK AT",
    "Aucune sauvegarde de moins de 8 jours alors qu’elles sont activées": "No backup newer than 8 days although they are enabled",
    "Le dernier test de restauration a échoué": "The last restore test failed",
    "jamais": "never",
}
PATTERNS = [
    (r"^(.+) est arrêté$", "{1} is stopped"),
    (r"^(.+) en difficulté$", "{1} is unhealthy"),
    (r"^Le service (.+) n'est pas en marche \((.+)\)\.$", "The service {1} is not running ({2})."),
    (r"^Le service (.+) répond « unhealthy »\.$", "The service {1} reports “unhealthy”."),
    (r"^Disque presque plein : (.+)$", "Disk nearly full: {1}"),
    (r"^(\d+) % utilisés, (\d+) Go libres \((.+)\)\.$", "{1}% used, {2} GB free ({3})."),
    (r"^Disque plein dans environ (\d+) jour\(s\) : (.+)$", "Disk full in about {1} day(s): {2}"),
    (r"^Le disque se remplit de (.+) Go par jour ; il serait plein vers le (.+) \((\d+) Go libres\)\. Voir Système > Historique, ou lancez l'assistant de nettoyage\.$",
     "The disk fills by {1} GB per day; it would be full around {2} ({3} GB free). See System > History, or run the cleanup assistant."),
    (r"^Étape en échec : (.+)$", "Step failed: {1}"),
    (r"^Rétabli : (.+)$", "Restored: {1}"),
    (r"^Le certificat de l'appliance expire dans moins de (\d+) jours : ajoutez ou changez une adresse dans la console pour le renouveler\.$",
     "The appliance certificate expires in less than {1} days: add or change an address in the console to renew it."),
    (r"^(.+) : mémoire presque saturée$", "{1}: memory almost full"),
    (r"^(.+) : mémoire en forte hausse$", "{1}: memory growing fast"),
    (r"^(.+) : processeur saturé$", "{1}: CPU saturated"),
    (r"^Mémoire à (\d+) % de sa limite\.$", "Memory at {1}% of its limit."),
    (r"^Mémoire (\d+) Mo, (.+) fois sa valeur habituelle\.$", "Memory {1} MB, {2} times its usual value."),
    (r"^Processeur à (\d+) % de la machine depuis (\d+) minutes\.$", "CPU at {1}% of the machine for {2} minutes."),
    (r"^(.+) : (\d+) faille\(s\) corrigeable\(s\) dont (\d+) critique\(s\)$", "{1}: {2} fixable vulnerability(ies), {3} critical"),
    (r"^Mettez à jour l'application concernée depuis Applications > Mises à jour\. Détail : page Vulnérabilités\.$",
     "Update the affected application from Applications > Updates. Details: Vulnerabilities page."),
    (r"^Mode vacances terminé$", "Vacation mode ended"),
    (r"^Mode vacances : (.+)$", "Vacation mode: {1}"),
    (r"^(.+) - semaine du (.+) au (.+)$", "{1} - week of {2} to {3}"),
    (r"^- (.+)$", "- {1}"),
    (r"^Services : (\d+) / (\d+) fonctionnent$", "Services: {1} / {2} running"),
    (r"^Disque (.+) : (\d+) % \((.+) sur (.+)\)$", "Disk {1}: {2}% ({3} of {4})"),
    (r"^Sauvegardes : (\d+) \(dernière : (.+)\), copie hors serveur (oui|non)(, test de restauration (réussi|ÉCHOUÉ) le (.+))?$", None),
    (r"^Version : (.+?)( \(mise à jour disponible\))?$", None),
    (r"^Incidents cette semaine : (\d+)(?: \((.+)\))?$", None),
    (r"^Invitations actives : (\d+)(?:, dont (\d+) expire\(nt\) sous 7 jours)?$", None),
    (r"^Note : (.+)$", "Note: {1}"),
    (r"^Alerte en cours : (.+)$", "Alert in progress: {1}"),
    (r"^Services à l’arrêt ou en difficulté : (.+)$", "Services stopped or unhealthy: {1}"),
    (r"^(.+) : (\d+) % utilisé$", "{1}: {2}% used"),
    (r"^(.+) : plein dans environ (.+) jours$", "{1}: full in about {2} days"),
]


def language():
    try:
        value = json.loads(SETTINGS_FILE.read_text()).get("notify", "fr")
    except (OSError, ValueError, AttributeError):
        value = "fr"
    return value if value in LANGS else "fr"


def save(lang):
    if lang not in LANGS:
        raise ctl.SeedboxError("langue inconnue : " + ", ".join(LANGS))
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps({"notify": lang}))
    os.chmod(SETTINGS_FILE, 0o644)
    return lang


def _special(index, match):
    """Modèles qui ont des parties facultatives."""
    g = match.groups()
    if index == "backups":
        out = f"Backups: {g[0]} (latest: {g[1]}), off-server copy {'yes' if g[2] == 'oui' else 'no'}"
        if g[3]:
            out += f", restore test {'succeeded' if g[4] == 'réussi' else 'FAILED'} on {g[5]}"
        return out
    if index == "version":
        return f"Version: {g[0]}" + (" (update available)" if g[1] else "")
    if index == "incidents":
        return f"Incidents this week: {g[0]}" + (" (" + ", ".join(tr(x, "en") for x in g[1].split(", ")) + ")" if g[1] else "")
    return f"Active invitations: {g[0]}" + (f", {g[1]} expiring within 7 days" if g[1] else "")


SPECIAL = ["backups", "version", "incidents", "invitations"]


def tr(text, lang=None):
    lang = lang or language()
    if lang != "en" or not isinstance(text, str):
        return text
    if "\n" in text:
        return "\n".join(tr(line, lang) for line in text.split("\n"))
    stripped = text.strip()
    if stripped in EXACT:
        return text.replace(stripped, EXACT[stripped])
    special = iter(SPECIAL)
    for pattern, template in PATTERNS:
        key = next(special) if template is None else None
        match = re.match(pattern, stripped)
        if not match:
            continue
        if template is None:
            return text.replace(stripped, _special(key, match))
        parts = [tr(p, lang) if p and not re.fullmatch(r"[\d.,/ %:\-A-Za-z0-9_]+", p) else p for p in match.groups()]
        return text.replace(stripped, template.format("", *parts))
    return text
