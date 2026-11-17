'use strict';
// SeedBoxBuntu - console : pages « Vue d'ensemble » et « Guide de style », réglages d'affichage.
// Chargé après features.js et avant app.js ; s'appuie sur ui.js (tuiles, états, formats) et sur api/showError d'app.js.

// ---------------------------------------------------------------------------------------------------------------
//  Vue d'ensemble : les mêmes tuiles, la même échelle d'états que le reste de la console
// ---------------------------------------------------------------------------------------------------------------
function setTile(id, kind, value, detail, pillText) {
  const tile = $(id);
  const key = statusKind(kind);
  tile.className = `card tile ${key}`;
  tile.querySelector('.tile-pill').replaceChildren(pill(pillText, key));
  tile.querySelector('.tile-value').textContent = value;
  tile.querySelector('.tile-detail').textContent = detail;
}
const tileFailed = (id, error) => setTile(id, 'unknown', '—', `Indisponible : ${error.message}`, 'Inconnu');

async function overviewHealth() {
  const status = await api('/api/status');
  const apps = status.apps;
  const down = apps.filter((a) => a.state !== 'running' || a.health === 'unhealthy');
  const sick = apps.some((a) => a.health === 'unhealthy');
  const kind = !status.deployed ? 'warn' : sick ? 'err' : down.length ? 'warn' : 'ok';
  const names = down.slice(0, 3).map((a) => a.name).join(', ');
  setTile('tile-health', kind, `${apps.length - down.length} / ${apps.length}`,
    !status.deployed ? 'Le déploiement n’est pas terminé.' : down.length ? `À regarder : ${names}${down.length > 3 ? '…' : ''}` : 'Tous les services répondent.',
    kind === 'ok' ? 'OK' : kind === 'err' ? 'Erreur' : 'Attention');
}

async function overviewSpace() {
  const data = await api('/api/system');
  const disks = data.disks;
  $('overview-disks').replaceChildren(...(disks.length
    ? disks.map((d) => gauge(d.percent, d.label, `${d.path} : ${fmtSize(d.used)} / ${fmtSize(d.total)}`)) : [emptyItem('Aucun disque détecté.')]));
  if (!disks.length) { setTile('tile-space', 'unknown', '—', 'Aucun disque détecté.', 'Inconnu'); return; }
  const worst = disks.reduce((a, b) => (b.percent > a.percent ? b : a));
  const kind = levelOf(worst.percent);
  setTile('tile-space', kind, fmtPercent(worst.percent), `${worst.label} : ${fmtSize(Math.max(0, worst.total - worst.used))} libres sur ${fmtSize(worst.total)}`,
    kind === 'ok' ? 'OK' : kind === 'err' ? 'Critique' : 'Presque plein');
  const more = $('tile-space').querySelector('.more-link');
  more.setAttribute('href', kind === 'ok' ? '#system' : '#cleanup');
  more.textContent = kind === 'ok' ? 'Voir le système' : 'Voir ce qui prend de la place';
}

async function overviewUpdates() {
  const data = await api('/api/appliance');
  const { installed, check, status } = data;
  const version = `Version installée : ${installed.version}${installed.commit ? ` (${installed.commit.slice(0, 7)})` : ''}`;
  if (status && status.state === 'error') setTile('tile-updates', 'err', 'Échec', `La dernière opération a échoué. ${version}`, 'Erreur');
  else if (check && check.available) setTile('tile-updates', 'info', 'Disponible', `${check.remote ? check.remote.short : ''} · ${version}`, 'Information');
  else if (check && !check.error) setTile('tile-updates', 'ok', 'À jour', version, 'OK');
  else setTile('tile-updates', 'unknown', 'Non vérifié', version, 'Inconnu');
}

async function overviewAlerts() {
  const data = await api('/api/alerts');
  const active = data.active;
  const worst = active.some((a) => a.level === 'critical') ? 'err' : active.length ? 'warn' : 'ok';
  setTile('tile-alerts', worst, String(active.length), active.length ? active[0].title : 'Aucun problème constaté.', worst === 'ok' ? 'OK' : worst === 'err' ? 'Erreur' : 'Attention');
  $('overview-alerts').replaceChildren(...(active.length
    ? active.map((a) => el('li', {}, pill(a.notified ? 'signalé' : 'constaté', { critical: 'err', warning: 'warn' }[a.level] || 'warn'), ` ${a.title}`,
      a.detail ? el('span', { class: 'muted' }, ` : ${a.detail}`) : null,
      a.key && a.key.startsWith('disque:') ? [' ', el('a', { href: '#cleanup' }, 'Voir ce qui prend de la place')] : null))
    : [emptyItem('Aucun problème constaté.')]));
}

async function overviewGuests() {
  const data = await api('/api/guests');
  const active = data.invites.filter((i) => i.status === 'active');
  const accounts = data.accounts.filter((a) => a.guest).length;
  const next = active.map((i) => i.expires).filter(Boolean).sort((a, b) => a - b)[0];
  setTile('tile-guests', active.length ? 'info' : 'ok', String(active.length),
    `${accounts} compte(s) invité(s)${next ? ` · prochaine échéance ${fmtDay(next)}` : ''}`, active.length ? 'Information' : 'OK');
}

// Toujours chargés : ils alimentent aussi les cartes « Disques » et « Problèmes en cours » sous les tuiles
const OVERVIEW_ALWAYS = new Set(['space', 'alerts']);
function overviewJobs() {
  const loaders = { health: overviewHealth, space: overviewSpace, updates: overviewUpdates, alerts: overviewAlerts, backup: overviewBackup, guests: overviewGuests, setup: overviewSetup };
  const shown = new Set(visibleTiles());
  return Object.entries(loaders).filter(([id]) => (shown.has(id) || OVERVIEW_ALWAYS.has(id)) && tileLayout().some((t) => t.id === id)).map(([id, run]) => [`tile-${id}`, run]);
}

let overviewFailures = 0;
async function refreshOverview() {
  applyTileLayout();
  const results = await Promise.all(overviewJobs().map(([id, run]) => run().then(() => true, (error) => { tileFailed(id, error); return false; })));
  overviewFailures = results.every((ok) => !ok) ? overviewFailures + 1 : 0;
  if (kioskActive()) {
    kioskUpdated(overviewFailures === 0);
    if (overviewFailures >= 5) location.reload();                 // session expirée ou réseau coupé : retour à la connexion
  }
}

async function loadOverview() {
  await refreshOverview();
  clearInterval(viewTimer);
  viewTimer = setInterval(() => { refreshOverview().catch(() => {}); }, 30000);
}

// ---------------------------------------------------------------------------------------------------------------
//  Guide de style : chaque composant rendu avec les vrais styles
// ---------------------------------------------------------------------------------------------------------------
const COLOR_GROUPS = [
  ['Surfaces', ['bg', 'panel', 'panel2', 'line', 'line2', 'chip', 'code']],
  ['Texte', ['text', 'muted']],
  ['Marque', ['brand', 'accent', 'accent-hover', 'on-accent']],
  ['États', ['ok', 'warn', 'err', 'info', 'unknown', 'ok-bg', 'warn-bg', 'err-bg', 'info-bg', 'unknown-bg']],
  ['Séries', ['c0', 'c1', 'c2', 'c3']],
];
function swatch(name) {
  const chip = el('div', { class: 'sw-chip' });
  chip.style.background = `var(--${name})`;
  return el('div', { class: 'sw' }, chip, `--${name}`);
}

function renderContrast() {
  $('sg-contrast').replaceChildren(...contrastReport().map((r) => el('tr', {},
    el('td', {}, r.fg), el('td', {}, r.bg), el('td', { class: r.ok ? '' : 'fail' }, `${fmtNumber(r.ratio, 2)} : 1`),
    el('td', {}, pill(r.ok ? 'AA' : 'Sous le seuil', r.ok ? 'ok' : 'err')))));
}

function loadStyleguide() {
  $('sg-colors').replaceChildren(...COLOR_GROUPS.flatMap(([title, names]) => [el('h3', { class: 'small muted sg-group' }, title), ...names.map(swatch)]));
  const space = [1, 2, 3, 4, 5, 6, 7].map((n) => {
    const bar = el('span', { class: 'demo-box' });
    bar.style.width = `var(--sp-${n})`;
    return el('div', { class: 'row small' }, el('code', {}, `--sp-${n}`), bar);
  });
  const radii = [['--r-sm'], ['--r'], ['--r-pill']].map(([name]) => {
    const box = el('span', { class: 'demo-box demo-radius' });
    box.style.borderRadius = `var(${name})`;
    return el('div', { class: 'row small' }, el('code', {}, name), box);
  });
  $('sg-space').replaceChildren(...space, ...radii);
  $('sg-pills').replaceChildren(...['ok', 'warn', 'err', 'info', 'unknown'].map((k) => pill('', k)));
  $('sg-row-ok').replaceChildren(pill('actif', 'ok'));
  $('sg-row-warn').replaceChildren(pill('en difficulté', 'err'));
  $('sg-gauges').replaceChildren(gauge(42, 'Disque normal', '420 Go / 1 To'), gauge(88, 'Disque presque plein', 'seuil 85 %'), gauge(97, 'Disque critique', 'seuil 95 %'));
  $('sg-states').replaceChildren(stateBlock('loading', 'Chargement en cours…'), stateBlock('empty', 'Rien à afficher pour le moment.'), stateBlock('error', 'Impossible de charger les données.'));
  $('sg-skeleton').replaceChildren(...skeletonRows(2, 2));
  const now = Math.floor(Date.now() / 1000);
  const points = Array.from({ length: 48 }, (_, i) => [now - 86400 + i * 1800, 55 + 38 * Math.sin(i / 7)]);
  $('sg-chart').replaceChildren(lineChart([{ name: 'Exemple', points }], { seconds: 86400, now, fmt: fmtPercent, max: 100, title: 'Courbe en pourcentage' }));
  $('sg-formats').replaceChildren(
    el('dt', {}, 'Date et heure'), el('dd', {}, fmtDateTime(now)), el('dt', {}, 'Jour'), el('dd', {}, fmtDay(now)),
    el('dt', {}, 'Heure'), el('dd', {}, fmtTime(now)), el('dt', {}, 'Taille'), el('dd', {}, `${fmtSize(512)} · ${fmtSize(48 * 1024)} · ${fmtSize(3.4 * 1024 ** 2)} · ${fmtSize(1.2 * 1024 ** 3)}`),
    el('dt', {}, 'Débit'), el('dd', {}, fmtRate(5.5 * 1024 ** 2)), el('dt', {}, 'Durée'), el('dd', {}, `${fmtDuration(45)} · ${fmtDuration(7500)} · ${fmtDuration(300000)}`),
    el('dt', {}, 'Pourcentage'), el('dd', {}, fmtPercent(87.4)));
  $('sg-glossary').replaceChildren(...GLOSSARY.map(([verb, name, meaning]) => el('tr', {},
    el('td', { class: 'nowrap' }, icon(name), ' ', el('strong', {}, verb)), el('td', { class: 'muted' }, meaning))));
  renderContrast();
}

// ---------------------------------------------------------------------------------------------------------------
//  Branchements communs : tiroir, réglages d'affichage, barre mobile, guide de style
// ---------------------------------------------------------------------------------------------------------------
function initPages() {
  $('overview-refresh').addEventListener('click', () => { loadOverview().catch(showError); });
  $('drawer-close').addEventListener('click', () => drawer.close());
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !$('drawer').classList.contains('hidden') && !document.querySelector('dialog[open]')) drawer.close();
  });
  document.addEventListener('click', (event) => {
    if (!$('sheet').classList.contains('hidden') && !event.target.closest('#sheet, #tabbar')) closeSheet();
  });
  for (const button of document.querySelectorAll('#display-open, [data-display-open], #sg-display')) button.addEventListener('click', openDisplay);
  $('display-close').addEventListener('click', () => $('display-dialog').close());
  $('display-reset').addEventListener('click', () => {
    const before = window.Display.get();
    window.Display.reset(); refreshDisplay(); renderContrastIfOpen();
    if (before.lang !== window.Display.get().lang) { languageChanged(window.Display.get().lang); return; }
    undoToast('Affichage réinitialisé.', () => { window.Display.set(before); refreshDisplay(); renderContrastIfOpen(); });
  });
  $('display-dialog').addEventListener('close', renderContrastIfOpen);
  $('display-dialog').addEventListener('click', renderContrastIfOpen);
  $('sg-contrast-run').addEventListener('click', renderContrast);
  $('sg-toast').addEventListener('click', () => toast('ok', 'Exemple de notification : l’action est terminée.'));
  $('sg-drawer').addEventListener('click', () => {
    drawer.show('Exemple de tâche', 'ok', 'terminé', 'Étape 1 : vérification\nÉtape 2 : application\nTerminé.');
    drawer.open();
  });
  $('sg-confirm').addEventListener('click', (event) => confirmAction(event.currentTarget, 'Exemple : aucune donnée n’est réellement supprimée.', () => toast('info', 'Action confirmée (exemple).')));
}
function renderContrastIfOpen() {
  if (!$('view-styleguide').classList.contains('hidden')) setTimeout(renderContrast, 50);
}
