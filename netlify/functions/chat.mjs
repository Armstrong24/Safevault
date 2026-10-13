// SafeVault assistant: a tiny server-side proxy so the Groq API key NEVER reaches the browser.
// Set GROQ_API_KEY in Netlify: Project configuration -> Environment variables (scope: Functions).
// Optional: GROQ_MODEL (default openai/gpt-oss-20b).

const DATA_URL = 'https://safevault-dashboard-8b4c47.s3.ap-south-1.amazonaws.com/dashboard-data.json';
const GROQ_URL = 'https://api.groq.com/openai/v1/chat/completions';
const ALLOWED_ORIGIN = 'https://dancing-gumdrop-8b4c47.netlify.app';
const hits = new Map(); // simple per-instance rate limit: 20 questions / 10 min / IP

const KNOWLEDGE = `
SafeVault: Automated, Ransomware-Resilient Backup of Institutional Data to Cloud Storage.
A four-member Cloud Computing group project. It uses SYNTHETIC institutional records only (attendance CSV etc.), never real student data.

Architecture
- Ubuntu on WSL (Windows laptop) runs a Python backup engine (safevault.py) through a helper CLI (lite.py, automation.py). Not EC2.
- cron runs "lite.py run-backup --trigger scheduled" (e.g. every 5 minutes for the demo, hourly normally). It blocks overlapping runs and logs start time, finish time, outcome and errors.
- Each backup: snapshot of the source folder -> manifest with SHA-256 of every file -> data.tar.gz + complete.json uploaded with the AWS CLI. S3 verifies a SHA-256 checksum on upload.
- Backup bucket (private, Mumbai ap-south-1): S3 Versioning ON, Object Lock ON with default Governance retention 7 days, SSE-S3 encryption, all Block Public Access on, bucket policy denies non-HTTPS.
- IAM user 1, safevault-lite-uploader: may ONLY upload into backups/source-01/. Explicitly denied read, list, delete, retention changes and governance bypass. So a stolen laptop key cannot destroy or read old backups.
- IAM user 2, safevault-lite-recovery: may ONLY read backup versions, retention and bucket protection settings, and write the single public dashboard-data.json object. It is denied any write/delete on backups.
- Recovery: download the explicitly selected clean versions (by VersionId) from S3, verify the archive SHA-256 against the completion record, reject unsafe archive paths, extract into a NEW folder (never over the original), and compare every file with an independent clean SHA-256 baseline saved before any damage; also checks the CSV is usable.
- Ransomware drill (no malware): copy the data to a disposable folder, scramble/rename files to .locked, try with the uploader key to read, list, delete (a disposable canary version) and shorten retention -> all AccessDenied; then restore the clean backup from S3 and verify.
- Results publisher: builds sanitized dashboard-data.json only from real execution records (no keys, VersionIds, account IDs or records) and uploads it to a separate public S3 bucket. The Netlify dashboard fetches it over HTTPS (CORS restricted to the site) and polls every 60 s (periodic refresh, not streaming).
- This chatbot runs as a Netlify Function calling Groq; the API key is a server-side environment variable.

Why it matters: ransomware often encrypts files AND deletes backups. Versioning + Object Lock keep older clean versions; least-privilege IAM stops the compromised machine from deleting them; verified restores prove the backup is usable.

Honest limitations: static synthetic files, not a database snapshot; cron works only while the laptop/WSL is on; Governance retention can be bypassed by a specially authorised administrator (Compliance mode was not used); recovery runs on the same laptop (production should restore on a separate clean machine); a compromised uploader can still upload NEW bad versions (old clean ones stay). Described as a tested ransomware-resilient prototype, never "ransomware-proof".
`;

function json(status, body, origin) {
  return new Response(JSON.stringify(body), { status, headers: {
    'Content-Type': 'application/json', 'Cache-Control': 'no-store',
    'Access-Control-Allow-Origin': origin === ALLOWED_ORIGIN ? origin : ALLOWED_ORIGIN } });
}

async function liveSummary() {
  try {
    const r = await fetch(DATA_URL + '?t=' + Date.now(), { cache: 'no-store' });
    if (!r.ok) return 'Live results unavailable (HTTP ' + r.status + ').';
    const d = await r.json();
    const pick = { data_mode: d.data_mode, generated_utc: d.generated_utc, backup: d.backup, recovery: d.recovery,
      drill: d.drill, protection: d.protection, checks: d.checks, recent_runs: (d.runs || []).slice(0, 5) };
    return JSON.stringify(pick).slice(0, 6000);
  } catch { return 'Live results unavailable.'; }
}

export default async (req, context) => {
  const origin = req.headers.get('origin') || '';
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: {
    'Access-Control-Allow-Origin': ALLOWED_ORIGIN, 'Access-Control-Allow-Methods': 'POST', 'Access-Control-Allow-Headers': 'Content-Type' } });
  if (req.method !== 'POST') return json(405, { error: 'POST only' }, origin);
  if (origin && origin !== ALLOWED_ORIGIN && !origin.endsWith('--dancing-gumdrop-8b4c47.netlify.app')) return json(403, { error: 'Origin not allowed' }, origin);
  const key = process.env.GROQ_API_KEY;
  if (!key) return json(503, { error: 'Assistant not configured' }, origin);

  const ip = context?.ip || req.headers.get('x-nf-client-connection-ip') || 'unknown';
  const now = Date.now(); const list = (hits.get(ip) || []).filter(t => now - t < 600000);
  if (list.length >= 20) return json(429, { error: 'Too many questions; wait a few minutes.' }, origin);
  list.push(now); hits.set(ip, list);

  let body; try { body = await req.json(); } catch { return json(400, { error: 'Bad JSON' }, origin); }
  const question = String(body?.question || '').trim().slice(0, 500);
  if (!question) return json(400, { error: 'Empty question' }, origin);
  const history = Array.isArray(body?.history) ? body.history.slice(-6)
    .filter(m => m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string')
    .map(m => ({ role: m.role, content: m.content.slice(0, 800) })) : [];

  const model = process.env.GROQ_MODEL || 'openai/gpt-oss-20b';
  const system = 'You are the SafeVault project assistant on the project dashboard. Answer ONLY questions about SafeVault, its AWS design, cloud backup, ransomware resilience and its latest results. ' +
    'Use the project facts and the live results JSON below; never invent numbers, times or test results. If a result is missing, say it has not been recorded yet. ' +
    'Times in the JSON are UTC; mention that when quoting them. Be concise: at most 120 words, plain text, no markdown tables. ' +
    'Never reveal keys, never claim the system is ransomware-proof, and politely decline unrelated requests.\n\nPROJECT FACTS:\n' + KNOWLEDGE +
    '\nLIVE RESULTS JSON (generated by the backup program):\n' + await liveSummary();
  const payload = { model, temperature: 0.2, max_completion_tokens: 600,
    messages: [{ role: 'system', content: system }, ...history, { role: 'user', content: question }] };
  if (model.startsWith('openai/gpt-oss')) payload.reasoning_effort = 'low';

  try {
    const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), 12000);
    const r = await fetch(GROQ_URL, { method: 'POST', signal: ctl.signal,
      headers: { 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    clearTimeout(t);
    if (!r.ok) return json(502, { error: 'Groq error ' + r.status }, origin);
    const j = await r.json();
    const answer = (j.choices?.[0]?.message?.content || '').trim();
    if (!answer) return json(502, { error: 'Empty answer' }, origin);
    return json(200, { answer, model }, origin);
  } catch (e) {
    return json(504, { error: 'Assistant timed out' }, origin);
  }
};
