import os
import statistics
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from studio.draw import (
    STRIP_ASPECT,
    STRIP_H,
    VIDEO_W,
    crop_box,
    floor_scale,
    remap_ladder,
    shot_boxes,
)
from studio.errors import StudioError
from studio.timing import plan_shots, scene_spans, word_scene_index
from test.studio.test_timing import make_timings


def scene_of_shot(shot, counts, timings):
    """Escenas a las que pertenecen las palabras cubiertas por un plano."""
    index = word_scene_index(counts, len(timings))
    return {
        index[i]
        for i, w in enumerate(timings)
        if shot["start"] <= w["start"] < shot["end"]
    }


class ShotsBase(unittest.TestCase):
    def setUp(self):
        # 8 escenas de ~24 palabras: la forma recomendada para el formato D
        self.counts = [24] * 8
        self.words = [f"palabra{i}" for i in range(192)]
        self.timings = make_timings(self.words, word_dur=0.28, gap=0.06)
        self.audio = self.timings[-1]["end"] + 0.6

    def shots(self, **kwargs):
        opts = dict(target_shot=2.4, min_shot=1.5, max_shots_per_scene=4)
        opts.update(kwargs)
        return plan_shots(self.counts, self.timings, self.audio, **opts)


class TestContiguity(ShotsBase):
    def test_covers_whole_audio(self):
        shots = self.shots()
        self.assertEqual(shots[0]["start"], 0.0)
        self.assertAlmostEqual(shots[-1]["end"], self.audio)

    def test_shots_are_contiguous(self):
        shots = self.shots()
        for shot, siguiente in zip(shots, shots[1:]):
            self.assertEqual(shot["end"], siguiente["start"])

    def test_every_shot_has_duration(self):
        for shot in self.shots():
            self.assertGreater(shot["end"] - shot["start"], 0)


class TestSceneBoundaries(ShotsBase):
    def test_no_shot_crosses_a_scene(self):
        for shot in self.shots():
            escenas = scene_of_shot(shot, self.counts, self.timings)
            self.assertLessEqual(len(escenas), 1, f"plano multiescena: {shot}")

    def test_every_scene_produces_shots(self):
        vistos = {s["scene"] for s in self.shots()}
        self.assertEqual(sorted(vistos), list(range(len(self.counts))))

    def test_scene_shots_match_spans(self):
        # el primer plano de una escena empieza donde la escena, y el último
        # acaba donde la escena
        shots = self.shots()
        spans = scene_spans(self.counts, self.timings, self.audio)
        for scene, (start, end) in enumerate(spans):
            propios = [s for s in shots if s["scene"] == scene]
            self.assertEqual(propios[0]["start"], start)
            self.assertEqual(propios[-1]["end"], end)

    def test_index_and_total_are_consistent(self):
        shots = self.shots()
        for scene in range(len(self.counts)):
            propios = [s for s in shots if s["scene"] == scene]
            self.assertEqual([s["index"] for s in propios], list(range(len(propios))))
            self.assertTrue(all(s["of"] == len(propios) for s in propios))


class TestLimits(ShotsBase):
    def test_respects_max_shots_per_scene(self):
        for tope in (1, 2, 3, 4, 6):
            shots = self.shots(max_shots_per_scene=tope)
            for scene in range(len(self.counts)):
                propios = [s for s in shots if s["scene"] == scene]
                self.assertLessEqual(len(propios), tope, f"tope={tope}")
                self.assertGreaterEqual(len(propios), 1)

    def test_one_shot_per_scene_equals_spans(self):
        shots = self.shots(max_shots_per_scene=1)
        spans = scene_spans(self.counts, self.timings, self.audio)
        self.assertEqual([(s["start"], s["end"]) for s in shots], spans)

    def test_no_shot_below_min(self):
        min_shot = 1.5
        for shot in self.shots(min_shot=min_shot):
            duracion = shot["end"] - shot["start"]
            # la excepción admitida: una escena entera más corta que el mínimo
            if shot["of"] == 1:
                continue
            self.assertGreaterEqual(duracion, min_shot - 1e-9)

    def test_cuts_land_on_word_starts(self):
        inicios = {round(w["start"], 6) for w in self.timings}
        shots = self.shots()
        for shot in shots:
            if shot["index"] > 0:
                self.assertIn(round(shot["start"], 6), inicios)

    def test_invalid_parameters_raise(self):
        with self.assertRaises(StudioError):
            self.shots(max_shots_per_scene=0)
        with self.assertRaises(StudioError):
            self.shots(target_shot=0)


class TestRhythm(ShotsBase):
    def test_hits_the_reference_cadence(self):
        """La referencia corta cada ~2,4 s: 31 cortes en 75 s (0,41 cortes/s)."""
        shots = self.shots()
        duraciones = [s["end"] - s["start"] for s in shots]
        mediana = statistics.median(duraciones)
        self.assertGreater(mediana, 1.8)
        self.assertLess(mediana, 3.2)
        cortes_por_segundo = len(shots) / self.audio
        self.assertGreater(cortes_por_segundo, 0.28)
        self.assertLess(cortes_por_segundo, 0.55)

    def test_more_shots_than_scenes(self):
        # si no, no habríamos ganado nada sobre el formato A
        self.assertGreater(len(self.shots()), len(self.counts) * 2)


class TestDeterminism(ShotsBase):
    def test_same_input_same_output(self):
        self.assertEqual(self.shots(), self.shots())


class TestDegenerate(unittest.TestCase):
    def test_single_scene(self):
        timings = make_timings([f"w{i}" for i in range(30)])
        shots = plan_shots([30], timings, timings[-1]["end"] + 0.5)
        self.assertTrue(all(s["scene"] == 0 for s in shots))
        self.assertEqual(shots[0]["start"], 0.0)

    def test_single_word(self):
        timings = make_timings(["hola"])
        shots = plan_shots([1], timings, timings[-1]["end"] + 0.3)
        self.assertEqual(len(shots), 1)

    def test_scene_shorter_than_min_shot(self):
        # dos escenas muy desiguales: la corta debe dar exactamente un plano
        timings = make_timings([f"w{i}" for i in range(21)], word_dur=0.2, gap=0.03)
        shots = plan_shots([1, 20], timings, timings[-1]["end"] + 0.4, min_shot=1.5)
        primera = [s for s in shots if s["scene"] == 0]
        self.assertEqual(len(primera), 1)

    def test_words_without_pauses(self):
        timings = make_timings([f"w{i}" for i in range(40)], gap=0.0)
        shots = plan_shots([20, 20], timings, timings[-1]["end"] + 0.3)
        for shot, siguiente in zip(shots, shots[1:]):
            self.assertEqual(shot["end"], siguiente["start"])


class TestCropBoxes(unittest.TestCase):
    GEMINI_2K = (2752, 1536)      # 16:9 a 2K, medido
    POLLINATIONS = (1024, 576)    # 16:9 gratuito, medido

    def test_box_stays_inside_the_image(self):
        for src in (self.GEMINI_2K, self.POLLINATIONS, (1280, 720)):
            for scale in (1.0, 0.62, 0.45):
                for cx, cy in ((0.5, 0.5), (0.0, 0.0), (1.0, 1.0)):
                    x0, y0, x1, y1 = crop_box(*src, scale, cx, cy)
                    self.assertGreaterEqual(x0, 0)
                    self.assertGreaterEqual(y0, 0)
                    self.assertLessEqual(x1, src[0])
                    self.assertLessEqual(y1, src[1])
                    self.assertGreater(x1, x0)
                    self.assertGreater(y1, y0)

    def test_box_keeps_strip_aspect(self):
        for scale in (1.0, 0.8, 0.62, 0.45):
            x0, y0, x1, y1 = crop_box(*self.GEMINI_2K, scale, 0.5, 0.5)
            aspecto = (x1 - x0) / (y1 - y0)
            self.assertAlmostEqual(aspecto, STRIP_ASPECT, delta=STRIP_ASPECT * 0.005)

    def test_boxes_are_integers_and_deterministic(self):
        una = shot_boxes(*self.GEMINI_2K, 4, 0)
        otra = shot_boxes(*self.GEMINI_2K, 4, 0)
        self.assertEqual(una, otra)
        for caja in una:
            self.assertTrue(all(isinstance(v, int) for v in caja))

    def test_first_shot_is_the_widest(self):
        cajas = shot_boxes(*self.GEMINI_2K, 4, 0)
        areas = [(x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in cajas]
        self.assertEqual(max(areas), areas[0])

    def test_no_two_consecutive_shots_are_equal(self):
        cajas = shot_boxes(*self.GEMINI_2K, 4, 0)
        for caja, siguiente in zip(cajas, cajas[1:]):
            self.assertNotEqual(caja, siguiente)

    def test_final_quality_never_upscales(self):
        # es la razón de pagar 2K: los planos cerrados se recortan, no se amplían
        for caja in shot_boxes(*self.GEMINI_2K, 4, 0):
            self.assertGreaterEqual(caja[2] - caja[0], VIDEO_W)
            self.assertGreaterEqual(caja[3] - caja[1], STRIP_H)

    def test_odd_scenes_are_mirrored_but_same_size(self):
        pares = shot_boxes(*self.GEMINI_2K, 4, 0)
        impares = shot_boxes(*self.GEMINI_2K, 4, 1)
        self.assertNotEqual(pares[2], impares[2])
        for a, b in zip(pares, impares):
            self.assertEqual(a[2] - a[0], b[2] - b[0])

    def test_draft_ladder_has_a_floor(self):
        escalera = remap_ladder(floor=0.75)
        self.assertAlmostEqual(escalera[0][0], 1.0)
        self.assertGreaterEqual(min(s for s, _, _ in escalera), 0.75 - 1e-9)

    def test_floor_scale_matches_measured_sources(self):
        # 2752x1536 -> franja 1080x607: cabe recortando al 39,5 %
        self.assertAlmostEqual(floor_scale(*self.GEMINI_2K), 0.395, places=3)
        # la fuente gratuita ya no da margen: haría falta ampliar
        self.assertGreater(floor_scale(*self.POLLINATIONS), 1.0)


if __name__ == "__main__":
    unittest.main()
