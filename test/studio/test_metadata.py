import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import story_publish
from studio import metadata
from studio.story import validate_story

CRUDO = {
    "youtube_title": "El emperador que nombró cónsul a su caballo",
    "youtube_description": "Calígula gobernó cuatro años.\nSu caballo le sobrevivió.",
    "tiktok_caption": "Roma tuvo un emperador que quiso hacer cónsul a su caballo.",
    "instagram_caption": "La historia real de Incitato.",
    "keywords": ["historia", "antigua roma", "calígula", "curiosidades", "imperio romano"],
}


def story(story_id="demo", lang="es"):
    return validate_story(
        {
            "id": story_id,
            "format": "D",
            "lang": lang,
            "title": "Título de prueba",
            "scenes": [{"text": "hola mundo cruel", "prompt": "a fox"}],
        }
    )


class TestSlugify(unittest.TestCase):
    def test_strips_accents_and_spaces(self):
        self.assertEqual(metadata.slugify_tag("Antigua Roma"), "antiguaroma")
        self.assertEqual(metadata.slugify_tag("calígula"), "caligula")

    def test_drops_punctuation(self):
        self.assertEqual(metadata.slugify_tag("¿qué pasó?"), "quepaso")

    def test_is_deterministic(self):
        self.assertEqual(metadata.slugify_tag("Historia"), metadata.slugify_tag("Historia"))


class TestHashtags(unittest.TestCase):
    def test_youtube_always_carries_shorts(self):
        etiquetas = metadata.hashtags_for(CRUDO["keywords"], "youtube")
        self.assertIn("#Shorts", etiquetas)

    def test_respects_the_platform_maximum(self):
        muchas = [f"palabra{i}" for i in range(30)]
        for plataforma, (_, maximo) in (
            (p, metadata.PLATFORM_RULES[p]["tags"]) for p in metadata.PLATFORM_RULES
        ):
            self.assertLessEqual(len(metadata.hashtags_for(muchas, plataforma)), maximo)

    def test_no_duplicates(self):
        etiquetas = metadata.hashtags_for(["roma", "Roma", "ROMA", "historia"], "tiktok")
        self.assertEqual(len(etiquetas), len(set(etiquetas)))

    def test_skips_empty_and_too_short(self):
        etiquetas = metadata.hashtags_for(["", "a", "ab", "historia"], "tiktok")
        self.assertEqual(etiquetas, ["#historia"])

    def test_instagram_allows_more_than_tiktok(self):
        muchas = [f"tema{i}" for i in range(12)]
        self.assertGreater(
            len(metadata.hashtags_for(muchas, "instagram")),
            len(metadata.hashtags_for(muchas, "tiktok")),
        )


class TestTitleRules(unittest.TestCase):
    def test_strips_illegal_characters(self):
        # la API de YouTube devuelve 400 si el título lleva < o >
        self.assertNotIn("<", metadata.clean_title("Un <título> raro", 100))
        self.assertNotIn(">", metadata.clean_title("Un <título> raro", 100))

    def test_truncates_on_a_word_boundary(self):
        largo = "palabra " * 30
        recortado = metadata.clean_title(largo, 50)
        self.assertLessEqual(len(recortado), 50)
        self.assertFalse(recortado.endswith("palabr"))

    def test_short_titles_are_untouched(self):
        self.assertEqual(metadata.clean_title("Corto", 100), "Corto")

    def test_normalize_never_exceeds_the_api_limit(self):
        crudo = dict(CRUDO, youtube_title="x" * 300)
        meta = metadata.normalize(crudo, story(), 65.0, "es")
        self.assertLessEqual(len(meta["youtube"]["title"]), 100)


class TestNormalize(unittest.TestCase):
    def setUp(self):
        self.meta = metadata.normalize(CRUDO, story(), 68.4, "es")

    def test_carries_the_deterministic_fields(self):
        self.assertEqual(self.meta["story_id"], "demo")
        self.assertEqual(self.meta["lang"], "es")
        self.assertEqual(self.meta["duration_s"], 68.4)
        self.assertEqual(self.meta["format"], "D")

    def test_every_platform_has_text_and_tags(self):
        for plataforma in ("youtube", "tiktok", "instagram"):
            bloque = self.meta[plataforma]
            self.assertTrue(bloque["hashtags"])
            self.assertTrue(bloque.get("caption") or bloque.get("description"))

    def test_instagram_falls_back_to_the_tiktok_caption(self):
        sin_ig = {k: v for k, v in CRUDO.items() if k != "instagram_caption"}
        meta = metadata.normalize(sin_ig, story(), 60.0, "es")
        self.assertEqual(meta["instagram"]["caption"], CRUDO["tiktok_caption"])

    def test_key_changes_with_the_script(self):
        otra = validate_story(
            {"id": "demo", "format": "D", "scenes": [{"text": "otro texto", "prompt": "a fox"}]}
        )
        self.assertNotEqual(metadata.key_for(story(), "es"), metadata.key_for(otra, "es"))

    def test_key_changes_with_the_language(self):
        self.assertNotEqual(metadata.key_for(story(), "es"), metadata.key_for(story(), "en"))


class TestRenderCaption(unittest.TestCase):
    def setUp(self):
        self.meta = metadata.normalize(CRUDO, story(), 65.0, "es")

    def test_is_only_text_and_hashtags(self):
        # se pega tal cual en el móvil: nada de "Título:" ni markdown
        texto = metadata.render_caption(self.meta, "tiktok")
        self.assertNotIn("Título", texto)
        self.assertNotIn("#Shorts", texto)
        self.assertTrue(texto.startswith(CRUDO["tiktok_caption"][:20]))

    def test_youtube_includes_shorts(self):
        self.assertIn("#Shorts", metadata.render_caption(self.meta, "youtube"))

    def test_respects_the_length_limit(self):
        crudo = dict(CRUDO, tiktok_caption="x" * 5000)
        meta = metadata.normalize(crudo, story(), 65.0, "es")
        self.assertLessEqual(len(metadata.render_caption(meta, "tiktok")), 2200)


class TestYoutubeBody(unittest.TestCase):
    def body(self, **kwargs):
        meta = metadata.normalize(CRUDO, story(), 65.0, "es")
        return metadata.youtube_body(meta, **kwargs)

    def test_declares_made_for_kids_explicitly(self):
        self.assertIs(self.body()["status"]["selfDeclaredMadeForKids"], False)

    def test_tags_have_no_hash(self):
        for etiqueta in self.body()["snippet"]["tags"]:
            self.assertFalse(etiqueta.startswith("#"))

    def test_language_follows_the_story(self):
        self.assertEqual(self.body()["snippet"]["defaultAudioLanguage"], "es-MX")
        meta = metadata.normalize(CRUDO, story(lang="en"), 65.0, "en")
        cuerpo = metadata.youtube_body(meta)
        self.assertEqual(cuerpo["snippet"]["defaultAudioLanguage"], "en-US")

    def test_scheduling_forces_private(self):
        cuerpo = self.body(privacy="public", publish_at="2026-08-20T18:00:00Z")
        self.assertEqual(cuerpo["status"]["privacyStatus"], "private")
        self.assertEqual(cuerpo["status"]["publishAt"], "2026-08-20T18:00:00Z")

    def test_default_is_private(self):
        self.assertEqual(self.body()["status"]["privacyStatus"], "private")


class TestLedger(unittest.TestCase):
    def lineas(self, *entradas):
        return [json.dumps(e) for e in entradas]

    def test_empty_ledger_allows_upload(self):
        motivo, _ = metadata.already_uploaded("abc", "demo", [])
        self.assertIsNone(motivo)

    def test_same_file_is_detected(self):
        lineas = self.lineas({"sha256": "abc", "story_id": "demo", "video_id": "V1"})
        motivo, entrada = metadata.already_uploaded("abc", "demo", lineas)
        self.assertEqual(motivo, "mismo")
        self.assertEqual(entrada["video_id"], "V1")

    def test_rerender_of_the_same_story_is_detected(self):
        lineas = self.lineas({"sha256": "viejo", "story_id": "demo", "video_id": "V1"})
        motivo, entrada = metadata.already_uploaded("nuevo", "demo", lineas)
        self.assertEqual(motivo, "rerender")
        self.assertEqual(entrada["video_id"], "V1")

    def test_other_stories_do_not_block(self):
        lineas = self.lineas({"sha256": "otro", "story_id": "otra", "video_id": "V9"})
        motivo, _ = metadata.already_uploaded("abc", "demo", lineas)
        self.assertIsNone(motivo)

    def test_corrupt_lines_are_ignored(self):
        lineas = ["{roto", "", json.dumps({"sha256": "abc", "story_id": "demo"})]
        motivo, _ = metadata.already_uploaded("abc", "demo", lineas)
        self.assertEqual(motivo, "mismo")


class CacheDeBorradores(unittest.TestCase):
    """Un borrador es un fallo, no un resultado: no debe cachearse.

    Pasó en producción el 2026-08-10: con la API sin créditos se escribió un
    metadata.json con draft=true, y días después --pack lo reutilizó como si
    fuera bueno. El vídeo se subió con la primera frase del guion por
    descripción, y el permiso 'youtube.upload' no permite corregirlo después.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.relato = story()
        self.clave = metadata.key_for(self.relato, "es")
        self.llamadas = []

    def escribir(self, draft):
        carpeta = os.path.join(self.dir, "publish")
        os.makedirs(carpeta, exist_ok=True)
        guardado = {"key": self.clave, "draft": draft, "youtube": {"title": "cacheado"}}
        with open(os.path.join(carpeta, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(guardado, f)

    def ejecutar(self):
        def falso_build(relato, duracion, lang, model):
            self.llamadas.append(lang)
            return {"key": self.clave, "youtube": {"title": "recien generado"}}, True

        with mock.patch.object(story_publish, "build_metadata", falso_build), \
             mock.patch.object(story_publish.ffmpeg, "media_duration", lambda _: 65.0):
            meta, _ = story_publish.metadata_for(self.relato, self.dir, "es", "m")
        return meta

    def test_borrador_se_regenera(self):
        self.escribir(draft=True)
        meta = self.ejecutar()
        self.assertEqual(len(self.llamadas), 1, "debería haber vuelto a pedirlos")
        self.assertEqual(meta["youtube"]["title"], "recien generado")
        self.assertFalse(meta["draft"])

    def test_completo_se_reutiliza(self):
        self.escribir(draft=False)
        meta = self.ejecutar()
        self.assertEqual(self.llamadas, [], "no debería haber llamado al modelo")
        self.assertEqual(meta["youtube"]["title"], "cacheado")


if __name__ == "__main__":
    unittest.main()
