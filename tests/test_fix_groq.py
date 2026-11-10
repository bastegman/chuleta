# -*- coding: utf-8 -*-
"""fix-groq (10/11/2026, msgs 1468-1474: «se queda loading y no hace transcript nunca… para mí debería seguir
funcionando»). Desde sin-groq (d74bf0a) el motor rechazaba unos ajustes con un proveedor vacío o desconocido, que hasta
fe29522 transcribían con Groq. Ahora solo los rechaza en «Probar» del asistente sin proveedor elegido (la marca
engines.SIN_PROVEEDOR que pone server._candidata); unos ajustes guardados siguen yendo a Groq, como siempre. Una
instalación nueva sigue sin sugerir Groq (test_sin_groq)."""
import shutil
import tempfile
import unittest

from support import TempTree, engines  # noqa: F401  (support prepara sys.path)
from test_e2e_procesado import CLAVE, GroqFalso

try:
    import av  # noqa: F401  (write_flac: la petición de verdad manda un FLAC)
except ImportError:  # pragma: no cover
    av = None


class MotorTests(unittest.TestCase):
    def test_ajustes_guardados_con_proveedor_vacio_van_a_groq(self):
        """Como en fe29522: «provider» vacío, desconocido o ausente en unos ajustes guardados = Groq."""
        for cfg in ({"provider": "", "api_key": "k"}, {"provider": "inventado", "api_key": "k"},
                    {"provider": None, "api_key": "k"}, {"api_key": "k"}):
            with self.subTest(cfg=cfg):
                url, headers, model = engines.CloudEngine.endpoint(cfg)
                self.assertEqual(url, engines.PROVIDERS["groq"]["url"])
                self.assertEqual(headers, {"Authorization": "Bearer k"})
                self.assertEqual(model, "whisper-large-v3-turbo")  # el primero de Groq, como antes
        self.assertEqual(engines.CloudEngine.endpoint({"provider": "", "api_key": "k", "model": "whisper-large-v3"})[2],
                         "whisper-large-v3")

    def test_probar_sin_proveedor_no_va_a_nadie(self):
        for p in ("", "inventado"):
            with self.subTest(p=p), self.assertRaises(engines.EngineError) as cm:
                engines.CloudEngine.endpoint({"provider": p, "api_key": "k", engines.SIN_PROVEEDOR: True})
            self.assertIn("Elige el proveedor", str(cm.exception))

    @unittest.skipUnless(av, "PyAV no instalado (el FLAC de la petición de verdad lo necesita)")
    def test_transcribe_de_verdad_contra_un_groq_falso(self):
        """Una petición real (CloudEngine.test, la de «Probar conexión») con unos ajustes guardados sin proveedor:
        llega a Groq con la clave guardada. Con 0d5169b fallaba antes de salir («Elige el proveedor…»)."""
        groq = GroqFalso()
        tmp = tempfile.mkdtemp(prefix="fixgroq-")
        antes = dict(engines.PROVIDERS["groq"])
        try:
            engines.PROVIDERS["groq"] = dict(antes, url=groq.base + "/audio/transcriptions")
            secs = engines.CloudEngine(tmp).test({"provider": "", "api_key": CLAVE, "model": "whisper-large-v3"})
            self.assertGreaterEqual(secs, 0)
            self.assertEqual(groq.peticiones, [("/openai/v1/audio/transcriptions", "whisper-large-v3", True)])
            with self.assertRaises(engines.EngineError):  # y «Probar» del asistente sin proveedor, no sale
                engines.CloudEngine(tmp).test({"provider": "", "api_key": CLAVE, engines.SIN_PROVEEDOR: True})
            self.assertEqual(len(groq.peticiones), 1)
        finally:
            engines.PROVIDERS["groq"] = antes
            groq.httpd.shutdown()
            shutil.rmtree(tmp, ignore_errors=True)


class CandidataTests(TempTree):
    def app(self, **st):
        from test_carpetas import make_app
        return make_app(self, **st)

    def test_la_marca_solo_en_la_prueba_sin_proveedor(self):
        a = self.app()
        st, _ = a._candidata({"candidata": {"procesado": "api", "cloud": {"provider": "", "api_key": "k"}}})
        self.assertTrue(st["cloud"].get(engines.SIN_PROVEEDOR))
        with self.assertRaises(engines.EngineError):
            engines.CloudEngine.endpoint(st["cloud"])
        st, _ = a._candidata({"candidata": {"procesado": "api", "cloud": {"provider": "groq", "api_key": "k"}}})
        self.assertNotIn(engines.SIN_PROVEEDOR, st["cloud"])
        self.assertNotIn(engines.SIN_PROVEEDOR, a.settings.get()["cloud"])  # la prueba no guarda nada

    def test_quien_ya_usa_groq_sin_proveedor_guardado(self):
        """Unos ajustes viejos con «provider» vacío y su clave: la prueba (sin elegir nada) y la cola, a Groq."""
        a = self.app(procesado="api", cloud={"provider": "", "api_key": "gsk_x"})
        st, _ = a._candidata({"candidata": {"procesado": "api", "cloud": {}}})
        self.assertNotIn(engines.SIN_PROVEEDOR, st["cloud"])  # elegido (procesado api): no se marca
        self.assertEqual(engines.CloudEngine.endpoint(st["cloud"])[0], engines.PROVIDERS["groq"]["url"])
        self.assertEqual(engines.CloudEngine.endpoint(a.settings.get()["cloud"])[0], engines.PROVIDERS["groq"]["url"])


if __name__ == "__main__":
    unittest.main()
