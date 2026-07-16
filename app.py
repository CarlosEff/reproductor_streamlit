import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import requests
import streamlit as st


st.set_page_config(
    page_title="Reproductor multimedia",
    page_icon="▶️",
    layout="centered",
)

st.title("▶️ Reproductor de audio y video")
st.write(
    "Pega una URL que descargue un archivo de audio o video. "
    "La aplicación lo descargará y lo mostrará en un reproductor."
)


def detectar_tipo_archivo(
    content_type: str,
    url: str,
    primeros_bytes: bytes,
) -> tuple[str | None, str]:
    """
    Detecta si el contenido es audio o video y devuelve una extensión sugerida.
    """
    content_type = (content_type or "").lower().split(";")[0].strip()
    extension_url = Path(urlparse(url).path).suffix.lower()

    if content_type.startswith("audio/"):
        extension = mimetypes.guess_extension(content_type) or extension_url or ".mp3"
        return "audio", extension

    if content_type.startswith("video/"):
        extension = mimetypes.guess_extension(content_type) or extension_url or ".mp4"
        return "video", extension

    # Firmas comunes de audio
    if primeros_bytes.startswith(b"ID3"):
        return "audio", ".mp3"

    if primeros_bytes[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio", ".mp3"

    if primeros_bytes.startswith(b"RIFF"):
        if b"WAVE" in primeros_bytes[:16]:
            return "audio", ".wav"

        if b"AVI " in primeros_bytes[:16]:
            return "video", ".avi"

    # Contenedor MP4 / M4A / MOV
    if b"ftyp" in primeros_bytes[:32]:
        if extension_url in {".m4a", ".aac"}:
            return "audio", extension_url
        return "video", extension_url or ".mp4"

    # WebM / Matroska
    if primeros_bytes.startswith(b"\x1aE\xdf\xa3"):
        return "video", extension_url or ".webm"

    # ASF: puede ser WMA o WMV
    if primeros_bytes.startswith(b"0&\xb2u\x8ef\xcf\x11"):
        if extension_url == ".wma":
            return "audio", ".wma"
        return "video", ".wmv"

    extensiones_audio = {
        ".mp3", ".wav", ".ogg", ".m4a",
        ".aac", ".flac", ".wma",
    }

    extensiones_video = {
        ".mp4", ".webm", ".mov", ".m4v",
        ".avi", ".wmv", ".mpeg", ".mpg",
    }

    if extension_url in extensiones_audio:
        return "audio", extension_url

    if extension_url in extensiones_video:
        return "video", extension_url

    return None, extension_url or ".bin"


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

    respuesta = requests.get(
        url,
        headers=headers,
        timeout=90,
        allow_redirects=True,
    )
    respuesta.raise_for_status()

    contenido = respuesta.content

    if not contenido:
        raise ValueError("El servidor respondió sin contenido.")

    content_type = respuesta.headers.get(
        "Content-Type",
        "application/octet-stream",
    )

    tipo, extension = detectar_tipo_archivo(
        content_type=content_type,
        url=respuesta.url,
        primeros_bytes=contenido[:64],
    )

    return {
        "contenido": contenido,
        "content_type": content_type,
        "tipo": tipo,
        "extension": extension,
        "url_final": respuesta.url,
        "tamano": len(contenido),
    }


url_parametro = st.query_params.get("url", "")

url = st.text_input(
    "URL del audio o video",
    value=url_parametro,
    placeholder="https://servidor.com/archivo.ashx?hit=...",
)

if url:
    if not url.lower().startswith(("http://", "https://")):
        st.error("La URL debe comenzar con http:// o https://")
        st.stop()

    try:
        with st.spinner("Cargando contenido..."):
            archivo = descargar_archivo(url)

        tamano_mb = archivo["tamano"] / (1024 * 1024)

        st.caption(
            f"Tipo detectado: {archivo['content_type']} · "
            f"Tamaño: {tamano_mb:.2f} MB"
        )

        if archivo["tipo"] == "audio":
            st.audio(
                archivo["contenido"],
                format=archivo["content_type"],
            )

        elif archivo["tipo"] == "video":
            st.video(
                archivo["contenido"],
                format=archivo["content_type"],
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

        st.divider()

        st.write("Enlace directo para compartir:")

        URL_APP = "https://effective-creative-repo.streamlit.app/"

        st.code(
            f"{URL_APP}?url={url}",
            language=None,
        )
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
