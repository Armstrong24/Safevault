// GET /.netlify/functions/file?path=records/attendance.csv  (Authorization: Bearer <token>)
// Sends one file from the clean backup to the signed-in user as a download.
// Only files whose SHA-256 matched the clean baseline were published, and the bytes are
// checked against that SHA-256 again here before they are sent.
import { createHash } from 'node:crypto';
import { userFromRequest, json } from '../lib/auth.mjs';
import { loadData } from './data.mjs';

export default async (req) => {
  if (req.method !== 'GET') return json(405, { error: 'GET only' });
  let user;
  try { user = userFromRequest(req); } catch { return json(503, { error: 'Login is not configured on the server yet.' }); }
  if (!user) return json(401, { error: 'Please log in.' });
  const path = new URL(req.url).searchParams.get('path') || '';
  if (!path || path.length > 300 || path.includes('..') || path.startsWith('/')) return json(400, { error: 'Bad file path.' });
  const { data, error } = await loadData();
  if (error) return error;
  const info = (data.files || []).find(f => f.path === path);
  const b64 = (data.downloads || {})[path];
  if (!info || typeof b64 !== 'string') return json(404, { error: 'That file is not available for download.' });
  const bytes = Buffer.from(b64, 'base64');
  if (createHash('sha256').update(bytes).digest('hex') !== info.sha256) return json(409, { error: 'File failed its SHA-256 check; not sent.' });
  const name = (info.name || 'file').replace(/[^\w.\- ]/g, '_');
  return new Response(bytes, { status: 200, headers: {
    'Content-Type': 'application/octet-stream', 'Cache-Control': 'no-store',
    'Content-Disposition': 'attachment; filename="' + name + '"', 'X-SafeVault-SHA256': info.sha256 } });
};
