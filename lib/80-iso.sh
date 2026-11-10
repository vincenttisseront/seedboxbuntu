# ----------------------------------------------------------------------------
#  8. Construction de l'ISO (structure de démarrage d'origine conservée)
# ----------------------------------------------------------------------------
build_iso() {
  log "Construction de l'ISO"
  # Correspondances "fichier local -> chemin dans l'ISO"
  MAPS=("$W/iso/autoinstall.yaml" /autoinstall.yaml
        "$W/iso/seedboxbuntu/late.sh" /seedboxbuntu/late.sh
        "$W/iso/seedboxbuntu/update.sh" /seedboxbuntu/update.sh
        "$W/iso/autoinstall-update.yaml" /autoinstall-update.yaml
        "$W/iso/seedboxbuntu/VERSION" /seedboxbuntu/VERSION
        "$W/iso/seedboxbuntu/rootfs.tar" /seedboxbuntu/rootfs.tar
        "$W/iso/seedboxbuntu/p2p-source.tar.zst" /seedboxbuntu/p2p-source.tar.zst
        "${GRUB_MAPS[@]}")
  [[ -f $W/iso/seedboxbuntu/p2p-image.tar.zst ]] && MAPS+=("$W/iso/seedboxbuntu/p2p-image.tar.zst" /seedboxbuntu/p2p-image.tar.zst)

  # md5sum.txt (utilisé par la vérification d'intégrité du support)
  if grep -qxF /md5sum.txt "$W/iso-files.txt" && iso_extract "$SRC_ISO" /md5sum.txt "$W/src/md5sum.txt"; then
    info "mise à jour de md5sum.txt"
    local i skip=() p
    for ((i = 1; i < ${#MAPS[@]}; i += 2)); do skip+=("./${MAPS[i]#/}"); done
    printf '%s\n' "${skip[@]}" > "$W/md5-skip.txt"
    awk 'NR==FNR {s[$0]=1; next} !(substr($0, 35) in s)' "$W/md5-skip.txt" "$W/src/md5sum.txt" > "$W/iso/md5sum.txt"
    for ((i = 0; i < ${#MAPS[@]}; i += 2)); do
      p="./${MAPS[i+1]#/}"
      printf '%s  %s\n' "$(md5sum < "${MAPS[i]}" | cut -d' ' -f1)" "$p" >> "$W/iso/md5sum.txt"
    done
    MAPS+=("$W/iso/md5sum.txt" /md5sum.txt)
  fi

  # Espace disque
  local need avail
  need=$(( $(stat -c %s "$SRC_ISO") / 1024 + 1048576 ))
  for ((i = 0; i < ${#MAPS[@]}; i += 2)); do need=$(( need + $(stat -c %s "${MAPS[i]}") / 1024 )); done
  avail=$(df -Pk "$(dirname "$OUTPUT")" | awk 'NR==2 {print $4}')
  ((avail > need)) || die "espace insuffisant pour $OUTPUT : $((need / 1024)) Mo nécessaires, $((avail / 1024)) Mo libres"

  local args=(-indev "$SRC_ISO" -outdev "$OUTPUT" -compliance iso_9660_level=3)
  for ((i = 0; i < ${#MAPS[@]}; i += 2)); do args+=(-map "${MAPS[i]}" "${MAPS[i+1]}"); done
  args+=(-boot_image any replay)
  rm -f "$OUTPUT"
  xorriso "${args[@]}" 2>"$W/xorriso.log" || { tail -n 30 "$W/xorriso.log" >&2; die "xorriso a échoué"; }
}
