'use strict';

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'onclick') node.addEventListener('click', value);
    else if (value !== undefined && value !== null) node.setAttribute(key, value);
  }
  node.append(...children.flat().filter((c) => c !== null && c !== undefined));
  return node;
}

async function api(path, body) {
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
  const box = $('job');
  box.classList.remove('hidden');
  $('job-title').textContent = 'Erreur';
  $('job-log').textContent = error.message;
}

let jobTimer = null;
function followJob(id, done) {
  const box = $('job');
  box.classList.remove('hidden');
  box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  clearInterval(jobTimer);
  jobTimer = setInterval(async () => {
    try {
      const job = await api(`/api/jobs/${id}`);
      $('job-title').textContent = `${job.title} : ${{ running: 'en cours', ok: 'terminé', error: 'échec' }[job.state]}`;
      $('job-log').textContent = job.log || '...';
      if (job.state !== 'running') {
        clearInterval(jobTimer);
        if (done) done();
      }
    } catch (error) {
      // Traefik ou Authelia peuvent redémarrer pendant un changement d'adresse
      $('job-log').textContent += '\nConsole momentanément injoignable...';
    }
  }, 1500);
}

async function runJob(path, body, done) {
  try {
    const { job } = await api(path, body);
    followJob(job, done);
  } catch (error) {
    showError(error);
  }
}

function confirmAction(button, label, action) {
  // Double clic explicite plutôt qu'une boîte de dialogue du navigateur
  if (button.dataset.armed) {
    delete button.dataset.armed;
    button.textContent = button.dataset.label;
    action();
    return;
  }
  button.dataset.armed = '1';
  button.dataset.label = button.textContent;
  button.textContent = label;
  setTimeout(() => {
    if (button.dataset.armed) {
      delete button.dataset.armed;
      button.textContent = button.dataset.label;
    }
  }, 4000);
}

function statePill(app) {
  const running = app.state === 'running';
  const sick = app.health === 'unhealthy';
  const label = sick ? 'en difficulté' : running ? (app.health === 'starting' ? 'démarrage' : 'actif')
    : app.state === 'absent' ? 'non démarré' : app.state;
  return el('span', { class: `pill ${sick ? 'err' : running ? 'ok' : 'warn'}` }, label);
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
    el('td', {}, el('span', { class: `pill ${step.ok ? 'ok' : step.ignored ? 'warn' : 'err'}` }, step.ok ? 'OK' : step.ignored ? 'ignorée' : 'échec')),
    el('td', { class: 'muted small' }, step.error || ''),
    el('td', {}, !step.ok && !step.ignored && !step.critical ? el('button', {
      class: 'btn sm', type: 'button',
      onclick: () => runJob('/api/steps/ignore', { label: step.label }, loadSteps),
    }, 'Ignorer') : null)));
  $('steps-list').replaceChildren(...(rows.length ? rows : [el('tr', {}, el('td', { colspan: '4', class: 'muted' }, 'Aucune étape exécutée pour le moment.'))]));
}

async function loadApps() {
  loadSteps().catch(showError);
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
        event.target.disabled = true;
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
    const input = el('input', { type: 'password', minlength: '8', placeholder: 'Nouveau mot de passe', autocomplete: 'new-password' });
    const change = el('button', {
      class: 'btn sm', type: 'button',
      onclick: () => {
        if (input.value.length < 8) return showError(new Error('8 caractères au minimum'));
        return runJob(`/api/users/${encodeURIComponent(user.login)}/password`, { password: input.value }, () => { input.value = ''; });
      },
    }, 'Changer');
    const remove = user.login === session.user ? null : el('button', {
      class: 'btn sm danger', type: 'button',
      onclick: (event) => confirmAction(event.target, 'Confirmer ?', () => runJob(`/api/users/${encodeURIComponent(user.login)}/delete`, {}, loadUsers)),
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

const bytes = (value) => {
  if (!value) return '0 o';
  const units = ['o', 'Kio', 'Mio', 'Gio', 'Tio', 'Pio'];
  const power = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** power).toFixed(power > 1 ? 2 : 0)} ${units[power]}`;
};

async function loadTrackers() {
  const data = await api('/api/trackers');
  const rules = Object.entries(data.rules);
  $('rules-list').replaceChildren(...(rules.length ? rules.map(([keyword, rule]) => {
    const hours = el('input', { type: 'number', min: '0', value: rule.min_seed_hours ?? 0 });
    const ratio = el('input', { type: 'number', step: '0.1', min: '-1', value: rule.max_ratio ?? -1 });
    return el('tr', {}, el('td', {}, rule.name || keyword), el('td', {}, hours), el('td', {}, ratio),
      el('td', {}, el('button', {
        class: 'btn sm', type: 'button',
        onclick: () => runJob('/api/trackers/rule', { keyword, min_seed_hours: hours.value, max_ratio: ratio.value }, loadTrackers),
      }, 'Enregistrer')));
  }) : [el('tr', {}, el('td', { colspan: '4', class: 'muted' }, 'Aucun tracker : ajoutez un indexeur dans Prowlarr, puis synchronisez.'))]));
  $('stats-list').replaceChildren(...(data.stats.length ? data.stats.map((s) => el('tr', {},
    el('td', {}, s.tracker), el('td', {}, String(s.torrents)), el('td', {}, bytes(s.uploaded)), el('td', {}, bytes(s.downloaded)),
    el('td', {}, s.ratio === null ? '-' : String(s.ratio)), el('td', {}, `${bytes(s.upspeed)}/s`)))
    : [el('tr', {}, el('td', { colspan: '6', class: 'muted' }, 'Aucun torrent, ou qBittorrent arrêté.'))]));
}

async function runCheck() {
  const button = $('check-run');
  button.disabled = true;
  $('check-summary').textContent = 'Contrôle en cours…';
  try {
    const result = await api('/api/check');
    const marks = { ok: 'OK', warn: 'Alerte', fail: 'Échec' };
    const classes = { ok: 'ok', warn: 'warn', fail: 'err' };
    let group = null;
    const rows = [];
    for (const item of result.items) {
      if (item.group !== group) {
        group = item.group;
        rows.push(el('li', { class: 'group' }, group));
      }
      rows.push(el('li', {}, el('span', { class: `pill ${classes[item.status]}` }, marks[item.status]), ` ${item.label}`,
        item.detail ? el('span', { class: 'muted' }, ` : ${item.detail}`) : null));
    }
    $('check-list').replaceChildren(...rows);
    const s = result.summary;
    $('check-summary').textContent = `${s.ok} réussi(s), ${s.warn} alerte(s), ${s.fail} échec(s)`;
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

function showKindFields() {
  const kind = $('share-kind').value;
  for (const node of $('share-form').querySelectorAll('[data-kinds]')) {
    node.classList.toggle('hidden', !node.dataset.kinds.split(' ').includes(kind));
  }
  $('share-source').placeholder = kind === 'nfs' ? '192.168.1.10:/volume1/torrents' : '//192.168.1.10/torrents';
  const versions = (sharesData && (kind === 'nfs' ? sharesData.nfs_versions : sharesData.smb_versions)) || [''];
  $('share-version').replaceChildren(...versions.map((v) => el('option', { value: v }, v || 'automatique (conseillé)')));
}

function shareAction(name, action, label) {
  return el('button', {
    class: `btn sm${action === 'remove' ? ' danger' : ''}`, type: 'button',
    onclick: (event) => {
      const go = () => runJob(`/api/shares/${name}/${action}`, {}, loadShares);
      return action === 'remove' ? confirmAction(event.target, 'Confirmer ?', go) : go();
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
    el('td', {}, el('span', { class: `pill ${share.mounted ? 'ok' : 'err'}` }, share.mounted ? 'monté' : 'absent')),
    el('td', {}, share.mounted ? `${bytes(share.free)} / ${bytes(share.total)}` : '-'),
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
      share.roles.length ? null : shareAction(share.name, 'remove', 'Supprimer')))))
    : [el('tr', {}, el('td', { colspan: '6', class: 'muted' }, 'Aucun partage : ajoutez-en un ci-dessous.'))]));
  const mounted = data.shares.filter((s) => s.mounted);
  $('roles-list').replaceChildren(...data.roles.map((role) => {
    const choice = el('select', {},
      el('option', { value: '' }, role.local ? 'Stockage local' : 'Désactivé'),
      ...mounted.map((s) => el('option', { value: s.name }, `Partage ${s.name}`)));
    choice.value = role.share;
    const sub = el('input', { value: role.path, placeholder: role.path });
    const toggle = () => { sub.disabled = !choice.value; };
    choice.addEventListener('change', toggle);
    toggle();
    return el('tr', {}, el('td', {}, role.label), el('td', {}, choice), el('td', {}, sub),
      el('td', { class: 'secret' }, role.location || '-'),
      el('td', {}, el('button', {
        class: 'btn sm', type: 'button',
        onclick: () => runJob('/api/shares/role', { role: role.role, share: choice.value, path: sub.value }, loadShares),
      }, 'Appliquer')));
  }));
  $('share-disk').replaceChildren(...(data.disks.length ? data.disks.map((d) => el('option', { value: d.uuid },
    `${d.device} - ${d.label || 'sans nom'} - ${d.fstype} - ${bytes(d.size)}`))
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
        onclick: (event) => confirmAction(event.target, 'Confirmer ?', () => runJob(`/api/rotate/${rotation[name]}`, {})),
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

const NEXT_FORMAT = new Intl.DateTimeFormat('fr-FR', { dateStyle: 'full', timeStyle: 'short' });
function nextRun(value) {
  // systemd renvoie « Sun 2026-11-08 03:30:00 CET » : date et heure suffisent
  const match = /(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})/.exec(value || '');
  return match ? `Prochain passage : ${NEXT_FORMAT.format(new Date(`${match[1]}T${match[2]}`))}` : 'Aucun passage planifié';
}

function fill(form, values) {
  for (const [key, value] of Object.entries(values)) {
    const input = form.elements[key];
    if (!input) continue;
    if (input.type === 'checkbox') input.checked = !!value; else input.value = value;
  }
}

const STATE_LABEL = { ok: ['OK', 'ok'], warn: ['À vérifier', 'warn'], fail: ['Anomalie', 'err'] };

async function loadAudit() {
  $('audit-score').textContent = 'Audit en cours...';
  const data = await api('/api/security');
  const { ok, warn, fail } = data.score;
  $('audit-score').textContent = `${ok} contrôle(s) conforme(s), ${warn} à vérifier, ${fail} anomalie(s)`;
  $('audit-list').replaceChildren(...data.checks.map((c) => el('tr', {},
    el('td', {}, c.label), el('td', {}, el('span', { class: `pill ${STATE_LABEL[c.state][1]}` }, STATE_LABEL[c.state][0])),
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

function applianceDate(text) {
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' });
}

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
      el('dd', {}, `${check.remote.short} du ${applianceDate(check.remote.date)} : ${check.remote.message}`),
      el('dt', {}, 'État'), el('dd', {}, check.available ? 'mise à jour disponible' : check.reason),
      el('dt', {}, 'Contrôle'), el('dd', {}, applianceDate(check.checked * 1000)));
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
  $('appliance-apply').textContent = running ? 'En cours...' : check && check.available ? 'Mettre à jour' : 'Réinstaller la dernière version';
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
    el('td', {}, el('span', { class: `pill ${u.second_factor ? 'ok' : 'warn'}` }, u.second_factor ? 'enregistrée' : 'aucune')))));
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
  fillNested($('alerts-form'), data.settings);
  const rows = data.active.map((a) => el('li', {}, el('span', { class: `pill ${LEVEL_CLASS[a.level] || 'warn'}` }, a.notified ? 'signalé' : 'constaté'),
    ` ${a.title}`, a.detail ? el('span', { class: 'muted' }, ` : ${a.detail}`) : null));
  $('alerts-active').replaceChildren(...(rows.length ? rows : [el('li', { class: 'muted' }, 'Aucun problème constaté.')]));
}

let viewTimer = null;
let netPrev = null;
const fmtRate = (bytes) => `${fmtSize(Math.max(0, bytes))}/s`;
const fmtDuration = (s) => { const d = Math.floor(s / 86400); const h = Math.floor((s % 86400) / 3600); const m = Math.floor((s % 3600) / 60); return `${d ? `${d} j ` : ''}${h} h ${m} min`; };

async function refreshSystem() {
  const data = await api('/api/system');
  $('sys-up').textContent = `En marche depuis ${fmtDuration(data.uptime)}`;
  const cpu = Math.min(100, Math.round((data.load[0] / data.cpus) * 100));
  $('sys-cpu').value = cpu;
  $('sys-cpu-t').textContent = `Charge ${data.load.map((v) => v.toFixed(2)).join(' / ')} sur ${data.cpus} cœur(s) : ${cpu} %`;
  const m = data.memory;
  $('sys-mem').value = m.total ? Math.round((100 * m.used) / m.total) : 0;
  $('sys-mem-t').textContent = `${fmtSize(m.used)} utilisés sur ${fmtSize(m.total)}${m.swap_total ? ` ; swap ${fmtSize(m.swap_used)} / ${fmtSize(m.swap_total)}` : ''}`;
  const n = data.network;
  if (netPrev && netPrev.interface === n.interface && data.time > netPrev.time) {
    const dt = data.time - netPrev.time;
    $('sys-net').textContent = `↓ ${fmtRate((n.rx - netPrev.rx) / dt)}  ↑ ${fmtRate((n.tx - netPrev.tx) / dt)}`;
  } else $('sys-net').textContent = 'mesure en cours…';
  netPrev = { ...n, time: data.time };
  $('sys-net-t').textContent = n.interface ? `Interface ${n.interface} : ${fmtSize(n.rx)} reçus, ${fmtSize(n.tx)} émis depuis le démarrage` : '';
  $('sys-disks').replaceChildren(...data.disks.map((d) => el('div', { class: 'disk' },
    el('div', { class: 'row' }, el('strong', {}, d.label), el('span', { class: 'muted small' }, `${d.path} : ${fmtSize(d.used)} / ${fmtSize(d.total)} (${d.percent} %)`)),
    el('progress', { max: '100', value: String(d.percent), class: d.percent >= 95 ? 'crit' : d.percent >= 85 ? 'warn' : '' }))));
  const smart = data.smart;
  $('sys-smart').replaceChildren(...(smart.available
    ? (smart.disks.length ? smart.disks.map((d) => el('li', {}, el('span', { class: `pill ${!d.supported ? 'warn' : d.passed ? 'ok' : 'err'}` }, !d.supported ? 'SMART indisponible' : d.passed ? 'SMART OK' : 'SMART : alerte'),
      ` /dev/${d.name} ${d.model || ''}`, d.supported ? el('span', { class: 'muted' }, ` ${d.temperature != null ? `${d.temperature} °C` : ''}${d.hours != null ? ` · ${d.hours} h de fonctionnement` : ''}`) : null))
      : [el('li', { class: 'muted' }, 'Aucun disque physique détecté (machine virtuelle ?).')])
    : [el('li', { class: 'muted' }, smart.reason)]));
}

async function refreshContainers() {
  const data = await api('/api/system/containers');
  $('sys-containers').replaceChildren(...data.containers.map((c) => el('tr', {},
    el('td', {}, c.name), el('td', {}, `${c.cpu.toFixed(1)} %`), el('td', {}, `${fmtSize(c.memory)} (${c.memory_percent.toFixed(1)} %)`), el('td', { class: 'muted small' }, c.net))));
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

function fmtSize(bytes) {
  if (bytes >= 1 << 30) return `${(bytes / (1 << 30)).toFixed(1)} Go`;
  if (bytes >= 1 << 20) return `${(bytes / (1 << 20)).toFixed(1)} Mo`;
  return `${Math.max(1, Math.round(bytes / 1024))} Ko`;
}

async function loadBackup() {
  const data = await api('/api/backup');
  fill($('backup-form'), data.settings);
  renderDays($('backup-days'), data.settings.days);
  $('backup-warn').classList.toggle('hidden', data.destination_ok);
  $('backup-warn').textContent = `Le partage « ${data.settings.destination} » n'est pas monté : la sauvegarde automatique échouera tant qu'il est absent.`;
  const rows = data.backups.map((b) => el('tr', {},
    el('td', {}, new Date(b.time * 1000).toLocaleString('fr-FR'), b.name.includes('-avant') ? el('span', { class: 'muted small' }, ' (avant restauration)') : null),
    el('td', { class: 'muted' }, b.where === 'local' ? 'ce serveur' : b.where),
    el('td', { class: 'muted' }, fmtSize(b.size)),
    el('td', {}, el('div', { class: 'row' },
      el('a', { class: 'btn sm', href: `api/backup/download?name=${encodeURIComponent(b.name)}` }, 'Télécharger'),
      el('button', { class: 'btn sm', type: 'button', onclick: (event) => confirmAction(event.target, 'Confirmer la restauration ?',
        () => runJob('/api/backup/restore', { name: b.name }, loadBackup)) }, 'Restaurer'),
      el('button', { class: 'btn sm danger', type: 'button', onclick: (event) => confirmAction(event.target, 'Confirmer ?',
        () => runJob('/api/backup/delete', { name: b.name }, loadBackup)) }, 'Supprimer')))));
  $('backup-list').replaceChildren(...(rows.length ? rows : [el('tr', {}, el('td', { colspan: '4', class: 'muted' }, 'Aucune sauvegarde pour le moment.'))]));
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
      ? el('button', { class: 'btn sm danger', type: 'button', onclick: (event) => confirmAction(event.target, 'Confirmer le retrait ?',
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
  if (!rows.length) nodes.push(el('tr', {}, el('td', { colspan: '5', class: 'muted' }, 'Aucune application.')));
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
      ? el('span', { class: 'pill ok' }, 'installé')
      : el('button', { class: 'btn sm primary', type: 'button', onclick: () => runJob('/api/catalog/pack', { id: pack.id }, loadCatalog) }, 'Installer le pack'))));
  $('preset-card').classList.toggle('hidden', !catalogData.preset);
  if (catalogData.preset) $('preset-form').elements.value.value = catalogData.preset;
}

async function loadCatalog() {
  catalogData = await api('/api/catalog');
  const when = catalogData.fetched ? new Date(catalogData.fetched * 1000).toLocaleString('fr-FR') : 'jamais';
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
        actions.push(choice, el('button', { class: 'btn sm danger', type: 'button', onclick: (event) => confirmAction(event.target, 'Revenir à cette version ?',
          () => runJob('/api/versions/rollback', { service: svc.service, to: choice.value }, loadVersions)) }, 'Revenir'));
      }
      if (svc.pinned) actions.push(el('button', { class: 'btn sm', type: 'button', onclick: () => runJob('/api/versions/unpin', { service: svc.service }, loadVersions) }, 'Libérer'));
    }
    return el('tr', {},
      el('td', {}, svc.service, svc.pinned ? el('span', { class: 'pill warn' }, 'épinglé') : null, svc.running ? null : el('span', { class: 'pill err' }, 'arrêté')),
      el('td', { class: 'small' }, svc.version || svc.id),
      el('td', { class: 'small muted' }, (svc.created || '').slice(0, 10) || '—'),
      el('td', { class: 'small muted' }, svc.local ? 'image construite sur place' : svc.previous.length ? `${svc.previous.length}` : 'aucune'),
      el('td', {}, el('div', { class: 'row' }, ...actions)));
  });
  $('versions-list').replaceChildren(...rows);
}

const VIEWS = { system: async () => { await loadSystem(); loadHistory().catch(showError); loadGpu().catch(showError); }, alerts: loadAlerts, backup: async () => { await loadBackup(); loadOffsite().catch(showError); loadImport().catch(showError); }, catalog: loadCatalog, settings: loadSettings, apps: loadApps, users: loadUsers, trackers: loadTrackers, shares: loadShares, vpn: loadVpn, domain: async () => { await loadApps(); await loadRemote(); }, p2p: loadP2p, secrets: null,
  guests: loadGuests, cleanup: loadCleanup, journal: () => loadJournal() };

function route() {
  const name = location.hash.slice(1) in VIEWS ? location.hash.slice(1) : 'apps';
  for (const section of document.querySelectorAll('.view')) section.classList.add('hidden');
  $(`view-${name}`).classList.remove('hidden');
  for (const link of document.querySelectorAll('#nav a')) link.classList.toggle('active', link.hash === `#${name}`);
  clearInterval(viewTimer);
  clearInterval(containersTimer);
  clearInterval(featureTimer);
  if (VIEWS[name]) VIEWS[name]().catch(showError);
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
$('ts-off').addEventListener('click', (event) => confirmAction(event.target, 'Confirmer ?', () => runJob('/api/remote/tailscale', { mode: 'disable' }, loadRemote)));
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
$('upgrade-skip').addEventListener('click', (event) => confirmAction(event.target, 'Confirmer l’abandon ?', () => runJob('/api/steps/skip-upgrade', {}, loadSteps)));
$('versions-load').addEventListener('click', () => loadVersions().catch(showError));
$('appliance-check').addEventListener('click', async (event) => {
  event.target.disabled = true;
  event.target.textContent = 'Recherche...';
  try { renderAppliance(await api('/api/appliance/check', {})); } catch (error) { showError(error); }
  event.target.textContent = 'Rechercher une mise à jour';
  event.target.disabled = false;
});
$('appliance-apply').addEventListener('click', (event) => confirmAction(event.target, 'Confirmer la mise à jour ?', () => applianceAction('/api/appliance/apply', {})));
$('appliance-rollback').addEventListener('click', (event) => confirmAction(event.target, 'Confirmer le retour arrière ?', () => applianceAction('/api/appliance/rollback', {})));
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
$('share-kind').addEventListener('change', showKindFields);
$('share-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const data = formData(event.target);
  if (data.kind === 'disk') data.source = data.disk;
  delete data.disk;
  runJob('/api/shares', data, () => { event.target.reset(); loadShares(); });
});
$('share-credentials').addEventListener('submit', (event) => {
  event.preventDefault();
  const data = formData(event.target);
  runJob(`/api/shares/${data.name}/credentials`, data, () => { event.target.reset(); loadShares(); });
});
$('deploy').addEventListener('click', () => runJob('/api/deploy', {}, loadApps));
$('trackers-sync').addEventListener('click', () => runJob('/api/trackers/sync', {}, loadTrackers));
$('vpn-disable').addEventListener('click', (event) => confirmAction(event.target, 'Confirmer ?', () => runJob('/api/vpn/disable', {}, loadVpn)));
$('p2p-update').addEventListener('click', () => runJob('/api/p2p/update', {}, loadP2p));
$('p2p-session').addEventListener('click', () => runJob('/api/p2p/session', {}, loadP2p));
$('secrets-show').addEventListener('click', async () => {
  try { renderSecrets(await api('/api/credentials')); } catch (error) { showError(error); }
});

initFeatures();
window.addEventListener('hashchange', route);
api('/api/session').then((session) => {
  $('who').textContent = session.user;
  $('version').textContent = session.version;
}).catch(showError);
route();

// Application installable (PWA) : l'interface reste ouverte sans réseau, les données non
function showOffline() { $('offline').classList.toggle('hidden', navigator.onLine); }
window.addEventListener('online', showOffline);
window.addEventListener('offline', showOffline);
showOffline();
if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch((error) => console.warn('service worker :', error.message));
