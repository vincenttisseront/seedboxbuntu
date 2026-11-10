# ----------------------------------------------------------------------------
#  5. Script de fin d'installation (late-commands de Subiquity)
# ----------------------------------------------------------------------------
gen_late() {
  cat > "$W/iso/seedboxbuntu/late.sh" <<'__SB_LATE__'
#!/bin/bash
# SeedBoxBuntu - finalisation de l'installation (late-commands de Subiquity).
# Exécuté dans l'environnement de l'installeur ; le système installé est monté sur /target.
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
unset PYTHONPATH PYTHONHOME LD_LIBRARY_PATH LD_PRELOAD 2>/dev/null || true

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
T="${TARGET_MOUNT_POINT:-/target}"
SB_VERSION="$(cat "$SRC/VERSION" 2>/dev/null || echo inconnue)"
mkdir -p "$T/var/log"
LOG="$T/var/log/seedboxbuntu-install.log"
exec > >(tee -a "$LOG") 2>&1
step() { echo "[seedboxbuntu] $(date +%T) $*"; }
fail() { echo "[seedboxbuntu] ERREUR : $*" >&2; exit 1; }
MOUNTED=()
cleanup() { local i; for ((i = ${#MOUNTED[@]} - 1; i >= 0; i--)); do umount -l "${MOUNTED[i]}" 2>/dev/null || true; done; }
trap cleanup EXIT
trap 'fail "ligne $LINENO : $BASH_COMMAND"' ERR

[[ -d $T/etc && -x $T/bin/bash ]] || fail "système installé introuvable dans $T"
step "SeedBoxBuntu $SB_VERSION : finalisation de l'installation"

# 1. Fichiers de l'appliance
step "copie des fichiers de l'appliance"
tar -xpf "$SRC/rootfs.tar" -C "$T" --numeric-owner --no-overwrite-dir

# 2. p2p-aggregator : sources (mises à jour) et image (premier démarrage sans jeton)
step "p2p-aggregator"
mkdir -p "$T/opt/seedbox/apps" "$T/var/lib/seedboxbuntu/images"
chroot "$T" zstd -dc < "$SRC/p2p-source.tar.zst" | tar -xf - -C "$T/opt/seedbox/apps" --no-same-owner
if [[ -f $SRC/p2p-image.tar.zst ]]; then
  cp -f "$SRC/p2p-image.tar.zst" "$T/var/lib/seedboxbuntu/images/p2p-aggregator.tar.zst"
fi

# 3. Marqueur lu par le menu de l'ISO (empêche toute réinstallation en boucle)
mkdir -p "$T/boot"
echo "SeedBoxBuntu $SB_VERSION installé le $(date -Is)" > "$T/boot/seedboxbuntu-installed"
printf 'set sb_installed_version="%s"\n' "$SB_VERSION" > "$T/boot/seedboxbuntu-installed.cfg"

# 4. Configuration dans le système installé
for d in dev proc sys; do
  if ! mountpoint -q "$T/$d"; then mkdir -p "$T/$d"; mount --bind "/$d" "$T/$d"; MOUNTED+=("$T/$d"); fi
done
step "configuration du système installé"
# Installation lancée sur la console série : l'assistant y sera aussi proposé
SERIAL_TTY=$(grep -o 'console=ttyS[0-9]*' /proc/cmdline 2>/dev/null | tail -n1 | cut -d= -f2 || true)
chroot "$T" /usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  LANG=C.UTF-8 SERIAL_TTY="$SERIAL_TTY" /bin/bash /usr/local/lib/seedboxbuntu/postinstall.sh

# 5. UEFI : démarrer sur le disque installé au prochain démarrage
if [[ -d /sys/firmware/efi/efivars ]] && command -v efibootmgr >/dev/null 2>&1; then
  n=$(efibootmgr 2>/dev/null | awk 'tolower($0) ~ /ubuntu/ {sub(/^Boot/, "", $1); sub(/\*$/, "", $1); print $1; exit}' || true)
  if [[ -n $n ]] && efibootmgr -n "$n" >/dev/null 2>&1; then step "UEFI : démarrage suivant sur Boot$n"; fi
fi
step "installation de SeedBoxBuntu terminée"
__SB_LATE__
  chmod 0755 "$W/iso/seedboxbuntu/late.sh"
  printf '%s\n' "$SB_VERSION" > "$W/iso/seedboxbuntu/VERSION"
}
