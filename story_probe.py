"""Sondeo barato de la API de Gemini antes de gastar en un reel entero.

Responde tres preguntas con dinero real pero poco:
  1. ¿Qué resolución devuelve de verdad cada tier? (hay un issue abierto donde
     el modelo ignora imageSize y siempre entrega 1K)
  2. ¿Funciona la consistencia de personaje con imágenes de referencia?
  3. ¿El TTS devuelve marcas de palabra o hará falta alinear a posteriori?

Escribe todo en el scratchpad: no toca storage/ ni ningún manifiesto.

    venv/Scripts/python.exe story_probe.py --out <carpeta>
"""

import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from studio import gemini
from studio.errors import StudioError

STYLE = "cinematic, photorealistic, dramatic lighting, highly detailed"
CHARACTER = (
    "a 49 year old bulgarian writer with thick dark mustache, receding dark "
    "hair, wearing a brown tweed coat over a beige sweater and a dark red scarf"
)


def save(data, path):
    with open(path, "wb") as f:
        f.write(data)
    return path


def dims(data):
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        return img.size


def probe_resolution(out_dir, results):
    print("\n=== 1. Resolución real por tier ===")
    casos = [
        ("9:16", "1K", "vertical-1k"),
        ("9:16", "2K", "vertical-2k"),
        ("16:9", "1K", "apaisado-1k"),
    ]
    for aspect, size, name in casos:
        prompt = f"a lone lighthouse on a cliff at sunrise, {STYLE}"
        try:
            data, meta = gemini.generate_image(prompt, aspect, image_size=size)
        except StudioError as e:
            print(f"  {name}: FALLO -> {e}")
            results.append({"prueba": name, "error": str(e)})
            continue
        width, height = dims(data)
        path = save(data, os.path.join(out_dir, f"{name}.png"))
        print(
            f"  {name}: pedido {aspect} {size} -> recibido {width}x{height}"
            f"  ({len(data) // 1024} KB, {meta['mime']}, {meta['cost_usd']:.4f} $)"
        )
        results.append(
            {
                "prueba": name,
                "pedido": f"{aspect} {size}",
                "recibido": [width, height],
                "coste_usd": meta["cost_usd"],
                "tokens": meta["usage"],
                "archivo": path,
            }
        )


def probe_character(out_dir, results):
    print("\n=== 2. Consistencia de personaje ===")
    sheet_prompt = (
        f"character reference sheet of {CHARACTER}. Full body front view on the "
        f"left and head-and-shoulders close-up on the right, plain neutral grey "
        f"background, even flat lighting, no scenery. {STYLE}"
    )
    try:
        sheet, meta = gemini.generate_image(sheet_prompt, "16:9", image_size="1K")
    except StudioError as e:
        print(f"  hoja de personaje: FALLO -> {e}")
        results.append({"prueba": "hoja-personaje", "error": str(e)})
        return
    save(sheet, os.path.join(out_dir, "personaje-hoja.png"))
    print(f"  hoja de personaje: {dims(sheet)[0]}x{dims(sheet)[1]}  ({meta['cost_usd']:.4f} $)")
    results.append({"prueba": "hoja-personaje", "coste_usd": meta["cost_usd"]})

    escenas = [
        ("escena-a", "waiting at a rainy london bus stop, red double decker bus behind"),
        ("escena-b", "lying ill in a dim hospital bed at night, worried doctor nearby"),
    ]
    for name, escena in escenas:
        try:
            data, meta = gemini.generate_image(
                f"{escena}, {STYLE}", "9:16", image_size="1K",
                refs=[(sheet, "image/png")],
            )
        except StudioError as e:
            print(f"  {name}: FALLO -> {e}")
            results.append({"prueba": name, "error": str(e)})
            continue
        save(data, os.path.join(out_dir, f"personaje-{name}.png"))
        print(f"  {name}: {dims(data)[0]}x{dims(data)[1]}  ({meta['cost_usd']:.4f} $)")
        results.append({"prueba": name, "coste_usd": meta["cost_usd"]})
    print("  -> compara personaje-hoja.png con personaje-escena-*.png: ¿es la misma persona?")


def probe_tts(out_dir, results):
    print("\n=== 3. Voz: ¿trae marcas de palabra? ===")
    disponibles = [m for m in gemini.list_models() if "tts" in m]
    print(f"  modelos de voz visibles: {', '.join(disponibles) or 'ninguno'}")
    if not disponibles:
        results.append({"prueba": "tts", "error": "sin modelos de voz"})
        return
    modelo = "gemini-3.1-flash-tts" if "gemini-3.1-flash-tts" in disponibles else disponibles[0]

    frase = "Londres, mil novecientos setenta y ocho. Todo comenzó con un paraguas."
    try:
        pcm, meta = gemini.synthesize(frase, model=modelo)
    except StudioError as e:
        print(f"  FALLO -> {e}")
        results.append({"prueba": "tts", "error": str(e)})
        return

    save(pcm, os.path.join(out_dir, "voz.pcm"))
    # ¿alguna parte de la respuesta trae tiempos?
    con_tiempos = any(
        clave in json.dumps(meta["raw_parts"])[:4000]
        for clave in ("timepoint", "timeOffset", "wordTiming", "startOffset")
    )
    print(f"  {modelo}: {len(pcm) // 1024} KB de {meta['mime']}  ({meta['cost_usd']:.4f} $)")
    print(f"  marcas de palabra en la respuesta: {'SÍ' if con_tiempos else 'NO'}")
    if not con_tiempos:
        print("  -> hará falta alinear con faster-whisper contra el guion")
    results.append(
        {
            "prueba": "tts",
            "modelo": modelo,
            "mime": meta["mime"],
            "marcas_de_palabra": con_tiempos,
            "coste_usd": meta["cost_usd"],
        }
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="carpeta donde dejar las pruebas")
    parser.add_argument("--skip", default="", help="pruebas a omitir: resolucion,personaje,tts")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    omitir = {s.strip() for s in args.skip.split(",") if s.strip()}
    results = []

    if "resolucion" not in omitir:
        probe_resolution(args.out, results)
    if "personaje" not in omitir:
        probe_character(args.out, results)
    if "tts" not in omitir:
        probe_tts(args.out, results)

    total = sum(r.get("coste_usd", 0.0) for r in results)
    with open(os.path.join(args.out, "probe.json"), "w", encoding="utf-8") as f:
        json.dump({"resultados": results, "coste_total_usd": total}, f,
                  ensure_ascii=False, indent=1)
    print(f"\n=== coste real total: {total:.4f} $ ===")
    print(f"resultados en {args.out}")


if __name__ == "__main__":
    try:
        main()
    except StudioError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)
