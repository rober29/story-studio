"""Carga y validación de las historias declaradas en stories/*.json.

El orden importa: se valida la estructura ANTES de sustituir los placeholders
de personaje, para que un JSON incompleto produzca un error que nombre el
campo que falta en vez de un KeyError a mitad de la sustitución.
"""

import json
import os
import re

from studio.errors import StudioError

FORMATS = ("A", "B", "C", "D")

# El formato D imita el montaje de los reels de monigotes: franja 16:9 con
# banner de serie y cortes cada ~2,4 s. El estilo por defecto tiene que ser el
# contrario del cinematográfico general, o una historia D sin 'style' saldría
# fotorrealista sin que nada avise.
#
# Cambiado el 2026-08-11 de monigotes crudos a caricatura ilustrada. El reel de
# Mansa Musa salió así por accidente —la descripción del personaje pesó más que
# el estilo, que decía 'no shading'— y el resultado gustó más: mismo coste, más
# rico. Se elige a propósito y se deja de pelear consigo mismo.
#
# OJO: 'style' entra en image_key. Tocar esta constante invalida la caché de
# TODAS las historias en formato D, así que volver a renderizar una ya publicada
# costaría sus imágenes otra vez. Los reels ya hechos no se tocan.
D_STYLE = (
    "hand-drawn cartoon illustration, simple characters with round heads, dot eyes "
    "and small simple mouths, black ink outlines of even weight, FLAT colour fills "
    "with no gradients and only minimal shading, EVERY object in the scene is "
    "filled with colour including animals and props, hand-painted gouache "
    "backgrounds with visible brush strokes, warm earthy palette, off-white paper "
    "texture, no detailed facial features, no crosshatching, no 3d render, "
    "no photorealism"
)

FORMAT_STYLE = {"D": D_STYLE}

DEFAULTS = {
    "format": "C",
    "voice": "es-MX-DaliaNeural",
    "voice_rate": 1.0,
    "style": "cinematic, photorealistic, epic lighting, highly detailed",
    "watermark": "",
    "banner": "",
    "part": "",
    "max_images": 45,
    "min_beat_duration": 1.1,
    "max_beat_duration": 2.6,
    # 0 por defecto, y es deliberado: la música de resource/songs/ viene del
    # proyecto original SIN NINGUNA PROCEDENCIA (27 de 29 pistas duran
    # exactamente 180,000 s y tienen los tags borrados, o sea que están
    # recortadas de fuentes desconocidas). Incrustarla en un reel monetizado
    # arriesga una reclamación de Content ID, que desvía los ingresos en
    # silencio. En TikTok e Instagram conviene además añadir la música desde la
    # propia app: está pre-autorizada y el algoritmo favorece los sonidos nativos.
    "bgm_volume": 0.0,
    "max_chars_per_card": 28,
    "max_lines_per_card": 2,
    "characters": {},
    "series": "",
    "lang": "es",
    # historia de la que se heredan las imágenes ya pagadas (versiones en
    # otro idioma: el prompt de imagen está en inglés y no cambia al traducir)
    "images_from": "",
    # duración mínima de la voz en segundos; 0 desactiva la comprobación.
    # TikTok solo remunera a partir de 60 s, así que 65 deja colchón.
    "min_duration": 0,
    # formato D: de cada imagen se sacan varios planos recortando regiones
    # distintas, para cortar cada ~2,4 s sin pagar una imagen por corte
    "target_shot": 2.4,
    "min_shot": 1.5,
    "max_shots_per_scene": 4,
    # proveedor usado en --quality final; en draft siempre se usa el gratuito.
    # 2k por defecto: a 1k la imagen sale a 768px de ancho y habría que
    # ampliarla igual que con el generador gratuito (medido, no supuesto)
    "image_provider": "gemini",
    "image_tier": "2k",
}

PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z_][\w-]*\}")


def _read_json(path, what):
    if not os.path.isfile(path):
        raise StudioError(f"no existe {what}: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise StudioError(f"{path} no es JSON válido: línea {e.lineno}, {e.msg}")
    if not isinstance(data, dict):
        raise StudioError(f"{path}: la raíz debe ser un objeto JSON")
    return data


def load_series(name, base_dir, source="<story>"):
    """Lee stories/series/<name>.json."""
    if name != os.path.basename(name) or name in (".", ".."):
        raise StudioError(
            f"{source}: 'series' es un nombre de archivo, no una ruta "
            f"(recibido: {name!r})"
        )
    return _read_json(
        os.path.join(base_dir, "series", f"{name}.json"), f"la serie {name!r}"
    )


def merge_series(raw, series):
    """Combina serie e historia. Precedencia: historia > serie > DEFAULTS.

    Los personajes se mezclan clave a clave para que una parte pueda añadir uno
    puntual sin repetir todo el reparto. Que el 'style' y las descripciones sean
    literalmente el mismo string en todas las partes es lo que garantiza que las
    hojas de personaje se reutilicen entre ellas en vez de volver a pagarse.
    """
    merged = {k: v for k, v in series.items() if k != "parts"}
    characters = dict(series.get("characters") or {})
    characters.update(raw.get("characters") or {})
    merged.update(raw)
    if characters:
        merged["characters"] = characters
    if "parts" in series and "part" not in raw:
        try:
            merged["part"] = series["parts"].index(raw.get("id")) + 1
        except (ValueError, AttributeError):
            pass
    return merged


def load_story(path):
    """Lee, valida y resuelve una historia. Única puerta de entrada."""
    raw = _read_json(path, "el archivo de historia")

    # la serie se mezcla ANTES de validar, así toda la validación existente
    # corre sobre el diccionario final
    if raw.get("series"):
        series = load_series(raw["series"], os.path.dirname(os.path.abspath(path)), path)
        raw = merge_series(raw, series)

    story = validate_story(raw, source=path)
    resolve_prompts(story, source=path)
    return story


def part_label(story):
    """Etiqueta del banner: 'Pt. 3' desde un entero, o el string tal cual."""
    part = story.get("part")
    if isinstance(part, bool) or part in (None, ""):
        return ""
    if isinstance(part, int):
        return f"Pt. {part}"
    return str(part)


def validate_story(raw, source="<story>"):
    """Comprueba la estructura y aplica defaults. Devuelve una copia."""
    story = dict(DEFAULTS)
    story.update(raw)

    story_id = story.get("id")
    if not isinstance(story_id, str) or not story_id.strip():
        raise StudioError(f"{source}: falta 'id' (string no vacío)")
    # el id nombra un directorio bajo storage/, no puede escaparse de él
    if story_id != os.path.basename(story_id) or story_id in (".", ".."):
        raise StudioError(
            f"{source}: 'id' no puede contener separadores de ruta ni '..' "
            f"(recibido: {story_id!r})"
        )

    if story["format"] not in FORMATS:
        raise StudioError(
            f"{source}: 'format' debe ser uno de {FORMATS}, "
            f"recibido {story['format']!r}"
        )

    # estilo por defecto propio del formato, solo si no lo fijaron la historia
    # ni su serie: el default general es cinematográfico y arruinaría un reel D
    if "style" not in raw and story["format"] in FORMAT_STYLE:
        story["style"] = FORMAT_STYLE[story["format"]]

    scenes = story.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise StudioError(f"{source}: 'scenes' debe ser una lista con al menos una escena")

    for i, scene in enumerate(scenes):
        where = f"{source}: escena {i}"
        if not isinstance(scene, dict):
            raise StudioError(f"{where} debe ser un objeto")
        for field in ("text", "prompt"):
            value = scene.get(field)
            if not isinstance(value, str) or not value.strip():
                raise StudioError(f"{where}: falta '{field}' (string no vacío)")
        if not scene["text"].split():
            raise StudioError(f"{where}: 'text' no contiene ninguna palabra")

    _check_number(story, "max_images", source, minimum=1, integer=True)
    _check_number(story, "min_beat_duration", source, minimum=0.05)
    _check_number(story, "max_beat_duration", source, minimum=0.05)
    _check_number(story, "voice_rate", source, minimum=0.1)
    _check_number(story, "max_chars_per_card", source, minimum=8, integer=True)
    _check_number(story, "max_lines_per_card", source, minimum=1, integer=True)
    _check_number(story, "target_shot", source, minimum=0.5)
    _check_number(story, "min_shot", source, minimum=0.3)
    _check_number(story, "max_shots_per_scene", source, minimum=1, integer=True)
    _check_number(story, "min_duration", source, minimum=0)

    if story["min_shot"] > story["target_shot"]:
        raise StudioError(
            f"{source}: 'min_shot' ({story['min_shot']}) no puede superar "
            f"'target_shot' ({story['target_shot']})"
        )

    if story["max_beat_duration"] <= story["min_beat_duration"]:
        raise StudioError(
            f"{source}: 'max_beat_duration' ({story['max_beat_duration']}) debe ser "
            f"mayor que 'min_beat_duration' ({story['min_beat_duration']})"
        )

    bgm = story["bgm_volume"]
    if not isinstance(bgm, (int, float)) or isinstance(bgm, bool) or not 0 <= bgm <= 1:
        raise StudioError(f"{source}: 'bgm_volume' debe estar entre 0 y 1, recibido {bgm!r}")

    origen = story["images_from"]
    if origen:
        if not isinstance(origen, str) or origen != os.path.basename(origen) or origen in (".", ".."):
            raise StudioError(
                f"{source}: 'images_from' debe ser el id de otra historia, sin "
                f"separadores de ruta (recibido: {origen!r})"
            )
        if origen == story_id:
            raise StudioError(f"{source}: 'images_from' no puede apuntar a la propia historia")
        if story["format"] == "B":
            raise StudioError(
                f"{source}: 'images_from' no sirve en formato B, porque cada prompt "
                f"lleva dentro el texto narrado (el caption del beat) y una versión "
                f"en otro idioma necesita imágenes nuevas de todas formas. Usa "
                f"formato C o D para las traducciones, o quita 'images_from'"
            )

    if not isinstance(story["characters"], dict):
        raise StudioError(f"{source}: 'characters' debe ser un objeto nombre -> descripción")
    for name, desc in story["characters"].items():
        if not isinstance(desc, str) or not desc.strip():
            raise StudioError(f"{source}: la descripción del personaje {name!r} está vacía")

    for field in ("voice", "style"):
        if not isinstance(story[field], str) or not story[field].strip():
            raise StudioError(f"{source}: '{field}' debe ser un string no vacío")

    if story["format"] in ("A", "D") and not story["banner"]:
        print(
            f"[aviso] {source}: formato {story['format']} sin 'banner'; "
            f"el video saldrá sin la pastilla de serie"
        )

    return story


def _check_number(story, field, source, minimum, integer=False):
    value = story[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StudioError(f"{source}: '{field}' debe ser numérico, recibido {value!r}")
    if integer and int(value) != value:
        raise StudioError(f"{source}: '{field}' debe ser entero, recibido {value!r}")
    if value < minimum:
        raise StudioError(f"{source}: '{field}' debe ser >= {minimum}, recibido {value!r}")
    if integer:
        story[field] = int(value)


def resolve_prompts(story, source="<story>"):
    """Sustituye {personaje} en los prompts. Muta las escenas de story.

    Anota además qué personajes aparecen en cada escena antes de sustituirlos:
    esa información se perdía al reemplazar el texto, y es la que permite pasar
    la hoja de referencia del personaje correcto a los generadores que la
    admiten. Va ordenada porque entra en la clave de caché.
    """
    characters = story["characters"]
    for i, scene in enumerate(story["scenes"]):
        prompt = scene["prompt"]
        scene["characters_present"] = sorted(
            name for name in characters if "{" + name + "}" in prompt
        )
        for name, desc in characters.items():
            prompt = prompt.replace("{" + name + "}", desc)
        leftover = PLACEHOLDER_RE.findall(prompt)
        if leftover:
            known = ", ".join(sorted(characters)) or "(ninguno)"
            raise StudioError(
                f"{source}: escena {i} usa {leftover[0]} pero ese personaje no está "
                f"definido en 'characters'. Definidos: {known}"
            )
        scene["prompt"] = prompt
    return story


# Nombre heredado, de cuando cada historia vivía en su propio directorio y no
# hacía falta distinguirlas por archivo.
LEGACY_VIDEO = "final-1.mp4"


def video_path(task_dir, story):
    """Ruta donde ESCRIBIR el MP4 final.

    Lleva el id de la historia, que desde la convención de sufijos ya incluye el
    idioma. Importa al descargarlo al móvil para publicar: 'mansa-musa-es.mp4' y
    'mansa-musa-en.mp4' se distinguen de un vistazo en la carpeta de descargas,
    y dos 'final-1.mp4' no.
    """
    return os.path.join(task_dir, f"{story['id']}.mp4")


def find_video(task_dir, story):
    """Ruta desde donde LEER el MP4, aceptando el nombre antiguo.

    Los reels renderizados antes de este cambio siguen en disco como
    final-1.mp4; verify y la publicación tienen que encontrarlos igual, o un
    cambio de nombre obligaría a volver a renderizar (y a pagar) lo ya hecho.
    """
    nuevo = video_path(task_dir, story)
    if os.path.isfile(nuevo):
        return nuevo
    viejo = os.path.join(task_dir, LEGACY_VIDEO)
    return viejo if os.path.isfile(viejo) else nuevo


def script_text(story):
    """Guion completo tal y como se envía al TTS."""
    return " ".join(scene["text"].strip() for scene in story["scenes"])


def scene_word_counts(story):
    """Palabras declaradas por escena, según el guion escrito."""
    return [len(scene["text"].split()) for scene in story["scenes"]]
