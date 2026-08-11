# Story Studio

Generador de reels verticales (9:16) a partir de una historia declarada en un JSON.
Escribes el guion y los prompts de imagen; el resto —voz, imágenes, subtítulos
karaoke, montaje y control de calidad— es automático.

No necesita claves de API: las imágenes vienen de [Pollinations](https://pollinations.ai)
y la voz de Edge TTS, ambas gratuitas.

```bash
venv\Scripts\python.exe story_studio.py --story stories\markov.json
```

El resultado queda en `storage/tasks/story-<id>/final-1.mp4`.

---

## Los tres formatos

| Formato | Qué es | Cuándo usarlo |
|---|---|---|
| **C** | Slideshow cinematográfico: una imagen por escena con zoom lento (Ken Burns), título de capítulo con emoji y subtítulos karaoke abajo | Divulgación, historia, documental |
| **B** | Una imagen por *beat* de narración (cada 1–3 s) con el texto grande horneado en el centro | Ritmo alto, retención, curiosidades |
| **A** | Franja 16:9 de estilo caricatura sobre fondo borroso, con pastilla de serie ("Pt. 1") y karaoke sobre la franja | Series por partes, humor |

---

## Escribir la historia con IA

Puedes redactar el JSON a mano o pedirlo a partir de un tema:

```bash
venv\Scripts\python.exe story_writer.py --topic "el naufragio del Essex" --format B --scenes 7 --duration 45 --out stories\essex.json
```

Genera guion, prompts de imagen en inglés, capítulos con emoji y fichas de
personaje, cuidando las reglas que importan: números con letra, reparto
equilibrado del texto entre escenas y la duración objetivo.

Antes de escribir el archivo lo valida **con el mismo código que usa el
pipeline**, así que si el resultado se guarda es porque `story_studio.py` lo va
a aceptar. Si no pasa, lo reintenta una vez con el error delante.

No genera ningún video: revisa y edita el archivo, y luego lánzalo tú. Usa un
modelo de texto de Gemini, que tiene tier gratuito, así que **no cuesta nada**.

Opciones: `--format A|B|C`, `--scenes N`, `--duration segundos`, `--lang`,
`--voice`, `--model`, `--force` (sobrescribir).

## Música: por defecto no se incrusta, y es a propósito

`bgm_volume` vale **0** por defecto. Las pistas de `resource/songs/` vienen del
proyecto original **sin ninguna procedencia**: 27 de las 29 duran exactamente
180,000 segundos y tienen los tags borrados, o sea que están recortadas de
fuentes desconocidas. Incrustarlas en un reel monetizado expone a una
reclamación de Content ID, que desvía los ingresos **en silencio**.

Para TikTok e Instagram, además, lo óptimo es añadir la música **desde la propia
app** al publicar: está pre-autorizada y el algoritmo favorece los sonidos
nativos de la plataforma. Incrustarla en el MP4 es lo peor de ambos mundos.

En YouTube subido por API el vídeo va tal cual, así que se queda solo con la
narración — que para reels de divulgación con karaoke funciona bien.

Si algún día quieres música incrustada, usa fuentes con licencia documentada
(YouTube Audio Library, Pixabay Music o Free Music Archive filtrando por CC0) y
guarda junto a los archivos el título, autor, licencia y URL de cada pista.

## Duración: por qué importan los 60 segundos

TikTok solo remunera vídeos de **60 segundos o más**, y paga entre 6 y 20 veces
más que YouTube Shorts por la misma vista. La duración del reel es exactamente
la del audio y no se puede alargar en el montaje: la única palanca es escribir
más texto.

Declara `"min_duration": 65` (65 y no 60, para tener colchón) y el pipeline
avisa si el guion se queda corto, diciendo cuántas palabras faltan.

```bash
venv\Scripts\python.exe story_pace.py --measure
venv\Scripts\python.exe story_pace.py --probe --story stories\faros.json
```

`--measure` recalcula el ritmo real desde los reels ya generados. `--probe`
sintetiza un guion en unos segundos **sin tocar nada**, para iterar la duración
antes de generar una sola imagen.

Ritmo medido: **español 2,15-2,50 palabras/s, inglés 2,55-3,05**. El inglés
narra un 19 % más rápido, así que una traducción literal dura *menos* que el
original: para 65 segundos hacen falta **163 palabras en español y 199 en inglés**.

## Versiones en otro idioma (casi gratis)

La clave de caché de una imagen incluye su `prompt` —que está en inglés y no
cambia al traducir— pero **no el texto narrado**. Así que una versión en otro
idioma reutiliza las imágenes ya pagadas:

```json
{ "id": "odd-history-3", "lang": "en", "images_from": "cosas-raras-3",
  "voice": "en-US-AndrewNeural", "scenes": [ ... ] }
```

Conserva **idénticos** el `style`, los `characters` y el `prompt` de cada escena
(entran en la clave: tocarlos cuesta dinero) y traduce solo los `text`.

Las imágenes se enlazan en duro desde la historia donante, así que `--prune` de
una **no** puede borrar lo que pagó la otra. Con `--phase plan --quality final`
verás `0,00 $`. Usa `--no-adopt` si quieres forzar imágenes propias.

No funciona en formato B: ahí el prompt lleva dentro el texto narrado.

## Publicar

```bash
venv\Scripts\python.exe story_publish.py --story stories\faros.json --pack
```

Deja en `storage/publish/<fecha>_<id>_<idioma>/` el MP4, la portada y **un
archivo de texto por plataforma con exactamente lo que hay que pegar** — sin
rótulos ni markdown, para poder abrirlo en el móvil, seleccionar todo y pegar.

YouTube sí se sube solo:

```bash
venv\Scripts\python.exe story_publish.py --story stories\faros.json --youtube --channel es
```

Opciones: `--dry-run` (muestra lo que enviaría sin subir), `--privacy`,
`--publish-at` para programarlo, `--authorize` y `--check-auth`.

Se sube como **privado** por defecto. Y no se puede subir dos veces lo mismo: un
libro mayor guarda el sha256 de cada vídeo publicado.

TikTok e Instagram se publican a mano a propósito: su API exige auditorías de
2-4 semanas y, sin aprobar, deja el contenido en privado.

## El JSON de la historia

Mínimo viable:

```json
{
  "id": "mi-historia",
  "format": "C",
  "scenes": [
    {
      "text": "Lo que dice la voz en esta escena.",
      "prompt": "what the image shows, in english"
    }
  ]
}
```

### Campos

| Campo | Por defecto | Qué hace |
|---|---|---|
| `id` | *(obligatorio)* | Nombre de la carpeta de salida. Sin barras ni `..` |
| `format` | `"C"` | `A`, `B` o `C` |
| `scenes` | *(obligatorio)* | Lista de escenas; cada una con `text` y `prompt` |
| `title` | `id` | Solo informativo |
| `voice` | `es-MX-DaliaNeural` | Cualquier voz de Edge TTS (ver abajo) |
| `voice_rate` | `1.0` | Velocidad del habla |
| `style` | cinemático | Sufijo que se añade a **todos** los prompts: es lo que unifica la estética |
| `characters` | `{}` | Fichas de personaje reutilizables (ver abajo) |
| `watermark` | `""` | Texto pequeño en la parte inferior |
| `bgm_volume` | `0.15` | Volumen de la música de fondo, `0` la desactiva |
| `max_chars_per_card` / `max_lines_per_card` | `28` / `2` | Tamaño de las tarjetas de subtítulo (formatos A y C) |

Solo para **formato B**:

| Campo | Por defecto | Qué hace |
|---|---|---|
| `max_images` | `45` | Tope duro de imágenes (y de descargas) |
| `min_beat_duration` | `1.1` | Segundos mínimos por imagen. Bájalo a `0.8` para un ritmo más hipnótico, súbelo a `1.5` para uno más pausado |
| `max_beat_duration` | `2.6` | Segundos máximos antes de forzar un corte |

Solo para **formato A**:

| Campo | Qué hace |
|---|---|
| `banner` | Texto de la pastilla naranja, p. ej. `"Curiosidades de la Historia"` |
| `part` | Número de parte, p. ej. `"Pt. 1"` |

Solo para **formato C**: cada escena admite `chapter`, un título que aparece arriba
mientras dura la escena. Si empieza por un emoji, se dibuja en color:

```json
{ "chapter": "⚔️ 1521: El sitio final", "text": "...", "prompt": "..." }
```

### Fichas de personaje

El mismo personaje descrito de dos formas distintas produce dos caras distintas.
Defínelo una vez y referéncialo con `{nombre}`:

```json
"characters": {
  "markov": "a 49 year old bulgarian writer with thick dark mustache, receding dark hair, wearing a brown tweed coat and dark red scarf"
},
"scenes": [
  { "text": "Espera el autobús.", "prompt": "{markov} waiting at a london bus stop, rainy street" }
]
```

Cuanto más específica sea la ficha (edad, pelo, prendas **con colores**), más
estable sale el personaje. Si usas un `{nombre}` que no existe, el pipeline se
detiene diciendo qué escena y qué placeholder.

### Consejos de escritura

- **Los prompts, en inglés**: los modelos de imagen responden bastante mejor.
- **Los números, con letra** en `text`: escribe "mil quinientos veintiuno", no "1521".
  El TTS lo pronuncia igual, pero así las palabras del guion y las del audio
  coinciden y los subtítulos quedan mejor repartidos.
- **Una idea por escena.** La duración de cada imagen se calcula a partir de lo
  que tarda en narrarse su texto.

### Voces

Cualquier voz de Edge TTS. Las más útiles en español:

```
es-MX-DaliaNeural (F)   es-MX-JorgeNeural (M)
es-ES-ElviraNeural (F)  es-ES-AlvaroNeural (M)
es-AR-ElenaNeural (F)   es-CO-GonzaloNeural (M)
```

Lista completa: `venv\Scripts\python.exe -m edge_tts --list-voices`

---

## Cómo funciona

Cuatro fases, cada una en su propio proceso para que la memoria se libere entre ellas:

1. **assets** — genera la voz, captura los tiempos de cada palabra y descarga las imágenes.
2. **visuals** — monta el video mudo: zoom, banners, marca de agua.
3. **render** — hornea el karaoke, lo superpone y mezcla voz con música.
4. **verify** — audita el MP4 resultante y falla si algo no cuadra.

### Caché y reanudación

Cada archivo generado se identifica por el hash de aquello de lo que depende.
Cambias el `style` → se rebajan todas las imágenes; cambias el texto de una
escena → se rehace la voz y todo lo que cuelga de ella; no cambias nada → no se
descarga ni se renderiza nada.

Si una corrida se interrumpe (o falla una descarga), relánzala: continúa donde
iba sin repetir el TTS ni desalinear los subtítulos ya calculados.

Para ver qué se regeneraría **sin gastar descargas ni escribir nada**:

```bash
venv\Scripts\python.exe story_studio.py --story stories\markov.json --phase plan
```

### Comandos

| Comando | Para qué |
|---|---|
| `--phase plan` | Dry-run: qué se regeneraría, por qué y cuánto costaría |
| `--phase assets\|visuals\|render\|verify` | Ejecutar una sola fase |
| `--quality draft\|final` | Generadores gratuitos (por defecto) o de pago |
| `--budget N` | Tope de gasto en dólares para esa corrida (por defecto 2) |
| `--yes` | No preguntar antes de gastar ni de borrar |
| `--force` | Ignorar la caché de esa fase |
| `--prune` | Borrar imágenes que ya nadie referencia |

### Borrador y final

`--quality draft` (el valor por defecto) usa Pollinations y Edge TTS: gratis, sin
límite de iteraciones. Cuando el reel te convence, `--quality final` lo regenera
con los proveedores de pago que declare la historia.

Los dos modos **conviven en disco**: sus imágenes se guardan por separado y
alternar entre ellos no borra las del otro. Antes de gastar, `--phase plan`
te dice cuánto costaría, y la generación pide confirmación si pasa de 0,50 $.

```json
"image_provider": "gemini",
"image_tier": "1k"
```

Estos dos campos solo se usan en modo final; en borrador se ignoran.

---

## Control de calidad

`--phase verify` revisa el MP4 y escribe `storage/tasks/story-<id>/verify/report.json`:

- La imagen dura lo mismo que la voz (ni se corta antes ni deja cola congelada)
- No hay fotogramas negros ni planos congelados
- Hay tantas imágenes distintas como escenas o beats (detecta caché rancia)
- Los subtítulos se ven durante la narración
- El resaltado amarillo del karaoke está activo (formatos A y C)

Comparar el `report.json` entre corridas sirve como red de regresión.

---

## Problemas frecuentes

**"el TTS no devolvió marcas de palabra"** — la voz no existe o no hay conexión.
Comprueba el nombre con `--list-voices`.

**"fallaron N imágenes"** — Pollinations falló o limitó. Relanza: lo ya
descargado está en caché y solo se reintenta lo que falta.

**"el generador devolvió imágenes más pequeñas de lo pedido"** — es normal:
Pollinations topa el lado largo en 1024 px, así que las imágenes se amplían a
1080x1920. Es el techo de calidad del proveedor gratuito.

**"escena N quedaría con X s de duración"** — esa escena tiene muy poco texto
comparada con las demás. Alárgala o fusiónala con la vecina.

**El video no pasa la verificación** — mira el detalle en pantalla y el
`report.json`. Si es la caché, `--force` o `--prune` y vuelve a generar.

---

## Desarrollo

```bash
venv\Scripts\python.exe -m unittest discover -s test/studio -t .
```

El paquete `studio/` separa la lógica pura (`timing.py`, `story.py`, `cache.py`,
sin dependencias pesadas) del dibujo y el vídeo, así que los tests de la parte
delicada —el reparto de palabras entre escenas y la agrupación temporal— corren
en menos de un segundo.

Si cambias el código de dibujo o de agrupación, sube la constante correspondiente
(`TIMING_V`, `CAPTION_V`, `KARAOKE_V`, `IMG_V`) en `studio/cache.py`: es lo único
que la caché no puede deducir sola.
