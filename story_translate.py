"""Traduce una historia reutilizando las imágenes ya pagadas.

    venv/Scripts/python.exe story_translate.py --story stories/mansa-musa-mali-es.json

La versión traducida cuesta CERO en imágenes: la clave de caché incluye el
prompt (que va en inglés y no cambia) pero no el texto narrado, así que la
historia hija adopta por enlace duro las imágenes de la madre declarando
'images_from'. Solo se paga la llamada de texto, que son céntimos.

Lo que NO se puede tocar, porque entra en image_key y valdría 0,89 $ moverlo:
los 'prompt', el 'style', las descripciones de 'characters', el formato y el
número y orden de las escenas. Por eso los prompts NO forman parte del esquema
de respuesta: no es una instrucción que el modelo pueda desobedecer, es que no
tiene dónde escribirlos.
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from studio import backlog, gemini, pacing
from studio.cache import write_json
from studio.errors import StudioError
from studio.story import merge_series, resolve_prompts, validate_story

STORIES_DIR = os.path.join(ROOT, "stories")
BACKLOG_PATH = os.path.join(STORIES_DIR, "backlog.json")

IDIOMAS = {"es": "español", "en": "inglés"}

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    # obligatorio para poder comprobar que vuelven N escenas EN
                    # ORDEN antes de escribir nada: si el modelo se salta una,
                    # el texto acabaría bajo la imagen equivocada
                    "index": {"type": "integer"},
                    "text": {"type": "string"},
                    "chapter": {"type": "string"},
                },
                "required": ["index", "text"],
            },
        },
    },
    "required": ["title", "scenes"],
}


def destino_de(ruta, origen_lang, destino_lang):
    """stories/x-es.json -> stories/x-en.json.

    Si el archivo no lleva sufijo de idioma —las historias anteriores a esta
    convención— se añade, en vez de fallar.
    """
    carpeta, archivo = os.path.split(os.path.abspath(ruta))
    base = os.path.splitext(archivo)[0]
    if base.endswith(f"-{origen_lang}"):
        base = base[: -len(origen_lang) - 1]
    return os.path.join(carpeta, f"{base}-{destino_lang}.json")


def build_prompt(story, lang_code, palabras, palabras_max, error=None):
    idioma = IDIOMAS.get(lang_code, lang_code)
    escenas = "\n".join(
        f'{i}. ({len(s["text"].split())} palabras) {s["text"]}'
        for i, s in enumerate(story["scenes"])
    )
    por_escena = max(6, (palabras + palabras_max) // 2 // len(story["scenes"]))

    prompt = f"""Adapta este guion de reel vertical al {idioma}.

TÍTULO ORIGINAL: {story.get('title', '')}

ESCENAS ORIGINALES:
{escenas}

REGLAS DURAS:

1. Devuelve EXACTAMENTE {len(story['scenes'])} escenas, con 'index' de 0 a
   {len(story['scenes']) - 1} y en ese orden. Cada una cuenta lo mismo que su
   original: van sobre una imagen fija que ya existe y no se puede cambiar.
2. El total debe sumar entre {palabras} y {palabras_max} palabras
   (~{por_escena} por escena), y NUNCA menos de {palabras}.

   Esto es lo que más importa y lo que más se falla: NO es una traducción
   literal. El {idioma} narra más rápido que el español, así que traducir
   palabra por palabra da un guion MÁS CORTO en segundos, y por debajo del
   mínimo de sesenta segundos TikTok no lo remunera. Necesitas MÁS palabras que
   el original, no las mismas: desarrolla, añade un detalle concreto, no
   rellenes con adjetivos.
3. Escribe los NÚMEROS CON LETRA ("thirteen twenty-four", no "1324"): el
   recuento del guion tiene que coincidir con el del audio.
4. Reparte el texto de forma EQUILIBRADA entre escenas.
5. Nombres propios, fechas y cifras se conservan exactos. Adaptar no es
   inventar.
6. 'title' es el título del reel en {idioma}, no una traducción literal si
   suena forzada.
"""
    if error:
        prompt += f"\nEl intento anterior fue rechazado:\n{error}\nCorrígelo.\n"
    return prompt


def componer(original, traducido, story_id, origen_id, serie, min_duration):
    """Monta la historia hija conservando byte a byte lo que entra en caché."""
    hija = {"id": story_id}
    if serie:
        hija["series"] = serie
    else:
        hija["format"] = original.get("format", "D")
    # la línea que hace que las imágenes salgan gratis
    hija["images_from"] = origen_id
    hija["title"] = traducido.get("title") or original.get("title", story_id)
    if min_duration:
        hija["min_duration"] = min_duration

    # style y characters se copian TAL CUAL: ya están en inglés y son
    # literalmente parte de la clave de caché de cada imagen
    for campo in ("style", "characters", "voice_rate", "target_shot"):
        if campo in original:
            hija[campo] = original[campo]

    por_indice = {int(s["index"]): s for s in traducido["scenes"] if "index" in s}
    escenas = []
    for i, escena in enumerate(original["scenes"]):
        nueva = {}
        if escena.get("chapter"):
            nueva["chapter"] = por_indice[i].get("chapter") or escena["chapter"]
        nueva["text"] = por_indice[i]["text"].strip()
        # el prompt viene del ORIGINAL, nunca de la respuesta del modelo
        nueva["prompt"] = escena["prompt"]
        escenas.append(nueva)
    hija["scenes"] = escenas
    return hija


def comprobar_indices(traducido, esperadas):
    escenas = traducido.get("scenes") or []
    if len(escenas) != esperadas:
        raise StudioError(
            f"se pidieron {esperadas} escenas y llegaron {len(escenas)}"
        )
    indices = sorted(int(s["index"]) for s in escenas if "index" in s)
    if indices != list(range(esperadas)):
        raise StudioError(
            f"los 'index' no cubren 0..{esperadas - 1} sin repetir: {indices}"
        )


def anotar_backlog(origen_id, nuevo_id):
    """Deja constancia de que el tema ya tiene versión traducida.

    No consume una entrada nueva: el backlog cuenta TEMAS, y la traducción es el
    mismo tema en otro idioma. Solo se añade el id a la lista de historias.
    """
    if not os.path.isfile(BACKLOG_PATH):
        return None
    data = backlog.load(BACKLOG_PATH)
    for tema in data["temas"]:
        if origen_id in (tema.get("historias") or []):
            if nuevo_id not in tema["historias"]:
                tema["historias"].append(nuevo_id)
                backlog.save(BACKLOG_PATH, data)
                return tema["slug"]
            return tema["slug"]
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--story", required=True, help="historia original")
    parser.add_argument("--out", help="ruta de salida; por defecto <base>-<lang>.json")
    parser.add_argument("--lang", default="en", choices=sorted(IDIOMAS))
    parser.add_argument("--from-lang", default="es", choices=sorted(IDIOMAS))
    parser.add_argument("--series", default="hidden-history",
                        help="serie del idioma destino")
    parser.add_argument("--seconds", type=int,
                        help="duración mínima; por defecto la del original")
    parser.add_argument("--model", default="gemini-3.6-flash")
    parser.add_argument("--force", action="store_true", help="sobrescribir si existe")
    args = parser.parse_args()

    ruta = os.path.abspath(args.story)
    # se lee el JSON CRUDO, no load_story: lo que provee la serie no debe
    # hornearse dentro del archivo hijo
    with open(ruta, encoding="utf-8") as f:
        original = json.load(f)

    salida = os.path.abspath(args.out) if args.out else destino_de(
        ruta, args.from_lang, args.lang
    )
    if os.path.exists(salida) and not args.force:
        raise StudioError(f"{salida} ya existe; usa --force si quieres reemplazarlo")

    story_id = os.path.splitext(os.path.basename(salida))[0]
    if story_id == original["id"]:
        raise StudioError(
            f"el destino tendría el mismo id que el original ({story_id}); "
            f"renombra el archivo de origen con sufijo de idioma"
        )

    # la serie se comprueba antes de llamar al modelo: fallo rápido y gratis
    serie = None
    if args.series:
        from studio.story import load_series

        serie = load_series(args.series, os.path.dirname(salida), salida)
        if serie.get("format") == "B":
            raise StudioError(
                "el formato B no se puede traducir reutilizando imágenes: sus "
                "prompts llevan dentro el texto narrado, así que la versión en "
                "otro idioma necesita imágenes nuevas de todas formas"
            )

    seconds = args.seconds or original.get("min_duration") or (serie or {}).get(
        "min_duration"
    ) or pacing.RECOMMENDED_FLOOR
    palabras = pacing.target_words(seconds, args.lang)
    palabras_max = int(palabras * 1.12)
    print(
        f"{original['id']} -> {story_id} | objetivo {palabras}-{palabras_max} "
        f"palabras para {seconds}s en {IDIOMAS[args.lang]}"
    )

    error = None
    for intento in (1, 2):
        print(f"traduciendo (intento {intento})...")
        traducido, usage = gemini.generate_json(
            build_prompt(original, args.lang, palabras, palabras_max, error),
            SCHEMA, model=args.model,
        )
        try:
            comprobar_indices(traducido, len(original["scenes"]))
            hija = componer(original, traducido, story_id, original["id"],
                            args.series, seconds)
            candidato = json.loads(json.dumps(hija))
            if serie:
                candidato = merge_series(candidato, serie)
            comprobada = validate_story(candidato, source=salida)
            resolve_prompts(comprobada, source=salida)
            pacing.check_script(
                sum(len(s["text"].split()) for s in comprobada["scenes"]),
                args.lang, seconds, source=salida,
            )
            break
        except (StudioError, KeyError, ValueError) as e:
            print(f"  rechazado: {e}")
            error = str(e)
            if intento == 2:
                raise StudioError(f"la traducción no salió válida: {e}")

    write_json(salida, hija)
    total = sum(len(s["text"].split()) for s in hija["scenes"])
    print(f"\nescrito: {salida}")
    print(f"{len(hija['scenes'])} escenas, {total} palabras "
          f"(~{total / pacing.PACE[args.lang]['median']:.0f}s)")

    slug = anotar_backlog(original["id"], story_id)
    if slug:
        print(f"anotado en el backlog como version de {slug!r}")

    print(f"tokens usados: {usage.get('totalTokenCount', '?')}")
    print("\nComprueba que las imagenes se adopten y no se paguen:")
    print(f"  venv\\Scripts\\python.exe story_studio.py --story {salida} "
          f"--phase plan --quality final")


if __name__ == "__main__":
    try:
        main()
    except StudioError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)
