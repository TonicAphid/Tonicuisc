/* ==========================================================================
   Tonicuisc 落地页脚本
   三件事：生成右侧方块云、滚动进场、把下载按钮指向最新版本。
   没有依赖，没有构建，全部原生 API。
   ========================================================================== */

(function () {
  'use strict';

  /* ------------------------------------------------------------ 方块云 */
  // 用固定种子的伪随机，保证同一尺寸下每次刷新布局一致（不会“跳”）。
  function mulberry32(seed) {
    return function () {
      seed |= 0;
      seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // 主站方块墙的配色：白 → 极浅蓝 → 中蓝，越蓝越少。
  const PALETTE = [
    ['#ffffff', 0.24],
    ['#f8fcff', 0.14],
    ['#eef7ff', 0.13],
    ['#e2f1ff', 0.14],
    ['#d3e9ff', 0.13],
    ['#c0e0ff', 0.11],
    ['#a6d4ff', 0.08],
    ['#8ac6ff', 0.03],
  ];

  function pickColor(rand) {
    let r = rand();
    for (let i = 0; i < PALETTE.length; i++) {
      r -= PALETTE[i][1];
      if (r <= 0) return PALETTE[i][0];
    }
    return PALETTE[0][0];
  }

  function buildCubes() {
    const host = document.getElementById('cubes');
    if (!host) return;

    const width = host.clientWidth;
    const height = host.clientHeight;
    if (!width || !height) return;

    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const rand = mulberry32(20261024 + Math.round(width));
    const unit = Math.max(38, Math.min(84, Math.round(Math.min(width / 8.6, height / 6.2))));
    const gap = Math.max(3, Math.round(unit * 0.06));
    const cols = Math.ceil(width / unit) + 2;
    const rows = Math.ceil(height / unit) + 2;
    const startX = -unit * 0.55;
    const startY = -unit * 0.45;

    // 占位表：保证方块不重叠，整面墙像铺砖一样严丝合缝。
    const used = [];
    for (let r = 0; r < rows; r++) used.push(new Array(cols).fill(false));

    const frag = document.createDocumentFragment();
    host.textContent = '';

    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) {
        if (used[row][col]) continue;

        // 偶尔空一格，避免过于机械。
        if (rand() < 0.05) { used[row][col] = true; continue; }

        // 大小：大多数是 1×1，少数并成 2×1 / 1×2 / 2×2。
        let w = 1;
        let h = 1;
        const roll = rand();
        if (roll > 0.915) { w = 2; h = 2; }
        else if (roll > 0.78) { w = 2; h = 1; }
        else if (roll > 0.63) { w = 1; h = 2; }

        if (col + w > cols) w = 1;
        if (row + h > rows) h = 1;

        let free = true;
        for (let y = row; y < row + h && free; y++) {
          for (let x = col; x < col + w; x++) {
            if (used[y][x]) { free = false; break; }
          }
        }
        if (!free) { w = 1; h = 1; }

        for (let y = row; y < row + h; y++) {
          for (let x = col; x < col + w; x++) used[y][x] = true;
        }

        const size = unit - gap;
        const el = document.createElement('i');
        const color = pickColor(rand);

        el.style.left = (startX + col * unit) + 'px';
        el.style.top = (startY + row * unit) + 'px';
        el.style.width = (size * w + gap * (w - 1)) + 'px';
        el.style.height = (size * h + gap * (h - 1)) + 'px';
        el.style.setProperty('--rad', Math.round(unit * 0.15) + 'px');
        // 上亮下实的轻微渐变，模仿主站那种“塑料块”的质感。
        el.style.background =
          'linear-gradient(170deg, rgba(255,255,255,.55) 0%, rgba(255,255,255,0) 58%), ' + color;

        // 极少数 1×1 做成圆形，避免整面墙太规整。
        if (w === 1 && h === 1 && rand() < 0.035) el.classList.add('round');

        // 极少数块轻轻浮动，其余保持静止。
        if (!reduce && rand() < 0.16) {
          el.className = 'drift';
          el.style.setProperty('--dur', (9 + rand() * 6).toFixed(2) + 's');
          el.style.setProperty('--delay', (-rand() * 9).toFixed(2) + 's');
        }

        // 1×1 的块上偶尔放个小图标（小屏幕 / 圆点 / 两条线）。
        if (w === 1 && h === 1 && rand() < 0.18) {
          const detail = document.createElement('span');
          const kind = rand();
          detail.className = 'd ' + (kind < 0.45 ? 'screen' : kind < 0.75 ? 'dot' : 'lines');
          el.appendChild(detail);
        }

        frag.appendChild(el);
      }
    }

    host.appendChild(frag);
  }

  // 尺寸变了才重排（窗口拖动时避免每一帧都重建）。
  function watchCubes() {
    const host = document.getElementById('cubes');
    if (!host || typeof ResizeObserver !== 'function') {
      window.addEventListener('resize', buildCubes);
      return;
    }
    let lastWidth = host.clientWidth;
    let timer = 0;
    const ro = new ResizeObserver(function () {
      const w = host.clientWidth;
      if (Math.abs(w - lastWidth) < 24) return;
      lastWidth = w;
      window.clearTimeout(timer);
      timer = window.setTimeout(buildCubes, 160);
    });
    ro.observe(host);
  }

  /* ------------------------------------------------------------ 滚动进场 */
  function setupReveal() {
    const items = Array.prototype.slice.call(document.querySelectorAll('.reveal'));
    if (!items.length) return;

    // 没有 IntersectionObserver 或者用户不想要动画：直接显示。
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce || !('IntersectionObserver' in window)) {
      items.forEach(function (el) { el.classList.add('in'); });
      return;
    }

    const io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        const el = entry.target;
        // 同一屏里按顺序错开一点，看起来像依次浮上来。
        const delay = Number(el.dataset.delay || 0);
        window.setTimeout(function () { el.classList.add('in'); }, delay);
        io.unobserve(el);
      });
    }, { rootMargin: '0px 0px -12% 0px', threshold: 0.12 });

    // 给同一组里的卡片编号，做出阶梯感。
    document.querySelectorAll('.cards .reveal, .faq .reveal').forEach(function (el, i) {
      el.dataset.delay = String(Math.min(i, 6) * 70);
    });

    items.forEach(function (el) { io.observe(el); });
  }

  /* ------------------------------------------------------------ 导航毛玻璃 */
  function setupNav() {
    const nav = document.getElementById('nav');
    if (!nav) return;

    let ticking = false;
    function update() {
      ticking = false;
      nav.classList.toggle('scrolled', window.scrollY > 12);
    }
    window.addEventListener('scroll', function () {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(update);
    }, { passive: true });
    update();
  }

  /* ------------------------------------------------------------ 下载链接 */
  function setupDownload() {
    const win = document.getElementById('dl-win');
    const version = document.getElementById('dl-version');
    if (!win) return;

    // 全部用同源相对地址：页面由 Python 服务托管，域名换成哪个都能用。
    // 下载按钮指向 /download/windows，由服务端 302 到最新的安装包。
    if (!win.getAttribute('href')) win.href = 'download/windows';

    if (typeof fetch !== 'function') return;

    const url = 'api/update?platform=windows&arch=x64';
    const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = window.setTimeout(function () {
      if (ctrl) ctrl.abort();
    }, 6000);

    fetch(url, { signal: ctrl ? ctrl.signal : undefined, mode: 'cors' })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) {
        if (!data || typeof data !== 'object') return;

        // 服务端字段：latest / asset.url / asset.direct_url / asset.size
        const label = data.latest || data.version || '';
        if (label && version) {
          version.textContent = /^v/i.test(label) ? label : 'v' + label;
        }

        const asset = data.asset || {};

        const size = document.getElementById('dl-size');
        if (size && asset.size > 0) {
          size.textContent = '约 ' + Math.max(1, Math.round(asset.size / 1048576)) + ' MB';
        }
      })
      .catch(function () {
        // 静默失败：按钮仍指向 /download/windows，由服务端兜底。
      })
      .then(function () {
        window.clearTimeout(timer);
      });
  }

  /* ------------------------------------------------------------ 启动 */
  function boot() {
    buildCubes();
    watchCubes();
    setupReveal();
    setupNav();
    setupDownload();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
