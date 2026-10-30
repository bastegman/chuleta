# -*- coding: utf-8 -*-
"""Ejecuta los tests y FALLA si no se ha ejecutado ninguno (un `discover` que no encuentra nada sale en verde).

    python tests/run.py            desde la raíz del repo (equivale a `python -m unittest discover -s tests -v`)
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_TESTS = 40  # si bajan de aquí es que algo no se está descubriendo


def main():
    suite = unittest.defaultTestLoader.discover(HERE, pattern="test_*.py", top_level_dir=HERE)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.testsRun == 0:
        print("ERROR: no se ha ejecutado ningún test (¿discover no encuentra tests/test_*.py?)", file=sys.stderr)
        return 2
    if result.testsRun < MIN_TESTS:
        print(f"ERROR: solo {result.testsRun} tests (se esperaban al menos {MIN_TESTS})", file=sys.stderr)
        return 2
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
