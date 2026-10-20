"""Offline tests for the file catalog (catalog.py) and the user login (access.py)."""
import datetime as dt, json, os, pathlib, sys, tempfile, unittest
from unittest.mock import patch
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import access, catalog, safevault as sv


class Catalog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, data):
        p = self.root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data); return p

    def test_demo_files_get_five_categories(self):
        self.put('records/attendance.csv', b'student_id,present\nDEMO001,yes\n')
        self.put('website/index.html', b'<h1>x</h1>\n')
        self.put('file with spaces.txt', b'Synthetic project data only.\n')
        self.put('empty-file.txt', b'')
        self.put('sample.bin', os.urandom(4096))
        got = {f['path']: f['category'] for f in catalog.scan(self.root)}
        self.assertEqual(got, {'records/attendance.csv': 'records', 'website/index.html': 'web',
                               'file with spaces.txt': 'text', 'empty-file.txt': 'empty', 'sample.bin': 'binary'})

    def test_magic_bytes_beat_wrong_extension(self):
        r = catalog.classify(self.put('photo.pdf', b'\x89PNG\r\n\x1a\n' + b'0' * 50))
        self.assertEqual(r['category'], 'images'); self.assertIn('mismatch', r['warning'])
        self.assertEqual(catalog.classify(self.put('report.pdf', b'%PDF-1.7 ...'))['category'], 'documents')
        self.assertIsNone(catalog.classify(self.put('report.docx', b'PK\x03\x04rest'))['warning'])

    def test_encrypted_csv_is_flagged(self):
        r = catalog.classify(self.put('marks.csv', bytes(range(256)) * 4))
        self.assertIn('not readable text', r['warning'])

    def test_ransomware_extension_is_suspicious(self):
        self.assertEqual(catalog.classify(self.put('attendance.csv.locked', b'x'))['category'], 'suspicious')

    def test_manifest_records_category_and_category_restore(self):
        src = self.root / 'src'; src.mkdir()
        (src / 'a.csv').write_text('id\n1\n'); (src / 'b.txt').write_text('hello\n'); (src / 'c.csv').write_text('id\n2\n')
        c = sv.validate_config({'backend': 'local', 'source_directory': str(src), 'state_directory': str(self.root / 'state'),
                                'local_store': str(self.root / 'store'), 'prefix': 'backups/source-01', 'monitoring': False})
        r = sv.backup(c)
        out = sv.restore(c, r['record_key'], r['record_version_id'], self.root / 'out', selected=lambda e: e['category'] == 'records')
        self.assertEqual(out['restored_files'], 2); self.assertEqual(out['validated_files'], 3)
        self.assertEqual(sorted(p.name for p in (self.root / 'out').iterdir()), ['a.csv', 'c.csv'])
        self.assertEqual({e['path']: e['category'] for e in out['catalog']}, {'a.csv': 'records', 'b.txt': 'text', 'c.csv': 'records'})


class Login(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(access, '_home', lambda: pathlib.Path(self.tmp.name)); self.patch.start()

    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup()

    def test_password_stored_only_as_salted_hash(self):
        access.create_user('arnav', 'Vault2026x', 'admin')
        raw = access.users_file().read_text()
        self.assertNotIn('Vault2026x', raw)
        u = json.loads(raw)['users']['arnav']
        self.assertEqual(u['password']['algorithm'], 'pbkdf2_sha256'); self.assertEqual(len(u['password']['salt']), 32)
        self.assertNotEqual(access.hash_password('same1234')['hash'], access.hash_password('same1234')['hash'])

    def test_first_user_must_be_admin_and_weak_passwords_refused(self):
        with self.assertRaises(access.AccessError): access.create_user('priya', 'Look2026y', 'viewer')
        for bad in ('short1', 'onlyletters', '12345678', 'arnav2026x'):
            with self.assertRaises(access.AccessError): access.create_user('arnav', bad, 'admin')

    def test_login_logout_and_session(self):
        access.create_user('arnav', 'Vault2026x', 'admin')
        with self.assertRaises(access.AccessError): access.login('arnav', 'wrong123x')
        with self.assertRaises(access.AccessError): access.login('nobody', 'Vault2026x')
        access.login('arnav', 'Vault2026x')
        self.assertEqual(access.current()['role'], 'admin')
        access.logout(); self.assertIsNone(access.current())

    def test_lockout_after_three_failures(self):
        access.create_user('arnav', 'Vault2026x', 'admin')
        for _ in range(3):
            with self.assertRaises(access.AccessError): access.login('arnav', 'wrong123x')
        with self.assertRaises(access.AccessError) as e: access.login('arnav', 'Vault2026x')
        self.assertIn('locked', str(e.exception))

    def test_tampered_or_expired_session_refused(self):
        access.create_user('arnav', 'Vault2026x', 'admin'); access.login('arnav', 'Vault2026x')
        s = json.loads(access.session_file().read_text())
        s['token'] = '0' * 64; access.session_file().write_text(json.dumps(s))
        self.assertIsNone(access.current())
        access.login('arnav', 'Vault2026x')
        s = json.loads(access.session_file().read_text())
        s['expires_utc'] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).isoformat()
        access.session_file().write_text(json.dumps(s))
        self.assertIsNone(access.current())

    def test_roles(self):
        access.create_user('arnav', 'Vault2026x', 'admin'); access.create_user('priya', 'Look2026y', 'viewer')
        access.login('priya', 'Look2026y')
        self.assertEqual(access.require('status', None)['username'], 'priya')
        with self.assertRaises(access.AccessError): access.require('cloud-restore', None)
        self.assertIsNone(access.require('local-demo', None))


if __name__ == '__main__':
    unittest.main()
