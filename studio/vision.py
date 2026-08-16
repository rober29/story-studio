"""Comprueba sobre la IMAGEN lo que el prompt no puede garantizar.

NO_TEXT es una petición al generador, no un contrato: puede ignorarla y lo hace.
Medido el 2026-08-16 sobre cosas-raras-3, generado antes de que existiera la
prohibición: cuatro de cinco imágenes traían texto horneado en inglés —'HAIL!',
'SPQR', 'I COMMAND YOU TO STOP, SEA!'— y a simple vista solo se había detectado
una esquina.

Por eso esta comprobación mira el resultado y no la instrucción. Es la única de
todo el pipeline que necesita un modelo para verificar, así que:

- Avisa, nunca aborta. Un falso positivo no debe frenar una producción, y un
  fallo de red tampoco.
- Temperatura 0. Una verificación que cambia de opinión entre dos ejecuciones
  idénticas no sirve.
- Corre justo después de generar las imágenes, que es cuando regenerar una
  cuesta 0,13 $ en vez de rehacer el reel entero.
"""

from studio.errors import StudioError, TransientError

PROMPT = (
    "Look at this illustration. Does it contain any legible letters, words or "
    "numbers rendered as text anywhere in the image — on signs, labels, boxes, "
    "banners, papers or anywhere else? Decorative squiggles that only imitate "
    "handwriting without forming readable characters do NOT count."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "has_text": {"type": "boolean"},
        "found": {"type": "string"},
    },
    "required": ["has_text", "found"],
}


def find_text(data, model="gemini-3.6-flash"):
    """(hay_texto, qué_dice, coste). Devuelve (False, '', 0.0) si no se pudo mirar.

    Tragarse el error es deliberado: esto es un aviso, y que la red falle no
    puede tumbar una fase que ya ha pagado sus imágenes.
    """
    from studio import gemini

    try:
        respuesta, usage = gemini.inspect_image(data, PROMPT, SCHEMA, model=model)
    except (StudioError, TransientError, OSError):
        return False, "", 0.0
    return (
        bool(respuesta.get("has_text")),
        (respuesta.get("found") or "").strip(),
        usage.get("cost_usd", 0.0),
    )


def scan(rutas, model="gemini-3.6-flash"):
    """Revisa varias imágenes. Devuelve [(ruta, qué_dice)] solo de las que fallan."""
    encontrados = []
    for ruta in rutas:
        try:
            with open(ruta, "rb") as f:
                datos = f.read()
        except OSError:
            continue
        hay, dice, _ = find_text(datos, model=model)
        if hay:
            encontrados.append((ruta, dice))
    return encontrados
