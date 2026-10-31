"""Export authorised real traces read-only into private experimental fixtures."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3

CASES = {"chemistry":"0f091608d85c4eef9b945ecc22104d46", "stokes":"3d81c866b2364d7fbe8b87f376669f68"}


def prepare(source_root, output):
    source_root, output = Path(source_root).resolve(), Path(output).resolve()
    if output.is_relative_to(source_root):
        raise PermissionError("Fixtures must be outside the live V4 worktree")
    output.mkdir(parents=True,exist_ok=True)
    baseline = {}
    with sqlite3.connect((source_root/'ennoscholar_v4_test.db').as_uri()+'?mode=ro',uri=True) as db:
        for name,rid in CASES.items():
            sid,owner,data=db.execute('SELECT session_id,owner_id,data FROM scholar_v4_runs WHERE id=?',(rid,)).fetchone()
            session=db.execute('SELECT data FROM scholar_v4_sessions WHERE id=?',(sid,)).fetchone()[0]
            state=json.loads(session)
            case={"run_id":rid,"session_id":sid,"owner_id":owner,"state":state,"run":json.loads(data)}
            with gzip.open(output/(name+'.json.gz'),'wt',encoding='utf-8') as stream:
                json.dump(case,stream,ensure_ascii=False)
            baseline[name]={"run":hashlib.sha256(data.encode()).hexdigest(),"session":hashlib.sha256(session.encode()).hexdigest()}
            for a in state['articles'].values():
                filename=a.get('fulltext',{}).get('pdf_file')
                if name=='stokes' and filename:
                    source=source_root/'backend_api/storage/scholar_v4/pdf'/filename
                    target=output/'pdf'/filename;target.parent.mkdir(exist_ok=True)
                    if hashlib.sha256(source.read_bytes()).hexdigest()+'.pdf' != filename:
                        raise ValueError('Archived PDF fingerprint mismatch')
                    if not target.exists():os.link(source,target)
    (output/'source-hashes.json').write_text(json.dumps(baseline,indent=2),encoding='utf-8')
    return baseline


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source-root',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.source_root,args.output),indent=2))
