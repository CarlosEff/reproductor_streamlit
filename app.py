import ipaddress
import mimetypes
import os
import socket
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import requests
import streamlit as st


# URL pública de esta aplicación.
URL_APP = "https://effective-creative-repo.streamlit.app/"

# Límite de seguridad para evitar cargar archivos demasiado grandes.
MAX_FILE_SIZE_MB = 250
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024


st.set_page_config(
    page_title="Reproductor multimedia",
    page_icon="▶️",
    layout="centered",
)

st.title("▶️ Reproductor de audio y video")
st.write(
    "Pega una liga que descargue un audio o video. "
    "La aplicación obtiene el archivo y lo muestra en un reproductor."
)


def validar_url_publica(url: str) -> None:
    """
    Permite únicamente HTTP/HTTPS y bloquea direcciones locales o privadas.
    Esto es importante porque la app estará publicada en Internet.
    """
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("La URL debe comenzar con http:// o https://")

    if not parsed.hostname:
        raise ValueError("La URL no contiene un dominio válido.")

    try:
        direcciones = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as error:
        raise ValueError("No se pudo resolver el dominio de la URL.") from error

    for direccion in direcciones:
        ip_texto = direccion[4][0]
        ip = ipaddress.ip_address(ip_texto)

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(
                "Por seguridad, no se permiten direcciones locales o privadas."
            )


def descargar_con_redirecciones(
    url: str,
    headers: dict,
    timeout: int = 90,
    max_redirecciones: int = 8,
) -> requests.Response:
    """
    Sigue redirecciones manualmente para validar cada destino.
    """
    url_actual = url

    for _ in range(max_redirecciones + 1):
        validar_url_publica(url_actual)

        respuesta = requests.get(
            url_actual,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )

        if respuesta.status_code in {301, 302, 303, 307, 308}:
            ubicacion = respuesta.headers.get("Location")

            if not ubicacion:
                respuesta.close()
                raise requests.RequestException(
                    "El servidor respondió con una redirección sin destino."
                )

            nueva_url = urljoin(url_actual, ubicacion)
            respuesta.close()
            url_actual = nueva_url
            continue

        respuesta.raise_for_status()
        return respuesta

    raise requests.TooManyRedirects(
        f"Se superó el máximo de {max_redirecciones} redirecciones."
    )


def detectar_tipo_archivo(
    content_type: str,
    url: str,
    primeros_bytes: bytes,
) -> tuple[str | None, str, str]:
    """
    Devuelve:
    - tipo: audio, video o None
    - extensión sugerida
    - MIME apropiado para el reproductor
    """
    content_type_limpio = (content_type or "").lower().split(";")[0].strip()
    extension_url = Path(urlparse(url).path).suffix.lower()

    mapa_audio = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".flac": "audio/flac",
        ".wma": "audio/x-ms-wma",
    }

    mapa_video = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
        ".m4v": "video/mp4",
        ".avi": "video/x-msvideo",
        ".wmv": "video/x-ms-wmv",
        ".mpeg": "video/mpeg",
        ".mpg": "video/mpeg",
    }

    if content_type_limpio.startswith("audio/"):
        extension = (
            mimetypes.guess_extension(content_type_limpio)
            or extension_url
            or ".mp3"
        )
        return "audio", extension, content_type_limpio

    if content_type_limpio.startswith("video/"):
        extension = (
            mimetypes.guess_extension(content_type_limpio)
            or extension_url
            or ".mp4"
        )

        if "wmv" in content_type_limpio or "x-ms-asf" in content_type_limpio:
            extension = ".wmv"

        return "video", extension, content_type_limpio

    # MP3
    if primeros_bytes.startswith(b"ID3"):
        return "audio", ".mp3", "audio/mpeg"

    if primeros_bytes[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio", ".mp3", "audio/mpeg"

    # WAV o AVI
    if primeros_bytes.startswith(b"RIFF"):
        if b"WAVE" in primeros_bytes[:16]:
            return "audio", ".wav", "audio/wav"

        if b"AVI " in primeros_bytes[:16]:
            return "video", ".avi", "video/x-msvideo"

    # MP4, M4A o MOV
    if b"ftyp" in primeros_bytes[:32]:
        if extension_url in {".m4a", ".aac"}:
            return "audio", extension_url, mapa_audio.get(extension_url, "audio/mp4")

        extension = extension_url if extension_url in mapa_video else ".mp4"
        return "video", extension, mapa_video.get(extension, "video/mp4")

    # WebM / Matroska
    if primeros_bytes.startswith(b"\x1aE\xdf\xa3"):
        extension = extension_url if extension_url in {".webm", ".mkv"} else ".webm"
        return "video", extension, "video/webm"

    # ASF: normalmente WMA o WMV
    if primeros_bytes.startswith(b"0&\xb2u\x8ef\xcf\x11"):
        if extension_url == ".wma":
            return "audio", ".wma", "audio/x-ms-wma"

        return "video", ".wmv", "video/x-ms-wmv"

    if extension_url in mapa_audio:
        return "audio", extension_url, mapa_audio[extension_url]

    if extension_url in mapa_video:
        return "video", extension_url, mapa_video[extension_url]

    return None, extension_url or ".bin", content_type_limpio or "application/octet-stream"


@st.cache_data(show_spinner=False, ttl=1800)
def descargar_archivo(url: str) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),
        "Accept": "audio/*,video/*,application/octet-stream,*/*",
    }

    respuesta = descargar_con_redirecciones(
        url=url,
        headers=headers,
        timeout=90,
    )

    try:
        content_length = respuesta.headers.get("Content-Length")

        if content_length and int(content_length) > MAX_FILE_SIZE_BYTES:
            raise ValueError(
                f"El archivo supera el límite de {MAX_FILE_SIZE_MB} MB."
            )

        bloques = []
        total = 0

        for bloque in respuesta.iter_content(chunk_size=1024 * 1024):
            if not bloque:
                continue

            total += len(bloque)

            if total > MAX_FILE_SIZE_BYTES:
                raise ValueError(
                    f"El archivo supera el límite de {MAX_FILE_SIZE_MB} MB."
                )

            bloques.append(bloque)

        contenido = b"".join(bloques)

        if not contenido:
            raise ValueError("El servidor respondió sin contenido.")

        content_type = respuesta.headers.get(
            "Content-Type",
            "application/octet-stream",
        )

        tipo, extension, mime_reproductor = detectar_tipo_archivo(
            content_type=content_type,
            url=respuesta.url,
            primeros_bytes=contenido[:64],
        )

        return {
            "contenido": contenido,
            "content_type": content_type,
            "mime_reproductor": mime_reproductor,
            "tipo": tipo,
            "extension": extension,
            "url_final": respuesta.url,
            "tamano": len(contenido),
        }

    finally:
        respuesta.close()


@st.cache_data(show_spinner=False, ttl=1800)
def convertir_video_a_mp4(contenido: bytes, extension_entrada: str) -> bytes:
    """
    Convierte formatos no compatibles con el navegador, como WMV, a MP4.
    """
    ruta_entrada = None
    ruta_salida = None

    extension_segura = extension_entrada if extension_entrada.startswith(".") else ".wmv"

    try:
        with tempfile.NamedTemporaryFile(
            suffix=extension_segura,
            delete=False,
        ) as archivo_entrada:
            archivo_entrada.write(contenido)
            ruta_entrada = archivo_entrada.name

        with tempfile.NamedTemporaryFile(
            suffix=".mp4",
            delete=False,
        ) as archivo_salida:
            ruta_salida = archivo_salida.name

        comando = [
            "ffmpeg",
            "-y",
            "-i",
            ruta_entrada,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            ruta_salida,
        ]

        resultado = subprocess.run(
            comando,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if resultado.returncode != 0:
            detalle = resultado.stderr[-2000:] if resultado.stderr else "Sin detalle."
            raise RuntimeError(
                "FFmpeg no pudo convertir el video.\n\n"
                f"Detalle:\n{detalle}"
            )

        with open(ruta_salida, "rb") as archivo:
            convertido = archivo.read()

        if not convertido:
            raise RuntimeError("FFmpeg generó un archivo vacío.")

        return convertido

    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            "La conversión tardó más de cinco minutos y fue cancelada."
        ) from error

    finally:
        for ruta in (ruta_entrada, ruta_salida):
            if ruta and os.path.exists(ruta):
                try:
                    os.remove(ruta)
                except OSError:
                    pass


def necesita_conversion_mp4(archivo: dict) -> bool:
    extension = archivo["extension"].lower()
    mime = archivo["mime_reproductor"].lower()

    return (
        extension in {".wmv", ".avi", ".mpeg", ".mpg"}
        or "wmv" in mime
        or "x-ms-asf" in mime
    )


url_parametro = st.query_params.get("url", "")

url = st.text_input(
    "URL del audio o video",
    value=url_parametro,
    placeholder="https://auditsa.com.mx/w/...",
)

if url:
    try:
        validar_url_publica(url)

        with st.spinner("Descargando contenido..."):
            archivo = descargar_archivo(url)

        tamano_mb = archivo["tamano"] / (1024 * 1024)

        st.caption(
            f"Tipo recibido: {archivo['content_type']} · "
            f"Tamaño: {tamano_mb:.2f} MB"
        )

        if archivo["tipo"] == "audio":
            st.audio(
                archivo["contenido"],
                format=archivo["mime_reproductor"],
            )

        elif archivo["tipo"] == "video":
            if necesita_conversion_mp4(archivo):
                with st.spinner("Convirtiendo el video a MP4..."):
                    video_mp4 = convertir_video_a_mp4(
                        archivo["contenido"],
                        archivo["extension"],
                    )

                st.video(
                    video_mp4,
                    format="video/mp4",
                )

            else:
                st.video(
                    archivo["contenido"],
                    format=archivo["mime_reproductor"],
                )

        else:
            st.warning(
                "No se pudo identificar automáticamente si el archivo "
                "es audio o video."
            )

            tipo_manual = st.radio(
                "Selecciona el tipo de contenido",
                ["Audio", "Video"],
                horizontal=True,
            )

            if tipo_manual == "Audio":
                st.audio(archivo["contenido"])
            else:
                st.video(archivo["contenido"])

        enlace_compartir = (
            f"{URL_APP.rstrip('/')}/?url={quote(url, safe='')}"
        )

        st.divider()
        st.write("**Enlace directo para compartir:**")
        st.code(enlace_compartir, language=None)
        st.link_button("Abrir enlace directo", enlace_compartir)

    except requests.exceptions.Timeout:
        st.error("El servidor tardó demasiado en responder.")

    except requests.exceptions.HTTPError as error:
        codigo = (
            error.response.status_code
            if error.response is not None
            else "desconocido"
        )
        st.error(f"El servidor respondió con el error HTTP {codigo}.")

    except requests.exceptions.RequestException as error:
        st.error(f"No se pudo descargar el contenido: {error}")

    except Exception as error:
        st.error(f"Ocurrió un error: {error}")
