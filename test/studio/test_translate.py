import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import story_translate
from studio.errors import StudioError

ORIGINAL = {
    "id": "mansa-musa-es",
    "series": "historia-oculta",
    "title": "Mansa Musa y la gran inflación",
    "min_duration": 68,
    "characters": {"rey": "a west african king in a bright yellow robe"},
    "scenes": [
        {"text": "Primera escena en español.", "prompt": "wide shot of {rey} in a market"},
        {"text": "Segunda escena en español.", "prompt": "wide shot of {rey} on a throne"},
        {"text": "Tercera escena en español.", "prompt": "wide shot of {rey} in the desert"},
    ],
}

TRADUCIDO = {
    "title": "Mansa Musa and the Great Inflation",
    "scenes": [
        {"index": 0, "text": "First scene in English."},
        {"index": 1, "text": "Second scene in English."},
        {"index": 2, "text": "Third scene in English."},
    ],
}


def componer(traducido=None):
    return story_translate.componer(
        ORIGINAL, traducido or TRADUCIDO, "mansa-musa-en", ORIGINAL["id"],
        "hidden-history", 68,
    )


class TestLoQueNoSePuedeTocar(unittest.TestCase):
    """Todo esto entra en image_key: moverlo cuesta 0,89 $ en imágenes nuevas."""

    def test_los_prompts_vienen_del_original_byte_a_byte(self):
        hija = componer()
        self.assertEqual(
            [s["prompt"] for s in hija["scenes"]],
            [s["prompt"] for s in ORIGINAL["scenes"]],
        )

    def test_el_modelo_no_puede_colar_un_prompt(self):
        # el esquema no tiene campo 'prompt', pero si un día lo tuviera, componer
        # debe seguir ignorándolo: los prompts salen del original y de ningún
        # otro sitio
        envenenado = {
            "title": "x",
            "scenes": [
                {"index": i, "text": f"scene {i}", "prompt": "PROMPT INVENTADO"}
                for i in range(3)
            ],
        }
        hija = componer(envenenado)
        for escena in hija["scenes"]:
            self.assertNotIn("INVENTADO", escena["prompt"])

    def test_los_personajes_se_copian_intactos(self):
        self.assertEqual(componer()["characters"], ORIGINAL["characters"])

    def test_no_cambia_el_numero_ni_el_orden_de_escenas(self):
        hija = componer()
        self.assertEqual(len(hija["scenes"]), len(ORIGINAL["scenes"]))
        self.assertIn("market", hija["scenes"][0]["prompt"])
        self.assertIn("desert", hija["scenes"][2]["prompt"])


class TestAdopcion(unittest.TestCase):
    def test_declara_images_from_al_original(self):
        # la línea que hace que las imágenes salgan gratis
        self.assertEqual(componer()["images_from"], "mansa-musa-es")

    def test_traduce_titulo_y_textos(self):
        hija = componer()
        self.assertEqual(hija["title"], "Mansa Musa and the Great Inflation")
        self.assertEqual(hija["scenes"][0]["text"], "First scene in English.")


class TestOrden(unittest.TestCase):
    """El 'index' existe para que un texto no acabe bajo la imagen equivocada."""

    def test_reordena_una_respuesta_desordenada(self):
        revuelto = {
            "title": "x",
            "scenes": [
                {"index": 2, "text": "tercera"},
                {"index": 0, "text": "primera"},
                {"index": 1, "text": "segunda"},
            ],
        }
        hija = componer(revuelto)
        self.assertEqual([s["text"] for s in hija["scenes"]],
                         ["primera", "segunda", "tercera"])

    def test_rechaza_si_faltan_escenas(self):
        with self.assertRaises(StudioError):
            story_translate.comprobar_indices({"scenes": [{"index": 0, "text": "a"}]}, 3)

    def test_rechaza_indices_repetidos(self):
        malo = {"scenes": [{"index": 0, "text": "a"}, {"index": 0, "text": "b"}]}
        with self.assertRaises(StudioError):
            story_translate.comprobar_indices(malo, 2)

    def test_rechaza_indices_fuera_de_rango(self):
        malo = {"scenes": [{"index": 1, "text": "a"}, {"index": 5, "text": "b"}]}
        with self.assertRaises(StudioError):
            story_translate.comprobar_indices(malo, 2)


class TestNombreDeSalida(unittest.TestCase):
    def test_cambia_el_sufijo_de_idioma(self):
        salida = story_translate.destino_de("stories/mansa-musa-es.json", "es", "en")
        self.assertTrue(salida.endswith("mansa-musa-en.json"))

    def test_lo_anade_si_no_lo_llevaba(self):
        # las historias anteriores a la convención no deben hacerlo fallar
        salida = story_translate.destino_de("stories/tenochtitlan.json", "es", "en")
        self.assertTrue(salida.endswith("tenochtitlan-en.json"))

    def test_no_confunde_un_guion_es_dentro_de_la_palabra(self):
        salida = story_translate.destino_de("stories/pesaje-es.json", "es", "en")
        self.assertTrue(salida.endswith("pesaje-en.json"))


if __name__ == "__main__":
    unittest.main()
