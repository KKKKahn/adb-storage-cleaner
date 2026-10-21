'use strict';

(function () {
  const TOKEN_NODE = document.querySelector('meta[name="klean-token"]');
  const TOKEN = TOKEN_NODE ? TOKEN_NODE.content : '';
  const state = {
    data: null,
    kind: 'all',
    selected: new Set(),
    loading: false,
  };

  function $(id) { return document.getElementById(id); }

  function formatBytes(value) {
    const number = Number(value) || 0;
    if (number <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let result = number;
    let index = 0;
    while (result >= 1000 && index < units.length - 1) {
      result /= 1000;
      index += 1;
    }
    return (index === 0 ? result.toFixed(0) : result.toFixed(2)) + ' ' + units[index];
  }

  function basename(path) {
    return String(path || '').split('/').pop();
  }

  function formatSessionName(name) {
    const match = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(String(name));
    if (!match) return name;
    return match[1] + '-' + match[2] + '-' + match[3] + ' ' +
      match[4] + ':' + match[5] + ':' + match[6];
  }

  function toastMessage(message, isError) {
    if (typeof window.toast === 'function') {
      window.toast(message, isError);
      return;
    }
    const node = $('toast');
    if (!node) return;
    node.textContent = message;
    node.className = 'toast' + (isError ? ' err' : '');
    window.clearTimeout(toastMessage.timer);
    toastMessage.timer = window.setTimeout(function () {
      node.className = 'toast hidden';
    }, isError ? 6000 : 3200);
  }

  async function api(path, options) {
    const opts = options || {};
    const headers = Object.assign({}, opts.headers || {});
    headers['X-Klean-Token'] = TOKEN;
    if (opts.body) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, {
      method: opts.method || 'GET',
      headers: headers,
      body: opts.body,
    });
    const text = await response.text();
    let data = {};
    if (text) {
      try { data = JSON.parse(text); } catch (error) { data = {}; }
    }
    if (!response.ok) throw new Error(data.error || ('请求失败 (' + response.status + ')'));
    return data;
  }

  function entryKey(kind, backup) {
    return kind + '|' + backup;
  }

  function allSessions() {
    if (!state.data) return [];
    const rows = (state.data.sessions || []).concat(state.data.video_sessions || []);
    rows.sort(function (a, b) { return b.session.localeCompare(a.session); });
    return rows;
  }

  function visibleSessions() {
    return allSessions().filter(function (session) {
      return state.kind === 'all' || session.kind === state.kind;
    });
  }

  function visibleEntries() {
    const rows = [];
    visibleSessions().forEach(function (session) {
      session.entries.forEach(function (entry) { rows.push(entry); });
    });
    return rows;
  }

  function selectedSize() {
    let total = 0;
    allSessions().forEach(function (session) {
      session.entries.forEach(function (entry) {
        if (state.selected.has(entryKey(session.kind, entry.backup))) {
          total += entry.original_size;
        }
      });
    });
    return total;
  }

  function injectMarkup() {
    document.body.insertAdjacentHTML('beforeend', [
      '<div id="backup-modal" class="modal hidden" role="dialog" aria-modal="true" aria-label="原片备份">',
      '  <div class="modal-card backup-card">',
      '    <div class="panel-head">',
      '      <div class="panel-title">',
      '        <h3>原片备份</h3>',
      '        <span id="backup-total" class="pill"></span>',
      '      </div>',
      '      <button id="backup-close" class="ghost" type="button">关闭</button>',
      '    </div>',
      '    <p id="backup-sub" class="sub"></p>',
      '    <div class="backup-toolbar">',
      '      <div class="backup-tabs" id="backup-tabs" role="tablist">',
      '        <button type="button" class="backup-tab active" data-kind="all" role="tab">全部</button>',
      '        <button type="button" class="backup-tab" data-kind="live" role="tab">实况照片</button>',
      '        <button type="button" class="backup-tab" data-kind="video" role="tab">普通视频</button>',
      '      </div>',
      '      <label class="check-field backup-check">',
      '        <input id="backup-select-all" type="checkbox">',
      '        <span>全选当前列表</span>',
      '      </label>',
      '      <span id="backup-selection" class="sub"></span>',
      '    </div>',
      '    <div id="backup-list" class="backup-list"></div>',
      '    <div class="panel-actions backup-actions">',
      '      <span class="sub backup-note">原片只保存在这台电脑；确认手机播放正常后再清除。</span>',
      '      <button id="backup-clear-selected" class="ghost danger-btn" type="button" disabled>清除所选</button>',
      '      <button id="backup-clear-all" class="ghost danger-btn" type="button">清除全部</button>',
      '    </div>',
      '  </div>',
      '</div>',
      '<div id="backup-preview" class="modal hidden" role="dialog" aria-modal="true" aria-label="原片预览">',
      '  <div class="modal-card preview-card">',
      '    <div class="panel-head">',
      '      <div class="panel-title">',
      '        <h3 id="backup-preview-title">预览原片</h3>',
      '        <span id="backup-preview-sub" class="sub"></span>',
      '      </div>',
      '      <button id="backup-preview-close" class="ghost" type="button">关闭</button>',
      '    </div>',
      '    <video id="backup-preview-video" controls playsinline></video>',
      '    <p id="backup-preview-hint" class="sub"></p>',
      '  </div>',
      '</div>',
      '<div id="backup-confirm" class="modal hidden" role="dialog" aria-modal="true" aria-labelledby="backup-confirm-title">',
      '  <div class="modal-card">',
      '    <h3 id="backup-confirm-title"></h3>',
      '    <div id="backup-confirm-body" class="modal-body"></div>',
      '    <div class="modal-actions">',
      '      <button id="backup-confirm-cancel" class="ghost" type="button">取消</button>',
      '      <button id="backup-confirm-ok" class="primary danger-btn" type="button">删除</button>',
      '    </div>',
      '  </div>',
      '</div>',
    ].join('\n'));
  }

  let confirmResolver = null;

  function ask(title, lines, okLabel) {
    return new Promise(function (resolve) {
      confirmResolver = resolve;
      $('backup-confirm-title').textContent = title;
      const body = $('backup-confirm-body');
      body.textContent = '';
      lines.filter(Boolean).forEach(function (line) {
        const paragraph = document.createElement('p');
        paragraph.textContent = line;
        body.appendChild(paragraph);
      });
      $('backup-confirm-ok').textContent = okLabel || '确认';
      $('backup-confirm').classList.remove('hidden');
      $('backup-confirm-ok').focus();
    });
  }

  function resolveConfirm(value) {
    $('backup-confirm').classList.add('hidden');
    const resolver = confirmResolver;
    confirmResolver = null;
    if (resolver) resolver(value);
  }

  function setBusy(busy) {
    $('backup-clear-selected').disabled = busy || state.selected.size === 0;
    $('backup-clear-all').disabled = busy || !state.data ||
      allSessions().length === 0;
  }

  async function open() {
    $('backup-modal').classList.remove('hidden');
    $('backup-list').textContent = '';
    const loading = document.createElement('p');
    loading.className = 'sub backup-empty';
    loading.textContent = '正在读取备份…';
    $('backup-list').appendChild(loading);
    $('backup-sub').textContent = '';
    $('backup-total').textContent = '';
    state.selected.clear();
    state.kind = 'all';
    document.querySelectorAll('.backup-tab').forEach(function (tab) {
      tab.classList.toggle('active', tab.dataset.kind === 'all');
    });
    await load(true);
  }

  async function load(initial) {
    state.loading = true;
    setBusy(true);
    try {
      const data = await api('/api/backups');
      state.data = data;
      const live = (data.sessions || []).reduce(function (sum, item) {
        return sum + item.count;
      }, 0);
      const video = (data.video_sessions || []).reduce(function (sum, item) {
        return sum + item.count;
      }, 0);
      const dirs = [];
      if (live) dirs.push('实况 ' + formatBytes(data.total_bytes || 0));
      if (video) dirs.push('视频 ' + formatBytes(data.video_total_bytes || 0));
      $('backup-sub').textContent = (data.all_total_bytes
        ? formatBytes(data.all_total_bytes) + ' · ' + live + ' 个实况 + ' + video + ' 个视频原片 · '
        : '') + '保存在这台电脑';
      $('backup-total').textContent = (live + video) + ' 个';
      const sizeNode = $('backup-size');
      if (sizeNode) sizeNode.textContent = formatBytes(data.all_total_bytes || 0);
      if (!initial) state.selected.clear();
      render();
    } catch (error) {
      state.data = null;
      $('backup-list').textContent = '';
      const failed = document.createElement('p');
      failed.className = 'sub backup-empty';
      failed.textContent = error.message;
      $('backup-list').appendChild(failed);
      toastMessage(error.message, true);
    } finally {
      state.loading = false;
      setBusy(false);
    }
  }

  function render() {
    if (!state.data) return;
    const visible = visibleSessions();
    const entries = visibleEntries();
    const list = $('backup-list');
    list.textContent = '';
    const allSelected = entries.length > 0 && entries.every(function (entry) {
      return state.selected.has(entryKey(entry.kind, entry.backup));
    });
    const selectAll = $('backup-select-all');
    selectAll.checked = allSelected;
    selectAll.indeterminate = !allSelected && entries.some(function (entry) {
      return state.selected.has(entryKey(entry.kind, entry.backup));
    });
    selectAll.disabled = entries.length === 0;
    const selectedCount = state.selected.size;
    $('backup-selection').textContent = selectedCount
      ? '已选 ' + selectedCount + ' 个 · ' + formatBytes(selectedSize())
      : '';
    setBusy(false);
    if (!visible.length) {
      const empty = document.createElement('p');
      empty.className = 'sub backup-empty';
      empty.textContent = state.kind === 'live'
        ? '还没有实况照片原片备份。'
        : state.kind === 'video'
          ? '还没有普通视频原片备份。'
          : '还没有原片备份。每次「应用到手机」前都会自动备份。';
      list.appendChild(empty);
      return;
    }
    visible.forEach(function (session) { list.appendChild(renderSession(session)); });
  }

  function kindLabel(kind) {
    return kind === 'video' ? '普通视频' : '实况照片';
  }

  function selectionForSession(session) {
    return session.entries.every(function (entry) {
      return state.selected.has(entryKey(session.kind, entry.backup));
    });
  }

  function renderSession(session) {
    const card = document.createElement('div');
    card.className = 'backup-session';
    const head = document.createElement('div');
    head.className = 'backup-head';
    const main = document.createElement('div');
    main.className = 'backup-head-main';
    const kind = document.createElement('span');
    kind.className = 'backup-kind ' + session.kind;
    kind.textContent = kindLabel(session.kind);
    const title = document.createElement('strong');
    title.textContent = formatSessionName(session.session);
    const meta = document.createElement('span');
    meta.className = 'sub';
    meta.textContent = session.count + ' 个 · ' + formatBytes(session.bytes);
    main.appendChild(kind);
    main.appendChild(title);
    main.appendChild(meta);
    head.appendChild(main);

    const actions = document.createElement('div');
    actions.className = 'backup-head-actions';
    const select = document.createElement('button');
    select.type = 'button';
    select.className = 'mini ghost';
    select.textContent = selectionForSession(session) ? '取消本批' : '全选本批';
    select.addEventListener('click', function () {
      const every = selectionForSession(session);
      session.entries.forEach(function (entry) {
        const key = entryKey(session.kind, entry.backup);
        if (every) state.selected.delete(key);
        else state.selected.add(key);
      });
      render();
    });
    actions.appendChild(select);

    const restore = document.createElement('button');
    restore.type = 'button';
    restore.className = 'mini ghost';
    restore.textContent = '恢复本批';
    restore.addEventListener('click', function () { restoreSession(session); });
    actions.appendChild(restore);

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'mini ghost danger-btn';
    remove.textContent = '删除本批';
    remove.addEventListener('click', function () {
      deleteSession(session, session.count, session.bytes);
    });
    actions.appendChild(remove);
    head.appendChild(actions);
    card.appendChild(head);

    const entries = document.createElement('div');
    entries.className = 'backup-entries';
    session.entries.forEach(function (entry) {
      entries.appendChild(renderEntry(session, entry));
    });
    card.appendChild(entries);
    return card;
  }

  function renderEntry(session, entry) {
    const row = document.createElement('div');
    row.className = 'backup-row';
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.checked = state.selected.has(entryKey(session.kind, entry.backup));
    check.setAttribute('aria-label', '选择 ' + basename(entry.mp4));
    check.addEventListener('change', function () {
      const key = entryKey(session.kind, entry.backup);
      if (check.checked) state.selected.add(key);
      else state.selected.delete(key);
      render();
    });
    row.appendChild(check);

    const thumb = document.createElement('button');
    thumb.type = 'button';
    thumb.className = 'backup-thumb';
    thumb.title = '预览原片';
    const image = document.createElement('img');
    image.loading = 'lazy';
    image.alt = '';
    image.src = '/api/backups/thumb?kind=' + encodeURIComponent(session.kind) +
      '&backup=' + encodeURIComponent(entry.backup) +
      '&token=' + encodeURIComponent(TOKEN);
    image.addEventListener('error', function () {
      image.classList.add('hidden');
      const fallback = thumb.querySelector('.backup-fallback');
      if (fallback) fallback.classList.remove('hidden');
    });
    const fallback = document.createElement('span');
    fallback.className = 'backup-fallback hidden';
    fallback.textContent = '▶';
    thumb.appendChild(image);
    thumb.appendChild(fallback);
    thumb.addEventListener('click', function () { preview(session, entry); });
    row.appendChild(thumb);

    const text = document.createElement('div');
    text.className = 'backup-row-text';
    const name = document.createElement('strong');
    name.textContent = (entry.date ? entry.date + ' ' + entry.time + ' · ' : '') +
      basename(entry.mp4);
    name.title = entry.mp4;
    const sub = document.createElement('span');
    sub.className = 'sub';
    sub.textContent = kindLabel(session.kind) + ' · ' + formatBytes(entry.original_size) +
      ' · ' + formatSessionName(session.session);
    text.appendChild(name);
    text.appendChild(sub);
    row.appendChild(text);

    const play = document.createElement('button');
    play.type = 'button';
    play.className = 'ghost backup-play';
    play.textContent = '预览';
    play.addEventListener('click', function () { preview(session, entry); });
    row.appendChild(play);
    return row;
  }

  function backupUrl(kind, entry) {
    return '/api/backups/file?kind=' + encodeURIComponent(kind) +
      '&backup=' + encodeURIComponent(entry.backup) +
      '&token=' + encodeURIComponent(TOKEN);
  }

  function preview(session, entry) {
    const video = $('backup-preview-video');
    $('backup-preview-title').textContent =
      (entry.date ? entry.date + ' ' + entry.time + ' · ' : '') + basename(entry.mp4);
    $('backup-preview-sub').textContent =
      kindLabel(session.kind) + '原片 · ' + formatBytes(entry.original_size) +
      ' · ' + formatSessionName(session.session);
    $('backup-preview-hint').textContent = '';
    video.onerror = function () {
      $('backup-preview-hint').textContent = '无法播放这个备份文件，可能已被移动或删除。';
    };
    video.src = backupUrl(session.kind, entry);
    $('backup-preview').classList.remove('hidden');
    const play = video.play();
    if (play && play.catch) play.catch(function () {});
  }

  function closePreview() {
    const video = $('backup-preview-video');
    video.onerror = null;
    video.pause();
    video.removeAttribute('src');
    video.load();
    $('backup-preview').classList.add('hidden');
  }

  function close() {
    closePreview();
    if (confirmResolver) resolveConfirm(false);
    $('backup-modal').classList.add('hidden');
  }

  async function deleteSelected() {
    const files = [];
    let bytes = 0;
    allSessions().forEach(function (session) {
      session.entries.forEach(function (entry) {
        if (state.selected.has(entryKey(session.kind, entry.backup))) {
          files.push({ kind: session.kind, backup: entry.backup });
          bytes += entry.original_size;
        }
      });
    });
    if (!files.length) return;
    const ok = await ask('清除所选原片备份？', [
      '将从这台电脑永久删除 ' + files.length + ' 个原片备份（' + formatBytes(bytes) + '）。',
      '删除后，这些文件无法再恢复成原始版本。',
      '请先确认手机上的照片和视频播放正常。',
    ], '清除所选');
    if (!ok) return;
    try {
      const result = await api('/api/delete-backups', {
        method: 'POST',
        body: JSON.stringify({
          files: files,
          confirm: 'delete-backups',
        }),
      });
      state.selected.clear();
      toastMessage('已清除 ' + result.deleted_files + ' 个原片备份');
      await load(false);
      notifyChanged();
    } catch (error) {
      toastMessage(error.message, true);
    }
  }

  async function deleteSession(session, count, bytes) {
    const ok = await ask('删除这批原片备份？', [
      '将从这台电脑永久删除 ' + count + ' 个原片（' + formatBytes(bytes) + '）。',
      '删除后，这些文件无法再恢复成原始版本。',
      '请先确认手机上的照片和视频播放正常。',
      '批次：' + formatSessionName(session.session) + ' · ' + kindLabel(session.kind),
    ], '删除本批');
    if (!ok) return;
    try {
      const result = await api('/api/delete-backups', {
        method: 'POST',
        body: JSON.stringify({
          session: session.session,
          kind: session.kind,
          confirm: 'delete-backups',
        }),
      });
      session.entries.forEach(function (entry) {
        state.selected.delete(entryKey(session.kind, entry.backup));
      });
      toastMessage('已删除 ' + result.deleted_files + ' 个原片备份');
      await load(false);
      notifyChanged();
    } catch (error) {
      toastMessage(error.message, true);
    }
  }

  async function clearAll() {
    if (!state.data) return;
    const sessions = allSessions();
    const count = sessions.reduce(function (sum, item) { return sum + item.count; }, 0);
    if (!count) {
      toastMessage('当前没有备份');
      return;
    }
    const ok = await ask('清除全部原片备份？', [
      '将删除 ' + sessions.length + ' 个批次、共 ' + count + ' 个原片备份，无法撤销。',
      '删除后无法再恢复任何原始版本。',
      '如果手机上的压缩版还没有逐张确认，请不要删除。',
    ], '清除全部');
    if (!ok) return;
    try {
      const result = await api('/api/delete-backups', {
        method: 'POST',
        body: JSON.stringify({ session: '*', confirm: 'delete-backups' }),
      });
      state.selected.clear();
      toastMessage('已清除 ' + result.deleted_sessions + ' 个批次 / ' +
        result.deleted_files + ' 个文件');
      await load(false);
      notifyChanged();
    } catch (error) {
      toastMessage(error.message, true);
    }
  }

  async function restoreSession(session) {
    const ok = await ask('恢复这批原片？', [
      '将从电脑备份恢复 ' + session.count + ' 个原片（' + formatBytes(session.bytes) + '）。',
      '只替换 MP4，JPG 不会改动；恢复后需要确认手机相册。',
      '如果手机上的文件已经被其他方式修改，会跳过该文件，避免覆盖新内容。',
      '批次：' + formatSessionName(session.session) + ' · ' + kindLabel(session.kind),
    ], '确认恢复');
    if (!ok) return;
    try {
      const data = await api('/api/restore-backups', {
        method: 'POST',
        body: JSON.stringify({
          session: session.session,
          kind: session.kind,
        }),
      });
      close();
      if (typeof window.showJob === 'function') {
        window.showJob(data.job_id, 'restore-backup');
      } else if (typeof window.loadSummary === 'function') {
        await window.loadSummary();
      }
    } catch (error) {
      toastMessage(error.message, true);
    }
  }

  function notifyChanged() {
    try {
      if (typeof window.loadSummary === 'function') {
        const result = window.loadSummary();
        if (result && result.catch) result.catch(function () {});
      }
    } catch (error) {}
    window.dispatchEvent(new CustomEvent('klean-backups-changed'));
  }

  function bind() {
    $('backup-btn').addEventListener('click', open);
    $('backup-close').addEventListener('click', close);
    $('backup-modal').addEventListener('click', function (event) {
      if (event.target === $('backup-modal')) close();
    });
    $('backup-tabs').addEventListener('click', function (event) {
      const button = event.target.closest('.backup-tab');
      if (!button) return;
      state.kind = button.dataset.kind;
      state.selected.clear();
      document.querySelectorAll('.backup-tab').forEach(function (tab) {
        tab.classList.toggle('active', tab === button);
      });
      render();
    });
    $('backup-select-all').addEventListener('change', function () {
      const checked = this.checked;
      visibleEntries().forEach(function (entry) {
        const key = entryKey(entry.kind, entry.backup);
        if (checked) state.selected.add(key);
        else state.selected.delete(key);
      });
      render();
    });
    $('backup-clear-selected').addEventListener('click', deleteSelected);
    $('backup-clear-all').addEventListener('click', clearAll);
    $('backup-preview-close').addEventListener('click', closePreview);
    $('backup-preview').addEventListener('click', function (event) {
      if (event.target === $('backup-preview')) closePreview();
    });
    $('backup-confirm-cancel').addEventListener('click', function () {
      resolveConfirm(false);
    });
    $('backup-confirm-ok').addEventListener('click', function () {
      resolveConfirm(true);
    });
    $('backup-confirm').addEventListener('click', function (event) {
      if (event.target === $('backup-confirm')) resolveConfirm(false);
    });
    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Escape') return;
      if (!$('backup-confirm').classList.contains('hidden')) {
        resolveConfirm(false);
        return;
      }
      if (!$('backup-preview').classList.contains('hidden')) {
        closePreview();
        return;
      }
      if (!$('backup-modal').classList.contains('hidden')) close();
    });
  }

  injectMarkup();
  bind();
  window.KleanBackups = { open: open, close: close };
})();
