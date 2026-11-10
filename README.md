# SeedBoxBuntu

ISO Ubuntu Server 24.04 qui installe seule une seedbox Docker sécurisée : Traefik + Authelia, Servarr (Radarr, Sonarr, Prowlarr…), Jellyfin, Seerr, qBittorrent (direct ou VPN), autobrr, cross-seed, qbit_manage, Homepage, Dockge, Watchtower, p2p-aggregator, console web, menu texte, partages et catalogue d'applications.

Ce dossier contient **tout** ce qu'il faut pour reconstruire l'ISO. Il remplace l'ancien script unique `build-seedboxbuntu.sh`, où l'appliance entière était embarquée en here-documents.

## Construire l'ISO

| Système | Commande | Prérequis |
|---|---|---|
| Windows 10/11 | `powershell -ExecutionPolicy Bypass -File .\build.ps1` | Docker Desktop démarré (conteneurs Linux), ~20 Go libres |
| Linux / WSL2 | `./build.sh` | `xorriso curl python3 zstd git openssl` (installés via apt si absents) ; Docker pour embarquer l'image p2p |

L'ISO est écrite dans `out/` (Windows) ou dans le dossier courant (Linux). `./build.sh --help` liste les variables (`HOST_NAME`, `KEYBOARD`, `TIMEZONE`, `P2P_SOURCE`, `GITHUB_TOKEN`, `EXTRA_CA_CERTS`…), `build.ps1` expose les mêmes réglages en paramètres.

## Organisation du dossier

```
build.sh              point d'entrée : documentation, variables, main()
build.ps1             lanceur Windows (Docker Desktop) : exécute build.sh dans un conteneur Ubuntu
lib/                  étapes de la construction, chargées dans l'ordre des numéros
  00-commun.sh          journal, téléchargement avec reprise, extraction d'ISO
  10-prerequis.sh       outils nécessaires, certificats supplémentaires
  20-iso-ubuntu.sh      téléchargement/vérification de l'ISO Ubuntu, analyse de son démarrage
  30-p2p.sh             récupération de p2p-aggregator, image Docker embarquée
  40-appliance.sh       copie de rootfs/, stack/, templates/, p2p/ vers le futur système + droits
  50-late.sh            late.sh : finalisation de l'installation (Subiquity)
  55-update.sh          update.sh : mise à jour d'une installation existante
  60-autoinstall.sh     autoinstall.yaml (installation sans question)
  70-grub.sh            menus GRUB BIOS + UEFI
  80-iso.sh             assemblage xorriso (structure de démarrage conservée)
  85-verification.sh    contrôles de l'ISO produite, empreinte SHA-256
  90-test-vm.sh         ./build.sh --test-vm : essai dans QEMU/KVM
rootfs/               fichiers copiés tels quels à la racine du serveur (usr/local/…, etc/…)
stack/                docker-compose.yml, catalogue d'applications, → /usr/share/seedboxbuntu/stack
templates/            modèles de configuration (Authelia, Traefik, Homepage…) → …/templates
p2p/                  correctifs appliqués à p2p-aggregator → …/p2p
assets/               logos et favicon (sources, non embarqués)
catalog-exemple.json  exemple de catalogue d'applications personnalisé
tests/                tests sans Docker ni root ; tests/run.sh les lance tous
.github/workflows/    intégration continue (les mêmes tests)
```

## Modifier l'appliance

- **Modifier un fichier** : éditez-le dans `rootfs/`, `stack/` ou `templates/` ; il est copié à son emplacement au prochain `build.sh`. Rien à régénérer, rien à recoller dans un script.
- **Ajouter un fichier** : déposez-le au bon endroit de l'arborescence. Le chemin dans `rootfs/` est son chemin sur le serveur (`rootfs/etc/foo.conf` → `/etc/foo.conf`) ; `stack/`, `templates/` et `p2p/` vont sous `/usr/share/seedboxbuntu/`.
- **Droits** (fonction `appliance_mode` de `lib/40-appliance.sh`, indépendante du bit exécutable pour résister à une copie Windows ou un zip) : `etc/sudoers.d/*` en 0440 ; `usr/local/sbin`, `usr/local/bin`, `etc/update-motd.d`, `seedboxctl.py`, `server.py`, `postinstall.sh` en 0755 ; le reste en 0644.
- **Modifier la construction elle-même** (GRUB, autoinstall, late.sh…) : le module correspondant de `lib/`. Les fichiers `late.sh` et `update.sh` sont des here-documents de `50-late.sh` et `55-update.sh`.
- **Fins de ligne** : tout doit rester en LF. `.gitattributes` s'en charge avec git ; `build.sh` refuse un fichier en CRLF et le dit.

## Mettre à jour un serveur déjà installé, sans ISO

Une fois les sources poussées sur GitHub (`vincenttisseront/seedboxbuntu`, dépôt privé par défaut), le serveur se met à jour seul depuis la console web : **Paramètres > Mise à jour de l'appliance** (ou `seedboxctl appliance ...`, ou l'entrée « Mise à jour de l'appliance » du menu texte).

1. « Rechercher une mise à jour » compare le commit installé à celui de la branche suivie (`main`) et liste les nouveautés. Le contrôle se refait seul toutes les 6 h ; une bannière s'affiche sur la page Applications quand une version est disponible.
2. « Mettre à jour » télécharge l'archive du commit, la **vérifie** (Python, shell, JSON, fichiers indispensables, sudoers, démarrage de la nouvelle version, docker-compose.yml), sauvegarde les fichiers remplacés, installe `rootfs/`, `stack/`, `templates/` et `p2p/` avec les mêmes droits que l'ISO, active les services, puis applique la stack (`seedboxctl upgrade`). Rien n'est installé si une vérification échoue. Pas de redémarrage du serveur : seule la console redémarre à la fin, et la page se recharge.
3. « Annuler la dernière mise à jour » remet les fichiers, la version et l'état d'avant (trois sauvegardes conservées). Les données et les migrations déjà faites dans les applications ne sont pas annulées.
4. Si l'étape finale (téléchargement d'images, liaisons) échoue, les fichiers restent installés et `seedbox-upgrade.service` réessaie toutes les 2 minutes.

Dépôt privé : enregistrez un jeton GitHub en lecture seule (le même que celui de P2P Aggregator, à étendre à ce dépôt) dans la page P2P Aggregator, dans « Source des mises à jour », ou dans le menu texte. Le jeton n'est envoyé qu'à `api.github.com`. Dépôt, branche ou étiquette se changent dans « Source des mises à jour ».

La version installée devient « AAAA.MM.JJ-HHMM » de la date du commit (UTC). Une ISO plus récente remplace sans difficulté une mise à jour faite depuis la console ; une ISO plus ancienne est refusée comme d'habitude. L'ISO note le commit de ses sources (`SOURCE_COMMIT`) quand `build.sh` est lancé depuis un clone git, ou avec `SB_SOURCE_COMMIT="<sha> <date ISO>"`.

## Tests

```
tests/run.sh
```

Contrôles statiques (Python, JavaScript, shell, YAML/JSON, modules `lib/`, droits produits par `gen_rootfs`), sauvegarde/restauration, alertes, catalogue et versions, rôles et règles Authelia, mise à jour N-1 → N, mise à jour depuis GitHub (faux GitHub local : installation, retour arrière, archives refusées, échec en cours d'écriture), durcissement, migration. Nécessite `python3`, `pyyaml` et `node`. Les mêmes tests tournent sur GitHub (`.github/workflows/ci.yml`).
