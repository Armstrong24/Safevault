import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import safevault as sv

class Lab(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.src = self.root / 'source'; self.src.mkdir()
        (self.src / 'nested').mkdir(); (self.src / 'empty dir').mkdir()
        (self.src / 'nested' / 'file with spaces.txt').write_text('known good\n')
        (self.src / 'empty.txt').touch()
        self.c = sv.validate_config({'backend': 'local', 'source_directory': str(self.src),
                     'state_directory': str(self.root / 'state'), 'local_store': str(self.root / 'store'),
                     'prefix': 'backups/source-01'})
        self.cloud = sv.LocalStore(self.c)
        self.silence = contextlib.redirect_stdout(io.StringIO()); self.silence.__enter__()
    def tearDown(self):
        self.silence.__exit__(None, None, None)
        self.tmp.cleanup()
    def b(self): return sv.backup(self.c, self.cloud)
    def r(self, receipt, name='restored', selected=None):
        return sv.restore(self.c, receipt['record_key'], receipt['record_version_id'],
                          self.root / name, selected, self.cloud)
    def test_full_restore_and_empty_files_directories(self):
        receipt = self.b(); result = self.r(receipt)
        self.assertEqual((self.root / 'restored/nested/file with spaces.txt').read_text(), 'known good\n')
        self.assertTrue((self.root / 'restored/empty dir').is_dir())
        self.assertEqual((self.root / 'restored/empty.txt').stat().st_size, 0)
        self.assertEqual(result['validated_files'], 2)
    def test_single_file(self):
        receipt = self.b(); result = self.r(receipt, selected='nested/file with spaces.txt')
        self.assertEqual(result['restored_files'], 1)
        self.assertFalse((self.root / 'restored/empty.txt').exists())
    def test_corrupted_source_can_restore_older_run(self):
        clean = self.b(); (self.src / 'nested/file with spaces.txt').write_text('corrupted')
        bad = self.b(); self.assertNotEqual(clean['run_id'], bad['run_id'])
        self.r(clean)
        self.assertEqual((self.root / 'restored/nested/file with spaces.txt').read_text(), 'known good\n')
    def test_missing_requested_file(self):
        with self.assertRaises(sv.VaultError): self.r(self.b(), selected='absent.txt')
        self.assertFalse((self.root / 'restored').exists())
    def test_destination_not_overwritten(self):
        receipt = self.b(); (self.root / 'restored').mkdir(); (self.root / 'restored/keep').write_text('safe')
        with self.assertRaises(sv.VaultError): self.r(receipt)
        self.assertEqual((self.root / 'restored/keep').read_text(), 'safe')
    def test_overlap_destination_refused(self):
        receipt = self.b()
        with self.assertRaises(sv.VaultError):
            sv.restore(self.c, receipt['record_key'], receipt['record_version_id'], self.src / 'sub', cloud=self.cloud)
    def test_source_symlink_refused(self):
        (self.src / 'link').symlink_to(self.src / 'empty.txt')
        with self.assertRaises(sv.VaultError): self.b()
    def test_symlink_directory_refused(self):
        (self.src / 'link-dir').symlink_to(self.src / 'nested', target_is_directory=True)
        with self.assertRaises(sv.VaultError): self.b()
    def test_empty_source_refused(self):
        (self.src / 'empty.txt').unlink(); (self.src / 'nested/file with spaces.txt').unlink()
        with self.assertRaises(sv.VaultError): self.b()
    def test_size_bound(self):
        self.c['max_source_bytes'] = 1
        with self.assertRaises(sv.VaultError): self.b()
    def test_file_count_bound(self):
        self.c['max_files'] = 1
        with self.assertRaises(sv.VaultError): self.b()
    def test_overlap_config_refused(self):
        c = dict(self.c, state_directory=str(self.src / 'state'))
        with self.assertRaises(sv.VaultError): sv.validate_config(c)
    def test_explicit_version_required(self):
        receipt = self.b()
        with self.assertRaises(sv.VaultError):
            sv.restore(self.c, receipt['record_key'], 'null', self.root / 'out', cloud=self.cloud)
    def test_listing_records(self):
        self.b(); self.b()
        self.assertEqual(len(self.cloud.list_records()), 2)
    def test_archive_corruption_detected(self):
        receipt = self.b()
        blob = self.cloud._folder(receipt['archive_key']) / (receipt['archive_version_id'] + '.blob')
        blob.write_bytes(b'bad archive')
        with self.assertRaises(sv.VaultError): self.r(receipt)
        self.assertFalse((self.root / 'restored').exists())
    def malicious(self, receipt, member):
        path = self.root / 'hostile.tar.gz'
        with tarfile.open(path, 'w:gz') as tar:
            tar.addfile(member, io.BytesIO(b'x') if member.isfile() else None)
        uploaded = self.cloud.put(receipt['archive_key'], path)
        record_path = self.root / 'record.json'
        self.cloud.get(receipt['record_key'], receipt['record_version_id'], record_path)
        r = sv.load_json(record_path)
        r.update(archive_sha256=sv.digest(path), archive_bytes=path.stat().st_size,
                 archive_version_id=uploaded['VersionId'])
        sv.save_json(record_path, r)
        info = self.cloud.put(receipt['record_key'], record_path)
        return dict(receipt, record_version_id=info['VersionId'])
    def test_traversal_archive_refused_even_with_matching_archive_hash(self):
        m = tarfile.TarInfo('../escape'); m.size = 1
        receipt = self.malicious(self.b(), m)
        with self.assertRaises(sv.VaultError): self.r(receipt)
        self.assertFalse((self.root / 'escape').exists())
    def test_link_archive_refused_even_with_matching_archive_hash(self):
        m = tarfile.TarInfo('data/link'); m.type = tarfile.SYMTYPE; m.linkname = '/etc/passwd'
        receipt = self.malicious(self.b(), m)
        with self.assertRaises(sv.VaultError): self.r(receipt)
    def test_completion_upload_failure_not_published_as_success(self):
        class Failing(sv.LocalStore):
            metrics = []
            def put(self, key, path):
                if key.endswith('/complete.json'): raise sv.VaultError('injected completion failure')
                return super().put(key, path)
            def metric(self, values): self.metrics.append(values)
        cloud = Failing(self.c)
        with self.assertRaises(sv.VaultError): sv.backup(self.c, cloud)
        self.assertFalse(list((self.root / 'state/receipts').glob('*.json')))
        self.assertFalse(any('BackupSucceeded' in m for m in cloud.metrics))
    def test_lock_excludes_overlap(self):
        with sv.job_lock(self.c):
            with self.assertRaises(sv.VaultError): self.b()
    def test_safe_relative_bad_paths(self):
        for p in ['/etc/passwd', '../a', 'a/../b', 'a//b', 'a\\b', './a', '']:
            with self.subTest(path=p), self.assertRaises(sv.VaultError): sv.safe_relative(p)
    def test_aws_upload_flags_and_checksum(self):
        c = dict(self.c, backend='aws', bucket='safevault-test-example', region='ap-south-1')
        aws = sv.AWSStore(c); path = self.src / 'empty.txt'
        expected = sv.base64.b64encode(bytes.fromhex(sv.digest(path))).decode()
        with patch.object(aws, '_run', return_value={'VersionId': 'v1', 'ChecksumSHA256': expected}) as run:
            aws.put('backups/source-01/run/data.tar.gz', path)
            args = run.call_args[0][0]
            self.assertIn('--checksum-sha256', args); self.assertIn(expected, args)
            self.assertNotIn('--object-lock-mode', args)
    def test_aws_nonversioned_upload_refused(self):
        aws = sv.AWSStore(dict(self.c, backend='aws', bucket='safevault-test-example', region='ap-south-1'))
        with patch.object(aws, '_run', return_value={'VersionId': 'null'}):
            with self.assertRaises(sv.VaultError): aws.put('key', self.src / 'empty.txt')
    def test_aws_checksum_mismatch_refused(self):
        aws = sv.AWSStore(dict(self.c, backend='aws', bucket='safevault-test-example', region='ap-south-1'))
        with patch.object(aws, '_run', return_value={'VersionId': 'v1', 'ChecksumSHA256': 'bad'}):
            with self.assertRaises(sv.VaultError): aws.put('key', self.src / 'empty.txt')
    def test_aws_get_pins_version(self):
        aws = sv.AWSStore(dict(self.c, backend='aws', bucket='safevault-test-example', region='ap-south-1'))
        with patch.object(aws, '_run', return_value={}) as run:
            aws.get('key', 'trusted-version', self.root / 'download')
            args = run.call_args[0][0]
            self.assertEqual(args[args.index('--version-id') + 1], 'trusted-version')
    def test_local_download_limit(self):
        receipt = self.b()
        with self.assertRaises(sv.VaultError):
            self.cloud.get(receipt['archive_key'], receipt['archive_version_id'], self.root / 'big', max_bytes=1)
    def test_perfile_hash_mismatch_even_if_archive_hash_matches(self):
        receipt = self.b()
        original = self.cloud._folder(receipt['archive_key']) / (receipt['archive_version_id'] + '.blob')
        tampered = self.root / 'tampered.tar.gz'
        with tarfile.open(original, 'r:gz') as inp, tarfile.open(tampered, 'w:gz') as out:
            for member in inp.getmembers():
                if member.isfile():
                    data = inp.extractfile(member).read()
                    if member.name == 'data/nested/file with spaces.txt': data = b'x' * len(data)
                    out.addfile(member, io.BytesIO(data))
                else: out.addfile(member)
        uploaded = self.cloud.put(receipt['archive_key'], tampered)
        path = self.root / 'new-record.json'
        self.cloud.get(receipt['record_key'], receipt['record_version_id'], path)
        r = sv.load_json(path)
        r.update(archive_sha256=sv.digest(tampered), archive_bytes=tampered.stat().st_size,
                 archive_version_id=uploaded['VersionId'])
        sv.save_json(path, r); done = self.cloud.put(receipt['record_key'], path)
        with self.assertRaises(sv.VaultError): self.r(dict(receipt, record_version_id=done['VersionId']))
        self.assertFalse((self.root / 'restored').exists())
    def test_local_cannot_claim_retention(self):
        with self.assertRaises(sv.VaultError): sv.audit_retention(self.c, 'key', 'version')
    def test_aws_retention_audit(self):
        receipt = self.b()
        c = dict(self.c, backend='aws', bucket='safevault-test-example', region='ap-south-1')
        aws = sv.AWSStore(c)
        def download(key, version, dest, max_bytes=None):
            self.cloud.get(key, version, dest, max_bytes=max_bytes)
        with patch.object(aws, 'get', side_effect=download), patch.object(aws, '_run', return_value={
                'Retention': {'Mode': 'GOVERNANCE', 'RetainUntilDate': '2099-01-01T00:00:00+00:00'}}):
            result = sv.audit_retention(c, receipt['record_key'], receipt['record_version_id'], aws)
            self.assertEqual(len(result['checks']), 2)
    def test_aws_missing_retention_refused(self):
        receipt = self.b()
        c = dict(self.c, backend='aws', bucket='safevault-test-example', region='ap-south-1')
        aws = sv.AWSStore(c)
        def download(key, version, dest, max_bytes=None):
            self.cloud.get(key, version, dest, max_bytes=max_bytes)
        with patch.object(aws, 'get', side_effect=download), patch.object(aws, '_run', return_value={}):
            with self.assertRaises(sv.VaultError):
                sv.audit_retention(c, receipt['record_key'], receipt['record_version_id'], aws)
    def test_policy_generation_scoped(self):
        out = self.root / 'policies'; sv.write_policies('safevault-test-example', 'backups/source-01', out)
        policy = sv.load_json(out / 'uploader.json')
        allows = [s for s in policy['Statement'] if s['Effect'] == 'Allow']
        self.assertEqual(allows[0]['Action'], 's3:PutObject')
        self.assertEqual(allows[0]['Resource'], 'arn:aws:s3:::safevault-test-example/backups/source-01/*')
        self.assertEqual(sv.load_json(out / 'lifecycle.json')['Rules'][0]['Transitions'][0]['Days'], 30)

if __name__ == '__main__': unittest.main()
