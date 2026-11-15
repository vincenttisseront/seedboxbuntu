'use strict';
// SeedBoxBuntu - console : socle visuel commun. Tout ce qui doit rester identique d'une page à l'autre
// vit ici : navigation groupée, échelle d'états, notifications, tiroir de tâches, boîte de confirmation,
// états de chargement / vide / erreur, formats (dates, tailles, durées) et glossaire des actions.
// Chargé avant features.js et app.js ; ne dépend que de display.js (réglages d'affichage).

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'onclick') node.addEventListener('click', value);
    else if (value !== undefined && value !== null) node.setAttribute(key, value);
  }
  node.append(...children.flat().filter((c) => c !== null && c !== undefined));
  return node;
}

// ---------------------------------------------------------------------------------------------------------------
//  Formats : une seule manière d'écrire une date, une taille, une durée, un débit
// ---------------------------------------------------------------------------------------------------------------
const EN = document.documentElement.lang === 'en';
const LOCALE = EN ? 'en-GB' : 'fr-FR';
const asDate = (value) => {
  if (value === null || value === undefined || value === '' || value === 0) return null;
  const date = typeof value === 'number' ? new Date(value < 1e11 ? value * 1000 : value) : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
};
const DATE_TIME = new Intl.DateTimeFormat(LOCALE, { dateStyle: 'short', timeStyle: 'short' });
const DATE_ONLY = new Intl.DateTimeFormat(LOCALE, { dateStyle: 'medium' });
const TIME_ONLY = new Intl.DateTimeFormat(LOCALE, { timeStyle: 'medium' });
const DATE_FULL = new Intl.DateTimeFormat(LOCALE, { dateStyle: 'full', timeStyle: 'short' });
/** Instant : « 15/11/2026 14:45 ». Accepte des secondes Unix, des millisecondes ou une date ISO. */
const fmtDateTime = (value) => { const d = asDate(value); return d ? DATE_TIME.format(d) : '-'; };
/** Jour : « 15 nov. 2026 ». */
const fmtDay = (value) => { const d = asDate(value); return d ? DATE_ONLY.format(d) : '-'; };
const fmtTime = (value) => { const d = asDate(value); return d ? TIME_ONLY.format(d) : '-'; };
/** Date complète pour une échéance : « dimanche 15 novembre 2026 à 14:45 ». */
const fmtFull = (value) => { const d = asDate(value); return d ? DATE_FULL.format(d) : '-'; };
/** Taille : « 512 o », « 12 Ko », « 3,4 Mo », « 1,2 Go », « 2,50 To » (base 1024, un seul jeu d'unités). */
function fmtSize(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${Math.round(bytes)} ${EN ? 'B' : 'o'}`;
  const units = EN ? ['KB', 'MB', 'GB', 'TB', 'PB'] : ['Ko', 'Mo', 'Go', 'To', 'Po'];
  const power = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length);
  const scaled = bytes / 1024 ** power;
  return `${scaled.toLocaleString(LOCALE, { maximumFractionDigits: power <= 1 ? 0 : power === 2 ? 1 : 2, minimumFractionDigits: 0 })} ${units[power - 1]}`;
}
const fmtRate = (value) => `${fmtSize(Math.max(0, value))}/s`;
/** Durée : « 3 j 4 h », « 2 h 05 min », « 45 s ». */
function fmtDuration(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  const d = Math.floor(s / 86400); const h = Math.floor((s % 86400) / 3600); const m = Math.floor((s % 3600) / 60);
  if (d) return `${d} ${EN ? 'd' : 'j'} ${h} h`;
  if (h) return `${h} h ${String(m).padStart(2, '0')} min`;
  if (m) return `${m} min`;
  return `${s} s`;
}
const AXIS_DAY = new Intl.DateTimeFormat(LOCALE, { day: '2-digit', month: '2-digit' });
const AXIS_TIME = new Intl.DateTimeFormat(LOCALE, { hour: '2-digit', minute: '2-digit' });
/** Graduation d'un axe de temps : « 15/11 » sur plusieurs jours, « 14:45 » sinon. */
const fmtAxis = (value, byDay) => { const d = asDate(value); return d ? (byDay ? AXIS_DAY : AXIS_TIME).format(d) : ''; };
/** Nombre à virgule française : « 1,25 ». */
const fmtNumber = (value, digits = 1) => Number(value).toLocaleString(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits });
const fmtPercent = (value) => `${Math.round(Number(value) || 0)}${EN ? '' : ' '}%`;

// ---------------------------------------------------------------------------------------------------------------
//  Icônes : un seul jeu (icons.svg), toujours via <use>
// ---------------------------------------------------------------------------------------------------------------
const SVGNS = 'http://www.w3.org/2000/svg';
function icon(name, extra = '') {
  const svg = document.createElementNS(SVGNS, 'svg');
  svg.setAttribute('class', `ic${extra ? ` ${extra}` : ''}`);
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(SVGNS, 'use');
  use.setAttribute('href', `icons.svg#i-${name}`);
  svg.append(use);
  return svg;
}

// ---------------------------------------------------------------------------------------------------------------
//  Échelle d'états : ok, attention, erreur, information, inconnu — même couleur, même icône, même mot partout
// ---------------------------------------------------------------------------------------------------------------
const STATUS = {
  ok: { label: 'OK', icon: 'check-circle' },
  warn: { label: 'Attention', icon: 'alert-circle' },
  err: { label: 'Erreur', icon: 'x-circle' },
  info: { label: 'Information', icon: 'info' },
  unknown: { label: 'Inconnu', icon: 'help' },
};
const STATUS_ALIAS = { crit: 'err', critical: 'err', fail: 'err', error: 'err', warning: 'warn', attention: 'warn', good: 'ok', none: 'unknown', '': 'unknown' };
function statusKind(kind) {
  const key = STATUS_ALIAS[kind] || kind;
  return STATUS[key] ? key : 'unknown';
}
/** Pastille d'état : icône + texte + couleur. Sans texte, le libellé standard de l'état est utilisé. */
function pill(text, kind) {
  const key = statusKind(kind);
  return el('span', { class: `pill ${key}` }, icon(STATUS[key].icon), text || STATUS[key].label);
}
/** Seuils partagés par les jauges, les graphiques et les alertes (en % ; voir --th-warn / --th-crit). */
const THRESHOLDS = { warn: 85, crit: 95 };
const levelOf = (percent) => (percent >= THRESHOLDS.crit ? 'err' : percent >= THRESHOLDS.warn ? 'warn' : 'ok');
/** Jauge : libellé, détail, barre colorée par l'état, et pastille dès que le seuil d'attention est franchi. */
function gauge(percent, label, detail) {
  const value = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
  const level = levelOf(value);
  return el('div', { class: 'gauge' },
    el('div', { class: 'row gauge-head' }, el('strong', {}, label), detail ? el('span', { class: 'muted small' }, detail) : null,
      level === 'ok' ? null : pill(level === 'err' ? 'Critique' : 'Presque plein', level)),
    el('progress', { max: '100', value: String(value), class: level, 'aria-label': `${label} : ${value} %` }));
}

// ---------------------------------------------------------------------------------------------------------------
//  États de chargement, de vide et d'erreur : mêmes blocs partout
// ---------------------------------------------------------------------------------------------------------------
const STATE_ICON = { loading: 'refresh', empty: 'info', error: 'x-circle' };
function stateBlock(kind, text) {
  return el('div', { class: `state ${kind}`, role: kind === 'error' ? 'alert' : 'status' },
    kind === 'loading' ? el('span', { class: 'spinner', 'aria-hidden': 'true' }) : icon(STATE_ICON[kind]), el('span', {}, text));
}
const stateRow = (cols, kind, text) => el('tr', { class: 'state-row' }, el('td', { colspan: String(cols) }, stateBlock(kind, text)));
const emptyRow = (cols, text) => stateRow(cols, 'empty', text);
const emptyItem = (text) => el('li', { class: 'state-item' }, stateBlock('empty', text));
function skeletonRows(cols, count = 3) {
  return Array.from({ length: count }, () => el('tr', { class: 'skeleton-row', 'aria-hidden': 'true' },
    ...Array.from({ length: cols }, () => el('td', {}, el('span', { class: 'skeleton' })))));
}
/** Remplit chaque tableau encore vide d'une page de lignes d'attente, pour éviter le saut de mise en page. */
function markLoading(view) {
  view.setAttribute('aria-busy', 'true');
  for (const body of view.querySelectorAll('tbody')) {
    if (body.children.length) continue;
    const cols = body.closest('table').querySelectorAll('thead th').length || 3;
    body.replaceChildren(...skeletonRows(cols));
  }
}
function markLoaded(view, error) {
  view.removeAttribute('aria-busy');
  for (const body of view.querySelectorAll('tbody')) {
    if (!body.querySelector('.skeleton-row')) continue;
    const cols = body.closest('table').querySelectorAll('thead th').length || 3;
    body.replaceChildren(error ? stateRow(cols, 'error', `Impossible de charger : ${error.message}`) : emptyRow(cols, 'Rien à afficher.'));
  }
}

/** Change le texte d'un bouton en gardant son icône. */
function setLabel(button, text) {
  const mark = button.querySelector('.ic');
  button.replaceChildren(...(mark ? [mark] : []), text);
}

// ---------------------------------------------------------------------------------------------------------------
//  Notifications (toasts) et tiroir de tâches : le résultat d'une action apparaît à côté d'elle
// ---------------------------------------------------------------------------------------------------------------
/** Erreurs affichées pendant cette session : elles disparaissent de l'écran, le centre de notifications les garde. */
const toastLog = [];
function toast(kind, text, options = {}) {
  const key = statusKind(kind);
  if (key === 'err' && !options.busy) { toastLog.unshift({ t: Date.now() / 1000, level: 'critical', title: text }); toastLog.length = Math.min(toastLog.length, 20); }
  const holder = $('toasts');
  const body = el('span', { class: 'toast-text' }, text);
  const close = el('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Fermer', onclick: () => api_close() }, icon('x'));
  const head = el('div', { class: 'toast-main' }, key === 'info' && options.busy ? el('span', { class: 'spinner', 'aria-hidden': 'true' }) : icon(STATUS[key].icon), body);
  const node = el('div', { class: `toast ${key}`, role: key === 'err' ? 'alert' : 'status' }, head, close);
  let timer = null;
  function api_close() { clearTimeout(timer); node.remove(); }
  function setTimer(ms) { clearTimeout(timer); if (ms) timer = setTimeout(api_close, ms); }
  if (options.action) head.append(el('button', { class: 'btn sm', type: 'button', onclick: options.action.run }, options.action.label));
  holder.append(node);
  while (holder.children.length > 4) holder.firstElementChild.remove();
  setTimer(options.sticky ? 0 : options.ttl || (key === 'err' ? 12000 : 5000));
  return {
    close: api_close,
    update(newKind, newText, opts = {}) {
      const k = statusKind(newKind);
      node.className = `toast ${k}`;
      node.setAttribute('role', k === 'err' ? 'alert' : 'status');
      head.firstElementChild.replaceWith(opts.busy ? el('span', { class: 'spinner', 'aria-hidden': 'true' }) : icon(STATUS[k].icon));
      body.textContent = newText;
      setTimer(opts.sticky ? 0 : k === 'err' ? 12000 : 5000);
    },
  };
}

const drawer = {
  open() {
    $('drawer').classList.remove('hidden');
    $('drawer').setAttribute('aria-hidden', 'false');
    document.body.classList.add('drawer-open');
    $('drawer-close').focus({ preventScroll: true });
  },
  close() {
    $('drawer').classList.add('hidden');
    $('drawer').setAttribute('aria-hidden', 'true');
    document.body.classList.remove('drawer-open');
  },
  show(title, kind, text, log) {
    $('drawer-title').textContent = title;
    $('drawer-state').replaceChildren(pill(text, kind));
    $('drawer-log').textContent = log || '...';
    const box = $('drawer-log');
    box.scrollTop = box.scrollHeight;
  },
};

// ---------------------------------------------------------------------------------------------------------------
//  Boîtes de dialogue : confirmation d'une action destructrice et réglages d'affichage
// ---------------------------------------------------------------------------------------------------------------
function ask({ title, message, confirm = 'Confirmer', danger = false }) {
  return new Promise((resolve) => {
    const dialog = $('dialog');
    $('dialog-title').textContent = title;
    $('dialog-text').textContent = message || '';
    const yes = $('dialog-yes');
    yes.className = `btn ${danger ? 'danger' : 'primary'}`;
    yes.replaceChildren(confirm);
    yes.removeAttribute('data-ic');
    decorate(dialog);
    let answer = false;
    const done = () => { dialog.removeEventListener('close', done); resolve(answer); };
    dialog.addEventListener('close', done);
    yes.onclick = () => { answer = true; dialog.close(); };
    $('dialog-no').onclick = () => dialog.close();
    dialog.showModal();
    $('dialog-no').focus();
  });
}

/** Demande confirmation dans une boîte de dialogue, puis exécute l'action. Le texte du bouton nomme l'action. */
function confirmAction(button, message, action) {
  const verb = (button.getAttribute('aria-label') || button.textContent || 'Confirmer').trim();
  const danger = button.classList.contains('danger') || /supprim|vider|effac|retir|révoqu|delete|empty|remove|revoke|erase/i.test(verb);
  ask({ title: verb, message: /^Confirmer\s*\??$/.test(message) ? 'Cette action modifie le système. Voulez-vous continuer ?' : message, confirm: verb, danger })
    .then((yes) => { if (yes) action(); });
}

const DISPLAY_LABELS = {
  theme: ['Thème', { auto: 'Automatique', light: 'Clair', dark: 'Sombre' }],
  density: ['Densité', { comfortable: 'Confortable', compact: 'Compacte' }],
  contrast: ['Contraste', { normal: 'Normal', high: 'Élevé' }],
  text: ['Taille du texte', { normal: 'Normale', large: 'Grande', xlarge: 'Très grande' }],
  lang: ['Langue', { fr: 'Français', en: 'English' }],
};
function displayControls() {
  const current = window.Display.get();
  return Object.entries(DISPLAY_LABELS).map(([key, [title, labels]]) => el('div', { class: 'field' },
    el('span', { class: 'field-label' }, title),
    el('div', { class: 'segmented', role: 'group', 'aria-label': title },
      ...window.Display.CHOICES[key].map((choice) => el('button', {
        class: `seg${current[key] === choice ? ' active' : ''}`, type: 'button', 'aria-pressed': String(current[key] === choice),
        onclick: () => { window.Display.set({ [key]: choice }); refreshDisplay(); if (key === 'lang') languageChanged(choice); },
      }, labels[choice])))));
}
function refreshDisplay() {
  for (const holder of document.querySelectorAll('.display-controls')) holder.replaceChildren(...displayControls());
}
function openDisplay() {
  refreshDisplay();
  $('display-dialog').showModal();
}

// ---------------------------------------------------------------------------------------------------------------
//  Glossaire des actions : un verbe = un sens = une icône (voir docs/GLOSSAIRE.md)
// ---------------------------------------------------------------------------------------------------------------
const GLOSSARY = [
  ['Enregistrer', 'save', 'Conserver ce qui est saisi dans un formulaire.'],
  ['Appliquer', 'check', 'Mettre une configuration en service : le système change tout de suite.'],
  ['Ajouter', 'plus', 'Ajouter un élément à une liste qui existe déjà.'],
  ['Créer', 'plus', 'Produire un nouvel objet : compte, invitation, sauvegarde.'],
  ['Modifier', 'pencil', 'Rouvrir un élément pour en changer les valeurs.'],
  ['Supprimer', 'trash', 'Effacer des données : définitif, avec confirmation.'],
  ['Vider', 'trash', 'Supprimer tout le contenu d’un dossier ou d’une corbeille.'],
  ['Retirer', 'minus-circle', 'Détacher un élément d’une liste ou d’une configuration, sans effacer ses données.'],
  ['Révoquer', 'ban', 'Annuler un accès accordé : invitation, jeton, session.'],
  ['Annuler', 'x', 'Abandonner l’opération en cours sans rien changer.'],
  ['Fermer', 'x', 'Fermer une fenêtre ou un panneau.'],
  ['Confirmer', 'check', 'Valider une boîte de dialogue.'],
  ['Actualiser', 'refresh', 'Relire les données affichées, sans rien modifier.'],
  ['Relancer', 'refresh', 'Rejouer une opération ou un service.'],
  ['Réessayer', 'refresh', 'Rejouer une opération qui a échoué.'],
  ['Redémarrer', 'refresh', 'Arrêter puis redémarrer un service.'],
  ['Synchroniser', 'refresh', 'Mettre deux éléments d’accord entre eux.'],
  ['Vérifier', 'check-circle', 'Lancer un contrôle qui ne modifie rien.'],
  ['Tester', 'check-circle', 'Essayer une connexion ou un réglage sans l’appliquer.'],
  ['Comparer', 'search', 'Mettre deux états côte à côte pour voir ce qui diffère.'],
  ['Simuler', 'play', 'Montrer ce que ferait une opération sans la faire.'],
  ['Rechercher', 'search', 'Chercher dans des données.'],
  ['Filtrer', 'search', 'Réduire une liste.'],
  ['Analyser', 'search', 'Parcourir pour établir un état des lieux.'],
  ['Télécharger', 'download', 'Récupérer un fichier depuis le serveur.'],
  ['Sauvegarder', 'download', 'Réservé aux sauvegardes du serveur.'],
  ['Importer', 'upload', 'Faire entrer des données dans l’appliance.'],
  ['Envoyer', 'send', 'Transmettre vers l’extérieur : message, copie hors serveur.'],
  ['Mettre à jour', 'upload', 'Installer une version plus récente.'],
  ['Revenir', 'undo', 'Rétablir la version précédente.'],
  ['Restaurer', 'undo', 'Remettre des données depuis une sauvegarde.'],
  ['Copier', 'copy', 'Copier dans le presse-papiers.'],
  ['Afficher', 'eye', 'Montrer une information masquée ou la suite d’une liste.'],
  ['Régler', 'sliders', 'Ouvrir la page qui règle un point de configuration.'],
  ['Défaire', 'undo', 'Annuler une action qui vient d’être faite : notification avec retour immédiat.'],
  ['Installer', 'play', 'Mettre en place une application.'],
  ['Activer', 'play', 'Mettre une fonction en service.'],
  ['Désactiver', 'ban', 'Mettre une fonction hors service.'],
  ['Abandonner', 'x', 'Renoncer à une opération en attente.'],
  ['Ignorer', 'x', 'Ne plus signaler ce point.'],
  ['Changer', 'pencil', 'Remplacer une valeur existante : mot de passe, rôle.'],
  ['Monter', 'play', 'Monter un partage ou un disque.'],
  ['Démonter', 'x', 'Démonter un partage ou un disque.'],
  ['Identifiants', 'key', 'Saisir ou voir les identifiants d’un partage.'],
  ['Limites', 'sliders', 'Régler les limites d’un compte.'],
  ['Libérer', 'undo', 'Retirer l’épinglage d’un service.'],
  ['Renouveler', 'refresh', 'Remplacer un secret par un nouveau.'],
  ['Réinstaller', 'upload', 'Réinstaller la version courante.'],
  ['Mettre à la corbeille', 'trash', 'Mettre de côté avant suppression définitive.'],
];
const ACTION_ICON = Object.fromEntries(GLOSSARY.map(([verb, name]) => [verb.toLowerCase(), name]));
const VERBS_BY_LENGTH = Object.keys(ACTION_ICON).sort((a, b) => b.length - a.length);
/** Ajoute l'icône du glossaire devant chaque bouton dont le texte commence par un verbe connu. */
function decorate(root = document) {
  for (const button of root.querySelectorAll('.btn:not([data-ic]), .btn[data-ic=""]')) {
    const text = button.textContent.trim().toLowerCase();
    const verb = VERBS_BY_LENGTH.find((v) => text === v || text.startsWith(`${v} `) || text.startsWith(`${v} `) || text.startsWith(`${v}’`));
    const name = button.dataset.icon || (verb && ACTION_ICON[verb]);
    button.dataset.ic = name || '-';
    if (name && !button.querySelector('.ic')) button.prepend(icon(name));
  }
}
let decorating = false;
function watchDecorations() {
  decorate(document);
  new MutationObserver(() => {
    if (decorating) return;
    decorating = true;
    requestAnimationFrame(() => { decorate(document); decorating = false; });
  }).observe(document.body, { childList: true, subtree: true });
}

// ---------------------------------------------------------------------------------------------------------------
//  Navigation groupée : barre latérale (ordinateur) et barre basse (téléphone), une seule source
// ---------------------------------------------------------------------------------------------------------------
const NAV = [
  { id: 'overview', label: 'Vue d’ensemble', icon: 'home', items: [{ view: 'overview', label: 'Vue d’ensemble', icon: 'home' }, { view: 'maison', label: 'Maison', icon: 'monitor' }] },
  { id: 'services', label: 'Services', icon: 'layers', items: [
    { view: 'apps', label: 'Applications', icon: 'apps' }, { view: 'catalog', label: 'Catalogue', icon: 'package' },
    { view: 'trackers', label: 'Trackers', icon: 'target' }, { view: 'p2p', label: 'P2P Aggregator', icon: 'share' },
    { view: 'quality', label: 'Qualité des médias', icon: 'check-circle' }] },
  { id: 'storage', label: 'Stockage', icon: 'disk', items: [
    { view: 'shares', label: 'Partages', icon: 'folder' }, { view: 'backup', label: 'Sauvegardes', icon: 'download' },
    { view: 'cleanup', label: 'Nettoyage', icon: 'trash' }, { view: 'disks', label: 'Santé des disques', icon: 'disk' },
    { view: 'medialist', label: 'Liste des médias', icon: 'file' }] },
  { id: 'network', label: 'Réseau', icon: 'globe', items: [
    { view: 'vpn', label: 'VPN', icon: 'shield' }, { view: 'domain', label: 'Adresse', icon: 'globe' }, { view: 'netcheck', label: 'Diagnostic', icon: 'check-circle' }, { view: 'bandwidth', label: 'Débit', icon: 'activity' }] },
  { id: 'security', label: 'Sécurité', icon: 'lock', items: [
    { view: 'users', label: 'Comptes', icon: 'users' }, { view: 'guests', label: 'Invités', icon: 'user-plus' },
    { view: 'secrets', label: 'Identifiants', icon: 'key' }, { view: 'activity', label: 'Activité', icon: 'activity' },
    { view: 'vulns', label: 'Vulnérabilités', icon: 'shield' }] },
  { id: 'ops', label: 'Exploitation', icon: 'activity', items: [
    { view: 'system', label: 'Système', icon: 'monitor' }, { view: 'alerts', label: 'Alertes', icon: 'bell' },
    { view: 'report', label: 'Rapport', icon: 'file' }, { view: 'vacation', label: 'Vacances', icon: 'sun' }, { view: 'diagnose', label: 'Dépannage', icon: 'help' },
    { view: 'telecommande', label: 'Télécommande', icon: 'play' }, { view: 'setup', label: 'Assistant', icon: 'check-circle' },
    { view: 'journal', label: 'Journal', icon: 'file' }, { view: 'changes', label: 'Journal des réglages', icon: 'undo' }, { view: 'settings', label: 'Paramètres', icon: 'sliders' },
    { view: 'styleguide', label: 'Guide de style', icon: 'palette' }] },
];
/** Pages d'un observateur : lecture seule, aucune page qui expose un secret ou un réglage. */
const OBSERVER_VIEWS = ['overview', 'apps', 'system', 'alerts', 'report', 'maison'];
const readonlyMode = () => typeof SESSION !== 'undefined' && SESSION.readonly;
/** Navigation visible pour le compte courant. */
const visibleNav = () => NAV.map((g) => ({ ...g, items: g.items.filter((i) => !readonlyMode() || OBSERVER_VIEWS.includes(i.view)) })).filter((g) => g.items.length);
const NAV_VIEWS = NAV.flatMap((g) => g.items.map((i) => i.view));
const navLink = (item) => el('a', { href: `#${item.view}`, 'data-view': item.view }, icon(item.icon), el('span', {}, item.label));

function buildNav() {
  const side = $('nav');
  side.replaceChildren(...visibleNav().map((group) => (group.items.length === 1
    ? el('div', { class: 'nav-group single' }, navLink(group.items[0]))
    : el('div', { class: 'nav-group' }, el('div', { class: 'nav-title' }, group.label), ...group.items.map(navLink)))));
  const bar = $('tabbar');
  bar.replaceChildren(...visibleNav().map((group) => (group.items.length === 1
    ? el('a', { href: `#${group.items[0].view}`, class: 'tabbar-item', 'data-group': group.id }, icon(group.icon), el('span', {}, group.id === 'overview' ? 'Accueil' : group.items[0].label))
    : el('button', { type: 'button', class: 'tabbar-item', 'data-group': group.id, 'aria-haspopup': 'true', 'aria-expanded': 'false',
      onclick: () => toggleSheet(group.id) }, icon(group.icon), el('span', {}, group.label)))));
}
function toggleSheet(groupId) {
  const sheet = $('sheet');
  const group = visibleNav().find((g) => g.id === groupId);
  const same = !sheet.classList.contains('hidden') && sheet.dataset.group === groupId;
  closeSheet();
  if (same) return;
  sheet.dataset.group = groupId;
  sheet.replaceChildren(el('div', { class: 'nav-title' }, group.label), ...group.items.map(navLink));
  sheet.classList.remove('hidden');
  for (const b of document.querySelectorAll('.tabbar-item[data-group]')) b.setAttribute('aria-expanded', String(b.dataset.group === groupId));
}
function closeSheet() {
  $('sheet').classList.add('hidden');
  for (const b of document.querySelectorAll('.tabbar-item[aria-expanded]')) b.setAttribute('aria-expanded', 'false');
}
function setActiveNav(view) {
  for (const link of document.querySelectorAll('#nav a, #sheet a')) {
    const active = link.dataset.view === view;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  }
  const group = NAV.find((g) => g.items.some((i) => i.view === view));
  for (const item of document.querySelectorAll('.tabbar-item')) item.classList.toggle('active', !!group && item.dataset.group === group.id);
  closeSheet();
}

// ---------------------------------------------------------------------------------------------------------------
//  Contraste : mesure réelle des couleurs affichées (voir « Guide de style » et tests/visual_test.py)
// ---------------------------------------------------------------------------------------------------------------
function parseColor(text) {
  const m = /rgba?\(([^)]+)\)/.exec(text);
  if (!m) return null;
  const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
  return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
}
const mix = (fg, bg) => [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat(1);
function luminance(c) {
  const f = (x) => { const v = x / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
}
function contrastRatio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
const CONTRAST_PAIRS = [
  ['--text', '--bg'], ['--text', '--panel'], ['--muted', '--panel'], ['--muted', '--bg'], ['--brand', '--panel'],
  ['--on-accent', '--accent'], ['--ok', '--panel', '--ok-bg'], ['--warn', '--panel', '--warn-bg'], ['--err', '--panel', '--err-bg'],
  ['--info', '--panel', '--info-bg'], ['--unknown', '--panel', '--unknown-bg'],
];
function contrastReport() {
  const probe = el('span', { class: 'probe' });
  document.body.append(probe);
  const read = (name) => { probe.style.color = `var(${name})`; return parseColor(getComputedStyle(probe).color); };
  const rows = CONTRAST_PAIRS.map(([fg, bg, tint]) => {
    let back = read(bg);
    if (tint) back = mix(read(tint), back);
    const front = read(fg);
    const ratio = contrastRatio(front[3] < 1 ? mix(front, back) : front, back);
    return { fg, bg: tint ? `${bg} + ${tint}` : bg, ratio, ok: ratio >= 4.5 };
  });
  probe.remove();
  return rows;
}
