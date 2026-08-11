"""Escribe el JSON de una historia a partir de un tema.

    venv/Scripts/python.exe story_writer.py --topic "el paraguas búlgaro" \
        --format B --scenes 7 --duration 45 --out stories/markov2.json

Genera el guion, los prompts de imagen, los capítulos y las fichas de personaje,
y lo valida con el mismo código que usa el pipeline: si el resultado no pasa la
validación, no se escribe. Deliberadamente NO genera el video: revisa y edita el
archivo, y luego lánzalo con story_studio.py.

Usa un modelo de texto de Gemini, que tiene tier gratuito.
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from studio import gemini, pacing
from studio.errors import StudioError
from studio.story import (
    FORMAT_STYLE,
    load_series,
    merge_series,
    resolve_prompts,
    validate_story,
)

# El ritmo vive en studio/pacing.py, con las constantes medidas sobre los
# reels reales y la comprobación que impide guiones demasiado cortos.

FORMAT_HINTS = {
    "A": (
        "Formato A: caricatura con banner de serie. El estilo debe describir "
        "dibujo de monigotes ('simple stick figure cartoon, thick black "
        "outlines, flat bright colors, white background'). Tono con humor. "
        "Incluye 'banner' con el nombre de la serie y 'part' con 'Pt. 1'."
    ),
    "B": (
        "Formato B: una imagen por cada par de segundos, texto grande encima. "
        "Funciona mejor con narrativa de suspense y un personaje recurrente: "
        "define ese personaje en 'characters' y úsalo con {nombre} en los "
        "prompts de las escenas donde aparezca."
    ),
    "C": (
        "Formato C: documental cinematográfico. Cada escena lleva 'chapter', "
        "un título corto que empieza por un emoji, y varias escenas seguidas "
        "pueden compartir el mismo capítulo para agruparlas."
    ),
    "D": (
        "Formato D: caricatura de monigotes dentro de una franja 16:9 sobre "
        "fondo borroso, con banner de serie encima y subtítulos dentro de la "
        "franja.\n"
        "NO propongas 'style': el pipeline aplica el suyo (monigotes a "
        "rotulador sobre fondo pintado) y cualquier estilo que escribas lo "
        "anularía.\n"
        "IMPORTANTE sobre los 'prompt': de la ÚNICA imagen de cada escena se "
        "recortan TRES O CUATRO encuadres distintos (general, medio, primer "
        "plano). Por eso cada prompt debe describir una escena ANCHA con "
        "varios elementos repartidos —protagonista al centro, algo a la "
        "izquierda, algo a la derecha, actividad al fondo—. Un primer plano "
        "cerrado no da de sí para cuatro encuadres y los cortes parecerán "
        "repetidos.\n"
        "Usa un personaje recurrente definido en 'characters' y refiérelo con "
        "{nombre} en todas las escenas donde aparezca. Tono divulgativo con "
        "humor seco."
    ),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "style": {"type": "string"},
        "banner": {"type": "string"},
        "part": {"type": "string"},
        # lista y no diccionario: el esquema de Gemini no admite claves libres
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["name", "description"],
            },
        },
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chapter": {"type": "string"},
                    "text": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["text", "prompt"],
            },
        },
    },
    "required": ["title", "style", "scenes"],
}


def build_prompt(topic, fmt, scenes, duration, lang, extra_error=None, lang_code="es"):
    # objetivo contra el ritmo MÁS RÁPIDO observado: si el TTS corre rápido y el
    # guion iba justo, el reel se queda por debajo del mínimo de TikTok
    words = pacing.target_words(duration, lang_code)
    per_scene = max(6, words // scenes)
    prompt = f"""Eres guionista de reels verticales de divulgación en {lang}.
Escribe una historia sobre: {topic}

REGLAS DURAS (el pipeline las valida y rechaza el resultado si no se cumplen):

1. Exactamente {scenes} escenas.
2. El conjunto de los campos 'text' debe sumar unas {words} palabras
   (~{per_scene} por escena). Reparte el texto de forma EQUILIBRADA: si una
   escena queda mucho más corta que las demás, el pipeline aborta.
3. Los 'text' van en {lang} y son lo que se narra en voz alta. Escribe los
   NÚMEROS CON LETRA ("mil quinientos veintiuno", no "1521"): el conteo de
   palabras del guion tiene que coincidir con el del audio.
4. Los 'prompt' van SIEMPRE EN INGLÉS y describen lo que se ve, no lo que se
   dice. Concretos y visuales: sujeto, acción, entorno, luz. Sin texto ni
   letras dentro de la imagen.
5. 'style' es un sufijo en inglés que se añade a todos los prompts y unifica la
   estética. Descríbelo con precisión (medio, iluminación, paleta).
6. Si hay un personaje que aparece en varias escenas, añádelo a la lista
   'characters' con un 'name' de una sola palabra en minúsculas y sin llaves, y
   una 'description' FÍSICA muy específica en inglés (edad, pelo, rasgos,
   prendas CON COLOR). Refiérete a él como {{name}} dentro de los prompts. Solo
   puedes usar {{name}} de personajes que hayas definido en la lista.

ESTRUCTURA NARRATIVA: la primera escena es el gancho (una afirmación que
sorprenda o una pregunta), las intermedias desarrollan cronológicamente, y la
última cierra con la consecuencia o el dato que se recuerda.

{FORMAT_HINTS[fmt]}
"""
    if extra_error:
        prompt += (
            f"\nEl intento anterior fue rechazado por el validador con este "
            f"error:\n{extra_error}\nCorrígelo.\n"
        )
    return prompt


def compose(data, story_id, fmt, voice, series=None, part=None, min_duration=0):
    """Monta el JSON final en el orden en que se lee cómodamente.

    Cuando la historia pertenece a una serie, se omite todo lo que la serie ya
    provee: repetirlo aquí congelaría valores que deberían poder cambiarse en
    un solo sitio.
    """
    story = {"id": story_id}
    if series:
        story["series"] = series
    else:
        story["format"] = fmt
    if part is not None:
        story["part"] = part
    story["title"] = data.get("title", story_id)
    if voice and not series:
        story["voice"] = voice
    if min_duration:
        story["min_duration"] = min_duration

    # Los formatos con estilo propio (D) NO deben llevar 'style': validate_story
    # solo aplica FORMAT_STYLE cuando el campo no viene, así que escribirlo aquí
    # anularía el estilo del formato y el reel saldría con la estética genérica.
    if fmt not in FORMAT_STYLE and not series:
        story["style"] = data["style"]

    if fmt in ("A", "D") and not series:
        story["banner"] = data.get("banner", data.get("title", ""))

    personajes = {
        c["name"].strip("{}"): c["description"]
        for c in data.get("characters") or []
        if c.get("name") and c.get("description")
    }
    if personajes and not series:
        story["characters"] = personajes

    scenes = []
    for scene in data["scenes"]:
        item = {}
        if fmt == "C" and scene.get("chapter"):
            item["chapter"] = scene["chapter"]
        item["text"] = scene["text"].strip()
        item["prompt"] = scene["prompt"].strip()
        scenes.append(item)
    story["scenes"] = scenes
    return story


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--topic", required=True, help="tema de la historia")
    parser.add_argument("--out", required=True, help="ruta del JSON a escribir")
    parser.add_argument("--format", default="D", choices=("A", "B", "C", "D"))
    parser.add_argument("--scenes", type=int, default=7,
                        help="7 escenas es el óptimo del formato D")
    parser.add_argument("--seconds", type=int, default=68,
                        help="duración MÍNIMA objetivo; 65+ para monetizar en TikTok")
    parser.add_argument("--duration", type=int, help="alias de --seconds")
    parser.add_argument("--lang", default="español")
    parser.add_argument("--voice", default="", help="voz de Edge TTS a fijar")
    parser.add_argument("--series", default="", help="serie a la que pertenece")
    parser.add_argument("--part", type=int, help="número de parte dentro de la serie")
    parser.add_argument("--model", default="gemini-3.6-flash")
    parser.add_argument("--force", action="store_true", help="sobrescribir si existe")
    args = parser.parse_args()

    seconds = args.duration or args.seconds
    lang_code = "en" if args.lang.lower().startswith(("en", "ing")) else "es"

    if os.path.exists(args.out) and not args.force:
        raise StudioError(f"{args.out} ya existe; usa --force si quieres reemplazarlo")
    story_id = os.path.splitext(os.path.basename(args.out))[0]

    # la serie se comprueba ANTES de llamar al modelo: fallo rápido y gratis
    fmt = args.format
    if args.series:
        serie = load_series(args.series, os.path.dirname(os.path.abspath(args.out)), args.out)
        fmt = serie.get("format", fmt)
        print(f"serie {args.series!r}: formato {fmt}, banner {serie.get('banner', '')!r}")

    error = None
    for intento in (1, 2):
        print(f"escribiendo la historia (intento {intento})...")
        data, usage = gemini.generate_json(
            build_prompt(args.topic, fmt, args.scenes, seconds, args.lang, error,
                         lang_code=lang_code),
            SCHEMA,
            model=args.model,
        )
        story = compose(data, story_id, fmt, args.voice,
                        series=args.series, part=args.part, min_duration=seconds)
        try:
            # el validador del pipeline es el contrato: si pasa aquí, el
            # generador de video no lo va a rechazar después
            candidato = json.loads(json.dumps(story))
            if args.series:
                candidato = merge_series(candidato, serie)
            checked = validate_story(candidato, source=args.out)
            resolve_prompts(checked, source=args.out)
            # y el guion tiene que dar para la duración pedida, o TikTok no paga
            pacing.check_script(
                sum(len(s["text"].split()) for s in checked["scenes"]),
                lang_code, seconds, source=args.out,
            )
            break
        except StudioError as e:
            print(f"  rechazado: {e}")
            error = str(e)
            if intento == 2:
                raise StudioError(
                    f"el modelo no consiguió producir una historia válida: {e}"
                )

    palabras = sum(len(s["text"].split()) for s in story["scenes"])
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(story, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"\n{json.dumps(story, ensure_ascii=False, indent=2)}\n")
    print(f"escrito: {args.out}")
    print(
        f"{len(story['scenes'])} escenas, {palabras} palabras "
        f"(~{palabras / pacing.PACE['es']['median']:.0f}s de narración)"
    )
    print(f"tokens usados: {usage.get('totalTokenCount', '?')} (tier gratuito)")
    print("\nRevisa y edita el archivo, y cuando te convenza:")
    print(f"  venv\\Scripts\\python.exe story_studio.py --story {args.out} --phase plan")


if __name__ == "__main__":
    try:
        main()
    except StudioError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)
