import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from studio.pipeline import NO_TEXT, scene_prompt


class TestProhibicionDeRotular(unittest.TestCase):
    """El texto lo pone la capa de subtítulos, nunca la imagen.

    Antes no se aplicaba a las escenas, con el argumento de que el prompt lo
    escribía una persona. Ese argumento caducó cuando story_writer empezó a
    escribirlos: el generador puso 'RUM' y 'SUPPLIES' en unas cajas que nadie
    pidió, y en inglés sobre un reel en español.
    """

    def test_se_impone_por_defecto(self):
        salida = scene_prompt({"prompt": "wide shot of a ship deck with crates"})
        self.assertIn(NO_TEXT, salida)

    def test_conserva_el_prompt_original(self):
        original = "wide shot of a ship deck with crates"
        self.assertTrue(scene_prompt({"prompt": original}).startswith(original))

    def test_la_escotilla_deja_pedir_un_cartel(self):
        # la escena rara que SÍ necesita rotulación no debe quedar bloqueada
        for pedido in (
            "a wooden sign hanging over the tavern door",
            "a newspaper headline about the scandal",
            "an inscription carved into the stone",
        ):
            with self.subTest(pedido=pedido):
                self.assertNotIn(NO_TEXT, scene_prompt({"prompt": pedido}))

    def test_no_confunde_palabras_que_contienen_sign(self):
        # 'design' y 'designated' contienen 'sign' pero no piden rotular
        salida = scene_prompt({"prompt": "an ornate design on the ship hull"})
        self.assertIn(NO_TEXT, salida)

    def test_es_insensible_a_mayusculas(self):
        self.assertNotIn(NO_TEXT, scene_prompt({"prompt": "a large SIGN on the wall"}))


if __name__ == "__main__":
    unittest.main()
