// POST /.netlify/functions/login  {username, password}  ->  {token, username, expires}
// The password is checked server-side against the PBKDF2 hash in DASHBOARD_USERS.
import { checkLogin, makeToken, loadUsers, json } from '../lib/auth.mjs';

const hits = new Map(); // per-instance: at most 10 login attempts per IP per 10 minutes

export default async (req, context) => {
  if (req.method !== 'POST') return json(405, { error: 'POST only' });
  const users = loadUsers();
  if (!users.size || !process.env.SESSION_SECRET) return json(503, { error: 'Login is not configured on the server yet.' });
  const ip = context?.ip || req.headers.get('x-nf-client-connection-ip') || 'unknown';
  const now = Date.now(); const list = (hits.get(ip) || []).filter(t => now - t < 600000);
  if (list.length >= 10) return json(429, { error: 'Too many login attempts. Wait a few minutes.' });
  list.push(now); hits.set(ip, list);
  let body; try { body = await req.json(); } catch { return json(400, { error: 'Bad request' }); }
  const username = String(body?.username || '').slice(0, 64), password = String(body?.password || '').slice(0, 256);
  if (!username || !password) return json(400, { error: 'Enter username and password.' });
  const r = checkLogin(username, password, users);
  if (!r.ok) {
    console.log(JSON.stringify({ event: 'login_failed', user: username.toLowerCase(), utc: new Date().toISOString() }));
    return json(r.status, { error: r.error });
  }
  console.log(JSON.stringify({ event: 'login_success', user: r.username, utc: new Date().toISOString() }));
  return json(200, { ...makeToken(r.username), username: r.username });
};
