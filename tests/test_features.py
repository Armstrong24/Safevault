"""Offline tests for the file catalog (catalog.py)."""
import datetime as dt, json, os, pathlib, sys, tempfile, unittest
from unittest.mock import patch
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import catalog, safevault as sv


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


if __name__ == '__main__':
    unittest.main()
