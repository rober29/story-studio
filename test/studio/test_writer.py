import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import story_writer
from studio.story import merge_series, resolve_prompts, validate_story

SERIE = {
    "format": "D",
    "banner": "Historia Oculta",
    "voice": "es-MX-DaliaNeural",
    "characters": {"narrador": "a stick figure with a red bow tie"},
}

CRUDO = {
    "title": "La Guerra del Asiento",
    "style": "no debería usarse en formato D",
    "characters": [
        {"name": "narrador", "description": "descripción distinta que NO debe ganar"},
        {"name": "fandino", "description": "a stick figure with a blue naval coat"},
    ],
    "scenes": [
        {"text": "El capitán {fandino} abordó el navío.", "prompt": "wide shot of {fandino} on a deck"},
        {"text": "El {narrador} lo cuenta.", "prompt": "wide shot of {narrador} pointing"},
    ],
}


class TestPersonajesEnSerie(unittest.TestCase):
    """Una historia de serie puede añadir su propio reparto.

    Regresión real del 2026-08-11: compose descartaba TODOS los personajes
    cuando la historia pertenecía a una serie. El modelo definía correctamente
    a 'fandino', se perdía al componer, y el validador rechazaba la historia
    por un {fandino} sin definir. Se gastaron los dos reintentos persiguiendo
    un fallo que no estaba en la respuesta del modelo.
    """

    def compose(self):
        return story_writer.compose(
            CRUDO, "guerra-asiento", "D", "", series="historia-oculta",
            series_characters=SERIE["characters"],
        )

    def test_conserva_los_personajes_propios(self):
        story = self.compose()
        self.assertIn("fandino", story["characters"])

    def test_no_repite_los_que_ya_da_la_serie(self):
        # repetirlos aquí congelaría un valor que debe vivir en un solo sitio,
        # y rompería la reutilización de las fichas de referencia entre reels
        self.assertNotIn("narrador", self.compose()["characters"])

    def test_la_historia_resultante_valida(self):
        story = self.compose()
        fusionada = merge_series(story, dict(SERIE))
        comprobada = validate_story(fusionada, source="<test>")
        resolve_prompts(comprobada, source="<test>")
        # ambos personajes acaban sustituidos: uno de la serie, otro propio
        self.assertIn("blue naval coat", comprobada["scenes"][0]["prompt"])
        self.assertIn("red bow tie", comprobada["scenes"][1]["prompt"])

    def test_el_formato_D_no_lleva_style_propio(self):
        # validate_story solo aplica FORMAT_STYLE cuando el campo no viene
        self.assertNotIn("style", self.compose())

    def test_sin_serie_se_conservan_todos(self):
        story = story_writer.compose(CRUDO, "x", "C", "")
        self.assertEqual(set(story["characters"]), {"narrador", "fandino"})


class TestBandaDePalabras(unittest.TestCase):
    """El prompt pide una banda por encima del mínimo, no la cifra exacta."""

    def prompt(self, partes=1):
        return story_writer.build_prompt(
            "un tema", "D", 7, 68, "español", lang_code="es", partes=partes
        )

    def test_pide_mas_de_lo_estrictamente_necesario(self):
        from studio import pacing

        minimo = pacing.target_words(68, "es")
        texto = self.prompt()
        self.assertIn(str(minimo), texto)
        # medido: pidiendo la cifra exacta el modelo entregaba 167 de 171
        self.assertIn(str(int(minimo * 1.12)), texto)

    def test_el_arco_exige_el_minimo_en_cada_parte(self):
        self.assertIn("EN CADA PARTE", self.prompt(partes=3))

    def test_el_arco_pide_ganchos_salvo_en_la_ultima(self):
        texto = self.prompt(partes=3)
        self.assertIn("MENOS LA ÚLTIMA", texto)

    def test_una_sola_parte_no_habla_de_arcos(self):
        self.assertNotIn("REGLAS DEL ARCO", self.prompt())


if __name__ == "__main__":
    unittest.main()
