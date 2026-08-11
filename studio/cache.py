"""Caché por contenido: claves derivadas de las entradas, manifiesto atómico.

No hay grafo de invalidación explícito. La clave de cada artefacto incluye las
claves de sus entradas, así que la cascada es automática: cambiar el estilo
cambia la clave de cada imagen, que cambia la del frame horneado, que cambia la
del video combinado. No se puede olvidar un arco porque no hay arcos.
"""

import hashlib
import json
import os
import tempfile

from studio.errors import StudioError

# Se suben a mano al cambiar el código que produce cada artefacto. Es la parte
# que la caché no puede deducir sola.
IMG_V = 2  # v2: la clave incluye proveedor, modelo y referencias de personaje
TIMING_V = 2  # v2: las tarjetas cortan también en las pausas largas
CAPTION_V = 1
KARAOKE_V = 1
CHAR_V = 1  # hojas de referencia de personaje
ALIGN_V = 1  # alineación de palabras cuando el TTS no da marcas
SHOTS_V = 1  # reparto de planos del formato D
FRAME_V = 1  # fotogramas compuestos (franja + fondo) del formato D

MANIFEST_VERSION = 1


def key(*parts):
    """Clave estable y corta para un conjunto de entradas."""
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def write_atomic(path, data, mode="wb"):
    """Escribe sin dejar nunca un archivo a medias que la caché dé por bueno."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    encoding = None if "b" in mode else "utf-8"
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".part")
    os.close(fd)
    try:
        with open(tmp, mode, encoding=encoding) as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def write_json(path, payload):
    write_atomic(path, json.dumps(payload, ensure_ascii=False, indent=1), mode="w")


def read_json(path, what):
    if not os.path.isfile(path):
        raise StudioError(f"falta {what}: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise StudioError(f"{path} está corrupto ({e.msg}); bórralo y vuelve a generarlo")


class Manifest:
    """Registro de qué artefacto se generó con qué clave.

    Se guarda tras CADA artefacto, no al final de la fase: es lo que permite
    que un fallo en la imagen 39 de 40 conserve las 38 anteriores.
    """

    def __init__(self, path, story_id, story_format):
        self.path = path
        self.data = {
            "version": MANIFEST_VERSION,
            "story_id": story_id,
            "format": story_format,
            "artifacts": {},
        }
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if loaded.get("version") == MANIFEST_VERSION:
                    self.data = loaded
            except (json.JSONDecodeError, OSError):
                pass  # manifiesto ilegible: se reconstruye solo, no es fatal
        self.data["story_id"] = story_id
        self.data["format"] = story_format
        self.data.setdefault("artifacts", {})

    @property
    def artifacts(self):
        return self.data["artifacts"]

    def fresh(self, slot, expected_key, base_dir):
        """True si el artefacto registrado sigue siendo válido y utilizable."""
        entry = self.artifacts.get(slot)
        if not entry or entry.get("key") != expected_key:
            return False
        path = os.path.join(base_dir, entry["file"])
        if not os.path.isfile(path):
            return False
        size = entry.get("bytes")
        if size is not None and os.path.getsize(path) != size:
            return False
        return True

    def path_of(self, slot, base_dir):
        entry = self.artifacts.get(slot)
        return os.path.join(base_dir, entry["file"]) if entry else None

    def record(self, slot, expected_key, rel_file, base_dir, **extra):
        full = os.path.join(base_dir, rel_file)
        entry = {"key": expected_key, "file": rel_file.replace("\\", "/")}
        if os.path.isfile(full):
            entry["bytes"] = os.path.getsize(full)
        entry.update(extra)
        self.artifacts[slot] = entry
        self.save()

    def forget(self, slot):
        if self.artifacts.pop(slot, None) is not None:
            self.save()

    def referenced_files(self):
        return {e["file"] for e in self.artifacts.values() if "file" in e}

    def save(self):
        write_json(self.path, self.data)
