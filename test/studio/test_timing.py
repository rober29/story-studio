import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from studio.errors import StudioError
from studio.timing import (
    allocate_scene_words,
    build_beats,
    build_cards,
    cards_to_srt,
    karaoke_states,
    scene_spans,
    srt_timestamp,
    word_scene_index,
)


def make_timings(words, word_dur=0.4, gap=0.05, pauses=None):
    """Timings sintéticos; pauses = {índice: segundos extra antes de esa palabra}."""
    pauses = pauses or {}
    timings, t = [], 0.1
    for i, word in enumerate(words):
        t += pauses.get(i, 0.0)
        timings.append({"word": word, "start": round(t, 4), "end": round(t + word_dur, 4)})
        t += word_dur + gap
    return timings


class TestAllocateSceneWords(unittest.TestCase):
    def assert_invariants(self, counts, n_words):
        alloc = allocate_scene_words(counts, n_words)
        self.assertEqual(len(alloc), len(counts))
        self.assertEqual(sum(alloc), n_words, f"counts={counts} n_words={n_words}")
        self.assertTrue(all(a >= 1 for a in alloc), f"escena vacía: {alloc}")
        return alloc

    def test_exact_match(self):
        # el caso de las tres historias actuales: TTS devuelve lo declarado
        self.assert_invariants([15, 9, 8, 7, 14, 11, 14, 22], 100)

    def test_tts_returns_more_words(self):
        # "EE.UU." o "1978" se parten en varios WordBoundary
        for extra in (1, 5, 50, 200):
            self.assert_invariants([10, 10, 10], 30 + extra)

    def test_tts_returns_fewer_words(self):
        # el TTS junta tokens: ninguna escena puede quedarse sin palabras
        for n_words in (29, 20, 10, 3):
            self.assert_invariants([10, 10, 10], n_words)

    def test_last_scene_always_used(self):
        # el bug del truncado por la derecha: la última escena se quedaba fuera
        counts = [10, 10, 10, 10, 10, 10]
        index = word_scene_index(counts, 40)
        self.assertIn(len(counts) - 1, index)
        self.assertEqual(sorted(set(index)), list(range(len(counts))))

    def test_minimum_one_word_per_scene(self):
        alloc = self.assert_invariants([10, 10, 10], 3)
        self.assertEqual(alloc, [1, 1, 1])

    def test_fewer_words_than_scenes_raises(self):
        with self.assertRaises(StudioError):
            allocate_scene_words([5, 5, 5], 2)

    def test_no_scenes_raises(self):
        with self.assertRaises(StudioError):
            allocate_scene_words([], 5)

    def test_extreme_disproportion(self):
        self.assert_invariants([1, 100], 3)
        self.assert_invariants([100, 1, 1], 3)
        self.assert_invariants([1, 1, 1], 100)

    def test_single_scene(self):
        self.assertEqual(allocate_scene_words([12], 12), [12])


class TestWordSceneIndex(unittest.TestCase):
    def test_length_is_exact(self):
        for n_words in (3, 10, 47, 200):
            self.assertEqual(len(word_scene_index([10, 5, 20], n_words)), n_words)

    def test_non_decreasing(self):
        index = word_scene_index([7, 3, 11, 5], 40)
        self.assertEqual(index, sorted(index))

    def test_covers_every_scene(self):
        counts = [4, 9, 2, 15, 6]
        for n_words in (5, 12, 36, 90):
            index = word_scene_index(counts, n_words)
            self.assertEqual(sorted(set(index)), list(range(len(counts))))


class TestSceneSpans(unittest.TestCase):
    def setUp(self):
        self.counts = [4, 6, 5]
        self.timings = make_timings([f"w{i}" for i in range(15)])
        self.audio = self.timings[-1]["end"] + 0.9

    def test_starts_at_zero_and_ends_at_audio_duration(self):
        spans = scene_spans(self.counts, self.timings, self.audio)
        self.assertEqual(spans[0][0], 0.0)
        self.assertAlmostEqual(spans[-1][1], self.audio)

    def test_contiguous_without_gaps_or_overlaps(self):
        spans = scene_spans(self.counts, self.timings, self.audio)
        for (_, end), (next_start, _) in zip(spans, spans[1:]):
            self.assertEqual(end, next_start)

    def test_no_zero_duration_scene(self):
        spans = scene_spans(self.counts, self.timings, self.audio)
        for start, end in spans:
            self.assertGreater(end - start, 0.01)

    def test_survives_fewer_words_than_declared(self):
        # 15 palabras declaradas, el TTS devolvió 5
        timings = make_timings([f"w{i}" for i in range(5)])
        spans = scene_spans([20, 20, 20], timings, timings[-1]["end"] + 0.5)
        self.assertEqual(len(spans), 3)
        for start, end in spans:
            self.assertGreater(end - start, 0.01)

    def test_audio_shorter_than_timings_raises(self):
        with self.assertRaises(StudioError):
            scene_spans(self.counts, self.timings, 1.0)

    def test_empty_timings_raises(self):
        with self.assertRaises(StudioError):
            scene_spans(self.counts, [], 10.0)


class TestBuildBeats(unittest.TestCase):
    def setUp(self):
        self.counts = [12, 10, 14]
        self.words = [f"palabra{i}" for i in range(36)]
        self.timings = make_timings(self.words, pauses={12: 0.5, 24: 0.4})
        self.audio = self.timings[-1]["end"] + 0.8

    def beats(self, **kwargs):
        opts = dict(max_images=45, min_beat_duration=1.1, max_beat_duration=2.6)
        opts.update(kwargs)
        return build_beats(self.counts, self.timings, self.audio, **opts)

    def test_respects_max_images(self):
        for max_images in (1, 2, 3, 5, 10, 40):
            beats = self.beats(max_images=max_images)
            self.assertLessEqual(len(beats), max_images, f"max_images={max_images}")

    def test_max_images_zero_raises(self):
        with self.assertRaises(StudioError):
            self.beats(max_images=0)

    def test_conserves_every_word(self):
        # el invariante que evita captions que no corresponden a la narración
        for max_images in (2, 5, 12, 45):
            beats = self.beats(max_images=max_images)
            spoken = " ".join(b["caption"] for b in beats).split()
            self.assertEqual(spoken, [w.upper() for w in self.words])

    def test_beats_are_contiguous_and_cover_audio(self):
        beats = self.beats()
        self.assertEqual(beats[0]["start"], 0.0)
        self.assertAlmostEqual(beats[-1]["end"], self.audio)
        for beat, nxt in zip(beats, beats[1:]):
            self.assertEqual(beat["end"], nxt["start"])

    def test_no_beat_crosses_scene_boundary(self):
        beats = self.beats(max_images=45)
        index = word_scene_index(self.counts, len(self.timings))
        consumed = 0
        for beat in beats:
            scenes = set(index[consumed : consumed + beat["words"]])
            self.assertEqual(len(scenes), 1, f"beat multiescena: {beat['caption']}")
            consumed += beat["words"]

    def test_minimum_duration_is_honoured(self):
        beats = self.beats(min_beat_duration=1.1)
        for beat in beats[:-1]:
            self.assertGreaterEqual(beat["end"] - beat["start"], 0.5)

    def test_single_word_story(self):
        timings = make_timings(["hola"])
        beats = build_beats([1], timings, timings[-1]["end"] + 0.3)
        self.assertEqual(len(beats), 1)
        self.assertEqual(beats[0]["caption"], "HOLA")
        self.assertEqual(beats[0]["start"], 0.0)

    def test_words_without_pauses(self):
        timings = make_timings([f"w{i}" for i in range(20)], gap=0.0)
        beats = build_beats([20], timings, timings[-1]["end"] + 0.2)
        spoken = " ".join(b["caption"] for b in beats).split()
        self.assertEqual(len(spoken), 20)

    def test_long_pause_in_the_middle(self):
        timings = make_timings([f"w{i}" for i in range(12)], pauses={6: 3.0})
        beats = build_beats([6, 6], timings, timings[-1]["end"] + 0.4)
        for beat, nxt in zip(beats, beats[1:]):
            self.assertEqual(beat["end"], nxt["start"])


class TestBuildCards(unittest.TestCase):
    def setUp(self):
        self.words = "el paraguas de la reina de la noche de la ciudad".split()
        self.timings = make_timings(self.words)
        self.audio = self.timings[-1]["end"] + 0.6

    def test_conserves_every_word(self):
        cards = build_cards(self.timings, self.audio)
        spoken = " ".join(c["text"] for c in cards).split()
        self.assertEqual(spoken, self.words)

    def test_position_matches_repeated_words(self):
        # con "de" y "la" repetidas, position debe apuntar a la ocurrencia real
        for card in build_cards(self.timings, self.audio):
            tokens = card["text"].split()
            for word in card["words"]:
                self.assertEqual(tokens[word["position"]], word["word"])

    def test_no_empty_cards_and_ordered(self):
        for card in build_cards(self.timings, self.audio):
            self.assertTrue(card["text"].strip())
            self.assertLess(card["start_time"], card["end_time"])

    def test_cards_do_not_flicker_between_each_other(self):
        cards = build_cards(self.timings, self.audio)
        for card, nxt in zip(cards, cards[1:]):
            self.assertEqual(card["end_time"], nxt["start_time"])

    def test_breaks_on_long_pause(self):
        # una pausa larga marca el final de una frase: la tarjeta no debe
        # arrastrar las primeras palabras de la siguiente
        words = "final de frase inicio de otra".split()
        timings = make_timings(words, pauses={3: 0.8})
        cards = build_cards(timings, timings[-1]["end"] + 0.4, max_chars=40, max_lines=2)
        self.assertGreaterEqual(len(cards), 2)
        self.assertEqual(cards[0]["text"], "final de frase")
        self.assertTrue(cards[1]["text"].startswith("inicio"))

    def test_respects_char_budget(self):
        cards = build_cards(self.timings, self.audio, max_chars=12, max_lines=1)
        for card in cards:
            self.assertLessEqual(len(card["text"]), 12 + max(len(w) for w in self.words))


class TestKaraokeStates(unittest.TestCase):
    def test_states_cover_each_word_once(self):
        words = "uno dos tres cuatro cinco".split()
        timings = make_timings(words)
        cards = build_cards(timings, timings[-1]["end"] + 0.5)
        states = karaoke_states(cards)
        self.assertEqual(len(states), len(words))
        highlighted = []
        for state in states:
            self.assertLess(state["start"], state["end"])
            tokens = state["text"].split()
            self.assertIn(state["highlight"], range(len(tokens)))
            highlighted.append(tokens[state["highlight"]])
        self.assertEqual(highlighted, words)

    def test_highlight_extends_until_next_word(self):
        timings = make_timings("uno dos tres".split(), pauses={2: 1.0})
        cards = build_cards(timings, timings[-1]["end"] + 0.3)
        states = karaoke_states(cards)
        for state, nxt in zip(states, states[1:]):
            self.assertEqual(state["end"], nxt["start"])


class TestSrt(unittest.TestCase):
    def test_timestamp_format(self):
        self.assertEqual(srt_timestamp(0), "00:00:00,000")
        self.assertEqual(srt_timestamp(1.5), "00:00:01,500")
        self.assertEqual(srt_timestamp(3661.25), "01:01:01,250")

    def test_timestamp_rounding_does_not_overflow(self):
        self.assertEqual(srt_timestamp(1.9999), "00:00:02,000")

    def test_srt_has_one_block_per_card(self):
        timings = make_timings("una dos tres cuatro".split())
        cards = build_cards(timings, timings[-1]["end"] + 0.4, max_chars=8, max_lines=1)
        srt = cards_to_srt(cards)
        self.assertEqual(srt.count("-->"), len(cards))


if __name__ == "__main__":
    unittest.main()
