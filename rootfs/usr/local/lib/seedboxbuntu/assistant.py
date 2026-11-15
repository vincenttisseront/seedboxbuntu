"""SeedBoxBuntu - assistant de première installation : liste de contrôle de la configuration.

Chaque point est calculé à partir de l'état réel du serveur (rien n'est stocké) : ok (fait), warn (à faire :
la sécurité ou les données en dépendent), info (facultatif). « seedboxctl assistant show » imprime le résultat.
"""
import json

import seedboxctl as ctl


def _item(item_id, group, title, state, detail, view):
    return {"id": item_id, "group": group, "title": title, "state": state, "detail": detail, "view": view}


def _safe(item_id, group, title, view, compute):
    try:
        state, detail = compute()
    except Exception as error:  # noqa: BLE001 - un contrôle en panne ne doit pas masquer les autres
        state, detail = "unknown", f"Contrôle impossible : {str(error)[:120]}"
    return _item(item_id, group, title, state, detail, view)


def checklist():
    env = ctl.env_read()

    def deployed():
        ok = (ctl.STATE / "deployed").exists()
        return ("ok", "Tous les services sont déployés.") if ok else ("warn", "Le déploiement n’est pas terminé : relancez-le depuis Applications.")

    def address():
        if env.get("DOMAIN_MODE") == "auto":
            return "info", "Adresse automatique (IP du serveur) : un nom de domaine donne un certificat reconnu."
        return "ok", f"Adresse : {env.get('DOMAIN', '')}"

    def twofa():
        mode = env.get("AUTH_2FA", "none")
        if mode == "none":
            return "warn", "La double authentification est désactivée : un mot de passe volé suffit à entrer."
        devices = ctl.auth_devices()
        missing = [u["login"] for u in ctl.user_list() if u["role"] == "admin" and u["login"] not in devices]
        return ("warn", "Administrateur sans seconde étape : " + ", ".join(missing)) if missing else ("ok", "Seconde étape exigée et enregistrée.")

    def accounts():
        users = ctl.user_list()
        return ("ok", f"{len(users)} comptes.") if len(users) > 1 else ("info", "Un seul compte : créez-en un par personne plutôt que de partager le mot de passe.")

    def storage():
        data = ctl.shares().overview()
        if data["warnings"]:
            return "warn", data["warnings"][0] if isinstance(data["warnings"][0], str) else "Un partage demande votre attention."
        if data["shares"]:
            return "ok", f"{len(data['shares'])} partage(s) configuré(s)."
        return "info", "Stockage local seulement : ajoutez un disque ou un partage réseau pour la bibliothèque."

    def backup():
        cfg = ctl.backup().settings()
        return ("ok", "Sauvegarde automatique activée.") if cfg.get("enabled") else ("warn", "Aucune sauvegarde automatique : une panne de disque perdrait la configuration.")

    def offsite():
        module = ctl.offsite()
        if not module.configured():
            return "warn", "Les sauvegardes ne quittent pas le serveur : configurez une copie hors serveur."
        last = (module.overview().get("last_test") or {})
        if last and not last.get("ok"):
            return "warn", "Le dernier test de restauration a échoué."
        return "ok", "Copie hors serveur configurée."

    def alerts():
        module = ctl.alerts()
        cfg = module.settings()
        channels = [name for name, configured, _ in module.CHANNELS if configured(cfg)]
        if cfg["enabled"] and channels:
            return "ok", "Alertes envoyées par : " + ", ".join(channels)
        return "warn", "Aucun canal d’alerte : un service en panne ne vous préviendra pas."

    def updates():
        cfg = ctl.appliance().overview()["config"]
        if cfg.get("auto", {}).get("enabled"):
            return "ok", "Mise à jour automatique de l’appliance activée."
        return "info", "Mise à jour de l’appliance manuelle : activez-la ou vérifiez régulièrement."

    def report():
        cfg = ctl.weekly_report().settings()
        return ("ok", "Rapport hebdomadaire activé.") if cfg["enabled"] else ("info", "Rapport hebdomadaire désactivé : un résumé chaque semaine évite de tout surveiller à la main.")

    items = [
        _safe("deployed", "Base", "Services déployés", "apps", deployed),
        _safe("address", "Base", "Adresse d’accès", "domain", address),
        _safe("accounts", "Sécurité", "Comptes personnels", "users", accounts),
        _safe("twofa", "Sécurité", "Double authentification", "users", twofa),
        _safe("storage", "Stockage", "Stockage et partages", "shares", storage),
        _safe("backup", "Données", "Sauvegarde automatique", "backup", backup),
        _safe("offsite", "Données", "Copie hors serveur", "backup", offsite),
        _safe("alerts", "Exploitation", "Alertes", "alerts", alerts),
        _safe("updates", "Exploitation", "Mises à jour", "settings", updates),
        _safe("report", "Exploitation", "Rapport hebdomadaire", "report", report),
    ]
    todo = [i for i in items if i["state"] in ("warn", "unknown")]
    return {"items": items, "todo": len(todo), "total": len(items),
            "secure": not any(i["state"] == "warn" and i["group"] in ("Sécurité", "Données") for i in items)}


def show():
    print(json.dumps(checklist(), indent=2, ensure_ascii=False))
