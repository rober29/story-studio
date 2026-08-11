"""Composición de texto e imagen con PIL.

Todo lo que se dibuja sobre el video pasa por aquí. Las funciones son puras
(entradas -> Image), así que se pueden inspeccionar y testear sin renderizar
un video entero.
"""

import os
import unicodedata

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from studio.errors import StudioError

VIDEO_W, VIDEO_H = 1080, 1920

# La franja del formato A/D: 16:9 sobre el ancho del vídeo, centrada.
STRIP_H = VIDEO_W * 9 // 16          # 607
STRIP_TOP = (VIDEO_H - STRIP_H) // 2  # 656
STRIP_ASPECT = VIDEO_W / STRIP_H      # 1.7792, no exactamente 16/9

# Escalera de encuadres del formato D, en coordenadas normalizadas sobre la
# imagen fuente: (escala, centro_x, centro_y). El plano 0 es siempre el general
# —primero se establece la escena y luego se entra—, y los cerrados van
# sesgados hacia arriba porque las caras viven en la mitad superior.
SHOT_LADDER = (
    (1.00, 0.50, 0.50),   # general
    (0.62, 0.44, 0.47),   # medio, izquierda
    (0.45, 0.55, 0.42),   # primer plano
    (0.66, 0.58, 0.51),   # medio, derecha
)

# En borrador la imagen es pequeña y no hay margen real para recortar: se
# comprime la escalera en vez de quitar planos, para que el ritmo de montaje
# se pueda juzgar gratis aunque los planos cerrados salgan blandos.
DRAFT_MIN_SCALE = 0.75

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOLD_FONT = os.path.join("C:\\Windows\\Fonts", "segoeuib.ttf")
EMOJI_FONT = os.path.join("C:\\Windows\\Fonts", "seguiemj.ttf")
FALLBACK_FONT = os.path.join(ROOT, "resource", "fonts", "MicrosoftYaHeiBold.ttc")

_FONT_CACHE = {}


def load_font(size, path=None):
    """Carga una fuente escalable. Nunca cae a la bitmap por defecto.

    ImageFont.load_default() ignora el tamaño: si se usara como respaldo, los
    banners y subtítulos saldrían de ~11 px sin que nada avisara.
    """
    candidates = [path, BOLD_FONT, FALLBACK_FONT]
    tried = []
    for candidate in candidates:
        if not candidate:
            continue
        tried.append(candidate)
        cache_key = (candidate, int(size))
        if cache_key in _FONT_CACHE:
            return _FONT_CACHE[cache_key]
        try:
            font = ImageFont.truetype(candidate, int(size))
        except (OSError, IOError):
            continue
        _FONT_CACHE[cache_key] = font
        return font
    raise StudioError(
        "no pude cargar ninguna fuente escalable. Probadas: " + ", ".join(tried)
    )


def check_fonts():
    """Preflight: falla antes del TTS si no hay con qué dibujar."""
    load_font(48)
    return True


def hex_to_rgb(value, field="color"):
    text = str(value).lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if len(text) != 6:
        raise StudioError(f"{field} debe ser hexadecimal #RRGGBB, recibido {value!r}")
    try:
        return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise StudioError(f"{field} debe ser hexadecimal #RRGGBB, recibido {value!r}")


def split_emoji(text):
    """Separa un emoji inicial del resto del título.

    Comprueba la categoría Unicode en vez de 'no alfanumérico': con el criterio
    antiguo, un '¡' o un '«' se dibujaban con la fuente de emoji y desaparecían
    del texto.
    """
    if not text:
        return "", ""
    first = text[0]
    is_emoji = unicodedata.category(first) == "So" or ord(first) >= 0x1F000
    if not is_emoji:
        return "", text
    emoji, rest = first, text[1:]
    # arrastra selector de variación, ZWJ y modificadores de tono de piel
    while rest and (ord(rest[0]) in (0xFE0F, 0x200D) or 0x1F3FB <= ord(rest[0]) <= 0x1F3FF):
        emoji += rest[0]
        rest = rest[1:]
    return emoji, rest.lstrip()


def _wrap(words, font, max_width):
    """Reparte palabras en líneas que caben en max_width. Devuelve índices."""
    lines, current = [], []
    for i, word in enumerate(words):
        trial = current + [i]
        text = " ".join(words[j] for j in trial)
        if current and font.getlength(text) > max_width:
            lines.append(current)
            current = [i]
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def fit_text(words, max_width, max_lines, font_size, min_size=30, font_path=None):
    """Busca el mayor tamaño que quepa en max_lines. Falla si no cabe."""
    size = int(font_size)
    while size >= min_size:
        font = load_font(size, font_path)
        lines = _wrap(words, font, max_width)
        if len(lines) <= max_lines and all(
            font.getlength(" ".join(words[j] for j in line)) <= max_width for line in lines
        ):
            return font, lines
        size -= 4
    raise StudioError(
        f"el texto {' '.join(words)!r} no cabe en {max_lines} línea(s) de "
        f"{max_width}px ni reduciendo la fuente a {min_size}px"
    )


def _draw_lines(draw, lines, words, font, origin_y, canvas_w, line_height,
                fill, stroke_color, stroke_width, highlight=None, highlight_fill=None):
    """Dibuja líneas centradas; resalta la palabra de índice global highlight."""
    y = origin_y
    for line in lines:
        text = " ".join(words[j] for j in line)
        x = (canvas_w - font.getlength(text)) / 2
        for index in line:
            word = words[index]
            color = highlight_fill if (highlight is not None and index == highlight) else fill
            draw.text(
                (x, y), word, font=font, fill=color,
                stroke_width=stroke_width, stroke_fill=stroke_color,
            )
            x += font.getlength(word + " ")
        y += line_height


def render_caption(text, video_width=VIDEO_W, font_size=78, max_lines=2,
                   fill="#FFFFFF", stroke_color="#000000"):
    """Caption central del formato B.

    El lienzo se dimensiona DESPUÉS de fijar fuente y número de líneas, así el
    centro óptico no se mueve aunque el texto se haya encogido.
    """
    words = str(text).split()
    if not words:
        raise StudioError("caption vacío")
    max_width = int(video_width * 0.92)
    font, lines = fit_text(words, max_width, max_lines, font_size)

    line_height = int(font.size * 1.32)
    margin = int(font.size * 0.35)
    height = len(lines) * line_height + margin * 2
    stroke = max(3, int(font.size / 18))

    img = Image.new("RGBA", (video_width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    _draw_lines(
        draw, lines, words, font, margin, video_width, line_height,
        fill=hex_to_rgb(fill) + (255,),
        stroke_color=hex_to_rgb(stroke_color) + (255,),
        stroke_width=stroke,
    )
    return img


def render_karaoke(text, highlight, video_width=VIDEO_W, video_height=VIDEO_H,
                   font_size=55, max_lines=2, position="bottom", custom_position=85.0,
                   fill="#FFFFFF", highlight_fill="#FFFF00", stroke_color="#000000",
                   stroke_width=2):
    """Lienzo completo con el subtítulo ya colocado y una palabra resaltada.

    Se devuelve a tamaño de video (no un recorte) para poder superponerlo tal
    cual con ffmpeg, sin cálculos de posición en otro sitio.
    """
    words = str(text).split()
    if not words:
        raise StudioError("tarjeta de subtítulo vacía")
    if not 0 <= highlight < len(words):
        raise StudioError(f"índice de resaltado {highlight} fuera de {len(words)} palabras")

    max_width = int(video_width * 0.9)
    font, lines = fit_text(words, max_width, max_lines, font_size)
    line_height = int(font.size * 1.3)
    block_height = len(lines) * line_height

    if position == "top":
        y = video_height * 0.05
    elif position == "strip":
        # anclado a la franja y no a la altura del vídeo: así la posición no
        # depende de cuántas líneas tenga la tarjeta y el subtítulo no salta
        strip_h = video_width * 9 // 16
        strip_top = (video_height - strip_h) // 2
        y = strip_top + strip_h * (float(custom_position) / 100.0)
        y = min(y, strip_top + strip_h + 40 - block_height)
        y = max(y, strip_top)
    elif position == "custom":
        y = (video_height - block_height) * (float(custom_position) / 100.0)
    elif position == "center":
        y = (video_height - block_height) / 2
    else:  # bottom
        y = video_height * 0.93 - block_height
    margin = 12
    y = max(margin, min(y, video_height - block_height - margin))

    img = Image.new("RGBA", (video_width, video_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    _draw_lines(
        draw, lines, words, font, int(y), video_width, line_height,
        fill=hex_to_rgb(fill) + (255,),
        stroke_color=hex_to_rgb(stroke_color) + (255,),
        stroke_width=int(stroke_width),
        highlight=highlight,
        highlight_fill=hex_to_rgb(highlight_fill, "word_highlight_color") + (255,),
    )
    return img


def render_chapter_banner(text, video_width=VIDEO_W, font_size=42):
    """Título de capítulo del formato C: emoji opcional + texto con sombra."""
    emoji, label = split_emoji(str(text))
    font = load_font(font_size)
    height = int(font_size * 2.6)
    img = Image.new("RGBA", (video_width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    label_width = font.getlength(label)
    emoji_width = font_size + 12 if emoji else 0
    x = (video_width - label_width - emoji_width) / 2
    y = font_size // 2

    if emoji:
        drawn = False
        try:
            emoji_font = ImageFont.truetype(EMOJI_FONT, font_size)
            draw.text((x, y), emoji, font=emoji_font, embedded_color=True)
            drawn = True
        except (OSError, IOError, ValueError) as e:
            print(f"[aviso] no pude dibujar el emoji {emoji!r}: {e}")
        # solo se avanza si se dibujó algo: si no, quedaría un hueco a la izquierda
        if drawn:
            x += emoji_width

    draw.text(
        (x, y), label, font=font, fill=(255, 255, 255, 255),
        stroke_width=2, stroke_fill=(0, 0, 0, 220),
    )
    return img


def render_series_banner(text, part, video_width=VIDEO_W, font_size=40):
    """Pastilla naranja del formato A con el número de parte."""
    font = load_font(font_size)
    part_text = f"  {part}" if part else ""
    text_width = font.getlength(text) + font.getlength(part_text)

    pad_x, pad_y = 36, 20
    width = int(text_width + pad_x * 2)
    height = int(font_size + pad_y * 2)
    img = Image.new("RGBA", (video_width, height + 8), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    x0 = (video_width - width) // 2
    draw.rounded_rectangle(
        (x0, 4, x0 + width, height + 4), radius=height // 2,
        fill=(247, 165, 26, 255), outline=(120, 70, 0, 255), width=3,
    )
    text_x = x0 + pad_x
    text_y = 4 + pad_y - 4
    draw.text((text_x, text_y), text, font=font, fill=(60, 30, 0, 255))
    if part_text:
        draw.text(
            (text_x + font.getlength(text), text_y), part_text,
            font=font, fill=(200, 30, 30, 255),
        )
    return img


def render_watermark(text, video_width=VIDEO_W, font_size=30):
    font = load_font(font_size)
    img = Image.new("RGBA", (video_width, int(font_size * 1.6)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    x = (video_width - font.getlength(text)) / 2
    draw.text((x, 0), text, font=font, fill=(255, 255, 255, 150),
              stroke_width=1, stroke_fill=(0, 0, 0, 160))
    return img


def floor_scale(src_w, src_h, out_w=VIDEO_W, out_h=STRIP_H, aspect=STRIP_ASPECT):
    """Escala mínima que no obliga a ampliar la imagen fuente."""
    if src_w / src_h > aspect:
        base_w, base_h = src_h * aspect, float(src_h)
    else:
        base_w, base_h = float(src_w), src_w / aspect
    return max(out_w / base_w, out_h / base_h)


def remap_ladder(ladder=SHOT_LADDER, floor=0.0):
    """Comprime la escalera para que ninguna escala baje del suelo.

    Conserva la forma: el plano general sigue siendo 1.0 y el orden relativo de
    los demás se mantiene.
    """
    if floor <= 0:
        return tuple(ladder)
    menor = min(s for s, _, _ in ladder)
    if menor >= floor or menor >= 1.0:
        return tuple(ladder)
    return tuple(
        (floor + (s - menor) * (1.0 - floor) / (1.0 - menor), cx, cy)
        for s, cx, cy in ladder
    )


def crop_box(src_w, src_h, scale, cx, cy, aspect=STRIP_ASPECT):
    """Caja de recorte entera con el aspecto de la franja, dentro de la imagen.

    Si el centro pedido dejaría la caja fuera del borde, se empuja hacia dentro
    en vez de encogerla: así el tamaño pedido (y por tanto el nivel de zoom) se
    respeta siempre.
    """
    if src_w / src_h > aspect:
        base_w, base_h = src_h * aspect, float(src_h)
    else:
        base_w, base_h = float(src_w), src_w / aspect
    width = max(16.0, min(base_w * scale, float(src_w)))
    height = max(16.0, min(width / aspect, float(src_h)))
    width = height * aspect

    x0 = cx * src_w - width / 2
    y0 = cy * src_h - height / 2
    x0 = max(0.0, min(x0, src_w - width))
    y0 = max(0.0, min(y0, src_h - height))
    return (int(round(x0)), int(round(y0)),
            int(round(x0 + width)), int(round(y0 + height)))


def shot_boxes(src_w, src_h, count, scene_index=0, draft=False):
    """Cajas de recorte de los planos de una escena, en orden.

    Determinista: entra en la clave de caché. En escenas impares la escalera se
    espeja horizontalmente para que no todas las escenas se muevan igual.
    """
    suelo = DRAFT_MIN_SCALE if draft else floor_scale(src_w, src_h)
    escalera = remap_ladder(SHOT_LADDER, suelo)
    cajas = []
    for i in range(count):
        scale, cx, cy = escalera[i % len(escalera)]
        if scene_index % 2 and i:  # el general nunca se espeja: cx ya es 0.5
            cx = 1.0 - cx
        cajas.append(crop_box(src_w, src_h, scale, cx, cy))
    return cajas


def blurred_background(image, video_width=VIDEO_W, video_height=VIDEO_H, radius=40):
    """Fondo desenfocado a pantalla completa, a partir de la imagen entera."""
    fondo = image.resize((video_width, video_height), Image.LANCZOS)
    return fondo.filter(ImageFilter.GaussianBlur(radius)).convert("RGBA")


def strip_from(image, box=None, video_width=VIDEO_W, strip_height=STRIP_H):
    """Recorta la región pedida y la escala al tamaño de la franja."""
    recorte = image.crop(box) if box else image
    return recorte.resize((video_width, strip_height), Image.LANCZOS)


def compose_strip_frame(background, strip, banner=None, video_height=VIDEO_H,
                        strip_top=STRIP_TOP):
    """Pega la franja sobre el fondo ya desenfocado."""
    frame = background.copy()
    frame.paste(strip, (0, strip_top))
    if banner is not None:
        frame.alpha_composite(banner, (0, max(0, strip_top - banner.height - 24)))
    return frame.convert("RGB")


def load_scene_image(path, size=(VIDEO_W, VIDEO_H)):
    try:
        img = Image.open(path)
    except (OSError, ValueError) as e:
        raise StudioError(f"no pude abrir {path}: {e}. Bórrala y reejecuta --phase assets")
    with img:
        img = img.convert("RGB")
        if img.size != size:
            img = img.resize(size, Image.LANCZOS)
        return img.copy()


def compose_cartoon_frame(image_path, banner=None, video_width=VIDEO_W, video_height=VIDEO_H):
    """Formato A: franja 16:9 nítida sobre fondo borroso, con banner encima."""
    try:
        original = Image.open(image_path)
    except (OSError, ValueError) as e:
        raise StudioError(f"no pude abrir {image_path}: {e}")
    with original:
        original = original.convert("RGB")
        background = blurred_background(original, video_width, video_height)
        strip = strip_from(original, None, video_width, video_width * 9 // 16)
    return compose_strip_frame(
        background, strip, banner, video_height, (video_height - strip.height) // 2
    )
