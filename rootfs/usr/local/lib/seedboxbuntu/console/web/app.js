'use strict';
// SeedBoxBuntu - console : pages et actions. Le socle visuel (formats, états, notifications,
// tiroir de tâches, confirmation, navigation) est dans ui.js ; les pages récentes dans features.js.

async function api(path, body) {
  if (body !== undefined && readonlyMode() && path !== '/api/prefs') throw new Error(tr('Lecture seule : cette action est réservée aux administrateurs.'));
  const options = body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  };
  // Chemin relatif : la console est servie sous https://<adresse>/console/
  const response = await fetch(path.replace(/^\//, ''), options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function showError(error) {
  toast('err', (error && error.message) || String(error));
}

const JOB_STATE = { running: ['info', 'en cours'], ok: ['ok', 'terminé'], error: ['err', 'échec'] };
let latestJob = null;
// Une tâche longue : notification qui suit son avancement, détails dans le tiroir (ouvert d'office en cas d'échec)
function followJob(id, done) {
  latestJob = id;
  const note = toast('info', 'Tâche en cours…', { sticky: true, busy: true, action: { label: 'Détails', run: () => drawer.open() } });
  drawer.show('Tâche en cours', 'info', 'en cours', '');
  const poll = async () => {
    try {
      const job = await api(`/api/jobs/${id}`);
      const [kind, word] = JOB_STATE[job.state] || ['unknown', job.state];
      if (latestJob === id) drawer.show(job.title, kind, word, job.log);
      if (job.state === 'running') { note.update('info', `${job.title} : en cours…`, { sticky: true, busy: true }); return; }
      clearInterval(timer);
      note.update(kind, `${job.title} : ${word}`);
      if (job.state === 'error' && latestJob === id) drawer.open();
      if (done) done();
    } catch (error) {
      // Traefik ou Authelia peuvent redémarrer pendant un changement d'adresse
      if (latestJob === id) $('drawer-log').textContent += '\nConsole momentanément injoignable...';
    }
  };
  const timer = setInterval(poll, 1500);
  poll();
}

async function runJob(path, body, done) {
  try {
    const { job } = await api(path, body);
    followJob(job, done);
  } catch (error) {
    showError(error);
  }
}

function statePill(app) {
  const running = app.state === 'running';
  const sick = app.health === 'unhealthy';
  const label = sick ? 'en difficulté' : running ? (app.health === 'starting' ? 'démarrage' : 'actif')
    : app.state === 'absent' ? 'non démarré' : app.state === 'exited' ? 'arrêté' : app.state;
  return pill(label, sick ? 'err' : running ? 'ok' : 'warn');
}

const ROLE_LABEL = { admin: 'Administrateur', manager: 'Gestionnaire', user: 'Utilisateur' };
const LOGIN_LABEL = { sso: 'Connexion unique', comptes: 'Comptes de l’appliance', public: 'Publique' };

// Adresse saisie dans le navigateur conservée (IP, nom ou domaine) : chemin, ou port dédié
// pour les applications qui ne fonctionnent qu'à la racine d'une adresse
function appUrl(app) {
  return app.port ? `https://${location.hostname}:${app.port}/` : app.path;
}

async function loadSteps() {
  const data = await api('/api/steps');
  const up = data.upgrade;
  $('upgrade-box').classList.toggle('hidden', !up.pending);
  $('upgrade-log').classList.toggle('hidden', !up.pending);
  $('upgrade-skip').classList.toggle('hidden', !up.pending);
  if (up.pending) {
    $('upgrade-box').textContent = `Mise à jour en attente (${up.detail}) : service ${up.service}. Elle est relancée toutes les 2 minutes tant qu'une étape essentielle échoue.`;
    $('upgrade-log').textContent = up.log || '';
  }
  const rows = data.steps.map((step) => el('tr', {},
    el('td', {}, step.label, step.critical ? el('span', { class: 'muted small' }, ' (essentielle)') : null),
    el('td', {}, pill(step.ok ? 'OK' : step.ignored ? 'ignorée' : 'échec', step.ok ? 'ok' : step.ignored ? 'warn' : 'err')),
    el('td', { class: 'muted small' }, step.error || ''),
    el('td', {}, !step.ok && !step.ignored && !step.critical ? el('button', {
      class: 'btn sm', type: 'button',
      onclick: () => runJob('/api/steps/ignore', { label: step.label }, loadSteps),
    }, 'Ignorer') : null)));
  $('steps-list').replaceChildren(...(rows.length ? rows : [emptyRow(4, 'Aucune étape exécutée pour le moment.')]));
}

async function loadApps() {
  if (!readonlyMode()) loadSteps().catch(showError);
  api('/api/appliance').then((data) => renderAppliance(data)).catch(() => {});
  const status = await api('/api/status');
  const notice = $('apps-state');
  notice.classList.toggle('hidden', status.deployed);
  notice.textContent = 'Le déploiement n’est pas terminé : relancez la configuration si cet état persiste.';
  $('apps-list').replaceChildren(...status.apps.map((app) => el('tr', {},
    el('td', {}, el('a', { href: appUrl(app), target: '_blank', rel: 'noopener' }, app.name)),
    el('td', {}, statePill(app)),
    el('td', { class: 'muted' }, LOGIN_LABEL[app.login] || ''),
    el('td', {}, app.service === 'console' ? null : el('button', {
      class: 'btn sm', type: 'button',
      onclick: async (event) => {
        event.currentTarget.disabled = true;
        await api(`/api/services/${app.service}/restart`, {}).catch(showError);
        loadApps();
      },
    }, 'Redémarrer')))));
  const prowlarr = status.apps.find((a) => a.id === 'prowlarr');
  if (prowlarr) $('tracker-add').href = `${appUrl(prowlarr)}add`;
  const domain = $('domain-state');
  domain.replaceChildren(
    el('dt', {}, 'Adresse principale'), el('dd', {}, el('a', { href: `https://${status.domain}/` }, `https://${status.domain}/`)),
    el('dt', {}, 'Adresses acceptées'), el('dd', {}, (status.addresses || []).join(', ')),
    el('dt', {}, 'Mode'), el('dd', {}, status.domain_mode === 'auto' ? 'automatique (IP du serveur)' : 'nom personnalisé'),
    el('dt', {}, 'Certificat'), el('dd', {}, status.acme ? 'Let’s Encrypt (nom personnalisé) et autorité locale (IP)' : 'autorité locale'),
  );
}

async function loadUsers() {
  const { users } = await api('/api/users');
  const session = await api('/api/session');
  $('users-list').replaceChildren(...users.map((user) => {
    const input = el('input', { type: 'password', minlength: '8', placeholder: 'Nouveau mot de passe', autocomplete: 'new-password', 'aria-label': `Nouveau mot de passe de ${user.login}` });
    const change = el('button', {
      class: 'btn sm', type: 'button',
      onclick: () => {
        if (input.value.length < 8) return showError(new Error('8 caractères au minimum'));
        return runJob(`/api/users/${encodeURIComponent(user.login)}/password`, { password: input.value }, () => { input.value = ''; });
      },
    }, 'Changer');
    const remove = user.login === session.user ? null : el('button', {
      class: 'btn sm danger', type: 'button',
      onclick: (event) => confirmAction(event.currentTarget, `Le compte « ${user.login} » est supprimé de toutes les applications (connexion unique, Jellyfin, Seerr).`, () => runJob(`/api/users/${encodeURIComponent(user.login)}/delete`, {}, loadUsers)),
    }, 'Supprimer');
    const role = el('select', { 'aria-label': `Rôle de ${user.login}` },
      ...Object.entries(ROLE_LABEL).map(([value, label]) => el('option', { value, selected: value === user.role ? '' : null }, label)));
    role.value = user.role;
    const apply = el('button', { class: 'btn sm', type: 'button', onclick: () => {
      if (role.value !== user.role) runJob(`/api/users/${encodeURIComponent(user.login)}/role`, { role: role.value }, loadUsers);
    } }, 'Appliquer');
    return el('tr', {}, el('td', {}, user.login),
      el('td', {}, el('div', { class: 'row' }, role, apply)),
      el('td', {}, el('div', { class: 'row' }, input, change, remove)));
  }));
}

async function loadTrackers() {
  const data = await api('/api/trackers');
  const rules = Object.entries(data.rules);
  $('rules-list').replaceChildren(...(rules.length ? rules.map(([keyword, rule]) => {
    const hours = el('input', { type: 'number', min: '0', value: rule.min_seed_hours ?? 0, 'aria-label': `Durée minimale de partage en heures, ${rule.name || keyword}` });
    const ratio = el('input', { type: 'number', step: '0.1', min: '-1', value: rule.max_ratio ?? -1, 'aria-label': `Ratio maximal, ${rule.name || keyword}` });
    return el('tr', {}, el('td', {}, rule.name || keyword), el('td', {}, hours), el('td', {}, ratio),
      el('td', {}, el('button', {
        class: 'btn sm', type: 'button',
        onclick: () => runJob('/api/trackers/rule', { keyword, min_seed_hours: hours.value, max_ratio: ratio.value }, loadTrackers),
      }, 'Enregistrer')));
  }) : [emptyRow(4, 'Aucun tracker : ajoutez un indexeur dans Prowlarr, puis synchronisez.')]));
  $('stats-list').replaceChildren(...(data.stats.length ? data.stats.map((s) => el('tr', {},
    el('td', {}, s.tracker), el('td', {}, String(s.torrents)), el('td', {}, fmtSize(s.uploaded)), el('td', {}, fmtSize(s.downloaded)),
    el('td', {}, s.ratio === null ? '-' : String(s.ratio)), el('td', {}, `${fmtSize(s.upspeed)}/s`)))
    : [emptyRow(6, 'Aucun torrent, ou qBittorrent arrêté.')]));
}

async function runCheck() {
  const button = $('check-run');
  button.disabled = true;
  $('check-summary').textContent = 'Contrôle en cours…';
  try {
    const result = await api('/api/check');
    const marks = { ok: 'OK', warn: 'Attention', fail: 'Erreur' };
    const classes = { ok: 'ok', warn: 'warn', fail: 'err' };
    let group = null;
    const rows = [];
    for (const item of result.items) {
      if (item.group !== group) {
        group = item.group;
        rows.push(el('li', { class: 'group' }, group));
      }
      rows.push(el('li', {}, pill(marks[item.status], classes[item.status]), ` ${item.label}`,
        item.detail ? el('span', { class: 'muted' }, ` : ${item.detail}`) : null));
    }
    $('check-list').replaceChildren(...rows);
    const s = result.summary;
    $('check-summary').textContent = `${s.ok} OK, ${s.warn} à surveiller, ${s.fail} en erreur`;
  } catch (error) {
    $('check-summary').textContent = '';
    showError(error);
  } finally {
    button.disabled = false;
  }
}

async function loadVpn() {
  const vpn = await api('/api/vpn');
  $('vpn-state').replaceChildren(
    el('dt', {}, 'Mode'), el('dd', {}, vpn.enabled ? `VPN (${vpn.state || 'actif'})` : 'accès direct, sans VPN'),
    el('dt', {}, 'Fournisseur'), el('dd', {}, vpn.provider || '-'),
    el('dt', {}, 'Adresse de sortie'), el('dd', {}, vpn.enabled ? (vpn.ip || '-') : 'celle du serveur'),
    el('dt', {}, 'Port entrant'), el('dd', {}, !vpn.enabled ? String(vpn.port || '6881') : vpn.port_forwarding ? String(vpn.port || 'en attente') : 'désactivé'),
  );
}

async function loadP2p() {
  const p2p = await api('/api/p2p');
  const state = p2p.update_available === null ? 'à vérifier (jeton requis)' : p2p.update_available ? 'mise à jour disponible' : 'à jour';
  $('p2p-state').replaceChildren(
    el('dt', {}, 'Version'), el('dd', {}, `${p2p.version || '?'}${p2p.commit ? ` (${p2p.commit})` : ''}`),
    el('dt', {}, 'Dépôt'), el('dd', {}, p2p.error || state),
    el('dt', {}, 'Jeton GitHub'), el('dd', {}, p2p.token ? 'enregistré' : 'absent'),
  );
}

const KIND_LABEL = { smb: 'SMB', nfs: 'NFS', disk: 'Disque' };
let sharesData = null;
let foundShares = [];
const NEW_FOLDER = '__new__';

function showKindFields() {
  const kind = $('share-kind').value;
  for (const node of $('share-form').querySelectorAll('[data-kinds]')) {
    node.classList.toggle('hidden', !node.dataset.kinds.split(' ').includes(kind));
  }
  $('share-source').placeholder = kind === 'nfs' ? '192.168.1.10:/volume1/torrents' : '//192.168.1.10/torrents';
  $('share-found').classList.toggle('hidden', kind === 'disk' || !foundShares.length);
  $('share-discover-state').classList.toggle('hidden', kind === 'disk' || !$('share-discover-state').textContent);
  const versions = (sharesData && (kind === 'nfs' ? sharesData.nfs_versions : sharesData.smb_versions)) || [''];
  $('share-version').replaceChildren(...versions.map((v) => el('option', { value: v }, v || 'automatique (conseillé)')));
}


// Recherche des partages d'un serveur avec l'identifiant saisi, puis choix dans la liste
function shortName(text) {
  const clean = text.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]/g, '').slice(0, 20);
  return /^[a-z]/.test(clean) ? clean : (clean ? `p${clean}`.slice(0, 20) : '');
}
async function discoverShares() {
  const form = $('share-form');
  const state = $('share-discover-state');
  const kind = form.elements.kind.value;
  const host = form.elements.host.value.trim();
  if (!host) { toast('warn', 'Saisissez d’abord l’adresse ou le nom du serveur.'); return; }
  const button = $('share-discover');
  button.disabled = true;
  state.textContent = 'Recherche en cours...';
  state.classList.remove('hidden');
  try {
    const answer = await api('/api/shares/discover', { kind, host, username: form.elements.username.value, password: form.elements.password.value,
      domain: form.elements.domain.value, version: form.elements.version.value });
    foundShares = answer.shares;
    $('share-pick').replaceChildren(el('option', { value: '' }, `${foundShares.length} trouvé(s) : choisissez`),
      ...foundShares.map((share, index) => el('option', { value: String(index) }, share.comment ? `${share.name} · ${share.comment}` : share.name)));
    $('share-found').classList.remove('hidden');
    state.textContent = `${foundShares.length} partage(s) trouvé(s) sur ${host}.`;
    if (foundShares.length === 1) { $('share-pick').value = '0'; pickShare(); }
  } catch (error) {
    foundShares = [];
    $('share-found').classList.add('hidden');
    state.textContent = error.message || String(error);
  } finally { button.disabled = false; }
}
function pickShare() {
  const share = foundShares[Number($('share-pick').value)];
  if (!share) return;
  $('share-source').value = share.source;
  const name = $('share-form').elements.name;
  if (!name.value) name.value = shortName(share.name.split('/').filter(Boolean).pop() || share.name);
}

function shareAction(name, action, label) {
  return el('button', {
    class: `btn sm${action === 'remove' ? ' danger' : ''}`, type: 'button',
    onclick: (event) => {
      const go = () => runJob(`/api/shares/${name}/${action}`, {}, loadShares);
      return action === 'remove' ? confirmAction(event.currentTarget, `Le partage « ${name} » est retiré ; les fichiers qu’il contient ne sont pas effacés.`, go) : go();
    },
  }, label);
}

async function loadShares() {
  const data = await api('/api/shares');
  sharesData = data;
  const labels = Object.fromEntries(data.roles.map((r) => [r.role, r.label]));
  const notes = $('shares-notes');
  notes.classList.toggle('hidden', !data.warnings.length);
  notes.replaceChildren(...data.warnings.map((w) => el('div', {}, w)));
  $('shares-list').replaceChildren(...(data.shares.length ? data.shares.map((share) => el('tr', {},
    el('td', {}, share.name, el('div', { class: 'muted small' }, KIND_LABEL[share.kind] + (share.version ? ` ${share.version}` : '')
      + (share.username ? ` - ${share.username}` : ''))),
    el('td', { class: 'secret' }, share.kind === 'disk' ? `${share.label || share.fstype} (${share.fstype})` : share.source),
    el('td', {}, pill(share.mounted ? 'monté' : 'absent', share.mounted ? 'ok' : 'err')),
    el('td', {}, share.mounted ? `${fmtSize(share.free)} / ${fmtSize(share.total)}` : '-'),
    el('td', { class: 'small' }, share.roles.map((r) => labels[r]).join(', ') || '-'),
    el('td', {}, el('div', { class: 'row' },
      share.mounted ? shareAction(share.name, 'umount', 'Démonter') : shareAction(share.name, 'mount', 'Monter'),
      share.kind === 'smb' ? el('button', {
        class: 'btn sm', type: 'button',
        onclick: () => {
          $('share-credentials').classList.remove('hidden');
          $('credentials-share').value = share.name;
          $('share-credentials').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        },
      }, 'Identifiants') : null,
      share.roles.length ? null : shareAction(share.name, 'remove', 'Retirer')))))
    : [emptyRow(6, 'Aucun partage : ajoutez-en un ci-dessous.')]));
  const mounted = data.shares.filter((s) => s.mounted);
  $('roles-list').replaceChildren(...data.roles.map((role) => {
    const choice = el('select', { 'aria-label': `Emplacement : ${role.label}` },
      el('option', { value: '' }, role.local ? 'Stockage local' : 'Désactivé'),
      ...mounted.map((s) => el('option', { value: s.name }, `Partage ${s.name}`)));
    choice.value = role.share;
    // Sous-dossier : liste des dossiers réellement présents sur le partage choisi (+ « Nouveau dossier » pour en créer un)
    const sub = el('select', { 'aria-label': `Sous-dossier : ${role.label}` });
    const custom = el('input', { class: 'hidden', placeholder: role.path, 'aria-label': `Nouveau dossier : ${role.label}` });
    const chosenPath = () => (sub.value === NEW_FOLDER ? custom.value.trim() : sub.value);
    const showCustom = () => custom.classList.toggle('hidden', sub.disabled || sub.value !== NEW_FOLDER);
    const fillFolders = async () => {
      const share = choice.value;
      sub.replaceChildren();
      if (!share) { sub.disabled = true; showCustom(); return; }
      sub.disabled = false;
      let existing = [];
      try { existing = (await api(`/api/shares/folders?share=${encodeURIComponent(share)}`)).folders; } catch (error) { showError(error); }
      if (choice.value !== share) return;      // l'utilisateur a changé de partage pendant la lecture
      const wanted = role.path;
      const options = existing.map((folder) => el('option', { value: folder }, folder));
      if (wanted && !existing.includes(wanted)) options.unshift(el('option', { value: wanted }, `${wanted} (à créer)`));
      sub.replaceChildren(...options, el('option', { value: NEW_FOLDER }, 'Nouveau dossier...'));
      sub.value = wanted || (existing[0] || NEW_FOLDER);
      showCustom();
    };
    choice.addEventListener('change', () => { fillFolders(); });
    sub.addEventListener('change', showCustom);
    fillFolders();
    return el('tr', {}, el('td', {}, role.label), el('td', {}, choice), el('td', {}, sub, custom),
      el('td', { class: 'secret' }, role.location || '-'),
      el('td', {}, el('button', {
        class: 'btn sm', type: 'button',
        onclick: () => runJob('/api/shares/role', { role: role.role, share: choice.value, path: chosenPath() }, loadShares),
      }, 'Appliquer')));
  }));
  $('share-disk').replaceChildren(...(data.disks.length ? data.disks.map((d) => el('option', { value: d.uuid },
    `${d.device} - ${d.label || 'sans nom'} - ${d.fstype} - ${fmtSize(d.size)}`))
    : [el('option', { value: '' }, 'Aucun disque libre détecté')]));
  const smb = data.shares.filter((s) => s.kind === 'smb');
  $('credentials-share').replaceChildren(...smb.map((s) => el('option', { value: s.name }, s.name)));
  if (!smb.length) $('share-credentials').classList.add('hidden');
  showKindFields();
}

function renderSecrets(values) {
  const rotation = { qBittorrent: 'qbittorrent', autobrr: 'autobrr', Radarr: 'radarr', Sonarr: 'sonarr', Prowlarr: 'prowlarr' };
  $('secrets-list').replaceChildren(...Object.entries(values).map(([name, items]) => {
    const rows = Object.entries(items).flatMap(([label, value]) => [
      el('dt', {}, label), el('dd', { class: 'secret' }, value || (label === 'mot de passe' ? 'changé par vous' : '-'))]);
    return el('div', { class: 'card' }, el('h2', {}, name), el('dl', { class: 'kv' }, ...rows),
      rotation[name] ? el('button', {
        class: 'btn sm', type: 'button',
        onclick: (event) => confirmAction(event.currentTarget, 'Le secret est remplacé et les applications qui en dépendent sont reconfigurées.', () => runJob(`/api/rotate/${rotation[name]}`, {})),
      }, 'Renouveler') : null);
  }));
}

const DAYS = [['mon', 'Lun'], ['tue', 'Mar'], ['wed', 'Mer'], ['thu', 'Jeu'], ['fri', 'Ven'], ['sat', 'Sam'], ['sun', 'Dim']];

function renderDays(container, value) {
  const chosen = value === 'daily' ? DAYS.map(([id]) => id) : value.split(',');
  container.replaceChildren(...DAYS.map(([id, label]) => el('label', {},
    el('input', { type: 'checkbox', name: `day-${id}`, ...(chosen.includes(id) ? { checked: '' } : {}) }), label)));
}

function readDays(container) {
  const chosen = DAYS.map(([id]) => id).filter((id) => container.querySelector(`[name=day-${id}]`).checked);
  if (!chosen.length) throw new Error('Choisissez au moins un jour');
  return chosen.length === 7 ? 'daily' : chosen.join(',');
}

function nextRun(value) {
  // systemd renvoie « Sun 2026-11-08 03:30:00 CET » : date et heure suffisent
  const match = /(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})/.exec(value || '');
  return match ? `Prochain passage : ${fmtFull(`${match[1]}T${match[2]}`)}` : 'Aucun passage planifié';
}

function fill(form, values) {
  for (const [key, value] of Object.entries(values)) {
    const input = form.elements[key];
    if (!input) continue;
    if (input.type === 'checkbox') input.checked = !!value; else input.value = value;
  }
}

const STATE_LABEL = { ok: ['OK', 'ok'], warn: ['Attention', 'warn'], fail: ['Erreur', 'err'] };

async function loadAudit() {
  $('audit-score').textContent = 'Audit en cours...';
  const data = await api('/api/security');
  const { ok, warn, fail } = data.score;
  $('audit-score').textContent = `${ok} contrôle(s) conforme(s), ${warn} à vérifier, ${fail} anomalie(s)`;
  $('audit-list').replaceChildren(...data.checks.map((c) => el('tr', {},
    el('td', {}, c.label), el('td', {}, pill(STATE_LABEL[c.state][0], STATE_LABEL[c.state][1])),
    el('td', { class: 'muted small' }, c.detail || ''))));
  const web = data.ports.base.web.join(', ');
  const extra = [...data.ports.extra.tcp.map(String), ...data.ports.extra.udp.map((p) => `${p}/udp`)];
  $('ports-base').replaceChildren(
    el('dt', {}, 'Accès au serveur'), el('dd', {}, `SSH : ${data.ports.base.ssh}`),
    el('dt', {}, 'Applications'), el('dd', {}, `${web} (Traefik)`),
    el('dt', {}, 'Ajoutés'), el('dd', {}, extra.join(', ') || 'aucun'));
  if (document.activeElement !== $('ports-extra')) $('ports-extra').value = extra.join(', ');
}

// --- Mise à jour de l'appliance depuis GitHub -------------------------------------------------------
let applianceTimer = null;
let applianceWasRunning = false;
const APPLIANCE_STATE = { running: 'en cours', rolled_back: 'annulée automatiquement', ok: 'terminée', partial: 'terminée, finalisation en arrière-plan', error: 'échec' };

function renderAppliance(data) {
  const { installed, check, status, config } = data;
  const running = status.state === 'running';
  const rows = [
    el('dt', {}, 'Version installée'),
    el('dd', {}, `${installed.version}${installed.commit ? ` (commit ${installed.commit.slice(0, 7)}${installed.source === 'console' ? ', installé depuis la console' : ', installé par l’ISO'})` : ' (ISO)'}`),
    el('dt', {}, 'Source'), el('dd', {}, `${config.repo} (${config.channel === 'stable' ? 'dernière version publiée' : config.ref})${data.token ? '' : ' - sans jeton GitHub'}`),
  ];
  if (check && check.error) rows.push(el('dt', {}, 'Dernier contrôle'), el('dd', {}, check.error));
  else if (check && check.remote) {
    rows.push(el('dt', {}, 'Dernière version'),
      el('dd', {}, `${check.remote.short} du ${fmtDateTime(check.remote.date)} : ${check.remote.message}`),
      el('dt', {}, 'État'), el('dd', {}, check.available ? 'mise à jour disponible' : check.reason),
      el('dt', {}, 'Contrôle'), el('dd', {}, fmtDateTime(check.checked * 1000)));
  } else rows.push(el('dt', {}, 'État'), el('dd', {}, 'pas encore vérifié'));
  if (status.state) rows.push(el('dt', {}, 'Dernière opération'),
    el('dd', {}, `${status.action === 'rollback' ? 'retour arrière' : 'mise à jour'} ${APPLIANCE_STATE[status.state] || status.state}${status.phase && status.state !== 'error' ? ` : ${status.phase}` : ''}${status.error ? ` : ${status.error}` : ''}`));
  $('appliance-state').replaceChildren(...rows);
  const changes = (check && check.available && check.changes) || [];
  $('appliance-changes').classList.toggle('hidden', !changes.length);
  $('appliance-changes-list').replaceChildren(...changes.map((line) => el('li', {}, line)));
  const notice = $('appliance-notice');
  const pendingText = data.pending && !running ? 'Une mise à jour de la stack est en attente : elle se termine en arrière-plan (nouvel essai toutes les 2 minutes).' : '';
  notice.textContent = running ? 'Mise à jour en cours : cette console redémarre à la fin, la page se recharge ensuite.' : pendingText;
  notice.classList.toggle('hidden', !(running || pendingText));
  const log = $('appliance-log');
  log.textContent = data.log || '';
  log.classList.toggle('hidden', !(running || (data.log && status.state && status.state !== 'ok')));
  log.scrollTop = log.scrollHeight;
  $('appliance-check').disabled = running;
  $('appliance-apply').disabled = running;
  $('appliance-rollback').disabled = running || !data.can_rollback;
  setLabel($('appliance-apply'), running ? 'En cours…' : check && check.available ? 'Mettre à jour' : 'Réinstaller la dernière version');
  if (document.activeElement !== $('appliance-repo')) $('appliance-repo').value = config.repo;
  if (document.activeElement !== $('appliance-ref')) $('appliance-ref').value = config.ref;
  const banner = $('appliance-banner');
  banner.classList.toggle('hidden', !(check && check.available));
  if (check && check.available) $('appliance-banner-text').textContent = `Mise à jour de l’appliance disponible (${check.remote ? check.remote.short : ''}).`;
  renderApplianceExtras(data);
  return running;
}

async function loadAppliance() {
  const data = await api('/api/appliance');
  const running = renderAppliance(data);
  clearInterval(applianceTimer);
  if (running) {
    applianceWasRunning = true;
    applianceTimer = setInterval(async () => {
      try {
        const now = await api('/api/appliance');
        if (!renderAppliance(now)) {
          clearInterval(applianceTimer);
          // La console a redémarré : recharger l'interface pour obtenir les nouveaux fichiers
          if (applianceWasRunning && now.status.state !== 'error') setTimeout(() => location.reload(), 2500);
          applianceWasRunning = false;
        }
      } catch (error) {
        $('appliance-notice').classList.remove('hidden');
        $('appliance-notice').textContent = 'Console momentanément injoignable (redémarrage en cours)...';
      }
    }, 2000);
  }
}

async function applianceAction(path, body) {
  try {
    renderAppliance(await api(path, body));
    loadAppliance().catch(showError);
  } catch (error) { showError(error); }
}

async function loadSettings() {
  loadAppliance().catch(showError);
  const data = await api('/api/settings/updates');
  const { security, apps } = data.settings;
  $('sec-scope').replaceChildren(...Object.entries(data.scopes).map(([id, label]) => el('option', { value: id }, label)));
  fill($('upd-security'), security);
  fill($('upd-apps'), apps);
  renderDays($('sec-days'), security.days);
  renderDays($('apps-days'), apps.days);
  $('sec-next').textContent = security.enabled ? nextRun(data.next.security) : 'Mises à jour automatiques désactivées';
  $('apps-next').textContent = apps.enabled ? nextRun(data.next.apps) : 'Mises à jour automatiques désactivées';
  const notice = $('reboot-notice');
  notice.classList.toggle('hidden', !data.reboot_required);
  notice.textContent = `Un redémarrage du serveur est nécessaire pour terminer l'installation de correctifs${data.reboot_packages.length ? ` (${data.reboot_packages.join(', ')})` : ''}.`;
  loadAudit().catch(showError);
  loadAuth().catch(showError);
}

async function loadAuth() {
  const data = await api('/api/auth');
  $('auth-form').elements.mode.value = data.mode;
  $('auth-users').replaceChildren(...data.users.map((u) => el('tr', {},
    el('td', {}, u.login), el('td', { class: 'muted' }, (ROLE_LABEL[u.role] || u.role).toLowerCase()),
    el('td', {}, pill(u.second_factor ? 'enregistrée' : 'aucune', u.second_factor ? 'ok' : 'warn')))));
  $('auth-notif').textContent = data.notifications || 'Aucun code de vérification en attente.';
}

function nestedForm(form) {
  const out = {};
  for (const element of form.elements) {
    if (!element.name) continue;
    const value = element.type === 'checkbox' ? element.checked : element.type === 'number' ? Number(element.value) : element.value;
    const [head, tail] = element.name.split('.');
    if (tail) { out[head] = out[head] || {}; out[head][tail] = value; } else out[head] = value;
  }
  return out;
}

function fillNested(form, values) {
  for (const element of form.elements) {
    if (!element.name) continue;
    const [head, tail] = element.name.split('.');
    const value = tail ? (values[head] || {})[tail] : values[head];
    if (value === undefined) continue;
    if (element.type === 'checkbox') element.checked = !!value; else element.value = value;
  }
}

const LEVEL_CLASS = { critical: 'err', warning: 'warn', ok: 'ok' };
async function loadAlerts() {
  const data = await api('/api/alerts');
  if (data.settings) fillNested($('alerts-form'), data.settings);
  const rows = data.active.map((a) => el('li', {}, pill(a.notified ? 'signalé' : 'constaté', LEVEL_CLASS[a.level] || 'warn'),
    ` ${a.title}`, a.detail ? el('span', { class: 'muted' }, ` : ${a.detail}`) : null));
  $('alerts-active').replaceChildren(...(rows.length ? rows : [emptyItem('Aucun problème constaté.')]));
}

let viewTimer = null;
let netPrev = null;

async function refreshSystem() {
  const data = await api('/api/system');
  $('sys-up').textContent = `En marche depuis ${fmtDuration(data.uptime)}`;
  const cpu = Math.min(100, Math.round((data.load[0] / data.cpus) * 100));
  const m = data.memory;
  const mem = m.total ? Math.round((100 * m.used) / m.total) : 0;
  $('sys-cpu-g').replaceChildren(gauge(cpu, fmtPercent(cpu), `Charge ${data.load.map((v) => fmtNumber(v, 2)).join(' / ')} sur ${data.cpus} cœur(s)`));
  $('sys-mem-g').replaceChildren(gauge(mem, fmtPercent(mem), `${fmtSize(m.used)} utilisés sur ${fmtSize(m.total)}${m.swap_total ? ` ; swap ${fmtSize(m.swap_used)} / ${fmtSize(m.swap_total)}` : ''}`));
  const n = data.network;
  if (netPrev && netPrev.interface === n.interface && data.time > netPrev.time) {
    const dt = data.time - netPrev.time;
    $('sys-net').textContent = `↓ ${fmtRate((n.rx - netPrev.rx) / dt)}  ↑ ${fmtRate((n.tx - netPrev.tx) / dt)}`;
  } else $('sys-net').textContent = 'mesure en cours…';
  netPrev = { ...n, time: data.time };
  $('sys-net-t').textContent = n.interface ? `Interface ${n.interface} : ${fmtSize(n.rx)} reçus, ${fmtSize(n.tx)} émis depuis le démarrage` : '';
  $('sys-disks').replaceChildren(...data.disks.map((d) => gauge(d.percent, d.label, `${d.path} : ${fmtSize(d.used)} / ${fmtSize(d.total)} (${fmtPercent(d.percent)})`)));
  const smart = data.smart;
  $('sys-smart').replaceChildren(...(smart.available
    ? (smart.disks.length ? smart.disks.map((d) => el('li', {}, pill(!d.supported ? 'SMART indisponible' : d.passed ? 'SMART OK' : 'SMART : alerte', !d.supported ? 'warn' : d.passed ? 'ok' : 'err'),
      ` /dev/${d.name} ${d.model || ''}`, d.supported ? el('span', { class: 'muted' }, ` ${d.temperature != null ? `${d.temperature} °C` : ''}${d.hours != null ? ` · ${d.hours} h de fonctionnement` : ''}`) : null))
      : [emptyItem('Aucun disque physique détecté (machine virtuelle ?).')])
    : [el('li', { class: 'muted' }, smart.reason)]));
}

async function refreshContainers() {
  const data = await api('/api/system/containers');
  $('sys-containers').replaceChildren(...data.containers.map((c) => el('tr', {},
    el('td', {}, c.name), el('td', {}, `${fmtNumber(c.cpu)} %`), el('td', {}, `${fmtSize(c.memory)} (${fmtNumber(c.memory_percent)} %)`), el('td', { class: 'muted small' }, c.net))));
}

async function loadLog() {
  const source = $('log-source').value;
  if (!source) return;
  const data = await api(`/api/system/logs?source=${encodeURIComponent(source)}&lines=${$('log-lines').value}`);
  const box = $('log-view');
  box.textContent = data.text || '(journal vide)';
  box.scrollTop = box.scrollHeight;
}

async function loadSystem() {
  netPrev = null;
  await refreshSystem();
  refreshContainers().catch(showError);
  if (readonlyMode()) { clearInterval(viewTimer); viewTimer = setInterval(() => { refreshSystem().catch(() => {}); }, 5000); setInterval_containers(); return; }
  const sources = await api('/api/system/sources');
  const previous = $('log-source').value;
  $('log-source').replaceChildren(
    ...sources.containers.map((n) => el('option', { value: `c:${n}` }, `conteneur ${n}`)),
    ...sources.host.map((n) => el('option', { value: `h:${n}` }, `service ${n}`)));
  if (previous) $('log-source').value = previous;
  loadLog().catch(showError);
  clearInterval(viewTimer);
  viewTimer = setInterval(() => { refreshSystem().catch(() => {}); }, 5000);
  setInterval_containers();
}

let containersTimer = null;
function setInterval_containers() {
  clearInterval(containersTimer);
  containersTimer = setInterval(() => { refreshContainers().catch(() => {}); }, 15000);
}

async function loadRemote() {
  const data = await api('/api/remote');
  $('hosts-form').elements.value.value = data.extra_hosts || '';
  const t = data.tailscale;
  const rows = [['État', !t.enabled ? 'désactivé' : !t.running ? 'conteneur arrêté' : t.state === 'Running' ? 'connecté' : t.state === 'NeedsLogin' ? 'connexion à valider' : t.state || 'démarrage']];
  if (t.live_dns_name) rows.push(['Adresse', t.live_dns_name]);
  if (t.ips && t.ips.length) rows.push(['Adresses Tailscale', t.ips.join(', ')]);
  const nodes = rows.flatMap(([k, v]) => [el('dt', {}, k), el('dd', {}, v)]);
  if (t.login_url) nodes.push(el('dt', {}, 'Connexion'), el('dd', {}, el('a', { href: t.login_url, target: '_blank', rel: 'noopener' }, 'Valider cette machine sur Tailscale')));
  $('ts-state').replaceChildren(...nodes);
  $('ts-form').elements.hostname.value = t.hostname || 'seedbox';
  const d = data.dns;
  $('dns-provider').replaceChildren(...Object.entries(d.providers).map(([id, label]) => el('option', { value: id }, label)));
  if (d.provider) $('dns-provider').value = d.provider;
  $('dns-state').textContent = !d.custom_domain ? 'Nom de domaine et e-mail Let\u2019s Encrypt requis (formulaire ci-dessus).'
    : d.provider ? `Actif avec ${d.provider} : variables enregistrées (${d.variables.join(', ') || 'aucune'}).` : 'Défi HTTP (port 80) en service.';
}

async function loadBackup() {
  const data = await api('/api/backup');
  fill($('backup-form'), data.settings);
  renderDays($('backup-days'), data.settings.days);
  $('backup-warn').classList.toggle('hidden', data.destination_ok);
  $('backup-warn').textContent = `Le partage « ${data.settings.destination} » n'est pas monté : la sauvegarde automatique échouera tant qu'il est absent.`;
  const rows = data.backups.map((b) => el('tr', {},
    el('td', {}, fmtDateTime(b.time), b.name.includes('-avant') ? el('span', { class: 'muted small' }, ' (avant restauration)') : null),
    el('td', { class: 'muted' }, b.where === 'local' ? 'ce serveur' : b.where),
    el('td', { class: 'muted' }, fmtSize(b.size)),
    el('td', {}, el('div', { class: 'row' },
      el('a', { class: 'btn sm', href: `api/backup/download?name=${encodeURIComponent(b.name)}` }, 'Télécharger'),
      el('button', { class: 'btn sm', type: 'button', onclick: (event) => confirmAction(event.currentTarget, `La configuration est remplacée par celle de « ${b.name} » ; l’état actuel est sauvegardé avant.`,
        () => runJob('/api/backup/restore', { name: b.name }, loadBackup)) }, 'Restaurer'),
      el('button', { class: 'btn sm danger', type: 'button', onclick: (event) => confirmAction(event.currentTarget, `La sauvegarde « ${b.name} » est supprimée définitivement.`,
        () => runJob('/api/backup/delete', { name: b.name }, loadBackup)) }, 'Supprimer')))));
  $('backup-list').replaceChildren(...(rows.length ? rows : [emptyRow(4, 'Aucune sauvegarde pour le moment.')]));
  loadSandbox(data.backups);
}

let catalogData = null;
function renderCatalog() {
  if (!catalogData) return;
  const words = $('catalog-search').value.toLowerCase().split(/\s+/).filter(Boolean);
  const rows = catalogData.apps.filter((app) => !words.length
    || words.every((w) => `${app.name} ${app.id} ${app.category} ${app.description}`.toLowerCase().includes(w)));
  let category = null;
  const nodes = [];
  for (const app of rows.slice(0, 300)) {
    const heading = app.source === 'linuxserver' ? app.category : `${app.category} — ${app.source === 'intégré' ? 'reliées à la seedbox' : 'votre catalogue'}`;
    if (heading !== category) {
      category = heading;
      nodes.push(el('tr', {}, el('th', { colspan: '5', class: 'muted small' }, category)));
    }
    const origin = app.source === 'intégré' ? 'intégré (relié)' : app.source === 'linuxserver' ? 'linuxserver.io (générique)' : 'votre catalogue';
    const button = app.installed
      ? el('button', { class: 'btn sm danger', type: 'button', onclick: (event) => confirmAction(event.currentTarget, `« ${app.name} » est arrêtée et retirée de la seedbox.`,
        () => runJob('/api/catalog/remove', { id: app.id }, loadCatalog)) }, 'Retirer')
      : el('button', { class: 'btn sm primary', type: 'button', onclick: () => runJob('/api/catalog/install', { id: app.id }, loadCatalog) }, 'Installer');
    nodes.push(el('tr', {},
      el('td', {}, app.installed && !app.headless ? el('a', { href: `${app.path}/`, target: '_blank', rel: 'noopener' }, app.name) : app.name),
      el('td', { class: 'small' }, app.description),
      el('td', { class: 'muted small' }, origin),
      el('td', {}, app.installed ? statePill(app) : null),
      el('td', {}, button)));
  }
  if (rows.length > 300) nodes.push(el('tr', {}, el('td', { colspan: '5', class: 'muted' }, `${rows.length - 300} autres : affinez la recherche.`)));
  if (!rows.length) nodes.push(emptyRow(5, 'Aucune application.'));
  $('catalog-list').replaceChildren(...nodes);
}

function renderPacks() {
  const packs = (catalogData && catalogData.packs) || [];
  $('packs-card').classList.toggle('hidden', !packs.length);
  $('packs-list').replaceChildren(...packs.map((pack) => el('div', { class: 'pack' },
    el('h3', {}, pack.name),
    el('p', { class: 'small muted' }, pack.description),
    el('p', { class: 'small' }, pack.apps.map((a) => `${a.name}${a.installed ? ' ✓' : ''}`).join(' · ')),
    pack.complete
      ? pill('installé', 'ok')
      : el('button', { class: 'btn sm primary', type: 'button', onclick: () => runJob('/api/catalog/pack', { id: pack.id }, loadCatalog) }, 'Installer le pack'))));
  $('preset-card').classList.toggle('hidden', !catalogData.preset);
  if (catalogData.preset) $('preset-form').elements.value.value = catalogData.preset;
}

async function loadCatalog() {
  catalogData = await api('/api/catalog');
  const when = catalogData.fetched ? fmtDateTime(catalogData.fetched) : 'jamais';
  const sources = Object.entries(catalogData.sources || {}).map(([name, detail]) => `${name} : ${detail}`).join(' ; ');
  $('catalog-state').textContent = `Liste actualisée : ${when}${sources ? ` (${sources})` : ''}.`
    + (catalogData.rejected.length ? ` ${catalogData.rejected.length} entrée(s) de votre catalogue refusée(s) : ${catalogData.rejected.slice(0, 3).join(' ; ')}` : '');
  $('catalog-url-form').elements.url.value = catalogData.catalog_url || '';
  renderPacks();
  renderCatalog();
}

let versionsOpen = false;
async function loadVersions() {
  const data = await api('/api/versions');
  versionsOpen = true;
  $('versions-wrap').classList.remove('hidden');
  const rows = data.services.map((svc) => {
    const choice = el('select', { 'aria-label': `Version de ${svc.service} à rétablir` },
      ...svc.previous.map((p) => el('option', { value: p.id }, `${p.version || p.id} (${(p.created || '').slice(0, 10)})`)));
    const actions = [];
    if (!svc.local) {
      actions.push(el('button', { class: 'btn sm', type: 'button', onclick: () => runJob('/api/versions/update', { service: svc.service }, loadVersions) }, 'Mettre à jour'));
      if (svc.previous.length) {
        actions.push(choice, el('button', { class: 'btn sm danger', type: 'button', onclick: (event) => confirmAction(event.currentTarget, `« ${svc.service} » revient à la version choisie ; il est ensuite épinglé (plus de mise à jour automatique).`,
          () => runJob('/api/versions/rollback', { service: svc.service, to: choice.value }, loadVersions)) }, 'Revenir'));
      }
      if (svc.pinned) actions.push(el('button', { class: 'btn sm', type: 'button', onclick: () => runJob('/api/versions/unpin', { service: svc.service }, loadVersions) }, 'Libérer'));
    }
    return el('tr', {},
      el('td', {}, svc.service, svc.pinned ? pill('épinglé', 'warn') : null, svc.running ? null : pill('arrêté', 'err')),
      el('td', { class: 'small' }, svc.version || svc.id),
      el('td', { class: 'small muted' }, (svc.created || '').slice(0, 10) || '—'),
      el('td', { class: 'small muted' }, svc.local ? 'image construite sur place' : svc.previous.length ? `${svc.previous.length}` : 'aucune'),
      el('td', {}, el('div', { class: 'row' }, ...actions)));
  });
  $('versions-list').replaceChildren(...rows);
}

const VIEWS = { overview: loadOverview, styleguide: loadStyleguide, system: async () => { await loadSystem(); loadHistory().catch(showError); loadResources().catch(showError); if (!readonlyMode()) loadGpu().catch(showError); }, alerts: async () => { await loadAlerts(); loadMessageLanguage().catch(showError); loadPush().catch(showError); }, backup: async () => { await loadBackup(); loadOffsite().catch(showError); loadImport().catch(showError); loadBackupDiff().catch(showError); loadRestoredrill().catch(showError); }, catalog: loadCatalog, settings: async () => { await loadSettings(); loadWindow().catch(showError); loadLlm().catch(showError); }, apps: async () => { await loadApps(); loadRollout().catch(showError); }, users: async () => { await loadUsers(); loadEmergency().catch(showError); }, trackers: async () => { await loadTrackers(); loadRatio().catch(showError); loadTrackerstats().catch(showError); }, shares: loadShares, vpn: async () => { await loadVpn(); loadVpnwatch().catch(showError); }, domain: async () => { await loadApps(); await loadRemote(); loadNetwork().catch(showError); }, p2p: loadP2p, aggregator: loadAggregator, secrets: async () => { await loadRotation(); loadAppPasswords().catch(showError); },
  guests: async () => { await loadGuests(); loadTitleshare().catch(showError); loadPeoplereport().catch(showError); loadGuestquota().catch(showError); }, cleanup: loadCleanup, journal: () => loadJournal(), setup: loadSetup, report: loadReport,
  activity: loadActivity, vulns: loadVulns, netcheck: loadNetcheck, vacation: loadVacation,
  disks: async () => { await loadDisks(); loadDiskusage().catch(showError); }, changes: loadChanges, quality: loadQuality, library: loadLibrary, care: loadCare, upgrades: loadUpgrades, seeds: loadSeeds, maison: loadMaison, bandwidth: loadBandwidth, diagnose: loadDiagnose, medialist: loadMedialist, telecommande: loadTelecommande };

function route() {
  const requested = location.hash.slice(1);
  // #kiosk : la vue d'ensemble en grand, sans menu ; tout autre chemin quitte le mode kiosque
  if (requested === 'kiosk' || requested === 'maison') enterKiosk(); else exitKiosk();
  const target = requested === 'kiosk' ? 'overview' : requested;
  const name = target in VIEWS && (!readonlyMode() || OBSERVER_VIEWS.includes(target)) ? target : 'overview';
  setCustomizing(false);
  for (const section of document.querySelectorAll('.view')) section.classList.add('hidden');
  const view = $(`view-${name}`);
  view.classList.remove('hidden');
  setActiveNav(name);
  document.title = `${tr(NAV.flatMap((g) => g.items).find((i) => i.view === name).label)} - SeedBoxBuntu`;
  clearInterval(viewTimer);
  clearInterval(containersTimer);
  clearInterval(featureTimer);
  window.scrollTo(0, 0);
  if (!VIEWS[name]) return;
  markLoading(view);
  Promise.resolve().then(() => VIEWS[name]()).then(() => markLoaded(view), (error) => { markLoaded(view, error); showError(error); });
}

function formData(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  for (const box of form.querySelectorAll('input[type=checkbox]')) data[box.name] = box.checked;
  return data;
}

function bindForm(id, path, done) {
  $(id).addEventListener('submit', (event) => {
    event.preventDefault();
    const form = event.target;
    runJob(path, formData(form), () => { form.reset(); if (done) done(); });
  });
}

function saveUpdates(section, form, days) {
  return (event) => {
    event.preventDefault();
    try {
      const values = formData(form);
      const payload = { enabled: values.enabled, time: values.time, days: readDays(days) };
      for (const key of ['scope', 'reboot', 'reboot_time', 'p2p']) if (key in values) payload[key] = values[key];
      runJob('/api/settings/updates', { [section]: payload }, loadSettings);
    } catch (error) { showError(error); }
  };
}

$('upd-security').addEventListener('submit', saveUpdates('security', $('upd-security'), $('sec-days')));
$('upd-apps').addEventListener('submit', saveUpdates('apps', $('upd-apps'), $('apps-days')));
$('sec-run').addEventListener('click', () => runJob('/api/updates/run', { what: 'security' }, loadSettings));
$('apps-run').addEventListener('click', () => runJob('/api/updates/run', { what: 'apps' }, loadSettings));
$('auth-form').addEventListener('submit', (event) => {
  event.preventDefault();
  runJob('/api/auth/2fa', formData(event.target), loadAuth);
});
$('audit-reload').addEventListener('click', () => loadAudit().catch(showError));
$('audit-apply').addEventListener('click', () => runJob('/api/security/apply', {}, loadAudit));
$('ports-form').addEventListener('submit', (event) => {
  event.preventDefault();
  runJob('/api/security/ports', formData(event.target), loadAudit);
});
bindForm('user-form', '/api/users', loadUsers);
bindForm('vpn-form', '/api/vpn', loadVpn);
$('check-run').addEventListener('click', runCheck);
$('alerts-form').addEventListener('submit', (event) => {
  event.preventDefault();
  runJob('/api/alerts/settings', nestedForm(event.target), loadAlerts);
});
$('alerts-test').addEventListener('click', () => runJob('/api/alerts/test', {}, loadAlerts));
$('log-refresh').addEventListener('click', () => loadLog().catch(showError));
$('log-source').addEventListener('change', () => loadLog().catch(showError));
$('log-lines').addEventListener('change', () => loadLog().catch(showError));
$('hosts-form').addEventListener('submit', (event) => {
  event.preventDefault();
  runJob('/api/remote/hosts', { value: event.target.elements.value.value }, loadRemote);
});
$('ts-form').addEventListener('submit', (event) => {
  event.preventDefault();
  runJob('/api/remote/tailscale', { mode: 'set', hostname: event.target.elements.hostname.value, key: event.target.elements.key.value }, () => { event.target.elements.key.value = ''; loadRemote(); });
});
$('ts-off').addEventListener('click', (event) => confirmAction(event.currentTarget, 'L’accès distant par Tailscale s’arrête.', () => runJob('/api/remote/tailscale', { mode: 'disable' }, loadRemote)));
$('dns-form').addEventListener('submit', (event) => {
  event.preventDefault();
  runJob('/api/remote/dns', { mode: 'set', provider: $('dns-provider').value, variables: $('dns-vars').value }, () => { $('dns-vars').value = ''; loadRemote(); });
});
$('dns-off').addEventListener('click', () => runJob('/api/remote/dns', { mode: 'disable' }, loadRemote));
$('backup-create').addEventListener('click', () => runJob('/api/backup/create', {}, loadBackup));
$('backup-form').addEventListener('submit', (event) => {
  event.preventDefault();
  try {
    const values = formData(event.target);
    runJob('/api/backup/settings', { enabled: values.enabled, days: readDays($('backup-days')), time: values.time,
      keep: Number(values.keep), destination: values.destination.trim() }, loadBackup);
  } catch (error) { showError(error); }
});
$('backup-key-show').addEventListener('click', async () => {
  try { $('backup-key').textContent = (await api('/api/backup/key')).key; } catch (error) { showError(error); }
});
$('steps-retry').addEventListener('click', () => runJob('/api/steps/retry', {}, loadSteps));
$('upgrade-skip').addEventListener('click', (event) => confirmAction(event.currentTarget, 'La mise à jour en attente est abandonnée : elle ne sera plus réessayée.', () => runJob('/api/steps/skip-upgrade', {}, loadSteps)));
$('versions-load').addEventListener('click', () => loadVersions().catch(showError));
$('appliance-check').addEventListener('click', async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  setLabel(button, 'Recherche…');
  try { renderAppliance(await api('/api/appliance/check', {})); } catch (error) { showError(error); }
  setLabel(button, 'Rechercher une mise à jour');
  button.disabled = false;
});
$('appliance-apply').addEventListener('click', (event) => confirmAction(event.currentTarget, 'La nouvelle version est installée sans redémarrer le serveur ; cette console redémarre à la fin. Un retour arrière reste possible.', () => applianceAction('/api/appliance/apply', {})));
$('appliance-rollback').addEventListener('click', (event) => confirmAction(event.currentTarget, 'La version précédente de l’appliance est rétablie ; cette console redémarre.', () => applianceAction('/api/appliance/rollback', {})));
$('appliance-source-save').addEventListener('click', async () => {
  try {
    const token = $('appliance-token').value.trim();
    if (token) { await api('/api/p2p/token', { token }); $('appliance-token').value = ''; }
    renderAppliance(await api('/api/appliance/source', { repo: $('appliance-repo').value.trim(), ref: $('appliance-ref').value.trim() }));
  } catch (error) { showError(error); }
});
$('preset-form').addEventListener('submit', (event) => {
  event.preventDefault();
  runJob('/api/catalog/preset', { value: event.target.elements.value.value }, loadCatalog);
});
$('catalog-search').addEventListener('input', renderCatalog);
$('catalog-refresh').addEventListener('click', () => runJob('/api/catalog/refresh', {}, loadCatalog));
$('catalog-url-form').addEventListener('submit', (event) => {
  event.preventDefault();
  runJob('/api/catalog/url', { url: new FormData(event.target).get('url') },
    () => runJob('/api/catalog/refresh', {}, loadCatalog));
});
bindForm('domain-form', '/api/domain', loadApps);
$('token-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api('/api/p2p/token', formData(event.target));
    event.target.reset();
    loadP2p();
  } catch (error) { showError(error); }
});
$('share-kind').addEventListener('change', () => { foundShares = []; $('share-discover-state').textContent = ''; showKindFields(); });
$('share-discover').addEventListener('click', discoverShares);
$('share-pick').addEventListener('change', pickShare);
$('share-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const data = formData(event.target);
  if (data.kind === 'disk') data.source = data.disk;
  delete data.disk;
  runJob('/api/shares', data, () => { event.target.reset(); foundShares = []; $('share-found').classList.add('hidden'); $('share-discover-state').textContent = ''; $('share-discover-state').classList.add('hidden'); loadShares(); });
});
$('share-credentials').addEventListener('submit', (event) => {
  event.preventDefault();
  const data = formData(event.target);
  runJob(`/api/shares/${data.name}/credentials`, data, () => { event.target.reset(); loadShares(); });
});
$('deploy').addEventListener('click', () => runJob('/api/deploy', {}, loadApps));
$('trackers-sync').addEventListener('click', () => runJob('/api/trackers/sync', {}, loadTrackers));
$('vpn-disable').addEventListener('click', (event) => confirmAction(event.currentTarget, 'qBittorrent repasse en accès direct : son trafic ne passe plus par le tunnel VPN.', () => runJob('/api/vpn/disable', {}, loadVpn)));
$('p2p-update').addEventListener('click', () => runJob('/api/p2p/update', {}, loadP2p));
$('p2p-session').addEventListener('click', () => runJob('/api/p2p/session', {}, loadP2p));
$('secrets-show').addEventListener('click', async () => {
  try { renderSecrets(await api('/api/credentials')); } catch (error) { showError(error); }
});

async function start() {
  try {
    const session = await loadSession();
    for (const node of document.querySelectorAll('.who')) node.textContent = session.user;
    $('version').textContent = session.version;
    if (adoptAccountLanguage()) return;
  } catch (error) { showError(error); }
  buildNav();
  initFeatures();
  initPages();
  initExtras();
  initOps();
  initOps2();
  initOps3();
  initOps4();
  initOps5();
  initNetwork();
  watchDecorations();
  window.addEventListener('hashchange', route);
  route();
}
start();

// Application installable (PWA) : l'interface reste ouverte sans réseau, les données non
function showOffline() { $('offline').classList.toggle('hidden', navigator.onLine); }
window.addEventListener('online', showOffline);
window.addEventListener('offline', showOffline);
showOffline();
if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch((error) => console.warn('service worker :', error.message));
