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
docs/                 GLOSSAIRE.md : vocabulaire, verbes d'action, formats de la console
tests/                tests sans Docker ni root ; tests/run.sh les lance tous (tests/visual/ : captures de référence)
tools/                manifest.py : manifeste et signature d'une version publiée
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

### Canaux, planification, garde-fou et signature

Dans **Paramètres > Mise à jour de l'appliance > Canal, contrôles et planification** :

- **Canal** : *stable* suit la dernière version publiée sur GitHub (étiquette `v…`, avec ses notes) ; *développement* suit le dernier commit de la branche.
- **Mise à jour automatique** : jours et heure au choix (`seedbox-appliance.timer`). Une version écartée par le garde-fou n'est pas réessayée seule.
- **Garde-fou** : après l'installation, l'état des services est surveillé quelques minutes ; si l'un d'eux qui fonctionnait avant tombe, la version précédente est rétablie automatiquement, la version fautive est marquée « écartée » et une alerte critique part. « Réessayer quand même » force une nouvelle tentative.
- **Versions signées** : une version publiée porte `manifest.json` (empreinte SHA-256 de chaque fichier, commit, étiquette) et `manifest.json.sig` (signature SSH, espace de noms `seedboxbuntu-release`). Enregistrez la clé publique de confiance dans la console ; avec « N'installer que des versions signées », seule une signature valide (ou un commit vérifié par GitHub) est acceptée. Un manifeste altéré ou qui ne correspond pas aux fichiers est toujours refusé.

Publier une version :

```
git tag v1.5.0 && git push origin v1.5.0       # .github/workflows/release.yml teste, construit le manifeste et publie
```

Pour signer, créez une clé dédiée (`ssh-keygen -t ed25519 -f release_key -C seedboxbuntu`), placez la clé privée dans le secret GitHub `RELEASE_SIGNING_KEY` et collez `release_key.pub` dans la console. Manuellement : `tools/manifest.py build --tag v1.5.0 --commit <sha>`, `sign`, `verify`.

La version installée devient « AAAA.MM.JJ-HHMM » de la date du commit (UTC). Une ISO plus récente remplace sans difficulté une mise à jour faite depuis la console ; une ISO plus ancienne est refusée comme d'habitude. L'ISO note le commit de ses sources (`SOURCE_COMMIT`) quand `build.sh` est lancé depuis un clone git, ou avec `SB_SOURCE_COMMIT="<sha> <date ISO>"`.

## Exploitation : ce que la console ajoute

| Fonction | Où | Ligne de commande |
|---|---|---|
| Copie des sauvegardes hors du serveur (SFTP, S3, WebDAV, B2, rclone) et test de restauration mensuel | Sauvegardes | `seedboxctl offsite show\|set\|push\|restore-test` |
| Historique CPU, mémoire, réseau, disques ; prévision « disque plein dans N jours » (alerte) | Système | `seedboxctl metrics show\|forecast` |
| Assistant de nettoyage avec corbeille (orphelins, partiels, doublons, dossiers vides, Docker) | Nettoyage | `seedboxctl cleanup scan\|trash\|restore\|empty` |
| Transcodage matériel de Jellyfin (Intel/AMD VAAPI, QSV, NVIDIA NVENC) | Système | `seedboxctl gpu show\|set\|test` |
| Invitations par lien, limites par compte (échéance, écrans, débit, quotas Seerr) | Invités | `seedboxctl guests list\|create\|revoke\|profile` |
| Journal d'audit chaîné, recherche et export des journaux de tous les services | Journal | `seedboxctl audit show\|verify\|export\|logs` |
| Import d'une autre seedbox (Radarr, Sonarr, Lidarr, Readarr, Prowlarr, torrents qBittorrent) | Sauvegardes | `seedboxctl import scan\|run\|clear` |
| Rapport hebdomadaire (services, disques, sauvegardes, mises à jour, incidents) envoyé sur vos canaux d'alerte | Rapport | `seedboxctl report show\|build\|send\|set\|auto` |
| Liste de contrôle de première installation (calculée sur l'état réel, mène à la page qui règle chaque point) | Assistant | `seedboxctl assistant show` |
| Ressources de chaque conteneur (processeur, mémoire, tendance) et alerte de dérive : mémoire saturée, fuite probable, processeur saturé | Système | `seedboxctl resources show\|set` |
| Vérification d'une sauvegarde à blanc (déchiffrement, bases, réglages, ce qu'une restauration remplacerait) | Sauvegardes | `seedboxctl backup check [nom]` |
| Fenêtre de maintenance : les tâches lourdes automatiques ne démarrent que dans la plage choisie | Paramètres | `seedboxctl window show\|set\|tick` |
| Activité par personne (dernière connexion, inactivité, demandes, quotas), facultative | Activité | `seedboxctl activity show\|set` |
| Langue des messages envoyés (alertes, rapport) : français ou anglais | Alertes | `/etc/seedboxbuntu/language.json` |
| Vulnérabilités des images Docker (Trivy), analyse hebdomadaire et alerte | Vulnérabilités | `seedboxctl vulnscan show\|set\|scan\|auto` |
| Diagnostic réseau : Internet, DNS, domaine, certificat, horloge, VPN, port entrant, qBittorrent | Diagnostic | `seedboxctl netcheck` |
| Mode vacances : torrents en pause et invitations refusées jusqu'à une date, reprise automatique | Vacances | `seedboxctl vacation show\|start --until\|stop` |
| Notifications push dans le navigateur (Web Push chiffré, clé VAPID du serveur) | Alertes | `seedboxctl push show\|test` |
| Export et import des seuls réglages de la console, avec simulation | Sauvegardes | `seedboxctl config export [--secrets]\|import [--apply]` |

Points d'attention :

- **Invitations** : la page `/invite/<jeton>` est la seule route publique (sans connexion unique). Elle est limitée en débit par Traefik, par adresse dans la console, et ne donne jamais accès à l'administration ; seule l'empreinte du jeton est conservée.
- **Journal d'audit** : aucun mot de passe, clé ni jeton n'y est écrit ; les empreintes chaînées font apparaître une modification maladroite, mais ne protègent pas d'un administrateur qui sait les refaire : exportez-le régulièrement.
- **Rôle observateur** : un compte du groupe `observers` (créé avec `seedboxctl user add <nom> --role observer`, ou depuis Comptes) ne fait que regarder : vue d'ensemble, applications, système, alertes et rapport. La restriction est appliquée par le serveur (liste blanche de routes en lecture, seule exception : ses propres préférences d'affichage), pas seulement par l'interface ; les alertes et l'état de l'appliance lui sont filtrés (ni secrets, ni adresses, ni configuration). Dans Seerr il reçoit le droit « demander » seulement.
- **Rapport hebdomadaire** : désactivé par défaut. `seedbox-report.timer` passe chaque jour à 08:10 mais n'envoie que le jour choisi, une seule fois, par les canaux d'alerte configurés. Le texte envoyé est en français ; la console l'affiche aussi sans l'envoyer.
- **Ressources par conteneur** : relevées toutes les 5 minutes avec les mesures de la machine (clés `ctr.*` de `metrics.db`, masquées de l'historique machine). Trois dérives alertent : mémoire à plus de 90 % de la limite 3 mesures de suite, fuite probable (plus de 512 Mo et plus de 2 fois la médiane habituelle), processeur à plus de 90 % de la machine pendant 30 minutes (Jellyfin ignoré : transcodage). Seuils modifiables dans Système.
- **Vérification d'une sauvegarde** : répétition à blanc dans un dossier temporaire, sans démarrer de conteneur : elle prouve que l'archive se déchiffre, que les bases SQLite sont saines et que les réglages se lisent, pas que les applications redémarreront. Le test de restauration hors serveur (mensuel) reste le contrôle de bout en bout de la copie distante.
- **Fenêtre de maintenance** : jours et heures (la fin peut passer minuit). Une tâche automatique qui se déclenche hors fenêtre est mise en attente et lancée par le passage de surveillance de 5 minutes dès l'ouverture ; un lancement manuel n'est jamais retenu, ni les sauvegardes et la surveillance.
- **Activité par personne** : désactivée par défaut. Elle lit Jellyfin et Seerr, montre « en lecture » sans jamais le titre regardé, et repère les comptes inactifs depuis 30 jours.
- **Vulnérabilités** : Trivy est lancé dans un conteneur éphémère (`aquasec/trivy`, la base est mise en cache dans un volume) ; seules les failles critiques et élevées qui ont un correctif sont comptées. Désactivé par défaut ; l'analyse hebdomadaire (`seedbox-vulnscan.timer`, dimanche matin) respecte la fenêtre de maintenance et demande l'accès à Internet.
- **Mode vacances** : met en pause les torrents actifs (ceux qui l'étaient déjà ne sont pas touchés), fait démarrer les nouveaux en pause et refuse les liens d'invitation ; limité à 120 jours. À la date, ou à « Désactiver », seuls les torrents mis en pause par le mode reprennent et la préférence de qBittorrent est rétablie ; si qBittorrent est injoignable à ce moment, le mode reste actif et la reprise est retentée toutes les 5 minutes.
- **Notifications push** : chaque appareil s'abonne depuis Alertes ; le serveur chiffre le message (RFC 8291) et le signe (VAPID, RFC 8292) puis l'envoie au service de push du navigateur (Google, Mozilla, Apple, Microsoft), qui ne voit que du texte chiffré. Seules ces adresses sont acceptées (pas d'envoi vers une adresse choisie par un utilisateur). Il faut HTTPS, un navigateur compatible et `python3-cryptography` ; sur iPhone, la console doit être ajoutée à l'écran d'accueil. Les observateurs peuvent s'abonner.
- **Réglages de la console** : le fichier exporté ne contient ni comptes, ni médias, ni applications ; sans l'option « secrets », les jetons, webhooks, mots de passe et le sujet ntfy sont masqués (à l'import, un secret masqué laisse la valeur actuelle). L'import commence par une simulation et n'applique que les sections cochées et valides.
- **Notifications** : la cloche réunit les alertes en cours et les derniers événements (200 conservés, sans détail sensible) ; « lu » se mesure par compte.
- **Import** : déposez les sauvegardes de l'ancienne seedbox dans `<stockage>/import`. Seule la base de données des applications est reprise (la clé d'API, le port et la connexion restent ceux de ce serveur), les chemins sont convertis d'après une table « ancien dossier → nouveau dossier », une sauvegarde complète est faite avant, et l'ancienne base est gardée. Les torrents sont ajoutés en pause.

### Confort d'usage (console)

- **Recherche rapide** : `Ctrl+K` (ou `⌘K`) ouvre une recherche de pages, d'applications et d'actions, entièrement au clavier.
- **Vue d'ensemble personnalisable** : « Personnaliser » déplace, masque ou affiche chaque tuile ; la disposition est mémorisée par compte sur le serveur, et le masquage se défait depuis la notification.
- **Mode kiosque** : `#kiosk` (bouton « Mode kiosque ») affiche la vue d'ensemble seule, en grand, avec l'heure, sur un écran mural ; elle se rafraîchit seule et garde l'écran allumé quand le navigateur le permet. Elle dépend de la session de connexion unique : si celle-ci expire, la page se recharge pour redemander la connexion.
- **Assistant de configuration** : page « Assistant » et tuile « Configuration » ; un rappel apparaît tant qu'un point de sécurité ou de données reste à régler (et peut être écarté).
- **Défaire** : les actions réversibles (masquer une tuile, mettre à la corbeille) proposent « Défaire » pendant quelques secondes dans la notification.
- **Langues** : français (d'origine) et anglais, choisis dans « Affichage » et mémorisés par compte. Le dictionnaire anglais (`console/web/lang-en.js`) est appliqué au texte affiché ; un texte absent reste en français, jamais une clé brute. Restent en français : les textes envoyés par ntfy, Discord et e-mail, les pages d'erreur Traefik, les journaux, et les contenus venant de tiers (notes de version GitHub, catalogue personnel).

## Charte graphique de la console

Toute l'interface (console, page d'invitation, pages d'erreur, connexion Authelia, Homepage) partage la même identité :

- **Jetons** : `console/web/tokens.css` est l'unique source des couleurs, espacements, rayons, tailles, ombres et seuils (`--th-warn: 85`, `--th-crit: 95`). `app.css` ne contient aucune couleur en dur ; la densité, le contraste élevé et la taille du texte ne font que redéfinir ces jetons.
- **Échelle d'états** : `ok`, `attention`, `erreur`, `information`, `inconnu`, toujours en icône + texte + couleur (pastilles, notices, notifications, tuiles, jauges).
- **Navigation** par groupes (Services, Stockage, Réseau, Sécurité, Exploitation) : barre latérale sur ordinateur, barre du bas et feuille de navigation sur téléphone.
- **Retour d'action** uniforme : notification pour une action courte, tiroir de tâche pour une opération longue, confirmation dans une boîte de dialogue pour tout ce qui détruit.
- **Affichage** (menu « Affichage ») : clair / sombre / automatique, densité, contraste élevé, taille du texte ; mémorisés dans le navigateur.
- **Pages** : « Vue d'ensemble » (mêmes tuiles que Homepage) et « Guide de style » (chaque composant avec les vrais styles, mesure de contraste AA en direct).
- **Vocabulaire** : `docs/GLOSSAIRE.md` fixe les libellés, les verbes d'action et les formats de dates, tailles et durées ; `ui.js` en est l'unique implémentation.
- **Hors console** : pages `/invite/…` et `/errors/50x` (Traefik, pour 502-504) sur les mêmes jetons ; logo et favicon Authelia (`templates/config/authelia/assets/`) ; `templates/config/homepage/custom.css`.

Ajouter une page : une section `view-…` dans `index.html`, son entrée dans `NAV` de `ui.js` (groupe, libellé, icône), un chargeur dans `app.js`. Les tests refusent une couleur en dur, un bouton hors des classes `btn`/`tab`/`seg`/`icon-btn`/`tabbar-item` et un libellé qui contredit le glossaire.

## Tests

```
tests/run.sh
```

Contrôles statiques (Python, JavaScript, shell, YAML/JSON, identifiants de l'interface, CSP, unités systemd, modules `lib/`, droits produits par `gen_rootfs`), sauvegarde/restauration, alertes, catalogue et versions, rôles et règles Authelia, mise à jour N-1 → N, mise à jour depuis GitHub (faux GitHub local : installation, retour arrière, archives refusées, échec en cours d'écriture), copie hors serveur, historique et prévision, nettoyage, GPU, invitations (route publique comprise), audit, import, durcissement, migration, jetons de style (`tokens_test.py`), rôle observateur (`observer_test.py` : le vrai serveur refuse tout sauf la lecture, y compris sur une connexion réutilisée), ressources par conteneur, fenêtre de maintenance, vérification d'une sauvegarde, activité, vulnérabilités, diagnostic réseau, mode vacances, export/import des réglages (`ops_test.py`), notifications push (`push_test.py` : vecteur de la RFC 8291, signature VAPID, liste blanche des adresses), traduction anglaise (`i18n_test.py` : tout le HTML statique est traduit, plafond de phrases restées en français pour les pages réelles), accessibilité (`a11y_test.py` : noms accessibles, titres, repères, lien d'évitement, parcours au clavier sans piège, focus visible, dialogues Échap, animations réduites, plus les règles WCAG A/AA d'axe-core si `npm install axe-core` a été fait), et rendu réel de chaque page en clair, sombre et sur téléphone (`visual_test.py` : erreurs JavaScript, débordements, boutons, contraste AA, parcours dont recherche, notifications, personnalisation, kiosque, anglais et compte observateur, comparaison aux captures de `tests/visual/baseline/`). Nécessite `python3`, `pyyaml` et `node` ; le test visuel demande en plus `pip install playwright pillow` (ignoré sinon). Après un changement voulu du rendu : `python3 tests/visual_test.py --update`. La CI ne compare pas les captures (`VISUAL_COMPARE=off`, elles dépendent des polices du poste) mais garde tous les autres contrôles. Les mêmes tests tournent sur GitHub (`.github/workflows/ci.yml`).
