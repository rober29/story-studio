import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from studio.cache import Manifest, key, write_atomic, write_json


class TestKey(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(key("a", 1, {"x": 2}), key("a", 1, {"x": 2}))

    def test_insensitive_to_dict_order(self):
        self.assertEqual(key({"a": 1, "b": 2}), key({"b": 2, "a": 1}))

    def test_sensitive_to_every_input(self):
        base = ("prompt", "style", 1080, 1920, 42, 1)
        baseline = key(*base)
        for i in range(len(base)):
            changed = list(base)
            changed[i] = "OTRO" if isinstance(base[i], str) else base[i] + 1
            self.assertNotEqual(baseline, key(*changed), f"campo {i} no afecta a la clave")

    def test_short_and_hex(self):
        value = key("x")
        self.assertEqual(len(value), 12)
        int(value, 16)


class TestWriteAtomic(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_writes_bytes_and_text(self):
        binary = os.path.join(self.dir, "a.bin")
        write_atomic(binary, b"hola")
        with open(binary, "rb") as f:
            self.assertEqual(f.read(), b"hola")

        text = os.path.join(self.dir, "b.txt")
        write_atomic(text, "camión", mode="w")
        with open(text, encoding="utf-8") as f:
            self.assertEqual(f.read(), "camión")

    def test_leaves_no_partial_file_on_failure(self):
        target = os.path.join(self.dir, "c.bin")
        with self.assertRaises(TypeError):
            write_atomic(target, object())
        self.assertFalse(os.path.exists(target))
        self.assertEqual([f for f in os.listdir(self.dir) if f.endswith(".part")], [])

    def test_overwrites_existing(self):
        target = os.path.join(self.dir, "d.bin")
        write_atomic(target, b"uno")
        write_atomic(target, b"dos")
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"dos")


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "manifest.json")

    def make(self):
        return Manifest(self.path, "demo", "C")

    def artifact(self, name="img.jpg", content=b"x" * 100):
        path = os.path.join(self.dir, name)
        write_atomic(path, content)
        return name

    def test_fresh_only_when_key_matches(self):
        manifest = self.make()
        rel = self.artifact()
        manifest.record("img/0", "abc", rel, self.dir)
        self.assertTrue(manifest.fresh("img/0", "abc", self.dir))
        self.assertFalse(manifest.fresh("img/0", "otra", self.dir))

    def test_not_fresh_when_file_missing(self):
        manifest = self.make()
        rel = self.artifact()
        manifest.record("img/0", "abc", rel, self.dir)
        os.remove(os.path.join(self.dir, rel))
        self.assertFalse(manifest.fresh("img/0", "abc", self.dir))

    def test_not_fresh_when_size_changed(self):
        manifest = self.make()
        rel = self.artifact()
        manifest.record("img/0", "abc", rel, self.dir)
        write_atomic(os.path.join(self.dir, rel), b"corto")
        self.assertFalse(manifest.fresh("img/0", "abc", self.dir))

    def test_survives_reload(self):
        manifest = self.make()
        rel = self.artifact()
        manifest.record("img/0", "abc", rel, self.dir)
        self.assertTrue(self.make().fresh("img/0", "abc", self.dir))

    def test_survives_corrupt_manifest(self):
        write_atomic(self.path, "{roto", mode="w")
        manifest = self.make()  # no debe explotar: se reconstruye vacío
        self.assertEqual(manifest.artifacts, {})

    def test_records_after_each_artifact(self):
        # una caída a mitad conserva lo ya registrado
        manifest = self.make()
        for i in range(3):
            rel = self.artifact(f"img{i}.jpg")
            manifest.record(f"img/{i}", f"k{i}", rel, self.dir)
        with open(self.path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(len(saved["artifacts"]), 3)

    def test_referenced_files(self):
        manifest = self.make()
        manifest.record("img/0", "abc", self.artifact("a.jpg"), self.dir)
        manifest.record("img/1", "def", self.artifact("b.jpg"), self.dir)
        self.assertEqual(manifest.referenced_files(), {"a.jpg", "b.jpg"})

    def test_write_json_roundtrip(self):
        target = os.path.join(self.dir, "x.json")
        write_json(target, {"á": [1, 2]})
        with open(target, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"á": [1, 2]})


if __name__ == "__main__":
    unittest.main()
