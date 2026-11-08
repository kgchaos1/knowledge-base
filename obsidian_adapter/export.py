"""Export into a dedicated Vault. Credentials are prompted, never persisted."""
import argparse
import getpass
from pathlib import Path
from .client import Client
from .vault import apply_snapshot,build_zip

def main():
    parser=argparse.ArgumentParser(description='Export a dedicated PKM Obsidian test vault')
    parser.add_argument('--server',default='http://8.133.220.164:8091')
    parser.add_argument('--directory',required=True)
    parser.add_argument('--zip',dest='zip_path')
    parser.add_argument('--allow-http',action='store_true',help='Explicitly accept plaintext transport risk')
    args=parser.parse_args()
    client=Client(args.server,allow_http=args.allow_http)
    client.login(getpass.getpass('PKM management password (not saved): '))
    notes,images,snapshot=client.fetch()
    result=apply_snapshot(Path(args.directory),notes,images,snapshot)
    print({key:len(value) for key,value in result.items()})
    if args.zip_path:
        output=Path(args.zip_path)
        if output.exists():raise SystemExit('ZIP already exists; choose a new output name')
        with output.open('xb') as f:f.write(build_zip(Path(args.directory)))
        print('Vault ZIP created')
if __name__=='__main__':main()
