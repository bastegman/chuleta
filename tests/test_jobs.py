# -*- coding: utf-8 -*-
"""Cola de trabajos, escritura de transcripciones (write_bytes, LiveOutputs) y correcciones en directo."""
import os
import unittest

from support import FakeJob, TempTree, bare_jobs, server, srt_text


class ActiveForTests(unittest.TestCase):
    def test_transcripcion_en_cola_o_en_marcha(self):
        jobs = bare_jobs([FakeJob("a.wav", status="queued"), FakeJob("b.wav", status="done"),
                          FakeJob("c.wav", status="running", live=True)])
        self.assertEqual(jobs.active_for("a.wav").path, "a.wav")
        self.assertIsNone(jobs.active_for("b.wav"))
        self.assertTrue(jobs.active_for("c.wav").live)
        self.assertIsNone(jobs.active_for("zzz.wav"))

    def test_cancel_y_clear(self):
        q, r, d = FakeJob("q.wav", status="queued"), FakeJob("r.wav", status="running"), FakeJob("d.wav", status="done")
        q.id, r.id, d.id = "q", "r", "d"
        jobs = bare_jobs([q, r, d])
        self.assertTrue(jobs.cancel("q"))
        self.assertEqual(q.status, "cancelled")
        self.assertTrue(jobs.cancel("r"))
        self.assertEqual((r.status, r.cancel, r.stage), ("running", True, "Cancelando…"))
        self.assertFalse(jobs.cancel("d"))
        jobs.clear()
        self.assertEqual([j.id for j in jobs.list], ["r"])


class WriteBytesTests(TempTree):
    def test_sustituye_en_el_sitio_y_trunca(self):
        p = os.path.join(self.root, "a.txt")
        server.write_bytes(p, b"contenido largo")
        server.write_bytes(p, b"corto")
        with open(p, "rb") as f:
            self.assertEqual(f.read(), b"corto")
        server.write_bytes(p, b"+mas", append=True)
        with open(p, "rb") as f:
            self.assertEqual(f.read(), b"corto+mas")

    def test_write_outputs_bom_en_txt(self):
        audio = os.path.join(self.root, "r.wav")
        server.write_outputs(audio, [(0.0, 1.0, "hola"), (1.0, 2.0, "adiós")])
        with open(os.path.join(self.root, "r.txt"), "rb") as f:
            self.assertEqual(f.read(), "﻿hola\nadiós\n".encode("utf-8"))
        with open(os.path.join(self.root, "r.srt"), "rb") as f:
            self.assertEqual(f.read().decode("utf-8"), srt_text([(0.0, 1.0, "hola"), (1.0, 2.0, "adiós")]))


class EditsTests(unittest.TestCase):
    def test_apply_edits(self):
        segs = [(0.0, 1.0, "uno"), (1.004, 2.0, "dos"), (2.0, 3.0, "tres")]
        edits = {server.seg_key(1.0): "DOS", server.seg_key(2.0): ""}
        self.assertEqual(server.apply_edits(segs, edits), [(0.0, 1.0, "uno"), (1.004, 2.0, "DOS")])
        self.assertEqual(server.apply_edits(segs, {}), segs)

    def test_seg_key_redondea_como_el_srt(self):
        self.assertEqual(server.seg_key(1.0049), server.seg_key("1.0"))
        self.assertEqual(server.seg_key(12.345), 12.35)


class LiveOutputsTests(TempTree):
    def read(self, ext):
        with open(os.path.join(self.root, "live" + ext), "rb") as f:
            return f.read().decode("utf-8-sig")

    def test_anade_y_reescribe(self):
        out = server.LiveOutputs(os.path.join(self.root, "live.wav"))
        s1 = [(0.0, 1.0, "uno"), (1.0, 2.0, "dos")]
        out.write(s1)
        size_txt = os.path.getsize(os.path.join(self.root, "live.txt"))
        out.write(s1 + [(2.0, 3.0, "tres")])  # se añade al final
        self.assertGreater(os.path.getsize(os.path.join(self.root, "live.txt")), size_txt)
        self.assertEqual(self.read(".txt"), "uno\ndos\ntres\n")
        self.assertEqual(self.read(".srt"), srt_text(s1 + [(2.0, 3.0, "tres")]))
        out.write(s1 + [(2.0, 3.0, "tres")])  # sin novedades: no toca nada
        self.assertEqual(self.read(".srt"), srt_text(s1 + [(2.0, 3.0, "tres")]))
        out.write([(0.0, 1.0, "UNO"), (1.0, 2.0, "dos")])  # cambia algo anterior: se reescribe entero
        self.assertEqual(self.read(".txt"), "UNO\ndos\n")
        self.assertEqual(self.read(".srt"), srt_text([(0.0, 1.0, "UNO"), (1.0, 2.0, "dos")]))

    def test_si_borran_el_archivo_se_reescribe(self):
        out = server.LiveOutputs(os.path.join(self.root, "live.wav"))
        out.write([(0.0, 1.0, "uno")])
        os.remove(os.path.join(self.root, "live.txt"))
        out.write([(0.0, 1.0, "uno"), (1.0, 2.0, "dos")])
        self.assertEqual(self.read(".txt"), "uno\ndos\n")


if __name__ == "__main__":
    unittest.main()
