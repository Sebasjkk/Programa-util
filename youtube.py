"""Acesso à API do YouTube: login, envio, playlists e lista de vídeos do canal."""

import json
from pathlib import Path
from typing import Callable, Iterator

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from config import CATEGORIA_GAMING, CLIENT_SECRET, PRIVACIDAD, TOKEN

SCOPES = ["https://www.googleapis.com/auth/youtube"]

MOTIVOS_LIMITE = {"quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded", "rateLimitExceeded"}
TITULOS_OCULTOS = {"Private video", "Deleted video", "Vídeo privado", "Vídeo excluído",
                   "Video privado", "Video eliminado"}
MIME = {".mp4": "video/mp4", ".mkv": "video/x-matroska"}
TAMANO_TROZO = 16 * 1024 * 1024


class LimiteAlcanzado(Exception):
    """O YouTube não deixa fazer mais chamadas/envios hoje."""


def motivo(error: HttpError) -> str:
    try:
        return json.loads(error.content)["error"]["errors"][0]["reason"]
    except Exception:
        return ""


def _credenciales() -> Credentials:
    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            creds = None  # no modo "Teste" o token vence em 7 dias
    if not creds or not creds.valid:
        if not CLIENT_SECRET.exists():
            raise SystemExit(f"Falta o {CLIENT_SECRET.name} na pasta do programa ({CLIENT_SECRET.parent}).\n"
                             "Veja no README como gerar esse arquivo no Google Cloud.")
        print("O navegador vai abrir para você entrar na sua conta do YouTube...")
        flujo = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
        creds = flujo.run_local_server(port=0)
    TOKEN.write_text(creds.to_json())
    return creds


class YouTube:
    def __init__(self, al_gastar: Callable[[int], None]):
        """`al_gastar(unidades)` é chamado a cada chamada que consome cota geral."""
        self.api = build("youtube", "v3", credentials=_credenciales())
        self.al_gastar = al_gastar

    def _ejecutar(self, pedido, unidades: int):
        try:
            respuesta = pedido.execute(num_retries=5)
        except HttpError as e:
            if motivo(e) in MOTIVOS_LIMITE:
                raise LimiteAlcanzado(motivo(e)) from e
            if motivo(e) == "accessNotConfigured":
                raise SystemExit(
                    "A YouTube Data API v3 não está ativada no projeto do Google Cloud.\n"
                    "Ative em https://console.cloud.google.com/apis/library/youtube.googleapis.com, "
                    "espere alguns minutos e tente de novo."
                ) from e
            raise
        self.al_gastar(unidades)
        return respuesta

    def _paginar(self, metodo, **params) -> Iterator[dict]:
        token = None
        while True:
            respuesta = self._ejecutar(metodo(maxResults=50, pageToken=token, **params), 1)
            yield from respuesta.get("items", [])
            token = respuesta.get("nextPageToken")
            if not token:
                return

    # --- vídeos do canal --------------------------------------------------

    def videos_del_canal(self) -> list[tuple[str, str]]:
        """[(video_id, título)] de tudo o que está no canal, incluindo os privados."""
        canal = self._ejecutar(self.api.channels().list(part="contentDetails", mine=True), 1)
        if not canal.get("items"):
            raise SystemExit(
                "A conta com que você entrou não tem canal no YouTube.\n"
                "Crie o canal em https://www.youtube.com/create_channel, ou apague o token.json "
                "e entre com a conta do canal."
            )
        subidos = canal["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        videos = [
            (item["snippet"]["resourceId"]["videoId"], item["snippet"]["title"])
            for item in self._paginar(self.api.playlistItems().list, part="snippet", playlistId=subidos)
        ]
        # Caso a lista devolva um título genérico em vez do real, eles são pedidos de novo.
        ocultos = [vid for vid, titulo in videos if titulo in TITULOS_OCULTOS]
        reales = {}
        for i in range(0, len(ocultos), 50):
            respuesta = self._ejecutar(self.api.videos().list(part="snippet", id=",".join(ocultos[i:i + 50])), 1)
            reales.update({v["id"]: v["snippet"]["title"] for v in respuesta.get("items", [])})
        return [(vid, reales.get(vid, titulo)) for vid, titulo in videos]

    def estados_de_proceso(self, ids: list[str]) -> dict[str, str]:
        """{video_id: uploadStatus} — 'processed' significa que o YouTube terminou de processar sem erro."""
        estados = {}
        for i in range(0, len(ids), 50):
            respuesta = self._ejecutar(self.api.videos().list(part="status", id=",".join(ids[i:i + 50])), 1)
            estados.update({v["id"]: v["status"]["uploadStatus"] for v in respuesta.get("items", [])})
        return estados

    # --- playlists --------------------------------------------------------

    def playlists_del_canal(self) -> dict[str, str]:
        """{título: playlist_id}"""
        return {
            p["snippet"]["title"]: p["id"]
            for p in self._paginar(self.api.playlists().list, part="snippet", mine=True)
        }

    def crear_playlist(self, nombre: str) -> str:
        cuerpo = {"snippet": {"title": nombre}, "status": {"privacyStatus": PRIVACIDAD}}
        return self._ejecutar(self.api.playlists().insert(part="snippet,status", body=cuerpo), 50)["id"]

    def videos_en_playlist(self, playlist_id: str) -> set[str]:
        return {
            item["contentDetails"]["videoId"]
            for item in self._paginar(self.api.playlistItems().list, part="contentDetails", playlistId=playlist_id)
        }

    def agregar_a_playlist(self, playlist_id: str, video_id: str) -> None:
        cuerpo = {"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
        self._ejecutar(self.api.playlistItems().insert(part="snippet", body=cuerpo), 50)

    # --- envio ------------------------------------------------------------

    def subir_video(self, ruta: Path, titulo: str, descripcion: str, fecha_iso: str,
                    progreso: Callable[[float], None]) -> str:
        """Envia o arquivo e devolve o video_id. Erros de rede/5xx são repetidos automaticamente."""
        cuerpo = {
            "snippet": {"title": titulo, "description": descripcion, "categoryId": CATEGORIA_GAMING},
            "status": {"privacyStatus": PRIVACIDAD, "selfDeclaredMadeForKids": False},
            "recordingDetails": {"recordingDate": fecha_iso},
        }
        media = MediaFileUpload(str(ruta), mimetype=MIME.get(ruta.suffix.lower(), "application/octet-stream"),
                                chunksize=TAMANO_TROZO, resumable=True)
        pedido = self.api.videos().insert(part="snippet,status,recordingDetails", body=cuerpo, media_body=media)
        respuesta = None
        try:
            while respuesta is None:
                estado, respuesta = pedido.next_chunk(num_retries=10)
                if estado:
                    progreso(estado.progress())
        except HttpError as e:
            if motivo(e) in MOTIVOS_LIMITE:
                raise LimiteAlcanzado(motivo(e)) from e
            raise
        return respuesta["id"]
