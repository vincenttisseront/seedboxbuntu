# ----------------------------------------------------------------------------
#  Utilitaires
# ----------------------------------------------------------------------------
if [[ -t 1 ]]; then C1=$'\e[1;36m'; C2=$'\e[1;31m'; C3=$'\e[1;33m'; C0=$'\e[0m'; else C1=""; C2=""; C3=""; C0=""; fi
log()  { printf '%s==>%s %s\n' "$C1" "$C0" "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s[!]%s %s\n' "$C3" "$C0" "$*" >&2; }
die()  { printf '%s[ERREUR]%s %s\n' "$C2" "$C0" "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1; }
trap 'die "échec à la ligne $LINENO : $BASH_COMMAND"' ERR

usage() { sed -n '2,/^set -E/{/^set -E/!p}' "$SRC_DIR/build.sh" | sed 's/^# \{0,1\}//'; }

download() {  # download URL DEST : téléchargement avec reprise et nouvelles tentatives
  local url=$1 dest=$2 rc=0
  mkdir -p "$(dirname "$dest")"
  info "téléchargement : $url"
  curl -fL --retry 5 --retry-delay 3 --connect-timeout 20 --progress-bar -C - -o "$dest.part" "$url" || rc=$?
  if ((rc != 0 && rc != 60)); then
    rc=0; curl -fL --retry 5 --retry-delay 3 --connect-timeout 20 --progress-bar -o "$dest.part" "$url" || rc=$?
  fi
  ((rc == 60)) && cert_error "$url"
  ((rc == 0)) || die "téléchargement impossible : $url"
  mv -f "$dest.part" "$dest"
}

cert_error() {   # certificat HTTPS refusé (curl code 60) : explication et solution
  die "certificat HTTPS non reconnu pour $1
         Cause habituelle : proxy d'entreprise qui inspecte le HTTPS (Zscaler, Netskope...).
         Fournissez son certificat racine (fichier PEM, demandez-le à votre équipe IT) :
             EXTRA_CA_CERTS=/chemin/racine-entreprise.pem ./build.sh"
}

sha256_ok() { [[ -f $1 ]] && [[ "$(sha256sum "$1" | cut -d' ' -f1)" == "$2" ]]; }

iso_extract() {  # iso_extract ISO CHEMIN_ISO DEST
  mkdir -p "$(dirname "$3")"
  rm -f "$3"
  xorriso -osirrox on -indev "$1" -extract "$2" "$3" >/dev/null 2>&1 || return 1
  chmod u+w "$3"
}
