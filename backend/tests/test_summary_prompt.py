"""Verify summarizer contract with no network and no real configuration."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

class SummaryPromptTests(unittest.TestCase):
    def test_structured_summary_and_empty_guard(self):
        code = '''import asyncio
from app.ai.anthropic import AnthropicProvider
p=object.__new__(AnthropicProvider)
captured=[]
async def fake(messages,temperature=0.5):
 captured.extend(messages)
 return '【核心结论】测试结论'
p._call_api=fake
assert asyncio.run(p.summarize('原文只有一句具体事实'))=='【核心结论】测试结论'
text=captured[0]['content']
for heading in ['【核心结论】','【关键要点】','【依据与例子】','【适用边界与待核实】']:
 assert heading in text
assert '不要虚构' in text and '原文只有一句具体事实' in text
try: asyncio.run(p.summarize(' '))
except ValueError: pass
else: raise AssertionError('Empty body accepted')
'''
        with tempfile.TemporaryDirectory() as cwd:
            env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            result=subprocess.run([sys.executable,'-B','-c',code],cwd=cwd,env=env,capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
