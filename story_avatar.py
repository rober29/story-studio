"""Genera avatares de perfil con el estilo de monigotes del formato D.

Existe como script aparte y no como fase del pipeline porque un avatar no es
un artefacto de una historia: no entra en ningún manifiesto, no tiene clave de
caché y se genera dos veces en la vida del proyecto. Usa siempre el proveedor
gratuito — pagar por un avatar de 200x200 no compra nada.

El encuadre importa: TikTok, YouTube e Instagram recortan la foto de perfil en
un CIRCULO, así que el sujeto va centrado y con aire alrededor. Lo que toque
las esquinas se pierde.
"""

import argparse
import os

from studio.providers import PollinationsImages
from studio.story import D_STYLE

# Nada de "portrait", "bust" ni "front-facing": medido el 2026-08-10, ese
# vocabulario arrastra al generador a retrato semirrealista y anula el D_STYLE
# por completo (salieron cuatro caras humanas pintadas al oleo). Los prompts que
# funcionan en formato D son simples y de accion, describiendo un dibujo.
NARRADOR = (
    "one stick figure man drawn with thick black marker lines, round white head, "
    "two dot eyes, wavy single-line mouth, small red bow tie"
)

PROMPTS = {
    "es": (
        f"{NARRADOR}, standing and waving, holding a rolled parchment scroll, "
        f"whole figure small and centered on a plain flat warm ochre background, "
        f"wide empty margin all around, NO TEXT, no letters, no words"
    ),
    "en": (
        f"{NARRADOR}, standing and waving, holding a small magnifying glass, "
        f"whole figure small and centered on a plain flat dusty teal background, "
        f"wide empty margin all around, NO TEXT, no letters, no words"
    ),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", choices=sorted(PROMPTS), default="es")
    parser.add_argument("--count", type=int, default=4, help="variantes a generar")
    parser.add_argument("--out", default="storage/brand/avatars")
    args = parser.parse_args()

    provider = PollinationsImages()
    os.makedirs(args.out, exist_ok=True)

    for i in range(args.count):
        seed = 1000 + i
        data, _ = provider.fetch(PROMPTS[args.lang], D_STYLE, 1024, 1024, seed)
        path = os.path.join(args.out, f"avatar-{args.lang}-{seed}.jpg")
        # escritura atómica, misma convención que el resto del pipeline
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
        print(f"  {path}  ({len(data) // 1024} KB)")


if __name__ == "__main__":
    main()
