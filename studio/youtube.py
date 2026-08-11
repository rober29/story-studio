"""Subida a YouTube: OAuth de escritorio y videos.insert reanudable.

El flujo de autorización se hace a mano con la stdlib en vez de con
google-auth-oauthlib, que no está instalado: añadirlo arrastraría dependencias
que chocan con el pin de google-generativeai que usa app/services/llm.py. Es el
mismo criterio que ya sigue studio/gemini.py, y son unas pocas líneas para algo
que se ejecuta dos veces en la vida del proyecto.

La subida sí usa googleapiclient, que sí está instalado y aporta reanudación,
reintentos y progreso.
"""

import json
import os
import socket
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from studio.cache import write_atomic
from studio.errors import StudioError, TransientError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS_DIR = os.path.join(ROOT, "storage", "secrets")

# Solo subir. Con este permiso el script no puede leer, editar ni borrar nada
# del canal: que no pueda borrar un duplicado es deliberado.
SCOPE = "https://www.googleapis.com/auth/youtube.upload"
# v2 y no el /o/oauth2/auth que trae el JSON descargado de Google: ese endpoint
# es el heredado y redirige, pero la redirección se rompe con un 400 "malformed"
# en /signin/oauth/delegation cuando la cuenta tiene canales de marca — que es
# justo nuestro caso, dos canales bajo una misma cuenta.
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"

_secret_cache = None


def client_config():
    """Credenciales del cliente OAuth, de la variable de entorno o del archivo."""
    global _secret_cache
    if _secret_cache:
        return _secret_cache

    ruta = os.environ.get("YOUTUBE_CLIENT_SECRET_FILE") or os.path.join(
        SECRETS_DIR, "youtube_client.json"
    )
    if not os.path.isfile(ruta):
        raise StudioError(
            f"falta el cliente OAuth de YouTube en {ruta}.\n"
            f"  1. Google Cloud Console > APIs y servicios > Credenciales\n"
            f"  2. Crear credenciales > ID de cliente de OAuth > Aplicación de escritorio\n"
            f"  3. Descargar el JSON y guardarlo ahí (storage/ está en .gitignore)"
        )
    with open(ruta, encoding="utf-8") as f:
        datos = json.load(f)
    config = datos.get("installed") or datos.get("web")
    if not config or "client_id" not in config:
        raise StudioError(f"{ruta} no parece un cliente OAuth de aplicación de escritorio")
    _secret_cache = config
    return config


def redact(text):
    """Quita cualquier secreto de un texto antes de imprimirlo."""
    salida = str(text)
    try:
        config = client_config()
    except StudioError:
        return salida
    for valor in (config.get("client_secret"), config.get("client_id")):
        if valor:
            salida = salida.replace(valor, "***")
    for campo in ("refresh_token", "access_token"):
        salida = _mask_field(salida, campo)
    return salida


def _mask_field(text, field):
    marca = f'"{field}"'
    while marca in text:
        inicio = text.index(marca)
        fin = text.find(",", inicio)
        fin = len(text) if fin == -1 else fin
        text = text[:inicio] + f'"{field}": "***"' + text[fin:]
        if text.count(marca) and '"***"' in text[inicio:fin + 8]:
            break
    return text


def token_path(channel):
    return os.path.join(SECRETS_DIR, f"youtube_token_{channel}.json")


def _post_form(url, campos):
    datos = urllib.parse.urlencode(campos).encode()
    peticion = urllib.request.Request(url, data=datos, method="POST")
    try:
        with urllib.request.urlopen(peticion, timeout=60) as respuesta:
            return json.loads(respuesta.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        cuerpo = redact(e.read().decode("utf-8", "replace")[:300])
        if e.code in (429, 500, 502, 503, 504):
            raise TransientError(f"OAuth respondió {e.code}: {cuerpo}")
        raise StudioError(f"OAuth rechazó la petición ({e.code}): {cuerpo}")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise TransientError(f"no hubo respuesta del servidor de OAuth: {redact(e)}")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def authorize(channel, abrir_navegador=True):
    """Flujo interactivo por loopback. Se ejecuta una vez por canal.

    Con abrir_navegador=False solo imprime la URL: hace falta cuando el
    navegador por defecto tiene varias sesiones de Google abiertas y hay que
    pegarla en una ventana de incógnito.
    """
    config = client_config()
    puerto = _free_port()
    redirect = f"http://127.0.0.1:{puerto}"
    parametros = {
        "client_id": config["client_id"],
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        # 'consent' fuerza a que Google devuelva refresh_token también en
        # re-autorizaciones; 'select_account' fuerza además el selector de
        # cuenta y de canal de marca. Sin lo segundo, autorizar un segundo canal
        # reutiliza en silencio la identidad del primero y los dos tokens
        # acaban apuntando al mismo sitio (pasó el 2026-08-10).
        "prompt": "select_account consent",
    }
    url = f"{AUTH_URI}?{urllib.parse.urlencode(parametros)}"

    recibido = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            consulta = urllib.parse.urlparse(self.path).query
            recibido.update(urllib.parse.parse_qs(consulta))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            mensaje = "Listo, ya puedes cerrar esta pestaña." if "code" in recibido \
                else "No se recibió el código de autorización."
            self.wfile.write(f"<html><body><h3>{mensaje}</h3></body></html>".encode())

        def log_message(self, *args):
            pass  # sin ruido en consola

    if abrir_navegador:
        print(f"abriendo el navegador para autorizar el canal {channel!r}...")
        print(f"si no se abre solo, entra aquí:\n  {url}")
        webbrowser.open(url)
    else:
        print(f"autoriza el canal {channel!r} abriendo esta URL:\n  {url}")
    with HTTPServer(("127.0.0.1", puerto), Handler) as servidor:
        # 10 min: el flujo pasa por elegir cuenta, elegir canal de marca y
        # saltarse el aviso de app no verificada; con 5 se agotaba a media faena
        servidor.timeout = 600
        servidor.handle_request()

    if "code" not in recibido:
        error = recibido.get("error", ["sin código"])[0]
        raise StudioError(f"la autorización no devolvió código: {error}")

    tokens = _post_form(TOKEN_URI, {
        "code": recibido["code"][0],
        "client_id": config["client_id"],
        "client_secret": config.get("client_secret", ""),
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
    })
    if "refresh_token" not in tokens:
        raise StudioError(
            "Google no devolvió refresh_token. Revoca el acceso en "
            "https://myaccount.google.com/permissions y vuelve a autorizar"
        )
    write_atomic(token_path(channel), json.dumps(tokens, indent=1), mode="w")
    print(f"token guardado en {token_path(channel)}")
    return tokens


def load_credentials(channel):
    """Credenciales listas para usar, refrescando el token si hace falta."""
    ruta = token_path(channel)
    if not os.path.isfile(ruta):
        raise StudioError(
            f"el canal {channel!r} no está autorizado todavía. "
            f"Ejecuta: story_publish.py --authorize --channel {channel}"
        )
    with open(ruta, encoding="utf-8") as f:
        tokens = json.load(f)
    config = client_config()

    from google.oauth2.credentials import Credentials

    return Credentials(
        token=tokens.get("access_token"),
        refresh_token=tokens["refresh_token"],
        token_uri=TOKEN_URI,
        client_id=config["client_id"],
        client_secret=config.get("client_secret"),
        scopes=[SCOPE],
    )


def check_auth(channel):
    """Refresca el token e informa. Detecta el fallo silencioso más común.

    Mientras la app de OAuth siga en modo 'Testing', los refresh tokens caducan
    en pocos días y la automatización deja de funcionar sin avisar.
    """
    from google.auth.transport.requests import Request

    credenciales = load_credentials(channel)
    try:
        credenciales.refresh(Request())
    except Exception as e:
        raise StudioError(
            f"no se pudo refrescar el token del canal {channel!r}: {redact(e)}\n"
            f"Si la app de OAuth sigue en modo 'Testing', los tokens caducan a los "
            f"pocos días: pásala a producción en la pantalla de consentimiento"
        )
    print(f"canal {channel!r}: token válido")
    print(f"  caduca: {credenciales.expiry} (UTC)")
    return True


def upload(video_path, body, channel, progress=True):
    """videos.insert con subida reanudable. Devuelve el id del vídeo."""
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    if not os.path.isfile(video_path):
        raise StudioError(f"no existe el vídeo a subir: {video_path}")

    servicio = build("youtube", "v3", credentials=load_credentials(channel),
                     cache_discovery=False)
    medio = MediaFileUpload(video_path, chunksize=4 * 1024 * 1024, resumable=True,
                            mimetype="video/mp4")
    peticion = servicio.videos().insert(part="snippet,status", body=body, media_body=medio)

    respuesta = None
    while respuesta is None:
        try:
            estado, respuesta = peticion.next_chunk()
        except HttpError as e:
            motivo = ""
            try:
                motivo = json.loads(e.content.decode()).get("error", {}).get(
                    "errors", [{}])[0].get("reason", "")
            except Exception:
                pass
            if motivo in ("quotaExceeded", "uploadLimitExceeded"):
                raise StudioError(
                    "se agotó la cuota de subida de YouTube; se reinicia a medianoche "
                    "hora del Pacífico. El paquete sigue en storage/publish/ y se "
                    "puede reintentar tal cual"
                )
            if e.resp.status in (500, 502, 503, 504):
                raise TransientError(f"YouTube respondió {e.resp.status}: {redact(e)}")
            raise StudioError(f"YouTube rechazó la subida: {redact(e)}")
        if estado and progress:
            print(f"  subiendo... {int(estado.progress() * 100)}%")

    video_id = respuesta.get("id")
    if not video_id:
        raise StudioError(f"la respuesta no traía id de vídeo: {redact(respuesta)}")
    return video_id
