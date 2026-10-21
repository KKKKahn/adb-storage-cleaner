'use strict';

const TOKEN = document.querySelector('meta[name="adbclean-token"]').content;
const PAGE_SIZE = 200;
const BATCH_SUGGESTION = 200;
const EST_RATIO = {
  visually_lossless: 0.575,
  balanced: 0.44,
  small: 0.30,
  lossless: 1.0,
  custom: 0.44,
};
const SPEED_LABELS = {
  hardware: '硬件加速',
  veryfast: '快',
  medium: '均衡',
  slow: '最小体积',
};

const state = {
  summary: null,
  dates: [],
  months: [],
  albums: [],
  view: null,
  sideTab: 'date',
  photosByMp4: new Map(),
  selected: new Map(),
  expandedMonths: new Set(),
  rawPhotos: [],
  viewGrouped: false,
  currentPhotos: [],
  renderedCount: 0,
  lastDividerDate: '',
  sortMode: 'time',
  minBytes: 0,
  job: null,
  jobRows: new Map(),
  jobRowsId: '',
  actionsSignature: '',
  poll: null,
  events: null,
  jobInflight: false,
  jobFinished: false,
  compressJob: null,
  uncheckedResults: new Set(),
  clearedSessions: new Set(),
  viewToken: 0,
  starting: false,
  speedInitialized: false,
  busy: false,
  ffmpegOk: true,
  notice: null,
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

function formatDuration(ms) {
  if (!ms) return '时长未知';
  return (ms / 1000).toFixed(1) + ' 秒';
}

function basename(path) {
  return String(path || '').split('/').pop();
}

function weekday(dateText) {
  const parts = dateText.split('-').map(Number);
  if (parts.length !== 3) return '';
  const date = new Date(parts[0], parts[1] - 1, parts[2]);
  return ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][date.getDay()] || '';
}

function thumbUrl(photo) {
  return '/api/thumb?path=' + encodeURIComponent(photo.mp4) +
    '&token=' + encodeURIComponent(TOKEN);
}

function previewUrl(jobId, mp4) {
  return '/api/preview?job=' + encodeURIComponent(jobId) +
    '&path=' + encodeURIComponent(mp4) + '&token=' + encodeURIComponent(TOKEN);
}

function phoneVideoUrl(photo) {
  return '/api/media?path=' + encodeURIComponent(photo.mp4) +
    '&token=' + encodeURIComponent(TOKEN);
}

async function api(path, options) {
  const opts = options || {};
  const headers = Object.assign({}, opts.headers || {});
  headers['X-Adbclean-Token'] = TOKEN;
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
  if (!response.ok) {
    throw new Error(data.error || ('请求失败 (' + response.status + ')'));
  }
  return data;
}

function toast(message, isError) {
  const node = $('toast');
  node.textContent = message;
  node.className = 'toast' + (isError ? ' err' : '');
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(function () {
    node.className = 'toast hidden';
  }, isError ? 6000 : 3200);
}

function showNotice(message, tone) {
  const node = $('notice');
  if (!message) {
    node.className = 'notice hidden';
    node.textContent = '';
    return;
  }
  node.textContent = message;
  node.className = 'notice' + (tone ? ' ' + tone : '');
}

/* --------------------------------------------------------------- bootstrap */

async function init() {
  bindEvents();
  setupInfiniteScroll();
  updateSelectionUI();
  await loadSummary();
  await loadDates();
  if (state.dates.length) {
    const newest = state.dates[0];
    state.expandedMonths.add(newest.date.slice(0, 7));
    await showDate(newest.date);
  } else {
    showEmpty('没有发现「JPG + MP4」配对的实况照片。');
  }
}

async function loadSummary() {
  state.summary = await api('/api/summary');
  const summary = state.summary;
  const device = summary.device || {};
  $('device-line').textContent =
    (device.model || 'Android 设备') +
    (device.android ? ' · Android ' + device.android : '') +
    ' · 扫描于 ' + (summary.scanned_at || '未知');
  $('total-pairs').textContent = (summary.totals.pairs || 0).toLocaleString('zh-CN');
  $('total-size').textContent = formatBytes(summary.totals.bytes);
  $('backup-size').textContent = formatBytes(summary.backup_bytes);
  state.ffmpegOk = Boolean(summary.ffmpeg && summary.ffmpeg.ok);
  const speedSelect = $('speed-select');
  const hardwareOption = speedSelect.querySelector('option[value="hardware"]');
  if (summary.hardware && summary.hardware.ok) {
    if (!state.speedInitialized) {
      speedSelect.value = 'hardware';
      state.speedInitialized = true;
    }
  } else {
    if (hardwareOption) hardwareOption.remove();
    if (speedSelect.value === 'hardware') speedSelect.value = 'veryfast';
    state.speedInitialized = true;
  }
  let notice = null;
  if (!state.ffmpegOk) {
    notice = { message: '未找到 ffmpeg/ffprobe，压缩功能不可用。安装后重启：brew install ffmpeg', tone: 'err' };
  } else if (summary.local_free && summary.local_free < 5 * 1000 * 1000 * 1000) {
    notice = {
      message: '本机磁盘剩余 ' + formatBytes(summary.local_free) + '，批量压缩可能空间不足，建议分批。',
      tone: 'warn',
    };
  } else if (summary.hardware && !summary.hardware.ok) {
    notice = {
      message: '当前系统未检测到硬件编码，将使用软件 veryfast；功能完整，速度会比 macOS 硬件编码慢。',
      tone: '',
    };
  } else if (summary.scanned_at) {
    const scanned = Date.parse(summary.scanned_at);
    if (!Number.isNaN(scanned) && Date.now() - scanned > 2 * 3600 * 1000) {
      notice = {
        message: '扫描缓存生成于 ' + summary.scanned_at.replace('T', ' ').slice(0, 16) +
          '；最近新增或移动的照片可能没有显示。点「重新扫描」更新。',
        tone: 'warn',
      };
    }
  }
  state.notice = notice;
  updateSelectionUI();
}

async function loadDates() {
  const data = await api('/api/dates');
  state.dates = data.dates || [];
  state.months = data.months || [];
  state.albums = data.albums || [];
  renderSidebar();
}

/* ----------------------------------------------------------------- sidebar */

function selectedCountByDate() {
  const map = new Map();
  state.selected.forEach(function (photo) {
    map.set(photo.date, (map.get(photo.date) || 0) + 1);
  });
  return map;
}

function selectedCountByMonth() {
  const map = new Map();
  state.selected.forEach(function (photo) {
    const month = photo.date.slice(0, 7);
    map.set(month, (map.get(month) || 0) + 1);
  });
  return map;
}

function selectedCountByAlbum() {
  const map = new Map();
  state.selected.forEach(function (photo) {
    map.set(photo.album, (map.get(photo.album) || 0) + 1);
  });
  return map;
}

function updateSelectionBadges() {
  const dateCounts = selectedCountByDate();
  const monthCounts = selectedCountByMonth();
  const albumCounts = selectedCountByAlbum();
  document.querySelectorAll('.date-row[data-date]').forEach(function (row) {
    const badge = row.querySelector('.date-selected');
    const count = dateCounts.get(row.dataset.date) || 0;
    if (badge) badge.textContent = count ? '已选 ' + count : '';
  });
  document.querySelectorAll('.month-group[data-month]').forEach(function (group) {
    const month = group.dataset.month;
    const count = monthCounts.get(month) || 0;
    const badge = group.querySelector('.month-selected');
    if (badge) badge.textContent = count ? '已选 ' + count : '';
    const button = group.querySelector('.month-head .mini');
    const monthRow = state.months.find(function (row) { return row.month === month; });
    if (button && monthRow) {
      button.textContent = count >= monthRow.count && monthRow.count > 0 ? '取消本月' : '全选本月';
      button.classList.toggle('on', count > 0);
    }
  });
  document.querySelectorAll('.album-row[data-album]').forEach(function (row) {
    const badge = row.querySelector('.date-selected');
    const count = albumCounts.get(row.dataset.album) || 0;
    if (badge) badge.textContent = count ? '已选 ' + count : '';
  });
}

function updateSelectedHeader() {
  const photos = Array.from(state.selected.values());
  const bytes = photos.reduce(function (sum, photo) { return sum + photo.size; }, 0);
  if (state.view && state.view.type === 'selected') {
    $('view-sub').textContent = photos.length + ' 张 · ' + formatBytes(bytes) +
      ' · 压缩前可再次核对';
  }
}

function renderSidebar() {
  const query = ($('search').value || '').trim();
  const list = $('month-list');
  const scrollTop = list.scrollTop;
  list.textContent = '';
  if (state.sideTab === 'album') {
    renderAlbumList(list, query);
    list.scrollTop = scrollTop;
    return;
  }
  const counts = selectedCountByDate();
  const monthCounts = selectedCountByMonth();

  state.months.forEach(function (monthRow) {
    const month = monthRow.month;
    let dates = state.dates.filter(function (row) {
      return row.date.slice(0, 7) === month;
    });
    const monthMatches = !query || month.indexOf(query) !== -1;
    if (query && !monthMatches) {
      dates = dates.filter(function (row) { return row.date.indexOf(query) !== -1; });
      if (!dates.length) return;
    }
    const open = query ? true : state.expandedMonths.has(month);
    const group = document.createElement('div');
    group.className = 'month-group' + (open ? ' open' : '');
    group.dataset.month = month;

    const head = document.createElement('div');
    head.className = 'month-head';

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'month-toggle';
    const caret = document.createElement('span');
    caret.className = 'caret';
    caret.textContent = '▸';
    toggle.appendChild(caret);
    const label = document.createElement('span');
    label.textContent = month;
    toggle.appendChild(label);
    const meta = document.createElement('span');
    meta.className = 'month-meta';
    meta.textContent = monthRow.count.toLocaleString('zh-CN') + ' 张 · ' + formatBytes(monthRow.bytes);
    toggle.appendChild(meta);
    toggle.addEventListener('click', function () {
      const isOpen = group.classList.contains('open');
      if (isOpen) {
        state.expandedMonths.delete(month);
        group.classList.remove('open');
      } else {
        state.expandedMonths.add(month);
        group.classList.add('open');
      }
      if (state.view && state.view.type === 'month' && state.view.month === month && isOpen) return;
      showMonth(month);
    });
    head.appendChild(toggle);

    const monthBadge = document.createElement('span');
    monthBadge.className = 'date-selected month-selected';
    const monthSelected = monthCounts.get(month) || 0;
    monthBadge.textContent = monthSelected ? '已选 ' + monthSelected : '';
    head.appendChild(monthBadge);

    const monthSelect = document.createElement('button');
    monthSelect.type = 'button';
    monthSelect.className = 'mini';
    monthSelect.textContent = monthSelected >= monthRow.count && monthRow.count > 0 ? '取消本月' : '全选本月';
    monthSelect.addEventListener('click', function (event) {
      event.stopPropagation();
      toggleMonthSelection(month);
    });
    head.appendChild(monthSelect);
    group.appendChild(head);

    const rows = document.createElement('div');
    rows.className = 'date-rows';
    dates.forEach(function (row) {
      const dayRow = document.createElement('div');
      dayRow.className = 'date-row';
      dayRow.dataset.date = row.date;
      dayRow.tabIndex = 0;
      dayRow.setAttribute('role', 'button');
      if (state.view && state.view.type === 'date' && state.view.date === row.date) {
        dayRow.classList.add('active');
      }

      const dayLabel = document.createElement('span');
      dayLabel.className = 'date-label';
      dayLabel.textContent = row.date.slice(5);
      dayRow.appendChild(dayLabel);

      const dayMeta = document.createElement('span');
      dayMeta.className = 'date-meta';
      dayMeta.textContent = row.count + ' 张 · ' + formatBytes(row.bytes);
      dayRow.appendChild(dayMeta);

      const dayBadge = document.createElement('span');
      dayBadge.className = 'date-selected';
      const daySelected = counts.get(row.date) || 0;
      dayBadge.textContent = daySelected ? '已选 ' + daySelected : '';
      dayRow.appendChild(dayBadge);

      const daySelect = document.createElement('button');
      daySelect.type = 'button';
      daySelect.className = 'mini';
      daySelect.textContent = '全选';
      daySelect.addEventListener('click', function (event) {
        event.stopPropagation();
        toggleDateSelection(row.date);
      });
      dayRow.appendChild(daySelect);

      dayRow.addEventListener('click', function () { showDate(row.date); });
      dayRow.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          showDate(row.date);
        }
      });
      rows.appendChild(dayRow);
    });
    group.appendChild(rows);
    list.appendChild(group);
  });
  list.scrollTop = scrollTop;
}

/* ------------------------------------------------------------------- views */

function showEmpty(message) {
  $('grid').textContent = '';
  $('empty').classList.remove('hidden');
  $('load-more-wrap').classList.add('hidden');
  if (message) {
    $('empty').querySelector('p').textContent = message;
  }
}

function renderAlbumList(list, query) {
  const counts = selectedCountByAlbum();
  const albums = state.albums.filter(function (album) {
    if (!query) return true;
    return (album.name + ' ' + album.parent).toLowerCase().indexOf(query.toLowerCase()) !== -1;
  });
  if (!albums.length) {
    const empty = document.createElement('p');
    empty.className = 'sub';
    empty.style.padding = '12px';
    empty.textContent = '没有匹配的相册。';
    list.appendChild(empty);
    return;
  }
  albums.forEach(function (album) {
    const row = document.createElement('div');
    row.className = 'album-row';
    row.dataset.album = album.album;
    row.tabIndex = 0;
    row.setAttribute('role', 'button');
    if (state.view && state.view.type === 'album' && state.view.album === album.album) {
      row.classList.add('active');
    }
    const main = document.createElement('div');
    main.className = 'album-main';
    const name = document.createElement('span');
    name.className = 'album-name';
    name.textContent = album.name;
    name.title = album.album;
    const path = document.createElement('span');
    path.className = 'album-path';
    path.textContent = album.parent;
    main.appendChild(name);
    main.appendChild(path);
    row.appendChild(main);

    const meta = document.createElement('span');
    meta.className = 'album-meta';
    meta.textContent = album.count.toLocaleString('zh-CN') + ' 张 · ' + formatBytes(album.bytes);
    row.appendChild(meta);

    const badge = document.createElement('span');
    badge.className = 'date-selected';
    const selected = counts.get(album.album) || 0;
    badge.textContent = selected ? '已选 ' + selected : '';
    row.appendChild(badge);

    const select = document.createElement('button');
    select.type = 'button';
    select.className = 'mini';
    select.textContent = '全选';
    select.addEventListener('click', function (event) {
      event.stopPropagation();
      toggleAlbumSelection(album.album);
    });
    row.appendChild(select);

    row.addEventListener('click', function () { showAlbum(album.album); });
    row.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        showAlbum(album.album);
      }
    });
    list.appendChild(row);
  });
}

function sortPhotos(photos) {
  const sorted = photos.slice();
  if (state.sortMode === 'size') {
    sorted.sort(function (a, b) { return b.size - a.size; });
  }
  return sorted;
}

function applyView() {
  let photos = state.rawPhotos;
  if (state.minBytes > 0) {
    photos = photos.filter(function (photo) { return photo.size >= state.minBytes; });
  }
  const grouped = state.viewGrouped && state.sortMode !== 'size';
  const hint = $('filter-hint');
  if (state.minBytes > 0 || state.sortMode === 'size') {
    const total = state.rawPhotos.reduce(function (sum, photo) { return sum + photo.size; }, 0);
    const shown = photos.reduce(function (sum, photo) { return sum + photo.size; }, 0);
    hint.textContent = '显示 ' + photos.length + ' / ' + state.rawPhotos.length +
      ' 张 · ' + formatBytes(shown) + ' / ' + formatBytes(total);
  } else {
    hint.textContent = '';
  }
  renderGrid(sortPhotos(photos), grouped);
}

function startViewLoad() {
  const token = ++state.viewToken;
  const timer = window.setTimeout(function () {
    if (token === state.viewToken) renderSkeleton();
  }, 120);
  return {
    token: token,
    finish: function () {
      window.clearTimeout(timer);
      return token === state.viewToken;
    },
  };
}

function renderSkeleton() {
  const grid = $('grid');
  grid.textContent = '';
  $('empty').classList.add('hidden');
  $('load-more-wrap').classList.add('hidden');
  const fragment = document.createDocumentFragment();
  for (let index = 0; index < 10; index += 1) {
    const card = document.createElement('div');
    card.className = 'skeleton-card';
    const thumb = document.createElement('div');
    thumb.className = 'skeleton-thumb';
    const line1 = document.createElement('div');
    line1.className = 'skeleton-line';
    const line2 = document.createElement('div');
    line2.className = 'skeleton-line short';
    card.appendChild(thumb);
    card.appendChild(line1);
    card.appendChild(line2);
    fragment.appendChild(card);
  }
  grid.appendChild(fragment);
  $('view-sub').textContent = '正在读取配对清单…';
}

function viewFailed(error) {
  toast(error.message, true);
  showEmpty('读取失败：' + error.message);
}

async function showDate(date) {
  const load = startViewLoad();
  let data;
  try {
    data = await api('/api/photos?date=' + encodeURIComponent(date));
  } catch (error) {
    if (load.finish()) viewFailed(error);
    return;
  }
  if (!load.finish()) return;
  const photos = (data.photos || []).slice().sort(function (a, b) {
    return a.time.localeCompare(b.time);
  });
  state.view = { type: 'date', date: date };
  state.expandedMonths.add(date.slice(0, 7));
  photos.forEach(function (photo) { state.photosByMp4.set(photo.mp4, photo); });
  const totalBytes = photos.reduce(function (sum, photo) { return sum + photo.size; }, 0);
  const selectedHere = photos.filter(function (photo) { return state.selected.has(photo.mp4); }).length;
  $('view-title').textContent = date + ' · ' + weekday(date);
  $('view-sub').textContent = photos.length + ' 张 · ' + formatBytes(totalBytes) +
    (selectedHere ? ' · 已选 ' + selectedHere : '');
  $('select-scope-btn').textContent = '全选本日';
  $('select-scope-btn').classList.remove('hidden');
  state.rawPhotos = photos;
  state.viewGrouped = false;
  applyView();
  renderSidebar();
  $('content').scrollTop = 0;
}

async function showMonth(month) {
  const load = startViewLoad();
  let data;
  try {
    data = await api('/api/photos?month=' + encodeURIComponent(month));
  } catch (error) {
    if (load.finish()) viewFailed(error);
    return;
  }
  if (!load.finish()) return;
  const photos = (data.photos || []).slice().sort(function (a, b) {
    if (a.date !== b.date) return b.date.localeCompare(a.date);
    return a.time.localeCompare(b.time);
  });
  state.view = { type: 'month', month: month };
  state.expandedMonths.add(month);
  photos.forEach(function (photo) { state.photosByMp4.set(photo.mp4, photo); });
  const totalBytes = photos.reduce(function (sum, photo) { return sum + photo.size; }, 0);
  const days = new Set(photos.map(function (photo) { return photo.date; })).size;
  const selectedHere = photos.filter(function (photo) { return state.selected.has(photo.mp4); }).length;
  $('view-title').textContent = month;
  $('view-sub').textContent = days + ' 天 · ' + photos.length + ' 张 · ' + formatBytes(totalBytes) +
    (selectedHere ? ' · 已选 ' + selectedHere : '');
  $('select-scope-btn').textContent = '全选本月';
  $('select-scope-btn').classList.remove('hidden');
  state.rawPhotos = photos;
  state.viewGrouped = true;
  applyView();
  renderSidebar();
  $('content').scrollTop = 0;
}

async function showAlbum(album) {
  const load = startViewLoad();
  let data;
  try {
    data = await api('/api/photos?album=' + encodeURIComponent(album));
  } catch (error) {
    if (load.finish()) viewFailed(error);
    return;
  }
  if (!load.finish()) return;
  const photos = (data.photos || []).slice().sort(function (a, b) {
    if (a.date !== b.date) return b.date.localeCompare(a.date);
    return a.time.localeCompare(b.time);
  });
  state.view = { type: 'album', album: album };
  photos.forEach(function (photo) { state.photosByMp4.set(photo.mp4, photo); });
  const totalBytes = photos.reduce(function (sum, photo) { return sum + photo.size; }, 0);
  const days = new Set(photos.map(function (photo) { return photo.date; })).size;
  const selectedHere = photos.filter(function (photo) { return state.selected.has(photo.mp4); }).length;
  const segments = album.split('/').filter(Boolean);
  $('view-title').textContent = (segments[segments.length - 1] || album) + ' · 相册';
  $('view-sub').textContent = album + ' · ' + days + ' 天 · ' + photos.length +
    ' 张 · ' + formatBytes(totalBytes) + (selectedHere ? ' · 已选 ' + selectedHere : '');
  $('select-scope-btn').textContent = '全选本相册';
  $('select-scope-btn').classList.remove('hidden');
  state.rawPhotos = photos;
  state.viewGrouped = true;
  applyView();
  renderSidebar();
  $('content').scrollTop = 0;
}

function showSelected() {
  state.viewToken += 1;
  const photos = Array.from(state.selected.values()).sort(function (a, b) {
    if (a.date !== b.date) return b.date.localeCompare(a.date);
    return a.time.localeCompare(b.time);
  });
  state.view = { type: 'selected' };
  const totalBytes = photos.reduce(function (sum, photo) { return sum + photo.size; }, 0);
  $('view-title').textContent = '已选照片';
  $('view-sub').textContent = photos.length + ' 张 · ' + formatBytes(totalBytes) +
    ' · 压缩前可再次核对';
  $('select-scope-btn').classList.add('hidden');
  state.rawPhotos = photos;
  state.viewGrouped = true;
  applyView();
  renderSidebar();
  $('content').scrollTop = 0;
}

function renderGrid(photos, grouped) {
  $('grid').textContent = '';
  $('empty').classList.add('hidden');
  state.currentPhotos = photos;
  state.renderedCount = 0;
  state.lastDividerDate = '';
  state.grouped = grouped;
  if (!photos.length) {
    showEmpty(state.view && state.view.type === 'selected'
      ? '还没有选中的照片。勾选缩略图后再回来。'
      : '这一天没有可显示的实况照片。');
    return;
  }
  appendNextPage();
}

let loadObserver = null;

function setupInfiniteScroll() {
  if (loadObserver || !('IntersectionObserver' in window)) return;
  loadObserver = new IntersectionObserver(
    function (entries) {
      if (!entries[0].isIntersecting) return;
      if ($('load-more-wrap').classList.contains('hidden')) return;
      appendNextPage();
    },
    { root: $('content'), rootMargin: '600px 0px' }
  );
  loadObserver.observe($('load-more-wrap'));
}

function appendNextPage() {
  const photos = state.currentPhotos;
  if (!photos.length || state.renderedCount >= photos.length) return;
  const end = Math.min(state.renderedCount + PAGE_SIZE, photos.length);
  const fragment = document.createDocumentFragment();
  for (let index = state.renderedCount; index < end; index += 1) {
    const photo = photos[index];
    if (state.grouped && photo.date !== state.lastDividerDate) {
      fragment.appendChild(makeDateDivider(photo.date));
      state.lastDividerDate = photo.date;
    }
    fragment.appendChild(makeCard(photo));
  }
  $('grid').appendChild(fragment);
  state.renderedCount = end;
  const more = state.renderedCount < photos.length;
  $('load-more-wrap').classList.toggle('hidden', !more);
  if (more) {
    $('load-more').textContent = '加载更多（剩余 ' + (photos.length - state.renderedCount) + ' 张）';
  }
}

function makeDateDivider(date) {
  const photos = state.currentPhotos.filter(function (photo) { return photo.date === date; });
  const size = photos.reduce(function (sum, photo) { return sum + photo.size; }, 0);
  const selected = photos.filter(function (photo) { return state.selected.has(photo.mp4); }).length;
  const divider = document.createElement('div');
  divider.className = 'date-divider';
  divider.dataset.date = date;

  const left = document.createElement('div');
  left.textContent = date + ' · ' + weekday(date) + '  ';
  const meta = document.createElement('span');
  meta.className = 'divider-meta';
  meta.textContent = photos.length + ' 张 · ' + formatBytes(size) +
    (selected ? ' · 已选 ' + selected : '');
  left.appendChild(meta);
  divider.appendChild(left);

  const actions = document.createElement('div');
  actions.className = 'divider-actions';
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'mini';
  button.textContent = selected === photos.length && photos.length ? '取消本日' : '全选本日';
  button.addEventListener('click', function () {
    togglePhotos(state.currentPhotos.filter(function (photo) {
      return photo.date === date;
    }), date);
  });
  actions.appendChild(button);
  divider.appendChild(actions);
  return divider;
}

function makeCard(photo) {
  const card = document.createElement('article');
  card.className = 'card';
  card.dataset.mp4 = photo.mp4;
  if (state.selected.has(photo.mp4)) card.classList.add('selected');

  if (photo.compressed) {
    card.classList.add('is-compressed');
    const badge = document.createElement('span');
    badge.className = 'compressed-badge';
    badge.textContent = '✓';
    badge.setAttribute('aria-label', '已压缩');
    badge.title = '已压缩' + (photo.history_crf ? '（CRF' + photo.history_crf + '）' : '') +
      (photo.history_size ? ' · ' + formatBytes(photo.history_size) : '');
    card.appendChild(badge);
  }

  const checkWrap = document.createElement('label');
  checkWrap.className = 'card-check';
  checkWrap.title = '选择这张照片';
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.checked = state.selected.has(photo.mp4);
  checkbox.addEventListener('change', function (event) {
    event.stopPropagation();
    togglePhoto(photo, checkbox.checked);
  });
  const checkUi = document.createElement('span');
  checkUi.className = 'check-ui';
  checkWrap.appendChild(checkbox);
  checkWrap.appendChild(checkUi);
  card.appendChild(checkWrap);

  const thumb = document.createElement('div');
  thumb.className = 'thumb';
  const img = document.createElement('img');
  img.loading = 'lazy';
  img.decoding = 'async';
  img.alt = '';
  img.src = thumbUrl(photo);
  img.addEventListener('load', function () {
    img.classList.add('loaded');
    thumb.classList.add('loaded');
  });
  img.addEventListener('error', function () {
    img.remove();
    thumb.classList.add('loaded');
    const fallback = document.createElement('div');
    fallback.className = 'fallback';
    fallback.textContent = '缩略图不可用';
    thumb.appendChild(fallback);
  });
  thumb.appendChild(img);
  card.appendChild(thumb);

  const metaRow = document.createElement('div');
  metaRow.className = 'card-meta';
  const time = document.createElement('span');
  time.className = 'time';
  time.textContent = photo.time;
  const size = document.createElement('span');
  size.className = 'size';
  size.textContent = formatBytes(photo.size);
  metaRow.appendChild(time);
  metaRow.appendChild(size);
  card.appendChild(metaRow);

  const foot = document.createElement('div');
  foot.className = 'card-foot';
  const name = document.createElement('span');
  name.className = 'name';
  name.textContent = basename(photo.mp4).replace(/\.mp4$/i, '');
  name.title = photo.mp4;
  const duration = document.createElement('span');
  duration.className = 'duration';
  duration.textContent = photo.unknown_duration ? '时长未知' : formatDuration(photo.duration_ms);
  foot.appendChild(name);
  foot.appendChild(duration);
  card.appendChild(foot);

  let selectTimer = null;
  card.addEventListener('click', function (event) {
    if (event.target.closest('input, label')) return;
    if (selectTimer) {
      window.clearTimeout(selectTimer);
      selectTimer = null;
      return;
    }
    selectTimer = window.setTimeout(function () {
      selectTimer = null;
      togglePhoto(photo, !state.selected.has(photo.mp4));
    }, 220);
  });
  card.addEventListener('dblclick', function (event) {
    if (event.target.closest('input, label')) return;
    if (selectTimer) {
      window.clearTimeout(selectTimer);
      selectTimer = null;
    }
    openPhonePreview(photo);
  });
  return card;
}

/* --------------------------------------------------------------- selection */

function togglePhoto(photo, checked) {
  if (checked && photo.compressed && !$('recompress-toggle').checked) {
    toast('这张已压缩过；勾选「允许更深压缩」后再选', true);
    document.querySelectorAll('.card').forEach(function (card) {
      if (card.dataset.mp4 !== photo.mp4) return;
      card.classList.remove('selected');
      const input = card.querySelector('input');
      if (input) input.checked = false;
    });
    return;
  }
  if (checked) {
    state.selected.set(photo.mp4, photo);
  } else {
    state.selected.delete(photo.mp4);
    state.uncheckedResults.add(photo.mp4);
  }
  document.querySelectorAll('.card').forEach(function (card) {
    if (card.dataset.mp4 !== photo.mp4) return;
    card.classList.toggle('selected', checked);
    const input = card.querySelector('input');
    if (input) input.checked = checked;
    if (!checked && state.view && state.view.type === 'selected') card.remove();
  });
  updateSelectionUI();
  updateSelectionBadges();
  if (state.view && state.view.type === 'selected') {
    state.rawPhotos = state.rawPhotos.filter(function (item) { return item.mp4 !== photo.mp4; });
    updateSelectedHeader();
    if (!state.selected.size) {
      showEmpty('还没有选中的照片。勾选缩略图后再回来。');
      $('view-sub').textContent = '0 张';
    }
  } else {
    refreshDividers();
  }
}

async function photosForDate(date) {
  const data = await api('/api/photos?date=' + encodeURIComponent(date));
  const photos = data.photos || [];
  photos.forEach(function (photo) { state.photosByMp4.set(photo.mp4, photo); });
  return photos;
}

async function photosForMonth(month) {
  const data = await api('/api/photos?month=' + encodeURIComponent(month));
  const photos = data.photos || [];
  photos.forEach(function (photo) { state.photosByMp4.set(photo.mp4, photo); });
  return photos;
}

async function photosForAlbum(album) {
  const data = await api('/api/photos?album=' + encodeURIComponent(album));
  const photos = data.photos || [];
  photos.forEach(function (photo) { state.photosByMp4.set(photo.mp4, photo); });
  return photos;
}

function togglePhotos(photos, label) {
  if (!photos.length) {
    toast('没有可选择的照片', true);
    return false;
  }
  const recompress = $('recompress-toggle').checked;
  const usable = photos.filter(function (photo) {
    return recompress || !photo.compressed;
  });
  const skipped = photos.length - usable.length;
  if (!usable.length) {
    toast('这些照片都已压缩过；勾选「允许更深压缩」后再选', true);
    return false;
  }
  const allSelected = usable.every(function (photo) {
    return state.selected.has(photo.mp4);
  });
  usable.forEach(function (photo) {
    if (allSelected) state.selected.delete(photo.mp4);
    else state.selected.set(photo.mp4, photo);
  });
  syncVisibleCards();
  updateSelectionUI();
  updateSelectionBadges();
  if (state.view && state.view.type === 'selected') showSelected();
  else refreshDividers();
  toast((allSelected
    ? '已取消 ' + label + ' 的 ' + usable.length + ' 张'
    : '已选中 ' + label + ' 的 ' + usable.length + ' 张') +
    (skipped ? '；跳过 ' + skipped + ' 张已压缩照片' : ''));
  return allSelected;
}

async function toggleDateSelection(date) {
  try {
    togglePhotos(await photosForDate(date), date);
  } catch (error) {
    toast(error.message, true);
  }
}

async function toggleMonthSelection(month) {
  try {
    togglePhotos(await photosForMonth(month), month);
  } catch (error) {
    toast(error.message, true);
  }
}

async function toggleAlbumSelection(album) {
  try {
    const segments = album.split('/').filter(Boolean);
    togglePhotos(await photosForAlbum(album), segments[segments.length - 1] || album);
  } catch (error) {
    toast(error.message, true);
  }
}

function toggleDisplayedScope() {
  if (!state.view || state.view.type === 'selected') return;
  if (!state.currentPhotos.length) {
    toast('当前筛选结果为空，先调整体积筛选', true);
    return;
  }
  const labels = { date: state.view.date, month: state.view.month, album: state.view.album };
  togglePhotos(state.currentPhotos.slice(), labels[state.view.type] || '当前范围');
}

async function selectLargest() {
  try {
    const data = await api('/api/largest?limit=50');
    const recompress = $('recompress-toggle').checked;
    const photos = (data.photos || []).filter(function (photo) {
      return recompress || !photo.compressed;
    });
    if (!photos.length) {
      toast('最大的 50 个都已压缩过；勾选「允许更深压缩」后再试', true);
      return;
    }
    photos.forEach(function (photo) { state.selected.set(photo.mp4, photo); });
    updateSelectionUI();
    syncVisibleCards();
    renderSidebar();
    showSelected();
    toast('已选中体积最大的 ' + photos.length + ' 个');
  } catch (error) {
    toast(error.message, true);
  }
}

function syncVisibleCards() {
  document.querySelectorAll('.card').forEach(function (card) {
    const checked = state.selected.has(card.dataset.mp4);
    card.classList.toggle('selected', checked);
    const input = card.querySelector('input');
    if (input) input.checked = checked;
  });
}

function refreshDividers() {
  const dividers = document.querySelectorAll('.date-divider');
  const visible = new Set(Array.from(document.querySelectorAll('.card')).map(function (card) {
    return card.dataset.mp4;
  }));
  dividers.forEach(function (divider) {
    const date = divider.dataset.date;
    if (!date) return;
    const photos = state.currentPhotos.filter(function (photo) {
      return photo.date === date && visible.has(photo.mp4);
    });
    const selected = photos.filter(function (photo) { return state.selected.has(photo.mp4); }).length;
    const meta = divider.querySelector('.divider-meta');
    if (meta) {
      const size = photos.reduce(function (sum, photo) { return sum + photo.size; }, 0);
      meta.textContent = photos.length + ' 张 · ' + formatBytes(size) +
        (selected ? ' · 已选 ' + selected : '');
    }
    const button = divider.querySelector('button');
    if (button) button.textContent = selected === photos.length && photos.length ? '取消本日' : '全选本日';
  });
}

function updateSelectionUI() {
  const photos = Array.from(state.selected.values());
  const bytes = photos.reduce(function (sum, photo) { return sum + photo.size; }, 0);
  $('selected-count').textContent = photos.length.toLocaleString('zh-CN');
  $('selected-size').textContent = formatBytes(bytes);
  $('selection-summary').textContent = photos.length
    ? '已选 ' + photos.length + ' 个 · ' + formatBytes(bytes)
    : '未选择';
  $('selected-view-btn').textContent = '已选 ' + photos.length;

  const preset = $('preset').value;
  const ratio = EST_RATIO[preset] || 0.44;
  const localFree = state.summary ? Number(state.summary.local_free) || 0 : 0;
  const needed = Math.round(bytes * 1.5) + 256 * 1024 * 1024;
  if (!photos.length) {
    $('selection-estimate').textContent = '';
  } else if (preset === 'lossless') {
    $('selection-estimate').textContent = '无损重封装：画质不变，体积几乎不会减小（估算）';
  } else {
    const estimated = bytes * ratio;
    $('selection-estimate').textContent = '预计约 ' + formatBytes(estimated) +
      ' · 可省约 ' + formatBytes(Math.max(0, bytes - estimated)) + '（估算）';
  }
  if (photos.length && needed > localFree && localFree > 0) {
    $('selection-estimate').textContent += ' · 空间不足时超出部分会自动跳过';
  }
  const suggestion = (state.summary && state.summary.batch_suggestion) || BATCH_SUGGESTION;
  const average = photos.length ? bytes / photos.length : 0;
  const capacity = average > 0 && localFree > 0
    ? Math.max(1, Math.floor((localFree * 0.8) / (average * 1.5)))
    : suggestion;
  if (photos.length > suggestion) {
    showNotice(
      '建议每批约 ' + suggestion + ' 个；当前已选 ' + photos.length +
      ' 个，仍可继续。按当前空间约可处理 ' + capacity +
      ' 个，超出部分会因空间不足跳过；已压缩照片默认不重复处理。',
      'warn'
    );
  } else if (photos.length && needed > localFree && localFree > 0) {
    showNotice(
      '本机空间偏紧：本批约需 ' + formatBytes(needed) + '，可用 ' +
      formatBytes(localFree) + '。会先处理能容纳的文件，剩余标记为空间不足。',
      'warn'
    );
  } else {
    refreshNotice();
  }
  updateBusy();
}

function refreshNotice() {
  if (state.notice) showNotice(state.notice.message, state.notice.tone);
  else showNotice('');
}

function updateBusy() {
  const busy = state.busy || state.starting;
  const selected = state.selected.size;
  $('compress-btn').disabled = busy || selected === 0 || !state.ffmpegOk;
  $('rescan-btn').disabled = busy;
  $('refresh-btn').disabled = busy;
  $('select-scope-btn').disabled = busy;
  $('bar-selected-btn').disabled = selected === 0;
  $('compress-btn').textContent = busy
    ? (state.starting ? '准备中…' : '任务进行中…')
    : ('压缩所选' + (selected ? '（' + selected + '）' : ''));
}

/* -------------------------------------------------------------------- job */

function jobTitle(kind) {
  if (kind === 'compress') return '压缩任务';
  if (kind === 'apply') return '应用到手机';
  if (kind === 'restore') return '恢复原文件';
  if (kind === 'restore-backup') return '从电脑备份恢复';
  if (kind === 'scan') return '重新扫描';
  return '任务';
}

function jobStatusLabel(status) {
  if (status === 'running') return '进行中';
  if (status === 'done') return '已完成';
  if (status === 'error') return '失败';
  return status;
}

function itemStatusLabel(status) {
  const labels = {
    pending: '待处理', running: '处理中', ready: '可应用', skipped: '已跳过',
    error: '错误', applied: '已应用', restored: '已恢复',
  };
  return labels[status] || status;
}

function showJob(jobId, kind) {
  if (state.poll) window.clearInterval(state.poll);
  if (state.events) { state.events.close(); state.events = null; }
  state.jobFinished = false;
  state.jobInflight = false;
  $('job-panel').classList.remove('hidden');
  $('job-panel').classList.remove('collapsed');
  $('job-collapse').textContent = '收起详情';
  state.jobRowsId = '';
  state.jobRows = new Map();
  state.actionsSignature = '';
  state.busy = true;
  state.job = { id: jobId, kind: kind, status: 'running', items: [], done: 0, total: 0, progress: 0 };
  $('job-title').textContent = jobTitle(kind);
  $('job-status').textContent = '进行中';
  $('job-status').className = 'pill running';
  $('job-items').textContent = '';
  $('job-actions').textContent = '';
  $('job-counts').textContent = '';
  $('job-progress').style.width = '0%';
  $('job-message').textContent = '正在准备…';
  updateBusy();
  $('job-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  async function finalize() {
    if (state.jobFinished) return;
    state.jobFinished = true;
    if (state.poll) { window.clearInterval(state.poll); state.poll = null; }
    if (state.events) { state.events.close(); state.events = null; }
    try {
      const job = await api('/api/jobs/' + encodeURIComponent(jobId));
      state.job = job;
      renderJob(job);
      state.busy = false;
      updateBusy();
      await afterJob(job);
    } catch (error) {
      state.busy = false;
      updateBusy();
      toast(error.message, true);
    }
  }

  async function tick() {
    if (state.jobFinished || state.jobInflight) return;
    state.jobInflight = true;
    try {
      const job = await api('/api/jobs/' + encodeURIComponent(jobId));
      if (state.jobFinished) return;
      state.job = job;
      renderJob(job);
      if (job.status !== 'running') await finalize();
    } catch (error) {
      if (!state.events) {
        if (state.poll) { window.clearInterval(state.poll); state.poll = null; }
        state.busy = false;
        updateBusy();
        toast(error.message, true);
      }
    } finally {
      state.jobInflight = false;
    }
  }

  tick();
  state.poll = window.setInterval(tick, 1500);

  if (window.EventSource) {
    const events = new EventSource(
      '/api/jobs/' + encodeURIComponent(jobId) + '/events?token=' + encodeURIComponent(TOKEN)
    );
    state.events = events;
    events.onmessage = function (event) {
      if (state.jobFinished) return;
      let progress;
      try { progress = JSON.parse(event.data); } catch (error) { return; }
      state.job = Object.assign({}, state.job, progress);
      renderJobProgress(progress);
      if (progress.status !== 'running') finalize();
    };
    events.onerror = function () {
      events.close();
      if (state.events === events) state.events = null;
    };
  }
}

function renderJobProgress(job) {
  $('job-panel').classList.toggle('job-done', job.status !== 'running');
  const pill = $('job-status');
  pill.textContent = jobStatusLabel(job.status);
  pill.className = 'pill ' + job.status;
  const percent = job.total > 0
    ? Math.min(100, Math.round((job.progress != null ? job.progress : job.done / job.total) * 100))
    : (job.status === 'done' ? 100 : 0);
  $('job-progress').style.width = percent + '%';
  $('job-message').textContent = job.error || job.message || '';
  if (job.status === 'running') {
    $('job-counts').textContent = job.total ? job.done + ' / ' + job.total : '';
  }
}

function renderJob(job) {
  $('job-title').textContent = jobTitle(job.kind);
  renderJobProgress(job);
  const pill = $('job-status');
  pill.textContent = jobStatusLabel(job.status);
  pill.className = 'pill ' + job.status;
  if (job.status !== 'running' && job.items.length) {
    const good = job.items.filter(function (item) {
      return item.status === 'ready' || item.status === 'applied' || item.status === 'restored';
    }).length;
    const skipped = job.items.filter(function (item) { return item.status === 'skipped'; }).length;
    const errors = job.items.filter(function (item) { return item.status === 'error'; }).length;
    $('job-counts').textContent = '完成 ' + good + ' · 跳过 ' + skipped + ' · 失败 ' + errors;
  } else if (job.status !== 'running') {
    $('job-counts').textContent = '';
  }
  renderJobRows(job);
  renderJobActions(job);
}

function renderJobRows(job) {
  if (state.jobRowsId !== job.id) {
    state.jobRowsId = job.id;
    state.jobRows = new Map();
    $('job-items').textContent = '';
  }
  job.items.forEach(function (item) {
    let row = state.jobRows.get(item.mp4);
    if (!row || row.dataset.status !== item.status) {
      const fresh = buildResultRow(item, job);
      if (row) row.replaceWith(fresh);
      else $('job-items').appendChild(fresh);
      state.jobRows.set(item.mp4, fresh);
    }
  });
}

function buildResultRow(item, job) {
  const row = document.createElement('div');
  row.className = 'result-row';
  row.dataset.status = item.status;
  row.dataset.mp4 = item.mp4;

  if (job.kind === 'compress' && item.status === 'ready') {
    const checkWrap = document.createElement('label');
    checkWrap.className = 'r-check';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.dataset.apply = '1';
    checkbox.dataset.mp4 = item.mp4;
    checkbox.checked = !state.uncheckedResults.has(item.mp4);
    checkbox.addEventListener('change', function () {
      if (checkbox.checked) state.uncheckedResults.delete(item.mp4);
      else state.uncheckedResults.add(item.mp4);
    });
    checkWrap.appendChild(checkbox);
    row.appendChild(checkWrap);
  }

  const main = document.createElement('div');
  main.className = 'r-main';
  const name = document.createElement('div');
  name.className = 'r-name';
  const time = document.createElement('strong');
  time.textContent = item.time;
  const badge = document.createElement('span');
  badge.className = 'badge';
  badge.textContent = itemStatusLabel(item.status);
  if (item.status === 'ready') badge.classList.add('ok');
  if (item.status === 'error') badge.classList.add('err');
  if (item.status === 'skipped') badge.classList.add('warn');
  name.appendChild(time);
  name.appendChild(badge);
  main.appendChild(name);

  const path = document.createElement('div');
  path.className = 'r-path';
  path.textContent = basename(item.mp4);
  path.title = item.mp4;
  main.appendChild(path);

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
    const pct = item.original_size ? Math.round(saved * 100 / item.original_size) : 0;
    numbers.textContent = formatBytes(item.original_size) + ' → ' +
      formatBytes(item.compressed_size) + ' · 省 ' + pct + '%';
    const detail = [];
    if (item.ssim) detail.push('SSIM ' + item.ssim.toFixed(4));
    if (item.elapsed_ms) detail.push('耗时 ' + (item.elapsed_ms / 1000).toFixed(1) + 's');
    if (detail.length) {
      const extra = document.createElement('div');
      extra.className = 'r-ssim';
      extra.textContent = detail.join(' · ');
      numbers.appendChild(extra);
    }
  } else if (item.original_size) {
    numbers.textContent = formatBytes(item.original_size);
  }
  row.appendChild(numbers);

  if (job.kind === 'compress' && item.status === 'ready') {
    const preview = document.createElement('button');
    preview.type = 'button';
    preview.className = 'ghost';
    preview.textContent = '预览';
    preview.addEventListener('click', function () {
      if (!state.compressJob) return;
      openPreview(item);
    });
    row.appendChild(preview);
  }
  return row;
}

function renderJobActions(job) {
  const signature = job.id + ':' + job.status + ':' +
    (job.cancel_requested ? '1' : '0') + ':' +
    job.items.map(function (item) { return item.status; }).join('');
  if (signature === state.actionsSignature) return;
  state.actionsSignature = signature;
  const actions = $('job-actions');
  actions.textContent = '';

  if (job.kind === 'compress' && job.status === 'running') {
    const stop = document.createElement('button');
    stop.type = 'button';
    stop.className = 'ghost danger-btn';
    stop.textContent = job.cancel_requested ? '正在停止…' : '停止任务';
    stop.disabled = Boolean(job.cancel_requested);
    stop.addEventListener('click', function () { cancelJob(job.id); });
    actions.appendChild(stop);
  }

  if (job.kind === 'compress' && job.status === 'done') {
    const ready = job.items.filter(function (item) { return item.status === 'ready'; });
    if (ready.length) {
      const hint = document.createElement('span');
      hint.className = 'sub';
      hint.textContent = '先预览确认，再应用到手机；应用前会自动备份原 MP4。';
      actions.appendChild(hint);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'primary';
      button.textContent = '应用所选（' + ready.length + '）';
      button.addEventListener('click', applyResults);
      actions.appendChild(button);
    } else if (job.items.every(function (item) { return item.status !== 'pending' && item.status !== 'running'; })) {
      const hint = document.createElement('span');
      hint.className = 'sub';
      hint.textContent = '没有可应用的结果：看看每行的跳过/错误原因。';
      actions.appendChild(hint);
    }
  }

  if (job.kind === 'apply' && job.status === 'done') {
    const applied = job.items.filter(function (item) { return item.status === 'applied'; });
    const backedUp = applied.find(function (item) { return item.backup; });
    const session = backedUp ? backedUp.backup.split('/')[0] : '';
    if (applied.length) {
      if (session && state.clearedSessions.has(session)) {
        const hint = document.createElement('span');
        hint.className = 'sub';
        hint.textContent = '本批备份已清除，无法再从本机恢复。';
        actions.appendChild(hint);
      } else {
        if (session) {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'ghost';
          button.textContent = '恢复原文件（' + applied.length + '）';
          button.addEventListener('click', restoreResults);
          actions.appendChild(button);
        }
        if (session) {
          const clear = document.createElement('button');
          clear.type = 'button';
          clear.className = 'ghost danger-btn';
          clear.textContent = '清除本批备份';
          clear.addEventListener('click', function () {
            clearAppliedBackups(session, applied.length);
          });
          actions.appendChild(clear);
        }
      }
    }
  }

  if (job.kind === 'scan' && job.status === 'done') {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'ghost';
    button.textContent = '刷新列表';
    button.addEventListener('click', async function () {
      await loadSummary();
      await loadDates();
      await reloadView();
    });
    actions.appendChild(button);
  }
}

async function cancelJob(jobId) {
  try {
    await api('/api/jobs/' + encodeURIComponent(jobId) + '/cancel', {
      method: 'POST',
      body: '{}',
    });
    if (state.job) {
      state.job.cancel_requested = true;
      state.job.message = '正在停止，等待当前文件结束…';
      state.actionsSignature = '';
      renderJobActions(state.job);
    }
    toast('已请求停止；当前文件结束后不会再开始新文件');
  } catch (error) {
    toast(error.message, true);
  }
}

async function afterJob(job) {
  if (job.kind === 'compress') {
    state.compressJob = job;
    job.items.forEach(function (item) {
      if (item.status !== 'pending') state.selected.delete(item.mp4);
    });
    updateSelectionUI();
    updateSelectionBadges();
    syncVisibleCards();
    toast('压缩完成：' + job.items.filter(function (item) { return item.status === 'ready'; }).length + ' 个可应用');
    return;
  }
  if (job.kind === 'apply') {
    const applied = job.items.filter(function (item) { return item.status === 'applied'; });
    applied.forEach(function (item) { state.selected.delete(item.mp4); });
    updateSelectionUI();
    await loadSummary();
    await loadDates();
    await reloadView();
    toast('已应用 ' + applied.length + ' 个到手机');
    return;
  }
  if (job.kind === 'restore' || job.kind === 'restore-backup') {
    await loadSummary();
    await loadDates();
    await reloadView();
    toast('已恢复 ' + job.items.filter(function (item) { return item.status === 'restored'; }).length + ' 个');
    return;
  }
  if (job.kind === 'scan') {
    await loadSummary();
    await loadDates();
    await reloadView();
    toast('扫描完成');
  }
}

async function reloadView() {
  const view = state.view;
  try {
    if (view && view.type === 'date') {
      const exists = state.dates.some(function (row) { return row.date === view.date; });
      if (exists) return await showDate(view.date);
    }
    if (view && view.type === 'month') {
      const exists = state.months.some(function (row) { return row.month === view.month; });
      if (exists) return await showMonth(view.month);
    }
    if (view && view.type === 'album') {
      const exists = state.albums.some(function (row) { return row.album === view.album; });
      if (exists) return await showAlbum(view.album);
    }
    if (view && view.type === 'selected') return showSelected();
    if (state.dates.length) return await showDate(state.dates[0].date);
    showEmpty('没有发现「JPG + MP4」配对的实况照片。');
  } catch (error) {
    toast(error.message, true);
  }
}

/* ------------------------------------------------------------------ actions */

async function compressSelected() {
  if (state.starting || state.busy) return;
  const photos = Array.from(state.selected.values());
  if (!photos.length) return;
  if (!state.ffmpegOk) {
    toast('未安装 ffmpeg，无法压缩', true);
    return;
  }
  const preset = $('preset').value;
  let crf = null;
  if (preset === 'custom') {
    crf = Number($('crf').value);
    if (!Number.isInteger(crf) || crf < 14 || crf > 30) {
      toast('自定义 CRF 必须在 14–30 之间', true);
      return;
    }
  }
  const bytes = photos.reduce(function (sum, photo) { return sum + photo.size; }, 0);
  const ratio = EST_RATIO[preset] || 0.44;
  const speed = $('speed-select').value;
  const computeSsim = $('ssim-toggle').checked;
  const recompress = $('recompress-toggle').checked;
  const compressedSelected = photos.filter(function (photo) { return photo.compressed; }).length;
  const suggestion = (state.summary && state.summary.batch_suggestion) || BATCH_SUGGESTION;
  const estimate = preset === 'lossless'
    ? '无损重封装体积几乎不变。'
    : '预计压缩后约 ' + formatBytes(bytes * ratio) + '（估算）。';
  const ok = await confirmDialog('开始压缩？', [
    '将压缩 ' + photos.length + ' 个伴生 MP4，原始体积 ' + formatBytes(bytes) + '。',
    estimate,
    '编码速度：' + (SPEED_LABELS[speed] || speed) +
      ' · SSIM：' + (computeSsim ? '计算' : '跳过'),
    compressedSelected
      ? '其中 ' + compressedSelected + ' 张已压缩过，只有更深档位才会再次处理。'
      : '',
    '只处理 MP4，JPG 定格画面不会改动。',
    '压缩结果先保存在本机，预览确认后再应用到手机。',
    photos.length > suggestion
      ? '建议每批约 ' + suggestion + ' 个；空间不足时会先处理可容纳部分，剩余标记为跳过，已应用的下次不会重复处理。'
      : '',
  ]);
  if (!ok) return;
  state.starting = true;
  updateBusy();
  try {
    const data = await api('/api/compress', {
      method: 'POST',
      body: JSON.stringify({
        paths: photos.map(function (photo) { return photo.mp4; }),
        preset: preset,
        crf: crf,
        speed: speed,
        compute_ssim: computeSsim,
        recompress: recompress,
      }),
    });
    if (data.warning) toast(data.warning);
    state.uncheckedResults = new Set();
    showJob(data.job_id, 'compress');
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.starting = false;
    updateBusy();
  }
}

async function applyResults() {
  if (state.starting || state.busy) return;
  const job = state.job;
  if (!job || job.kind !== 'compress') return;
  const checked = Array.from(document.querySelectorAll('#job-items input[data-apply]:checked'))
    .map(function (input) { return input.dataset.mp4; });
  if (!checked.length) {
    toast('没有勾选可应用的结果', true);
    return;
  }
  const items = job.items.filter(function (item) { return checked.indexOf(item.mp4) !== -1; });
  const oldBytes = items.reduce(function (sum, item) { return sum + item.original_size; }, 0);
  const newBytes = items.reduce(function (sum, item) { return sum + item.compressed_size; }, 0);
  const ok = await confirmDialog('替换手机上的 MP4？', [
    '将替换 ' + checked.length + ' 个 MP4：' + formatBytes(oldBytes) + ' → ' + formatBytes(newBytes) + '。',
    '替换前会自动备份原文件到本机 Studio 备份目录。',
    '只替换 MP4，JPG 不动；替换后会重新扫描媒体库。',
    '如果相册动态异常，可立即用「恢复原文件」回滚。',
  ], '确认替换', true);
  if (!ok) return;
  state.starting = true;
  updateBusy();
  try {
    const data = await api('/api/apply', {
      method: 'POST',
      body: JSON.stringify({ job_id: job.id, paths: checked }),
    });
    showJob(data.job_id, 'apply');
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.starting = false;
    updateBusy();
  }
}

async function restoreResults() {
  if (state.starting || state.busy) return;
  const job = state.job;
  if (!job || job.kind !== 'apply' || !state.compressJob) return;
  const paths = job.items.filter(function (item) { return item.status === 'applied'; })
    .map(function (item) { return item.mp4; });
  if (!paths.length) return;
  const ok = await confirmDialog('恢复原始 MP4？', [
    '将把 ' + paths.length + ' 个 MP4 恢复为压缩前的原文件（来自本机备份）。',
    'JPG 始终未改动。',
    '恢复后会重新扫描媒体库。',
  ]);
  if (!ok) return;
  state.starting = true;
  updateBusy();
  try {
    const data = await api('/api/restore', {
      method: 'POST',
      body: JSON.stringify({ job_id: state.compressJob.id, paths: paths }),
    });
    showJob(data.job_id, 'restore');
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.starting = false;
    updateBusy();
  }
}

async function rescan() {
  if (state.starting || state.busy) return;
  const ok = await confirmDialog('重新扫描手机？', [
    '将重新统计 /sdcard 文件并读取 MediaStore 时长，通常需要 30–120 秒。',
    '扫描期间不能开始新的压缩或替换任务。',
  ]);
  if (!ok) return;
  state.starting = true;
  updateBusy();
  try {
    const data = await api('/api/scan', { method: 'POST', body: '{}' });
    showJob(data.job_id, 'scan');
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.starting = false;
    updateBusy();
  }
}

async function refreshAll() {
  try {
    await loadSummary();
    await loadDates();
    await reloadView();
    toast('已刷新');
  } catch (error) {
    toast(error.message, true);
  }
}

/* ----------------------------------------------------------------- backups */

function formatSessionName(name) {
  const match = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(String(name));
  if (!match) return name;
  return match[1] + '-' + match[2] + '-' + match[3] + ' ' +
    match[4] + ':' + match[5] + ':' + match[6];
}

async function openBackups() {
  try {
    const data = await api('/api/backups');
    const sessions = data.sessions || [];
    $('backup-sub').textContent = '原始 MP4 保存在这台电脑：' + data.backup_dir +
      ' · 合计 ' + formatBytes(data.total_bytes);
    $('backup-free').textContent = state.summary
      ? '本机剩余空间 ' + formatBytes(state.summary.local_free) +
        ' · 备份占用 ' + formatBytes(data.total_bytes)
      : '';
    const list = $('backup-list');
    list.textContent = '';
    if (!sessions.length) {
      const empty = document.createElement('p');
      empty.className = 'sub';
      empty.textContent = '还没有备份。每次「应用到手机」前都会自动备份原 MP4。';
      list.appendChild(empty);
    }
    sessions.forEach(function (session) {
      const card = document.createElement('div');
      card.className = 'backup-session';
      const head = document.createElement('div');
      head.className = 'backup-head';
      const title = document.createElement('strong');
      title.textContent = formatSessionName(session.session) + ' 的批次';
      const meta = document.createElement('span');
      meta.className = 'sub';
      meta.textContent = session.count + ' 个 · ' + formatBytes(session.bytes) +
        ' · restore.sh 可手动执行';
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'primary';
      button.textContent = '恢复本批（' + session.count + '）';
      button.addEventListener('click', function () {
        restoreBackupSession(session);
      });
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'ghost danger-btn';
      remove.textContent = '删除本批';
      remove.addEventListener('click', function () {
        deleteBackupSession(session.session, session.count);
      });
      head.appendChild(title);
      head.appendChild(meta);
      head.appendChild(button);
      head.appendChild(remove);
      card.appendChild(head);

      const entries = document.createElement('div');
      entries.className = 'backup-entries hidden';
      let rendered = false;
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'backup-details';
      toggle.textContent = '展开文件列表（' + session.count + '）';

      function renderEntries() {
        if (rendered) return;
        rendered = true;
        const fragment = document.createDocumentFragment();
        session.entries.slice(0, 200).forEach(function (entry) {
          const row = document.createElement('div');
          row.className = 'backup-entry';
          const label = document.createElement('span');
          label.textContent = (entry.date ? entry.date + ' ' + entry.time + ' · ' : '') +
            basename(entry.mp4);
          label.title = entry.mp4;
          const size = document.createElement('span');
          size.textContent = formatBytes(entry.original_size);
          row.appendChild(label);
          row.appendChild(size);
          fragment.appendChild(row);
        });
        if (session.entries.length > 200) {
          const more = document.createElement('div');
          more.className = 'sub';
          more.textContent = '…另有 ' + (session.entries.length - 200) + ' 个';
          fragment.appendChild(more);
        }
        entries.appendChild(fragment);
      }

      function setEntriesOpen(open) {
        if (open) renderEntries();
        entries.classList.toggle('hidden', !open);
        toggle.textContent = open
          ? '收起文件列表'
          : '展开文件列表（' + session.count + '）';
      }

      toggle.addEventListener('click', function () {
        setEntriesOpen(entries.classList.contains('hidden'));
      });
      card.appendChild(toggle);
      card.appendChild(entries);
      if (session.count <= 3) setEntriesOpen(true);
      list.appendChild(card);
    });
    $('backup-modal').classList.remove('hidden');
  } catch (error) {
    toast(error.message, true);
  }
}

async function clearAppliedBackups(sessionName, count) {
  const ok = await confirmDialog('清除本批备份？', [
    '将永久删除这台电脑上的 ' + count + ' 个原始 MP4 备份，无法撤销。',
    '删除后，这些照片无法再恢复成原始动态片段。',
    '请先逐张确认手机相册里的动态效果正常。',
    '批次：' + sessionName,
  ], '永久删除', true);
  if (!ok) return;
  try {
    const data = await api('/api/delete-backups', {
      method: 'POST',
      body: JSON.stringify({ session: sessionName, confirm: 'delete-backups' }),
    });
    state.clearedSessions.add(sessionName);
    toast('已清除本批 ' + data.deleted_files + ' 个备份');
    await loadSummary();
    state.actionsSignature = '';
    if (state.job) renderJobActions(state.job);
  } catch (error) {
    toast(error.message, true);
  }
}

async function deleteBackupSession(sessionName, count) {
  const ok = await confirmDialog('永久删除这批备份？', [
    '将从电脑上删除 ' + count + ' 个原始 MP4 备份，无法撤销。',
    '删除后，这些照片就再也无法恢复成原始动态片段（除非你另有备份）。',
    '请先确认手机相册里的动态效果正常，再删除备份。',
    '批次：' + sessionName,
  ], '永久删除', true);
  if (!ok) return;
  try {
    const data = await api('/api/delete-backups', {
      method: 'POST',
      body: JSON.stringify({ session: sessionName, confirm: 'delete-backups' }),
    });
    state.clearedSessions.add(sessionName);
    toast('已删除 ' + data.deleted_files + ' 个备份文件');
    await loadSummary();
    await openBackups();
    if (state.job) renderJobActions(state.job);
  } catch (error) {
    toast(error.message, true);
  }
}

async function deleteAllBackups() {
  try {
    const data = await api('/api/backups');
    const sessions = data.sessions || [];
    if (!sessions.length) {
      toast('当前没有备份');
      return;
    }
    const files = sessions.reduce(function (sum, item) { return sum + item.count; }, 0);
    const ok = await confirmDialog('永久删除全部备份？', [
      '将删除 ' + sessions.length + ' 个批次、共 ' + files + ' 个原始 MP4 备份，无法撤销。',
      '删除后无法再用 Studio 恢复任何原始动态片段。',
      '如果手机上的压缩版还没有逐张确认，请不要删除。',
    ], '永久删除全部', true);
    if (!ok) return;
    const result = await api('/api/delete-backups', {
      method: 'POST',
      body: JSON.stringify({ session: '*', confirm: 'delete-backups' }),
    });
    sessions.forEach(function (item) { state.clearedSessions.add(item.session); });
    toast('已清除 ' + result.deleted_sessions + ' 个批次 / ' + result.deleted_files + ' 个文件');
    await loadSummary();
    await openBackups();
    if (state.job) renderJobActions(state.job);
  } catch (error) {
    toast(error.message, true);
  }
}

async function restoreBackupSession(session) {
  if (state.starting || state.busy) return;
  const ok = await confirmDialog('恢复这一批原始 MP4？', [
    '将从电脑备份恢复 ' + session.count + ' 个 MP4（' + formatBytes(session.bytes) + '）。',
    '只替换 MP4，JPG 不会改动；恢复后会重新扫描媒体库。',
    '如果手机上的 MP4 已经被其他方式修改，会跳过该文件，避免覆盖新内容。',
    '批次：' + formatSessionName(session.session) + ' · ' + session.session,
  ], '确认恢复', true);
  if (!ok) return;
  state.starting = true;
  updateBusy();
  try {
    const data = await api('/api/restore-backups', {
      method: 'POST',
      body: JSON.stringify({ session: session.session }),
    });
    $('backup-modal').classList.add('hidden');
    showJob(data.job_id, 'restore-backup');
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.starting = false;
    updateBusy();
  }
}

/* ----------------------------------------------------------------- preview */

function openVideoPreview(title, sub, src, loadingSub) {
  const video = $('preview-video');
  $('preview-title').textContent = title;
  $('preview-sub').textContent = loadingSub || sub;
  video.onloadeddata = function () {
    $('preview-sub').textContent = sub;
  };
  video.onerror = function () {
    $('preview-sub').textContent = '视频加载失败，请确认手机仍连接并保持解锁';
  };
  video.src = src;
  $('preview').classList.remove('hidden');
  const play = video.play();
  if (play && play.catch) play.catch(function () {});
}

function openPreview(item) {
  if (!state.compressJob) return;
  const saved = item.original_size ? Math.round((item.original_size - item.compressed_size) * 100 / item.original_size) : 0;
  const sub = formatBytes(item.original_size) + ' → ' +
    formatBytes(item.compressed_size) + ' · 省 ' + saved + '%' +
    (item.ssim ? ' · SSIM ' + item.ssim.toFixed(4) : '');
  openVideoPreview(
    item.time + ' · ' + basename(item.mp4) + '（压缩版）',
    sub,
    previewUrl(state.compressJob.id, item.mp4)
  );
}

function openPhonePreview(photo) {
  const size = formatBytes(photo.size);
  openVideoPreview(
    photo.time + ' · ' + basename(photo.mp4),
    size + ' · ' + (photo.compressed ? '手机上的压缩版' : '原始 MP4') + ' · 双击卡片播放',
    phoneVideoUrl(photo),
    size + ' · 正在从手机加载视频…'
  );
}

function closePreview() {
  const video = $('preview-video');
  video.onloadeddata = null;
  video.onerror = null;
  video.pause();
  video.removeAttribute('src');
  video.load();
  $('preview').classList.add('hidden');
}

/* ------------------------------------------------------------------- modal */

let modalResolver = null;

function confirmDialog(title, lines, okLabel, danger) {
  return new Promise(function (resolve) {
    modalResolver = resolve;
    $('modal-title').textContent = title;
    const body = $('modal-body');
    body.textContent = '';
    lines.filter(Boolean).forEach(function (line) {
      const paragraph = document.createElement('p');
      paragraph.textContent = line;
      body.appendChild(paragraph);
    });
    $('modal-ok').textContent = okLabel || '确认';
    $('modal-ok').className = 'primary' + (danger ? ' danger-btn' : '');
    $('modal').querySelector('.modal-card').classList.toggle('danger', Boolean(danger));
    $('modal').classList.remove('hidden');
    $('modal-ok').focus();
  });
}

function closeModal(value) {
  $('modal').classList.add('hidden');
  const resolver = modalResolver;
  modalResolver = null;
  if (resolver) resolver(value);
}

/* ------------------------------------------------------------------ events */

function switchSideTab(tab) {
  state.sideTab = tab;
  $('tab-date').classList.toggle('active', tab === 'date');
  $('tab-album').classList.toggle('active', tab === 'album');
  $('search').placeholder = tab === 'date' ? '搜索日期，如 2026-10' : '搜索相册或文件夹';
  $('search').value = '';
  renderSidebar();
}

function bindEvents() {
  $('compress-btn').addEventListener('click', compressSelected);
  $('selected-view-btn').addEventListener('click', showSelected);
  $('clear-selection').addEventListener('click', function () {
    state.selected.clear();
    syncVisibleCards();
    updateSelectionUI();
    renderSidebar();
    if (state.view && state.view.type === 'selected') showSelected();
  });
  $('select-scope-btn').addEventListener('click', toggleDisplayedScope);
  $('preset').addEventListener('change', function () {
    $('crf-field').classList.toggle('hidden', $('preset').value !== 'custom');
    $('speed-select').disabled = $('preset').value === 'lossless';
    updateSelectionUI();
  });
  $('crf').addEventListener('input', updateSelectionUI);
  $('recompress-toggle').addEventListener('change', function () {
    if (!this.checked) {
      let removed = 0;
      state.selected.forEach(function (photo, key) {
        if (photo.compressed) {
          state.selected.delete(key);
          removed += 1;
        }
      });
      if (removed) {
        syncVisibleCards();
        updateSelectionBadges();
        if (state.view && state.view.type === 'selected') showSelected();
        toast('已取消 ' + removed + ' 张已压缩照片');
      }
    }
    updateSelectionUI();
  });
  let searchTimer = null;
  $('search').addEventListener('input', function () {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(renderSidebar, 120);
  });
  $('load-more').addEventListener('click', appendNextPage);
  $('refresh-btn').addEventListener('click', refreshAll);
  $('rescan-btn').addEventListener('click', rescan);
  $('largest-btn').addEventListener('click', selectLargest);
  $('bar-selected-btn').addEventListener('click', showSelected);
  $('content').addEventListener('scroll', function () {
    $('content').classList.toggle('scrolled', $('content').scrollTop > 8);
  }, { passive: true });
  document.addEventListener('keydown', function (event) {
    if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) return;
    const tag = (event.target && event.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    event.preventDefault();
    $('search').focus();
  });
  $('tab-date').addEventListener('click', function () { switchSideTab('date'); });
  $('tab-album').addEventListener('click', function () { switchSideTab('album'); });
  $('size-filter').addEventListener('change', function () {
    state.minBytes = Number($('size-filter').value) || 0;
    applyView();
  });
  $('sort-select').addEventListener('change', function () {
    state.sortMode = $('sort-select').value;
    applyView();
  });

  $('modal-ok').addEventListener('click', function () { closeModal(true); });
  $('modal-cancel').addEventListener('click', function () { closeModal(false); });
  $('modal').addEventListener('click', function (event) {
    if (event.target === $('modal')) closeModal(false);
  });

  $('preview-close').addEventListener('click', closePreview);
  $('preview').addEventListener('click', function (event) {
    if (event.target === $('preview')) closePreview();
  });
  $('job-collapse').addEventListener('click', function () {
    const collapsed = $('job-panel').classList.toggle('collapsed');
    this.textContent = collapsed ? '展开详情' : '收起详情';
  });
  $('backup-btn').addEventListener('click', openBackups);
  $('backup-clear-all').addEventListener('click', deleteAllBackups);
  $('backup-close').addEventListener('click', function () {
    $('backup-modal').classList.add('hidden');
  });
  $('backup-modal').addEventListener('click', function (event) {
    if (event.target === $('backup-modal')) $('backup-modal').classList.add('hidden');
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    if (!$('modal').classList.contains('hidden')) closeModal(false);
    else if (!$('preview').classList.contains('hidden')) closePreview();
    else if (!$('backup-modal').classList.contains('hidden')) {
      $('backup-modal').classList.add('hidden');
    }
  });
}

init().catch(function (error) {
  toast(error.message, true);
  $('device-line').textContent = '启动失败：' + error.message;
});
