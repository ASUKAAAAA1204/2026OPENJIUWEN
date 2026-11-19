"""Audit saved M0 evidence without converting missing evidence into PASS."""
from pathlib import Path
import json,xml.etree.ElementTree as ET,hashlib
ROOT=Path(__file__).resolve().parents[2]
def audit():
 out=ROOT/'outputs/baseline';checks=[]
 source_digest=hashlib.sha256()
 for path in sorted((ROOT/'src').rglob('*.py')):
  source_digest.update(path.relative_to(ROOT).as_posix().encode());source_digest.update(b'\0');source_digest.update(path.read_bytes())
 current_source=source_digest.hexdigest()
 def record(name,passed,detail):checks.append({'requirement':name,'passed':bool(passed),'detail':detail})
 ids=[]
 for filename in ['jiuwen-full-fixed-junit.xml','jiuwen-full-round2.xml','jiuwen-full-round3.xml']:
  path=out/filename
  if not path.exists():record(filename,False,'missing');continue
  cases=list(ET.parse(path).getroot().iter('testcase'));bad=[c for c in cases if any(c.find(k) is not None for k in ['failure','error','skipped'])]
  ids.append(sorted(c.attrib.get('classname','')+'::'+c.attrib['name'] for c in cases));record(filename,len(cases)==730 and not bad,{'tests':len(cases),'bad':len(bad)})
 record('identical regression identities',len(ids)==3 and all(x==ids[0] for x in ids),'three saved runs')
 for file in ['rebuilds.json','sdk-rebuilds.json']:
  p=out/file;rows=json.loads(p.read_text(encoding='utf-8-sig')) if p.exists() else []
  record(file,len(rows)==2 and all(x.get('lock_match') for x in rows),rows)
 for file in ['all-import-origins.json','external-all-import-origins.json']:
  p=out/file;data=json.loads(p.read_text(encoding='utf-8-sig')) if p.exists() else {}
  record(file,bool(data.get('modules')) and all(x['valid'] for x in data['modules']),data.get('module_count',0))
 for filename in ['sdk-rebuild1-full.xml','sdk-rebuild2-full.xml']:
  path=out/filename
  cases=list(ET.parse(path).getroot().iter('testcase')) if path.exists() else []
  record(filename,len(cases)==730 and all(not any(c.find(k) is not None for k in ['failure','error','skipped']) for c in cases),{'tests':len(cases)})
 archive=ROOT/'archive/workspace-20261118'
 manifest=json.loads((archive/'manifest.json').read_text(encoding='utf-8'))
 failures=[]
 for row in manifest['archived_files']:
  path=archive/row['path']
  if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:failures.append(row['path'])
 record('original archive integrity',not failures,{'checked':len(manifest['archived_files']),'failures':failures})
 record('root pip residuals',not list(ROOT.glob('pip-*')),'root directories only')
 for key,requirement in [('C','C independent conflict review'),('B','B framework environment versions and control evidence'),('D','D CI and startup evidence')]:
  receipt=ROOT/'docs/reviews/baseline/receipts'/f'{key}.json'
  problems=[]
  if not receipt.exists():problems.append('missing receipt')
  else:
   try:
    data=json.loads(receipt.read_text(encoding='utf-8-sig'))
    if data.get('reviewer_role')!=key or not data.get('reviewer'):problems.append('missing reviewer identity/role')
    if data.get('status')!='PASS':problems.append('receipt is not PASS')
    if not data.get('scope'):problems.append('missing explicit scope')
    if data.get('source_sha256')!=current_source:problems.append('receipt is not bound to current source')
    required={'B':{'three_framework_versions','control_36_cases','control_108_runs','live_model_9_runs'},'C':{'conflict_16_review','frozen_nodeids','regression_review'},'D':{'ui_verify','startup_stop_3_rounds','ci_failure_injection'}}[key]
    covered=set(data.get('requirements',[]))
    if not required.issubset(covered):problems.append('receipt does not cover mandatory requirements')
    evidence=data.get('evidence',[])
    if not evidence:problems.append('missing evidence')
    for item in evidence:
     path=(ROOT/item['path']).resolve()
     if not path.is_relative_to(ROOT) or not path.is_file():problems.append('invalid evidence path');continue
     if hashlib.sha256(path.read_bytes()).hexdigest()!=item.get('sha256'):problems.append('evidence fingerprint mismatch')
   except (ValueError,KeyError,TypeError):problems.append('malformed receipt')
  record(requirement,not problems,problems or 'Receipt integrity validated; substantive human review remains required')
 result={'source_sha256':current_source,'status':'PASS' if all(c['passed'] for c in checks) else 'PARTIAL','checks':checks}
 (out/'completion-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(audit())
