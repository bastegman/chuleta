# -*- coding: utf-8 -*-
"""Aprender de tus correcciones (ítem 17, 09/11/2026): aprender.py (qué vale la pena proponer, reglas seguras, aplicar y
deshacer sin tocar lo que no es suyo, añadir a una lista sin cambiarle la codificación ni el estilo) y su parte del
servidor (la regla al terminar una transcripción, solo en su proyecto y en frases que nadie ha tocado; /api/aprender y
compañía; el ajuste «aprendido» al renombrar un proyecto y al rescatar un settings.json roto). Los nombres son
inventados. La interfaz la prueba test_ui_aprender."""
import json
import os
import shutil
import stat
import types
import unittest
from unittest import mock

from support import TempTree, bare_jobs, engines, server, srt_text  # noqa: F401  (support prepara sys.path)
import aprender  # noqa: E402

A = aprender


def segs(*textos, paso=5.0):
    return [{"start": round(i * paso, 2), "end": round(i * paso + paso, 2), "text": t} for i, t in enumerate(textos)]


def terminos(ps):
    return [p["termino"] for p in ps]


def accionables(r, lista=None):
    """Las propuestas que la interfaz enseña con la lista de la reunión (o `lista`): el término no está en ella, o hay
    una regla segura que aún no tienes en el proyecto de esa lista (como aprQue en aprender.js)."""
    lista = r["lista"] if lista is None else lista
    proy = next((l["project"] for l in r["listas"] if l["id"] == lista), "") if lista else ""
    return [p for p in r["propuestas"] if not (lista in p["en"] if lista else p["en_general"])
            or any(x["regla"] and not any(A.fold(pr) == A.fold(proy) for pr in x["reglas_en"]) for x in p["antes"])]


# ----------------------------------------------------------------------------- qué vale la pena proponer

class PropuestasTests(unittest.TestCase):
    ORIG = segs("Hola a todos, empezamos con orbalia.",
                "Ella dice que el tesa de orbalia va bien.",
                "vale, la sap y el cliente.",
                "Lo q dijimos ayer.",
                "Bueno, sap otra vez.")
    AHORA = segs("Hola a todos, empezamos con Orvalia.",
                 "Ella dice que el TSA de Orvalia va bien.",
                 "Vale, la SAP y Juan.",
                 "Lo que dijimos ayer.",
                 "Bueno, sap otra vez.")

    def test_nombres_y_siglas_si_erratas_y_redaccion_no(self):
        ps = A.propuestas(self.ORIG, self.AHORA)
        self.assertEqual(terminos(ps), ["Orvalia", "TSA", "SAP"])  # lo más corregido primero
        self.assertEqual(ps[0]["veces"], 2)
        self.assertEqual(ps[0]["antes"], [{"texto": "orbalia", "veces": 2, "regla": True}])
        self.assertEqual(ps[1]["antes"], [{"texto": "tesa", "veces": 1, "regla": True}])
        # «sap» sigue en la última frase: a veces está bien escrito así, no es una regla segura
        self.assertEqual(ps[2]["antes"], [{"texto": "sap", "veces": 1, "regla": False}])
        # fuera: «Vale» (palabra corriente con mayúscula), «Lo que» (errata), «Juan» (otra forma de decirlo)
        self.assertNotIn("Juan", terminos(ps))

    def test_solo_tildes_minusculas_y_palabras_corrientes_no(self):
        ps = A.propuestas(segs("Habló Jose de la reunion.", "eso dijo el."),
                          segs("Habló José de la reunión.", "Eso dijo él."))
        self.assertEqual(ps, [])

    def test_mayuscula_al_empezar_frase(self):
        """Con mayúscula al empezar una frase cuenta si no sale nunca en minúsculas (un nombre), y no si sale (una
        palabra corriente que Whisper puso en mayúscula)."""
        ps = A.propuestas(segs("Eya lo sabe.", "Orbalya lo sabe."), segs("Ella lo sabe.", "Orvalia lo sabe."))
        self.assertEqual(terminos(ps), ["Orvalia"])
        # «cumbre» sale en minúsculas en otra frase: es una palabra corriente, no un nombre
        ps = A.propuestas(segs("Kumbre lo sabe.", "la cumbre fue ayer."),
                          segs("Cumbre lo sabe.", "la cumbre fue ayer."))
        self.assertEqual(ps, [])
        # con mayúscula en mitad de frase sí es un nombre («la Cumbre de Lisboa»)
        ps = A.propuestas(segs("Fue en la kumbre de Lisboa."), segs("Fue en la Cumbre de Lisboa."))
        self.assertEqual(terminos(ps), ["Cumbre"])

    def test_formas_de_termino(self):
        ps = A.propuestas(segs("con el ai fon", "el b dos b", "con m 365", "las k p is",
                               "el sucess factors", "con el banco del norte"),
                          segs("con el iPhone", "el B2B", "con M365", "las KPIs",
                               "el SuccessFactors", "con el Banco del Norte"))
        self.assertEqual(sorted(terminos(ps)), sorted(["iPhone", "B2B", "M365", "KPIs", "SuccessFactors",
                                                       "Banco del Norte"]))

    def test_nombre_de_varias_palabras(self):
        ps = A.propuestas(segs("hablamos con ruiz and pardo ayer"), segs("hablamos con Ruiz & Pardo ayer"))
        self.assertEqual(terminos(ps), ["Ruiz & Pardo"])
        ps = A.propuestas(segs("vamos con el molino verde"), segs("vamos con El Molino Verde"))
        self.assertEqual(terminos(ps), ["El Molino Verde"])
        ps = A.propuestas(segs("habló jose"), segs("habló José"))  # un nombre que Whisper escribió en minúscula
        self.assertEqual(terminos(ps), ["José"])

    def test_mayusculas_al_empezar_frase_y_minusculas_del_original(self):
        """Lo que Whisper escribió en minúsculas (el original) no cuenta como «palabra corriente»: solo lo corregido."""
        ps = A.propuestas(segs("microsoft teams lo dijo"), segs("Microsoft Teams lo dijo"))
        self.assertEqual(terminos(ps), ["Microsoft Teams"])

    def test_inicios_repetidos_no_se_comparan(self):
        o = [{"start": 1.0, "text": "con orbalia"}, {"start": 1.0, "text": "y orbalia"}, {"start": 2.0, "text": "tesa"}]
        n = [{"start": 1.0, "text": "con Orvalia"}, {"start": 1.0, "text": "y Orvalia"}, {"start": 2.0, "text": "TSA"}]
        self.assertEqual(terminos(A.propuestas(o, n)), ["TSA"])

    def test_dos_correcciones_juntas_son_dos(self):
        ps = A.propuestas(segs("con la orbalia tesa"), segs("con la Orvalia TSA"))
        self.assertEqual(terminos(ps), ["Orvalia", "TSA"])

    def test_siglas_dichas_letra_a_letra(self):
        """Se propone añadirla, pero no cambiarla sola: «te ese a» son palabras corrientes."""
        ps = A.propuestas(segs("el te ese a de ayer"), segs("el TSA de ayer"))
        antes = [{"texto": "te ese a", "veces": 1, "regla": False}]
        self.assertEqual(ps, [{"termino": "TSA", "veces": 1, "antes": antes}])

    def test_varias_formas_de_lo_mismo(self):
        ps = A.propuestas(segs("con orbalia", "con Orbalya", "con orbalia"),
                          segs("con Orvalia", "con Orvalia", "con Orvalia"))
        self.assertEqual(len(ps), 1)
        self.assertEqual(ps[0]["veces"], 3)
        self.assertEqual([(x["texto"], x["veces"]) for x in ps[0]["antes"]], [("orbalia", 2), ("Orbalya", 1)])

    def test_lo_de_tenca_lo_dicho_que_no_y_las_reglas_que_no(self):
        ps = A.propuestas(self.ORIG, self.AHORA, excluir={("orbalia", "orvalia")}, no={"tsa"})
        self.assertEqual(terminos(ps), ["SAP"])
        ps = A.propuestas(self.ORIG, self.AHORA, no_reglas=[("orbalia", "orvalia")])
        self.assertFalse(ps[0]["antes"][0]["regla"])

    def test_sin_tiempos_por_lineas(self):
        o = [{"start": None, "text": t} for t in ("uno", "con orbalia", "tres")]
        n = [{"start": None, "text": t} for t in ("uno", "nueva", "con Orvalia", "tres")]
        self.assertEqual(terminos(A.propuestas(o, n)), ["Orvalia"])

    def test_frases_que_no_casan_por_tiempo_no_cuentan(self):
        o = [{"start": 1.0, "text": "con orbalia"}]
        n = [{"start": 2.0, "text": "con Orvalia"}]
        self.assertEqual(A.propuestas(o, n), [])

    def test_como_mucho_ocho(self):
        nombres = ["Alfa", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India", "Juliett"]
        o = segs(*[f"con {n.lower()}x" for n in nombres])
        n = segs(*[f"con {n}" for n in nombres])
        self.assertEqual(len(A.propuestas(o, n)), A.MAX_PROPUESTAS)


class ReglaSeguraTests(unittest.TestCase):
    def test_casos(self):
        self.assertTrue(A.regla_segura("orbalia", "Orvalia", "todo con Orvalia"))
        self.assertFalse(A.regla_segura("orbalia", "Orvalia", "con Orvalia y orbalia"))  # quedó uno sin corregir
        self.assertTrue(A.regla_segura("sap", "SAP", "con SAP"))  # solo mayúsculas: vale (lo correcto no cuenta)
        self.assertTrue(A.regla_segura("iphone", "iPhone", ""))
        # solo mayúsculas en una palabra corriente: cambiaría cada «plan» o «banco» de las reuniones siguientes
        self.assertFalse(A.regla_segura("plan", "Plan", ""))
        self.assertFalse(A.regla_segura("banco", "Banco", ""))
        self.assertFalse(A.regla_segura("son a e", "Orvalia", ""))  # palabras corrientes
        self.assertFalse(A.regla_segura("ap", "AP", ""))  # menos de 3 letras
        self.assertFalse(A.regla_segura("el cliente", "Orvalia", ""))  # no se parecen
        self.assertFalse(A.regla_segura("uno dos tres cuatro", "UDTC", ""))  # más de 3 palabras

    def test_parecido(self):
        self.assertGreaterEqual(A.parecido("güindous", "Windows"), A.PARECIDO_REGLA)  # por cómo suena
        self.assertGreaterEqual(A.parecido("kuber netis", "Kubernetes"), A.PARECIDO_REGLA)
        self.assertLess(A.parecido("el cliente", "Orvalia"), A.PARECIDO_PROPUESTA)
        self.assertEqual(A.parecido("", "x"), 0.0)


# ----------------------------------------------------------------------------- aplicar y deshacer

class AplicarTests(unittest.TestCase):
    R = [{"de": "orbalia", "a": "Orvalia"}, {"de": "tesa", "a": "TSA"}]

    def test_sin_mayusculas_palabra_entera_y_de_una_vez(self):
        t, h = A.aplicar("La orbalia y la ORBALIA, Orvalia, orbaliasierra y el tesa.", self.R)
        self.assertEqual(t, "La Orvalia y la Orvalia, Orvalia, orbaliasierra y el TSA.")
        self.assertEqual([(x["de"], x["a"]) for x in h],
                         [("orbalia", "Orvalia"), ("ORBALIA", "Orvalia"), ("tesa", "TSA")])
        for x in h:
            self.assertEqual(t[x["pos"]:x["pos"] + len(x["a"])], x["a"])

    def test_no_encadena(self):
        t, h = A.aplicar("uno dos", [{"de": "uno", "a": "dos"}, {"de": "dos", "a": "tres"}])
        self.assertEqual(t, "dos tres")

    def test_lo_ya_correcto_no_se_toca(self):
        self.assertEqual(A.aplicar("con SAP", [{"de": "sap", "a": "SAP"}]), ("con SAP", []))
        # una regla de solo mayúsculas sí cambia lo que está en minúsculas (revisión 1: no se aplicaba nunca)
        t, h = A.aplicar("hablamos del kpi y del KPI", [{"de": "kpi", "a": "KPI"}])
        self.assertEqual((t, [x["de"] for x in h]), ("hablamos del KPI y del KPI", ["kpi"]))
        t, h = A.aplicar("con S/4 HANA y hana", [{"de": "hana", "a": "S/4 HANA"}])
        self.assertEqual(t, "con S/4 HANA y S/4 HANA")
        self.assertEqual(len(h), 1)

    def test_deshechas_y_vacias(self):
        self.assertEqual(A.aplicar("con orbalia", self.R, deshechas={"orbalia"}), ("con orbalia", []))
        self.assertEqual(A.aplicar("", self.R), ("", []))
        self.assertEqual(A.aplicar("con orbalia", []), ("con orbalia", []))

    def test_varias_palabras(self):
        t, h = A.aplicar("el te  ese a", [{"de": "te ese a", "a": "TSA"}])
        self.assertEqual(t, "el TSA")
        self.assertEqual(h[0]["de"], "te  ese a")

    def entradas(self, texto):
        t, h = A.aplicar(texto, self.R)
        return t, [dict(x, start=0.0, texto=t) for x in h]

    def test_deshacer_preciso_y_posiciones_al_dia(self):
        t, es = self.entradas("orbalia con tesa y orbalia")
        self.assertEqual(t, "Orvalia con TSA y Orvalia")
        nuevo, quedan, n, s = A.deshacer(t, es, lambda e: e["a"] == "TSA")
        self.assertEqual((nuevo, n, s), ("Orvalia con tesa y Orvalia", 1, 0))
        self.assertEqual([nuevo[e["pos"]:e["pos"] + 7] for e in quedan], ["Orvalia", "Orvalia"])
        self.assertTrue(all(e["texto"] == nuevo for e in quedan))
        nuevo, quedan, n, s = A.deshacer(nuevo, quedan, lambda e: True)
        self.assertEqual((nuevo, quedan, n, s), ("orbalia con tesa y orbalia", [], 2, 0))

    def test_frase_cambiada_despues(self):
        t, es = self.entradas("hoy con orbalia")
        # la has tocado (otra palabra): una sola «Orvalia» y un solo cambio suyo: se encuentra
        self.assertEqual(A.posiciones("ayer con Orvalia", es, "hoy con orbalia"), [9])
        nuevo, quedan, n, s = A.deshacer("ayer con Orvalia", es, lambda e: True, "hoy con orbalia")
        self.assertEqual((nuevo, n, s), ("ayer con orbalia", 1, 0))
        # dos «Orvalia» y un solo cambio suyo: no se sabe cuál puso Tenca, no se toca ninguna
        self.assertEqual(A.posiciones("Orvalia con Orvalia", es, "hoy con orbalia"), [None])
        nuevo, quedan, n, s = A.deshacer("Orvalia con Orvalia", es, lambda e: True, "hoy con orbalia")
        self.assertEqual((nuevo, quedan, n, s), ("Orvalia con Orvalia", [], 0, 1))
        # lo de antes no estaba en lo que escribió Whisper: no es un cambio de Tenca
        self.assertEqual(A.posiciones("ayer con Orvalia", es, "hoy con otra cosa"), [None])
        self.assertEqual(A.posiciones(None, es), [None])

    def test_frase_cambiada_con_dos_cambios_del_mismo_termino(self):
        """Tenca cambió las dos «orbalia» y luego tocaste otra palabra: siguen siendo las dos suyas, en orden."""
        t, es = self.entradas("orbalia y orbalia hoy")
        self.assertEqual(A.posiciones("Orvalia y Orvalia ayer", es, "orbalia y orbalia hoy"), [0, 10])
        nuevo, quedan, n, s = A.deshacer("Orvalia y Orvalia ayer", es, lambda e: True, "orbalia y orbalia hoy")
        self.assertEqual((nuevo, n, s), ("orbalia y orbalia ayer", 2, 0))
        # si añadiste otra «Orvalia» tú, ya no cuadra: ninguna se marca ni se deshace
        self.assertEqual(A.posiciones("Orvalia y Orvalia y Orvalia", es, "orbalia y orbalia hoy"), [None, None])


# ----------------------------------------------------------------------------- añadir a una lista

class AnadirTests(TempTree):
    def leer(self, raw):
        p = os.path.join(self.tmp, "lista.txt")
        with open(p, "wb") as f:
            f.write(raw)
        return server.read_vocab(p)[1]

    def test_estilos_y_codificaciones(self):
        casos = [
            (b"SAP, TSA\n", b"SAP, TSA, Orval\xc3\xada\n"),
            (b"SAP, TSA\r\n", b"SAP, TSA, Orval\xc3\xada\r\n"),
            (b"SAP,TSA", b"SAP,TSA,Orval\xc3\xada"),
            (b"SAP, TSA,", b"SAP, TSA, Orval\xc3\xada"),
            (b"SAP;TSA", b"SAP;TSA;Orval\xc3\xada"),
            (b"SAP\tTSA", b"SAP\tTSA\tOrval\xc3\xada"),
            (b"SAP", b"SAP, Orval\xc3\xada"),
            (b"# desc\nSAP\nTSA\n", b"# desc\nSAP\nTSA\nOrval\xc3\xada\n"),
            (b"Contrato marco\r\nNDA\r\n", b"Contrato marco\r\nNDA\r\nOrval\xc3\xada\r\n"),
            (b"SAP\nTSA", b"SAP\nTSA\nOrval\xc3\xada"),
            (b"", b"Orval\xc3\xada\n"),
            (b"# solo descripcion\n", b"# solo descripcion\nOrval\xc3\xada\n"),
            (b"\xef\xbb\xbfSAP, TSA\n", b"\xef\xbb\xbfSAP, TSA, Orval\xc3\xada\n"),
            ("José, SAP\r\n".encode("cp1252"), "José, SAP, Orvalía\r\n".encode("cp1252")),
            ("SAP, TSA\r\n".encode("utf-16"), None),
            (b"\xfe\xff" + "SAP, TSA".encode("utf-16-be"), b"\xfe\xff" + "SAP, TSA, Orvalía".encode("utf-16-be")),
        ]
        for raw, esperado in casos:
            with self.subTest(raw=raw):
                nuevo = A.anadir_termino(raw, "Orvalía")
                if esperado is not None:
                    self.assertEqual(nuevo, esperado)
                antes = self.leer(raw)
                self.assertEqual(self.leer(nuevo), antes + ["Orvalía"])  # lo de antes, igual, y el término al final

    def test_lo_que_no_se_puede_sin_estropear(self):
        with self.assertRaises(A.NoSePuede):
            A.anadir_termino("José, SAP\n".encode("cp1252"), "Łódź")  # no cabe en cp1252
        with self.assertRaises(A.NoSePuede):
            A.anadir_termino(b"\xef\xbb\xbfSAP\n\xe9\n", "x")  # UTF-8 con BOM y una línea que no lo es
        with self.assertRaises(A.NoSePuede):
            A.anadir_termino(b"\xff\xfe\x00\xd8", "x")  # UTF-16 roto

    def test_vocabulario_general(self):
        self.assertEqual(A.anadir_texto("", "Orvalia"), "Orvalia")
        self.assertEqual(A.anadir_texto("SAP, TSA", "Orvalia"), "SAP, TSA, Orvalia")
        self.assertEqual(A.anadir_texto("SAP, TSA,\n", "Orvalia"), "SAP, TSA, Orvalia")
        self.assertEqual(A.anadir_texto("SAP\nTSA\n", "Orvalia"), "SAP\nTSA\nOrvalia")

    def test_termino_valido(self):
        self.assertEqual(A.termino_valido("  Orvalia   Sierra "), "Orvalia Sierra")
        self.assertEqual(A.termino_valido("a\tb"), "a b")
        for malo in ("", None, "a, b", "a;b", "#x", "x" * 81, " \n "):
            self.assertIsNone(A.termino_valido(malo), malo)


class SrtTests(unittest.TestCase):
    def test_tiempos_tal_cual(self):
        texto = srt_text([(0.0, 4.5, "Hola orbalia"), (5.123, 8.0, "Otra frase")])
        bs = A.srt_bloques(texto)
        self.assertEqual([b["start"] for b in bs], [0.0, 5.12])  # como parse_srt
        self.assertEqual(A.srt_texto(bs), texto)
        bs[0]["text"] = "Hola Orvalia"
        self.assertIn("00:00:05,123 --> 00:00:08,000", A.srt_texto(bs))
        self.assertEqual([s["text"] for s in server.parse_srt(A.srt_texto(bs))], ["Hola Orvalia", "Otra frase"])


# ----------------------------------------------------------------------------- el servidor

def settings_store(tmp, **extra):
    st = server.Store(os.path.join(tmp, "settings.json"), server.DEFAULT_SETTINGS)
    if extra:
        st.update(lambda d: d.update(extra))
    return st


def make_app(t, **st):
    a = server.App.__new__(server.App)
    a.settings = settings_store(t.tmp, **st)
    a.library = server.Library(server.Durations(os.path.join(t.data_dir, "durations.json")))
    a.transcripts = server.Transcripts()
    a.jobs = bare_jobs([])
    a.jobs.settings = a.settings
    a.jobs.schedule_outputs = lambda rel, delay=4.0: None
    return a


REGLA = {"de": "orbalia", "a": "Orvalia", "proyecto": "Cliente Alfa", "at": 1}


class ServidorTests(TempTree):
    def setUp(self):
        super().setUp()
        self.write("Cliente Alfa/Vocabulario/Finanzas.txt", "# Comité\nSAP, S/4HANA\n")
        self.write("Cliente Alfa/Vocabulario/Operaciones.txt", "WMS\n")
        self.app = make_app(self)
        self.addCleanup(server._TEMAS.clear)
        self._log = mock.patch.object(server, "log")
        self._log.start()
        self.addCleanup(self._log.stop)

    def reunion(self, rel, textos, proyecto="Cliente Alfa", workstream="Finanzas"):
        self.wav(rel, seconds=0.5)
        base = os.path.join(self.root, os.path.splitext(rel)[0])
        with open(base + ".srt", "w", encoding="utf-8") as f:
            f.write(srt_text([(i * 5.0, i * 5.0 + 4.0, t) for i, t in enumerate(textos)]))
        with open(base + ".txt", "w", encoding="utf-8-sig") as f:
            f.write("\n".join(textos) + "\n")
        if proyecto:
            with open(base + ".md", "w", encoding="utf-8") as f:
                f.write(f"---\nproyecto: {proyecto}\nworkstream: {workstream}\n---\n")
        return base

    def textos(self, base):
        return [s["text"] for s in server.parse_srt(server.read_text(base + ".srt"))]

    def corregir(self, rel, textos):
        """Como la interfaz al guardar una corrección a mano."""
        base = os.path.join(self.root, os.path.splitext(rel)[0])
        ss = server.parse_srt(server.read_text(base + ".srt"))
        self.app.save_transcript({"path": rel, "segments": [dict(s, text=t) for s, t in zip(ss, textos)]})

    def reglas(self, *rs):
        self.app.settings.update(lambda d: d.update(aprendido={"reglas": list(rs), "no": [], "no_reglas": []}))

    # --- la regla, al terminar una transcripción
    def test_solo_en_su_proyecto_y_con_copia_del_original(self):
        b1 = self.reunion("Cliente Alfa/r1.wav", ["Hoy con orbalia.", "Nada que ver."])
        b2 = self.reunion("Otro/r2.wav", ["Hoy con orbalia."], proyecto=None)
        self.reglas(REGLA)
        st = self.app.settings.get()
        self.assertEqual(server.aplicar_aprendido("Cliente Alfa/r1.wav", st), 1)
        self.assertEqual(server.aplicar_aprendido("Otro/r2.wav", st), 0)
        self.assertEqual(self.textos(b1), ["Hoy con Orvalia.", "Nada que ver."])
        with open(b1 + ".txt", encoding="utf-8-sig") as f:
            self.assertEqual(f.read(), "Hoy con Orvalia.\nNada que ver.\n")
        self.assertEqual(self.textos(b2), ["Hoy con orbalia."])
        self.assertFalse(os.path.isdir(server.backup_dir("Otro/r2.wav")))  # sin cambios, sin copia
        bdir = server.backup_dir("Cliente Alfa/r1.wav")
        with open(os.path.join(bdir, "original.srt"), encoding="utf-8") as f:
            self.assertIn("Hoy con orbalia.", f.read())
        with open(os.path.join(bdir, "meta.json"), encoding="utf-8") as f:
            self.assertTrue(json.load(f)["auto"])
        reg = server.registro_aprendido("Cliente Alfa/r1.wav")
        self.assertEqual([(c["start"], c["de"], c["a"], c["pos"]) for c in reg["cambios"]],
                         [(0.0, "orbalia", "Orvalia", 8)])
        # otra vez (una transcripción que continúa): lo ya cambiado no se repite
        self.assertEqual(server.aplicar_aprendido("Cliente Alfa/r1.wav", st), 0)

    def test_regla_sin_proyecto_solo_en_reuniones_sin_proyecto(self):
        self.reunion("Cliente Alfa/r1.wav", ["con orbalia"])
        b2 = self.reunion("Otro/r2.wav", ["con orbalia"], proyecto=None)
        self.reglas(dict(REGLA, proyecto=""))
        st = self.app.settings.get()
        self.assertEqual(server.aplicar_aprendido("Cliente Alfa/r1.wav", st), 0)
        self.assertEqual(server.aplicar_aprendido("Otro/r2.wav", st), 1)
        self.assertEqual(self.textos(b2), ["con Orvalia"])

    def test_el_proyecto_de_la_raiz_con_cualquiera_de_sus_nombres(self):
        self.write("Vocabulario/General.txt", "x\n")
        self.reunion("r.wav", ["con orbalia"], proyecto="Chuleta", workstream="General")
        self.reglas(dict(REGLA, proyecto=server.root_name()))
        self.assertEqual(server.aplicar_aprendido("r.wav", self.app.settings.get()), 1)

    def test_lo_corregido_a_mano_y_lo_deshecho_no_se_toca(self):
        b = self.reunion("Cliente Alfa/r1.wav", ["con orbalia", "y orbalia otra vez", "tercera"])
        self.corregir("Cliente Alfa/r1.wav", ["con orbalia", "y orbalia otra vez, dijo", "tercera"])  # tocó la 2.ª
        self.reglas(REGLA)
        st = self.app.settings.get()
        self.assertEqual(server.aplicar_aprendido("Cliente Alfa/r1.wav", st), 1)
        self.assertEqual(self.textos(b), ["con Orvalia", "y orbalia otra vez, dijo", "tercera"])
        server.deshacer_aprendido("Cliente Alfa/r1.wav", "orbalia", "Orvalia")
        self.assertEqual(self.textos(b)[0], "con orbalia")
        self.assertEqual(server.aplicar_aprendido("Cliente Alfa/r1.wav", st), 0)  # deshecha en esta reunión

    def test_sin_reglas_no_lee_nada(self):
        with mock.patch.object(server, "safe", side_effect=AssertionError("no debía leer")):
            self.assertEqual(server.aplicar_aprendido("no/existe.wav", self.app.settings.get()), 0)

    def test_after_transcribe_la_aplica_antes_que_lo_demas_y_un_fallo_no_lo_para(self):
        self.reunion("Cliente Alfa/r1.wav", ["con orbalia"])
        self.reglas(REGLA)
        orden = []
        jobs = bare_jobs([])
        jobs.settings = self.app.settings
        jobs.write_outputs = lambda rel: orden.append(("destinos", self.textos(
            os.path.join(self.root, "Cliente Alfa", "r1"))[0]))
        jobs.auto_voices = lambda rel: False
        jobs.auto_summary = lambda rel: orden.append(("resumen", None))
        jobs._after_transcribe("Cliente Alfa/r1.wav")
        self.assertEqual(orden, [("destinos", "con Orvalia"), ("resumen", None)])
        orden.clear()
        with mock.patch.object(server, "aplicar_aprendido", side_effect=RuntimeError("roto")), \
                mock.patch.object(server, "log_exc") as le:
            jobs._after_transcribe("Cliente Alfa/r1.wav")
        self.assertEqual([o[0] for o in orden], ["destinos", "resumen"])
        le.assert_called_once()

    # --- la API
    def test_propuesta_anadir_con_regla_y_no(self):
        rel = "Cliente Alfa/r1.wav"
        self.reunion(rel, ["Hoy con orbalia.", "El tesa de orbalia.", "Lo q dijimos."])
        self.assertEqual(self.app.aprender_get({"path": rel})["propuestas"], [])  # sin corregir, nada
        self.corregir(rel, ["Hoy con Orvalia.", "El TSA de Orvalia.", "Lo que dijimos."])
        r = self.app.aprender_get({"path": rel})
        fin = server.find_vocab("Cliente Alfa", "Finanzas")
        self.assertEqual(r["lista"], fin["id"])
        self.assertEqual(r["proyecto"], "Cliente Alfa")
        self.assertEqual(terminos(r["propuestas"]), ["Orvalia", "TSA"])
        self.assertEqual({(l["project"], l["name"]) for l in r["listas"]},
                         {("Cliente Alfa", "Finanzas"), ("Cliente Alfa", "Operaciones")})
        self.assertEqual(r["propuestas"][0]["en"], [])
        out = self.app.aprender_anadir({"path": rel, "termino": "orvalia", "lista": fin["id"], "reglas": ["Orbalia"]})
        self.assertEqual((out["ya_estaba"], out["cabe"], out["reglas"]), (False, True, 1))
        with open(fin["_abs"], encoding="utf-8") as f:
            self.assertEqual(f.read(), "# Comité\nSAP, S/4HANA, Orvalia\n")
        self.assertEqual(self.app.aprender_reglas({})["reglas"],
                         [{"de": "orbalia", "a": "Orvalia", "proyecto": "Cliente Alfa"}])
        # ya contestada en esta reunión: no se vuelve a proponer en ella; TSA sí, hasta que la contestas
        self.assertEqual(terminos(self.app.aprender_get({"path": rel})["propuestas"]), ["TSA"])
        out = self.app.aprender_anadir({"path": rel, "termino": "TSA", "lista": fin["id"], "reglas": []})
        self.assertEqual(out["reglas"], 0)
        self.assertEqual(self.app.aprender_get({"path": rel})["propuestas"], [])
        # sin la casilla, la regla no se vuelve a ofrecer en ninguna reunión
        self.assertEqual(server.aprendido_de(self.app.settings.get())["no_reglas"], [["tesa", "tsa"]])
        self.assertEqual(server.registro_aprendido(rel)["respondidas"], ["orvalia", "tsa"])

    def test_solo_lo_que_se_propone(self):
        rel = "Cliente Alfa/r1.wav"
        self.reunion(rel, ["Hoy con orbalia.", "y sap", "y sap"])
        self.corregir(rel, ["Hoy con Orvalia.", "y SAP", "y sap"])
        fin = server.find_vocab("Cliente Alfa", "Finanzas")["id"]
        for b, msg in (({"termino": "Otra"}, "ya no está"),
                       ({"termino": "Orvalia", "reglas": ["otra"]}, "ya no se puede"),
                       ({"termino": "Orvalia", "lista": "Cliente Alfa/Vocabulario/No.txt"}, "ya no existe"),
                       ({"termino": "SAP", "reglas": ["sap"]}, "ya no se puede")):  # «sap» queda: regla no segura
            with self.subTest(b=b), self.assertRaises(server.ApiError) as cm:
                self.app.aprender_anadir(dict({"path": rel, "lista": fin}, **b))
            self.assertIn(msg, cm.exception.msg)
        self.assertEqual(server.aprendido_de(self.app.settings.get())["reglas"], [])

    def test_no_y_vocabulario_general(self):
        rel = "Otro/r2.wav"
        self.reunion(rel, ["con orbalia", "con tesa"], proyecto=None)
        self.corregir(rel, ["con Orvalia", "con TSA"])
        r = self.app.aprender_get({"path": rel})
        self.assertEqual(r["lista"], "")
        self.app.aprender_no({"termino": "TSA"})
        self.assertEqual(terminos(self.app.aprender_get({"path": rel})["propuestas"]), ["Orvalia"])
        self.assertEqual(self.app.aprender_reglas({})["no"], ["TSA"])  # como lo escribiste, para Ajustes
        self.assertEqual(self.app.aprender_olvidar({"no": "tsa"})["no"], [])  # «quitar»: se vuelve a proponer
        self.assertEqual(terminos(self.app.aprender_get({"path": rel})["propuestas"]), ["Orvalia", "TSA"])
        self.app.aprender_no({"termino": "TSA"})
        self.app.settings.update(lambda d: d.update(prompt="SAP, TSA"))
        out = self.app.aprender_anadir({"path": rel, "termino": "Orvalia", "lista": "", "reglas": ["orbalia"]})
        self.assertEqual(out["prompt"], "SAP, TSA, Orvalia")
        self.assertEqual(self.app.settings.get()["prompt"], "SAP, TSA, Orvalia")
        self.assertEqual(server.aprendido_de(self.app.settings.get())["reglas"][0]["proyecto"], "")
        self.assertEqual(self.app.aprender_get({"path": rel})["propuestas"], [])

    def test_al_vocabulario_general_desde_una_reunion_con_tema_no_se_repite(self):
        """Revisión 1: tras añadirlo al vocabulario general, al volver a la reunión se proponía otra vez para su
        lista."""
        rel = "Cliente Alfa/r1.wav"
        self.reunion(rel, ["con orbalia"])
        self.corregir(rel, ["con Orvalia"])
        self.app.aprender_anadir({"path": rel, "termino": "Orvalia", "lista": "", "reglas": []})
        self.assertEqual(self.app.aprender_get({"path": rel})["propuestas"], [])

    def test_regla_de_otro_proyecto_se_ofrece_en_este(self):
        rel = "Cliente Alfa/r1.wav"
        self.reunion(rel, ["con orbalia"])
        self.corregir(rel, ["con Orvalia"])
        self.reglas(dict(REGLA, proyecto="Otro"))
        r = self.app.aprender_get({"path": rel})
        self.assertEqual(r["propuestas"][0]["antes"][0]["reglas_en"], ["Otro"])
        self.assertEqual(terminos(accionables(r)), ["Orvalia"])
        self.app.aprender_anadir({"path": rel, "termino": "Orvalia", "lista": r["lista"], "reglas": ["orbalia"]})
        self.assertEqual(sorted(x["proyecto"] for x in self.app.aprender_reglas({})["reglas"]),
                         ["Cliente Alfa", "Otro"])

    def test_regla_cuando_el_termino_ya_estaba_en_la_lista(self):
        rel = "Cliente Alfa/r1.wav"
        self.reunion(rel, ["con sap y es cuatro hana"])
        self.corregir(rel, ["con SAP y S/4HANA"])
        r = self.app.aprender_get({"path": rel})
        p = r["propuestas"][0]
        self.assertEqual(p["termino"], "SAP")
        self.assertEqual(p["en"], [r["lista"]])
        out = self.app.aprender_anadir({"path": rel, "termino": "SAP", "lista": r["lista"], "reglas": ["sap"]})
        self.assertTrue(out["ya_estaba"])
        with open(server.find_vocab(None, None, r["lista"])["_abs"], encoding="utf-8") as f:
            self.assertEqual(f.read(), "# Comité\nSAP, S/4HANA\n")  # el archivo, sin tocar

    def test_mientras_se_transcribe_nada(self):
        rel = "Cliente Alfa/r1.wav"
        self.reunion(rel, ["con orbalia"])
        self.corregir(rel, ["con Orvalia"])
        self.app.jobs.list.append(type("J", (), {"path": rel, "kind": "transcribe", "status": "running"})())
        self.assertEqual(self.app.aprender_get({"path": rel})["propuestas"], [])

    def test_lista_conectada_solo_lectura_y_cuando_vuelve(self):
        ext = os.path.join(self.tmp, "OneDrive", "Listas")
        os.makedirs(ext)
        lista = os.path.join(ext, "Legal.txt")
        with open(lista, "wb") as f:
            f.write(b"Contrato marco\r\nNDA\r\n")
        vid = server.link_id(ext)
        server.VOCAB_LINKS[vid] = {"name": "Listas", "path": ext}
        rel = "Cliente Alfa/r1.wav"
        self.reunion(rel, ["con tesa"])
        self.corregir(rel, ["con TSA"])
        lid = server.VLINK + vid + "/Legal.txt"
        os.chmod(lista, stat.S_IREAD)
        try:
            with self.assertRaises(server.ApiError) as cm:
                self.app.aprender_anadir({"path": rel, "termino": "TSA", "lista": lid, "reglas": ["tesa"]})
        finally:
            os.chmod(lista, stat.S_IREAD | stat.S_IWRITE)
        self.assertIn("No puedo escribir en «Legal.txt»: es de solo lectura", cm.exception.msg)
        self.assertEqual(server.aprendido_de(self.app.settings.get())["reglas"], [])  # sin el término, sin regla
        out = self.app.aprender_anadir({"path": rel, "termino": "TSA", "lista": lid, "reglas": ["tesa"]})
        self.assertEqual(out["lista"]["project"], "Listas")
        with open(lista, "rb") as f:
            self.assertEqual(f.read(), b"Contrato marco\r\nNDA\r\nTSA\r\n")
        self.assertEqual(server.aprendido_de(self.app.settings.get())["reglas"][0]["proyecto"], "Listas")

    def test_la_lista_de_la_reunion_sin_conexion(self):
        """Revisión 1: con su carpeta de listas sin conexión, la propuesta se iba en silencio al vocabulario general
        y «Añadir» decía «ya no existe»."""
        ext = os.path.join(self.tmp, "OneDrive", "Listas")
        os.makedirs(ext)
        with open(os.path.join(ext, "Legal.txt"), "w", encoding="utf-8") as f:
            f.write("NDA\n")
        vid = server.link_id(ext)
        server.VOCAB_LINKS[vid] = {"name": "Listas", "path": ext}
        rel = "Cliente Alfa/r1.wav"
        self.reunion(rel, ["con tesa"], proyecto="Listas", workstream="Legal")
        self.corregir(rel, ["con TSA"])
        lid = self.app.aprender_get({"path": rel})["lista"]
        self.assertEqual(lid, server.VLINK + vid + "/Legal.txt")
        shutil.move(ext, ext + " (sin conexión)")
        r = self.app.aprender_get({"path": rel})
        self.assertIsNone(r["lista"])
        self.assertEqual(r["falta"], {"proyecto": "Listas", "workstream": "Legal"})
        with self.assertRaises(server.ApiError) as cm:
            self.app.aprender_anadir({"path": rel, "termino": "TSA", "lista": lid, "reglas": []})
        self.assertIn("No encuentro tu carpeta de listas «Listas»: ¿sin conexión", cm.exception.msg)

    def test_motivos(self):
        v = {"_abs": os.path.join(self.tmp, "no", "existe", "Legal.txt")}
        self.assertIn("No encuentro la carpeta de «Legal.txt»", server.motivo_lista(FileNotFoundError(), v))
        v = {"_abs": os.path.join(self.root, "Cliente Alfa", "Vocabulario", "Finanzas.txt")}
        e = PermissionError(13, "x")
        e.winerror = 32
        self.assertIn("está abierta en otro programa", server.motivo_lista(e, v))
        self.assertIn("(OSError)", server.motivo_lista(OSError(5, "x"), v))
        v = server.find_vocab("Cliente Alfa", "Finanzas")
        with mock.patch.object(server.os, "stat", return_value=mock.Mock(st_file_attributes=0x400000, st_size=10)):
            with self.assertRaises(server.ApiError) as cm:
                server.anadir_a_lista(v, "Orvalia")
        self.assertIn("solo está en OneDrive", cm.exception.msg)

    def test_transcripcion_con_marcas_deshacer_y_olvidar(self):
        rel = "Cliente Alfa/r1.wav"
        b = self.reunion(rel, ["Hoy con orbalia y orbalia.", "Nada."])
        self.reglas(REGLA)
        server.aplicar_aprendido(rel, self.app.settings.get())
        t = self.app.transcript({"path": rel})
        self.assertTrue(t["has_backup"])
        self.assertEqual([(m["start"], m["pos"], m["len"], m["de"]) for m in t["aprendido"]["marcas"]],
                         [(0.0, 8, 7, "orbalia"), (0.0, 18, 7, "orbalia")])
        self.assertEqual(t["aprendido"]["reglas"], [{"de": "orbalia", "a": "Orvalia", "veces": 2}])
        # lo cambiado por Tenca no se propone como si lo hubieras corregido tú
        self.assertEqual(self.app.aprender_get({"path": rel})["propuestas"], [])
        out = self.app.aprender_deshacer({"path": rel, "de": "orbalia", "a": "Orvalia"})
        self.assertEqual(out, {"deshechos": 2, "no_deshechos": 0, "regla": {"proyecto": "Cliente Alfa"}})
        self.assertEqual(self.textos(b), ["Hoy con orbalia y orbalia.", "Nada."])
        self.assertIsNone(self.app.transcript({"path": rel})["aprendido"])
        with self.assertRaises(server.ApiError):
            self.app.aprender_deshacer({"path": rel, "de": "orbalia", "a": "Orvalia"})
        self.assertEqual(self.app.aprender_olvidar({"de": "Orbalia", "proyecto": "cliente alfa"}),
                         {"reglas": [], "no": []})

    def test_marcas_en_unidades_de_javascript(self):
        rel = "Cliente Alfa/r1.wav"
        self.reunion(rel, ["👍 con orbalia"])
        self.reglas(REGLA)
        server.aplicar_aprendido(rel, self.app.settings.get())
        m = self.app.transcript({"path": rel})["aprendido"]["marcas"][0]
        self.assertEqual((m["pos"], m["len"]), (len("👍 con ".encode("utf-16-le")) // 2, 7))  # 7, no 6

    def test_dos_frases_con_el_mismo_inicio_no_se_tocan(self):
        """Revisión 1: se cambiaban sin marca ni «Deshacer» (las marcas van por el inicio de la frase)."""
        rel = "Cliente Alfa/r1.wav"
        b = self.reunion(rel, ["x"])
        with open(b + ".srt", "w", encoding="utf-8") as f:
            f.write(srt_text([(10.0, 11.0, "con orbalia"), (10.0, 12.0, "y orbalia"), (13.0, 14.0, "otra orbalia")]))
        self.reglas(REGLA)
        self.assertEqual(server.aplicar_aprendido(rel, self.app.settings.get()), 1)
        self.assertEqual(self.textos(b), ["con orbalia", "y orbalia", "otra Orvalia"])
        self.assertEqual(len(self.app.transcript({"path": rel})["aprendido"]["marcas"]), 1)

    def test_el_registro_va_antes_que_el_texto(self):
        rel = "Cliente Alfa/r1.wav"
        b = self.reunion(rel, ["con orbalia"])
        self.reglas(REGLA)
        with mock.patch.object(server, "guardar_registro", side_effect=OSError("disco lleno")):
            with self.assertRaises(OSError):
                server.aplicar_aprendido(rel, self.app.settings.get())
        self.assertEqual(self.textos(b), ["con orbalia"])  # sin registro, sin cambio: nunca uno sin su marca

    def test_restaurar_el_original_se_lleva_lo_de_tenca(self):
        rel = "Cliente Alfa/r1.wav"
        b = self.reunion(rel, ["con orbalia"])
        self.reglas(REGLA)
        server.aplicar_aprendido(rel, self.app.settings.get())
        self.app.restore_transcript({"path": rel})
        self.assertEqual(self.textos(b), ["con orbalia"])
        self.assertEqual(server.registro_aprendido(rel)["cambios"], [])

    def test_deshacer_mientras_se_transcribe(self):
        rel = "Cliente Alfa/r1.wav"
        self.reunion(rel, ["con orbalia"])
        self.reglas(REGLA)
        server.aplicar_aprendido(rel, self.app.settings.get())
        self.app.jobs.list.append(type("J", (), {"path": rel, "kind": "transcribe", "status": "queued"})())
        with self.assertRaises(server.ApiError):
            self.app.aprender_deshacer({"path": rel, "de": "orbalia", "a": "Orvalia"})

    # --- el ajuste
    def test_ajuste_raro_no_rompe(self):
        malo = {"reglas": [None, {"de": "", "a": "x"}, {"de": "a"}], "no": [1, ""],
                "no_reglas": [["a"], ["a", 2], "ab"]}
        for raro in (None, [], "x", {"reglas": "x"}, malo):
            self.assertEqual(server.aprendido_de({"aprendido": raro}), {"reglas": [], "no": [], "no_reglas": []})
        r = server.aprendido_de({"aprendido": {"reglas": [{"de": "x", "a": "y", "proyecto": 3}]}})["reglas"]
        self.assertEqual(r, [{"de": "x", "a": "y", "proyecto": ""}])

    def test_renombrar_el_proyecto(self):
        d = {"aprendido": {"reglas": [dict(REGLA), dict(REGLA, de="tesa", proyecto="Otro")], "no": [], "no_reglas": []}}
        server.settings_moved(d, "", "", project=("cliente alfa", "Cliente Beta"))
        self.assertEqual([r["proyecto"] for r in d["aprendido"]["reglas"]], ["Cliente Beta", "Otro"])

    def test_se_rescata_de_un_settings_roto(self):
        bueno = json.dumps({"aprendido": {"reglas": [REGLA], "no": ["tsa"], "no_reglas": []}})
        self.assertEqual(server.Store._rescue((bueno[:-1] + ', "prompt": ').encode())["aprendido"]["reglas"], [REGLA])
        self.assertNotIn("aprendido", server.Store._rescue(b'{"aprendido": {"reglas": []}, "x":'))

    def test_el_diagnostico_no_lleva_nombres(self):
        for linea, esperado in (
                ("✦ Comité Orvalia.wav: 2 cambios con lo aprendido de tus correcciones",
                 "✦ <texto>: 2 cambios con lo aprendido de tus correcciones"),
                ("✦ aprendido: Orvalia en Cliente Alfa · Finanzas (1 regla)",
                 "✦ aprendido: <texto> en <texto> (1 regla)"),
                ("✦ regla olvidada: orbalia → Orvalia", "✦ regla olvidada: <texto>"),
                ("↺ Comité.wav: 1 cambio aprendido deshecho", "↺ <texto>: 1 cambio aprendido deshecho")):
            self.assertEqual(server.diag_line(linea), esperado)


class ColaTests(TempTree):
    """Jobs._run de verdad con un motor de mentira, como test_privacidad: la regla llega al texto al terminar una
    transcripción y también cuando el repaso final falla y se queda el texto del directo (revisión 1)."""
    REL = "Cliente Alfa/r.wav"

    def setUp(self):
        super().setUp()
        self.write("Cliente Alfa/Vocabulario/Finanzas.txt", "SAP\n")
        self.write("Cliente Alfa/r.md", "---\nproyecto: Cliente Alfa\nworkstream: Finanzas\n---\n")
        self.wav(self.REL, seconds=0.5)
        self.addCleanup(server._TEMAS.clear)

    def jobs(self, transcribe):
        j = bare_jobs([])
        nube = dict(server.DEFAULT_SETTINGS["cloud"], api_key="gsk_" + "0" * 20)  # falsa: el motor es de mentira
        apr = {"reglas": [REGLA], "no": [], "no_reglas": []}
        j.settings = settings_store(self.tmp, aprendido=apr, procesado="api", cloud=nube)
        j.library = types.SimpleNamespace(find=lambda rel: {}, at=1)
        j.submit_summary = lambda rel, auto=False: None
        j.local = j.cloud = types.SimpleNamespace(transcribe=transcribe)
        return j

    def correr(self, j, final_pass=False):
        jb = server.Job(self.REL, "r.wav", "nube", None)
        jb.final_pass = final_pass
        with j.lock:
            j.list.append(jb)
        with mock.patch.object(server, "log"):
            j._run(jb)
        return jb

    def texto(self):
        return [s["text"] for s in server.parse_srt(server.read_text(os.path.join(self.root, "Cliente Alfa", "r.srt")))]

    def test_al_terminar_una_transcripcion(self):
        j = self.jobs(lambda *a, **k: ([(0.0, 1.0, "hola orbalia"), (1.0, 1.9, "adiós")], "es", 2.0))
        jb = self.correr(j)
        self.assertEqual(jb.status, "done", jb.error)
        self.assertEqual(self.texto(), ["hola Orvalia", "adiós"])

    def test_si_el_repaso_final_falla_se_queda_el_directo_con_la_regla(self):
        with open(os.path.join(self.root, "Cliente Alfa", "r.srt"), "w", encoding="utf-8") as f:
            f.write(srt_text([(0.0, 1.0, "del directo con orbalia")]))

        def falla(*a, **k):
            raise engines.EngineError("HTTP 500")
        jb = self.correr(self.jobs(falla), final_pass=True)
        self.assertEqual(jb.status, "error")
        self.assertEqual(self.texto(), ["del directo con Orvalia"])


class CopiaOriginalTests(TempTree):
    def test_la_correccion_a_mano_sigue_igual(self):
        """La copia del original que hace la primera corrección a mano (ahora copia_original) no ha cambiado: sin
        «auto» en meta.json y con el .srt y el .txt de Whisper."""
        self.wav("r.wav", seconds=0.5)
        with open(os.path.join(self.root, "r.srt"), "w", encoding="utf-8") as f:
            f.write(srt_text([(0.0, 2.0, "uno")]))
        with open(os.path.join(self.root, "r.txt"), "w", encoding="utf-8-sig") as f:
            f.write("uno\n")
        app = make_app(self)
        with mock.patch.object(server, "log"):
            app.save_transcript({"path": "r.wav", "segments": [{"start": 0.0, "end": 2.0, "text": "dos"}]})
        bdir = server.backup_dir("r.wav")
        self.assertEqual(sorted(os.listdir(bdir)), ["meta.json", "original.srt", "original.txt"])
        with open(os.path.join(bdir, "meta.json"), encoding="utf-8") as f:
            self.assertNotIn("auto", json.load(f))
        self.assertFalse(server.copia_original("r.wav", os.path.join(self.root, "r")))  # ya hay copia: no se pisa
        shutil.rmtree(bdir)


if __name__ == "__main__":
    unittest.main()
