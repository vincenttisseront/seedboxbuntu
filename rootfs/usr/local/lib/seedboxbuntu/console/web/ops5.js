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

function initOps5() {
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
