#!/bin/bash
# SeedBoxBuntu - configuration du système installé (exécuté en chroot par late.sh)
set -Eeuo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
say() { echo "[postinstall] $*"; }

say "répertoires de l'appliance"
mkdir -p /var/lib/seedboxbuntu/images /etc/seedboxbuntu /opt/seedbox/apps
chmod 0700 /etc/seedboxbuntu

# Racine d'entreprise fournie au build : nécessaire aux pulls Docker du premier démarrage
if [[ -f /usr/local/share/ca-certificates/seedboxbuntu-extra.crt ]]; then
  say "autorité de certification supplémentaire"
  update-ca-certificates
fi

say "droits sudo du menu console"
chmod 0440 /etc/sudoers.d/seedboxbuntu
visudo -cf /etc/sudoers.d/seedboxbuntu >/dev/null

say "console : bannière"
cp -f /usr/share/seedboxbuntu/issue /etc/issue

say "activation des services"
# SYSTEMD_OFFLINE=1 : activation hors ligne garantie, même si systemd ne détecte pas le chroot
export SYSTEMD_OFFLINE=1
systemctl enable seedbox-console.service seedbox-deploy.service seedbox-refresh.timer seedbox-shares.timer docker.service \
  seedbox-firewall.service seedbox-autoupdate.timer seedbox-upgrade.service seedbox-backup.timer seedbox-alerts.timer
# Assistant de premier démarrage : réseau, nom de machine, mot de passe
systemctl enable seedbox-firstboot@tty1.service
if [[ ${SERIAL_TTY:-} =~ ^ttyS[0-9]+$ ]]; then
  systemctl enable "seedbox-firstboot@${SERIAL_TTY}.service"
  say "assistant également sur la console série $SERIAL_TTY"
fi

say "durcissement : console et SSH"
systemctl mask ctrl-alt-del.target >/dev/null 2>&1 || true
# Le reste du durcissement (pare-feu, fail2ban, mises à jour planifiées) s'applique au démarrage
# suivant, dans le système installé : « seedboxctl harden »
if command -v sshd >/dev/null && sshd -t 2>&1 | grep -q "50-seedboxbuntu"; then
  rm -f /etc/ssh/sshd_config.d/50-seedboxbuntu.conf
  say "  configuration SSH durcie refusée par sshd : retirée (SSH reste utilisable)"
fi
say "terminé"
