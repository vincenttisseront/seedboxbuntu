# Glossaire de l'interface

Un verbe a **un seul sens** et **une seule icône**, dans toute la console. Ce document est la référence ;
la liste vit dans `rootfs/usr/local/lib/seedboxbuntu/console/web/ui.js` (`GLOSSARY`) et s'affiche aussi,
avec les vrais boutons, dans la page **Guide de style** de la console. `tests/static_check.py` vérifie que
chaque verbe de ce tableau est dans `ui.js`, que son icône existe dans `icons.svg` et qu'aucun synonyme
interdit n'apparaît dans un libellé de bouton.

## Verbes

| Verbe | Icône | Sens |
|---|---|---|
| Enregistrer | `save` | Conserver ce qui est saisi dans un formulaire. |
| Appliquer | `check` | Mettre une configuration en service : le système change tout de suite. |
| Ajouter | `plus` | Ajouter un élément à une liste qui existe déjà. |
| Créer | `plus` | Produire un nouvel objet : compte, invitation, sauvegarde. |
| Modifier | `pencil` | Rouvrir un élément pour en changer les valeurs. |
| Supprimer | `trash` | Effacer des données : définitif, avec confirmation. |
| Vider | `trash` | Supprimer tout le contenu d’un dossier ou d’une corbeille. |
| Retirer | `minus-circle` | Détacher un élément d’une liste ou d’une configuration, sans effacer ses données. |
| Révoquer | `ban` | Annuler un accès accordé : invitation, jeton, session. |
| Annuler | `x` | Abandonner l’opération en cours sans rien changer. |
| Fermer | `x` | Fermer une fenêtre ou un panneau. |
| Confirmer | `check` | Valider une boîte de dialogue. |
| Actualiser | `refresh` | Relire les données affichées, sans rien modifier. |
| Relancer | `refresh` | Rejouer une opération ou un service. |
| Réessayer | `refresh` | Rejouer une opération qui a échoué. |
| Redémarrer | `refresh` | Arrêter puis redémarrer un service. |
| Synchroniser | `refresh` | Mettre deux éléments d’accord entre eux. |
| Vérifier | `check-circle` | Lancer un contrôle qui ne modifie rien. |
| Tester | `check-circle` | Essayer une connexion ou un réglage sans l’appliquer. |
| Simuler | `play` | Montrer ce que ferait une opération sans la faire. |
| Rechercher | `search` | Chercher dans des données. |
| Filtrer | `search` | Réduire une liste. |
| Analyser | `search` | Parcourir pour établir un état des lieux. |
| Télécharger | `download` | Récupérer un fichier depuis le serveur. |
| Sauvegarder | `download` | Réservé aux sauvegardes du serveur. |
| Importer | `upload` | Faire entrer des données dans l’appliance. |
| Envoyer | `send` | Transmettre vers l’extérieur : message, copie hors serveur. |
| Mettre à jour | `upload` | Installer une version plus récente. |
| Revenir | `undo` | Rétablir la version précédente. |
| Restaurer | `undo` | Remettre des données depuis une sauvegarde. |
| Copier | `copy` | Copier dans le presse-papiers. |
| Afficher | `eye` | Montrer une information masquée ou la suite d’une liste. |
| Installer | `play` | Mettre en place une application. |
| Activer | `play` | Mettre une fonction en service. |
| Désactiver | `ban` | Mettre une fonction hors service. |
| Abandonner | `x` | Renoncer à une opération en attente. |
| Ignorer | `x` | Ne plus signaler ce point. |
| Changer | `pencil` | Remplacer une valeur existante : mot de passe, rôle. |
| Monter | `play` | Monter un partage ou un disque. |
| Démonter | `x` | Démonter un partage ou un disque. |
| Identifiants | `key` | Saisir ou voir les identifiants d’un partage. |
| Limites | `sliders` | Régler les limites d’un compte. |
| Libérer | `undo` | Retirer l’épinglage d’un service. |
| Renouveler | `refresh` | Remplacer un secret par un nouveau. |
| Réinstaller | `upload` | Réinstaller la version courante. |
| Mettre à la corbeille | `trash` | Mettre de côté avant suppression définitive. |

## Synonymes interdits

| À éviter | Employer |
|---|---|
| Effacer | Supprimer (ou Vider pour un dossier entier) |
| Enlever | Retirer |
| Valider | Appliquer (configuration) ou Enregistrer (formulaire) |
| Rafraîchir, Recharger | Actualiser |
| Éditer | Modifier |
| Sauvegarder, pour enregistrer un formulaire | Enregistrer : « Sauvegarder » est réservé aux sauvegardes du serveur |

## Distinctions à retenir

- **Enregistrer** garde des valeurs ; **Appliquer** change le système tout de suite.
- **Supprimer** efface des données et demande une confirmation ; **Retirer** détache un élément sans effacer ses données ; **Révoquer** annule un accès donné.
- **Annuler** abandonne l'opération en cours ; **Revenir** rétablit la version précédente.
- Toute action destructrice passe par une boîte de confirmation dont le bouton reprend le verbe de l'action.

## Formats

| Donnée | Fonction (`ui.js`) | Exemple |
|---|---|---|
| Date et heure | `fmtDateTime` | 15/11/2026 14:45 |
| Jour | `fmtDay` | 15 nov. 2026 |
| Heure | `fmtTime` | 14:45:30 |
| Taille | `fmtSize` | 512 o · 12 Ko · 3,4 Mo · 1,2 Go |
| Débit | `fmtRate` | 5,5 Mo/s |
| Durée | `fmtDuration` | 3 j 4 h · 2 h 05 min · 45 s |
| Pourcentage | `fmtPercent` | 87 % |
| Nombre décimal | `fmtNumber` | 1,25 |

Aucun autre code n'appelle `toLocaleString`, `toFixed` ou `Intl` pour afficher ces données.

## États

Une seule échelle, partout (pastilles, jauges, notifications, messages, tuiles, courbes) :
**OK** (vert), **Attention** (orange), **Erreur** (rouge), **Information** (bleu), **Inconnu** (gris).
Seuils des jauges et des courbes en pourcentage : attention à 85 %, critique à 95 % (`THRESHOLDS` dans `ui.js`, `--th-warn` et `--th-crit` dans `tokens.css`).
