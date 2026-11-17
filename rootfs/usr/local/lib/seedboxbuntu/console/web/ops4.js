'use strict';
// SeedBoxBuntu - console : bibliothèques par partage, remplacements, allègement, optimisation des seeds, assistant IA externe.

const volumeLabel = (volume) => (volume === 'local' ? 'Disque local' : volume);
const signedSize = (value) => (value < 0 ? `−${fmtSize(-value)}` : `+${fmtSize(value)}`);
const checkCell = (key, label, extra = {}) => el('td', {}, el('input', { type: 'checkbox', 'aria-label': label, 'data-key': key, ...extra }));
const picked = (tbody) => [...tbody.querySelectorAll('input[type=checkbox]:checked')].map((box) => box.getAttribute('data-key'));
const kvList = (pairs) => pairs.flatMap(([label, value]) => [el('dt', {}, label), el('dd', {}, value)]);
const chipsOf = (items) => el('div', { class: 'row' }, ...items.map((i) => pill(`${i.label} · ${fmtSize(i.size)}`, 'info')));

// ---------------------------------------------------------------------------------------------------------------
//  Assistant IA externe : bouton d'avis (pages Bibliothèques et Seeds) et réglages
// ---------------------------------------------------------------------------------------------------------------
async function loadAiBox(topic) {
  const button = $(`${topic === 'seeds' ? 'seeds' : 'library'}-ai`);
  const box = $(`${topic === 'seeds' ? 'seeds' : 'library'}-avis`);
  const data = await api('/api/llm');
  button.disabled = !data.settings.enabled;
  button.title = data.settings.enabled ? '' : 'Activez le connecteur dans Paramètres.';
  const last = data.last;
  const show = !!(last && last.topic === topic);
  box.classList.toggle('hidden', !show);
  if (show) $(`${topic === 'seeds' ? 'seeds' : 'library'}-avis-texte`).textContent = `${last.text}\n\n(${last.model}, ${fmtDateTime(last.at)})`;
}
function askAi(topic) {
  runJob('/api/llm/explain', { topic }, () => loadAiBox(topic).catch(showError));
}
async function loadLlm() {
  const data = await api('/api/llm');
  const form = $('llm-card');
  const s = data.settings;
  form.elements.enabled.checked = s.enabled;
  form.elements.url.value = s.url;
  form.elements.model.value = s.model;
  form.elements.token.value = '';
  form.elements.timeout.value = String(s.timeout);
  form.elements.verify_tls.checked = s.verify_tls;
  form.elements.send_trackers.checked = s.send_trackers;
  form.elements.send_titles.checked = s.send_titles;
  $('llm-token-clear').disabled = !s.has_token;
  const state = $('llm-state');
  state.className = `notice ${s.enabled ? 'ok' : 'info'}`;
  state.textContent = s.enabled ? `Connecteur actif : ${s.model} sur ${s.url}${s.has_token ? ' (jeton enregistré)' : ''}.` : 'Connecteur désactivé : rien n’est envoyé.';
}
async function saveLlm(event) {
  event.preventDefault();
  const form = event.target;
  const body = { enabled: form.elements.enabled.checked, url: form.elements.url.value.trim(), model: form.elements.model.value.trim(),
    timeout: Number(form.elements.timeout.value), verify_tls: form.elements.verify_tls.checked, send_trackers: form.elements.send_trackers.checked,
    send_titles: form.elements.send_titles.checked };
  if (form.elements.token.value) body.token = form.elements.token.value;
  try {
    await api('/api/llm/settings', body);
    toast('ok', 'Connecteur enregistré.');
    loadLlm().catch(showError);
  } catch (error) { showError(error); }
}
async function testLlm() {
  const button = $('llm-test');
  button.disabled = true;
  try {
    const result = await api('/api/llm/test', {});
    $('llm-models').replaceChildren(...result.models.map((m) => el('option', { value: m.name })));
    toast(result.model_ok ? 'ok' : 'warn', result.model_ok ? `Serveur joint (version ${result.version || 'inconnue'}), modèle présent : ${result.ms} ms.`
      : `Serveur joint, mais le modèle choisi n’y est pas : ${result.models.length} modèle(s) disponible(s) dans la liste de saisie.`);
  } catch (error) { showError(error); } finally { button.disabled = false; }
}
async function clearLlmToken() {
  try {
    await api('/api/llm/settings', { token: '' });
    toast('ok', 'Jeton retiré.');
    loadLlm().catch(showError);
  } catch (error) { showError(error); }
}

// ---------------------------------------------------------------------------------------------------------------
//  Bibliothèques : carte, versions multiples, copies cachées, profils, déplacement
// ---------------------------------------------------------------------------------------------------------------
async function loadLibrary() {
  const data = await api('/api/library');
  const state = $('library-state');
  const failed = Object.entries(data.errors || {});
  state.className = `notice ${failed.length ? 'warn' : 'info'}`;
  state.textContent = data.scanning ? 'Relevé en cours…'
    : failed.length ? `Lecture impossible : ${failed.map(([app, text]) => `${app} (${text})`).join(' ; ')}.`
      : data.at ? `Relevé du ${fmtDateTime(data.at)} : ${fmtNumber(data.files, 0)} fichier(s).` : 'Aucun relevé pour le moment : cliquez sur Analyser les bibliothèques.';
  $('library-volumes').replaceChildren(...(data.volumes.length ? data.volumes.map((v) => el('tr', {},
    el('td', {}, volumeLabel(v.volume)), el('td', {}, fmtNumber(v.titles, 0)), el('td', {}, fmtSize(v.size)),
    el('td', {}, v.free === null ? '-' : fmtSize(v.free)), el('td', {}, v.mb_per_min === null ? '-' : fmtNumber(v.mb_per_min)),
    el('td', {}, chipsOf(v.resolutions.slice(0, 4))), el('td', {}, chipsOf(v.codecs.slice(0, 3)))))
    : [emptyRow(7, 'Aucun volume : lancez le relevé.')]));
  $('versions-summary').textContent = data.versions_total
    ? `${data.versions_total} titre(s) en plusieurs versions : jusqu’à ${fmtSize(data.versions_saving)} récupérables en gardant la meilleure de chacun. Rien n’est supprimé ici : décidez dans Radarr ou Sonarr.`
    : 'Aucun titre en plusieurs versions.';
  $('multiver-list').replaceChildren(...(data.versions.length ? data.versions.map((e) => el('tr', {},
    el('td', {}, e.title, el('div', { class: 'muted small' }, e.kind === 'film' ? 'Film' : 'Épisode')),
    el('td', {}, el('ul', {}, ...e.files.map((f) => el('li', { class: 'state-item small' }, `${f.quality || '?'} · ${fmtSize(f.size)} · ${volumeLabel(f.volume)}`)))),
    el('td', {}, fmtSize(e.saving)))) : [emptyRow(3, 'Rien à signaler.')]));
  loadCopyaudit().catch(showError);
  loadShareprofile().catch(showError);
  loadRelocate().catch(showError);
  loadAiBox('library').catch(showError);
}

async function loadCopyaudit() {
  const data = await api('/api/copyaudit');
  const state = $('copyaudit-state');
  state.className = 'notice info';
  state.textContent = data.at ? `Recherche du ${fmtDateTime(data.at)} : ${data.total} copie(s), ${fmtSize(data.saving)} récupérables${data.blocked ? `, ${fmtSize(data.blocked)} sur deux disques (lien impossible)` : ''}.`
    : 'Aucune recherche pour le moment : cliquez sur Analyser (quelques minutes sur un gros dossier).';
  const possible = data.items.filter((i) => i.same_disk);
  $('copyaudit-relink').disabled = !possible.length;
  $('copyaudit-relink-all').disabled = !possible.length;
  $('copyaudit-all').checked = false;
  $('copyaudit-all').disabled = !possible.length;
  $('copyaudit-list').replaceChildren(...(data.items.length ? data.items.map((i) => el('tr', {},
    checkCell(i.download, `Relier ${i.title}`, i.same_disk ? {} : { disabled: '' }), el('td', { class: 'small' }, i.download), el('td', {}, i.title),
    el('td', {}, fmtSize(i.size)), el('td', {}, pill(i.same_disk ? 'possible' : 'deux disques', i.same_disk ? 'ok' : 'warn')))) : [emptyRow(5, 'Aucune copie cachée.')]));
}
// Liage en masse : la progression (fichier i sur n) est lue dans le journal de la tâche
function relinkProgress(job) {
  const bar = $('copyaudit-progress');
  const lines = (job.log || '').trim().split('\n');
  const last = [...lines].reverse().map((line) => line.match(/(\d+)\/(\d+) : (.*?) \((\d+) reliée\(s\), (\d+) octets récupérés\)/)).find(Boolean);
  const state = $('copyaudit-state');
  state.className = 'notice info';
  if (!last) { bar.classList.remove('hidden'); bar.removeAttribute('value'); state.textContent = 'Recherche des copies à relier…'; return; }
  const [, index, total, name, count, bytes] = last;
  bar.classList.remove('hidden');
  bar.max = Number(total);
  bar.value = Number(index) - 1;
  state.textContent = `Liage en cours : fichier ${index} sur ${total} (${name}) · ${count} copie(s) reliée(s), ${fmtSize(Number(bytes))} récupérés.`;
}
function relinkAll() {
  runJob('/api/copyaudit/relink', { all: true }, (job) => {
    $('copyaudit-progress').classList.add('hidden');
    const log = (job && job.log) || '';
    const result = log.match(/copies reliées : (\d+) fichier\(s\), (\d+) octets récupérés/);
    const refused = [...log.matchAll(/ignorée : .*? : (.+)/g)].map((m) => m[1]);
    if (job && job.state === 'error') toast('err', `Liage interrompu : ${(log.trim().split('\n').pop() || '').replace('[seedboxctl] ', '')}`);
    else if (result) toast(Number(result[1]) ? 'ok' : 'warn', `${result[1]} copie(s) reliée(s), ${fmtSize(Number(result[2]))} récupérés${refused.length ? ` ; ${refused.length} ignorée(s) : ${refused[0]}` : ''}.`);
    loadCopyaudit().catch(showError);
  }, relinkProgress);
}
async function relinkCopies() {
  const paths = picked($('copyaudit-list'));
  if (!paths.length) { toast('warn', 'Cochez au moins une copie.'); return; }
  try {
    const result = await api('/api/copyaudit/relink', { paths });
    toast(result.skipped.length ? 'warn' : 'ok', `${result.done} copie(s) reliée(s), ${fmtSize(result.saved)} récupérés${result.skipped.length ? ` ; ${result.skipped.length} ignorée(s) : ${result.skipped[0].reason}` : ''}.`);
    loadCopyaudit().catch(showError);
  } catch (error) { showError(error); }
}

async function loadShareprofile() {
  const data = await api('/api/shareprofile');
  $('shareprofile-auto').checked = data.settings.auto;
  const rows = [];
  for (const app of ['radarr', 'sonarr']) {
    const profiles = data.profiles[app] || [];
    for (const [volume, v] of Object.entries(data.volumes[app] || {})) {
      const select = el('select', { 'aria-label': `Profil voulu pour ${volumeLabel(volume)} (${app})` }, el('option', { value: '' }, 'aucun'),
        ...profiles.map((p) => el('option', { value: String(p.id) }, p.name)));
      select.value = v.wanted ? String(v.wanted) : '';
      select.addEventListener('change', async () => {
        try {
          await api(select.value ? '/api/shareprofile/set' : '/api/shareprofile/unset', { app, volume, profile: Number(select.value) });
          loadShareprofile().catch(showError);
        } catch (error) { showError(error); }
      });
      rows.push(el('tr', {}, el('td', {}, app === 'radarr' ? 'Films' : 'Séries'), el('td', {}, volumeLabel(volume)), el('td', {}, fmtNumber(v.titles, 0)),
        el('td', {}, Object.entries(v.profiles).map(([name, count]) => `${name} (${count})`).join(' · ')), el('td', {}, select),
        el('td', {}, v.wanted ? pill(String(v.wrong), v.wrong ? 'warn' : 'ok') : '-')));
    }
  }
  $('shareprofile-list').replaceChildren(...(rows.length ? rows : [emptyRow(6, data.errors && data.errors.all ? `Lecture impossible : ${data.errors.all}` : 'Aucune bibliothèque lue.')]));
}
async function applyShareprofile() {
  try {
    const result = await api('/api/shareprofile/apply', {});
    const total = Object.values(result.changed).reduce((a, b) => a + b, 0);
    toast('ok', total ? `${total} titre(s) mis au profil de leur volume.` : 'Tous les titres ont déjà le profil voulu.');
    loadShareprofile().catch(showError);
  } catch (error) { showError(error); }
}

async function loadRelocate(volume) {
  const data = await api(`/api/relocate${volume ? `?volume=${encodeURIComponent(volume)}` : ''}`);
  const from = $('relocate-volume');
  from.replaceChildren(...data.volumes.map((v) => el('option', { value: v }, volumeLabel(v))));
  from.value = data.volume;
  const targets = [...new Set([...data.volumes, ...Object.keys(data.roots)])].filter((v) => v !== data.volume);
  const chosen = $('relocate-target').value;
  $('relocate-target').replaceChildren(...targets.map((v) => el('option', { value: v }, `${volumeLabel(v)}${data.roots[v] ? '' : ' (aucun dossier racine)'}`)));
  if (targets.includes(chosen)) $('relocate-target').value = chosen;
  $('relocate-form').elements.idle_days.value = String(data.settings.idle_days);
  const state = $('relocate-state');
  state.className = `notice ${data.reliable ? 'info' : 'warn'}`;
  state.textContent = !data.volume ? 'Aucun relevé : lancez l’analyse des bibliothèques.'
    : !data.reliable ? 'Jellyfin ne remonte aucune lecture : cette liste est peu fiable, vérifiez-la titre par titre.'
      : `${data.total} titre(s) inactifs depuis ${data.settings.idle_days} jours sur ce volume ; ${fmtSize(data.reclaim)} réellement libérés (fichiers sans lien avec un torrent).`;
  $('relocate-move').disabled = !data.candidates.length;
  $('relocate-list').replaceChildren(...(data.candidates.length ? data.candidates.map((t) => el('tr', {},
    checkCell(`${t.app}:${t.id}`, `Déplacer ${t.title}`), el('td', {}, t.title, el('div', { class: 'muted small' }, t.app === 'radarr' ? 'Film' : 'Série')),
    el('td', {}, fmtSize(t.size)), el('td', {}, t.reclaim ? fmtSize(t.reclaim) : pill('rien (lié à un torrent)', 'warn')),
    el('td', {}, t.last_played ? fmtDateTime(t.last_played) : el('span', { class: 'muted small' }, 'jamais lu'))))
    : [emptyRow(5, 'Aucun titre inactif sur ce volume.')]));
}
async function moveTitles() {
  const chosen = picked($('relocate-list')).map((k) => k.split(':'));
  const to = $('relocate-target').value;
  if (!chosen.length) { toast('warn', 'Cochez au moins un titre.'); return; }
  try {
    let moved = 0;
    for (const app of ['radarr', 'sonarr']) {
      const ids = chosen.filter(([a]) => a === app).map(([, id]) => Number(id));
      if (ids.length) moved += (await api('/api/relocate/move', { app, from: $('relocate-volume').value, to, ids })).moved;
    }
    toast('ok', `Déplacement lancé pour ${moved} titre(s) : Radarr et Sonarr s’en occupent.`);
    loadRelocate($('relocate-volume').value).catch(showError);
  } catch (error) { showError(error); }
}
async function saveRelocate(event) {
  event.preventDefault();
  try {
    await api('/api/relocate/settings', { idle_days: Number(event.target.elements.idle_days.value) });
    toast('ok', 'Réglage enregistré.');
    loadRelocate($('relocate-volume').value).catch(showError);
  } catch (error) { showError(error); }
}

// ---------------------------------------------------------------------------------------------------------------
//  Remplacements sous budget et allègement des fichiers
// ---------------------------------------------------------------------------------------------------------------
let replacerData = null;
const budgetsText = (budgets) => Object.entries(budgets).map(([v, gb]) => `${v}=${gb}`).join(' ');
function parseBudgets(text) {
  const out = {};
  for (const part of text.split(/[\s,;]+/).filter(Boolean)) {
    const match = /^([a-z][a-z0-9]{0,19}|local)=(\d{1,6})$/.exec(part);
    if (!match) throw new Error(`Budget illisible : « ${part} ». Écrivez par exemple nas=200 local=50.`);
    out[match[1]] = Number(match[2]);
  }
  return out;
}
function updateSimulation() {
  if (!replacerData) return;
  const ids = new Set(picked($('replacer-list')));
  const rows = replacerData.plan.selected.filter((p) => ids.has(p.id));
  const growth = rows.reduce((a, p) => a + Math.max(p.delta, 0), 0);
  const peak = rows.reduce((a, p) => a + p.release.size, 0);
  const final = rows.reduce((a, p) => a + p.delta, 0);
  $('replacer-sim').textContent = rows.length
    ? `Simulation : ${rows.length} remplacement(s), croissance ${fmtSize(growth)}, pic temporaire ${fmtSize(peak)} (l’ancien fichier reste en corbeille ${replacerData.settings.recycle_days} jours), bilan final ${signedSize(final)}.`
    : 'Cochez des propositions pour simuler leur effet sur l’espace.';
  $('replacer-apply').disabled = !rows.length;
}
async function loadReplacer() {
  const data = await api('/api/replacer');
  replacerData = data;
  const form = $('replacer-form');
  for (const key of ['max_growth_pct', 'min_seeders', 'per_run', 'recycle_days', 'budget_gb', 'keep_free_gb']) form.elements[key].value = String(data.settings[key]);
  form.elements.budgets.value = budgetsText(data.settings.budgets);
  const safety = $('replacer-safety');
  safety.className = `notice ${data.safety.ok ? 'ok' : 'warn'}`;
  safety.textContent = data.safety.ok
    ? `Corbeille active (${data.safety.apps.radarr.days} jours) : ${fmtSize(data.safety.size)} actuellement conservés.`
    : 'Corbeille inactive dans Radarr ou Sonarr : aucun remplacement ne sera lancé tant qu’elle n’est pas activée.';
  $('replacer-safety-on').disabled = data.safety.ok;
  const state = $('replacer-state');
  state.className = 'notice info';
  state.textContent = data.at ? `Recherche du ${fmtDateTime(data.at)} : ${data.searched} titre(s) examiné(s), ${data.plan.selected.length + data.plan.deferred.length} proposition(s)${data.errors.length ? `, ${data.errors.length} erreur(s)` : ''}.`
    : 'Aucune recherche pour le moment : cliquez sur Rechercher des remplacements (lent : chaque titre interroge tous vos indexeurs).';
  const row = (p, defer) => el('tr', {}, checkCell(p.id, `Remplacer ${p.title}`, defer ? { disabled: '' } : { checked: '' }),
    el('td', {}, p.title, el('div', { class: 'muted small' }, p.reason)), el('td', {}, volumeLabel(p.volume)),
    el('td', {}, `${p.cur.quality || '?'} · ${fmtSize(p.cur.size)}`), el('td', {}, el('span', { class: 'small' }, p.release.title), el('div', { class: 'muted small' }, `${fmtSize(p.release.size)} · ${p.release.indexer} · ${p.release.seeders} sources`)),
    el('td', {}, signedSize(p.delta)), el('td', {}, p.why), el('td', {}, defer ? pill(defer, 'warn') : pill('dans le budget', 'ok')));
  const rows = [...data.plan.selected.map((p) => row(p, '')), ...data.plan.deferred.map((p) => row(p, p.defer))];
  $('replacer-list').replaceChildren(...(rows.length ? rows : [emptyRow(8, 'Aucune proposition.')]));
  $('replacer-list').querySelectorAll('input').forEach((box) => box.addEventListener('change', updateSimulation));
  updateSimulation();
}
async function saveReplacer(event) {
  event.preventDefault();
  const form = event.target;
  try {
    const body = { budgets: parseBudgets(form.elements.budgets.value) };
    for (const key of ['max_growth_pct', 'min_seeders', 'per_run', 'recycle_days', 'budget_gb', 'keep_free_gb']) body[key] = Number(form.elements[key].value);
    await api('/api/replacer/settings', body);
    toast('ok', 'Règles enregistrées.');
    loadReplacer().catch(showError);
  } catch (error) { showError(error); }
}
async function applyReplacer() {
  try {
    const result = await api('/api/replacer/apply', { ids: picked($('replacer-list')) });
    toast(result.skipped.length ? 'warn' : 'ok', `${result.done} version(s) lancée(s)${result.skipped.length ? ` ; ${result.skipped.length} écartée(s) : ${result.skipped[0].reason}` : ''}. Radarr et Sonarr importent à l’arrivée.`);
    loadReplacer().catch(showError);
  } catch (error) { showError(error); }
}
async function enableSafety() {
  try {
    await api('/api/replacer/safety', {});
    toast('ok', 'Corbeille activée dans Radarr et Sonarr.');
    loadReplacer().catch(showError);
  } catch (error) { showError(error); }
}

const SLIM_KIND = { recompress: 'Recompression x265', tracks: 'Retrait de pistes' };
const SLIM_STATE = { attente: 'info', 'en cours': 'warn', fait: 'ok', échec: 'err', écarté: 'unknown' };
async function loadMediaslim() {
  const data = await api('/api/mediaslim');
  const form = $('mediaslim-form');
  form.elements.window_start.value = String(data.settings.window_start);
  form.elements.window_end.value = String(data.settings.window_end);
  form.elements.keep_audio.value = data.settings.keep_audio.join(' ');
  form.elements.keep_subs.value = data.settings.keep_subs.join(' ');
  form.elements.crf.value = String(data.settings.crf);
  form.elements.trash_days.value = String(data.settings.trash_days);
  const state = $('mediaslim-state');
  state.className = 'notice info';
  state.textContent = `Traitement de ${data.settings.window_start} h à ${data.settings.window_end} h · ${data.running ? 'en cours' : 'en attente'} · déjà gagné : ${fmtSize(data.saved)}.`;
  $('mediaslim-candidates').replaceChildren(...(data.candidates.length ? data.candidates.slice(0, 60).map((c) => el('tr', {},
    checkCell(`${c.kind}|${c.path}`, `Alléger ${c.title}`, c.queued ? { disabled: '' } : {}), el('td', {}, c.title, el('div', { class: 'muted small' }, c.why)),
    el('td', {}, SLIM_KIND[c.kind]), el('td', {}, fmtSize(c.size)), el('td', {}, c.estimate ? fmtSize(c.estimate) : pill('rien (lié à un torrent)', 'warn'))))
    : [emptyRow(5, 'Aucun fichier à alléger : lancez d’abord l’analyse des bibliothèques.')]));
  $('mediaslim-queue').replaceChildren(...(data.queue.length ? data.queue.map((q) => el('tr', {},
    el('td', {}, q.title), el('td', {}, SLIM_KIND[q.kind] || q.kind), el('td', {}, pill(q.state, SLIM_STATE[q.state] || 'unknown'), q.detail ? el('div', { class: 'muted small' }, q.detail) : null),
    el('td', {}, q.saved ? fmtSize(q.saved) : '-'),
    el('td', {}, q.state === 'en cours' ? null : el('button', { class: 'btn sm', type: 'button', onclick: () => removeSlim(q.id) }, 'Retirer'))))
    : [emptyRow(5, 'File vide.')]));
}
async function addSlim() {
  const chosen = picked($('mediaslim-candidates')).map((k) => k.split('|'));
  if (!chosen.length) { toast('warn', 'Cochez au moins un fichier.'); return; }
  try {
    let added = 0;
    for (const kind of ['recompress', 'tracks']) {
      const paths = chosen.filter(([k]) => k === kind).map(([, p]) => p);
      if (paths.length) added += (await api('/api/mediaslim/add', { kind, paths })).added;
    }
    toast('ok', `${added} fichier(s) ajouté(s) à la file : traitement la nuit prochaine.`);
    loadMediaslim().catch(showError);
  } catch (error) { showError(error); }
}
async function removeSlim(id) {
  try {
    await api('/api/mediaslim/remove', { id });
    loadMediaslim().catch(showError);
  } catch (error) { showError(error); }
}
async function saveMediaslim(event) {
  event.preventDefault();
  const form = event.target;
  const langs = (text) => text.split(/[\s,;]+/).filter(Boolean);
  try {
    await api('/api/mediaslim/settings', { window_start: Number(form.elements.window_start.value), window_end: Number(form.elements.window_end.value),
      keep_audio: langs(form.elements.keep_audio.value), keep_subs: langs(form.elements.keep_subs.value), crf: Number(form.elements.crf.value),
      trash_days: Number(form.elements.trash_days.value) });
    toast('ok', 'Réglages enregistrés.');
    loadMediaslim().catch(showError);
  } catch (error) { showError(error); }
}

// ---------------------------------------------------------------------------------------------------------------
//  Optimisation des seeds
// ---------------------------------------------------------------------------------------------------------------
const CLASS_KIND = { utile: 'ok', normal: 'info', rare: 'info', 'attendu mais muet': 'warn', 'sans valeur': 'warn', 'sans valeur (règles inconnues)': 'unknown', protégé: 'ok', 'en cours': 'info' };
function trackerCard(t) {
  const rule = t.rule ? `ratio ${fmtNumber(t.rule.min_ratio)} · ${t.rule.min_hours} h` : 'aucune règle déclarée';
  return el('div', { class: 'card' },
    el('div', { class: 'card-head' }, el('h2', {}, t.tracker), pill(rule, t.rule ? 'ok' : 'unknown')),
    el('dl', { class: 'kv' }, ...kvList([
      ['Torrents', fmtNumber(t.torrents, 0)], ['Taille partagée', fmtSize(t.size)],
      ['Envoyé sur 7 jours', fmtSize(t.up7)], ['Envoyé sur 30 jours', fmtSize(t.up30)],
      ['Ratio', t.ratio === null ? '-' : fmtNumber(t.ratio)], ['Objectif de ratio', t.goal ? fmtNumber(t.goal) : '-'],
      ...(t.site ? [['Ratio du site', t.site.ratio === null ? '-' : fmtNumber(t.site.ratio, 2)], ['Ratio exigé par le site', t.site.required === null ? '-' : fmtNumber(t.site.required, 2)]] : []),
      ['Efficacité (envoyé par Go stocké, 30 j)', fmtNumber(t.efficiency, 2)],
      ['Objectif atteint dans', t.eta_days === null ? (t.goal ? 'inconnu' : '-') : t.eta_days === 0 ? 'déjà atteint' : `${fmtNumber(t.eta_days, 0)} jour(s)`],
      ['Historique', `${t.covered} jour(s)${t.covered_ok ? '' : ' : partiel'}`]])),
    el('div', { class: 'row' }, ...t.classes.map((c) => pill(`${c.class} · ${c.torrents}`, CLASS_KIND[c.class] || 'info'))),
    el('ul', {}, ...t.advice.map((a) => el('li', { class: 'state-item' }, a))),
    el('h3', {}, 'Par âge'),
    el('div', { class: 'table-wrap', tabindex: '0', role: 'region', 'aria-label': `Âge des torrents de ${t.tracker}` }, el('table', {},
      el('thead', {}, el('tr', {}, el('th', {}, 'Âge'), el('th', {}, 'Torrents'), el('th', {}, 'Taille'), el('th', {}, 'Envoyé sur 30 jours'))),
      el('tbody', {}, ...t.buckets.map((b) => el('tr', {}, el('td', {}, b.label), el('td', {}, fmtNumber(b.torrents, 0)), el('td', {}, fmtSize(b.size)), el('td', {}, fmtSize(b.up30))))))),
    el('h3', {}, 'Ce qui rapporte le plus'),
    el('div', { class: 'table-wrap', tabindex: '0', role: 'region', 'aria-label': `Meilleurs torrents de ${t.tracker}` }, el('table', {},
      el('thead', {}, el('tr', {}, el('th', {}, 'Torrent'), el('th', {}, 'Taille'), el('th', {}, 'Envoyé sur 30 jours'), el('th', {}, 'Sources / demandeurs'), el('th', {}, 'Classe'))),
      el('tbody', {}, ...t.top.map((r) => el('tr', {}, el('td', { class: 'small' }, r.name), el('td', {}, fmtSize(r.size)), el('td', {}, fmtSize(r.up30)),
        el('td', {}, `${r.seeds} / ${r.leechers}`), el('td', {}, pill(r.class, CLASS_KIND[r.class] || 'info'))))))));
}
async function loadSeeds() {
  const data = await api('/api/seedopt');
  const state = $('seeds-state');
  state.className = `notice ${data.available ? 'info' : 'warn'}`;
  state.textContent = !data.available ? 'qBittorrent est arrêté : aucune analyse possible.'
    : `${fmtNumber(data.torrents, 0)} torrent(s) suivis ; historique de ${data.history_days} jour(s)${data.history_days < 7 ? ' : les chiffres sur 7 et 30 jours sont encore partiels (un relevé par heure).' : '.'}`;
  const rules = Object.entries(data.settings.rules);
  $('seedrules-list').replaceChildren(...(rules.length ? rules.map(([host, r]) => el('tr', {}, el('td', {}, host), el('td', {}, r.min_ratio ? fmtNumber(r.min_ratio) : 'aucun'),
    el('td', {}, r.min_hours ? `${r.min_hours} h` : 'aucun'),
    el('td', {}, el('button', { class: 'btn sm', type: 'button', onclick: () => removeSeedRule(host) }, 'Retirer')))) : [emptyRow(4, 'Aucune règle : aucun tracker n’est proposé au retrait.')]));
  const form = $('seedthresholds-form');
  form.elements.idle_days.value = String(data.settings.idle_days);
  form.elements.idle_pct.value = String(data.settings.idle_pct);
  $('seeds-trackers').replaceChildren(...data.trackers.map(trackerCard));
  $('seeds-silent').replaceChildren(...(data.silent.length ? data.silent.map((r) => el('tr', {}, checkCell(r.hash, `Réannoncer ${r.name}`), el('td', { class: 'small' }, r.name),
    el('td', {}, r.tracker), el('td', {}, fmtSize(r.size)), el('td', {}, String(r.leechers)))) : [emptyRow(5, 'Aucun torrent attendu mais muet.')]));
  $('seeds-reannounce').disabled = !data.silent.length;
  $('seeds-retire-summary').textContent = data.retirable_total
    ? `${data.retirable_total} torrent(s) sans valeur (${fmtSize(data.retirable_size)}), règles du tracker remplies. Retirer un torrent n’économise pas d’espace : ses fichiers ont presque toujours un autre lien (la médiathèque) ; l’espace des copies se récupère dans Bibliothèques. L’intérêt est d’arrêter de partager ce qui n’apporte rien.`
    : 'Aucun torrent sans valeur dont les règles du tracker sont connues et remplies.';
  $('seeds-retire').disabled = !data.retirable.length;
  $('seeds-retire-list').replaceChildren(...(data.retirable.length ? data.retirable.map((r) => el('tr', {}, checkCell(r.hash, `Retirer ${r.name}`), el('td', { class: 'small' }, r.name),
    el('td', {}, r.tracker), el('td', {}, fmtSize(r.size)), el('td', {}, r.all_linked ? pill('liés ailleurs', 'ok') : pill('sans autre lien', 'warn')),
    el('td', {}, `${fmtNumber(r.age, 0)} j`))) : [emptyRow(6, 'Rien à retirer.')]));
  loadAiBox('seeds').catch(showError);
}
async function saveSeedRule(event) {
  event.preventDefault();
  const form = event.target;
  try {
    await api('/api/seedopt/rule', { tracker: form.elements.tracker.value.trim().toLowerCase(), ratio: Number(form.elements.ratio.value), hours: Number(form.elements.hours.value) });
    toast('ok', 'Règle enregistrée.');
    form.elements.tracker.value = '';
    loadSeeds().catch(showError);
  } catch (error) { showError(error); }
}
async function removeSeedRule(host) {
  try {
    await api('/api/seedopt/unrule', { tracker: host });
    loadSeeds().catch(showError);
  } catch (error) { showError(error); }
}
async function saveSeedThresholds(event) {
  event.preventDefault();
  const form = event.target;
  try {
    await api('/api/seedopt/thresholds', { idle_days: Number(form.elements.idle_days.value), idle_pct: Number(form.elements.idle_pct.value) });
    toast('ok', 'Seuils enregistrés.');
    loadSeeds().catch(showError);
  } catch (error) { showError(error); }
}
async function reannounceSeeds() {
  const hashes = picked($('seeds-silent'));
  if (!hashes.length) { toast('warn', 'Cochez au moins un torrent.'); return; }
  try {
    const result = await api('/api/seedopt/reannounce', { hashes });
    toast('ok', `Réannonce envoyée pour ${result.done} torrent(s).`);
  } catch (error) { showError(error); }
}
async function retireSeeds() {
  const hashes = picked($('seeds-retire-list'));
  if (!hashes.length) { toast('warn', 'Cochez au moins un torrent.'); return; }
  try {
    const result = await api('/api/seedopt/retire', { hashes, delete_files: $('seeds-delete-files').checked });
    toast('ok', `${result.done} torrent(s) retiré(s) de qBittorrent.`);
    loadSeeds().catch(showError);
  } catch (error) { showError(error); }
}

async function loadUpgrades() {
  await loadReplacer();
  loadMediaslim().catch(showError);
}

// ---------------------------------------------------------------------------------------------------------------
//  Branchements
// ---------------------------------------------------------------------------------------------------------------
function initOps4() {
  $('library-scan').addEventListener('click', () => runJob('/api/library/scan', {}, () => loadLibrary().catch(showError)));
  $('library-ai').addEventListener('click', () => askAi('library'));
  $('seeds-ai').addEventListener('click', () => askAi('seeds'));
  $('copyaudit-scan').addEventListener('click', () => runJob('/api/copyaudit/scan', {}, () => loadCopyaudit().catch(showError)));
  $('copyaudit-all').addEventListener('change', (event) => $('copyaudit-list').querySelectorAll('input[type=checkbox]:not(:disabled)').forEach((box) => { box.checked = event.target.checked; }));
  $('copyaudit-relink-all').addEventListener('click', (event) => confirmAction(event.currentTarget,
    'Toutes les copies reliables sont comparées octet par octet au fichier de la médiathèque, puis remplacées par un lien vers lui. Le torrent continue de partager le même contenu ; les fichiers de la médiathèque ne sont jamais modifiés. Les copies sur deux disques sont laissées telles quelles.',
    relinkAll));
  $('copyaudit-relink').addEventListener('click', (event) => confirmAction(event.currentTarget,
    'Chaque copie cochée est comparée octet par octet au fichier de la médiathèque, puis remplacée par un lien vers lui. Le torrent continue de partager le même contenu ; le fichier de la médiathèque n’est jamais modifié.', relinkCopies));
  $('shareprofile-apply').addEventListener('click', (event) => confirmAction(event.currentTarget,
    'Les titres de chaque volume prennent le profil de qualité choisi pour ce volume. Aucun fichier n’est téléchargé ni supprimé ; ce que Radarr et Sonarr chercheront ensuite change.', applyShareprofile));
  $('shareprofile-auto').addEventListener('change', async (event) => {
    try { await api('/api/shareprofile/auto', { on: event.target.checked }); toast('ok', event.target.checked ? 'Automatique : contrôle chaque heure.' : 'Automatique désactivé.'); } catch (error) { showError(error); }
  });
  $('relocate-volume').addEventListener('change', (event) => loadRelocate(event.target.value).catch(showError));
  $('relocate-form').addEventListener('submit', saveRelocate);
  $('relocate-move').addEventListener('click', (event) => confirmAction(event.currentTarget,
    'Radarr ou Sonarr déplacent chaque titre coché vers un dossier racine du volume d’arrivée. Entre deux disques, c’est une copie puis une suppression : long, et Jellyfin verra le titre disparaître puis revenir.', moveTitles));
  $('replacer-search').addEventListener('click', () => runJob('/api/replacer/search', {}, () => loadReplacer().catch(showError)));
  $('replacer-safety-on').addEventListener('click', (event) => confirmAction(event.currentTarget,
    'Radarr et Sonarr mettront les fichiers remplacés dans une corbeille au lieu de les effacer, pendant le délai choisi.', enableSafety));
  $('replacer-form').addEventListener('submit', saveReplacer);
  $('replacer-apply').addEventListener('click', (event) => confirmAction(event.currentTarget,
    'Les versions cochées sont téléchargées. À l’arrivée, Radarr et Sonarr décident d’importer ou non ; l’ancien fichier va en corbeille. Le pic d’espace est la somme des deux fichiers jusqu’à la purge.', applyReplacer));
  $('mediaslim-add').addEventListener('click', addSlim);
  $('mediaslim-form').addEventListener('submit', saveMediaslim);
  $('seedrules-form').addEventListener('submit', saveSeedRule);
  $('seedthresholds-form').addEventListener('submit', saveSeedThresholds);
  $('seeds-refresh').addEventListener('click', () => loadSeeds().catch(showError));
  $('seeds-reannounce').addEventListener('click', reannounceSeeds);
  $('seeds-retire').addEventListener('click', (event) => confirmAction(event.currentTarget,
    'Les torrents cochés sont retirés de qBittorrent : ils cessent d’être partagés. Les fichiers restent, sauf si vous avez coché leur suppression (permise seulement quand chacun a un autre lien).', retireSeeds));
  $('llm-card').addEventListener('submit', saveLlm);
  $('llm-test').addEventListener('click', testLlm);
  $('llm-token-clear').addEventListener('click', clearLlmToken);
}
