# ----------------------------------------------------------------------------
#  6. Configuration autoinstall (aucune question pendant l'installation)
# ----------------------------------------------------------------------------
gen_autoinstall() {
  local f="$W/iso/autoinstall.yaml" hash
  # Mot de passe aléatoire jamais affiché : le compte reste inutilisable jusqu'à l'assistant
  # de premier démarrage, qui fixe le mot de passe choisi par l'administrateur
  hash=$(openssl rand -base64 30 | openssl passwd -6 -stdin)
  {
    echo "# SeedBoxBuntu $SB_VERSION - installation automatique (Subiquity)"
    echo "# Deux temps : Subiquity exécute ces clés, puis cloud-init exécute la section"
    echo "# user-data au premier démarrage. cloud-init doit donc rester actif."
    echo "version: 1"
    echo "interactive-sections: []"
    echo "locale: $LOCALE"
    echo "keyboard:"
    echo "  layout: $KEYBOARD"
    [[ -n $KEYBOARD_VARIANT ]] && echo "  variant: $KEYBOARD_VARIANT"
    echo "timezone: $TIMEZONE"
    echo "refresh-installer:"
    echo "  update: false"
    echo "source:"
    [[ -n $SOURCE_ID ]] && echo "  id: $SOURCE_ID"
    echo "  search_drivers: false"
    echo "apt:"
    echo "  geoip: false"
    echo "  # Docker vient des dépôts : sans miroir, autant échouer tout de suite"
    echo "  fallback: abort"
    echo "# Tout le plus grand disque dans un seul volume : /mnt/storage reste sur le système"
    echo "# de fichiers racine, ce qui garantit les hardlinks entre downloads/ et media/."
    echo "storage:"
    echo "  layout:"
    echo "    name: lvm"
    echo "    sizing-policy: all"
    echo "    match:"
    echo "      size: largest"
    echo "# Premier compte : uid/gid 1000, groupe sudo ; son mot de passe est choisi dans l'assistant"
    echo "identity:"
    echo "  hostname: $HOST_NAME"
    echo "  realname: SeedBox"
    echo "  username: seedbox"
    echo "  password: '$hash'"
    echo "ssh:"
    echo "  install-server: true"
    echo "  allow-pw: true"
    echo "packages:"
    # cifs-utils, nfs-common, ntfs-3g : partages réseau et disques montés depuis la console ; rclone : sauvegardes hors serveur
    for p in docker.io docker-compose-v2 ufw fail2ban unattended-upgrades curl git zstd whiptail python3 openssl cifs-utils nfs-common ntfs-3g rclone; do echo "  - $p"; done
    echo "updates: security"
    echo "late-commands:"
    echo "  - bash /cdrom/seedboxbuntu/late.sh"
    echo "user-data:"
    echo "  runcmd:"
    echo "    # Durcissement : pare-feu (SSH, web et ports des applications seulement), garde des ports"
    echo "    # publiés par Docker, fail2ban, mises à jour planifiées. Le port de partage de qBittorrent"
    echo "    # (6881) est ouvert au déploiement en mode direct. Repli : pare-feu minimal si l'étape échoue."
    echo "    - /usr/local/sbin/seedboxctl harden || { ufw default deny incoming; ufw limit 22/tcp; ufw allow 443/tcp; ufw allow 80/tcp; ufw --force enable; }"
    echo "    # Téléchargement des images, puis déploiement dès la fin de l'assistant"
    echo "    - [systemctl, start, --no-block, seedbox-deploy.service]"
    echo "shutdown: reboot"
  } > "$f"
  if python3 -c 'import yaml' 2>/dev/null; then
    python3 - "$f" <<'PY' || die "autoinstall.yaml invalide"
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
assert d["version"] == 1 and d["late-commands"] and d["storage"]["layout"]["name"] == "lvm"
assert d["identity"]["username"] == "seedbox" and d["user-data"]["runcmd"]
PY
  fi
}
