"""Run on the PKM server to set login credentials without shell/history exposure.

Usage: python3 deploy/set_login_password.py
Run with the project environment (Python 3.11+) and app dependencies installed.
Does not start/restart services, print secrets, or change platform credentials.
"""
import getpass
import os
from pathlib import Path
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.auth import hash_password


def update_env(text, password_hash, secret):
    keys = {'PKM_PASSWORD_HASH': password_hash, 'SECRET_KEY': secret,
            'PKM_PUBLIC_ORIGIN': 'https://kgchaos1.eu.cc'}
    lines = []
    for line in text.splitlines():
        key = line.split('=', 1)[0].strip()
        if key not in keys:
            lines.append(line)
    # Compose single-quoted env values preserve literal dollar signs in PBKDF2.
    for key, value in keys.items():
        lines.append(key + "='" + value + "'")
    return '\n'.join(lines) + '\n'


def main():
    target = ROOT / '.env'
    original = target.read_text(encoding='utf-8')
    password = getpass.getpass('New PKM login password (at least 12 characters): ')
    if len(password) < 12:
        raise SystemExit('Password must have at least 12 characters; nothing changed.')
    if password != getpass.getpass('Confirm password: '):
        raise SystemExit('Passwords do not match; nothing changed.')
    updated = update_env(original, hash_password(password), secrets.token_urlsafe(48))
    # Atomic replacement in the same directory; existing .env is read first.
    fd, name = tempfile.mkstemp(prefix='.pkm-auth-', dir=str(ROOT))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, 0o600)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    print('Login hash and session secret saved; no plaintext password stored.')
    print('Existing signed sessions become invalid after container recreation.')

if __name__ == '__main__':
    main()
