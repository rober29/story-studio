"""Revisa una historia ANTES de gastar en imágenes.

Cada aviso de aquí nació de un fallo real que se descubrió mirando fotogramas de
un reel ya pagado. Detectarlos sobre el JSON cuesta cero y llega a tiempo; verlos
en el vídeo cuesta 0,13 $ por imagen y llega tarde.

Lo que NO puede vivir aquí, y conviene tenerlo claro: la composición. Que las
figuras del fondo salgan del tamaño de las de primer plano y parezcan gigantes
es un defecto de la imagen, no del prompt, y solo se ve mirándola.

Módulo puro: solo stdlib, sin red ni disco.
"""

import re

# Un prompt de formato D se recorta en tres o cuatro encuadres. Por debajo de
# esto no hay material para cuatro recortes distintos y los cortes se repiten.
# Calibrado sobre los prompts reales: los de mansa-musa-mali rondan las 45
# palabras, los flojos de guerra-asiento las 30.
PROMPT_MINIMO = 32

# Palabras que sacan al personaje del estilo del canal. El estilo global es
# austero a propósito y NO basta para frenarlas: medido el 2026-08-11.
PROHIBIDAS = re.compile(
    r"\b(photorealistic|photo-realistic|realistic|3d render|3d-render|cinematic|"
    r"detailed portrait|hyperrealistic)\b",
    re.I,
)

# Señales de que el prompt reparte elementos por el encuadre en vez de amontonar
# todo en el centro.
REPARTO = re.compile(r"\b(left|right|background|foreground|behind|horizon|distance)\b", re.I)

# Señales de escena poblada. No basta con repartir: 'cajas a la derecha y
# gaviotas al fondo' cumple el reparto y da cuatro recortes aburridos.
POBLADA = re.compile(
    r"\b(crowd|crowds|crowded|bustling|busy|thousands|hundreds|dozens|many|"
    r"citizens|people|workers|soldiers|traders|merchants|customers|passersby|"
    r"onlookers|attendants|mourners|collectors|patrons|villagers|procession|"
    r"market|queue|audience)\b",
    re.I,
)

# Colores, para saber si una descripción de personaje trae algo que pintar.
COLORES = re.compile(
    r"\b(red|blue|green|yellow|golden|gold|black|white|grey|gray|brown|purple|"
    r"orange|pink|silver|crimson|navy|beige|teal|ochre|scarlet|emerald)\b",
    re.I,
)


class Aviso:
    __slots__ = ("codigo", "donde", "mensaje")

    def __init__(self, codigo, donde, mensaje):
        self.codigo = codigo
        self.donde = donde
        self.mensaje = mensaje

    def __repr__(self):
        return f"Aviso({self.codigo!r}, {self.donde!r})"

    def __str__(self):
        return f"  [{self.codigo}] {self.donde}: {self.mensaje}"


def lint_characters(story):
    """Las descripciones de personaje, que son la mitad del estilo."""
    avisos = []
    for nombre, desc in (story.get("characters") or {}).items():
        donde = f"personaje {nombre!r}"
        prohibida = PROHIBIDAS.search(desc)
        if prohibida:
            avisos.append(
                Aviso(
                    "personaje-fuera-de-estilo", donde,
                    f"usa {prohibida.group(0)!r}; esa palabra saca al personaje del "
                    f"estilo del canal y el estilo global no basta para frenarla",
                )
            )
        if len(desc.split()) < 8:
            avisos.append(
                Aviso(
                    "personaje-pobre", donde,
                    f"solo {len(desc.split())} palabras; el estilo base es austero a "
                    f"propósito y la riqueza sale de aquí. Añade complexión, porte, "
                    f"rasgos y prendas",
                )
            )
        elif not COLORES.search(desc):
            avisos.append(
                Aviso(
                    "personaje-sin-color", donde,
                    "no menciona ningún color; sin él la figura sale a medio pintar",
                )
            )
    return avisos


def lint_scenes(story):
    """Los prompts, escena por escena."""
    avisos = []
    for i, scene in enumerate(story["scenes"]):
        prompt = scene.get("prompt", "")
        donde = f"escena {i}"
        palabras = len(prompt.split())

        # El fallo que costó 0,26 $ el 2026-08-15: las dos escenas del arco de
        # Norton sin personaje nombrado salieron visiblemente más pobres, porque
        # toda la riqueza del formato viene de esa descripción.
        if not scene.get("characters_present"):
            avisos.append(
                Aviso(
                    "escena-sin-personaje", donde,
                    "no usa ningún {personaje}; describe a su figura principal con "
                    "el mismo detalle dentro del prompt o saldrá con el estilo "
                    "desnudo",
                )
            )
        if palabras < PROMPT_MINIMO:
            avisos.append(
                Aviso(
                    "prompt-corto", donde,
                    f"{palabras} palabras; por debajo de {PROMPT_MINIMO} no hay "
                    f"material para cuatro encuadres distintos",
                )
            )
        if not REPARTO.search(prompt):
            avisos.append(
                Aviso(
                    "prompt-sin-reparto", donde,
                    "no sitúa nada a izquierda, derecha ni al fondo; los cuatro "
                    "recortes saldrán casi iguales",
                )
            )
        elif not POBLADA.search(prompt):
            avisos.append(
                Aviso(
                    "prompt-vacio", donde,
                    "reparte elementos pero no hay gente ni actividad. 'cajas a la "
                    "derecha y gaviotas al fondo' cumple el reparto y da cuatro "
                    "recortes aburridos",
                )
            )
    return avisos


def lint_story(story):
    """Todos los avisos de una historia, en orden de aparición.

    Solo aplica al formato D, y no por pereza: cada regla de aquí nace de dos
    rasgos que son suyos y de nadie más. De la ÚNICA imagen de cada escena se
    recortan tres o cuatro encuadres —de ahí que un prompt escueto o sin reparto
    dé cortes repetidos— y su estilo global es austero a propósito, así que la
    riqueza tiene que venir de la descripción del personaje.

    Los formatos A y C usan una imagen por escena con zoom y un estilo
    cinematográfico; el B, una por beat. Aplicarles estas reglas producía más de
    veinte avisos por historia, todos falsos.
    """
    if story.get("format") != "D":
        return []
    return lint_characters(story) + lint_scenes(story)


def lint_serie(historias):
    """Avisos que solo se ven comparando las partes de un arco.

    `historias` es una lista de historias ya validadas. El caso que importa: las
    partes de un arco comparten ficha de personaje SOLO si la descripción es
    idéntica byte a byte. Si diverge, cada parte paga su propia ficha —0,13 $ de
    más por parte— y además el personaje cambia de aspecto entre reels.
    """
    avisos = []
    por_nombre = {}
    for story in historias:
        for nombre, desc in (story.get("characters") or {}).items():
            por_nombre.setdefault(nombre, {}).setdefault(desc, []).append(story["id"])

    for nombre, variantes in por_nombre.items():
        if len(variantes) > 1:
            detalle = "; ".join(
                f"{', '.join(ids)}" for ids in variantes.values()
            )
            avisos.append(
                Aviso(
                    "ficha-divergente", f"personaje {nombre!r}",
                    f"descrito de {len(variantes)} formas distintas ({detalle}). "
                    f"Cada variante paga su propia ficha y el personaje cambiará "
                    f"de aspecto entre partes",
                )
            )
    return avisos
