import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from studio import images, pipeline
from studio.cache import Manifest
from studio.errors import StudioError, TransientError
from studio.providers import ImageProvider, PollinationsImages, image_provider_for
from studio.story import validate_story

def make_jpeg(width=1080, height=1920):
    """Un JPEG real del tamaño pedido, sin depender de archivos externos.

    Un degradado en vez de ruido: lo que se comprueba es el tamaño y el
    formato, y generar ruido píxel a píxel hacía la suite quince veces más lenta.
    """
    import io as _io

    from PIL import Image

    gradient = Image.linear_gradient("L").resize((width, height))
    img = Image.merge("RGB", (gradient, gradient.transpose(Image.ROTATE_90 if width == height else Image.FLIP_TOP_BOTTOM), gradient))
    buffer = _io.BytesIO()
    img.save(buffer, format="JPEG", quality=70)
    return buffer.getvalue()


class FakeProvider(ImageProvider):
    name = "fake"
    model = "m1"
    tier = "1k"
    uses_refs = True
    paid = True

    def cost_usd(self):
        return 0.10


def story(**overrides):
    base = {
        "id": "demo",
        "format": "C",
        "scenes": [{"text": "hola mundo", "prompt": "a lighthouse"}],
    }
    base.update(overrides)
    return validate_story(base)


class TestExtensionFromContent(unittest.TestCase):
    def test_detects_jpeg_and_png(self):
        self.assertEqual(images.extension_for(make_jpeg(64, 64)), ".jpg")
        self.assertEqual(images.extension_for(b"\x89PNG\r\n\x1a\n" + b"x" * 20), ".png")

    def test_unknown_content(self):
        self.assertIsNone(images.extension_for(b"<html>nope</html>"))


class TestTransportChecks(unittest.TestCase):
    def test_small_body_is_transient(self):
        with self.assertRaises(TransientError):
            images.check_transport(b"x" * 10, "image/jpeg")

    def test_html_error_page_is_transient(self):
        with self.assertRaises(TransientError):
            images.check_transport(b"<html>" + b"x" * 20000, "text/html")

    def test_non_image_bytes_are_transient(self):
        with self.assertRaises(TransientError):
            images.check_transport(b"NOTIMG" + b"x" * 20000, "image/jpeg")


class TestRetryPolicy(unittest.TestCase):
    def test_retries_transient(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise TransientError("corte de red")
            return "ok"

        self.assertEqual(images.with_retries(flaky, backoff=(0, 0, 0)), "ok")
        self.assertEqual(len(calls), 3)

    def test_does_not_retry_definitive_errors(self):
        # esto es lo que evita pagar tres veces por la misma imagen
        calls = []

        def bad():
            calls.append(1)
            raise StudioError("proporción incorrecta")

        with self.assertRaises(StudioError):
            images.with_retries(bad, backoff=(0, 0, 0))
        self.assertEqual(len(calls), 1)

    def test_gives_up_after_retries(self):
        with self.assertRaises(StudioError):
            images.with_retries(
                lambda: (_ for _ in ()).throw(TransientError("x")), backoff=(0, 0, 0)
            )


class TestSaveImage(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_writes_with_real_extension(self):
        # el destino se pasa sin extensión: la pone el contenido
        data = make_jpeg(576, 1024)
        stem = os.path.join(self.dir, "img-abc")
        path, size_bytes, dims = images.save_image(data, stem, 1080, 1920)
        self.assertTrue(path.endswith(".jpg"))
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(dims, (576, 1024))
        self.assertEqual(size_bytes, len(data))

    def test_accepts_smaller_image_with_same_aspect(self):
        # los generadores topan el lado largo; eso se tolera y se amplía luego
        data = make_jpeg(576, 1024)
        images.save_image(data, os.path.join(self.dir, "ok"), 1080, 1920)

    def test_rejects_wrong_aspect(self):
        # estirar una imagen apaisada a vertical se vería, así que se rechaza
        data = make_jpeg(1024, 576)
        with self.assertRaises(StudioError):
            images.save_image(data, os.path.join(self.dir, "x"), 1080, 1920)

    def test_rejects_too_small(self):
        data = make_jpeg(180, 320)
        with self.assertRaises(StudioError):
            images.save_image(data, os.path.join(self.dir, "y"), 1080, 1920)


class TestProviderSelection(unittest.TestCase):
    def test_draft_is_always_free(self):
        provider = image_provider_for(story(image_provider="gemini"), "draft")
        self.assertEqual(provider.name, "pollinations")
        self.assertFalse(provider.paid)

    def test_unknown_final_provider_is_explicit(self):
        with self.assertRaises(StudioError) as ctx:
            image_provider_for(story(image_provider="inexistente"), "final")
        self.assertIn("draft", str(ctx.exception))

    def test_pollinations_url_has_no_key_and_escapes(self):
        url = PollinationsImages().url("a/b portrait", "cinematic", 1080, 1920, 7)
        self.assertIn("%2F", url)
        self.assertNotIn("key=", url)


class TestCacheKeyIncludesProvider(unittest.TestCase):
    def key_for(self, provider, refs=None):
        return pipeline.image_key(story(), "un prompt", 1080, 1920, 1, provider, refs)

    def test_same_provider_same_key(self):
        self.assertEqual(self.key_for(FakeProvider()), self.key_for(FakeProvider()))

    def test_different_provider_different_key(self):
        # sin esto, pasar a final reutilizaría las imágenes gratuitas
        self.assertNotEqual(
            self.key_for(PollinationsImages()), self.key_for(FakeProvider())
        )

    def test_different_tier_different_key(self):
        other = FakeProvider()
        other.tier = "2k"
        self.assertNotEqual(self.key_for(FakeProvider()), self.key_for(other))

    def test_refs_only_matter_when_provider_uses_them(self):
        free = PollinationsImages()
        self.assertEqual(self.key_for(free, ("hoja1",)), self.key_for(free, None))
        paid = FakeProvider()
        self.assertNotEqual(self.key_for(paid, ("hoja1",)), self.key_for(paid, None))


class TestSlotNamespacing(unittest.TestCase):
    def test_draft_and_final_do_not_collide(self):
        # es lo que impide que --prune borre las imágenes de pago
        self.assertNotEqual(
            pipeline.slot_for("img", "draft", "scene-00"),
            pipeline.slot_for("img", "final", "scene-00"),
        )

    def test_frame_slots_keep_their_prefix(self):
        # prune distingue el directorio base por este prefijo
        self.assertTrue(
            pipeline.slot_for("frame", "final", "beat-003").startswith("frame/")
        )


class TestEstimationUsesFreshBeats(unittest.TestCase):
    """El presupuesto debe reflejar los parámetros actuales, no los guardados."""

    def jobs_for(self, n_beats):
        data = validate_story(
            {
                "id": "demo",
                "format": "B",
                "scenes": [{"text": "hola mundo", "prompt": "a fox"}],
            }
        )
        beats = [
            {"caption": f"BEAT {i}", "start": i, "end": i + 1, "scene": 0, "words": 2}
            for i in range(n_beats)
        ]
        # task_dir no se llega a leer porque los beats vienen dados
        return pipeline.image_jobs(data, "/no/existe", FakeProvider(), "final", beats=beats)

    def test_job_count_follows_given_beats(self):
        self.assertEqual(len(self.jobs_for(12)), 12)
        self.assertEqual(len(self.jobs_for(21)), 21)

    def test_each_beat_gets_its_own_key(self):
        claves = [j["key"] for j in self.jobs_for(6)]
        self.assertEqual(len(set(claves)), 6)


class TestCharacterSheets(unittest.TestCase):
    def story_with_characters(self):
        from studio.story import resolve_prompts

        data = validate_story(
            {
                "id": "demo",
                "format": "C",
                "characters": {"a": "a red fox", "b": "a grey wolf"},
                "scenes": [
                    {"text": "uno dos tres", "prompt": "{a} corre"},
                    {"text": "cuatro cinco seis", "prompt": "un paisaje vacío"},
                ],
            }
        )
        resolve_prompts(data)
        return data

    def test_only_characters_actually_used(self):
        # 'b' está definido pero no aparece en ninguna escena: no se paga su hoja
        self.assertEqual(pipeline.characters_used(self.story_with_characters()), ["a"])

    def test_free_provider_needs_no_sheets(self):
        data = self.story_with_characters()
        manifest = Manifest(os.path.join(tempfile.mkdtemp(), "m.json"), "demo", "C")
        self.assertEqual(
            pipeline.pending_sheets(data, PollinationsImages(), manifest, "draft"), []
        )

    def test_paid_provider_lists_missing_sheets(self):
        data = self.story_with_characters()
        manifest = Manifest(os.path.join(tempfile.mkdtemp(), "m.json"), "demo", "C")
        self.assertEqual(
            pipeline.pending_sheets(data, FakeProvider(), manifest, "final"), ["a"]
        )

    def test_sheet_key_ignores_story_id(self):
        # así una serie por partes reutiliza la misma cara sin volver a pagarla
        uno = self.story_with_characters()
        otro = self.story_with_characters()
        otro["id"] = "otra-historia"
        provider = FakeProvider()
        self.assertEqual(
            pipeline.character_sheet_key(uno, "a", provider),
            pipeline.character_sheet_key(otro, "a", provider),
        )

    def test_sheet_key_changes_with_description(self):
        data = self.story_with_characters()
        provider = FakeProvider()
        antes = pipeline.character_sheet_key(data, "a", provider)
        data["characters"]["a"] = "a blue fox"
        self.assertNotEqual(antes, pipeline.character_sheet_key(data, "a", provider))

    def test_refs_only_for_scenes_with_that_character(self):
        data = self.story_with_characters()
        jobs = pipeline.image_jobs(data, "/no/existe", FakeProvider(), "final")
        self.assertEqual(jobs[0]["characters"], ["a"])
        self.assertEqual(jobs[1]["characters"], [])
        # y la escena sin personaje tiene clave distinta de la que sí lo lleva
        self.assertNotEqual(jobs[0]["key"], jobs[1]["key"])

    def test_load_refs_reads_each_sheet_once(self):
        carpeta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, carpeta, True)
        ruta = os.path.join(carpeta, "sheet-x.png")
        with open(ruta, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"y" * 50)
        cache = {}
        refs = pipeline.load_refs({"a": ruta}, ["a", "a"], cache)
        self.assertEqual(len(refs), 2)
        self.assertEqual(len(cache), 1)
        self.assertEqual(refs[0][1], "image/png")

    def test_load_refs_skips_unknown_characters(self):
        self.assertEqual(pipeline.load_refs({}, ["fantasma"], {}), [])


class TestCharactersPresent(unittest.TestCase):
    def test_records_which_characters_appear(self):
        from studio.story import resolve_prompts

        data = validate_story(
            {
                "id": "demo",
                "format": "C",
                "characters": {"a": "a red fox", "b": "a grey wolf"},
                "scenes": [
                    {"text": "uno", "prompt": "{a} corre"},
                    {"text": "dos", "prompt": "{a} y {b} se miran"},
                    {"text": "tres", "prompt": "un paisaje vacío"},
                ],
            }
        )
        resolve_prompts(data)
        presentes = [s["characters_present"] for s in data["scenes"]]
        self.assertEqual(presentes, [["a"], ["a", "b"], []])
        # y la sustitución textual se mantiene
        self.assertIn("a red fox", data["scenes"][0]["prompt"])


if __name__ == "__main__":
    unittest.main()
