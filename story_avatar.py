"""Genera los avatares y las portadas de los canales.

    venv/Scripts/python.exe story_avatar.py --lang es --quality final
    venv/Scripts/python.exe story_avatar.py --lang en --quality draft --count 4

Existe como script aparte y no como fase del pipeline porque una imagen de marca
no es un artefacto de una historia: no entra en ningún manifiesto, no tiene clave
de caché y se genera un puñado de veces en la vida del proyecto.

La portada se pide pasándole el avatar como REFERENCIA, no repitiendo el prompt:
así el personaje es literalmente el mismo y no uno parecido. Es el mismo
mecanismo que mantiene al narrador consistente entre escenas de un reel.
"""

import argparse
import os

from studio import providers
from studio.errors import StudioError
from studio.story import D_STYLE

# El mismo monigote que narra los reels en las dos series. Cambiar de personaje
# en el avatar lo desconectaría de sus propios vídeos, así que los canales se
# distinguen por color y objeto, que es lo único que se lee a tamaño de
# miniatura dentro de un círculo de sesenta píxeles.
NARRADOR = (
    "one stick figure man drawn with thick black marker lines, round white head, "
    "two dot eyes, wavy single-line mouth, small red bow tie"
)

# Nada de "portrait", "bust" ni "front-facing": medido el 2026-08-10, ese
# vocabulario arrastra al generador a retrato semirrealista y anula el D_STYLE
# por completo. Los prompts que funcionan describen un dibujo y una acción.
# A sesenta píxeles no se lee una expresión ni un objeto pequeño: se lee la
# SILUETA. Por eso cada canal viste al personaje en vez de limitarse a cambiarle
# el gesto —un salacot y un morrión recortan siluetas muy distintas— y por eso
# el disfraz se aplica también a la portada: si el avatar lleva casco y la
# portada no, dejan de parecer el mismo personaje.
#
# La pajarita roja se conserva en ambos: es el hilo con el narrador que sale
# dentro de los reels.
MARCAS = {
    "es": {
        "traje": (
            "wearing a shiny spanish conquistador morion helmet with a curved crest, "
            "and a small ruffled white collar over the red bow tie"
        ),
        "pose": (
            "holding a long brass spyglass up to one eye, and that eye is drawn "
            "comically huge and round through the glass while the other stays a "
            "small dot, leaning forward towards the viewer"
        ),
        "fondo": "plain flat warm ochre background",
        "portada": (
            "a wooden caravel sailing ship with square sails on the horizon, an open "
            "treasure chest spilling gold coins in the middle distance, a rolled "
            "nautical map and a compass on the ground"
        ),
        "objetos_banner": (
            "a small wooden caravel ship, an open chest of gold coins, a rolled "
            "nautical map and a compass"
        ),
    },
    "en": {
        "traje": (
            "wearing a beige explorer pith helmet and a small canvas satchel strap "
            "across the chest, over the red bow tie"
        ),
        "pose": (
            "kneeling and holding up a freshly dug gold coin with one hand while the "
            "other holds a small brush, looking at the coin with wide eyes"
        ),
        "fondo": "plain flat dusty teal background",
        "portada": (
            "an egyptian obelisk half buried in sand on the far left, an open "
            "excavation pit with pottery shards and a spilled bag of gold coins in "
            "the middle distance, stone ruins on the horizon"
        ),
        "objetos_banner": (
            "a small egyptian obelisk, a broken clay amphora, a spilled bag of gold "
            "coins and a stack of old books"
        ),
    },
}

SIN_TEXTO = "NO TEXT, no letters, no words, no watermark, no signature"


def prompt_avatar(lang):
    """Encuadre cerrado: las tres plataformas recortan el avatar en CÍRCULO.

    La primera versión salía de cuerpo entero y centrada, que se ve bien en el
    archivo y fatal donde importa: a sesenta píxeles de diámetro la cabeza
    quedaba minúscula y los pies y la mano se perdían fuera del círculo. Lo que
    tiene que llenar el cuadro es la cara.
    """
    marca = MARCAS[lang]
    return (
        f"big close-up drawing of the head and shoulders of {NARRADOR}, "
        f"{marca['traje']}, {marca['pose']}. The round white head is LARGE and "
        f"fills most of the square, centered. Flat {marca['fondo']}. Nothing "
        f"important near the corners. {SIN_TEXTO}"
    )


def prompt_portada(lang):
    """Composición pensada para cómo Facebook recorta la portada.

    En escritorio la foto de perfil se superpone abajo a la izquierda, y en móvil
    se recortan los laterales. Por eso el personaje va a la derecha y el tercio
    izquierdo queda despejado: lo que se ponga ahí lo tapa el avatar.
    """
    marca = MARCAS[lang]
    return (
        f"wide horizontal banner. {NARRADOR}, {marca['traje']}, standing full body "
        f"on the RIGHT THIRD of the image and gesturing towards the left. "
        f"{marca['portada']}. The LEFT THIRD is empty {marca['fondo']} with nothing "
        f"important in it. {SIN_TEXTO}"
    )


def prompt_banner(lang):
    """Banner de canal de YouTube, que recorta mucho más que Facebook.

    YouTube sube 2560x1440 pero la ZONA SEGURA —lo único que se ve en móvil y en
    televisión— es una franja central de 1546x423, o sea el 60 % del ancho y el
    29 % del alto. Todo lo que importe tiene que caber ahí; el resto es sangrado
    que se pierde en la mayoría de pantallas.

    Por eso el personaje va CENTRADO y no a un lado como en la portada de
    Facebook, donde el problema opuesto era dejar sitio a la foto de perfil.
    """
    marca = MARCAS[lang]
    return (
        f"wide horizontal banner, {marca['fondo']}. All the drawing is confined to "
        f"a NARROW HORIZONTAL BAND across the exact middle of the image: "
        f"{NARRADOR}, {marca['traje']}, standing in the CENTER, with {marca['objetos_banner']} "
        f"arranged close beside it on the left and on the right, all at the same "
        f"height. The top third and the bottom third are completely empty flat "
        f"background. Wide empty margins on the far left and far right edges. "
        f"{SIN_TEXTO}"
    )


def generar(provider, prompt, ancho, alto, seed, destino, refs=()):
    datos, meta = provider.fetch(prompt, D_STYLE, ancho, alto, seed, refs=refs)
    with open(destino + ".part", "wb") as f:
        f.write(datos)
    os.replace(destino + ".part", destino)
    coste = meta.get("cost_usd", 0.0)
    print(f"  {destino}  ({len(datos) // 1024} KB, {coste:.4f} $)")
    return datos, meta


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--lang", choices=sorted(MARCAS), default="es")
    parser.add_argument("--quality", default="draft", choices=("draft", "final"),
                        help="draft usa el generador gratuito; final, Gemini")
    parser.add_argument("--count", type=int, default=1, help="variantes a generar")
    parser.add_argument("--solo", choices=("avatar", "portada", "banner"),
                        help="genera solo una pieza; por defecto avatar y portada")
    parser.add_argument("--out", default="storage/brand")
    args = parser.parse_args()

    if args.quality == "final":
        provider = providers.GeminiImages()
        # 4 imágenes en 2K salen por medio dólar; avisar antes es la misma
        # cortesía que el pipeline tiene con las imágenes de las historias
        print(f"proveedor de pago: ~{provider.cost_usd():.3f} $ por imagen")
    else:
        provider = providers.PollinationsImages()

    os.makedirs(args.out, exist_ok=True)
    # el banner es opt-in: sin él, un --lang es sin más regeneraría (y pisaría)
    # las piezas que ya estén aprobadas y subidas
    quiere = {args.solo} if args.solo else {"avatar", "portada"}
    quiere_avatar = "avatar" in quiere
    quiere_portada = "portada" in quiere

    total = 0.0
    for i in range(args.count):
        seed = 1000 + i
        sufijo = f"-{seed}" if args.count > 1 else ""
        avatar_path = os.path.join(args.out, f"avatar-{args.lang}{sufijo}.jpg")

        avatar_bytes = None
        if quiere_avatar:
            avatar_bytes, meta = generar(
                provider, prompt_avatar(args.lang), 1024, 1024, seed, avatar_path
            )
            total += meta.get("cost_usd", 0.0)

        # portada y banner heredan el personaje del avatar en vez de
        # redescribirlo: así es el mismo monigote y no uno parecido
        anchas = [
            ("portada", prompt_portada, "portada" in quiere),
            ("banner", prompt_banner, "banner" in quiere),
        ]
        refs = ()
        if any(pedida for _, _, pedida in anchas) and provider.uses_refs:
            if avatar_bytes is None and os.path.isfile(avatar_path):
                with open(avatar_path, "rb") as f:
                    avatar_bytes = f.read()
            if avatar_bytes is None:
                raise StudioError(
                    f"falta {avatar_path} para usarlo de referencia.\n"
                    f"  Genera antes el avatar con --solo avatar"
                )
            refs = ((avatar_bytes, "image/jpeg"),)

        for nombre, constructor, pedida in anchas:
            if not pedida:
                continue
            _, meta = generar(
                provider, constructor(args.lang), 1920, 1080, seed,
                os.path.join(args.out, f"{nombre}-{args.lang}{sufijo}.jpg"), refs=refs
            )
            total += meta.get("cost_usd", 0.0)

    if total:
        print(f"\ntotal: {total:.4f} $")


if __name__ == "__main__":
    try:
        main()
    except StudioError as e:
        import sys

        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)
