#!/usr/bin/env node
/**
 * Tonicuisc 网页入口（零依赖）。
 *
 *   node webapp/server.js
 *
 * 负责三件事：
 *   1. 托管落地页（`/`）和网页播放器（`/music`）；
 *   2. 用 HttpOnly Cookie 保存会话，API Key 只留在服务端；
 *   3. 把 `/api`、`/login`、`/health` 反代给 Python（FastAPI），
 *      音频流也走这里，这样浏览器不用自己带 `X-API-Key`。
 *
 * 环境变量：
 *   TONICUISC_WEB_HOST      监听地址，默认 127.0.0.1
 *   TONICUISC_WEB_PORT      监听端口，默认 8080
 *   TONICUISC_API           Python 服务地址，默认 http://127.0.0.1:8000
 *   TONICUISC_WEB_PUBLIC    静态目录，默认 webapp/public
 *   TONICUISC_WEB_SESSION_TTL  会话有效期（秒），默认 604800（7 天）
 *
 * 不装任何 npm 包：部署机上只要有 Node 18+ 就能直接跑。
 */

'use strict';

const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const HOST = process.env.TONICUISC_WEB_HOST || '127.0.0.1';
const PORT = Number(process.env.TONICUISC_WEB_PORT || 8080);
const API_BASE = (process.env.TONICUISC_API || 'http://127.0.0.1:8000').replace(/\/+$/, '');
const PUBLIC_DIR = path.resolve(process.env.TONICUISC_WEB_PUBLIC || path.join(__dirname, 'public'));
const SESSION_TTL = Number(process.env.TONICUISC_WEB_SESSION_TTL || 7 * 24 * 60 * 60) * 1000;

const SESSION_COOKIE = 'tuisc_session';
const SESSION_COOKIE_MAX_AGE = Math.max(60, Math.round(SESSION_TTL / 1000));
const MAX_JSON_BODY = 64 * 1024;

/** @type {Map<string, {id:string, apiKey:string|null, me:object|null, pending:null|{user_code:string,poll_secret:string,expires_at:number}, created_at:number, last_seen:number}>} */
const sessions = new Map();

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.mjs': 'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.txt': 'text/plain; charset=utf-8',
  '.woff2': 'font/woff2',
};

const PLATFORMS = new Set(['windows', 'macos', 'linux']);

//: 设备码字母表：去掉 0/O/1/I，和 Python 侧保持一致（32 个字符）
const CODE_ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ';

const HOP_BY_HOP = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
]);

// ---------------------------------------------------------------- 小工具

function now() {
  return Date.now();
}

function protoOf(req) {
  const forwarded = String(req.headers['x-forwarded-proto'] || '').split(',')[0].trim();
  if (forwarded === 'https' || forwarded === 'http') return forwarded;
  return req.socket && req.socket.encrypted ? 'https' : 'http';
}

function clientIp(req) {
  const forwarded = String(req.headers['x-forwarded-for'] || '').split(',')[0].trim();
  if (forwarded) return forwarded.slice(0, 64);
  return (req.socket && req.socket.remoteAddress ? req.socket.remoteAddress : 'unknown').slice(0, 64);
}

function parseCookies(header) {
  const out = {};
  for (const part of String(header || '').split(';')) {
    const index = part.indexOf('=');
    if (index < 0) continue;
    const key = part.slice(0, index).trim();
    if (!key) continue;
    out[key] = decodeURIComponent(part.slice(index + 1).trim());
  }
  return out;
}

function sessionOf(req) {
  const id = parseCookies(req.headers.cookie)[SESSION_COOKIE];
  if (!id) return null;
  const session = sessions.get(id);
  if (!session) return null;
  if (now() - session.last_seen > SESSION_TTL) {
    sessions.delete(id);
    return null;
  }
  session.last_seen = now();
  return session;
}

function newSession() {
  const id = crypto.randomBytes(32).toString('base64url');
  const session = {
    id,
    apiKey: null,
    me: null,
    pending: null,
    created_at: now(),
    last_seen: now(),
  };
  sessions.set(id, session);
  return session;
}

function cookieFor(req, value, maxAge) {
  const parts = [`${SESSION_COOKIE}=${encodeURIComponent(value)}`, 'Path=/', 'HttpOnly', 'SameSite=Lax', `Max-Age=${maxAge}`];
  if (protoOf(req) === 'https') parts.push('Secure');
  return parts.join('; ');
}

function setSessionCookie(req, res, session) {
  res.setHeader('Set-Cookie', cookieFor(req, session.id, SESSION_COOKIE_MAX_AGE));
}

function clearSessionCookie(req, res) {
  res.setHeader('Set-Cookie', cookieFor(req, '', 0));
}

function sendJson(res, status, payload, headers) {
  const body = Buffer.from(JSON.stringify(payload));
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': String(body.length),
    'cache-control': 'no-store',
    ...(headers || {}),
  });
  res.end(body);
}

function sendText(res, status, text, type) {
  const body = Buffer.from(String(text));
  res.writeHead(status, {
    'content-type': type || 'text/plain; charset=utf-8',
    'content-length': String(body.length),
    'cache-control': 'no-store',
  });
  res.end(body);
}

function sendErrorPage(res, status, title, detail) {
  const safe = (value) =>
    String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  const html = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${safe(title)} · Tonicuisc</title>
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #f7fbff; color: #0b0f17;
         font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; }
  @media (prefers-color-scheme: dark) {
    body { background: #0a0f17; color: #eef4fb; }
    p { color: #8fa1b6 !important; }
  }
  main { max-width: 520px; padding: 40px 28px; text-align: center; }
  h1 { margin: 0 0 12px; font-size: 26px; letter-spacing: -.02em; }
  p { margin: 0 0 26px; color: #6b7a8d; font-size: 14.5px; line-height: 1.75; }
  a { display: inline-block; padding: 11px 26px; border-radius: 999px;
      background: #0a6cf1; color: #fff; text-decoration: none; font-weight: 600; font-size: 14.5px; }
</style>
</head>
<body>
<main>
  <h1>${safe(title)}</h1>
  <p>${safe(detail)}</p>
  <a href="/">返回首页</a>
</main>
</body>
</html>`;
  sendText(res, status, html, 'text/html; charset=utf-8');
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_JSON_BODY) {
        reject(Object.assign(new Error('请求体太大'), { status: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (!chunks.length) return resolve({});
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      } catch {
        reject(Object.assign(new Error('请求体不是合法 JSON'), { status: 400 }));
      }
    });
    req.on('error', reject);
  });
}

function randomUserCode() {
  const bytes = crypto.randomBytes(8);
  let raw = '';
  for (let i = 0; i < 8; i += 1) raw += CODE_ALPHABET[bytes[i] % CODE_ALPHABET.length];
  return `${raw.slice(0, 4)}-${raw.slice(4)}`;
}

function normalizeUserCode(value) {
  const raw = String(value || '')
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, '');
  if (raw.length !== 8) return '';
  return `${raw.slice(0, 4)}-${raw.slice(4)}`;
}

// ---------------------------------------------------------------- Python 调用

async function apiCall(req, pathname, options = {}) {
  const headers = {
    accept: 'application/json',
    'x-forwarded-for': clientIp(req),
    'x-forwarded-proto': protoOf(req),
    ...(options.headers || {}),
  };
  if (options.key) headers['x-api-key'] = options.key;
  let body;
  if (options.body !== undefined) {
    headers['content-type'] = 'application/json; charset=utf-8';
    body = JSON.stringify(options.body);
  }
  try {
    const response = await fetch(API_BASE + pathname, {
      method: options.method || 'GET',
      headers,
      body,
      signal: AbortSignal.timeout(options.timeout || 30000),
    });
    const text = await response.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = null;
    }
    return { status: response.status, data, text, headers: response.headers };
  } catch (error) {
    return { status: 0, data: null, text: String(error && error.message ? error.message : error), headers: null };
  }
}

function detailOf(result, fallback) {
  const detail = result && result.data && result.data.detail;
  if (typeof detail === 'string' && detail.trim()) return detail.trim();
  if (result && result.status === 0) return `连不上 Python 服务（${API_BASE}）：${result.text}`;
  return fallback || `服务端返回 ${result ? result.status : '未知'} 错误`;
}

// ---------------------------------------------------------------- 会话 API

async function sessionStart(req, res) {
  let session = sessionOf(req);
  if (!session) {
    session = newSession();
    setSessionCookie(req, res, session);
  }
  if (session.apiKey) {
    return sendJson(res, 200, { authenticated: true, state: 'ready', me: session.me });
  }

  // 设备码只有 32^8 种，撞上了（409）就换一个再试。
  for (let attempt = 0; attempt < 6; attempt += 1) {
    const userCode = randomUserCode();
    const pollSecret = crypto.randomBytes(24).toString('base64url');
    const result = await apiCall(req, '/api/device/start', {
      method: 'POST',
      body: { user_code: userCode, poll_secret: pollSecret, name: '网页播放器' },
    });

    if (result.status === 200) {
      const expiresIn = Number(result.data && result.data.expires_in) || 600;
      session.pending = {
        user_code: normalizeUserCode(result.data && result.data.user_code) || userCode,
        poll_secret: pollSecret,
        expires_at: now() + expiresIn * 1000,
      };
      return sendJson(res, 200, {
        authenticated: false,
        state: 'pending',
        user_code: session.pending.user_code,
        expires_in: expiresIn,
        login_url: `/login?code=${encodeURIComponent(session.pending.user_code)}`,
      });
    }
    if (result.status === 409) continue;
    if (result.status === 429) {
      return sendJson(res, 429, { detail: detailOf(result, '请求太频繁，请稍后再试') });
    }
    return sendJson(res, 502, { detail: detailOf(result, '生成设备码失败') });
  }
  return sendJson(res, 500, { detail: '连续生成的设备码都被占用了，请稍后再试' });
}

async function sessionGet(req, res) {
  const session = sessionOf(req);
  if (!session) {
    return sendJson(res, 200, { authenticated: false, state: 'anonymous' });
  }

  if (session.apiKey) {
    const me = await apiCall(req, '/api/me', { key: session.apiKey });
    if (me.status === 200 && me.data) {
      session.me = me.data;
      return sendJson(res, 200, { authenticated: true, state: 'ready', me: me.data });
    }
    if (me.status === 401) {
      session.apiKey = null;
      session.me = null;
      return sendJson(res, 200, { authenticated: false, state: 'expired' });
    }
    // Python 暂时不可用：先让页面继续用缓存的账户信息，别把人踢出去
    if (session.me) {
      return sendJson(res, 200, { authenticated: true, state: 'ready', me: session.me, degraded: true });
    }
    return sendJson(res, 200, { authenticated: false, state: 'offline', detail: detailOf(me, '服务端暂时不可用') });
  }

  if (session.pending) {
    if (session.pending.expires_at <= now()) {
      session.pending = null;
      return sendJson(res, 200, { authenticated: false, state: 'expired' });
    }
    const result = await apiCall(req, '/api/device/status', {
      method: 'POST',
      body: { user_code: session.pending.user_code, poll_secret: session.pending.poll_secret },
    });
    if (result.status !== 200) {
      return sendJson(res, 200, {
        authenticated: false,
        state: 'pending',
        user_code: session.pending.user_code,
        expires_in: Math.max(0, Math.round((session.pending.expires_at - now()) / 1000)),
        detail: detailOf(result, '暂时查不到登录状态'),
      });
    }
    const status = result.data && result.data.status;
    if (status === 'approved' && result.data.api_key) {
      session.apiKey = result.data.api_key;
      session.pending = null;
      const me = await apiCall(req, '/api/me', { key: session.apiKey });
      if (me.status === 200 && me.data) session.me = me.data;
      return sendJson(res, 200, { authenticated: true, state: 'ready', me: session.me });
    }
    if (status === 'pending') {
      return sendJson(res, 200, {
        authenticated: false,
        state: 'pending',
        user_code: session.pending.user_code,
        expires_in: Math.max(0, Math.round((session.pending.expires_at - now()) / 1000)),
      });
    }
    session.pending = null;
    return sendJson(res, 200, { authenticated: false, state: status || 'expired' });
  }

  return sendJson(res, 200, { authenticated: false, state: 'anonymous' });
}

async function sessionManual(req, res) {
  let payload;
  try {
    payload = await readJson(req);
  } catch (error) {
    return sendJson(res, error.status || 400, { detail: error.message });
  }
  const apiKey = String(payload.api_key || '').trim();
  if (apiKey.length < 16) {
    return sendJson(res, 400, { detail: 'API Key 看起来不完整' });
  }
  const me = await apiCall(req, '/api/me', { key: apiKey });
  if (me.status === 401 || me.status === 403) {
    return sendJson(res, 401, { detail: '这个 API Key 无效或已被吊销' });
  }
  if (me.status !== 200 || !me.data) {
    return sendJson(res, 502, { detail: detailOf(me, '校验 API Key 失败') });
  }
  let session = sessionOf(req);
  if (!session) session = newSession();
  session.apiKey = apiKey;
  session.me = me.data;
  session.pending = null;
  setSessionCookie(req, res, session);
  return sendJson(res, 200, { authenticated: true, state: 'ready', me: me.data });
}

function sessionLogout(req, res) {
  const session = sessionOf(req);
  if (session) sessions.delete(session.id);
  clearSessionCookie(req, res);
  return sendJson(res, 200, { ok: true });
}

function sessionCancel(req, res) {
  const session = sessionOf(req);
  if (session) session.pending = null;
  return sendJson(res, 200, { ok: true });
}

// ---------------------------------------------------------------- 反代

function proxy(req, res) {
  let target;
  try {
    target = new URL(API_BASE + req.url);
  } catch {
    return sendErrorPage(res, 400, '请求地址不对', 'Node 无法解析这个请求地址。');
  }

  const headers = {};
  for (const [key, value] of Object.entries(req.headers)) {
    if (HOP_BY_HOP.has(key.toLowerCase())) continue;
    if (key.toLowerCase() === 'host') continue;
    headers[key] = value;
  }
  // 让 Python 拿到真实来源（限速、登录锁定都靠它）
  headers['x-forwarded-for'] = clientIp(req);
  headers['x-forwarded-proto'] = protoOf(req);
  // 音频 Range 不能被压缩中间层搅乱
  headers['accept-encoding'] = 'identity';

  const session = sessionOf(req);
  if (session && session.apiKey) {
    headers['x-api-key'] = session.apiKey;
  } else {
    delete headers['x-api-key'];
  }

  const upstream = http.request(
    {
      protocol: target.protocol,
      hostname: target.hostname,
      port: target.port || (target.protocol === 'https:' ? 443 : 80),
      path: target.pathname + target.search,
      method: req.method,
      headers,
    },
    (response) => {
      const out = {};
      for (const [key, value] of Object.entries(response.headers)) {
        if (HOP_BY_HOP.has(key.toLowerCase())) continue;
        out[key] = value;
      }
      res.writeHead(response.statusCode || 502, out);
      response.pipe(res);
      response.on('error', () => res.destroy());
    },
  );

  upstream.on('error', (error) => {
    if (res.headersSent) {
      res.destroy();
      return;
    }
    sendErrorPage(res, 502, '连不上音乐服务', `Node 已经起来了，但反代的 Python 服务没有响应：${error.message}`);
  });

  res.on('close', () => {
    if (!res.writableEnded) upstream.destroy();
  });

  req.pipe(upstream);
}

// ---------------------------------------------------------------- 下载入口

async function downloadLatest(req, res, platform, url) {
  if (!PLATFORMS.has(platform)) {
    return sendErrorPage(res, 404, '没有这个平台的安装包', `不认识 ${platform}，可选 windows / macos / linux。`);
  }
  const arch = String(url.searchParams.get('arch') || (platform === 'macos' ? 'arm64' : 'x64')).slice(0, 16);
  const result = await apiCall(
    req,
    `/api/update?platform=${encodeURIComponent(platform)}&arch=${encodeURIComponent(arch)}`,
  );
  if (result.status !== 200 || !result.data) {
    return sendErrorPage(res, 502, '检查更新失败', detailOf(result, '稍后再试。'));
  }
  const asset = result.data.asset || {};
  const target = String(asset.url || asset.direct_url || '');
  if (!target) {
    return sendErrorPage(res, 404, '还没有可下载的安装包', String(result.data.message || '稍后再试。'));
  }
  res.writeHead(302, { location: target, 'cache-control': 'no-store' });
  res.end();
}

// ---------------------------------------------------------------- 静态文件

function staticFileFor(pathname) {
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return null;
  }
  if (decoded.includes('\0')) return null;

  let relative;
  if (decoded === '/' || decoded === '/index.html') relative = 'index.html';
  else if (decoded === '/music' || decoded === '/music/' || decoded === '/music/index.html') relative = 'music/index.html';
  else if (decoded.startsWith('/music/')) relative = `music/${decoded.slice('/music/'.length)}`;
  else relative = decoded.replace(/^\/+/, '');

  const file = path.resolve(PUBLIC_DIR, relative);
  if (file !== PUBLIC_DIR && !file.startsWith(PUBLIC_DIR + path.sep)) return null;
  return file;
}

function serveStatic(req, res, pathname) {
  const file = staticFileFor(pathname);
  if (!file) return false;

  let stat;
  try {
    stat = fs.statSync(file);
  } catch {
    return false;
  }
  if (!stat.isFile()) return false;

  const ext = path.extname(file).toLowerCase();
  const isHtml = ext === '.html';
  const revalidate = isHtml || ext === '.css' || ext === '.js' || ext === '.mjs';
  res.writeHead(200, {
    'content-type': MIME[ext] || 'application/octet-stream',
    'content-length': String(stat.size),
    // HTML / CSS / JS 每次都回源校验：手工拷贝部署时不会出现「新 HTML + 旧 JS」
    'cache-control': revalidate ? 'no-cache' : 'public, max-age=300',
    'last-modified': stat.mtime.toUTCString(),
  });
  if (req.method === 'HEAD') {
    res.end();
    return true;
  }
  fs.createReadStream(file)
    .on('error', () => {
      if (!res.headersSent) sendErrorPage(res, 500, '读取文件失败', '静态文件在读取过程中出错了。');
      else res.destroy();
    })
    .pipe(res);
  return true;
}

// ---------------------------------------------------------------- 路由

function route(req, res) {
  const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);
  const pathname = url.pathname;
  const method = req.method || 'GET';

  // 会话：全部在 Node 这边，不经过 Python
  if (pathname === '/music/api/session') {
    if (method === 'GET') return sessionGet(req, res);
    if (method === 'POST') return sessionStart(req, res);
    if (method === 'DELETE') return sessionLogout(req, res);
    return sendJson(res, 405, { detail: '不支持这个请求方法' });
  }
  if (pathname === '/music/api/session/manual' && method === 'POST') return sessionManual(req, res);
  if (pathname === '/music/api/session/cancel' && method === 'POST') return sessionCancel(req, res);

  // 下载：查一次最新 Release，302 到安装包（和落地页按钮同源，换域名不用改）
  if (pathname.startsWith('/download/') && (method === 'GET' || method === 'HEAD')) {
    const platform = pathname.slice('/download/'.length).replace(/\/+$/, '').toLowerCase();
    return downloadLatest(req, res, platform, url);
  }
  if (pathname === '/download') {
    res.writeHead(302, { location: '/' });
    return res.end();
  }

  // 带 code 的 /login：直接替用户提交一次 lookup，省得再手输设备码
  if (pathname === '/login' && method === 'GET' && url.searchParams.get('code')) {
    const code = normalizeUserCode(url.searchParams.get('code'));
    if (!code) return proxy(req, res);
    const body = new URLSearchParams({ action: 'lookup', user_code: code }).toString();
    return (async () => {
      try {
        const response = await fetch(`${API_BASE}/login`, {
          method: 'POST',
          headers: {
            'content-type': 'application/x-www-form-urlencoded',
            'x-forwarded-for': clientIp(req),
            'x-forwarded-proto': protoOf(req),
          },
          body,
          signal: AbortSignal.timeout(20000),
        });
        const html = await response.text();
        sendText(res, response.status, html, 'text/html; charset=utf-8');
      } catch (error) {
        sendErrorPage(res, 502, '连不上登录页', `Python 服务没有响应：${error.message}`);
      }
    })();
  }

  // Python 的接口、登录页、探活
  if (pathname === '/login' || pathname === '/health' || pathname.startsWith('/api/')) {
    return proxy(req, res);
  }

  if (method === 'GET' || method === 'HEAD') {
    if (serveStatic(req, res, pathname)) return;
  }

  if (pathname === '/favicon.ico') {
    res.writeHead(204);
    return res.end();
  }

  return sendErrorPage(res, 404, '页面不存在', `没有找到 ${pathname}。`);
}

const server = http.createServer((req, res) => {
  const started = now();
  res.on('finish', () => {
    let pathname = req.url || '/';
    try {
      pathname = new URL(pathname, 'http://x').pathname;
    } catch {
      // ignore
    }
    console.log(`[web] ${req.method} ${pathname} ${res.statusCode} ${now() - started}ms`);
  });
  try {
    route(req, res);
  } catch (error) {
    console.error('[web] 未捕获的异常', error);
    if (!res.headersSent) sendErrorPage(res, 500, '服务器出错了', String(error && error.message ? error.message : error));
    else res.destroy();
  }
});

server.on('clientError', (error, socket) => {
  if (socket.writable) socket.end('HTTP/1.1 400 Bad Request\r\n\r\n');
});

// 定期清掉过期会话，避免长时间运行时 Map 越积越多
const sweeper = setInterval(() => {
  const deadline = now() - SESSION_TTL;
  for (const [id, session] of sessions) {
    if (session.last_seen < deadline) sessions.delete(id);
  }
}, 10 * 60 * 1000);
sweeper.unref();

function shutdown(signal) {
  console.log(`[web] 收到 ${signal}，正在退出…`);
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 3000).unref();
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));

server.listen(PORT, HOST, () => {
  console.log(`[web] Tonicuisc 网页服务已启动：http://${HOST}:${PORT}`);
  console.log(`[web] 落地页 /，网页播放器 /music`);
  console.log(`[web] 反代目标 ${API_BASE}`);
  if (HOST === '0.0.0.0' || HOST === '::') {
    console.log('[web] 提示：直接暴露到公网时，请在前面放 Caddy / Nginx 处理 HTTPS');
  }
});

module.exports = { server };
