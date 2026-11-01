# -*- coding: utf-8 -*-
"""Los workflows de GitHub Actions (.github/workflows): qué se lanza dónde y qué comprueba la CI en Windows.

PyYAML no está en requirements-dev: `cargar_yaml` lee el subconjunto de YAML que usan estos ficheros (mapas y listas en
bloque; listas `[a, b]` y mapas `{a: b}` en línea, anidados y en una o varias líneas; escalares con o sin comillas;
bloques `|`, `|-`, `>` y `>-`, y comentarios) y da ValueError con lo demás (anclas, alias, etiquetas, escapes en
comillas dobles, escalares sin comillas en varias líneas…), para que un workflow escrito de otra forma haga fallar el
test en vez de leerse mal. Los escalares quedan como cadenas: `timeout-minutes: 30` da «30»."""
import glob
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # sin importar support: aquí no hace falta la app
WORKFLOWS = os.path.join(REPO, ".github", "workflows")
_CLAVE = re.compile(r"([A-Za-z0-9_.-]+)\s*:(?:\s+(.*))?$")
_NO_SOPORTADO = tuple("&*!|>%@`?")  # al principio de un valor: anclas, alias, etiquetas, bloques raros, claves «?»
_FIN_DE_CLAVE = ("", " ", "\t", ",", "[", "]", "{", "}")  # lo que puede seguir al «:» que cierra una clave sin comillas


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


def _blancos(r, j):
    while r[j:j + 1] in (" ", "\t"):
        j += 1
    return j


def _sin_comentario(linea):
    """(la línea sin su comentario final, cuántos `[` y `{` abre menos los `]` y `}` que cierra), sin contar lo que va
    entre comillas. Una comilla solo abre una cadena al principio de un escalar (tras un blanco, `[`, `{`, `,` o `:`);
    en `it's` es una letra más."""
    prof, q, i = 0, None, 0
    while i < len(linea):
        c = linea[i]
        if q:
            if q == '"' and c == "\\":
                raise ValueError(f"escapes en comillas dobles (no soportados): {linea!r}")
            if c == q and q == "'" and linea[i + 1:i + 2] == "'":
                i += 1
            elif c == q:
                q = None
        elif c in ("'", '"') and (i == 0 or linea[i - 1] in " \t[{,:"):
            q = c
        elif c == "#" and (i == 0 or linea[i - 1] in " \t"):
            return linea[:i], prof
        elif c in ("[", "{"):
            prof += 1
        elif c in ("]", "}"):
            prof -= 1
        i += 1
    if q:
        raise ValueError(f"comilla sin cerrar (una cadena en varias líneas no está soportada): {linea!r}")
    return linea, prof


def _elemento(r, j, clave=False):
    """(valor, posición tras él) de un elemento de `[…]`, o de una clave o un valor de `{…}`, que empieza en r[j]."""
    c = r[j:j + 1]
    if c in ("[", "{"):
        if clave:
            raise ValueError(f"clave compuesta en un mapa en línea (no soportada): {r!r}")
        return _flujo(r, j)
    if c in ("'", '"'):
        return _cadena(r, j)
    k = j
    while j < len(r) and r[j] not in ",[]{}" and not (r[j] == ":" and r[j + 1:j + 2] in _FIN_DE_CLAVE):
        j += 1
    v = r[k:j].rstrip()
    if not v or v.startswith(_NO_SOPORTADO + ("#",)) or v == "-" or v.startswith(("- ", "-\t")):
        raise ValueError(f"elemento vacío o no soportado en una lista o un mapa en línea: {r!r}")
    if r[j:j + 1] in ("[", "{"):  # p. ej. ${{ … }} sin comillas: dentro de [ ] o { } va entre comillas
        raise ValueError(f"[ o {{ dentro de un escalar sin comillas en una lista o un mapa en línea: {r!r}")
    if r[j:j + 1] == ":" and not clave:  # [a: b] o {a: b: c}: un mapa implícito
        raise ValueError(f"«: » dentro de un valor de una lista o un mapa en línea (no soportado): {r!r}")
    return v, j


def _flujo(r, j):
    """(valor, posición tras el cierre) de la lista `[…]` o el mapa `{…}` que empieza en r[j]."""
    cierre = "]" if r[j] == "[" else "}"
    out = [] if cierre == "]" else {}
    j += 1
    while True:
        j = _blancos(r, j)
        if j >= len(r):
            raise ValueError(f"lista o mapa en línea sin cerrar: {r!r}")
        if r[j] == cierre:  # también tras una coma final: [a, b,]
            return out, j + 1
        if cierre == "]":
            v, j = _elemento(r, j)
            out.append(v)
        else:
            k, j = _elemento(r, j, clave=True)
            if k in out:
                raise ValueError(f"clave repetida {k!r} en un mapa en línea: {r!r}")
            j = _blancos(r, j)
            if r[j:j + 1] != ":":
                raise ValueError(f"clave sin «:» en un mapa en línea: {r!r}")
            j = _blancos(r, j + 1)
            if r[j:j + 1] in (",", "}"):
                out[k] = None
            else:
                out[k], j = _elemento(r, j)
        j = _blancos(r, j)
        if r[j:j + 1] == ",":
            j += 1
        elif r[j:j + 1] != cierre:
            raise ValueError(f"lista o mapa en línea mal formado: {r!r}")


def _en_linea(r):
    """Escalar, lista `[…]` o mapa `{…}` escritos tras su clave o su guion (sin el comentario final). Una lista o un
    mapa que siguen en otras líneas llegan ya unidos en una (_Lector.flujo)."""
    r = r.strip()
    if r[:1] in ("'", '"'):
        v, j = _cadena(r, 0)
        _nada_mas(r, j)
        return v
    if r[:1] in ("[", "{"):
        v, j = _flujo(r, 0)
        _nada_mas(r, j)
        return v
    if r.startswith(_NO_SOPORTADO):
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
        """Lo que empieza en la línea actual, con sangría n: una lista, un mapa, o `[…]`/`{…}` en su propia línea."""
        s = self.lineas[self.i].strip()
        if s[:1] in ("[", "{"):
            self.i += 1
            return _en_linea(self.flujo(s))
        return self.lista(n) if self.guion() else self.mapa(n)

    def flujo(self, texto):
        """La lista `[…]` o el mapa `{…}` que empieza en `texto` (lo que sigue a su clave o a su guion, cuya línea ya se
        ha leído) con las líneas siguientes que hagan falta para cerrarlo, sin comentarios y unidas con un espacio."""
        partes, prof, primera = [], 0, self.i
        while True:
            parte, d = _sin_comentario(texto)
            partes.append(parte.strip())
            prof += d
            if prof <= 0:
                return " ".join(partes)
            if self.i >= len(self.lineas):
                raise ValueError(f"lista o mapa en línea sin cerrar (desde la línea {primera}): {partes[0]!r}")
            texto = self.lineas[self.i]
            self.i += 1

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
                out.append(_en_linea(self.flujo(contenido) if contenido[:1] in ("[", "{") else contenido))
        return out

    def valor(self, resto, n):
        indicador = re.sub(r"\s#.*", "", resto).strip()  # «run: |  # comentario» también es un bloque
        if indicador in ("|", "|-", ">", ">-"):
            return self.bloque(indicador, n)
        if resto[:1] in ("[", "{"):
            return _en_linea(self.flujo(resto))
        if resto and not resto.startswith("#"):
            return _en_linea(resto)
        sig = self.sangria()  # sin valor en la línea: un nodo más sangrado, una lista a la misma altura o nada
        if sig > n:
            return self.nodo(sig)
        if sig == n and self.guion():
            return self.lista(n)
        return None

    def bloque(self, indicador, n):
        """Bloque `|` (literal) o `>` (plegado: dos líneas seguidas se unen con un espacio, y cada línea vacía entre
        ellas queda como un salto); con `-`, sin el salto final."""
        lineas, primera = [], self.i
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
        for k, x in enumerate(lineas):
            if x.strip() and len(x) - len(x.lstrip(" ")) < b:  # YAML no lo admite; cortarla perdería texto
                raise ValueError(f"línea {primera + k + 1} menos sangrada que la primera del bloque")
        filas = [x[b:] if x.strip() else "" for x in lineas]
        while filas and not filas[-1]:
            filas.pop()
        texto = "\n".join(filas)
        if indicador[0] == ">":
            if any(f[:1] in (" ", "\t") for f in filas):
                raise ValueError(f"bloque > con líneas más sangradas (no soportado, desde la línea {primera + 1})")
            cuerpo = texto.lstrip("\n")  # los saltos de las líneas vacías del principio se quedan como están
            texto = texto[:len(texto) - len(cuerpo)] + re.sub(
                r"\n+", lambda m: " " if len(m.group()) == 1 else "\n" * (len(m.group()) - 1), cuerpo)
        return texto + ("" if indicador.endswith("-") else "\n")


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


_TERMINO = re.compile(r"(!?)([A-Za-z_][\w.-]*)(?:\s*(==|!=)\s*'((?:[^']|'')*)')?")


def evaluar_condicion(expr, ctx):
    """Valor de una expresión `${{ … }}` de Actions hecha solo de términos unidos con && (`contexto == 'literal'`,
    `contexto != 'literal'` o `!contexto`), con los valores de `ctx`. Da ValueError con lo demás (||, paréntesis,
    funciones), para que un cambio de forma haga fallar el test en vez de evaluarse mal, y KeyError con un contexto que
    `ctx` no trae. Como Actions, compara las cadenas sin distinguir mayúsculas."""
    m = re.fullmatch(r"\$\{\{(.*)\}\}", expr.strip())
    if not m:
        raise ValueError(f"no es una expresión ${{{{ … }}}}: {expr!r}")
    valores = []
    for termino in m.group(1).split("&&"):
        t = _TERMINO.fullmatch(termino.strip())
        if not t or (t.group(1) and t.group(3)):
            raise ValueError(f"término no soportado: {termino.strip()!r}")
        neg, nombre, op, literal = t.groups()
        v = ctx[nombre]  # todos, aunque uno anterior ya sea falso: un contexto que falta en el test se ve siempre
        if op:
            iguales = str(v).lower() == literal.replace("''", "'").lower()
            valores.append(iguales if op == "==" else not iguales)
        else:
            valores.append(not v if neg else bool(v))
    return all(valores)


# Contexto de Actions de un push a main del repo propio, hecho por una persona; los demás casos cambian una clave.
_PUSH_MAIN = {"github.event_name": "push", "github.ref": "refs/heads/main", "github.event.repository.fork": False,
              "github.actor": "octocat"}
_PUSH_RAMA = {**_PUSH_MAIN, "github.ref": "refs/heads/fix-seguridad"}


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

    def test_estilo_flujo(self):
        """Un workflow de ejemplo en estilo flujo: el `matrix.include` de un diagnóstico con un mapa en línea por
        variante, listas y mapas anidados, en una línea o en varias (con comentarios dentro), y bloques plegados."""
        texto = "\n".join((
            "name: diag",
            "on:",
            "  push: {branches: [main, 'rama-x'], paths: []}",
            "  workflow_dispatch: {}",
            "jobs:",
            "  diag:",
            "    strategy:",
            "      fail-fast: false",
            "      matrix:",
            "        include:",
            "          # una variante por línea",
            '          - {nombre: a, ref: 5c55e54, modo: ventana, extra: "--lanzar shell --previo", url: https://x/y}',
            "          - {nombre: b, ref: 'HEAD', extra: '', nada: , lista: [1, [2, 3], {c: d}],}",
            "          - {nombre: 'c # no es un comentario', ref: it's}  # esto sí",
            "    runs-on: windows-latest",
            "    timeout-minutes: 25",
            "    env: {",
            "      A: '1',  # comentario dentro",
            '      B: "2"',
            "      }",
            "    steps:",
            "      - [uno,",
            "         dos,",
            "        ]",
            "      - run: >",
            "          python diag.py",
            "          --seconds 180",
            "",
            "          fin",
            "        shell: >-  # sin el salto final",
            "          bash",
            "      -",
            "        {uses: a@b}",
            "  otro:",
            "    timeout-minutes: 5",
            "    needs:",
            "      [diag]",
            ""))
        self.assertEqual(cargar_yaml(texto), {
            "name": "diag",
            "on": {"push": {"branches": ["main", "rama-x"], "paths": []}, "workflow_dispatch": {}},
            "jobs": {
                "diag": {
                    "strategy": {"fail-fast": "false", "matrix": {"include": [
                        {"nombre": "a", "ref": "5c55e54", "modo": "ventana", "extra": "--lanzar shell --previo",
                         "url": "https://x/y"},
                        {"nombre": "b", "ref": "HEAD", "extra": "", "nada": None,
                         "lista": ["1", ["2", "3"], {"c": "d"}]},
                        {"nombre": "c # no es un comentario", "ref": "it's"}]}},
                    "runs-on": "windows-latest", "timeout-minutes": "25", "env": {"A": "1", "B": "2"},
                    "steps": [["uno", "dos"], {"run": "python diag.py --seconds 180\nfin\n", "shell": "bash"},
                              {"uses": "a@b"}]},
                "otro": {"timeout-minutes": "5", "needs": ["diag"]}}})

    def test_bloques(self):
        """`|` guarda los saltos y `>` los pliega (una línea vacía, un salto); con `-`, sin el salto final; las líneas
        vacías del principio se quedan."""
        for texto, esperado in (("a: |\n  uno\n  dos\n", "uno\ndos\n"), ("a: |-\n  uno\n    dos\n", "uno\n  dos"),
                                ("a: >\n  uno\n  dos\n\n\n  tres\n", "uno dos\n\ntres\n"),
                                ("a: >-\n\n  uno\n  dos\n\nb: 1\n", "\nuno dos"), ("a: >\nb: 1\n", "")):
            with self.subTest(texto):
                self.assertEqual(cargar_yaml(texto)["a"], esperado)

    def test_lo_que_no_sabe_leer_falla(self):
        for texto in ("a: 1\na: 2", "a: &x 1", "a: *x", "a: !!str 1", "a:\n\tb: 1", "a: 'b", 'a: "\\n"', "a: |+\n  b",
                      "a: >\n  b\n    c",  # plegado con una línea más sangrada
                      "a: |\n    b\n   c",  # una línea menos sangrada que la primera del bloque
                      "a: {b}", "a: {b: 1, b: 2}", "a: [b: c]", "a: {b: c: d}", "a: {[b]: c}", "a: [b,,c]",
                      "a: [x${{ y }}]", "a: {b: 1} c", "a: [- b]", "a: [b, [c]", "a: [b,\n  c", "a: [b\n  # c]",
                      "a: [ -f x ] && echo"):
            with self.subTest(texto), self.assertRaises(ValueError):
                cargar_yaml(texto)

    def test_lee_todos_los_workflows(self):
        nombres = sorted(n for n in os.listdir(WORKFLOWS) if n.endswith((".yml", ".yaml")))
        self.assertIn("tests.yml", nombres)
        for nombre in nombres:
            with self.subTest(nombre):
                try:
                    wf = workflow(nombre)
                except ValueError as e:  # GitHub lo leería: hay que ampliar el lector, no saltarse el fichero
                    self.fail(f"cargar_yaml (tests/test_ci.py) no sabe leer {nombre}: {e}")
                self.assertTrue(wf["name"] and wf["on"] and wf["jobs"])
                for job in wf["jobs"].values():  # sin tope, un cuelgue gasta los 360 min por defecto del runner
                    self.assertGreater(int(job["timeout-minutes"]), 0)


class EvaluarCondicionTests(unittest.TestCase):
    def test_subconjunto(self):
        ctx = {"a": "push", "b": False, "c": "X", "d.e": "dependabot[bot]"}
        self.assertIs(evaluar_condicion("${{ a == 'PUSH' && !b && c != 'y' }}", ctx), True)  # sin mayúsculas
        self.assertIs(evaluar_condicion("${{ a == 'push' && b }}", ctx), False)
        self.assertIs(evaluar_condicion("${{ c != 'x' }}", ctx), False)
        self.assertIs(evaluar_condicion("${{ d.e != 'dependabot[bot]' }}", ctx), False)

    def test_lo_que_no_sabe_evaluar_falla(self):
        for expr in ("a == 'push'", "${{ a == 'push' || b }}", "${{ (a == 'push') }}", "${{ !a == 'push' }}",
                     "${{ startsWith(a, 'p') }}", "${{ a == push }}"):
            with self.subTest(expr), self.assertRaises(ValueError):
                evaluar_condicion(expr, {"a": "push", "b": True})
        with self.assertRaises(KeyError):  # también tras un término falso: el test tiene que dar todos los contextos
            evaluar_condicion("${{ a == 'otro' && z }}", {"a": "push"})


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


class AgnosticismoCITests(unittest.TestCase):
    """P2-39: el test de agnosticismo corre en la CI con la lista privada (el secreto NOTAS_PALABRAS_VETADAS) y, si
    falta en un push a main, falla en vez de saltarse; en las demás ramas, sin el secreto, se salta con un aviso."""

    def paso(self):
        """(job, paso) de tests.yml que recibe la lista; tiene que haber exactamente uno."""
        pares = [(job, p) for job in workflow("tests.yml")["jobs"].values() for p in job["steps"]
                 if "NOTAS_PALABRAS_VETADAS" in (p.get("env") or {})]
        self.assertEqual(len(pares), 1)
        return pares[0]

    def test_agnosticismo_con_el_secreto_solo_en_su_paso(self):
        """La lista privada llega del secreto de Actions solo al paso que ejecuta el test de agnosticismo."""
        wf = workflow("tests.yml")
        self.assertNotIn("env", wf)
        for job in wf["jobs"].values():
            self.assertNotIn("NOTAS_PALABRAS_VETADAS", job.get("env") or {})
        _, paso = self.paso()
        self.assertEqual(paso["env"]["NOTAS_PALABRAS_VETADAS"], "${{ secrets.NOTAS_PALABRAS_VETADAS }}")
        self.assertIn("test_package.TemplatesTests.test_sin_rastro_de_clientes", paso["run"])
        self.assertEqual(paso.get("working-directory"), "tests")

    def test_agnosticismo_obligatorio_solo_en_los_push_a_main(self):
        """Sin el secreto, el paso solo falla en los push a main del repo propio: ahí, si falta, o se borra o se vacía
        al rotarlo, el test falla en vez de saltarse en verde. En las demás ramas (también las que se rebasan sobre
        main mientras el secreto no existe), en los pull_request, en los forks y con Dependabot, que no recibe los
        secretos de Actions, se salta con un aviso: un rojo en cada push de cada rama acostumbraría a ignorar la CI."""
        self.assertIn("push", eventos(workflow("tests.yml")["on"]))
        job, paso = self.paso()
        exigir = paso["env"].get("NOTAS_EXIGIR_VETADAS", "")
        for caso, ctx, esperado in (
                ("push a main", _PUSH_MAIN, True),
                ("push a otra rama, también una rebasada sobre main", _PUSH_RAMA, False),
                ("pull request", {**_PUSH_MAIN, "github.event_name": "pull_request", "github.ref": "refs/pull/7/merge"},
                 False),
                ("push a main de un fork", {**_PUSH_MAIN, "github.event.repository.fork": True}, False),
                ("push de Dependabot", {**_PUSH_MAIN, "github.actor": "dependabot[bot]"}, False)):
            with self.subTest(caso):
                self.assertIs(evaluar_condicion(exigir, ctx), esperado)
        for d in (job, paso):  # nada que lo deje en verde aunque falle ni que lo salte en un push
            self.assertNotIn("continue-on-error", d)
            self.assertNotIn("if", d)
        run = paso["run"]
        self.assertIn('[ "$NOTAS_EXIGIR_VETADAS" = "true" ]', run)  # el ::error, solo donde se exige
        self.assertIn("::error title=Agnosticismo::", run)
        self.assertIn("::warning title=Agnosticismo::", run)  # donde no se exige, el salto se ve en el resumen del run
        self.assertNotIn("|| true", run)
        self.assertNotIn("exit 0", run)
        self.assertTrue(run.rstrip().endswith("test_package.TemplatesTests.test_sin_rastro_de_clientes"))

    @unittest.skipIf(os.name == "nt" or not shutil.which("bash"), "el paso corre con bash, en ubuntu-latest")
    def test_paso_de_agnosticismo_en_bash(self):
        """El `run` del paso, con `bash -e` como en el runner y NOTAS_EXIGIR_VETADAS calculada para cada caso: sin el
        secreto, rojo en un push a main y verde con aviso en otra rama; con él, el test se ejecuta (no se salta) en las
        dos y pasa si nada casa."""
        _, paso = self.paso()
        exigir = paso["env"]["NOTAS_EXIGIR_VETADAS"]
        tmp = tempfile.mkdtemp(prefix="chuleta-ci-")
        self.addCleanup(shutil.rmtree, tmp, True)
        python = os.path.join(tmp, "python")  # el `python` del runner: un script y no un enlace, para no salir del venv
        with open(python, "w", encoding="utf-8") as f:
            f.write(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
        os.chmod(python, 0o755)
        script = os.path.join(tmp, "paso.sh")
        with open(script, "w", encoding="utf-8") as f:
            f.write(paso["run"])
        base = {k: v for k, v in os.environ.items() if not k.startswith(("NOTAS_", "GIT_"))}
        base["PATH"] = tmp + os.pathsep + base.get("PATH", "")
        nada = "(?!)"  # una lista que no casa con nada, tampoco con este fichero
        for caso, ctx, lista, esperado in (("push a main sin el secreto", _PUSH_MAIN, "", "rojo"),
                                           ("push a otra rama sin el secreto", _PUSH_RAMA, "", "aviso"),
                                           ("push a main con el secreto", _PUSH_MAIN, nada, "revisado"),
                                           ("push a otra rama con el secreto", _PUSH_RAMA, nada, "revisado")):
            with self.subTest(caso):
                env = dict(base, NOTAS_PALABRAS_VETADAS=lista,
                           NOTAS_EXIGIR_VETADAS="true" if evaluar_condicion(exigir, ctx) else "false")
                r = subprocess.run(["bash", "-e", script], cwd=os.path.join(REPO, "tests"), env=env,
                                   capture_output=True, encoding="utf-8", errors="replace", timeout=300)
                salida = (r.stdout + r.stderr).replace("::", ": :")  # que un fallo no deje anotaciones en el run
                if esperado == "rojo":
                    self.assertNotEqual(r.returncode, 0, salida)
                    self.assertIn("::error title=Agnosticismo::", r.stdout, salida)
                    continue
                self.assertEqual(r.returncode, 0, salida)
                self.assertNotIn("::error", r.stdout, salida)
                self.assertEqual("::warning title=Agnosticismo::" in r.stdout, esperado == "aviso", salida)
                self.assertRegex(r.stderr, r"\.\.\. skipped " if esperado == "aviso" else r"(?m)\.\.\. ok$", salida)


if __name__ == "__main__":
    unittest.main()
