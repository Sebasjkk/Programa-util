"""Estado persistente (SQLite): quais vídeos foram enviados, em qual playlist, e quanta cota foi usada hoje."""

import sqlite3
from datetime import datetime, timedelta, timezone

from config import DB

PENDIENTE = "pendente"           # falta enviar (tem hardlink na pasta de trabalho)
RESERVADO_API = "reservado_api"  # a API está enviando (foi tirado da pasta de trabalho)
SUBIDO = "enviado"               # está no canal, falta colocar na playlist
EN_PLAYLIST = "na_playlist"      # concluído
ERROR = "erro"                   # a API não conseguiu enviar; fica na pasta para enviar à mão

ESQUEMA = """
CREATE TABLE IF NOT EXISTS videos (
    ruta        TEXT PRIMARY KEY,
    tamano      INTEGER NOT NULL,
    juego       TEXT NOT NULL,
    playlist    TEXT NOT NULL,
    fecha       TEXT NOT NULL,
    titulo      TEXT NOT NULL,
    estado      TEXT NOT NULL DEFAULT 'pendente',
    video_id    TEXT,
    error       TEXT,
    actualizado TEXT
);
CREATE TABLE IF NOT EXISTS playlists (
    nombre      TEXT PRIMARY KEY,
    playlist_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS copias (
    ruta           TEXT PRIMARY KEY,  -- cópia idêntica que não é enviada
    ruta_principal TEXT NOT NULL      -- o arquivo que é enviado (videos.ruta)
);
CREATE TABLE IF NOT EXISTS cuota (
    dia      TEXT PRIMARY KEY,
    unidades INTEGER NOT NULL DEFAULT 0,
    subidas  INTEGER NOT NULL DEFAULT 0
);
"""


def conectar() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(ESQUEMA)
    return con


def cambiar_estado(con, ruta: str, estado: str, video_id: str | None = None, error: str | None = None):
    with con:
        con.execute(
            "UPDATE videos SET estado = ?, video_id = COALESCE(?, video_id), error = ?, actualizado = ? "
            "WHERE ruta = ?",
            (estado, video_id, error, datetime.now().isoformat(timespec="seconds"), ruta),
        )


def _dia_pacifico() -> str:
    """A cota do YouTube reinicia à meia-noite da Califórnia."""
    try:
        from zoneinfo import ZoneInfo
        ahora = datetime.now(ZoneInfo("America/Los_Angeles"))
    except Exception:
        ahora = datetime.now(timezone(timedelta(hours=-8)))
    return ahora.date().isoformat()


def registrar_cuota(con, unidades: int = 0, subidas: int = 0):
    with con:
        con.execute(
            "INSERT INTO cuota (dia, unidades, subidas) VALUES (?, ?, ?) "
            "ON CONFLICT(dia) DO UPDATE SET unidades = unidades + excluded.unidades, "
            "subidas = subidas + excluded.subidas",
            (_dia_pacifico(), unidades, subidas),
        )


def cuota_de_hoy(con) -> tuple[int, int]:
    """(unidades usadas, envios feitos) no dia de cota atual."""
    fila = con.execute("SELECT unidades, subidas FROM cuota WHERE dia = ?", (_dia_pacifico(),)).fetchone()
    return (fila["unidades"], fila["subidas"]) if fila else (0, 0)
