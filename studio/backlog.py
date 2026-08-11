"""Backlog de temas: qué contar mañana y qué ya se contó.

El pipeline sabe producir un reel a partir de un tema, pero no sabía de dónde
sacar el tema ni recordaba cuáles había gastado. Este módulo es ese registro.

Módulo puro, misma convención que studio/pacing.py y studio/metadata.py: solo
stdlib, sin red y sin tocar Gemini, para poder testearlo en frío. Las llamadas
al modelo viven en story_topics.py.

El archivo vive en stories/backlog.json y NO en storage/ a propósito: es
contenido curado que se versiona en git y cuyo valor está en que lo revises.
storage/ es material regenerable y está en .gitignore.
"""

import datetime
import json
import os
import unicodedata

from studio.cache import write_json
from studio.errors import StudioError
from studio.metadata import slugify_tag

BACKLOG_VERSION = 1

CATEGORIES = (
    "personaje-historico",
    "evento-historico",
    "personaje-mitologico",
    "evento-mitologico",
)

# Un tema se consume una sola vez, y al hacerlo se anota CÓMO: un reel único o
# una serie de varias partes. Sin esa distinción no se puede saber si un tema ya
# dio de sí todo lo que tenía o solo se rozó.
MODES = ("corto", "extendido")

# Palabras que no distinguen un tema de otro. Van en los dos idiomas porque los
# títulos del backlog pueden estar en cualquiera de ellos.
STOPWORDS = frozenset(
    """
    el la los las un una unos unas de del al a ante bajo con contra desde en
    entre hacia hasta para por segun sin sobre tras y o u e ni que quien cual
    cuyo como cuando donde mas mientras muy no se su sus lo les me te nos os
    the a an of in on at to for from by with and or but that which who whose
    how when where why was were is are be been his her its their it he she they
    """.split()
)

# Longitud mínima para considerar que una palabra identifica una entidad. Por
# debajo de esto ("rey", "mar", "oro") aparece en demasiados temas distintos.
MIN_ENTIDAD = 6


def empty():
    """Backlog vacío pero válido."""
    return {"version": BACKLOG_VERSION, "temas": []}


def load(path):
    """Lee el backlog. Un archivo inexistente es un backlog vacío, no un error."""
    if not os.path.isfile(path):
        return empty()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise StudioError(f"{path} no es JSON válido: línea {e.lineno}, {e.msg}")
    return validate(data, source=path)


def save(path, data):
    """Escritura atómica: nunca deja un backlog a medias si se interrumpe."""
    write_json(path, validate(data, source=path))


def validate(data, source="<backlog>"):
    """Comprueba la estructura y rellena lo que falte. Devuelve el mismo dict."""
    if not isinstance(data, dict):
        raise StudioError(f"{source}: la raíz debe ser un objeto JSON")
    if data.get("version") != BACKLOG_VERSION:
        raise StudioError(
            f"{source}: 'version' debe ser {BACKLOG_VERSION}, "
            f"recibido {data.get('version')!r}"
        )
    temas = data.get("temas")
    if not isinstance(temas, list):
        raise StudioError(f"{source}: 'temas' debe ser una lista")

    vistos = set()
    for i, tema in enumerate(temas):
        donde = f"{source}: tema {i}"
        if not isinstance(tema, dict):
            raise StudioError(f"{donde} debe ser un objeto")
        for campo in ("slug", "titulo"):
            valor = tema.get(campo)
            if not isinstance(valor, str) or not valor.strip():
                raise StudioError(f"{donde}: falta '{campo}' (string no vacío)")
        if tema["slug"] in vistos:
            raise StudioError(f"{donde}: 'slug' duplicado ({tema['slug']!r})")
        vistos.add(tema["slug"])
        # el slug nombra un archivo bajo stories/, no puede escaparse de ahí
        if tema["slug"] != os.path.basename(tema["slug"]) or tema["slug"] in (".", ".."):
            raise StudioError(
                f"{donde}: 'slug' no puede contener separadores de ruta "
                f"(recibido: {tema['slug']!r})"
            )
        if tema.get("categoria") and tema["categoria"] not in CATEGORIES:
            raise StudioError(
                f"{donde}: 'categoria' debe ser una de {CATEGORIES}, "
                f"recibido {tema['categoria']!r}"
            )
        estado = tema.setdefault("estado", "pendiente")
        if estado not in ("pendiente", "usado"):
            raise StudioError(f"{donde}: 'estado' debe ser pendiente o usado")
        modo = tema.setdefault("modo", None)
        if modo is not None and modo not in MODES:
            raise StudioError(f"{donde}: 'modo' debe ser uno de {MODES}, recibido {modo!r}")
        if estado == "usado" and not modo:
            raise StudioError(f"{donde}: un tema usado tiene que declarar 'modo'")
        tema.setdefault("epoca", "")
        # Etiqueta libre, no una enumeración cerrada: obligar al modelo a elegir
        # de una lista fija le hace forzar encajes malos, y el reparto funciona
        # con cualquier cadena mientras se repita entre temas parecidos.
        tema.setdefault("motivo", "")
        tema.setdefault("gancho", "")
        tema.setdefault("historias", [])
        tema.setdefault("fecha", None)
        if not isinstance(tema["historias"], list):
            raise StudioError(f"{donde}: 'historias' debe ser una lista de ids")
    return data


# --------------------------------------------------------------------------
# Consulta
# --------------------------------------------------------------------------

def pendientes(data):
    return [t for t in data["temas"] if t["estado"] == "pendiente"]


def usados(data):
    return [t for t in data["temas"] if t["estado"] == "usado"]


def siguiente(data):
    """El primer tema pendiente. El orden del archivo ES la prioridad.

    Que sea el primero y no uno al azar es deliberado: reordenar el archivo a
    mano tiene que ser la forma de decidir qué va mañana.
    """
    libres = pendientes(data)
    if not libres:
        raise StudioError(
            "no quedan temas pendientes en el backlog.\n"
            "  Genera más con:  story_topics.py --generar 30"
        )
    return libres[0]


def buscar(data, slug):
    for tema in data["temas"]:
        if tema["slug"] == slug:
            return tema
    raise StudioError(f"no hay ningún tema con slug {slug!r} en el backlog")


def reparto(data):
    """Cuántos temas hay por categoría y por época, contando solo los usados.

    Sirve para ver el agrupamiento: cinco emperadores romanos seguidos no se
    detectan leyendo un tema, se detectan mirando el reparto.
    """
    cuentas = {"categoria": {}, "epoca": {}, "motivo": {}}
    for tema in usados(data):
        for eje in cuentas:
            valor = tema.get(eje) or f"(sin {eje})"
            cuentas[eje][valor] = cuentas[eje].get(valor, 0) + 1
    return cuentas


def racimos(temas, minimo=2):
    """Motivos que se repiten en la lista dada, del más repetido al menos.

    Es lo que hay que mirar al curar un lote: el duplicado que importa no es el
    tema repetido —eso lo caza es_duplicado— sino el motivo repetido, que hace
    que dos historias distintas se sientan la misma.
    """
    cuenta = {}
    for tema in temas:
        clave = tema.get("motivo") or ""
        if clave:
            cuenta.setdefault(clave, []).append(tema["titulo"])
    return sorted(
        ((m, t) for m, t in cuenta.items() if len(t) >= minimo),
        key=lambda par: (-len(par[1]), par[0]),
    )


# --------------------------------------------------------------------------
# Mutación
# --------------------------------------------------------------------------

def marcar_usado(data, slug, modo, historias, fecha=None):
    """Consume un tema. Se llama SOLO cuando el guion ya se escribió con éxito.

    Si el modelo falla la validación, el tema tiene que seguir pendiente: un
    fallo del generador no debe gastar una idea.
    """
    if modo not in MODES:
        raise StudioError(f"'modo' debe ser uno de {MODES}, recibido {modo!r}")
    if not historias:
        raise StudioError("marcar_usado necesita al menos un id de historia")
    tema = buscar(data, slug)
    if tema["estado"] == "usado":
        raise StudioError(
            f"el tema {slug!r} ya se usó el {tema.get('fecha')} "
            f"como {tema.get('modo')} en {', '.join(tema.get('historias') or [])}"
        )
    tema["estado"] = "usado"
    tema["modo"] = modo
    tema["historias"] = list(historias)
    tema["fecha"] = fecha or datetime.date.today().isoformat()
    return tema


def clave_reparto(tema):
    """Eje por el que separar los temas. El motivo manda sobre la categoría.

    Revisando el primer lote real salió claro que la categoría no es el eje que
    importa: 'El rey Midas' y 'El monstruo Fafnir' son los dos personajes
    mitológicos —misma categoría— y cuentan la MISMA historia, la codicia por
    el oro arruinando a alguien. Categorías distintas pueden repetir motivo y
    categorías iguales pueden no repetirlo.

    Lo que el espectador percibe como "esto ya lo vi" es el motivo, no la
    taxonomía. Por eso se separa por ahí, y solo se cae a la categoría cuando
    el tema no lo declara.
    """
    return tema.get("motivo") or tema.get("categoria") or ""


def intercalar(temas, clave=clave_reparto):
    """Reordena en round-robin, sin perder ninguno.

    Medido con el primer lote real: al pedir treinta temas "repartidos entre
    las cuatro categorías", el modelo los devuelve AGRUPADOS —ocho monarcas,
    luego ocho sucesos, luego catorce mitos—. Como `siguiente` respeta el orden
    del archivo, eso publicaría ocho reyes seguidos.

    Pedírselo al modelo en el prompt no es fiable; reordenar aquí sí. Dentro de
    cada grupo se conserva el orden original, que es donde el modelo sí aporta
    criterio.
    """
    grupos = {}
    for tema in temas:
        grupos.setdefault(clave(tema), []).append(tema)

    orden = sorted(grupos, key=lambda c: (-len(grupos[c]), c))
    salida = []
    while any(grupos[c] for c in orden):
        for categoria in orden:
            if grupos[categoria]:
                salida.append(grupos[categoria].pop(0))
    return salida


def anadir(data, temas):
    """Añade candidatos al final, saltando los que ya existen por slug."""
    existentes = {t["slug"] for t in data["temas"]}
    nuevos = []
    for tema in temas:
        if tema["slug"] in existentes:
            continue
        existentes.add(tema["slug"])
        data["temas"].append(tema)
        nuevos.append(tema)
    return nuevos


# --------------------------------------------------------------------------
# Control de repetición
# --------------------------------------------------------------------------

def significativas(titulo):
    """Palabras del título que distinguen un tema de otro, normalizadas."""
    palabras = set()
    for bruto in str(titulo).split():
        limpia = slugify_tag(bruto)
        if limpia and limpia not in STOPWORDS:
            palabras.add(limpia)
    return palabras


def _solapamiento(a, b):
    """Coeficiente de solapamiento, no Jaccard.

    Jaccard castiga mucho los títulos cortos: 'La caída de X' contra 'La
    conquista de X' comparten una de tres palabras y darían 0,33 aunque hablen
    de lo mismo. Dividir entre el conjunto MÁS PEQUEÑO refleja mejor la
    pregunta que importa: cuánto de un título está contenido en el otro.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def es_duplicado(titulo, existentes, umbral=0.6):
    """Devuelve el título que choca, o None. Deliberadamente conservador.

    Solo marca cuando hay MUCHO solapamiento y al menos dos palabras
    compartidas. Con una sola palabra en común no se puede decidir: 'La caída
    de Roma' y 'Los baños de Roma' comparten 'roma' y son temas distintos.

    Esto atrapa las reformulaciones ('Calígula nombró cónsul a su caballo'
    contra 'El caballo al que Calígula nombró cónsul') y NO atrapa dos enfoques
    distintos de la misma entidad. Eso último lo ve la revisión humana, que es
    la razón de generar los temas por lotes y no de uno en uno.
    """
    palabras = significativas(titulo)
    for otro in existentes:
        comunes = palabras & significativas(otro)
        if len(comunes) >= 2 and _solapamiento(palabras, significativas(otro)) >= umbral:
            return otro
    return None


def parecidos(titulo, existentes):
    """Choques débiles: comparten una entidad probable. Solo para avisar.

    No rechaza nada; dirige la atención de quien cura la lista hacia los pares
    que conviene mirar dos veces.
    """
    palabras = {p for p in significativas(titulo) if len(p) >= MIN_ENTIDAD}
    avisos = []
    for otro in existentes:
        compartidas = palabras & significativas(otro)
        if compartidas and not es_duplicado(titulo, [otro]):
            avisos.append((otro, sorted(compartidas)))
    return avisos


def exclusiones(data, titulos_existentes=()):
    """Todo lo que el modelo NO debe volver a proponer.

    Incluye los temas usados y los pendientes: proponer algo que ya está en la
    cola de espera es tan inútil como proponer algo ya publicado.
    """
    vistos = []
    for titulo in list(titulos_existentes) + [t["titulo"] for t in data["temas"]]:
        if titulo and titulo not in vistos:
            vistos.append(titulo)
    return vistos


# --------------------------------------------------------------------------
# Slugs
# --------------------------------------------------------------------------

def slug_de(titulo, ocupados=()):
    """'La caída de Tenochtitlan' -> 'caida-tenochtitlan'.

    El slug nombra el archivo de la historia y el directorio de sus imágenes en
    storage/, así que tiene que ser estable, corto y libre de acentos.
    """
    plano = unicodedata.normalize("NFKD", str(titulo))
    plano = "".join(c for c in plano if not unicodedata.combining(c)).lower()
    trozos = [
        "".join(c for c in palabra if c.isalnum())
        for palabra in plano.split()
    ]
    trozos = [t for t in trozos if t and t not in STOPWORDS][:4]
    base = "-".join(trozos) or "tema"

    if base not in ocupados:
        return base
    for n in range(2, 100):
        candidato = f"{base}-{n}"
        if candidato not in ocupados:
            return candidato
    raise StudioError(f"no se pudo derivar un slug libre a partir de {titulo!r}")
