#!/usr/bin/env python3
"""Make a dashboard login entry for Netlify. The password is typed hidden and never saved.

Run (Windows or Ubuntu):  python3 scripts/make_dashboard_user.py arnav
Copy the printed line into the Netlify environment variable DASHBOARD_USERS.
For more users, join the lines with ;   e.g.  arnav:pbkdf2_sha256$...;member2:pbkdf2_sha256$...
"""
import getpass, hashlib, re, secrets, sys

ITERATIONS = 310_000

def entry(username, password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS)
    return username + ':pbkdf2_sha256$' + str(ITERATIONS) + '$' + salt.hex() + '$' + digest.hex()

def main():
    username = (sys.argv[1] if len(sys.argv) > 1 else input('Username: ')).strip().lower()
    if not re.fullmatch(r'[a-z][a-z0-9_.-]{2,31}', username):
        sys.exit('Username: 3-32 lowercase letters/digits, starting with a letter.')
    password = getpass.getpass('Password (hidden): ')
    if len(password) < 8 or not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        sys.exit('Password needs at least 8 characters with a letter and a number.')
    if getpass.getpass('Repeat password: ') != password:
        sys.exit('Passwords do not match.')
    print('\nDASHBOARD_USERS value (paste into Netlify, keep it secret):\n' + entry(username, password))
    print('\nSESSION_SECRET value (only needed once, any long random text works):\n' + secrets.token_urlsafe(48))

if __name__ == '__main__':
    main()
