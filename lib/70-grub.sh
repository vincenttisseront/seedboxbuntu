# ----------------------------------------------------------------------------
#  7. Menus GRUB (BIOS + UEFI, sans mode graphique forcé)
# ----------------------------------------------------------------------------
entry() {  # entry TITRE ID NOYAU INITRD PARAMS_AVANT PARAMS_APRES
  printf 'menuentry "%s" --id %s {\n\tset gfxpayload=keep\n\tlinux\t%s %s ---%s\n\tinitrd\t%s\n}\n' \
    "$1" "$2" "$3" "$5" "${6:+ $6}" "$4"
}

gen_grub() {
  local m ai="autoinstall subiquity.autoinstallpath=/cdrom/autoinstall.yaml" g="$W/iso/boot/grub/grub.cfg"
  mkdir -p "$W/iso/boot/grub"
  {
    cat <<EOF
# SeedBoxBuntu $SB_VERSION - menu de demarrage de l'ISO ($UBUNTU_NAME)
# Volontairement sans theme graphique ni mode video impose (compatibilite maximale) ;
# titres en ASCII : le mode texte BIOS ne sait pas afficher les accents.
set default="sb-install"
set timeout=$GRUB_TIMEOUT
set timeout_style=menu

loadfont unicode

set menu_color_normal=white/black
set menu_color_highlight=black/light-gray

EOF
    if [[ -n $BIOS_MODS ]]; then
      # En BIOS, les tables de partitions et ext4 ne sont pas chargées automatiquement
      # (en UEFI elles sont intégrées au GRUB signé, et insmod y serait refusé par Secure Boot).
      printf 'if [ "$grub_platform" = "pc" ]; then\n'
      for m in $BIOS_MODS; do printf '\tinsmod %s\n' "$m"; done
      printf 'fi\n\n'
    fi
    cat <<'EOF'
# Si SeedBoxBuntu est deja installe sur un disque de ce serveur, on propose de le METTRE A JOUR
# (rien n'est efface) plutot que de tout reinstaller ; a version egale, on demarre dessus.
# La cle USB peut rester branchee sans risque de reinstallation.
set sb_root=""
set sb_boot=""
set sb_installed_version=""
EOF
    printf 'set sb_new_version="%s"\n' "$SB_VERSION"
    cat <<'EOF'
if search --no-floppy --file --set=sb_root /seedboxbuntu-installed; then
	set sb_boot=""
elif search --no-floppy --file --set=sb_root /boot/seedboxbuntu-installed; then
	set sb_boot="/boot"
fi
export sb_root
export sb_boot
if [ -n "$sb_root" ]; then
	if [ -f ($sb_root)$sb_boot/seedboxbuntu-installed.cfg ]; then
		source ($sb_root)$sb_boot/seedboxbuntu-installed.cfg
	fi
	set default="sb-local"
	set timeout=15
	if [ -z "$sb_installed_version" -o "$sb_installed_version" '<' "$sb_new_version" ]; then
		set default="sb-update"
	fi
	menuentry "Demarrer SeedBoxBuntu deja installe ($sb_installed_version)" --id sb-local {
		if [ "$grub_platform" = "efi" ]; then
			configfile ($sb_root)$sb_boot/grub/grub.cfg
EOF
    if ((BIOS_CHAIN)); then
      cat <<'EOF'
		else
			regexp --set=1:sb_disk '^([^,]+)' "$sb_root"
			set root=$sb_disk
			chainloader +1
			boot
EOF
    else
      cat <<'EOF'
		else
			configfile ($sb_root)$sb_boot/grub/grub.cfg
EOF
    fi
    cat <<'EOF'
		fi
	}
EOF
    entry "METTRE A JOUR SeedBoxBuntu deja installe (conserve vos donnees)" sb-update "$KERNEL" "$INITRD" "autoinstall subiquity.autoinstallpath=/cdrom/autoinstall-update.yaml" "" | sed 's/^/\t/'
    entry "METTRE A JOUR SeedBoxBuntu - console serie ttyS0 115200 (IPMI/SOL)" sb-update-serial "$KERNEL" "$INITRD" "autoinstall subiquity.autoinstallpath=/cdrom/autoinstall-update.yaml" "console=tty0 console=ttyS0,115200n8" | sed 's/^/\t/'
    cat <<'EOF'
fi

EOF
    entry "Installer SeedBoxBuntu   (automatique - EFFACE le plus grand disque)" sb-install "$KERNEL" "$INITRD" "$ai" ""
    entry "Installer SeedBoxBuntu - affichage de secours (nomodeset)" sb-install-safe "$KERNEL" "$INITRD" "$ai" "nomodeset"
    entry "Installer SeedBoxBuntu - console serie ttyS0 115200 (IPMI/SOL)" sb-install-serial "$KERNEL" "$INITRD" "$ai" "console=tty0 console=ttyS0,115200n8"
    [[ -n $HWE_KERNEL ]] && entry "Installer SeedBoxBuntu - noyau HWE (materiel recent)" sb-install-hwe "$HWE_KERNEL" "$HWE_INITRD" "$ai" ""
    cat <<'EOF'
if [ "$grub_platform" = "efi" ]; then
menuentry "Demarrer sur le peripherique suivant" {
	exit 1
}
menuentry "Parametres du firmware UEFI" {
	fwsetup
}
EOF
    if [[ -n $MEMTEST ]]; then
      printf 'else\nmenuentry "Test de la memoire (memtest86+)" {\n\tlinux16 %s\n}\n' "$MEMTEST"
    fi
    echo "fi"
  } > "$g"

  GRUB_MAPS=("$g" /boot/grub/grub.cfg)
  if grep -qxF /boot/grub/x86_64-efi/grub.cfg "$W/iso-files.txt"; then
    mkdir -p "$W/iso/boot/grub/x86_64-efi"; cp "$g" "$W/iso/boot/grub/x86_64-efi/grub.cfg"
    GRUB_MAPS+=("$W/iso/boot/grub/x86_64-efi/grub.cfg" /boot/grub/x86_64-efi/grub.cfg)
  fi
  if grep -qxF /boot/grub/loopback.cfg "$W/iso-files.txt"; then
    local lb="$W/iso/boot/grub/loopback.cfg" isoscan='iso-scan/filename=${iso_path}'
    {
      entry "Installer SeedBoxBuntu (automatique - EFFACE le plus grand disque)" sb-install "$KERNEL" "$INITRD" "$isoscan $ai" ""
      entry "Installer SeedBoxBuntu - affichage de secours (nomodeset)" sb-install-safe "$KERNEL" "$INITRD" "$isoscan $ai" "nomodeset"
    } > "$lb"
    GRUB_MAPS+=("$lb" /boot/grub/loopback.cfg)
  fi
}
