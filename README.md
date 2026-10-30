# Notas

App local de Windows para grabar reuniones, transcribirlas y resumirlas. Funciona en tu propio PC, en una
ventana de Edge, sin instalar nada más que Python y unas librerías. Todo lo tuyo (grabaciones, transcripciones,
notas) se queda en la carpeta donde descomprimas la app.

- **Grabar** el micrófono y el sonido del ordenador (Teams, Zoom, el navegador…) a la vez, con `Ctrl+Alt+R`
  desde cualquier programa.
- **Transcribir** mientras se habla o al terminar: en tu PC (faster-whisper) o en la nube (Groq, OpenAI,
  Azure OpenAI o cualquier API compatible con OpenAI).
- **Corregir** la transcripción en la propia página y **tomar notas** por reunión (bloques, propiedades,
  etiquetas, estado, participantes, tema).
- **Resumen con IA** con puntos clave, decisiones y próximos pasos enlazados al minuto del audio.
- **Organizar**: carpetas, carpetas vinculadas (p. ej. de OneDrive), favoritos, tabla, calendario y búsqueda.
- **Vocabulario** por proyecto y tema para que Whisper escriba bien nombres y siglas.
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
- Ajustes y API key, en `%LOCALAPPDATA%\AudiosApp` (fuera de OneDrive). Ahí quedan también los registros
  (`server.log`, `worker-*.log`, `recorder.log`).
- Con la nube, el audio (para transcribir) y el texto (para resumir) se envían al proveedor que configures.
  Antes de grabar o enviar reuniones de trabajo, confirma que lo permiten la política de tu empresa y el acuerdo
  con tu cliente, si lo hay.
- Las listas de vocabulario (`<proyecto>/Vocabulario/*.txt`) suelen llevar nombres de personas y empresas:
  no se incluyen en el zip que genera la app salvo que lo marques, y este repositorio las ignora.

## Desarrollo

El servidor es Python 3.10–3.13 con la librería estándar más `faster-whisper`, `soundcard`, `requests` y
`truststore`; la interfaz es HTML/JS sin compilar (`_app/static`). No hay paso de build: `_app/Notas.bat`
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
cola, paquete instalable, prompts, grabadora) con dobles y ficheros temporales.

### Generar el zip instalable

```
python3 scripts/build_zip.py            # deja dist/Notas - instalador.zip (sin vocabulario)
```

## Licencia

MIT. Ver `LICENSE`.
