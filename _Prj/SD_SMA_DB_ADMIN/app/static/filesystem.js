async function openFilesystemBrowser({ purpose, initialPath = '', allowFiles = false }) {
  const overlay = document.getElementById('filesystem-modal-overlay');
  const title = document.getElementById('filesystem-modal-title');
  const pathLabel = document.getElementById('filesystem-modal-path');
  const list = document.getElementById('filesystem-modal-list');
  const cancel = document.getElementById('filesystem-modal-cancel');
  const select = document.getElementById('filesystem-modal-select');
  const refresh = document.getElementById('filesystem-modal-refresh');
  if (!overlay) throw new Error(SdI18n.t('dbAdmin.openFilesystemBrowser.text1'));
  let current = '';
  let removablePollTimer = null;
  let rootSignature = '';

  return new Promise((resolve, reject) => {
    const close = () => {
      if (removablePollTimer) clearInterval(removablePollTimer);
      removablePollTimer = null;
      overlay.style.display = 'none';
    };
    const finish = value => { close(); resolve(value); };
    const fail = error => { close(); reject(error); };
    cancel.onclick = () => finish('');
    overlay.onclick = event => { if (event.target === overlay) finish(''); };
    select.style.display = allowFiles ? 'none' : '';
    select.onclick = () => finish(current);
    refresh.style.display = purpose === 'removable-directory' ? '' : 'none';
    title.textContent = allowFiles
      ? SdI18n.t('dbAdmin.openFilesystemBrowser.text2', { value1: purpose.toUpperCase() })
      : (purpose === 'removable-directory' ? SdI18n.t('dbAdmin.openFilesystemBrowser.text3') : SdI18n.t('dbAdmin.openFilesystemBrowser.text4'));
    overlay.style.display = 'flex';

    const renderButton = (label, meta, onClick) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'filesystem-entry';
      const name = document.createElement('span');
      const detail = document.createElement('span');
      name.textContent = label;
      detail.textContent = meta;
      detail.className = 'muted';
      button.append(name, detail);
      button.onclick = onClick;
      list.appendChild(button);
    };
    const renderRoots = data => {
      current = '';
      select.disabled = true;
      pathLabel.textContent = SdI18n.t('dbAdmin.openFilesystemBrowser.text5');
      list.innerHTML = '';
      for (const root of data.roots || []) {
        renderButton(root.name, root.exists ? root.path : SdI18n.t('dbAdmin.openFilesystemBrowser.text6'), () => {
          if (root.exists) showDirectory(root.path).catch(fail);
        });
      }
      if (!(data.roots || []).some(root => root.exists)) {
        renderButton(SdI18n.t('dbAdmin.openFilesystemBrowser.text7'), SdI18n.t('dbAdmin.openFilesystemBrowser.text8'), () => {});
      }
    };
    const showRoots = async () => {
      const data = await fetchJson(`/api/filesystem/roots?purpose=${encodeURIComponent(purpose)}`);
      rootSignature = JSON.stringify((data.roots || []).map(root => [root.path, root.exists]));
      renderRoots(data);
    };
    const showDirectory = async path => {
      const data = await fetchJson(`/api/filesystem/entries?purpose=${encodeURIComponent(purpose)}&path=${encodeURIComponent(path)}`);
      current = data.current;
      select.disabled = false;
      pathLabel.textContent = current;
      list.innerHTML = '';
      if (data.parent) renderButton('..', SdI18n.t('dbAdmin.openFilesystemBrowser.text9'), () => showDirectory(data.parent).catch(fail));
      else renderButton(SdI18n.t('dbAdmin.openFilesystemBrowser.text10'), SdI18n.t('dbAdmin.openFilesystemBrowser.text11'), () => showRoots().catch(fail));
      for (const entry of data.entries || []) {
        const isDirectory = entry.type === 'directory';
        renderButton(entry.name, isDirectory ? SdI18n.t('dbAdmin.openFilesystemBrowser.text12') : entry.modified_at, () => {
          if (isDirectory) showDirectory(entry.path).catch(fail);
          else if (allowFiles) finish(entry.path);
        });
      }
    };
    const pollRemovableRoots = async () => {
      if (purpose !== 'removable-directory') return;
      const data = await fetchJson(`/api/filesystem/roots?purpose=${encodeURIComponent(purpose)}`);
      const nextSignature = JSON.stringify((data.roots || []).map(root => [root.path, root.exists]));
      if (nextSignature === rootSignature) return;
      rootSignature = nextSignature;
      const roots = (data.roots || []).filter(root => root.exists);
      const normalizedCurrent = current.toLowerCase();
      const currentStillAvailable = roots.some(root => normalizedCurrent.startsWith(root.path.toLowerCase()));
      if (!current || !currentStillAvailable) renderRoots(data);
    };
    refresh.onclick = () => showRoots().catch(fail);
    if (purpose === 'removable-directory') {
      removablePollTimer = setInterval(() => pollRemovableRoots().catch(() => {}), 1000);
    }
    (initialPath ? showDirectory(initialPath).catch(() => showRoots()) : showRoots()).catch(fail);
  });
}

