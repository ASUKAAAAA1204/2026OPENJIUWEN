"""Verify every installed project module has a unique origin under active src."""
from pathlib import Path
from importlib.machinery import PathFinder
import importlib,json,sys
ROOT=Path(__file__).resolve().parents[2]
def main():
 package=importlib.import_module('assurance_ledger');base=Path(package.__file__).resolve().parent
 rows=[]
 for source in sorted((ROOT/'src/assurance_ledger').rglob('*.py')):
  relative=source.relative_to(ROOT/'src');parts=list(relative.with_suffix('').parts)
  if parts[-1]=='__init__':parts.pop()
  name='.'.join(parts)
  # Resolve directly through package search locations without executing SDK modules.
  paths=sys.path;spec=None
  for i in range(len(parts)):
   spec=PathFinder.find_spec('.'.join(parts[:i+1]),paths)
   if spec is None:break
   paths=spec.submodule_search_locations
  origin=Path(spec.origin).resolve() if spec and spec.origin else None
  rows.append({'module':name,'origin':str(origin),'valid':origin==source.resolve()})
 print(json.dumps({'python':sys.executable,'module_count':len(rows),'modules':rows},indent=2))
 return not all(row['valid'] for row in rows)
if __name__=='__main__':raise SystemExit(main())
