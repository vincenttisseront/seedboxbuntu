'use strict';
// SeedBoxBuntu - console : historique, copie hors serveur, nettoyage, invités, journal, GPU, import et mise à jour signée.
// Chargé avant app.js : les fonctions ci-dessous s'appuient sur ses outils ($, el, api, runJob, confirmAction, fmtSize…)
// qui n'existent qu'à l'appel ; initFeatures() est lancée par app.js une fois le document prêt.

let featureTimer = null;

const fmtDateTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' }) : '-');
const fmtDay = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString('fr-FR', { dateStyle: 'medium' }) : '-');
const pill = (text, kind) => el('span', { class: `pill ${kind || ''}` }, text);
const emptyRow = (cols, text) => el('tr', {}, el('td', { colspan: String(cols), class: 'muted' }, text));
const labelled = (text, input) => el('label', {}, text, input);

// ---------------------------------------------------------------------------------------------------------------
//  Graphiques (SVG construit à la main : aucune bibliothèque, aucun style en ligne)
// ---------------------------------------------------------------------------------------------------------------
const SVGNS = 'http://www.w3.org/2000/svg';
function svgEl(tag, attrs = {}, ...kids) {
  const node = document.createElementNS(SVGNS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  node.append(...kids);
  return node;
}

function niceMax(value) {
  if (!(value > 0)) return 1;
  const base = 10 ** Math.floor(Math.log10(value));
  for (const factor of [1, 2, 2.5, 5, 10]) if (value <= factor * base) return factor * base;
  return 10 * base;
}

const W = 640; const H = 170; const PAD = { l: 64, r: 10, t: 8, b: 22 };

// series : [{ name, cls, points: [[t, v]] }] ; options : { seconds, now, fmt, max, title }
function lineChart(series, options) {
  const { seconds, now, fmt, title } = options;
  const start = now - seconds;
  const all = series.flatMap((s) => s.points.map((p) => p[1]));
  // zoom : courbe de niveau qui évolue peu (disque) ; l'axe ne part alors pas de zéro, ce que les graduations disent
  let low = 0;
  let top = options.max || niceMax(Math.max(0, ...all) * 1.05);
  if (options.zoom && all.length) {
    const lo = Math.min(...all); const hi = Math.max(...all);
    const span = Math.max(hi - lo, hi * 0.01, 1);
    low = Math.max(0, lo - span * 0.2); top = hi + span * 0.2;
  }
  const x = (t) => PAD.l + ((t - start) / seconds) * (W - PAD.l - PAD.r);
  const y = (v) => PAD.t + (1 - (Math.min(Math.max(v, low), top) - low) / (top - low)) * (H - PAD.t - PAD.b);
  const gap = Math.max(900, seconds / 40);
  const root = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', class: 'plot' });
  const last = series[0] && series[0].points.length ? series[0].points[series[0].points.length - 1][1] : null;
  root.setAttribute('aria-label', `${title}${last !== null ? ` : dernière valeur ${fmt(last)}` : ' : aucune mesure'}`);
  for (const fraction of [0, 0.5, 1]) {
    const value = low + (top - low) * fraction;
    root.append(svgEl('line', { class: 'grid', x1: PAD.l, x2: W - PAD.r, y1: y(value), y2: y(value) }),
      svgEl('text', { class: 'tick', x: PAD.l - 6, y: y(value) + 4, 'text-anchor': 'end' }, value === 0 ? '0' : fmt(value)));
  }
  const dayScale = seconds > 86400 * 1.5;
  for (let i = 0; i <= 4; i += 1) {
    const t = start + (seconds * i) / 4;
    const d = new Date(t * 1000);
    const text = dayScale ? d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' }) : d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    root.append(svgEl('text', { class: 'tick', x: x(t), y: H - 6, 'text-anchor': i === 0 ? 'start' : i === 4 ? 'end' : 'middle' }, text));
  }
  series.forEach((s, index) => {
    let path = '';
    let previous = null;
    for (const [t, v] of s.points) {
      path += `${previous === null || t - previous > gap ? 'M' : 'L'}${x(t).toFixed(1)} ${y(v).toFixed(1)} `;
      previous = t;
    }
    if (path) root.append(svgEl('path', { class: `line s${index}`, d: path.trim() }));
    if (s.points.length === 1) root.append(svgEl('circle', { class: `dot s${index}`, cx: x(s.points[0][0]), cy: y(s.points[0][1]), r: 3 }));
  });
  const cursor = svgEl('line', { class: 'cursor hidden', y1: PAD.t, y2: H - PAD.b });
  const marks = series.map((s, index) => svgEl('circle', { class: `mark s${index} hidden`, r: 4 }));
  const hit = svgEl('rect', { class: 'hit', x: PAD.l, y: PAD.t, width: W - PAD.l - PAD.r, height: H - PAD.t - PAD.b });
  root.append(cursor, ...marks, hit);

  const tip = el('div', { class: 'tooltip hidden', role: 'status' });
  const box = el('div', { class: 'chart-box' }, root, tip);
  const nearest = (s, t) => s.points.reduce((best, p) => (best === null || Math.abs(p[0] - t) < Math.abs(best[0] - t) ? p : best), null);
  const show = (event) => {
    const rect = root.getBoundingClientRect();
    if (!rect.width) return;
    const t = start + (((event.clientX - rect.left) / rect.width) * W - PAD.l) / (W - PAD.l - PAD.r) * seconds;
    const picks = series.map((s) => nearest(s, t));
    const ref = picks.find(Boolean);
    if (!ref) return;
    cursor.setAttribute('x1', x(ref[0])); cursor.setAttribute('x2', x(ref[0])); cursor.classList.remove('hidden');
    picks.forEach((p, i) => {
      marks[i].classList.toggle('hidden', !p);
      if (p) { marks[i].setAttribute('cx', x(p[0])); marks[i].setAttribute('cy', y(p[1])); }
    });
    tip.replaceChildren(el('div', { class: 'muted small' }, fmtDateTime(ref[0])),
      ...series.map((s, i) => (picks[i] ? el('div', {}, el('span', { class: `swatch s${i}` }), `${series.length > 1 ? `${s.name} : ` : ''}${fmt(picks[i][1])}`) : null)));
    tip.classList.remove('hidden');
    const left = ((x(ref[0]) / W) * rect.width);
    tip.style.left = `${Math.min(Math.max(left, 60), rect.width - 60)}px`;
  };
  const hide = () => { cursor.classList.add('hidden'); marks.forEach((m) => m.classList.add('hidden')); tip.classList.add('hidden'); };
  hit.addEventListener('pointermove', show);
  hit.addEventListener('pointerdown', show);
  hit.addEventListener('pointerleave', hide);

  const legend = series.length > 1
    ? el('div', { class: 'legend' }, ...series.map((s, i) => el('span', {}, el('span', { class: `swatch s${i}` }), s.name))) : null;
  return el('figure', { class: 'chart' }, el('figcaption', {}, title, last !== null ? el('b', {}, ` ${fmt(last)}`) : null), legend, box);
}

const pct = (v) => `${Math.round(v)} %`;
const rate = (v) => `${fmtSize(v)}/s`;
let historyRange = '24h';

async function loadHistory() {
  const data = await api(`/api/metrics?range=${historyRange}`);
  const seconds = { '1h': 3600, '6h': 21600, '24h': 86400, '7j': 604800, '30j': 2592000, '1an': 31536000 }[data.range] || 86400;
  $('history-ranges').replaceChildren(...data.ranges.map((r) => el('button', {
    class: `seg${r === data.range ? ' active' : ''}`, type: 'button', 'aria-pressed': String(r === data.range),
    onclick: () => { historyRange = r; loadHistory().catch(showError); },
  }, r)));
  const s = data.series;
  const has = (key) => s[key] && s[key].length;
  $('history-empty').classList.toggle('hidden', Object.values(s).some((p) => p.length > 1));
  const base = { seconds, now: data.now };
  const charts = [];
  if (has('cpu')) charts.push(lineChart([{ name: 'Processeur', points: s.cpu }], { ...base, fmt: pct, max: 100, title: 'Processeur' }));
  if (has('mem')) charts.push(lineChart([{ name: 'Mémoire', points: s.mem }], { ...base, fmt: pct, max: 100, title: 'Mémoire' }));
  if (has('load')) charts.push(lineChart([{ name: 'Charge', points: s.load }], { ...base, fmt: (v) => v.toFixed(2), title: 'Charge moyenne (1 min)' }));
  if (has('net_rx') || has('net_tx')) {
    charts.push(lineChart([{ name: 'Reçu', points: s.net_rx || [] }, { name: 'Émis', points: s.net_tx || [] }], { ...base, fmt: rate, title: 'Réseau' }));
  }
  for (const key of Object.keys(s).filter((k) => k.startsWith('disk:'))) {
    const path = key.slice(5);
    charts.push(lineChart([{ name: 'Utilisé', points: s[key] }], { ...base, fmt: fmtSize, zoom: true, title: `Disque ${path}` }));
  }
  if (has('containers')) charts.push(lineChart([{ name: 'Conteneurs', points: s.containers }], { ...base, fmt: (v) => String(Math.round(v)), zoom: true, title: 'Conteneurs actifs' }));
  $('history-charts').replaceChildren(...charts);
  const rows = data.forecast.map((f) => {
    let status;
    if (!f.reliable) status = pill('prévision indisponible', 'warn');
    else if (f.days === null) status = pill('stable ou en baisse', 'ok');
    else status = pill(`plein dans ${f.days} jour${f.days > 1 ? 's' : ''}${f.full_on ? ` (${f.full_on})` : ''}`, f.days < 14 ? 'err' : f.days < 60 ? 'warn' : 'ok');
    const detail = f.reliable && f.per_day > 0 ? ` +${fmtSize(f.per_day)} par jour` : !f.reliable ? ' historique trop court ou évolution irrégulière' : '';
    return el('li', {}, status, ` ${f.path} : ${fmtSize(f.used)} sur ${fmtSize(f.total)}`, el('span', { class: 'muted' }, detail));
  });
  $('history-forecast').replaceChildren(...(rows.length ? rows : [el('li', { class: 'muted' }, 'Aucune mesure de disque pour le moment.')]));
}

// ---------------------------------------------------------------------------------------------------------------
//  Transcodage matériel
// ---------------------------------------------------------------------------------------------------------------
const GPU_MODES = { auto: 'Automatique', off: 'Désactivé', vaapi: 'VAAPI (Intel, AMD)', qsv: 'Quick Sync (Intel)', nvenc: 'NVENC (NVIDIA)' };
async function loadGpu() {
  const data = await api('/api/gpu');
  $('gpu-mode').replaceChildren(...data.modes.map((m) => el('option', { value: m }, GPU_MODES[m] || m)));
  $('gpu-mode').value = data.settings.mode;
  const devices = data.devices.map((d) => d.name || d.path || String(d)).join(', ');
  $('gpu-state').replaceChildren(
    el('dt', {}, 'État'), el('dd', {}, pill(data.active ? 'actif' : 'inactif', data.active ? 'ok' : 'warn')),
    el('dt', {}, 'Méthode'), el('dd', {}, GPU_MODES[data.effective] || (data.effective === 'none' ? 'aucune' : data.effective)),
    el('dt', {}, 'Détail'), el('dd', {}, data.reason || '-'),
    el('dt', {}, 'Cartes détectées'), el('dd', {}, devices || (data.nvidia && data.nvidia.name) || 'aucune'));
}

// ---------------------------------------------------------------------------------------------------------------
//  Copie hors du serveur
// ---------------------------------------------------------------------------------------------------------------
const OFFSITE_LABEL = {
  host: 'Serveur', port: 'Port (22 par défaut)', user: 'Utilisateur', provider: 'Fournisseur', endpoint: 'Adresse du service (https://…)',
  region: 'Région', bucket: 'Nom du bucket', access_key: 'Clé d’accès', url: 'Adresse (https://…)', vendor: 'Type (nextcloud, owncloud, other)',
  account: 'Identifiant de compte', password: 'Mot de passe', private_key: 'Clé privée SSH (PEM, facultative)', secret_key: 'Clé secrète',
  key: 'Clé d’application', config: 'Configuration rclone (section [offsite])',
};
let offsiteData = null;

function offsiteFields(kind) {
  const spec = offsiteData.types[kind];
  const box = $('offsite-fields');
  if (!spec) { box.replaceChildren(); return; }
  const saved = offsiteData.settings.type === kind ? offsiteData.settings.fields : {};
  const nodes = spec.fields.map((name) => {
    const input = name === 'provider'
      ? el('select', { name: `f.${name}` }, ...offsiteData.providers.map((p) => el('option', { value: p }, p)))
      : el('input', { name: `f.${name}`, autocomplete: 'off', ...(name === 'port' ? { type: 'number', min: '1', max: '65535' } : {}) });
    input.value = saved[name] || (name === 'provider' ? 'Other' : '');
    return labelled(OFFSITE_LABEL[name] || name, input);
  });
  for (const name of spec.secrets) {
    const multiline = name === 'private_key' || name === 'config';
    const input = multiline ? el('textarea', { name: `s.${name}`, rows: '3', spellcheck: 'false', autocomplete: 'off' }) : el('input', { name: `s.${name}`, type: 'password', autocomplete: 'new-password' });
    if (offsiteData.secrets_present[name]) input.setAttribute('placeholder', 'enregistré : laisser vide pour le conserver');
    nodes.push(labelled(OFFSITE_LABEL[name] || name, input));
  }
  box.replaceChildren(...nodes);
}

async function loadOffsite() {
  const data = await api('/api/offsite');
  offsiteData = data;
  const form = $('offsite-form');
  const type = $('offsite-type');
  type.replaceChildren(el('option', { value: '' }, 'Aucune'), ...Object.entries(data.types).map(([id, t]) => el('option', { value: id }, t.label)));
  type.value = data.settings.type;
  form.elements.enabled.checked = data.settings.enabled;
  form.elements.path.value = data.settings.path;
  form.elements.keep.value = data.settings.keep;
  offsiteFields(data.settings.type);
  const push = data.last_push;
  const test = data.last_test;
  $('offsite-state').replaceChildren(
    el('dt', {}, 'Destination'), el('dd', {}, data.configured ? `${data.types[data.settings.type].label}${data.settings.enabled ? '' : ' (envoi désactivé)'}` : 'non configurée'),
    el('dt', {}, 'Dernier envoi'), el('dd', {}, push ? [pill(push.ok ? 'réussi' : 'échec', push.ok ? 'ok' : 'err'), ` ${fmtDateTime(push.at)}${push.ok ? ` : ${push.name} (${fmtSize(push.size)})` : ` : ${push.error}`}`] : 'aucun'),
    el('dt', {}, 'Test de restauration'), el('dd', {}, test ? [pill(test.ok ? 'réussi' : 'échec', test.ok ? 'ok' : 'err'),
      ` ${fmtDateTime(test.at)}${test.ok ? ` : ${test.files} fichiers, ${test.databases} base(s) saine(s) (${test.where})` : ` : ${test.error}`}`] : 'jamais fait (automatique chaque mois)'),
    ...(data.installed ? [] : [el('dt', {}, 'rclone'), el('dd', {}, 'sera installé à l’enregistrement')]));
  for (const id of ['offsite-connection', 'offsite-push', 'offsite-test']) $(id).disabled = !data.configured;
}

function offsiteSubmit(event) {
  event.preventDefault();
  const form = event.target;
  const fields = {}; const secrets = {};
  for (const input of form.elements) {
    if (input.name.startsWith('f.')) fields[input.name.slice(2)] = input.value;
    if (input.name.startsWith('s.')) secrets[input.name.slice(2)] = input.value;
  }
  runJob('/api/offsite/settings', {
    enabled: form.elements.enabled.checked, type: form.elements.type.value, path: form.elements.path.value,
    keep: Number(form.elements.keep.value), fields, secrets,
  }, loadOffsite);
}

// ---------------------------------------------------------------------------------------------------------------
//  Import d'une autre seedbox
// ---------------------------------------------------------------------------------------------------------------
let importData = null;
const IMPORT_TARGET = { radarr: 'movies', sonarr: 'tv', lidarr: 'music', readarr: 'books' };
const IMPORT_NAME = { qbittorrent: 'qBittorrent' };

function importRow(old = '', next = '') {
  const row = el('div', { class: 'map-row' },
    el('input', { 'aria-label': 'Ancien dossier', placeholder: '/home/ancien/media/films', value: old, autocomplete: 'off', spellcheck: 'false' }),
    el('span', { class: 'muted', 'aria-hidden': 'true' }, '→'),
    el('input', { 'aria-label': 'Nouveau dossier', placeholder: '/data/media/movies', value: next, autocomplete: 'off', spellcheck: 'false' }),
    el('button', { class: 'btn sm', type: 'button', 'aria-label': 'Retirer la ligne', onclick: (event) => event.target.closest('.map-row').remove() }, '×'));
  return row;
}

async function loadImport() {
  const data = await api('/api/import');
  importData = data;
  $('import-folder').textContent = data.folder;
  const items = data.items;
  $('import-items').classList.toggle('hidden', !items.length);
  $('import-empty').classList.toggle('hidden', !!items.length || !data.scanned);
  $('import-map-box').classList.toggle('hidden', !items.length);
  $('import-list').replaceChildren(...items.map((i) => {
    const content = i.type === 'qbit' ? `${i.torrents} torrent(s)` : `${i.count} élément(s)${i.folders.length ? `, dossiers : ${i.folders.join(', ')}` : ''}`;
    return el('tr', {},
      el('td', {}, el('input', { type: 'checkbox', 'data-id': i.id, ...(i.problem ? { disabled: '' } : { checked: '' }), 'aria-label': `Importer ${i.app}` })),
      el('td', {}, IMPORT_NAME[i.app] || i.app[0].toUpperCase() + i.app.slice(1), i.problem ? [' ', pill(i.problem, 'warn')] : null),
      el('td', { class: 'small' }, content), el('td', { class: 'muted small' }, `${i.file}${i.date ? ` (${fmtDay(i.date)})` : ''}`));
  }));
  const targets = Object.entries(data.targets || {});
  $('import-targets').textContent = targets.length ? `Dossiers de ce serveur : ${targets.map(([role, path]) => `${role} = ${path}`).join(' · ')}` : '';
  const map = $('import-map');
  if (!map.children.length) {
    const rows = [];
    for (const i of items.filter((x) => x.type === 'arr')) {
      const target = data.targets[IMPORT_TARGET[i.app]];
      for (const folder of i.folders) rows.push(importRow(folder, target || ''));
    }
    map.replaceChildren(...(rows.length ? rows : [importRow()]));
  }
}

function importPayload(dry) {
  const ids = [...document.querySelectorAll('#import-list input[data-id]:checked')].map((b) => b.dataset.id);
  if (!ids.length) throw new Error('Cochez au moins un élément à importer');
  const pairs = [...document.querySelectorAll('#import-map .map-row')].map((row) => [...row.querySelectorAll('input')].map((i) => i.value.trim()))
    .filter(([a, b]) => a || b);
  return { ids, map: pairs, dry_run: dry };
}

// ---------------------------------------------------------------------------------------------------------------
//  Invités
// ---------------------------------------------------------------------------------------------------------------
const PROFILE_FIELDS = [
  ['account_days', 'Durée de vie du compte (jours, 0 : sans échéance)'],
  ['max_sessions', 'Écrans simultanés dans Jellyfin (0 : illimité)'],
  ['bitrate_mbps', 'Débit maximal hors réseau local (Mb/s, 0 : illimité)'],
  ['downloads', 'Autoriser le téléchargement de fichiers depuis Jellyfin'],
  ['seerr_movie_limit', 'Demandes de films dans Seerr (0 : illimité)'],
  ['seerr_movie_days', 'Période des demandes de films (jours)'],
  ['seerr_tv_limit', 'Demandes de séries dans Seerr (0 : illimité)'],
  ['seerr_tv_days', 'Période des demandes de séries (jours)'],
];
let guestsData = null;

function profileInputs(values) {
  return PROFILE_FIELDS.map(([name, text]) => {
    if (name === 'downloads') {
      return el('label', { class: 'check' }, el('input', { name, type: 'checkbox', ...(values[name] ? { checked: '' } : {}) }), text);
    }
    const [min, max] = guestsData.limits[name];
    return labelled(text, el('input', { name, type: 'number', min: String(min), max: String(max), value: String(values[name]) }));
  });
}

function readProfile(container) {
  const out = {};
  for (const [name] of PROFILE_FIELDS) {
    const input = container.querySelector(`[name=${name}]`);
    out[name] = name === 'downloads' ? input.checked : Number(input.value);
  }
  return out;
}

function profileSummary(p) {
  const parts = [];
  if (p.max_sessions) parts.push(`${p.max_sessions} écran(s)`);
  if (p.bitrate_mbps) parts.push(`${p.bitrate_mbps} Mb/s`);
  if (!p.downloads) parts.push('sans téléchargement');
  if (p.seerr_movie_limit) parts.push(`${p.seerr_movie_limit} film(s)/${p.seerr_movie_days} j`);
  if (p.seerr_tv_limit) parts.push(`${p.seerr_tv_limit} série(s)/${p.seerr_tv_days} j`);
  return parts.join(' · ') || 'aucune limite';
}

let editingLogin = '';
function openGuestEdit(account) {
  editingLogin = account.login;
  $('guest-edit-title').textContent = `Limites de ${account.login}`;
  $('guest-edit-fields').replaceChildren(...profileInputs(account.profile),
    labelled('Prolonger : nouvelle échéance dans (jours à partir d’aujourd’hui, 0 : sans échéance ; vide : inchangée)',
      el('input', { name: 'extend', type: 'number', min: '0', max: '365', placeholder: 'inchangée' })));
  $('guest-edit').classList.remove('hidden');
  $('guest-edit').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function loadGuests() {
  const data = await api('/api/guests');
  guestsData = data;
  if (!$('guest-profile').children.length) $('guest-profile').replaceChildren(...profileInputs(data.defaults));
  $('guest-invites').replaceChildren(...(data.invites.length ? data.invites.map((i) => el('tr', {},
    el('td', {}, i.label || el('span', { class: 'muted' }, i.id)),
    el('td', {}, pill(i.status, i.status === 'active' ? 'ok' : 'warn')),
    el('td', { class: 'muted' }, fmtDay(i.expires)),
    el('td', {}, `${i.uses} / ${i.max_uses}`),
    el('td', { class: 'muted small' }, i.accounts.join(', ') || '-'),
    el('td', {}, i.status === 'active' ? el('button', { class: 'btn sm danger', type: 'button',
      onclick: (event) => confirmAction(event.target, 'Confirmer ?', async () => {
        try { await api('/api/guests/revoke', { id: i.id }); loadGuests().catch(showError); } catch (error) { showError(error); }
      }) }, 'Révoquer') : null)))
    : [emptyRow(6, 'Aucune invitation pour le moment.')]));
  $('guest-accounts').replaceChildren(...(data.accounts.length ? data.accounts.map((a) => el('tr', {},
    el('td', {}, a.login),
    el('td', { class: 'muted small' }, a.guest ? `invité${a.label ? ` (${a.label})` : ''}` : 'compte créé à la main'),
    el('td', {}, a.expires ? fmtDay(a.expires) : el('span', { class: 'muted' }, 'sans échéance')),
    el('td', { class: 'muted small' }, profileSummary(a.profile)),
    el('td', {}, el('button', { class: 'btn sm', type: 'button', onclick: () => openGuestEdit(a) }, 'Limites'))))
    : [emptyRow(5, 'Aucun compte « utilisateur ».')]));
  $('guest-history').textContent = data.history.join('\n');
  $('guest-history-card').classList.toggle('hidden', !data.history.length);
}

async function guestCreate(event) {
  event.preventDefault();
  const form = event.target;
  try {
    const created = await api('/api/guests/invite', {
      label: form.elements.label.value, days: Number(form.elements.days.value), uses: Number(form.elements.uses.value),
      profile: readProfile($('guest-profile')),
    });
    const link = `${location.origin}${created.path}`;
    $('guest-link-text').textContent = link;
    $('guest-link-note').textContent = `Valable jusqu’au ${fmtDay(created.expires)}. Seule l’empreinte du lien est conservée : il ne pourra plus être réaffiché.`;
    $('guest-link').classList.remove('hidden');
    form.elements.label.value = '';
    loadGuests().catch(showError);
  } catch (error) { showError(error); }
}

async function copyLink() {
  const text = $('guest-link-text').textContent;
  try {
    await navigator.clipboard.writeText(text);
    $('guest-link-copy').textContent = 'Copié';
  } catch (error) {
    const range = document.createRange();
    range.selectNodeContents($('guest-link-text'));
    const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
    $('guest-link-copy').textContent = 'Sélectionné : copiez avec Ctrl+C';
  }
  setTimeout(() => { $('guest-link-copy').textContent = 'Copier le lien'; }, 3000);
}

function guestEditSubmit(event) {
  event.preventDefault();
  const box = $('guest-edit-fields');
  const extend = box.querySelector('[name=extend]').value;
  const login = editingLogin;
  runJob('/api/guests/profile', { login, profile: readProfile(box) }, () => {
    if (extend !== '') runJob('/api/guests/extend', { login, days: Number(extend) }, loadGuests);
    else loadGuests().catch(showError);
  });
  $('guest-edit').classList.add('hidden');
}

// ---------------------------------------------------------------------------------------------------------------
//  Nettoyage
// ---------------------------------------------------------------------------------------------------------------
function checkedIds(container) { return [...container.querySelectorAll('input[data-id]:checked')].map((b) => b.dataset.id); }

function selectAll(box, table) {
  box.addEventListener('change', () => table.querySelectorAll('input[data-id]').forEach((b) => { b.checked = box.checked; }));
}

async function loadCleanup() {
  const data = await api('/api/cleanup');
  $('cleanup-summary').textContent = data.scanned
    ? `Dernière analyse : ${fmtDateTime(data.scanned)} — ${fmtSize(data.total)} récupérables au total.` : 'Aucune analyse pour le moment : cliquez sur Analyser.';
  const notes = $('cleanup-notes');
  notes.classList.toggle('hidden', !data.notes.length);
  notes.textContent = data.notes.join(' ');
  const cards = data.categories.filter((c) => c.count).map((c) => {
    const all = el('input', { type: 'checkbox', 'aria-label': 'Tout sélectionner' });
    const body = el('tbody', {}, ...c.items.map((i) => el('tr', {},
      el('td', {}, el('input', { type: 'checkbox', 'data-id': i.id, 'aria-label': i.name })),
      el('td', {}, i.name, el('div', { class: 'muted small path' }, i.path)),
      el('td', {}, fmtSize(i.size)), el('td', { class: 'muted small' }, i.reason))));
    const table = el('table', {}, el('thead', {}, el('tr', {}, el('th', { class: 'check-col' }, all), el('th', {}, 'Élément'), el('th', {}, 'Taille'), el('th', {}, 'Pourquoi'))), body);
    selectAll(all, table);
    const verb = c.trashable ? 'Mettre à la corbeille' : 'Supprimer';
    const card = el('div', { class: 'card' },
      el('div', { class: 'title' }, el('h2', {}, `${c.label} · ${c.count} · ${fmtSize(c.size)}`),
        el('button', { class: 'btn sm', type: 'button', onclick: (event) => {
          const ids = checkedIds(card);
          if (!ids.length) { showError(new Error('Cochez au moins un élément')); return; }
          confirmAction(event.target, c.trashable ? 'Confirmer ?' : 'Supprimer définitivement ?', () => runJob('/api/cleanup/trash', { ids }, loadCleanup));
        } }, verb)),
      el('p', { class: 'muted small' }, c.hint + (c.count > c.items.length ? ` (les ${c.items.length} plus gros sont listés)` : '')),
      el('div', { class: 'table-wrap' }, table));
    return card;
  });
  $('cleanup-categories').replaceChildren(...(cards.length ? cards : [el('div', { class: 'card muted' }, data.scanned ? 'Rien à nettoyer : le stockage est propre.' : '')]));
  const trash = data.trash;
  $('cleanup-trash-summary').textContent = trash.count ? `${trash.count} élément(s), ${fmtSize(trash.size)} — vidée automatiquement après ${trash.days} jours.` : 'La corbeille est vide.';
  $('cleanup-trash').replaceChildren(...trash.items.map((r) => el('tr', {},
    el('td', {}, el('input', { type: 'checkbox', 'data-id': r.id, 'aria-label': r.name })),
    el('td', {}, r.name, el('div', { class: 'muted small path' }, r.original)), el('td', {}, fmtSize(r.size)), el('td', { class: 'muted small' }, fmtDateTime(r.at)))));
  $('cleanup-restore').disabled = !trash.count;
  $('cleanup-empty').disabled = !trash.count;
}

// ---------------------------------------------------------------------------------------------------------------
//  Journal : actions et recherche dans les journaux
// ---------------------------------------------------------------------------------------------------------------
const OUTCOME = { ok: ['réussi', 'ok'], 'refusé': ['refusé', 'warn'], erreur: ['erreur', 'err'] };
let journalOffset = 0;
const JOURNAL_PAGE = 100;

function journalQuery() {
  const form = $('journal-form');
  const q = new URLSearchParams();
  for (const name of ['q', 'user', 'source', 'outcome']) if (form.elements[name].value) q.set(name, form.elements[name].value);
  return q;
}

async function loadJournal(more = false) {
  journalOffset = more ? journalOffset : 0;
  const q = journalQuery();
  q.set('limit', String(JOURNAL_PAGE)); q.set('offset', String(journalOffset));
  const data = await api(`/api/audit?${q}`);
  const form = $('journal-form');
  for (const [name, values] of [['user', data.users], ['source', data.sources]]) {
    const select = form.elements[name]; const current = select.value;
    select.replaceChildren(el('option', { value: '' }, name === 'user' ? 'Tous' : 'Toutes'), ...values.map((v) => el('option', { value: v }, v)));
    select.value = current;
  }
  const rows = data.events.map((e) => el('tr', {},
    el('td', { class: 'muted small nowrap' }, new Date(e.ts * 1000).toLocaleString('fr-FR')),
    el('td', { class: 'small' }, e.source), el('td', {}, e.user), el('td', { class: 'muted small' }, e.ip || '-'),
    el('td', {}, e.action, e.detail ? el('div', { class: 'muted small' }, e.detail) : null),
    el('td', {}, pill(...(OUTCOME[e.outcome] || [e.outcome, ''])))));
  const body = $('journal-list');
  if (more) body.append(...rows); else body.replaceChildren(...(rows.length ? rows : [emptyRow(6, 'Aucun événement pour ces critères.')]));
  journalOffset += data.events.length;
  $('journal-count').textContent = `${Math.min(journalOffset, data.total)} sur ${data.total} événement(s)`;
  $('journal-more').classList.toggle('hidden', journalOffset >= data.total);
  const exportQuery = journalQuery(); exportQuery.set('format', 'csv');
  $('journal-export').setAttribute('href', `api/audit/export?${exportQuery}`);
  const status = await api('/api/audit/status');
  const chain = status.chain;
  $('journal-chain').replaceChildren(pill(chain.ok ? 'journal intact' : 'journal altéré', chain.ok ? 'ok' : 'err'),
    ` ${chain.events} événement(s), empreintes chaînées${chain.ok ? '' : ` : première anomalie à l’événement ${chain.first_bad}`}.`);
}

function logsQuery() {
  const form = $('logs-form');
  const q = new URLSearchParams({ since: form.elements.since.value });
  if (form.elements.q.value) q.set('q', form.elements.q.value);
  if (form.elements.source.value) q.set('sources', form.elements.source.value);
  if (form.elements.regex.checked) q.set('regex', '1');
  return q;
}

async function searchLogs() {
  $('logs-summary').textContent = 'Recherche en cours…';
  const q = logsQuery(); q.set('limit', '500');
  const data = await api(`/api/logs/search?${q}`);
  $('logs-summary').textContent = `${data.total} ligne(s) dans ${data.sources} source(s)${data.truncated ? ' — les 500 plus récentes sont affichées' : ''}.`;
  $('logs-view').textContent = data.lines.map((l) => `${new Date(l.ts * 1000).toLocaleTimeString('fr-FR')} [${l.source}] ${l.line}`).join('\n') || '(aucune ligne)';
  $('logs-export').setAttribute('href', `api/logs/export?${logsQuery()}`);
}

async function loadLogSources() {
  const sources = await api('/api/system/sources');
  const select = $('logs-source'); const current = select.value;
  select.replaceChildren(el('option', { value: '' }, 'Toutes'),
    ...sources.containers.map((n) => el('option', { value: `c:${n}` }, `conteneur ${n}`)), ...sources.host.map((n) => el('option', { value: `h:${n}` }, `service ${n}`)));
  select.value = current;
}

function showJournalTab(name) {
  for (const tab of document.querySelectorAll('#view-journal .tab')) {
    tab.classList.toggle('active', tab.dataset.tab === name);
    tab.setAttribute('aria-selected', String(tab.dataset.tab === name));
  }
  $('tab-actions').classList.toggle('hidden', name !== 'actions');
  $('tab-logs').classList.toggle('hidden', name !== 'logs');
  if (name === 'logs') loadLogSources().catch(showError);
}

// ---------------------------------------------------------------------------------------------------------------
//  Mise à jour de l'appliance : canal, signature, planification, garde-fou
// ---------------------------------------------------------------------------------------------------------------
const TRUST_LABEL = { signed: 'version signée (manifeste vérifié)', commit: 'commit signé, vérifié par GitHub', none: 'non signée' };

function renderApplianceExtras(data) {
  const { config, check, installed, blocked } = data;
  const focus = document.activeElement;
  const setValue = (id, value, prop = 'value') => { if (focus !== $(id)) $(id)[prop] = value; };
  setValue('appliance-channel', config.channel);
  setValue('appliance-guard', config.guard, 'checked');
  setValue('appliance-signed', config.require_signed, 'checked');
  setValue('appliance-auto', config.auto.enabled, 'checked');
  setValue('appliance-auto-time', config.auto.time);
  if (!$('appliance-auto-days').contains(focus)) renderDays($('appliance-auto-days'), config.auto.days);
  $('appliance-auto-next').textContent = config.auto.enabled ? `Chaque ${config.auto.days === 'daily' ? 'jour' : 'jour choisi'} à ${config.auto.time}` : 'Mise à jour automatique désactivée';
  $('appliance-signer-state').textContent = data.signers.length
    ? `Clé de confiance : ${data.signers.join(', ')}` : 'Aucune clé enregistrée : les versions signées ne peuvent pas être vérifiées.';
  const rows = [el('dt', {}, 'Canal'), el('dd', {}, config.channel === 'stable' ? 'stable (versions publiées)' : 'développement (dernier commit)')];
  if (installed.trust) rows.push(el('dt', {}, 'Authenticité'), el('dd', {}, TRUST_LABEL[installed.trust] || installed.trust));
  if (installed.tag) rows.push(el('dt', {}, 'Étiquette installée'), el('dd', {}, installed.tag));
  $('appliance-state').append(...rows);
  if (check && check.remote && check.remote.tag && check.remote.notes) {
    $('appliance-changes').classList.remove('hidden');
    $('appliance-changes-list').prepend(el('li', {}, el('b', {}, check.remote.tag), ' — ', check.remote.notes.split('\n').find((l) => l.trim()) || ''));
  }
  const box = $('appliance-blocked');
  box.classList.toggle('hidden', !blocked);
  if (blocked) {
    $('appliance-blocked-text').textContent = `La version ${blocked.tag || blocked.short} a été annulée automatiquement (${blocked.reason}) et n’est plus proposée. `;
  }
}

function applianceExtraSave() {
  const days = readDays($('appliance-auto-days'));
  applianceAction('/api/appliance/source', {
    repo: '', ref: '', channel: $('appliance-channel').value, guard: $('appliance-guard').checked, require_signed: $('appliance-signed').checked,
    auto: { enabled: $('appliance-auto').checked, days, time: $('appliance-auto-time').value },
  });
}

// ---------------------------------------------------------------------------------------------------------------
//  Branchements
// ---------------------------------------------------------------------------------------------------------------
function initFeatures() {
  $('guest-form').addEventListener('submit', guestCreate);
  $('guest-link-copy').addEventListener('click', copyLink);
  $('guest-edit').addEventListener('submit', guestEditSubmit);
  $('guest-edit-cancel').addEventListener('click', () => $('guest-edit').classList.add('hidden'));

  $('cleanup-scan').addEventListener('click', () => runJob('/api/cleanup/scan', {}, loadCleanup));
  $('cleanup-restore').addEventListener('click', () => {
    const ids = checkedIds($('cleanup-trash'));
    if (!ids.length) { showError(new Error('Cochez au moins un élément')); return; }
    runJob('/api/cleanup/restore', { ids }, loadCleanup);
  });
  $('cleanup-empty').addEventListener('click', (event) => confirmAction(event.target, 'Supprimer définitivement ?', () => runJob('/api/cleanup/empty', {}, loadCleanup)));

  $('journal-form').addEventListener('submit', (event) => { event.preventDefault(); loadJournal().catch(showError); });
  $('journal-more').addEventListener('click', () => loadJournal(true).catch(showError));
  $('logs-form').addEventListener('submit', (event) => { event.preventDefault(); searchLogs().catch(showError); });
  for (const tab of document.querySelectorAll('#view-journal .tab')) tab.addEventListener('click', () => showJournalTab(tab.dataset.tab));

  $('gpu-save').addEventListener('click', () => runJob('/api/gpu/settings', { mode: $('gpu-mode').value }, loadGpu));
  $('gpu-test').addEventListener('click', () => runJob('/api/gpu/test', {}, loadGpu));

  $('offsite-type').addEventListener('change', (event) => offsiteFields(event.target.value));
  $('offsite-form').addEventListener('submit', offsiteSubmit);
  $('offsite-connection').addEventListener('click', () => runJob('/api/offsite/connection', {}, loadOffsite));
  $('offsite-push').addEventListener('click', () => runJob('/api/offsite/push', {}, loadOffsite));
  $('offsite-test').addEventListener('click', () => runJob('/api/offsite/restore-test', {}, loadOffsite));
  $('offsite-clear').addEventListener('click', (event) => confirmAction(event.target, 'Confirmer ?', () => runJob('/api/offsite/clear', {}, loadOffsite)));

  $('import-scan').addEventListener('click', () => runJob('/api/import/scan', {}, loadImport));
  $('import-map-add').addEventListener('click', () => $('import-map').append(importRow()));
  $('import-dry').addEventListener('click', () => { try { runJob('/api/import/run', importPayload(true)); } catch (error) { showError(error); } });
  $('import-run').addEventListener('click', (event) => {
    try {
      const payload = importPayload(false);
      confirmAction(event.target, 'Confirmer l’import ?', () => runJob('/api/import/run', payload, loadImport));
    } catch (error) { showError(error); }
  });
  $('import-clear').addEventListener('click', (event) => confirmAction(event.target, 'Vider le dossier ?', () => runJob('/api/import/clear', {}, loadImport)));

  $('appliance-extra-save').addEventListener('click', () => { try { applianceExtraSave(); } catch (error) { showError(error); } });
  $('appliance-signer-save').addEventListener('click', () => applianceAction('/api/appliance/signer', { key: $('appliance-signer').value }));
  $('appliance-force').addEventListener('click', (event) => confirmAction(event.target, 'Confirmer ?', () => applianceAction('/api/appliance/apply', { force: true })));
}
