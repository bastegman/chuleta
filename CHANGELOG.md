# Cambios

## Sin publicar

Punto de partida: la versión del 30/10/2026 tal como se exportaba desde la propia app, saneada para que no lleve
nada de ningún proyecto concreto.

### Cambiado
- La app es agnóstica: ejemplos, placeholders, avisos y el manual ya no nombran a ningún cliente, empresa ni
  persona. Los avisos de privacidad hablan de «la política de tu empresa y el acuerdo con tu cliente, si lo hay».
- La etiqueta «Workstream» de la interfaz pasa a «Tema». Los ficheros `.md` siguen usando las claves `proyecto`
  y `workstream`, así que las reuniones existentes no cambian.
- Los textos de la interfaz nombran al proveedor configurado («Hecho con IA en Groq/OpenAI/…») en vez de dar
  por hecho Groq.
- El idioma por defecto en instalaciones nuevas es «Detectar». La opción «Español + inglés» sigue ahí y los
  ajustes ya guardados no cambian.
- `requirements.txt`, `LICENSE` (MIT), `README.md` y este fichero.

### Añadido
- `_app/export/` (plantillas del instalador con la fecha como `{FECHA}`), que faltaba en el zip exportado.
