# ----------------------------------------------------------------------------
#  1. Prérequis
# ----------------------------------------------------------------------------
use_extra_ca() {   # hôte Linux : ajoute EXTRA_CA_CERTS aux certificats de confiance (curl + Ollama)
  [[ -n ${EXTRA_CA_CERTS:-} ]] || return 0
  [[ -f $EXTRA_CA_CERTS ]] || die "EXTRA_CA_CERTS introuvable : $EXTRA_CA_CERTS"
  grep -q 'BEGIN CERTIFICATE' "$EXTRA_CA_CERTS" || die "EXTRA_CA_CERTS n'est pas un fichier PEM : $EXTRA_CA_CERTS"
  local sys bundle="$W/ca-bundle.pem"
  for sys in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/cert.pem; do [[ -f $sys ]] && break; done
  mkdir -p "$W"; { [[ -f $sys ]] && cat "$sys"; cat "$EXTRA_CA_CERTS"; } > "$bundle"
  export CURL_CA_BUNDLE="$bundle" SSL_CERT_FILE="$bundle"
  info "certificats racines supplémentaires : $EXTRA_CA_CERTS"
}

check_deps() {
  log "Vérification des prérequis"
  use_extra_ca
  local missing=() cmd
  for cmd in xorriso curl python3 tar sha256sum md5sum awk sed find stat df zstd git openssl; do need "$cmd" || missing+=("$cmd"); done
  if ((${#missing[@]})); then
    if need apt-get; then
      local sudo=""; [[ $EUID -ne 0 ]] && sudo=sudo
      warn "outils manquants : ${missing[*]} -> installation via apt"
      $sudo apt-get update -qq && $sudo apt-get install -y -qq xorriso curl python3 zstd git openssl coreutils gawk sed findutils tar
    else
      die "outils manquants : ${missing[*]} (installez : xorriso curl python3 zstd git openssl)"
    fi
  fi
  [[ $GRUB_TIMEOUT =~ ^[0-9]+$ ]] || die "GRUB_TIMEOUT doit être un nombre de secondes"
  [[ $P2P_PREBUILD =~ ^(auto|0|1)$ ]] || die "P2P_PREBUILD doit valoir auto, 0 ou 1"
  mkdir -p "$CACHE" "$W"
}
