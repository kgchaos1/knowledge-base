"""Explicit live connector test. Pass admin password only to subprocess memory."""
from pathlib import Path
import os,subprocess
ROOT=Path(__file__).resolve().parents[1]
password=(ROOT/'PKM-login-private.txt').read_text(encoding='utf-8').split('登录密码：',1)[1].splitlines()[0]
result=subprocess.run(['node','test/live.cjs'],cwd=ROOT/'obsidian-plugin',env=dict(os.environ,PKM_PASSWORD=password))
raise SystemExit(result.returncode)
