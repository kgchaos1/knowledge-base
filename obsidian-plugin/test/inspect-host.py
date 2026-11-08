"""Read installed Obsidian code only; never reads vaults or user secrets."""
from pathlib import Path
import struct,json
p=Path(r'D:\app\Obsidian\resources\obsidian.asar')
with p.open('rb') as f:
 h=f.read(16);header_size=struct.unpack('<I',h[4:8])[0];json_size=struct.unpack('<I',h[12:16])[0]
 f.seek(16);header=json.loads(f.read(json_size))
 def walk(node,prefix=''):
  for name,entry in node.get('files',{}).items():
   path=prefix+'/'+name
   if 'files' in entry:yield from walk(entry,path)
   else:yield path,entry
 for name,entry in walk(header):
  if name in ['/app.js','/obsidian.js']:
   f.seek(8+header_size+int(entry['offset']));data=f.read(entry['size'])
   target=Path('.tooling/obsidian-host-'+Path(name).name);target.write_bytes(data);print(target)
