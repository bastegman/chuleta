# -*- coding: utf-8 -*-
"""Solo diagnostico (workflow diag-bloqueo): arranca _app/server.py tal cual, con faulthandler volcando las pilas de
TODOS los hilos cada N segundos a un fichero. No cambia nada de la app: se usa igual en cualquier commit.

  pythonw diag_launch.py <carpeta _app> <fichero de pilas> <cada_s> [argumentos de server.py...]
"""
import faulthandler
import os
import runpy
import sys


def main():
    app_dir, fh_path, every = sys.argv[1], sys.argv[2], float(sys.argv[3])
    f = open(fh_path, "a", encoding="utf-8")  # abierto todo el proceso: faulthandler escribe en su descriptor
    faulthandler.enable(file=f, all_threads=True)
    faulthandler.dump_traceback_later(every, repeat=True, file=f)
    server = os.path.join(app_dir, "server.py")
    sys.argv = [server] + sys.argv[4:]
    sys.path.insert(0, app_dir)
    os.chdir(app_dir)
    runpy.run_path(server, run_name="__main__")


if __name__ == "__main__":
    main()
