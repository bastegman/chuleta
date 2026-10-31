# Chuleta

App local de Windows para grabar reuniones, transcribirlas y resumirlas. Funciona en tu propio PC, en su propia
ventana (WebView2, el motor de Edge que trae Windows), sin instalar nada más que Python y unas librerías. Todo lo tuyo (grabaciones, transcripciones,
notas) se queda en la carpeta donde descomprimas la app.

- **Grabar** el micrófono y el sonido del ordenador (Teams, Zoom, el navegador…) a la vez, con `Ctrl+Alt+R`
  desde cualquier programa.
- **Transcribir** mientras se habla o al terminar: en tu PC (faster-whisper) o en la nube (Groq, OpenAI,
  Azure OpenAI o cualquier API compatible con OpenAI). En la nube el directo va casi en streaming: trozos de 3–6 s
  cortados en las pausas, solo con la voz, al ritmo que permite el cupo gratuito de Groq (y con claves de otras
  cuentas de respaldo si las añades); con «Modo seguir» y, si quieres, traducción al español en una columna.
- **Corregir** la transcripción en la propia página y **tomar notas** por reunión (bloques, propiedades,
  etiquetas, estado, participantes, tema).
- **Resumen con IA** con puntos clave, decisiones y próximos pasos enlazados al minuto del audio.
- **Organizar**: carpetas, carpetas vinculadas (p. ej. de OneDrive), favoritos, tabla, calendario y búsqueda.
- **Vocabulario** por proyecto y tema para que Whisper escriba bien nombres y siglas (y se pueden importar las
  listas de otra carpeta).
- **Perfil de reunión**: carpeta de destino, idioma (también mezclas español + inglés / portugués), tema de
  vocabulario y un contexto de una o dos frases con los que empieza cada grabación, también con `Ctrl+Alt+R`; se
  ajustan por reunión al grabar y sobre la marcha en su página (el directo los usa desde el siguiente trozo).
- **Destinos de salida**: cada reunión, además, como documento (`.md` con propiedades, resumen, notas y
  transcripción; `.txt`; o `.docx`) en las carpetas que elijas (un vault de Obsidian, la del proyecto en OneDrive,
  una biblioteca de SharePoint sincronizada…), al terminar y al guardar cambios, sin mover el original ni pisar
  nada ajeno.
- **Reunión en curso**: al grabar, la app sugiere el nombre, el tema y los participantes a partir de la ventana de
  reunión de Teams y, si lo activas, de la cita de tu calendario de Outlook (clásico); al detectar una llamada
  aparece un aviso flotante con «Grabar».
- **Quién habla** (opcional): separa las voces de cada reunión en tu PC (sherpa-onnx), etiqueta «Persona 1»,
  «Persona 2», «Tú»… en la transcripción, deja ponerles nombre y, con consentimiento, recordarlas en las
  próximas reuniones. El resumen usa esos nombres.
- **Compartir**: desde Ajustes se genera un zip instalable para otra persona.

## Instalar (Windows, sin permisos de administrador)

1. Descarga el zip (`Chuleta - instalador.zip`) o este repositorio y descomprímelo donde sea (Descargas vale).
2. Doble clic en `INSTALAR.bat`. Copia la app a su carpeta propia, `%USERPROFILE%\Chuleta` (fuera de Descargas y de
   OneDrive; si esa carpeta ya existe con otras cosas, `Chuleta (2)`; `INSTALAR.bat -Destino <carpeta>` para otra
   nueva o vacía), donde se guardarán las reuniones. Si no hay Python lo instala, instala las librerías
   (`requirements.txt`), crea el acceso directo «Chuleta» en el escritorio y en el menú Inicio y la añade a
   **Aplicaciones instaladas** (`HKCU\…\Uninstall\Chuleta`, sin administrador). Al terminar dice dónde ha quedado;
   la carpeta descomprimida ya se puede borrar.
3. Se abre Chuleta. Para transcribir en la nube: ⚙ Ajustes → Transcripción en la nube → tu API key.
   Sin nube también funciona (transcribe en el propio PC, más lento).
4. Cerrar la ventana deja Chuleta en segundo plano (el atajo `Ctrl+Alt+R` sigue funcionando); el acceso directo la
   vuelve a abrir. Para apagarla del todo: ⚙ Ajustes → Apagar Chuleta.
5. Para actualizar, descomprime el zip nuevo y ejecuta su `INSTALAR.bat`: encuentra la instalación anterior (por
   Aplicaciones instaladas, por su acceso directo «Chuleta» o «Notas», el nombre anterior, o en
   `%USERPROFILE%\Chuleta`), la actualiza en su carpeta con tus grabaciones (nunca las mueve) y renombra el acceso
   directo «Notas» a «Chuleta»; no borra nada.

### Desinstalar

**Configuración → Aplicaciones → Aplicaciones instaladas → Chuleta → Desinstalar** (o Panel de control → Programas y
características). Quita los accesos directos, la entrada de Aplicaciones instaladas y la carpeta `_app` (solo lo que
es de la app). **Las reuniones se quedan**: audios, transcripciones, notas, resúmenes, hablantes, carpetas de proyecto
y listas de vocabulario siguen en la carpeta de Chuleta, junto al `LEEME.txt`, y una reinstalación vuelve a usar esa
carpeta. Una casilla, desmarcada por defecto, borra además `%LOCALAPPDATA%\AudiosApp` (ajustes, API keys, huellas de
voz y registros). Si la app está grabando, no toca nada. Sin ventana: `QuietUninstallString` (`desinstalar.ps1
-Silencioso`, que no borra `AudiosApp` salvo con `-BorrarDatos`); registro en `%TEMP%\chuleta-desinstalar.log`.
Python y sus librerías se quedan.

El manual completo está en `LEEME.txt`.

## Privacidad

- Grabaciones, transcripciones y notas viven en la carpeta de Chuleta, en tu PC.
- Ajustes y API key, en `%LOCALAPPDATA%\AudiosApp` (fuera de OneDrive). La API key se guarda cifrada con DPAPI
  (solo tu cuenta de Windows en ese PC la descifra; si DPAPI fallara, se guardaría en claro y quedaría anotado en
  `server.log`). Ahí quedan también los registros (`server.log`,
  `worker-*.log`, `recorder.log`, `voces.log`).
- «Quién habla» es opcional y funciona en local: por reunión se escribe `<grabación>.hablantes.json` junto al
  audio (tramos y «Persona N», sin datos de voz); las huellas de voz que decidas recordar van a
  `%LOCALAPPDATA%\AudiosApp\voces.json`, cifrado con DPAPI, máximo 5 por persona, y se pueden olvidar una a una o
  todas. Una huella de voz es un dato biométrico: la app solo la guarda si marcas que tienes el consentimiento de
  esa persona. Los modelos (≈47 MB) se descargan de los releases de sherpa-onnx al activar la opción, con su sha256
  comprobado: segmentación pyannote 3.0 (MIT) y TitaNet-S (`titanet_small`, NVIDIA NeMo; su ficha en NGC remite a
  la licencia del NeMo Toolkit, Apache-2.0; el `.onnx` lo publica el proyecto sherpa-onnx).
- Con la nube, el audio (para transcribir) y el texto (para resumir) se envían al proveedor que configures.
  Antes de grabar o enviar reuniones de trabajo, confirma que lo permiten la política de tu empresa y el acuerdo
  con tu cliente, si lo hay.
- La reunión en curso se lee en el propio PC: el título de la ventana de Teams siempre, y la cita de Outlook clásico
  (por COM) solo si lo activas en Ajustes → Reuniones. Los nombres de los asistentes sugeridos solo se escriben en
  la cabecera del `.md` de esa grabación (puedes borrarlos ahí); nada se envía fuera.
- Las listas de vocabulario (`<proyecto>/Vocabulario/*.txt`) suelen llevar nombres de personas y empresas:
  no se incluyen en el zip que genera la app salvo que lo marques, y este repositorio las ignora.
- Los destinos de salida escriben texto (nunca audio) en las carpetas que tú elijas: si están sincronizadas
  (OneDrive, SharePoint), ese texto sale de tu PC por esa vía.

## Desarrollo

El servidor es Python 3.10–3.13 con la librería estándar más `faster-whisper`, `soundcard`, `requests`,
`truststore`, `pywebview` (la ventana, sobre WebView2; sin ella la interfaz se abre en Edge en modo app), y
`sherpa-onnx` y `pywin32` (opcionales, importación perezosa); la interfaz es HTML/JS sin compilar (`_app/static`). No
hay paso de build: el acceso directo lanza `pythonw.exe server.py` (sin consola) y `_app/Chuleta.bat` lo mismo con
consola. El nombre visible de la app es `common.APP_NAME` (y `-AppName` en el instalador); por compatibilidad no cambian
con él la carpeta de datos `AudiosApp`, la cabecera `X-Audios` ni las claves de los `.md`. La versión que se ve en
Ajustes → Acerca de sale de `_app/version.txt` (lo escribe `scripts/build_zip.py`: `AAAA-MM-DD · sha`); en una copia
de trabajo sin ese fichero se enseña la fecha y hora del código.

En macOS/Linux el servidor arranca para desarrollar la interfaz y la lógica (la grabadora, el atajo global,
la papelera y los avisos son solo de Windows):

```
python3 _app/server.py --port 8766 --data /tmp/notas-datos --no-browser
```

### Tests

```
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python tests/run.py          # unittest; falla si no se ejecuta ningún test
.venv/bin/flake8                       # configuración en setup.cfg
```

Los tests no necesitan Windows, `faster-whisper` ni `pywebview` (la ventana se prueba con un doble): cubren la lógica pura (SRT, notas, árbol de carpetas,
cola, paquete instalable, prompts, grabadora, voces) con dobles y ficheros temporales. La prueba de integración de
voces (`tests/test_voices_integration.py`) solo corre si hay `sherpa-onnx`, `say` (macOS) y `NOTAS_VOCES_DATA`
apunta a una carpeta de datos con los modelos en `models/voces/`. Los tests de escritorio de Windows
(`tests/test_reunion_windows.py`: ventana de Teams simulada, aviso flotante con captura de pantalla) solo corren allí,
en el workflow `reunion-windows`; en el resto se saltan.

«Quién habla» y el cifrado con DPAPI se han desarrollado y probado en macOS (la diarización con sherpa-onnx real;
DPAPI con un doble, porque `crypt32` solo existe en Windows). En Windows, DPAPI real se prueba en el job
`windows-latest` de GitHub Actions (`tests/test_voices.py::test_dpapi_real`); el instalador, la grabadora y la
interfaz de voces aún no se han ejecutado a mano en Windows. Si algo no cuadra, abre un issue con el texto de
`server.log` o `voces.log`.

### Banco de pruebas del directo en la nube

```
.venv/bin/python scripts/directo_bench.py synth --lang es --out /tmp/es.wav   # audio con `say` (macOS) y pausas
.venv/bin/python scripts/directo_bench.py run /tmp/es.wav --latency 1.0       # chunker + ritmo con reloj falso
.venv/bin/python scripts/directo_bench.py run /tmp/es.wav --real              # Groq de verdad (GROQ_API_KEY)
```

Imprime la distribución de tamaños de trozo, cuánto silencio se manda, la latencia (fin del audio del trozo → texto
escrito) y el ritmo de peticiones. Con `--remaining N` simula lo que queda del cupo del día (la ventana crece).
La VAD es la de faster-whisper si está instalada; si no, una por energía.

### Banco de rendimiento

```
.venv/bin/python scripts/rendimiento_bench.py                    # red, worker, audio y biblioteca
.venv/bin/python scripts/rendimiento_bench.py red biblioteca --json despues.json
```

Mide lo que limita la transcripción por nuestro lado: el coste de abrir una conexión con `api.groq.com` en cada
petición frente a reutilizarla (sin clave: la API responde 401 y basta para medir DNS + TCP + TLS), el arranque en
frío del proceso de Whisper frente a tenerlo ya cargado (y su memoria), una hora de audio sintético (decodificar,
silencios, FLAC y el tiempo total con una API falsa) y una biblioteca de 1.000 reuniones (`scan()`,
`Library.refresh()` y `/api/tree` por HTTP). Las medidas `worker` y `audio` necesitan `faster-whisper` y PyAV en
ese Python; las demás, no. No lee `.env` ni manda ninguna petición autenticada.

Ajustes relacionados (en `settings.json`, por `/api/settings`): `worker_keepalive_min` (minutos que el modelo
local sigue cargado tras el último trabajo; 30 por defecto, 0 = 5 min como antes) y `worker_preload` (al arrancar,
cargar el modelo local del directo si ya está descargado; activado). En el directo en la nube, `server.log` y la
cabecera del directo desglosan la latencia de cada trozo por fases (preparación, cupo, red, inferencia si el
servidor la declara, pintado), para saber si el cuello está en Groq, en la red o en este lado.

### Generar el zip instalable

```
python3 scripts/build_zip.py            # deja dist/Chuleta - instalador.zip (sin vocabulario) y _app/version.txt
```

### Regenerar los iconos

```
.venv/bin/pip install pillow && .venv/bin/python make_icons.py
```

Rasteriza `_app/static/logo.svg` (y `design/logo-small.svg` para 16 y 20 px) con Chrome o Edge headless y escribe
`_app/static/chuleta.ico` (nueve tamaños, todos en BMP: Windows no siempre pinta bien las entradas PNG pequeñas) y
`_app/static/chuleta.png`. Solo hace falta si cambia el logo.

## Licencia

MIT. Ver `LICENSE`.
