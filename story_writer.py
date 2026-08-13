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

from studio import backlog, gemini, pacing
from studio.cache import write_json
from studio.errors import StudioError
from studio.story import (
    FORMAT_STYLE,
    load_series,
    merge_series,
    resolve_prompts,
    validate_story,
)

STORIES_DIR = os.path.join(ROOT, "stories")
BACKLOG_PATH = os.path.join(STORIES_DIR, "backlog.json")

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
        "Y AMBICIÓN, que es lo que más falla: cada escena es un LUGAR CON GENTE "
        "HACIENDO COSAS, no dos personajes y unos objetos. Pide escala cuando el "
        "tema la admita —multitudes, procesiones, mercados llenos, ejércitos, "
        "ciudades— y profundidad en tres planos: algo cerca, algo a media "
        "distancia y algo en el horizonte.\n"
        "Compara. ASÍ SÍ: 'massive desert caravan, thousands of attendants "
        "marching in fine silk on the left, camels carrying heavy chests on the "
        "right, vast dunes stretching to the horizon'. ASÍ NO: 'wooden crates "
        "stacked on the right and seagulls flying in the sky'. El segundo cumple "
        "el reparto izquierda-derecha-fondo y aun así da cuatro recortes "
        "aburridos, porque no hay nada que mirar.\n"
        "Usa un personaje recurrente definido en 'characters' y refiérelo con "
        "{nombre} en todas las escenas donde aparezca. Descríbelo RICO y "
        "específico: edad, complexión, porte, rasgos y prendas CON COLOR. Esa "
        "riqueza es deliberada — el estilo global es austero a propósito y la "
        "descripción del personaje es lo que lo compensa.\n"
        "Lo único prohibido en esa descripción es 'photorealistic', 'realistic', "
        "'3d render', 'cinematic' y 'detailed portrait': esas cinco palabras sí "
        "rompen el estilo, y el estilo global no basta para frenarlas.\n"
        "Tono divulgativo con humor seco."
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


# El arco de varias partes se pide ENTERO en una sola llamada. Generar la parte
# 2 sabiendo solo lo que dice la 1 hace que el arco se descosa: el modelo no
# puede reservar el giro final si no sabe todavía cuál es. Aquí planea el
# conjunto y luego lo corta.
SCHEMA_PARTES = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "style": {"type": "string"},
        "banner": {"type": "string"},
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
        "partes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
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
                "required": ["title", "scenes"],
            },
        },
    },
    "required": ["title", "partes"],
}


def build_prompt(topic, fmt, scenes, duration, lang, extra_error=None, lang_code="es",
                 partes=1):
    # objetivo contra el ritmo MÁS RÁPIDO observado: si el TTS corre rápido y el
    # guion iba justo, el reel se queda por debajo del mínimo de TikTok
    words = pacing.target_words(duration, lang_code)
    # Se le pide una BANDA por encima del mínimo, no la cifra exacta. Medido:
    # pidiendo "unas 171 palabras" el modelo entrega 167 y falla la comprobación
    # por cuatro palabras. Un 12 % de margen absorbe ese desvío, y pasarse no
    # cuesta nada —el reel dura un par de segundos más— mientras que quedarse
    # corto cuesta un reintento entero, o tres en un arco de tres partes.
    words_max = int(words * 1.12)
    words_mid = (words + words_max) // 2
    per_scene = max(6, words_mid // scenes)
    if partes > 1:
        cabecera = f"""Eres guionista de reels verticales de divulgación en {lang}.
Escribe una historia sobre: {topic}

La historia se cuenta en {partes} REELS CONSECUTIVOS. Devuélvela en 'partes',
una entrada por reel, en orden.

REGLAS DEL ARCO (además de las reglas duras de abajo, que se aplican a CADA
parte por separado):

A. Planea la historia completa ANTES de partirla. Cada parte cubre un tramo
   cronológico propio: la parte dos continúa donde acabó la uno, no la resume.
B. Todas las partes MENOS LA ÚLTIMA cierran en gancho: una pregunta abierta o
   un giro anunciado pero no revelado. Es lo único que hace que el espectador
   busque la siguiente.
C. La primera escena de las partes dos en adelante recuerda en media frase
   dónde nos quedamos, sin repetir lo ya contado.
D. Guarda la revelación más fuerte para la última parte.
"""
    else:
        cabecera = f"""Eres guionista de reels verticales de divulgación en {lang}.
Escribe una historia sobre: {topic}
"""

    prompt = f"""{cabecera}
REGLAS DURAS (el pipeline las valida y rechaza el resultado si no se cumplen):

1. Exactamente {scenes} escenas{" POR PARTE" if partes > 1 else ""}.
2. El conjunto de los campos 'text' debe sumar entre {words} y {words_max}
   palabras{" EN CADA PARTE" if partes > 1 else ""}, apuntando a {words_mid}
   (~{per_scene} por escena). NUNCA menos de {words}: el pipeline rechaza el
   guion que se quede corto, y pasarse un poco no cuesta nada. Reparte el texto
   de forma EQUILIBRADA: si una escena queda mucho más corta que las demás, el
   pipeline aborta.
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


def compose(data, story_id, fmt, voice, series=None, part=None, min_duration=0,
            series_characters=()):
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
    # Con serie se omiten los personajes que la serie YA define: repetirlos aquí
    # congelaría en la historia un valor que debe poder cambiarse en un solo
    # sitio, y romperia la reutilización de las fichas de referencia.
    #
    # Pero solo esos. Antes se descartaban TODOS, y como merge_series mezcla los
    # personajes clave a clave, una historia puede y debe poder añadir su reparto
    # propio. Descartarlo dejaba los {nombre} de esos personajes sin definir y el
    # validador rechazaba una historia correcta por un fallo que era nuestro.
    propios = {n: d for n, d in personajes.items() if n not in (series_characters or ())}
    if propios:
        story["characters"] = propios

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


def validar(story, serie, out, lang_code, seconds):
    """Somete la historia al MISMO validador que usa el pipeline.

    Si pasa aquí, el generador de vídeo no la va a rechazar después. La copia
    por json es deliberada: merge_series y resolve_prompts mutan, y no queremos
    que el archivo que se escribe lleve la serie horneada dentro.
    """
    candidato = json.loads(json.dumps(story))
    if serie:
        candidato = merge_series(candidato, serie)
    checked = validate_story(candidato, source=out)
    resolve_prompts(checked, source=out)
    # y el guion tiene que dar para la duración pedida, o TikTok no paga
    pacing.check_script(
        sum(len(s["text"].split()) for s in checked["scenes"]),
        lang_code, seconds, source=out,
    )


def escribir(story, ruta):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(story, f, ensure_ascii=False, indent=2)
        f.write("\n")


def registrar_partes(series_name, base_dir, ids):
    """Añade los ids a la lista 'parts' de la serie, en orden.

    Es lo que hace que merge_series numere Pt. 1..N automáticamente. Y encaja
    con la regla que ya seguimos: solo las historias genuinamente multiparte
    llevan número, porque solo ellas están en esta lista.
    """
    ruta = os.path.join(base_dir, "series", f"{series_name}.json")
    with open(ruta, encoding="utf-8") as f:
        serie = json.load(f)
    partes = serie.get("parts") or []
    serie["parts"] = partes + [i for i in ids if i not in partes]
    write_json(ruta, serie)
    return ruta


def resumen(story):
    palabras = sum(len(s["text"].split()) for s in story["scenes"])
    segundos = palabras / pacing.PACE["es"]["median"]
    return f"{len(story['scenes'])} escenas, {palabras} palabras (~{segundos:.0f}s)"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--topic", help="tema de la historia")
    parser.add_argument("--siguiente", action="store_true",
                        help="toma el primer tema pendiente de stories/backlog.json")
    parser.add_argument("--partes", type=int, default=1, metavar="N",
                        help="cuenta el tema en N reels consecutivos")
    parser.add_argument("--out", help="ruta del JSON a escribir")
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

    if not args.topic and not args.siguiente:
        parser.error('usa --topic "..." o --siguiente')
    if args.partes < 1:
        parser.error("--partes necesita un entero positivo")

    seconds = args.duration or args.seconds
    lang_code = "en" if args.lang.lower().startswith(("en", "ing")) else "es"

    # El tema puede venir del backlog. El registro NO se toca hasta que los
    # guiones estén escritos: un fallo del modelo no debe gastar una idea.
    data = tema = None
    topic, out = args.topic, args.out
    if args.siguiente:
        data = backlog.load(BACKLOG_PATH)
        tema = backlog.siguiente(data)
        # el gancho es lo que hace contable el tema: el título solo nombra al
        # sujeto ("Mansa Musa de Malí"), el gancho trae la historia
        topic = ". ".join(p for p in (tema["titulo"], tema.get("gancho")) if p)
        # el idioma va en el nombre desde el principio: la version traducida es
        # <slug>-en y se reconoce como pareja de un vistazo, en storage/ y en git
        out = out or os.path.join(STORIES_DIR, f"{tema['slug']}-{lang_code}.json")
        print(f"tema del backlog: {tema['titulo']}")
    if not out:
        parser.error("hace falta --out, o --siguiente para derivarlo del backlog")

    base_dir = os.path.dirname(os.path.abspath(out))
    story_id = os.path.splitext(os.path.basename(out))[0]
    rutas = (
        [out] if args.partes == 1
        else [os.path.join(base_dir, f"{story_id}-{n}.json")
              for n in range(1, args.partes + 1)]
    )
    for ruta in rutas:
        if os.path.exists(ruta) and not args.force:
            raise StudioError(f"{ruta} ya existe; usa --force si quieres reemplazarlo")

    # la serie se comprueba ANTES de llamar al modelo: fallo rápido y gratis
    fmt, serie = args.format, None
    if args.series:
        serie = load_series(args.series, base_dir, out)
        fmt = serie.get("format", fmt)
        print(f"serie {args.series!r}: formato {fmt}, banner {serie.get('banner', '')!r}")

    error = None
    for intento in (1, 2):
        cuantas = "la historia" if args.partes == 1 else f"el arco de {args.partes} partes"
        print(f"escribiendo {cuantas} (intento {intento})...")
        crudo, usage = gemini.generate_json(
            build_prompt(topic, fmt, args.scenes, seconds, args.lang, error,
                         lang_code=lang_code, partes=args.partes),
            SCHEMA if args.partes == 1 else SCHEMA_PARTES,
            model=args.model,
        )
        try:
            historias = []
            for i, ruta in enumerate(rutas):
                if args.partes == 1:
                    trozo, numero = crudo, args.part
                else:
                    partes = crudo.get("partes") or []
                    if len(partes) != args.partes:
                        raise StudioError(
                            f"se pidieron {args.partes} partes y llegaron {len(partes)}"
                        )
                    # lo compartido (estilo, banner, personajes) vive arriba; sin
                    # esto cada parte tendría personajes distintos y las fichas
                    # de referencia dejarían de reutilizarse entre reels
                    trozo = {
                        "title": partes[i].get("title") or crudo.get("title", ""),
                        "style": crudo.get("style", ""),
                        "banner": crudo.get("banner", ""),
                        "characters": crudo.get("characters") or [],
                        "scenes": partes[i].get("scenes") or [],
                    }
                    # con serie, el número lo deriva merge_series de la lista
                    # 'parts'; sin serie no hay lista, así que se fija aquí
                    numero = None if args.series else i + 1
                sub_id = os.path.splitext(os.path.basename(ruta))[0]
                historia = compose(trozo, sub_id, fmt, args.voice, series=args.series,
                                   part=numero, min_duration=seconds,
                                   series_characters=(serie or {}).get("characters") or ())
                validar(historia, serie, ruta, lang_code, seconds)
                historias.append(historia)
            break
        except StudioError as e:
            print(f"  rechazado: {e}")
            error = str(e)
            if intento == 2:
                raise StudioError(
                    f"el modelo no consiguió producir una historia válida: {e}"
                )

    # Todo validado: recién ahora se escribe. Un arco a medias es peor que
    # ningún arco, así que o se escriben todas las partes o ninguna.
    for historia, ruta in zip(historias, rutas):
        escribir(historia, ruta)
        print(f"escrito: {ruta}  ({resumen(historia)})")

    if args.partes > 1 and args.series:
        ruta_serie = registrar_partes(args.series, base_dir,
                                      [os.path.splitext(os.path.basename(r))[0] for r in rutas])
        print(f"registradas como partes de la serie en {ruta_serie}")

    if tema is not None:
        backlog.marcar_usado(
            data, tema["slug"],
            "corto" if args.partes == 1 else "extendido",
            [os.path.splitext(os.path.basename(r))[0] for r in rutas],
        )
        backlog.save(BACKLOG_PATH, data)
        print(f"tema {tema['slug']!r} marcado como usado en el backlog")

    print(f"tokens usados: {usage.get('totalTokenCount', '?')}")
    print("\nRevisa y edita el archivo, y cuando te convenza:")
    print(f"  venv\\Scripts\\python.exe story_studio.py --story {rutas[0]} --phase plan")


if __name__ == "__main__":
    try:
        main()
    except StudioError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)
