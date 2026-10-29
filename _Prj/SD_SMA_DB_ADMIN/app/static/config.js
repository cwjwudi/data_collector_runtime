let configuredConnection = {};
let defaultOutputDir = '';

async function fetchJson(url, body) {
  const response = await fetch(url, body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || JSON.stringify(data));
  return data;
}

function showConnectionHint(text, ok = false) {
  const hint = document.getElementById('connectionHint');
  hint.textContent = text;
  hint.className = ok ? 'muted ok' : 'muted warn';
}

function getConnectionPayload() {
  return {
    engine: 'mysql',
    host: document.getElementById('dbHost').value.trim(),
    port: Number(document.getElementById('dbPort').value),
    username: document.getElementById('dbUsername').value.trim(),
    password: document.getElementById('dbPassword').value,
    database: configuredConnection.database || '',
  };
}

async function testConnection(save = false) {
  if (!document.getElementById('connectionForm').reportValidity()) return;
  const buttons = ['btnTestConnection', 'btnSaveConnection'].map(id => document.getElementById(id));
  buttons.forEach(button => { button.disabled = true; });
  try {
    const connection = getConnectionPayload();
    const result = await fetchJson('/api/connect/test', connection);
    if (!result.ok) throw new Error(result.message);
    if (save) {
      const saved = await fetchJson('/api/config/connection', connection);
      configuredConnection = saved.default_connection;
      document.getElementById('dbPassword').value = '';
    }
    showConnectionHint(SdI18n.t(save ? 'dbConfig.status.saved' : 'dbConfig.status.connected'), true);
  } catch (error) {
    showConnectionHint(SdI18n.t('dbConfig.status.failed', { reason: error.message }));
  } finally {
    buttons.forEach(button => { button.disabled = Boolean(configuredConnection.password_managed); });
  }
}

async function init() {
  const config = await fetchJson('/api/config');
  defaultOutputDir = config.backup_dir || '';
  document.getElementById('outputDir').value = config.output_dir || defaultOutputDir;
  document.getElementById('outputForm').addEventListener('submit', event => {
    event.preventDefault();
    saveOutputDirectory();
  });
  document.getElementById('btnChooseOutputDir').addEventListener('click', async () => {
    try {
      const input = document.getElementById('outputDir');
      const selected = await openFilesystemBrowser({ purpose: 'directory', initialPath: input.value.trim() });
      if (selected) input.value = selected;
    } catch (error) {
      showOutputHint(error.message);
    }
  });
  document.getElementById('btnUseDefaultOutputDir').addEventListener('click', () => {
    document.getElementById('outputDir').value = defaultOutputDir;
  });
  configuredConnection = config.default_connection;
  document.getElementById('dbHost').value = configuredConnection.host;
  document.getElementById('dbPort').value = configuredConnection.port;
  document.getElementById('dbUsername').value = configuredConnection.username;
  const managed = Boolean(configuredConnection.password_managed);
  document.getElementById('connectionFields').disabled = managed;
  document.getElementById('btnTestConnection').disabled = managed;
  document.getElementById('btnSaveConnection').disabled = managed;
  showConnectionHint(SdI18n.t(managed ? 'dbConfig.status.managed' :
    (configuredConnection.password_configured ? 'dbConfig.status.configured' : 'dbConfig.status.empty')), true);
  document.getElementById('connectionForm').addEventListener('submit', event => {
    event.preventDefault();
    testConnection(true);
  });
  document.getElementById('btnTestConnection').addEventListener('click', () => testConnection());
  document.getElementById('btnClearPassword').addEventListener('click', () => {
    document.getElementById('dbPassword').value = '';
  });
  document.getElementById('btnTogglePassword').addEventListener('click', () => {
    const input = document.getElementById('dbPassword');
    const button = document.getElementById('btnTogglePassword');
    input.type = input.type === 'password' ? 'text' : 'password';
    const key = input.type === 'password' ? 'dbConfig.actions.show' : 'dbConfig.actions.hide';
    button.setAttribute('data-i18n', key);
    button.setAttribute('data-i18n-aria', key);
    button.textContent = SdI18n.t(key);
    button.setAttribute('aria-label', SdI18n.t(key));
  });
}

async function saveOutputDirectory() {
  if (!document.getElementById('outputForm').reportValidity()) return;
  const button = document.getElementById('btnSaveOutputDir');
  button.disabled = true;
  try {
    const result = await fetchJson('/api/config/output', {
      output_dir: document.getElementById('outputDir').value.trim(),
    });
    document.getElementById('outputDir').value = result.output_dir;
    showOutputHint(SdI18n.t('dbConfig.output.saved'), true);
  } catch (error) {
    showOutputHint(SdI18n.t('dbConfig.output.failed', { reason: error.message }));
  } finally {
    button.disabled = false;
  }
}

function showOutputHint(text, ok = false) {
  const hint = document.getElementById('outputHint');
  hint.textContent = text;
  hint.className = ok ? 'muted ok' : 'muted warn';
}

init().catch(error => showConnectionHint(error.message));
