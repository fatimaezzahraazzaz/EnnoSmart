"""Contrôle autonome du paquet EnnoScholar corrigé."""

from __future__ import annotations

import argparse
import compileall
import importlib
import pkgutil
import sys
import unittest
from pathlib import Path


def run(package: Path) -> int:
    package = package.resolve()
    parent = package.parent
    sys.path.insert(0, str(parent))

    if not compileall.compile_dir(str(package), quiet=1, force=True):
        print("ERREUR: au moins un module ne compile pas.")
        return 1

    root = importlib.import_module(package.name)
    failures = []
    for module in pkgutil.walk_packages(root.__path__, root.__name__ + "."):
        if ".tests." in module.name:
            continue
        try:
            importlib.import_module(module.name)
        except Exception as exc:  # pragma: no cover - diagnostic d'installation
            failures.append((module.name, type(exc).__name__, str(exc)))

    if failures:
        for name, kind, message in failures:
            print(f"ERREUR IMPORT: {name}: {kind}: {message}")
        return 1

    suite = unittest.defaultTestLoader.discover(str(package / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1

    print(f"OK - {package.name} {getattr(root, '__version__', '')}: compilation, imports et tests.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, default=Path(__file__).resolve().parent)
    return run(parser.parse_args().package)


if __name__ == "__main__":
    raise SystemExit(main())
