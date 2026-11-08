import importlib.util
from pathlib import Path
import sys
import unittest
from dotenv import dotenv_values
from io import StringIO

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
spec = importlib.util.spec_from_file_location('login_setup', ROOT / 'deploy/set_login_password.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class LoginSetupTests(unittest.TestCase):
    def test_preserves_credentials_and_quotes_pbkdf2_dollars(self):
        original = "ZHIHU_COOKIE='example=a; b=c'\nANTHROPIC_AUTH_TOKEN='test-only'\nSECRET_KEY=old\nPKM_PASSWORD_HASH=old\n"
        hashed = 'pbkdf2_sha256$600000$abcdef$123456'
        result = module.update_env(original, hashed, 'test-secret')
        parsed = dotenv_values(stream=StringIO(result))
        self.assertEqual(parsed['PKM_PASSWORD_HASH'], hashed)
        self.assertEqual(parsed['SECRET_KEY'], 'test-secret')
        self.assertEqual(parsed['ZHIHU_COOKIE'], 'example=a; b=c')
        self.assertEqual(parsed['ANTHROPIC_AUTH_TOKEN'], 'test-only')
        self.assertEqual(result.count('PKM_PASSWORD_HASH='), 1)
        self.assertIn("PKM_PASSWORD_HASH='pbkdf2_sha256$", result)

if __name__ == '__main__':
    unittest.main()
