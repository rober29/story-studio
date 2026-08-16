import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from studio.pipeline import MARCA_ROTULO, NO_TEXT, scene_prompt


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

    def test_la_escotilla_es_una_marca_explicita(self):
        pedido = f"a wooden sign over the tavern door {MARCA_ROTULO}"
        self.assertNotIn(NO_TEXT, scene_prompt({"prompt": pedido}))

    def test_la_marca_se_borra_del_prompt(self):
        # es una instrucción para nosotros, no para el generador
        salida = scene_prompt({"prompt": f"a tavern sign {MARCA_ROTULO}"})
        self.assertNotIn(MARCA_ROTULO, salida)
        self.assertEqual(salida, "a tavern sign")

    def test_mencionar_carteles_sin_la_marca_NO_desactiva_nada(self):
        """La regresión del 2026-08-15, que costó una imagen inutilizable.

        El prompt decía 'collectors bidding with paddle signs' —paletas de
        subasta— y la escotilla de entonces buscaba la palabra 'signs'. El
        generador llenó la imagen de carteles inventados, con faltas y en inglés
        sobre un reel en español.
        """
        for inocente in (
            "collectors bidding with paddle signs",
            "an ornate design on the ship hull",
            "a signpost at the crossroads",
            "the king's signature on the treaty",
            "a newspaper office with printing presses",
        ):
            with self.subTest(inocente=inocente):
                self.assertIn(NO_TEXT, scene_prompt({"prompt": inocente}))


if __name__ == "__main__":
    unittest.main()
