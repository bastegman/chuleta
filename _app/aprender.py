# -*- coding: utf-8 -*-
"""Aprender de tus correcciones (ítem 17 del backlog, 09/11/2026; la idea es de Quill, en la research de competencia
que aprobó Bernardo en el msg 1242).

Al corregir una transcripción, Tenca guarda lo que escribió Whisper (DATA_DIR/originales). Comparando las dos versiones
salen las palabras que has cambiado y, de ellas, solo cuentan los nombres propios, las siglas y los términos de cliente:
las erratas sueltas, las tildes y los cambios de redacción no. Con tu sí, el término entra en una lista de vocabulario
(Whisper lo escribirá bien la próxima vez) y, si lo eliges, una regla «lo que escribió Whisper → lo correcto» se aplica
en las siguientes transcripciones de ese proyecto: solo en frases que nadie ha tocado, marcada en el texto y con
«Deshacer». Nada sale del PC.

Aquí solo hay texto: qué cambió, qué vale la pena proponer, cómo aplicar y deshacer una regla y cómo añadir un término a
una lista sin estropearla. Las rutas, los ajustes y la API están en server.py."""
import difflib
import re
import unicodedata
from collections import Counter

from common import SRT_TIME, fold

# un término: empieza y acaba en letra o cifra; dentro valen guiones, barras, puntos, apóstrofos y «&» («S/4HANA»,
# «P&G», «e-commerce», «D'Hondt»)
TOKEN = re.compile(r"[^\W_](?:[\w'’&+./-]*[^\W_])?")

# palabras de uso corriente en español, inglés y portugués: con mayúscula al empezar una frase no son un nombre
COMUNES = set("""
a al algo alguien algun alguna algunas alguno algunos ahi ahora alli antes aqui asi aunque bien bueno buenos buenas
cada casi claro como con contra cual cuales cuando cuanto de del desde despues donde dos el ella ellas ellos en
entonces entre era eran eres es esa esas ese eso esos esta estamos estan estas este esto estos estoy fue fueron gracias
ha habia hace hacer han hasta hay he hemos hola igual la las le les lo los mas me mi mis mira mucho muchos muy nada
ni no nos nosotros nuestra nuestro o ok okay otra otras otro otros oye para pero perfecto poco por porque pues que
quien se sea ser si sin sobre solo son su sus tambien tan te tenemos tengo tiene tienen todo todos tu tus un una uno
unos usted ustedes vale vamos voy y ya yo eh ah bueno vale venga dale sale
an and are as at be but by can could do does for from get go going good great have he hello hey hi how i if in is
it its just know let like me my no not now of oh on one or our out right see so some that the their them then there
these they think this those to up us want was we well what when where which who why will with would yeah yes you your
ao aos as com da das depois do dos ela ele eles em essa esse eu isso isto ja mas muito na nas nao num numa nos ola
pela pelo sem seu sua tem tambem voce voces obrigado obrigada sim entao bom boa
""".split())

# dentro de un nombre de varias palabras («Banco del Norte», «Ruiz & Pardo»): valen en medio, no en los extremos
ENLACES = {"de", "del", "la", "las", "los", "el", "y", "e", "&", "of", "the", "and", "da", "das", "do", "dos", "di",
           "von", "van", "der", "den", "le", "du"}
CONJUNCIONES = {"y", "e", "and", "&"}
ARTICULOS = {"el", "la", "los", "las", "the", "o", "a", "os", "as", "le", "les"}  # «El Molino Verde», «The Green Room»

MAX_NUEVO = 5        # palabras del término corregido («Banco del Norte de Chile»)
MAX_ANTES = 5        # palabras de lo que escribió Whisper («te ese a» → «TSA»)
MAX_REGLA = 3        # una regla automática: como mucho 3 palabras de origen
PARECIDO_PROPUESTA = 0.4  # por debajo es un cambio de redacción («el cliente» → «Orvalia»), no algo mal oído
PARECIDO_REGLA = 0.5
MAX_PROPUESTAS = 8


def tokens(text):
    return [(m.group(0), m.start(), m.end()) for m in TOKEN.finditer(text or "")]


def sin_tildes(s):
    """Sin tildes pero con sus mayúsculas: «José» → «Jose»."""
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if not unicodedata.combining(c))


def letras(s):
    return "".join(c for c in fold(s) if c.isalnum())


_SONIDOS = ((r"ph", "f"), (r"ch", "c"), (r"sh", "s"), (r"qu", "k"), (r"ll", "y"), (r"c(?=[ei])", "s"), (r"c", "k"),
            (r"z", "s"), (r"v", "b"), (r"w", "u"), (r"y", "i"), (r"h", ""), (r"(.)\1+", r"\1"))


def sonido(s):
    """Lo que suena, a grandes rasgos y en español: «Orbalia» y «Orvalia», «güindous» y «Windows», «tesa» y «TSA» se
    acercan."""
    s = letras(s)
    for a, b in _SONIDOS:
        s = re.sub(a, b, s)
    return s


def parecido(a, b):
    """0..1: lo que se parecen dos textos por sus letras o por cómo suenan (el mayor de los dos)."""
    x, y = letras(a), letras(b)
    if not x or not y:
        return 0.0
    return max(difflib.SequenceMatcher(None, x, y, autojunk=False).ratio(),
               difflib.SequenceMatcher(None, sonido(a), sonido(b), autojunk=False).ratio())


def es_sigla(t):
    """«SAP», «TSA», «KPIs» (la s final del plural), «S/4HANA»."""
    ls = [c for c in t if c.isalpha()]
    return sum(c.isupper() for c in ls) >= 2 and all(c.isupper() for c in ls[:-1])


def inicio_de_frase(text, pos):
    i = pos - 1
    while i >= 0 and (text[i].isspace() or text[i] in "«\"'“‘(¿¡[-—"):
        i -= 1
    return i < 0 or text[i] in ".!?…:"


def es_termino(tok, text, pos, minusculas):
    """Una palabra que vale la pena aprender: sigla, mayúsculas por dentro («iPhone», «SuccessFactors»), letras y
    cifras («B2B», «M365») o un nombre propio: con mayúscula en mitad de frase o, al empezar una, si en la transcripción
    no sale nunca en minúsculas (así «Ella», «Vale» o «Bueno» no cuentan)."""
    f = fold(tok)
    if f in COMUNES or sum(c.isalnum() for c in tok) < 2 or not any(c.isalpha() for c in tok):
        return False
    cifras = any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok)
    if es_sigla(tok) or re.search(r"[a-zß-ÿ][A-ZÀ-Þ]", tok) or cifras:
        return True
    if not tok[0].isupper():
        return False
    return not inicio_de_frase(text, pos) or f not in minusculas


def es_nombre(toks, text, minusculas):
    """Las palabras corregidas forman un nombre: cada una es un término o un enlace en medio («Banco del Norte»);
    delante puede ir un artículo con mayúscula («El Molino Verde»)."""
    n = len(toks)
    for k, (t, a, _) in enumerate(toks):
        if es_termino(t, text, a, minusculas):
            continue
        if 0 < k < n - 1 and fold(t) in ENLACES:
            continue
        if k == 0 and n > 1 and t[:1].isupper() and fold(t) in ARTICULOS:
            continue
        return False
    return any(es_termino(t, text, a, minusculas) for t, a, _ in toks)


def clave(start):
    return None if start is None else round(float(start), 2)


def repetidos(segs):
    """Los inicios (clave) que llevan dos o más frases."""
    vistos = Counter(clave(s["start"]) for s in segs if s.get("start") is not None)
    return {k for k, n in vistos.items() if n > 1}


def pares(original, actual):
    """(lo que escribió Whisper, lo que hay ahora, inicio) de cada frase cambiada. Con tiempos, por el inicio de la
    frase (el de la copia del original y el del .srt corregido coinciden); sin ellos (.txt), por líneas."""
    if original and actual and all(s.get("start") is not None for s in list(original) + list(actual)):
        repes = repetidos(original) | repetidos(actual)  # dos frases con el mismo inicio: no se sabe cuál es cuál
        antes = {clave(s["start"]): s["text"] for s in original}
        out = []
        for s in actual:
            o = antes.get(clave(s["start"]))
            if o is not None and o != s["text"] and clave(s["start"]) not in repes:
                out.append((o, s["text"], s["start"]))
        return out
    a, b = [s["text"] for s in original or []], [s["text"] for s in actual or []]
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "replace":
            continue
        if i2 - i1 == j2 - j1:
            out += [(a[i], b[j], None) for i, j in zip(range(i1, i2), range(j1, j2))]
        else:
            out.append((" ".join(a[i1:i2]), " ".join(b[j1:j2]), None))
    return out


def cambios(antes, ahora):
    """Las sustituciones de palabras entre dos versiones de una frase: [(lo de antes, lo de ahora, tokens de ahora)].
    Las palabras se comparan tal cual (con mayúsculas y tildes) y sin la puntuación de alrededor."""
    ta, tb = tokens(antes), tokens(ahora)
    out = []
    sm = difflib.SequenceMatcher(None, [t for t, _, _ in ta], [t for t, _, _ in tb], autojunk=False)
    for op, i1, i2, j1, j2 in _juntar_nombres(sm.get_opcodes(), ta, tb):
        if op != "replace":
            continue
        # «la orbalia tesa» → «la Orvalia TSA» llega como un solo cambio de dos palabras: si cada una se parece a la
        # suya y está mal escrita (no solo en mayúsculas o tildes, como en «el molino verde» → «El Molino Verde», que
        # es un nombre), son dos correcciones («Orvalia» y «TSA»), no el nombre «Orvalia TSA»
        if i2 - i1 == j2 - j1 > 1 and all(parecido(ta[i1 + k][0], tb[j1 + k][0]) >= PARECIDO_PROPUESTA
                                          and fold(ta[i1 + k][0]) != fold(tb[j1 + k][0]) for k in range(i2 - i1)):
            out += [(ta[i1 + k][0], tb[j1 + k][0], tb[j1 + k:j1 + k + 1]) for k in range(i2 - i1)]
            continue
        out.append((antes[ta[i1][1]:ta[i2 - 1][2]], ahora[tb[j1][1]:tb[j2 - 1][2]], tb[j1:j2]))
    return out


def _juntar_nombres(ops, ta, tb):
    """«banco del norte» → «Banco del Norte» llega como dos cambios («banco», «norte») con «del» igual en medio: si los
    dos solo ponen mayúsculas o tildes y lo de en medio son enlaces, es un nombre de varias palabras."""
    def nombre(a, b):  # «banco» → «Banco»: la misma palabra con mayúscula inicial, y no una corriente («Vale»)
        return fold(a) == fold(b) and b[:1].isupper() and not es_sigla(b) and fold(b) not in COMUNES
    solo_forma = lambda o: (o[0] == "replace" and o[2] - o[1] == o[4] - o[3] and all(  # noqa: E731
        nombre(ta[o[1] + k][0], tb[o[3] + k][0]) for k in range(o[2] - o[1])))
    # «y», «e», «and» no: «ana y pedro» → «Ana y Pedro» son dos nombres, no uno
    enlaces = lambda o: (o[0] == "equal" and 0 < o[2] - o[1] <= 2  # noqa: E731
                         and all(fold(t) in ENLACES - CONJUNCIONES for t, _, _ in ta[o[1]:o[2]]))
    out, juntos = [], set()
    for o in ops:
        if (len(out) >= 2 and solo_forma(o) and (out[-2] in juntos or solo_forma(out[-2]))
                and enlaces(out[-1])):  # y otra vez: «Banco del Norte de Chile»
            a = out[-2]
            out[-2:] = [("replace", a[1], o[2], a[3], o[4])]
            juntos.add(out[-1])
        else:
            out.append(o)
    return out


def patron(de):
    toks = [t for t, _, _ in tokens(de)]
    if not toks:
        return None
    return r"(?<!\w)" + r"\s+".join(re.escape(t) for t in toks) + r"(?!\w)"


def normal(s):
    """Para comparar una regla con lo que encaja en el texto: sus palabras sin tildes ni mayúsculas."""
    return " ".join(fold(t) for t, _, _ in tokens(s))


def queda(de, termino, texto):
    """Lo que escribió Whisper sigue en el texto corregido (sin contar lo que ya es el término bien escrito): no se
    corrigió en todas partes, así que no es una regla segura."""
    p = patron(de)
    return bool(p) and any(m.group(0) != termino for m in re.finditer(p, texto, re.IGNORECASE))


def propuestas(original, actual, excluir=(), no=(), no_reglas=()):
    """Lo que vale la pena proponer al comparar lo que escribió Whisper (`original`) con lo corregido (`actual`), las
    dos listas de {"start", "text"}: [{"termino", "veces", "antes": [{"texto", "veces", "regla"}]}], lo más corregido
    primero. `excluir`: pares (normal(de), normal(a)) que cambió Tenca, no tú; `no`: términos que dijiste que no
    (normal); `no_reglas`: pares de reglas que no quisiste. "regla" dice si es seguro cambiarlo solo la próxima vez:
    se parece a lo correcto, no son palabras corrientes y lo corregiste en todas partes."""
    # las palabras que escribes en minúsculas (en lo corregido: el original tiene justo lo que estaba mal escrito)
    minusculas = {fold(t) for x in actual or [] for t, _, _ in tokens(x["text"]) if t == t.lower()}
    excluir, no, no_reglas = set(excluir), set(no), {tuple(p) for p in no_reglas}
    todo = "\n".join(s["text"] for s in actual or [])
    grupos, orden = {}, []
    for antes, ahora, _ in pares(original, actual):
        for de, nuevo, toks in cambios(antes, ahora):
            if not (0 < len(toks) <= MAX_NUEVO) or len(tokens(de)) > MAX_ANTES:
                continue
            if sin_tildes(de) == sin_tildes(nuevo):
                continue  # solo tildes («Jose» → «José»): una errata, no un nombre que Whisper no conocía
            if not es_nombre([(t, a, b) for t, a, b in toks], ahora, minusculas):
                continue
            if fold(de) == fold(nuevo) and inicio_de_frase(ahora, toks[0][1]) and not (
                    _forma_propia(toks[0][0]) or any(es_termino(t, ahora, a, minusculas) for t, a, _ in toks[1:])):
                continue  # «efectivamente» → «Efectivamente» al empezar frase: solo la mayúscula de la frase
            k, kd = normal(nuevo), normal(de)
            if (kd, k) in excluir or k in no or parecido(de, nuevo) < PARECIDO_PROPUESTA:
                continue
            g = grupos.get(k)
            if g is None:
                g = grupos[k] = {"formas": Counter(), "antes": {}, "veces": 0}
                orden.append(k)
            g["formas"][nuevo] += 1
            g["veces"] += 1
            a = g["antes"].setdefault(kd, {"formas": Counter(), "veces": 0})
            a["formas"][de] += 1
            a["veces"] += 1
    out = []
    for k in orden:
        g = grupos[k]
        termino = g["formas"].most_common(1)[0][0]
        antes = []
        for kd, a in g["antes"].items():
            de = a["formas"].most_common(1)[0][0]
            regla = (kd != k or de != termino) and (kd, k) not in no_reglas and regla_segura(de, termino, todo)
            antes.append({"texto": de, "veces": a["veces"], "regla": regla})
        antes.sort(key=lambda x: -x["veces"])
        out.append({"termino": termino, "veces": g["veces"], "antes": antes})
    out.sort(key=lambda p: -p["veces"])  # estable: a igualdad, en el orden del texto
    return out[:MAX_PROPUESTAS]


def regla_valida(de, termino):
    """Lo que hace segura una regla sin mirar ninguna transcripción: pocas palabras, no todas corrientes, al menos 3
    letras y, si solo cambian mayúsculas o tildes, una sigla o una palabra como «iPhone» («plan» → «Plan» no). Lo usan
    las propuestas, el diccionario de Ajustes y la cola (una regla escrita a mano en settings.json, o de una versión
    anterior, que no cumpla esto no se aplica)."""
    toks = [t for t, _, _ in tokens(de)]
    if not toks or len(toks) > MAX_REGLA or len(letras(de)) < 3 or not termino or de == termino:
        return False
    if all(fold(t) in COMUNES or fold(t) in ENLACES for t in toks):
        return False
    return letras(de) != letras(termino) or all(_forma_propia(t) for t, _, _ in tokens(termino))


def regla_segura(de, termino, texto):
    """Cambiar `de` por `termino` sin preguntar en las próximas reuniones solo si: se parecen (lo mal oído, no otra
    forma de decirlo), son pocas palabras y no todas corrientes, tiene al menos 3 letras y en esta reunión lo corregiste
    en todas partes (si dejaste alguno, es que a veces está bien)."""
    # y, además, que se parezcan (lo mal oído, no otra forma de decirlo) y que no quede ninguno sin corregir
    return regla_valida(de, termino) and parecido(de, termino) >= PARECIDO_REGLA and not queda(de, termino, texto)


def _forma_propia(t):
    """Una palabra que solo se escribe así: sigla, mayúsculas por dentro («iPhone») o letras y cifras («M365»)."""
    return es_sigla(t) or bool(re.search(r"[a-zß-ÿ][A-ZÀ-Þ]", t)) or (any(c.isdigit() for c in t)
                                                                      and any(c.isalpha() for c in t))


# ----------------------------------------------------------------------------- aplicar y deshacer reglas

def aplicar(texto, reglas, deshechas=()):
    """Aplica las reglas [{"de", "a"}] a una frase de una vez (sin encadenarlas). Devuelve (texto nuevo,
    [{"de": lo que había, "a", "pos": dónde empieza en el texto nuevo}]). No toca lo que ya está bien escrito, ni lo que
    forma parte del término correcto («HANA» dentro de «S/4 HANA» no se cambia si la regla escribe «S/4 HANA»), ni las
    reglas deshechas en esta reunión (`deshechas`: normal(de))."""
    mapa = {}
    for r in reglas:
        k = normal(r.get("de"))
        if k and k not in deshechas and r.get("a") and k not in mapa:
            mapa[k] = r["a"]
    if not mapa or not texto:
        return texto, []
    pats = sorted({patron(r["de"]) for r in reglas if normal(r.get("de")) in mapa}, key=len, reverse=True)
    rx = re.compile("|".join(pats), re.IGNORECASE)
    out, hechos, i = [], [], 0
    for m in rx.finditer(texto):
        a = mapa.get(normal(m.group(0)))
        if not a or m.group(0) == a or _dentro_de(texto, m.start(), m.end(), a):
            continue
        out.append(texto[i:m.start()])
        pos = sum(len(x) for x in out)
        out.append(a)
        hechos.append({"de": m.group(0), "a": a, "pos": pos})
        i = m.end()
    if not hechos:
        return texto, []
    out.append(texto[i:])
    return "".join(out), hechos


def _dentro_de(texto, s, e, a):
    """El trozo [s, e) ya es parte de `a` escrito en el texto (sin mirar mayúsculas), como «HANA» dentro de «S/4 HANA».
    No cuenta el propio trozo: en una regla de solo mayúsculas («kpi» → «KPI») es `a` en minúsculas, no `a` escrito."""
    lo = max(0, s - len(a))
    zona, fa = _plano(texto[lo:min(len(texto), e + len(a))]), _plano(a)  # sin tildes ni mayúsculas, misma longitud
    k = zona.find(fa)
    while k != -1:
        i, j = lo + k, lo + k + len(a)
        if i <= s and j >= e and (i, j) != (s, e):
            return True
        k = zona.find(fa, k + 1)
    return False


def _plano(s):
    """Minúsculas y sin tildes, letra a letra (la misma longitud que `s`, como foldMap en app.js)."""
    return "".join(unicodedata.normalize("NFD", c)[0].lower() if c else c for c in s)


def _ocurrencias(texto, a):
    p = r"(?<!\w)" + re.escape(a) + r"(?!\w)"
    return [m.start() for m in re.finditer(p, texto)]


def posiciones(texto, entradas, original=None):
    """Dónde está ahora, en la frase, cada cambio de Tenca (o None si ya no se sabe): si la frase sigue como la dejó
    Tenca, donde la dejó; si la has tocado después, solo si ese término sale en la frase tantas veces como cambios
    suyos hay con él (así todas son suyas, en el mismo orden) y lo de antes sigue en el original de Whisper
    (`original`, si se tiene). Si no cuadra, no se marca ni se deshace: mejor eso que señalar o cambiar algo tuyo."""
    if texto is not None and all(e.get("texto") == texto for e in entradas):
        return [e["pos"] if texto[e["pos"]:e["pos"] + len(e["a"])] == e["a"] else None for e in entradas]
    out = [None] * len(entradas)
    if texto is None:
        return out
    for a in {e["a"] for e in entradas}:
        idx = sorted((i for i, e in enumerate(entradas) if e["a"] == a),
                     key=lambda i: (entradas[i].get("pos") is None, entradas[i].get("pos") or 0))
        occ = _ocurrencias(texto, a)
        if len(occ) != len(idx):
            continue
        if original is not None and any(len(_ocurrencias(original, entradas[i]["de"])) < sum(
                1 for j in idx if entradas[j]["de"] == entradas[i]["de"]) for i in idx):
            continue
        for i, p in zip(idx, occ):
            out[i] = p
    return out


def deshacer(texto, entradas, quitar, original=None):
    """Vuelve a poner lo que escribió Whisper en los cambios de `entradas` (los de Tenca en esta frase) para los que
    quitar(entrada) es cierto. Devuelve (texto nuevo, las demás entradas con su posición al día, deshechos, no
    deshechos: los que ya no se encuentran porque has cambiado la frase; esos se dejan como están)."""
    pos = posiciones(texto, entradas, original)
    hechos, saltados = [], 0
    for e, p in sorted(zip(entradas, pos), key=lambda x: -(x[1] if x[1] is not None else -1)):
        if not quitar(e):
            continue
        if p is None:
            saltados += 1
            continue
        texto = texto[:p] + e["de"] + texto[p + len(e["a"]):]
        hechos.append((e, p, len(e["de"]) - len(e["a"])))
    quedan = []
    for e, p in zip(entradas, pos):
        if quitar(e):  # deshecha o, si ya no se encontraba, olvidada: la regla no vuelve en esta reunion
            continue
        if p is not None:
            p += sum(d for x, q, d in hechos if q < p)
        quedan.append(dict(e, pos=p, texto=texto) if p is not None else dict(e, texto=None))
    return texto, quedan, len(hechos), saltados


# ----------------------------------------------------------------------------- .srt: cambiar el texto sin tocar tiempos

def srt_bloques(text):
    """Los bloques de un .srt: [{"start", "cab" (líneas hasta la de tiempos, incluida), "text"}]. El inicio, como lo da
    common.parse_srt (redondeado a 2 decimales), para casar con lo que ve la interfaz."""
    out = []
    for block in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").strip()):
        lines = block.split("\n")
        for i, line in enumerate(lines):
            m = SRT_TIME.search(line)
            if m:
                g = [int(x) for x in m.groups()]
                start = g[0] * 3600 + g[1] * 60 + g[2] + int(m.group(4).ljust(3, "0")) / 1000
                txt = " ".join(l.strip() for l in lines[i + 1:] if l.strip())
                if txt:
                    out.append({"start": round(start, 2), "cab": lines[:i + 1], "text": txt})
                break
    return out


def srt_texto(bloques):
    """El .srt de nuevo, numerado y con los tiempos tal cual (como common.srt_block: una línea de texto por bloque)."""
    return "\n".join(f"{i}\n{b['cab'][-1].strip()}\n{b['text']}\n" for i, b in enumerate(bloques, 1))


# ----------------------------------------------------------------------------- añadir un término a una lista

class NoSePuede(Exception):
    """El término no se puede añadir a ese archivo sin riesgo de estropearlo (el motivo va en el mensaje)."""


def termino_valido(t):
    t = " ".join(str(t or "").split())
    if not t or len(t) > 80 or re.search(r"[,;\t\n#]", t):
        return None
    return t


def anadir_texto(texto, termino):
    """El vocabulario general (un texto de Ajustes): el término al final, como está escrito (comas o uno por línea)."""
    texto = texto or ""
    lineas = [l.strip() for l in texto.splitlines() if l.strip() and not l.strip().startswith("#")]
    if not lineas:
        return (texto.rstrip("\n") + "\n" if texto.strip() else "") + termino
    if len(lineas) > 1 and not any(re.search(r"[,;\t]", l) for l in lineas):
        return texto.rstrip("\n") + "\n" + termino
    cuerpo = texto.rstrip()
    return cuerpo + (" " if cuerpo.endswith((",", ";")) else ", ") + termino


def anadir_termino(raw, termino):
    """Los bytes de una lista .txt con `termino` añadido al final y lo demás tal cual: la misma codificación (UTF-8 con
    o sin BOM, UTF-16 con BOM o la de Windows, cp1252), los mismos saltos de línea y el mismo estilo: detrás del último
    término con su separador (coma, punto y coma o tabulador) o, si la lista va uno por línea, en una línea nueva. Así
    se puede escribir en una carpeta de listas conectada sin cambiarle nada más a nadie."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        codec = "utf-16-le" if raw.startswith(b"\xff\xfe") else "utf-16-be"
        try:
            text = raw[2:].decode(codec)
        except UnicodeDecodeError:
            raise NoSePuede("codificacion")
        return raw[:2] + _anadir_lineas(text, termino).encode(codec)
    bom = raw[:3] if raw.startswith(b"\xef\xbb\xbf") else b""
    body = raw[len(bom):]
    eol = b"\r\n" if b"\r\n" in body else b"\n"
    lines = body.split(b"\n")
    dec, cp = [], False
    for l in lines:
        try:
            dec.append(l.decode("utf-8"))
        except UnicodeDecodeError:
            if bom:
                raise NoSePuede("codificacion")  # UTF-8 con BOM y una línea que no lo es: mejor no tocarla
            dec.append(l.decode("cp1252", errors="replace"))
            cp = True

    def enc(s):
        if cp:  # una lista de la página de códigos de Windows: lo nuevo, igual (si cabe en ella)
            try:
                return s.encode("cp1252")
            except UnicodeEncodeError:
                raise NoSePuede("codificacion")
        return s.encode("utf-8")

    utiles = [i for i, s in enumerate(dec) if s.strip() and not s.strip().startswith("#")]
    una_por_linea = len(utiles) > 1 and not any(re.search(r"[,;\t]", dec[i]) for i in utiles)
    if not utiles or una_por_linea:
        fin = body if not body or body.endswith(b"\n") else body + eol
        return bom + fin + enc(termino) + (eol if not body or body.endswith(b"\n") else b"")
    i = utiles[-1]
    linea, cr = (lines[i][:-1], b"\r") if lines[i].endswith(b"\r") else (lines[i], b"")
    sep = _sep(dec[i].rstrip("\r").rstrip())
    resto = linea[len(linea.rstrip()):]  # los espacios del final se quedan detrás
    lines[i] = linea.rstrip() + enc(sep + termino) + resto + cr
    return bom + b"\n".join(lines)


def _sep(linea):
    """El separador con el que va escrita la línea («, », «,», «; », «;» o un tabulador), para seguir igual. Si la línea
    ya acaba en uno, solo el espacio que lleve."""
    usados = Counter(m.group(0) for m in re.finditer(r"[,;] ?|\t", linea))
    sep = usados.most_common(1)[0][0] if usados else ", "
    if linea.endswith((",", ";", "\t")):
        return "" if linea.endswith("\t") or not sep.endswith(" ") else " "
    return sep


def _anadir_lineas(text, termino):
    """Como anadir_termino, sobre el texto ya decodificado (UTF-16)."""
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.split("\n")
    utiles = [i for i, s in enumerate(lines) if s.strip() and not s.strip().startswith("#")]
    if not utiles or (len(utiles) > 1 and not any(re.search(r"[,;\t]", lines[i]) for i in utiles)):
        if not text or text.endswith("\n"):
            return text + termino + eol
        return text + eol + termino
    i = utiles[-1]
    cr = "\r" if lines[i].endswith("\r") else ""
    s = lines[i].rstrip("\r")
    cuerpo = s.rstrip()
    lines[i] = cuerpo + _sep(cuerpo) + termino + s[len(cuerpo):] + cr
    return "\n".join(lines)
