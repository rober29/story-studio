"""Mapeo palabra -> escena y agrupación temporal. Solo stdlib: testeable en frío.

Todo el pipeline deriva sus tiempos de aquí. Las dos reglas que gobiernan el
módulo, y de las que dependen los defectos que arregla:

1. El reparto de palabras entre escenas se hace UNA sola vez
   (`word_scene_index`). Antes había dos algoritmos distintos —uno para los
   beats y otro para los spans— que discrepaban cuando el TTS devolvía un
   número de palabras distinto del declarado en el guion.
2. Los tramos son contiguos y cubren [0.0, audio_duration] por construcción.
   El inicio es 0.0 (no el de la primera palabra) y el final es la duración
   real del mp3 (no la de la última palabra), así que la pista visual no se
   adelanta ni se queda corta respecto a la voz.
"""

import math

from studio.errors import StudioError

# Cuánto puede desviarse la duración de la pista visual respecto a la de la voz.
# ASIMÉTRICO a propósito: un vídeo más CORTO trunca la narración, mientras que
# uno más largo solo congela el último fotograma un instante. Por eso apenas se
# tolera por abajo y con holgura por arriba.
#
# Viven aquí y no en pipeline.py o verify.py porque los DOS comprueban lo mismo
# —el montaje al terminar la fase de vídeo, y el MP4 final— y tenerlo duplicado
# ya causó un fallo: phase_visuals usaba ±0,25 simétrico y rechazaba un reel que
# verify habría dado por bueno.
DELTA_MIN = -0.05
DELTA_MAX = 0.60

# Cola que se añade al ÚLTIMO plano de un reel para caer siempre del lado
# seguro. ffmpeg cuantiza cada segmento a fotogramas enteros, así que la suma de
# veintiocho planos puede quedar unos milisegundos por debajo del audio; medido
# el 2026-08-14, un reel salió 53 ms corto y falló la comprobación por tres
# milésimas.
COLA_ULTIMO_PLANO = 0.15


def allocate_scene_words(counts, n_words):
    """Reparte n_words palabras reales entre escenas según sus pesos.

    Cuota de Hare (mayores restos): garantiza que la suma sea EXACTA, cosa que
    un round() independiente por escena no hace. Toda escena recibe >= 1.
    """
    n = len(counts)
    if n == 0:
        raise StudioError("no hay escenas que repartir")
    if n_words < n:
        raise StudioError(
            f"el TTS devolvió {n_words} palabras pero la historia tiene {n} escenas; "
            f"cada escena necesita al menos una palabra"
        )

    declared = sum(counts)
    if declared <= 0:
        exact = [n_words / n] * n
    else:
        exact = [c * n_words / declared for c in counts]

    alloc = [max(1, math.floor(e)) for e in exact]
    diff = n_words - sum(alloc)

    if diff > 0:
        order = sorted(range(n), key=lambda i: exact[i] - math.floor(exact[i]), reverse=True)
        k = 0
        while diff > 0:
            alloc[order[k % n]] += 1
            diff -= 1
            k += 1
    while diff < 0:
        for i in sorted(range(n), key=lambda i: alloc[i], reverse=True):
            if diff == 0:
                break
            if alloc[i] > 1:
                alloc[i] -= 1
                diff += 1

    return alloc


def word_scene_index(counts, n_words):
    """Escena a la que pertenece cada palabra. len() == n_words exacto."""
    index = []
    for scene, n in enumerate(allocate_scene_words(counts, n_words)):
        index.extend([scene] * n)
    return index


def _check_timings(timings, audio_duration):
    if not timings:
        raise StudioError("no hay timings de palabras; ¿falló el TTS?")
    if audio_duration <= 0:
        raise StudioError(f"duración de audio inválida: {audio_duration}")
    last = timings[-1]["end"]
    if audio_duration < last - 0.05:
        raise StudioError(
            f"el audio dura {audio_duration:.3f}s pero la última palabra termina en "
            f"{last:.3f}s; los timings no corresponden a este audio"
        )


def scene_spans(counts, timings, audio_duration):
    """Intervalo (inicio, fin) de cada escena, contiguos y sin huecos."""
    _check_timings(timings, audio_duration)
    index = word_scene_index(counts, len(timings))

    first = {}
    for i, scene in enumerate(index):
        first.setdefault(scene, i)

    bounds = [0.0]
    for scene in range(1, len(counts)):
        bounds.append(float(timings[first[scene]]["start"]))
    bounds.append(float(audio_duration))

    spans = list(zip(bounds[:-1], bounds[1:]))
    for i, (start, end) in enumerate(spans):
        if end - start <= 0.01:
            raise StudioError(
                f"la escena {i} quedaría con {end - start:.3f}s de duración; "
                f"revisa que su texto no sea desproporcionadamente corto"
            )
    return spans


def build_beats(
    counts,
    timings,
    audio_duration,
    max_images=45,
    min_beat_duration=1.1,
    max_beat_duration=2.6,
):
    """Agrupa palabras en beats por duración (formato B).

    Garantiza: los beats son contiguos y cubren [0, audio_duration], ninguna
    palabra se pierde ni se duplica, y len(beats) <= max_images.
    """
    _check_timings(timings, audio_duration)
    if max_images < 1:
        raise StudioError(f"'max_images' debe ser >= 1, recibido {max_images}")

    n = len(timings)
    scene_of = word_scene_index(counts, n)
    # el suelo real sube si max_images obliga a beats más largos
    floor_beat = max(min_beat_duration, audio_duration / max_images)

    groups = []
    current = []
    for i, word in enumerate(timings):
        if current:
            span = timings[current[-1]]["end"] - timings[current[0]]["start"]
            gap = word["start"] - timings[current[-1]]["end"]
            if (
                scene_of[i] != scene_of[current[0]]
                or span >= max_beat_duration
                or (gap > 0.25 and span >= floor_beat * 0.6)
            ):
                groups.append(current)
                current = []
        current.append(i)
        span = timings[current[-1]]["end"] - timings[current[0]]["start"]
        next_gap = timings[i + 1]["start"] - word["end"] if i + 1 < n else None
        if span >= floor_beat and (next_gap is None or next_gap > 0.15):
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    groups = _merge_stubs(groups, timings, audio_duration, scene_of, floor_beat)
    groups = _enforce_max(groups, timings, audio_duration, scene_of, max_images)

    starts, ends = _group_bounds(groups, timings, audio_duration)
    beats = []
    for g, start, end in zip(groups, starts, ends):
        beats.append(
            {
                "caption": " ".join(timings[i]["word"] for i in g).upper(),
                "start": start,
                "end": end,
                "scene": scene_of[g[0]],
                "words": len(g),
            }
        )
    return beats


def _group_bounds(groups, timings, audio_duration):
    """Tiempos contiguos: cada beat acaba donde empieza el siguiente."""
    starts = [0.0] + [float(timings[g[0]]["start"]) for g in groups[1:]]
    ends = starts[1:] + [float(audio_duration)]
    return starts, ends


def _merge_stubs(groups, timings, audio_duration, scene_of, floor_beat):
    """Absorbe los beats residuales demasiado cortos en su vecino."""
    if len(groups) < 2:
        return groups
    merged = [groups[0]]
    for g in groups[1:]:
        prev = merged[-1]
        span = timings[g[-1]]["end"] - timings[g[0]]["start"]
        if span < floor_beat * 0.5 and scene_of[g[0]] == scene_of[prev[0]]:
            merged[-1] = prev + g
        else:
            merged.append(g)
    return merged


def _enforce_max(groups, timings, audio_duration, scene_of, max_images):
    """Fusiona pares adyacentes hasta cumplir el tope de imágenes.

    Prefiere fusionar dentro de una misma escena; solo cruza escenas si no
    queda otra opción, porque mezclar contextos visuales se nota.
    """
    while len(groups) > max_images:
        starts, ends = _group_bounds(groups, timings, audio_duration)
        best = None
        for j in range(1, len(groups)):
            same_scene = scene_of[groups[j][0]] == scene_of[groups[j - 1][0]]
            combined = ends[j] - starts[j - 1]
            candidate = (0 if same_scene else 1, combined, j)
            if best is None or candidate < best:
                best = candidate
        j = best[2]
        groups[j - 1] = groups[j - 1] + groups[j]
        del groups[j]
    return groups


def _scene_cuts(start, end, n, word_indices, timings, min_shot):
    """Cortes interiores de una escena, en inicios de palabra.

    Devuelve n-1 tiempos, o menos si no caben respetando min_shot (en ese caso
    el llamador reduce n y reintenta).
    """
    while n > 1:
        ideales = [start + (end - start) * k / n for k in range(1, n)]
        cortes, usados, previo, ok = [], set(), start, True
        for ideal in ideales:
            mejor = None
            for wi in word_indices:
                momento = float(timings[wi]["start"])
                if wi in usados or momento - previo < min_shot or end - momento < min_shot:
                    continue
                if mejor is None or abs(momento - ideal) < abs(mejor[0] - ideal):
                    mejor = (momento, wi)
            if mejor is None:
                ok = False
                break
            cortes.append(mejor[0])
            usados.add(mejor[1])
            previo = mejor[0]
        if ok and len(cortes) == n - 1:
            return cortes
        n -= 1
    return []


def plan_shots(counts, timings, audio_duration, target_shot=2.4, min_shot=1.5,
               max_shots_per_scene=4):
    """Parte cada escena en planos de ~target_shot segundos (formato D).

    De cada imagen se sacan varios encuadres, así que el ritmo de corte deja de
    depender de cuántas imágenes se paguen. Los cortes caen en inicios de
    palabra para que no partan una a la mitad, y ningún plano cruza de escena
    porque se parte de scene_spans.

    Garantiza: planos contiguos que cubren [0, audio_duration], ninguno por
    debajo de min_shot (salvo que la escena entera dure menos), y entre 1 y
    max_shots_per_scene planos por escena.
    """
    _check_timings(timings, audio_duration)
    if max_shots_per_scene < 1:
        raise StudioError(f"'max_shots_per_scene' debe ser >= 1, recibido {max_shots_per_scene}")
    if target_shot <= 0 or min_shot <= 0:
        raise StudioError("'target_shot' y 'min_shot' deben ser positivos")

    spans = scene_spans(counts, timings, audio_duration)
    index = word_scene_index(counts, len(timings))
    palabras_de = {}
    for i, scene in enumerate(index):
        palabras_de.setdefault(scene, []).append(i)

    shots = []
    for scene, (start, end) in enumerate(spans):
        duracion = end - start
        # int(x + 0.5) y no round(): round() es bancario y da resultados
        # distintos en los empates, y esto entra en una clave de caché
        deseados = max(1, int(duracion / target_shot + 0.5))
        caben = max(1, int(duracion // min_shot))
        n = min(max_shots_per_scene, deseados, caben)

        cortes = _scene_cuts(start, end, n, palabras_de.get(scene, []), timings, min_shot)
        limites = [start] + cortes + [end]
        total = len(limites) - 1
        for k in range(total):
            shots.append(
                {
                    "scene": scene,
                    "index": k,
                    "of": total,
                    "start": limites[k],
                    "end": limites[k + 1],
                }
            )
    return shots


def build_cards(timings, audio_duration, max_chars=28, max_lines=2, sentence_gap=0.35):
    """Agrupa palabras en tarjetas de subtítulo (formatos A y C).

    Corta por presupuesto de caracteres y también en las pausas largas: las
    marcas del TTS no llevan puntuación, pero un silencio de más de
    `sentence_gap` casi siempre es un punto, y cortar ahí evita tarjetas que
    mezclan el final de una frase con el principio de la siguiente.

    'position' es el índice real de la palabra dentro del texto de la tarjeta,
    repeticiones incluidas: es lo que permite resaltar la ocurrencia correcta
    de palabras como "de" o "la" en vez de siempre la primera.
    """
    _check_timings(timings, audio_duration)
    budget = max_chars * max_lines

    cards = []
    current = []
    length = 0
    for word in timings:
        text = word["word"]
        extra = len(text) + (1 if current else 0)
        pause = current and word["start"] - current[-1]["end"] > sentence_gap
        if current and (length + extra > budget or pause):
            cards.append(current)
            current, length = [], 0
            extra = len(text)
        current.append(word)
        length += extra
    if current:
        cards.append(current)

    out = []
    for words in cards:
        text = " ".join(w["word"] for w in words)
        out.append(
            {
                "start_time": float(words[0]["start"]),
                "end_time": float(words[-1]["end"]),
                "text": text,
                "words": [
                    {
                        "word": w["word"],
                        "start": float(w["start"]),
                        "end": float(w["end"]),
                        "position": i,
                    }
                    for i, w in enumerate(words)
                ],
            }
        )

    # cada tarjeta permanece hasta que la releva la siguiente: sin parpadeos
    for i in range(len(out) - 1):
        out[i]["end_time"] = out[i + 1]["start_time"]
    return out


def karaoke_states(cards):
    """Estados (inicio, fin, texto, palabra resaltada) para hornear el karaoke.

    El resaltado de cada palabra se extiende hasta que empieza la siguiente, en
    lugar de volver a blanco durante las pausas.
    """
    states = []
    for card in cards:
        words = card["words"]
        for i, word in enumerate(words):
            start = card["start_time"] if i == 0 else float(word["start"])
            end = float(words[i + 1]["start"]) if i + 1 < len(words) else card["end_time"]
            if end - start <= 0.001:
                continue
            states.append(
                {
                    "start": start,
                    "end": end,
                    "text": card["text"],
                    "highlight": word["position"],
                }
            )
    return states


def srt_timestamp(seconds):
    hours, rest = divmod(float(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    millis = int(round((secs - int(secs)) * 1000))
    secs = int(secs)
    if millis == 1000:  # el redondeo puede desbordar al segundo siguiente
        millis, secs = 0, secs + 1
    return f"{int(hours):02d}:{int(minutes):02d}:{secs:02d},{millis:03d}"


def cards_to_srt(cards):
    lines = []
    for i, card in enumerate(cards, 1):
        lines.append(
            f"{i}\n{srt_timestamp(card['start_time'])} --> "
            f"{srt_timestamp(card['end_time'])}\n{card['text']}\n"
        )
    return "\n".join(lines) + "\n"
