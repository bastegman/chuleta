# -*- coding: utf-8 -*-
"""Los workflows de GitHub Actions (.github/workflows): qué se lanza dónde y qué comprueba la CI en Windows.

PyYAML no está en requirements-dev: `cargar_yaml` lee el subconjunto de YAML que usan estos ficheros (mapas y listas en
bloque, con claves sin comillas de letras, cifras y `_.-`; listas `[a, b]` y mapas `{a: b}` en línea, anidados y en una
o varias líneas; escalares con o sin comillas; bloques `|`, `|-`, `>` y `>-`, y comentarios) y da ValueError con lo
demás (anclas, alias, etiquetas, claves entre comillas o con otros caracteres, también en un elemento de una lista,
escapes en comillas dobles, escalares en varias líneas…) y con lo que YAML no admite (`a: b: c`, `a: - b`, tabuladores
fuera de comillas y de comentarios…), para que un workflow escrito de otra forma haga fallar el test en vez de leerse
mal. Los escalares quedan como cadenas: `timeout-minutes: 30` da «30»."""
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
_CLAVE = re.compile(r"([A-Za-z0-9_.-]+) *:(?: +(.*))?$")  # con espacios: un tabulador ahí no es YAML
_NO_SOPORTADO = tuple("&*!|>%@`?")  # al principio de un valor: anclas, alias, etiquetas, bloques raros, claves «?»
_FIN_DE_CLAVE = ("", " ", "\n", ",", "[", "]", "{", "}")  # lo que puede seguir al «:» que cierra una clave sin comillas


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
    """Tras un valor, solo espacios y el comentario (un tabulador tampoco: YAML no lo admite)."""
    j = _blancos(r, j)
    if j < len(r) and r[j] != "#":
        raise ValueError(f"texto de más tras el valor: {r!r}")


def _blancos(r, j, saltos=False):
    """Salta los espacios y, con `saltos`, los \\n que unen las líneas de una lista o un mapa en línea
    (_Lector.flujo)."""
    while r[j:j + 1] == " " or (saltos and r[j:j + 1] == "\n"):
        j += 1
    return j


def _sin_comentario(linea):
    """(la línea sin su comentario final, cuántos `[` y `{` abre menos los `]` y `}` que cierra), sin contar lo que va
    entre comillas. Una comilla solo abre una cadena donde puede empezar un valor (al principio de la línea o tras `[`,
    `{`, `,` o `:`, con o sin blancos en medio); en `it's` o en `a '` es un carácter más. Un tabulador fuera de comillas
    y del comentario da ValueError: YAML no lo admite."""
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
        elif c in ("'", '"') and linea[:i].rstrip(" ")[-1:] in ("", "[", "{", ",", ":"):
            q = c
        elif c == "\t":
            raise ValueError(f"tabulador fuera de comillas (YAML no lo admite): {linea!r}")
        elif c == "#" and (i == 0 or linea[i - 1] == " "):
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
    # sin comillas, un escalar acaba en su línea (YAML lo plegaría con la de debajo: no soportado) y en un «?» (PyYAML
    # lo lee como una clave «?»)
    while j < len(r) and r[j] not in ",[]{}?\n" and not (r[j] == ":" and r[j + 1:j + 2] in _FIN_DE_CLAVE):
        j += 1
    v = r[k:j].rstrip(" ")
    if r[j:j + 1] == "\n" and r[_blancos(r, j, saltos=True):][:1] not in (",", "]", "}"):
        raise ValueError(f"escalar sin comillas en varias líneas en una lista o un mapa en línea (no soportado): {r!r}")
    if not v or v.startswith(_NO_SOPORTADO + ("#", ":")) or v == "-" or v.startswith(("- ", "-\t")):
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
        j = _blancos(r, j, saltos=True)
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
            j = _blancos(r, j)  # el «:» de la clave, en su misma línea
            if r[j:j + 1] != ":":
                raise ValueError(f"clave sin «:» en un mapa en línea: {r!r}")
            j = _blancos(r, j + 1, saltos=True)
            if r[j:j + 1] in (",", "}"):
                out[k] = None
            else:
                out[k], j = _elemento(r, j)
        j = _blancos(r, j, saltos=True)
        if r[j:j + 1] == ",":
            j += 1
        elif r[j:j + 1] != cierre:
            raise ValueError(f"lista o mapa en línea mal formado: {r!r}")


def _en_linea(r):
    """Escalar, lista `[…]` o mapa `{…}` escritos tras su clave o su guion (sin el comentario final). Una lista o un
    mapa que siguen en otras líneas llegan ya unidos en una (_Lector.flujo). Sin comillas, YAML no admite tabuladores,
    «: » ni «:» al final (sería un mapa: `- a b: c` es {"a b": "c"}, y `a: b: c`, un error), ni «- », «,», «]» o «}»
    al principio."""
    r = r.strip(" ")
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
    m = re.search(" #", r)
    v = (r[:m.start()] if m else r).rstrip(" ")
    if "\t" in v or re.search(r":( |$)", v) or v == "-" or v.startswith(("- ", ",", "]", "}")):
        raise ValueError(f"escalar sin comillas que YAML no admite o que no es un escalar: {r!r}")
    return v


class _Lector:
    def __init__(self, texto):
        texto = texto.replace("\r\n", "\n")
        if re.search("[\r\x85\u2028\u2029]", texto):  # YAML también los cuenta como saltos de línea
            raise ValueError("salto de línea que no es \\n ni \\r\\n (\\r suelto, NEL, U+2028 o U+2029)")
        self.lineas = texto.split("\n")
        self.i = 0

    def sangria(self):
        """Sangría de la siguiente línea con contenido (salta vacías y comentarios), o -1 al final. Un tabulador en la
        sangría, también en la de una línea en blanco o la de un comentario, da ValueError: YAML no lo admite."""
        while self.i < len(self.lineas):
            linea = self.lineas[self.i]
            resto = linea.lstrip(" ")
            if resto[:1] == "\t":
                raise ValueError(f"tabulador en la sangría (línea {self.i + 1})")
            if resto and not resto.startswith("#"):
                return len(linea) - len(resto)
            self.i += 1
        return -1

    def guion(self):
        s = self.lineas[self.i].strip(" ")
        return s == "-" or s.startswith("- ")

    def nodo(self, n):
        """Lo que empieza en la línea actual, con sangría n: una lista, un mapa, o `[…]`/`{…}` en su propia línea."""
        s = self.lineas[self.i].strip(" ")
        if s[:1] in ("[", "{"):
            self.i += 1
            return _en_linea(self.flujo(s))
        return self.lista(n) if self.guion() else self.mapa(n)

    def flujo(self, texto):
        """La lista `[…]` o el mapa `{…}` que empieza en `texto` (lo que sigue a su clave o a su guion, cuya línea ya se
        ha leído) con las líneas siguientes que hagan falta para cerrarlo, sin comentarios y unidas con un \\n."""
        partes, prof, primera = [], 0, self.i
        while True:
            parte, d = _sin_comentario(texto)
            partes.append(parte.strip(" "))
            prof += d
            if prof <= 0:
                return "\n".join(partes)
            if self.i >= len(self.lineas):
                raise ValueError(f"lista o mapa en línea sin cerrar (desde la línea {primera}): {partes[0]!r}")
            texto = self.lineas[self.i]
            self.i += 1

    def mapa(self, n):
        out = {}
        while self.sangria() == n and not self.guion():
            m = _CLAVE.match(self.lineas[self.i].strip(" "))
            if not m:
                raise ValueError(f"no sé leer la línea {self.i + 1}: {self.lineas[self.i].strip()!r}")
            if m.group(1) in out:
                raise ValueError(f"clave repetida {m.group(1)!r} (línea {self.i + 1})")
            self.i += 1
            out[m.group(1)] = self.valor(m.group(2) or "", n)
        return out

    def lista(self, n):
        out = []
        while self.sangria() == n and self.guion():
            linea = self.lineas[self.i]
            tras_guion = linea.strip(" ")[1:]
            contenido = tras_guion.lstrip(" ")
            if not contenido or contenido.startswith("#"):
                # «-» solo o con un comentario: el elemento sigue en las líneas de debajo, más sangrado
                self.i += 1
                sig = self.sangria()
                out.append(self.nodo(sig) if sig > n else None)
            elif _CLAVE.match(contenido) or contenido == "-" or contenido.startswith("- "):
                # «- clave: valor» o «- - x»: un mapa o una lista que empiezan en la misma línea, a la altura de lo que
                # sigue al guion. Una clave que no es de _CLAVE («- a b: c») acaba en _en_linea, que da ValueError
                # como mapa() con la misma clave fuera de una lista
                col = len(linea) - len(linea.lstrip(" ")) + 1 + len(tras_guion) - len(contenido)
                self.lineas[self.i] = " " * col + contenido
                out.append(self.mapa(col) if _CLAVE.match(contenido) else self.lista(col))
            else:
                self.i += 1
                out.append(_en_linea(self.flujo(contenido) if contenido[:1] in ("[", "{") else contenido))
        return out

    def valor(self, resto, n):
        indicador = re.sub(" #.*", "", resto).rstrip(" ")  # «run: |  # comentario» también es un bloque
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
        ellas queda como un salto); con `-`, sin el salto final, que tampoco hay si el bloque acaba el fichero sin él.
        Como en YAML, la sangría del bloque es la de su primera línea con algo más que espacios (un tabulador cuenta), y
        una línea con solo espacios queda vacía si no pasa de esa sangría y, si pasa, guarda los espacios de más."""
        lineas, primera = [], self.i
        while self.i < len(self.lineas):
            linea = self.lineas[self.i]
            if linea.strip(" ") and len(linea) - len(linea.lstrip(" ")) <= n:
                break
            lineas.append(linea)
            self.i += 1
        sangrias = [len(x) - len(x.lstrip(" ")) for x in lineas]
        llenas = [k for k, x in enumerate(lineas) if x.strip(" ")]
        if not llenas:
            return ""
        b = sangrias[llenas[0]]
        for k, x in enumerate(lineas):
            if x.strip(" ") and sangrias[k] < b:  # YAML no lo admite; cortarla perdería texto
                raise ValueError(f"línea {primera + k + 1} menos sangrada que la primera del bloque")
            if k < llenas[0] and sangrias[k] > b:
                raise ValueError(f"línea en blanco {primera + k + 1} con más espacios que la primera del bloque")
        filas = [x[b:] for x in lineas]
        while filas and not filas[-1]:
            filas.pop()
        sin_salto = indicador.endswith("-") or primera + len(filas) == len(self.lineas)  # el fichero acaba sin \n
        texto = "\n".join(filas)
        if indicador[0] == ">":
            if any(f[:1] in (" ", "\t") for f in filas):
                raise ValueError(f"bloque > con líneas más sangradas (no soportado, desde la línea {primera + 1})")
            cuerpo = texto.lstrip("\n")  # los saltos de las líneas vacías del principio se quedan como están
            texto = texto[:len(texto) - len(cuerpo)] + re.sub(
                r"\n+", lambda m: " " if len(m.group()) == 1 else "\n" * (len(m.group()) - 1), cuerpo)
        return texto + ("" if sin_salto else "\n")


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
            "          - {nombre: 'c # no es un comentario', ref: it's, cita: a 'b}  # esto sí",
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
                        {"nombre": "c # no es un comentario", "ref": "it's", "cita": "a 'b"}]}},
                    "runs-on": "windows-latest", "timeout-minutes": "25", "env": {"A": "1", "B": "2"},
                    "steps": [["uno", "dos"], {"run": "python diag.py --seconds 180\nfin\n", "shell": "bash"},
                              {"uses": "a@b"}]},
                "otro": {"timeout-minutes": "5", "needs": ["diag"]}}})

    def test_listas_compactas(self):
        """`- - x`: una lista dentro de otra que empieza en la misma línea que el guion de fuera (antes se leía como la
        cadena «- x»)."""
        self.assertEqual(cargar_yaml("a:\n- - x\n"), {"a": [["x"]]})
        texto = "a:\n- - x\n  - y\n- - - z\n  - {b: c}\n- -\n  - e\n- - k: 1\n    l: 2\n"
        esperado = {"a": [["x", "y"], [["z"], {"b": "c"}], [None, "e"], [{"k": "1", "l": "2"}]]}
        self.assertEqual(cargar_yaml(texto), esperado)

    def test_bloques(self):
        """`|` guarda los saltos y `>` los pliega (una línea vacía, un salto); con `-`, sin el salto final; las líneas
        vacías del principio se quedan."""
        for texto, esperado in (("a: |\n  uno\n  dos\n", "uno\ndos\n"), ("a: |-\n  uno\n    dos\n", "uno\n  dos"),
                                ("a: >\n  uno\n  dos\n\n\n  tres\n", "uno dos\n\ntres\n"),
                                ("a: >-\n\n  uno\n  dos\n\nb: 1\n", "\nuno dos"), ("a: >\nb: 1\n", "")):
            with self.subTest(texto):
                self.assertEqual(cargar_yaml(texto)["a"], esperado)

    def test_bloques_con_lineas_de_espacios(self):
        """Como en YAML (y en PyYAML): una línea de un bloque con solo espacios, más que la sangría del bloque, guarda
        los que le sobran (antes quedaba vacía), y un tabulador tras la sangría es texto; si el fichero acaba en el
        bloque sin \\n, el bloque tampoco lo lleva. Una línea en blanco del principio con más espacios que la primera
        con texto, o un tabulador dentro de la sangría, no es YAML válido; y en un bloque > no está soportado."""
        for texto, esperado in (("a: |\n  uno\n      \n  dos\n", "uno\n    \ndos\n"),
                                ("a: |\n  uno\n      \nb: 1\n", "uno\n    \n"), ("a: |-\n  uno\n      \n", "uno\n    "),
                                ("a: |\n  uno\n  \t\n  dos\n", "uno\n\t\ndos\n"), ("a: |\n  \n  uno\n", "\nuno\n"),
                                ("a: |\n  uno", "uno"), ("a: >\n  uno\n  dos", "uno dos"),
                                ("a: |\n  uno\n  ", "uno\n")):
            with self.subTest(texto):
                self.assertEqual(cargar_yaml(texto)["a"], esperado)
        for texto in ("a: |\n   \n  uno\n", "a: |\n  uno\n \t\n  dos\n", "a: |\n  uno\n\t\nb: 1\n",
                      "a: >\n  uno\n      \n  dos\n"):
            with self.subTest(texto), self.assertRaises(ValueError):
                cargar_yaml(texto)

    def test_claves_en_una_lista_como_en_un_mapa(self):
        """Una clave que el lector no admite (con espacios, «/», `${{ }}`, «#»…) da ValueError tanto en un mapa como en
        un elemento de una lista. Antes, en la lista, `- a b: c` se leía como la cadena «a b: c», y PyYAML da
        {"a b": "c"}. Tras « #» empieza un comentario, también en un elemento."""
        for texto in ("a b: c", "a:\n- a b: c", "a:\n- x/y: c", "steps:\n  - ${{ x }}: c", "- - a b: c", "- a#b: c"):
            with self.subTest(texto), self.assertRaises(ValueError):
                cargar_yaml(texto)
        self.assertEqual(cargar_yaml("- a #b: c\n- a: b #c: d\n"), ["a", {"a": "b"}])

    def test_guion_con_comentario(self):
        """`- # comentario` es como `-` solo: el elemento sigue en las líneas de debajo, o está vacío (antes era la
        cadena «# comentario»)."""
        self.assertEqual(cargar_yaml("a:\n- # c\n- y\n"), {"a": [None, "y"]})
        self.assertEqual(cargar_yaml("a:\n  - # c\n    k: v\n  - - # d\n    - z\n"), {"a": [{"k": "v"}, [None, "z"]]})

    def test_lo_que_yaml_no_admite_falla(self):
        """Lo que PyYAML rechaza da ValueError en vez de leerse como otra cosa: «: » o «- » dentro de un valor sin
        comillas, «,» o «]» al principio, un tabulador fuera de comillas y de comentarios (también en una línea en
        blanco o antes de un comentario), un salto de línea que no es \\n ni \\r\\n, y «:x» o «?» en [ ] o { }."""
        for texto in ("a: b: c", "- a: b: c", "a: b:", "a: - b", "a: -", "a: ,b", "a: ]", "a:\tb", "a\t: b",
                      "a: b\t# c", "a: 'b'\t# c", "a: b\t", "- a\t", "a: [b,\tc]", "a: 1\n\t\nb: 2",
                      "a: 1\n\t# c\nb: 2", "a: |\t\n  x", "a: b\rc: d", "a: b\u2028c", "a: [x, :y]", "a: [b?c]"):
            with self.subTest(texto), self.assertRaises(ValueError):
                cargar_yaml(texto)

    def test_lo_que_no_sabe_leer_falla(self):
        for texto in ("a: 1\na: 2", "a: &x 1", "a: *x", "a: !!str 1", "a:\n\tb: 1", "a: 'b", 'a: "\\n"', "a: |+\n  b",
                      "a: >\n  b\n    c",  # plegado con una línea más sangrada
                      "a: |\n    b\n   c",  # una línea menos sangrada que la primera del bloque
                      "a: {b}", "a: {b: 1, b: 2}", "a: [b: c]", "a: {b: c: d}", "a: {[b]: c}", "a: [b,,c]",
                      "a: [x${{ y }}]", "a: {b: 1} c", "a: [- b]", "a: [b, [c]", "a: [b,\n  c", "a: [b\n  # c]",
                      "a: [ -f x ] && echo",
                      # un escalar sin comillas partido en dos líneas dentro de [ ] o { }: YAML lo pliega (antes se unía
                      # con un espacio, mal si había una línea vacía o un comentario en medio)
                      "a: [u\n\n  dos]", "a: {b: c\n  d}", "a: [u\n  # c\n  dos]"):
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


class WorkflowsWindowsTests(unittest.TestCase):
    """P2-38: lo que solo se puede probar en Windows corre también al llegar a main, no solo en ramas ya fusionadas."""

    def test_e2e_y_escritorio_en_main_y_a_mano(self):
        for nombre, rutas in (("lookfeel-e2e.yml", ("_app/**", "INSTALAR.bat", "ABRIR CHULETA.bat", "requirements.txt",
                                                    "scripts/build_zip.py", "scripts/e2e/**",
                                                    "scripts/lookfeel_spike.py")),  # lo importa scripts/e2e/ventana.py
                              ("reunion-windows.yml", ("_app/**", "tests/**", "requirements.txt")),
                              # fix-windows: la suite en Windows con el formato regional español y la ventana real
                              ("windows-real.yml", ("_app/**", "tests/**", "requirements.txt"))):
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


_AVISO = "::warning title=Agnosticismo sin ejecutar::"
_EN_LOCAL = 'NOTAS_PALABRAS_VETADAS="$(cat ~/chuleta-vetadas.txt)" .venv/bin/python tests/run.py'


class WorkflowsDependenciasTests(unittest.TestCase):
    def test_quien_ejecuta_la_suite_instala_numpy_y_requests(self):
        """La suite necesita numpy y requests, que es lo que instala tests.yml. Un workflow que la ejecuta sin
        requests da un error en test_directo_auditoria (resumen con Azure) y un fallo en el diagnóstico (las
        excepciones de requests). Pasó con reunion-windows, que desde P2-38 corre también con los push a main."""
        vistos = []
        for nombre in sorted(n for n in os.listdir(WORKFLOWS) if n.endswith((".yml", ".yaml"))):
            for jname, job in workflow(nombre)["jobs"].items():
                runs = [str(p.get("run") or "") for p in job.get("steps") or []]
                i = next((k for k, r in enumerate(runs) if "tests/run.py" in r), None)
                if i is None:
                    continue
                vistos.append(nombre)
                with self.subTest(f"{nombre}: {jname}"):
                    instala = " ".join(r for r in runs[:i] if "pip install" in r)
                    for paquete in ("numpy", "requests"):
                        self.assertRegex(instala, rf"(?<![\w.-]){paquete}(?![\w.-])", paquete)
        self.assertIn("tests.yml", vistos)


class AgnosticismoCITests(unittest.TestCase):
    """P2-39: el test de agnosticismo corre en la CI con la lista privada (el secreto NOTAS_PALABRAS_VETADAS) y falla si
    encuentra algo. Sin el secreto no se ejecuta, y el propio test lo avisa en el resumen del run (::warning, con
    NOTAS_AVISAR_SIN_VETADAS) sin poner ningún job en rojo, ni en las ramas ni en main. Si un día se exige el secreto
    (NOTAS_EXIGIR_VETADAS), hay que cambiar estos tests."""

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

    def test_sin_el_secreto_avisa_y_no_falla(self):
        """Nada exige el secreto (NOTAS_EXIGIR_VETADAS no está en ningún env de tests.yml), así que su falta no pone en
        rojo ningún push, tampoco a main; y nada deja en verde una coincidencia ni salta el paso (continue-on-error, if,
        `|| true`, exit). Sin la lista, el propio test deja el ::warning (NOTAS_AVISAR_SIN_VETADAS): el paso no repite
        en bash cuándo está vacía (con blancos que no son ASCII, bash y Python no coincidían)."""
        wf = workflow("tests.yml")
        self.assertIn("push", eventos(wf["on"]))
        job, paso = self.paso()
        for d in (wf, job, paso):
            self.assertNotIn("NOTAS_EXIGIR_VETADAS", d.get("env") or {})
        for d in (job, paso):
            self.assertNotIn("continue-on-error", d)
            self.assertNotIn("if", d)
        self.assertIn(paso["env"].get("NOTAS_AVISAR_SIN_VETADAS"), ("1", "true"))
        run = paso["run"]
        self.assertNotIn("NOTAS_PALABRAS_VETADAS", run)
        self.assertNotIn("::", run)
        self.assertNotIn("|| true", run)
        self.assertNotRegex(run, r"\bexit\b")
        self.assertTrue(run.rstrip().endswith("test_package.TemplatesTests.test_sin_rastro_de_clientes"))

    @unittest.skipIf(os.name == "nt" or not shutil.which("bash"), "el paso corre con bash, en ubuntu-latest")
    def test_paso_de_agnosticismo_en_bash(self):
        """El `run` del paso con `bash -e` y su `env` como lo rellena GitHub (sin el secreto, la expresión da «»), en un
        push a main (lo más estricto): sin la lista, o con una que solo trae comas y blancos (el secreto vaciado al
        rotarlo), también blancos que no son ASCII, sale en verde con el aviso al principio de una línea de la salida y
        el test saltado; con una que no casa con nada, el test se ejecuta y pasa, sin aviso; con una que casa, sale en
        rojo con fichero y línea, sin el texto encontrado."""
        _, paso = self.paso()
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
        base.update(PATH=tmp + os.pathsep + base.get("PATH", ""), GITHUB_ACTIONS="true",
                    GITHUB_EVENT_NAME="push", GITHUB_REF="refs/heads/main")
        secreto = "${{ secrets.NOTAS_PALABRAS_VETADAS }}"
        for caso, lista, esperado in (("sin el secreto", "", "aviso"), ("sin la variable", None, "aviso"),
                                      ("con solo comas y blancos", " , \n\t,", "aviso"),
                                      ("con blancos que no son ASCII", "\x1c,\xa0\n\u202f", "aviso"),
                                      ("con una lista que no casa con nada", "(?!)", "ok"),
                                      ("con una lista que casa", r"^name: tests\b", "rojo")):
            with self.subTest(caso):
                env = dict(base)
                for k, v in paso["env"].items():
                    self.assertTrue(v == secreto or "${{" not in v, v)  # otra expresión: hay que simularla aquí
                    if v != secreto:
                        env[k] = v
                    elif lista is not None:
                        env[k] = lista
                r = subprocess.run(["bash", "-e", script], cwd=os.path.join(REPO, "tests"), env=env,
                                   capture_output=True, encoding="utf-8", errors="replace", timeout=300)
                salida = (r.stdout + r.stderr).replace("::", ": :")  # que un fallo no deje anotaciones en el run
                aviso = re.search("(?m)^" + re.escape(_AVISO) + ".*$", r.stdout)
                self.assertEqual(bool(aviso), esperado == "aviso", salida)
                if aviso:
                    self.assertIn("no se ha ejecutado", aviso.group(), salida)
                    self.assertIn(_EN_LOCAL, aviso.group(), salida)
                self.assertNotIn("::error", r.stdout, salida)
                if esperado == "rojo":
                    self.assertNotEqual(r.returncode, 0, salida)
                    self.assertIn(".github/workflows/tests.yml:1 (patrón 1)", r.stderr, salida)
                    self.assertNotIn("name: tests", r.stderr, salida)  # el informe no repite lo encontrado
                    continue
                self.assertEqual(r.returncode, 0, salida)
                self.assertRegex(r.stderr, r"\.\.\. skipped " if esperado == "aviso" else r"(?m)\.\.\. ok$", salida)


if __name__ == "__main__":
    unittest.main()
