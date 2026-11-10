# ----------------------------------------------------------------------------
#  5b. Mise à jour d'une installation existante (sans rien effacer)
# ----------------------------------------------------------------------------
gen_update() {
  cat > "$W/iso/seedboxbuntu/update.sh" <<'__SB_UPDATE__'
#!/bin/bash
# SeedBoxBuntu - mise a jour d'une installation existante (early-commands de Subiquity).
# Aucun disque n'est reparti ni formate : on remplace les fichiers de l'appliance, puis le
# serveur redemarre et applique la mise a jour (paquets, pare-feu, stack) avec Internet.
# Messages sans accents : la console de l'installeur n'affiche pas toujours l'UTF-8.
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
unset PYTHONPATH PYTHONHOME LD_LIBRARY_PATH LD_PRELOAD 2>/dev/null || true
set -uo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
T="${SB_TARGET:-/target}"
NEW="$(cat "$SRC/VERSION" 2>/dev/null || echo inconnue)"
LOG="${SB_UPDATE_LOG:-/var/log/seedboxbuntu-update.log}"
mkdir -p "$(dirname "$LOG")" "$T" /mnt/probe

# Affichage : VT 3 (Subiquity occupe la 1) et console serie eventuelle
OUTS=()
if [[ -z ${SB_NO_TTY:-} ]]; then
  OUTS=(/dev/tty3)
  SERIAL_TTY=$(grep -o 'console=ttyS[0-9]*' /proc/cmdline 2>/dev/null | tail -n1 | cut -d= -f2 || true)
  [[ -n $SERIAL_TTY && -c /dev/$SERIAL_TTY ]] && OUTS+=("/dev/$SERIAL_TTY")
  chvt 3 2>/dev/null || true
fi
SERIAL_TTY="${SERIAL_TTY:-}"
exec > >(tee -a "$LOG" "${OUTS[@]}" >/dev/null) 2>&1
say()  { printf '[mise a jour] %s\n' "$*"; }
MOUNTED=()
cleanup() { local i; sync; for ((i = ${#MOUNTED[@]} - 1; i >= 0; i--)); do umount -l "${MOUNTED[i]}" 2>/dev/null || true; done; }

# Ne jamais rendre la main a Subiquity : sans autre section, il installerait a zero
finish() {   # finish MESSAGE : redemarre
  cleanup
  say "$1"
  [[ -n ${SB_NO_REBOOT:-} ]] && exit 0
  sleep 8
  systemctl reboot 2>/dev/null || reboot -f
  sleep infinity
}
die() {
  say "ERREUR : $*"
  say "Rien n'a ete efface. Retirez la cle USB puis redemarrez : le serveur demarre sur son installation."
  cleanup
  [[ -n ${SB_NO_REBOOT:-} ]] && exit 1
  sleep infinity
}
trap 'die "ligne $LINENO : $BASH_COMMAND"' ERR
set -E

say "SeedBoxBuntu $NEW : recherche de l'installation existante"
vgchange -ay >/dev/null 2>&1 || true
udevadm settle 2>/dev/null || true

ROOT_DEV=""; FOUND=0
# blkid -p : lecture directe des disques, sans dependre de la base udev
while read -r dev; do
  fstype=$(blkid -p -o value -s TYPE "$dev" 2>/dev/null || true)
  case $fstype in ext4|xfs|btrfs) ;; *) continue ;; esac
  if mount -o ro "$dev" /mnt/probe 2>/dev/null; then
    if [[ -f /mnt/probe/usr/share/seedboxbuntu/VERSION || -d /mnt/probe/etc/seedboxbuntu ]]; then
      FOUND=$((FOUND + 1)); [[ -z $ROOT_DEV ]] && ROOT_DEV=$dev
    fi
    umount /mnt/probe
  fi
done < <(lsblk -rpno PATH 2>/dev/null)
[[ -n $ROOT_DEV ]] || die "aucune installation SeedBoxBuntu trouvee sur les disques de ce serveur"
((FOUND > 1)) && say "attention : $FOUND installations trouvees, la premiere est utilisee ($ROOT_DEV)"
say "installation trouvee : $ROOT_DEV"

mount "$ROOT_DEV" "$T"; MOUNTED+=("$T")
OLD="$(cat "$T/usr/share/seedboxbuntu/VERSION" 2>/dev/null || echo inconnue)"
say "version installee : $OLD ; version de cette cle : $NEW"
if [[ $OLD != inconnue && $NEW != inconnue && $OLD > $NEW ]]; then
  die "l'installation ($OLD) est plus recente que cette cle ($NEW) : mise a jour refusee"
fi
avail=$(df --output=avail -m "$T" | tail -n1 | tr -d ' ')
((avail > 200)) || die "espace libre insuffisant sur le systeme installe (${avail} Mo)"

# /boot et /boot/efi : lus dans le fstab du systeme installe (les partages reseau ne sont pas montes)
while read -r src mp; do
  mkdir -p "$T$mp"
  if mount "$src" "$T$mp" 2>/dev/null; then MOUNTED+=("$T$mp"); else say "attention : $mp non monte ($src)"; fi
done < <(awk '$1 !~ /^#/ && ($2 == "/boot" || $2 == "/boot/efi") {print $1, $2}' "$T/etc/fstab")

say "Les donnees (bibliotheques, comptes, secrets, partages, reglages) ne sont pas touchees."
say "Pour annuler : redemarrez le serveur dans les 10 secondes (rien n'est encore modifie)."
sleep "${SB_UPDATE_DELAY:-10}"

# Sauvegarde des fichiers de l'appliance remplaces (petite) et de la configuration
BK="$T/var/lib/seedboxbuntu/backups"
mkdir -p "$BK"; chmod 0700 "$BK"
ARCHIVE="$BK/avant-$NEW-$(date +%Y%m%d-%H%M%S).tar.gz"
say "sauvegarde des fichiers actuels : ${ARCHIVE#"$T"}"
(cd "$T" && tar -czf "$ARCHIVE" --ignore-failed-read \
   usr/local/lib/seedboxbuntu usr/local/sbin/seedbox-setup usr/local/sbin/seedboxctl usr/local/bin/seedbox-menu \
   usr/share/seedboxbuntu etc/systemd/system/seedbox* etc/sysctl.d etc/ssh/sshd_config.d \
   etc/profile.d/zz-seedboxbuntu.sh etc/update-motd.d/60-seedboxbuntu etc/sudoers.d/seedboxbuntu \
   etc/seedboxbuntu opt/seedbox/.env opt/seedbox/docker-compose.yml opt/seedbox/config/trackers.json \
   etc/docker/daemon.json etc/fstab 2>/dev/null) || say "sauvegarde incomplete (poursuite)"
chmod 0600 "$ARCHIVE"
ls -1t "$BK"/avant-*.tar.gz 2>/dev/null | tail -n +4 | xargs -r rm -f

say "remplacement des fichiers de l'appliance"
tar -xpf "$SRC/rootfs.tar" -C "$T" --numeric-owner --no-overwrite-dir

if [[ -z ${SB_NO_CHROOT:-} ]]; then
  for d in dev proc sys; do
    if ! mountpoint -q "$T/$d"; then mkdir -p "$T/$d"; mount --bind "/$d" "$T/$d"; MOUNTED+=("$T/$d"); fi
  done
  say "activation des services"
  chroot "$T" /usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LANG=C.UTF-8 SERIAL_TTY="$SERIAL_TTY" /bin/bash /usr/local/lib/seedboxbuntu/postinstall.sh >/dev/null
fi

# Marqueurs : la mise a jour sera appliquee au demarrage suivant (seedbox-upgrade.service)
mkdir -p "$T/boot" "$T/var/lib/seedboxbuntu"
echo "SeedBoxBuntu $NEW mis a jour le $(date -Is) (depuis $OLD)" > "$T/boot/seedboxbuntu-installed"
printf 'set sb_installed_version="%s"\n' "$NEW" > "$T/boot/seedboxbuntu-installed.cfg"
echo "de $OLD vers $NEW le $(date -Is)" > "$T/var/lib/seedboxbuntu/upgrade-pending"
cp -f "$LOG" "$T/var/log/seedboxbuntu-update.log" 2>/dev/null || true

# UEFI : demarrer sur le disque installe au prochain demarrage
if [[ -d /sys/firmware/efi/efivars ]] && command -v efibootmgr >/dev/null 2>&1; then
  n=$(efibootmgr 2>/dev/null | awk 'tolower($0) ~ /ubuntu/ {sub(/^Boot/, "", $1); sub(/\*$/, "", $1); print $1; exit}' || true)
  [[ -n $n ]] && efibootmgr -n "$n" >/dev/null 2>&1 || true
fi
finish "Fichiers remplaces ($OLD -> $NEW). Redemarrage : la mise a jour se termine au demarrage (Internet requis). Retirez la cle USB."
__SB_UPDATE__
  chmod 0755 "$W/iso/seedboxbuntu/update.sh"
  # Autoinstall de mise à jour : tout est fait par early-commands. Si le script rendait la main,
  # toutes les sections deviendraient interactives : jamais d'effacement automatique.
  {
    echo "# SeedBoxBuntu $SB_VERSION - mise a jour d'une installation existante (aucun effacement)"
    echo "version: 1"
    echo "interactive-sections:"
    echo '  - "*"'
    echo "early-commands:"
    echo "  - bash /cdrom/seedboxbuntu/update.sh"
  } > "$W/iso/autoinstall-update.yaml"
}
