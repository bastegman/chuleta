# Notas

App local de Windows para grabar reuniones, transcribirlas y resumirlas. Funciona en tu propio PC, en una
ventana de Edge, sin instalar nada más que Python y unas librerías. Todo lo tuyo (grabaciones, transcripciones,
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
- **Vocabulario** por proyecto y tema para que Whisper escriba bien nombres y siglas.
- **Quién habla** (opcional): separa las voces de cada reunión en tu PC (sherpa-onnx), etiqueta «Persona 1»,
  «Persona 2», «Tú»… en la transcripción, deja ponerles nombre y, con consentimiento, recordarlas en las
  próximas reuniones. El resumen usa esos nombres.
- **Compartir**: desde Ajustes se genera un zip instalable para otra persona.

## Instalar (Windows, sin permisos de administrador)

1. Descarga el zip (`Notas - instalador.zip`) o este repositorio y descomprímelo donde quieras tener tus
   reuniones (Documentos, Escritorio, OneDrive…).
2. Doble clic en `INSTALAR.bat`. Si no hay Python lo instala, instala las librerías (`requirements.txt`) y
   crea el acceso directo «Notas» en el escritorio y en el menú Inicio.
3. Se abre Notas. Para transcribir en la nube: ⚙ Ajustes → Transcripción en la nube → tu API key.
   Sin nube también funciona (transcribe en el propio PC, más lento).

El manual completo está en `LEEME.txt`.

## Privacidad

- Grabaciones, transcripciones y notas viven en la carpeta de Notas, en tu PC.
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
- Las listas de vocabulario (`<proyecto>/Vocabulario/*.txt`) suelen llevar nombres de personas y empresas:
  no se incluyen en el zip que genera la app salvo que lo marques, y este repositorio las ignora.

## Desarrollo

El servidor es Python 3.10–3.13 con la librería estándar más `faster-whisper`, `soundcard`, `requests`,
`truststore` y `sherpa-onnx` (opcional, importación perezosa); la interfaz es HTML/JS sin compilar (`_app/static`). No hay paso de build: `_app/Notas.bat`
lanza `python server.py`.

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

Los tests no necesitan Windows ni `faster-whisper`: cubren la lógica pura (SRT, notas, árbol de carpetas,
cola, paquete instalable, prompts, grabadora, voces) con dobles y ficheros temporales. La prueba de integración de
voces (`tests/test_voices_integration.py`) solo corre si hay `sherpa-onnx`, `say` (macOS) y `NOTAS_VOCES_DATA`
apunta a una carpeta de datos con los modelos en `models/voces/`.

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

### Generar el zip instalable

```
python3 scripts/build_zip.py            # deja dist/Notas - instalador.zip (sin vocabulario)
```

### Regenerar los iconos

```
.venv/bin/pip install pillow && .venv/bin/python make_icons.py
```

Rasteriza `_app/static/logo.svg` (y `design/logo-small.svg` para 16 y 20 px) con Chrome o Edge headless y escribe
`_app/static/notas.ico` (nueve tamaños, todos en BMP: Windows no siempre pinta bien las entradas PNG pequeñas) y
`_app/static/notas.png`. Solo hace falta si cambia el logo.

## Licencia

MIT. Ver `LICENSE`.
