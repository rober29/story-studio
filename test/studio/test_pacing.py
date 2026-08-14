import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from studio import pacing
from studio.errors import StudioError

# Los siete reels medidos, con su duración de voz real. Esta tabla es la fuente
# de las constantes de studio/pacing.py: si alguien las cambia sin volver a
# medir, estos tests lo dicen.
# Todas las duraciones están NORMALIZADAS a velocidad 1.0, igual que las
# reporta story_pace.py --measure: un reel con voice_rate 0,9 dura un 11 % más
# y sin corregirlo aparentaría narrar más despacio de lo que narra.
MEDIDOS = [
    ("plaga-baile", 94, 43.66),
    ("tutankamon", 103, 46.80),
    ("markov", 92, 40.97),
    ("cosas-raras-3", 171, 69.02),
    ("orina-romana", 107, 43.15),
    ("tenochtitlan", 100, 40.30),
    ("yamaguchi", 125, 50.35),
    ("mansa-musa-mali", 181, 72.65),
    ("guerra-asiento", 171, 68.42),
    # el que rompió la cota: 2,672 cuando 'fast' estaba en 2,50
    ("norton-i-emperador-estados-1", 182, 68.11),
]

# Los siete que existían antes de que hubiera comprobación de duración.
ANTES_DEL_CHECK = {
    "plaga-baile", "tutankamon", "markov", "cosas-raras-3",
    "orina-romana", "tenochtitlan", "yamaguchi",
}

# Los reels en inglés medidos. Ojo a la dispersión: 2,950 contra 2,463 con la
# misma voz. Palabras/s no es una constante del idioma, depende de lo largas que
# sean las palabras del guion y de cuántas pausas lleve.
MEDIDOS_EN = [
    ("odd-history-3", 175, 59.33),
    ("mansa-musa-mali-en", 212, 86.09),
    ("guerra-asiento-en", 214, 82.94),
    ("norton-i-emperador-estados-1-en", 215, 83.57),
]


class TestConstantsMatchMeasurements(unittest.TestCase):
    def ritmos(self):
        return [pacing.measure(w, s) for _, w, s in MEDIDOS]

    def test_fast_is_above_every_observed_pace(self):
        # es la garantía de que un guion nunca sale más corto de lo pedido
        self.assertGreaterEqual(pacing.PACE["es"]["fast"], max(self.ritmos()))

    def test_slow_is_at_or_below_every_observed_pace(self):
        self.assertLessEqual(pacing.PACE["es"]["slow"], min(self.ritmos()))

    def test_declared_sample_count_matches(self):
        self.assertEqual(pacing.PACE["es"]["samples"], len(MEDIDOS))

    def test_english_fast_is_above_the_measured_pace(self):
        medido = [pacing.measure(w, s) for _, w, s in MEDIDOS_EN]
        self.assertGreaterEqual(pacing.PACE["en"]["fast"], max(medido))
        self.assertEqual(pacing.PACE["en"]["samples"], len(MEDIDOS_EN))

    def test_english_is_faster_than_spanish(self):
        # si esto dejara de cumplirse, traducir dejaría de acortar los reels
        self.assertGreater(pacing.PACE["en"]["fast"], pacing.PACE["es"]["fast"])

    def test_english_needs_more_words_for_the_same_duration(self):
        # el error que cometió la primera traducción: mismas palabras, menos tiempo
        self.assertGreater(pacing.target_words(65, "en"), pacing.target_words(65, "es"))


class TestTargetWords(unittest.TestCase):
    def test_guarantees_the_floor_at_the_fastest_pace(self):
        for suelo in (30, 60, 65, 68, 90):
            palabras = pacing.target_words(suelo, "es")
            minimo, _ = pacing.estimate_duration(palabras, "es")
            self.assertGreaterEqual(minimo, suelo)

    def test_recommended_floor_asks_for_a_sane_amount(self):
        # Banda y no cifra exacta: el número sale de 'fast', que se recalibra
        # con cada reel nuevo. Lo que este test tiene que atrapar es un orden de
        # magnitud absurdo, no que la calibración haya cambiado.
        self.assertTrue(150 <= pacing.target_words(65, "es") <= 200)

    def test_more_seconds_means_more_words(self):
        self.assertGreater(pacing.target_words(70, "es"), pacing.target_words(60, "es"))

    def test_rejects_non_positive(self):
        with self.assertRaises(StudioError):
            pacing.target_words(0, "es")

    def test_unknown_language_is_explicit(self):
        with self.assertRaises(StudioError) as ctx:
            pacing.target_words(60, "fr")
        self.assertIn("fr", str(ctx.exception))


class TestEstimateDuration(unittest.TestCase):
    def test_band_contains_every_measured_reel(self):
        for nombre, palabras, segundos in MEDIDOS:
            minimo, maximo = pacing.estimate_duration(palabras, "es")
            self.assertGreaterEqual(segundos, minimo - 0.01, nombre)
            self.assertLessEqual(segundos, maximo + 0.01, nombre)

    def test_min_is_below_max(self):
        minimo, maximo = pacing.estimate_duration(150, "es")
        self.assertLess(minimo, maximo)


class TestCheckScript(unittest.TestCase):
    def test_passes_with_enough_words(self):
        pacing.check_script(pacing.target_words(65, "es"), "es", 65)

    def test_fails_short_and_says_how_many_are_missing(self):
        with self.assertRaises(StudioError) as ctx:
            pacing.check_script(100, "es", 65, source="demo.json")
        mensaje = str(ctx.exception)
        self.assertIn("demo.json", mensaje)
        self.assertIn("100 palabras", mensaje)
        # el hueco se deriva de la calibración, no se codifica: lo que se
        # comprueba es que el mensaje lo diga, para que el reintento sepa
        # cuántas palabras alargar
        faltan = pacing.target_words(65, "es") - 100
        self.assertIn(str(faltan), mensaje)

    def test_no_floor_means_no_check(self):
        pacing.check_script(1, "es", 0)

    def test_the_existing_reels_would_be_rejected_for_tiktok(self):
        # El hallazgo que motivó todo esto: de los siete reels que existían
        # ANTES de que hubiera comprobación de duración, solo uno superaba los
        # 60 s de TikTok. Se filtra por nombre y no por posición en MEDIDOS,
        # porque a esa tabla se le siguen añadiendo reels nuevos —escritos ya
        # con el suelo— y contarlos aquí borraría el hallazgo.
        califican = 0
        for nombre, palabras, _ in MEDIDOS:
            if nombre not in ANTES_DEL_CHECK:
                continue
            try:
                pacing.check_script(palabras, "es", pacing.TIKTOK_FLOOR)
                califican += 1
            except StudioError:
                pass
        self.assertEqual(califican, 1)


class TestVoices(unittest.TestCase):
    def test_both_languages_have_both_genders(self):
        for lang in ("es", "en"):
            self.assertIn("F", pacing.DEFAULT_VOICES[lang])
            self.assertIn("M", pacing.DEFAULT_VOICES[lang])

    def test_voice_names_look_like_edge_voices(self):
        for lang, voces in pacing.DEFAULT_VOICES.items():
            for voz in voces.values():
                self.assertTrue(voz.endswith("Neural"), voz)
                self.assertTrue(voz.startswith(lang + "-"), voz)


if __name__ == "__main__":
    unittest.main()
