# ----------------------------------------------------------------------------
#  4. Fichiers de l'appliance (copiés depuis rootfs/, stack/, templates/ et p2p/)
# ----------------------------------------------------------------------------
# Mode d'un fichier de l'appliance : appliance_mode CHEMIN_DANS_LE_SYSTEME FICHIER_SOURCE
# (indépendant du bit « exécutable » : un dossier copié depuis Windows ou un zip le perd)
appliance_mode() {
  case $1 in
    /etc/sudoers.d/*) echo 0440 ;;
    /usr/local/sbin/*|/usr/local/bin/*|/etc/update-motd.d/*) echo 0755 ;;
    */seedboxctl.py|*/server.py|*/postinstall.sh) echo 0755 ;;
    /usr/share/seedboxbuntu/*) echo 0644 ;;
    *) [[ -x $2 ]] && echo 0755 || echo 0644 ;;
  esac
}

# copy_tree DOSSIER_SOURCE PREFIXE_DANS_LE_SYSTEME : copie récursive dans $R avec les bons modes
copy_tree() {
  local dir=$1 prefix=$2 f rel dest
  [[ -d $dir ]] || die "dossier introuvable : $dir (le dossier des sources est incomplet)"
  while IFS= read -r -d '' f; do
    rel=${f#"$dir"/}; dest="$prefix/$rel"
    case $f in
      *.png|*.woff2|*.ico) ;;
      *) ! grep -q $'\r' "$f" || die "fins de ligne Windows (CRLF) dans $f : convertissez en LF (voir .gitattributes)" ;;
    esac
    mkdir -p "$(dirname "$R$dest")"
    cp -f "$f" "$R$dest"
    chmod "$(appliance_mode "$dest" "$f")" "$R$dest"
  done < <(find "$dir" -type f ! -path '*/__pycache__/*' -print0 | sort -z)
}

# Commit des sources, noté dans l'ISO : la console sait quelle version du dépôt est installée et
# propose la mise à jour depuis GitHub. Format : « SHA DATE VERSION [modifié] » (la version permet
# d'ignorer une ancienne note laissée par une mise à jour qui n'en contient pas). SB_SOURCE_COMMIT="SHA DATE"
# le fournit quand le dossier n'est pas un clone git (copie, archive). Sans information : aucun fichier,
# la console se fie à la date de l'ISO.
source_commit() {
  local sha date g=(git -c safe.directory='*' -C "$SRC_DIR")
  if [[ -n ${SB_SOURCE_COMMIT:-} ]]; then echo "$SB_SOURCE_COMMIT $SB_VERSION"; return 0; fi
  need git && "${g[@]}" rev-parse --git-dir >/dev/null 2>&1 || return 0
  sha=$("${g[@]}" rev-parse HEAD 2>/dev/null) && date=$("${g[@]}" log -1 --format=%cI 2>/dev/null) || return 0
  if [[ -n $("${g[@]}" status --porcelain 2>/dev/null) ]]; then echo "$sha $date $SB_VERSION modifié"; else echo "$sha $date $SB_VERSION"; fi
}

gen_rootfs() {
  log "Génération des fichiers de l'appliance"
  R="$W/rootfs"
  rm -rf "$R"; mkdir -p "$R"
  local sub
  copy_tree "$SRC_DIR/rootfs" ""
  for sub in stack templates p2p; do copy_tree "$SRC_DIR/$sub" "/usr/share/seedboxbuntu/$sub"; done
  printf '%s\n' "$SB_VERSION" > "$R/usr/share/seedboxbuntu/VERSION"
  source_commit > "$R/usr/share/seedboxbuntu/SOURCE_COMMIT"
  [[ -s $R/usr/share/seedboxbuntu/SOURCE_COMMIT ]] || rm -f "$R/usr/share/seedboxbuntu/SOURCE_COMMIT"
  if [[ -n ${EXTRA_CA_CERTS:-} ]]; then
    mkdir -p "$R/usr/local/share/ca-certificates"
    cp -f "$EXTRA_CA_CERTS" "$R/usr/local/share/ca-certificates/seedboxbuntu-extra.crt"
  fi
  mkdir -p "$W/iso/seedboxbuntu"
  tar --owner=0 --group=0 -cpf "$W/iso/seedboxbuntu/rootfs.tar" -C "$R" .
}

p2p_source() {   # sources d'origine : les correctifs sont réappliqués à chaque construction
  tar --owner=0 --group=0 --zstd -cf "$W/iso/seedboxbuntu/p2p-source.tar.zst" -C "$W/p2p" p2p-aggregator
}
