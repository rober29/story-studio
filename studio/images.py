"""Validación y escritura de las imágenes generadas.

Este módulo ya no habla con ningún proveedor concreto: recibe bytes y los
valida antes de tocar el disco. Quién los produce está en studio/providers.py.

La separación no es cosmética. Antes, la validación vivía dentro del bucle de
reintentos, así que una respuesta correcta que no pasaba la validación se
volvía a pedir tres veces. Con un proveedor gratuito eso solo perdía tiempo;
con uno de pago, triplicaba la factura sin ninguna posibilidad de mejorar.
"""

import io
import time

from studio.cache import write_atomic
from studio.errors import StudioError, TransientError

MIN_BYTES = 10_000
MIN_SHORT_SIDE = 400
ASPECT_TOLERANCE = 0.03
BACKOFF = (6, 12, 24)

# firma -> extensión real del archivo
MAGIC = ((b"\xff\xd8\xff", ".jpg"), (b"\x89PNG\r\n\x1a\n", ".png"))


def extension_for(data):
    """Extensión que corresponde al contenido, no la que nos gustaría."""
    for signature, suffix in MAGIC:
        if data.startswith(signature):
            return suffix
    return None


def check_transport(data, content_type):
    """Comprueba que el proveedor entregó algo que parece una imagen.

    Lo que se mira aquí es señal de que la petición salió mal (página de error
    HTML, cuerpo vacío), no de que la imagen no nos convenga: por eso lanza
    TransientError y sí se reintenta.
    """
    if not data or len(data) < MIN_BYTES:
        raise TransientError(f"respuesta demasiado pequeña ({len(data or b'')} bytes)")
    if content_type and not content_type.lower().startswith("image/"):
        snippet = data[:120].decode("utf-8", "replace").replace("\n", " ")
        raise TransientError(f"el proveedor devolvió {content_type!r} en vez de una imagen: {snippet}")
    if extension_for(data) is None:
        raise TransientError("el contenido no es un JPEG ni un PNG")


def validate(data, width, height):
    """Comprueba que la imagen sirve. Devuelve su tamaño real.

    Un fallo aquí NO se reintenta: el proveedor entregó una imagen (y si es de
    pago, ya la cobró); pedir la misma otra vez no la va a arreglar.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            size = img.size
    except (UnidentifiedImageError, OSError) as e:
        raise StudioError(f"imagen ilegible: {e}")

    # Los generadores suelen limitar el lado largo y devolver una imagen más
    # pequeña de la pedida conservando la proporción. Eso se tolera (se amplía
    # al montar). Lo que no, es un cambio de proporción: estiraría la imagen.
    wanted = width / height
    got = size[0] / size[1]
    if abs(got - wanted) / wanted > ASPECT_TOLERANCE:
        raise StudioError(
            f"el proveedor devolvió {size[0]}x{size[1]} (proporción {got:.3f}) "
            f"en vez de la proporción {wanted:.3f} pedida"
        )
    if min(size) < MIN_SHORT_SIDE:
        raise StudioError(f"la imagen es demasiado pequeña: {size[0]}x{size[1]}")
    return size


def save_image(data, out_path_without_ext, width, height):
    """Valida y escribe de forma atómica. Devuelve (ruta real, bytes, tamaño).

    La extensión sale del contenido: Pollinations devuelve JPEG y Gemini PNG, y
    un .jpg que en realidad es PNG deja archivos que la limpieza no encuentra.
    """
    size = validate(data, width, height)
    path = out_path_without_ext + extension_for(data)
    write_atomic(path, data)
    return path, len(data), size


def with_retries(call, retries=3, backoff=BACKOFF, label="la operación"):
    """Reintenta solo lo transitorio. Cualquier otro error sube a la primera."""
    last = None
    for attempt in range(retries):
        try:
            return call()
        except TransientError as e:
            last = e
            if attempt < retries - 1:
                delay = backoff[min(attempt, len(backoff) - 1)]
                print(f"    intento {attempt + 1}/{retries} falló ({e}); reintento en {delay}s")
                time.sleep(delay)
    raise StudioError(f"{label} falló tras {retries} intentos: {last}")
