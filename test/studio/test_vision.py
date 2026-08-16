import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from studio import vision
from studio.errors import StudioError, TransientError


class TestFindText(unittest.TestCase):
    """Avisa, nunca aborta: es la única comprobación que depende de un modelo."""

    def responde(self, payload):
        return mock.patch(
            "studio.gemini.inspect_image",
            return_value=(payload, {"cost_usd": 0.0004}),
        )

    def test_detecta_el_texto_y_dice_cual(self):
        with self.responde({"has_text": True, "found": "HAIL! on a banner"}):
            hay, dice, coste = vision.find_text(b"x")
        self.assertTrue(hay)
        self.assertEqual(dice, "HAIL! on a banner")
        self.assertEqual(coste, 0.0004)

    def test_una_imagen_limpia_no_avisa(self):
        with self.responde({"has_text": False, "found": "nothing"}):
            hay, _, _ = vision.find_text(b"x")
        self.assertFalse(hay)

    def test_un_fallo_de_red_no_tumba_la_fase(self):
        # la fase ya pagó sus imágenes; un aviso que no se pudo hacer no puede
        # tirar abajo lo que ya costó dinero
        for error in (TransientError("sin red"), StudioError("mal"), OSError("boom")):
            with self.subTest(error=type(error).__name__):
                with mock.patch("studio.gemini.inspect_image", side_effect=error):
                    self.assertEqual(vision.find_text(b"x"), (False, "", 0.0))


class TestScan(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.rutas = []
        for nombre in ("a.jpg", "b.jpg"):
            ruta = os.path.join(self.dir, nombre)
            with open(ruta, "wb") as f:
                f.write(b"fake")
            self.rutas.append(ruta)

    def test_devuelve_solo_las_que_fallan(self):
        def falso(data, model="x"):
            return (True, "SPQR", 0.0) if data == b"fake" else (False, "", 0.0)

        with mock.patch.object(vision, "find_text", side_effect=falso):
            fallan = vision.scan(self.rutas)
        self.assertEqual(len(fallan), 2)
        self.assertEqual(fallan[0][1], "SPQR")

    def test_un_archivo_ilegible_se_salta_sin_romper(self):
        with mock.patch.object(vision, "find_text", return_value=(False, "", 0.0)):
            self.assertEqual(vision.scan(["/no/existe.jpg"]), [])

    def test_temperatura_cero_en_el_esquema_de_pregunta(self):
        # una verificación que cambia de opinión entre ejecuciones no sirve
        self.assertIn("has_text", vision.SCHEMA["properties"])
        self.assertIn("has_text", vision.SCHEMA["required"])


class TestPrompt(unittest.TestCase):
    def test_excluye_los_garabatos_decorativos(self):
        """El estilo dibuja billetes y papeles con garabatos que imitan letra.

        Sin esa exclusión, cada billete de Norton daría un falso positivo.
        """
        self.assertIn("squiggles", vision.PROMPT.lower())
        self.assertIn("do not count", vision.PROMPT.lower())


if __name__ == "__main__":
    unittest.main()
