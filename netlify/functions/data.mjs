// GET /.netlify/functions/data  (Authorization: Bearer <token>)  ->  the sanitized dashboard-data.json
// The dashboard reads its results ONLY through this function, so results are shown only after login.
// File contents (the "downloads" part) are removed here; they are served one file at a time by the
// "file" function, so the page stays small when it refreshes every 60 seconds.
import { userFromRequest, json } from '../lib/auth.mjs';

const DATA_URL = 'https://safevault-dashboard-8b4c47.s3.ap-south-1.amazonaws.com/dashboard-data.json';

export async function loadData() {
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), 10000);
  try {
    const r = await fetch(DATA_URL + '?t=' + Date.now(), { cache: 'no-store', signal: ctl.signal });
    if (!r.ok) return { error: json(502, { error: 'S3 returned HTTP ' + r.status }) };
    const text = await r.text();
    if (text.length > 1048576) return { error: json(502, { error: 'Data too large' }) };
    return { data: JSON.parse(text) };
  } catch { return { error: json(504, { error: 'Could not reach S3' }) }; }
  finally { clearTimeout(t); }
}

export default async (req) => {
  if (req.method !== 'GET') return json(405, { error: 'GET only' });
  let user;
  try { user = userFromRequest(req); } catch { return json(503, { error: 'Login is not configured on the server yet.' }); }
  if (!user) return json(401, { error: 'Please log in.' });
  const { data, error } = await loadData();
  if (error) return error;
  delete data.downloads;
  return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
};
