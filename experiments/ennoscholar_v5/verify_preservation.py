"""Read-only integrity check of V4 and the copied scientific baseline."""
import hashlib
import json
from pathlib import Path
import sqlite3

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def verify():
    baseline=json.loads((HERE/'baseline-v4.json').read_text(encoding='utf-8'))
    source=Path(baseline['source'])
    mismatches=[]
    for name,digest in baseline['files'].items():
        for root in (source,ROOT):
            path=root/name
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                mismatches.append(str(path))
    records=json.loads((HERE/'baseline-data.json').read_text(encoding='utf-8'))
    with sqlite3.connect((source/'ennoscholar_v4_test.db').as_uri()+'?mode=ro',uri=True) as db:
        for row in records:
            for table,ident,key in [('scholar_v4_runs',row['run_id'],'run'),('scholar_v4_sessions',row['session_id'],'session')]:
                data=db.execute(f'SELECT data FROM {table} WHERE id=?',(ident,)).fetchone()[0]
                if hashlib.sha256(data.encode()).hexdigest()!=row[key]:mismatches.append(table+':'+ident)
    pdf='7c5a6ac509b734b9b92fd51cbef2d659136fc2fee521c67126197b012f6cdd69.pdf'
    for path in [source/'backend_api/storage/scholar_v4/pdf'/pdf,HERE/'.artifacts/pdf'/pdf]:
        if hashlib.sha256(path.read_bytes()).hexdigest()+'.pdf'!=pdf:mismatches.append(str(path))
    report={'v4_package_files_checked':len(baseline['files']),'experimental_v4_copy_identical':not mismatches,
        'real_database_rows_checked':4,'stokes_pdf_sha256':pdf[:-4],'mismatches':mismatches}
    assert not mismatches,report
    (HERE/'.artifacts/preservation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':verify()
