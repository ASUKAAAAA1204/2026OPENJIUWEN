"""Capture active source inventory and verify original archive without mutation."""
from pathlib import Path
import hashlib,json,subprocess,ast
ROOT=Path(__file__).resolve().parents[2]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 out=ROOT/'outputs/baseline';out.mkdir(parents=True,exist_ok=True)
 archive=ROOT/'archive/workspace-20261118'
 manifest=json.loads((archive/'manifest.json').read_text(encoding='utf-8'))
 failures=[]
 for row in manifest['archived_files']:
  p=archive/row['path']
  if not p.is_file() or sha(p)!=row['sha256']: failures.append(row['path'])
 files=[]
 for folder in ['src','tests','scripts','fixtures']:
  for p in (ROOT/folder).rglob('*'):
   if p.is_file() and '__pycache__' not in p.parts:
    files.append({'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p),'bytes':p.stat().st_size})
 conflicts=[]
 for op in manifest['operations']:
  if op['action']!='conflict-imported-selected':continue
  active=ROOT/op['active'];original=ROOT/op['root_original']; imported=archive/'imported-project'/op['active']
  conflicts.append({'path':op['active'],'original_sha256':sha(original),'imported_sha256':sha(imported),'active_sha256':sha(active),'active_matches_imported':sha(active)==sha(imported)})
 report={'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'status':subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True),'archive_checked':len(manifest['archived_files']),'archive_failures':failures,'files':files,'conflicts':conflicts}
 (out/'inventory.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'archive_checked':report['archive_checked'],'archive_failures':failures,'active_files':len(files),'conflicts':len(conflicts)},ensure_ascii=False))
 return bool(failures)
if __name__=='__main__':raise SystemExit(main())
