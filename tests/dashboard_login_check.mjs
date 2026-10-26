// Offline check of the dashboard login functions (run by tests/test_dashboard_login.py).
import assert from 'node:assert/strict';
const [, , usersLine] = process.argv;
process.env.DASHBOARD_USERS = usersLine;
process.env.SESSION_SECRET = 'x'.repeat(48);
const auth = await import('../netlify/lib/auth.mjs');
const login = (await import('../netlify/functions/login.mjs')).default;
const data = (await import('../netlify/functions/data.mjs')).default;
const post = (b, ip='1.1.1.1') => login(new Request('https://x/login', { method: 'POST', body: JSON.stringify(b), headers: { 'content-type': 'application/json' } }), { ip });

// hash made by the Python script is accepted by the Node function
let r = await post({ username: 'arnav', password: 'Vault2026x' });
assert.equal(r.status, 200); const { token, expires } = await r.json();
assert.ok(token && new Date(expires) > new Date());
assert.equal(auth.verifyToken(token), 'arnav');
// wrong password / unknown user give the same message
r = await post({ username: 'arnav', password: 'wrong1234' }, '2.2.2.2'); assert.equal(r.status, 401);
const m1 = (await r.json()).error;
r = await post({ username: 'nobody', password: 'wrong1234' }, '2.2.2.2'); assert.equal((await r.json()).error, m1);
// tampered and expired tokens are refused
const [p, s] = token.split('.');
const forged = Buffer.from(JSON.stringify({ u: 'arnav', exp: Date.now() + 1e9 })).toString('base64url') + '.' + s;
assert.equal(auth.verifyToken(forged), null);
assert.equal(auth.verifyToken(token, Date.now() + 61 * 60000), null);
// data endpoint needs a token
r = await data(new Request('https://x/data')); assert.equal(r.status, 401);
const file = (await import('../netlify/functions/file.mjs')).default;
r = await file(new Request('https://x/file?path=records/attendance.csv')); assert.equal(r.status, 401);
r = await file(new Request('https://x/file?path=../etc/passwd', { headers: { authorization: 'Bearer ' + token } })); assert.equal(r.status, 400);
// lockout after 5 wrong passwords
for (let i = 0; i < 5; i++) await post({ username: 'arnav', password: 'bad' + i + 'xxxxx' }, '3.3.3.' + i);
r = await post({ username: 'arnav', password: 'Vault2026x' }, '4.4.4.4'); assert.equal(r.status, 429);
// no password or hash ever appears in the token
assert.ok(!Buffer.from(p, 'base64url').toString().includes('Vault'));
console.log('DASHBOARD LOGIN CHECKS PASSED');
