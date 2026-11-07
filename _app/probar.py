# -*- coding: utf-8 -*-
"""«Probar» del asistente «Configurar Chuleta» (07/11/2026): una prueba real de punta a punta con la configuración que
se está eligiendo, sin guardarla.

Graba 3 s del micro («Di: hola, esto es una prueba»), los transcribe con el modo elegido y, si el modo resume, le pide
el resumen a Claude (con la cuenta del usuario) o a la API. Si no se oye voz, o el micro no se puede usar, prueba la
cadena con 1 s de silencio y lo dice (decisión de Claudia: así se prueba también el micro, sin audio empaquetado ni
licencias). Se para en el primer fallo, con la frase de explicar.py y su botón.

El trabajo de verdad lo hacen las funciones que le pasa el servidor (hacer): aquí solo el orden, los estados y los
textos. Así se prueba sin Whisper, sin micro y sin red (tests/test_ux_configurar.py)."""
import threading
import time
import uuid

import explicar

FRASE = "Hola, esto es una prueba"
SEGUNDOS = 3
# con Claude, la cuenta va primero: si no hay sesión, se sabe antes de bajar 2 GB (revisión del 07/11/2026)
PASOS = {"local": ("modelos", "micro", "transcribir"),
         "agente": ("cuenta", "modelos", "micro", "transcribir", "resumir"),
         "api": ("micro", "transcribir", "resumir")}
TEXTO_MUESTRA = "Hola, esto es una prueba de Chuleta."  # lo que se resume si no hay voz que resumir
OJO_MICRO = (" Ojo: no se ha oído tu voz. Revisa el micro en ● Grabar: la barra «Nivel» tiene que moverse al "
             "hablar.")
AVISO_SIN_TEXTO = ("La transcripción funciona, pero no ha entendido la frase de prueba: revisa el idioma en "
                   "Ajustes → Grabar.")


class Cancelada(Exception):
    pass


class Prueba(threading.Thread):
    """modo: local | agente | api. nombres: {"transcribe": "en este PC" | "con Groq", "resume": "Claude (tu cuenta)" |
    "Groq", "proveedor": "Groq" | None}. hacer: funciones del servidor:
      modelos(report, cancelled)                     deja descargados los modelos de Whisper que hacen falta
      grabar(segundos, report, cancelled)            -> (wav, hay_voz, nota): nota si el micro no se pudo usar
      silencio()                                     -> wav de 1 s de silencio
      transcribir(wav, report, cancelled)            -> texto
      resumir(texto, report, cancelled)              -> texto
    report(texto) cambia el detalle del paso en curso. candidata: True si es una configuración aún sin guardar.
    al_terminar(prueba): lo llama el hilo al acabar (el servidor guarda la última prueba si no es candidata)."""

    def __init__(self, modo, hacer, nombres, candidata=False, al_terminar=None, espera=None):
        """espera: la prueba anterior, ya cancelada: esta no empieza hasta que acaba (comparten micro y Whisper)."""
        super().__init__(daemon=True, name="probar")
        self.espera = espera
        self.id = uuid.uuid4().hex[:10]
        self.modo = modo if modo in PASOS else "local"
        self.hacer, self.nombres, self.candidata, self.al_terminar = hacer, nombres or {}, candidata, al_terminar
        self.lock = threading.Lock()
        self.pasos = [{"id": p, "texto": self._titulo(p), "estado": "espera", "detalle": "", "segundos": None}
                      for p in PASOS[self.modo]]
        self.fallo, self.listo, self.terminada, self.cancelar = None, False, False, False
        self.resultado, self.empezada, self.acabada, self._t0 = "", time.time(), None, {}
        self.nota_micro, self.firma = None, None  # el error crudo del micro (al log) y la configuración probada

    def _titulo(self, p):
        n = self.nombres
        return {"cuenta": "Claude Code con tu cuenta",
                "modelos": "Modelos de transcripción en este PC",
                "micro": f"Grabar {SEGUNDOS} s del micro",
                "transcribir": f"Transcribir {n.get('transcribe') or 'en este PC'}",
                "resumir": f"Resumir con {n.get('resume') or 'IA'}"}[p]

    # --- estado (para /api/probar)
    def public(self):
        now = time.time()
        with self.lock:
            pasos = [dict(p, segundos=round(now - self._t0[p["id"]]) if p["estado"] == "en_curso" else p["segundos"])
                     for p in self.pasos]
            return {"id": self.id, "modo": self.modo, "pasos": pasos, "fallo": self.fallo, "listo": self.listo,
                    "terminada": self.terminada, "resultado": self.resultado, "candidata": self.candidata,
                    "empezada": self.empezada, "cuando": self.acabada}

    def _set(self, pid, **kw):
        with self.lock:
            p = next(x for x in self.pasos if x["id"] == pid)
            if kw.get("estado") == "en_curso":
                self._t0[pid] = time.time()
            elif kw.get("estado") in ("ok", "aviso", "error") and pid in self._t0:
                p["segundos"] = round(time.time() - self._t0[pid])
            p.update(kw)

    def cancelled(self):
        return self.cancelar

    # --- la prueba
    def run(self):
        wav, voz, texto = None, False, ""
        if self.espera is not None:
            self.espera.join(20)
        try:
            for pid in PASOS[self.modo]:
                if self.cancelar:
                    raise Cancelada()
                self._set(pid, estado="en_curso", detalle="")

                def report(t, pid=pid):
                    if t:
                        self._set(pid, detalle=str(t))
                try:
                    if pid == "cuenta":
                        self.hacer["cuenta"](report, self.cancelled)
                        self._set(pid, estado="ok", detalle="Claude Code responde con tu cuenta")
                    elif pid == "modelos":
                        self.hacer["modelos"](report, self.cancelled)
                        self._set(pid, estado="ok", detalle="Descargados")
                    elif pid == "micro":
                        wav, voz, nota = self.hacer["grabar"](SEGUNDOS, report, self.cancelled)
                        if voz:
                            self._set(pid, estado="ok", detalle="Se ha oído tu voz")
                        else:
                            why = "No he podido usar el micro" if nota else "No se ha oído voz"
                            self._set(pid, estado="aviso", detalle=f"{why}: sigo la prueba sin voz (1 s de silencio).")
                            with self.lock:
                                self.nota_micro = nota
                            wav = self.hacer["silencio"]()
                    elif pid == "transcribir":
                        texto = (self.hacer["transcribir"](wav, report, self.cancelled) or "").strip()
                        if voz and texto:
                            self._set(pid, estado="ok", detalle=f"«{texto[:140]}»")
                        elif voz:
                            self._set(pid, estado="aviso", detalle=AVISO_SIN_TEXTO)
                        else:
                            self._set(pid, estado="ok", detalle="Funciona (con silencio no sale texto)")
                    elif pid == "resumir":
                        r = (self.hacer["resumir"](texto if voz and texto else TEXTO_MUESTRA, report,
                                                   self.cancelled) or "").strip()
                        quien = "Claude" if self.modo == "agente" else (self.nombres.get("proveedor") or "El servicio")
                        self._set(pid, estado="ok", detalle=f"{quien} ha contestado" + (f": «{r[:140]}»" if r else ""))
                except Cancelada:
                    raise
                except Exception as e:  # el primer fallo para la prueba, con su frase y su botón
                    if self.cancelar:
                        raise Cancelada()
                    ex = explicar.explicar(e, proveedor=self.nombres.get("proveedor"))
                    self._set(pid, estado="error", detalle=ex["frase"])
                    with self.lock:
                        self.fallo = {"paso": pid, "frase": ex["frase"], "accion": ex["accion"],
                                      "tecnico": ex["tecnico"]}
                    break
            with self.lock:
                self.listo = self.fallo is None
                if self.listo:
                    prov = self.nombres.get("proveedor") or "la API"
                    micro = next((p for p in self.pasos if p["id"] == "micro"), {})
                    ojo = "" if micro.get("estado") != "aviso" else OJO_MICRO
                    self.resultado = {
                        "local": "Listo. Tus reuniones se transcriben en este PC. No se resumen con IA.",
                        "agente": "Listo. Tus reuniones se transcriben en este PC y las resume Claude con tu cuenta.",
                        "api": f"Listo. Tus reuniones se transcriben y se resumen con {prov}."}[self.modo] + ojo
        except Cancelada:
            with self.lock:
                for p in self.pasos:
                    if p["estado"] in ("espera", "en_curso"):
                        p["estado"], p["detalle"] = "espera", ""
                self.fallo = {"paso": None, "frase": "Prueba cancelada.", "accion": None, "tecnico": ""}
        finally:
            with self.lock:
                self.terminada, self.acabada = True, time.time()
            if self.al_terminar:
                try:
                    self.al_terminar(self)
                except Exception:
                    pass

    def resumen(self):
        """Lo que se guarda como última prueba (Ajustes la enseña)."""
        with self.lock:
            return {"cuando": self.acabada, "ok": bool(self.listo), "modo": self.modo,
                    "frase": None if self.listo else (self.fallo or {}).get("frase")}
