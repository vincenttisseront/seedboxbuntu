#!/usr/bin/env bash
# =============================================================================
#   SeedBoxBuntu - générateur d'ISO  (Ubuntu Server LTS + stack seedbox Docker)
# =============================================================================
#   Usage :
#     ./build.sh                      télécharge Ubuntu Server
#     ./build.sh ubuntu-24.04.x-live-server-amd64.iso   ISO locale
#     ./build.sh --test-vm [--bios] seedboxbuntu.iso    test en VM QEMU
#
#   Dossier des sources : build.sh, lib/ (étapes de la construction), rootfs/ (fichiers de
#   l'appliance), stack/, templates/, p2p/ (correctifs). Voir README.md.
#
#   Systèmes : Linux ou WSL2 (Ubuntu/Debian conseillé).
#   Windows   : lancer build.ps1 (Docker Desktop) depuis ce même dossier :
#               powershell -ExecutionPolicy Bypass -File .\build.ps1
#
#   Paramètres facultatifs (variables d'environnement) :
#     UBUNTU_RELEASE    24.04          branche Ubuntu Server téléchargée
#     P2P_REPO          dépôt GitHub de p2p-aggregator
#     P2P_SOURCE        (vide)         clone local, à la place du téléchargement
#     P2P_BRANCH        main           branche clonée
#     GITHUB_TOKEN      (vide)         jeton de lecture du dépôt privé
#     P2P_PREBUILD      auto           auto | 1 | 0 : image embarquée dans l'ISO
#     P2P_IMAGE_TAR     (vide)         image déjà exportée (docker save | zstd)
#     HOST_NAME         seedbox        nom de machine proposé par l'assistant
#     LOCALE            fr_FR.UTF-8
#     KEYBOARD          fr             clavier de la console
#     KEYBOARD_VARIANT  (vide)         ex. latin9, oss, mac ...
#     TIMEZONE          Europe/Paris
#     GRUB_TIMEOUT      20             secondes avant l'installation automatique
#     WORKDIR           ./seedboxbuntu-build
#     OUTPUT            ./seedboxbuntu-<version>-amd64.iso
#     EXTRA_CA_CERTS    (vide)         certificat racine PEM d'un proxy d'entreprise qui
#                                      inspecte le HTTPS ; repris sur le serveur installé
#
#   Déroulé sur le serveur cible :
#     1. démarrage sur l'ISO -> installation 100 % automatique, sans aucune question
#     2. redémarrage -> assistant sur la console : réseau (DHCP ou IP fixe), nom de
#        machine, mot de passe (compte SSH seedbox et compte web admin)
#     3. déploiement automatique de la stack ; Internet est nécessaire à l'installation
#        (paquets Docker) et au premier démarrage (images).
#     4. https://<ip>/ (tableau de bord), https://<ip>/console/ ou menu seedbox-menu.
#
#   Serveur déjà installé : le menu de l'ISO détecte l'installation et propose de la METTRE
#   À JOUR (entrée par défaut si l'ISO est plus récente) au lieu de tout réinstaller :
#   fichiers de l'appliance remplacés, comptes, secrets, bibliothèques, partages et réglages
#   conservés, sauvegarde dans /var/lib/seedboxbuntu/backups. Le serveur redémarre, puis
#   termine la mise à jour (paquets, pare-feu, stack) : Internet requis.
#
#   Paramètres (console web ou seedbox-menu) : mises à jour de sécurité du système et des
#   applications planifiées, audit de sécurité, ports ouverts. Pare-feu : SSH, web (80, 443)
#   et ports dédiés de Seerr (5055), Dockge (5001) et P2P Aggregator (3000) uniquement ;
#   plus le port de partage de qBittorrent (6881) en accès direct.
#
#   qBittorrent : accès direct (par défaut, sans VPN, pour un usage en réseau local) ou dans un
#   tunnel VPN WireGuard (console > VPN, ou « seedboxctl vpn direct|set »). Dockge gère la pile
#   « seedbox » (docker-compose.yml) comme vos autres piles. « seedboxctl check » (ou le bouton
#   Vérifier de la console) teste l'API de chaque application et leurs liaisons.
# =============================================================================
set -Eeuo pipefail
shopt -s nullglob

SRC_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"   # dossier des sources

SB_VERSION="${SB_VERSION:-$(date +%Y.%m.%d-%H%M)}"   # AAAA.MM.JJ-HHMM : comparable par GRUB
UBUNTU_RELEASE="${UBUNTU_RELEASE:-24.04}"
P2P_REPO="${P2P_REPO:-https://github.com/vincenttisseront/p2p-aggregator.git}"
P2P_SOURCE="${P2P_SOURCE:-}"
P2P_BRANCH="${P2P_BRANCH:-main}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
P2P_PREBUILD="${P2P_PREBUILD:-auto}"
P2P_IMAGE_TAR="${P2P_IMAGE_TAR:-}"
HOST_NAME="${HOST_NAME:-seedbox}"
LOCALE="${LOCALE:-fr_FR.UTF-8}"
KEYBOARD="${KEYBOARD:-fr}"
KEYBOARD_VARIANT="${KEYBOARD_VARIANT:-}"
TIMEZONE="${TIMEZONE:-Europe/Paris}"
GRUB_TIMEOUT="${GRUB_TIMEOUT:-20}"
WORKDIR="${WORKDIR:-$PWD/seedboxbuntu-build}"
OUTPUT="${OUTPUT:-}"

CACHE="$WORKDIR/cache"
W="$WORKDIR/work"
SRC_ISO=""

# Étapes de la construction : lib/NN-nom.sh, chargées dans l'ordre
for _lib in "$SRC_DIR"/lib/*.sh; do
  # shellcheck source=/dev/null
  source "$_lib"
done
unset _lib

main() {
  case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    --test-vm) shift; test_vm "$@"; exit 0 ;;
  esac
  case $(uname -s) in
    MINGW*|MSYS*|CYGWIN*) die "Windows détecté : lancez build.ps1 depuis PowerShell (Docker Desktop requis)" ;;
    Darwin) die "macOS non pris en charge : construisez depuis Linux, WSL2 ou Windows (build.ps1)" ;;
  esac
  [[ -n ${1:-} ]] && SRC_ISO=$(readlink -f "$1")
  check_deps
  [[ -n $SRC_ISO ]] || fetch_ubuntu_iso
  inspect_iso
  [[ -n $OUTPUT ]] || OUTPUT="$PWD/seedboxbuntu-$SB_VERSION-amd64.iso"
  OUTPUT=$(readlink -f "$OUTPUT")
  rm -rf "$W/iso"; mkdir -p "$W/iso/seedboxbuntu"
  fetch_p2p
  gen_rootfs
  p2p_source
  p2p_image
  gen_late
  gen_update
  gen_autoinstall
  gen_grub
  build_iso
  verify_iso
  log "ISO prête : $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
  info "Ubuntu   : $UBUNTU_NAME"
  info "Clavier  : $KEYBOARD${KEYBOARD_VARIANT:+ ($KEYBOARD_VARIANT)}  - $LOCALE - $TIMEZONE"
  info "SHA-256  : $(cut -d' ' -f1 "$OUTPUT.sha256")"
  echo
  info "Au premier démarrage du serveur, l'assistant de la console demande le réseau, le nom"
  info "de machine et le mot de passe (compte SSH seedbox et compte web admin)."
  info "Applications ensuite : https://<ip>/ (tableau de bord) et https://<ip>/console/"
  echo
  if [[ -z ${SB_WINDOWS:-} ]]; then
    info "Clé USB (Linux) : sudo dd if=$(basename "$OUTPUT") of=/dev/sdX bs=4M status=progress oflag=sync"
    info "Clé USB (Windows) : Rufus en mode « DD » ou balenaEtcher"
    info "Test en VM      : $0 --test-vm $(basename "$OUTPUT")"
  fi
}

main "$@"
