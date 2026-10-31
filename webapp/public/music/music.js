/*
 * Tonicuisc 网页播放器
 *
 * 纯原生 JS，无构建、无依赖。所有请求都走同源：
 *   - 会话：/music/api/session（Node 维护 HttpOnly Cookie）
 *   - 业务：/api/*（Node 反代 Python，并注入 X-API-Key）
 *   - 音频：/api/stream/*（Node 转发 Range，浏览器不用带 key）
 */
(() => {
  'use strict';

  // ------------------------------------------------------------------ 常量

  const PAGE_SIZE = 15;
  const QUEUE_KEY = 'tuisc.queue.v1';
  const THEME_KEY = 'tuisc.theme.v1';

  const MODES = [
    { id: 'order', label: '顺序播放', icon: 'i-order', badge: '' },
    { id: 'loopAll', label: '列表循环', icon: 'i-repeat', badge: '' },
    { id: 'loopOne', label: '单曲循环', icon: 'i-repeat', badge: '1' },
    { id: 'shuffle', label: '随机播放', icon: 'i-shuffle', badge: '' },
  ];

  const LIB_KINDS = {
    like: { title: '我喜欢', eyebrow: 'Liked' },
    favorite: { title: '收藏', eyebrow: 'Saved' },
    history: { title: '播放历史', eyebrow: 'History' },
  };

  // ------------------------------------------------------------------ DOM

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const el = {
    gate: $('#gate'),
    gateLoading: $('#gateLoading'),
    gateCodeWrap: $('#gateCodeWrap'),
    gateCode: $('#gateCode'),
    gateLoginLink: $('#gateLoginLink'),
    gateStatus: $('#gateStatus'),
    gateRefresh: $('#gateRefresh'),
    gateRestart: $('#gateRestart'),
    gateError: $('#gateError'),
    gateErrorText: $('#gateErrorText'),
    gateRetry: $('#gateRetry'),
    copyCode: $('#copyCode'),
    manualForm: $('#manualForm'),
    manualKey: $('#manualKey'),

    app: $('#app'),
    main: $('#main'),
    sidebar: $('.sidebar'),
    nowPanel: $('#nowPanel'),
    nowClose: $('#nowClose'),

    sideUser: $('#sideUser'),
    sideAvatar: $('#sideAvatar'),
    mobileAvatar: $('#mobileAvatar'),
    navLibCount: $('#navLibCount'),

    views: {
      search: $('#view-search'),
      library: $('#view-library'),
      list: $('#view-list'),
      profile: $('#view-profile'),
      artist: $('#view-artist'),
    },

    searchForm: $('#searchForm'),
    searchInput: $('#searchInput'),
    searchSubmit: $('#searchSubmit'),
    sourceRow: $('#sourceRow'),
    sourceChips: $('#sourceChips'),
    searchStatus: $('#searchStatus'),
    historyBlock: $('#historyBlock'),
    historyChips: $('#historyChips'),
    historyEmpty: $('#historyEmpty'),
    historyRefresh: $('#historyRefresh'),
    searchList: $('#searchList'),
    searchEmpty: $('#searchEmpty'),
    searchMore: $('#searchMore'),
    searchMoreBtn: $('#searchMoreBtn'),
    searchMoreNote: $('#searchMoreNote'),

    libraryRefresh: $('#libraryRefresh'),
    openQueueCard: $('#openQueueCard'),
    queueCardSub: $('#queueCardSub'),

    listBack: $('#listBack'),
    listEyebrow: $('#listEyebrow'),
    listTitle: $('#listTitle'),
    listPlayAll: $('#listPlayAll'),
    listClear: $('#listClear'),
    listStatus: $('#listStatus'),
    listSongs: $('#listSongs'),
    listEmpty: $('#listEmpty'),

    artistBack: $('#artistBack'),
    artistName: $('#artistName'),
    artistPlayAll: $('#artistPlayAll'),
    artistRefresh: $('#artistRefresh'),
    artistStatus: $('#artistStatus'),
    artistSongs: $('#artistSongs'),
    artistEmpty: $('#artistEmpty'),

    profileUser: $('#profileUser'),
    profileAvatar: $('#profileAvatar'),
    profileDevice: $('#profileDevice'),
    profileDeviceId: $('#profileDeviceId'),
    deviceList: $('#deviceList'),
    deviceEmpty: $('#deviceEmpty'),
    devicesRefresh: $('#devicesRefresh'),
    reloginBtn: $('#reloginBtn'),
    logoutBtn: $('#logoutBtn'),
    aboutVersion: $('#aboutVersion'),
    aboutServer: $('#aboutServer'),
    aboutLatest: $('#aboutLatest'),
    themeSegment: $('#themeSegment'),
    themeBtn: $('#themeBtn'),
    themeIcon: $('#themeIcon'),
    themeLabel: $('#themeLabel'),

    nowCover: $('#nowCover'),
    nowCoverWrap: $('#nowCoverWrap'),
    nowTitle: $('#nowTitle'),
    nowSinger: $('#nowSinger'),
    nowLike: $('#nowLike'),
    nowFav: $('#nowFav'),
    nowDownload: $('#nowDownload'),
    nowQueue: $('#nowQueue'),
    nowQueue2: $('#nowQueue2'),
    nowStyle: $('#nowStyle'),
    nowVolume: $('#nowVolume'),
    nowMute: $('#nowMute'),
    lyrics: $('#lyrics'),
    lyricEmpty: $('#lyricEmpty'),
    lyricLines: $('#lyricLines'),
    nowCur: $('#nowCur'),
    nowDur: $('#nowDur'),
    nowSeek: $('#nowSeek'),
    nowMode: $('#nowMode'),
    nowPrev: $('#nowPrev'),
    nowPlay: $('#nowPlay'),
    nowNext: $('#nowNext'),

    pbCover: $('#pbCover'),
    pbCoverBtn: $('#pbCoverBtn'),
    pbTitle: $('#pbTitle'),
    pbSinger: $('#pbSinger'),
    pbLike: $('#pbLike'),
    pbMode: $('#pbMode'),
    pbPrev: $('#pbPrev'),
    pbPlay: $('#pbPlay'),
    pbNext: $('#pbNext'),
    pbQueue: $('#pbQueue'),
    pbCur: $('#pbCur'),
    pbDur: $('#pbDur'),
    pbSeek: $('#pbSeek'),
    pbMute: $('#pbMute'),
    pbVolume: $('#pbVolume'),
    pbDownload: $('#pbDownload'),
    pbExpand: $('#pbExpand'),

    queueDrawer: $('#queueDrawer'),
    queueCount: $('#queueCount'),
    queueNow: $('#queueNow'),
    queueList: $('#queueList'),
    queueClear: $('#queueClear'),

    modalHost: $('#modalHost'),
    toastHost: $('#toastHost'),
    audio: $('#audio'),
    themeColor: $('#themeColor'),
  };

  const audio = el.audio;

  // ------------------------------------------------------------------ 状态

  const state = {
    booted: false,
    sessionLost: false,
    gateOpen: true,
    pairing: false,
    pairAttempted: false,
    pendingExpiresAt: 0,
    pollTimer: 0,
    gateTimer: 0,
    me: null,

    view: 'search',
    artistFrom: 'search',

    theme: 'auto',

    sources: [],
    activeSources: new Set(),

    results: [],
    keyword: '',
    offset: 0,
    hasMore: false,
    searching: false,
    loadingMore: false,
    epoch: 0,
    clientElapsed: 0,
    serverElapsed: 0,
    searchError: '',

    history: [],

    library: {
      counts: { like: 0, favorite: 0, history: 0 },
      like: new Set(),
      favorite: new Set(),
    },

    covers: new Map(),
    coverRequested: new Set(),

    queue: [],
    index: -1,
    mode: 'loopAll',
    playing: false,
    loading: false,
    position: 0,
    duration: 0,
    seekDragging: false,
    seekValue: 0,
    //: 刚跳转到的位置。`audio.currentTime` 要过几帧才真的变过去，
    //: 这段时间进度条先用这个值，不然会「跳过去又弹回来」闪一下。
    seekPending: null,
    seekPendingAt: 0,
    volume: 1,
    muted: false,
    audioSongId: null,

    lyric: {
      songId: null,
      lines: [],
      synced: false,
      hasWords: false,
      active: -1,
      loading: false,
      error: '',
      enhancedReady: false,
    },

    listKind: '',
    listSongs: [],
    listLoading: false,
    listError: '',

    artistSongs: [],
    artistLoading: false,
    artistError: '',

    devices: [],
  };

  let lyricFrame = 0;
  let lyricScrollFrame = 0;
  let nowCloseTimer = 0;

  //: `audio.currentTime` 的采样锚点。浏览器给的不一定每帧都变（有的实现是「台阶值」），
  //: 中间那几十毫秒用真实经过的时间补上——App 端 `_displayPosition` 就是这么做的。
  //: 不补的话，歌词会一顿一顿地追人声，看起来就是「慢半拍」。
  let clockPos = -1;
  let clockAt = 0;

  // ------------------------------------------------------------------ 工具

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  //: 宽屏下页面本身不滚（见 music.css 的「桌面：页面本身不滚」），滚的是 `.main`。
  //: 窄屏还是整页滚。判断只写在这一处，别的地方都用 scrollHost()。
  const DESKTOP_MQ = window.matchMedia('(min-width: 1201px)');

  function scrollHost() {
    return DESKTOP_MQ.matches && el.main ? el.main : null;
  }

  function scrollHostToTop() {
    const host = scrollHost();
    if (host) host.scrollTop = 0;
    else window.scrollTo(0, 0);
  }

  /// 是否快滚到底了（触底加载更多用）。
  function scrollHostNearBottom(threshold) {
    const host = scrollHost();
    if (host) return host.scrollTop + host.clientHeight >= host.scrollHeight - threshold;
    const doc = document.documentElement;
    return window.innerHeight + window.scrollY >= doc.scrollHeight - threshold;
  }

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => {
      switch (ch) {
        case '&': return '&amp;';
        case '<': return '&lt;';
        case '>': return '&gt;';
        case '"': return '&quot;';
        default: return '&#39;';
      }
    });
  }

  function icon(name, extra) {
    return `<svg class="ic${extra ? ' ' + extra : ''}"><use href="#${name}"></use></svg>`;
  }

  function setUse(button, name) {
    const use = button && button.querySelector('use');
    if (use) use.setAttribute('href', '#' + name);
  }

  function idPath(id) {
    return String(id || '')
      .split('/')
      .map((part) => encodeURIComponent(part))
      .join('/')
      .replace(/%3A/gi, ':');
  }

  function fmtTime(seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value) || value <= 0) return '0:00';
    const total = Math.floor(value);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  function fmtDate(seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value) || value <= 0) return '-';
    const d = new Date(value * 1000);
    const two = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`;
  }

  /// 设备「最后使用」用相对时间：一行放得下，也不会被省略号截掉年份。
  function fmtRelative(seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value) || value <= 0) return '-';
    const diff = Math.max(0, Math.floor(Date.now() / 1000 - value));
    if (diff < 60) return '刚刚';
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
    if (diff < 7 * 86400) return `${Math.floor(diff / 86400)} 天前`;
    return fmtDate(value).slice(0, 10);
  }

  function initialOf(name) {
    const text = String(name || '').trim();
    return text ? text.charAt(0).toUpperCase() : 'T';
  }

  function currentSong() {
    return state.index >= 0 && state.index < state.queue.length ? state.queue[state.index] : null;
  }

  function modeOf(id) {
    return MODES.find((item) => item.id === id) || MODES[1];
  }

  function tailOf(song) {
    return [song && song.album, song && song.duration].filter(Boolean).join(' · ');
  }

  function coverFor(song) {
    if (!song) return '';
    return state.covers.get(song.id) || song.cover_url || '';
  }

  function setStatus(node, text, kind) {
    if (!node) return;
    node.textContent = text || '';
    node.classList.toggle('err', kind === 'err');
    node.classList.toggle('ok', kind === 'ok');
  }

  // ------------------------------------------------------------------ Toast / 弹窗

  function toast(message, options = {}) {
    if (!message) return;
    const node = document.createElement('div');
    node.className = 'toast' + (options.error ? ' is-error' : '');
    node.textContent = String(message);
    el.toastHost.appendChild(node);
    while (el.toastHost.children.length > 3) el.toastHost.firstElementChild.remove();
    const life = Number(options.duration) || (options.error ? 4200 : 2400);
    setTimeout(() => {
      node.classList.add('is-out');
      setTimeout(() => node.remove(), 240);
    }, life);
  }

  function confirmDialog(options = {}) {
    return new Promise((resolve) => {
      const host = el.modalHost;
      host.hidden = false;
      host.innerHTML = '';

      const card = document.createElement('div');
      card.className = 'modal-card';
      card.setAttribute('role', 'dialog');
      card.setAttribute('aria-modal', 'true');

      const title = document.createElement('h2');
      title.textContent = options.title || '确认';
      const message = document.createElement('p');
      message.textContent = options.message || '';

      const actions = document.createElement('div');
      actions.className = 'modal-actions';

      const cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'btn btn-ghost btn-sm';
      cancel.textContent = options.cancelText || '取消';

      const ok = document.createElement('button');
      ok.type = 'button';
      ok.className = 'btn btn-primary btn-sm';
      if (options.danger) {
        ok.style.background = 'var(--red)';
        ok.style.boxShadow = '0 12px 24px -12px rgba(229,72,77,.8)';
      }
      ok.textContent = options.confirmText || '确定';

      const close = (value) => {
        host.hidden = true;
        host.innerHTML = '';
        resolve(value);
      };

      cancel.addEventListener('click', () => close(false));
      ok.addEventListener('click', () => close(true));
      host.addEventListener('click', (event) => {
        if (event.target === host) close(false);
      }, { once: true });

      actions.append(cancel, ok);
      card.append(title, message, actions);
      host.appendChild(card);
      ok.focus();
    });
  }

  // ------------------------------------------------------------------ 请求

  class ApiError extends Error {
    constructor(message, status, data) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.data = data;
    }
  }

  async function api(path, options = {}) {
    const init = {
      method: options.method || 'GET',
      credentials: 'same-origin',
      headers: { accept: 'application/json', ...(options.headers || {}) },
    };
    if (options.body !== undefined) {
      init.headers['content-type'] = 'application/json';
      init.body = JSON.stringify(options.body);
    }
    const response = await fetch(path, init);
    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = null;
      }
    }
    if (!response.ok) {
      const detail = data && typeof data.detail === 'string' ? data.detail : `HTTP ${response.status}`;
      if (response.status === 401) onSessionLost('登录已失效，请重新配对');
      throw new ApiError(detail, response.status, data);
    }
    return data;
  }

  // 歌词默认是纯文本 LRC，不能走 JSON 解析
  async function apiText(path) {
    const response = await fetch(path, { credentials: 'same-origin', headers: { accept: 'text/plain' } });
    const text = await response.text();
    if (!response.ok) {
      let detail = '';
      try {
        const data = JSON.parse(text);
        if (data && typeof data.detail === 'string') detail = data.detail;
      } catch {
        // 不是 JSON 就用默认文案
      }
      if (response.status === 401) onSessionLost('登录已失效，请重新配对');
      throw new ApiError(detail || `HTTP ${response.status}`, response.status, null);
    }
    return text;
  }

  async function sessionRequest(path, options = {}) {
    const response = await fetch(path, {
      method: options.method || 'GET',
      credentials: 'same-origin',
      headers: options.body !== undefined ? { 'content-type': 'application/json' } : undefined,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = null;
      }
    }
    if (!response.ok) {
      throw new ApiError((data && data.detail) || `HTTP ${response.status}`, response.status, data);
    }
    return data;
  }

  // ------------------------------------------------------------------ 会话

  function showGate() {
    el.gate.hidden = false;
    el.gate.classList.remove('is-hiding');
    state.gateOpen = true;
    el.app.hidden = true;
  }

  function hideGate() {
    el.gate.classList.add('is-hiding');
    state.gateOpen = false;
    clearInterval(state.pollTimer);
    state.pollTimer = 0;
    setTimeout(() => {
      if (!state.gateOpen) el.gate.hidden = true;
    }, 340);
  }

  function gateLoading() {
    el.gateLoading.hidden = false;
    el.gateCodeWrap.hidden = true;
    el.gateError.hidden = true;
  }

  function gateShowCode(code, expiresIn) {
    el.gateLoading.hidden = true;
    el.gateError.hidden = true;
    el.gateCodeWrap.hidden = false;
    el.gateCode.textContent = code;
    el.gateLoginLink.href = '/login?code=' + encodeURIComponent(code);
    state.pendingExpiresAt = Date.now() + (Number(expiresIn) || 600) * 1000;
    updateGateStatus();
    startPolling();
  }

  function gateShowError(message) {
    clearInterval(state.pollTimer);
    state.pollTimer = 0;
    el.gateLoading.hidden = true;
    el.gateCodeWrap.hidden = true;
    el.gateError.hidden = false;
    el.gateErrorText.textContent = String(message || '出了点问题，请重试。');
  }

  function updateGateStatus() {
    if (!state.gateOpen || !state.pendingExpiresAt) return;
    const left = Math.max(0, Math.round((state.pendingExpiresAt - Date.now()) / 1000));
    if (!left) {
      el.gateStatus.textContent = '设备码已过期，请换一个';
      return;
    }
    const minutes = Math.ceil(left / 60);
    el.gateStatus.textContent = `等待批准…（约 ${minutes} 分钟）`;
  }

  function startPolling() {
    if (state.pollTimer) return;
    state.pollTimer = setInterval(async () => {
      if (!state.gateOpen || state.pairing) return;
      try {
        const data = await sessionRequest('/music/api/session');
        applySession(data);
      } catch {
        // 网络抖动：下一轮再试
      }
    }, 2200);
  }

  async function beginPairing() {
    if (state.pairing) return;
    state.pairing = true;
    state.pairAttempted = true;
    state.pendingExpiresAt = 0;
    gateLoading();
    try {
      const data = await sessionRequest('/music/api/session', { method: 'POST' });
      if (data && data.authenticated) {
        applySession(data);
        return;
      }
      if (data && data.user_code) {
        gateShowCode(data.user_code, data.expires_in);
      } else {
        gateShowError('服务端没有返回设备码，请重试。');
      }
    } catch (error) {
      gateShowError(error.message || '生成设备码失败');
    } finally {
      state.pairing = false;
    }
  }

  async function resetToGate(message, { error = true } = {}) {
    state.booted = false;
    state.sessionLost = true;
    state.me = null;
    state.pairAttempted = false;
    clearInterval(state.pollTimer);
    state.pollTimer = 0;

    audio.pause();
    state.audioSongId = null;
    audio.removeAttribute('src');
    try { audio.load(); } catch { /* ignore */ }
    state.playing = false;
    state.loading = false;
    state.queue = [];
    state.index = -1;
    state.position = 0;
    state.duration = 0;
    state.lyric = { songId: null, lines: [], synced: false, hasWords: false, active: -1, loading: false, error: '', enhancedReady: false };

    persistQueue();
    renderQueue();
    renderNow();
    renderProgress();
    renderLyrics();
    showGate();

    if (message) toast(message, { error });

    try {
      await sessionRequest('/music/api/session', { method: 'DELETE' });
    } catch {
      // 服务端不在也照样让用户重试
    }
    beginPairing();
  }

  function onSessionLost(message) {
    if (state.sessionLost) return;
    resetToGate(message || '登录已失效，请重新配对');
  }

  function applySession(data) {
    if (!data || typeof data !== 'object') return;
    if (data.authenticated) {
      state.sessionLost = false;
      state.pairAttempted = false;
      state.me = data.me || state.me;
      hideGate();
      el.app.hidden = false;
      updateAccountUI();
      boot();
      return;
    }
    if (data.state === 'pending') {
      state.pendingExpiresAt = Date.now() + (Number(data.expires_in) || 600) * 1000;
      if (data.user_code) gateShowCode(data.user_code, data.expires_in);
      startPolling();
      return;
    }
    if (data.state === 'anonymous') {
      if (!state.pairing && !state.pairAttempted) beginPairing();
      return;
    }
    if (data.state === 'expired') {
      gateShowError('设备码已过期。点下面的按钮重新生成一个。');
      return;
    }
    if (data.state === 'denied') {
      gateShowError('这次登录被拒绝了。如果还想登录，请重新生成设备码。');
      return;
    }
    if (data.state === 'claimed') {
      gateShowError('这个设备码已经被使用过了，请重新生成一个。');
      return;
    }
    if (data.state === 'offline') {
      gateShowError(data.detail || '服务端暂时不可用，请稍后重试。');
      return;
    }
    gateShowError('登录状态未知，请重试。');
  }

  async function checkSession() {
    try {
      const data = await sessionRequest('/music/api/session');
      applySession(data);
    } catch (error) {
      if (state.gateOpen) gateShowError(error.message || '连不上服务端');
    }
  }

  // ------------------------------------------------------------------ 启动 / 账户

  function boot() {
    if (state.booted) {
      updateAccountUI();
      return;
    }
    state.booted = true;
    el.app.hidden = false;
    updateAccountUI();
    restoreQueue();
    renderQueue();
    renderMode();
    renderNow();
    renderProgress();
    renderVolume();
    renderLyrics();
    // 队列是从 localStorage 恢复的：歌还在，歌词和封面也要跟着回来，
    // 否则刷新后播放条显示着歌、右边却写「暂无歌词」。
    const restored = currentSong();
    if (restored) {
      loadLyrics(restored);
      resolveCovers([restored]);
    }
    loadSources();
    loadHistory();
    loadSummary();
    showView(state.view || 'search');
  }

  function updateAccountUI() {
    const me = state.me || {};
    const name = me.username || '未登录';
    el.sideUser.textContent = name;
    el.sideAvatar.textContent = initialOf(name);
    el.mobileAvatar.textContent = initialOf(name);
    el.profileUser.textContent = name;
    el.profileAvatar.textContent = initialOf(name);
    el.profileDevice.textContent = me.device_name ? `本机：${me.device_name}` : '网页播放器';
    const deviceId = String(me.device_id || '-');
    el.profileDeviceId.textContent = `设备 ID：${deviceId.length > 8 ? deviceId.slice(0, 8) + '…' : deviceId}`;
  }

  async function loadSummary() {
    try {
      const data = await api('/api/library/summary');
      const counts = (data && data.counts) || {};
      state.library.counts = {
        like: Number(counts.like) || 0,
        favorite: Number(counts.favorite) || 0,
        history: Number(counts.history) || 0,
      };
      state.library.like = new Set(((data && data.like_ids) || []).map(String));
      state.library.favorite = new Set(((data && data.favorite_ids) || []).map(String));
      renderCounts();
      refreshRowStates();
    } catch (error) {
      if (error.status === 401) return;
      // 摘要失败不影响听歌
    }
  }

  function renderCounts() {
    const counts = state.library.counts;
    $$('[data-count]').forEach((node) => {
      node.textContent = String(Number(counts[node.dataset.count]) || 0);
    });
    const total = (Number(counts.like) || 0) + (Number(counts.favorite) || 0);
    el.navLibCount.hidden = total <= 0;
    el.navLibCount.textContent = total > 99 ? '99+' : String(total);
  }

  function bumpCount(kind, delta) {
    const current = Number(state.library.counts[kind]) || 0;
    state.library.counts[kind] = Math.max(0, current + delta);
    renderCounts();
  }

  async function toggleLibrary(kind, song) {
    if (!song || !song.id) return;
    if (kind !== 'like' && kind !== 'favorite') return;
    const set = kind === 'like' ? state.library.like : state.library.favorite;
    const has = set.has(song.id);
    try {
      if (has) {
        await api(`/api/library/${kind}/${idPath(song.id)}`, { method: 'DELETE' });
        set.delete(song.id);
        bumpCount(kind, -1);
        toast(kind === 'like' ? `已取消喜欢 ${song.name}` : `已取消收藏 ${song.name}`);
      } else {
        const data = await api(`/api/library/${kind}`, { method: 'POST', body: { song_id: song.id } });
        set.add(song.id);
        if (data && data.counts) {
          state.library.counts = {
            like: Number(data.counts.like) || 0,
            favorite: Number(data.counts.favorite) || 0,
            history: Number(data.counts.history) || 0,
          };
          renderCounts();
        } else {
          bumpCount(kind, 1);
        }
        toast(kind === 'like' ? `已加入我喜欢 ${song.name}` : `已收藏 ${song.name}`);
      }
      refreshRowStates();
    } catch (error) {
      if (error.status === 401) return;
      toast(`操作失败：${error.message}`, { error: true });
    }
  }

  // ------------------------------------------------------------------ 主题

  function resolveTheme(choice) {
    if (choice === 'light' || choice === 'dark') return choice;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function applyTheme(choice, persist = true) {
    state.theme = choice;
    const resolved = resolveTheme(choice);
    document.documentElement.dataset.theme = resolved;
    if (el.themeColor) el.themeColor.setAttribute('content', resolved === 'dark' ? '#0a0f17' : '#ffffff');
    if (persist) {
      try { localStorage.setItem(THEME_KEY, choice); } catch { /* ignore */ }
    }
    const labels = { auto: '跟随系统', light: '浅色', dark: '深色' };
    const icons = { auto: 'i-monitor', light: 'i-sun', dark: 'i-moon' };
    el.themeLabel.textContent = labels[choice] || labels.auto;
    setUse(el.themeIcon, icons[choice] || icons.auto);
    $$('[data-theme-choice]', el.themeSegment).forEach((button) => {
      button.classList.toggle('is-on', button.dataset.themeChoice === choice);
    });
  }

  function initTheme() {
    let choice = 'auto';
    try { choice = localStorage.getItem(THEME_KEY) || 'auto'; } catch { /* ignore */ }
    if (!['auto', 'light', 'dark'].includes(choice)) choice = 'auto';
    applyTheme(choice, false);
    if (window.matchMedia) {
      const query = window.matchMedia('(prefers-color-scheme: dark)');
      const listener = () => {
        if (state.theme === 'auto') applyTheme('auto', false);
      };
      if (query.addEventListener) query.addEventListener('change', listener);
      else if (query.addListener) query.addListener(listener);
    }
  }

  // ------------------------------------------------------------------ 视图

  function navTarget(name) {
    if (name === 'list') return 'library';
    if (name === 'artist') return 'search';
    return name;
  }

  function showView(name) {
    if (!el.views[name]) name = 'search';
    state.view = name;
    Object.entries(el.views).forEach(([key, node]) => {
      node.hidden = key !== name;
    });
    const target = navTarget(name);
    $$('.nav-item[data-nav], .tab-item[data-nav]').forEach((button) => {
      button.classList.toggle('is-active', button.dataset.nav === target);
    });
    closeNowPanel();
    scrollHostToTop();
    if (name === 'library') {
      loadSummary();
      updateQueueSub();
    }
    if (name === 'profile') loadProfile();
    if (name === 'search' && !state.results.length) loadHistory();
  }

  // ------------------------------------------------------------------ 搜索

  function hideHistory() {
    el.historyBlock.hidden = true;
  }

  function showHistory() {
    el.historyBlock.hidden = false;
  }

  async function loadSources() {
    try {
      const data = await api('/api/sources');
      state.sources = Array.isArray(data && data.items) ? data.items : [];
      if (!state.activeSources.size) {
        state.sources.forEach((source) => state.activeSources.add(source.alias));
      }
      renderSources();
    } catch (error) {
      if (error.status === 401) return;
      el.sourceRow.hidden = true;
    }
  }

  function renderSources() {
    if (state.sources.length <= 1) {
      el.sourceRow.hidden = true;
      return;
    }
    el.sourceRow.hidden = false;
    el.sourceChips.innerHTML = '';
    state.sources.forEach((source) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'chip' + (state.activeSources.has(source.alias) ? ' is-on' : '');
      button.textContent = source.label || source.name || source.alias;
      button.title = `音源：${source.alias}`;
      button.addEventListener('click', () => {
        if (state.activeSources.has(source.alias)) {
          if (state.activeSources.size === 1) {
            toast('至少要留一个音源');
            return;
          }
          state.activeSources.delete(source.alias);
        } else {
          state.activeSources.add(source.alias);
        }
        renderSources();
        if (state.keyword) runSearch(state.keyword, { refresh: true });
      });
      el.sourceChips.appendChild(button);
    });
  }

  async function loadHistory() {
    try {
      const data = await api('/api/history?limit=20');
      state.history = Array.isArray(data && data.items) ? data.items : [];
      renderHistory();
    } catch (error) {
      if (error.status === 401) return;
      state.history = [];
      renderHistory();
    }
  }

  function renderHistory() {
    el.historyChips.innerHTML = '';
    const items = state.history.filter((item) => item && item.keyword);
    el.historyEmpty.hidden = items.length > 0;
    items.forEach((item) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'chip';
      button.textContent = item.keyword;
      button.title = item.result_count ? `上次搜到 ${item.result_count} 首` : '再搜一次';
      button.addEventListener('click', () => {
        el.searchInput.value = item.keyword;
        runSearch(item.keyword, { refresh: true });
      });
      el.historyChips.appendChild(button);
    });
  }

  function renderSkeleton(container, count) {
    container.innerHTML = '';
    for (let i = 0; i < count; i += 1) {
      const row = document.createElement('div');
      row.className = 'skeleton-row';
      row.innerHTML = `
        <span class="skeleton cover"></span>
        <span><span class="skeleton line" style="width:${55 + (i % 3) * 12}%"></span><span class="skeleton line short"></span></span>
        <span class="skeleton" style="height:22px"></span>`;
      container.appendChild(row);
    }
  }

  async function runSearch(keyword, options = {}) {
    const text = String(keyword || '').trim();
    if (!text) return;
    const append = Boolean(options.append);
    if (state.searching || state.loadingMore) return;

    if (!append) {
      state.epoch += 1;
      state.keyword = text;
      state.results = [];
      state.offset = 0;
      state.hasMore = false;
      state.searching = true;
      state.searchError = '';
      state.clientElapsed = 0;
      state.serverElapsed = 0;
      el.searchSubmit.disabled = true;
      hideHistory();
      el.searchEmpty.hidden = true;
      el.searchMore.hidden = true;
      setStatus(el.searchStatus, '正在搜索…');
      renderSkeleton(el.searchList, 6);
    } else {
      state.loadingMore = true;
      el.searchMoreBtn.disabled = true;
      el.searchMoreNote.textContent = '正在加载更多…';
    }

    const epoch = state.epoch;
    const started = performance.now();

    try {
      const params = new URLSearchParams({
        keyword: text,
        offset: String(append ? state.offset : 0),
        limit: String(PAGE_SIZE),
      });
      if (!append && options.refresh !== false) params.set('refresh', 'true');
      const sources = Array.from(state.activeSources);
      if (sources.length) params.set('sources', sources.join(','));

      const data = await api('/api/search?' + params.toString());
      if (epoch !== state.epoch) return;

      const items = Array.isArray(data && data.items) ? data.items : [];
      if (!append) {
        state.results = items;
        state.offset = items.length;
        state.searching = false;
        state.clientElapsed = (performance.now() - started) / 1000;
        state.serverElapsed = Number(data && data.elapsed) || 0;
        el.searchSubmit.disabled = false;
      } else {
        const existing = new Set(state.results.map((song) => song.id));
        const fresh = items.filter((song) => !existing.has(song.id));
        state.results = state.results.concat(fresh);
        state.offset += items.length;
        state.loadingMore = false;
      }
      state.hasMore = Boolean(data && data.has_more) && items.length > 0;
      renderSearchResults();
      updateSearchStatus();
      resolveCovers(items);
    } catch (error) {
      if (error.status === 401) return;
      state.searching = false;
      state.loadingMore = false;
      state.hasMore = false;
      state.searchError = error.message;
      el.searchSubmit.disabled = false;
      if (!append) {
        state.results = [];
        el.searchList.innerHTML = '';
      }
      updateSearchStatus();
    } finally {
      state.searching = false;
      state.loadingMore = false;
      el.searchSubmit.disabled = false;
      updateSearchStatus();
    }
  }

  function updateSearchStatus() {
    if (state.searching) {
      setStatus(el.searchStatus, '正在搜索…');
      return;
    }
    if (state.loadingMore) {
      setStatus(el.searchStatus, `正在加载更多…（已有 ${state.results.length} 首）`);
      return;
    }
    if (state.searchError) {
      setStatus(el.searchStatus, `搜索失败：${state.searchError}`, 'err');
      return;
    }
    if (state.keyword && state.results.length) {
      const client = state.clientElapsed ? ` · 用时 ${state.clientElapsed.toFixed(1)} 秒` : '';
      const server = state.serverElapsed ? `（服务端 ${state.serverElapsed.toFixed(1)} 秒）` : '';
      setStatus(el.searchStatus, `搜索完成${client}${server} · 共 ${state.results.length} 首`);
    } else if (state.keyword) {
      setStatus(el.searchStatus, '没有搜到结果，换个关键词试试。', 'err');
    } else {
      setStatus(el.searchStatus, '');
    }

    const hasResults = state.results.length > 0;
    if (state.loadingMore) {
      el.searchMore.hidden = false;
      el.searchMoreBtn.hidden = false;
      el.searchMoreBtn.disabled = true;
      el.searchMoreNote.textContent = '正在加载更多…';
    } else if (state.hasMore) {
      el.searchMore.hidden = false;
      el.searchMoreBtn.hidden = false;
      el.searchMoreBtn.disabled = false;
      el.searchMoreNote.textContent = '';
    } else if (hasResults) {
      el.searchMore.hidden = false;
      el.searchMoreBtn.hidden = true;
      el.searchMoreNote.textContent = `共 ${state.results.length} 首`;
    } else {
      el.searchMore.hidden = true;
    }
  }

  function renderSearchResults() {
    el.searchList.innerHTML = '';
    if (!state.results.length) {
      el.searchEmpty.hidden = false;
      const title = el.searchEmpty.querySelector('h3');
      const desc = el.searchEmpty.querySelector('p');
      if (title) title.textContent = state.keyword ? '没有搜到结果' : '输入关键词开始搜索';
      if (desc) desc.textContent = state.keyword
        ? '换个关键词、换一个音源，或者稍后再试。'
        : '搜歌名、歌手或专辑都行。结果滑到底会自动加载下一页。';
      return;
    }
    el.searchEmpty.hidden = true;
    state.results.forEach((song, index) => {
      el.searchList.appendChild(songRow(song, { queue: state.results, index, remove: false }));
    });
    refreshRowStates(el.searchList);
    hydrateImages(el.searchList);
  }

  function loadMore() {
    if (!state.keyword || !state.hasMore || state.loadingMore || state.searching) return;
    runSearch(state.keyword, { append: true });
  }

  // ------------------------------------------------------------------ 歌曲行

  function coverMarkup(song) {
    const url = coverFor(song);
    return `<span class="cover-fallback">${icon('i-music')}</span>` +
      `<img src="${url ? esc(url) : ''}" alt="" loading="lazy" data-cover-id="${esc(song.id)}"${url ? '' : ' hidden'}>`;
  }

  function songRow(song, options = {}) {
    const row = document.createElement('div');
    const isCurrent = currentSong() && currentSong().id === song.id;
    const liked = state.library.like.has(song.id);
    const faved = state.library.favorite.has(song.id);
    const tail = tailOf(song);

    row.className = 'song-row' + (isCurrent ? ' is-current' : '');
    row.dataset.id = song.id;
    row.setAttribute('role', 'listitem');
    row.tabIndex = 0;
    row.innerHTML = `
      <span class="song-cover">${coverMarkup(song)}<span class="eq" aria-hidden="true"><i></i><i></i><i></i></span></span>
      <span class="song-main">
        <p class="song-title">${esc(song.name || '未知歌曲')}</p>
        <p class="song-sub">
          ${song.singers ? `<button class="singer-link" type="button" data-act="singer">${esc(song.singers)}</button>` : ''}
          ${tail ? `<span class="tail">${song.singers ? '· ' : ''}${esc(tail)}</span>` : ''}
        </p>
      </span>
      <span class="song-tag">${esc(String(song.ext || '').toUpperCase())}</span>
      <span class="song-actions">
        <button class="icon-btn${liked ? ' is-liked' : ''}" type="button" data-act="like" title="${liked ? '取消喜欢' : '喜欢'}">${icon(liked ? 'i-heart-fill' : 'i-heart')}</button>
        <button class="icon-btn${faved ? ' is-faved' : ''}" type="button" data-act="favorite" title="${faved ? '取消收藏' : '收藏'}">${icon(faved ? 'i-bookmark-fill' : 'i-bookmark')}</button>
        <button class="icon-btn" type="button" data-act="download" title="下载">${icon('i-download')}</button>
        ${options.remove ? `<button class="icon-btn danger" type="button" data-act="remove" title="移除">${icon('i-trash')}</button>` : ''}
      </span>`;

    const activate = () => {
      if (options.queue && Number.isInteger(options.index)) playQueue(options.queue, options.index);
      else playQueue([song], 0);
    };

    row.addEventListener('click', (event) => {
      if (event.target.closest('button')) return;
      activate();
    });
    row.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        activate();
      }
    });

    row.addEventListener('click', (event) => {
      const button = event.target.closest('[data-act]');
      if (!button) return;
      const act = button.dataset.act;
      event.stopPropagation();
      if (act === 'singer') {
        openArtist(song.singers);
      } else if (act === 'like') {
        toggleLibrary('like', song);
      } else if (act === 'favorite') {
        toggleLibrary('favorite', song);
      } else if (act === 'download') {
        downloadSong(song);
      } else if (act === 'remove' && options.onRemove) {
        options.onRemove(song, row);
      }
    });

    return row;
  }

  function hydrateImages(root) {
    $$('img[data-cover-id]', root).forEach((img) => {
      if (img.dataset.bound) return;
      img.dataset.bound = '1';
      img.addEventListener('error', () => {
        img.remove();
      });
    });
  }

  function refreshRowStates(root = document) {
    $$('.song-row[data-id]', root).forEach((row) => {
      const id = row.dataset.id;
      const liked = state.library.like.has(id);
      const faved = state.library.favorite.has(id);
      const isCurrent = currentSong() && currentSong().id === id;
      row.classList.toggle('is-current', Boolean(isCurrent));
      const like = row.querySelector('[data-act="like"]');
      if (like) {
        like.classList.toggle('is-liked', liked);
        like.title = liked ? '取消喜欢' : '喜欢';
        setUse(like, liked ? 'i-heart-fill' : 'i-heart');
      }
      const fav = row.querySelector('[data-act="favorite"]');
      if (fav) {
        fav.classList.toggle('is-faved', faved);
        fav.title = faved ? '取消收藏' : '收藏';
        setUse(fav, faved ? 'i-bookmark-fill' : 'i-bookmark');
      }
    });
    updateNowButtons();
  }

  async function resolveCovers(songs) {
    const ids = [];
    (songs || []).forEach((song) => {
      if (!song || !song.id) return;
      if (state.coverRequested.has(song.id)) return;
      state.coverRequested.add(song.id);
      ids.push(song.id);
    });
    if (!ids.length) return;
    const batch = ids.slice(0, 200);
    try {
      const data = await api('/api/covers', { method: 'POST', body: { ids: batch } });
      const covers = (data && data.covers) || {};
      Object.entries(covers).forEach(([id, url]) => {
        if (url) state.covers.set(id, String(url));
      });
      applyCoverUpdates();
    } catch (error) {
      if (error.status === 401) return;
      batch.forEach((id) => state.coverRequested.delete(id));
    }
  }

  function applyCoverUpdates() {
    $$('img[data-cover-id]').forEach((img) => {
      const url = state.covers.get(img.dataset.coverId);
      if (url && img.getAttribute('src') !== url) {
        img.src = url;
        img.hidden = false;
      }
    });
    const song = currentSong();
    if (song) {
      const url = coverFor(song);
      if (url) {
        setCoverImage(el.nowCover, url);
        setCoverImage(el.pbCover, url);
      }
    }
  }

  // ------------------------------------------------------------------ 列表

  async function openLibrary(kind) {
    const meta = LIB_KINDS[kind];
    if (!meta) return;
    state.listKind = kind;
    state.listSongs = [];
    state.listLoading = true;
    state.listError = '';
    el.listEyebrow.textContent = meta.eyebrow;
    el.listTitle.textContent = meta.title;
    el.listSongs.innerHTML = '';
    el.listEmpty.hidden = true;
    setStatus(el.listStatus, '正在加载…');
    showView('list');
    renderSkeleton(el.listSongs, 5);
    try {
      const data = await api(`/api/library/${kind}?limit=200`);
      state.listSongs = Array.isArray(data && data.items) ? data.items : [];
      state.listLoading = false;
      renderLibraryList();
      resolveCovers(state.listSongs);
    } catch (error) {
      if (error.status === 401) return;
      state.listLoading = false;
      state.listError = error.message;
      el.listSongs.innerHTML = '';
      el.listEmpty.hidden = false;
      const title = el.listEmpty.querySelector('h3');
      const desc = el.listEmpty.querySelector('p');
      if (title) title.textContent = '加载失败';
      if (desc) desc.textContent = error.message;
      setStatus(el.listStatus, `加载失败：${error.message}`, 'err');
    }
  }

  function renderLibraryList() {
    el.listSongs.innerHTML = '';
    setStatus(el.listStatus, state.listSongs.length ? `共 ${state.listSongs.length} 首` : '');
    if (!state.listSongs.length) {
      el.listEmpty.hidden = false;
      const title = el.listEmpty.querySelector('h3');
      const desc = el.listEmpty.querySelector('p');
      if (title) title.textContent = '这里还是空的';
      if (desc) desc.textContent = '去搜索页听几首，或者点一下红心。';
      return;
    }
    el.listEmpty.hidden = true;
    state.listSongs.forEach((song, index) => {
      el.listSongs.appendChild(songRow(song, {
        queue: state.listSongs,
        index,
        remove: true,
        onRemove: (item) => removeFromLibrary(item),
      }));
    });
    refreshRowStates(el.listSongs);
    hydrateImages(el.listSongs);
  }

  async function removeFromLibrary(song) {
    if (!state.listKind) return;
    try {
      await api(`/api/library/${state.listKind}/${idPath(song.id)}`, { method: 'DELETE' });
      state.listSongs = state.listSongs.filter((item) => item.id !== song.id);
      if (state.listKind === 'like') {
        state.library.like.delete(song.id);
        bumpCount('like', -1);
      }
      if (state.listKind === 'favorite') {
        state.library.favorite.delete(song.id);
        bumpCount('favorite', -1);
      }
      renderLibraryList();
      toast(`已从「${LIB_KINDS[state.listKind].title}」移除 ${song.name}`);
    } catch (error) {
      if (error.status === 401) return;
      toast(`移除失败：${error.message}`, { error: true });
    }
  }

  async function clearLibrary() {
    if (!state.listKind || !state.listSongs.length) return;
    const meta = LIB_KINDS[state.listKind];
    const ok = await confirmDialog({
      title: `清空「${meta.title}」？`,
      message: '这个操作不可撤销。',
      confirmText: '清空',
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/library/${state.listKind}`, { method: 'DELETE' });
      state.listSongs = [];
      if (state.listKind === 'like') {
        state.library.like.clear();
        state.library.counts.like = 0;
      }
      if (state.listKind === 'favorite') {
        state.library.favorite.clear();
        state.library.counts.favorite = 0;
      }
      renderCounts();
      renderLibraryList();
      toast(`已清空「${meta.title}」`);
    } catch (error) {
      if (error.status === 401) return;
      toast(`清空失败：${error.message}`, { error: true });
    }
  }

  // ------------------------------------------------------------------ 歌手

  async function openArtist(name) {
    const artist = String(name || '').trim();
    if (!artist) return;
    state.artistFrom = ['search', 'library', 'list'].includes(state.view) ? state.view : 'search';
    state.artistSongs = [];
    state.artistLoading = true;
    state.artistError = '';
    el.artistName.textContent = artist;
    el.artistSongs.innerHTML = '';
    el.artistEmpty.hidden = true;
    setStatus(el.artistStatus, '正在加载…');
    showView('artist');
    renderSkeleton(el.artistSongs, 5);
    try {
      const params = new URLSearchParams({ name: artist, limit: '100' });
      const sources = Array.from(state.activeSources);
      if (sources.length) params.set('sources', sources.join(','));
      const data = await api('/api/artist?' + params.toString());
      state.artistSongs = Array.isArray(data && data.items) ? data.items : [];
      state.artistLoading = false;
      renderArtist();
      resolveCovers(state.artistSongs);
    } catch (error) {
      if (error.status === 401) return;
      state.artistLoading = false;
      state.artistError = error.message;
      el.artistSongs.innerHTML = '';
      el.artistEmpty.hidden = false;
      const title = el.artistEmpty.querySelector('h3');
      const desc = el.artistEmpty.querySelector('p');
      if (title) title.textContent = '加载失败';
      if (desc) desc.textContent = error.message;
      setStatus(el.artistStatus, `加载失败：${error.message}`, 'err');
    }
  }

  function renderArtist() {
    el.artistSongs.innerHTML = '';
    setStatus(el.artistStatus, state.artistSongs.length ? `共 ${state.artistSongs.length} 首` : '');
    if (!state.artistSongs.length) {
      el.artistEmpty.hidden = false;
      const title = el.artistEmpty.querySelector('h3');
      const desc = el.artistEmpty.querySelector('p');
      if (title) title.textContent = '没有搜到歌';
      if (desc) desc.textContent = '换个音源或者稍后再试。';
      return;
    }
    el.artistEmpty.hidden = true;
    state.artistSongs.forEach((song, index) => {
      el.artistSongs.appendChild(songRow(song, { queue: state.artistSongs, index, remove: false }));
    });
    refreshRowStates(el.artistSongs);
    hydrateImages(el.artistSongs);
  }

  // ------------------------------------------------------------------ 播放

  function playQueue(songs, startIndex) {
    const list = (songs || []).filter((song) => song && song.id);
    if (!list.length) return;
    state.queue = list.map((song) => ({ ...song }));
    state.index = Math.max(0, Math.min(Number(startIndex) || 0, list.length - 1));
    persistQueue();
    renderQueue();
    renderNow();
    loadCurrent(true);
  }

  function loadCurrent(autoplay) {
    const song = currentSong();
    if (!song) return;
    state.audioSongId = song.id;
    state.loading = true;
    state.position = 0;
    state.duration = Number(song.duration_s) || 0;
    state.seekDragging = false;
    state.seekValue = 0;

    try {
      audio.pause();
    } catch { /* ignore */ }
    audio.src = '/api/stream/' + idPath(song.id);
    try { audio.load(); } catch { /* ignore */ }

    renderNow();
    renderProgress();
    loadLyrics(song);
    resolveCovers([song]);
    reportHistory(song);

    if (autoplay) {
      const promise = audio.play();
      if (promise && promise.catch) {
        promise.catch((error) => {
          state.loading = false;
          state.playing = false;
          renderNow();
          const message = error && error.name === 'NotAllowedError'
            ? '浏览器拦截了自动播放，请再点一次播放'
            : `播放失败：${error && error.message ? error.message : error}`;
          toast(message, { error: true });
        });
      }
    }
  }

  function togglePlay() {
    const song = currentSong();
    if (!song) {
      toast('先搜一首歌吧');
      return;
    }
    if (state.audioSongId !== song.id) {
      loadCurrent(true);
      return;
    }
    if (audio.paused) {
      const promise = audio.play();
      if (promise && promise.catch) {
        promise.catch((error) => toast(`播放失败：${error.message || error}`, { error: true }));
      }
    } else {
      audio.pause();
    }
  }

  function nextIndex(auto) {
    const queue = state.queue;
    if (!queue.length) return null;
    if (queue.length === 1) {
      if (auto && state.mode === 'order') return null;
      return 0;
    }
    if (state.mode === 'shuffle') {
      let next = state.index;
      while (next === state.index) next = Math.floor(Math.random() * queue.length);
      return next;
    }
    const next = state.index + 1;
    if (next < queue.length) return next;
    if (!auto || state.mode === 'loopAll') return 0;
    return null;
  }

  function playAt(index) {
    if (index < 0 || index >= state.queue.length) return;
    state.index = index;
    persistQueue();
    renderQueue();
    renderNow();
    loadCurrent(true);
  }

  function next(auto = false) {
    const target = nextIndex(auto);
    if (target === null) {
      audio.pause();
      try { audio.currentTime = 0; } catch { /* ignore */ }
      state.playing = false;
      renderNow();
      return;
    }
    playAt(target);
  }

  function previous() {
    if (!state.queue.length) return;
    if (audio.currentTime > 3) {
      seekTo(0);
      return;
    }
    const target = state.index <= 0 ? state.queue.length - 1 : state.index - 1;
    playAt(target);
  }

  function handleEnded() {
    if (state.mode === 'loopOne') {
      try { audio.currentTime = 0; } catch { /* ignore */ }
      const promise = audio.play();
      if (promise && promise.catch) promise.catch(() => {});
      return;
    }
    const target = nextIndex(true);
    if (target === null) {
      audio.pause();
      try { audio.currentTime = 0; } catch { /* ignore */ }
      state.playing = false;
      renderNow();
      return;
    }
    playAt(target);
  }

  function removeAt(index) {
    if (index < 0 || index >= state.queue.length) return;
    const wasCurrent = index === state.index;
    state.queue.splice(index, 1);
    if (!state.queue.length) {
      clearQueue(false);
      return;
    }
    if (wasCurrent) {
      state.index = Math.min(index, state.queue.length - 1);
      persistQueue();
      renderQueue();
      renderNow();
      loadCurrent(true);
      return;
    }
    if (index < state.index) state.index -= 1;
    persistQueue();
    renderQueue();
    renderNow();
  }

  function clearQueue(showToast = true) {
    state.audioSongId = null;
    try {
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
    } catch { /* ignore */ }
    state.queue = [];
    state.index = -1;
    state.playing = false;
    state.loading = false;
    state.position = 0;
    state.duration = 0;
    state.lyric = { songId: null, lines: [], synced: false, hasWords: false, active: -1, loading: false, error: '', enhancedReady: false };
    persistQueue();
    renderQueue();
    renderNow();
    renderProgress();
    renderLyrics();
    if (showToast) toast('已清空播放队列');
  }

  function cycleMode() {
    const index = MODES.findIndex((item) => item.id === state.mode);
    state.mode = MODES[(index + 1) % MODES.length].id;
    persistQueue();
    renderMode();
    updateQueueSub();
    toast(`播放模式：${modeOf(state.mode).label}`, { duration: 1200 });
  }

  function renderMode() {
    const mode = modeOf(state.mode);
    [el.pbMode, el.nowMode].forEach((button) => {
      if (!button) return;
      button.innerHTML = icon(mode.icon) + (mode.badge ? `<span class="mode-badge">${mode.badge}</span>` : '');
      button.title = `播放模式：${mode.label}`;
    });
    updateQueueSub();
  }

  function durationOf() {
    const live = Number(audio.duration);
    if (Number.isFinite(live) && live > 0) return live;
    const song = currentSong();
    return Number(song && song.duration_s) || 0;
  }

  function seekTo(seconds) {
    const total = durationOf();
    if (!total) return;
    const target = Math.max(0, Math.min(Number(seconds) || 0, total));
    try { audio.currentTime = target; } catch { /* ignore */ }
    state.position = target;
    // 乐观值：浏览器真的把 currentTime 挪过去之前，进度条先停在这儿
    state.seekPending = target;
    state.seekPendingAt = performance.now();
    renderProgress();
    updateLyricActive(true);
    updateMediaSessionPosition();
  }

  /// 播放位置的连续值（秒）。
  ///
  /// 直接读 `audio.currentTime` 时，如果它几帧才更新一次，歌词就会「跳着追」；
  /// 这里在两次采样之间按真实时间往前推（最多 0.3s，真卡住时不会一直跑）。
  function playbackClock() {
    const live = Number(audio.currentTime) || 0;
    const now = performance.now();
    if (audio.paused || live !== clockPos) {
      clockPos = live;
      clockAt = now;
      return live;
    }
    return live + Math.min(0.3, (now - clockAt) / 1000);
  }

  /// 进度条当前该显示的位置：拖动中 → 拖到哪算哪；刚跳转 → 用乐观值；否则读播放器。
  function progressPosition() {
    if (state.seekDragging) return state.seekValue;
    const live = playbackClock();
    if (state.seekPending != null) {
      const settled = Math.abs(live - state.seekPending) < 0.8;
      const expired = performance.now() - state.seekPendingAt > 1500;
      if (settled || expired) state.seekPending = null;
      else return state.seekPending;
    }
    return live;
  }

  function renderProgress() {
    const total = durationOf();
    const position = progressPosition();
    const pct = total > 0 ? Math.max(0, Math.min(100, (position / total) * 100)) : 0;
    const value = total > 0 ? Math.round((position / total) * 1000) : 0;
    [el.pbSeek, el.nowSeek].forEach((input) => {
      if (!input) return;
      input.max = '1000';
      input.value = String(value);
      input.style.setProperty('--p', pct + '%');
      input.disabled = !total;
    });
    el.pbCur.textContent = fmtTime(position);
    el.pbDur.textContent = fmtTime(total);
    el.nowCur.textContent = fmtTime(position);
    el.nowDur.textContent = fmtTime(total);

    // 黑胶模式的唱臂：跟着播放进度从唱片外圈往里扫
    if (el.nowCoverWrap) {
      const arm = total > 0 ? Math.max(0, Math.min(1, position / total)) : 0;
      el.nowCoverWrap.style.setProperty('--arm', arm.toFixed(4));
    }
  }

  function renderVolume() {
    const pct = Math.round((state.muted ? 0 : state.volume) * 100);
    const muted = state.muted || state.volume <= 0;
    [el.pbVolume, el.nowVolume].forEach((input) => {
      if (!input) return;
      input.value = String(pct);
      input.style.setProperty('--p', pct + '%');
    });
    [el.pbMute, el.nowMute].forEach((button) => {
      if (!button) return;
      setUse(button, muted ? 'i-mute' : 'i-volume');
      button.title = state.muted ? '取消静音' : '静音';
    });
  }

  function persistQueue() {
    try {
      localStorage.setItem(QUEUE_KEY, JSON.stringify({
        queue: state.queue,
        index: state.index,
        mode: state.mode,
        volume: state.volume,
        muted: state.muted,
      }));
    } catch {
      // 隐私模式 / 存储满了：不影响播放
    }
  }

  function restoreQueue() {
    let saved = null;
    try {
      saved = JSON.parse(localStorage.getItem(QUEUE_KEY) || 'null');
    } catch {
      saved = null;
    }
    if (saved && Array.isArray(saved.queue)) {
      state.queue = saved.queue.filter((song) => song && song.id);
      state.index = Number.isInteger(saved.index) && saved.index >= 0 && saved.index < state.queue.length ? saved.index : (state.queue.length ? 0 : -1);
      state.mode = MODES.some((item) => item.id === saved.mode) ? saved.mode : 'loopAll';
      state.volume = Number.isFinite(saved.volume) ? Math.max(0, Math.min(1, saved.volume)) : 1;
      state.muted = Boolean(saved.muted);
    }
    audio.volume = state.volume;
    audio.muted = state.muted;
  }

  function reportHistory(song) {
    if (!song || !song.id) return;
    api('/api/library/history', { method: 'POST', body: { song_id: song.id } }).catch(() => {
      // 历史失败不影响播放
    });
  }

  function downloadSong(song) {
    if (!song || !song.id) return;
    const link = document.createElement('a');
    link.href = '/api/download/' + idPath(song.id);
    link.rel = 'noopener';
    document.body.appendChild(link);
    link.click();
    link.remove();
    toast(`已开始下载：${song.name}`, { duration: 1800 });
  }

  // ------------------------------------------------------------------ 播放器 UI

  function setCoverImage(img, url) {
    if (!img) return;
    if (url) {
      if (img.getAttribute('src') !== url) img.src = url;
      img.hidden = false;
    } else {
      img.hidden = true;
      img.removeAttribute('src');
    }
  }

  function renderNow() {
    const song = currentSong();
    const url = song ? coverFor(song) : '';
    setCoverImage(el.nowCover, url);
    setCoverImage(el.pbCover, url);

    el.nowTitle.textContent = song ? song.name : '未在播放';
    el.nowSinger.textContent = song ? (song.singers || '未知歌手') : '搜一首歌开始';
    el.nowSinger.disabled = !song || !song.singers;

    el.pbTitle.textContent = song ? song.name : '未在播放';
    el.pbSinger.textContent = song ? (song.singers || '未知歌手') : '搜一首歌开始';

    document.title = song ? `${song.name} · Tonicuisc` : '网页播放器 · Tonicuisc';

    // 黑胶模式的唱片只在播放时转
    el.nowPanel.classList.toggle('is-playing', state.playing);

    updateNowButtons();
    updatePlayButtons();
    refreshCurrentRow();
    updateMediaSession();
    updateQueueSub();
  }

  function updateNowButtons() {
    const song = currentSong();
    const liked = Boolean(song && state.library.like.has(song.id));
    const faved = Boolean(song && state.library.favorite.has(song.id));

    el.nowLike.classList.toggle('is-liked', liked);
    el.nowFav.classList.toggle('is-faved', faved);
    const likeText = el.nowLike.querySelector('span');
    const favText = el.nowFav.querySelector('span');
    if (likeText) likeText.textContent = liked ? '已喜欢' : '喜欢';
    if (favText) favText.textContent = faved ? '已收藏' : '收藏';
    setUse(el.nowLike, liked ? 'i-heart-fill' : 'i-heart');
    setUse(el.nowFav, faved ? 'i-bookmark-fill' : 'i-bookmark');

    el.pbLike.classList.toggle('is-liked', liked);
    setUse(el.pbLike, liked ? 'i-heart-fill' : 'i-heart');

    el.nowLike.disabled = !song;
    el.nowFav.disabled = !song;
    el.nowDownload.disabled = !song;
    el.pbDownload.disabled = !song;
  }

  function updatePlayButtons() {
    const playing = state.playing;
    [el.pbPlay, el.nowPlay].forEach((button) => {
      if (!button) return;
      setUse(button, playing ? 'i-pause' : 'i-play');
      button.classList.toggle('is-loading', state.loading);
      button.setAttribute('aria-label', playing ? '暂停' : '播放');
    });
  }

  function refreshCurrentRow() {
    const song = currentSong();
    $$('.song-row[data-id], .queue-row[data-id]').forEach((row) => {
      row.classList.toggle('is-current', Boolean(song && row.dataset.id === song.id));
    });
  }

  function updateQueueSub() {
    const song = currentSong();
    const mode = modeOf(state.mode);
    el.queueCardSub.textContent = song
      ? `正在播放：${song.name} · 队列 ${state.queue.length} 首 · ${mode.label}`
      : '还没有在播的歌';
    el.queueNow.textContent = song
      ? `正在播放：${song.name} · ${mode.label}`
      : '还没有在播的歌';
  }

  function renderQueue() {
    el.queueCount.textContent = String(state.queue.length);
    updateQueueSub();
    el.queueList.innerHTML = '';
    if (!state.queue.length) {
      const empty = document.createElement('p');
      empty.className = 'block-empty';
      empty.style.padding = '18px 8px';
      empty.textContent = '队列是空的。在搜索页点一首歌就会进来。';
      el.queueList.appendChild(empty);
      return;
    }
    state.queue.forEach((song, index) => {
      const row = document.createElement('div');
      row.className = 'queue-row' + (index === state.index ? ' is-current' : '');
      row.dataset.id = song.id;
      row.innerHTML = `
        <span class="song-cover">${coverMarkup(song)}</span>
        <span class="song-main">
          <p class="song-title">${esc(song.name || '未知歌曲')}</p>
          <p class="song-sub"><span class="tail">${esc(song.singers || '未知歌手')}</span></p>
        </span>
        <button class="icon-btn" type="button" data-act="remove" title="从队列移除">${icon('i-close')}</button>`;
      row.addEventListener('click', (event) => {
        if (event.target.closest('[data-act="remove"]')) {
          event.stopPropagation();
          removeAt(index);
          return;
        }
        playAt(index);
      });
      el.queueList.appendChild(row);
    });
    hydrateImages(el.queueList);
  }

  function openQueue() {
    renderQueue();
    el.queueDrawer.hidden = false;
    document.body.classList.add('lock');
  }

  function closeQueue() {
    el.queueDrawer.hidden = true;
    document.body.classList.remove('lock');
  }

  function openNowPanel() {
    clearTimeout(nowCloseTimer);
    el.nowPanel.classList.remove('is-closing');
    el.nowPanel.classList.add('is-open');
    document.body.classList.add('lock');
    recenterLyric();
  }

  function closeNowPanel() {
    if (!el.nowPanel.classList.contains('is-open')) {
      if (el.queueDrawer.hidden) document.body.classList.remove('lock');
      return;
    }
    // 宽屏是全屏铺满：先播「从上往下滑走」再摘掉 is-open，否则会硬切回侧栏
    if (DESKTOP_MQ.matches) {
      if (el.nowPanel.classList.contains('is-closing')) return;
      el.nowPanel.classList.add('is-closing');
      clearTimeout(nowCloseTimer);
      nowCloseTimer = window.setTimeout(() => {
        el.nowPanel.classList.remove('is-open', 'is-closing');
        if (el.queueDrawer.hidden) document.body.classList.remove('lock');
        recenterLyric();
      }, 300);
      return;
    }
    el.nowPanel.classList.remove('is-open', 'is-closing');
    if (el.queueDrawer.hidden) document.body.classList.remove('lock');
    recenterLyric();
  }

  /// 全屏播放页的封面样式：方形封面 / 黑胶唱片（会转 + 唱臂跟着进度扫）
  const NOW_STYLE_KEY = 'tonicuisc.nowStyle';

  function applyNowStyle(style) {
    const vinyl = style === 'vinyl';
    el.nowPanel.classList.toggle('is-vinyl', vinyl);
    if (el.nowStyle) {
      el.nowStyle.classList.toggle('is-on', vinyl);
      el.nowStyle.title = vinyl ? '切换回方形封面' : '切换黑胶唱片';
    }
    try { localStorage.setItem(NOW_STYLE_KEY, vinyl ? 'vinyl' : 'cover'); } catch { /* ignore */ }
    recenterLyric();
  }

  function toggleVinyl() {
    applyNowStyle(el.nowPanel.classList.contains('is-vinyl') ? 'cover' : 'vinyl');
  }

  function initNowStyle() {
    let style = 'cover';
    try { style = localStorage.getItem(NOW_STYLE_KEY) || 'cover'; } catch { /* ignore */ }
    applyNowStyle(style);
  }

  /// 面板尺寸变了（展开成全屏 / 收回侧栏），当前这句要重新居中。
  /// 等一帧再量，否则量到的还是旧布局。
  function recenterLyric() {
    requestAnimationFrame(() => updateLyricActive(true));
  }

  // ------------------------------------------------------------------ Media Session

  function updateMediaSession() {
    if (!('mediaSession' in navigator)) return;
    const song = currentSong();
    if (!song) {
      navigator.mediaSession.metadata = null;
      navigator.mediaSession.playbackState = 'none';
      return;
    }
    const cover = coverFor(song);
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: song.name || '未知歌曲',
        artist: song.singers || '',
        album: song.album || 'Tonicuisc',
        artwork: cover ? [{ src: cover, sizes: '512x512' }] : [],
      });
    } catch {
      // 个别浏览器不支持 MediaMetadata 的字段
    }
    navigator.mediaSession.playbackState = state.playing ? 'playing' : 'paused';
  }

  function updateMediaSessionPosition() {
    if (!('mediaSession' in navigator) || !navigator.mediaSession.setPositionState) return;
    const total = durationOf();
    if (!total) return;
    try {
      navigator.mediaSession.setPositionState({
        duration: total,
        playbackRate: audio.playbackRate || 1,
        position: Math.max(0, Math.min(Number(audio.currentTime) || 0, total)),
      });
    } catch {
      // 有些浏览器在 duration 变化时会抛
    }
  }

  function bindMediaSession() {
    if (!('mediaSession' in navigator)) return;
    const handlers = {
      play: () => { if (audio.paused) togglePlay(); },
      pause: () => audio.pause(),
      previoustrack: () => previous(),
      nexttrack: () => next(false),
      seekbackward: (details) => seekTo((Number(audio.currentTime) || 0) - (details.seekOffset || 5)),
      seekforward: (details) => seekTo((Number(audio.currentTime) || 0) + (details.seekOffset || 5)),
      seekto: (details) => {
        if (typeof details.seekTime === 'number') seekTo(details.seekTime);
      },
    };
    Object.entries(handlers).forEach(([name, handler]) => {
      try { navigator.mediaSession.setActionHandler(name, handler); } catch { /* ignore */ }
    });
  }

  // ------------------------------------------------------------------ 歌词

  function parseLrc(raw, title, artist) {
    const text = String(raw || '').replace(/\r\n/g, '\n').trim();
    if (!text || text.toUpperCase() === 'NULL') return { lines: [], synced: false, hasWords: false };

    const stampSource = /\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g;
    const metaTag = /\[[^\]]*\]/g;
    const pinyinTone = /[āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ]/;
    const cjk = /[\u4e00-\u9fff]/;
    const lines = [];
    const seen = new Map();

    for (const rawLine of text.split('\n')) {
      stampSource.lastIndex = 0;
      const stamps = Array.from(rawLine.matchAll(stampSource));
      const content = rawLine.replace(stampSource, '').replace(metaTag, '').trim();
      if (!stamps.length) {
        if (content) lines.push({ time: 0, text: content, words: [], translation: '', roman: '' });
        continue;
      }
      for (const stamp of stamps) {
        const fraction = stamp[3];
        const millis = fraction ? Number(fraction.padEnd(3, '0').slice(0, 3)) : 0;
        const time = (Number(stamp[1]) * 60 + Number(stamp[2])) * 1000 + millis;
        if (time > 0 && seen.has(time)) {
          const index = seen.get(time);
          const old = lines[index];
          if (old && content && cjk.test(content) && !cjk.test(old.text) && pinyinTone.test(old.text)) {
            old.text = content;
          }
          continue;
        }
        lines.push({ time, text: content, words: [], translation: '', roman: '' });
        if (time > 0) seen.set(time, lines.length - 1);
      }
    }

    let body = lines.filter((line) => line.text);
    if (!body.length) return { lines: [], synced: false, hasWords: false };
    body = stripHeader(body, title, artist);
    if (!body.length) return { lines: [], synced: false, hasWords: false };
    const synced = body.some((line) => line.time > 0);
    if (synced) body.sort((a, b) => a.time - b.time);
    return { lines: body, synced, hasWords: false };
  }

  function normName(value) {
    return String(value || '')
      .toLowerCase()
      .replace(/[\s\-_–—·:：()（）\[\]【】]+/g, '');
  }

  //: 只删「歌名 - 歌手」那一行头部。制作名单（词：/承制：/音乐发行：…）**保留**——
  //: 服务端已经保证它们不注音，这里再按正则删一遍只会把正文一起吞掉。
  function stripHeader(lines, title, artist) {
    const wantTitle = normName(title);
    const wantArtist = normName(artist);
    if (!wantTitle && !wantArtist) return lines;

    const isArtist = (text) => Boolean(wantArtist) &&
      (text === wantArtist || (text.length <= 12 && text.includes(wantArtist)));

    const limit = Math.min(lines.length, 3);
    let cut = 0;
    for (let i = 0; i < limit; i += 1) {
      if (lines[i].time !== 0) break;
      const text = normName(lines[i].text);
      if (!text || lines[i].text.length > 15) break;
      if (wantTitle && text === wantTitle) {
        cut = i + 1;
        continue;
      }
      if (isArtist(text)) {
        cut = i + 1;
        break;
      }
      if (i + 1 < limit && isArtist(normName(lines[i + 1].text))) {
        cut = i + 2;
      }
      break;
    }
    return cut ? lines.slice(cut) : lines;
  }

  function parseEnhanced(data) {
    if (!data || !Array.isArray(data.lines)) return null;
    const lines = [];
    data.lines.forEach((item) => {
      if (!item || typeof item !== 'object') return;
      const words = Array.isArray(item.words) ? item.words.map((word) => ({
        start: Number(word.s) || 0,
        end: Number(word.e) || 0,
        text: String(word.t || ''),
        roman: String(word.r || ''),
      })) : [];
      let text = String(item.text || '');
      if (!text) text = words.map((word) => word.text).join('');
      if (!text && !words.length) return;
      lines.push({
        time: Number(item.start) || 0,
        text,
        words,
        translation: String(item.translation || ''),
        roman: String(item.roman || ''),
      });
    });
    if (!lines.length) return null;
    lines.sort((a, b) => a.time - b.time);
    return { lines, synced: true, hasWords: lines.some((line) => line.words.length > 0) };
  }

  /// 单个字/词唱到哪了（0~1）。逐词上色而不是整行一个渐变：
  /// 整行一个渐变时，长句一旦折行，第二行会从 0 重新填充，看着就像「一行变两段」。
  function wordProgress(word, positionMs) {
    const span = word.end - word.start;
    if (span <= 0) return positionMs >= word.start ? 1 : 0;
    if (positionMs <= word.start) return 0;
    if (positionMs >= word.end) return 1;
    return (positionMs - word.start) / span;
  }

  async function loadLyrics(song) {
    const songId = song.id;
    state.lyric = {
      songId,
      lines: [],
      synced: false,
      hasWords: false,
      active: -1,
      loading: true,
      error: '',
      enhancedReady: false,
    };
    renderLyrics();
    pollEnhanced(songId);

    try {
      const raw = await apiText('/api/lyric/' + idPath(songId));
      if (state.lyric.songId !== songId || state.lyric.enhancedReady) return;
      const sheet = parseLrc(raw, song.name, song.singers);
      state.lyric = { ...state.lyric, ...sheet, loading: false, error: '' };
      renderLyrics();
      updateLyricActive(true);
    } catch (error) {
      if (error.status === 401) return;
      if (state.lyric.songId !== songId || state.lyric.enhancedReady) return;
      state.lyric.loading = false;
      state.lyric.error = error.status === 404 ? '暂无歌词' : `歌词获取失败：${error.message}`;
      renderLyrics();
    }
  }

  async function pollEnhanced(songId) {
    for (let attempt = 0; attempt < 6; attempt += 1) {
      if (state.lyric.songId !== songId) return;
      try {
        const data = await api('/api/lyric/' + idPath(songId) + '?format=enhanced');
        if (state.lyric.songId !== songId) return;
        if (data && data.ready) {
          const sheet = parseEnhanced(data);
          if (sheet) {
            state.lyric = {
              ...state.lyric,
              ...sheet,
              loading: false,
              error: '',
              enhancedReady: true,
              active: -1,
            };
            renderLyrics();
            updateLyricActive(true);
            return;
          }
        }
      } catch (error) {
        if (error.status === 401) return;
      }
      await sleep(2000);
    }
  }

  function renderLyrics() {
    const lyric = state.lyric;
    el.lyricLines.innerHTML = '';
    if (!currentSong()) {
      el.lyricEmpty.hidden = false;
      el.lyricEmpty.querySelector('p').textContent = '播放后显示歌词';
      return;
    }
    if (lyric.loading) {
      el.lyricEmpty.hidden = false;
      el.lyricEmpty.querySelector('p').textContent = '正在加载歌词…';
      return;
    }
    if (lyric.error) {
      el.lyricEmpty.hidden = false;
      el.lyricEmpty.querySelector('p').textContent = lyric.error;
      return;
    }
    if (!lyric.lines.length) {
      el.lyricEmpty.hidden = false;
      el.lyricEmpty.querySelector('p').textContent = '暂无歌词';
      return;
    }

    el.lyricEmpty.hidden = true;
    lyric.lines.forEach((line, index) => {
      const node = document.createElement('div');
      node.className = 'lyric-line' + (lyric.synced ? ' is-synced' : '');
      node.dataset.index = String(index);
      const main = document.createElement('span');
      const karaoke = Boolean(line.words && line.words.length);
      main.className = 'main-text' + (karaoke ? ' kara' : '');
      if (karaoke) {
        // 每个字/词一个 span，各自按自己的时间填充（见 wordProgress）。
        line.words.forEach((word, wi) => {
          const span = document.createElement('span');
          span.className = 'w';
          span.dataset.w = String(wi);
          span.textContent = word.text;
          main.appendChild(span);
        });
        if (!main.textContent) main.textContent = line.text || '♪';
      } else {
        main.textContent = line.text || '♪';
      }
      node.appendChild(main);
      if (line.translation) {
        const trans = document.createElement('span');
        trans.className = 'trans';
        trans.textContent = line.translation;
        node.appendChild(trans);
      }
      if (line.roman) {
        const roman = document.createElement('span');
        roman.className = 'roman';
        roman.textContent = line.roman;
        node.appendChild(roman);
      }
      if (lyric.synced) {
        node.addEventListener('click', () => seekTo(line.time / 1000));
      }
      el.lyricLines.appendChild(node);
    });

    lyric.active = -1;
    updateLyricActive(true);
  }

  function updateLyricActive(force = false) {
    const lyric = state.lyric;
    if (!lyric.lines.length || !lyric.synced) return;
    // 和进度条用同一个位置：点了歌词/拖了进度条，高亮立刻跟过去，不用等 currentTime 真的挪好
    const position = progressPosition() * 1000;
    let index = -1;
    for (let i = 0; i < lyric.lines.length; i += 1) {
      if (lyric.lines[i].time <= position) index = i;
      else break;
    }
    if (index !== lyric.active || force) {
      lyric.active = index;
      $$('.lyric-line', el.lyricLines).forEach((node, i) => {
        node.classList.toggle('is-active', i === index);
      });
      if (index >= 0) scrollLyric(index, force);
    }
    const node = index >= 0 ? el.lyricLines.children[index] : null;
    const activeLine = index >= 0 ? lyric.lines[index] : null;
    if (node && activeLine && activeLine.words && activeLine.words.length) {
      const spans = node.querySelectorAll('.main-text.kara .w');
      activeLine.words.forEach((word, wi) => {
        const span = spans[wi];
        if (!span) return;
        span.style.setProperty('--p', (wordProgress(word, position) * 100).toFixed(2) + '%');
      });
    }
  }

  function scrollLyric(index, jump) {
    const node = el.lyricLines.children[index];
    if (!node) return;
    const target = Math.max(0, node.offsetTop - el.lyrics.clientHeight / 2 + node.offsetHeight / 2);
    if (jump) {
      // 跳转 / 刚加载：直接落位，别让歌词慢慢飘过去
      stopLyricScroll();
      el.lyrics.scrollTop = target;
      return;
    }
    tweenLyricScroll(target);
  }

  /// 自己补间，不用浏览器自带的 `behavior: 'smooth'`：
  /// 那条动画又短又硬（~300ms 匀速），下一句来得快时还会被硬生生打断，看着一顿一顿的。
  function tweenLyricScroll(target) {
    const start = el.lyrics.scrollTop;
    const distance = target - start;
    if (Math.abs(distance) < 1) return;
    stopLyricScroll();
    // 距离越远走得越久，但都压在 200~460ms：太长会「慢半拍」，太短又硬
    const duration = Math.min(460, Math.max(200, Math.abs(distance) * 2.6));
    const startedAt = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - startedAt) / duration);
      const eased = 1 - Math.pow(1 - t, 3); // easeOutCubic：起步快、收尾软
      el.lyrics.scrollTop = start + distance * eased;
      lyricScrollFrame = t < 1 ? requestAnimationFrame(step) : 0;
    };
    lyricScrollFrame = requestAnimationFrame(step);
  }

  function stopLyricScroll() {
    if (lyricScrollFrame) cancelAnimationFrame(lyricScrollFrame);
    lyricScrollFrame = 0;
  }

  function startLyricLoop() {
    if (lyricFrame) return;
    let progressAt = 0;
    const step = (now) => {
      if (audio.paused) {
        lyricFrame = 0;
        return;
      }
      updateLyricActive();
      // 进度条 / 时间文字 15fps 足够，别跟歌词抢每一帧
      if (now - progressAt >= 66) {
        progressAt = now;
        renderProgress();
      }
      lyricFrame = requestAnimationFrame(step);
    };
    lyricFrame = requestAnimationFrame(step);
  }

  function stopLyricLoop() {
    if (lyricFrame) cancelAnimationFrame(lyricFrame);
    lyricFrame = 0;
  }

  // ------------------------------------------------------------------ 我的

  async function loadProfile() {
    try {
      const [me, devices] = await Promise.all([api('/api/me'), api('/api/devices')]);
      state.me = me || state.me;
      state.devices = Array.isArray(devices && devices.items) ? devices.items : [];
      updateAccountUI();
      renderDevices();
    } catch (error) {
      if (error.status === 401) return;
      toast(`加载账户信息失败：${error.message}`, { error: true });
    }
    loadAbout();
  }

  async function loadAbout() {
    el.aboutVersion.textContent = 'Web 1.0';
    try {
      const health = await api('/health');
      const sources = Array.isArray(health && health.sources) ? health.sources.join(' / ') : '-';
      el.aboutServer.textContent = `${sources} · ${(health && health.database) || '-'}`;
    } catch {
      el.aboutServer.textContent = '暂时连不上';
    }
    try {
      const update = await api('/api/update?platform=windows&arch=x64');
      el.aboutLatest.textContent = update && update.latest ? `v${update.latest}` : '未知';
    } catch {
      el.aboutLatest.textContent = '检查失败';
    }
  }

  function renderDevices() {
    el.deviceList.innerHTML = '';
    const devices = state.devices.filter((device) => device && !device.revoked);
    el.deviceEmpty.hidden = devices.length > 0;
    devices.forEach((device) => {
      const row = document.createElement('div');
      row.className = 'device-row';
      row.innerHTML = `
        <span class="device-icon">${icon(device.current ? 'i-devices' : 'i-devices')}</span>
        <span class="device-text">
          <b>${esc(device.name || '未命名设备')}</b>
          <small>${esc(device.username || '无账户（旧版残留）')} · 最后使用 ${esc(fmtRelative(device.last_seen))}</small>
        </span>
        ${device.current ? '<span class="device-badge">本机</span>' : ''}
        <button class="icon-btn danger" type="button" title="吊销">${icon('i-logout')}</button>`;
      const revoke = row.querySelector('button');
      revoke.addEventListener('click', () => revokeDevice(device));
      el.deviceList.appendChild(row);
    });
  }

  async function revokeDevice(device) {
    const ok = await confirmDialog({
      title: '吊销这台设备？',
      message: `${device.name || '未命名设备'}\n\n吊销后它需要重新登录。`,
      confirmText: '吊销',
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/devices/${encodeURIComponent(device.id)}`, { method: 'DELETE' });
      if (device.current) {
        resetToGate('本机设备已被吊销，请重新登录');
        return;
      }
      toast(`已吊销 ${device.name || '该设备'}`);
      await loadProfile();
    } catch (error) {
      if (error.status === 401) return;
      toast(`吊销失败：${error.message}`, { error: true });
    }
  }

  async function logout() {
    const ok = await confirmDialog({
      title: '退出登录？',
      message: '清除这个浏览器的会话，API Key 也会从服务端内存里删掉。',
      confirmText: '退出',
      danger: true,
    });
    if (!ok) return;
    resetToGate('已退出登录', { error: false });
  }

  async function relogin() {
    const ok = await confirmDialog({
      title: '重新配对？',
      message: '会先退出当前会话，然后生成一个新的设备码。',
      confirmText: '重新配对',
    });
    if (!ok) return;
    resetToGate('请用新的设备码登录', { error: false });
  }

  // ------------------------------------------------------------------ 事件绑定

  function bindGate() {
    el.copyCode.addEventListener('click', async () => {
      const code = el.gateCode.textContent || '';
      if (!code) return;
      try {
        await navigator.clipboard.writeText(code);
        toast('设备码已复制');
      } catch {
        toast('复制失败，请手动选中设备码');
      }
    });

    el.gateRefresh.addEventListener('click', () => checkSession());
    el.gateRetry.addEventListener('click', () => beginPairing());
    el.gateRestart.addEventListener('click', async () => {
      try {
        await sessionRequest('/music/api/session/cancel', { method: 'POST' });
      } catch { /* ignore */ }
      beginPairing();
    });

    el.manualForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const key = el.manualKey.value.trim();
      if (!key) return;
      const button = el.manualForm.querySelector('button[type="submit"]');
      if (button) button.disabled = true;
      try {
        const data = await sessionRequest('/music/api/session/manual', {
          method: 'POST',
          body: { api_key: key },
        });
        el.manualKey.value = '';
        applySession({ authenticated: true, state: 'ready', me: data && data.me });
      } catch (error) {
        gateShowError(error.message || 'API Key 登录失败');
      } finally {
        if (button) button.disabled = false;
      }
    });
  }

  function bindSearch() {
    el.searchForm.addEventListener('submit', (event) => {
      event.preventDefault();
      runSearch(el.searchInput.value, { refresh: true });
    });

    el.searchInput.addEventListener('input', () => {
      if (!el.searchInput.value.trim() && !state.searching) {
        state.keyword = '';
        state.results = [];
        state.hasMore = false;
        state.searchError = '';
        el.searchList.innerHTML = '';
        el.searchEmpty.hidden = true;
        el.searchMore.hidden = true;
        showHistory();
        updateSearchStatus();
      }
    });

    el.historyRefresh.addEventListener('click', () => loadHistory());
    el.searchMoreBtn.addEventListener('click', () => loadMore());

    let scrollTick = 0;
    const onScroll = () => {
      if (scrollTick) return;
      scrollTick = window.setTimeout(() => {
        scrollTick = 0;
        if (state.view !== 'search') return;
        if (scrollHostNearBottom(700)) loadMore();
      }, 160);
    };
    // 宽屏滚的是 `.main`（它自己的 scroll 事件不会冒到 window），两边都挂
    window.addEventListener('scroll', onScroll, { passive: true });
    if (el.main) el.main.addEventListener('scroll', onScroll, { passive: true });
  }

  function bindPlayer() {
    // 用户自己滚歌词时别跟他抢：一滚就停掉自动补间
    el.lyrics.addEventListener('wheel', stopLyricScroll, { passive: true });
    el.lyrics.addEventListener('touchstart', stopLyricScroll, { passive: true });

    el.pbPlay.addEventListener('click', togglePlay);
    el.nowPlay.addEventListener('click', togglePlay);
    el.pbPrev.addEventListener('click', previous);
    el.nowPrev.addEventListener('click', previous);
    el.pbNext.addEventListener('click', () => next(false));
    el.nowNext.addEventListener('click', () => next(false));
    el.pbMode.addEventListener('click', cycleMode);
    el.nowMode.addEventListener('click', cycleMode);

    el.pbLike.addEventListener('click', () => {
      const song = currentSong();
      if (song) toggleLibrary('like', song);
    });
    el.nowLike.addEventListener('click', () => {
      const song = currentSong();
      if (song) toggleLibrary('like', song);
    });
    el.nowFav.addEventListener('click', () => {
      const song = currentSong();
      if (song) toggleLibrary('favorite', song);
    });
    el.nowDownload.addEventListener('click', () => downloadSong(currentSong()));
    el.pbDownload.addEventListener('click', () => downloadSong(currentSong()));

    [el.pbQueue, el.nowQueue, el.nowQueue2, el.openQueueCard].forEach((button) => {
      button.addEventListener('click', openQueue);
    });
    el.pbExpand.addEventListener('click', openNowPanel);
    el.pbCoverBtn.addEventListener('click', openNowPanel);
    el.nowClose.addEventListener('click', closeNowPanel);

    el.nowSinger.addEventListener('click', () => {
      const song = currentSong();
      if (song && song.singers) openArtist(song.singers);
    });

    el.queueClear.addEventListener('click', async () => {
      if (!state.queue.length) return;
      const ok = await confirmDialog({
        title: '清空播放队列？',
        message: '当前播放会停止，队列里的歌都会移除。',
        confirmText: '清空',
        danger: true,
      });
      if (!ok) return;
      clearQueue();
      closeQueue();
    });

    $$('[data-close="queue"]').forEach((node) => node.addEventListener('click', closeQueue));

    [el.pbSeek, el.nowSeek].forEach((input) => {
      input.addEventListener('input', () => {
        const total = durationOf();
        if (!total) return;
        state.seekDragging = true;
        state.seekValue = (Number(input.value) / 1000) * total;
        renderProgress();
      });
      input.addEventListener('change', () => {
        const total = durationOf();
        if (!total) return;
        const value = (Number(input.value) / 1000) * total;
        state.seekDragging = false;
        seekTo(value);
      });
    });

    el.pbVolume.addEventListener('input', () => {
      state.volume = Math.max(0, Math.min(1, Number(el.pbVolume.value) / 100));
      state.muted = state.volume <= 0;
      audio.volume = state.volume;
      audio.muted = state.muted;
      renderVolume();
      persistQueue();
    });

    el.pbMute.addEventListener('click', () => {
      state.muted = !state.muted;
      audio.muted = state.muted;
      if (!state.muted && state.volume <= 0) {
        state.volume = 0.6;
        audio.volume = state.volume;
      }
      renderVolume();
      persistQueue();
    });

    // 全屏播放页里那套音量（和播放条上的共用同一份 state）
    el.nowVolume.addEventListener('input', () => {
      state.volume = Math.max(0, Math.min(1, Number(el.nowVolume.value) / 100));
      state.muted = state.volume <= 0;
      audio.volume = state.volume;
      audio.muted = state.muted;
      renderVolume();
      persistQueue();
    });

    el.nowMute.addEventListener('click', () => {
      state.muted = !state.muted;
      audio.muted = state.muted;
      if (!state.muted && state.volume <= 0) {
        state.volume = 0.6;
        audio.volume = state.volume;
      }
      renderVolume();
      persistQueue();
    });

    el.nowStyle.addEventListener('click', toggleVinyl);
  }

  function bindAudio() {
    audio.addEventListener('play', () => {
      state.playing = true;
      state.loading = false;
      renderNow();
      startLyricLoop();
    });
    audio.addEventListener('pause', () => {
      state.playing = false;
      renderNow();
      stopLyricLoop();
    });
    audio.addEventListener('waiting', () => {
      state.loading = true;
      renderNow();
    });
    audio.addEventListener('canplay', () => {
      state.loading = false;
      renderNow();
    });
    audio.addEventListener('loadedmetadata', () => {
      state.duration = durationOf();
      renderProgress();
      updateMediaSessionPosition();
    });
    audio.addEventListener('durationchange', () => {
      renderProgress();
    });
    audio.addEventListener('timeupdate', () => {
      if (!state.seekDragging) state.position = Number(audio.currentTime) || 0;
      renderProgress();
      // 兜底：万一 rAF 循环中途断了（比如某次 paused 判断），这里把它拉回来，
      // 否则歌词会退化成一秒四跳
      if (state.playing) startLyricLoop();
      updateLyricActive();
      updateMediaSessionPosition();
    });
    audio.addEventListener('ended', handleEnded);
    audio.addEventListener('error', () => {
      if (!state.audioSongId) return;
      state.loading = false;
      state.playing = false;
      renderNow();
      toast('播放失败：音源暂时不可用，换一首试试', { error: true });
    });
    bindMediaSession();
  }

  function bindProfile() {
    el.devicesRefresh.addEventListener('click', () => loadProfile());
    el.logoutBtn.addEventListener('click', logout);
    el.reloginBtn.addEventListener('click', relogin);
    el.libraryRefresh.addEventListener('click', () => loadSummary());

    $$('[data-theme-choice]', el.themeSegment).forEach((button) => {
      button.addEventListener('click', () => applyTheme(button.dataset.themeChoice));
    });
    el.themeBtn.addEventListener('click', () => {
      const order = ['auto', 'light', 'dark'];
      const next = order[(order.indexOf(state.theme) + 1) % order.length];
      applyTheme(next);
    });
  }

  function bindNav() {
    $$('[data-nav]').forEach((button) => {
      button.addEventListener('click', () => showView(button.dataset.nav));
    });
    $$('[data-lib]').forEach((button) => {
      button.addEventListener('click', () => openLibrary(button.dataset.lib));
    });
    el.listBack.addEventListener('click', () => showView('library'));
    el.artistBack.addEventListener('click', () => showView(state.artistFrom || 'search'));
    el.listPlayAll.addEventListener('click', () => {
      if (state.listSongs.length) playQueue(state.listSongs, 0);
    });
    el.listClear.addEventListener('click', clearLibrary);
    el.artistPlayAll.addEventListener('click', () => {
      if (state.artistSongs.length) playQueue(state.artistSongs, 0);
    });
    el.artistRefresh.addEventListener('click', () => openArtist(el.artistName.textContent));
  }

  function bindKeyboard() {
    document.addEventListener('keydown', (event) => {
      const target = event.target;
      const typing = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);

      if (event.key === 'Escape') {
        if (!el.modalHost.hidden) {
          el.modalHost.hidden = true;
          el.modalHost.innerHTML = '';
          return;
        }
        if (!el.queueDrawer.hidden) {
          closeQueue();
          return;
        }
        if (el.nowPanel.classList.contains('is-open')) {
          closeNowPanel();
          return;
        }
      }

      if (typing) return;
      if (event.ctrlKey || event.metaKey || event.altKey) return;

      switch (event.code) {
        case 'Space':
          event.preventDefault();
          togglePlay();
          break;
        case 'ArrowLeft':
          event.preventDefault();
          seekTo((Number(audio.currentTime) || 0) - 5);
          break;
        case 'ArrowRight':
          event.preventDefault();
          seekTo((Number(audio.currentTime) || 0) + 5);
          break;
        case 'KeyN':
          next(false);
          break;
        case 'KeyP':
          previous();
          break;
        case 'KeyL': {
          const song = currentSong();
          if (song) toggleLibrary('like', song);
          break;
        }
        case 'KeyM':
          state.muted = !state.muted;
          audio.muted = state.muted;
          renderVolume();
          persistQueue();
          break;
        case 'KeyQ':
          openQueue();
          break;
        case 'Slash':
          event.preventDefault();
          showView('search');
          el.searchInput.focus();
          break;
        default:
          break;
      }
    });
  }

  // ------------------------------------------------------------------ 初始化

  function init() {
    el.modalHost.hidden = true;
    initTheme();
    initNowStyle();
    bindGate();
    bindSearch();
    bindPlayer();
    bindAudio();
    bindProfile();
    bindNav();
    bindKeyboard();
    renderMode();
    renderCounts();
    updateAccountUI();

    state.gateTimer = setInterval(updateGateStatus, 1000);
    checkSession();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
