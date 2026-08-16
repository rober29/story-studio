"""Comprobaciones automáticas sobre el MP4 final.

La idea es que ningún reel llegue a publicarse con un defecto que una máquina
podría haber visto: imagen que acaba antes que la voz, fotogramas negros,
planos congelados, subtítulos que no se dibujaron o caché rancia sirviendo la
misma imagen en escenas distintas.
"""

import glob
import os
import re
import subprocess

from studio import ffmpeg, timing
from studio.cache import read_json, write_json
from studio.errors import StudioError
from studio.pipeline import load_audio, open_manifest, story_paths
from studio.story import find_video, scene_word_counts

SAMPLE_FPS = 2
# suficientemente grande para que el texto fino sobreviva al reescalado: a
# 270x480 un caption corto se quedaba por debajo del umbral de detección
THUMB_W, THUMB_H = 405, 720
# Fracción mínima de píxeles casi blancos en la banda de texto. Calibrado
# midiendo el mismo video antes y después de superponer los subtítulos:
# sin texto la banda da 0.0000 exacto, con texto 0.0195-0.0577. El umbral está
# muy por encima del ruido y muy por debajo de cualquier caso real con texto.
TEXT_PIXEL_RATIO = 0.002

# banda vertical (fracción de altura) donde debe verse el texto de cada formato
# el formato D ancla el subtítulo dentro de la franja (1188-1250 px), así que
# su banda es estrecha y va más arriba que la del A
TEXT_BAND = {"A": (0.55, 0.88), "B": (0.38, 0.62), "C": (0.78, 0.99), "D": (0.60, 0.70)}
CONTROL_BAND = {"A": (0.25, 0.50), "B": (0.05, 0.30), "C": (0.45, 0.70), "D": (0.36, 0.55)}


class Check:
    def __init__(self, name, passed, detail):
        self.name = name
        self.passed = passed
        self.detail = detail

    def as_dict(self):
        return {"check": self.name, "pass": self.passed, "detail": self.detail}


def _sample_frames(video, out_dir):
    for stale in glob.glob(os.path.join(out_dir, "f*.jpg")):
        os.remove(stale)
    ffmpeg.run(
        ["ffmpeg", "-y", "-v", "error", "-i", video,
         "-vf", f"fps={SAMPLE_FPS},scale={THUMB_W}:{THUMB_H}", "-q:v", "3",
         os.path.join(out_dir, "f%04d.jpg")],
        "extraer fotogramas para verificar",
    )
    return sorted(glob.glob(os.path.join(out_dir, "f*.jpg")))


def _detect(video, filter_expr, pattern):
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", video, "-vf", filter_expr, "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return re.findall(pattern, proc.stderr or "")


def _phash(path):
    from PIL import Image

    with Image.open(path) as img:
        small = img.convert("L").resize((16, 16), Image.LANCZOS)
    pixels = list(small.getdata())
    average = sum(pixels) / len(pixels)
    return "".join("1" if p > average else "0" for p in pixels)


def _band_stats(path, band, control):
    from PIL import Image

    with Image.open(path) as img:
        rgb = img.convert("RGB")
        width, height = rgb.size

        def scan(bounds):
            top, bottom = int(height * bounds[0]), int(height * bounds[1])
            crop = rgb.crop((0, top, width, bottom))
            pixels = list(crop.getdata())
            bright = sum(1 for r, g, b in pixels if r > 240 and g > 240 and b > 240)
            yellow = sum(1 for r, g, b in pixels if r > 200 and g > 200 and b < 80)
            return bright / len(pixels), yellow / len(pixels)

        bright_ratio, yellow_ratio = scan(band)
        control_ratio, _ = scan(control)
    return bright_ratio, yellow_ratio, control_ratio


def verify_story(story):
    task_dir, _ = story_paths(story)
    video = find_video(task_dir, story)
    if not os.path.isfile(video):
        raise StudioError(f"no existe {video}; ejecuta el pipeline completo primero")

    out_dir = os.path.join(task_dir, "verify")
    os.makedirs(out_dir, exist_ok=True)
    fmt = story["format"]
    checks, metrics = [], {}

    # --- 1. streams y geometría ---
    info = ffmpeg.probe(video)
    streams = {s.get("codec_type"): s for s in info.get("streams", [])}
    video_stream = streams.get("video")
    if not video_stream or "audio" not in streams:
        checks.append(Check("streams", False, "falta el stream de video o el de audio"))
        return _report(story, out_dir, checks, metrics)
    size = (video_stream.get("width"), video_stream.get("height"))
    checks.append(
        Check("geometria", size == (1080, 1920), f"{size[0]}x{size[1]} (esperado 1080x1920)")
    )
    metrics["size"] = size
    metrics["pix_fmt"] = video_stream.get("pix_fmt")

    # --- 2. duración de imagen frente a voz (por stream, no por contenedor) ---
    video_duration = ffmpeg.stream_duration(video, "v")
    voice_duration = ffmpeg.media_duration(os.path.join(task_dir, "audio.mp3"))
    delta = video_duration - voice_duration
    metrics.update(
        {"video_duration": video_duration, "voice_duration": voice_duration, "delta": delta}
    )
    checks.append(
        Check(
            "duracion",
            timing.DELTA_MIN <= delta <= timing.DELTA_MAX,
            f"imagen {video_duration:.3f}s, voz {voice_duration:.3f}s, delta {delta:+.3f}s",
        )
    )

    # --- 3. negros y 4. congelados ---
    blacks = _detect(video, "blackdetect=d=0.4:pix_th=0.10", r"black_start:(\d+\.?\d*)")
    checks.append(Check("sin_negros", not blacks, f"{len(blacks)} tramo(s) negro(s) {blacks[:3]}"))
    metrics["black_starts"] = blacks

    # en los formatos B y D cada plano es una imagen estática por diseño, así
    # que solo tiene sentido buscar congelados más largos que el plano más
    # largo previsto; si no, el propio ritmo de montaje daría un falso positivo
    freeze_limit = 2.0
    if fmt in ("B", "D"):
        archivo = "beats.json" if fmt == "B" else "shots.json"
        tramos = read_json(os.path.join(task_dir, archivo), archivo)
        freeze_limit = max(t["end"] - t["start"] for t in tramos) + 0.5
    freezes = _detect(
        video, f"freezedetect=n=-60dB:d={freeze_limit:.2f}", r"freeze_start: (\d+\.?\d*)"
    )
    checks.append(
        Check(
            "sin_congelados",
            not freezes,
            f"{len(freezes)} congelado(s) >{freeze_limit:.1f}s {freezes[:3]}",
        )
    )
    metrics["freeze_starts"] = freezes
    metrics["freeze_limit"] = freeze_limit

    # --- 5. variedad de imágenes ---
    manifest = open_manifest(story)
    timings, audio_duration = load_audio(story, manifest, task_dir)
    if fmt == "B":
        beats = read_json(os.path.join(task_dir, "beats.json"), "beats.json")
        moments = [(b["start"] + b["end"]) / 2 for b in beats]
    else:
        spans = timing.scene_spans(scene_word_counts(story), timings, audio_duration)
        moments = [(a + b) / 2 for a, b in spans]

    hashes = []
    for i, at in enumerate(moments):
        shot = os.path.join(out_dir, f"shot{i:03d}.jpg")
        ffmpeg.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", video,
             "-frames:v", "1", "-vf", f"scale={THUMB_W}:{THUMB_H}", shot],
            f"muestrear el plano {i}",
        )
        hashes.append(_phash(shot))
    distinct = len(set(hashes))
    metrics["distinct_images"] = distinct
    metrics["expected_images"] = len(moments)
    checks.append(
        Check(
            "variedad_imagenes",
            distinct >= max(1, int(0.9 * len(moments))),
            f"{distinct} distintas de {len(moments)} esperadas",
        )
    )

    # --- 6/7/8. texto en pantalla ---
    frames = _sample_frames(video, out_dir)
    start_at, end_at = timings[0]["start"], timings[-1]["end"]
    narrated = [
        f for i, f in enumerate(frames) if start_at <= (i + 1) / SAMPLE_FPS <= end_at
    ]
    if narrated:
        stats = [_band_stats(f, TEXT_BAND[fmt], CONTROL_BAND[fmt]) for f in narrated]
        # Se compara contra un umbral absoluto, no contra otra banda: con
        # ilustraciones pálidas (acuarela, cielos claros) la banda de control
        # también es brillante y el texto real se marcaba como ausente.
        with_text = [1 for bright, _, _ in stats if bright > TEXT_PIXEL_RATIO]
        text_ratio = len(with_text) / len(stats)
        metrics["text_frames_ratio"] = round(text_ratio, 3)
        checks.append(
            Check(
                "texto_visible",
                text_ratio >= 0.75,
                f"{text_ratio:.0%} de los fotogramas narrados muestran texto",
            )
        )
        if fmt in ("A", "C", "D"):
            yellow_ratio = sum(1 for _, y, _ in stats if y > 0.0005) / len(stats)
            metrics["karaoke_frames_ratio"] = round(yellow_ratio, 3)
            checks.append(
                Check(
                    "karaoke_activo",
                    yellow_ratio >= 0.25,
                    f"{yellow_ratio:.0%} de los fotogramas tienen resaltado amarillo",
                )
            )

    # --- 9. sanidad del archivo ---
    size_mb = os.path.getsize(video) / 1e6
    metrics["size_mb"] = round(size_mb, 2)
    checks.append(Check("tamano_archivo", size_mb > 0.5, f"{size_mb:.1f} MB"))

    return _report(story, out_dir, checks, metrics)


def _report(story, out_dir, checks, metrics):
    payload = {
        "story_id": story["id"],
        "format": story["format"],
        "metrics": metrics,
        "checks": [c.as_dict() for c in checks],
        "passed": all(c.passed for c in checks),
    }
    write_json(os.path.join(out_dir, "report.json"), payload)

    print(f"\n== verificación de {story['id']} (formato {story['format']}) ==")
    for check in checks:
        mark = "PASS" if check.passed else "FALLO"
        print(f"  [{mark:5}] {check.name}: {check.detail}")
    print("  resultado:", "OK" if payload["passed"] else "HAY FALLOS")
    return payload
