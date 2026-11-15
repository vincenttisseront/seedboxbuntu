'use strict';
// SeedBoxBuntu - console : santé des disques (SMART), débit par plages, objectifs de ratio, mise à jour par lots, comparaison de
// sauvegardes, dépannage guidé, accès de secours, liste des médias, télécommande.
// Chargé après ops.js et avant app.js ; s'appuie sur ui.js (formats, états), ops.js (sparkline) et app.js (api, runJob).

// ---------------------------------------------------------------------------------------------------------------
//  Santé des disques
// ---------------------------------------------------------------------------------------------------------------
const DISK_LEVEL = { ok: ['', 'ok'], warning: ['à surveiller', 'warn'], critical: ['à remplacer', 'err'] };
async function loadDisks() {
  const data = await api('/api/smart');
  const state = $('disks-state');
  const worst = data.disks.some((d) => d.level === 'critical') ? 'critical' : data.disks.some((d) => d.level === 'warning') ? 'warning' : 'ok';
  state.className = `notice ${!data.available ? 'info' : worst === 'ok' ? 'ok' : worst === 'warning' ? 'warn' : 'err'}`;
  state.textContent = !data.available ? (data.reason || 'Aucune lecture pour le moment : cliquez sur Analyser (une lecture a ensuite lieu toutes les heures).')
    : worst === 'ok' ? `Les ${data.disks.length} disque(s) vont bien. Dernière lecture : ${fmtDateTime(data.last)}.`
      : 'Au moins un disque montre des signes de fatigue : lisez le détail ci-dessous et vérifiez que vos sauvegardes sont récentes.';
  $('disks-list').replaceChildren(...(data.disks.length ? data.disks.map((d) => {
    const v = d.values;
    const [label, kind] = DISK_LEVEL[d.level];
    return el('tr', {},
      el('td', {}, d.name, el('div', { class: 'muted small' }, d.model || '')),
      el('td', {}, pill(label, kind)),
      el('td', {}, v.realloc === undefined ? '-' : String(v.realloc), d.realloc_growth_30d ? pill(`+${d.realloc_growth_30d}`, 'warn') : null),
      el('td', {}, v.pending === undefined ? '-' : v.pending ? pill(String(v.pending), 'warn') : '0'),
      el('td', {}, v.temp === undefined ? '-' : `${v.temp} °C`),
      el('td', {}, v.wear === undefined ? '-' : `${v.wear} %`),
      el('td', {}, sparkline(d.series.temp.map((p) => p[1]), 'Tendance de la température', ' °C')));
  }) : [emptyRow(7, 'Aucun disque physique lisible.')]));
  $('disks-notes').replaceChildren(...data.disks.flatMap((d) => d.notes.map((note) => el('li', {},
    pill('', DISK_LEVEL[d.level][1]), el('strong', {}, d.name), el('span', { class: 'muted' }, ` ${note}`)))));
}

// ---------------------------------------------------------------------------------------------------------------
//  Débit par plages horaires
// ---------------------------------------------------------------------------------------------------------------
const rateText = (kib) => (kib ? fmtRate(kib * 1024) : 'illimité');
function slotRow(slot) {
  const days = el('div', { class: 'days' }, ...DAYS.map(([, label], index) => el('label', {},
    el('input', { type: 'checkbox', 'data-day': String(index), ...(slot.days.includes(index) ? { checked: '' } : {}), 'aria-label': label }), label)));
  const field = (label, name, value, type = 'number') => el('label', {}, label, el('input', { type, 'data-field': name, value: String(value), ...(type === 'number' ? { min: '0', max: '10000000' } : { required: '' }) }));
  return el('div', { class: 'card slot' },
    days,
    el('div', { class: 'split' }, field('De', 'start', slot.start, 'time'), field('À', 'end', slot.end, 'time')),
    el('div', { class: 'split' }, field('Débit descendant (ko/s, 0 : illimité)', 'down', slot.down), field('Débit montant (ko/s, 0 : illimité)', 'up', slot.up)),
    el('div', { class: 'row' }, el('button', { class: 'btn sm danger', type: 'button', onclick: (event) => event.currentTarget.closest('.slot').remove() }, 'Retirer')));
}
async function loadBandwidth() {
  const data = await api('/api/bandwidth');
  const form = $('bandwidth-form');
  fill(form, data.settings);
  $('bandwidth-slots').replaceChildren(...data.settings.slots.map(slotRow));
  $('bandwidth-add').disabled = false;
  $('bandwidth-add').dataset.max = String(data.max_slots);
  const state = $('bandwidth-state');
  state.className = `notice ${data.qbittorrent ? 'info' : 'warn'}`;
  state.textContent = !data.qbittorrent ? 'qBittorrent n’est pas installé : les plages n’ont rien à régler.'
    : !data.settings.enabled ? 'Les plages sont désactivées : qBittorrent garde ses propres limites.'
      : `Limites en ce moment : descendant ${rateText(data.now.down)}, montant ${rateText(data.now.up)}${data.now.slot === null ? ' (hors plage)' : ` (plage ${data.now.slot + 1})`}.${data.next ? ` Prochain changement : ${fmtDateTime(data.next.at)}.` : ''}`;
}
function addSlot() {
  const holder = $('bandwidth-slots');
  if (holder.children.length >= Number($('bandwidth-add').dataset.max || 8)) { toast('warn', 'Nombre maximal de plages atteint.'); return; }
  holder.append(slotRow({ days: [0, 1, 2, 3, 4], start: '08:00', end: '23:00', down: 0, up: 2000 }));
}
async function saveBandwidth(event) {
  event.preventDefault();
  const form = event.target;
  const slots = [...$('bandwidth-slots').children].map((row) => ({
    days: [...row.querySelectorAll('[data-day]')].filter((box) => box.checked).map((box) => Number(box.dataset.day)),
    start: row.querySelector('[data-field=start]').value, end: row.querySelector('[data-field=end]').value,
    down: Number(row.querySelector('[data-field=down]').value || 0), up: Number(row.querySelector('[data-field=up]').value || 0) }));
  try {
    await api('/api/bandwidth/settings', { enabled: form.elements.enabled.checked, default_down: Number(form.elements.default_down.value || 0),
      default_up: Number(form.elements.default_up.value || 0), slots });
    toast('ok', 'Plages de débit enregistrées.');
    loadBandwidth().catch(showError);
  } catch (error) { showError(error); }
}

// ---------------------------------------------------------------------------------------------------------------
//  Objectifs de ratio (page Trackers)
// ---------------------------------------------------------------------------------------------------------------
const RATIO_STATE = { below: ['sous l’objectif', 'err'], near: ['proche du minimum', 'warn'], ok: ['', 'ok'], unknown: ['pas assez de données', 'unknown'], none: ['', ''] };
async function loadRatio() {
  const data = await api('/api/ratio');
  fill($('ratio-form'), data.settings);
  $('ratio-list').replaceChildren(...(data.trackers.length ? data.trackers.map((t) => {
    const [label, kind] = RATIO_STATE[t.status];
    const input = el('input', { type: 'number', step: '0.1', min: '0.1', max: '50', 'data-host': t.tracker, value: t.goal === null ? '' : String(t.goal), 'aria-label': `Ratio minimal, ${t.tracker}` });
    return el('tr', {}, el('td', {}, t.tracker), el('td', {}, t.ratio === null ? '-' : String(t.ratio)), el('td', {}, input),
      el('td', {}, kind ? pill(label, kind) : '-', t.status === 'below' ? el('div', { class: 'muted small' }, `Il manque environ ${fmtSize(t.missing)} à envoyer.`) : null),
      el('td', { class: 'muted small' }, `${fmtSize(t.week_up)} envoyés · ${fmtSize(t.week_down)} reçus`));
  }) : [emptyRow(5, 'Aucun tracker connu : les torrents de qBittorrent apparaissent ici.')]));
}
async function saveRatio(event) {
  event.preventDefault();
  const goals = {};
  for (const input of document.querySelectorAll('#ratio-list input[data-host]')) if (input.value !== '') goals[input.dataset.host] = Number(input.value);
  try {
    await api('/api/ratio/settings', { goals, margin: Number(event.target.elements.margin.value), min_gb: Number(event.target.elements.min_gb.value) });
    toast('ok', 'Objectifs de ratio enregistrés.');
    loadRatio().catch(showError);
  } catch (error) { showError(error); }
}

// ---------------------------------------------------------------------------------------------------------------
//  Mise à jour par lots (page Applications)
// ---------------------------------------------------------------------------------------------------------------
const ROLLOUT_STATE = { 'mis à jour': 'ok', 'déjà à jour': 'ok', 'retour arrière': 'warn', 'échec': 'err', 'ignoré': 'unknown' };
async function loadRollout() {
  if (readonlyMode()) return;
  const data = await api('/api/rollout');
  const done = Object.fromEntries(((data.last || {}).items || []).map((i) => [i.service, i]));
  $('rollout-list').replaceChildren(...(data.plan.batch.length ? data.plan.batch.map((row) => el('tr', {},
    el('td', {}, row.service, row.running ? null : pill('arrêté', 'err')),
    el('td', {}, row.healthcheck ? pill('oui', 'ok') : el('span', { class: 'muted small' }, 'non : seulement « en marche »')),
    el('td', {}, done[row.service] ? [pill(done[row.service].result, ROLLOUT_STATE[done[row.service].result] || 'unknown'),
      done[row.service].detail ? el('span', { class: 'muted small' }, ` ${done[row.service].detail}`) : null] : '-')))
    : [emptyRow(3, 'Aucun service dans le lot.')]));
  const apart = data.plan.apart.map((r) => r.service);
  const pinned = data.plan.skipped.map((r) => r.service);
  $('rollout-apart').textContent = `${apart.length ? `À mettre à jour à part, un par un : ${apart.join(', ')} (une coupure efface la session de la console ou le tunnel de qBittorrent).` : ''}${pinned.length ? ` Épinglés, donc laissés tels quels : ${pinned.join(', ')}.` : ''}`.trim();
  $('rollout-run').disabled = !data.plan.batch.length;
}

// ---------------------------------------------------------------------------------------------------------------
//  Comparaison de deux sauvegardes (page Sauvegardes)
// ---------------------------------------------------------------------------------------------------------------
async function loadBackupDiff() {
  if (readonlyMode()) return;
  const data = await api('/api/backupdiff');
  const label = (b) => `${fmtDateTime(b.time)} · ${b.where === 'local' ? 'ce serveur' : b.where}`;
  const choices = () => data.backups.map((b) => el('option', { value: b.name }, label(b)));
  $('backupdiff-old').replaceChildren(...(data.backups.length ? choices() : [el('option', { value: '' }, 'Aucune sauvegarde')]));
  $('backupdiff-new').replaceChildren(el('option', { value: data.current }, 'Ce serveur, maintenant'), ...choices());
  if (data.backups.length > 1) $('backupdiff-old').selectedIndex = 1;
  $('backupdiff-new').selectedIndex = data.backups.length > 1 ? 1 : 0;
  $('backupdiff-run').disabled = !data.backups.length;
}
const DIFF_LABEL = { added: ['ajouté', 'info'], removed: ['retiré', 'warn'], changed: ['modifié', 'info'] };
function renderBackupDiff(result) {
  const s = result.summary;
  const rows = [el('div', { class: 'notice info' }, `${s.changed} fichier(s) de réglages modifié(s) · ${s.added} ajouté(s) · ${s.removed} retiré(s) · ${s.binary} base(s) de données ou fichier(s) binaire(s) modifié(s) · ${s.same} identique(s).`)];
  for (const file of result.files) {
    const [label, kind] = DIFF_LABEL[file.change];
    const head = el('summary', {}, el('code', {}, file.path), ' ', pill(file.kind === 'binaire' && file.change === 'changed' ? 'modifié (binaire)' : label, kind));
    rows.push(file.diff.length ? el('details', {}, head, el('pre', { class: 'log' }, file.diff.join('\n') + (file.more ? `\n… ${file.more} ligne(s) de plus` : '')))
      : el('div', { class: 'row' }, head));
  }
  if (result.omitted) rows.push(el('p', { class: 'muted small' }, `${result.omitted} autre(s) fichier(s) non montré(s).`));
  if (!result.files.length) rows.push(el('p', { class: 'muted' }, 'Aucune différence : ces deux états sont identiques.'));
  $('backupdiff-result').replaceChildren(...rows);
  $('backupdiff-result').classList.remove('hidden');
}
async function runBackupDiff() {
  const button = $('backupdiff-run');
  button.disabled = true;
  const note = toast('info', 'Comparaison en cours…', { sticky: true, busy: true });
  try {
    renderBackupDiff(await api('/api/backupdiff/compare', { older: $('backupdiff-old').value, newer: $('backupdiff-new').value }));
    note.update('ok', 'Comparaison terminée.');
  } catch (error) { note.update('err', error.message); } finally { button.disabled = false; }
}

// ---------------------------------------------------------------------------------------------------------------
//  Dépannage guidé
// ---------------------------------------------------------------------------------------------------------------
const STEP_KIND = { ok: 'ok', info: 'info', warn: 'warn', fail: 'err' };
const ALERT_SERVICE = /^(\S+) (?:est arrêté|en difficulté)$/;
async function loadDiagnose() {
  const [list, alerts] = await Promise.all([api('/api/diagnose'), api('/api/alerts')]);
  $('diagnose-service').replaceChildren(...list.services.map((name) => el('option', { value: name }, name)));
  const sick = [...new Set(alerts.active.map((a) => (ALERT_SERVICE.exec(a.title) || [])[1]).filter((name) => name && list.services.includes(name)))];
  $('diagnose-alerts').replaceChildren(...(sick.length ? sick.map((name) => el('li', {}, pill('', 'err'), el('strong', {}, name), ' ',
    el('button', { class: 'btn sm', type: 'button', onclick: () => { $('diagnose-service').value = name; runDiagnose(name).catch(showError); } }, 'Analyser')))
    : [emptyItem('Aucun service en alerte pour le moment.')]));
  $('diagnose-run').disabled = !list.services.length;
  if (sick.length) { $('diagnose-service').value = sick[0]; runDiagnose(sick[0]).catch(showError); } else $('diagnose-result').classList.add('hidden');
}
async function runDiagnose(service) {
  const button = $('diagnose-run');
  button.disabled = true;
  try {
    const result = await api(`/api/diagnose/run?service=${encodeURIComponent(service)}`);
    $('diagnose-summary').className = `notice ${{ ok: 'ok', info: 'info', warn: 'warn', fail: 'err' }[result.level] || 'info'}`;
    $('diagnose-summary').replaceChildren(el('strong', {}, result.service), el('span', {}, result.summary));
    $('diagnose-steps').replaceChildren(...result.steps.map((s) => el('li', {}, pill('', STEP_KIND[s.level]), el('strong', {}, s.title), el('span', { class: 'muted' }, ` ${s.detail}`))));
    const apiName = service === 'qbittorrent-direct' ? 'qbittorrent' : service;
    const buttons = {
      restart: () => el('button', { class: 'btn sm', type: 'button', onclick: (event) => confirmAction(event.currentTarget, `Le service « ${service} » est redémarré.`,
        async () => { try { await api(`/api/services/${apiName}/restart`, {}); toast('ok', 'Service redémarré.'); runDiagnose(service).catch(showError); } catch (error) { showError(error); } }) }, 'Redémarrer'),
      rollback: () => el('button', { class: 'btn sm danger', type: 'button', onclick: (event) => confirmAction(event.currentTarget, `« ${service} » revient à sa version précédente ; il est ensuite épinglé (plus de mise à jour automatique).`,
        () => runJob('/api/versions/rollback', { service }, () => runDiagnose(service).catch(showError))) }, 'Revenir'),
      cleanup: () => el('a', { class: 'btn sm', href: '#cleanup' }, 'Analyser') };
    $('diagnose-actions').replaceChildren(...(result.actions.length ? result.actions.map((a) => el('li', {}, buttons[a.id](), el('strong', {}, a.label), el('span', { class: 'muted' }, ` ${a.why}`)))
      : [emptyItem('Aucune action à proposer.')]));
    $('diagnose-log').textContent = result.log.join('\n') || 'Aucune ligne de journal.';
    $('diagnose-result').classList.remove('hidden');
  } finally { button.disabled = false; }
}

// ---------------------------------------------------------------------------------------------------------------
//  Accès de secours (page Comptes)
// ---------------------------------------------------------------------------------------------------------------
async function loadEmergency() {
  if (readonlyMode()) return;
  const data = await api('/api/emergency');
  $('emergency-state').replaceChildren(
    el('dt', {}, 'Code de secours'), el('dd', {}, data.configured ? `créé le ${fmtDateTime(data.created)}${data.by ? ` par ${data.by}` : ''}` : 'aucun : créez-en un maintenant'),
    el('dt', {}, 'Dernier usage'), el('dd', {}, data.last_used ? fmtDateTime(data.last_used) : 'jamais'),
    el('dt', {}, 'Verrouillage'), el('dd', {}, data.locked_seconds ? `verrouillé encore ${Math.ceil(data.locked_seconds / 60)} min` : data.fails ? `${data.fails} essai(s) raté(s)` : 'aucun'));
  setLabel($('emergency-init'), data.configured ? 'Renouveler' : 'Créer un code');
  $('emergency-init').dataset.configured = data.configured ? '1' : '';
}
async function createEmergency() {
  try {
    const data = await api('/api/emergency/init', {});
    $('emergency-code-text').textContent = data.code;
    $('emergency-code').classList.remove('hidden');
    toast('ok', 'Code de secours créé : notez-le maintenant.');
    loadEmergency().catch(showError);
  } catch (error) { showError(error); }
}
async function copyEmergency() {
  try { await navigator.clipboard.writeText($('emergency-code-text').textContent); setLabel($('emergency-copy'), 'Copié'); } catch (error) { toast('warn', 'Copie impossible : sélectionnez le code à la main.'); }
  setTimeout(() => setLabel($('emergency-copy'), 'Copier'), 3000);
}

// ---------------------------------------------------------------------------------------------------------------
//  Liste des médias (export du catalogue Radarr et Sonarr)
// ---------------------------------------------------------------------------------------------------------------
let medialistPlanName = '';
async function loadMedialist() {
  const data = await api('/api/catalogexport');
  fill($('medialist-form'), data.settings);
  const state = $('medialist-state');
  const down = Object.entries(data.apps).filter(([, up]) => !up).map(([name]) => name);
  state.className = `notice ${down.length ? 'warn' : 'info'}`;
  state.textContent = down.length ? `${down.join(' et ')} ne tourne${down.length > 1 ? 'nt' : ''} pas : ${down.length > 1 ? 'ils ne peuvent' : 'il ne peut'} ni être exporté${down.length > 1 ? 's' : ''} ni recevoir un import.`
    : data.exports.length ? `Dernier export le ${fmtDateTime(data.exports[0].created)} ; les ${data.retention} derniers sont conservés avec les sauvegardes.` : 'Aucun export pour le moment : créez-en un.';
  $('medialist-list').replaceChildren(...(data.exports.length ? data.exports.map((e) => el('tr', {},
    el('td', {}, fmtDateTime(e.created)), el('td', {}, String(e.movies)), el('td', {}, String(e.series)), el('td', { class: 'muted' }, fmtSize(e.bytes)),
    el('td', {}, el('div', { class: 'row' },
      el('a', { class: 'btn sm', href: `api/catalogexport/download?name=${encodeURIComponent(e.name)}&format=json`, download: e.name }, 'Télécharger'),
      el('a', { class: 'btn sm', href: `api/catalogexport/download?name=${encodeURIComponent(e.name)}&format=csv`, download: e.name.replace('.json', '.csv') }, 'Télécharger en CSV'),
      el('button', { class: 'btn sm', type: 'button', onclick: () => planMedialist(e.name).catch(showError) }, 'Simuler')))))
    : [emptyRow(5, 'Aucun export.')]));
}
async function planMedialist(name) {
  const plan = await api('/api/catalogexport/plan', { name });
  medialistPlanName = plan.name;
  const rows = [];
  for (const [app, info] of Object.entries(plan.apps)) {
    const title = app === 'radarr' ? 'Films (Radarr)' : 'Séries (Sonarr)';
    rows.push(el('h3', {}, title));
    if (!info.ok || info.error) { rows.push(el('div', { class: 'notice warn' }, info.error || `${app} n’est pas joignable.`)); if (!info.ok) continue; }
    rows.push(el('p', {}, `${info.to_add} à ajouter · ${info.already} déjà présent(s) · ${info.total} dans l’export${info.replaced ? ` · ${info.replaced} avec un profil ou un dossier remplacé` : ''}.`));
    if (info.sample.length) rows.push(el('ul', { class: 'checks' }, ...info.sample.map((s) => el('li', {}, `${s.title}${s.year ? ` (${s.year})` : ''}`, ...s.notes.map((note) => pill(note, 'info'))))));
  }
  if (plan.rejected) rows.push(el('p', { class: 'muted small' }, `${plan.rejected} entrée(s) invalide(s) ignorée(s).`));
  $('medialist-plan').replaceChildren(...rows);
  $('medialist-apply').disabled = !Object.values(plan.apps).some((a) => a.to_add);
  $('medialist-plan-card').classList.remove('hidden');
}

// ---------------------------------------------------------------------------------------------------------------
//  Télécommande
// ---------------------------------------------------------------------------------------------------------------
async function loadTelecommande() {
  const refresh = async () => {
    const data = await api('/api/remote-control');
    const alerts = $('remote-alerts');
    alerts.className = `notice ${data.alerts_total ? 'err' : 'ok'}`;
    alerts.textContent = data.alerts_total ? `${data.alerts_total} alerte(s) en cours : ${data.alerts.map((a) => a.title).join(' · ')}` : 'Aucune alerte en cours : tout va bien.';
    const d = data.downloads;
    $('remote-downloads').replaceChildren(...(d.available ? [
      el('dt', {}, 'Téléchargements actifs'), el('dd', {}, String(d.active)), el('dt', {}, 'En pause'), el('dd', {}, String(d.paused)),
      el('dt', {}, 'Débit descendant'), el('dd', {}, fmtRate(d.down)), el('dt', {}, 'Débit montant'), el('dd', {}, fmtRate(d.up)),
      el('dt', {}, 'Mis en pause d’ici'), el('dd', {}, String(data.paused_by_remote))]
      : [el('dt', {}, 'qBittorrent'), el('dd', {}, 'non joignable')]));
    $('remote-pause').disabled = !d.available || data.vacation.active;
    $('remote-resume').disabled = !d.available || data.vacation.active || !data.paused_by_remote;
    $('remote-vacation').textContent = data.vacation.active ? `Mode vacances actif jusqu’au ${fmtDay(data.vacation.until)} : la pause est gérée par ce mode.` : 'Le mode vacances est désactivé.';
  };
  await refresh();
  clearInterval(viewTimer);
  viewTimer = setInterval(() => { refresh().catch(() => {}); }, 15000);
}
async function remoteAct(path, done) {
  try {
    const result = await api(path, {});
    toast('ok', done(result));
    loadTelecommande().catch(showError);
  } catch (error) { showError(error); }
}

// ---------------------------------------------------------------------------------------------------------------
//  Branchements
// ---------------------------------------------------------------------------------------------------------------
function initOps2() {
  $('disks-sample').addEventListener('click', () => runJob('/api/smart/sample', {}, loadDisks));
  $('bandwidth-add').addEventListener('click', addSlot);
  $('bandwidth-form').addEventListener('submit', saveBandwidth);
  $('ratio-form').addEventListener('submit', saveRatio);
  $('rollout-run').addEventListener('click', (event) => confirmAction(event.currentTarget, 'Les services du lot sont mis à jour un par un ; chacun peut être indisponible quelques instants.',
    () => runJob('/api/rollout/run', { keep_going: $('rollout-continue').checked }, loadRollout)));
  $('backupdiff-run').addEventListener('click', runBackupDiff);
  $('diagnose-run').addEventListener('click', () => runDiagnose($('diagnose-service').value).catch(showError));
  $('emergency-init').addEventListener('click', (event) => (event.currentTarget.dataset.configured
    ? confirmAction(event.currentTarget, 'L’ancien code de secours ne fonctionne plus : imprimez le nouveau.', createEmergency) : createEmergency()));
  $('emergency-copy').addEventListener('click', copyEmergency);
  $('medialist-run').addEventListener('click', () => runJob('/api/catalogexport/run', {}, loadMedialist));
  $('medialist-apply').addEventListener('click', (event) => confirmAction(event.currentTarget, 'Les titres absents sont ajoutés à Radarr et Sonarr, sans recherche : rien ne se télécharge.',
    () => runJob('/api/catalogexport/apply', { name: medialistPlanName }, () => { $('medialist-plan-card').classList.add('hidden'); loadMedialist().catch(showError); })));
  $('medialist-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api('/api/catalogexport/settings', { enabled: form.elements.enabled.checked, days: Number(form.elements.days.value), keep: Number(form.elements.keep.value) });
      toast('ok', 'Export planifié enregistré.');
      loadMedialist().catch(showError);
    } catch (error) { showError(error); }
  });
  $('remote-pause').addEventListener('click', () => remoteAct('/api/remote-control/pause', (r) => `${r.paused} torrent(s) mis en pause.`));
  $('remote-resume').addEventListener('click', () => remoteAct('/api/remote-control/resume', (r) => `${r.resumed} torrent(s) repris.`));
}
