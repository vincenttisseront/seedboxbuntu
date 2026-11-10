# ----------------------------------------------------------------------------
#  2. ISO Ubuntu Server
# ----------------------------------------------------------------------------
fetch_ubuntu_iso() {
  log "ISO Ubuntu Server $UBUNTU_RELEASE"
  local base="https://releases.ubuntu.com/$UBUNTU_RELEASE" sums="$CACHE/SHA256SUMS-$UBUNTU_RELEASE" line sha name
  download "$base/SHA256SUMS" "$sums"
  line=$(grep -E 'ubuntu-[0-9.]+-live-server-amd64\.iso$' "$sums" | sort -k2 -V | tail -n1) \
    || die "aucune ISO live-server amd64 trouvée dans $base/SHA256SUMS"
  sha=${line%% *}; name=${line##* }; name=${name#\*}
  SRC_ISO="$CACHE/$name"
  if sha256_ok "$SRC_ISO" "$sha"; then
    info "déjà présente et vérifiée : $name"
  else
    download "$base/$name" "$SRC_ISO"
    sha256_ok "$SRC_ISO" "$sha" || die "somme SHA-256 incorrecte pour $name"
    info "somme SHA-256 vérifiée"
  fi
}

inspect_iso() {
  log "Analyse de l'ISO source : $(basename "$SRC_ISO")"
  [[ -f $SRC_ISO ]] || die "ISO introuvable : $SRC_ISO"
  xorriso -indev "$SRC_ISO" -find / -type f 2>/dev/null | sed -n "s/^'\(.*\)'$/\1/p" > "$W/iso-files.txt"
  iso_has() { grep -qxF "$1" "$W/iso-files.txt"; }
  iso_has /boot/grub/grub.cfg || die "ce n'est pas une ISO Ubuntu Server live (pas de /boot/grub/grub.cfg)"
  grep -q '^/casper/' "$W/iso-files.txt" || die "ce n'est pas une ISO Ubuntu Server live (pas de /casper)"

  xorriso -indev "$SRC_ISO" -report_el_torito plain 2>/dev/null > "$W/boot-src.txt" || true
  xorriso -indev "$SRC_ISO" -report_system_area plain 2>/dev/null >> "$W/boot-src.txt" || true
  HAS_BIOS=0; HAS_UEFI=0
  grep -Eq 'El Torito boot img :.* BIOS ' "$W/boot-src.txt" && HAS_BIOS=1
  grep -Eq 'El Torito boot img :.* UEFI ' "$W/boot-src.txt" && HAS_UEFI=1
  ((HAS_UEFI)) || die "l'ISO source n'a pas d'image de démarrage UEFI"
  ((HAS_BIOS)) || warn "l'ISO source n'a pas d'image de démarrage BIOS (UEFI uniquement)"
  info "démarrage : BIOS=$HAS_BIOS UEFI=$HAS_UEFI"

  mkdir -p "$W/src"
  iso_extract "$SRC_ISO" /boot/grub/grub.cfg "$W/src/grub.cfg"
  KERNEL=$(awk '$1=="linux"  && $2 ~ /^\// {print $2; exit}' "$W/src/grub.cfg")
  INITRD=$(awk '$1=="initrd" && $2 ~ /^\// {print $2; exit}' "$W/src/grub.cfg")
  [[ -n $KERNEL && -n $INITRD ]] || die "noyau/initrd introuvables dans grub.cfg de l'ISO"
  iso_has "$KERNEL" && iso_has "$INITRD" || die "noyau $KERNEL ou initrd $INITRD absent de l'ISO"
  info "noyau : $KERNEL  initrd : $INITRD"
  HWE_KERNEL=""; HWE_INITRD=""
  if iso_has /casper/hwe-vmlinuz && iso_has /casper/hwe-initrd && [[ $KERNEL != /casper/hwe-vmlinuz ]]; then
    HWE_KERNEL=/casper/hwe-vmlinuz; HWE_INITRD=/casper/hwe-initrd; info "noyau HWE disponible"
  fi
  MEMTEST=$(awk '$1=="linux16" {print $2; exit}' "$W/src/grub.cfg")
  [[ -n $MEMTEST ]] && ! iso_has "$MEMTEST" && MEMTEST=""
  BIOS_CHAIN=0 BIOS_MODS=""
  iso_has /boot/grub/i386-pc/regexp.mod && iso_has /boot/grub/i386-pc/chain.mod && BIOS_CHAIN=1
  local m
  for m in part_gpt part_msdos ext2; do iso_has "/boot/grub/i386-pc/$m.mod" && BIOS_MODS+=" $m"; done

  ISO_LABEL=$(xorriso -indev "$SRC_ISO" -pvd_info 2>/dev/null | sed -n 's/^Volume Id *: *//p' | head -n1)
  UBUNTU_NAME=$(basename "$SRC_ISO" .iso)
  if iso_has /.disk/info && iso_extract "$SRC_ISO" /.disk/info "$W/src/disk-info"; then
    UBUNTU_NAME=$(head -c 120 "$W/src/disk-info")
  fi
  SOURCE_ID=""
  if iso_has /casper/install-sources.yaml && iso_extract "$SRC_ISO" /casper/install-sources.yaml "$W/src/install-sources.yaml"; then
    grep -Eq '^[- ]*id: *ubuntu-server *$' "$W/src/install-sources.yaml" && SOURCE_ID=ubuntu-server
  fi
  info "$UBUNTU_NAME (volume : ${ISO_LABEL:-?})"
}
