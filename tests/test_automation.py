"""Offline tests for automation.py. The end-to-end test uses tests/fakeaws/aws (a simulated AWS CLI);
it proves program logic only, NOT your real AWS configuration."""
import argparse, fcntl, json, os, pathlib, subprocess, sys, tempfile, unittest
from unittest.mock import patch
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lite, automation


class Helpers(unittest.TestCase):
    def test_scrub_removes_private_identifiers(self):
        msg = 'User: arn:aws:iam::123456789012:user/x AKIAABCDEFGHIJKLMNOP on s3-bucket-fcc-project at ' + str(lite.HOME) + '/x'
        out = automation.scrub(msg)
        for bad in ('123456789012', 'AKIA', 's3-bucket-fcc-project', str(lite.HOME), 'arn:aws'):
            self.assertNotIn(bad, out)

    def test_public_guard_blocks_version_ids(self):
        with self.assertRaises(lite.LabError):
            automation.assert_public_safe(json.dumps({'VersionId': 'abc'}))
        with self.assertRaises(lite.LabError):
            automation.assert_public_safe('{"x": "123456789012"}')
        automation.assert_public_safe('{"ok": true}')

    def test_compare_reports_missing_changed_unexpected(self):
        m, c, u = automation.compare({'a': '1', 'b': '2'}, {'a': '1', 'b': '9', 'z': '3'})
        self.assertEqual((m, c, u), ([], ['b'], ['z']))
        m, c, u = automation.compare({'a': '1'}, {})
        self.assertEqual(m, ['a'])

    def test_cron_line(self):
        line = automation.cron_line(5)
        self.assertTrue(line.startswith('*/5 * * * * '))
        self.assertIn('run-backup --trigger scheduled', line)
        self.assertTrue(line.endswith(automation.CRON_MARK))
        self.assertTrue(automation.cron_line(60).startswith('0 * * * * '))
        with self.assertRaises(lite.LabError):
            automation.cron_line(7)

    def test_overlapping_run_is_skipped_and_recorded(self):
        with tempfile.TemporaryDirectory() as x:
            state = pathlib.Path(x)
            with patch.object(lite, 'STATE', state):
                state.mkdir(exist_ok=True)
                with open(state / 'run-backup.lock', 'a') as held:
                    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    args = argparse.Namespace(trigger='scheduled', select_clean=False, monitoring=False, no_publish=True)
                    self.assertEqual(automation.run_backup(args), 0)
                runs = automation.read_jsonl(state / 'runs.jsonl')
                self.assertEqual(runs[-1]['outcome'], 'skipped-overlap')

    def test_failure_never_hides_last_success(self):
        with tempfile.TemporaryDirectory() as x:
            state = pathlib.Path(x)
            with patch.object(lite, 'STATE', state):
                automation.append_jsonl(state / 'runs.jsonl', {'started_utc': '2026-10-08T10:00:00+00:00', 'finished_utc': '2026-10-08T10:00:01+00:00', 'trigger': 'scheduled', 'outcome': 'success', 'run_id': 'r1', 'archive_bytes': 10, 'file_count': 5, 'data_cutoff_utc': '2026-10-08T10:00:00+00:00', 'matches_clean_baseline': True})
                automation.append_jsonl(state / 'runs.jsonl', {'started_utc': '2026-10-08T10:05:00+00:00', 'finished_utc': '2026-10-08T10:05:01+00:00', 'trigger': 'scheduled', 'outcome': 'failed', 'error': 'network'})
                d = automation.build_dashboard_data()
                self.assertEqual(d['backup']['last_attempt_outcome'], 'failed')
                self.assertEqual(d['backup']['last_success_utc'], '2026-10-08T10:00:01+00:00')
                self.assertEqual(d['recovery']['status'], 'not-tested')
                self.assertFalse(d['checks']['cloud_recovery'])
                self.assertEqual(d['data_mode'], 'measured')


@unittest.skipIf(os.environ.get('SAFEVAULT_E2E_INNER') == '1', 'nested run')
class EndToEndSimulatedAWS(unittest.TestCase):
    def test_full_flow_with_simulated_aws(self):
        fake = ROOT / 'tests' / 'fakeaws' / 'aws'
        fake.chmod(fake.stat().st_mode | 0o111)  # executable bit is lost when copied through Windows
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as store:
            env = dict(os.environ, HOME=home, FAKEAWS_ROOT=store, SAFEVAULT_E2E_INNER='1',
                       PATH=str(ROOT / 'tests' / 'fakeaws') + os.pathsep + os.environ['PATH'],
                       SAFEVAULT_DASHBOARD_URL='file://' + store + '/public/dashboard-data.json')
            for k in lite.CREDENTIAL_ENV:
                env.pop(k, None)

            def run(*a, ok=True, stdin=''):
                p = subprocess.run([sys.executable, str(ROOT / 'lite.py'), *a], env=env, input=stdin, capture_output=True, text=True, timeout=120)
                if ok:
                    self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                return p
            run('local-demo'); run('policies'); run('configure-source')
            # login is required before operating SafeVault
            self.assertEqual(run('run-backup', ok=False).returncode, 3)
            run('create-user', '--username', 'arnav', stdin='Vault2026x\nVault2026x\n')
            self.assertEqual(run('cloud-restore', ok=False).returncode, 3)
            self.assertEqual(run('login', '--username', 'arnav', stdin='wrong-pass1\n', ok=False).returncode, 3)
            self.assertIn('LOGIN SUCCESSFUL', run('login', '--username', 'arnav', stdin='Vault2026x\n').stdout)
            self.assertIn('BACKUP SUCCEEDED', run('run-backup').stdout)
            run('run-backup', '--trigger', 'scheduled'); run('run-backup', '--trigger', 'scheduled')
            self.assertIn('PROTECTION VERIFIED', run('verify-protection').stdout)
            self.assertIn('RECOVERY PASSED from S3: 5/5', run('cloud-restore').stdout)
            out = run('ransomware-drill', '--yes').stdout
            self.assertIn('AccessDenied', out); self.assertIn('DRILL PASSED', out)
            # file categories + fetch by category from the S3 backup
            out = run('categories').stdout
            self.assertIn('5 files in 5 categories', out); self.assertIn('no type mismatches', out)
            self.assertIn('FETCH PASSED: 1/1 Records', run('fetch', '--category', 'records').stdout)
            self.assertIn('Web pages', run('fetch', '--list').stdout)
            self.assertIn('FETCH PASSED: 1/1 file', run('fetch', '--file', 'sample.bin').stdout)
            # a viewer may look but not operate
            run('create-user', '--username', 'priya', '--role', 'viewer', stdin='Look2026y\nLook2026y\n')
            run('logout'); run('login', '--username', 'priya', stdin='Look2026y\n')
            self.assertIn('Logged in as priya (viewer)', run('status').stdout)
            self.assertIn('Admin only', run('fetch', '--category', 'records', ok=False).stderr)
            run('logout'); run('login', '--username', 'arnav', stdin='Vault2026x\n')
            data = json.loads((pathlib.Path(store) / 'public' / 'dashboard-data.json').read_text())
            self.assertEqual(data['data_mode'], 'measured')
            self.assertGreaterEqual(data['backup']['scheduled_successes'], 2)
            self.assertTrue(all(data['checks'][k] for k in ('scheduled_runs', 'cloud_recovery', 'protection_verified', 'drill_passed', 'attacker_blocked')))
            raw = json.dumps(data)
            for bad in ('VersionId', '123456789012', 's3-bucket-fcc-project', home):
                self.assertNotIn(bad, raw)
            # a failed scheduled run is recorded as failed, and the last success is kept
            src = pathlib.Path(home) / 'safevault-lite-lab' / 'restored-good'
            src.rename(src.with_name('hidden'))
            p = run('run-backup', '--trigger', 'scheduled', ok=False)
            self.assertNotEqual(p.returncode, 0)
            src.with_name('hidden').rename(src)
            data = json.loads((pathlib.Path(store) / 'public' / 'dashboard-data.json').read_text())
            self.assertEqual(data['backup']['last_attempt_outcome'], 'failed')
            self.assertIsNotNone(data['backup']['last_success_utc'])


if __name__ == '__main__':
    unittest.main()
