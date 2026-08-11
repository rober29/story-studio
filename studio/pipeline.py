"""Las tres fases del pipeline: assets -> visuals -> render.

Cada fase corre en su propio proceso (ver story_studio.py) para que la memoria
se libere entre ellas. Toda la lógica de reanudación vive en el manifiesto: una
fase interrumpida se retoma sin rehacer lo que ya estaba bien.
"""

import glob
import os
import random
import re

from studio import draw, ffmpeg, images, pacing, providers, timing
from studio.cache import (
    ALIGN_V,
    CAPTION_V,
    CHAR_V,
    FRAME_V,
    IMG_V,
    KARAOKE_V,
    SHOTS_V,
    TIMING_V,
    Manifest,
    key,
    read_json,
    write_atomic,
    write_json,
)
from studio.errors import StudioError
from studio.story import part_label, scene_word_counts, script_text, video_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIDEO_W, VIDEO_H = draw.VIDEO_W, draw.VIDEO_H
FPS = 30
SONGS_DIR = os.path.join(ROOT, "resource", "songs")
SHEETS_DIR = os.path.join(ROOT, "storage", "character_sheets")

# Tope duro por corrida: una historia con max_images alto y proveedor de pago
# puede costar varios dólares sin que nadie lo haya pedido explícitamente.
MAX_PAID_IMAGES_PER_RUN = 30

# (posición, tamaño, valor de custom_position) del karaoke por formato.
# El 87,6 % de D sale de medir dónde cae el subtítulo en el reel de referencia.
KARAOKE_STYLE = {
    "A": ("custom", 48, 62.0),
    "D": ("strip", 48, 87.6),
}
DEFAULT_KARAOKE = ("bottom", 55, 85.0)


# --------------------------------------------------------------------------
# Rutas y manifiesto
# --------------------------------------------------------------------------

def story_paths(story):
    task_dir = os.path.join(ROOT, "storage", "tasks", f"story-{story['id']}")
    images_dir = os.path.join(ROOT, "storage", "story_images", story["id"])
    os.makedirs(task_dir, exist_ok=True)
    os.makedirs(images_dir, exist_ok=True)
    return task_dir, images_dir


def open_manifest(story):
    task_dir, _ = story_paths(story)
    return Manifest(os.path.join(task_dir, "manifest.json"), story["id"], story["format"])


def preflight(story):
    ffmpeg.require_binaries()
    draw.check_fonts()
    task_dir, _ = story_paths(story)
    if not os.access(task_dir, os.W_OK):
        raise StudioError(f"no puedo escribir en {task_dir}")


# --------------------------------------------------------------------------
# Claves
# --------------------------------------------------------------------------

def audio_key(story):
    return key(
        "audio", script_text(story), story["voice"], story["voice_rate"], ALIGN_V
    )


def beats_key(story, akey):
    return key(
        "beats", akey, story["max_images"], story["min_beat_duration"],
        story["max_beat_duration"], scene_word_counts(story), TIMING_V,
    )


def cards_key(story, akey):
    return key(
        "cards", akey, story["max_chars_per_card"], story["max_lines_per_card"], TIMING_V,
    )


def shots_key(story, akey):
    return key(
        "shots", akey, story["target_shot"], story["min_shot"],
        story["max_shots_per_scene"], scene_word_counts(story), SHOTS_V,
    )


def image_key(story, prompt, width, height, seed, provider, refs_key=None):
    """Clave de una imagen.

    Incluye el proveedor y el modelo: sin eso, cambiar de borrador a final
    reutilizaría las imágenes gratuitas creyendo que son las de pago. Las
    referencias de personaje solo entran si el proveedor las usa, para que
    editar una ficha no mueva las claves del borrador.
    """
    return key(
        "img", prompt, story["style"], width, height, seed, IMG_V,
        provider.cache_tag(), refs_key if provider.uses_refs else None,
    )


def image_size(story):
    # el formato A recorta una franja 16:9, el resto usa el lienzo vertical
    return (1280, 720) if story["format"] in ("A", "D") else (VIDEO_W, VIDEO_H)


def slot_for(kind, quality, name):
    """Slots separados por calidad.

    Sin esta separación, generar en final sobrescribiría la entrada del
    borrador en el manifiesto, el archivo gratuito quedaría sin referenciar y
    --prune lo borraría; al volver a borrador, el borrado sería el de pago.
    """
    return f"{kind}/{quality}/{name}"


# --------------------------------------------------------------------------
# Fase 1: assets
# --------------------------------------------------------------------------

def _run_tts(story, task_dir):
    """Sintetiza la voz y valida las marcas por palabra, con escritura atómica."""
    provider = providers.voice_provider_for(story["voice"])
    audio_path = os.path.join(task_dir, "audio.mp3")
    staging = audio_path + ".part"
    try:
        words = provider.synthesize(
            script_text(story), story["voice"], story["voice_rate"], staging
        )
    except BaseException:
        if os.path.exists(staging):
            os.remove(staging)
        raise

    # todo el timing del pipeline cuelga de estas marcas: se comprueban aquí,
    # una vez, en lugar de fallar de forma rara tres fases más adelante
    if not words:
        raise StudioError("el TTS devolvió marcas vacías")
    for i, word in enumerate(words):
        if word["end"] <= word["start"]:
            raise StudioError(f"la palabra {i} ({word['word']!r}) tiene duración cero")
        if i and word["start"] < words[i - 1]["start"]:
            raise StudioError(f"las marcas de palabra no van en orden (posición {i})")

    os.replace(staging, audio_path)
    duration = ffmpeg.media_duration(audio_path)
    if duration <= 0:
        raise StudioError("el audio generado está vacío")
    write_json(os.path.join(task_dir, "word_timings.json"), words)

    # La duración del reel es exactamente la del audio y no se puede alargar en
    # el montaje, así que si el guion se queda corto hay que saberlo AQUÍ, antes
    # de gastar en imágenes: por debajo de 60 s TikTok no remunera.
    suelo = story.get("min_duration") or 0
    if suelo and duration < suelo:
        lang = story.get("lang", "es")
        escritas = len(script_text(story).split())
        faltan = max(1, pacing.target_words(suelo, lang) - escritas)
        print(
            f"[aviso] la voz dura {duration:.1f}s y el suelo declarado son {suelo}s. "
            f"Añade unas {faltan} palabras al guion, o pon "
            f'"voice_rate": {max(0.8, duration / suelo):.2f} para estirarlo '
            f"(no cuesta imágenes en los formatos A, C y D)"
        )
    return words, duration


def load_audio(story, manifest, task_dir):
    """Devuelve (timings, audio_duration) exigiendo que la fase assets corriera."""
    entry = manifest.artifacts.get("audio")
    timings = read_json(
        os.path.join(task_dir, "word_timings.json"),
        "word_timings.json (ejecuta --phase assets)",
    )
    if entry and entry.get("duration"):
        return timings, float(entry["duration"])
    audio_path = os.path.join(task_dir, "audio.mp3")
    if not os.path.isfile(audio_path):
        raise StudioError("falta audio.mp3; ejecuta --phase assets")
    return timings, ffmpeg.media_duration(audio_path)


def character_sheet_key(story, name, provider):
    """Clave de la hoja de referencia de un personaje.

    No incluye el id de la historia a propósito: una serie por partes reutiliza
    la misma cara entre reels sin volver a pagarla.
    """
    return key(
        "charsheet", name, story["characters"][name], story["style"],
        provider.cache_tag(), CHAR_V,
    )


# Los generadores buenos siguiendo instrucciones rotulan de verdad: si el
# prompt menciona lo que se está narrando, lo dibujan como cartel o subtítulo
# dentro de la imagen y el reel acaba con el texto duplicado. El texto lo pone
# siempre la capa de subtítulos, nunca la imagen.
NO_TEXT = (
    "No text, no letters, no words, no captions, no subtitles, no signs and no "
    "writing anywhere in the image."
)

# Palabras con las que un prompt pide rotulación A PROPÓSITO. Si aparecen, no se
# impone NO_TEXT: la escotilla existe para la escena rara que necesita un cartel.
PIDE_ROTULO = re.compile(
    r"\b(sign|signs|signage|poster|placard|inscription|inscribed|written|"
    r"lettering|handwriting|newspaper|headline|scoreboard)\b",
    re.I,
)


def scene_prompt(scene):
    """Prompt de una imagen de escena, con la prohibición de rotular por defecto.

    Antes no se aplicaba, con el argumento de que el prompt lo escribía una
    persona y debía poder pedir un cartel. Ese argumento caducó cuando
    story_writer empezó a escribirlos: medido el 2026-08-11, el generador puso
    'RUM' y 'SUPPLIES' en unas cajas que nadie había pedido, y en inglés sobre un
    reel en español. El texto lo pone siempre la capa de subtítulos.
    """
    prompt = scene["prompt"]
    if PIDE_ROTULO.search(prompt):
        return prompt
    return f"{prompt}. {NO_TEXT}"


SHEET_PROMPT = (
    "character reference sheet of {description}. Full body front view on the "
    "left and head-and-shoulders close-up on the right, plain neutral grey "
    "background, even flat lighting, neutral expression, no scenery, no text. "
    "{style}"
)


def characters_used(story):
    """Personajes que aparecen de verdad en alguna escena."""
    return sorted(
        {c for scene in story["scenes"] for c in scene.get("characters_present", [])}
    )


def pending_sheets(story, provider, manifest, quality):
    """Personajes cuya hoja de referencia habría que generar."""
    if not provider.uses_refs:
        return []
    faltan = []
    for name in characters_used(story):
        ckey = character_sheet_key(story, name, provider)
        if manifest.fresh(f"char/{quality}/{name}", ckey, SHEETS_DIR):
            continue
        # la hoja se nombra por contenido y vive fuera de la historia: si otra
        # ya la generó (una serie por partes, p. ej.) se reutiliza sin pagar
        if _existing_sheet(ckey):
            continue
        faltan.append(name)
    return faltan


def _existing_sheet(ckey):
    for candidate in glob.glob(os.path.join(SHEETS_DIR, f"sheet-{ckey}.*")):
        return candidate
    return None


def ensure_character_sheets(story, provider, manifest, quality):
    """Genera las hojas que falten. Devuelve ({personaje: ruta}, gasto)."""
    if not provider.uses_refs:
        return {}, 0.0
    usados = characters_used(story)
    if not usados:
        return {}, 0.0

    os.makedirs(SHEETS_DIR, exist_ok=True)
    sheets, spent = {}, 0.0
    for name in usados:
        ckey = character_sheet_key(story, name, provider)
        slot = f"char/{quality}/{name}"
        existing = _existing_sheet(ckey)
        if existing:
            if not manifest.fresh(slot, ckey, SHEETS_DIR):
                manifest.record(slot, ckey, os.path.basename(existing), SHEETS_DIR,
                                character=name, cost_usd=0.0)
            sheets[name] = existing
            continue

        print(f"  hoja de personaje: {name}")
        prompt = SHEET_PROMPT.format(
            description=story["characters"][name], style=story["style"]
        )
        data, meta = images.with_retries(
            lambda: provider.fetch(prompt, "", 1280, 720, 0),
            label=f"generar la hoja de {name}",
        )
        path, _, real_size = images.save_image(
            data, os.path.join(SHEETS_DIR, f"sheet-{ckey}"), 1280, 720
        )
        spent += meta.get("cost_usd", 0.0)
        manifest.record(slot, ckey, os.path.basename(path), SHEETS_DIR,
                        character=name, source_size=list(real_size),
                        cost_usd=meta.get("cost_usd", 0.0))
        sheets[name] = path
    return sheets, spent


def load_refs(sheets, names, cache):
    """Bytes de las hojas de los personajes de una escena."""
    refs = []
    for name in names:
        path = sheets.get(name)
        if not path:
            continue
        if path not in cache:
            with open(path, "rb") as f:
                data = f.read()
            mime = "image/png" if path.endswith(".png") else "image/jpeg"
            cache[path] = (data, mime)
        refs.append(cache[path])
    return refs


def adoptable_images(story, jobs, verbose=False):
    """Trabajos cuya imagen ya existe en la historia donante. Solo lee.

    Sirve para las versiones en otro idioma: el prompt de imagen está en inglés
    y no cambia al traducir, así que la clave es la misma y la imagen ya pagada
    vale tal cual.
    """
    donante = story.get("images_from")
    if not donante:
        return []
    donor_dir = os.path.join(ROOT, "storage", "story_images", donante)
    if not os.path.isdir(donor_dir):
        if verbose:
            print(f"[aviso] 'images_from' apunta a {donante!r}, que no tiene imágenes")
        return []

    encontrados = []
    for job in jobs:
        for candidato in glob.glob(os.path.join(donor_dir, f"img-{job['key']}.*")):
            encontrados.append((job, candidato))
            break

    if verbose and jobs and not encontrados:
        print(
            f"[aviso] ninguna imagen de {donante!r} encaja. Las causas, por orden "
            f"de probabilidad: el 'style' difiere (es lo que más pesa en la clave), "
            f"el formato difiere (cambia el tamaño pedido), o la calidad difiere "
            f"(draft y final no comparten imágenes por diseño)"
        )
    return encontrados


def adopt_images(story, manifest, jobs, images_dir):
    """Enlaza lo heredable desde la donante y lo registra con coste 0.

    Se usa enlace duro: son dos entradas de directorio sobre el mismo inodo, así
    que el --prune de una historia no puede llevarse lo de la otra.
    """
    adoptados = set()
    for job, origen in adoptable_images(story, jobs, verbose=True):
        nombre = os.path.basename(origen)
        destino = os.path.join(images_dir, nombre)
        if not os.path.isfile(destino):
            _link_or_copy(origen, destino)
        manifest.record(
            job["slot"], job["key"], nombre, images_dir,
            prompt=job["prompt"][:120], cost_usd=0.0,
            adopted_from=story["images_from"],
        )
        adoptados.add(job["key"])
    if adoptados:
        print(
            f"  {len(adoptados)} imágenes heredadas de {story['images_from']!r} "
            f"(sin coste)"
        )
    return adoptados


def image_jobs(story, task_dir, provider, quality, beats=None):
    """Descripción de cada imagen que necesita la historia, con su clave.

    `beats` permite estimar sobre una agrupación recién calculada en memoria:
    si el JSON en disco quedó obsoleto al cambiar los parámetros, contar sus
    entradas daría un presupuesto que no corresponde a lo que se va a generar.
    """
    width, height = image_size(story)
    raw = []
    if story["format"] == "B":
        if beats is None:
            beats = read_json(os.path.join(task_dir, "beats.json"), "beats.json")
        seen_in_scene = {}
        for i, beat in enumerate(beats):
            scene = story["scenes"][beat["scene"]]
            # el caption se usa como contexto de qué momento ilustrar, pero
            # entre comillas el modelo lo entendía como algo que rotular
            prompt = (
                f"{scene['prompt']}. The moment being narrated: "
                f"{beat['caption'].lower()}. {NO_TEXT}"
            )
            # La semilla varía dentro de la escena: con una semilla fija y
            # prompts casi idénticos el generador devuelve la misma imagen dos
            # veces seguidas y parece que el video se congela. La coherencia la
            # dan la ficha de personaje y el prompt de escena, no la semilla.
            nth = seen_in_scene.get(beat["scene"], 0)
            seen_in_scene[beat["scene"]] = nth + 1
            raw.append(
                ("beat-%03d" % i, prompt, 500 + beat["scene"] * 100 + nth,
                 scene.get("characters_present", []))
            )
    else:
        for i, scene in enumerate(story["scenes"]):
            raw.append(
                ("scene-%02d" % i, scene_prompt(scene), 100 + i,
                 scene.get("characters_present", []))
            )

    out = []
    for name, prompt, seed, present in raw:
        refs_key = (
            tuple(character_sheet_key(story, c, provider) for c in present)
            if provider.uses_refs and present
            else None
        )
        ikey = image_key(story, prompt, width, height, seed, provider, refs_key)
        out.append(
            {
                "slot": slot_for("img", quality, name),
                "prompt": prompt,
                "seed": seed,
                "key": ikey,
                "stem": f"img-{ikey}",
                "characters": present,
                "width": width,
                "height": height,
            }
        )
    return out


def confirm_spend(estimated, count, provider, budget=None, assume_yes=False,
                  threshold=0.50):
    """Avisa antes de gastar. Es la última red antes de la factura."""
    if count > MAX_PAID_IMAGES_PER_RUN:
        raise StudioError(
            f"la historia pide {count} imágenes de pago y el tope por corrida es "
            f"{MAX_PAID_IMAGES_PER_RUN}. Baja 'max_images' o genera por partes"
        )
    if budget is not None and estimated > budget:
        raise StudioError(
            f"el gasto estimado ({estimated:.2f} $) supera --budget ({budget:.2f} $). "
            f"Sube el tope si de verdad quieres generarlo"
        )
    if estimated < threshold or assume_yes:
        return
    print(
        f"\n  Vas a generar {count} imágenes con {provider.describe()}: "
        f"~{estimated:.2f} $"
    )
    try:
        answer = input("  ¿Continuar? [s/N] ").strip().lower()
    except (EOFError, OSError):
        # sin terminal (subproceso, cron): no se puede preguntar, no se gasta
        raise StudioError(
            f"se necesitarían ~{estimated:.2f} $ y no hay terminal para confirmar. "
            f"Vuelve a lanzarlo con --yes si lo has revisado"
        )
    if answer not in ("s", "si", "sí", "y", "yes"):
        raise StudioError("cancelado por el usuario; no se ha gastado nada")


def phase_assets(story, force=False, quality="draft", budget=None, assume_yes=False,
                 no_adopt=False):
    preflight(story)
    task_dir, images_dir = story_paths(story)
    manifest = open_manifest(story)

    # --- voz + marcas de palabra ---
    akey = audio_key(story)
    if force or not manifest.fresh("audio", akey, task_dir):
        print("== TTS ==")
        timings, duration = _run_tts(story, task_dir)
        manifest.record("audio", akey, "audio.mp3", task_dir,
                        duration=duration, words=len(timings))
    else:
        timings, duration = load_audio(story, manifest, task_dir)
        print(f"== TTS en caché ({len(timings)} palabras, {duration:.2f}s) ==")

    counts = scene_word_counts(story)

    # --- agrupación temporal ---
    if story["format"] == "B":
        bkey = beats_key(story, akey)
        if force or not manifest.fresh("beats", bkey, task_dir):
            beats = timing.build_beats(
                counts, timings, duration,
                max_images=story["max_images"],
                min_beat_duration=story["min_beat_duration"],
                max_beat_duration=story["max_beat_duration"],
            )
            write_json(os.path.join(task_dir, "beats.json"), beats)
            manifest.record("beats", bkey, "beats.json", task_dir, count=len(beats))
            print(f"beats: {len(beats)} (tope {story['max_images']})")
    else:
        timing.scene_spans(counts, timings, duration)  # valida que ninguna quede vacía
        if story["format"] == "D":
            skey = shots_key(story, akey)
            if force or not manifest.fresh("shots", skey, task_dir):
                shots = timing.plan_shots(
                    counts, timings, duration,
                    target_shot=story["target_shot"],
                    min_shot=story["min_shot"],
                    max_shots_per_scene=story["max_shots_per_scene"],
                )
                write_json(os.path.join(task_dir, "shots.json"), shots)
                manifest.record("shots", skey, "shots.json", task_dir, count=len(shots))
                medias = sorted(s["end"] - s["start"] for s in shots)
                print(
                    f"planos: {len(shots)} sobre {len(counts)} escenas "
                    f"(mediana {medias[len(medias) // 2]:.2f}s, "
                    f"{len(shots) / duration:.2f} cortes/s)"
                )
        ckey = cards_key(story, akey)
        if force or not manifest.fresh("cards", ckey, task_dir):
            cards = timing.build_cards(
                timings, duration,
                max_chars=story["max_chars_per_card"],
                max_lines=story["max_lines_per_card"],
            )
            write_json(os.path.join(task_dir, "subtitle_enhanced.json"), cards)
            write_atomic(os.path.join(task_dir, "subtitle.srt"),
                         timing.cards_to_srt(cards), mode="w")
            manifest.record("cards", ckey, "subtitle_enhanced.json", task_dir,
                            count=len(cards))
            print(f"tarjetas de subtítulo: {len(cards)}")

    # --- imágenes ---
    provider = providers.image_provider_for(story, quality)
    jobs = image_jobs(story, task_dir, provider, quality)
    pending = [j for j in jobs if force or not manifest.fresh(j["slot"], j["key"], images_dir)]
    # la herencia va antes del gate de gasto: una traducción no debe llegar
    # siquiera a preguntar por dinero. Y gana a --force, que solo significa
    # "ignora la caché de ESTA historia", no "vuelve a pagar lo de la otra"
    if not no_adopt:
        adoptados = adopt_images(story, manifest, pending, images_dir)
        pending = [j for j in pending if j["key"] not in adoptados]
    faltan_hojas = pending_sheets(story, provider, manifest, quality)
    estimated = (len(pending) + len(faltan_hojas)) * provider.cost_usd()
    print(
        f"== imágenes: {len(jobs)} ({len(pending)} por generar) "
        f"con {provider.describe()} =="
    )
    if (pending or faltan_hojas) and provider.paid:
        confirm_spend(
            estimated, len(pending) + len(faltan_hojas), provider,
            budget=budget, assume_yes=assume_yes,
        )

    # las hojas van antes: son la referencia que reciben las escenas
    sheets, sheet_cost = ensure_character_sheets(story, provider, manifest, quality)
    ref_cache = {}

    failed, downscaled = [], []
    spent = sheet_cost  # las hojas también cuentan para el tope de gasto
    for n, job in enumerate(pending, 1):
        refs = load_refs(sheets, job["characters"], ref_cache)
        marca = f" (con {', '.join(job['characters'])})" if refs else ""
        print(f"[{n}/{len(pending)}] {job['slot']}{marca}")
        try:
            data, meta = images.with_retries(
                lambda: provider.fetch(
                    job["prompt"], story["style"], job["width"], job["height"],
                    job["seed"], refs=refs,
                ),
                label=f"generar {job['slot']}",
            )
            path, _, real_size = images.save_image(
                data, os.path.join(images_dir, job["stem"]), job["width"], job["height"]
            )
        except StudioError as e:
            print(f"    ERROR: {e}")
            failed.append(job["slot"])
            continue
        spent += meta.get("cost_usd", 0.0)
        # se registra tras cada imagen: una caída conserva todo lo anterior
        manifest.record(
            job["slot"], job["key"], os.path.basename(path), images_dir,
            prompt=job["prompt"][:120], source_size=list(real_size),
            provider=provider.name, model=provider.model, tier=provider.tier,
            cost_usd=meta.get("cost_usd", 0.0),
        )
        if min(real_size) < min(job["width"], job["height"]):
            downscaled.append(real_size)
        if budget is not None and spent > budget:
            raise StudioError(
                f"se alcanzó el tope de gasto ({spent:.2f} $ > {budget:.2f} $) "
                f"tras {n} imágenes. Lo generado está en caché; sube --budget para seguir"
            )

    if failed:
        raise StudioError(
            f"fallaron {len(failed)}/{len(jobs)} imágenes: {', '.join(failed)}. "
            f"Vuelve a ejecutar --phase assets; lo ya descargado está en caché"
        )
    if downscaled:
        worst = min(downscaled, key=lambda s: min(s))
        print(
            f"[aviso] el generador devolvió imágenes más pequeñas de lo pedido "
            f"(la menor, {worst[0]}x{worst[1]}); se ampliarán a {VIDEO_W}x{VIDEO_H} "
            f"al montar el video"
        )
    if spent:
        print(f"gasto de esta corrida: {spent:.2f} $")
    print("assets completo")


# --------------------------------------------------------------------------
# Fase 2: visuals
# --------------------------------------------------------------------------

def _require_images(story, manifest, task_dir, images_dir, quality):
    provider = providers.image_provider_for(story, quality)
    jobs = image_jobs(story, task_dir, provider, quality)
    missing = [j["slot"] for j in jobs if not manifest.fresh(j["slot"], j["key"], images_dir)]
    if missing:
        raise StudioError(
            f"faltan {len(missing)} imágenes ({', '.join(missing[:4])}"
            f"{'...' if len(missing) > 4 else ''}). Ejecuta --phase assets"
            + (f" --quality {quality}" if quality != "draft" else "")
        )
    for job in jobs:
        job["file"] = manifest.artifacts[job["slot"]]["file"]
    return jobs


def _overlay_png(story, chapter, path):
    """Banner de capítulo/serie + marca de agua sobre lienzo completo."""
    from PIL import Image

    layer = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    used = False
    if story["format"] == "C" and chapter:
        banner = draw.render_chapter_banner(chapter)
        layer.alpha_composite(banner, (0, int(VIDEO_H * 0.06)))
        used = True
    if story["format"] in ("A", "D") and story["banner"]:
        banner = draw.render_series_banner(story["banner"], part_label(story))
        strip_top = (VIDEO_H - VIDEO_W * 9 // 16) // 2
        layer.alpha_composite(banner, (0, max(0, strip_top - banner.height - 24)))
        used = True
    if story["watermark"]:
        mark = draw.render_watermark(story["watermark"])
        layer.alpha_composite(mark, (0, int(VIDEO_H * 0.93)))
        used = True
    if not used:
        return None
    layer.save(path)
    return path


def _scene_segment(image_path, overlay_path, duration, out_path, zoom=True):
    """Un plano: Ken Burns opcional + overlay estático, vía ffmpeg."""
    frames = max(1, int(round(duration * FPS)))
    total_zoom = min(0.14, 0.02 * duration)
    if zoom:
        base = (
            f"scale=2160:3840,zoompan=z='min(1+{total_zoom:.4f}*on/{frames},"
            f"{1 + total_zoom:.4f})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={VIDEO_W}x{VIDEO_H}:fps={FPS}"
        )
    else:
        base = f"scale={VIDEO_W}:{VIDEO_H},fps={FPS}"

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", image_path]
    if overlay_path:
        cmd += ["-loop", "1", "-i", overlay_path]
        filters = f"[0:v]{base}[bg];[bg][1:v]overlay=0:0:format=auto,format=yuv420p[v]"
    else:
        filters = f"[0:v]{base},format=yuv420p[v]"
    cmd += [
        "-t", f"{duration:.3f}", "-filter_complex", filters, "-map", "[v]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(FPS), out_path,
    ]
    ffmpeg.run(cmd, f"generar plano {os.path.basename(out_path)}")
    return out_path


def _visuals_slideshow(story, manifest, task_dir, images_dir, timings, duration, quality):
    """Formatos A y C: un plano por escena, con zoom y overlays."""
    jobs = _require_images(story, manifest, task_dir, images_dir, quality)
    counts = scene_word_counts(story)
    spans = timing.scene_spans(counts, timings, duration)

    work = os.path.join(task_dir, "segments")
    os.makedirs(work, exist_ok=True)
    for stale in glob.glob(os.path.join(work, "*")):
        os.remove(stale)

    segments = []
    for i, (job, (start, end)) in enumerate(zip(jobs, spans)):
        source = os.path.join(images_dir, job["file"])
        if story["format"] == "A":
            frame_path = os.path.join(work, f"frame-{i:02d}.jpg")
            draw.compose_cartoon_frame(source).save(frame_path, quality=93)
            source = frame_path
        overlay = _overlay_png(
            story, story["scenes"][i].get("chapter"), os.path.join(work, f"ov-{i:02d}.png")
        )
        segment = os.path.join(work, f"seg-{i:02d}.mp4")
        print(f"  plano {i + 1}/{len(jobs)} ({end - start:.2f}s)")
        _scene_segment(source, overlay, end - start, segment)
        segments.append(segment)

    out = os.path.join(task_dir, "combined-1.mp4")
    return ffmpeg.concat_segments(
        segments, os.path.join(work, "segments.txt"), out, "unir los planos"
    )


def _visuals_shots(story, manifest, task_dir, images_dir, timings, duration, quality):
    """Formato D: varios encuadres por imagen, con corte seco entre planos.

    El fondo desenfocado se calcula UNA vez por escena a partir de la imagen
    entera y se reutiliza en todos sus planos: si cambiara con el encuadre, cada
    corte movería a la vez la ventana y el halo, y se leería como un cambio de
    escena en lugar de un cambio de cámara.
    """
    from PIL import Image

    jobs = _require_images(story, manifest, task_dir, images_dir, quality)
    shots = read_json(os.path.join(task_dir, "shots.json"),
                      "shots.json (ejecuta --phase assets)")
    if not shots:
        raise StudioError("shots.json está vacío; ejecuta --phase assets")

    framed_dir = os.path.join(images_dir, "framed")
    os.makedirs(framed_dir, exist_ok=True)
    work = os.path.join(task_dir, "segments")
    os.makedirs(work, exist_ok=True)
    for stale in glob.glob(os.path.join(work, "*")):
        os.remove(stale)

    # el banner y la marca de agua no cambian en todo el vídeo: un solo PNG
    overlay = _overlay_png(story, None, os.path.join(work, "overlay.png"))
    draft = quality == "draft"

    por_escena = {}
    for shot in shots:
        por_escena.setdefault(shot["scene"], []).append(shot)

    segments, avisado = [], False
    for scene in sorted(por_escena):
        del_escena = por_escena[scene]
        source = os.path.join(images_dir, jobs[scene]["file"])
        with Image.open(source) as raw:
            original = raw.convert("RGB")
            src_w, src_h = original.size
            background = draw.blurred_background(original)
            cajas = draw.shot_boxes(src_w, src_h, len(del_escena), scene, draft)
            if not avisado and min(c[2] - c[0] for c in cajas) < VIDEO_W:
                print(
                    f"[aviso] las imágenes miden {src_w}x{src_h}: los planos "
                    f"cerrados se amplían y saldrán blandos. Con --quality final "
                    f"se recortan sin ampliar"
                )
                avisado = True

            for shot, caja in zip(del_escena, cajas):
                numero = shots.index(shot)
                fkey = key("frameD", jobs[scene]["key"], caja, VIDEO_W, VIDEO_H, FRAME_V)
                slot = slot_for("frame", quality, "shot-%03d" % numero)
                if not manifest.fresh(slot, fkey, framed_dir):
                    strip = draw.strip_from(original, caja)
                    frame = draw.compose_strip_frame(background, strip)
                    nombre = f"frame-{fkey}.jpg"
                    frame.save(os.path.join(framed_dir, nombre), quality=93)
                    manifest.record(slot, fkey, nombre, framed_dir, scene=scene)

                segmento = os.path.join(work, "seg-%03d.mp4" % numero)
                _scene_segment(
                    manifest.path_of(slot, framed_dir), overlay,
                    shot["end"] - shot["start"], segmento, zoom=False,
                )
                segments.append((numero, segmento))

    segments = [ruta for _, ruta in sorted(segments)]
    print(f"  {len(segments)} planos montados sobre {len(por_escena)} imágenes")
    out = os.path.join(task_dir, "combined-1.mp4")
    return ffmpeg.concat_segments(
        segments, os.path.join(work, "segments.txt"), out, "unir los planos"
    )


def _visuals_beats(story, manifest, task_dir, images_dir, timings, duration, quality):
    """Formato B: caption horneado en cada imagen, sin reencodes intermedios."""
    from PIL import Image

    jobs = _require_images(story, manifest, task_dir, images_dir, quality)
    beats = read_json(os.path.join(task_dir, "beats.json"), "beats.json")
    if not beats:
        raise StudioError("beats.json está vacío; ejecuta --phase assets")
    if len(beats) != len(jobs):
        raise StudioError(
            f"hay {len(beats)} beats pero {len(jobs)} imágenes registradas; "
            f"ejecuta --phase assets para rehacer la correspondencia"
        )

    framed_dir = os.path.join(images_dir, "framed")
    os.makedirs(framed_dir, exist_ok=True)
    work = os.path.join(task_dir, "segments")
    os.makedirs(work, exist_ok=True)
    for stale in glob.glob(os.path.join(work, "*")):
        os.remove(stale)
    watermark = draw.render_watermark(story["watermark"]) if story["watermark"] else None

    segments = []
    for i, (beat, job) in enumerate(zip(beats, jobs)):
        fkey = key("frame", job["key"], beat["caption"], VIDEO_W, VIDEO_H,
                   story["watermark"], CAPTION_V)
        slot = slot_for("frame", quality, "beat-%03d" % i)
        if not manifest.fresh(slot, fkey, framed_dir):
            frame = draw.load_scene_image(os.path.join(images_dir, job["file"])).convert("RGBA")
            caption = draw.render_caption(beat["caption"])
            frame.alpha_composite(caption, (0, (VIDEO_H - caption.height) // 2))
            if watermark is not None:
                frame.alpha_composite(watermark, (0, int(VIDEO_H * 0.93)))
            out_file = f"frame-{fkey}.jpg"
            frame.convert("RGB").save(os.path.join(framed_dir, out_file), quality=92)
            manifest.record(slot, fkey, out_file, framed_dir, caption=beat["caption"][:80])
        segment = os.path.join(work, f"seg-{i:03d}.mp4")
        _scene_segment(
            manifest.path_of(slot, framed_dir), None,
            beat["end"] - beat["start"], segment, zoom=False,
        )
        segments.append(segment)

    out = os.path.join(task_dir, "combined-1.mp4")
    return ffmpeg.concat_segments(
        segments, os.path.join(work, "segments.txt"), out, "unir los beats"
    )


def phase_visuals(story, force=False, quality="draft", budget=None, assume_yes=False):
    preflight(story)
    task_dir, images_dir = story_paths(story)
    manifest = open_manifest(story)
    timings, duration = load_audio(story, manifest, task_dir)

    if story["format"] == "B":
        out = _visuals_beats(story, manifest, task_dir, images_dir, timings, duration, quality)
    elif story["format"] == "D":
        out = _visuals_shots(story, manifest, task_dir, images_dir, timings, duration, quality)
    else:
        out = _visuals_slideshow(story, manifest, task_dir, images_dir, timings, duration, quality)

    made = ffmpeg.stream_duration(out, "v")
    print(f"combined: {out} ({made:.2f}s, audio {duration:.2f}s)")
    if abs(made - duration) > 0.25:
        raise StudioError(
            f"el video mudo dura {made:.2f}s y la voz {duration:.2f}s; "
            f"no deberían diferir. Revisa los tiempos de las escenas"
        )


# --------------------------------------------------------------------------
# Fase 3: render
# --------------------------------------------------------------------------

def _link_or_copy(source, destination):
    try:
        os.link(source, destination)
    except (OSError, NotImplementedError):
        import shutil

        shutil.copyfile(source, destination)


def _bake_karaoke(story, task_dir, video_duration):
    """Pista de subtítulos como secuencia de fotogramas.

    Se dibuja un PNG por estado distinto (memoria constante) y luego se enlaza
    uno por fotograma de salida. Es exacto por construcción: el fotograma i se
    reproduce en i/FPS, sin depender de que el demuxer respete duraciones.
    """
    cards = read_json(os.path.join(task_dir, "subtitle_enhanced.json"),
                      "subtitle_enhanced.json (ejecuta --phase assets)")
    states = timing.karaoke_states(cards)
    if not states:
        raise StudioError("no hay estados de karaoke que dibujar")

    subs_dir = os.path.join(task_dir, "subs")
    os.makedirs(subs_dir, exist_ok=True)
    # el formato D ancla el subtítulo a la franja (87,6 % de su altura, medido
    # sobre el reel de referencia); el A conserva su posición de siempre
    position, font_size, custom = KARAOKE_STYLE.get(
        story["format"], DEFAULT_KARAOKE
    )

    from PIL import Image

    blank = os.path.join(subs_dir, "blank.png")
    if not os.path.isfile(blank):
        Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0)).save(blank)

    made = {}
    for state in states:
        skey = key("kar", state["text"], state["highlight"], font_size,
                   position, custom, story["format"], KARAOKE_V)
        state["image"] = made.get(skey)
        if state["image"] is None:
            path = os.path.join(subs_dir, f"kar-{skey}.png")
            if not os.path.isfile(path):
                draw.render_karaoke(
                    state["text"], state["highlight"],
                    font_size=font_size, position=position, custom_position=custom,
                    max_lines=story["max_lines_per_card"],
                ).save(path)
            made[skey] = path
            state["image"] = path

    sequence_dir = os.path.join(subs_dir, "seq")
    os.makedirs(sequence_dir, exist_ok=True)
    for stale in glob.glob(os.path.join(sequence_dir, "*.png")):
        os.remove(stale)

    total = int(round(video_duration * FPS))
    cursor = 0
    for frame in range(total):
        moment = (frame + 0.5) / FPS
        while cursor < len(states) and states[cursor]["end"] <= moment:
            cursor += 1
        source = blank
        if cursor < len(states) and states[cursor]["start"] <= moment:
            source = states[cursor]["image"]
        _link_or_copy(source, os.path.join(sequence_dir, f"f{frame:06d}.png"))

    print(f"karaoke: {len(states)} estados, {len(made)} imágenes distintas, {total} fotogramas")
    return os.path.join(sequence_dir, "f%06d.png")


def _overlay_subtitles(combined, pattern, out_path):
    ffmpeg.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", combined,
         "-framerate", str(FPS), "-i", pattern,
         "-filter_complex",
         "[1:v]format=rgba[s];[0:v][s]overlay=0:0:eof_action=pass:format=auto[v]",
         "-map", "[v]", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p", "-r", str(FPS), out_path],
        "superponer los subtítulos",
    )
    return out_path


def _pick_bgm(story):
    if story["bgm_volume"] <= 0:
        return None
    songs = sorted(glob.glob(os.path.join(SONGS_DIR, "*.mp3")))
    if songs:
        print(
            "[aviso] estás incrustando música de resource/songs/, cuya licencia "
            "se desconoce: puede provocar una reclamación de copyright que "
            "desvíe los ingresos del reel. Pon bgm_volume en 0 y añade la música "
            "desde la propia app de TikTok o Instagram"
        )
    return random.choice(songs) if songs else None


def _mux_audio(video, voice, bgm, volume, out_path):
    duration = ffmpeg.stream_duration(video, "v")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-i", voice]
    if bgm:
        fade_at = max(0.0, duration - 3.0)
        cmd += ["-stream_loop", "-1", "-i", bgm]
        # normalize=0 es necesario: por defecto amix divide entre el número de
        # entradas y la voz se hunde a la mitad
        filters = (
            f"[1:a]volume=1.0,apad[a1];"
            f"[2:a]volume={volume},afade=t=out:st={fade_at:.2f}:d=3[a2];"
            f"[a1][a2]amix=inputs=2:duration=first:normalize=0[a]"
        )
    else:
        filters = "[1:a]volume=1.0,apad[a1];[a1]anull[a]"
    cmd += [
        "-filter_complex", filters, "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-t", f"{duration:.3f}", "-movflags", "+faststart", out_path,
    ]
    ffmpeg.run(cmd, "mezclar voz y música")
    return out_path


def phase_render(story, force=False, quality="draft", budget=None, assume_yes=False):
    preflight(story)
    task_dir, _ = story_paths(story)
    combined = os.path.join(task_dir, "combined-1.mp4")
    if not os.path.isfile(combined):
        raise StudioError("falta combined-1.mp4; ejecuta --phase visuals")
    voice_path = os.path.join(task_dir, "audio.mp3")
    if not os.path.isfile(voice_path):
        raise StudioError("falta audio.mp3; ejecuta --phase assets")

    if story["format"] == "B":
        with_subs = combined  # el caption ya está horneado en cada imagen
    else:
        pattern = _bake_karaoke(story, task_dir, ffmpeg.stream_duration(combined, "v"))
        with_subs = _overlay_subtitles(
            combined, pattern, os.path.join(task_dir, "video_subs.mp4")
        )

    final = video_path(task_dir, story)
    _mux_audio(with_subs, voice_path, _pick_bgm(story), story["bgm_volume"], final)
    print(f"\n=== VIDEO GENERADO ===\n{final}")
    return final


# --------------------------------------------------------------------------
# Dry run
# --------------------------------------------------------------------------

def phase_plan(story, quality="draft"):
    """Qué se regeneraría, por qué y cuánto costaría. Sin red y sin escribir."""
    task_dir, images_dir = story_paths(story)
    manifest = open_manifest(story)
    provider = providers.image_provider_for(story, quality)
    stale, estimated = [], 0.0

    akey = audio_key(story)
    if not manifest.fresh("audio", akey, task_dir):
        stale.append(("audio + marcas de palabra (script, voz o velocidad)", 0.0))

    beats = None
    if story["format"] == "B":
        if not manifest.fresh("beats", beats_key(story, akey), task_dir):
            stale.append(("beats.json (agrupación temporal)", 0.0))
            # se recalcula en memoria para que el presupuesto refleje los
            # parámetros actuales y no la agrupación que hay guardada
            try:
                timings, duration = load_audio(story, manifest, task_dir)
                beats = timing.build_beats(
                    scene_word_counts(story), timings, duration,
                    max_images=story["max_images"],
                    min_beat_duration=story["min_beat_duration"],
                    max_beat_duration=story["max_beat_duration"],
                )
            except StudioError:
                beats = None
    else:
        if not manifest.fresh("cards", cards_key(story, akey), task_dir):
            stale.append(("tarjetas de subtítulo", 0.0))
        if story["format"] == "D" and not manifest.fresh(
            "shots", shots_key(story, akey), task_dir
        ):
            # se recalcula en memoria para poder informar del ritmo real
            detalle = "plan de planos"
            try:
                timings, duracion = load_audio(story, manifest, task_dir)
                previstos = timing.plan_shots(
                    scene_word_counts(story), timings, duracion,
                    target_shot=story["target_shot"],
                    min_shot=story["min_shot"],
                    max_shots_per_scene=story["max_shots_per_scene"],
                )
                medias = sorted(s["end"] - s["start"] for s in previstos)
                detalle = (
                    f"plan de planos: {len(previstos)} planos, mediana "
                    f"{medias[len(medias) // 2]:.2f}s, "
                    f"{len(previstos) / duracion:.2f} cortes/s"
                )
            except StudioError:
                pass
            stale.append((detalle, 0.0))

    # El formato B necesita beats.json (y por tanto el audio) para saber
    # cuántas imágenes son; A y C no, así que se estiman en frío.
    faltan_hojas = pending_sheets(story, provider, manifest, quality)
    if faltan_hojas:
        coste = len(faltan_hojas) * provider.cost_usd()
        estimated += coste
        stale.append((f"{len(faltan_hojas)} hojas de personaje: "
                      + ", ".join(faltan_hojas), coste))

    try:
        jobs = image_jobs(story, task_dir, provider, quality, beats=beats)
        pending = [j for j in jobs if not manifest.fresh(j["slot"], j["key"], images_dir)]
        heredables = {j["key"] for j, _ in adoptable_images(story, pending)}
        if heredables:
            stale.append(
                (f"{len(heredables)} imágenes heredadas de "
                 f"{story['images_from']!r} (sin coste)", 0.0)
            )
            pending = [j for j in pending if j["key"] not in heredables]
        if pending:
            coste = len(pending) * provider.cost_usd()
            estimated += coste
            stale.append(
                (
                    f"{len(pending)}/{len(jobs)} imágenes ({provider.describe()}): "
                    + ", ".join(j["slot"].rsplit("/", 1)[-1] for j in pending[:6])
                    + ("..." if len(pending) > 6 else ""),
                    coste,
                )
            )
    except StudioError as e:
        stale.append((f"imágenes (no puedo contarlas todavía: {e})", 0.0))

    print(f"historia: {story['id']} (formato {story['format']}) | calidad: {quality}")
    if not stale:
        print("todo en caché: no se regeneraría nada")
    else:
        print("se regeneraría:")
        for item, coste in stale:
            money = f"   {coste:.2f} $" if coste else ""
            print(f"  - {item}{money}")
    if provider.paid:
        print(f"                                   ESTIMADO  {estimated:.2f} $")
    already = sum(
        float(e.get("cost_usd") or 0.0) for e in manifest.artifacts.values()
    )
    if already:
        print(f"ya gastado en esta historia: {already:.2f} $")
    return [item for item, _ in stale]


def prune(story, assume_yes=False):
    """Borra imágenes que ya nadie referencia.

    Desde que hay proveedores de pago, este directorio contiene dinero: se
    avisa antes de borrar y se exige confirmación explícita.
    """
    _, images_dir = story_paths(story)
    framed_dir = os.path.join(images_dir, "framed")
    manifest = open_manifest(story)

    # Entradas de un esquema de slots anterior (sin calidad): mantenerlas haría
    # que sus archivos parecieran vivos para siempre y prune no limpiaría nada.
    obsolete = [
        slot
        for slot in list(manifest.artifacts)
        if slot.split("/")[0] in ("img", "frame") and len(slot.split("/")) != 3
    ]
    for slot in obsolete:
        manifest.forget(slot)
    if obsolete:
        print(f"prune: {len(obsolete)} entradas de un formato antiguo descartadas")

    # cada slot guarda su ruta relativa a un directorio base distinto: los
    # frames horneados cuelgan de framed/, las imágenes de la raíz
    keep = set()
    paid_files = set()
    for slot, entry in manifest.artifacts.items():
        if "file" not in entry:
            continue
        base = framed_dir if slot.startswith("frame/") else images_dir
        full = os.path.normcase(os.path.abspath(os.path.join(base, entry["file"])))
        keep.add(full)
        if entry.get("cost_usd"):
            paid_files.add(full)

    # se buscan por prefijo y no por extensión: los proveedores devuelven jpg
    # o png y un glob de *.jpg dejaría los png fuera de la limpieza
    candidates = []
    for pattern in ("img-*.*", "frame-*.*"):
        candidates += glob.glob(os.path.join(images_dir, "**", pattern), recursive=True)

    orphans = [
        p for p in candidates
        if os.path.normcase(os.path.abspath(p)) not in keep
    ]
    if not orphans:
        print("prune: no hay nada que borrar")
        return 0

    total_mb = sum(os.path.getsize(p) for p in orphans) / 1e6
    print(f"prune: {len(orphans)} archivos huérfanos ({total_mb:.1f} MB)")
    for path in orphans[:8]:
        marca = "  [DE PAGO]" if os.path.normcase(os.path.abspath(path)) in paid_files else ""
        print(f"  - {os.path.basename(path)}{marca}")
    if len(orphans) > 8:
        print(f"  ... y {len(orphans) - 8} más")
    huerfanos_pagados = [
        p for p in orphans if os.path.normcase(os.path.abspath(p)) in paid_files
    ]
    if huerfanos_pagados:
        print(
            f"  ATENCIÓN: {len(huerfanos_pagados)} de ellos se generaron con un "
            f"proveedor de pago"
        )

    if not assume_yes:
        try:
            answer = input("  ¿Borrarlos? [s/N] ").strip().lower()
        except (EOFError, OSError):
            print("  sin terminal para confirmar: no se borra nada (usa --yes)")
            return 0
        if answer not in ("s", "si", "sí", "y", "yes"):
            print("  cancelado")
            return 0

    for path in orphans:
        os.remove(path)
    print(f"prune: {len(orphans)} archivos borrados")
    return len(orphans)
