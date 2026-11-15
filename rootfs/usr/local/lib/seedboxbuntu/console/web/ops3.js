'use strict';
// SeedBoxBuntu - console : journal des réglages, qualité des médias, page maison, simulation de restauration, espace par dossier,
// rotation des secrets, liens entre applications, partage d'un titre, bilan mensuel, surveillance du VPN.
// Chargé après ops2.js et avant app.js ; s'appuie sur ui.js (formats, états), ops.js (sparkline) et app.js (api, runJob).

// ---------------------------------------------------------------------------------------------------------------
//  Journal des réglages
// ---------------------------------------------------------------------------------------------------------------
async function loadChanges() {
  const data = await api('/api/changelog');
  $('changes-detail-card').classList.add('hidden');
  $('changes-list').replaceChildren(...(data.events.length ? data.events.map((e) => el('tr', {},
    el('td', {}, fmtDateTime(e.at)), el('td', {}, el('code', {}, e.file)),
    el('td', {}, e.by, e.undo ? pill('retour', 'info') : null),
    el('td', { class: 'muted small' }, e.action || ''),
    el('td', {}, el('div', { class: 'row' },
      el('button', { class: 'btn sm', type: 'button', onclick: () => showChange(e.id, e.file).catch(showError) }, 'Afficher'),
      e.latest ? el('button', { class: 'btn sm', type: 'button', onclick: (event) => confirmAction(event.currentTarget,
        `Le fichier ${e.file} reprend sa version d’avant cette entrée. Un retour est lui-même annulable.`, () => undoChange(e.id)) }, 'Revenir') : null))))
    : [emptyRow(5, `Aucune modification depuis le début du suivi (${data.files} fichier(s) surveillé(s)).`)]));
}
async function showChange(id, file) {
  const data = await api(`/api/changelog/detail?id=${encodeURIComponent(id)}`);
  $('changes-detail-title').textContent = `Détail : ${file}`;
  $('changes-detail').textContent = data.note || (data.lines.length ? data.lines.join('\n') + (data.more ? `\n… et ${data.more} ligne(s) de plus` : '') : 'Aucune ligne modifiée.');
  $('changes-detail-card').classList.remove('hidden');
}
async function undoChange(id) {
  try {
    await api('/api/changelog/undo', { id });
    toast('ok', 'Réglage remis dans son état précédent.');
    loadChanges().catch(showError);
  } catch (error) { showError(error); }
}

// ---------------------------------------------------------------------------------------------------------------
//  Qualité des médias
// ---------------------------------------------------------------------------------------------------------------
const kvRows = (pairs) => pairs.flatMap(([label, value]) => [el('dt', {}, label), el('dd', {}, String(value))]);
const describe = (row) => (row.season !== undefined ? `${row.title} · S${String(row.season).padStart(2, '0')}E${String(row.episode).padStart(2, '0')}` : `${row.title}${row.year ? ` (${row.year})` : ''}`);
async function loadQuality() {
  const data = await api('/api/mediaquality');
  const errors = Object.entries(data.errors || {});
  const state = $('quality-state');
  state.className = `notice ${errors.length ? 'warn' : 'info'}`;
  state.textContent = errors.length ? `Lecture impossible : ${errors.map(([app, text]) => `${app} (${text})`).join(' ; ')}.` : `Relevé du ${fmtDateTime(data.at)}.`;
  const radarr = data.apps.radarr;
  const sonarr = data.apps.sonarr;
  $('quality-radarr-upgrade').disabled = !radarr || !radarr.below_cutoff;
  $('quality-sonarr-upgrade').disabled = !sonarr || !sonarr.below_cutoff;
  $('quality-radarr-kv').replaceChildren(...(radarr ? kvRows([['Films suivis', radarr.total], ['Avec fichier', radarr.with_file], ['Sous le seuil de qualité', radarr.below_cutoff],
    ['Manquants (déjà sortis)', radarr.missing], ['Très compressés', radarr.low_rate], ['Doublons possibles', radarr.duplicates]]) : [el('dt', {}, 'Radarr'), el('dd', {}, 'non joignable')]));
  $('quality-radarr-list').replaceChildren(...(radarr ? [
    ...radarr.below_sample.map((r) => el('tr', {}, el('td', {}, describe(r)), el('td', {}, pill(`sous le seuil${r.quality ? ` : ${r.quality}` : ''}`, 'warn')))),
    ...radarr.low_rate_sample.map((r) => el('tr', {}, el('td', {}, describe(r)), el('td', {}, pill(`très compressé : ${fmtNumber(r.mb_per_min)} Mo par minute`, 'warn')))),
    ...radarr.duplicates_sample.map((r) => el('tr', {}, el('td', {}, describe(r)), el('td', {}, pill('doublon possible', 'info')))),
    ...radarr.missing_sample.map((r) => el('tr', {}, el('td', {}, describe(r)), el('td', {}, pill('manquant', 'unknown'))))] : []));
  if (!$('quality-radarr-list').children.length) $('quality-radarr-list').replaceChildren(emptyRow(2, 'Rien à signaler.'));
  $('quality-sonarr-kv').replaceChildren(...(sonarr ? kvRows([['Séries suivies', sonarr.total], ['Avec au moins un fichier', sonarr.with_file], ['Épisodes sous le seuil', sonarr.below_cutoff],
    ['Épisodes manquants', sonarr.missing]]) : [el('dt', {}, 'Sonarr'), el('dd', {}, 'non joignable')]));
  $('quality-sonarr-list').replaceChildren(...(sonarr ? [
    ...sonarr.below_sample.map((r) => el('tr', {}, el('td', {}, describe(r)), el('td', {}, pill('sous le seuil', 'warn')))),
    ...sonarr.missing_sample.map((r) => el('tr', {}, el('td', {}, describe(r)), el('td', {}, pill('manquant', 'unknown'))))] : []));
  if (!$('quality-sonarr-list').children.length) $('quality-sonarr-list').replaceChildren(emptyRow(2, 'Rien à signaler.'));
}
async function upgradeQuality(app) {
  try {
    const result = await api('/api/mediaquality/upgrade', { app, count: 20 });
    toast('ok', result.searched ? `Recherche lancée pour ${result.searched} titre(s) : ${app === 'radarr' ? 'Radarr' : 'Sonarr'} décide de télécharger ou non.` : 'Aucun titre sous le seuil.');
  } catch (error) { showError(error); }
}

// ---------------------------------------------------------------------------------------------------------------
//  Maison : l'état d'ensemble en grand (mode kiosque)
// ---------------------------------------------------------------------------------------------------------------
const houseTile = (title, kind, value, detail) => el('article', { class: `card tile ${statusKind(kind)}` },
  el('div', { class: 'tile-head' }, el('h2', {}, title), el('span', { class: 'tile-pill' }, pill('', kind))),
  el('div', { class: 'tile-value' }, value), el('p', { class: 'tile-detail' }, detail));
const HOUSE_VERDICT = { ok: ['ok', 'Tout va bien.'], warn: ['warn', 'Quelque chose mérite un coup d’œil.'], bad: ['err', 'Un problème demande votre attention.'] };
async function loadMaison() {
  const refresh = async () => {
    let data;
    try { data = await api('/api/house'); } catch (error) { kioskUpdated(false); throw error; }
    kioskUpdated(true);
    const [kind, text] = HOUSE_VERDICT[data.verdict];
    const verdict = $('maison-verdict');
    verdict.className = `notice ${kind}`;
    verdict.textContent = text;
    const s = data.services;
    const troubled = [...s.down, ...s.unhealthy];
    const d = data.downloads || {};
    const tiles = [
      houseTile('Services', s.down.length ? 'err' : s.unhealthy.length ? 'warn' : 'ok', `${s.running} / ${s.total}`, troubled.length ? `À regarder : ${troubled.join(', ')}` : 'Tous les services répondent.'),
      houseTile('Alertes', data.alerts.some((a) => a.level === 'critical') ? 'err' : data.alerts_total ? 'warn' : 'ok', String(data.alerts_total),
        data.alerts_total ? data.alerts.map((a) => a.title).join(' · ') : 'Aucune alerte en cours.'),
      houseTile('Téléchargements', data.vacation ? 'info' : d.available ? 'ok' : 'unknown', d.available ? fmtRate(d.down) : '—',
        data.vacation ? 'Mode vacances : tout est en pause.' : d.available ? `${d.active} actif(s) · ${d.paused} en pause · envoi ${fmtRate(d.up)}` : 'qBittorrent n’est pas joignable.'),
    ];
    if (data.vpn) tiles.push(houseTile('VPN', data.vpn.leak ? 'err' : ['healthy', 'running'].includes(data.vpn.state) ? 'ok' : 'warn', data.vpn.leak ? 'Fuite' : data.vpn.state === 'healthy' ? 'Protégé' : data.vpn.state,
      data.vpn.leak ? 'Le trafic ne passe pas par le tunnel.' : 'Le tunnel est en marche.'));
    const age = data.backup.age_days;
    tiles.push(houseTile('Sauvegarde', age === null ? 'warn' : age > 14 ? 'warn' : 'ok', age === null ? '—' : age === 0 ? 'Aujourd’hui' : `Il y a ${age} j`,
      data.backup.count ? `${data.backup.count} sauvegarde(s) gardée(s).` : 'Aucune sauvegarde.'));
    if (data.disk) tiles.push(houseTile('Espace', levelOf(data.disk.percent), fmtPercent(data.disk.percent), `${data.disk.label} : ${fmtSize(data.disk.free)} libres.`));
    $('maison-tiles').replaceChildren(...tiles);
  };
  await refresh();
  clearInterval(viewTimer);
  viewTimer = setInterval(() => { refresh().catch(() => {}); }, 30000);
}

// ---------------------------------------------------------------------------------------------------------------
//  Simulation de restauration (page Sauvegardes)
// ---------------------------------------------------------------------------------------------------------------
async function loadRestoredrill() {
  const data = await api('/api/restoredrill');
  const select = $('restoredrill-name');
  const chosen = select.value;
  select.replaceChildren(...(data.backups.length ? data.backups.map((b) => el('option', { value: b.name }, `${b.name} · ${fmtDateTime(b.time)}`))
    : [el('option', { value: '' }, 'Aucune sauvegarde')]));
  if (chosen && data.backups.some((b) => b.name === chosen)) select.value = chosen;
  $('restoredrill-run').disabled = !data.backups.length || !data.services.length || data.free_mb < data.needed_mb;
  const state = $('restoredrill-state');
  const last = data.last;
  state.className = `notice ${!data.services.length || data.free_mb < data.needed_mb ? 'warn' : last ? (last.ok ? 'ok' : 'err') : 'info'}`;
  state.textContent = !data.services.length ? 'Aucune application à essayer (Radarr, Sonarr, Prowlarr ou Lidarr).'
    : data.free_mb < data.needed_mb ? `Mémoire libre insuffisante : ${data.free_mb} Mo disponibles, ${data.needed_mb} Mo nécessaires.`
      : last ? `${last.ok ? 'Dernière simulation réussie' : 'Dernière simulation en échec'} : ${last.name}, le ${fmtDateTime(last.at)} (${fmtDuration(last.seconds)}).`
        : 'Aucune simulation faite pour le moment.';
  $('restoredrill-list').replaceChildren(...(last ? last.items.map((i) => el('tr', {},
    el('td', {}, i.service), el('td', {}, pill('', i.started ? 'ok' : 'err')), el('td', {}, pill('', i.api ? 'ok' : 'err')),
    el('td', {}, i.count === null ? '-' : `${i.count} ${i.label}`), el('td', { class: 'muted small' }, i.detail || '')))
    : [emptyRow(5, 'Lancez une simulation pour voir le résultat ici.')]));
}

// ---------------------------------------------------------------------------------------------------------------
//  Espace par dossier (page Santé des disques)
// ---------------------------------------------------------------------------------------------------------------
async function loadDiskusage() {
  const data = await api('/api/diskusage');
  const state = $('diskusage-state');
  state.className = 'notice info';
  state.textContent = data.running ? 'Mesure en cours…' : data.last ? `Dernière mesure : ${fmtDateTime(data.last)} (une par jour).` : 'Aucune mesure pour le moment : cliquez sur Analyser.';
  $('diskusage-list').replaceChildren(...(data.rows.length ? data.rows.map((r) => el('tr', {},
    el('td', {}, r.label), el('td', {}, fmtSize(r.size)), el('td', {}, fmtPercent(r.share)),
    el('td', {}, r.span_days ? (r.per_day > 0 ? pill(`+${fmtSize(r.per_day)} par jour`, data.top_growers.includes(r.key) ? 'warn' : 'info') : r.per_day < 0 ? `−${fmtSize(-r.per_day)} par jour` : 'stable') : el('span', { class: 'muted small' }, 'pas assez de recul')),
    el('td', {}, sparkline(r.spark, `Tendance : ${r.label}`, ' o'))))
    : [emptyRow(5, 'Aucune mesure pour le moment.')]));
}

// ---------------------------------------------------------------------------------------------------------------
//  Rotation des secrets (page Identifiants)
// ---------------------------------------------------------------------------------------------------------------
async function loadRotation() {
  const data = await api('/api/rotation');
  $('rotation-list').replaceChildren(...data.items.map((i) => el('tr', {},
    el('td', {}, i.label, i.present ? null : pill('absent', 'unknown')),
    el('td', {}, i.last ? `${fmtDateTime(i.last)}${i.by ? ` · ${i.by}` : ''}` : el('span', { class: 'muted small' }, 'jamais depuis le suivi')),
    el('td', {}, i.age_days === null ? '-' : pill(`${i.age_days} j`, i.age_days > data.old_after_days ? 'warn' : 'ok')),
    el('td', {}, el('button', { class: 'btn sm', type: 'button', ...(i.present && i.running ? {} : { disabled: '' }),
      onclick: (event) => confirmAction(event.currentTarget, i.id === 'console'
        ? 'Le secret partagé entre Traefik et la console est remplacé ; les pages ouvertes se rechargent d’un clic.'
        : `${i.label} sera indisponible quelques dizaines de secondes pendant le renouvellement de sa clé. Si elle ne répond pas ensuite, l’ancienne est remise.`,
      () => runJob('/api/rotation/run', { item: i.id }, () => loadRotation().catch(showError))) }, 'Renouveler')))));
}

// ---------------------------------------------------------------------------------------------------------------
//  Liens entre les applications (page Diagnostic)
// ---------------------------------------------------------------------------------------------------------------
const LINK_STATE = { ok: 'ok', warn: 'warn', error: 'err' };
async function runLinkcheck() {
  const button = $('linkcheck-run');
  button.disabled = true;
  $('linkcheck-list').replaceChildren(el('li', { class: 'state-item' }, stateBlock('loading', 'Contrôle des liens en cours (quelques secondes)…')));
  try {
    const data = await api('/api/linkcheck/run', {});
    const summary = $('linkcheck-summary');
    summary.className = `notice ${data.errors ? 'err' : data.problems ? 'warn' : 'ok'}`;
    summary.textContent = data.items.length ? (data.problems ? `${data.problems} point(s) à regarder sur ${data.items.length}.` : 'Tous les liens répondent.') : 'Aucune application à contrôler : les services sont arrêtés.';
    $('linkcheck-list').replaceChildren(...data.items.map((i) => el('li', {}, pill('', LINK_STATE[i.state] || 'unknown'), el('strong', {}, i.title),
      el('span', { class: 'muted' }, ` ${i.detail}`), i.hint ? el('div', { class: 'small' }, `À faire : ${i.hint}`) : null)));
  } catch (error) {
    $('linkcheck-list').replaceChildren();
    showError(error);
  } finally { button.disabled = false; }
}

// ---------------------------------------------------------------------------------------------------------------
//  Partage d'un seul titre et bilan mensuel (page Invités)
// ---------------------------------------------------------------------------------------------------------------
async function loadTitleshare() {
  const data = await api('/api/titleshare');
  $('titleshare-list').replaceChildren(...(data.shares.length ? data.shares.map((s) => el('tr', {},
    el('td', {}, `${s.title}${s.year ? ` (${s.year})` : ''}`, el('div', { class: 'muted small' }, s.kind === 'movie' ? 'Film' : 'Série')),
    el('td', {}, String(s.files)), el('td', {}, pill(fmtDateTime(s.expires), s.active ? 'ok' : 'warn')),
    el('td', {}, el('button', { class: 'btn sm danger', type: 'button', onclick: (event) => confirmAction(event.currentTarget,
      `Le partage de « ${s.title} » est supprimé : le lien, le compte créé et les liens vers les fichiers disparaissent. Le titre reste dans votre bibliothèque.`,
      () => revokeShare(s.id)) }, 'Révoquer'))))
    : [emptyRow(4, 'Aucun partage en cours.')]));
}
async function searchShare() {
  const form = $('titleshare-form');
  try {
    const data = await api(`/api/titleshare/search?kind=${encodeURIComponent(form.elements.kind.value)}&q=${encodeURIComponent(form.elements.q.value)}`);
    $('titleshare-results').replaceChildren(...(data.results.length ? data.results.map((r) => el('option', { value: String(r.id) }, `${r.title}${r.year ? ` (${r.year})` : ''}`))
      : [el('option', { value: '' }, 'Aucun titre avec fichier')]));
    if (!data.results.length) toast('warn', 'Aucun titre avec fichier ne correspond.');
  } catch (error) { showError(error); }
}
async function createShare(event) {
  event.preventDefault();
  const form = event.target;
  if (!form.elements.id.value) { toast('warn', 'Cherchez un titre, puis choisissez-le dans la liste.'); return; }
  try {
    const result = await api('/api/titleshare/create', { kind: form.elements.kind.value, id: Number(form.elements.id.value), days: Number(form.elements.days.value) });
    $('titleshare-link-text').textContent = `${location.origin}${result.path}`;
    $('titleshare-link').classList.remove('hidden');
    toast('ok', `Partage créé : ${result.title}.`);
    loadTitleshare().catch(showError);
  } catch (error) { showError(error); }
}
async function revokeShare(id) {
  try {
    await api('/api/titleshare/revoke', { id });
    toast('ok', 'Partage supprimé.');
    loadTitleshare().catch(showError);
  } catch (error) { showError(error); }
}
async function loadPeoplereport() {
  const data = await api('/api/peoplereport');
  const form = $('peoplereport-card');
  form.elements.enabled.checked = data.settings.enabled;
  form.elements.day.value = String(data.settings.day);
  $('peoplereport-send').disabled = !data.settings.enabled;
  $('peoplereport-list').replaceChildren(...(data.settings.enabled ? (data.people.length ? data.people.map((p) => el('tr', {},
    el('td', {}, p.login), el('td', {}, p.requests === null ? el('span', { class: 'muted small' }, 'pas de compte Seerr') : String(p.requests)),
    el('td', {}, p.requests === null ? '-' : String(p.available)), el('td', {}, p.requests === null ? '-' : String(p.pending))))
    : [emptyRow(4, 'Aucun compte.')]) : [emptyRow(4, 'Le bilan est désactivé : activez-le pour voir les nombres de chacun ici.')]));
}
async function savePeoplereport(event) {
  event.preventDefault();
  const form = event.target;
  try {
    await api('/api/peoplereport/settings', { enabled: form.elements.enabled.checked, day: Number(form.elements.day.value) });
    toast('ok', 'Bilan mensuel enregistré.');
    loadPeoplereport().catch(showError);
  } catch (error) { showError(error); }
}

// ---------------------------------------------------------------------------------------------------------------
//  Surveillance du tunnel (page VPN)
// ---------------------------------------------------------------------------------------------------------------
async function loadVpnwatch() {
  const data = await api('/api/vpnwatch');
  const form = $('vpnwatch-card');
  form.elements.enabled.checked = data.settings.enabled;
  form.elements.auto_stop.checked = data.settings.auto_stop;
  const state = $('vpnwatch-state');
  state.className = `notice ${data.leak ? 'err' : data.settings.enabled && data.mode === 'vpn' ? 'ok' : 'info'}`;
  state.textContent = data.mode !== 'vpn' ? 'qBittorrent n’est pas derrière le VPN : rien à surveiller.'
    : !data.settings.enabled ? 'La surveillance est désactivée.'
      : data.leak ? `Fuite constatée${data.stopped ? ' : les torrents sont en pause' : ''}.`
        : data.checked ? `Tunnel sain au dernier contrôle (${fmtDateTime(data.checked)}).` : 'Premier contrôle dans moins de 5 minutes.';
  $('vpnwatch-list').replaceChildren(...(data.history.length ? data.history.map((h) => el('tr', {}, el('td', {}, fmtDateTime(h.at)), el('td', {}, h.title), el('td', { class: 'muted small' }, h.detail)))
    : [emptyRow(3, 'Aucun événement.')]));
}
async function saveVpnwatch(event) {
  event.preventDefault();
  const form = event.target;
  try {
    await api('/api/vpnwatch/settings', { enabled: form.elements.enabled.checked, auto_stop: form.elements.auto_stop.checked });
    toast('ok', 'Surveillance du tunnel enregistrée.');
    loadVpnwatch().catch(showError);
  } catch (error) { showError(error); }
}

// ---------------------------------------------------------------------------------------------------------------
//  Branchements
// ---------------------------------------------------------------------------------------------------------------
function initOps3() {
  $('changes-refresh').addEventListener('click', () => loadChanges().catch(showError));
  $('quality-refresh').addEventListener('click', () => loadQuality().catch(showError));
  $('quality-radarr-upgrade').addEventListener('click', (event) => confirmAction(event.currentTarget,
    'Radarr cherche une meilleure version pour les 20 premiers films sous le seuil. Il ne télécharge que si un fichier meilleur est trouvé, et ne supprime rien d’autre que ce qu’il remplace.', () => upgradeQuality('radarr')));
  $('quality-sonarr-upgrade').addEventListener('click', (event) => confirmAction(event.currentTarget,
    'Sonarr cherche une meilleure version pour les 20 premiers épisodes sous le seuil. Il ne télécharge que si un fichier meilleur est trouvé.', () => upgradeQuality('sonarr')));
  $('restoredrill-run').addEventListener('click', (event) => confirmAction(event.currentTarget,
    'Des conteneurs jetables, sans réseau, démarrent à partir de la sauvegarde choisie ; vos applications ne sont pas touchées. Compter quelques minutes et 1,5 Go de mémoire.',
    () => runJob('/api/restoredrill/run', { name: $('restoredrill-name').value }, () => loadRestoredrill().catch(showError))));
  $('diskusage-scan').addEventListener('click', () => runJob('/api/diskusage/scan', {}, () => loadDiskusage().catch(showError)));
  $('linkcheck-run').addEventListener('click', () => runLinkcheck());
  $('linkcheck-summary').textContent = 'Cliquez sur Vérifier pour tester les liens entre les applications.';
  $('titleshare-search').addEventListener('click', () => searchShare());
  $('titleshare-form').addEventListener('submit', createShare);
  $('titleshare-link-copy').addEventListener('click', () => {
    navigator.clipboard.writeText($('titleshare-link-text').textContent).then(() => toast('ok', 'Lien copié.'), () => toast('warn', 'Copie impossible : sélectionnez le lien à la main.'));
  });
  $('peoplereport-card').addEventListener('submit', savePeoplereport);
  $('peoplereport-send').addEventListener('click', () => runJob('/api/peoplereport/send', {}, () => {}));
  $('vpnwatch-card').addEventListener('submit', saveVpnwatch);
}
