"""Dashboard login: the Python hash tool and the Node (Netlify Function) checker must agree.
Runs the Node checks only if Node.js 18+ is installed; otherwise the test is skipped."""
import pathlib, shutil, subprocess, sys, unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import make_dashboard_user as mdu


class DashboardLogin(unittest.TestCase):
    def test_entry_format_and_salt(self):
        a, b = mdu.entry('arnav', 'Vault2026x'), mdu.entry('arnav', 'Vault2026x')
        self.assertTrue(a.startswith('arnav:pbkdf2_sha256$310000$'))
        self.assertNotEqual(a, b)                      # random salt every time
        self.assertNotIn('Vault2026x', a)

    @unittest.skipUnless(shutil.which('node'), 'Node.js not installed')
    def test_netlify_functions(self):
        line = mdu.entry('arnav', 'Vault2026x') + ';' + mdu.entry('priya', 'Look2026y')
        p = subprocess.run(['node', str(ROOT / 'tests' / 'dashboard_login_check.mjs'), line], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('DASHBOARD LOGIN CHECKS PASSED', p.stdout)


if __name__ == '__main__':
    unittest.main()
