"""Transporte de la API de Gemini: clave, petición, errores y precios.

Todo el trato con Google pasa por aquí. REST plano con urllib en vez del SDK:
el SDK instalado (google-generativeai 0.8.3) es de la generación anterior y no
genera imágenes, y el nuevo arrastraría dependencias que chocan con el pin que
usa app/services/llm.py. La petición es un POST con JSON.
"""

import base64
import json
import os
import urllib.error
import urllib.request

from studio.errors import StudioError, TransientError

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"

# $ por millón de tokens (entrada, salida). De la tabla de precios de Google.
PRICES = {
    "gemini-3.1-flash-image": (0.50, 60.0),
    "gemini-3.1-flash-lite-image": (0.25, 30.0),
    "gemini-3-pro-image": (2.00, 120.0),
    "gemini-2.5-flash-image": (0.30, 30.0),
    "gemini-3.1-flash-tts": (0.50, 10.0),
    "gemini-2.5-flash-preview-tts": (0.50, 10.0),
}

_key_cache = None


def api_key():
    """Clave desde el entorno o desde config.toml, en ese orden."""
    global _key_cache
    if _key_cache:
        return _key_cache

    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        try:
            # import perezoso: el resto del paquete no depende de app.*
            from app.config import config

            key = (config.app.get("gemini_api_key") or "").strip()
        except Exception:
            key = ""
    if not key:
        raise StudioError(
            "falta la clave de Gemini. Ponla en la variable de entorno "
            "GEMINI_API_KEY o como gemini_api_key en config.toml "
            "(https://aistudio.google.com/apikey)"
        )
    _key_cache = key
    return key


def redact(text):
    """Quita la clave de cualquier texto que vaya a imprimirse."""
    try:
        key = api_key()
    except StudioError:
        return str(text)
    return str(text).replace(key, "***")


def cost_of(model, usage):
    """Coste real de una llamada a partir de los tokens que informa la API."""
    prices = PRICES.get(model)
    if not prices or not usage:
        return 0.0
    entrada, salida = prices
    prompt_tokens = usage.get("promptTokenCount", 0) or 0
    output_tokens = usage.get("candidatesTokenCount", 0) or 0
    return (prompt_tokens * entrada + output_tokens * salida) / 1e6


def post(model, payload, timeout=180):
    """POST a generateContent. Devuelve el JSON ya parseado.

    La clave viaja en la cabecera y NUNCA en la URL: este pipeline imprime
    excepciones y comandos, y en el query string acabaría en consola.
    """
    request = urllib.request.Request(
        f"{ENDPOINT}/{model}:generateContent",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key(),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = redact(e.read().decode("utf-8", "replace")[:400])
        if e.code in (429, 500, 502, 503, 504):
            raise TransientError(f"Gemini respondió {e.code}: {body}")
        raise StudioError(f"Gemini rechazó la petición ({e.code}): {body}")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise TransientError(f"no hubo respuesta de Gemini: {redact(e)}")


def _first_inline(response, kind):
    """Extrae la primera parte binaria de la respuesta."""
    for candidate in response.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("mimeType", "").startswith(kind):
                return base64.b64decode(inline["data"]), inline["mimeType"]
    finish = ""
    for candidate in response.get("candidates", []):
        finish = candidate.get("finishReason") or finish
    raise TransientError(
        f"la respuesta no traía {kind}"
        + (f" (finishReason: {finish})" if finish else "")
    )


def generate_image(prompt, aspect_ratio, image_size=None, refs=(),
                   model="gemini-3.1-flash-image"):
    """Genera una imagen. `refs` son bytes de imágenes de referencia."""
    parts = []
    for data, mime in refs:
        parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}})
    if refs:
        parts.append(
            {
                "text": "Use the reference images for the characters' appearance. "
                        "Keep face, hair and clothing identical. Scene: " + prompt
            }
        )
    else:
        parts.append({"text": prompt})

    image_config = {"aspectRatio": aspect_ratio}
    if image_size:
        image_config["imageSize"] = image_size

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": image_config,
        },
    }
    response = post(model, payload)
    data, mime = _first_inline(response, "image/")
    usage = response.get("usageMetadata", {})
    return data, {
        "cost_usd": cost_of(model, usage),
        "model": model,
        "mime": mime,
        "usage": usage,
    }


def synthesize(text, voice="Kore", model="gemini-3.1-flash-tts"):
    """Convierte texto en audio. Devuelve PCM crudo y sus metadatos."""
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
            },
        },
    }
    response = post(model, payload)
    data, mime = _first_inline(response, "audio/")
    usage = response.get("usageMetadata", {})
    return data, {
        "cost_usd": cost_of(model, usage),
        "model": model,
        "mime": mime,
        "usage": usage,
        "raw_parts": response.get("candidates", [{}])[0].get("content", {}).get("parts", []),
    }


def generate_json(prompt, schema, model="gemini-3.6-flash", temperature=0.9):
    """Pide una respuesta que cumpla un esquema JSON.

    Con responseSchema la API devuelve JSON directamente, así que no hace falta
    rescatar el objeto de entre vallas de markdown como en app/services/llm.py.
    """
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "temperature": temperature,
        },
    }
    response = post(model, payload)
    parts = response.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts)
    if not text.strip():
        finish = response.get("candidates", [{}])[0].get("finishReason", "?")
        raise TransientError(f"el modelo no devolvió texto (finishReason: {finish})")
    try:
        return json.loads(text), response.get("usageMetadata", {})
    except json.JSONDecodeError as e:
        raise TransientError(f"la respuesta no era JSON válido: {e}")


def list_models():
    """Modelos visibles para esta clave. Es una llamada gratuita."""
    request = urllib.request.Request(
        ENDPOINT, headers={"x-goog-api-key": api_key()}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    return [m["name"].split("/")[-1] for m in data.get("models", [])]
