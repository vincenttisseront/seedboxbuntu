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

// Trafic des dernières 24 heures : l'envoi au-dessus de l'axe, la réception en dessous, à la même échelle
function seriesTotal(points) {
  let total = 0;
  for (let i = 1; i < points.length; i += 1) total += points[i][1] * (points[i][0] - points[i - 1][0]);
  return total;
}

function pulsePath(points, t0, t1, max, sign) {
  const x = (t) => ((t - t0) / (t1 - t0)) * 1000;
  const y = (v) => 60 - sign * (Math.min(v, max) / max) * 56;
  const r = (n) => Math.round(n * 10) / 10;
  const line = points.map(([t, v], i) => `${i ? 'L' : 'M'}${r(x(t))},${r(y(v))}`).join('');
  return { line, area: `${line}L${r(x(points[points.length - 1][0]))},60L${r(x(points[0][0]))},60Z` };
}

function renderPulse(up, down, now) {
  const plot = $('pulse-plot');
  const t1 = now;
  const t0 = now - 86400;
  const max = Math.max(1, ...up.map((p) => p[1]), ...down.map((p) => p[1]));
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 1000 120');
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('aria-hidden', 'true');
  const add = (tag, attrs) => { const node = document.createElementNS(ns, tag); Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v)); svg.append(node); return node; };
  for (const [points, sign, cls] of [[down, -1, 'down'], [up, 1, 'up']]) {
    if (points.length < 2) continue;
    const path = pulsePath(points, t0, t1, max, sign);
    add('path', { d: path.area, class: `pulse-area ${cls}` });
    add('path', { d: path.line, class: `pulse-line ${cls}`, 'vector-effect': 'non-scaling-stroke' });
  }
  add('line', { x1: 0, x2: 1000, y1: 60, y2: 60, class: 'pulse-zero', 'vector-effect': 'non-scaling-stroke' });
  const cursor = add('line', { x1: 0, x2: 0, y1: 0, y2: 120, class: 'pulse-cursor hidden', 'vector-effect': 'non-scaling-stroke' });
  const readout = el('div', { class: 'pulse-readout hidden', 'aria-hidden': 'true' });
  plot.replaceChildren(svg, readout);
  // Lecture au survol : l'heure et les deux débits, sans rien masquer du tracé
  const nearest = (points, t) => points.reduce((best, p) => (Math.abs(p[0] - t) < Math.abs(best[0] - t) ? p : best), points[0]);
  plot.onpointermove = (event) => {
    const box = plot.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    const t = t0 + ratio * 86400;
    const u = up.length ? nearest(up, t) : [t, 0];
    const d = down.length ? nearest(down, t) : [t, 0];
    cursor.setAttribute('x1', ratio * 1000); cursor.setAttribute('x2', ratio * 1000);
    cursor.classList.remove('hidden');
    readout.replaceChildren(el('b', {}, fmtHM(u[0])), el('span', { class: 'up' }, `Envoyé ${fmtRate(u[1])}`), el('span', { class: 'down' }, `Reçu ${fmtRate(d[1])}`));
    readout.classList.remove('hidden');
    readout.classList.toggle('flip', ratio > .6);
    readout.style.left = `${ratio * 100}%`;
  };
  plot.onpointerleave = () => { cursor.classList.add('hidden'); readout.classList.add('hidden'); };
  $('pulse-axis').replaceChildren(...[0, 6, 12, 18].map((h) => el('span', {}, fmtHM(t0 + h * 3600))), el('span', {}, 'maintenant'));
}

async function overviewPulse() {
  const box = $('pulse');
  box.setAttribute('aria-busy', 'true');
  try {
    const data = await api('/api/metrics?range=24h');
    const up = (data.series && data.series.net_tx) || [];
    const down = (data.series && data.series.net_rx) || [];
    if (up.length < 2 && down.length < 2) {
      $('pulse-up').textContent = '—'; $('pulse-down').textContent = '—';
      $('pulse-plot').replaceChildren(stateBlock('empty', 'Pas encore de mesures : le serveur en relève une toutes les quelques minutes.'));
      $('pulse-axis').replaceChildren();
      return;
    }
    const sent = seriesTotal(up);
    const received = seriesTotal(down);
    $('pulse-up').textContent = fmtSize(sent);
    $('pulse-down').textContent = fmtSize(received);
    renderPulse(up, down, data.now || Math.floor(Date.now() / 1000));
  } catch (error) {
    $('pulse-up').textContent = '—'; $('pulse-down').textContent = '—';
    $('pulse-plot').replaceChildren(stateBlock('error', `Mesures indisponibles : ${error.message}`));
    $('pulse-axis').replaceChildren();
  } finally {
    box.removeAttribute('aria-busy');
  }
  // Débits de qBittorrent en ce moment (réservé aux administrateurs : un observateur ne voit que le tracé)
  if (readonlyMode()) return;
  try {
    const live = await api('/api/remote-control');
    const d = live.downloads;
    $('pulse-live').textContent = d && d.available ? `qBittorrent en ce moment : ${fmtRate(d.up)} envoyés, ${fmtRate(d.down)} reçus.` : '';
    $('pulse-live').classList.toggle('hidden', !(d && d.available));
  } catch (error) {
    $('pulse-live').classList.add('hidden');
  }
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
  overviewPulse();
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
