#!/usr/bin/env python3
"""SafeVault user login (username + password) and role-based access to the commands.

How it works
- Passwords are NEVER stored. We store a salted PBKDF2-HMAC-SHA256 hash (310,000 iterations,
  16-byte random salt per user) in ~/.config/safevault-lite/users.json (permissions 600).
- `login` compares hashes in constant time (hmac.compare_digest). 3 wrong passwords in a row
  lock the account for 5 minutes. Every login attempt is written to an audit log (no passwords).
- A successful login creates a session that expires after 30 minutes. The session file holds a
  random token; users.json holds only the token's SHA-256, so a copied/edited session file is refused.
- Roles: `admin` can run every SafeVault command. `viewer` can only look (status, categories, whoami).
- The very first account must be an admin and can be created without logging in; after that only a
  logged-in admin can add users.
- cron's unattended `run-backup --trigger scheduled` has no keyboard, so it runs without a session
  (an admin installs the schedule while logged in).

Scope (be honest in the viva): this login protects who may operate the SafeVault tool on this laptop.
Access to AWS itself is still enforced by IAM (separate uploader / recovery keys); the login does not
replace IAM, it adds an application-level layer in front of it.
"""
import datetime as dt
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys

ITERATIONS = 310_000
MAX_FAILURES = 3
LOCK_MINUTES = 5
SESSION_MINUTES = 30
ROLES = ('admin', 'viewer')

# command -> roles allowed. Commands not listed here (setup helpers) need no login.
ADMIN = ('admin',)
ANY = ('admin', 'viewer')
PROTECTED = {
    'run-backup': ADMIN, 'cloud-restore': ADMIN, 'verify-protection': ADMIN, 'ransomware-drill': ADMIN,
    'fetch': ADMIN, 'schedule': ADMIN, 'publish': ADMIN, 'users': ADMIN,
    'status': ANY, 'categories': ANY,
}


class AccessError(Exception):
    pass


def _home():
    return Path.home()


def users_file():
    return _home() / '.config' / 'safevault-lite' / 'users.json'


def session_file():
    return _home() / '.local' / 'state' / 'safevault-lite' / 'session.json'


def audit_file():
    return _home() / '.local' / 'state' / 'safevault-lite' / 'auth-log.jsonl'


def now():
    return dt.datetime.now(dt.timezone.utc)


def _load(path, default):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def audit(event, username, **fields):
    path = audit_file()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path, 'a') as f:
        f.write(json.dumps({'utc': now().isoformat(), 'event': event, 'username': username, **fields}) + '\n')
    os.chmod(path, 0o600)


# ---------------------------------------------------------------- password hashing
def hash_password(password, salt=None, iterations=ITERATIONS):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return {'algorithm': 'pbkdf2_sha256', 'iterations': iterations, 'salt': salt.hex(), 'hash': dk.hex()}


def verify_password(password, record):
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(record['salt']), record['iterations'])
    return hmac.compare_digest(dk.hex(), record['hash'])


def check_strength(username, password):
    if len(password) < 8:
        raise AccessError('Password must be at least 8 characters.')
    if not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        raise AccessError('Password must contain at least one letter and one number.')
    if username.lower() in password.lower():
        raise AccessError('Password must not contain the username.')


def valid_username(name):
    if not re.fullmatch(r'[a-z][a-z0-9_.-]{2,31}', name or ''):
        raise AccessError('Username: 3-32 characters, lowercase letters, digits, . _ - and starting with a letter.')
    return name


def ask_secret(prompt):
    # getpass hides typing on a real terminal; tests pipe the password through stdin instead.
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    print(prompt, end='', flush=True)
    line = sys.stdin.readline()
    print()
    return line.rstrip('\n')


def ask_username(given):
    return valid_username((given or input('Username: ')).strip().lower())


# ---------------------------------------------------------------- user store
def users():
    return _load(users_file(), {'schema': 1, 'users': {}})


def create_user(username, password, role):
    data = users()
    username = valid_username(username)
    if role not in ROLES:
        raise AccessError('Role must be admin or viewer.')
    if username in data['users']:
        raise AccessError('User already exists: ' + username)
    if not data['users'] and role != 'admin':
        raise AccessError('The first account must be an admin.')
    check_strength(username, password)
    data['users'][username] = {'role': role, 'password': hash_password(password), 'created_utc': now().isoformat(),
                               'failed_attempts': 0, 'locked_until': None, 'session_token_sha256': None}
    _save(users_file(), data)
    audit('user_created', username, role=role)


def login(username, password):
    data = users()
    user = data['users'].get(username)
    if user is None:
        audit('login_failed', username, reason='unknown user')
        raise AccessError('Invalid username or password.')  # same message: do not reveal which part was wrong
    if user.get('locked_until') and dt.datetime.fromisoformat(user['locked_until']) > now():
        audit('login_refused', username, reason='locked')
        raise AccessError('Account locked after ' + str(MAX_FAILURES) + ' wrong passwords. Try again after ' +
                          dt.datetime.fromisoformat(user['locked_until']).astimezone().strftime('%H:%M:%S') + '.')
    if not verify_password(password, user['password']):
        user['failed_attempts'] = user.get('failed_attempts', 0) + 1
        locked = user['failed_attempts'] >= MAX_FAILURES
        if locked:
            user['locked_until'] = (now() + dt.timedelta(minutes=LOCK_MINUTES)).isoformat()
            user['failed_attempts'] = 0
        _save(users_file(), data)
        audit('login_failed', username, reason='wrong password', locked=locked)
        raise AccessError('Invalid username or password.' + (' Account locked for ' + str(LOCK_MINUTES) + ' minutes.' if locked else ''))
    token = secrets.token_hex(32)
    expires = now() + dt.timedelta(minutes=SESSION_MINUTES)
    user.update(failed_attempts=0, locked_until=None, session_token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                last_login_utc=now().isoformat())
    _save(users_file(), data)
    _save(session_file(), {'username': username, 'token': token, 'expires_utc': expires.isoformat()})
    audit('login_success', username, role=user['role'])
    return {'username': username, 'role': user['role'], 'expires_utc': expires.isoformat()}


def current():
    """The logged-in user, or None. Expired, edited or copied sessions are refused."""
    s = _load(session_file(), None)
    if not s:
        return None
    user = users()['users'].get(s.get('username'))
    if not user or not user.get('session_token_sha256'):
        return None
    if not hmac.compare_digest(hashlib.sha256(str(s.get('token')).encode()).hexdigest(), user['session_token_sha256']):
        return None
    if dt.datetime.fromisoformat(s['expires_utc']) <= now():
        return None
    return {'username': s['username'], 'role': user['role'], 'expires_utc': s['expires_utc']}


def logout():
    s = _load(session_file(), None)
    if s:
        data = users()
        if s.get('username') in data['users']:
            data['users'][s['username']]['session_token_sha256'] = None
            _save(users_file(), data)
        session_file().unlink(missing_ok=True)
        audit('logout', s.get('username'))
        return s.get('username')
    return None


def require(command, args):
    """Called by lite.py before every command. Raises AccessError when the user may not run it."""
    roles = PROTECTED.get(command)
    if roles is None:
        return None
    if command == 'run-backup' and getattr(args, 'trigger', None) == 'scheduled' and not sys.stdin.isatty():
        return None  # unattended cron run (no keyboard); the schedule itself is installed by an admin
    if not users()['users']:
        raise AccessError('No SafeVault accounts yet. Create the admin first: python3 lite.py create-user --username YOURNAME')
    who = current()
    if who is None:
        raise AccessError('Please log in first: python3 lite.py login')
    if who['role'] not in roles:
        audit('access_denied', who['username'], command=command, role=who['role'])
        raise AccessError('User ' + who['username'] + ' (' + who['role'] + ') is not allowed to run ' + command + '. Admin only.')
    return who


# ---------------------------------------------------------------- CLI commands
def create_user_cmd(args):
    existing = users()['users']
    if existing:
        who = current()
        if not who or who['role'] != 'admin':
            raise AccessError('Only a logged-in admin can add users. Log in first: python3 lite.py login')
    role = 'admin' if not existing else args.role
    username = ask_username(args.username)
    password = ask_secret('New password: ')
    if ask_secret('Repeat password: ') != password:
        raise AccessError('Passwords do not match; no user created.')
    create_user(username, password, role)
    print('USER CREATED: ' + username + ' (' + role + '). Password stored only as a salted PBKDF2-SHA256 hash.')
    if not existing:
        print('This is the first account, so it is an admin. Now run: python3 lite.py login')
    return 0


def login_cmd(args):
    username = ask_username(args.username)
    who = login(username, ask_secret('Password: '))
    print('LOGIN SUCCESSFUL: ' + who['username'] + ' (' + who['role'] + '). Session valid for ' + str(SESSION_MINUTES) + ' minutes.')
    return 0


def logout_cmd(args):
    name = logout()
    print(('Logged out ' + name + '.') if name else 'Nobody was logged in.')
    return 0


def whoami_cmd(args):
    who = current()
    if who:
        left = int((dt.datetime.fromisoformat(who['expires_utc']) - now()).total_seconds() // 60)
        print('Logged in as ' + who['username'] + ' (' + who['role'] + '), session ends in about ' + str(left) + ' min.')
    else:
        print('Not logged in. Run: python3 lite.py login')
    return 0


def users_cmd(args):
    data = users()['users']
    print('{:<16} {:<7} {:<26} {}'.format('Username', 'Role', 'Created (UTC)', 'State'))
    for name, u in sorted(data.items()):
        locked = u.get('locked_until') and dt.datetime.fromisoformat(u['locked_until']) > now()
        print('{:<16} {:<7} {:<26} {}'.format(name, u['role'], u['created_utc'][:19], 'LOCKED' if locked else 'active'))
    return 0


def register(subs):
    p = subs.add_parser('create-user', help='Create a SafeVault login (first one is admin)')
    p.add_argument('--username')
    p.add_argument('--role', choices=ROLES, default='viewer')
    p.set_defaults(func=create_user_cmd)
    p = subs.add_parser('login', help='Log in with username and password (session lasts 30 min)')
    p.add_argument('--username')
    p.set_defaults(func=login_cmd)
    p = subs.add_parser('logout', help='End the current session')
    p.set_defaults(func=logout_cmd)
    p = subs.add_parser('whoami', help='Show who is logged in')
    p.set_defaults(func=whoami_cmd)
    p = subs.add_parser('users', help='List SafeVault users (admin only)')
    p.set_defaults(func=users_cmd)
