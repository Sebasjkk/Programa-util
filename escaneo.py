"""Percorre a pasta de clips e decide jogo, playlist, data e título de cada vídeo."""

import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import config

EXTENSIONES = {".mp4", ".mkv"}
CARPETAS_EXCLUIDAS = {"thumb_cache", "replay_cache"}

DESCONOCIDO = "Desconhecido"
PLAYLIST_HISTORICOS = "Históricos"
PLAYLIST_OBS = "Gravações OBS"
TITULO_OBS = "Gravação OBS"

# Nomes de jogo (em minúsculas) que são substituídos. O ShadowPlay troca o apóstrofo por "_".
ALIAS = {
    "": DESCONOCIDO,
    "unknown": DESCONOCIDO,
    "replay": DESCONOCIDO,
    "no man_s sky": "No Man's Sky",
    "overwatch": "Overwatch 2",
}

# Pastas que não são nome de jogo (para arquivos sem padrão no nome).
CARPETAS_GENERICAS = {
    "clips", "unknown", "videos", "vídeos", "downloads", "thumbnails", "capturas", "gravações",
    "clips medal", "clips discord", "clips comprimidos", "clipscomprimidos",
}

# League of Legends_replay_2024.02.29-12.04 / HITMAN 2_2024.06.29-16.40 / Replay_2024.11.24-16.10 / 2025.05.19-15.42_1
PATRON_SHADOWPLAY = re.compile(
    r"^(?:(?P<juego>.*?)_)?(?:replay_)?"
    r"(?P<y>\d{4})\.(?P<mo>\d{2})\.(?P<d>\d{2})-(?P<h>\d{2})\.(?P<mi>\d{2})(?:_\d+)?$",
    re.IGNORECASE,
)
# MedalTVMinecraft20250206162129
PATRON_MEDAL = re.compile(r"^MedalTV(?P<juego>.*?)(?P<ts>\d{14})$", re.IGNORECASE)
# Overwatch_1cdf577d-da07-4e1b-b7e6-7eae5ffa1e6e (clips do Discord: sem data no nome)
PATRON_DISCORD = re.compile(r"^(?P<juego>.+?)_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
# Highlight - 08-15-2026-12-14 (mês-dia-ano-hora-minuto)
PATRON_HIGHLIGHT = re.compile(r"^Highlight - (?P<mo>\d{2})-(?P<d>\d{2})-(?P<y>\d{4})-(?P<h>\d{2})-(?P<mi>\d{2})$")
# 2025-06-09 08-34-02
PATRON_OBS = re.compile(
    r"^(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2}) (?P<h>\d{2})-(?P<mi>\d{2})-(?P<s>\d{2})$"
)
# o forte leva - repo / oxe menina - dbd (nome escolhido à mão: o jogo vem depois do último " - ")
PATRON_NOMBRE_JUEGO = re.compile(r"^(?P<nombre>.+) - (?P<juego>[^-]*[^\W\d_][^-]*)$")


@dataclass
class Clip:
    ruta: Path
    tamano: int
    juego: str
    playlist: str
    fecha: datetime
    origen: str  # shadowplay / medal / obs / discord / highlight / nome_jogo / sem_padrao
    titulo: str = ""
    nombre_propio: str = ""  # título escolhido no nome do arquivo (se tiver)

    @property
    def titulo_base(self) -> str:
        if self.nombre_propio:
            return self.nombre_propio[:100]  # limite de título do YouTube
        nombre = TITULO_OBS if self.playlist == PLAYLIST_OBS else self.juego
        return f"{nombre} - {self.fecha:%Y-%m-%d %H.%M}"


def _es_comprimido(ruta: Path) -> bool:
    return any("comprimido" in parte.lower() for parte in ruta.parts)


def _es_historico(ruta: Path) -> bool:
    return bool(config.PASTA_HISTORICOS) and any(parte.lower() == config.PASTA_HISTORICOS for parte in ruta.parts)


def _carpeta_como_juego(ruta: Path, raiz: Path) -> str:
    if ruta.parent == raiz:
        return ""
    carpeta = ruta.parent.name
    if carpeta.lower() in CARPETAS_GENERICAS or "comprimido" in carpeta.lower():
        return ""
    return carpeta


def _normalizar(juego: str) -> str:
    juego = juego.strip()
    return ALIAS.get(juego.lower(), juego)


def _analizar(ruta: Path, tamano: int, raiz: Path) -> Clip:
    nombre = ruta.stem
    playlist = None
    nombre_propio = ""

    if m := PATRON_SHADOWPLAY.match(nombre):
        juego = m["juego"] or ""
        fecha = datetime(int(m["y"]), int(m["mo"]), int(m["d"]), int(m["h"]), int(m["mi"]))
        origen = "shadowplay"
    elif m := PATRON_MEDAL.match(nombre):
        juego = _carpeta_como_juego(ruta, raiz) or m["juego"]
        fecha = datetime.strptime(m["ts"], "%Y%m%d%H%M%S")
        origen = "medal"
    elif m := PATRON_OBS.match(nombre):
        juego = DESCONOCIDO
        playlist = PLAYLIST_OBS
        fecha = datetime(int(m["y"]), int(m["mo"]), int(m["d"]), int(m["h"]), int(m["mi"]), int(m["s"]))
        origen = "obs"
    elif m := PATRON_DISCORD.match(nombre):
        juego = m["juego"]
        fecha = datetime.fromtimestamp(ruta.stat().st_mtime).replace(microsecond=0)
        origen = "discord"
    elif m := PATRON_HIGHLIGHT.match(nombre):
        juego = _carpeta_como_juego(ruta, raiz)
        fecha = datetime(int(m["y"]), int(m["mo"]), int(m["d"]), int(m["h"]), int(m["mi"]))
        origen = "highlight"
    elif m := PATRON_NOMBRE_JUEGO.match(nombre):
        juego = m["juego"]
        nombre_propio = nombre.strip()
        fecha = datetime.fromtimestamp(ruta.stat().st_mtime).replace(microsecond=0)
        origen = "nome_jogo"
    else:
        juego = _carpeta_como_juego(ruta, raiz)
        fecha = datetime.fromtimestamp(ruta.stat().st_mtime).replace(microsecond=0)
        origen = "sem_padrao"

    juego = _normalizar(juego)
    if _es_historico(ruta):
        juego, playlist = DESCONOCIDO, PLAYLIST_HISTORICOS
    return Clip(ruta, tamano, juego, playlist or juego, fecha, origen, nombre_propio=nombre_propio)


def _unificar_mayusculas(clips: list[Clip]) -> None:
    """'HITMAN 2' e 'Hitman 2' vão para a mesma playlist: ganha a grafia mais usada."""
    variantes = defaultdict(Counter)
    for c in clips:
        variantes[c.juego.lower()][c.juego] += 1
    elegido = {clave: cont.most_common(1)[0][0] for clave, cont in variantes.items()}
    for c in clips:
        nuevo = elegido[c.juego.lower()]
        if c.playlist == c.juego:
            c.playlist = nuevo
        c.juego = nuevo


def escanear(raiz: Path, ya_subidos: dict[tuple[str, int], str] | None = None
             ) -> tuple[list[Clip], list[tuple[Path, str, str | None]]]:
    """Devolve (clips a enviar, [(caminho ignorado, motivo, caminho do original se for cópia idêntica)]).
    `ya_subidos` = {(nome em minúsculas, tamanho): caminho} de originais já enviados e apagados,
    para que as cópias duplicadas deles não sejam tratadas como vídeos novos."""
    encontrados = []
    for carpeta, subcarpetas, archivos in os.walk(raiz):
        subcarpetas[:] = [s for s in subcarpetas if s.lower() not in CARPETAS_EXCLUIDAS]
        for archivo in archivos:
            ruta = Path(carpeta) / archivo
            if ruta.suffix.lower() in EXTENSIONES:
                encontrados.append((ruta, ruta.stat().st_size))

    ya_subidos = ya_subidos or {}
    nombres_originales = {r.name.lower() for r, _ in encontrados if not _es_comprimido(r)}
    nombres_originales |= {nombre for nombre, _ in ya_subidos}
    # Primeiro os originais e os caminhos mais curtos, assim a cópia que fica é a "principal".
    encontrados.sort(key=lambda x: (_es_comprimido(x[0]), len(x[0].parts), str(x[0]).lower()))

    clips, omitidos, vistos = [], [], dict(ya_subidos)
    for ruta, tamano in encontrados:
        clave = (ruta.name.lower(), tamano)
        if _es_comprimido(ruta) and ruta.name.lower() in nombres_originales:
            omitidos.append((ruta, "versão comprimida de um clip que já está no original", None))
        elif clave in vistos:
            omitidos.append((ruta, f"cópia idêntica de {vistos[clave]}", str(vistos[clave])))
        else:
            vistos[clave] = ruta
            clips.append(_analizar(ruta, tamano, raiz))

    _unificar_mayusculas(clips)
    clips.sort(key=lambda c: (c.fecha, str(c.ruta).lower()))
    return clips, omitidos


def asignar_titulos(clips: list[Clip], ocupados: set[str]) -> None:
    """Dá títulos únicos (sem diferenciar maiúsculas) acrescentando ' (2)', ' (3)'... se precisar.
    `ocupados` é atualizado com os títulos atribuídos."""
    for c in clips:
        titulo, n = c.titulo_base, 2
        while titulo.casefold() in ocupados:
            titulo = f"{c.titulo_base} ({n})"
            n += 1
        c.titulo = titulo
        ocupados.add(titulo.casefold())
