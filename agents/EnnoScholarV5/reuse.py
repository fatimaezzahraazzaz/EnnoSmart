"""Load unchanged V4 implementation with V5-relative dependencies."""
import importlib.util
import sys
from pathlib import Path

def load(name, source):
    full = "agents.EnnoScholarV5." + name
    if full in sys.modules:
        return sys.modules[full]
    path = Path(__file__).resolve().parent.parent / "EnnoScholarV4" / source
    spec = importlib.util.spec_from_file_location(full, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(full, None)
        raise
    return module
