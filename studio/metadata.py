"""Título, descripción y hashtags por plataforma.

Módulo puro: define las reglas y las aplica, pero no habla con ninguna API ni
escribe en disco. La llamada al modelo vive en story_publish.py.

Los metadatos NO viven dentro del JSON de la historia a propósito: `audio_key`
incluye el guion completo, así que iterar sobre un título dentro de ese archivo
arriesgaría invalidar el audio y con él las tarjetas, los planos y el karaoke.
"""

import hashlib
import json
import re
import unicodedata

META_V = 1

# Reglas duras de cada plataforma. Las de YouTube no son estéticas: la API
# rechaza títulos de más de 100 caracteres y devuelve 400 si aparecen < o >.
PLATFORM_RULES = {
    "youtube": {"title_max": 100, "title_target": 70, "tags": (3, 5), "forced": ("Shorts",)},
    "tiktok": {"caption_max": 2200, "tags": (3, 5), "forced": ()},
    "instagram": {"caption_max": 2200, "tags": (5, 8), "forced": ()},
}

ILLEGAL_TITLE = re.compile(r"[<>]")

SCHEMA = {
    "type": "object",
    "properties": {
        "youtube_title": {"type": "string"},
        "youtube_description": {"type": "string"},
        "tiktok_caption": {"type": "string"},
        "instagram_caption": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["youtube_title", "youtube_description", "tiktok_caption", "keywords"],
}


def build_prompt(story, duration, lang):
    idioma = "inglés" if lang == "en" else "español"
    guion = " ".join(scene["text"] for scene in story["scenes"])
    return f"""Eres editor de contenido para reels verticales de divulgación.

Escribe los metadatos de publicación de este reel, TODO en {idioma}.

GUION NARRADO:
{guion}

REGLAS:
1. 'youtube_title': máximo 70 caracteres. Curiosidad o dato sorprendente, sin
   clickbait mentiroso. Nada de comillas angulares.
2. 'youtube_description': 2-4 frases. Las dos primeras líneas son lo único que
   se ve sin desplegar, así que el gancho va ahí. Sin hashtags: se añaden solos.
3. 'tiktok_caption': una o dos frases, con el gancho en los primeros ochenta
   caracteres. Sin hashtags.
4. 'instagram_caption': parecido pero algo más descriptivo. Sin hashtags.
5. 'keywords': entre ocho y catorce, en minúsculas, SIN almohadilla y sin
   espacios de más. Mezcla términos amplios del tema y otros específicos de
   esta historia concreta.

El reel dura {duration:.0f} segundos."""


def slugify_tag(keyword):
    """'Antigua Roma' -> 'antiguaroma'. Determinista y sin acentos."""
    plano = unicodedata.normalize("NFKD", str(keyword))
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", plano.lower())


def hashtags_for(keywords, platform):
    """Hashtags de una plataforma. Los construye el código, no el modelo.

    Un modelo devuelve almohadillas, mayúsculas y espacios de forma distinta
    cada vez; una función pura no.
    """
    reglas = PLATFORM_RULES[platform]
    minimo, maximo = reglas["tags"]
    vistos, etiquetas = set(), []
    for forzada in reglas["forced"]:
        etiquetas.append(f"#{forzada}")
        vistos.add(forzada.lower())
    for palabra in keywords:
        slug = slugify_tag(palabra)
        if not slug or slug in vistos or len(slug) < 3:
            continue
        vistos.add(slug)
        etiquetas.append(f"#{slug}")
        if len(etiquetas) >= maximo:
            break
    return etiquetas[:maximo] if len(etiquetas) >= minimo else etiquetas


def clean_title(title, limit):
    """Título válido para la API: sin < >, sin exceder el límite."""
    limpio = ILLEGAL_TITLE.sub("", str(title)).strip()
    if len(limpio) <= limit:
        return limpio
    recortado = limpio[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return recortado or limpio[:limit]


def normalize(raw, story, duration, lang):
    """Convierte la respuesta del modelo en metadatos listos para publicar."""
    keywords = [k for k in (raw.get("keywords") or []) if str(k).strip()]
    reglas = PLATFORM_RULES["youtube"]
    titulo = clean_title(raw.get("youtube_title") or story.get("title", story["id"]),
                         reglas["title_max"])

    return {
        "key": key_for(story, lang),
        "story_id": story["id"],
        "lang": lang,
        "format": story["format"],
        "duration_s": round(float(duration), 2),
        "keywords": keywords,
        "youtube": {
            "title": titulo,
            "description": ILLEGAL_TITLE.sub("", raw.get("youtube_description", "")).strip(),
            "hashtags": hashtags_for(keywords, "youtube"),
        },
        "tiktok": {
            "caption": raw.get("tiktok_caption", "").strip(),
            "hashtags": hashtags_for(keywords, "tiktok"),
        },
        "instagram": {
            "caption": (raw.get("instagram_caption") or raw.get("tiktok_caption", "")).strip(),
            "hashtags": hashtags_for(keywords, "instagram"),
        },
    }


def key_for(story, lang):
    """Clave de los metadatos: cambian si cambia el guion o el idioma."""
    guion = " ".join(scene["text"] for scene in story["scenes"])
    blob = json.dumps([story["id"], guion, lang, META_V], sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def render_caption(meta, platform):
    """El texto EXACTO que se pega en la app, sin cabeceras ni etiquetas.

    Se pega tal cual desde el móvil: abrir, seleccionar todo, copiar. Cualquier
    rótulo dentro del archivo obligaría a seleccionar a mano en una pantalla
    táctil, que es justo donde se pierde el tiempo.
    """
    bloque = meta[platform]
    cuerpo = bloque.get("caption") or bloque.get("description", "")
    etiquetas = " ".join(bloque["hashtags"])
    texto = f"{cuerpo}\n\n{etiquetas}".strip()
    limite = PLATFORM_RULES[platform].get("caption_max")
    return texto[:limite] if limite else texto


def youtube_body(meta, privacy="private", publish_at=None, category="27"):
    """Cuerpo de videos.insert. `category`: 27 educación, 24 entretenimiento."""
    descripcion = render_caption(meta, "youtube")
    idioma = "en-US" if meta["lang"] == "en" else "es-MX"
    snippet = {
        "title": meta["youtube"]["title"],
        "description": descripcion,
        "tags": [t.lstrip("#") for t in meta["youtube"]["hashtags"]],
        "categoryId": category,
        "defaultLanguage": idioma,
        "defaultAudioLanguage": idioma,
    }
    status = {
        "privacyStatus": privacy,
        # explícito siempre: omitirlo deja el vídeo en un estado que YouTube
        # reclama después, y para divulgación histórica la respuesta correcta
        # (y la monetizable) es que no está dirigido a niños
        "selfDeclaredMadeForKids": False,
    }
    if publish_at:
        # programar exige privado + publishAt; YouTube lo hace público a esa hora
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at
    return {"snippet": snippet, "status": status}


def sha256_of(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(chunk), b""):
            digest.update(bloque)
    return digest.hexdigest()


def already_uploaded(sha256, story_id, ledger_lines):
    """Comprueba el libro mayor. Devuelve (motivo, entrada) o (None, None).

    Función pura sobre las líneas del ledger: se testea sin red ni disco.
    """
    for linea in ledger_lines:
        linea = linea.strip()
        if not linea:
            continue
        try:
            entrada = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if entrada.get("sha256") == sha256:
            return "mismo", entrada
    for linea in reversed(list(ledger_lines)):
        linea = linea.strip()
        if not linea:
            continue
        try:
            entrada = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if entrada.get("story_id") == story_id:
            return "rerender", entrada
    return None, None
