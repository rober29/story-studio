"""Envoltorios de ffmpeg/ffprobe que fallan con diagnóstico, no en silencio."""

import json
import os
import shutil
import subprocess

from studio.cache import write_atomic
from studio.errors import StudioError


def require_binaries():
    missing = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
    if missing:
        raise StudioError(
            f"no encuentro {' ni '.join(missing)} en el PATH. "
            f"Instálalo (https://www.gyan.dev/ffmpeg/builds/) y reabre la terminal"
        )


def run(cmd, what):
    """Ejecuta y, si falla, muestra la cola de stderr y el comando completo."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except FileNotFoundError:
        raise StudioError(f"{what}: no encuentro el ejecutable {cmd[0]!r} en el PATH")
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-25:])
        raise StudioError(
            f"{what} falló (código {proc.returncode}):\n{tail}\n\ncomando: {' '.join(cmd)}"
        )
    return proc


def probe(path):
    if not os.path.isfile(path):
        raise StudioError(f"no existe el archivo a inspeccionar: {path}")
    proc = run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", path],
        f"ffprobe sobre {os.path.basename(path)}",
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise StudioError(f"ffprobe devolvió una respuesta ilegible para {path}")


def stream_duration(path, kind="v"):
    """Duración del stream de video ('v') o audio ('a'), no la del contenedor.

    El contenedor reporta el máximo de ambos y oculta el desajuste: un video
    cuya imagen acaba antes que la voz parece correcto si solo se mira format.
    """
    info = probe(path)
    prefix = "video" if kind == "v" else "audio"
    for stream in info.get("streams", []):
        if stream.get("codec_type") == prefix:
            duration = stream.get("duration")
            if duration is not None:
                return float(duration)
            # algunos contenedores solo llevan la duración en el formato
            fmt = info.get("format", {}).get("duration")
            if fmt is not None:
                return float(fmt)
    raise StudioError(f"{path} no tiene stream de {prefix}")


def media_duration(path):
    """Duración del contenedor; para el mp3 de voz es la referencia buena."""
    info = probe(path)
    duration = info.get("format", {}).get("duration")
    if duration is None:
        raise StudioError(f"no pude leer la duración de {path}")
    return float(duration)


def _quote(path):
    """Escapa una ruta para el demuxer concat de ffmpeg."""
    return os.path.abspath(path).replace("\\", "/").replace("'", r"'\''")


def concat_segments(paths, list_path, out_path, what="unir segmentos"):
    """Une segmentos de video ya codificados, sin recodificar.

    Deliberadamente NO se usa el demuxer concat con imágenes sueltas y
    directivas `duration`: en esta versión de ffmpeg no respeta las duraciones
    (medido: 3.04s pedidos producían 3.97s), así que la imagen se desincroniza
    de la voz. Con segmentos ya codificados la duración es exacta.
    """
    if not paths:
        raise StudioError("no hay segmentos que unir")
    lines = [f"file '{_quote(p)}'" for p in paths]
    write_atomic(list_path, "\n".join(lines) + "\n", mode="w")
    run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", list_path, "-c", "copy", out_path],
        what,
    )
    return out_path
