# -*- coding: utf-8 -*-
"""SRT, notas (.md), prompt de Whisper, filtros de segmentos y utilidades del resumen."""
import unittest

from support import engines, server, srt_text


class SrtTests(unittest.TestCase):
    def test_fmt_srt(self):
        self.assertEqual(server.fmt_srt(0), "00:00:00,000")
        self.assertEqual(server.fmt_srt(3661.5), "01:01:01,500")
        self.assertEqual(server.fmt_srt(59.9996), "00:01:00,000")  # redondea a milisegundos

    def test_parse_srt_roundtrip(self):
        segs = [(0.0, 1.5, "Hola, ¿qué tal?"), (1.5, 3.25, "Bien, gracias."), (3600.0, 3601.0, "Última")]
        out = server.parse_srt(srt_text(segs))
        self.assertEqual([(s["start"], s["end"], s["text"]) for s in out], segs)

    def test_parse_srt_tolera_crlf_puntos_y_varias_lineas(self):
        text = ("1\r\n00:00:01.5 --> 00:00:02,75\r\nprimera línea\r\nsegunda\r\n\r\n"
                "2\r\n00:00:03,000 --> 00:00:04,000\r\n\r\n")
        out = server.parse_srt(text)
        # el bloque sin texto se omite
        self.assertEqual(out, [{"start": 1.5, "end": 2.75, "text": "primera línea segunda"}])

    def test_parse_srt_vacio(self):
        self.assertEqual(server.parse_srt(""), [])


class NotesTests(unittest.TestCase):
    def test_roundtrip(self):
        props = {"titulo": "Kick-off", "etiquetas": ["a", "b c"], "estado": "Hecho", "proyecto": "Alfa",
                 "workstream": "Legal"}
        text = server.dump_notes(props, "## Notas\n\n- una\n")
        self.assertTrue(text.startswith("---\ntitulo: Kick-off\n"))
        p2, body = server.parse_notes(text)
        self.assertEqual(p2, props)
        self.assertEqual(body, "## Notas\n\n- una\n")

    def test_cabecera_desconocida_es_separador(self):
        props, body = server.parse_notes("---\nfoo: bar\n---\ntexto")
        self.assertEqual(props, {})
        self.assertEqual(body, "---\nfoo: bar\n---\ntexto")

    def test_vacio(self):
        self.assertEqual(server.dump_notes({}, "   \n"), "")
        self.assertEqual(server.parse_notes(""), ({}, ""))

    def test_claves_se_normalizan(self):
        props, _ = server.parse_notes("---\nTitulo:   Hola  \nEtiquetas: [x, , y]\n---\n")
        self.assertEqual(props, {"titulo": "Hola", "etiquetas": ["x", "y"]})


class PromptTests(unittest.TestCase):
    def test_split_terms(self):
        self.assertEqual(engines.split_terms(" a, b;c\n d ,, "), ["a", "b", "c", "d"])
        self.assertEqual(engines.split_terms(None), [])

    def test_build_prompt_recorta_por_el_final(self):
        count = lambda t: len(t.split())  # noqa: E731  (1 token por palabra, para que el test sea exacto)
        prompt, dropped = engines.build_prompt("Hola qué tal.", "uno, dos, tres, cuatro", count=count, limit=5)
        self.assertEqual(prompt, "Hola qué tal. uno, dos")
        self.assertEqual(dropped, ["tres", "cuatro"])

    def test_build_prompt_cabe_entero(self):
        prompt, dropped = engines.build_prompt("Estilo.", "a, b", count=lambda t: 1)
        self.assertEqual((prompt, dropped), ("Estilo. a, b", []))
        self.assertEqual(engines.build_prompt("", "", count=lambda t: 0), (None, []))

    def test_build_prompt_contexto_al_final_y_bytes(self):
        prompt, _ = engines.build_prompt("Estilo.", "a, b", count=lambda t: 1, context="lo último dicho")
        self.assertEqual(prompt, "Estilo. a, b lo último dicho")
        prompt, dropped = engines.build_prompt("Estilo.", "ñ" * 50 + ", b", count=lambda t: 1, max_bytes=40)
        self.assertLessEqual(len(prompt.encode("utf-8")), 40)
        self.assertTrue(dropped)

    def test_count_tokens_sin_modelo_estima(self):
        self.assertEqual(engines.count_tokens(""), 0)
        self.assertGreater(engines.count_tokens("hola que tal"), 0)


class SegmentTests(unittest.TestCase):
    def test_clean_segments_filtra(self):
        segs = [(0, 1, " Hola "), (1, 2, "Subtítulos realizados por la comunidad"), (2, 3, "Hola qué tal."),
                (3, 4, "I'm sorry"), (4, 5, "I'm sorry"), (5, 6, "ー音楽ー"), (6, 7, "")]
        out = engines.clean_segments(segs, prompt="Hola qué tal. Empezamos.", language="es")
        self.assertEqual([t for _, _, t in out], ["Hola", "I'm sorry"])

    def test_vocab_echo(self):
        terms = {"acme", "alfa", "beta", "gamma", "delta"}
        self.assertTrue(engines._vocab_echo("acme, alfa, beta, gamma, delta.", terms))
        self.assertFalse(engines._vocab_echo("acme y alfa hablaron de beta", terms))
        prompt = "Acme, Alfa, Beta, Gamma, Delta, Epsilon"
        out = engines.clean_segments([(0, 1, "Acme, Alfa, Beta, Gamma, Delta.")], prompt=prompt)
        self.assertEqual(out, [])

    def test_parse_verbose(self):
        res = {"segments": [{"start": 0, "end": 1, "text": "a", "no_speech_prob": 0.99, "avg_logprob": -1.5},
                            {"start": 1, "end": 2.5, "text": "b"}], "text": "a b"}
        self.assertEqual(engines.parse_verbose(res, 10.0, 13.0), [(11.0, 12.5, "b")])
        self.assertEqual(engines.parse_verbose({"text": "solo texto"}, 1.0, 2.0), [(1.0, 2.0, "solo texto")])

    def test_cut_points(self):
        parts = engines.CloudEngine._cut_points(1000.0, [(290.0, 292.0), (610.0, 612.0)], 300)
        self.assertEqual(parts, [(0.0, 291.0), (291.0, 611.0), (611.0, 911.0), (911.0, 1000.0)])  # ±45 s del objetivo
        self.assertEqual(engines.CloudEngine._cut_points(100.0, [], 300), [(0.0, 100.0)])
        self.assertEqual(engines.CloudEngine._cut_points(700.0, [], 300), [(0.0, 300), (300, 600), (600, 700.0)])


class SummaryTests(unittest.TestCase):
    def test_duration_s(self):
        self.assertAlmostEqual(engines._duration_s("6.08s"), 6.08)
        self.assertAlmostEqual(engines._duration_s("2m59.5s"), 179.5)
        self.assertAlmostEqual(engines._duration_s("1h2m"), 3720)
        self.assertEqual(engines._duration_s(None), 0.0)
        self.assertEqual(engines._duration_s("42"), 42.0)

    def test_lines_agrupa_con_minuto(self):
        segs = [{"start": 0, "text": "a" * 100}, {"start": 5, "text": "b" * 100}, {"start": 70, "text": "c" * 100},
                {"start": 3700, "text": "fin"}]
        lines = engines.Summarizer.lines(segs, width=250)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("[0:00] " + "a" * 100 + " " + "b" * 100))
        self.assertTrue(lines[1].startswith("[1:01:40] fin"))

    def test_stamp(self):
        self.assertEqual(engines._stamp(65), "1:05")
        self.assertEqual(engines._stamp(3661), "1:01:01")


class MiscTests(unittest.TestCase):
    def test_fold_y_natural_key(self):
        self.assertEqual(server.fold("Árbol CAFÉ"), "arbol cafe")
        self.assertLess(server.natural_key("2. b"), server.natural_key("10. a"))

    def test_clean_name(self):
        self.assertEqual(server.clean_name("  Hola. "), "Hola")
        for bad in ("a/b", "a:b", "..", "", "con?"):
            with self.assertRaises(server.ApiError):
                server.clean_name(bad)

    def test_masked(self):
        self.assertEqual(server.masked("gsk_1234567890abcdef"), "gsk_…cdef")
        self.assertEqual(server.masked("corta"), "••••")
        self.assertEqual(server.masked(""), "")


if __name__ == "__main__":
    unittest.main()
