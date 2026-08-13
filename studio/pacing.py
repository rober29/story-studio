"""Ritmo de narración: cuántas palabras hacen falta para durar N segundos.

Importa porque TikTok solo remunera vídeos de 60 segundos o más, y la duración
del reel es exactamente la del audio: no se puede alargar en el montaje (el
pipeline aborta si el vídeo y la voz difieren más de 0,25 s). El único control
es escribir suficiente texto.

Solo stdlib: se puede testear sin red ni TTS.
"""

from studio.errors import StudioError

# Medido cruzando stories/*.json con storage/tasks/*/verify/report.json.
# 'fast' es el PEOR CASO PARA LA DURACIÓN: si el TTS corre rápido, el reel sale
# corto. Por eso el suelo se garantiza contra 'fast' y no contra la mediana.
PACE = {
    # 7 reels medidos: 2,153 / 2,201 / 2,246 / 2,477 / 2,480 / 2,482 / 2,483
    "es": {"slow": 2.15, "median": 2.477, "fast": 2.50, "samples": 7},
    # Tres muestras, y discrepan MUCHO más que las siete españolas:
    #   odd-history-3        175 palabras / 59,33 s = 2,950
    #   mansa-musa-mali-en   212 palabras / 86,09 s = 2,463
    #   guerra-asiento-en    214 palabras / 82,94 s = 2,580
    #
    # Un 20 % de diferencia con la misma voz y la misma velocidad. La lección es
    # que palabras/s no es una constante del idioma: depende del TEXTO. El
    # segundo guion lleva 'pilgrimage', 'destabilized', 'catastrophic',
    # 'inexhaustible' —palabras largas que tardan más aunque cuenten como una— y
    # más comas, que son pausas.
    #
    # Consecuencia práctica: como target_words apunta a 'fast' para garantizar
    # el suelo, un guion inglés de palabra larga sale LARGO (86 s pidiendo 68).
    # Se acepta a propósito: pasarse cuesta retención, quedarse corto cuesta la
    # monetización entera. Pero conviene saber que el inglés no es predecible
    # como el español, y medir con story_pace.py --probe antes de dar por buena
    # una duración.
    "en": {"slow": 2.40, "median": 2.58, "fast": 3.05, "samples": 3},
}

# Voz por defecto de cada idioma, manteniendo el género para que una serie
# traducida se lea como el mismo canal. Nombres de docs/voice-list.txt.
DEFAULT_VOICES = {
    "es": {"F": "es-MX-DaliaNeural", "M": "es-MX-JorgeNeural"},
    "en": {"F": "en-US-AriaNeural", "M": "en-US-AndrewNeural"},
}

# Mínimo de TikTok para el programa de recompensas, más colchón.
TIKTOK_FLOOR = 60
RECOMMENDED_FLOOR = 65


def pace_for(lang):
    if lang not in PACE:
        raise StudioError(
            f"idioma {lang!r} sin ritmo calibrado; disponibles: {', '.join(sorted(PACE))}"
        )
    return PACE[lang]


def target_words(floor_seconds, lang="es"):
    """Palabras necesarias para no bajar de floor_seconds ni en el peor caso."""
    if floor_seconds <= 0:
        raise StudioError(f"la duración objetivo debe ser positiva, recibido {floor_seconds}")
    return int(floor_seconds * pace_for(lang)["fast"]) + 1


def estimate_duration(words, lang="es"):
    """Banda (mínimo, máximo) de segundos que durarán esas palabras."""
    ritmo = pace_for(lang)
    return words / ritmo["fast"], words / ritmo["slow"]


def measure(words, seconds):
    """Palabras por segundo de un reel ya generado."""
    if seconds <= 0:
        raise StudioError("duración inválida al medir el ritmo")
    return words / seconds


def check_script(words, lang, floor_seconds, source="<story>"):
    """Falla si el guion no garantiza el suelo de duración.

    Lanza StudioError en vez de devolver un booleano para encajar en el bucle
    de reintento de story_writer, que reinyecta el mensaje del error en el
    prompt del siguiente intento.
    """
    if not floor_seconds:
        return
    necesarias = target_words(floor_seconds, lang)
    if words >= necesarias:
        return
    faltan = necesarias - words
    minimo, maximo = estimate_duration(words, lang)
    raise StudioError(
        f"{source}: el guion tiene {words} palabras y duraría entre "
        f"{minimo:.0f} y {maximo:.0f} s, por debajo del suelo de {floor_seconds} s. "
        f"Faltan unas {faltan} palabras: alarga las escenas más cortas"
    )


def describe(lang):
    """Línea informativa sobre la calibración de un idioma."""
    ritmo = pace_for(lang)
    fuente = f"{ritmo['samples']} reels medidos" if ritmo["samples"] else "SIN MEDIR (provisional)"
    return (
        f"{lang}: {ritmo['slow']:.2f}-{ritmo['fast']:.2f} palabras/s "
        f"(mediana {ritmo['median']:.2f}) — {fuente}"
    )
