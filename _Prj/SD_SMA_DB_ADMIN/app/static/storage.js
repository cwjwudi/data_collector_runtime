async function refreshManagedStorage() {
  const panel = document.getElementById('managedStoragePanel');
  const status = document.getElementById('managedStorageStatus');
  const details = document.getElementById('managedStorageDetails');
  const button = document.getElementById('refreshManagedStorage');
  button.disabled = true;
  try {
    const response = await fetch('/api/storage/status');
    if (!response.ok) throw new Error();
    const result = await response.json();
    panel.hidden = !result.managed && !result.external;
    if (panel.hidden) return;
    const connection = document.getElementById('connectionPanel');
    if (connection) connection.hidden = true;
    status.className = result.available ? 'muted ok' : 'muted warn';
    details.textContent = '';
    if (result.available) {
      status.textContent = SdI18n.t('dbStorage.status.online');
      details.textContent = SdI18n.t(result.external ? 'dbStorage.status.externalDetails' : 'dbStorage.status.details', {
        target: result.target,
        version: result.version,
        size: (result.size_bytes / 1048576).toFixed(1),
        free: (result.free_bytes / 1073741824).toFixed(1),
      });
    } else if (result.error === 'credentials') {
      status.textContent = SdI18n.t('dbStorage.status.credentials');
    } else if (result.error === 'disk') {
      status.textContent = SdI18n.t('dbStorage.status.disk');
    } else {
      status.textContent = SdI18n.t('dbStorage.status.unavailable');
    }
  } catch {
    panel.hidden = false;
    status.className = 'muted warn';
    status.textContent = SdI18n.t('dbStorage.status.requestFailed');
  } finally {
    button.disabled = false;
  }
}

document.getElementById('refreshManagedStorage').addEventListener('click', refreshManagedStorage);
refreshManagedStorage();
