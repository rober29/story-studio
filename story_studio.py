"""Story Studio: genera reels verticales a partir de una historia en JSON.

    venv/Scripts/python.exe story_studio.py --story stories/markov.json

Formatos:
  A  caricatura en franja 16:9 sobre fondo borroso, con banner de serie
  B  una imagen por beat con el texto horneado encima
  C  slideshow cinemático con Ken Burns y títulos de capítulo

Las fases (assets -> visuals -> render -> verify) corren en procesos separados
para que la memoria se libere entre ellas. Cada una es reanudable: lo que ya
está bien generado no se rehace, y lo que dependía de algo que cambió sí.

Imágenes de Pollinations y voz de Edge TTS: sin claves de API.
"""

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from studio.errors import StudioError
from studio.story import load_story

PHASES = ("assets", "visuals", "render", "verify")


def run_phase(name, story, force=False, quality="draft", budget=None, assume_yes=False,
              no_adopt=False):
    from studio import pipeline

    opts = dict(force=force, quality=quality, budget=budget, assume_yes=assume_yes)
    if name == "assets":
        return pipeline.phase_assets(story, no_adopt=no_adopt, **opts)
    if name == "visuals":
        return pipeline.phase_visuals(story, **opts)
    if name == "render":
        return pipeline.phase_render(story, **opts)
    if name == "verify":
        from studio.verify import verify_story

        report = verify_story(story)
        if not report["passed"]:
            raise StudioError(
                "el video no pasó la verificación; revisa el detalle de arriba y "
                f"el informe en storage/tasks/story-{story['id']}/verify/report.json"
            )
        return report
    raise StudioError(f"fase desconocida: {name}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--story", required=True, help="ruta al JSON de la historia")
    parser.add_argument("--phase", default="all", choices=(*PHASES, "all", "plan"))
    parser.add_argument("--force", action="store_true",
                        help="ignora la caché de la fase indicada")
    parser.add_argument("--prune", action="store_true",
                        help="borra las imágenes que ya nadie referencia")
    parser.add_argument("--quality", default="draft", choices=("draft", "final"),
                        help="draft usa generadores gratuitos; final, los de pago")
    parser.add_argument("--budget", type=float, default=2.0,
                        help="tope de gasto en dólares para esta corrida")
    parser.add_argument("--yes", action="store_true",
                        help="no preguntar antes de gastar ni de borrar")
    parser.add_argument("--no-adopt", action="store_true",
                        help="no heredar imágenes de la historia de 'images_from'")
    args = parser.parse_args(argv)

    story_path = os.path.abspath(args.story)
    story = load_story(story_path)

    if args.prune:
        from studio import pipeline

        pipeline.prune(story, assume_yes=args.yes)
        return 0

    if args.phase == "plan":
        from studio import lint, pipeline

        # Antes del coste, porque es cuando todavía sale gratis arreglarlo. Cada
        # aviso viene de un fallo que se descubrió mirando un reel ya pagado.
        avisos = lint.lint_story(story)
        if avisos:
            print(f"revisión del guion: {len(avisos)} aviso(s)")
            for aviso in avisos:
                print(aviso)
            print()
        pipeline.phase_plan(story, quality=args.quality)
        return 0

    if args.phase != "all":
        run_phase(args.phase, story, force=args.force, quality=args.quality,
                  budget=args.budget, assume_yes=args.yes, no_adopt=args.no_adopt)
        return 0

    # cada fase en su propio proceso: la memoria vuelve al sistema entre ellas
    for phase in PHASES:
        print(f"\n######## FASE: {phase} ########")
        cmd = [sys.executable, os.path.abspath(__file__),
               "--story", story_path, "--phase", phase,
               "--quality", args.quality, "--budget", str(args.budget)]
        if args.force:
            cmd.append("--force")
        if args.yes:
            cmd.append("--yes")
        if args.no_adopt:
            cmd.append("--no-adopt")
        result = subprocess.run(cmd, cwd=ROOT)
        if result.returncode != 0:
            print(f"\nla fase '{phase}' falló (código {result.returncode})")
            return result.returncode
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except StudioError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\ninterrumpido", file=sys.stderr)
        sys.exit(130)
