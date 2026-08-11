import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from studio.draw import (
    VIDEO_H,
    VIDEO_W,
    hex_to_rgb,
    load_font,
    render_caption,
    render_karaoke,
    split_emoji,
)
from studio.errors import StudioError
from studio.providers import PollinationsImages


def pollinations_url(prompt, style, width, height, seed):
    return PollinationsImages().url(prompt, style, width, height, seed)


def text_bbox(image):
    """Caja del contenido no transparente."""
    return image.getbbox()


class TestSplitEmoji(unittest.TestCase):
    def test_emoji_with_variation_selector(self):
        # el 🏛️ real es U+1F3DB + U+FE0F: si el selector se queda en el texto,
        # aparece un carácter suelto delante del título
        emoji, label = split_emoji("\U0001F3DB️ Tenochtitlan: la joya")
        self.assertEqual(emoji, "\U0001F3DB️")
        self.assertEqual(label, "Tenochtitlan: la joya")

    def test_plain_emoji(self):
        emoji, label = split_emoji("\U0001F525 El sitio final")
        self.assertEqual(emoji, "\U0001F525")
        self.assertEqual(label, "El sitio final")

    def test_spanish_punctuation_is_not_emoji(self):
        for text in ("¡Increible!", "«La cita»", "— Guerra", '"Comillas"'):
            emoji, label = split_emoji(text)
            self.assertEqual(emoji, "", f"{text!r} no debería tratarse como emoji")
            self.assertEqual(label, text)

    def test_text_without_emoji(self):
        self.assertEqual(split_emoji("1519: La llegada"), ("", "1519: La llegada"))

    def test_empty(self):
        self.assertEqual(split_emoji(""), ("", ""))


class TestFonts(unittest.TestCase):
    def test_load_font_returns_scalable_font(self):
        font = load_font(60)
        self.assertGreater(font.getlength("MMMM"), font.getlength("MM"))

    def test_size_is_honoured(self):
        # una fuente bitmap ignoraría el tamaño y daría el mismo ancho
        self.assertGreater(load_font(80).getlength("hola"), load_font(30).getlength("hola"))


class TestHexToRgb(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(hex_to_rgb("#FFFF00"), (255, 255, 0))
        self.assertEqual(hex_to_rgb("fff"), (255, 255, 255))

    def test_invalid_raises(self):
        for bad in ("white", "#12", "#GGGGGG"):
            with self.assertRaises(StudioError, msg=bad):
                hex_to_rgb(bad)


class TestRenderCaption(unittest.TestCase):
    def test_width_matches_canvas(self):
        self.assertEqual(render_caption("HOLA MUNDO").size[0], VIDEO_W)

    def test_fits_within_safe_width(self):
        image = render_caption("DE PRONTO SIENTE UN PINCHAZO EN LA PIERNA")
        left, _, right, _ = text_bbox(image)
        self.assertGreaterEqual(left, 0)
        self.assertLessEqual(right, VIDEO_W)

    def test_long_caption_wraps_instead_of_clipping(self):
        short = render_caption("UNO DOS")
        long = render_caption("EL CASO DEL PARAGUAS BULGARO SIGUE SIENDO UNO DE LOS MAS AUDACES")
        self.assertGreater(long.size[1], short.size[1], "debería haber usado dos líneas")
        left, _, right, _ = text_bbox(long)
        self.assertGreaterEqual(left, 0)
        self.assertLessEqual(right, VIDEO_W)

    def test_optical_center_is_stable(self):
        # el bug original: al encoger la fuente, el texto se desplazaba hacia
        # arriba porque el lienzo se dimensionaba con el tamaño sin reducir
        for text in ("UNO", "UNA FRASE ALGO MAS LARGA", "OTRA FRASE BASTANTE MAS LARGA TODAVIA"):
            image = render_caption(text)
            _, top, _, bottom = text_bbox(image)
            center = (top + bottom) / 2
            self.assertAlmostEqual(center, image.size[1] / 2, delta=image.size[1] * 0.10)

    def test_impossible_caption_raises(self):
        with self.assertRaises(StudioError):
            render_caption("PALABRA" * 60, max_lines=1)

    def test_empty_caption_raises(self):
        with self.assertRaises(StudioError):
            render_caption("   ")


class TestRenderKaraoke(unittest.TestCase):
    def test_canvas_is_full_video_size(self):
        self.assertEqual(render_karaoke("una dos tres", 1).size, (VIDEO_W, VIDEO_H))

    def test_highlight_paints_yellow(self):
        image = render_karaoke("una dos tres", 1, highlight_fill="#FFFF00")
        pixels = image.convert("RGB").getdata()
        yellow = sum(1 for r, g, b in pixels if r > 200 and g > 200 and b < 80)
        self.assertGreater(yellow, 50, "no se pintó ninguna palabra en amarillo")

    def test_stays_inside_the_frame(self):
        for position, custom in (("bottom", 85.0), ("top", 5.0), ("custom", 62.0)):
            image = render_karaoke(
                "una frase con varias palabras para envolver en dos lineas",
                3, position=position, custom_position=custom,
            )
            left, top, right, bottom = text_bbox(image)
            self.assertGreaterEqual(top, 0)
            self.assertLessEqual(bottom, VIDEO_H)
            self.assertGreaterEqual(left, 0)
            self.assertLessEqual(right, VIDEO_W)

    def test_custom_position_extreme_is_clamped(self):
        image = render_karaoke("hola mundo", 0, position="custom", custom_position=100.0)
        _, _, _, bottom = text_bbox(image)
        self.assertLessEqual(bottom, VIDEO_H)

    def test_invalid_highlight_raises(self):
        with self.assertRaises(StudioError):
            render_karaoke("una dos", 5)


class TestPollinationsUrl(unittest.TestCase):
    def test_slash_is_escaped(self):
        # con safe='/' por defecto la barra rompía la ruta de la petición
        url = pollinations_url("black/white portrait", "cinematic", 1080, 1920, 7)
        self.assertNotIn("black/white", url)
        self.assertIn("%2F", url)

    def test_carries_size_and_seed(self):
        url = pollinations_url("a fox", "cinematic", 1080, 1920, 42)
        self.assertIn("width=1080", url)
        self.assertIn("height=1920", url)
        self.assertIn("seed=42", url)

    def test_accents_are_encoded(self):
        url = pollinations_url("un león", "estilo", 720, 1280, 1)
        self.assertNotIn("ó", url)


if __name__ == "__main__":
    unittest.main()
