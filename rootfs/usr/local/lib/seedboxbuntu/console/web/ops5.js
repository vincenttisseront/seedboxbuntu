'use strict';
// SeedBoxBuntu - console : entretien des médias (prévision, rapport, titres dormants, intégrité, renommage, sous-titres, énergie),
// quotas de lecture des invités, ratios réels des trackers.

const ratioText = (value) => (value === null || value === undefined ? '-' : fmtNumber(value, 2));
const bytesOrDash = (value) => (value === null || value === undefined ? '-' : fmtSize(value));

async function loadCare() {
  await Promise.all([loadSpacefore(), loadLibreport(), loadDormant(), loadIntegrity(), loadRenamer(), loadSubsgap(), loadEnergy()].map((p) => p.catch(showError)));
}

// --- prévision de saturation ---------------------------------------------------------------------------------------
async function loadSpacefore() {
  const data = await api('/api/spacefore');
  const form = $('spacefore-form');
  form.elements.warn_days.value = String(data.settings.warn_days);
  form.elements.alert.checked = data.settings.alert;
  $('spacefore-list').replaceChildren(...(data.volumes.length ? data.volumes.map((v) => el('tr', {},
    el('td', {}, volumeLabel(v.volume)), el('td', {}, fmtSize(v.free)), el('td', {}, fmtSize(v.total)),
    el('td', {}, v.slope_day === null ? '-' : signedSize(v.slope_day)),
    el('td', {}, v.days_left === null ? pill(v.level === 'ok' ? 'stable' : 'pas assez de relevés', v.level === 'ok' ? 'ok' : 'unknown') : pill(`${fmtNumber(v.days_left, 0)} jours`, v.level === 'warn' ? 'warn' : 'ok')),
    el('td', {}, v.date ? fmtDay(v.date) : '-')))
    : [emptyRow(6, 'Aucun relevé : le premier est pris dans la journée.')]));
}
async function saveSpacefore(event) {
  event.preventDefault();
  const form = event.target;
  try {
    await api('/api/spacefore/settings', { warn_days: Number(form.elements.warn_days.value), alert: form.elements.alert.checked });
    toast('ok', 'Réglage enregistré.');
    loadSpacefore().catch(showError);
  } catch (error) { showError(error); }
}

// --- rapport hebdomadaire -------------------------------------------------------------------------------------------
async function loadLibreport() {
  const data = await api('/api/libreport');
  const form = $('libreport-form');
  form.elements.enabled.checked = data.settings.enabled;
  form.elements.day.value = String(data.settings.day);
  form.elements.hour.value = String(data.settings.hour);
  $('libreport-preview').textContent = data.preview;
}
async function saveLibreport(event) {
  event.preventDefault();
  const form = event.target;
  try {
    await api('/api/libreport/settings', { enabled: form.elements.enabled.checked, day: Number(form.elements.day.value), hour: Number(form.elements.hour.value) });
    toast('ok', 'Réglage enregistré.');
    loadLibreport().catch(showError);
  } catch (error) { showError(error); }
}
async function sendLibreport() {
  try {
    await api('/api/libreport/send', {});
    toast('ok', 'Rapport envoyé.');
  } catch (error) { showError(error); }
}

// --- jamais lus et séries abandonnées ------------------------------------------------------------------------------
async function loadDormant() {
  const data = await api('/api/dormant');
  const state = $('dormant-state');
  state.className = `notice ${data.reliable && !data.errors.length ? 'info' : 'warn'}`;
  state.textContent = !data.at ? 'Aucune analyse pour le moment : cliquez sur Analyser.'
    : !data.reliable ? 'Jellyfin ne remonte aucune lecture : la liste des titres jamais lus serait trompeuse, elle est vide.'
      : `Analyse du ${fmtDateTime(data.at)} : ${data.unplayed_total} titre(s) jamais lus (${fmtSize(data.unplayed_size)}), ${data.abandoned_total} série(s) abandonnée(s) (${fmtSize(data.abandoned_size)}).`;
  const form = $('dormant-form');
  form.elements.unplayed_days.value = String(data.settings.unplayed_days);
  form.elements.abandon_days.value = String(data.settings.abandon_days);
  $('dormant-unplayed').replaceChildren(...(data.unplayed.length ? data.unplayed.map((t) => el('tr', {},
    checkCell(`${t.app}:${t.id}`, `Ne plus suivre ${t.title}`), el('td', {}, t.title, el('div', { class: 'muted small' }, t.app === 'radarr' ? 'Film' : 'Série')),
    el('td', {}, fmtSize(t.size)), el('td', {}, t.added || '-'))) : [emptyRow(4, 'Aucun titre jamais lu.')]));
  $('dormant-abandoned').replaceChildren(...(data.abandoned.length ? data.abandoned.map((t) => el('tr', {},
    checkCell(`${t.app}:${t.id}`, `Ne plus suivre ${t.title}`), el('td', {}, t.title, el('div', { class: 'muted small' }, t.ended ? 'terminée' : 'en cours de diffusion')),
    el('td', {}, `${t.have} / ${t.total}`), el('td', {}, fmtSize(t.size)), el('td', {}, t.last_added || '-'))) : [emptyRow(5, 'Aucune série abandonnée.')]));
  $('dormant-unmonitor').disabled = !data.unplayed.length && !data.abandoned.length;
}
async function unmonitorTitles() {
  const chosen = [...picked($('dormant-unplayed')), ...picked($('dormant-abandoned'))].map((k) => k.split(':'));
  if (!chosen.length) { toast('warn', 'Cochez au moins un titre.'); return; }
  try {
    let done = 0;
    for (const app of ['radarr', 'sonarr']) {
      const ids = [...new Set(chosen.filter(([a]) => a === app).map(([, id]) => Number(id)))];
      if (ids.length) done += (await api('/api/dormant/unmonitor', { app, ids })).done;
    }
    toast('ok', `${done} titre(s) retiré(s) du suivi.`);
    loadDormant().catch(showError);
  } catch (error) { showError(error); }
}
async function saveDormant(event) {
  event.preventDefault();
  const form = event.target;
  try {
    await api('/api/dormant/settings', { unplayed_days: Number(form.elements.unplayed_days.value), abandon_days: Number(form.elements.abandon_days.value) });
    toast('ok', 'Réglages enregistrés.');
    loadDormant().catch(showError);
  } catch (error) { showError(error); }
}

// --- intégrité -------------------------------------------------------------------------------------------------------
async function loadIntegrity() {
  const data = await api('/api/integrity');
  const state = $('integrity-state');
  state.className = `notice ${data.problems.length ? 'warn' : 'info'}`;
  state.textContent = !data.settings.enabled && !data.checked ? 'Contrôle désactivé : cochez « Contrôler chaque nuit » ou lancez-le à la main.'
    : `${data.checked} fichier(s) contrôlé(s) sur ${data.total}, ${data.pending} en attente, ${data.problems.length} douteux${data.running ? ' · contrôle en cours' : ''}.`;
  const form = $('integrity-form');
  form.elements.enabled.checked = data.settings.enabled;
  form.elements.per_night.value = String(data.settings.per_night);
  $('integrity-list').replaceChildren(...(data.problems.length ? data.problems.map((p) => el('tr', {},
    el('td', {}, p.title, el('div', { class: 'muted small' }, p.app === 'radarr' ? 'Film' : 'Série')), el('td', {}, pill(p.reason, 'warn')),
    el('td', {}, fmtDateTime(p.at)),
    el('td', {}, el('button', { class: 'btn sm', type: 'button', onclick: () => researchFile(p) }, 'Chercher une autre version')))) : [emptyRow(4, 'Aucun fichier douteux.')]));
}
async function researchFile(p) {
  try {
    await api('/api/integrity/research', { app: p.app, owner: p.id, file: p.file_id });
    toast('ok', 'Recherche lancée dans Radarr ou Sonarr.');
  } catch (error) { showError(error); }
}
async function saveIntegrity(event) {
  event.preventDefault();
  const form = event.target;
  try {
    await api('/api/integrity/settings', { enabled: form.elements.enabled.checked, per_night: Number(form.elements.per_night.value) });
    toast('ok', 'Réglages enregistrés.');
    loadIntegrity().catch(showError);
  } catch (error) { showError(error); }
}

// --- renommage ---------------------------------------------------------------------------------------------------------
async function loadRenamer() {
  const data = await api('/api/renamer');
  const state = $('renamer-state');
  state.className = 'notice info';
  state.textContent = !data.at ? 'Aucune analyse pour le moment : cliquez sur Analyser.'
    : `Analyse du ${fmtDateTime(data.at)} : ${data.titles_total} titre(s) à renommer, ${data.files_total} fichier(s)${data.complete ? '.' : ` ; la bibliothèque est examinée par lots, relancez l’analyse pour la suite (${data.cursor} sur ${data.total}).`}`;
  $('renamer-apply').disabled = !data.titles.length;
  $('renamer-list').replaceChildren(...(data.titles.length ? data.titles.map((t) => el('tr', {},
    checkCell(`${t.app}:${t.id}`, `Renommer ${t.title}`), el('td', {}, t.title, el('div', { class: 'muted small' }, t.app === 'radarr' ? 'Film' : 'Série')),
    el('td', {}, fmtNumber(t.count, 0)), el('td', { class: 'small' }, `${t.example.before} → ${t.example.after}`))) : [emptyRow(4, 'Rien à renommer dans ce qui a été examiné.')]));
}
async function applyRename() {
  const chosen = picked($('renamer-list')).map((k) => k.split(':'));
  if (!chosen.length) { toast('warn', 'Cochez au moins un titre.'); return; }
  try {
    let done = 0;
    for (const app of ['radarr', 'sonarr']) {
      const ids = chosen.filter(([a]) => a === app).map(([, id]) => Number(id));
      if (ids.length) done += (await api('/api/renamer/apply', { app, ids })).done;
    }
    toast('ok', `Renommage lancé pour ${done} titre(s).`);
    loadRenamer().catch(showError);
  } catch (error) { showError(error); }
}

// --- sous-titres -------------------------------------------------------------------------------------------------------
async function loadSubsgap() {
  const data = await api('/api/subsgap');
  $('subsgap-form').elements.langs.value = data.settings.langs.join(' ');
  const state = $('subsgap-state');
  state.className = 'notice info';
  state.textContent = data.analysed ? `${data.total} titre(s) sans sous-titres ni audio dans ${data.settings.langs.join(', ')} (${data.analysed} fichier(s) analysé(s) sur ${data.files}).`
    : 'Aucun fichier analysé : lancez d’abord le relevé des bibliothèques.';
  $('subsgap-list').replaceChildren(...(data.titles.length ? data.titles.map((t) => el('tr', {},
    el('td', {}, t.title, el('div', { class: 'muted small' }, t.app === 'radarr' ? 'Film' : 'Série')), el('td', {}, t.audio.join(' ') || '-'),
    el('td', {}, t.subs.join(' ') || 'aucun'), el('td', {}, fmtSize(t.size)))) : [emptyRow(4, 'Aucun titre à signaler.')]));
}
async function saveSubsgap(event) {
  event.preventDefault();
  try {
    await api('/api/subsgap/settings', { langs: event.target.elements.langs.value.split(/[\s,;]+/).filter(Boolean) });
    toast('ok', 'Réglage enregistré.');
    loadSubsgap().catch(showError);
  } catch (error) { showError(error); }
}

// --- énergie ---------------------------------------------------------------------------------------------------------------
async function loadEnergy() {
  const data = await api('/api/energy');
  const form = $('energy-form');
  for (const key of ['watts_hdd', 'watts_ssd', 'watts_standby', 'price_kwh']) form.elements[key].value = String(data.settings[key]);
  const state = $('energy-state');
  state.className = `notice ${data.hdparm ? 'info' : 'warn'}`;
  state.textContent = data.hdparm ? 'hdparm est installé : la mise en veille est disponible pour les disques à plateaux.' : 'hdparm n’est pas installé : la mise en veille n’est pas disponible (il sera ajouté par la maintenance).';
  $('energy-list').replaceChildren(...(data.volumes.length ? data.volumes.map((v) => {
    const minutes = el('input', { type: 'number', min: '0', max: '330', value: String(v.standby || 0), 'aria-label': `Veille de ${v.device || v.volume} (minutes)` });
    const canStandby = v.device && v.rotational && v.device !== data.system_device && data.hdparm;
    if (!canStandby) minutes.disabled = true;
    return el('tr', {}, el('td', {}, volumeLabel(v.volume)), el('td', {}, v.device ? `${v.device} · ${v.rotational ? 'plateaux' : 'SSD'}` : pill('non mesurable', 'unknown')),
      el('td', {}, `${bytesOrDash(v.free)} / ${bytesOrDash(v.total)}`), el('td', {}, v.idle === null ? '-' : fmtPercent(v.idle * 100)),
      el('td', {}, v.read_share === null ? '-' : fmtPercent(v.read_share * 100)), el('td', {}, v.watts === null ? '-' : `${fmtNumber(v.watts)} W`),
      el('td', {}, v.eur_year === null ? '-' : `${fmtNumber(v.eur_year, 2)} €`), el('td', {}, v.eur_tb_year === null ? '-' : `${fmtNumber(v.eur_tb_year, 2)} €`),
      el('td', {}, minutes, canStandby ? el('button', { class: 'btn sm', type: 'button', onclick: () => setStandby(v.device, minutes) }, 'Régler') : null));
  }) : [emptyRow(9, 'Aucun volume.')]));
}
async function setStandby(device, input) {
  try {
    await api('/api/energy/standby', { disk: device, minutes: Number(input.value) });
    toast('ok', 'Veille réglée.');
    loadEnergy().catch(showError);
  } catch (error) { showError(error); }
}
async function saveEnergy(event) {
  event.preventDefault();
  const form = event.target;
  const body = {};
  for (const key of ['watts_hdd', 'watts_ssd', 'watts_standby', 'price_kwh']) body[key] = Number(form.elements[key].value);
  try {
    await api('/api/energy/settings', body);
    toast('ok', 'Réglages enregistrés.');
    loadEnergy().catch(showError);
  } catch (error) { showError(error); }
}

// --- quotas de lecture (page Invités) ------------------------------------------------------------------------------
async function loadGuestquota() {
  const data = await api('/api/guestquota');
  const form = $('guestquota-card');
  form.elements.default.value = String(data.settings.default);
  form.elements.enforce.checked = data.settings.enforce;
  const state = $('guestquota-state');
  state.className = `notice ${data.available ? 'info' : 'warn'}`;
  state.textContent = data.available ? `${data.users.length} compte(s) non administrateur(s), fenêtre de ${data.days} jours.` : 'Jellyfin est injoignable : aucun compte lu.';
  $('guestquota-list').replaceChildren(...(data.users.length ? data.users.map((u) => {
    const quota = el('input', { type: 'number', min: '0', max: '10000', value: String(u.own_quota ? u.quota : 0), 'aria-label': `Quota de ${u.name}` });
    return el('tr', {}, el('td', {}, u.name), el('td', {}, fmtNumber(u.played, 0)), el('td', {}, quota, el('button', { class: 'btn sm', type: 'button', onclick: () => saveGuestQuota(u.name, quota) }, 'Fixer')),
      el('td', {}, u.suspended ? pill('lecture suspendue', 'err') : u.blocked ? pill('bloqué dans Jellyfin', 'unknown') : u.over ? pill('quota dépassé', 'warn') : pill(u.quota ? 'dans le quota' : 'sans quota', u.quota ? 'ok' : 'unknown')),
      el('td', {}, u.suspended ? el('button', { class: 'btn sm', type: 'button', onclick: () => resumeGuest(u.name) }, 'Rétablir') : null));
  }) : [emptyRow(5, 'Aucun compte invité.')]));
}
async function saveGuestQuota(name, input) {
  try {
    await api('/api/guestquota/quota', { login: name, count: Number(input.value) });
    toast('ok', 'Quota enregistré.');
    loadGuestquota().catch(showError);
  } catch (error) { showError(error); }
}
async function resumeGuest(name) {
  try {
    await api('/api/guestquota/resume', { login: name });
    toast('ok', 'Lecture rétablie.');
    loadGuestquota().catch(showError);
  } catch (error) { showError(error); }
}
async function saveGuestquotaSettings(event) {
  event.preventDefault();
  const form = event.target;
  try {
    await api('/api/guestquota/settings', { default: Number(form.elements.default.value), enforce: form.elements.enforce.checked });
    toast('ok', 'Réglages enregistrés.');
    loadGuestquota().catch(showError);
  } catch (error) { showError(error); }
}

// --- ratios réels des trackers (page Trackers) ----------------------------------------------------------------------
async function loadTrackerstats() {
  const data = await api('/api/trackerstats');
  const state = $('trackerstats-state');
  const session = data.session;
  state.className = `notice ${!data.available ? 'warn' : data.trackers.some((t) => t.danger && t.enabled) ? 'warn' : 'info'}`;
  state.textContent = !data.available ? 'Aucune lecture pour le moment : cliquez sur Actualiser (P2P Aggregator doit être démarré).'
    : `Relevé du ${fmtDateTime(data.at)}${session ? ` · tracker-session : ${session.healthy ? 'prêt' : session.reachable ? 'dégradé' : 'injoignable'}${session.latency_ms ? ` (${session.latency_ms} ms)` : ''}` : ''}.`;
  $('trackerstats-alert').checked = data.settings.alert;
  $('trackerstats-list').replaceChildren(...(data.trackers.length ? data.trackers.map((t) => el('tr', {},
    el('td', {}, t.name, el('div', { class: 'muted small' }, t.host || t.key)), el('td', {}, ratioText(t.ratio)), el('td', {}, ratioText(t.required)),
    el('td', {}, bytesOrDash(t.up)), el('td', {}, bytesOrDash(t.down)),
    el('td', {}, t.trend ? `${signedSize(t.trend.up)} sur ${fmtNumber(t.trend.days, 0)} jours` : '-'),
    el('td', {}, !t.enabled ? pill('désactivé', 'unknown') : t.error ? pill(t.error, 'err') : t.danger ? pill('sous le ratio exigé', 'warn') : t.ratio === null ? pill('non pris en charge', 'unknown') : pill('en règle', 'ok'))))
    : [emptyRow(7, 'Aucun compte tracker lu.')]));
  const aliases = Object.entries(data.settings.aliases);
  $('trackerstats-aliases').textContent = aliases.length ? `Alias : ${aliases.map(([host, name]) => `${host} → ${name}`).join(' ; ')}` : '';
}
async function saveTrackerAlias(event) {
  event.preventDefault();
  const form = event.target;
  try {
    await api('/api/trackerstats/alias', { host: form.elements.host.value.trim().toLowerCase(), account: form.elements.account.value.trim() });
    toast('ok', 'Alias enregistré.');
    form.reset();
    loadTrackerstats().catch(showError);
  } catch (error) { showError(error); }
}

// --- réseau de l'appliance (page Adresse) --------------------------------------------------------------------------------
let networkTimer = null;
function networkAddressLink(pending) {
  const next = pending && pending.new && pending.new.mode === 'static' ? pending.new.address.split('/')[0] : '';
  return next && next !== location.hostname ? `https://${next}/console/#domain` : '';
}
function showNetworkPending(pending) {
  const box = $('network-pending');
  clearInterval(networkTimer);
  box.classList.toggle('hidden', !pending);
  if (!pending) return;
  const link = networkAddressLink(pending);
  const text = $('network-pending-text');
  let left = pending.seconds_left;
  const draw = () => {
    text.replaceChildren(`Changement appliqué, en attente de confirmation : retour automatique dans ${left} s. `,
      ...(link ? ['Ouvrez ', el('a', { href: link }, link), ' (connectez-vous de nouveau), puis cliquez sur « Confirmer ».'] : ['Cliquez sur « Confirmer » pour le conserver.']));
  };
  draw();
  networkTimer = setInterval(() => {
    left -= 1;
    if (left <= 0) { clearInterval(networkTimer); text.textContent = 'Délai écoulé : l’ancienne configuration revient, rechargez cette page dans une minute.'; return; }
    draw();
  }, 1000);
}
async function loadNetwork() {
  const data = await api('/api/network');
  const form = $('network-form');
  const iface = form.elements.iface;
  iface.replaceChildren(...data.interfaces.map((i) => el('option', { value: i.name }, `${i.name} · ${i.state || '?'}${i.speed ? ` · ${i.speed}` : ''}`)));
  const saved = data.configured;
  if (saved && data.interfaces.some((i) => i.name === saved.iface)) iface.value = saved.iface;
  if (saved) {
    form.elements.mode.value = saved.mode;
    form.elements.address.value = saved.address;
    form.elements.gateway.value = saved.gateway;
    form.elements.dns.value = saved.dns.join(' ');
  }
  syncNetworkMode();
  $('network-state').replaceChildren(...kvList([
    ...data.interfaces.map((i) => [i.name, `${i.addresses.map((a) => a.cidr + (a.dynamic ? ' (DHCP)' : '')).join(', ') || 'aucune adresse'} · ${i.mac}`]),
    ['Passerelle', data.route.gateway || '-'], ['DNS', data.dns.join(', ') || '-'], ['Nom de la machine', data.hostname || '-']]));
  showNetworkPending(data.pending);
}
function syncNetworkMode() {
  $('network-static').classList.toggle('hidden', $('network-form').elements.mode.value !== 'static');
}
async function applyNetwork(event) {
  event.preventDefault();
  const form = event.target;
  const body = { iface: form.elements.iface.value, mode: form.elements.mode.value, address: form.elements.address.value.trim(),
    gateway: form.elements.gateway.value.trim(), dns: form.elements.dns.value.split(/[\s,;]+/).filter(Boolean) };
  try {
    const result = await api('/api/network/apply', body);
    toast('warn', 'Réseau en cours de changement : confirmez depuis la nouvelle adresse dans les 2 minutes.');
    showNetworkPending({ ...result.pending, new: { mode: result.mode, address: result.address } });
  } catch (error) { showError(error); }
}
async function keepNetwork() {
  try {
    await api('/api/network/confirm', {});
    toast('ok', 'Changement réseau conservé.');
    loadNetwork().catch(showError);
  } catch (error) { showError(error); }
}
async function revertNetwork() {
  try {
    await api('/api/network/revert', {});
    toast('ok', 'Ancienne configuration rétablie : la connexion peut se couper quelques secondes.');
    loadNetwork().catch(showError);
  } catch (error) { showError(error); }
}
function initNetwork() {
  $('network-form').addEventListener('submit', applyNetwork);
  $('network-form').elements.mode.addEventListener('change', syncNetworkMode);
  $('network-keep').addEventListener('click', keepNetwork);
  $('network-revert').addEventListener('click', (event) => confirmAction(event.currentTarget,
    'La configuration réseau précédente est rétablie et le serveur peut changer d’adresse.', revertNetwork));
}

// --- mots de passe des applications (page Identifiants) ------------------------------------------------------------------
let appPasswords = [];

function syncAppPassAccount() {
  const app = appPasswords.find((a) => a.id === $('apppass-app').value);
  const accounts = app ? app.accounts : [];
  $('apppass-login').replaceChildren(...(accounts.length ? accounts.map((name) => el('option', { value: name }, name))
    : [el('option', { value: '' }, app && app.id === 'jellyfin' ? 'Aucun compte lisible (Jellyfin arrêté ?)' : '-')]));
  $('apppass-form').elements.password.placeholder = app && app.typed ? 'Nouveau mot de passe (8 caractères au minimum)' : 'vide : généré';
  $('apppass-form').elements.password.required = Boolean(app && app.typed);
}

async function loadAppPasswords() {
  const data = await api('/api/apppasswords');
  appPasswords = data.apps;
  const chosen = $('apppass-app').value;
  $('apppass-app').replaceChildren(...appPasswords.map((a) => el('option', { value: a.id }, a.running ? a.name : `${a.name} (arrêté)`)));
  if (chosen) $('apppass-app').value = chosen;
  syncAppPassAccount();
}

function resetAppPassword(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const app = appPasswords.find((a) => a.id === form.elements.app.value);
  if (!app) return;
  const body = { app: app.id, login: app.id === 'jellyfin' ? form.elements.login.value : '', password: form.elements.password.value };
  runJob('/api/apppasswords/reset', body, () => { form.elements.password.value = ''; loadAppPasswords().catch(showError); loadRotation().catch(showError); });
}

// --- agrégateur de releases (page Agrégateur) ----------------------------------------------------------------------------
const AGG_PERIODS = [['24h', 'Nouveautés 24 h'], ['week', 'Cette semaine'], ['all', 'Tout']];
const AGG_SOURCE_STATE = { ok: ['ok', 'actif'], partial: ['warn', 'partiel'], error: ['err', 'en échec'], wait: ['info', 'en attente'], off: ['unknown', 'désactivé'], absent: ['unknown', 'non installé'] };
const AGG_INTERVALS = { 15: '15 minutes', 30: '30 minutes', 60: '1 heure', 120: '2 heures', 180: '3 heures', 240: '4 heures', 360: '6 heures' };
const aggState = { period: '24h', category: '', page: 1, pages: 1, open: new Set() };

const aggPageLink = (v) => (v.details && /^https?:\/\//i.test(v.details)
  ? el('a', { class: 'btn sm', href: v.details, target: '_blank', rel: 'noopener noreferrer', title: 'Page du torrent sur le tracker' }, 'Page ↗') : null);

const aggSendCell = (v) => (v.sent ? pill('envoyé', 'ok')
  : el('button', { class: 'btn sm primary', type: 'button', title: v.torrent_title, onclick: (event) => aggSend(event.currentTarget, v) }, 'Envoyer'));

function aggVariantRow(v) {
  return el('tr', {},
    el('td', { class: 'agg-name' }, el('div', { class: 'no-i18n' }, v.torrent_title), v.label ? el('div', { class: 'muted small' }, v.label) : null),
    el('td', {}, v.tracker), el('td', {}, v.size ? fmtSize(v.size) : '-'),
    el('td', {}, `${fmtNumber(v.seeders, 0)} / ${fmtNumber(v.leechers, 0)}`),
    el('td', {}, fmtDateTime(v.published)), el('td', { class: 'agg-actions' }, aggSendCell(v), aggPageLink(v)));
}

function aggVariantTable(groups) {
  const rows = [];
  for (const group of groups) {
    rows.push(el('tr', { class: 'group-row' }, el('th', { colspan: 6, scope: 'colgroup' }, group.bucket)));
    rows.push(...group.variants.map(aggVariantRow));
  }
  return el('div', { class: 'table-wrap' }, el('table', { class: 'compact' },
    el('thead', {}, el('tr', {}, el('th', {}, 'Torrent'), el('th', {}, 'Tracker'), el('th', {}, 'Taille'), el('th', {}, 'Seeders / leechers'),
      el('th', {}, 'Publié'), el('th', {}, ''))),
    el('tbody', {}, rows)));
}

// Version proposée en un clic : la meilleure qualité, puis le plus de seeders. Pour une série, plusieurs saisons et épisodes
// sont en jeu : on n'envoie pas à la place de l'utilisateur, il choisit dans le détail.
function aggQuickVariant(work) {
  if (work.groups && work.groups.length) return work.groups[0].variants[0];
  if (work.count === 1 && work.seasons && work.seasons.length) return work.seasons[0].groups[0].variants[0];
  return null;
}

function aggWork(work) {
  const many = work.count > 1;
  const quick = aggQuickVariant(work);
  const body = el('div', { class: 'agg-body' },
    ...(work.seasons ? work.seasons.map((season) => el('div', {}, el('h3', { class: 'small' }, season.label), aggVariantTable(season.groups)))
      : [aggVariantTable(work.groups || [])]));
  const toggle = many ? el('button', { class: 'btn sm agg-toggle', type: 'button', 'aria-label': 'Afficher les versions', 'aria-expanded': 'false' }, '▸')
    : el('span', { class: 'agg-toggle' });
  const show = (open) => {
    body.hidden = !open;
    if (many) { toggle.setAttribute('aria-expanded', String(open)); toggle.textContent = open ? '▾' : '▸'; }
    if (open) aggState.open.add(work.id); else aggState.open.delete(work.id);
  };
  const flip = () => show(body.hidden);
  const title = el('span', { class: 'agg-title' }, el('strong', { class: 'no-i18n' }, work.title), work.year ? el('span', { class: 'muted' }, ` (${work.year})`) : null);
  if (many) { toggle.addEventListener('click', flip); title.addEventListener('click', flip); title.classList.add('clickable'); }
  const meta = el('span', { class: 'agg-meta' },
    `${fmtNumber(work.count, 0)} torrent${work.count > 1 ? 's' : ''} · ${work.trackers.join(', ')} · ${work.qualities.join(' ')} · ${fmtNumber(work.seeders, 0)} seeders · ${fmtDateTime(work.latest)}`);
  const row = el('div', { class: 'agg-row' }, toggle, title, pill(work.label, 'info'), meta,
    el('span', { class: 'agg-actions' }, quick ? aggSendCell(quick) : null, quick ? aggPageLink(quick) : null));
  show(many && aggState.open.has(work.id));
  return el('div', { class: 'agg-work' }, row, body);
}

function aggSegments(box, items, current, choose) {
  box.replaceChildren(...items.map(([value, label]) => el('button', {
    class: `seg${value === current ? ' active' : ''}`, type: 'button', 'aria-pressed': value === current ? 'true' : 'false', onclick: () => choose(value),
  }, label)));
}

function aggFill(select, values, current, firstLabel) {
  select.replaceChildren(el('option', { value: '' }, firstLabel), ...values.map((v) => el('option', { value: v }, v)));
  select.value = values.includes(current) ? current : '';
}

async function loadAggCatalogue() {
  const form = $('agg-form').elements;
  const params = new URLSearchParams({ period: aggState.period, category: aggState.category, page: aggState.page, q: form.q.value.trim(), tracker: form.tracker.value,
    quality: form.quality.value, sort: form.sort.value, dir: form.dir.value, per: form.per.value });
  const data = await api(`/api/aggregator/catalogue?${params}`);
  aggState.page = data.page;
  aggState.pages = data.pages;
  aggSegments($('agg-periods'), AGG_PERIODS.map(([id, label]) => [id, `${label} (${fmtNumber(data.periods[id], 0)})`]), data.period,
    (value) => { aggState.period = value; aggState.page = 1; loadAggCatalogue().catch(showError); });
  const total = Object.values(data.counts).reduce((a, b) => a + b, 0);
  const cats = [['', `Toutes (${fmtNumber(total, 0)})`], ...data.categories.map((c) => [c.id, `${c.label} (${fmtNumber(data.counts[c.id] || 0, 0)})`])];
  if (data.counts.unknown) cats.push(['unknown', `Autres (${fmtNumber(data.counts.unknown, 0)})`]);
  aggSegments($('agg-categories'), cats, data.category, (value) => { aggState.category = value; aggState.page = 1; loadAggCatalogue().catch(showError); });
  aggFill(form.tracker, data.trackers, form.tracker.value, 'Tous');
  aggFill(form.quality, data.qualities, form.quality.value, 'Toutes');
  $('agg-summary').textContent = data.q ? `${fmtNumber(data.total, 0)} œuvre(s) pour « ${data.q} » (la recherche ignore la période).` : `${fmtNumber(data.total, 0)} œuvre(s).`;
  $('agg-list').replaceChildren(...(data.works.length ? data.works.map(aggWork)
    : [el('p', { class: 'muted' }, 'Rien à afficher : lancez une collecte, ou élargissez la période et les filtres.')]));
  $('agg-page').textContent = `Page ${data.page} / ${data.pages}`;
  $('agg-prev').disabled = data.page <= 1;
  $('agg-next').disabled = data.page >= data.pages;
}

async function aggSend(button, variant) {
  button.disabled = true;
  try {
    const result = await api('/api/aggregator/send', { id: variant.id });
    toast(result.state === 'envoye' ? 'ok' : 'info', `${result.title} : ${result.message}.`);
    await loadAggCatalogue();
  } catch (error) { button.disabled = false; showError(error); }
}

function aggSourceRow(source) {
  const [kind, label] = AGG_SOURCE_STATE[source.state] || AGG_SOURCE_STATE.wait;
  const act = (text, run, danger) => el('button', { class: `btn sm${danger ? ' danger' : ''}`, type: 'button', onclick: run }, text);
  const buttons = [];
  if (source.present) buttons.push(act('Tester', () => aggTest(source)));
  if (source.present && source.enabled) buttons.push(act('Collecter', () => runJob('/api/aggregator/sync', { source: source.id }, () => loadAggregator().catch(showError))));
  if (source.present) buttons.push(act(source.enabled ? 'Désactiver' : 'Activer', () => aggToggle(source)));
  if (!source.auto) buttons.push(act('Retirer', (event) => confirmAction(event.currentTarget,
    `La source « ${source.name} » et ses torrents sont retirés du catalogue.`, () => aggRemove(source)), true));
  return el('tr', {},
    el('td', {}, source.name, source.url ? el('div', { class: 'muted small no-i18n' }, source.url) : null),
    el('td', {}, pill(label, kind), source.error ? el('div', { class: 'muted small no-i18n' }, source.error) : null,
      source.fails >= 3 ? el('div', { class: 'small' }, `${source.fails} collectes en échec de suite`) : null),
    el('td', {}, source.last ? fmtDateTime(source.last) : '-'), el('td', {}, fmtNumber(source.collected, 0)), el('td', {}, fmtNumber(source.new, 0)),
    el('td', {}, fmtNumber(source.torrents, 0)), el('td', {}, el('div', { class: 'row' }, ...buttons)));
}

async function aggTest(source) {
  try {
    const result = await api('/api/aggregator/source/test', { id: source.id });
    toast(result.count ? 'ok' : 'warn', result.count ? `${source.name} répond : ${result.count} résultat(s) (${result.trackers.join(', ') || 'tracker inconnu'}).`
      : `${source.name} répond mais sans résultat (films, derniers jours).`);
  } catch (error) { showError(error); }
}

async function aggToggle(source) {
  try { await api('/api/aggregator/source/toggle', { id: source.id, enabled: !source.enabled }); await loadAggregator(); } catch (error) { showError(error); }
}

async function aggRemove(source) {
  try { await api('/api/aggregator/source/remove', { id: source.id }); toast('ok', 'Source retirée.'); await loadAggregator(); } catch (error) { showError(error); }
}

async function loadAggregator() {
  const data = await api('/api/aggregator');
  const state = $('agg-state');
  const sources = data.sources.filter((s) => s.enabled);
  state.className = `notice ${data.problems.length ? 'warn' : 'info'}`;
  const headline = data.running ? 'Collecte en cours…'
    : !data.enabled ? 'Collecte automatique désactivée : utilisez « Collecter maintenant » ou réactivez-la dans Collecte.'
    : !sources.length ? 'Aucune source active : Prowlarr et Jackett sont lus s’ils tournent, ou ajoutez une source Torznab ci-dessous.'
    : !data.last_sync ? 'Aucune collecte pour le moment : cliquez sur « Collecter maintenant ».'
    : `Dernière collecte : ${fmtDateTime(data.last_sync)}${data.next_sync ? ` · prochaine vers ${fmtDateTime(data.next_sync)}` : ''}.`;
  state.replaceChildren(...[headline, data.problems.length ? el('span', { class: 'no-i18n' }, ` ${data.problems.join(' ; ')}`) : null].filter(Boolean));
  $('agg-sources').replaceChildren(...(data.sources.length ? data.sources.map(aggSourceRow) : [emptyRow(7, 'Aucune source.')]));
  const t = data.totals;
  $('agg-totals').replaceChildren(
    el('dt', {}, 'Œuvres'), el('dd', {}, `${fmtNumber(t.works, 0)} (dont ${fmtNumber(t.day, 0)} en 24 h, ${fmtNumber(t.week, 0)} sur la semaine)`),
    el('dt', {}, 'Torrents'), el('dd', {}, fmtNumber(t.torrents, 0)), el('dt', {}, 'Envoyés à qBittorrent'), el('dd', {}, fmtNumber(t.sent, 0)),
    el('dt', {}, 'Conservation'), el('dd', {}, `${fmtNumber(data.retention, 0)} jours`));
  $('agg-timeline').replaceChildren(...data.timeline.map((d) => el('tr', {}, el('td', {}, d.day), el('td', {}, fmtNumber(d.indexed, 0)),
    el('td', {}, fmtNumber(d.added, 0)), el('td', {}, fmtNumber(d.purged, 0)))));
  const form = $('agg-settings').elements;
  form.enabled.checked = data.enabled;
  form.alert.checked = data.alert;
  form.interval.replaceChildren(...data.intervals.map((m) => el('option', { value: m }, AGG_INTERVALS[m] || `${m} minutes`)));
  form.interval.value = String(data.interval);
  form.retention.value = data.retention;
  form.limit.value = data.limit;
  form.category.value = data.category;
  await loadAggCatalogue();
}

async function saveAggSettings(event) {
  event.preventDefault();
  const f = event.target.elements;
  try {
    await api('/api/aggregator/settings', { enabled: f.enabled.checked, alert: f.alert.checked, interval: Number(f.interval.value), retention: Number(f.retention.value),
      limit: Number(f.limit.value), category: f.category.value.trim() });
    toast('ok', 'Réglages enregistrés.');
    await loadAggregator();
  } catch (error) { showError(error); }
}

async function addAggSource(event) {
  event.preventDefault();
  const f = event.target.elements;
  try {
    await api('/api/aggregator/source/add', { name: f.name.value.trim(), url: f.url.value.trim(), key: f.key.value });
    toast('ok', 'Source ajoutée : lancez une collecte pour la remplir.');
    event.target.reset();
    await loadAggregator();
  } catch (error) { showError(error); }
}

function initAggregator() {
  $('agg-sync').addEventListener('click', () => runJob('/api/aggregator/sync', {}, () => loadAggregator().catch(showError)));
  $('agg-form').addEventListener('submit', (event) => { event.preventDefault(); aggState.page = 1; loadAggCatalogue().catch(showError); });
  $('agg-prev').addEventListener('click', () => { aggState.page = Math.max(1, aggState.page - 1); loadAggCatalogue().catch(showError); });
  $('agg-next').addEventListener('click', () => { aggState.page = Math.min(aggState.pages, aggState.page + 1); loadAggCatalogue().catch(showError); });
  $('agg-settings').addEventListener('submit', saveAggSettings);
  $('agg-add').addEventListener('submit', addAggSource);
}

// --- sous-domaines des applications (page Adresse) ---------------------------------------------------------------------------
let subData = null;
const SUB_DNS = { ok: ['DNS correct', 'ok'], autre: ['autre adresse', 'warn'], absent: ['absent du DNS', 'err'], inconnu: ['sans réponse', 'warn'] };

async function loadSubdomains() {
  subData = await api('/api/subdomains');
  drawSubdomains();
}

const subReload = () => loadSubdomains().catch(showError);

function drawSubdomains() {
  const data = subData;
  const state = $('sub-state');
  state.className = `notice ${data.enabled ? 'info' : 'warn'}`;
  state.textContent = data.enabled ? `Sous-domaines actifs sous ${data.domain}.` : 'Sous-domaines désactivés : les applications ne répondent que par chemin.';
  $('sub-form').elements.domain.value = data.domain || data.suggestion || '';
  $('sub-off').classList.toggle('hidden', !data.enabled);
  $('sub-form').querySelector('button[type=submit]').textContent = data.enabled ? 'Changer de domaine' : 'Activer';
  $('sub-rename').classList.toggle('hidden', !data.enabled);
  $('sub-warnings').replaceChildren(...data.warnings.map((text) => el('li', {}, el('span', { class: 'no-i18n' }, text))));
  const rows = data.enabled ? data.apps : [];
  $('sub-list').replaceChildren(...(rows.length ? rows.map((row) => {
    const dns = SUB_DNS[row.state];
    return el('tr', {},
      el('td', {}, row.label),
      el('td', { class: 'no-i18n' }, row.enabled ? el('a', { href: row.url, target: '_blank', rel: 'noopener' }, row.host) : row.host),
      el('td', {}, !row.enabled ? '-' : dns ? [pill(dns[0], dns[1]), row.ips && row.ips.length ? el('span', { class: 'muted small no-i18n' }, ` ${row.ips.join(', ')}`) : null] : '-'),
      el('td', {}, row.portal ? '-' : LOGIN_LABEL[row.protected ? 'sso' : 'comptes']),
      el('td', {}, row.portal ? null : row.enabled
        ? el('button', { class: 'btn sm', type: 'button', onclick: () => runJob('/api/subdomains/toggle', { app: row.id, enabled: false }, subReload) }, 'Retirer')
        : el('button', { class: 'btn sm', type: 'button', onclick: () => runJob('/api/subdomains/toggle', { app: row.id, enabled: true }, subReload) }, 'Remettre')));
  }) : [emptyRow(5, 'Activez un domaine pour obtenir une adresse par application.')]));
  $('sub-app').replaceChildren(...data.apps.filter((a) => !a.portal).map((a) => el('option', { value: a.id }, a.label)));
  const zone = data.enabled ? data.apps.filter((a) => a.enabled).map((a) => `${a.host} ${data.record} ${data.target}`) : [];
  $('sub-zone').textContent = zone.join('\n');
  $('sub-zone').classList.toggle('hidden', !zone.length);
  $('sub-zone-title').classList.toggle('hidden', !zone.length);
}

function initSubdomains() {
  $('sub-refresh').addEventListener('click', subReload);
  $('sub-form').addEventListener('submit', (event) => {
    event.preventDefault();
    runJob('/api/subdomains/set', { domain: event.target.elements.domain.value.trim() }, subReload);
  });
  $('sub-off').addEventListener('click', (event) => confirmAction(event.currentTarget,
    'Les adresses par application ne répondent plus ; les adresses par chemin continuent de fonctionner. Authelia et Traefik sont relancés.',
    () => runJob('/api/subdomains/off', {}, subReload)));
  $('sub-rename').addEventListener('submit', (event) => {
    event.preventDefault();
    const f = event.target.elements;
    runJob('/api/subdomains/rename', { app: f.app.value, name: f.name.value.trim() }, () => { f.name.value = ''; subReload(); });
  });
}

// --- bannissements fail2ban (page Bannissements) ----------------------------------------------------------------------------
let bansData = null;

const bansAct = (path, body, ok) => async () => {
  try { bansData = await api(path, body); toast('ok', ok); drawBans(); } catch (error) { showError(error); }
};

function banActionLabel(action) { return action === 'Unban' ? 'débannie' : action === 'Restore Ban' ? 'bannie (reprise)' : 'bannie'; }

function drawBans() {
  const data = bansData;
  const state = $('bans-state');
  state.className = `notice ${!data.installed || !data.active ? 'warn' : 'info'}`;
  state.textContent = !data.installed ? 'fail2ban n’est pas installé sur ce serveur.' : !data.active ? 'fail2ban est arrêté : plus rien n’est bloqué. Le durcissement de l’appliance le relance.'
    : data.total_banned ? `fail2ban actif : ${fmtNumber(data.total_banned, 0)} adresse(s) bannie(s) en ce moment.` : 'fail2ban actif : aucune adresse bannie en ce moment.';
  $('bans-me').classList.toggle('hidden', !data.me || (data.me_ignored && !data.me_banned));
  $('bans-me-text').textContent = data.me_banned ? `Votre adresse (${data.me}) est bannie. Elle ne peut plus se connecter en SSH ; la console web reste accessible.`
    : `Votre adresse (${data.me}) peut être bannie après trop d’échecs de connexion SSH.`;
  $('bans-me-ignore').onclick = bansAct('/api/bans/ignore', { address: data.me }, 'Adresse ignorée : elle ne sera plus bannie.');
  const rows = data.jails.flatMap((jail) => jail.banned.map((b) => ({ ...b, jail: jail.name })));
  $('bans-list').replaceChildren(...(rows.length ? rows.map((row) => el('tr', {},
    el('td', { class: 'no-i18n' }, row.address, row.address === data.me ? ' ' : null, row.address === data.me ? pill('vous', 'warn') : null), el('td', {}, row.jail),
    el('td', {}, row.until ? fmtDateTime(row.until) : row.seconds && row.seconds < 0 ? 'définitif' : '-'),
    el('td', {}, el('div', { class: 'row' },
      el('button', { class: 'btn sm', type: 'button', onclick: bansAct('/api/bans/unban', { address: row.address, jail: row.jail }, 'Adresse débannie.') }, 'Débannir'),
      el('button', { class: 'btn sm', type: 'button', onclick: bansAct('/api/bans/ignore', { address: row.address }, 'Adresse débannie et ignorée désormais.') }, 'Débannir et ignorer')))))
    : [emptyRow(4, 'Aucune adresse bannie.')]));
  $('bans-unban-all').disabled = !rows.length;
  $('bans-jail').replaceChildren(...(data.jails.length ? data.jails.map((j) => el('option', { value: j.name }, j.name)) : [el('option', { value: 'sshd' }, 'sshd')]));
  $('bans-jails').replaceChildren(...(data.jails.length ? data.jails.map((j) => el('tr', {}, el('td', {}, j.name, j.error ? el('div', { class: 'muted small no-i18n' }, j.error) : null),
    el('td', {}, fmtNumber(j.failed, 0)), el('td', {}, fmtNumber(j.failed_total, 0)), el('td', {}, fmtNumber(j.banned.length, 0)), el('td', {}, fmtNumber(j.banned_total, 0))))
    : [emptyRow(5, 'Aucun jail actif.')]));
  const ignored = [...data.always, ...data.settings.ignore];
  $('bans-ignored').replaceChildren(...ignored.map((item) => el('li', {}, el('span', { class: 'no-i18n' }, item), ' ',
    data.always.includes(item) ? el('span', { class: 'muted small' }, 'toujours ignorée')
      : el('button', { class: 'btn sm', type: 'button', onclick: bansAct('/api/bans/unignore', { address: item }, 'Adresse retirée de la liste.') }, 'Retirer'))));
  const f = $('bans-settings').elements;
  f.ban_minutes.value = data.settings.ban_minutes;
  f.window_minutes.value = data.settings.window_minutes;
  f.retries.value = data.settings.retries;
  f.increment.checked = data.settings.increment;
  f.max_hours.value = data.settings.max_hours;
  $('bans-recent').replaceChildren(...(data.recent.length ? data.recent.map((r) => el('tr', {}, el('td', {}, fmtDateTime(r.at)), el('td', {}, r.jail),
    el('td', {}, banActionLabel(r.action)), el('td', { class: 'no-i18n' }, r.address))) : [emptyRow(4, 'Rien de récent.')]));
}

async function loadBans() {
  bansData = await api('/api/bans');
  drawBans();
}

function initBans() {
  $('bans-refresh').addEventListener('click', () => loadBans().catch(showError));
  $('bans-unban-all').addEventListener('click', (event) => confirmAction(event.currentTarget,
    'Toutes les adresses bannies sont libérées : celles qui tentent de deviner un mot de passe pourront recommencer.', bansAct('/api/bans/unban-all', {}, 'Toutes les adresses sont débannies.')));
  $('bans-add').addEventListener('submit', (event) => {
    event.preventDefault();
    const f = event.target.elements;
    bansAct('/api/bans/ban', { address: f.address.value.trim(), jail: f.jail.value }, 'Adresse bannie.')().then(() => event.target.reset());
  });
  $('bans-ignore-form').addEventListener('submit', (event) => {
    event.preventDefault();
    bansAct('/api/bans/ignore', { address: event.target.elements.address.value.trim() }, 'Adresse ignorée : elle ne sera plus bannie.')().then(() => event.target.reset());
  });
  $('bans-settings').addEventListener('submit', (event) => {
    event.preventDefault();
    const f = event.target.elements;
    bansAct('/api/bans/settings', { ban_minutes: Number(f.ban_minutes.value), window_minutes: Number(f.window_minutes.value), retries: Number(f.retries.value),
      increment: f.increment.checked, max_hours: Number(f.max_hours.value) }, 'Seuils enregistrés et appliqués.')();
  });
}

function initOps5() {
  initAggregator();
  initBans();
  initSubdomains();
  $('apppass-form').addEventListener('submit', resetAppPassword);
  $('apppass-app').addEventListener('change', syncAppPassAccount);
  $('care-refresh').addEventListener('click', () => loadCare().catch(showError));
  $('spacefore-form').addEventListener('submit', saveSpacefore);
  $('libreport-form').addEventListener('submit', saveLibreport);
  $('libreport-send').addEventListener('click', sendLibreport);
  $('dormant-scan').addEventListener('click', () => runJob('/api/dormant/scan', {}, () => loadDormant().catch(showError)));
  $('dormant-unmonitor').addEventListener('click', (event) => confirmAction(event.currentTarget,
    'Radarr et Sonarr arrêtent de chercher les titres cochés. Aucun fichier n’est supprimé ; le suivi se rétablit en un clic dans l’application.', unmonitorTitles));
  $('dormant-form').addEventListener('submit', saveDormant);
  $('integrity-run').addEventListener('click', () => runJob('/api/integrity/run', {}, () => loadIntegrity().catch(showError)));
  $('integrity-form').addEventListener('submit', saveIntegrity);
  $('renamer-scan').addEventListener('click', () => runJob('/api/renamer/scan', {}, () => loadRenamer().catch(showError)));
  $('renamer-apply').addEventListener('click', (event) => confirmAction(event.currentTarget,
    'Radarr et Sonarr renomment sur place les fichiers des titres cochés d’après leur format de nommage. Les torrents continuent de partager (le lien dur suit).', applyRename));
  $('subsgap-form').addEventListener('submit', saveSubsgap);
  $('energy-form').addEventListener('submit', saveEnergy);
  $('guestquota-card').addEventListener('submit', saveGuestquotaSettings);
  $('trackerstats-refresh').addEventListener('click', () => runJob('/api/trackerstats/refresh', {}, () => loadTrackerstats().catch(showError)));
  $('trackerstats-form').addEventListener('submit', saveTrackerAlias);
  $('trackerstats-alert').addEventListener('change', async (event) => {
    try { await api('/api/trackerstats/settings', { alert: event.target.checked }); toast('ok', 'Réglage enregistré.'); } catch (error) { showError(error); }
  });
}
