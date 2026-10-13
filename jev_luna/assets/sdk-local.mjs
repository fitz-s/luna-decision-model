// Explicit SDK factories. No fetch interception and no global environment mutation.
import { lstatSync, openSync, fstatSync, readSync, closeSync, constants } from 'node:fs';
import { homedir } from 'node:os';
import { isAbsolute, join } from 'node:path';

function localKey() {
  const home = process.env.JEV_LUNA_HOME?.trim() || (process.platform === 'darwin'
    ? join(homedir(), 'Library', 'Application Support', 'jev-luna')
    : process.platform === 'win32' ? join(process.env.APPDATA ?? join(homedir(), 'AppData', 'Roaming'), 'jev-luna')
      : join(process.env.XDG_CONFIG_HOME ?? join(homedir(), '.config'), 'jev-luna'));
  if (!isAbsolute(home)) throw new Error('JEV_LUNA_HOME must be absolute');
  const path = join(home, 'service.key'); const before = lstatSync(path);
  if (!before.isFile()) throw new Error('Local service.key must not be a symlink');
  const fd = openSync(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) | (constants.O_NONBLOCK ?? 0));
  try {
    const info = fstatSync(fd);
    if (!info.isFile() || info.ino !== before.ino || info.dev !== before.dev || info.size > 1024 || (process.platform !== 'win32' &&
        (info.mode & 0o077 || process.getuid && info.uid !== process.getuid()))) {
      throw new Error('Local service.key must be a protected regular file owned by this user');
    }
    const bytes = Buffer.alloc(1025); let size = 0;
    while (size < bytes.length) { const count = readSync(fd, bytes, size, bytes.length - size, null); if (!count) break; size += count; }
    const key = bytes.subarray(0, size).toString('utf8').trim();
    if (size > 1024 || !/^jvl_local_[A-Za-z0-9_-]{32,}$/.test(key)) throw new Error('Invalid local service credential');
    return key;
  } finally { closeSync(fd); }
}

function client(SDK, options, openai) {
  const { baseURL = openai ? 'http://127.0.0.1:20141/v1' : 'http://127.0.0.1:20141', ...rest } = options;
  if (['apiKey', 'adminAPIKey', 'provider', 'workloadIdentity', 'dataResidency', 'x509Transport', 'fetch'].some(key => key in rest)) {
    throw new Error('Local SDK factories own authentication and transport; retain caller policy options only');
  }
  const url = new URL(baseURL);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || !url.port ||
      url.username || url.password || url.search || url.hash || !
      (openai ? ['/v1', '/v1/'] : ['', '/']).includes(url.pathname)) {
    throw new Error('Use the explicit loopback SDK baseURL (OpenAI /v1; TypeSafe root)');
  }
  return new SDK({ ...rest, baseURL: baseURL.replace(/\/$/, ''), apiKey: localKey() });
}

export function openaiClient(OpenAI, options = {}) { return client(OpenAI, options, true); }
export function typesafeClient(TypeSafeClient, options = {}) { return client(TypeSafeClient, options, false); }
