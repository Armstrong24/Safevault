// SafeVault dashboard login: shared helpers used by the login, data and chat Netlify Functions.
//
// Netlify environment variables (Project configuration -> Environment variables, scope Functions, secret):
//   DASHBOARD_USERS  = "arnav:pbkdf2_sha256$310000$<salt hex>$<hash hex>;member2:pbkdf2_sha256$..."
//                      made with: python3 scripts/make_dashboard_user.py
//   SESSION_SECRET   = long random string used to sign login tokens (the script prints one)
//
// Passwords are never stored: only a salted PBKDF2-HMAC-SHA256 hash. Login tokens are signed with
// HMAC-SHA256 and expire after SESSION_MINUTES. Everything runs server-side; the browser never sees
// the hashes or the secret.
import crypto from 'node:crypto';

export const SESSION_MINUTES = 60;
const MAX_FAILURES = 5;            // per user, then locked for LOCK_MINUTES
const LOCK_MINUTES = 5;
const failures = new Map();        // best effort: kept in memory of the running function instance

const b64url = buf => Buffer.from(buf).toString('base64url');

export function loadUsers(env = process.env.DASHBOARD_USERS || '') {
  const users = new Map();
  for (const part of env.split(/[;\n]/).map(x => x.trim()).filter(Boolean)) {
    const i = part.indexOf(':');
    if (i < 1) continue;
    const name = part.slice(0, i).trim().toLowerCase();
    const [algo, iter, salt, hash] = part.slice(i + 1).trim().split('$');
    if (algo === 'pbkdf2_sha256' && /^\d+$/.test(iter) && /^[0-9a-f]+$/.test(salt) && /^[0-9a-f]{64}$/.test(hash))
      users.set(name, { iterations: Number(iter), salt, hash });
  }
  return users;
}

export function hashPassword(password, saltHex, iterations) {
  return crypto.pbkdf2Sync(String(password), Buffer.from(saltHex, 'hex'), iterations, 32, 'sha256').toString('hex');
}

const DUMMY = { iterations: 310000, salt: '00'.repeat(16), hash: '0'.repeat(64) };

// Returns {ok:true} or {ok:false, error, status}
export function checkLogin(username, password, users = loadUsers(), now = Date.now()) {
  username = String(username || '').trim().toLowerCase();
  const f = failures.get(username);
  if (f && f.lockedUntil > now)
    return { ok: false, status: 429, error: 'Too many wrong passwords. Try again in a few minutes.' };
  const user = users.get(username);
  const rec = user || DUMMY;                       // same work for unknown users, so timing reveals nothing
  const got = hashPassword(password, rec.salt, rec.iterations);
  const ok = !!user && crypto.timingSafeEqual(Buffer.from(got, 'hex'), Buffer.from(rec.hash, 'hex'));
  if (ok) { failures.delete(username); return { ok: true, username }; }
  const n = (f && f.lockedUntil && f.lockedUntil <= now ? 0 : (f?.count || 0)) + 1;
  failures.set(username, n >= MAX_FAILURES ? { count: 0, lockedUntil: now + LOCK_MINUTES * 60000 } : { count: n, lockedUntil: 0 });
  return { ok: false, status: 401, error: 'Invalid username or password.' + (n >= MAX_FAILURES ? ' Account locked for ' + LOCK_MINUTES + ' minutes.' : '') };
}

function secret() {
  const s = process.env.SESSION_SECRET || '';
  if (s.length < 32) throw new Error('SESSION_SECRET missing or shorter than 32 characters');
  return s;
}

export function makeToken(username, now = Date.now()) {
  const payload = b64url(JSON.stringify({ u: username, exp: now + SESSION_MINUTES * 60000 }));
  const sig = b64url(crypto.createHmac('sha256', secret()).update(payload).digest());
  return { token: payload + '.' + sig, expires: new Date(now + SESSION_MINUTES * 60000).toISOString() };
}

// Returns the username for a valid, unexpired token, otherwise null.
export function verifyToken(token, now = Date.now()) {
  const [payload, sig] = String(token || '').split('.');
  if (!payload || !sig) return null;
  const want = b64url(crypto.createHmac('sha256', secret()).update(payload).digest());
  const a = Buffer.from(sig), b = Buffer.from(want);
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null;
  try {
    const data = JSON.parse(Buffer.from(payload, 'base64url').toString());
    if (typeof data.u !== 'string' || !(data.exp > now)) return null;
    if (!loadUsers().has(data.u)) return null;     // user removed from DASHBOARD_USERS -> token stops working
    return data.u;
  } catch { return null; }
}

export function userFromRequest(req) {
  const h = req.headers.get('authorization') || '';
  return h.startsWith('Bearer ') ? verifyToken(h.slice(7)) : null;
}

export function json(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
}
