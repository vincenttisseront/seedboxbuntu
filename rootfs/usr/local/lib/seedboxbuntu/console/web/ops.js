'use strict';
// SeedBoxBuntu - console : exploitation avancée (ressources par conteneur, vérification d'une sauvegarde, réglages de la console,
// fenêtre de maintenance, activité, vulnérabilités, diagnostic réseau, mode vacances, notifications push).
// Chargé après extras.js et avant app.js ; s'appuie sur ui.js (formats, états), features.js (svgEl) et app.js (api, runJob).

const toDay = (index) => DAYS[index][0];
const dateInput = (seconds) => new Date(seconds * 1000).toISOString().slice(0, 10);

// ---------------------------------------------------------------------------------------------------------------
//  Ressources par conteneur
// ---------------------------------------------------------------------------------------------------------------
const DRIFT_LABEL = { memory: 'Mémoire presque saturée', leak: 'Mémoire en forte hausse', cpu: 'Processeur saturé' };
function sparkline(values, label = 'Tendance de la mémoire', unit = ' %') {
  const width = 90; const height = 24;
  const root = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, class: 'spark', role: 'img', 'aria-label': `${label} : ${values.length ? `${values[values.length - 1]}${unit}` : 'aucune mesure'}` });
  if (values.length > 1) {
    const hi = Math.max(...values, 1); const lo = Math.min(...values);
    const span = Math.max(hi - lo, 1);
    const step = width / (values.length - 1);
    const round = (v) => Math.round(v * 10) / 10;
    root.append(svgEl('path', { class: 'line s0', d: values.map((v, i) => `${i ? 'L' : 'M'}${round(i * step)} ${round(height - 2 - ((v - lo) / span) * (height - 4))}`).join(' ') }));
  }
  return root;
}
async function loadResources() {
  const card = $('resources-card');
  if (readonlyMode()) return;
  card.classList.remove('hidden');
  const data = await api('/api/resources');
  fill($('resources-form'), data.settings);
  const rows = data.containers.map((c) => el('tr', {},
    el('td', {}, c.name),
    el('td', {}, fmtPercent(c.cpu), el('span', { class: 'muted small' }, ` (max ${fmtPercent(c.cpu_max)})`)),
    el('td', {}, fmtSize(c.mem), el('span', { class: 'muted small' }, ` (max ${fmtSize(c.mem_max)})`)),
    el('td', {}, sparkline(c.spark)),
    el('td', {}, c.problems.length
      ? el('div', { class: 'row' }, ...c.problems.map((p) => pill(DRIFT_LABEL[p.code] || p.code, 'warn')))
      : pill('', 'ok'))));
  $('resources-list').replaceChildren(...(rows.length ? rows : [emptyRow(5, 'Les mesures des conteneurs apparaissent après quelques relevés (toutes les 5 minutes).')]));
}

// ---------------------------------------------------------------------------------------------------------------
//  Vérification d'une sauvegarde (répétition à blanc)
// ---------------------------------------------------------------------------------------------------------------
async function loadSandbox(backups) {
  const names = backups.filter((b) => !b.name.includes('-avant'));
  $('sandbox-name').replaceChildren(...(names.length ? names.map((b) => el('option', { value: b.name }, `${fmtDateTime(b.time)} · ${b.where === 'local' ? 'ce serveur' : b.where}`))
    : [el('option', { value: '' }, 'Aucune sauvegarde')]));
  $('sandbox-run').disabled = !names.length;
}
const CHANGE_LABEL = { same: 'identiques', changed: 'remplacés', added: 'ajoutés', missing: 'absents de la sauvegarde' };
function renderSandbox(report) {
  const box = $('sandbox-result');
  const rows = [el('div', { class: `notice ${report.ok ? 'ok' : 'err'}` },
    report.ok ? 'Sauvegarde saine : elle peut être restaurée.' : `${report.problems.length} problème(s) : cette sauvegarde ne serait pas restaurée proprement.`)];
  rows.push(el('dl', { class: 'kv' },
    el('dt', {}, 'Contenu'), el('dd', {}, `${report.files} fichier(s), ${report.databases} base(s) de données, ${report.configs} fichier(s) de réglages`),
    el('dt', {}, 'Une restauration'), el('dd', {}, ['changed', 'added', 'same'].filter((k) => report.changes[k] !== undefined).map((k) => `${report.changes[k]} ${CHANGE_LABEL[k]}`).join(' · ')),
    el('dt', {}, 'Durée'), el('dd', {}, `${fmtNumber(report.seconds)} s`)));
  if (report.problems.length) rows.push(el('ul', { class: 'checks' }, ...report.problems.map((p) => el('li', {}, pill('', 'err'), el('code', {}, p.file), el('span', { class: 'muted' }, ` : ${p.problem}`)))));
  if (report.changes.sample && report.changes.sample.length) {
    rows.push(el('p', { class: 'small muted' }, 'Quelques fichiers qu’une restauration remplacerait :'),
      el('ul', { class: 'checks' }, ...report.changes.sample.map((s) => el('li', {}, el('code', {}, s.path), el('span', { class: 'muted' }, s.change === 'changed' ? ' : remplacé' : ' : ajouté')))));
  }
  box.replaceChildren(...rows);
  box.classList.remove('hidden');
}
async function runSandbox() {
  const button = $('sandbox-run');
  button.disabled = true;
  const note = toast('info', 'Vérification de la sauvegarde…', { sticky: true, busy: true });
  try {
    renderSandbox(await api('/api/backup/check', { name: $('sandbox-name').value }));
    note.update('ok', 'Vérification terminée.');
  } catch (error) { note.update('err', error.message); } finally { button.disabled = false; }
}

// ---------------------------------------------------------------------------------------------------------------
//  Réglages de la console : export et import
// ---------------------------------------------------------------------------------------------------------------
let configBundle = null;
async function exportConfig() {
  try {
    const bundle = await api(`/api/config/export?secrets=${$('config-secrets').checked ? 1 : 0}`);
    const link = el('a', { href: URL.createObjectURL(new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' })), download: `seedboxbuntu-reglages-${dateInput(Date.now() / 1000)}.json` });
    document.body.append(link);
    link.click();
    link.remove();
    toast('ok', bundle.secrets ? 'Réglages exportés, secrets inclus : gardez ce fichier à l’abri.' : 'Réglages exportés.');
  } catch (error) { showError(error); }
}
const PLAN_STATE = { modifiée: 'info', inchangée: 'ok', appliquée: 'ok', refusée: 'err', ignorée: 'unknown' };
function renderPlan(rows) {
  const pickable = (row) => row.state === 'modifiée';
  $('config-plan-list').replaceChildren(...rows.map((row) => el('tr', {},
    el('td', {}, pickable(row) ? el('input', { type: 'checkbox', checked: '', 'data-section': row.section, 'aria-label': `Appliquer la section ${row.title}` }) : null),
    el('td', {}, row.title), el('td', {}, pill(row.state, PLAN_STATE[row.state] || 'unknown')), el('td', { class: 'muted small' }, row.detail || ''))));
  $('config-apply').disabled = !rows.some(pickable);
  $('config-plan-box').classList.remove('hidden');
}
async function planConfig() {
  const file = $('config-file').files[0];
  if (!file) { toast('warn', 'Choisissez d’abord un fichier de réglages.'); return; }
  try {
    if (file.size > 60 * 1024) throw new Error('Ce fichier est trop volumineux pour être une configuration de console.');
    configBundle = JSON.parse(await file.text());
    renderPlan((await api('/api/config/import', { bundle: configBundle })).sections);
  } catch (error) { showError(error instanceof SyntaxError ? new Error('Ce fichier n’est pas du JSON.') : error); }
}
async function applyConfig() {
  const only = [...document.querySelectorAll('#config-plan-list input[data-section]:checked')].map((box) => box.dataset.section);
  if (!only.length) { toast('warn', 'Aucune section sélectionnée.'); return; }
  try {
    renderPlan((await api('/api/config/import', { bundle: configBundle, apply: true, only })).sections);
    toast('ok', 'Réglages appliqués.');
  } catch (error) { showError(error); }
}

// ---------------------------------------------------------------------------------------------------------------
//  Fenêtre de maintenance (Paramètres)
// ---------------------------------------------------------------------------------------------------------------
async function loadWindow() {
  const data = await api('/api/window');
  const form = $('window-card');
  fill(form, data.settings);
  $('window-days').replaceChildren(...DAYS.map(([, label], index) => el('label', {},
    el('input', { type: 'checkbox', name: `wd-${index}`, ...(data.settings.days.includes(index) ? { checked: '' } : {}) }), label)));
  const state = $('window-state');
  const pending = data.pending.length ? ` ${data.pending.length} tâche(s) attendent l’ouverture.` : '';
  state.replaceChildren(!data.settings.enabled ? 'Aucune fenêtre : les tâches automatiques démarrent à l’heure prévue.'
    : data.open ? 'La fenêtre est ouverte.' : `La fenêtre est fermée : prochaine ouverture ${fmtDateTime(data.next_open)}.`, pending ? el('span', {}, pending) : null);
}
async function saveWindow(event) {
  event.preventDefault();
  const form = event.target;
  try {
    const days = DAYS.map((_, index) => index).filter((index) => form.elements[`wd-${index}`].checked);
    await api('/api/window/settings', { enabled: form.elements.enabled.checked, start: form.elements.start.value, end: form.elements.end.value, days });
    toast('ok', 'Fenêtre de maintenance enregistrée.');
    loadWindow().catch(showError);
  } catch (error) { showError(error); }
}

// ---------------------------------------------------------------------------------------------------------------
//  Activité par personne
// ---------------------------------------------------------------------------------------------------------------
async function loadActivity() {
  const data = await api('/api/activity');
  const form = $('activity-form');
  fill(form, data.settings);
  $('activity-off').classList.toggle('hidden', data.enabled);
  $('activity-card').classList.toggle('hidden', !data.enabled);
  const quota = (q) => Object.entries(q || {}).map(([kind, v]) => `${kind === 'movie' ? 'films' : 'séries'} ${v.used}/${v.limit}`).join(' · ') || '—';
  const rows = (data.people || []).map((p) => el('tr', {},
    el('td', {}, p.login, p.playing ? pill('en lecture', 'info') : null, p.role === 'admin' ? el('span', { class: 'muted small' }, ' · administrateur') : null),
    el('td', { class: 'muted' }, p.last_login ? fmtDateTime(p.last_login) : 'jamais'),
    el('td', {}, p.last_activity ? fmtDateTime(p.last_activity) : 'jamais',
      p.inactive_days !== null && p.inactive_days >= 30 ? pill(`inactif depuis ${p.inactive_days} jours`, 'warn') : null),
    el('td', {}, p.requests === null ? '—' : String(p.requests)),
    el('td', { class: 'muted small' }, quota(p.quota))));
  $('activity-list').replaceChildren(...(rows.length ? rows : [emptyRow(5, 'Aucun compte.')]));
}

// ---------------------------------------------------------------------------------------------------------------
//  Vulnérabilités
// ---------------------------------------------------------------------------------------------------------------
async function loadVulns() {
  const data = await api('/api/vulns');
  fill($('vulns-form'), data.settings);
  const summary = $('vulns-summary');
  summary.className = `notice ${!data.scanned ? 'info' : data.totals.critical ? '' : 'ok'}`.trim();
  summary.textContent = !data.scanned ? 'Aucune analyse pour le moment : cliquez sur Analyser (la première prend quelques minutes).'
    : `Images analysées le ${fmtDateTime(data.scanned)} : ${data.totals.critical} critique(s) et ${data.totals.high} élevée(s) corrigeable(s) au total.${data.error ? ` ${data.error}` : ''}`;
  const rows = data.images.map((i) => el('tr', {},
    el('td', {}, i.image.split('/').slice(-2).join('/')),
    el('td', { class: 'muted small' }, (i.containers || []).join(', ')),
    i.error ? el('td', { colspan: '4' }, pill('analyse impossible', 'unknown'), el('span', { class: 'muted small' }, ` ${i.error}`))
      : [el('td', {}, i.critical ? pill(String(i.critical), 'err') : '0'), el('td', {}, i.high ? pill(String(i.high), 'warn') : '0'),
        el('td', { class: 'muted' }, String(i.unfixed || 0)),
        el('td', { class: 'small' }, (i.top || []).slice(0, 3).map((v) => `${v.id} (${v.package} ${v.installed} → ${v.fixed})`).join(' · ') || '—')]));
  $('vulns-list').replaceChildren(...(rows.length ? rows : [emptyRow(6, 'Aucune image analysée.')]));
}

// ---------------------------------------------------------------------------------------------------------------
//  Diagnostic réseau
// ---------------------------------------------------------------------------------------------------------------
const NET_STATE = { ok: 'ok', info: 'info', warn: 'warn', error: 'err', unknown: 'unknown' };
async function loadNetcheck() {
  const button = $('netcheck-run');
  button.disabled = true;
  $('netcheck-list').replaceChildren(el('li', { class: 'state-item' }, stateBlock('loading', 'Contrôles en cours (quelques secondes)…')));
  try {
    const data = await api('/api/netcheck');
    const summary = $('netcheck-summary');
    summary.className = `notice ${data.errors ? 'err' : data.problems ? '' : 'ok'}`.trim();
    summary.textContent = data.problems ? `${data.problems} point(s) à regarder sur ${data.items.length}.` : 'Tout est en ordre : aucun problème réseau constaté.';
    $('netcheck-list').replaceChildren(...data.items.map((i) => el('li', {}, pill('', NET_STATE[i.state] || 'unknown'), el('strong', {}, i.title),
      el('span', { class: 'muted' }, ` ${i.detail}`), i.hint ? el('div', { class: 'small' }, `À faire : ${i.hint}`) : null)));
  } finally { button.disabled = false; }
}

// ---------------------------------------------------------------------------------------------------------------
//  Mode vacances
// ---------------------------------------------------------------------------------------------------------------
async function loadVacation() {
  const data = await api('/api/vacation');
  const state = $('vacation-state');
  state.className = `notice ${data.active ? '' : 'info'}`.trim();
  state.textContent = data.active ? `Mode vacances actif jusqu’au ${fmtDay(data.until)} : tout reprend automatiquement ce jour-là.` : 'Le mode vacances est désactivé : tout fonctionne normalement.';
  $('vacation-form').classList.toggle('hidden', data.active);
  $('vacation-active-card').classList.toggle('hidden', !data.active);
  const input = $('vacation-form').elements.until;
  input.min = dateInput(Date.now() / 1000 + 86400);
  input.max = dateInput(Date.now() / 1000 + data.max_days * 86400);
  $('vacation-info').replaceChildren(el('dt', {}, 'Depuis'), el('dd', {}, data.started ? fmtDateTime(data.started) : '—'),
    el('dt', {}, 'Reprise'), el('dd', {}, fmtDay(data.until)), el('dt', {}, 'Demandé par'), el('dd', {}, data.by || '—'),
    el('dt', {}, 'Torrents en pause'), el('dd', {}, data.qbittorrent ? String(data.paused) : 'qBittorrent non utilisé'));
}
async function refreshVacationBanner() {
  try {
    const data = await api('/api/vacation');
    $('vacation-banner').classList.toggle('hidden', !data.active);
    if (data.active) $('vacation-banner-text').textContent = `Mode vacances jusqu’au ${fmtDay(data.until)} : téléchargements en pause, invitations refusées.`;
  } catch (error) { /* la bannière n'est qu'un rappel */ }
}

// ---------------------------------------------------------------------------------------------------------------
//  Langue des messages envoyés (Alertes) et notifications push
// ---------------------------------------------------------------------------------------------------------------
async function loadMessageLanguage() {
  if (readonlyMode()) return;
  $('msglang-select').value = (await api('/api/messages-language')).notify;
}
const keyBytes = (text) => Uint8Array.from(atob(text.replace(/-/g, '+').replace(/_/g, '/')), (c) => c.charCodeAt(0));
const pushSupported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
async function pushRegistration() {
  const registration = await navigator.serviceWorker.getRegistration();
  return registration || navigator.serviceWorker.register('sw.js');
}
async function loadPush() {
  const state = $('push-state');
  const toggle = $('push-toggle');
  const status = await api('/api/push');
  let mine = false;
  if (pushSupported()) {
    try { mine = !!(await (await pushRegistration()).pushManager.getSubscription()); } catch (error) { mine = false; }
  }
  const reason = !pushSupported() ? 'Ce navigateur ne gère pas les notifications push (ou la console n’est pas ouverte en HTTPS).'
    : !status.available ? 'Le serveur ne peut pas envoyer de notifications push (module de chiffrement absent).'
      : Notification.permission === 'denied' ? 'Les notifications sont bloquées pour ce site dans le navigateur : autorisez-les dans ses réglages.' : '';
  state.className = `notice ${reason ? 'warn' : mine ? 'ok' : 'info'}`;
  state.textContent = reason || (mine ? `Cet appareil reçoit les alertes (${status.total} appareil(s) inscrit(s) sur ce serveur).` : `Cet appareil ne reçoit pas les alertes (${status.total} appareil(s) inscrit(s) sur ce serveur).`);
  toggle.disabled = !!reason;
  $('push-test').disabled = !!reason || !mine || readonlyMode();
  setLabel(toggle, mine ? 'Désactiver' : 'Activer');
  toggle.dataset.on = mine ? '1' : '';
  toggle.dataset.key = status.key;
}
async function togglePush() {
  const toggle = $('push-toggle');
  try {
    const registration = await pushRegistration();
    const existing = await registration.pushManager.getSubscription();
    if (existing) {
      await api('/api/push/unsubscribe', { endpoint: existing.endpoint });
      await existing.unsubscribe();
      toast('ok', 'Notifications désactivées sur cet appareil.');
    } else {
      if (await Notification.requestPermission() !== 'granted') throw new Error('Autorisation refusée : les notifications restent désactivées.');
      const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(toggle.dataset.key) });
      await api('/api/push/subscribe', { subscription: subscription.toJSON() });
      toast('ok', 'Notifications activées sur cet appareil.');
    }
  } catch (error) { showError(error); }
  loadPush().catch(showError);
}

// ---------------------------------------------------------------------------------------------------------------
//  Branchements
// ---------------------------------------------------------------------------------------------------------------
function initOps() {
  $('resources-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api('/api/resources/settings', { enabled: form.elements.enabled.checked, memory_percent: form.elements.memory_percent.value,
        leak_mb: form.elements.leak_mb.value, cpu_percent: form.elements.cpu_percent.value });
      toast('ok', 'Seuils enregistrés.');
      loadResources().catch(showError);
    } catch (error) { showError(error); }
  });
  $('sandbox-run').addEventListener('click', runSandbox);
  $('config-export').addEventListener('click', exportConfig);
  $('config-plan').addEventListener('click', planConfig);
  $('config-apply').addEventListener('click', applyConfig);
  $('window-card').addEventListener('submit', saveWindow);
  $('activity-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api('/api/activity/settings', { enabled: form.elements.enabled.checked, show_playing: form.elements.show_playing.checked });
      toast('ok', 'Réglage enregistré.');
      loadActivity().catch(showError);
    } catch (error) { showError(error); }
  });
  $('activity-refresh').addEventListener('click', () => loadActivity().catch(showError));
  $('vulns-scan').addEventListener('click', () => runJob('/api/vulns/scan', {}, loadVulns));
  $('vulns-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try { await api('/api/vulns/settings', { enabled: event.target.elements.enabled.checked }); toast('ok', 'Réglage enregistré.'); } catch (error) { showError(error); }
  });
  $('netcheck-run').addEventListener('click', () => loadNetcheck().catch(showError));
  $('vacation-form').addEventListener('submit', (event) => {
    event.preventDefault();
    runJob('/api/vacation/start', { until: event.target.elements.until.value }, () => { loadVacation().catch(showError); refreshVacationBanner(); });
  });
  $('vacation-stop').addEventListener('click', () => runJob('/api/vacation/stop', {}, () => { loadVacation().catch(showError); refreshVacationBanner(); }));
  $('msglang-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try { await api('/api/messages-language', { notify: event.target.elements.notify.value }); toast('ok', 'Langue des messages enregistrée.'); } catch (error) { showError(error); }
  });
  $('push-toggle').addEventListener('click', togglePush);
  $('push-test').addEventListener('click', () => runJob('/api/push/test', {}));
  refreshVacationBanner();
  setInterval(refreshVacationBanner, 300000);
}
