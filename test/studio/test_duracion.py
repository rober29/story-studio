import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from studio import timing


class TestToleranciaDeDuracion(unittest.TestCase):
    """Una sola banda para las dos comprobaciones que miden lo mismo.

    Regresión real del 2026-08-14: phase_visuals usaba ±0,25 simétrico y verify
    -0,05/+0,60 asimétrico. Al añadir la cola deliberada al último plano, un
    reel legítimo pasó verify pero fue rechazado por phase_visuals.
    """

    def test_es_asimetrica(self):
        # quedarse corto trunca narración; pasarse solo congela un fotograma
        self.assertLess(abs(timing.DELTA_MIN), timing.DELTA_MAX)

    def test_apenas_tolera_quedarse_corto(self):
        self.assertGreaterEqual(timing.DELTA_MIN, -0.10)
        self.assertLess(timing.DELTA_MIN, 0)

    def test_la_cola_cabe_en_la_banda(self):
        # si la cola no cupiera, TODOS los reels fallarían la comprobación
        self.assertLess(timing.COLA_ULTIMO_PLANO, timing.DELTA_MAX)

    def test_la_cola_supera_el_error_de_cuantizacion(self):
        # a 30 fps un fotograma son 33 ms; la cola tiene que cubrir varios,
        # porque el desfase se acumula a lo largo de veintiocho planos
        self.assertGreater(timing.COLA_ULTIMO_PLANO, 4 * (1 / 30))

    def test_la_cola_deja_margen_por_arriba(self):
        # con la cola puesta, un reel aún puede desviarse antes de fallar
        margen = timing.DELTA_MAX - timing.COLA_ULTIMO_PLANO
        self.assertGreater(margen, 0.25)


if __name__ == "__main__":
    unittest.main()
