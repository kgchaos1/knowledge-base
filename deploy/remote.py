"""Shared SSH access. No plaintext credentials in source or output.

Use PKM_SSH_HOST/USER/PASSWORD environment variables or a Windows user-scoped
DPAPI record at .local/remote.dpapi. Host keys must already be known.
"""
import ctypes
import json
import os
from pathlib import Path
import paramiko

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / '.local' / 'remote.dpapi'

class Blob(ctypes.Structure):
    _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]

def protect(data, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('DPAPI requires Windows; use environment credentials')
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    function = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    args = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    if not function(*args):
        raise RuntimeError('Windows credential protection failed')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.windll.kernel32.LocalFree(output.data)

def save_credentials(values):
    PRIVATE.parent.mkdir(exist_ok=True)
    PRIVATE.write_bytes(protect(json.dumps(values).encode()))

def credentials():
    if os.environ.get('PKM_SSH_PASSWORD'):
        return {'server':os.environ.get('PKM_SSH_HOST','8.133.220.164'),
                'user':os.environ.get('PKM_SSH_USER','root'),'password':os.environ['PKM_SSH_PASSWORD']}
    if PRIVATE.is_file():
        return json.loads(protect(PRIVATE.read_bytes(), decrypt=True))
    raise RuntimeError('Configure SSH credentials in environment; see deploy/README-maintenance.md')

def connect():
    values = credentials()
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(values['server'], username=values['user'], password=values['password'],
                   timeout=15, auth_timeout=15, banner_timeout=15)
    return client

def run(client, command, timeout=90):
    _, out, err = client.exec_command(command, timeout=timeout)
    text = out.read().decode('utf-8','replace')
    err.read()  # Do not echo arbitrary remote output that might contain secrets.
    code = out.channel.recv_exit_status()
    if code:
        raise RuntimeError('Remote operation failed (exit %s); inspect server privately' % code)
    return text
