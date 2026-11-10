# ----------------------------------------------------------------------------
#  9. Vérifications de l'ISO produite
# ----------------------------------------------------------------------------
verify_iso() {
  log "Vérification de l'ISO produite"
  local rep="$W/boot-out.txt" i p
  xorriso -indev "$OUTPUT" -report_el_torito plain 2>/dev/null > "$rep" || true
  xorriso -indev "$OUTPUT" -report_system_area plain 2>/dev/null >> "$rep" || true
  if ((HAS_BIOS)); then grep -Eq 'El Torito boot img :.* BIOS ' "$rep" || die "démarrage BIOS perdu"; fi
  grep -Eq 'El Torito boot img :.* UEFI ' "$rep" || die "démarrage UEFI perdu"
  for p in "MBR" "GPT"; do
    if grep -q "$p" "$W/boot-src.txt" && ! grep -q "$p" "$rep"; then die "structure $p perdue (clé USB non démarrable)"; fi
  done
  info "structure de démarrage conservée (El Torito BIOS + UEFI, MBR/GPT hybride)"
  xorriso -indev "$OUTPUT" -find / -type f 2>/dev/null | sed -n "s/^'\(.*\)'$/\1/p" > "$W/out-files.txt"
  for ((i = 1; i < ${#MAPS[@]}; i += 2)); do
    grep -qxF "${MAPS[i]}" "$W/out-files.txt" || die "fichier absent de l'ISO : ${MAPS[i]}"
  done
  mkdir -p "$W/check"
  for p in /autoinstall.yaml /autoinstall-update.yaml /boot/grub/grub.cfg /seedboxbuntu/late.sh /seedboxbuntu/update.sh; do
    iso_extract "$OUTPUT" "$p" "$W/check/x" && cmp -s "$W/check/x" "$W/iso$p" || die "contenu inattendu : $p"
  done
  grep -q ' autoinstall ' "$W/iso/boot/grub/grub.cfg" || die "mot-clé autoinstall absent du menu"
  info "fichiers SeedBoxBuntu présents et identiques"
  (cd "$(dirname "$OUTPUT")" && sha256sum "$(basename "$OUTPUT")" > "$(basename "$OUTPUT").sha256")
}
