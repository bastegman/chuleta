# -*- coding: utf-8 -*-
"""Los workflows de GitHub Actions (.github/workflows): qué se lanza dónde y qué comprueba la CI en Windows.

PyYAML no está en requirements-dev: `cargar_yaml` lee el subconjunto de YAML que usan estos ficheros (mapas, listas en
bloque y `[a, b]` en una línea, escalares con o sin comillas, bloques `|` y comentarios) y da ValueError con lo demás,
para que un workflow escrito de otra forma haga fallar el test en vez de leerse mal."""
import glob
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # sin importar support: aquí no hace falta la app
WORKFLOWS = os.path.join(REPO, ".github", "workflows")
_CLAVE = re.compile(r"([A-Za-z0-9_.-]+)\s*:(?:\s+(.*))?$")


def _cadena(r, i):
    """(valor, posición tras el cierre) de la cadena entre comillas que empieza en r[i]."""
    q, j, out = r[i], i + 1, []
    while j < len(r):
        c = r[j]
        if q == "'" and c == "'" and r[j + 1:j + 2] == "'":
            out.append("'")
            j += 2
            continue
        if q == '"' and c == "\\":
            raise ValueError(f"escapes en comillas dobles (no soportados): {r!r}")
        if c == q:
            return "".join(out), j + 1
        out.append(c)
        j += 1
    raise ValueError(f"comilla sin cerrar: {r!r}")


def _nada_mas(r, j):
    resto = r[j:].strip()
    if resto and not resto.startswith("#"):
        raise ValueError(f"texto de más tras el valor: {r!r}")


def _en_linea(r):
    """Escalar o lista `[a, b]` escritos en la misma línea que su clave o su guion (sin el comentario final)."""
    r = r.strip()
    if r[:1] in ("'", '"'):
        v, j = _cadena(r, 0)
        _nada_mas(r, j)
        return v
    if r.startswith("["):
        items, j = [], 1
        while True:
            while r[j:j + 1] == " ":
                j += 1
            if j >= len(r):
                raise ValueError(f"lista sin cerrar (en varias líneas no está soportada): {r!r}")
            if r[j] == "]":
                _nada_mas(r, j + 1)
                return items
            if r[j] in ("'", '"'):
                v, j = _cadena(r, j)
            else:
                k = j
                while j < len(r) and r[j] not in ",]":
                    j += 1
                v = r[k:j].strip()
            items.append(v)
            while r[j:j + 1] == " ":
                j += 1
            if r[j:j + 1] == ",":
                j += 1
            elif r[j:j + 1] != "]":
                raise ValueError(f"lista mal formada: {r!r}")
    if r[:1] in tuple("{&*!|>%@`?"):
        raise ValueError(f"YAML no soportado: {r!r}")
    m = re.search(r"\s#", r)
    return (r[:m.start()] if m else r).rstrip()


class _Lector:
    def __init__(self, texto):
        self.lineas = texto.replace("\r\n", "\n").split("\n")
        self.i = 0

    def sangria(self):
        """Sangría de la siguiente línea con contenido (salta vacías y comentarios), o -1 al final."""
        while self.i < len(self.lineas):
            linea = self.lineas[self.i]
            if linea.strip() and not linea.strip().startswith("#"):
                if linea[:len(linea) - len(linea.lstrip())].strip(" "):
                    raise ValueError(f"tabulador en la sangría (línea {self.i + 1})")
                return len(linea) - len(linea.lstrip(" "))
            self.i += 1
        return -1

    def guion(self):
        s = self.lineas[self.i].strip()
        return s == "-" or s.startswith("- ")

    def nodo(self, n):
        return self.lista(n) if self.guion() else self.mapa(n)

    def mapa(self, n):
        out = {}
        while self.sangria() == n and not self.guion():
            m = _CLAVE.match(self.lineas[self.i].strip())
            if not m:
                raise ValueError(f"no sé leer la línea {self.i + 1}: {self.lineas[self.i].strip()!r}")
            if m.group(1) in out:
                raise ValueError(f"clave repetida {m.group(1)!r} (línea {self.i + 1})")
            self.i += 1
            out[m.group(1)] = self.valor((m.group(2) or "").strip(), n)
        return out

    def lista(self, n):
        out = []
        while self.sangria() == n and self.guion():
            linea = self.lineas[self.i]
            contenido = linea.strip()[1:].strip()
            if not contenido:  # «-» solo: el elemento sigue en las líneas de debajo, más sangrado
                self.i += 1
                sig = self.sangria()
                out.append(self.nodo(sig) if sig > n else None)
            elif _CLAVE.match(contenido):  # «- clave: valor»: un mapa con las claves a la altura de «clave»
                col = len(linea) - len(linea.lstrip(" ")) + linea.strip().index(contenido)
                self.lineas[self.i] = " " * col + contenido
                out.append(self.mapa(col))
            else:
                self.i += 1
                out.append(_en_linea(contenido))
        return out

    def valor(self, resto, n):
        if resto in ("|", "|-"):
            return self.bloque(resto, n)
        if resto and not resto.startswith("#"):
            return _en_linea(resto)
        sig = self.sangria()  # sin valor en la línea: un nodo más sangrado, una lista a la misma altura o nada
        if sig > n:
            return self.nodo(sig)
        if sig == n and self.guion():
            return self.lista(n)
        return None

    def bloque(self, indicador, n):
        lineas = []
        while self.i < len(self.lineas):
            linea = self.lineas[self.i]
            if linea.strip() and len(linea) - len(linea.lstrip(" ")) <= n:
                break
            lineas.append(linea)
            self.i += 1
        llenas = [x for x in lineas if x.strip()]
        if not llenas:
            return ""
        b = len(llenas[0]) - len(llenas[0].lstrip(" "))
        texto = "\n".join(x[b:] if x.strip() else "" for x in lineas).rstrip("\n")
        return texto + ("\n" if indicador == "|" else "")


def cargar_yaml(texto):
    lector = _Lector(texto)
    n = lector.sangria()
    if n < 0:
        return None
    if n:
        raise ValueError("la primera línea con contenido tiene sangría")
    datos = lector.nodo(0)
    if lector.sangria() >= 0:
        raise ValueError(f"no sé leer la línea {lector.i + 1}: {lector.lineas[lector.i].strip()!r}")
    return datos


def workflow(nombre):
    with open(os.path.join(WORKFLOWS, nombre), encoding="utf-8") as f:
        return cargar_yaml(f.read())


def eventos(on):
    """Los eventos de `on:` (una cadena, una lista o un mapa)."""
    return {on} if isinstance(on, str) else set(on or ())


def como_lista(v):
    return [v] if isinstance(v, str) else list(v or [])


class CargarYamlTests(unittest.TestCase):
    def test_subconjunto(self):
        texto = ("name: x  # comentario\non:\n  workflow_dispatch:\n  push:\n    branches: [main, 'a b', \"c\"]\n"
                 "    paths:\n      - \"_app/**\"\n      - scripts/e2e/**\njobs:\n  j:\n    steps:\n"
                 "      - uses: a@b  # v1\n        with:\n          python-version: \"3.13\"\n"
                 "      - name: n\n        run: |\n          uno\n\n          # dos\n        shell: powershell\n")
        self.assertEqual(cargar_yaml(texto), {
            "name": "x", "on": {"workflow_dispatch": None,
                                "push": {"branches": ["main", "a b", "c"], "paths": ["_app/**", "scripts/e2e/**"]}},
            "jobs": {"j": {"steps": [{"uses": "a@b", "with": {"python-version": "3.13"}},
                                     {"name": "n", "run": "uno\n\n# dos\n", "shell": "powershell"}]}}})

    def test_lo_que_no_sabe_leer_falla(self):
        for texto in ("a: {b: 1}", "a: [b,\n  c]", "a: 1\na: 2", "a: &x 1", "a: >\n  b", "a:\n\tb: 1", "a: 'b"):
            with self.subTest(texto), self.assertRaises(ValueError):
                cargar_yaml(texto)

    def test_lee_todos_los_workflows(self):
        nombres = sorted(n for n in os.listdir(WORKFLOWS) if n.endswith((".yml", ".yaml")))
        self.assertIn("tests.yml", nombres)
        for nombre in nombres:
            with self.subTest(nombre):
                wf = workflow(nombre)
                self.assertTrue(wf["name"] and wf["on"] and wf["jobs"])
                for job in wf["jobs"].values():  # sin tope, un cuelgue gasta los 360 min por defecto del runner
                    self.assertGreater(int(job["timeout-minutes"]), 0)


class WorkflowsWindowsTests(unittest.TestCase):
    """P2-38: lo que solo se puede probar en Windows corre también al llegar a main, no solo en ramas ya fusionadas."""

    def test_e2e_y_escritorio_en_main_y_a_mano(self):
        for nombre, rutas in (("lookfeel-e2e.yml", ("_app/**", "INSTALAR.bat", "ABRIR CHULETA.bat", "requirements.txt",
                                                    "scripts/build_zip.py", "scripts/e2e/**",
                                                    "scripts/lookfeel_spike.py")),  # lo importa scripts/e2e/ventana.py
                              ("reunion-windows.yml", ("_app/**", "tests/**", "requirements.txt"))):
            with self.subTest(nombre):
                on = workflow(nombre)["on"]
                self.assertIn("workflow_dispatch", eventos(on))  # a mano: Actions -> Run workflow
                push = on["push"]
                self.assertIn("main", como_lista(push.get("branches")))
                self.assertNotIn("branches-ignore", push)
                self.assertNotIn("paths-ignore", push)
                paths = como_lista(push.get("paths"))
                for r in rutas + (".github/workflows/" + nombre,):  # un cambio del propio workflow también lo lanza
                    self.assertIn(r, paths)
                for r in paths:  # una errata en un filtro haría que no se lanzara nunca
                    self.assertTrue(glob.glob(os.path.join(REPO, r), recursive=True), r)

    def test_spike_archivado(self):
        """El spike de medición (lookfeel-spike) ya no se lanza con cada push: queda para repetirlo a mano."""
        if not os.path.exists(os.path.join(WORKFLOWS, "lookfeel-spike.yml")):
            self.skipTest("lookfeel-spike.yml retirado")
        self.assertEqual(eventos(workflow("lookfeel-spike.yml")["on"]), {"workflow_dispatch"})

    def test_tests_parsea_los_ps1_con_powershell_51(self):
        """tests.yml, en Windows: ParseFile de Windows PowerShell 5.1 (el que lanza INSTALAR.bat) sobre los .ps1 de
        _app (instalar.ps1 y, si existe, desinstalar.ps1), con un control que demuestra que detecta un error."""
        job = workflow("tests.yml")["jobs"]["tests"]
        self.assertIn("windows-latest", como_lista(job["strategy"]["matrix"]["os"]))
        pasos = [p for p in job["steps"] if "ParseFile" in (p.get("run") or "")]
        self.assertEqual(len(pasos), 1)
        paso = pasos[0]
        self.assertEqual(paso.get("shell"), "powershell")  # Windows PowerShell 5.1; «pwsh» sería la 7
        self.assertIn("runner.os == 'Windows'", paso.get("if", ""))
        self.assertIn("!cancelled()", paso.get("if", ""))  # también si fallan los tests: es otra comprobación
        run = paso["run"]
        for s in ("[System.Management.Automation.Language.Parser]::ParseFile", '"_app"', '"*.ps1"', "instalar.ps1",
                  '"Desktop"', ".Major -ne 5", "roto.ps1", "exit 1"):
            self.assertIn(s, run)
        self.assertTrue(run.isascii())  # sin BOM, PowerShell 5.1 leería el script como ANSI
        self.assertLess(job["steps"].index(next(p for p in job["steps"] if p.get("run") == "python tests/run.py")),
                        job["steps"].index(paso))

    def test_agnosticismo_con_el_secreto_solo_en_su_paso(self):
        """P2-39: la lista privada llega del secreto de Actions solo al paso que ejecuta el test de agnosticismo."""
        wf = workflow("tests.yml")
        self.assertNotIn("env", wf)
        pasos = []
        for job in wf["jobs"].values():
            self.assertNotIn("NOTAS_PALABRAS_VETADAS", job.get("env") or {})
            pasos += [p for p in job["steps"] if "NOTAS_PALABRAS_VETADAS" in (p.get("env") or {})]
        self.assertEqual(len(pasos), 1)
        self.assertEqual(pasos[0]["env"]["NOTAS_PALABRAS_VETADAS"], "${{ secrets.NOTAS_PALABRAS_VETADAS }}")
        self.assertIn("test_package.TemplatesTests.test_sin_rastro_de_clientes", pasos[0]["run"])
        self.assertEqual(pasos[0].get("working-directory"), "tests")

    def test_agnosticismo_falla_sin_el_secreto_en_los_push_del_repo_propio(self):
        """P2-39: en los push del repo propio el secreto tiene que estar. Si falta, o se borra o se vacía al rotarlo, el
        test falla en vez de saltarse en verde. En los demás casos, sin el secreto, se salta con un aviso: los
        pull_request (los de un fork no reciben secretos y los de una rama del repo ya pasan por el push de esa rama),
        los forks y Dependabot."""
        wf = workflow("tests.yml")
        self.assertIn("push", eventos(wf["on"]))
        pares = [(job, p) for job in wf["jobs"].values() for p in job["steps"]
                 if "NOTAS_PALABRAS_VETADAS" in (p.get("env") or {})]
        self.assertEqual(len(pares), 1)
        job, paso = pares[0]
        self.assertEqual(paso["env"].get("NOTAS_EXIGIR_VETADAS"),
                         "${{ github.event_name == 'push' && !github.event.repository.fork"
                         " && github.actor != 'dependabot[bot]' }}")
        for d in (job, paso):  # nada que lo deje en verde aunque falle ni que lo salte en un push
            self.assertNotIn("continue-on-error", d)
            self.assertNotIn("if", d)
        run = paso["run"]
        self.assertIn('[ "$NOTAS_EXIGIR_VETADAS" = "true" ]', run)  # el ::error, solo donde se exige
        self.assertIn("::error title=Agnosticismo::", run)
        self.assertNotIn("|| true", run)
        self.assertNotIn("exit 0", run)
        self.assertTrue(run.rstrip().endswith("test_package.TemplatesTests.test_sin_rastro_de_clientes"))


if __name__ == "__main__":
    unittest.main()
