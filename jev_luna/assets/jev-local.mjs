// Keep native Jev payloads and policy; route only Decision calls to the local subscription service.
import { lstatSync, openSync, fstatSync, readSync, closeSync, constants } from 'node:fs';
import { homedir } from 'node:os';
import { isAbsolute, join } from 'node:path';

const endpoint = process.env.DECIDE_D_URL ?? 'http://127.0.0.1:20141/v1/systemone';
const target = new URL(endpoint);
if (target.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(target.hostname) ||
    !target.port || target.username || target.password || target.pathname !== '/v1/systemone' || target.search || target.hash) {
  throw new Error('DECIDE_D_URL must be this app\'s loopback /v1/systemone endpoint');
}
const base = process.env.JEV_LUNA_HOME?.trim() || (process.platform === 'darwin'
  ? join(homedir(), 'Library', 'Application Support', 'jev-luna')
  : process.platform === 'win32'
    ? join(process.env.APPDATA ?? join(homedir(), 'AppData', 'Roaming'), 'jev-luna')
    : join(process.env.XDG_CONFIG_HOME ?? join(homedir(), '.config'), 'jev-luna'));
if (!isAbsolute(base)) throw new Error('JEV_LUNA_HOME must be absolute');
function localKey() {
  const path = join(base, 'service.key');
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.size > 1024 || (process.platform !== 'win32' && (stat.mode & 0o077 || process.getuid && stat.uid !== process.getuid()))) {
    throw new Error('Local service.key must be a protected regular file owned by this user');
  }
  const fd = openSync(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) | (constants.O_NONBLOCK ?? 0));
  let key;
  try {
    const opened = fstatSync(fd);
    if (!opened.isFile() || opened.size > 1024 || opened.ino !== stat.ino || opened.dev !== stat.dev ||
        (process.platform !== 'win32' && (opened.mode & 0o077 || process.getuid && opened.uid !== process.getuid()))) {
      throw new Error('Local service credential changed while opening');
    }
    const bytes = Buffer.alloc(1025);
    let size = 0;
    while (size < bytes.length) {
      const count = readSync(fd, bytes, size, bytes.length - size, null);
      if (!count) break;
      size += count;
    }
    if (size > 1024) throw new Error('Local service credential exceeded its byte budget');
    key = bytes.subarray(0, size).toString('utf8').trim();
  } finally { closeSync(fd); }
  if (!/^jvl_local_[A-Za-z0-9_-]{32,}$/.test(key)) throw new Error('Invalid local service credential');
  return key;
}
// Keep one process-local transport adapter, even when a host loads the preload twice.
const installation = Symbol.for('jev-luna.local-decision-transport.v1');
const installed = globalThis[installation];
if (installed && (installed.endpoint !== endpoint || installed.base !== base || globalThis.fetch !== installed.fetch)) {
  throw new Error('Decision preload is already installed with a different configuration or fetch; start a fresh host process');
}
// Satisfy the original SDK's key-presence check without reading a native paid API key.
process.env.TYPESAFE_API_KEY = 'local-decide-d-no-credential';
const originalFetch = installed?.originalFetch ?? globalThis.fetch;
const remoteEndpoints = new Set([
  'https://api.typesafe.ai/v1/systemone',
  'https://openrouter.ai/api/v1/systemone',
  'https://ai-gateway.vercel.sh/typesafe/v1/systemone',
  target.href,
]);
async function boundedBody(request, signal) {
  signal.throwIfAborted();
  if (!request.body) return undefined;
  const reader = request.body.getReader();
  let stop;
  const abort = new Promise((_, reject) => {
    stop = () => { reader.cancel().catch(() => {}); reject(signal.reason); };
    signal.addEventListener('abort', stop, { once: true });
    if (signal.aborted) stop();
  });
  const chunks = []; let bytes = 0;
  try {
    while (true) {
      signal.throwIfAborted();
      const { done, value } = await Promise.race([reader.read(), abort]);
      if (done) break;
      bytes += value.byteLength;
      if (bytes > 64*1024*1024) throw new Error('Decision body exceeded its operational byte budget');
      chunks.push(value);
    }
    return Buffer.concat(chunks, bytes);
  } catch (error) {
    reader.cancel().catch(() => {});
    throw error;
  } finally {
    signal.removeEventListener('abort', stop);
    reader.releaseLock();
  }
}
if (!installed) {
  const localFetch = async (input, options = {}) => {
    const url = input instanceof Request ? input.url : String(input);
    let parsed;
    try { parsed = new URL(url); } catch { return originalFetch(input, options); }
    // OpenAI's native Decision-looking routes are outside this local-only product.
    const decisionPaths = ['/v1/systemone', '/v1/systemone/', '/api/v1/systemone', '/api/v1/systemone/',
                           '/typesafe/v1/systemone', '/typesafe/v1/systemone/', '/v1/decisions', '/v1/decisions/'];
    const scopedPath = decisionPaths.includes(parsed.pathname.toLowerCase());
    if (parsed.hostname === 'api.openai.com' && scopedPath) {
      throw new Error('Unknown Decision endpoint; refusing a remote credential or billing route');
    }
    // Only the declared upstream Decision routes belong to this adapter. A URL
    // that happens to contain /v1/decisions in another application is unrelated.
    if (!remoteEndpoints.has(parsed.href)) {
      const knownHost = [...remoteEndpoints].some(value => new URL(value).hostname === parsed.hostname);
      if (knownHost && scopedPath) {
        throw new Error('Unknown Decision endpoint; refusing a remote credential or billing route');
      }
      return originalFetch(input, options);
    }
    const started = performance.now();
    const request = new Request(input, options);
    const requested = Number(request.headers.get('x-decide-d-timeout-ms') ?? 120000);
    if (!Number.isFinite(requested) || requested <= 0) throw new Error('Decision timeout must be finite and positive');
    // The caller's AbortSignal remains authoritative, including the native selector's 15s limit.
    const timeout = Math.min(120000, requested);
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(Math.max(1, Math.floor(timeout)))]);
    const body = await boundedBody(request, signal);
    const key = localKey();
    signal.throwIfAborted();
    const remaining = Math.floor(timeout - (performance.now()-started));
    if (remaining <= 0) throw new DOMException('Decision deadline exceeded', 'TimeoutError');
    const headers = new Headers(request.headers);
    for (const name of ['authorization', 'proxy-authorization', 'content-length', 'host', 'cookie']) headers.delete(name);
    headers.set('authorization', 'Bearer '+key);
    headers.set('x-decide-d-timeout-ms', String(remaining));
    return originalFetch(endpoint, { method: request.method, headers, body, signal, redirect: 'error' });
  };
  globalThis.fetch = localFetch;
  Object.defineProperty(globalThis, installation, {
    value: Object.freeze({ endpoint, base, originalFetch, fetch: localFetch }),
    configurable: false, writable: false,
  });
}
