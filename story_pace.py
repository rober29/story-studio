"""Mide y comprueba el ritmo de narración de las historias.

    venv/Scripts/python.exe story_pace.py --measure
    venv/Scripts/python.exe story_pace.py --probe --story stories/faros.json

--measure recorre los reels ya generados y calcula sus palabras por segundo.
Sirve para recalibrar studio/pacing.py con datos reales; es de solo lectura.

--probe sintetiza el guion con Edge TTS en una carpeta temporal y reporta la
duración real. No toca storage/ ni ningún manifiesto, así que se puede iterar
un guion hasta que dure lo que quieres ANTES de generar una sola imagen.
Cuesta 0 $ y unos segundos.
"""

import argparse
import glob
import json
import os
import statistics
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from studio import ffmpeg, pacing, providers
from studio.errors import StudioError
from studio.story import load_story, script_text


def measure_all():
    """Palabras por segundo de cada reel ya generado. Solo lectura."""
    filas = []
    for ruta in sorted(glob.glob(os.path.join(ROOT, "stories", "*.json"))):
        nombre = os.path.splitext(os.path.basename(ruta))[0]
        if nombre.startswith("_"):
            continue
        informe = os.path.join(
            ROOT, "storage", "tasks", f"story-{nombre}", "verify", "report.json"
        )
        if not os.path.isfile(informe):
            continue
        try:
            story = load_story(ruta)
            with open(informe, encoding="utf-8") as f:
                datos = json.load(f)
            segundos = float(datos["metrics"]["voice_duration"])
        except (StudioError, KeyError, ValueError, OSError):
            continue
        palabras = len(script_text(story).split())
        filas.append(
            {
                "id": story["id"],
                "format": story["format"],
                "lang": story.get("lang", "es"),
                "words": palabras,
                "seconds": segundos,
                "wps": pacing.measure(palabras, segundos),
            }
        )
    return filas


def print_measure():
    filas = measure_all()
    if not filas:
        print("no hay reels verificados todavía; genera alguno con --phase all")
        return
    print(f"{'reel':18}{'fmt':5}{'idioma':8}{'palabras':>9}{'voz':>9}{'palabras/s':>12}")
    for fila in sorted(filas, key=lambda f: f["wps"]):
        print(
            f"{fila['id']:18}{fila['format']:5}{fila['lang']:8}"
            f"{fila['words']:9}{fila['seconds']:8.2f}s{fila['wps']:12.3f}"
        )

    print()
    for lang in sorted({f["lang"] for f in filas}):
        ritmos = [f["wps"] for f in filas if f["lang"] == lang]
        print(
            f"  {lang}: {len(ritmos)} reels | min {min(ritmos):.3f} | "
            f"mediana {statistics.median(ritmos):.3f} | max {max(ritmos):.3f}"
        )
        print(f"       calibración actual -> {pacing.describe(lang)}")
    print()
    print("Si el max medido supera el 'fast' de studio/pacing.py, súbelo:")
    print("es el valor con el que se calcula cuántas palabras hacen falta.")


def probe(story_path):
    """Sintetiza el guion y reporta la duración real. No toca storage/."""
    story = load_story(story_path)
    lang = story.get("lang", "es")
    texto = script_text(story)
    palabras = len(texto.split())
    suelo = story.get("min_duration") or 0

    minimo, maximo = pacing.estimate_duration(palabras, lang)
    print(f"historia: {story['id']} (formato {story['format']}, idioma {lang})")
    print(f"  {palabras} palabras -> estimado {minimo:.1f}-{maximo:.1f} s")
    print(f"  {pacing.describe(lang)}")

    ffmpeg.require_binaries()
    provider = providers.voice_provider_for(story["voice"])
    temporal = os.path.join(tempfile.mkdtemp(prefix="pace-"), "voz.mp3")
    print(f"  sintetizando con {story['voice']}...")
    palabras_marcadas = provider.synthesize(
        texto, story["voice"], story["voice_rate"], temporal
    )
    real = ffmpeg.media_duration(temporal)
    ritmo = pacing.measure(palabras, real)

    print()
    print(f"  DURACIÓN REAL: {real:.2f} s  ({ritmo:.3f} palabras/s, "
          f"{len(palabras_marcadas)} marcas de palabra)")
    if suelo:
        estado = "OK" if real >= suelo else "POR DEBAJO DEL SUELO"
        print(f"  suelo declarado: {suelo} s -> {estado}")
    if real < pacing.TIKTOK_FLOOR:
        faltan = pacing.target_words(pacing.RECOMMENDED_FLOOR, lang) - palabras
        print(
            f"  [aviso] por debajo de los {pacing.TIKTOK_FLOOR} s que exige TikTok "
            f"para monetizar. Añade unas {max(1, faltan)} palabras"
        )
    ritmo_actual = pacing.pace_for(lang)
    if ritmo > ritmo_actual["fast"]:
        print(
            f"  [aviso] este reel narra a {ritmo:.3f} palabras/s, por encima del "
            f"'fast' de {ritmo_actual['fast']:.2f} en studio/pacing.py. Súbelo o "
            f"los guiones futuros saldrán cortos"
        )
    if not ritmo_actual["samples"]:
        print(
            f"  >>> el idioma '{lang}' no estaba calibrado: escribe {ritmo:.2f} en "
            f"PACE['{lang}'] de studio/pacing.py y sube 'samples'"
        )
    print(f"\n  audio de prueba: {temporal}")
    return real


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--measure", action="store_true",
                        help="mide el ritmo de los reels ya generados")
    parser.add_argument("--probe", action="store_true",
                        help="sintetiza un guion y reporta su duración real")
    parser.add_argument("--story", help="ruta al JSON (obligatorio con --probe)")
    args = parser.parse_args()

    if args.probe:
        if not args.story:
            raise StudioError("--probe necesita --story")
        probe(args.story)
    elif args.measure:
        print_measure()
    else:
        parser.error("indica --measure o --probe")


if __name__ == "__main__":
    try:
        main()
    except StudioError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)
