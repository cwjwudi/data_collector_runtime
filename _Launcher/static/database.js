const databaseErrorKeys = {
  unavailable: 'launcher.database.error.unavailable',
  permissions: 'launcher.database.error.permissions',
  authentication: 'launcher.database.error.authentication',
  database_missing: 'launcher.database.error.database_missing',
  authentication_plugin: 'launcher.database.error.authentication_plugin',
  tls: 'launcher.database.error.tls',
  tls_required: 'launcher.database.error.tls_required',
  authentication_or_tls: 'launcher.database.error.authentication_or_tls',
  client_tools: 'launcher.database.error.client_tools',
};

function databasePayload() {
  return {
    mode: $('databaseMode').value,
    external_credential_id: $('databaseProfile').value || undefined,
    credential: {
      host: $('credentialHost').value.trim(), port: Number($('credentialPort').value),
      database: $('credentialDatabase').value.trim(), username: $('credentialUsername').value.trim(),
      password: $('credentialPassword').value,
    },
  };
}

function databaseModeChanged() {
  const external = $('databaseMode').value === 'external';
  $('externalDatabaseFields').hidden = !external;
  $('externalDatabaseFields').disabled = !external;
  for (const id of ['credentialHost', 'credentialPort', 'credentialDatabase', 'credentialUsername']) $(id).required = external;
  $('credentialPassword').required = external && !$('databaseProfile').value;
}

function databaseProfileChanged() {
  const row = state.credentials.find(item => item.id === $('databaseProfile').value) || {};
  $('credentialHost').value = row.host || '';
  $('credentialPort').value = row.port || 3306;
  $('credentialDatabase').value = row.database || '';
  $('credentialUsername').value = row.username || 'root';
  $('credentialPassword').value = '';
  databaseModeChanged();
}

async function loadDatabaseManagement() {
  try {
    const [current, profiles] = await Promise.all([api('/api/launcher/database'), api('/api/launcher/credentials')]);
    state.database = current;
    state.credentials = profiles.credentials;
    const binding = current.binding;
    $('databaseCurrent').textContent = current.error || (current.legacy ? SdI18n.t('launcher.database.legacy') :
      binding?.mode === 'managed' ? SdI18n.t('launcher.database.managed') : SdI18n.t('launcher.database.external'));
    $('databaseTargets').replaceChildren();
    for (const [name, row] of Object.entries(current.targets)) {
      const line = document.createElement('p');
      line.textContent = `${name}: ${row.error || (row.target ? `${row.target.username}@${row.target.host}:${row.target.port}/${row.target.name}` : SdI18n.t('launcher.database.local'))}`;
      if (row.connected === false) line.textContent += ' · ' + SdI18n.t('launcher.database.error.unavailable');
      if (row.state === 'stopped' || row.state === 'failed') line.textContent += ' · ' + SdI18n.t('launcher.database.stopped');
      $('databaseTargets').append(line);
    }
    $('databaseMode').value = binding?.mode || (current.managed_installed ? 'managed' : 'external');
    const profile = $('databaseProfile');
    profile.replaceChildren(new Option(SdI18n.t('launcher.database.newProfile'), ''));
    for (const row of state.credentials) profile.append(new Option(row.name, row.id));
    profile.value = binding?.external_credential_id || '';
    databaseProfileChanged();
    const admin = state.status?.services.find(item => item.name === 'db_admin');
    $('databaseAdminLink').href = `${window.location.protocol}//${window.location.hostname}:${admin?.port || 8093}/admin`;
    if (current.last_result) showDatabaseResult(current.last_result);
  } catch (error) {
    $('databaseResult').textContent = error.message;
  }
}

function showDatabaseResult(result) {
  const lines = [result.ok ? SdI18n.t(result.stage === 'complete' ? 'launcher.database.applied' : 'launcher.database.tested') : SdI18n.t('launcher.database.failed')];
  if (result.error) lines.push(result.error);
  for (const [name, row] of Object.entries(result.services || {})) {
    const details = row.error ? SdI18n.t(databaseErrorKeys[row.error] || 'launcher.database.failed') : row.version || row.state || '';
    const missing = row.missing?.length ? SdI18n.t('launcher.database.missing', {value: row.missing.join(', ')}) : '';
    lines.push(`${name}: ${details} ${missing}`);
    if (row.tools) lines.push(...Object.values(row.tools));
  }
  if (!result.stage) lines.push(SdI18n.t('launcher.database.grantsHint'));
  if (result.rollback) lines.push(SdI18n.t(result.rollback.ok ? 'launcher.database.rolledBack' : 'launcher.database.rollbackFailed'));
  $('databaseResult').textContent = lines.join('\n');
}

async function runDatabaseAction(apply) {
  if (!$('databaseForm').reportValidity() || !await ensureAdmin()) return;
  const payload = databasePayload();
  if (apply) {
    const old = JSON.stringify(state.database?.targets || {});
    const target = payload.mode === 'managed' ? SdI18n.t('launcher.database.managed') : `${payload.credential.host}:${payload.credential.port}/${payload.credential.database}`;
    if (!await confirmAction(SdI18n.t('launcher.database.apply'), SdI18n.t('launcher.database.confirm', {old, target}))) return;
  }
  const buttons = ['databaseTest', 'databaseApply', 'databaseRefresh'].map($);
  buttons.forEach(button => {button.disabled = true;});
  try {
    const result = await api(`/api/launcher/database/${apply ? 'apply' : 'test'}`, {method: 'POST', body: JSON.stringify(payload)});
    if (apply) {
      if (result.ok) {$('credentialPassword').value = ''; toast(SdI18n.t('launcher.credentials.saved'));}
      await loadDatabaseManagement();
      await refreshStatus();
    }
    showDatabaseResult(result);
  } catch (error) {
    $('databaseResult').textContent = error.message;
  } finally {
    buttons.forEach(button => {button.disabled = false;});
  }
}

$('databaseMode').onchange = databaseModeChanged;
$('databaseProfile').onchange = databaseProfileChanged;
$('databaseTest').onclick = () => runDatabaseAction(false);
$('databaseRefresh').onclick = loadDatabaseManagement;
$('databaseForm').addEventListener('submit', event => {event.preventDefault(); runDatabaseAction(true);});
