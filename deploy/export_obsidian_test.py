"""Explicit one-shot test export; no server writes, no credentials in output."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from obsidian_adapter.client import Client
from obsidian_adapter.vault import apply_snapshot,build_zip

password=(ROOT/'PKM-login-private.txt').read_text(encoding='utf-8').split('登录密码：',1)[1].splitlines()[0]
client=Client('http://8.133.220.164:8091',allow_http=True)
client.login(password)
notes,images,snapshot=client.fetch()
output=Path(r'D:\PKM备份\Obsidian测试库')
result=apply_snapshot(output,notes,images,snapshot)
print('Notes:',len(notes),'attachments:',len(images),'result:',{k:len(v) for k,v in result.items()})
readme=output/'使用说明.md'
if not readme.exists():
    with readme.open('x',encoding='utf-8') as f:
        f.write('''# PKM Obsidian测试库\n\n在Obsidian中打开此文件夹作为仓库。摘要优先，原文在折叠callout中展开，图片均保存在附件目录。\n\n这是单次导出的本地库，不是已经运行的自动同步插件。请勿将此文件夹直接覆盖已有仓库。个人批注建议另建笔记；后续导出检测修改并保留冲突副本。\n''')
zip_path=ROOT/'PKM-Obsidian-Test-Vault.zip'
if zip_path.exists():raise RuntimeError('Existing deliverable ZIP requires review before overwrite')
with zip_path.open('xb') as f:f.write(build_zip(output))
print('Dedicated vault and whitelisted ZIP ready')
