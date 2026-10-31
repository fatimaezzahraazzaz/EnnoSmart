"""Reproducible runner: no dotenv, network, real DB writes or auto-loaded plugins."""
import os
from pathlib import Path
import sys
import types
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.dont_write_bytecode=True
sys.path[:0]=[str(ROOT/'experiments/ennoscholar_v5/.deps'),str(ROOT/'backend_api'),str(ROOT)]
tempfile.tempdir=str(ROOT/'experiments/ennoscholar_v5/.artifacts/temp')
Path(tempfile.tempdir).mkdir(parents=True,exist_ok=True)
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
os.environ['LANGSMITH_TRACING']='false'
os.environ['LANGCHAIN_TRACING_V2']='false'
os.environ['DATABASE_URL']='sqlite:///:memory:'
dotenv=types.ModuleType('dotenv');dotenv.load_dotenv=lambda *a,**k:False;dotenv.dotenv_values=lambda *a,**k:{}
sys.modules['dotenv']=dotenv


def audit(event,args):
    if event in {'socket.connect','socket.getaddrinfo','subprocess.Popen','os.system'}:
        raise AssertionError('Offline experiment: no network or child process')
    if event=='sqlite3.connect' and 'ennoscholar_v4_test.db' in str(args[0]) and 'mode=ro' not in str(args[0]):
        raise AssertionError('No writes to the real V4 database')
    if event=='open' and isinstance(args[0],(str,bytes)):
        path=Path(os.fsdecode(args[0]))
        if path.name.startswith('.env') or path.name.endswith('.env'):
            raise AssertionError('No credential file reads')
        flags=args[2] if len(args)>2 else 0
        write=flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)
        if write and path.name.lower() != os.devnull.lower() and not path.resolve().is_relative_to(ROOT):
            raise AssertionError('Writes are restricted to the V5 worktree')


sys.addaudithook(audit)

if __name__=='__main__':
    import pytest
    raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','-o','junit_family=xunit1',
        '-o','log_file='+str(ROOT/'experiments/ennoscholar_v5/.artifacts/pytest.log'),
        '--basetemp='+str(ROOT/'experiments/ennoscholar_v5/.artifacts/pytest'),
        '--junitxml='+str(ROOT/'experiments/ennoscholar_v5/.artifacts/tests.xml'),
        str(ROOT/'experiments/ennoscholar_v5/tests'),*sys.argv[1:]]))
