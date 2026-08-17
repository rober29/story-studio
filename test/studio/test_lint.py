import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from studio import lint
from studio.story import resolve_prompts, validate_story

RICO = (
    "A stout forty-year-old man with a bushy dark beard and an upright regal "
    "posture, wearing a worn navy blue military jacket with gold epaulets"
)

# Un prompt como los de mansa-musa-mali: reparte, puebla y da profundidad.
BUENO = (
    "Wide shot of {rey} riding a horse at the center of a massive desert caravan. "
    "On the left, thousands of attendants march in fine silk clothes. On the "
    "right, a line of camels carries heavy chests. In the background, vast dunes "
    "stretch to the horizon."
)


def historia(scenes, characters=None, formato="D"):
    story = validate_story(
        {
            "id": "demo",
            "format": formato,
            "characters": characters if characters is not None else {"rey": RICO},
            "scenes": [{"text": "hola mundo cruel", "prompt": p} for p in scenes],
        }
    )
    resolve_prompts(story)
    return story


class TestEscenaSinPersonaje(unittest.TestCase):
    """El fallo que costó 0,26 $ el 2026-08-15.

    Las dos únicas escenas del arco de Norton sin {personaje} —muerto en una,
    décadas después en la otra— salieron visiblemente más pobres, porque toda la
    riqueza del formato viene de esa descripción.
    """

    def codigos(self, prompt):
        return [a.codigo for a in lint.lint_story(historia([prompt]))]

    def test_avisa_cuando_falta_el_personaje(self):
        sin = BUENO.replace("{rey}", "a man")
        self.assertIn("escena-sin-personaje", self.codigos(sin))

    def test_no_avisa_cuando_esta(self):
        self.assertNotIn("escena-sin-personaje", self.codigos(BUENO))

    def test_se_da_por_atendido_si_describe_a_alguien_inline(self):
        """Un aviso que no reconoce su propia solución enseña a ignorarlo."""
        inline = (
            "Wide joyful ancient Greek festival. In the center, a stout elderly "
            "priest with a long white beard wearing a cream linen robe with a "
            "purple sash raises his arms over a stone altar. On the left a crowd "
            "of mortals feasts at long tables, on the right musicians play in the "
            "background."
        )
        self.assertNotIn("escena-sin-personaje", self.codigos(inline))

    def test_pero_no_basta_con_mencionar_un_color(self):
        soso = (
            "Wide shot of a red stone altar in the center of an empty plaza, "
            "columns on the left, more columns on the right, and hills in the "
            "distant background behind the whole empty scene."
        )
        self.assertIn("escena-sin-personaje", self.codigos(soso))


class TestPromptsPobres(unittest.TestCase):
    def codigos(self, prompt):
        return [a.codigo for a in lint.lint_story(historia([prompt]))]

    def test_un_prompt_escueto_no_da_para_cuatro_encuadres(self):
        self.assertIn("prompt-corto", self.codigos("{rey} stands on a ship deck"))

    def test_avisa_si_no_reparte_nada(self):
        largo = "{rey} " + "stands quietly thinking about his empire and his debts " * 4
        self.assertIn("prompt-sin-reparto", self.codigos(largo))

    def test_repartir_objetos_no_basta(self):
        """'cajas a la derecha y gaviotas al fondo' cumple el reparto y aburre."""
        vacio = (
            "Wide shot of {rey} on a ship deck in the center, wooden crates "
            "stacked on the left, barrels on the right, and seagulls flying in "
            "the cloudy sky in the background far behind them."
        )
        codigos = self.codigos(vacio)
        self.assertNotIn("prompt-sin-reparto", codigos)
        self.assertIn("prompt-vacio", codigos)

    def test_una_escena_poblada_no_dispara_nada(self):
        self.assertEqual(self.codigos(BUENO), [])


class TestPersonajes(unittest.TestCase):
    def codigos(self, desc):
        return [a.codigo for a in lint.lint_story(historia([BUENO], {"rey": desc}))]

    def test_caza_las_palabras_que_rompen_el_estilo(self):
        for palabra in ("photorealistic", "realistic", "3d render", "cinematic"):
            with self.subTest(palabra=palabra):
                self.assertIn(
                    "personaje-fuera-de-estilo", self.codigos(f"a {palabra} king")
                )

    def test_avisa_si_la_descripcion_es_escueta(self):
        self.assertIn("personaje-pobre", self.codigos("a king"))

    def test_avisa_si_no_hay_color_que_pintar(self):
        sin_color = (
            "A stout forty-year-old man with a bushy beard and an upright regal "
            "posture, wearing a worn military jacket with epaulets"
        )
        self.assertIn("personaje-sin-color", self.codigos(sin_color))

    def test_una_descripcion_rica_no_dispara_nada(self):
        self.assertEqual(self.codigos(RICO), [])


class TestSoloFormatoD(unittest.TestCase):
    """Aplicarlo a A, B y C daba más de veinte avisos por historia, todos falsos.

    Sus mecanismos son otros: una imagen por escena con zoom, o una por beat, y
    un estilo cinematográfico en vez de austero.
    """

    def test_los_demas_formatos_no_se_revisan(self):
        for formato in ("A", "B", "C"):
            with self.subTest(formato=formato):
                story = historia(["a man"], {"rey": "a king"}, formato=formato)
                self.assertEqual(lint.lint_story(story), [])


class TestFichaCompartida(unittest.TestCase):
    """Las partes de un arco comparten ficha SOLO si la descripción es idéntica."""

    def test_avisa_si_una_parte_describe_distinto(self):
        partes = [
            historia([BUENO], {"rey": RICO}),
            historia([BUENO], {"rey": RICO + " and a red sash"}),
        ]
        avisos = lint.lint_serie(partes)
        self.assertEqual([a.codigo for a in avisos], ["ficha-divergente"])

    def test_no_avisa_si_coinciden_byte_a_byte(self):
        partes = [historia([BUENO]), historia([BUENO])]
        self.assertEqual(lint.lint_serie(partes), [])


if __name__ == "__main__":
    unittest.main()
