'use strict';

const TOKEN = document.querySelector('meta[name="klean-token"]').content;
const PAGE_SIZE = 80;
const SUN_ICON =
  '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round">' +
  '<circle cx="12" cy="12" r="4"></circle>' +
  '<path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.66 6.34l1.41-1.41"></path></svg>';
const MOON_ICON =
  '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"></path></svg>';
const CHECK_ICON =
  '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M5 12.5l4.5 4.5L19 7.5"></path></svg>';

const state = {
  summary: null,
  months: [],
  albums: [],
  videos: [],
  filtered: [],
  renderedCount: 0,
  selected: new Map(),
  view: { type: 'all' },
  preset: 'balanced',
  minBytes: 0,
  compressedFilter: 'all',
  sortMode: 'time',
  job: null,
  compressJob: null,
  poll: null,
  pollInFlight: false,
  busy: false,
  finished: false,
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

function updateSessionSavings(data) {
  const value = $('session-saved-value');
  if (!value) return;
  value.textContent = formatBytes(data.session_saved_bytes || 0);
  const wrap = $('session-saved');
  if (wrap) {
    const files = data.session_saved_files || 0;
    wrap.title = files > 0
      ? '本次打开应用后，已应用 ' + files + ' 个压缩，累计节省空间'
      : '本次打开应用后，已应用压缩累计节省空间';
  }
}

function formatDuration(ms) {
  if (!ms) return '时长未知';
  const total = Math.round(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    return hours + ':' + String(minutes % 60).padStart(2, '0') + ':' + String(seconds).padStart(2, '0');
  }
  return minutes + ':' + String(seconds).padStart(2, '0');
}

function basename(path) {
  return String(path || '').split('/').pop();
}

function toast(message, isError) {
  const node = $('toast');
  node.textContent = message;
  node.className = 'toast' + (isError ? ' err' : '');
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(function () { node.className = 'toast hidden'; }, 4200);
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

function effectiveTheme() {
  return document.documentElement.dataset.theme ||
    (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
}

function notifyNativeTheme(theme) {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.set_theme) {
    const result = window.pywebview.api.set_theme(theme);
    if (result && result.catch) result.catch(function () {});
  }
}

function syncThemeButton() {
  const button = $('theme-btn');
  if (!button) return;
  const dark = effectiveTheme() === 'dark';
  button.innerHTML = dark ? MOON_ICON : SUN_ICON;
  const label = dark ? '切换到浅色主题' : '切换到深色主题';
  button.title = label;
  button.setAttribute('aria-label', label);
}

function applyTheme(theme, persist) {
  document.documentElement.dataset.theme = theme;
  if (persist) {
    try { localStorage.setItem('klean-theme', theme); } catch (error) {}
  }
  syncThemeButton();
  notifyNativeTheme(theme);
}

function initTheme() {
  syncThemeButton();
  notifyNativeTheme(effectiveTheme());
  if (window.matchMedia('(prefers-color-scheme: dark)').addEventListener) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
      if (!document.documentElement.dataset.theme) {
        syncThemeButton();
        notifyNativeTheme(effectiveTheme());
      }
    });
  }
}

/* ------------------------------------------------------------------ sidebar */

function renderSidebar() {
  const monthList = $('month-list');
  const albumList = $('album-list');
  monthList.textContent = '';
  albumList.textContent = '';
  state.months.forEach(function (row) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'video-side-row';
    button.dataset.month = row.month;
    const label = document.createElement('span');
    label.textContent = row.month;
    const meta = document.createElement('span');
    meta.className = 'video-side-count';
    meta.textContent = row.count + ' 个';
    button.appendChild(label);
    button.appendChild(meta);
    button.addEventListener('click', function () {
      setView({ type: 'month', month: row.month });
    });
    monthList.appendChild(button);
  });
  state.albums.forEach(function (row) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'video-side-row';
    button.dataset.album = row.album;
    button.title = row.album;
    const label = document.createElement('span');
    label.textContent = row.name;
    const meta = document.createElement('span');
    meta.className = 'video-side-count';
    meta.textContent = row.count + ' 个';
    button.appendChild(label);
    button.appendChild(meta);
    button.addEventListener('click', function () {
      setView({ type: 'album', album: row.album, name: row.name });
    });
    albumList.appendChild(button);
  });
  syncSidebarActive();
}

function syncSidebarActive() {
  document.querySelectorAll('.video-side-row').forEach(function (row) {
    let active = false;
    if (state.view.type === 'month') active = row.dataset.month === state.view.month;
    else if (state.view.type === 'album') active = row.dataset.album === state.view.album;
    row.classList.toggle('active', active);
  });
  $('all-videos').classList.toggle('active', state.view.type === 'all');
}

function setView(view) {
  state.view = view;
  syncSidebarActive();
  loadVideos();
}

/* --------------------------------------------------------------------- data */

async function loadSummary() {
  const data = await api('/api/video/summary');
  state.summary = data;
  const device = data.device || {};
  $('device-line').textContent =
    '普通视频 · ' + (device.model || 'Android 设备') +
    (device.android ? ' · Android ' + device.android : '') +
    ' · 扫描于 ' + (data.scanned_at || '未知');
  $('video-count').textContent = (data.count || 0).toLocaleString('zh-CN');
  $('video-size').textContent = formatBytes(data.bytes);
  $('video-compressed').textContent = (data.compressed_count || 0).toLocaleString('zh-CN');
  $('backup-size').textContent = formatBytes(data.backup_bytes || 0);
  updateSessionSavings(data);
  $('all-count').textContent = (data.count || 0) + ' 个';
  $('video-side-summary').textContent = formatBytes(data.bytes);
  updateSelectionUI();
}

async function loadSidebar() {
  const data = await api('/api/video/dates');
  state.months = data.months || [];
  state.albums = data.albums || [];
  renderSidebar();
}

async function loadVideos() {
  let query = '';
  if (state.view.type === 'month') {
    query = '?month=' + encodeURIComponent(state.view.month);
  } else if (state.view.type === 'album') {
    query = '?album=' + encodeURIComponent(state.view.album);
  } else if (state.view.type === 'range') {
    query = '?start=' + encodeURIComponent(state.view.start) +
      '&end=' + encodeURIComponent(state.view.end);
  }
  const data = await api('/api/video/list' + query);
  state.videos = data.videos || [];
  renderView();
}

function applyFilter() {
  const minBytes = state.minBytes;
  let videos = state.videos.filter(function (video) {
    if (video.size < minBytes) return false;
    if (state.compressedFilter === 'uncompressed' && video.compressed) return false;
    if (state.compressedFilter === 'compressed' && !video.compressed) return false;
    return true;
  });
  if (state.sortMode === 'size') {
    videos = videos.slice().sort(function (a, b) { return b.size - a.size; });
  } else {
    videos = videos.slice().sort(function (a, b) {
      if (a.date !== b.date) return b.date.localeCompare(a.date);
      return b.time.localeCompare(a.time);
    });
  }
  state.filtered = videos;
}

function renderView() {
  applyFilter();
  const totalBytes = state.filtered.reduce(function (sum, video) { return sum + video.size; }, 0);
  let title = '全部视频';
  if (state.view.type === 'month') title = state.view.month;
  else if (state.view.type === 'album') title = state.view.name || state.view.album;
  else if (state.view.type === 'range') title = state.view.start + ' ~ ' + state.view.end;
  const status = state.compressedFilter === 'uncompressed'
    ? '只看未压缩 · '
    : (state.compressedFilter === 'compressed' ? '只看已压缩 · ' : '');
  $('view-title').textContent = title;
  $('view-sub').textContent =
    status + state.filtered.length + ' 个 · ' + formatBytes(totalBytes) +
    ' · 建议每批 ≤ ' + formatBytes(state.summary ? state.summary.suggested_batch_bytes : 0);
  $('filter-hint').textContent = status + state.filtered.length + ' 个视频';
  $('video-grid').textContent = '';
  state.renderedCount = 0;
  const empty = $('video-empty');
  empty.classList.toggle('hidden', state.filtered.length !== 0);
  appendNextPage();
  syncVisibleCards();
  updateSelectionUI();
}

function appendNextPage() {
  const end = Math.min(state.renderedCount + PAGE_SIZE, state.filtered.length);
  const fragment = document.createDocumentFragment();
  for (let index = state.renderedCount; index < end; index += 1) {
    fragment.appendChild(makeCard(state.filtered[index]));
  }
  $('video-grid').appendChild(fragment);
  state.renderedCount = end;
  const more = state.renderedCount < state.filtered.length;
  $('load-more-wrap').classList.toggle('hidden', !more);
  if (more) {
    $('load-more').textContent = '加载更多（剩余 ' + (state.filtered.length - state.renderedCount) + ' 个）';
  }
}

function makeCard(video) {
  const card = document.createElement('article');
  card.className = 'video-card';
  card.dataset.path = video.path;
  if (state.selected.has(video.path)) card.classList.add('selected');

  const checkWrap = document.createElement('label');
  checkWrap.className = 'card-check';
  checkWrap.title = video.compressed ? '已压缩的视频不会重复处理' : '选择这个视频';
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.checked = state.selected.has(video.path);
  checkbox.addEventListener('change', function (event) {
    event.stopPropagation();
    toggleVideo(video, checkbox.checked);
  });
  const checkUi = document.createElement('span');
  checkUi.className = 'check-ui';
  checkWrap.appendChild(checkbox);
  checkWrap.appendChild(checkUi);
  card.appendChild(checkWrap);

  const thumb = document.createElement('div');
  thumb.className = 'video-thumb';
  const image = document.createElement('img');
  image.loading = 'lazy';
  image.decoding = 'async';
  image.alt = '';
  image.src = '/api/video/thumb?path=' + encodeURIComponent(video.path) + '&token=' + encodeURIComponent(TOKEN);
  image.addEventListener('load', function () { image.classList.add('loaded'); });
  image.addEventListener('error', function () {
    image.remove();
    const fallback = document.createElement('div');
    fallback.className = 'fallback';
    fallback.textContent = '缩略图不可用';
    thumb.appendChild(fallback);
  });
  thumb.appendChild(image);

  const play = document.createElement('span');
  play.className = 'video-play';
  thumb.appendChild(play);

  const duration = document.createElement('span');
  duration.className = 'video-duration';
  duration.textContent = formatDuration(video.duration_ms);
  thumb.appendChild(duration);

  if (video.width && video.height) {
    const resolution = document.createElement('span');
    resolution.className = 'video-res';
    resolution.textContent = video.width + '×' + video.height;
    thumb.appendChild(resolution);
  }

  if (video.compressed) {
    const badge = document.createElement('span');
    badge.className = 'compressed-badge';
    badge.innerHTML = CHECK_ICON;
    badge.setAttribute('aria-label', '已压缩');
    badge.title = '已压缩' + (video.history_crf ? '（CRF' + video.history_crf + '）' : '') +
      (video.history_size ? ' · ' + formatBytes(video.history_size) : '');
    thumb.appendChild(badge);
  }
  card.appendChild(thumb);

  const meta = document.createElement('div');
  meta.className = 'video-meta';
  const top = document.createElement('div');
  top.className = 'video-meta-top';
  const date = document.createElement('span');
  date.className = 'video-date';
  date.textContent = video.date + ' ' + video.time;
  const size = document.createElement('span');
  size.className = 'video-size';
  size.textContent = formatBytes(video.size);
  top.appendChild(date);
  top.appendChild(size);
  meta.appendChild(top);

  const name = document.createElement('div');
  name.className = 'video-name';
  name.textContent = basename(video.path);
  name.title = video.path;
  meta.appendChild(name);

  const album = document.createElement('div');
  album.className = 'video-album';
  album.textContent = video.album;
  album.title = video.album;
  meta.appendChild(album);
  card.appendChild(meta);

  card.addEventListener('click', function (event) {
    if (event.target.closest('input, label')) return;
    toggleVideo(video, !state.selected.has(video.path));
  });
  return card;
}

function toggleVideo(video, checked) {
  if (checked && video.compressed) {
    toast('这条视频已压缩过；视频赛道暂不支持重复压缩', true);
    syncVisibleCards();
    return;
  }
  if (checked) state.selected.set(video.path, video);
  else state.selected.delete(video.path);
  syncVisibleCards();
  updateSelectionUI();
}

function syncVisibleCards() {
  document.querySelectorAll('.video-card').forEach(function (card) {
    const selected = state.selected.has(card.dataset.path);
    card.classList.toggle('selected', selected);
    const input = card.querySelector('input');
    if (input) input.checked = selected;
  });
}

function updateSelectionUI() {
  let bytes = 0;
  state.selected.forEach(function (video) { bytes += video.size; });
  const count = state.selected.size;
  $('selected-count').textContent = count;
  $('selected-size').textContent = formatBytes(bytes);
  $('selected-view-btn').textContent = '已选 ' + count;
  $('clear-selected-btn').disabled = count === 0;
  $('compress-btn').disabled = count === 0 || state.busy;
  const available = state.filtered.filter(function (video) { return !video.compressed; });
  const allSelected = available.length > 0 && available.every(function (video) {
    return state.selected.has(video.path);
  });
  $('select-scope-btn').textContent = allSelected ? '取消全选' : '全选当前';
  const suggestion = state.summary ? state.summary.suggested_batch_bytes : 0;
  const hint = $('batch-hint');
  if (!count) {
    hint.textContent = suggestion
      ? '建议每批 ≤ ' + formatBytes(suggestion) + '，按总大小分批更稳'
      : '';
    hint.style.color = '';
  } else if (suggestion && bytes > suggestion) {
    hint.textContent = '已超过建议批次（≤ ' + formatBytes(suggestion) + '），建议分两批处理';
    hint.style.color = 'var(--warn)';
  } else {
    hint.textContent = '本批 ' + formatBytes(bytes) + ' · 建议每批 ≤ ' + formatBytes(suggestion);
    hint.style.color = '';
  }
}

function selectedPaths() {
  return Array.from(state.selected.keys());
}

function clearSelection() {
  if (!state.selected.size) return;
  const count = state.selected.size;
  state.selected.clear();
  syncVisibleCards();
  updateSelectionUI();
  toast('已清空 ' + count + ' 个已选');
}

function toggleCurrentScope() {
  const available = state.filtered.filter(function (video) { return !video.compressed; });
  if (!available.length) {
    toast(state.compressedFilter === 'compressed'
      ? '当前只看已压缩视频，不能重复压缩'
      : '当前筛选结果为空', true);
    return;
  }
  const allSelected = available.length > 0 && available.every(function (video) {
    return state.selected.has(video.path);
  });
  available.forEach(function (video) {
    if (allSelected) state.selected.delete(video.path);
    else state.selected.set(video.path, video);
  });
  syncVisibleCards();
  updateSelectionUI();
}

function selectLargest() {
  const available = state.filtered.filter(function (video) { return !video.compressed; });
  available.sort(function (a, b) { return b.size - a.size; });
  available.slice(0, 50).forEach(function (video) { state.selected.set(video.path, video); });
  syncVisibleCards();
  updateSelectionUI();
}

/* --------------------------------------------------------------------- jobs */

function videoJobTitle(kind) {
  if (kind === 'video-apply') return '应用视频';
  if (kind === 'video-restore') return '恢复视频';
  if (kind === 'restore-backup') return '从电脑备份恢复';
  return '视频压缩';
}

function itemStatusLabel(status) {
  const labels = {
    pending: '待处理', running: '处理中', ready: '可应用', skipped: '已跳过',
    error: '错误', applied: '已应用', restored: '已恢复',
  };
  return labels[status] || status;
}

function renderJobProgress(job) {
  $('video-job-title').textContent = videoJobTitle(job.kind);
  const pill = $('video-job-status');
  pill.textContent = job.status === 'running' ? '进行中' : (job.status === 'done' ? '已完成' : '失败');
  pill.className = 'pill ' + job.status;
  const percent = job.total > 0
    ? Math.min(100, Math.round((job.progress != null ? job.progress : job.done / job.total) * 100))
    : (job.status === 'done' ? 100 : 0);
  $('video-job-progress').style.width = percent + '%';
  $('video-job-message').textContent = job.error || job.message || '';
  if (job.status === 'running') {
    $('video-job-counts').textContent = job.total ? job.done + ' / ' + job.total : '';
  } else if (job.items && job.items.length) {
    const good = job.items.filter(function (item) {
      return item.status === 'ready' || item.status === 'applied' || item.status === 'restored';
    }).length;
    const skipped = job.items.filter(function (item) { return item.status === 'skipped'; }).length;
    const errors = job.items.filter(function (item) { return item.status === 'error'; }).length;
    $('video-job-counts').textContent = '完成 ' + good + ' · 跳过 ' + skipped + ' · 失败 ' + errors;
  }
}

function renderJobRows(job) {
  const list = $('video-job-items');
  list.textContent = '';
  (job.items || []).forEach(function (item) {
    const row = document.createElement('div');
    row.className = 'result-row';
    const main = document.createElement('div');
    main.className = 'r-main';
    const nameLine = document.createElement('div');
    nameLine.className = 'r-name';
    const name = document.createElement('strong');
    name.textContent = basename(item.mp4);
    const badge = document.createElement('span');
    badge.className = 'badge';
    badge.textContent = itemStatusLabel(item.status);
    if (item.status === 'ready') badge.classList.add('ok');
    if (item.status === 'error') badge.classList.add('err');
    if (item.status === 'skipped') badge.classList.add('warn');
    nameLine.appendChild(name);
    nameLine.appendChild(badge);
    main.appendChild(nameLine);
    if (item.reason) {
      const reason = document.createElement('div');
      reason.className = 'r-reason';
      reason.textContent = item.reason;
      main.appendChild(reason);
    }
    row.appendChild(main);

    const numbers = document.createElement('div');
    numbers.className = 'r-numbers';
    if (item.original_size && item.compressed_size) {
      const saved = Math.max(0, item.original_size - item.compressed_size);
      const percent = Math.round(saved * 100 / item.original_size);
      numbers.textContent = formatBytes(item.original_size) + ' → ' +
        formatBytes(item.compressed_size) + ' · 省 ' + percent + '%';
      if (item.elapsed_ms) {
        const elapsed = document.createElement('div');
        elapsed.className = 'r-ssim';
        elapsed.textContent = '耗时 ' + (item.elapsed_ms / 1000).toFixed(1) + 's';
        numbers.appendChild(elapsed);
      }
    } else if (item.original_size) {
      numbers.textContent = formatBytes(item.original_size);
    }
    row.appendChild(numbers);

    if (job.kind === 'video-compress' && item.status === 'ready') {
      const preview = document.createElement('button');
      preview.type = 'button';
      preview.className = 'ghost';
      preview.textContent = '预览';
      preview.addEventListener('click', function () {
        previewItem(job.id, item);
      });
      row.appendChild(preview);
    }
    list.appendChild(row);
  });
}

function renderJobActions(job) {
  const actions = $('video-job-actions');
  actions.textContent = '';
  if (job.kind === 'video-compress' && job.status === 'running') {
    const stop = document.createElement('button');
    stop.type = 'button';
    stop.className = 'ghost danger-btn';
    stop.textContent = job.cancel_requested ? '正在停止…' : '停止任务';
    stop.disabled = Boolean(job.cancel_requested);
    stop.addEventListener('click', stopJob);
    actions.appendChild(stop);
  }
  if (job.kind === 'video-compress' && job.status === 'done') {
    const ready = job.items.filter(function (item) { return item.status === 'ready'; });
    if (ready.length) {
      const hint = document.createElement('span');
      hint.className = 'sub';
      hint.textContent = '先预览确认，再应用；原视频会自动备份。';
      actions.appendChild(hint);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'primary';
      button.textContent = '应用所选（' + ready.length + '）';
      button.addEventListener('click', applyVideos);
      actions.appendChild(button);
    } else {
      const hint = document.createElement('span');
      hint.className = 'sub';
      hint.textContent = '没有可应用的视频：看看每行的跳过原因。';
      actions.appendChild(hint);
    }
  }
  if (job.kind === 'video-apply' && job.status === 'done') {
    const applied = job.items.filter(function (item) { return item.status === 'applied'; });
    if (applied.length) {
      const hint = document.createElement('span');
      hint.className = 'sub';
      hint.textContent = '原视频已备份在本机；确认手机播放正常后再清除备份。';
      actions.appendChild(hint);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'ghost';
      button.textContent = '恢复原视频（' + applied.length + '）';
      button.addEventListener('click', restoreVideos);
      actions.appendChild(button);
    }
  }
  if (job.kind === 'video-restore' && job.status === 'done') {
    const hint = document.createElement('span');
    hint.className = 'sub';
    hint.textContent = '已恢复的视频已从压缩历史移除。';
    actions.appendChild(hint);
  }
}

function renderJob(job) {
  renderJobProgress(job);
  renderJobRows(job);
  renderJobActions(job);
}

function showJob(jobId, kind) {
  if (state.poll) window.clearInterval(state.poll);
  state.finished = false;
  state.pollInFlight = false;
  state.busy = true;
  state.job = { id: jobId, kind: kind, status: 'running', items: [], done: 0, total: 0, progress: 0 };
  $('video-job-panel').classList.remove('hidden');
  $('video-job-items').textContent = '';
  $('video-job-actions').textContent = '';
  $('video-job-counts').textContent = '';
  $('video-job-progress').style.width = '0%';
  $('video-job-message').textContent = '正在准备…';
  $('video-job-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  updateSelectionUI();

  async function finalize() {
    if (state.finished) return;
    state.finished = true;
    if (state.poll) { window.clearInterval(state.poll); state.poll = null; }
    try {
      const job = await api('/api/jobs/' + encodeURIComponent(jobId));
      state.job = job;
      renderJob(job);
      state.busy = false;
      updateSelectionUI();
      await afterJob(job);
    } catch (error) {
      state.busy = false;
      updateSelectionUI();
      toast(error.message, true);
    }
  }

  async function tick() {
    if (state.finished || state.pollInFlight) return;
    state.pollInFlight = true;
    try {
      const job = await api('/api/jobs/' + encodeURIComponent(jobId));
      if (state.finished) return;
      state.job = job;
      renderJobProgress(job);
      if (job.status !== 'running') await finalize();
    } catch (error) {
      if (state.poll) { window.clearInterval(state.poll); state.poll = null; }
      state.busy = false;
      updateSelectionUI();
      toast(error.message, true);
    } finally {
      state.pollInFlight = false;
    }
  }

  tick();
  state.poll = window.setInterval(tick, 800);
}

async function afterJob(job) {
  if (job.kind === 'video-compress') {
    state.compressJob = job;
    return;
  }
  if (job.kind === 'video-apply' || job.kind === 'video-restore' ||
      job.kind === 'restore-backup') {
    state.selected.clear();
    await loadSummary();
    await loadSidebar();
    await loadVideos();
    if (job.kind === 'video-restore') state.compressJob = null;
  }
}

async function selectDateRange() {
  const start = $('range-start').value;
  const end = $('range-end').value;
  if (!start || !end) {
    toast('请先选择开始和结束日期', true);
    return;
  }
  if (start > end) {
    toast('开始日期不能晚于结束日期', true);
    return;
  }
  state.view = { type: 'range', start: start, end: end };
  syncSidebarActive();
  try {
    await loadVideos();
  } catch (error) {
    toast(error.message, true);
    return;
  }
  const usable = state.filtered.filter(function (video) { return !video.compressed; });
  state.selected.clear();
  usable.forEach(function (video) { state.selected.set(video.path, video); });
  syncVisibleCards();
  updateSelectionUI();
  $('range-hint').textContent = usable.length
    ? '已选 ' + usable.length + ' 个未压缩视频'
    : '范围内没有可压缩视频';
  if (!usable.length) {
    toast(state.compressedFilter === 'compressed'
      ? '范围内都是已压缩视频，不能重复压缩'
      : '这个日期范围内没有可压缩视频', true);
  } else {
    toast('已选中 ' + start + ' ~ ' + end + ' 的 ' + usable.length + ' 个视频');
  }
}

async function compressSelected() {
  const paths = selectedPaths();
  if (!paths.length) return;
  const suggestion = state.summary ? state.summary.suggested_batch_bytes : 0;
  const bytes = Array.from(state.selected.values()).reduce(function (sum, video) { return sum + video.size; }, 0);
  if (suggestion && bytes > suggestion) {
    toast('已超过建议批次 ' + formatBytes(suggestion) + '，建议分批；仍会按所选执行', false);
  }
  state.busy = true;
  updateSelectionUI();
  try {
    const data = await api('/api/video/compress', {
      method: 'POST',
      body: JSON.stringify({ paths: paths, preset: state.preset }),
    });
    if (data.warning) toast(data.warning, true);
    showJob(data.job_id, 'video-compress');
  } catch (error) {
    state.busy = false;
    updateSelectionUI();
    toast(error.message, true);
  }
}

async function applyVideos() {
  if (!state.compressJob) return;
  const paths = state.compressJob.items
    .filter(function (item) { return item.status === 'ready'; })
    .map(function (item) { return item.mp4; });
  if (!paths.length) return;
  try {
    const data = await api('/api/video/apply', {
      method: 'POST',
      body: JSON.stringify({ job_id: state.compressJob.id, paths: paths }),
    });
    showJob(data.job_id, 'video-apply');
  } catch (error) {
    toast(error.message, true);
  }
}

async function restoreVideos() {
  if (!state.compressJob || !state.job) return;
  const applied = state.job.items
    .filter(function (item) { return item.status === 'applied'; })
    .map(function (item) { return item.mp4; });
  if (!applied.length) return;
  try {
    const data = await api('/api/video/restore', {
      method: 'POST',
      body: JSON.stringify({ job_id: state.compressJob.id, paths: applied }),
    });
    showJob(data.job_id, 'video-restore');
  } catch (error) {
    toast(error.message, true);
  }
}

async function stopJob() {
  if (!state.job) return;
  try {
    await api('/api/jobs/' + encodeURIComponent(state.job.id) + '/cancel', {
      method: 'POST',
      body: '{}',
    });
    toast('已请求停止；当前视频结束后不再开始新视频');
  } catch (error) {
    toast(error.message, true);
  }
}

function previewItem(jobId, item) {
  const video = $('video-preview-video');
  $('video-preview-title').textContent = basename(item.mp4) + '（压缩版）';
  const saved = item.original_size
    ? Math.round((item.original_size - item.compressed_size) * 100 / item.original_size)
    : 0;
  $('video-preview-sub').textContent =
    formatBytes(item.original_size) + ' → ' + formatBytes(item.compressed_size) + ' · 省 ' + saved + '%';
  video.src = '/api/preview?job=' + encodeURIComponent(jobId) +
    '&path=' + encodeURIComponent(item.mp4) + '&token=' + encodeURIComponent(TOKEN);
  $('video-preview').classList.remove('hidden');
  const play = video.play();
  if (play && play.catch) play.catch(function () {});
}

function closePreview() {
  const video = $('video-preview-video');
  video.pause();
  video.removeAttribute('src');
  video.load();
  $('video-preview').classList.add('hidden');
}

/* ------------------------------------------------------------------- events */

function bindEvents() {
  $('all-videos').addEventListener('click', function () { setView({ type: 'all' }); });
  $('mode-btn').addEventListener('click', function () {
    window.location.href = '/?token=' + encodeURIComponent(TOKEN);
  });
  $('theme-btn').addEventListener('click', function () {
    applyTheme(effectiveTheme() === 'dark' ? 'light' : 'dark', true);
  });
  $('refresh-btn').addEventListener('click', async function () {
    try {
      await loadSummary();
      await loadSidebar();
      await loadVideos();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $('select-scope-btn').addEventListener('click', toggleCurrentScope);
  $('largest-btn').addEventListener('click', selectLargest);
  $('clear-selected-btn').addEventListener('click', clearSelection);
  $('load-more').addEventListener('click', appendNextPage);
  $('size-filter').addEventListener('change', function () {
    state.minBytes = Number(this.value) || 0;
    renderView();
  });
  $('compressed-filter').addEventListener('click', function (event) {
    const button = event.target.closest('.filter-seg-btn');
    if (!button) return;
    state.compressedFilter = button.dataset.value;
    this.querySelectorAll('.filter-seg-btn').forEach(function (item) {
      const active = item === button;
      item.classList.toggle('active', active);
      item.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    renderView();
  });
  $('range-select').addEventListener('click', selectDateRange);
  $('sort-select').addEventListener('change', function () {
    state.sortMode = this.value;
    renderView();
  });
  $('preset').addEventListener('change', function () {
    state.preset = this.value;
  });
  $('compress-btn').addEventListener('click', compressSelected);
  $('video-preview-close').addEventListener('click', closePreview);
  $('video-preview').addEventListener('click', function (event) {
    if (event.target === $('video-preview')) closePreview();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !$('video-preview').classList.contains('hidden')) {
      closePreview();
    }
  });
}

async function init() {
  bindEvents();
  initTheme();
  try {
    await loadSummary();
    await loadSidebar();
    await loadVideos();
  } catch (error) {
    toast(error.message, true);
    $('device-line').textContent = '加载失败：' + error.message;
  }
}

init();
