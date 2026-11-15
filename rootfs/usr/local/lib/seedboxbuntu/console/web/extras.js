'use strict';
// SeedBoxBuntu - console : session et rôle (lecture seule), préférences par compte, recherche Ctrl+K, centre de
// notifications, mode kiosque, tuiles personnalisables, assistant de configuration, rapport hebdomadaire, annulation.
// Chargé après pages.js et avant app.js ; s'appuie sur ui.js (composants), pages.js (tuiles) et api/showError d'app.js.

// ---------------------------------------------------------------------------------------------------------------
//  Session : qui est connecté, avec quels droits, et ses préférences (enregistrées sur le serveur, par compte)
// ---------------------------------------------------------------------------------------------------------------
const SESSION = { user: '', role: 'observer', readonly: true, prefs: {} };   // refus par défaut tant que le serveur n'a pas répondu

async function loadSession() {
  const data = await api('/api/session');
  Object.assign(SESSION, { user: data.user, role: data.role || 'admin', readonly: !!data.readonly, prefs: data.prefs || {} });
  return data;
}

/** Enregistre des préférences du compte ; l'échec est signalé mais ne bloque jamais l'écran. */
async function savePrefs(patch) {
  Object.assign(SESSION.prefs, patch);
  try { await api('/api/prefs', patch); } catch (error) { showError(error); }
}

/** Langue : mémorisée dans le navigateur (display.js) et dans le compte ; la page se recharge pour tout reformater. */
async function languageChanged(choice) {
  await savePrefs({ lang: choice });
  location.reload();
}
/** Première connexion sur un autre navigateur : la langue du compte s'applique. */
function adoptAccountLanguage() {
  const wanted = SESSION.prefs.lang;
  if (wanted && !window.Display.chosen('lang') && wanted !== window.Display.get().lang) {
    window.Display.set({ lang: wanted });
    location.reload();
    return true;
  }
  return false;
}

// ---------------------------------------------------------------------------------------------------------------
//  Lecture seule : l'observateur voit, rien de plus (le serveur refuse de toute façon ; ceci évite des boutons morts)
// ---------------------------------------------------------------------------------------------------------------
const READONLY_HIDE = ['check-run', 'steps-card', 'versions-card', 'gpu-card', 'alerts-form', 'log-source'];
function lockReadonly() {
  if (!SESSION.readonly) return;
  document.body.classList.add('readonly');
  for (const id of READONLY_HIDE) {
    const node = $(id);
    const card = node && (node.classList.contains('card') ? node : node.closest('.card'));
    if (card) card.classList.add('admin-only');
  }
  for (const node of document.querySelectorAll('main .btn:not([data-ro-ok]), main input, main select, main textarea')) {
    if (node.closest('[data-ro-ok]') || node.tagName === 'A') continue;
    if (!node.disabled) { node.disabled = true; node.setAttribute('title', 'Lecture seule : réservé aux administrateurs'); }
  }
}
function watchReadonly() {
  if (!SESSION.readonly) return;
  let pending = false;
  new MutationObserver(() => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; lockReadonly(); });
  }).observe(document.querySelector('main'), { childList: true, subtree: true });
  lockReadonly();
}

// ---------------------------------------------------------------------------------------------------------------
//  Annulation après coup : une action qui vient d'être faite peut être défaite pendant quelques secondes
// ---------------------------------------------------------------------------------------------------------------
function undoToast(text, undo, seconds = 10) {
  const note = toast('info', text, {
    ttl: seconds * 1000,
    action: { label: 'Défaire', run: async () => { note.close(); try { await undo(); } catch (error) { showError(error); } } },
  });
  return note;
}

// ---------------------------------------------------------------------------------------------------------------
//  Vue d'ensemble : tuiles réordonnables et masquables, par compte
// ---------------------------------------------------------------------------------------------------------------
const TILE_TITLES = { health: 'Santé', space: 'Espace', updates: 'Mises à jour', alerts: 'Alertes', backup: 'Sauvegardes', guests: 'Invitations', setup: 'Configuration' };
const TILE_ADMIN_ONLY = new Set(['backup', 'guests', 'setup']);       // routes interdites à l'observateur
const TILE_ORDER = ['health', 'space', 'updates', 'alerts', 'backup', 'guests', 'setup'];
let customizing = false;

/** Disposition effective : préférence du compte, tuiles inconnues ignorées, nouvelles tuiles ajoutées à la fin. */
function tileLayout() {
  const saved = Array.isArray(SESSION.prefs.overview) ? SESSION.prefs.overview : [];
  const seen = new Set();
  const out = [];
  for (const entry of saved) {
    if (TILE_ORDER.includes(entry.id) && !seen.has(entry.id)) { seen.add(entry.id); out.push({ id: entry.id, hidden: !!entry.hidden }); }
  }
  for (const id of TILE_ORDER) if (!seen.has(id)) out.push({ id, hidden: false });
  return out.filter((t) => !(SESSION.readonly && TILE_ADMIN_ONLY.has(t.id)));
}

function visibleTiles() {
  return tileLayout().filter((t) => !t.hidden).map((t) => t.id);
}

function applyTileLayout() {
  const holder = $('tiles');
  const layout = tileLayout();
  for (const tile of document.querySelectorAll('#tiles .tile')) {
    if (!layout.some((t) => `tile-${t.id}` === tile.id)) tile.classList.add('is-hidden');
  }
  layout.forEach((entry, index) => {
    const tile = $(`tile-${entry.id}`);
    tile.classList.toggle('is-hidden', entry.hidden);
    tile.style.order = String(index);
    tile.toggleAttribute('data-hidden', entry.hidden);
    renderTileTools(tile, entry, index, layout.length);
  });
  const hidden = layout.filter((t) => t.hidden).length;
  const note = $('tiles-hidden');
  note.classList.toggle('hidden', !hidden || customizing);
  note.textContent = hidden ? `${hidden} tuile(s) masquée(s) : utilisez Personnaliser pour les afficher de nouveau.` : '';
}

function renderTileTools(tile, entry, index, count) {
  let tools = tile.querySelector('.tile-tools');
  if (!tools) { tools = el('div', { class: 'tile-tools' }); tile.querySelector('.tile-head').after(tools); }
  const name = TILE_TITLES[entry.id];
  const button = (act, label, name_, onclick, disabled) => el('button', { class: 'icon-btn', type: 'button', 'aria-label': label, 'data-tile-act': `${act}-${entry.id}`,
    disabled: disabled ? '' : null, onclick }, icon(name_, act === 'up' ? 'flip' : ''));
  tools.replaceChildren(
    button('up', `Monter la tuile ${name}`, 'chevron', () => moveTile(entry.id, -1), index === 0),
    button('down', `Descendre la tuile ${name}`, 'chevron', () => moveTile(entry.id, 1), index === count - 1),
    button('hide', entry.hidden ? `Afficher la tuile ${name}` : `Masquer la tuile ${name}`, entry.hidden ? 'eye' : 'ban', () => toggleTile(entry.id)));
}

async function commitLayout(layout, focus) {
  const saving = savePrefs({ overview: layout });              // l'écran change tout de suite, l'enregistrement suit
  applyTileLayout();
  if (focus) {
    const id = focus.split('-')[1];
    const target = document.querySelector(`#tiles [data-tile-act="${focus}"]:not([disabled])`) || document.querySelector(`#tiles [data-tile-act$="-${id}"]:not([disabled])`);
    if (target) target.focus();       // le clavier reste sur la tuile qu'on vient de déplacer ou masquer
  }
  await saving;
}
function moveTile(id, delta) {
  const layout = tileLayout();
  const from = layout.findIndex((t) => t.id === id);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= layout.length) return;
  [layout[from], layout[to]] = [layout[to], layout[from]];
  commitLayout(layout, `${delta < 0 ? 'up' : 'down'}-${id}`);
}
function toggleTile(id) {
  const layout = tileLayout();
  const entry = layout.find((t) => t.id === id);
  entry.hidden = !entry.hidden;
  commitLayout(layout, `hide-${id}`);
  if (entry.hidden) {
    undoToast(`Tuile « ${TILE_TITLES[id]} » masquée.`, () => { const again = tileLayout(); again.find((t) => t.id === id).hidden = false; return commitLayout(again); });
  } else {
    loadOverview().catch(showError);                              // une tuile qui reparaît se remplit tout de suite
  }
}
function setCustomizing(on) {
  customizing = on;
  document.body.classList.toggle('customizing', on);
  $('overview-customize').setAttribute('aria-pressed', String(on));
  setLabel($('overview-customize'), on ? 'Terminer' : 'Personnaliser');
  applyTileLayout();
}

// ---------------------------------------------------------------------------------------------------------------
//  Tuiles supplémentaires : sauvegardes et configuration
// ---------------------------------------------------------------------------------------------------------------
async function overviewBackup() {
  const data = await api('/api/backup');
  const newest = data.backups[0];
  const enabled = data.settings.enabled;
  const age = newest ? (Date.now() / 1000 - newest.time) / 86400 : null;
  if (!newest) setTile('tile-backup', enabled ? 'warn' : 'info', 'Aucune', enabled ? 'Aucune sauvegarde alors que la sauvegarde automatique est active.' : 'Aucune sauvegarde : lancez-en une depuis Sauvegardes.', enabled ? 'Attention' : 'Information');
  else if (enabled && age > 8) setTile('tile-backup', 'warn', fmtDay(newest.time), `Plus de 8 jours depuis la dernière sauvegarde (${data.backups.length} conservées).`, 'Attention');
  else setTile('tile-backup', 'ok', fmtDay(newest.time), `${data.backups.length} sauvegarde(s) · ${enabled ? 'automatique' : 'manuelle'}`, 'OK');
}

async function overviewSetup() {
  const data = await api('/api/assistant');
  const risky = data.items.find((i) => i.state === 'warn' && ['Sécurité', 'Données'].includes(i.group));
  if (!data.todo) setTile('tile-setup', 'ok', 'Complète', 'Tous les points de configuration sont réglés.', 'OK');
  else setTile('tile-setup', risky ? 'warn' : 'info', `${data.todo} / ${data.total}`, risky ? risky.title : 'Quelques réglages facultatifs restent.', risky ? 'Attention' : 'Information');
}

// ---------------------------------------------------------------------------------------------------------------
//  Mode kiosque : la vue d'ensemble seule, en grand, qui se rafraîchit seule
// ---------------------------------------------------------------------------------------------------------------
let kioskClock = null;
let wakeLock = null;
function kioskActive() { return document.body.classList.contains('kiosk'); }
function tickClock() {
  const now = new Date();
  $('kiosk-clock').textContent = fmtTime(now);
  $('kiosk-clock').setAttribute('datetime', now.toISOString());
}
function enterKiosk() {
  if (kioskActive()) return;
  document.body.classList.add('kiosk');
  $('kiosk-bar').classList.remove('hidden');
  tickClock();
  kioskClock = setInterval(tickClock, 1000);
  if (navigator.wakeLock) navigator.wakeLock.request('screen').then((lock) => { wakeLock = lock; }).catch(() => {});
}
function exitKiosk() {
  if (!kioskActive()) return;
  document.body.classList.remove('kiosk');
  $('kiosk-bar').classList.add('hidden');
  clearInterval(kioskClock);
  if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
}
function kioskUpdated(ok) {
  $('kiosk-updated').textContent = ok ? `Mis à jour à ${fmtTime(new Date())}` : 'Connexion perdue : nouvel essai en cours…';
}

// ---------------------------------------------------------------------------------------------------------------
//  Recherche Ctrl+K : pages, actions et applications au clavier
// ---------------------------------------------------------------------------------------------------------------
const plain = (text) => text.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
let paletteApps = { at: 0, list: [] };
let paletteRows = [];
let paletteIndex = 0;

function paletteItems() {
  const items = [];
  for (const group of visibleNav()) {
    for (const page of group.items) items.push({ kind: 'Page', label: page.label, icon: page.icon, extra: group.label, run: () => { location.hash = `#${page.view}`; } });
  }
  items.push({ kind: 'Action', label: 'Affichage', icon: 'sun', extra: 'thème contraste densité langue', run: openDisplay });
  items.push({ kind: 'Action', label: 'Notifications', icon: 'bell', extra: 'alertes cloche', run: () => openNotifications() });
  items.push({ kind: 'Action', label: 'Mode kiosque', icon: 'monitor', extra: 'écran mural plein écran', run: () => { location.hash = '#kiosk'; } });
  items.push({ kind: 'Action', label: 'Personnaliser la vue d’ensemble', icon: 'sliders', extra: 'tuiles', run: () => { location.hash = '#overview'; setTimeout(() => setCustomizing(true), 50); } });
  items.push({ kind: 'Action', label: EN ? 'Passer en français' : 'Passer en anglais', icon: 'globe', extra: 'langue language english français', run: () => { window.Display.set({ lang: EN ? 'fr' : 'en' }); languageChanged(EN ? 'fr' : 'en'); } });
  items.push({ kind: 'Action', label: 'Actualiser la page', icon: 'refresh', extra: 'recharger', run: () => route() });
  if (!SESSION.readonly) items.push({ kind: 'Action', label: 'Lancer une sauvegarde', icon: 'download', extra: 'backup', run: () => runJob('/api/backup/create', {}) });
  for (const app of paletteApps.list) {
    items.push({ kind: 'Application', label: `Ouvrir ${app.name}`, icon: 'external', extra: app.id, run: () => window.open(appUrl(app), '_blank', 'noopener') });
  }
  return items;
}

function paletteFilter(query) {
  const words = plain(query).split(/\s+/).filter(Boolean);
  const all = paletteItems();
  if (!words.length) return all.filter((i) => i.kind !== 'Application').slice(0, 12);
  return all
    .map((item) => {
      const hay = plain(`${item.label} ${tr(item.label)} ${item.extra || ''} ${item.kind}`);
      if (!words.every((w) => hay.includes(w))) return null;
      const start = plain(tr(item.label)).startsWith(words[0]) ? 0 : 1;
      return { item, score: start };
    })
    .filter(Boolean)
    .sort((a, b) => a.score - b.score)
    .map((r) => r.item)
    .slice(0, 12);
}

function paletteRender() {
  const list = $('palette-list');
  paletteRows = paletteFilter($('palette-input').value);
  paletteIndex = Math.min(paletteIndex, Math.max(0, paletteRows.length - 1));
  list.replaceChildren(...(paletteRows.length ? paletteRows.map((item, index) => el('li', {
    role: 'option', id: `pal-${index}`, 'aria-selected': String(index === paletteIndex),
    onclick: () => paletteRun(index),
  }, icon(item.icon), el('span', {}, tr(item.label)), el('span', { class: 'kind' }, tr(item.kind))))
    : [el('li', { role: 'option', 'aria-selected': 'false', 'aria-disabled': 'true' }, el('span', { class: 'muted' }, 'Aucun résultat.'))]));
  const input = $('palette-input');
  if (paletteRows.length) input.setAttribute('aria-activedescendant', `pal-${paletteIndex}`); else input.removeAttribute('aria-activedescendant');
  const active = $(`pal-${paletteIndex}`);
  if (active && active.scrollIntoView) active.scrollIntoView({ block: 'nearest' });
}

function paletteRun(index) {
  const item = paletteRows[index];
  if (!item) return;
  $('palette').close();
  item.run();
}

async function openPalette() {
  if ($('palette').open) return;
  closeSheet();
  $('palette-input').value = '';
  paletteIndex = 0;
  $('palette').showModal();
  paletteRender();
  $('palette-input').focus();
  if (Date.now() - paletteApps.at > 60000) {
    try {
      const status = await api('/api/status');
      paletteApps = { at: Date.now(), list: status.apps };
      if ($('palette').open) paletteRender();
    } catch (error) { paletteApps.at = Date.now(); }
  }
}

function initPalette() {
  for (const button of document.querySelectorAll('[data-palette-open]')) button.addEventListener('click', openPalette);
  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      if ($('palette').open) $('palette').close(); else if (!kioskActive()) openPalette();
    }
  });
  const input = $('palette-input');
  input.addEventListener('input', () => { paletteIndex = 0; paletteRender(); });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!paletteRows.length) return;
      paletteIndex = (paletteIndex + (event.key === 'ArrowDown' ? 1 : -1) + paletteRows.length) % paletteRows.length;
      paletteRender();
    } else if (event.key === 'Enter') {
      event.preventDefault();
      paletteRun(paletteIndex);
    }
  });
  $('palette').addEventListener('click', (event) => { if (event.target === $('palette')) $('palette').close(); });
}

// ---------------------------------------------------------------------------------------------------------------
//  Centre de notifications : alertes en cours, événements récents, erreurs de la session
// ---------------------------------------------------------------------------------------------------------------
const NOTIF_LEVEL = { critical: 'err', warning: 'warn', ok: 'ok', info: 'info' };
let notif = { items: [], seen: 0, now: 0 };

function notifItems() {
  const local = toastLog.map((e) => ({ id: `l-${e.t}`, t: e.t, level: e.level, title: e.title, detail: 'Erreur affichée pendant cette session.', active: false, view: '' }));
  return [...notif.items.filter((i) => i.active), ...[...notif.items.filter((i) => !i.active), ...local].sort((a, b) => b.t - a.t)];
}
function unreadCount() { return notifItems().filter((i) => i.t > notif.seen).length; }

function renderBadge() {
  const count = unreadCount();
  for (const badge of document.querySelectorAll('[data-notif-badge]')) {
    badge.classList.toggle('hidden', !count);
    badge.textContent = count > 99 ? '99+' : String(count);
  }
  for (const button of document.querySelectorAll('[data-notif-open]')) {
    const base = button.dataset.label || (button.dataset.label = button.getAttribute('aria-label') || '');
    if (base) button.setAttribute('aria-label', count ? `${tr(base)} (${count})` : tr(base));
  }
}

async function refreshNotifications() {
  try {
    const data = await api('/api/notifications');
    notif = { items: data.items, seen: Math.max(notif.seen, data.seen || 0), now: data.now };
    renderBadge();
  } catch (error) { /* silencieux : le prochain passage réessaiera */ }
}

function renderNotifications() {
  const items = notifItems();
  $('notif-list').replaceChildren(...(items.length ? items.map((item) => el('li', { class: item.t > notif.seen ? 'unread' : '' },
    pill('', NOTIF_LEVEL[item.level] || 'info'),
    el('div', {}, el('strong', {}, item.title), item.detail ? el('div', { class: 'muted small' }, item.detail) : null),
    el('div', { class: 'meta' }, fmtDateTime(item.t), item.active ? ' · en cours' : '', item.view && (!SESSION.readonly || OBSERVER_VIEWS.includes(item.view))
      ? el('span', {}, ' · ', el('a', { href: `#${item.view}`, onclick: () => $('notif-dialog').close() }, 'Afficher')) : null)))
    : [emptyItem('Aucune notification.')]));
}

async function markNotificationsRead() {
  const stamp = Math.max(notif.now || 0, Date.now() / 1000);
  notif.seen = stamp;
  renderBadge();
  try { await api('/api/prefs', { notif_seen: stamp }); } catch (error) { showError(error); }
}

async function openNotifications() {
  closeSheet();
  await refreshNotifications();
  renderNotifications();
  $('notif-dialog').showModal();
}

function initNotifications() {
  for (const button of document.querySelectorAll('[data-notif-open]')) button.addEventListener('click', openNotifications);
  $('notif-close').addEventListener('click', () => $('notif-dialog').close());
  $('notif-read').addEventListener('click', async () => { await markNotificationsRead(); renderNotifications(); });
  $('notif-dialog').addEventListener('close', markNotificationsRead);
  notif.seen = SESSION.prefs.notif_seen === undefined ? Date.now() / 1000 - 86400 : SESSION.prefs.notif_seen;   // premier passage : les dernières 24 h comptent comme nouvelles
  refreshNotifications();
  setInterval(refreshNotifications, 60000);
}

// ---------------------------------------------------------------------------------------------------------------
//  Assistant de configuration
// ---------------------------------------------------------------------------------------------------------------
const SETUP_STATE = { ok: 'ok', warn: 'warn', info: 'info', unknown: 'unknown' };
async function loadSetup() {
  const data = await api('/api/assistant');
  const summary = $('setup-summary');
  summary.className = `notice ${data.todo ? (data.secure ? 'info' : '') : 'ok'}`.trim();
  summary.textContent = !data.todo ? 'Tout est en ordre : chaque point de la liste est réglé.'
    : data.secure ? `${data.todo} point(s) facultatif(s) ou à vérifier sur ${data.total}. Rien d’urgent pour la sécurité.`
      : `${data.todo} point(s) à regarder sur ${data.total} : la sécurité ou vos données en dépendent.`;
  $('setup-list').replaceChildren(...data.items.map((item) => el('tr', {},
    el('td', {}, el('strong', {}, item.title), el('div', { class: 'muted small' }, item.group)),
    el('td', {}, pill('', SETUP_STATE[item.state] || 'unknown')),
    el('td', { class: 'muted' }, item.detail),
    el('td', {}, el('a', { class: 'btn sm', href: `#${item.view}` }, 'Régler')))));
}

async function nudgeSetup() {
  if (SESSION.readonly || SESSION.prefs.setup_dismissed || kioskActive()) return;
  try { if (sessionStorage.getItem('sb.setup-nudged')) return; sessionStorage.setItem('sb.setup-nudged', '1'); } catch (error) { /* stockage indisponible : un rappel par page chargée */ }
  try {
    const data = await api('/api/assistant');
    if (!data.secure && location.hash !== '#setup') {
      toast('warn', `${data.todo} point(s) de configuration à regarder (sécurité ou données).`, { ttl: 15000, action: { label: 'Afficher', run: () => { location.hash = '#setup'; } } });
    }
  } catch (error) { /* l'assistant n'est qu'un rappel */ }
}

// ---------------------------------------------------------------------------------------------------------------
//  Rapport hebdomadaire
// ---------------------------------------------------------------------------------------------------------------
const REPORT_LEVEL = { ok: 'ok', warning: 'warn', critical: 'err' };
function renderReport(report) {
  if (!report) {
    $('report-state').replaceChildren();
    $('report-date').textContent = '';
    $('report-text').textContent = 'Aucun rapport pour le moment : cliquez sur Actualiser.';
    return;
  }
  $('report-title').textContent = report.title || 'Dernier rapport';
  $('report-state').replaceChildren(pill('', REPORT_LEVEL[report.level] || 'unknown'));
  $('report-date').textContent = `Généré le ${fmtDateTime(report.generated)}${report.sent ? ` · envoyé le ${fmtDateTime(report.sent)}` : ' · pas encore envoyé'}`;
  $('report-text').textContent = report.text || '';
}
async function loadReport() {
  const data = await api('/api/report');
  const form = $('report-form');
  form.elements.enabled.checked = !!data.settings.enabled;
  form.elements.send.checked = !!data.settings.send;
  $('report-day').replaceChildren(...data.days.map((day, index) => el('option', { value: String(index), selected: index === data.settings.day ? '' : null }, day)));
  const channels = $('report-channels');
  channels.classList.toggle('hidden', !!data.channels.length);
  channels.replaceChildren('Aucun canal d’alerte configuré : le rapport reste visible ici. Configurez ntfy, Discord, un webhook ou un e-mail dans ', el('a', { href: '#alerts' }, 'Alertes'), '.');
  renderReport(data.last);
}

// ---------------------------------------------------------------------------------------------------------------
//  Branchements
// ---------------------------------------------------------------------------------------------------------------
function initExtras() {
  initPalette();
  initNotifications();
  $('overview-customize').addEventListener('click', () => setCustomizing(!customizing));
  $('overview-kiosk').addEventListener('click', () => {
    location.hash = '#kiosk';
    if (document.documentElement.requestFullscreen) document.documentElement.requestFullscreen().catch(() => {});
  });
  $('kiosk-exit').addEventListener('click', () => { location.hash = '#overview'; });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && kioskActive() && !document.querySelector('dialog[open]')) location.hash = '#overview';
  });
  $('setup-refresh').addEventListener('click', () => loadSetup().catch(showError));
  $('setup-dismiss').addEventListener('click', async () => { await savePrefs({ setup_dismissed: true }); toast('ok', 'Le rappel est désactivé : l’assistant reste disponible dans Exploitation.'); });
  $('report-build').addEventListener('click', async () => {
    try { renderReport(await api('/api/report/build', {})); toast('ok', 'Rapport actualisé.'); } catch (error) { showError(error); }
  });
  $('report-send').addEventListener('click', () => runJob('/api/report/send', {}, loadReport));
  $('report-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api('/api/report/settings', { enabled: form.elements.enabled.checked, send: form.elements.send.checked, day: Number(form.elements.day.value) });
      toast('ok', 'Réglages du rapport enregistrés.');
    } catch (error) { showError(error); }
  });
  watchReadonly();
  nudgeSetup();
}
