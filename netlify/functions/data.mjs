// GET /.netlify/functions/data  (Authorization: Bearer <token>)  ->  the sanitized dashboard-data.json
// The dashboard reads its results ONLY through this function, so results are shown only after login.
import { userFromRequest, json } from '../lib/auth.mjs';

const DATA_URL = 'https://safevault-dashboard-8b4c47.s3.ap-south-1.amazonaws.com/dashboard-data.json';

export default async (req) => {
  if (req.method !== 'GET') return json(405, { error: 'GET only' });
  let user;
  try { user = userFromRequest(req); } catch { return json(503, { error: 'Login is not configured on the server yet.' }); }
  if (!user) return json(401, { error: 'Please log in.' });
  try {
    const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), 10000);
    const r = await fetch(DATA_URL + '?t=' + Date.now(), { cache: 'no-store', signal: ctl.signal });
    clearTimeout(t);
    if (!r.ok) return json(502, { error: 'S3 returned HTTP ' + r.status });
    const text = await r.text();
    if (text.length > 1048576) return json(502, { error: 'Data too large' });
    return new Response(text, { status: 200, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
  } catch { return json(504, { error: 'Could not reach S3' }); }
};
