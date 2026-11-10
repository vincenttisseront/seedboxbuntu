# ----------------------------------------------------------------------------
#  3. p2p-aggregator (dépôt privé : sources et image embarquées dans l'ISO)
# ----------------------------------------------------------------------------
fetch_p2p() {
  log "Application p2p-aggregator"
  P2P_DIR="$W/p2p/p2p-aggregator"
  rm -rf "$W/p2p"; mkdir -p "$W/p2p"
  if [[ -n $P2P_SOURCE ]]; then
    [[ -d $P2P_SOURCE/.git ]] || die "P2P_SOURCE n'est pas un clone git : $P2P_SOURCE"
    info "clone local : $P2P_SOURCE"
    git clone --quiet --no-hardlinks --depth 1 "file://$(readlink -f "$P2P_SOURCE")" "$P2P_DIR" 2>/dev/null \
      || cp -a "$P2P_SOURCE" "$P2P_DIR"
  else
    info "dépôt : $P2P_REPO ($P2P_BRANCH)"
    if [[ -n $GITHUB_TOKEN ]]; then
      # Le jeton passe par l'environnement : ni dans la ligne de commande, ni dans .git/config
      export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.https://github.com/.extraheader
      GIT_CONFIG_VALUE_0="Authorization: Basic $(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 -w0)"
      export GIT_CONFIG_VALUE_0
    fi
    GIT_TERMINAL_PROMPT=0 git clone --quiet --depth 1 --branch "$P2P_BRANCH" "$P2P_REPO" "$P2P_DIR" \
      || die "dépôt inaccessible : renseignez GITHUB_TOKEN ou P2P_SOURCE=/chemin/du/clone"
    unset GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0
  fi
  git -C "$P2P_DIR" remote set-url origin "$P2P_REPO"
  rm -f "$P2P_DIR/.env"   # jamais de secrets locaux dans l'ISO
  info "version : $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$P2P_DIR/package.json") ($(git -C "$P2P_DIR" rev-parse --short HEAD))"
}

p2p_image() {   # après gen_rootfs : l'image reçoit les correctifs de l'appliance
  mkdir -p "$W/iso/seedboxbuntu"
  P2P_IMAGE_OUT="$W/iso/seedboxbuntu/p2p-image.tar.zst"
  rm -f "$P2P_IMAGE_OUT"
  if [[ -n $P2P_IMAGE_TAR ]]; then
    cp -f "$P2P_IMAGE_TAR" "$P2P_IMAGE_OUT"; return 0
  fi
  if [[ $P2P_PREBUILD == 0 ]]; then
    info "image non embarquée : construction au premier démarrage du serveur"; return 0
  fi
  if ! docker info >/dev/null 2>&1; then
    [[ $P2P_PREBUILD == 1 ]] && die "Docker est nécessaire pour construire l'image (P2P_PREBUILD=1)"
    warn "Docker absent : image construite au premier démarrage du serveur"; return 0
  fi
  info "construction de l'image (plusieurs minutes)"
  local patch
  for patch in "$W/rootfs/usr/share/seedboxbuntu/p2p/patches/"*.patch; do
    git -C "$P2P_DIR" apply --check "$patch" 2>/dev/null && git -C "$P2P_DIR" apply "$patch"
  done
  cp -f "$P2P_DIR/.env.example" "$P2P_DIR/.env"   # prisma.config.ts exige DATABASE_URL au build
  docker build -q --target runner -t seedbox/p2p-aggregator:current "$P2P_DIR" >/dev/null || die "construction de p2p-aggregator impossible"
  rm -f "$P2P_DIR/.env"
  info "export et compression de l'image"
  docker save seedbox/p2p-aggregator:current | zstd -T0 -12 -q > "$P2P_IMAGE_OUT"
  info "image embarquée : $(du -h "$P2P_IMAGE_OUT" | cut -f1)"
}
