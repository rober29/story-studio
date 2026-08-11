import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from studio import pipeline
from studio.cache import Manifest, write_atomic
from studio.errors import StudioError
from studio.story import validate_story


def story_for(story_id, images_from="", fmt="D", style=None):
    raw = {
        "id": story_id,
        "format": fmt,
        "scenes": [{"text": "hola mundo", "prompt": "a lighthouse at dawn"}],
    }
    if images_from:
        raw["images_from"] = images_from
    if style:
        raw["style"] = style
    return validate_story(raw)


class AdoptBase(unittest.TestCase):
    """Monta un ROOT falso con una historia donante que ya tiene su imagen."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self._root_real = pipeline.ROOT
        pipeline.ROOT = self.root
        self.addCleanup(setattr, pipeline, "ROOT", self._root_real)

        self.donor_dir = os.path.join(self.root, "storage", "story_images", "es")
        self.heir_dir = os.path.join(self.root, "storage", "story_images", "en")
        os.makedirs(self.donor_dir)
        os.makedirs(self.heir_dir)

    def plant(self, clave, contenido=b"imagen" * 100):
        """Deja una imagen en la donante, con el nombre por contenido."""
        nombre = f"img-{clave}.jpg"
        write_atomic(os.path.join(self.donor_dir, nombre), contenido)
        return nombre

    def manifest(self):
        return Manifest(os.path.join(self.root, "m.json"), "en", "D")

    def jobs(self, clave):
        return [{"slot": "img/final/scene-00", "key": clave, "prompt": "a lighthouse"}]


class TestAdoptable(AdoptBase):
    def test_nothing_without_images_from(self):
        self.plant("abc")
        story = story_for("en")
        self.assertEqual(pipeline.adoptable_images(story, self.jobs("abc")), [])

    def test_finds_the_donor_image(self):
        self.plant("abc")
        story = story_for("en", images_from="es")
        encontrados = pipeline.adoptable_images(story, self.jobs("abc"))
        self.assertEqual(len(encontrados), 1)
        self.assertTrue(encontrados[0][1].endswith("img-abc.jpg"))

    def test_different_key_is_not_adopted(self):
        # es lo que pasa si cambia el style: la clave se mueve y no hay herencia
        self.plant("abc")
        story = story_for("en", images_from="es")
        self.assertEqual(pipeline.adoptable_images(story, self.jobs("otra")), [])

    def test_missing_donor_directory_is_tolerated(self):
        story = story_for("en", images_from="no-existe")
        self.assertEqual(pipeline.adoptable_images(story, self.jobs("abc")), [])


class TestAdopt(AdoptBase):
    def test_links_and_records_with_zero_cost(self):
        nombre = self.plant("abc")
        story = story_for("en", images_from="es")
        manifest = self.manifest()

        adoptados = pipeline.adopt_images(story, manifest, self.jobs("abc"), self.heir_dir)

        self.assertEqual(adoptados, {"abc"})
        self.assertTrue(os.path.isfile(os.path.join(self.heir_dir, nombre)))
        entrada = manifest.artifacts["img/final/scene-00"]
        self.assertEqual(entrada["cost_usd"], 0.0)
        self.assertEqual(entrada["adopted_from"], "es")

    def test_pruning_the_heir_leaves_the_donor_intact(self):
        # la razón de usar enlace duro: dos entradas de directorio, un inodo.
        # Sin esto, --prune de la traducción se llevaría lo que se pagó en la
        # historia original.
        nombre = self.plant("abc")
        story = story_for("en", images_from="es")
        pipeline.adopt_images(story, self.manifest(), self.jobs("abc"), self.heir_dir)

        os.remove(os.path.join(self.heir_dir, nombre))

        self.assertTrue(os.path.isfile(os.path.join(self.donor_dir, nombre)))
        with open(os.path.join(self.donor_dir, nombre), "rb") as f:
            self.assertTrue(f.read())

    def test_adopting_twice_is_harmless(self):
        self.plant("abc")
        story = story_for("en", images_from="es")
        manifest = self.manifest()
        pipeline.adopt_images(story, manifest, self.jobs("abc"), self.heir_dir)
        pipeline.adopt_images(story, manifest, self.jobs("abc"), self.heir_dir)
        self.assertEqual(len(manifest.artifacts), 1)


class TestValidation(unittest.TestCase):
    def test_format_b_is_rejected_with_the_reason(self):
        with self.assertRaises(StudioError) as ctx:
            story_for("en", images_from="es", fmt="B")
        mensaje = str(ctx.exception)
        self.assertIn("formato B", mensaje)
        self.assertIn("caption", mensaje)

    def test_cannot_inherit_from_itself(self):
        with self.assertRaises(StudioError):
            story_for("en", images_from="en")

    def test_path_traversal_is_rejected(self):
        for malo in ("../otro", "a/b", ".."):
            with self.assertRaises(StudioError, msg=malo):
                story_for("en", images_from=malo)

    def test_formats_c_and_d_are_allowed(self):
        for fmt in ("C", "D"):
            self.assertTrue(story_for("en", images_from="es", fmt=fmt))


if __name__ == "__main__":
    unittest.main()
