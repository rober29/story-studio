"""Errores del pipeline."""


class StudioError(Exception):
    """Fallo del pipeline con un mensaje que dice qué hacer al respecto.

    Se usa en lugar de assert (desaparecen con -O) y de sys.exit (no dejan
    rastro de la causa). El mensaje va dirigido a quien ejecuta el pipeline,
    no a quien lo programó: debe nombrar el archivo, la escena o el comando
    concreto que hay que revisar.
    """


class TransientError(StudioError):
    """Fallo del que tiene sentido reintentar: red, 5xx, 429, cuerpo vacío.

    La distinción importa con proveedores de pago: reintentar una respuesta
    correcta que simplemente no nos gusta (proporción rara, imagen ilegible)
    la vuelve a cobrar sin ninguna probabilidad de mejorar. Solo se reintenta
    lo que indica que el proveedor ni siquiera llegó a entregar la imagen.
    """
