# -*- coding: utf-8 -*-
"""
Avisos de la franja del directo (rediseño B6, puntos 1-3). Tres vigilantes pequeños, sin hilos ni E/S propios: los
alimentan lecturas que la app ya hace (el estado de la grabadora cada 0,25 s, el registro de privacidad del micro cada
8 s y lo que Whisper ya devuelve de cada trozo). No tocan el audio ni piden nada más a Whisper ni a la nube.

1. Callada: el micro o el sonido del ordenador llevan más de un minuto sin recibir nada («El micro no recibe sonido
   desde hace 1 min»). Ver la clase para el criterio y por qué no salta en los silencios normales de una reunión.
2. FinDeLlamada: la llamada de Teams que se estaba grabando ha terminado: «paro en 30 s · No terminar». Nunca
   para si la llamada sigue o si la lectura duda.
3. Propuesta: con un idioma fijo, lo último suena a otro: «Lo último suena a portugués · Cambiar». Varios bloques
   seguidos, nunca uno suelto, y nunca lo ya rechazado en esa grabación.
"""
import json
import os
import threading

import engines

# --- 1. fuente callada
# El medidor de la grabadora da el pico de cada 0,25 s en dBFS (recorder.Source.status; -100 si no llega nada).
MUERTO_DB = -80.0   # por debajo: silencio digital (ceros). Un micro de verdad en una sala callada da su ruido de fondo
#                     (picos de -65 a -45 dBFS): nunca baja de aquí salvo silenciado (tecla del portátil, Windows, el
#                     permiso del micro) o con el nivel a 0. El audio del ordenador sí da ceros cuando no suena nada
VOZ_DB = -45.0      # por encima, en el micro: alguien habla (la voz da picos de -35 a -6 dBFS)
AVISO_S = 60.0      # «más de un minuto» (msg 1378, punto 1)
PUERTA_S = 1.0      # un silencio digital de >= 1 s que se va solo...
PUERTA_VUELTAS = 2  # ...dos veces: el micro tiene puerta de ruido (da ceros entre frases): sin aviso del micro
HABLA_S = 5.0       # tras hablar por el micro, 5 s que aún son «hablando» (las pausas entre frases)
PASO_MAX_S = 2.0    # entre dos lecturas de 0,25 s: un hueco mayor (el PC se durmió) no cuenta entero

# --- 2. fin de la llamada
CONFIRMA_S = 10.0   # el micro de Teams suelto y sin ventana de reunión al menos esto antes de proponer
PARA_EN_S = 30.0    # «paro en 30 s»
CUBRE = 0.6         # la llamada cubre al menos el 60 % de lo grabado hasta que terminó (no una llamada corta
CUBRE_MIN_S = 120.0  # en medio de una reunión presencial) y ha durado al menos 2 min
TIC_MAX_S = 30.0    # entre dos lecturas (cada 8 s): un hueco mayor (suspensión) no suma tiempo de llamada

# --- 3. idioma propuesto
PROPONIBLES = ("es", "en", "pt", "fr", "it", "de", "ca")  # los que se pueden elegir (LANGS de app.js)
SEGUIDOS = 2         # trozos seguidos en el mismo otro idioma (nunca uno suelto)...
SEGUIDOS_VOZ_S = 30.0  # ...con al menos 30 s de voz entre ellos: en local, 2 trozos (de 20-45 s); en la nube, 5-10
BLOQUE_VOZ_S = 2.0   # un bloque con menos voz no cuenta, ni a favor ni en contra
PROB = 0.75          # probabilidad mínima del otro idioma (en local; la nube no la da)
PROB_FIJO = 0.5      # un bloque en el idioma fijado con esto o más corta la racha
MEZCLA_FIJO_S = 60.0  # con >= 60 s ya oídos en el idioma fijado y una mezcla de los dos (engines.PAIRS): la mezcla
CONTRA_S = 20.0      # una propuesta en pantalla se retira tras 20 s de voz en el idioma fijado


def _pruebas():
    """CHULETA_PRUEBAS_AVISOS (solo los E2E): JSON con tiempos más cortos, p. ej. {"AVISO_S": 4, "PARA_EN_S": 6}."""
    raw = os.environ.get("CHULETA_PRUEBAS_AVISOS")
    if not raw:
        return
    try:
        cambios = json.loads(raw)
    except ValueError:
        return
    for k, v in cambios.items():
        if k in ("AVISO_S", "CONFIRMA_S", "PARA_EN_S", "CUBRE", "CUBRE_MIN_S", "SEGUIDOS_VOZ_S") \
                and isinstance(v, (int, float)):
            globals()[k] = float(v)


_pruebas()


def _db(src):
    try:
        return float(src.get("db"))
    except (TypeError, ValueError):
        return -100.0


class Callada:
    """Cuánto lleva cada fuente sin recibir nada, para el aviso de la franja. Se alimenta con cada línea de estado de
    la grabadora (feed) y se lee con estado().

    Micro: «callado» = silencio digital (pico <= MUERTO_DB) seguido durante AVISO_S. Un silencio normal de reunión
    (llevas 20 min escuchando) no llega ahí: el micro sigue dando el ruido de la sala. Lo que sí llega: el micro
    silenciado en el portátil o en Windows, sin permiso, desconectado o con el nivel a 0, que es justo cuando no se
    graba tu voz. Excepción: un micro con puerta de ruido (Krisp, algunos cascos) da ceros entre frase y frase; si se ha
    visto volver solo de un silencio digital PUERTA_VUELTAS veces, en esa grabación no se avisa del micro. Con el micro
    silenciado en la app («Silenciar mi micro») no se avisa: lo has elegido tú.

    Ordenador: cuando no suena nada da ceros, así que el silencio solo cuenta si se esperaba oír a alguien: con una app
    de reuniones en llamada (en una reunión presencial el ordenador no suena y es normal), después de que en esa llamada
    haya sonado algo o hayas hablado (en la sala de espera, solo, tampoco), y sin contar el rato en que hablas tú
    (presentando, que los demás callen es normal). Avisa al juntar AVISO_S de ese silencio «escuchando»."""

    def __init__(self, avisar_s=None, log=None):
        self.avisar_s = AVISO_S if avisar_s is None else avisar_s
        self.log = log or (lambda m: None)
        self.last = None
        self.mic_muerto = None     # desde cuándo da silencio digital el micro (None: suena o está silenciado)
        self.mic_vueltas = 0       # silencios digitales de >= PUERTA_S que se fueron solos
        self.mic_puerta = False
        self.mic_voz = None        # última vez que se habló por el micro
        self.sys_sonido = None     # última vez que sonó el ordenador
        self.sys_escucha = 0.0     # segundos de silencio del ordenador mientras se escuchaba
        self.llamada_desde = None  # desde cuándo hay una app de reuniones con el micro abierto (lo dice el servidor)
        self.llamada_activa = False  # en esta llamada ya ha sonado algo o has hablado
        self.avisado = set()       # fuentes con el aviso puesto (para el log: una línea al empezar y otra al acabar)

    def feed(self, st, now, en_llamada=False):
        if not isinstance(st, dict) or st.get("t") is None:
            return
        dt = 0.0 if self.last is None else min(PASO_MAX_S, max(0.0, now - self.last))
        self.last = now
        mic = st.get("mic") if isinstance(st.get("mic"), dict) else None
        sysd = st.get("sys") if isinstance(st.get("sys"), dict) else None
        if en_llamada and self.llamada_desde is None:
            self.llamada_desde, self.llamada_activa, self.sys_escucha = now, False, 0.0
        elif not en_llamada:
            self.llamada_desde, self.sys_escucha = None, 0.0
        if mic is not None:
            db = _db(mic)
            if st.get("mic_muted"):
                self.mic_muerto = None
            elif db <= MUERTO_DB:
                if self.mic_muerto is None:
                    self.mic_muerto = now
            else:
                if self.mic_muerto is not None and now - self.mic_muerto >= PUERTA_S:
                    self.mic_vueltas += 1
                    if self.mic_vueltas >= PUERTA_VUELTAS and not self.mic_puerta:
                        self.mic_puerta = True
                        self.log("   micro: da silencio digital entre frases (puerta de ruido): en esta grabación no "
                                 "se avisa de que se calla")
                self.mic_muerto = None
                if db > VOZ_DB:
                    self.mic_voz = now
                    if en_llamada:
                        self.llamada_activa = True
        hablando = self.mic_voz is not None and now - self.mic_voz < HABLA_S
        if sysd is not None:
            if _db(sysd) > MUERTO_DB:
                self.sys_sonido, self.sys_escucha = now, 0.0
                if en_llamada:
                    self.llamada_activa = True
            elif en_llamada and not hablando:
                self.sys_escucha += dt
        self._notas(now)

    def estado(self, now):
        """{"mic": segundos, "sys": segundos} de las fuentes que hay que avisar (vacío: ninguna)."""
        out = {}
        if self.mic_muerto is not None and not self.mic_puerta and now - self.mic_muerto >= self.avisar_s:
            out["mic"] = int(now - self.mic_muerto)
        if self.llamada_desde is not None and self.llamada_activa and self.sys_escucha >= self.avisar_s:
            desde = max(self.sys_sonido or self.llamada_desde, self.llamada_desde)
            out["sys"] = int(now - desde)
        return out

    def _notas(self, now):
        e = self.estado(now)
        for k, nombre in (("mic", "el micro"), ("sys", "el sonido del ordenador")):
            if k in e and k not in self.avisado:
                self.avisado.add(k)
                self.log(f"   aviso: {nombre} no recibe sonido desde hace {e[k]} s")
            elif k not in e and k in self.avisado:
                self.avisado.discard(k)
                self.log(f"   {nombre} vuelve a recibir sonido (o ya no se avisa)")


class FinDeLlamada:
    """¿Ha terminado la llamada de Teams que se estaba grabando? Se llama con cada lectura (tic) y devuelve lo que toca
    hacer: None, "propone" (enseñar «paro en 30 s»), "cancela" (la llamada sigue o ya no se sabe: se quita el aviso) o
    "para" (pasados los 30 s sin «No terminar»: parar como un «Parar» normal).

    Señales, las dos a la vez: el micro de Teams en el registro de privacidad («uso»: lo tiene abierto; «libre»: lo
    soltó, con su hora de fin, o Teams se ha cerrado; «duda»: no se sabe qué procesos hay) y la ventana de la reunión de
    Teams (True / False / None si no se puede leer).
    Solo «libre» y sin ventana cuenta como terminada; todo lo demás cancela. Y la llamada tiene que ser la de esta
    grabación: cubrir al menos el CUBRE de lo grabado hasta que terminó y durar CUBRE_MIN_S (una llamada corta en medio
    de una reunión presencial no para nada). Tras «No terminar», no se vuelve a proponer hasta que haya otra
    llamada y termine."""

    def __init__(self):
        self.lock = threading.Lock()
        self._reset(None, None)

    def _reset(self, grabacion, now):
        self.grabacion, self.inicio, self.ultimo, self.ultimo_uso = grabacion, now, now, None
        self.en_llamada_s, self.libre_desde, self.para_en, self.seguir = 0.0, None, None, False

    def tic(self, grabacion, now, mic, ventana_fn=lambda: None):
        """grabacion: la que se está grabando (su ruta) o None; mic: "uso" | "libre" | "duda"; ventana_fn: ¿hay ventana
        de reunión de Teams? (True/False/None); solo se mira cuando hace falta."""
        with self.lock:
            if grabacion is None:
                if self.grabacion is not None:
                    self._reset(None, None)
                return None
            if grabacion != self.grabacion:
                self._reset(grabacion, now)
            dt = min(TIC_MAX_S, max(0.0, now - (self.ultimo if self.ultimo is not None else now)))
            self.ultimo = now
            if mic == "uso":
                self.en_llamada_s += dt
                self.libre_desde, self.seguir, self.ultimo_uso = None, False, now
                return self._cancelar()
            if mic != "libre" or self.seguir or self.en_llamada_s < CUBRE_MIN_S:
                self.libre_desde = None
                return self._cancelar()
            # lo grabado hasta la última lectura con la llamada en marcha (las dos cuentas, con las mismas lecturas)
            if self.en_llamada_s < CUBRE * max(0.0, self.ultimo_uso - self.inicio):
                self.libre_desde = None
                return self._cancelar()
            if ventana_fn() is not False:  # la ventana sigue (en espera, silenciado) o no se sabe: no
                self.libre_desde = None
                return self._cancelar()
            if self.libre_desde is None:
                self.libre_desde = now
            if self.para_en is None:
                if now - self.libre_desde < CONFIRMA_S:
                    return None
                self.para_en = now + PARA_EN_S
                return "propone"
            if now >= self.para_en:
                self.para_en, self.seguir = None, True
                return "para"
            return None

    def _cancelar(self):
        if self.para_en is None:
            return None
        self.para_en = None
        return "cancela"

    def pendiente(self):
        """Hay que mirar más a menudo (cada segundo): se está confirmando el fin o contando los 30 s."""
        with self.lock:
            return self.para_en is not None or self.libre_desde is not None

    def quedan(self, now):
        with self.lock:
            return None if self.para_en is None else max(0, int(round(self.para_en - now)))

    def seguir_grabando(self):
        """«No terminar»: fuera el aviso; no se vuelve a proponer hasta otra llamada. True si había aviso."""
        with self.lock:
            habia = self.para_en is not None
            self.para_en, self.libre_desde, self.seguir = None, None, True
            return habia


def _mezcla(a, b):
    """El código de la mezcla de los idiomas a y b ("es-pt"), o None si no la hay."""
    return next((k for k, par in engines.PAIRS.items() if set(par) == {a, b}), None)


class Propuesta:
    """«Lo último suena a …»: con un idioma fijo, en qué idioma suenan los trozos que se transcriben (oir, con lo que
    Whisper ya devuelve: en local el idioma de cada bloque oído en la salida del encoder, worker._Escucha; en la nube
    el campo language de verbose_json). Propone otro idioma solo con SEGUIDOS bloques seguidos en él (y
    SEGUIDOS_VOZ_S de voz), nunca por uno suelto; un bloque en el idioma fijado corta la racha. Si ya se habían oído
    MEZCLA_FIJO_S en el fijado y hay mezcla de los dos, propone la mezcla («español + portugués»). rechazos: los idiomas
    rechazados en esa grabación (un set que guarda el servidor por grabación): no se vuelven a proponer."""

    def __init__(self, rechazos=None):
        self.rechazos = rechazos if rechazos is not None else set()
        self.fijo, self.racha, self.voz, self.actual, self.contra = None, [], {}, None, 0.0

    def oir(self, fijo, bloques):
        """fijo: el idioma de la reunión ahora (None o una mezcla: no se propone nada); bloques: [(idioma, prob o
        None, segundos de voz)] del último trozo."""
        fijo = fijo or None
        if fijo != self.fijo:
            self.fijo, self.racha, self.voz, self.actual, self.contra = fijo, [], {}, None, 0.0
        if fijo is None or engines.lang_pair(fijo):
            return
        for lang, prob, voz in bloques or ():
            voz = float(voz or 0.0)
            if voz < BLOQUE_VOZ_S or not lang:
                continue
            if lang == fijo:
                if prob is None or prob >= PROB_FIJO:
                    self.voz[fijo] = self.voz.get(fijo, 0.0) + voz
                    self.racha = []
                    if self.actual:
                        self.contra += voz
                        if self.contra >= CONTRA_S:
                            self.actual = None
                continue
            if lang not in PROPONIBLES or (prob is not None and prob < PROB):
                continue
            if self.racha and self.racha[-1][0] != lang:
                self.racha = []
            self.racha.append((lang, voz))
        if len(self.racha) >= SEGUIDOS and sum(v for _, v in self.racha) >= SEGUIDOS_VOZ_S:
            otro = self.racha[-1][0]
            if otro not in self.rechazos and (not self.actual or self.actual["idioma"] != otro):
                mezcla = _mezcla(fijo, otro) if self.voz.get(fijo, 0.0) >= MEZCLA_FIJO_S else None
                self.actual, self.contra = {"idioma": otro, "cambiar_a": mezcla or otro, "desde": fijo}, 0.0

    def rechazar(self, idioma):
        self.rechazos.add(idioma)
        if self.actual and self.actual["idioma"] == idioma:
            self.actual = None
        self.racha = []

    def publica(self):
        a = self.actual
        return dict(a) if a and a["idioma"] not in self.rechazos else None
