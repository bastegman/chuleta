# -*- coding: utf-8 -*-
"""Avisos de la franja del directo (rediseño B6, puntos 1-3; msg 1378):

1. Fuente callada (directo_avisos.Callada): el micro o el ordenador sin sonido más de un minuto, pero no por los
   silencios normales de una reunión (escuchar callado, la sala de espera, una reunión presencial, presentar tú).
2. Fin de la llamada (FinDeLlamada + recording.teams_mic): «paro en 30 s · No terminar»; nunca para si la llamada
   sigue, si la ventana de la reunión sigue abierta o si no se puede saber (cerrar Teams sí es colgar, msg 1479).
3. Idioma propuesto (Propuesta + worker._Escucha + live.run): con un idioma fijo, «Lo último suena a …» con varios
   trozos seguidos, nunca uno suelto, y nunca lo rechazado en esa grabación.
Y el servidor: /api/rec/status, la parada (igual que «Parar»), /api/rec/seguir y /api/live/propuesta.
Todo con dobles."""
import contextlib
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

from support import engines, live, server  # noqa: F401  (support prepara sys.path)
import directo_avisos as da  # noqa: E402
import recording  # noqa: E402
import worker  # noqa: E402
from test_directo import DirectoJob, FakeCloud, FakeSummarizer, FakeWorker, energy_vad, synth  # noqa: E402
from test_idioma_directo import TimedRecorder  # noqa: E402

SR = live.SR


def st(mic=None, sys_db=None, muted=False):
    out = {"t": 1.0, "mic_muted": muted}
    if mic is not None:
        out["mic"] = {"db": mic, "error": None}
    if sys_db is not None:
        out["sys"] = {"db": sys_db, "error": None}
    return out


def alimentar(c, segundos, mic=None, sys_db=None, t0=0.0, muted=False, llamada=False, paso=0.25):
    """Lecturas de la grabadora cada 0,25 s durante `segundos` desde t0; devuelve el reloj al acabar."""
    n = int(round(segundos / paso))
    t = t0
    for i in range(n):
        t = t0 + (i + 1) * paso
        c.feed(st(mic, sys_db, muted), t, llamada)
    return t


# ----------------------------------------------------------------------------- 1. fuente callada

class CalladaMicroTests(unittest.TestCase):
    def test_escuchar_callado_no_avisa(self):
        # 20 min sin hablar: el micro sigue dando el ruido de la sala (-58 dBFS de pico): no es silencio digital
        c = da.Callada()
        t = alimentar(c, 1200, mic=-58.0, sys_db=-20.0, llamada=True)
        self.assertEqual(c.estado(t), {})

    def test_micro_muerto_un_minuto_avisa(self):
        c = da.Callada()
        t = alimentar(c, 10, mic=-40.0)
        t = alimentar(c, 59, mic=-100.0, t0=t)
        self.assertEqual(c.estado(t), {})  # aún no: «más de un minuto»
        t = alimentar(c, 2, mic=-100.0, t0=t)
        self.assertEqual(set(c.estado(t)), {"mic"})
        self.assertGreaterEqual(c.estado(t)["mic"], 60)
        t = alimentar(c, 0.25, mic=-30.0, t0=t)  # vuelve el sonido: fuera el aviso
        self.assertEqual(c.estado(t), {})

    def test_muerto_desde_el_principio_tambien(self):
        # sin permiso del micro o el elegido equivocado: ceros desde el primer segundo
        c = da.Callada()
        t = alimentar(c, 61, mic=-100.0)
        self.assertIn("mic", c.estado(t))

    def test_el_umbral_es_el_silencio_digital(self):
        c = da.Callada()
        t = alimentar(c, 120, mic=-79.0)  # muy bajo pero no cero: un micro muy silencioso, no avisa
        self.assertEqual(c.estado(t), {})
        t = alimentar(c, 61, mic=-80.0, t0=t)
        self.assertIn("mic", c.estado(t))

    def test_silenciado_en_la_app_no_avisa(self):
        c = da.Callada()
        t = alimentar(c, 120, mic=-100.0, muted=True)
        self.assertEqual(c.estado(t), {})
        t = alimentar(c, 30, mic=-100.0, t0=t)  # al reactivarlo empieza a contar de cero
        self.assertEqual(c.estado(t), {})
        t = alimentar(c, 31, mic=-100.0, t0=t)
        self.assertIn("mic", c.estado(t))

    def test_micro_con_puerta_de_ruido(self):
        # da ceros entre frases (Krisp, algunos cascos): tras verlo volver solo dos veces, no se avisa del micro
        logs = []
        c = da.Callada(log=logs.append)
        t = 0.0
        for _ in range(2):
            t = alimentar(c, 3, mic=-25.0, t0=t)
            t = alimentar(c, 1.5, mic=-100.0, t0=t)
        t = alimentar(c, 3, mic=-25.0, t0=t)
        t = alimentar(c, 300, mic=-100.0, t0=t)  # 5 min escuchando: ceros, y es normal con ese micro
        self.assertEqual(c.estado(t), {})
        self.assertTrue(any("puerta de ruido" in x for x in logs), logs)

    def test_una_vuelta_sola_no_es_puerta(self):
        # silenciado con la tecla y vuelto a activar una vez: el siguiente silencio largo sí se avisa
        c = da.Callada()
        t = alimentar(c, 3, mic=-25.0)
        t = alimentar(c, 5, mic=-100.0, t0=t)
        t = alimentar(c, 3, mic=-25.0, t0=t)
        t = alimentar(c, 61, mic=-100.0, t0=t)
        self.assertIn("mic", c.estado(t))

    def test_lecturas_sin_t_o_raras_no_rompen(self):
        c = da.Callada()
        c.feed({"mic": {"db": -100}}, 1.0)  # sin «t» (no es un estado de grabación): se ignora
        c.feed(None, 2.0)
        c.feed({"t": 1, "mic": {"db": None}, "sys": "x"}, 3.0)
        self.assertEqual(c.estado(3.0), {})

    def test_el_log_dice_cuando_empieza_y_cuando_acaba(self):
        logs = []
        c = da.Callada(log=logs.append)
        t = alimentar(c, 62, mic=-100.0)
        t = alimentar(c, 1, mic=-30.0, t0=t)
        self.assertEqual(len([x for x in logs if "el micro no recibe sonido" in x]), 1, logs)
        self.assertEqual(len([x for x in logs if "el micro vuelve" in x]), 1, logs)


class CalladaOrdenadorTests(unittest.TestCase):
    def test_reunion_presencial_no_avisa(self):
        # sin llamada, el ordenador no suena y es normal
        c = da.Callada()
        t = alimentar(c, 600, mic=-30.0, sys_db=-100.0)
        self.assertEqual(c.estado(t), {})

    def test_sala_de_espera_no_avisa(self):
        # en llamada pero aún no ha sonado nada ni has hablado (esperas a los demás)
        c = da.Callada()
        t = alimentar(c, 300, mic=-58.0, sys_db=-100.0, llamada=True)
        self.assertEqual(c.estado(t), {})

    def test_en_llamada_tras_oir_a_los_demas_avisa(self):
        c = da.Callada()
        t = alimentar(c, 30, mic=-58.0, sys_db=-20.0, llamada=True)
        t = alimentar(c, 59, mic=-58.0, sys_db=-100.0, llamada=True, t0=t)
        self.assertEqual(c.estado(t), {})
        t = alimentar(c, 2, mic=-58.0, sys_db=-100.0, llamada=True, t0=t)
        self.assertEqual(set(c.estado(t)), {"sys"})
        t = alimentar(c, 0.25, mic=-58.0, sys_db=-30.0, llamada=True, t0=t)
        self.assertEqual(c.estado(t), {})

    def test_mientras_hablas_tu_no_cuenta(self):
        # presentas 5 min seguidos: que los demás callen es normal
        c = da.Callada()
        t = alimentar(c, 10, mic=-58.0, sys_db=-20.0, llamada=True)
        t = alimentar(c, 300, mic=-20.0, sys_db=-100.0, llamada=True, t0=t)
        self.assertEqual(c.estado(t), {})
        # las pausas cortas entre frases (< 5 s) tampoco
        for _ in range(30):
            t = alimentar(c, 6, mic=-20.0, sys_db=-100.0, llamada=True, t0=t)
            t = alimentar(c, 3, mic=-58.0, sys_db=-100.0, llamada=True, t0=t)
        self.assertEqual(c.estado(t), {})
        # al dejar de hablar, el minuto de escuchar sin oír a nadie sí
        t = alimentar(c, 66, mic=-58.0, sys_db=-100.0, llamada=True, t0=t)
        self.assertIn("sys", c.estado(t))
        self.assertGreater(c.estado(t)["sys"], 300)  # «desde hace»: desde la última vez que sonó

    def test_haber_hablado_tambien_activa_la_llamada(self):
        c = da.Callada()
        t = alimentar(c, 4, mic=-20.0, sys_db=-100.0, llamada=True)  # «¿me oís?»
        t = alimentar(c, 70, mic=-58.0, sys_db=-100.0, llamada=True, t0=t)
        self.assertIn("sys", c.estado(t))

    def test_al_colgar_se_olvida(self):
        c = da.Callada()
        t = alimentar(c, 10, mic=-58.0, sys_db=-20.0, llamada=True)
        t = alimentar(c, 70, mic=-58.0, sys_db=-100.0, llamada=True, t0=t)
        self.assertIn("sys", c.estado(t))
        t = alimentar(c, 1, mic=-58.0, sys_db=-100.0, llamada=False, t0=t)
        self.assertEqual(c.estado(t), {})

    def test_un_hueco_entre_lecturas_no_cuenta_entero(self):
        # el PC se durmió 5 min entre dos lecturas: no son 5 min de silencio escuchando
        c = da.Callada()
        t = alimentar(c, 10, mic=-58.0, sys_db=-20.0, llamada=True)
        t = alimentar(c, 20, mic=-58.0, sys_db=-100.0, llamada=True, t0=t)
        c.feed(st(-58.0, -100.0), t + 300, True)
        self.assertNotIn("sys", c.estado(t + 300))


class RecorderProcessCalladaTests(unittest.TestCase):
    def test_cada_estado_alimenta_el_aviso_y_un_fallo_no_para_la_lectura(self):
        rp = recording.RecorderProcess.__new__(recording.RecorderProcess)
        rp.path, rp.levels, rp.status, rp.ended = "x.wav", [], {}, False
        rp.callada, rp.en_llamada = da.Callada(), False
        lineas = [b'{"t": 1, "mic": {"db": -100}}\n', b'no es json\n', b'{"t": 2, "mic": {"db": -100}}\n']
        rp.p = types.SimpleNamespace(stdout=iter(lineas))
        relojes = iter([0.0, 61.0])
        with mock.patch.object(recording.time, "monotonic", lambda: next(relojes)), \
                mock.patch.object(recording, "close_wav", lambda p: None):
            rp._read()
        self.assertIn("mic", rp.callada.estado(61.0))
        rp2 = recording.RecorderProcess.__new__(recording.RecorderProcess)
        rp2.path, rp2.levels, rp2.status, rp2.ended, rp2.en_llamada = "x.wav", [], {}, False, False
        rp2.callada = types.SimpleNamespace(feed=mock.Mock(side_effect=RuntimeError("roto")))
        rp2.p = types.SimpleNamespace(stdout=iter([b'{"t": 1}\n', b'{"t": 2}\n']))
        with mock.patch.object(recording, "close_wav", lambda p: None):
            rp2._read()  # no lanza
        self.assertEqual(rp2.status, {"t": 2})
        self.assertTrue(rp2.ended)


# ----------------------------------------------------------------------------- 2. fin de la llamada

class FinTests(unittest.TestCase):
    def setUp(self):
        self.f = da.FinDeLlamada()
        self.t = 0.0
        self.ventanas = 0

    def tic(self, mic, ventana=None, dt=8.0, grab="a.wav"):
        """ventana: los títulos de las ventanas de reunión de Teams (set); por defecto, la de la reunión con el micro
        en uso y ninguna con el micro suelto. True = la de la reunión; False = ninguna; None explícito = no se leen."""
        self.t += dt
        if ventana is None:
            ventana = {"Comité"} if mic == "uso" else set()
        elif ventana is True:
            ventana = {"Comité"}
        elif ventana is False:
            ventana = set()

        def v():
            self.ventanas += 1
            return ventana
        return self.f.tic(grab, self.t, mic, v)

    def tic_ilegible(self, mic, dt=1.0):
        self.t += dt
        return self.f.tic("a.wav", self.t, mic, lambda: None)

    def llamada(self, minutos):
        acciones = [self.tic("uso") for _ in range(int(minutos * 60 / 8))]
        self.assertEqual(set(acciones), {None})

    def test_al_colgar_propone_y_para_a_los_30_s(self):
        self.llamada(20)
        self.assertEqual(self.f.titulos, {"Comité"})  # durante la llamada se apuntan sus ventanas
        self.assertIsNone(self.tic("libre"))  # 1.ª lectura con el micro suelto: aún no (confirmar 5 s)
        acciones = [self.tic("libre", dt=1.0) for _ in range(7)]
        self.assertEqual(acciones.count("propone"), 1, acciones)
        self.assertEqual(acciones.index("propone"), 4)  # a los 5 s
        self.assertEqual(self.f.quedan(self.t), 30 - 2)
        self.assertTrue(self.f.pendiente())
        acciones = [self.tic("libre", dt=1.0) for _ in range(31)]
        self.assertEqual(acciones.count("para"), 1, acciones)
        self.assertIsNone(self.f.quedan(self.t))

    def test_nunca_para_si_la_llamada_sigue(self):
        self.llamada(20)
        for _ in range(12):
            self.tic("libre", dt=1.0)
        self.assertIsNotNone(self.f.quedan(self.t))
        self.assertEqual(self.tic("uso", dt=1.0), "cancela")  # vuelve a entrar (se cortó la red): fuera el aviso
        self.assertIsNone(self.f.quedan(self.t))
        for _ in range(40):
            self.assertNotEqual(self.tic("uso", dt=1.0), "para")

    def test_con_la_ventana_de_la_reunion_abierta_no(self):
        # silenciado o en espera: Teams soltó el micro, pero la reunión sigue en pantalla
        self.llamada(20)
        acciones = [self.tic("libre", ventana=True, dt=1.0) for _ in range(120)]
        self.assertEqual(set(acciones), {None})
        acciones = [self.tic_ilegible("libre") for _ in range(120)]  # no se pueden leer: duda
        self.assertEqual(set(acciones), {None})

    def test_la_ventana_reaparece_en_la_cuenta_atras(self):
        self.llamada(20)
        for _ in range(12):
            self.tic("libre", dt=1.0)
        self.assertEqual(self.tic("libre", ventana=True, dt=1.0), "cancela")

    def test_si_no_se_sabe_que_procesos_hay_es_duda(self):
        self.llamada(20)
        acciones = [self.tic("duda", dt=1.0) for _ in range(120)]
        self.assertEqual(set(acciones), {None})
        for _ in range(12):
            self.tic("libre", dt=1.0)
        self.assertEqual(self.tic("duda", dt=1.0), "cancela")

    def test_la_reunion_siguiente_no_cancela(self):
        # cuelgas y entras en la siguiente antes de que acabe la cuenta (msg 1479): con su ventana (otro título), la
        # cuenta sigue y para; la reunión siguiente no acaba en la misma grabación
        for hueco in (6, 20, 29):
            self.setUp()
            self.llamada(30)
            acciones = [self.tic("libre", dt=1.0) for _ in range(hueco)]
            self.assertIn("propone", acciones, hueco)
            acciones = [self.tic("uso", ventana={"Seguimiento semanal"}, dt=1.0) for _ in range(40)]
            self.assertEqual(acciones.count("para"), 1, (hueco, acciones))
            self.assertNotIn("cancela", acciones)

    def test_volver_a_entrar_en_la_misma_cancela(self):
        self.llamada(30)
        for _ in range(10):
            self.tic("libre", dt=1.0)
        self.assertEqual(self.tic("uso", ventana={"Comité", "Seguimiento semanal"}, dt=1.0), "cancela")
        self.setUp()
        self.llamada(30)
        for _ in range(10):
            self.tic("libre", dt=1.0)
        self.assertEqual(self.tic("uso", ventana=set(), dt=1.0), "cancela")  # micro sin ventana: no se sabe
        self.setUp()
        self.llamada(30)
        for _ in range(10):
            self.tic("libre", dt=1.0)
        self.assertEqual(self.tic_ilegible("uso"), "cancela")

    def test_la_ventana_de_la_siguiente_no_impide_proponer(self):
        # al colgar se abre ya la ventana de la reunión siguiente (antes de entrar): las de esta llamada no están
        self.llamada(30)
        acciones = [self.tic("libre", ventana={"Seguimiento semanal"}, dt=1.0) for _ in range(8)]
        self.assertIn("propone", acciones)

    def test_sin_titulo_de_la_llamada_hace_falta_que_no_haya_ninguna_ventana(self):
        # la llamada sin ventana propia reconocible (no se apuntó ningún título): cualquier ventana de reunión, duda
        for _ in range(int(20 * 60 / 8)):
            self.tic("uso", ventana=set())
        acciones = [self.tic("libre", ventana={"Otra"}, dt=1.0) for _ in range(60)]
        self.assertEqual(set(acciones), {None})
        acciones = [self.tic("libre", ventana=set(), dt=1.0) for _ in range(8)]
        self.assertIn("propone", acciones)

    def test_llamada_que_empieza_tarde_en_una_presencial_no(self):
        # revisión del 10/11: reunión presencial grabada; a los 5 min, una llamada de Teams de 10 min (cubre el 67 %
        # hasta que termina): no es la de esta grabación
        for _ in range(int(300 / 8)):
            self.tic("libre")
        self.llamada(10)
        acciones = [self.tic("libre", dt=1.0) for _ in range(60)]
        self.assertEqual(set(acciones), {None})

    def test_llamada_en_los_dos_primeros_minutos_si(self):
        # lo que no se puede distinguir de grabar una reunión de Teams: la cuenta atrás y el aviso de Windows lo dicen
        for _ in range(int(90 / 8)):
            self.tic("libre")
        self.llamada(4)
        acciones = [self.tic("libre", dt=1.0) for _ in range(8)]
        self.assertIn("propone", acciones)

    def test_una_llamada_corta_en_una_reunion_presencial_no(self):
        # grabas una reunión presencial; a los 10 min entra una llamada de Teams de 3 min y cuelgas
        for _ in range(int(600 / 8)):
            self.tic("libre")
        self.llamada(3)
        acciones = [self.tic("libre", dt=1.0) for _ in range(60)]
        self.assertEqual(set(acciones), {None})

    def test_una_llamada_de_menos_de_2_min_no(self):
        self.llamada(1.5)
        acciones = [self.tic("libre", dt=1.0) for _ in range(60)]
        self.assertEqual(set(acciones), {None})

    def test_empezar_a_grabar_antes_de_entrar(self):
        # 1,5 min antes de entrar y 40 de llamada: vale; 4 min antes, ya no es «la de esta grabación» (VINCULO_S)
        for _ in range(int(90 / 8)):
            self.tic("libre")
        self.llamada(40)
        acciones = [self.tic("libre", dt=1.0) for _ in range(15)]
        self.assertIn("propone", acciones)
        self.setUp()
        for _ in range(int(240 / 8)):
            self.tic("libre")
        self.llamada(40)
        acciones = [self.tic("libre", dt=1.0) for _ in range(15)]
        self.assertEqual(set(acciones), {None})

    def test_seguir_grabando(self):
        self.llamada(20)
        for _ in range(12):
            self.tic("libre", dt=1.0)
        self.assertTrue(self.f.seguir_grabando())
        acciones = [self.tic("libre", dt=1.0) for _ in range(120)]
        self.assertEqual(set(acciones), {None})  # ya no se propone en esta llamada
        self.assertFalse(self.f.seguir_grabando())  # sin aviso: nada que quitar
        # otra llamada en la misma grabación (otra reunión, otro título) que también termina: sí
        acciones = [self.tic("uso", ventana={"Seguimiento"}) for _ in range(int(30 * 60 / 8))]
        self.assertEqual(set(acciones), {None})
        self.assertEqual(self.f.titulos, {"Seguimiento"})
        acciones = [self.tic("libre", dt=1.0) for _ in range(15)]
        self.assertIn("propone", acciones)

    def test_otra_grabacion_empieza_de_cero(self):
        self.llamada(20)
        for _ in range(12):
            self.tic("libre", dt=1.0)
        self.assertIsNone(self.tic("libre", dt=1.0, grab="b.wav"))
        self.assertIsNone(self.f.quedan(self.t))
        self.assertIsNone(self.tic("libre", dt=1.0, grab=None))
        self.assertFalse(self.f.pendiente())

    def test_una_suspension_no_suma_tiempo_de_llamada(self):
        self.tic("uso")
        self.tic("uso", dt=3600)  # el PC durmió una hora: cuenta como mucho TIC_MAX_S
        acciones = [self.tic("libre", dt=1.0) for _ in range(30)]
        self.assertEqual(set(acciones), {None})


class TeamsMicTests(unittest.TestCase):
    now = 1_800_000_000.0

    def ft(self, hace_s):
        return int((self.now - hace_s + 11644473600) * 10_000_000)

    def test_estados(self):
        pk = "MSTeams_8wekyb3d8bbwe"
        en_uso = [(pk, self.ft(600), 0)]
        self.assertEqual(recording.teams_mic(en_uso, self.now, {"ms-teams.exe"}), "uso")
        # figura en uso pero Teams ya no está en marcha: se ha cerrado Teams, y eso también es colgar (msg 1479)
        self.assertEqual(recording.teams_mic(en_uso, self.now, {"explorer.exe"}), "libre")
        self.assertEqual(recording.teams_mic([(pk, self.ft(600), self.ft(5))], self.now, {"ms-teams.exe"}), "libre")
        self.assertEqual(recording.teams_mic([], self.now, set()), "libre")
        # otra app con el micro (Zoom) o Tenca: no es Teams
        otros = [("Zoom.exe", self.ft(60), 0), ("C:#Python#pythonw.exe", self.ft(60), 0)]
        self.assertEqual(recording.teams_mic(otros, self.now, {"zoom.exe", "pythonw.exe"}), "libre")
        # rancia (más de 12 h abierta): no cuenta
        self.assertEqual(recording.teams_mic([(pk, self.ft(13 * 3600), 0)], self.now, {"ms-teams.exe"}), "libre")
        # no se sabe qué procesos hay: duda
        with mock.patch.object(recording, "running_images", lambda: None):
            self.assertEqual(recording.teams_mic(en_uso, self.now), "duda")
        # el clásico, NonPackaged con su ruta
        clasico = [("C:#Users#u#AppData#Local#Microsoft#Teams#current#Teams.exe", self.ft(60), 0)]
        self.assertEqual(recording.teams_mic(clasico, self.now, {"teams.exe"}), "uso")
        # una entrada abierta y otra cerrada: manda la abierta
        self.assertEqual(recording.teams_mic([(pk, self.ft(900), self.ft(800))] + en_uso, self.now,
                                             {"ms-teams.exe"}), "uso")

    def test_fuera_de_windows_duda(self):
        with mock.patch.object(recording, "WINDOWS", False):
            self.assertEqual(recording.teams_mic_estado(), "duda")

    def test_tasklist_una_vez_por_paso(self):
        llamadas = []

        def run(*a, **kw):
            llamadas.append(a)
            return types.SimpleNamespace(stdout='"ms-teams.exe","1"\n')
        recording._IMAGES.update(at=-1e9, set=None)
        with mock.patch.object(recording, "WINDOWS", True), mock.patch.object(recording.subprocess, "run", run):
            self.assertEqual(recording.running_images(), {"ms-teams.exe"})
            self.assertEqual(recording.running_images(), {"ms-teams.exe"})
        self.assertEqual(len(llamadas), 1)
        recording._IMAGES.update(at=-1e9, set=None)


# ----------------------------------------------------------------------------- 3. idioma propuesto

class PropuestaTests(unittest.TestCase):
    def test_un_trozo_suelto_no(self):
        p = da.Propuesta()
        p.oir("es", [("pt", 0.95, 40.0)])
        self.assertIsNone(p.publica())
        p.oir("es", [("pt", 0.9, 25.0)])
        self.assertEqual(p.publica(), {"idioma": "pt", "cambiar_a": "pt", "desde": "es"})

    def test_hace_falta_voz_de_sobra(self):
        p = da.Propuesta()
        for _ in range(5):
            p.oir("es", [("pt", 0.9, 5.0)])  # 25 s
        self.assertIsNone(p.publica())
        p.oir("es", [("pt", 0.9, 5.0)])
        self.assertIsNotNone(p.publica())

    def test_el_fijado_corta_la_racha(self):
        p = da.Propuesta()
        p.oir("es", [("pt", 0.9, 20.0)])
        p.oir("es", [("es", 0.8, 20.0)])
        p.oir("es", [("pt", 0.9, 20.0)])
        self.assertIsNone(p.publica())
        p.oir("es", [("pt", 0.9, 20.0)])
        self.assertIsNotNone(p.publica())

    def test_dudosos_y_otros_no_cuentan_ni_cortan(self):
        p = da.Propuesta()
        p.oir("es", [("pt", 0.9, 20.0)])
        p.oir("es", [("pt", 0.5, 20.0)])   # poca probabilidad
        p.oir("es", [("gl", 0.95, 20.0)])  # gallego: no se puede elegir en la app
        p.oir("es", [("pt", 0.9, 1.0)])    # casi sin voz
        p.oir("es", [("es", 0.3, 20.0)])   # el fijado, pero dudoso
        self.assertIsNone(p.publica())
        p.oir("es", [("pt", 0.9, 20.0)])
        self.assertEqual(p.publica()["idioma"], "pt")

    def test_otro_idioma_distinto_reinicia(self):
        p = da.Propuesta()
        p.oir("es", [("pt", 0.9, 20.0)])
        p.oir("es", [("en", 0.9, 20.0)])
        self.assertIsNone(p.publica())
        p.oir("es", [("en", 0.9, 20.0)])
        self.assertEqual(p.publica()["idioma"], "en")

    def test_sin_idioma_fijo_o_con_mezcla_no_se_propone(self):
        for fijo in (None, "", "es-en", "es-pt"):
            p = da.Propuesta()
            for _ in range(4):
                p.oir(fijo, [("fr", 0.99, 30.0)])
            self.assertIsNone(p.publica(), fijo)

    def test_rechazado_no_vuelve_en_esa_grabacion(self):
        rech = set()
        p = da.Propuesta(rech)
        for _ in range(2):
            p.oir("es", [("pt", 0.9, 20.0)])
        p.rechazar("pt")
        self.assertIsNone(p.publica())
        for _ in range(6):
            p.oir("es", [("pt", 0.99, 30.0)])
        self.assertIsNone(p.publica())
        q = da.Propuesta(rech)  # el directo se paró y se reanudó: misma grabación, mismo set
        for _ in range(6):
            q.oir("es", [("pt", 0.99, 30.0)])
        self.assertIsNone(q.publica())
        for _ in range(2):  # otro idioma sí
            q.oir("es", [("en", 0.99, 30.0)])
        self.assertEqual(q.publica()["idioma"], "en")

    def test_con_mucho_del_fijado_antes_propone_la_mezcla(self):
        p = da.Propuesta()
        for _ in range(3):
            p.oir("es", [("es", 0.9, 25.0)])  # 75 s en español
        for _ in range(2):
            p.oir("es", [("pt", 0.9, 20.0)])
        self.assertEqual(p.publica(), {"idioma": "pt", "cambiar_a": "es-pt", "desde": "es"})
        q = da.Propuesta()  # francés no tiene mezcla con español: el idioma solo
        for _ in range(3):
            q.oir("es", [("es", 0.9, 25.0)])
        for _ in range(2):
            q.oir("es", [("fr", 0.9, 20.0)])
        self.assertEqual(q.publica()["cambiar_a"], "fr")
        r = da.Propuesta()
        for _ in range(3):
            r.oir("en", [("en", 0.9, 25.0)])
        for _ in range(2):
            r.oir("en", [("es", 0.9, 20.0)])
        self.assertEqual(r.publica()["cambiar_a"], "es-en")

    def test_se_retira_si_vuelve_el_fijado(self):
        p = da.Propuesta()
        for _ in range(2):
            p.oir("es", [("pt", 0.9, 20.0)])
        p.oir("es", [("es", 0.9, 10.0)])
        self.assertIsNotNone(p.publica())  # un trozo suelto en español no la quita
        p.oir("es", [("es", 0.9, 12.0)])
        self.assertIsNone(p.publica())

    def test_al_cambiar_el_idioma_se_olvida(self):
        p = da.Propuesta()
        for _ in range(2):
            p.oir("es", [("pt", 0.9, 20.0)])
        p.oir("pt", [])
        self.assertIsNone(p.publica())

    def test_en_la_nube_sin_probabilidad(self):
        p = da.Propuesta()
        for _ in range(6):
            p.oir("es", [("pt", None, 5.5)])
        self.assertEqual(p.publica()["idioma"], "pt")
        # si la API devuelve el idioma pedido (sin detectar), nunca se propone nada
        q = da.Propuesta()
        for _ in range(50):
            q.oir("es", [("es", None, 5.5)])
        self.assertIsNone(q.publica())


class _ModeloNativo:
    """El Whisper nativo (ctranslate2) de mentira: detect_language anota la forma de lo que recibe."""

    def __init__(self, fallar=False):
        self.formas, self.gen, self.fallar, self.is_multilingual = [], [], fallar, True

    def detect_language(self, features):
        if self.fallar:
            raise RuntimeError("formato")
        self.formas.append(features.shape)
        return [[("<|pt|>", 0.91234), ("<|es|>", 0.05)]]

    def generate(self, features, prompts, **kw):
        self.gen.append((features.shape, prompts, kw))
        return ["resultado"]


class EscuchaTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np = np
        fake = types.ModuleType("ctranslate2")
        fake.StorageView = types.SimpleNamespace(from_array=lambda a: a)
        self.mod = mock.patch.dict(sys.modules, {"ctranslate2": fake})
        self.mod.start()
        self.addCleanup(self.mod.stop)

    def test_oye_el_primer_bloque_y_genera_igual(self):
        inner = _ModeloNativo()
        m = types.SimpleNamespace(model=inner)
        enc = self.np.zeros((3, 1500, 8), self.np.float32)
        with worker.escuchar(m, True) as oido:
            self.assertIsNot(m.model, inner)
            self.assertTrue(m.model.is_multilingual)  # lo demás pasa al nativo
            self.assertEqual(m.model.generate(enc, [[1]], beam_size=1), ["resultado"])
            m.model.generate(enc, [[2]])  # una segunda tanda en el mismo trozo: no se vuelve a oír
        self.assertIs(m.model, inner)
        self.assertEqual(oido, [("pt", 0.912)])
        self.assertEqual(inner.formas, [(1, worker.OIR_MARCOS, 8)])  # solo el primer bloque y sus 10 s
        self.assertEqual([g[0] for g in inner.gen], [(3, 1500, 8), (3, 1500, 8)])  # generate, con todo
        self.assertEqual(inner.gen[0][2], {"beam_size": 1})

    def test_si_oir_falla_se_transcribe_igual(self):
        inner = _ModeloNativo(fallar=True)
        m = types.SimpleNamespace(model=inner)
        with worker.escuchar(m, True) as oido:
            self.assertEqual(m.model.generate(self.np.zeros((1, 1500, 8)), [[1]]), ["resultado"])
        self.assertEqual(oido, [])
        self.assertIs(m.model, inner)

    def test_apagado_no_toca_el_modelo(self):
        inner = _ModeloNativo()
        m = types.SimpleNamespace(model=inner)
        with worker.escuchar(m, False) as oido:
            self.assertIs(m.model, inner)
        self.assertEqual(oido, [])


# ----------------------------------------------------------------------------- live.run

SPANS = [(0.0, 3.4), (4.0, 7.6), (8.2, 11.3), (11.9, 15.0), (16.0, 19.2), (20.0, 23.1), (24.0, 27.0),
         (28.0, 31.0)]
LARGO = [(i * 4.0, i * 4.0 + 3.4) for i in range(40)]  # 160 s de frases de 3,4 s


class OidoWorker(FakeWorker):
    """Whisper local de mentira que oye `oye` (idioma) en cada ventana, si se le pide (req["oir"])."""

    def __init__(self, rec, oye="pt"):
        super().__init__(rec)
        self.reqs, self.oye = [], oye

    def request(self, req, audio=None, on_progress=None, timeout=None, idle_timeout=None, cancelled=None):
        self.reqs.append(dict(req))
        out = super().request(req, audio, on_progress, timeout, idle_timeout, cancelled)
        out["language"] = req["language"] or "es"
        out["oido"] = [(self.oye, 0.93)] if req.get("oir") else []
        return out


class OyeCloud(FakeCloud):
    """La nube dice que suena a `oye` aunque se le pida otro idioma (si la API lo hiciera así)."""

    def __init__(self, rec, oye="portuguese"):
        super().__init__(rec)
        self.oye = oye

    def transcribe_array(self, cfg, audio, language, prompt, tmp_dir, job_id, stage, cancelled, **kw):
        out, _ = super().transcribe_array(cfg, audio, language, prompt, tmp_dir, job_id, stage, cancelled, **kw)
        return out, self.oye


class LivePropuestaTests(unittest.TestCase):
    def session(self, spans, total, preset="medium", language="es", oye="pt", cloud_oye="portuguese", events=(),
                rechazos=None):
        tmp = tempfile.mkdtemp(prefix="notas-avisos-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "reunion.wav")
        opts = {"preset": preset, "language": language, "prompt": None, "about": None, "threads": 1, "tmp_dir": tmp,
                "cloud_cfg": {"provider": "groq", "api_key": "k1"}, "resume": [], "summarizer": FakeSummarizer()}
        if rechazos is not None:
            opts["rechazos"] = rechazos
        job = DirectoJob(preset=preset)
        vistos = []
        rec = TimedRecorder(path, synth(spans, total),
                            [(t, (lambda f=f: f(opts, job, vistos))) for t, f in events])
        opts.update(cloud=OyeCloud(rec, cloud_oye), worker=OidoWorker(rec, oye))
        logs = []
        with mock.patch.object(live, "time", types.SimpleNamespace(time=rec.time, sleep=rec.sleep)), \
                mock.patch.object(live, "speech", energy_vad):
            rec.sleep(0.6)
            segs, lang = live.run(job, path, opts, lambda s: None, logs.append)
        return types.SimpleNamespace(job=job, opts=opts, logs=logs, segs=segs, lang=lang, vistos=vistos)

    def test_en_local_con_varios_trozos_lo_propone(self):
        visto = lambda o, j, v: v.append((j.directo or {}).get("propuesta"))  # noqa: E731
        r = self.session(LARGO, 165.0, events=[(t, visto) for t in range(20, 165, 5)])
        reqs = r.opts["worker"].reqs
        self.assertTrue(any(q["oir"] for q in reqs), reqs)
        self.assertTrue(any(v == {"idioma": "pt", "cambiar_a": "pt", "desde": "es"} for v in r.vistos), r.vistos)
        # la primera vez, nunca con un trozo solo
        k = next(i for i, v in enumerate(r.vistos) if v)
        self.assertGreaterEqual(sum(1 for q in reqs if q["oir"]), 2)
        self.assertTrue(all(v is None for v in r.vistos[:k]))
        self.assertTrue(any("lo último suena a pt (fijado: es); se propone pt" in x for x in r.logs), r.logs)

    def test_sin_idioma_fijo_o_con_mezcla_no_se_pide_oir(self):
        for lang in (None, "es-pt"):
            r = self.session(LARGO[:20], 85.0, language=lang)
            self.assertFalse(any(q.get("oir") for q in r.opts["worker"].reqs), lang)
            self.assertIsNone(r.job.directo["propuesta"])

    def test_si_suena_al_fijado_nada(self):
        r = self.session(LARGO, 165.0, oye="es")
        self.assertIsNone(r.job.directo["propuesta"])
        self.assertFalse(any("suena a" in x for x in r.logs))

    def test_rechazado_en_esta_grabacion_no(self):
        r = self.session(LARGO, 165.0, rechazos={"pt"})
        self.assertIsNone(r.job.directo["propuesta"])

    def test_cambiar_al_propuesto_lo_quita_y_rehace(self):
        def cambiar(o, j, v):
            p = (j.directo or {}).get("propuesta")
            if p and not v:
                v.append(p)
                o["language"] = p["cambiar_a"]  # «Cambiar» = el cambio de idioma de siempre (Jobs.refresh_prompt)
        r = self.session(LARGO, 165.0, events=[(t, cambiar) for t in range(20, 160, 2)])
        self.assertTrue(r.vistos, r.logs)
        self.assertIsNone(r.job.directo["propuesta"])
        self.assertTrue(any("idioma pt desde ya" in x for x in r.logs), r.logs)

    def test_en_la_nube_lo_dice_la_respuesta_y_cambiar_rehace(self):
        # si la API dijera en language que suena a portugués aunque se le pidió español: se propone, y el texto
        # sigue contando como español (el que se pidió), así que «Cambiar» rehace lo último en portugués
        def cambiar(o, j, v):
            p = (j.directo or {}).get("propuesta")
            if p and not v:
                v.append(p)
                o["language"] = p["cambiar_a"]
        r = self.session(LARGO, 165.0, preset="nube", events=[(t, cambiar) for t in range(10, 160, 1)])
        self.assertEqual(r.vistos[:1], [{"idioma": "pt", "cambiar_a": "pt", "desde": "es"}], r.logs)
        self.assertTrue(any("idioma pt desde ya; se rehacen en él los últimos" in x for x in r.logs), r.logs)

    def test_en_la_nube_con_una_mezcla_el_idioma_es_el_detectado(self):
        # revisión del 10/11: con «es-pt» fijado, lang tiene que seguir siendo el detectado (es), no «es-pt», o el
        # filtro de ecos de clean_segments deja pasar las alucinaciones en otro alfabeto
        r = self.session(LARGO[:20], 85.0, preset="nube", language="es-pt", cloud_oye="spanish")
        self.assertEqual(r.lang, "es")
        self.assertEqual(r.job.directo["lang"], "es")
        self.assertIsNone(r.job.directo["propuesta"])

    def test_en_la_nube_con_el_idioma_que_se_pidio_nada(self):
        r = self.session(LARGO, 165.0, preset="nube", cloud_oye="spanish")
        self.assertIsNone(r.job.directo["propuesta"])
        self.assertEqual(r.lang, "es")


# ----------------------------------------------------------------------------- servidor

class FakeRec:
    def __init__(self, path):
        self.path, self.started, self.status = path, 0.0, {"t": 12.0, "mic": {"db": -100}}
        self.callada, self.en_llamada, self._alive, self.stopped = da.Callada(), False, True, 0

    def alive(self):
        return self._alive

    def stop(self, timeout=10):
        self.stopped += 1
        self._alive = False
        return 125.0

    def timeline(self):
        return None


class ServidorTests(unittest.TestCase):
    def setUp(self):
        self.tree = tempfile.mkdtemp(prefix="notas-avisos-srv-")
        self.addCleanup(shutil.rmtree, self.tree, ignore_errors=True)
        self.app = server.App.__new__(server.App)
        self.app.fin_llamada, self.app.parada_auto = da.FinDeLlamada(), None
        self.app.toggle_lock, self.app.rec_lock = server.threading.Lock(), server.threading.Lock()
        self.app.monitor, self.app.meeting = None, None
        self.app.vigia = types.SimpleNamespace(avisado=None)
        self.app.jobs = types.SimpleNamespace(claimed=set())
        self.app.library = types.SimpleNamespace(at=1)
        self.app._movs = lambda: {}
        self.wav = os.path.join(self.tree, "r.wav")
        self.app.rec = FakeRec(self.wav)
        self.ajustes = {"meeting_detect": True}
        self.app.settings = types.SimpleNamespace(get=lambda: self.ajustes)
        self.notas, self.logs = [], []
        self.reloj = [1000.0]
        self.mic = ["uso"]
        self.ventana = [set()]
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        for target, attr, val in ((server, "notify", lambda t, b: self.notas.append((t, b))),
                                  (server, "cue", lambda k: None), (server, "log", self.logs.append),
                                  (server, "rel_of", lambda p: os.path.basename(p)),
                                  (server, "teams_estado", lambda: (self.mic[0], lambda: self.ventana[0])),
                                  (server.time, "monotonic", lambda: self.reloj[0])):
            self.patches.enter_context(mock.patch.object(target, attr, val))

    def tic(self, mic, dt=8.0):
        self.reloj[0] += dt
        self.mic[0] = mic
        self.ventana[0] = {"Comité"} if mic == "uso" else set()
        self.app._fin_llamada_tic()

    def test_estado_lleva_los_avisos(self):
        self.app.rec.callada.feed({"t": 1, "mic": {"db": -100}}, 0.0)
        self.app.rec.callada.feed({"t": 2, "mic": {"db": -100}}, 70.0)
        self.reloj[0] = 70.0
        s = self.app.rec_status({})
        self.assertEqual(s["callada"], {"mic": 70})
        self.assertIsNone(s["fin_llamada"])

    def test_al_colgar_avisa_y_para_como_parar(self):
        for _ in range(int(20 * 60 / 8)):
            self.tic("uso")
        for _ in range(12):
            self.tic("libre", dt=1.0)
        self.assertEqual(self.app.rec_status({})["fin_llamada"]["app"], "Teams")
        self.assertTrue(any("La llamada ha terminado" == t for t, _ in self.notas), self.notas)
        rec = self.app.rec
        self.app.vigia.avisado = "Teams"
        visto = []
        stop = rec.stop
        rec.stop = lambda timeout=10: (visto.append(self.app.parada_auto), stop(timeout))[1]
        for _ in range(31):
            self.tic("libre", dt=1.0)
        self.assertEqual(rec.stopped, 1)  # rec_stop de siempre: la grabadora se para y se guarda
        # el porqué ya estaba puesto al parar (el sondeo que ve «ya no se graba» lo ve también)
        self.assertEqual(visto[0]["motivo"], "llamada")
        self.assertIsNone(self.app.vigia.avisado)  # la reunión siguiente puede volver a ofrecer «● Grabar»
        self.assertIsNone(self.app.rec)
        s = self.app.rec_status({})
        self.assertFalse(s["recording"])
        self.assertEqual(s["parada"]["motivo"], "llamada")
        self.assertEqual(self.app.rec_seguir({}), {"ok": True, "seguia": False, "grabando": False})  # clic tarde
        self.assertTrue(any("parada al terminar la llamada" in x for x in self.logs), self.logs)

    def test_seguir_grabando(self):
        for _ in range(int(20 * 60 / 8)):
            self.tic("uso")
        for _ in range(12):
            self.tic("libre", dt=1.0)
        self.assertEqual(self.app.rec_seguir({}), {"ok": True, "seguia": True, "grabando": True})
        for _ in range(60):
            self.tic("libre", dt=1.0)
        self.assertEqual(self.app.rec.stopped, 0)
        self.assertIsNone(self.app.rec_status({})["fin_llamada"])

    def test_con_la_deteccion_de_llamadas_apagada_no(self):
        self.ajustes["meeting_detect"] = False
        for _ in range(int(20 * 60 / 8)):
            self.tic("uso")
        for _ in range(60):
            self.tic("libre", dt=1.0)
        self.assertEqual(self.app.rec.stopped, 0)
        self.assertIsNone(self.app.rec_status({})["fin_llamada"])

    def test_si_ya_se_paro_a_mano_no_hace_nada(self):
        for _ in range(int(20 * 60 / 8)):
            self.tic("uso")
        for _ in range(12):
            self.tic("libre", dt=1.0)
        self.app.rec._alive = False  # «Parar» a mano en medio de la cuenta atrás
        for _ in range(40):
            self.tic("libre", dt=1.0)
        self.assertEqual(self.app.rec.stopped, 0)

    def test_nunca_para_otra_grabacion(self):
        # «Parar» a mano y otra grabación nueva justo entre la decisión de parar y la parada: la nueva no se toca
        nueva = FakeRec(os.path.join(self.tree, "nueva.wav"))

        def tic(grabacion, now, mic, ventana_fn=None):
            self.app.rec = nueva
            return "para"
        self.app.fin_llamada.tic = tic
        self.tic("libre", dt=1.0)
        self.assertIs(self.app.rec, nueva)
        self.assertEqual(nueva.stopped, 0)
        self.assertIsNone(self.app.parada_auto)

    def test_teams_estado_de_prueba(self):
        self.patches.close()
        tmp = os.path.join(self.tree, "teams.json")
        with mock.patch.dict(os.environ, {"CHULETA_PRUEBAS_TEAMS": tmp}):
            self.assertEqual(server.teams_estado()[0], "duda")  # sin fichero: duda, nunca para
            with open(tmp, "w", encoding="utf-8") as f:
                f.write('{"mic": "libre", "ventanas": ["Comité"]}')
            mic, v = server.teams_estado()
            self.assertEqual((mic, v()), ("libre", {"Comité"}))
            with open(tmp, "w", encoding="utf-8") as f:
                f.write('{"mic": "uso"}')  # sin «ventanas»: no se pueden leer
            mic, v = server.teams_estado()
            self.assertEqual((mic, v()), ("uso", None))


class PropuestaApiTests(unittest.TestCase):
    def test_rechazar(self):
        jobs = types.SimpleNamespace()
        job = types.SimpleNamespace(id="j1", name="r", opts={"rechazos": set()}, propuesta=da.Propuesta(),
                                    directo={"propuesta": {"idioma": "pt"}}, public=lambda: {"id": "j1"})
        job.propuesta.rechazos = job.opts["rechazos"]
        app = server.App.__new__(server.App)
        app.jobs = jobs
        jobs._live_job = lambda i: job
        with mock.patch.object(server, "log", lambda m: None):
            self.assertEqual(app.live_propuesta({"id": "j1", "rechazar": "pt"}), {"id": "j1"})
            with self.assertRaises(server.ApiError):
                app.live_propuesta({"id": "j1", "rechazar": "xx<script>"})
        self.assertEqual(job.opts["rechazos"], {"pt"})
        self.assertIsNone(job.directo["propuesta"])


if __name__ == "__main__":
    unittest.main()
