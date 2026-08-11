"""Genera y consulta el backlog de temas de stories/backlog.json.

    venv/Scripts/python.exe story_topics.py --generar 30
    venv/Scripts/python.exe story_topics.py --listar
    venv/Scripts/python.exe story_topics.py --generar 20 --categoria personaje-mitologico

Se pide un LOTE y no un tema al día, por tres razones: una llamada para treinta
temas cuesta menos que treinta llamadas; con la lista delante ves el
agrupamiento (cinco emperadores romanos seguidos no se detectan de uno en uno);
y sobre todo puedes curarla. La revisión humana es la única capa que atrapa dos
temas distintos que acaban en el mismo remate.

Deliberadamente NO escribe guiones: eso es story_writer.py --siguiente.
"""

import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from studio import backlog, gemini
from studio.errors import StudioError

BACKLOG_PATH = os.path.join(ROOT, "stories", "backlog.json")
STORIES_DIR = os.path.join(ROOT, "stories")

ENFOQUE = (
    "curiosidades de la historia con inclinación hacia el dinero, el poder y "
    "las decisiones absurdas de gente con autoridad; también mitología cuando "
    "el relato dé para un giro inesperado"
)

SCHEMA = {
    "type": "object",
    "properties": {
        "temas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string"},
                    "categoria": {"type": "string"},
                    "epoca": {"type": "string"},
                    "motivo": {"type": "string"},
                    "gancho": {"type": "string"},
                },
                "required": ["titulo", "categoria", "epoca", "motivo", "gancho"],
            },
        }
    },
    "required": ["temas"],
}


def titulos_existentes():
    """Títulos de las historias ya escritas, vengan o no del backlog."""
    titulos = []
    for ruta in sorted(glob.glob(os.path.join(STORIES_DIR, "*.json"))):
        if os.path.basename(ruta) == "backlog.json":
            continue
        try:
            with open(ruta, encoding="utf-8") as f:
                datos = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(datos, dict) and datos.get("title"):
            titulos.append(datos["title"])
    return titulos


def build_prompt(cuantos, exclusiones, reparto, enfoque, categoria=None):
    pedido = (
        f"todos de la categoría '{categoria}'"
        if categoria
        else "repartidos entre las cuatro categorías"
    )
    ya_hechos = "\n".join(f"- {t}" for t in exclusiones) or "- (ninguno todavía)"

    equilibrio = ""
    if reparto["epoca"]:
        cuenta = ", ".join(f"{k}: {v}" for k, v in sorted(reparto["epoca"].items()))
        equilibrio = (
            f"\nÉPOCAS YA PUBLICADAS ({cuenta}).\n"
            f"Compensa: propón sobre todo de épocas y regiones poco representadas.\n"
        )

    return f"""Eres documentalista de un canal de reels verticales sobre {enfoque}.

Propón {cuantos} temas nuevos para futuros reels, {pedido}.

CATEGORÍAS VÁLIDAS (usa exactamente estas cadenas):
- personaje-historico
- evento-historico
- personaje-mitologico
- evento-mitologico

REGLAS:
1. Cada tema debe poder contarse en unos setenta segundos con principio, giro
   y cierre. Si necesita contexto de diez minutos para entenderse, no sirve.
2. El 'titulo' es descriptivo y concreto, no un titular de clickbait. Nombra a
   la persona o al suceso: "Calígula nombró cónsul a su caballo", no "El
   emperador más loco de la historia".
3. El 'gancho' es UNA frase: el dato concreto que hace que valga la pena. Si no
   sabes escribirlo, el tema no es bueno.
4. 'epoca' sitúa el tema con dos o tres palabras: "Roma antigua", "Japón feudal",
   "Europa siglo XIX", "Mesopotamia".
5. 'motivo' es la MORALEJA en una o dos palabras con guion, en minúsculas: lo
   que el espectador se lleva. Reutiliza estas cuando encajen —codicia, engano,
   inmortalidad, impuesto-absurdo, guerra-absurda, locura-de-poder,
   burbuja-economica, fraude, venganza, perseverancia— y solo inventa una nueva
   si ninguna sirve. Es el campo que evita publicar en la misma semana dos
   historias distintas que terminan igual: "el rey Midas" y "el dragón Fafnir"
   son temas distintos y los dos son 'codicia'.
6. Nada de temas que dependan de imágenes reales de personas vivas o de sucesos
   del último siglo con víctimas identificables.
7. VARIEDAD: no más de dos temas de la misma época o civilización, y no más de
   tres que compartan 'motivo'.

NO REPITAS NI REFORMULES NINGUNO DE ESTOS, que ya están hechos o en cola:
{ya_hechos}
{equilibrio}"""


def normaliza(candidato, ocupados):
    """Convierte lo que devuelve el modelo en una entrada válida de backlog."""
    titulo = (candidato.get("titulo") or "").strip()
    if not titulo:
        return None
    categoria = (candidato.get("categoria") or "").strip().lower()
    return {
        "slug": backlog.slug_de(titulo, ocupados),
        "titulo": titulo,
        "categoria": categoria if categoria in backlog.CATEGORIES else "",
        "epoca": (candidato.get("epoca") or "").strip(),
        "motivo": (candidato.get("motivo") or "").strip().lower(),
        "gancho": (candidato.get("gancho") or "").strip(),
        "estado": "pendiente",
        "modo": None,
        "historias": [],
        "fecha": None,
    }


def generar(args, data):
    existentes = titulos_existentes()
    exclusiones = backlog.exclusiones(data, existentes)
    prompt = build_prompt(
        args.generar, exclusiones, backlog.reparto(data), args.enfoque, args.categoria
    )

    print(f"pidiendo {args.generar} temas (excluyendo {len(exclusiones)} ya conocidos)...")
    respuesta, usage = gemini.generate_json(prompt, SCHEMA, model=args.model)

    # los slugs no pueden chocar ni entre sí, ni con el backlog, ni con un
    # archivo de historia existente: el slug nombra stories/<slug>.json
    ocupados = {t["slug"] for t in data["temas"]}
    ocupados |= {
        os.path.splitext(os.path.basename(p))[0]
        for p in glob.glob(os.path.join(STORIES_DIR, "*.json"))
    }

    aceptados, rechazados, avisos = [], [], []
    for candidato in respuesta.get("temas") or []:
        tema = normaliza(candidato, ocupados)
        if tema is None:
            continue
        choque = backlog.es_duplicado(tema["titulo"], exclusiones)
        if choque:
            rechazados.append((tema["titulo"], choque))
            continue
        for otro, compartidas in backlog.parecidos(tema["titulo"], exclusiones):
            avisos.append((tema["titulo"], otro, compartidas))
        ocupados.add(tema["slug"])
        exclusiones.append(tema["titulo"])
        aceptados.append(tema)

    # el modelo los devuelve agrupados por categoría; se reparten antes de
    # añadirlos, porque `siguiente` respeta el orden del archivo
    nuevos = backlog.anadir(data, backlog.intercalar(aceptados))
    backlog.save(BACKLOG_PATH, data)

    for titulo, choque in rechazados:
        print(f"  descartado: {titulo!r}\n              choca con {choque!r}")
    for titulo, otro, compartidas in avisos:
        print(f"  ojo: {titulo!r}\n       comparte {', '.join(compartidas)} con {otro!r}")

    print(f"\n{len(nuevos)} temas añadidos a {BACKLOG_PATH}")
    if len(nuevos) < args.generar:
        print(
            f"  (se pidieron {args.generar}; el resto se descartó por repetido "
            f"o no vino en la respuesta)"
        )
    print(f"tokens usados: {usage.get('totalTokenCount', '?')}")
    print("\nRevisa la lista, borra los flojos y reordena: el orden ES la prioridad.")
    print("Cuando te convenza:")
    print("  venv\\Scripts\\python.exe story_writer.py --siguiente --series historia-oculta")


def listar(data):
    libres = backlog.pendientes(data)
    gastados = backlog.usados(data)

    print(f"== backlog: {len(libres)} pendientes, {len(gastados)} usados ==\n")

    if libres:
        print("PENDIENTES (en orden de prioridad):")
        for i, tema in enumerate(libres, 1):
            etiquetas = " · ".join(
                v for v in (tema.get("motivo"), tema.get("epoca")) if v
            )
            print(f"  {i:>3}. {tema['titulo']}")
            if etiquetas:
                print(f"       {etiquetas}")
        print()

        agrupados = backlog.racimos(libres)
        if agrupados:
            print("MOTIVOS QUE SE REPITEN (mira que no salgan seguidos):")
            for motivo, titulos in agrupados:
                print(f"  {motivo} ({len(titulos)}): {'; '.join(titulos)}")
            print()

    if gastados:
        print("USADOS:")
        for tema in gastados:
            partes = tema.get("historias") or []
            detalle = f"{tema.get('modo')}, {len(partes)} reel(s)"
            print(f"  {tema.get('fecha') or '?':<12} {tema['titulo']}  [{detalle}]")
        print()

    r = backlog.reparto(data)
    if r["categoria"]:
        print("reparto de lo publicado:")
        for clave in ("categoria", "epoca"):
            linea = ", ".join(f"{k}: {v}" for k, v in sorted(r[clave].items()))
            print(f"  por {clave}: {linea}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--generar", type=int, metavar="N",
                        help="pide N temas nuevos al modelo y los añade al backlog")
    parser.add_argument("--listar", action="store_true", help="muestra el backlog")
    parser.add_argument("--intercalar", action="store_true",
                        help="reparte los pendientes por categoría para no repetir tema seguido")
    parser.add_argument("--categoria", choices=backlog.CATEGORIES,
                        help="restringe la generación a una categoría")
    parser.add_argument("--enfoque", default=ENFOQUE, help="temática del canal")
    parser.add_argument("--model", default="gemini-3.6-flash")
    args = parser.parse_args()

    if not (args.generar or args.listar or args.intercalar):
        parser.error("usa --generar N, --intercalar o --listar")
    if args.generar is not None and args.generar < 1:
        parser.error("--generar necesita un entero positivo")

    data = backlog.load(BACKLOG_PATH)
    if args.generar:
        generar(args, data)
    if args.intercalar:
        # los usados son historia y su orden ya no significa nada; el orden que
        # importa es el de los pendientes, que es la cola de producción
        data["temas"] = backlog.usados(data) + backlog.intercalar(backlog.pendientes(data))
        backlog.save(BACKLOG_PATH, data)
        print(f"pendientes repartidos por categoría en {BACKLOG_PATH}")
    if args.listar:
        listar(data)


if __name__ == "__main__":
    try:
        main()
    except StudioError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)
