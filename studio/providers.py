"""Proveedores de imagen y de voz.

Son pocos y no se espera que crezcan mucho, así que esto es un diccionario y
un par de clases: nada de registro de plugins ni carga dinámica.

Cada proveedor declara un `cache_tag()` que entra en la clave de caché. Eso es
lo que hace que cambiar de borrador a final regenere lo que toca y, sobre todo,
que los archivos de ambos modos puedan convivir sin pisarse.
"""

import urllib.error
import urllib.parse
import urllib.request

from studio.errors import StudioError, TransientError
from studio.images import check_transport

USER_AGENT = "Mozilla/5.0"


# --------------------------------------------------------------------------
# Imagen
# --------------------------------------------------------------------------

class ImageProvider:
    name = ""
    model = ""
    tier = None
    uses_refs = False
    paid = False

    def cache_tag(self):
        return (self.name, self.model, self.tier)

    def cost_usd(self):
        """Coste estimado por imagen, para avisar antes de gastar."""
        return 0.0

    def fetch(self, prompt, style, width, height, seed, refs=()):
        """Devuelve (bytes, meta). No valida a fondo ni toca disco."""
        raise NotImplementedError

    def describe(self):
        tier = f" {self.tier}" if self.tier else ""
        return f"{self.name}:{self.model}{tier}"


class PollinationsImages(ImageProvider):
    name = "pollinations"
    model = "flux"
    tier = None
    uses_refs = False
    paid = False

    ENDPOINT = "https://image.pollinations.ai/prompt/"

    def url(self, prompt, style, width, height, seed):
        # safe="" es deliberado: con el valor por defecto ('/') una barra
        # dentro del prompt se cuela en la ruta y rompe la petición
        quoted = urllib.parse.quote(f"{prompt}, {style}", safe="")
        query = urllib.parse.urlencode(
            {
                "width": width,
                "height": height,
                "model": self.model,
                "nologo": "true",
                "seed": seed,
            }
        )
        return f"{self.ENDPOINT}{quoted}?{query}"

    def fetch(self, prompt, style, width, height, seed, refs=(), timeout=180):
        request = urllib.request.Request(
            self.url(prompt, style, width, height, seed),
            headers={"User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
                content_type = response.headers.get("Content-Type", "")
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise TransientError(f"no hubo respuesta del generador: {e}")
        check_transport(data, content_type)
        return data, {"cost_usd": 0.0, "model": self.model}


class GeminiImages(ImageProvider):
    """Gemini 3.1 Flash Image: 9:16 nativo y consistencia de personaje.

    Medido el 2026-08-09: el tier 1K entrega 768x1376, que sigue por debajo de
    los 1080 del video y habría que ampliarlo igual que con el generador
    gratuito. El 2K entrega 1536x2752 y se reduce a 1080, que es el mejor caso.
    Por eso 2K es el valor por defecto: pagar 1K no compra nitidez.
    """

    name = "gemini"
    model = "gemini-3.1-flash-image"
    tier = "2k"
    uses_refs = True
    paid = True

    # coste real medido por imagen, para estimar antes de gastar; el importe
    # que se registra sale siempre del usageMetadata de cada respuesta
    COSTS = {"1k": 0.095, "2k": 0.127, "4k": 0.24}

    def cost_usd(self):
        return self.COSTS.get((self.tier or "2k").lower(), 0.127)

    def aspect_ratio(self, width, height):
        """La API pide una proporción con nombre, no píxeles arbitrarios."""
        ratio = width / height
        for name, value in (("9:16", 9 / 16), ("16:9", 16 / 9), ("1:1", 1.0)):
            if abs(ratio - value) / value < 0.02:
                return name
        raise StudioError(
            f"Gemini no admite la proporción {width}x{height}; "
            f"usa 9:16, 16:9 o 1:1"
        )

    def fetch(self, prompt, style, width, height, seed, refs=()):
        # import perezoso: el camino gratuito no carga nada de Gemini
        from studio import gemini

        return gemini.generate_image(
            f"{prompt}, {style}",
            self.aspect_ratio(width, height),
            image_size=(self.tier or "2k").upper(),
            refs=refs,
            model=self.model,
        )


IMAGE_PROVIDERS = {
    "pollinations": PollinationsImages,
    "gemini": GeminiImages,
}


def image_provider_for(story, quality):
    """Proveedor de imagen según la calidad pedida."""
    if quality == "draft":
        return PollinationsImages()

    name = story.get("image_provider") or "gemini"
    factory = IMAGE_PROVIDERS.get(name)
    if factory is None:
        disponibles = ", ".join(sorted(IMAGE_PROVIDERS))
        raise StudioError(
            f"el proveedor de imagen {name!r} no está disponible todavía "
            f"(disponibles: {disponibles}). Usa --quality draft"
        )
    provider = factory()
    tier = story.get("image_tier")
    if tier:
        provider.tier = tier
    return provider


# --------------------------------------------------------------------------
# Voz
# --------------------------------------------------------------------------

class VoiceProvider:
    name = ""
    paid = False

    def cache_tag(self):
        return (self.name,)

    def cost_usd(self, text):
        return 0.0

    def synthesize(self, text, voice, rate, out_path):
        """Escribe el audio y devuelve las marcas por palabra, en segundos.

        La lista es el contrato del que cuelga todo el timing del pipeline:
        [{"word": str, "start": float, "end": float}, ...] en orden.
        """
        raise NotImplementedError


class EdgeVoice(VoiceProvider):
    """Edge TTS: gratuito y, sobre todo, entrega marcas por palabra exactas."""

    name = "edge"
    paid = False

    def synthesize(self, text, voice, rate, out_path):
        # import perezoso: el resto del paquete no depende de app.*
        from app.services import voice as voice_service

        sub_maker = voice_service.tts(
            text=text,
            voice_name=voice_service.parse_voice_name(voice),
            voice_rate=rate,
            voice_file=out_path,
        )
        if sub_maker is None or not getattr(sub_maker, "subs", None):
            raise StudioError(
                f"el TTS no devolvió marcas de palabra para la voz {voice!r}. "
                f"Comprueba que la voz existe y que hay conexión"
            )
        words = []
        for word, offset in zip(sub_maker.subs, sub_maker.offset):
            start, end = offset  # ticks de 100 ns
            words.append(
                {"word": word.strip(), "start": start / 1e7, "end": end / 1e7}
            )
        return words


VOICE_PROVIDERS = {
    "edge": EdgeVoice,
}


def voice_provider_for(voice_name):
    """El prefijo de la voz elige proveedor: 'gemini:Kore' -> gemini."""
    prefix = voice_name.split(":", 1)[0] if ":" in voice_name else "edge"
    factory = VOICE_PROVIDERS.get(prefix)
    if factory is None:
        disponibles = ", ".join(sorted(VOICE_PROVIDERS))
        raise StudioError(
            f"el proveedor de voz {prefix!r} no está disponible todavía "
            f"(disponibles: {disponibles})"
        )
    return factory()
