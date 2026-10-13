import argparse, contextlib, io, json, os, pathlib, re, tempfile, unittest
from unittest.mock import patch
import sys
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import lite
class Lite(unittest.TestCase):
    def test_profile_environment_cleared_and_restored(self):
        with patch.dict(os.environ,{'AWS_ACCESS_KEY_ID':'dummy','AWS_SECRET_ACCESS_KEY':'dummy','AWS_ROLE_ARN':'dummy'}):
            with lite.credentials_from_profile_or_role():self.assertTrue(all(k not in os.environ for k in lite.CREDENTIAL_ENV))
            self.assertEqual(os.environ['AWS_ACCESS_KEY_ID'],'dummy')
    def test_admin_source_identity_refused(self):
        class Cloud:
            def _run(self,a):return {'Arn':'arn:aws:iam::123456789012:user/admin','Account':'123456789012'}
        with self.assertRaises(lite.LabError):lite.identity(Cloud(),'uploader')
    def test_expected_source_identity(self):
        class Cloud:
            def _run(self,a):return {'Arn':'arn:aws:iam::123456789012:user/safevault-lite-uploader','Account':'123456789012'}
        self.assertEqual(lite.identity(Cloud(),'uploader')['Account'],'123456789012')
    def test_wrong_recovery_role_refused(self):
        class Cloud:
            def _run(self,a):return {'Arn':'arn:aws:sts::123456789012:assumed-role/Admin/Session','Account':'123456789012'}
        with self.assertRaises(lite.LabError):lite.identity(Cloud(),'restore')
    def test_expected_role(self):
        class Cloud:
            def _run(self,a):return {'Arn':'arn:aws:sts::123456789012:assumed-role/SafeVaultLiteRestoreRole/instance','Account':'123456789012'}
        self.assertTrue(lite.identity(Cloud(),'restore'))
    def test_network_failure_not_counted_as_iam_denial(self):
        class Cloud:
            def _run(self,a):raise lite.sv.VaultError('timeout')
        with self.assertRaises(lite.LabError):lite.expected_denial(Cloud(),[])
    def test_access_denied_counted(self):
        class Cloud:
            def _run(self,a):raise lite.sv.VaultError('AWS operation failed: An error occurred (AccessDenied) when calling')
        self.assertTrue(lite.expected_denial(Cloud(),[]))
    def test_unexpected_permission_refused(self):
        class Cloud:
            def _run(self,a):return {}
        with self.assertRaises(lite.LabError):lite.expected_denial(Cloud(),[])
    def test_no_consent(self):
        with patch('builtins.input',return_value='NO'),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(lite.LabError):lite.confirm('test','YES')
    def test_embedded_json_escaped(self):
        html=(ROOT/'website/index.html').read_text();out=lite.embed_evidence(html,{'test':'</script><script>alert(1)</script>'})
        embedded=re.search(r'<script type="application/json" id="evidence">(.*?)</script>',out,re.S).group(1)
        self.assertNotIn('</script>',embedded);self.assertEqual(json.loads(embedded)['test'],'</script><script>alert(1)</script>')
    def test_missing_evidence_marker_refused(self):
        with self.assertRaises(lite.LabError):lite.embed_evidence('<html></html>',{})
    def test_command_dispatch_configuration(self):
        self.assertTrue(callable(lite.configure_source))
    def test_report_matching_allowlist_and_duplicates(self):
        with tempfile.TemporaryDirectory() as x:
            state=pathlib.Path(x)/'state';state.mkdir();receipt={'run_id':'r','record_version_id':'v','record_key':'key','file_count':5,'data_cutoff_utc':'2026-01-01T00:00:00+00:00','archive_bytes':1024}
            lite.save(state/'clean-receipt.json',receipt)
            report={'run_id':'r','record_version_id':'v','record_key':'key','trial_type':'full','baseline_matched':True,'csv_usable':True,'validated_files':5,'restore_seconds':2.5,'verified_utc':'2026-01-01T01:00:00+00:00','retention_audit':{'checks':[{},{}]},'destination':'private/path','account':'private-account'}
            path=pathlib.Path(x)/'report.json';lite.save(path,report)
            with patch.object(lite,'STATE',state):
                evidence=lite.sanitized_evidence([str(path)]);raw=json.dumps(evidence)
                self.assertNotIn('private/path',raw);self.assertNotIn('private-account',raw);self.assertNotIn('record_key',raw);self.assertEqual(len(evidence['recoveries']),1)
                with self.assertRaises(lite.LabError):lite.sanitized_evidence([str(path),str(path)])
                report['record_version_id']='different';lite.save(path,report)
                with self.assertRaises(lite.LabError):lite.sanitized_evidence([str(path)])
    def test_empty_dashboard_has_no_success(self):
        with tempfile.TemporaryDirectory() as x,patch.object(lite,'STATE',pathlib.Path(x)):
            evidence=lite.sanitized_evidence([]);self.assertEqual(evidence['kind'],'empty');self.assertEqual(evidence['recoveries'],[]);self.assertFalse(evidence['checks']['transition_observed'])

    def test_all_documented_commands_dispatch(self):
        for command in ['local-demo','policies','configure-source','backup','corrupt-demo','same-key-test','begin-recovery','handoff','recover','iam-test','lock-test','failure-test','cron-entry','record-checks','dashboard']:
            self.assertTrue(callable(getattr(lite,command.replace('-','_'))),command)
    def test_same_key_result_bound_to_selected_archive(self):
        with tempfile.TemporaryDirectory() as x:
            state=pathlib.Path(x);receipt={'run_id':'r','record_key':'k','record_version_id':'v','archive_version_id':'selected-archive','file_count':1,'data_cutoff_utc':'2026-01-01T00:00:00+00:00','archive_bytes':100}
            lite.save(state/'clean-receipt.json',receipt)
            lite.save(state/'same-key-test.json',{'original_archive_version_id':'another-archive','uploaded_utc':'2026-01-01T00:30:00+00:00'})
            report={'run_id':'r','record_key':'k','record_version_id':'v','trial_type':'full','baseline_matched':True,'csv_usable':True,'validated_files':1,'restore_seconds':1,'verified_utc':'2026-01-01T01:00:00+00:00','retention_audit':{'checks':[{},{}]}}
            path=state/'report.json';lite.save(path,report)
            with patch.object(lite,'STATE',state):
                self.assertFalse(lite.sanitized_evidence([str(path)])['checks']['same_key_history'])
                lite.save(state/'same-key-test.json',{'original_archive_version_id':'selected-archive','uploaded_utc':'2026-01-01T00:30:00+00:00'})
                self.assertTrue(lite.sanitized_evidence([str(path)])['checks']['same_key_history'])
