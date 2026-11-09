# Tenca

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="design/tenca-logo-oscuro.png">
  <img alt="Tenca" src="design/tenca-logo.png" width="240">
</picture>

App local de Windows para grabar reuniones, transcribirlas y resumirlas. Funciona en tu propio PC, en su propia
ventana (WebView2, el motor de Edge que trae Windows), sin instalar nada más que Python y unas librerías. Las
grabaciones, transcripciones y notas se guardan en la carpeta de Tenca, `%USERPROFILE%\Tenca` (donde la instala
`INSTALAR.bat`), y en las carpetas que vincules; qué sale del PC, y cuándo, está en «Privacidad».

- **Grabar** el micrófono y el sonido del ordenador (Teams, Zoom, el navegador…) a la vez, con `Ctrl+Alt+R` desde
  cualquier programa.
- **Subir audio** que ya tengas (del móvil, de WhatsApp o de otra grabadora): el botón junto a Grabar, «Subir audio
  aquí» en cada carpeta, o arrastrarlo a la ventana. Se guarda como una reunión más, con su proyecto, su fecha y su
  carpeta, y se pone a transcribir. Lee m4a (también el «sin pérdida» del iPhone), opus y ogg de WhatsApp, mp3, wav,
  aac, webm, flac, amr y el sonido de un vídeo mp4 o mov; si un archivo no se puede leer, lo dice y no guarda nada.
  **Desde el móvil**, sin instalar nada en él: «Subir audio › Desde el móvil» enseña un código QR; lo escaneas, eliges
  las grabaciones en la página que se abre y entran solas, con lo que hayas elegido en el PC. El audio va directo del
  móvil al PC por tu wifi (no pasa por internet); cada código vale 10 minutos y para un solo móvil, y solo funciona en
  una red que Windows tiene como privada (la de casa).
- **Transcribir** mientras se habla o al terminar: en tu PC (faster-whisper) o en la nube (Groq, OpenAI, Azure OpenAI
  o cualquier API compatible con OpenAI). En la nube el directo va casi en streaming: trozos de 3–6 s cortados en las
  pausas, solo con la voz, al ritmo que permite el cupo gratuito de Groq (y con claves de respaldo de otras cuentas
  del mismo proveedor, si las añades); con «Modo seguir» y, si quieres, traducción al español o al inglés en una
  columna («Traducir al ▾», recordado por reunión; no se guarda). El idioma de la reunión se cambia en pleno directo y
  vale al momento: el trozo en curso se repite y lo escrito en el último minuto se rehace en el idioma nuevo.
- **Corregir** la transcripción en la propia página y **tomar notas** por reunión (bloques, propiedades, etiquetas,
  estado, participantes, proyecto).
- **Resumen con IA** con puntos clave, decisiones y próximos pasos enlazados al minuto del audio. Al terminar se hace
  solo si la reunión se transcribió en la nube; lo transcrito en local, a mano o si lo permites en Ajustes.
  «Copiar para correo» lo copia como acta con formato (título, fecha, asistentes confirmados y el resumen, sin
  Markdown ni minutos) para pegarlo en Outlook o en Teams.
- **Dónde se procesa, lo eliges tú**: solo en este PC (nada sale de él), con tu cuenta de Claude (resumen con tu
  suscripción a través de Claude Code, con el paso a paso dentro de la app) o con una API externa; las dos últimas,
  con su aviso.
- **Organizar**: carpetas, carpetas vinculadas (p. ej. de OneDrive), favoritos, tabla, calendario y búsqueda.
- **Vocabulario** por proyecto y workstream para que Whisper escriba bien nombres y siglas. Las listas se escriben en
  la app o se leen de una carpeta tuya («Usar las listas de una carpeta…»: cada `.txt` es un workstream y se lee en
  vivo, sin copiarlo); «Copiar listas de otra carpeta…» las copia a la de la app.
- **Una carpeta por reunión** (`AAAA-MM-DD Nombre`) dentro de la carpeta principal de su proyecto o workstream (sin
  asignar, la del proyecto: la que tiene su `Vocabulario`; si sus listas vienen de una carpeta conectada, la que le
  asignes en Ajustes → Proyectos o con «Usarlo en» al grabar). Lo grabado antes se queda donde está. En pleno directo,
  «Se guarda en … · Cambiar» elige otra: la reunión entera se mueve sola cuando terminan el directo, el repaso y el
  resumen (con «Deshacer» hasta entonces, y aunque cierres la app antes).
- **Grabar con una tarjeta que dice qué va a pasar**: la reunión, el proyecto y «Así se va a grabar» (dónde se
  guarda, idioma, vocabulario, quién transcribe y resume, el audio), cada línea con de dónde sale: esta reunión, el
  proyecto o Ajustes. «Cambiar» pregunta para qué reuniones. `Ctrl+Alt+R` y el aviso de Teams graban con el mismo
  plan, que calcula la app una sola vez. Ajustes abre en «Al grabar» con ese plan arriba y las reglas debajo; idioma
  y contexto se cambian también sobre la marcha en la página de la reunión.
- **Copias en otras carpetas** (los destinos de salida): cada reunión, además, como documento (`.md` con propiedades,
  resumen, notas y transcripción; `.txt`; o `.docx`) en las carpetas que elijas (un vault de Obsidian, la de documentos
  del proyecto en OneDrive, una biblioteca de SharePoint sincronizada…; no la de Tenca, ni una vinculada, ni una con
  grabaciones), al terminar y al guardar cambios, sin mover el original ni pisar nada ajeno. Si un nombre ya lo usa otra
  reunión o un fichero tuyo, el documento va a `<nombre> (<código>).md`.
- **Reunión en curso**: al grabar, la app sugiere el nombre, el proyecto y los participantes a partir de la ventana
  de reunión de Teams, de tus reuniones recientes y, si lo activas, de la cita de tu calendario de Outlook (clásico);
  los participantes quedan como sugeridos hasta que los confirmas. Al detectar una llamada aparece un aviso flotante
  con «Grabar».
- **Quién habla** (opcional): separa las voces de cada reunión en tu PC (sherpa-onnx), etiqueta «Persona 1»,
  «Persona 2», «Tú»… en la transcripción, deja ponerles nombre y, con consentimiento, recordarlas en las próximas
  reuniones. El resumen usa esos nombres.
- **Oculta al compartir pantalla**: en Teams, Zoom o Meet los demás no ven Tenca ni su aviso «Grabar» (ven lo que
  haya detrás); tú sí.
  Tampoco sale en capturas de pantalla. Se quita en Ajustes → Calendario y Teams → «Al compartir pantalla» (para
  una demo o una captura; vale al momento). Windows 10 2004 o posterior (`SetWindowDisplayAffinity` con
  `WDA_EXCLUDEFROMCAPTURE`).
- **Compartir**: desde Ajustes se genera un zip instalable para otra persona.

## Instalar (Windows, sin permisos de administrador)

1. Consigue el zip `Tenca - instalador.zip` o este repositorio. Antes de descomprimirlo, clic derecho → Propiedades →
   «Desbloquear» → Aceptar: así Windows no marca `INSTALAR.bat` como bajado de Internet y no sale el aviso «Unknown
   publisher» («Windows protegió su PC»; si sale igual, «Más información» → «Ejecutar de todas formas»: el instalador
   no lleva firma digital, nada más). Descomprímelo donde sea (Descargas vale). El zip lo
   crea una copia que ya funciona (⚙ Ajustes → Ayuda → Compartir Tenca → «Crear instalador para otra persona») o
   `scripts/build_zip.py` (ver «Generar el zip instalable»); el repositorio se descarga entero con Code → Download
   ZIP.
2. Doble clic en `INSTALAR.bat`. Copia la app a su carpeta propia, `%USERPROFILE%\Tenca` (fuera de Descargas y de
   OneDrive; si esa carpeta ya existe con otras cosas, `Tenca (2)`), donde se guardarán las reuniones; para otra
   carpeta, nueva o vacía, `INSTALAR.bat -Destino <carpeta>` (solo la primera vez: si ya está instalada, se actualiza
   donde está). Si no hay Python lo instala, instala las librerías (`requirements.txt`), crea el acceso directo
   «Tenca» en el escritorio y en el menú Inicio y la añade a **Aplicaciones instaladas**
   (`HKCU\…\Uninstall\Tenca`, sin administrador). Al terminar dice dónde ha quedado; la carpeta descomprimida ya se
   puede borrar.
3. Se abre Tenca. Lo primero, «Configurar Tenca», en la portada (más tarde está en ⚙ Ajustes → Procesado): eliges
   una de las tres opciones y al final lo pruebas todo:
   **Solo en este PC** (Whisper transcribe en tu ordenador, nada sale de él, sin resumen con IA), **Con tu cuenta de
   Claude** (transcribe en tu PC y resume con Claude usando tu suscripción, personal o de empresa, a través de Claude
   Code: la app te guía paso a paso, «Abrir PowerShell», instalar, escribir `claude` y entrar con tu cuenta en el
   navegador, con el inicio de sesión de tu empresa si es la suya, y «Probar»; no ve ni guarda tus credenciales, y
   también encuentra la copia de Claude Code de VS Code con su sesión) o
   **API externa** (Groq, OpenAI, Azure OpenAI o cualquier API compatible con OpenAI: transcripción rápida en la nube
   y resumen, con tu API key; con la primera key, la nube pasa a ser lo predeterminado para la cola y para el
   directo). Las dos últimas avisan de que el contenido de la reunión sale del PC: el texto, a Anthropic con la
   cuenta de Claude; el audio y el texto, al proveedor de la API. En algunas reuniones eso puede ser un bloqueante:
   compruébalo antes (política de tu empresa, acuerdo con tu cliente). Con Azure OpenAI el resumen aún no funciona,
   y con la API lo transcrito en local no se envía solo al resumen: ver «Privacidad».
4. Mientras está abierta, Tenca tiene su icono en la bandeja del sistema (junto al reloj; la primera vez Windows lo
   pone en los iconos ocultos, la flecha `^`, y se puede arrastrar junto al reloj). Cerrar la ventana la deja ahí, en
   segundo plano (el atajo `Ctrl+Alt+R` sigue funcionando): un clic en el icono o el acceso directo la vuelven a abrir.
   Para salir del todo: clic derecho en el icono → Salir (o ⚙ Ajustes → Ayuda → Apagar Tenca). El mismo menú tiene
   «Arrancar con Windows» (copia el acceso directo del menú Inicio en la carpeta Inicio de Windows; apagado por
   defecto). Sin WebView2 (la app se abre en Edge) no hay icono: se apaga desde Ajustes.
5. Para actualizar, descomprime el zip nuevo y ejecuta su `INSTALAR.bat`: encuentra la instalación anterior (por
   Aplicaciones instaladas, por su acceso directo o en su carpeta propia, también con los nombres anteriores de la
   app, «Chuleta» y «Notas») y la actualiza en su carpeta con tus grabaciones (nunca las mueve). Instalada encima de
   Chuleta, no queda nada con ese nombre: sus accesos directos (escritorio, menú Inicio y «Arrancar con Windows»)
   pasan a llamarse «Tenca», sin duplicados; su entrada de Aplicaciones instaladas y su `ABRIR CHULETA.bat` se
   quitan. La carpeta de las reuniones conserva su nombre (`%USERPROFILE%\Chuleta`: renombrarla rompería lo que
   apunta a ella), la app la enseña con ese nombre («Se guarda en Chuleta\…») y los datos siguen en
   `%LOCALAPPDATA%\AudiosApp`. Un anclaje de la barra de tareas no lo puede
   cambiar un instalador: el instalador lo detecta y dice cómo volver a anclarla. Si varias copias de Tenca usan la
   misma carpeta de reuniones (otro PC, OneDrive), actualízalas todas a la vez (ver «Privacidad»).

### Instalar con Setup.exe (otra forma, la misma instalación)

`Tenca - Setup.exe` es un asistente de Inno Setup que hace lo mismo que el zip y su `INSTALAR.bat`: doble clic,
«Instalar», y se abre la misma ventana del instalador con sus pasos. Por dentro lleva el contenido del zip y ejecuta su
`_app\instalar.ps1`, así que se instala igual (para tu usuario, sin permisos de administrador, en `%USERPROFILE%\Tenca`
o en la carpeta que ya tuvieras), con los mismos accesos directos, la misma entrada en «Aplicaciones instaladas» y el
mismo desinstalador; si ya la tenías (también con su nombre anterior), actualiza la de siempre sin tocar tus
reuniones. No lleva listas de vocabulario (se compila en la CI y es público). El zip sigue siendo la forma principal.

- **SmartScreen.** El Setup.exe no está firmado, así que Windows lo para la primera vez: «Windows protegió tu PC» →
  «Más información» → «Ejecutar de todas formas». Es el mismo aviso que con `INSTALAR.bat`; con un ejecutable sin
  firma no hay forma legítima de evitarlo. Firmar no lo quita del todo: un certificado nuevo también avisa hasta que
  acumula reputación. En un PC con Smart App Control activado (Windows 11 doméstico, no los de empresa) un ejecutable
  sin firma se bloquea sin botón para seguir: ahí, el zip.
- **En silencio** (para TI): `"Tenca - Setup.exe" /VERYSILENT /SUPPRESSMSGBOXES` instala sin preguntas ni ventanas y
  sin abrir la app (si falta Python y no hay winget, tampoco abre el navegador: lo dice en el registro); sale con
  código 1 si la instalación no terminó (el motivo, en
  `%LOCALAPPDATA%\AudiosApp\instalar.log`).
- Lo genera `python scripts/build_setup.py` en Windows con Inno Setup 6.7 (ver «Generar el Setup.exe»); la CI lo
  compila y lo prueba en cada push a main que lo toque (`setup-exe`, artefacto `Tenca-Setup`).

### Red y proxy de empresa

- Las llamadas de Tenca a sí misma (a `127.0.0.1`, las del acceso directo y del aviso flotante) no pasan por el
  proxy del sistema, cuya excepción `<local>` no cubre esa dirección: no hace falta pedir a IT una excepción para
  ella.
- Las peticiones a la nube (transcripción, resumen y traducción) usan el proxy que Windows tenga configurado a mano, o
  `HTTPS_PROXY`, y comprueban los certificados con los de Windows (`truststore`), que es donde la empresa instala el
  de su proxy. Un proxy que Windows configura solo, por script (PAC) o por detección automática, Python no lo lee.
- Si pip no puede descargar las librerías, el instalador dice qué paquete ha fallado y por qué: la red o el proxy, un
  Python demasiado nuevo, un pip que solo acepta paquetes compilados (`only-binary`) o el repositorio de paquetes de
  la empresa. pywebview es opcional: si no se instala, la app se abre en Edge.

### Desinstalar

**Configuración → Aplicaciones → Aplicaciones instaladas → Tenca → Desinstalar** (o Panel de control → Programas y
características). Quita los accesos directos, la entrada de Aplicaciones instaladas y la carpeta `_app` (solo lo que
es de la app). **Las reuniones se quedan**: audios, transcripciones, notas, resúmenes, hablantes, carpetas de proyecto
y listas de vocabulario siguen en su carpeta, junto al `LEEME.txt`, y las carpetas vinculadas no se tocan; una
reinstalación vuelve a esa carpeta (`%USERPROFILE%\Tenca`, o `%USERPROFILE%\Chuleta` si venía de ahí; si estaba en
otra, `INSTALAR.bat -Destino <esa carpeta>`).
Una casilla, desmarcada por defecto, borra además `%LOCALAPPDATA%\AudiosApp` (ajustes, API keys, huellas de voz y
registros). Si la app está grabando, no toca nada. Sin ventana: `QuietUninstallString` (`desinstalar.ps1 -Silencioso`,
que no borra `AudiosApp` salvo con `-BorrarDatos`); registro en `%TEMP%\tenca-desinstalar.log`. Python y sus
librerías se quedan.

El manual completo está en `LEEME.txt`.

## Privacidad

- Grabaciones, transcripciones y notas viven en la carpeta de Tenca (y en las carpetas vinculadas), en tu PC. Si una
  de esas carpetas está en OneDrive o SharePoint, se sincroniza como cualquier otra.
- Con «Con tu cuenta de Claude», el texto de la reunión (transcripción con quién habla si se sabe, notas, contexto,
  participantes confirmados e instrucciones; el audio no) se envía a Anthropic a través de Claude Code con la cuenta
  con la que iniciaste sesión: la de empresa, bajo el acuerdo de tu empresa; la personal, bajo sus términos. Tenca
  no ve ni guarda esas credenciales: solo lanza `claude -p` en tu PC, sin herramientas, sin guardar la sesión y sin tus
  hooks ni servidores MCP. Elegir esa opción, con su aviso, es el consentimiento: también se resumen solas las
  reuniones transcritas en local (las de los últimos 3 días).
- Con la nube, el audio (para transcribir) y el texto (para resumir) se envían al proveedor que configures. El resumen
  automático solo envía las reuniones transcritas enteras en la nube. Las transcritas en local solo se envían si marcas
  «Resumir también las reuniones transcritas en local» en Ajustes → Procesado → Resumen con IA, o si las resumes a mano.
  De cada reunión, el resumen envía el título (o el nombre del archivo), la fecha y la duración, la transcripción con
  quién habla, las notas, el contexto, los participantes confirmados y tus instrucciones; el audio, no. Antes de grabar
  o enviar reuniones de trabajo, confirma que lo permiten la política de tu empresa y el acuerdo con tu cliente, si lo
  hay.
- La reunión en curso se lee en el propio PC: el título de la ventana de Teams siempre, y la cita de Outlook clásico
  (por COM) solo si lo activas en Ajustes → Calendario y Teams, donde una frase dice si funciona y, si no, por qué. Los
  asistentes, la app sola solo los lee si «Probar con mi calendario» ha visto que Outlook los da sin preguntar; si
  pregunta, solo al pulsar «Probar». Los asistentes sugeridos se escriben en la cabecera del
  `.md` de esa grabación como sugeridos, con una nota detrás de los nombres (`participantes: Ana, Luis (sugeridos por
  Outlook, sin confirmar)`), y no van al resumen con IA ni a las copias en otras carpetas hasta que los confirmas o
  editas en la página de la reunión (puedes borrarlos ahí). Las grabaciones de antes no llevan la nota: sus
  participantes cuentan como confirmados. El nombre que se sugiere al grabar (el asunto de la cita o el título de la
  reunión de Teams), si lo aceptas, es el nombre de la grabación: va como título al resumen con IA y a las copias en
  otras carpetas.
- Si la misma carpeta de reuniones la usan varias copias de Tenca (otro PC, una carpeta de OneDrive compartida),
  actualízalas todas a la vez: una versión anterior toma los participantes sugeridos por confirmados (con la nota) y
  los envía a su resumen y a sus destinos, y la del 30/10 no conoce `idioma` ni `contexto` y, con ellos, toma la
  cabecera del `.md` entera por notas.
- «Quién habla» es opcional y funciona en local: por reunión se escribe `<grabación>.hablantes.json` junto al audio
  (tramos y «Persona N», sin datos de voz); las huellas de voz que decidas recordar van a
  `%LOCALAPPDATA%\AudiosApp\voces.json`, cifrado con DPAPI, máximo 5 por persona, y se pueden olvidar una a una o todas.
  Una huella de voz es un dato biométrico: la app solo la guarda si marcas que tienes el consentimiento de esa persona.
  Los nombres que les pongas van con la transcripción, al resumen con IA y a las copias en otras carpetas. Los modelos
  (≈47 MB) se descargan de los releases de sherpa-onnx al activar la opción, con su sha256 comprobado: segmentación
  pyannote 3.0 (MIT) y TitaNet-S (`titanet_small`, NVIDIA NeMo; su ficha en NGC remite a la licencia del NeMo Toolkit,
  Apache-2.0; el `.onnx` lo publica el proyecto sherpa-onnx).
- Las copias en otras carpetas escriben texto (nunca audio) en las carpetas que tú elijas, sin los participantes
  sugeridos que no hayas confirmado: si están sincronizadas (OneDrive, SharePoint), ese texto sale de tu PC por esa vía.
  Al borrar una reunión, sus documentos que nadie haya cambiado allí van también a la papelera.
- Las listas de vocabulario (`<proyecto>/Vocabulario/*.txt` y las de las carpetas conectadas) suelen llevar nombres de
  personas y empresas: no se incluyen en el zip que genera la app salvo que lo marques, y este repositorio las ignora.
  Una carpeta conectada se lee donde está (nada se copia) y quitarla no borra nada.
- La API local (`127.0.0.1:8765`) solo atiende a quien presenta el token de la sesión en marcha (ver «API local y
  token de sesión»): en un PC con varias sesiones de Windows a la vez (escritorio remoto, AVD, Citrix), otro usuario
  no puede leer tus reuniones ni manejar la grabadora.
- «Copiar diagnóstico» (Ajustes → Ayuda) solo copia datos técnicos: la versión, el sistema, el motor y el modelo, cómo
  es la carpeta de la app (sin su ruta), cuántas carpetas vinculadas y copias en otras carpetas hay y, de las últimas 30
  líneas de `server.log`, lo fijo de cada mensaje con sus cifras y códigos de error. Los nombres de reuniones, carpetas,
  proyectos, temas y personas, las rutas y el texto libre van como `<texto>`, y las claves no van. Revísalo antes de
  pegarlo y no lo publiques en un sitio público.
- En `%LOCALAPPDATA%\AudiosApp` (fuera de OneDrive: no viaja con la carpeta de las reuniones) quedan, entre otros:
  - `settings.json`: los ajustes y la API key, cifrada con DPAPI (solo tu cuenta de Windows en ese PC la descifra; si
    DPAPI fallara, se guardaría en claro y quedaría anotado en `server.log`). Si se estropea, se aparta como
    `settings.json.roto-<fecha>` y se rescatan las claves, las carpetas vinculadas y los destinos.
  - `voces.json` (las huellas de voz que decidas recordar), `salidas.json` (qué documento de cada destino es de qué
    reunión), `temas.json` (la lista de vocabulario elegida para cada reunión), `transcritas_en_la_nube.json` (qué
    reuniones de los últimos 7 días se transcribieron en la nube: su ruta y la identidad del audio, para el resumen
    automático) y `repasos_pendientes.json` (los repasos finales por hacer).
  - `originales\`: lo que transcribió Whisper antes de que lo corrijas, para «Restaurar original».
  - `sesion.token`: el token de la sesión en marcha (se borra al apagar). `tmp\`: los trozos de audio mientras se
    suben a la nube (se borran al subirlos, y lo que quede, al arrancar y al apagar). `webview\`: el perfil de la
    ventana.
  - Los registros: `server.log`, `worker-*.log`, `recorder.log`, `voces.log` e `instalar.log`. Llevan títulos de
    reuniones, proyectos, temas, nombres de personas y rutas: no los compartas tal cual.

## Desarrollo

El servidor es Python 3.10–3.13 con la librería estándar más `faster-whisper`, `soundcard`, `requests` y `truststore`
y, opcionales (el instalador no se para si fallan), `pywebview` (la ventana, sobre WebView2; sin ella la interfaz se
abre en Edge en modo app), `sherpa-onnx` y `pywin32` (importación perezosa); la interfaz es HTML/JS sin compilar
(`_app/static`). No hay paso de build: el acceso directo lanza `pythonw.exe server.py` (sin consola) y
`_app/Tenca.bat` lo mismo con consola. El nombre visible de la app es `common.APP_NAME` (y `-AppName` en el
instalador); por compatibilidad no cambian con él la carpeta de datos `AudiosApp`, la cabecera `X-Audios`, las
claves de los `.md` ni los identificadores internos que llevan el nombre anterior: el AppUserModelID `Bernardo.Chuleta`
(con otro, la ventana no se agruparía en los anclajes ya hechos), la cabecera `X-Chuleta-Token` (el instalador nuevo
apaga con ella la versión abierta) y las variables de entorno `CHULETA_*` y `NOTAS_*` de los procesos y de la CI.
La versión que se ve en Ajustes → Ayuda → Acerca de sale de `_app/version.txt` (lo escribe `scripts/build_zip.py`:
`AAAA-MM-DD · sha`); en una copia de trabajo sin ese fichero se enseña la fecha y hora del código.

En macOS/Linux el servidor arranca para desarrollar la interfaz y la lógica (la grabadora, el atajo global, la
papelera y los avisos son solo de Windows):

```
python3 _app/server.py --port 8766 --data /tmp/notas-datos --no-browser
```

Toma como carpeta de reuniones la que contiene `_app` (en una copia de trabajo, la raíz del repo: el `.gitignore` en
lista blanca evita que lo que se grabe ahí vaya al repositorio). Con `--no-browser` no abre ninguna ventana: la
interfaz está en `http://127.0.0.1:8766/?t=$(cat /tmp/notas-datos/sesion.token)`.

### API local y token de sesión

La interfaz y los programas de la app (el acceso directo, el aviso flotante, el instalador y el desinstalador) hablan
con `server.py` en `http://127.0.0.1:8765/` (o el `--port` que se le pase). En cada arranque se escribe un token
aleatorio en `sesion.token`, dentro de la carpeta de datos (`%LOCALAPPDATA%\AudiosApp`, o la de `--data`), que se
borra al apagar. `/api/*` y `/media` lo piden en la cabecera `X-Chuleta-Token`; la interfaz lo recibe como cookie
(HttpOnly, SameSite=Strict) al abrir `/?t=<token>`, que redirige a `/`. Sin él responden 403. Los POST piden además
`X-Audios: 1` y `Content-Length` (sin `Transfer-Encoding`), y se rechazan si traen un `Origin` que no es el de la app.
Un script que llame a la API tiene que volver a leer el fichero cada vez que la app arranca (el token cambia), y
conviene que no pase por el proxy del sistema:

```
curl --noproxy '*' -H "X-Chuleta-Token: $(cat /tmp/notas-datos/sesion.token)" http://127.0.0.1:8766/api/ping
```

### Tests

```
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt requests
.venv/bin/python tests/run.py          # unittest; falla si no se ejecuta ningún test
.venv/bin/flake8                       # configuración en setup.cfg
```

Los tests no necesitan Windows, `faster-whisper` ni `pywebview` (la ventana se prueba con un doble): cubren la lógica
pura (SRT, notas, árbol de carpetas, cola, paquete instalable, prompts, grabadora, voces) con dobles y ficheros
temporales. `requests` hace falta para los de la sesión HTTP, el resumen y el diagnóstico (la CI lo instala con
`numpy`). `tests/test_integracion.py` prueba lo que solo existe con todos los arreglos juntos. Los de PowerShell (el
parseo de los `.ps1` y el instalador ejecutado sobre un perfil temporal) usan `pwsh` o la variable `CHULETA_PWSH`
fuera de Windows; sin PowerShell se saltan. La prueba de integración de voces (`tests/test_voices_integration.py`)
solo corre si hay `sherpa-onnx`, `say` (macOS) y `NOTAS_VOCES_DATA` apunta a una carpeta de datos con los modelos en
`models/voces/`. Los tests de escritorio de Windows (`tests/test_reunion_windows.py`: ventana de Teams simulada, aviso
flotante con captura de pantalla) solo corren allí, en el workflow `reunion-windows`, y los de la ventana real
(`NOTAS_VENTANA_REAL=1`), en `windows-real`; en el resto se saltan.

«Quién habla» y el cifrado con DPAPI se han desarrollado y probado en macOS (la diarización con sherpa-onnx real;
DPAPI con un doble, porque `crypt32` solo existe en Windows). En Windows, DPAPI real se prueba en el job
`windows-latest` de GitHub Actions (`tests/test_voices.py::test_dpapi_real`); el instalador, la grabadora y la
interfaz de voces aún no se han ejecutado a mano en Windows.

Si algo no cuadra, abre un issue contando qué ha pasado, sin datos de tus reuniones: los issues de este repositorio
son públicos. Lo que copia Ajustes → Ayuda → «Copiar diagnóstico» solo lleva datos técnicos (los nombres, las
rutas y el texto libre van como `<texto>`, y las claves no van), pero la propia app aconseja revisarlo y no publicarlo
en un sitio público. `server.log` y `voces.log` sí llevan nombres de reuniones, carpetas y personas: no los pegues.

### CI

- `tests.yml`, con cada push y pull request: la suite en Ubuntu y Windows × Python 3.10–3.13, el parseo de
  `_app/*.ps1` con Windows PowerShell 5.1, flake8 y el job `agnosticismo`.
- `ui-smoke` (la interfaz en Chromium con Playwright, en Ubuntu y Windows: arranque con token, Ajustes, Reuniones,
  Calendario, Inicio y el diálogo de Grabar sin errores de JavaScript; el aviso de versión anterior y el de servidor
  lento). En local: `pip install playwright && python -m playwright install chromium`; sin Playwright, el test se
  salta.
- `lookfeel-e2e` (E2E del instalador y del desinstalador), `reunion-windows` (ventana de Teams simulada, aviso
  flotante) y `windows-real` (formato regional español, la ventana real ante el apagado de Windows y sin WebView2):
  con cada push a main que toque lo que prueban, y a mano. `lookfeel-spike` (medición archivada), solo a mano.
- `setup-exe`: compila el Setup.exe con el Inno Setup del runner y lo prueba en Windows con PowerShell 5.1
  (`scripts/e2e/setup_e2e.ps1`: instalación en silencio, el asistente pulsado con UI Automation y sus capturas,
  desinstalar, la versión de main anterior al renombrado con el Setup encima y un fallo simulado); con cada push a main
  que lo toque, y a mano. Deja el Setup.exe como artefacto `Tenca-Setup`.
- Agnosticismo: `test_sin_rastro_de_clientes` (`tests/test_package.py`) revisa todo lo que git tiene en el índice con
  la lista privada del secreto de Actions `NOTAS_PALABRAS_VETADAS` (expresiones regulares separadas por comas o una
  por línea, sin distinguir mayúsculas, con `^` y `$` por línea). Si encuentra algo, falla con fichero:línea y número
  de patrón, nunca con el texto. Sin el secreto, en un fork o con Dependabot, se salta y deja el aviso «Agnosticismo
  sin ejecutar» en el resumen del run; ningún job falla por eso. En local, con la lista en un fichero fuera del repo:
  `NOTAS_EXIGIR_VETADAS=1 NOTAS_PALABRAS_VETADAS="$(cat ~/tenca-vetadas.txt)" .venv/bin/python tests/run.py` (en
  Windows, ver el comentario de ese test).

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
python3 scripts/build_zip.py            # deja dist/Tenca - instalador.zip (sin vocabulario) y _app/version.txt
```

`--vocab [CARPETA]` añade las listas `<proyecto>/Vocabulario/*.txt` de esa carpeta (por defecto, la raíz del repo):
llevan nombres de personas y empresas, así que ese zip es solo para gente del mismo proyecto, nunca para publicar.

### Generar el Setup.exe

```
python scripts/build_setup.py                        # Windows, Inno Setup 6.7: dist/Tenca - Setup.exe (sin vocabulario)
python3 scripts/build_setup.py --preparar CARPETA    # cualquier sistema: solo el contenido y la orden de ISCC
```

Lleva lo mismo que el zip (sale de `server.build_package`) y la misma versión. El asistente es
`scripts/setup/tenca.iss`; su imagen lateral la rehace `python scripts/setup/imagenes.py` (Pillow) desde
`_app/static/tenca.png`.

### Regenerar los iconos

```
.venv/bin/pip install pillow && .venv/bin/python make_icons.py
```

Rasteriza `design/icono.svg` (y, para 16, 20, 24 y 32 px, `design/logo-small.svg` e `icono-20/24/32.svg`, dibujados en
la rejilla de su tamaño) con Chrome o Edge headless y escribe
`_app/static/tenca.ico` (nueve tamaños, todos en BMP: Windows no siempre pinta bien las entradas PNG pequeñas) y
`_app/static/tenca.png`. Solo hace falta si cambia el logo.

## Invitar al autor a un café

Tenca es gratis. Si te sirve, puedes [invitar al autor a un café por PayPal](https://www.paypal.com/donate?business=bdo.st.f%40gmail.com&currency_code=EUR). El mismo enlace está en
Ajustes › Ayuda › Acerca de; no hay ventanas emergentes ni recordatorios.

## Licencia

MIT. Ver `LICENSE`.

Las tipografías de `_app/static` (Fraunces y Manrope) van con la SIL Open Font License 1.1, no con la MIT: avisos y
licencia en `_app/static/fuentes-OFL.txt`. `_app/qrcodegen.py` es la librería de códigos QR de Project Nayuki, MIT, con
su aviso de copyright y su licencia en la cabecera del propio archivo.
