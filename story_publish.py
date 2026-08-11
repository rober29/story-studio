"""Empaqueta un reel para publicarlo y lo sube a YouTube.

    venv/Scripts/python.exe story_publish.py --story stories/faros.json --pack
    venv/Scripts/python.exe story_publish.py --story stories/faros.json --youtube --dry-run

El empaquetado deja una carpeta lista para publicar desde el móvil: el MP4, la
portada y un archivo de texto por plataforma que contiene EXACTAMENTE lo que hay
que pegar, sin rótulos ni markdown. YouTube se sube solo; TikTok e Instagram se
publican a mano porque su API exige auditorías de semanas.
"""

import argparse
import datetime
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from studio import ffmpeg, metadata
from studio.cache import write_atomic
from studio.errors import StudioError
from studio.pipeline import story_paths
from studio.story import find_video, load_story

PUBLISH_DIR = os.path.join(ROOT, "storage", "publish")
LEDGER = os.path.join(PUBLISH_DIR, "uploaded.jsonl")


def load_report(task_dir):
    ruta = os.path.join(task_dir, "verify", "report.json")
    if not os.path.isfile(ruta):
        raise StudioError(
            "falta el informe de verificación; ejecuta primero "
            "story_studio.py --phase verify"
        )
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def build_metadata(story, duration, lang, model):
    """Pide los metadatos al modelo. Si no hay API, deja un borrador utilizable."""
    from studio import gemini

    try:
        crudo, _ = gemini.generate_json(
            metadata.build_prompt(story, duration, lang), metadata.SCHEMA, model=model
        )
        return metadata.normalize(crudo, story, duration, lang), True
    except StudioError as e:
        print(f"[aviso] no se pudieron generar los metadatos con el modelo: {e}")
        print("[aviso] se escribe un BORRADOR a partir del guion; revísalo antes de publicar")
        primera = story["scenes"][0]["text"]
        crudo = {
            "youtube_title": story.get("title", story["id"]),
            "youtube_description": primera,
            "tiktok_caption": primera,
            "keywords": [],
        }
        return metadata.normalize(crudo, story, duration, lang), False


def metadata_for(story, task_dir, lang, model, force=False):
    """Metadatos cacheados por clave: cambian si cambia el guion o el idioma."""
    carpeta = os.path.join(task_dir, "publish")
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, "metadata.json")
    esperada = metadata.key_for(story, lang)

    if not force and os.path.isfile(ruta):
        with open(ruta, encoding="utf-8") as f:
            guardados = json.load(f)
        # 'draft' marca unos textos de emergencia escritos porque el modelo no
        # respondió. NO son un resultado válido que cachear: reutilizarlos hace
        # que una caída puntual de la API se hornee en todo lo que se publique
        # después. Se regeneran siempre; si el modelo sigue caído, vuelve a
        # salir un borrador y no se ha perdido nada.
        if guardados.get("key") == esperada and not guardados.get("draft"):
            return guardados, ruta

    duracion = ffmpeg.media_duration(os.path.join(task_dir, "audio.mp3"))
    meta, completo = build_metadata(story, duracion, lang, model)
    meta["draft"] = not completo
    write_atomic(ruta, json.dumps(meta, ensure_ascii=False, indent=1), mode="w")
    return meta, ruta


def package(story, meta, task_dir, assume_yes=False):
    """Carpeta lista para publicar. Un archivo = un pegado."""
    informe = load_report(task_dir)
    if not informe.get("passed") and not assume_yes:
        raise StudioError(
            "el reel no pasó la verificación: publicarlo es justo lo que verify "
            "existe para evitar. Revisa el informe o usa --yes si lo asumes"
        )

    video = find_video(task_dir, story)
    if not os.path.isfile(video):
        raise StudioError(f"no existe {video}; genera el reel primero")

    fecha = datetime.date.today().isoformat()
    destino = os.path.join(PUBLISH_DIR, f"{fecha}_{story['id']}_{meta['lang']}")
    os.makedirs(destino, exist_ok=True)

    shutil.copy2(video, os.path.join(destino, f"{story['id']}.mp4"))
    ffmpeg.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", "1.2", "-i", video,
         "-frames:v", "1", os.path.join(destino, "cover.jpg")],
        "extraer la portada",
    )

    # cada archivo contiene SOLO lo que se pega: se abre en el móvil, se
    # selecciona todo y se copia. Un rótulo dentro obligaría a seleccionar a mano
    write_atomic(os.path.join(destino, "tiktok.txt"),
                 metadata.render_caption(meta, "tiktok"), mode="w")
    write_atomic(os.path.join(destino, "instagram.txt"),
                 metadata.render_caption(meta, "instagram"), mode="w")
    write_atomic(os.path.join(destino, "youtube-title.txt"),
                 meta["youtube"]["title"], mode="w")
    write_atomic(os.path.join(destino, "youtube-description.txt"),
                 metadata.render_caption(meta, "youtube"), mode="w")
    write_atomic(os.path.join(destino, "youtube.json"),
                 json.dumps(meta, ensure_ascii=False, indent=1), mode="w")

    aviso = ""
    if meta.get("draft"):
        aviso = ("\n  OJO: los textos son un borrador automático porque el modelo "
                 "no respondió. Reescríbelos antes de publicar.\n")
    write_atomic(
        os.path.join(destino, "LEE.txt"),
        f"""{story.get('title', story['id'])}  ({meta['duration_s']:.0f}s, {meta['lang']})
{aviso}
1. YouTube: se sube solo con  story_publish.py --story <json> --youtube
2. TikTok:  abre tiktok.txt, copia todo y pega. Es donde más se cobra.
3. Instagram: abre instagram.txt, copia todo y pega.

El vídeo es {story['id']}.mp4 y la portada sugerida, cover.jpg
""",
        mode="w",
    )
    return destino


def upload_youtube(story, meta, destino, channel, privacy, publish_at,
                   category, dry_run=False, force=False):
    from studio import youtube

    video = os.path.join(destino, f"{story['id']}.mp4")
    cuerpo = metadata.youtube_body(meta, privacy=privacy, publish_at=publish_at,
                                   category=category)

    if dry_run:
        print(f"canal: {channel}")
        print(json.dumps(cuerpo, ensure_ascii=False, indent=1))
        print(f"\nvídeo: {video}")
        print("(--dry-run: no se ha subido nada)")
        return None

    digest = metadata.sha256_of(video)
    lineas = []
    if os.path.isfile(LEDGER):
        with open(LEDGER, encoding="utf-8") as f:
            lineas = f.readlines()
    motivo, previo = metadata.already_uploaded(digest, story["id"], lineas)
    if motivo and not force:
        enlace = f"https://youtu.be/{previo.get('video_id')}"
        if motivo == "mismo":
            raise StudioError(
                f"este mismo archivo ya se subió: {enlace} "
                f"({previo.get('uploaded_at')}). Usa --force-upload para duplicarlo"
            )
        raise StudioError(
            f"esta historia ya se subió como {enlace}, pero el vídeo actual es "
            f"distinto (lo has vuelto a renderizar). El anterior sigue publicado: "
            f"bórralo a mano si toca, y luego usa --force-upload"
        )

    video_id = youtube.upload(video, cuerpo, channel)
    entrada = {
        "video_id": video_id,
        "story_id": story["id"],
        "lang": meta["lang"],
        "channel": channel,
        "privacy": cuerpo["status"]["privacyStatus"],
        "sha256": digest,
        "uploaded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    os.makedirs(PUBLISH_DIR, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
    write_atomic(os.path.join(destino, ".published.json"),
                 json.dumps({"youtube": entrada}, ensure_ascii=False, indent=1), mode="w")

    print(f"\nsubido: https://youtu.be/{video_id}  ({cuerpo['status']['privacyStatus']})")
    return video_id


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--story", help="ruta al JSON de la historia")
    parser.add_argument("--pack", action="store_true", help="preparar la carpeta de publicación")
    parser.add_argument("--youtube", action="store_true", help="subir a YouTube")
    parser.add_argument("--channel", default="es", help="canal (nombra el token guardado)")
    parser.add_argument("--privacy", default="private",
                        choices=("private", "unlisted", "public"))
    parser.add_argument("--publish-at", help="fecha ISO8601 para programarlo")
    parser.add_argument("--category", default="27", help="27 educación, 24 entretenimiento")
    parser.add_argument("--model", default="gemini-3.6-flash")
    parser.add_argument("--authorize", action="store_true", help="autorizar un canal")
    parser.add_argument("--no-browser", action="store_true",
                        help="con --authorize, solo imprime la URL en vez de abrir el navegador")
    parser.add_argument("--check-auth", action="store_true", help="comprobar el token")
    parser.add_argument("--dry-run", action="store_true", help="mostrar sin subir")
    parser.add_argument("--force-meta", action="store_true", help="regenerar metadatos")
    parser.add_argument("--force-upload", action="store_true", help="subir aunque esté duplicado")
    parser.add_argument("--yes", action="store_true", help="empaquetar aunque verify falle")
    args = parser.parse_args()

    from studio import youtube

    if args.authorize:
        youtube.authorize(args.channel, abrir_navegador=not args.no_browser)
        return
    if args.check_auth:
        youtube.check_auth(args.channel)
        return
    if not args.story:
        parser.error("indica --story")

    story = load_story(os.path.abspath(args.story))
    task_dir, _ = story_paths(story)
    lang = story.get("lang", "es")
    meta, ruta_meta = metadata_for(story, task_dir, lang, args.model, force=args.force_meta)
    print(f"metadatos: {ruta_meta}")
    print(f"  título: {meta['youtube']['title']}")
    print(f"  etiquetas: {' '.join(meta['youtube']['hashtags'])}")

    destino = package(story, meta, task_dir, assume_yes=args.yes)
    print(f"paquete: {destino}")

    if args.youtube and meta.get("draft") and not args.yes:
        # subir es irreversible con nuestro permiso: el scope es solo de subida,
        # así que un título de emergencia no se puede corregir después por API.
        raise StudioError(
            "los metadatos son un borrador automático (el modelo no respondió), "
            "y con el permiso 'youtube.upload' no se pueden corregir una vez "
            "subido el vídeo.\n"
            "  Reintenta cuando la API responda, o usa --yes para subir igual "
            "y corregir el título a mano en YouTube Studio."
        )

    if args.youtube:
        upload_youtube(story, meta, destino, args.channel, args.privacy,
                       args.publish_at, args.category,
                       dry_run=args.dry_run, force=args.force_upload)
    elif not args.pack:
        print("\n(usa --youtube para subirlo, o --pack para dejarlo solo empaquetado)")


if __name__ == "__main__":
    try:
        main()
    except StudioError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)
