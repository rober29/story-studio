import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from studio.errors import StudioError
from studio.story import load_story, scene_word_counts, script_text, validate_story


def minimal(**overrides):
    story = {
        "id": "demo",
        "format": "C",
        "scenes": [
            {"text": "primera escena de prueba", "prompt": "a lighthouse at dawn"},
            {"text": "segunda escena de prueba", "prompt": "a storm over the sea"},
        ],
    }
    story.update(overrides)
    return story


class TestValidateStory(unittest.TestCase):
    def test_applies_defaults(self):
        story = validate_story(minimal())
        self.assertEqual(story["format"], "C")
        self.assertEqual(story["voice"], "es-MX-DaliaNeural")
        self.assertEqual(story["characters"], {})
        self.assertGreater(story["max_images"], 0)

    def test_missing_scenes_gives_readable_error(self):
        story = minimal()
        del story["scenes"]
        with self.assertRaises(StudioError) as ctx:
            validate_story(story)
        self.assertIn("scenes", str(ctx.exception))

    def test_empty_scenes_rejected(self):
        with self.assertRaises(StudioError):
            validate_story(minimal(scenes=[]))

    def test_scene_without_prompt_rejected(self):
        story = minimal()
        del story["scenes"][1]["prompt"]
        with self.assertRaises(StudioError) as ctx:
            validate_story(story)
        self.assertIn("escena 1", str(ctx.exception))

    def test_scene_with_blank_text_rejected(self):
        story = minimal()
        story["scenes"][0]["text"] = "   "
        with self.assertRaises(StudioError):
            validate_story(story)

    def test_bad_format_rejected(self):
        with self.assertRaises(StudioError):
            validate_story(minimal(format="Z"))

    def test_max_images_zero_rejected(self):
        # antes provocaba una división por cero al calcular la duración mínima
        with self.assertRaises(StudioError):
            validate_story(minimal(max_images=0))

    def test_beat_bounds_must_be_ordered(self):
        with self.assertRaises(StudioError):
            validate_story(minimal(min_beat_duration=3.0, max_beat_duration=1.0))

    def test_bgm_volume_out_of_range_rejected(self):
        with self.assertRaises(StudioError):
            validate_story(minimal(bgm_volume=1.8))

    def test_id_cannot_escape_storage(self):
        for bad in ("../otro", "a/b", ".."):
            with self.assertRaises(StudioError, msg=bad):
                validate_story(minimal(id=bad))

    def test_id_required(self):
        story = minimal()
        del story["id"]
        with self.assertRaises(StudioError):
            validate_story(story)


class TestResolvePrompts(unittest.TestCase):
    def write(self, story):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(story, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_substitutes_characters(self):
        story = minimal(
            characters={"hero": "a tall knight in silver armor"},
            scenes=[{"text": "el heroe avanza", "prompt": "{hero} walking uphill"}],
        )
        loaded = load_story(self.write(story))
        self.assertEqual(loaded["scenes"][0]["prompt"], "a tall knight in silver armor walking uphill")

    def test_two_characters_in_one_prompt(self):
        story = minimal(
            characters={"a": "a red fox", "b": "a grey wolf"},
            scenes=[{"text": "se encuentran", "prompt": "{a} meets {b} in the woods"}],
        )
        loaded = load_story(self.write(story))
        self.assertEqual(loaded["scenes"][0]["prompt"], "a red fox meets a grey wolf in the woods")

    def test_unknown_placeholder_names_scene_and_placeholder(self):
        story = minimal(
            characters={"hero": "a knight"},
            scenes=[
                {"text": "uno", "prompt": "{hero} rides"},
                {"text": "dos", "prompt": "{villain} waits"},
            ],
        )
        with self.assertRaises(StudioError) as ctx:
            load_story(self.write(story))
        message = str(ctx.exception)
        self.assertIn("escena 1", message)
        self.assertIn("{villain}", message)

    def test_missing_file(self):
        with self.assertRaises(StudioError):
            load_story("no/existe.json")

    def test_malformed_json(self):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        handle.write("{ esto no es json ")
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        with self.assertRaises(StudioError):
            load_story(handle.name)


class TestHelpers(unittest.TestCase):
    def test_script_joins_scene_texts(self):
        story = validate_story(minimal())
        self.assertEqual(
            script_text(story), "primera escena de prueba segunda escena de prueba"
        )

    def test_word_counts(self):
        self.assertEqual(scene_word_counts(validate_story(minimal())), [4, 4])


if __name__ == "__main__":
    unittest.main()
