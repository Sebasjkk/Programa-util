"""Envia os clips de jogos para o YouTube (uma playlist por jogo), coordenando o envio
automático pela API (100 por dia) com envios manuais pelo YouTube Studio.

Comandos:
    enviar_clips configurar     escolhe a pasta dos clips e as opções
    enviar_clips escanear       monta a lista de vídeos e o manifesto.csv
    enviar_clips preparar       cria a pasta de trabalho com hardlinks renomeados
    enviar_clips sincronizar    olha o canal: marca o que foi enviado à mão, monta playlists
    enviar_clips enviar         envia pela API até o limite diário
    enviar_clips status         resumo do progresso
"""

import argparse
import csv
import filecmp
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import config
import escaneo
import estado
from config import (CARPETA_PROGRAMA, EMPAQUETADO, LIMITE_SUBIDAS_DIA, LIMITE_UNIDADES_DIA, LOCK, LOG,
                    MANIFIESTO, SINCRONIZAR_CADA)
from estado import EN_PLAYLIST, ERROR, PENDIENTE, RESERVADO_API, SUBIDO

log = logging.getLogger("enviar_clips")


def _configurar_log():
    log.setLevel(logging.INFO)
    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(logging.Formatter("%(message)s"))
    archivo = logging.FileHandler(LOG, encoding="utf-8")
    archivo.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(consola)
    log.addHandler(archivo)


# --- pasta de trabalho (hardlinks) --------------------------------------------

def ruta_enlace(fila) -> Path:
    # O Windows corta pontos/espaços no fim do nome de uma pasta ("R.E.P.O." -> "R.E.P.O").
    carpeta = fila["playlist"].rstrip(". ")
    return config.CARPETA_PARA_SUBIR / carpeta / (fila["titulo"] + Path(fila["ruta"]).suffix.lower())


def quitar_enlace(enlace: Path) -> bool:
    """Apaga um hardlink da pasta de trabalho. Nunca apaga um arquivo que seja a única cópia."""
    if not enlace.exists():
        return False
    if config.CARPETA_PARA_SUBIR.resolve() not in enlace.resolve().parents:
        raise RuntimeError(f"Tentativa de apagar algo fora da pasta de trabalho: {enlace}")
    if os.stat(enlace).st_nlink < 2:
        log.warning("%s não foi apagado: não é um hardlink (seria a única cópia).", enlace)
        return False
    enlace.unlink()
    return True


def crear_enlace(fila) -> bool:
    enlace, original = ruta_enlace(fila), Path(fila["ruta"])
    if enlace.exists() or not original.exists():
        return False
    enlace.parent.mkdir(parents=True, exist_ok=True)
    os.link(original, enlace)
    return True


def reconciliar_carpeta(con) -> tuple[int, int]:
    """Deixa na pasta de trabalho exatamente os vídeos que faltam enviar. Devolve (criados, apagados)."""
    esperados = {
        os.path.normcase(ruta_enlace(f)): f
        for f in con.execute("SELECT * FROM videos WHERE estado IN (?, ?)", (PENDIENTE, ERROR))
    }
    borrados = creados = 0
    if config.CARPETA_PARA_SUBIR.exists():
        for archivo in [p for p in config.CARPETA_PARA_SUBIR.rglob("*") if p.is_file()]:
            if os.path.normcase(archivo) not in esperados and quitar_enlace(archivo):
                borrados += 1
    for fila in esperados.values():
        creados += crear_enlace(fila)
    if config.CARPETA_PARA_SUBIR.exists():
        for carpeta in sorted((p for p in config.CARPETA_PARA_SUBIR.iterdir() if p.is_dir()), reverse=True):
            if not any(carpeta.iterdir()):
                carpeta.rmdir()
    return creados, borrados


# --- configurar -------------------------------------------------------------

def cmd_configurar(_args):
    atual = config.RAIZ_CLIPS
    print("Em qual pasta estão os seus clips? Cole o caminho completo (ex.: D:\\Vídeos\\Clips).")
    print("Dica: no Explorador, clique com o botão direito na pasta > Copiar como caminho.")
    if atual:
        print(f"Atual: {atual}  (Enter para manter)")
    while True:
        resposta = input("Pasta dos clips: ").strip().strip('"')
        if not resposta and atual:
            pasta = atual
            break
        pasta = Path(resposta)
        if resposta and pasta.is_absolute() and pasta.is_dir():
            break
        print("Essa pasta não existe (use o caminho completo, com a letra do disco). Tente de novo.")

    print("\nQuando o YouTube terminar de processar um vídeo, o programa pode apagar o arquivo original")
    print("do PC para liberar espaço. Se fizer isso, o YouTube passa a ser o único lugar com o clip.")
    padrao = "s/N" if not config.APAGAR_ORIGINAIS else "S/n"
    resposta = input(f"Apagar os originais depois de enviados? [{padrao}]: ").strip().lower()
    apagar = config.APAGAR_ORIGINAIS if not resposta else resposta in ("s", "sim")

    config.salvar(pasta, apagar)
    print(f"\nConfiguração salva em {config.CONFIG}")
    print(f"  Pasta dos clips:     {config.RAIZ_CLIPS}")
    print(f"  Pasta de trabalho:   {config.CARPETA_PARA_SUBIR}")
    print(f"  Apagar originais:    {'sim' if config.APAGAR_ORIGINAIS else 'não'}")


# --- escanear ---------------------------------------------------------------

def cmd_escanear(_args):
    con = estado.conectar()
    filas = {f["ruta"]: f for f in con.execute("SELECT * FROM videos")}
    en_youtube = {ruta: f for ruta, f in filas.items() if f["estado"] in (SUBIDO, EN_PLAYLIST)}
    # Originais já enviados que não estão mais no caminho (foram apagados, ou o programa mudou de PC):
    # as cópias deles não são enviadas de novo.
    ya_subidos = {(Path(ruta).name.lower(), f["tamano"]): ruta
                  for ruta, f in en_youtube.items() if not Path(ruta).exists()}
    clips, omitidos = escaneo.escanear(config.RAIZ_CLIPS, ya_subidos)

    # O que já está no YouTube (ou tem arquivo e não está pendente) mantém a linha e o título;
    # o resto é recalculado com o escaneamento.
    fijos = {ruta: f for ruta, f in filas.items()
             if ruta in en_youtube or (f["estado"] != PENDIENTE and Path(ruta).exists())}
    ocupados = {f["titulo"].casefold() for f in fijos.values()}
    a_guardar = [c for c in clips if str(c.ruta) not in fijos]
    escaneo.asignar_titulos(a_guardar, ocupados)

    en_escaneo = {str(c.ruta) for c in clips}
    with con:
        for ruta in filas:
            if ruta not in fijos and ruta not in en_escaneo:
                con.execute("DELETE FROM videos WHERE ruta = ?", (ruta,))
        con.executemany(
            "INSERT INTO videos (ruta, tamano, juego, playlist, fecha, titulo) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ruta) DO UPDATE SET tamano = excluded.tamano, juego = excluded.juego, "
            "playlist = excluded.playlist, fecha = excluded.fecha, titulo = excluded.titulo",
            [(str(c.ruta), c.tamano, c.juego, c.playlist, c.fecha.isoformat(), c.titulo) for c in a_guardar],
        )
        con.execute("DELETE FROM copias")
        con.executemany("INSERT INTO copias (ruta, ruta_principal) VALUES (?, ?)",
                        [(str(ruta), principal) for ruta, _, principal in omitidos if principal])

    filas = con.execute("SELECT * FROM videos ORDER BY playlist, fecha").fetchall()
    with open(MANIFIESTO, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["titulo", "playlist", "jogo", "data", "tamanho_mb", "estado", "caminho_original"])
        for fila in filas:
            w.writerow([fila["titulo"], fila["playlist"], fila["juego"], fila["fecha"],
                        round(fila["tamano"] / 2**20, 1), fila["estado"], fila["ruta"]])
        for ruta, motivo, _ in omitidos:
            w.writerow(["", "", "", "", round(ruta.stat().st_size / 2**20, 1), f"ignorado: {motivo}", str(ruta)])

    por_playlist = defaultdict(lambda: [0, 0])
    for fila in filas:
        por_playlist[fila["playlist"]][0] += 1
        por_playlist[fila["playlist"]][1] += fila["tamano"]
    print(f"{'Playlist':<45}{'Vídeos':>7}{'GB':>8}")
    for nombre, (n, bytes_) in sorted(por_playlist.items(), key=lambda x: -x[1][0]):
        print(f"{nombre:<45}{n:>7}{bytes_ / 2**30:>8.2f}")
    total = sum(f["tamano"] for f in filas)
    print(f"\nTotal: {len(filas)} vídeos, {total / 2**30:.1f} GB")
    if omitidos:
        motivos = Counter(m.split(" de ")[0] for _, m, _ in omitidos)
        print(f"Ignorados: {len(omitidos)} ({', '.join(f'{n} {m}' for m, n in motivos.items())})")
    sin_patron = [c for c in clips if c.origen == "sem_padrao"]
    if sin_patron:
        print("Sem data no nome (é usada a data de modificação):")
        for c in sin_patron:
            print(f"  {c.ruta.relative_to(config.RAIZ_CLIPS)} -> {c.titulo}")
    print(f"\nDetalhes em {MANIFIESTO}")


# --- preparar ---------------------------------------------------------------

def cmd_preparar(_args):
    if (os.path.splitdrive(config.CARPETA_PARA_SUBIR)[0].lower()
            != os.path.splitdrive(config.RAIZ_CLIPS)[0].lower()):
        raise SystemExit("A pasta de trabalho tem que estar no mesmo disco que os clips (hardlinks).")
    con = estado.conectar()
    creados, borrados = reconciliar_carpeta(con)
    faltan = con.execute("SELECT COUNT(*) FROM videos WHERE estado IN (?, ?)", (PENDIENTE, ERROR)).fetchone()[0]
    print(f"Pasta de trabalho: {config.CARPETA_PARA_SUBIR}")
    print(f"  {creados} links criados, {borrados} apagados, {faltan} vídeos para enviar.")
    print("  Não ocupa espaço extra: são hardlinks dos originais.")
    print("  Se os seus clips forem arquivos ocultos, estes também serão: no Explorador, ative")
    print("  Exibir > Mostrar > Itens ocultos para vê-los e arrastá-los para o YouTube Studio.")


# --- sincronizar ------------------------------------------------------------

def _hay_cuota(con, unidades: int) -> bool:
    return estado.cuota_de_hoy(con)[0] + unidades <= LIMITE_UNIDADES_DIA


def _playlist_id(con, yt, nombre: str, cache_canal: dict) -> tuple[str | None, bool]:
    """(playlist_id, recém_criada). Cria a playlist se não existir e houver cota."""
    fila = con.execute("SELECT playlist_id FROM playlists WHERE nombre = ?", (nombre,)).fetchone()
    if fila:
        return fila["playlist_id"], False
    if "todas" not in cache_canal:
        cache_canal["todas"] = yt.playlists_del_canal()
    pl_id, creada = cache_canal["todas"].get(nombre), False
    if pl_id is None:
        if not _hay_cuota(con, 50):
            return None, False
        pl_id, creada = yt.crear_playlist(nombre), True
        log.info("Playlist criada: %s", nombre)
    with con:
        con.execute("INSERT OR REPLACE INTO playlists (nombre, playlist_id) VALUES (?, ?)", (nombre, pl_id))
    return pl_id, creada


def _olvidar_playlist(con, nombre: str):
    with con:
        con.execute("DELETE FROM playlists WHERE nombre = ?", (nombre,))


def completar_playlists(con, yt) -> tuple[int, int]:
    """Coloca na playlist os vídeos enviados. Devolve (adicionados, pendentes por falta de cota)."""
    from googleapiclient.errors import HttpError

    grupos = defaultdict(list)
    for f in con.execute("SELECT * FROM videos WHERE estado = ? ORDER BY fecha", (SUBIDO,)):
        grupos[f["playlist"]].append(f)
    agregados = sin_cuota = 0
    cache_canal = {}
    for nombre, filas in grupos.items():
        for intento in range(2):
            pl_id, creada = _playlist_id(con, yt, nombre, cache_canal)
            if pl_id is None:
                break
            try:
                ya_estan = set() if creada else yt.videos_en_playlist(pl_id)
                break
            except HttpError as e:
                if e.resp.status != 404 or intento:
                    raise
                _olvidar_playlist(con, nombre)  # foi apagada à mão: é buscada/criada de novo
                cache_canal.clear()
        if pl_id is None:
            sin_cuota += len(filas)
            continue
        for f in filas:
            if f["video_id"] not in ya_estan:
                if not _hay_cuota(con, 50):
                    sin_cuota += 1
                    continue
                yt.agregar_a_playlist(pl_id, f["video_id"])
                agregados += 1
            estado.cambiar_estado(con, f["ruta"], EN_PLAYLIST)
    return agregados, sin_cuota


def borrar_originales_subidos(con, yt) -> int:
    """Se a opção estiver ativada, apaga da pasta de clips os arquivos cujo vídeo o YouTube já terminou
    de processar sem erro, junto com as cópias. Se ainda estiver processando, ou falhou, não apaga nada."""
    if not config.APAGAR_ORIGINAIS:
        return 0
    candidatos = []
    for f in con.execute("SELECT * FROM videos WHERE estado IN (?, ?) AND video_id IS NOT NULL",
                         (SUBIDO, EN_PLAYLIST)).fetchall():
        copias = [Path(r) for (r,) in con.execute("SELECT ruta FROM copias WHERE ruta_principal = ?", (f["ruta"],))]
        copias = [c for c in copias if c.exists()]
        if Path(f["ruta"]).exists() or copias:
            candidatos.append((f, copias))
    if not candidatos:
        return 0
    estados_yt = yt.estados_de_proceso([f["video_id"] for f, _ in candidatos])
    raiz = config.RAIZ_CLIPS.resolve()
    borrados = 0
    for f, copias in candidatos:
        original, estado_yt = Path(f["ruta"]), estados_yt.get(f["video_id"])
        if estado_yt in ("failed", "rejected"):
            log.warning("O YouTube não conseguiu processar '%s' (%s): o original NÃO foi apagado. "
                        "https://youtu.be/%s", f["titulo"], estado_yt, f["video_id"])
            continue
        if estado_yt != "processed":
            continue  # ainda está processando; é apagado na próxima sincronização
        if original.exists():
            if raiz not in original.resolve().parents or original.stat().st_size != f["tamano"]:
                log.warning("%s não foi apagado: não bate com o arquivo registrado.", original)
                continue
            # As cópias são comparadas byte a byte com o original antes de serem apagadas.
            a_borrar = [c for c in copias if filecmp.cmp(c, original, shallow=False)] + [original]
        else:
            # O original já não existe (foi apagado antes ou é outro PC): a cópia tem que
            # bater em nome e tamanho com o vídeo enviado.
            a_borrar = [c for c in copias
                        if c.name.lower() == original.name.lower() and c.stat().st_size == f["tamano"]]
        for archivo in a_borrar:
            if raiz not in archivo.resolve().parents:
                continue
            try:
                archivo.unlink()
            except OSError as e:
                log.warning("Não foi possível apagar %s: %s", archivo, e)
                continue
            borrados += 1
            log.info("Apagado (já está no YouTube): %s", archivo.relative_to(config.RAIZ_CLIPS))
    return borrados


def sincronizar(con, yt, reiniciar_reservados: bool):
    """Compara o canal com a base pelo título: detecta envios manuais, duplicados e monta playlists."""
    por_titulo = defaultdict(list)
    for vid, titulo in yt.videos_del_canal():
        por_titulo[titulo.strip().casefold()].append(vid)
    ids_en_canal = {vid for ids in por_titulo.values() for vid in ids}

    detectados, perdidos, duplicados = 0, [], []
    for f in con.execute("SELECT * FROM videos").fetchall():
        ids = por_titulo.get(f["titulo"].casefold(), [])
        if len(ids) > 1:
            duplicados.append((f["titulo"], ids))
        if f["estado"] in (PENDIENTE, ERROR, RESERVADO_API) and ids:
            estado.cambiar_estado(con, f["ruta"], SUBIDO, video_id=ids[0])
            detectados += 1
        elif f["estado"] == RESERVADO_API and reiniciar_reservados:
            estado.cambiar_estado(con, f["ruta"], PENDIENTE)  # envio pela API que ficou pela metade
        elif f["estado"] in (SUBIDO, EN_PLAYLIST) and f["video_id"] not in ids_en_canal:
            if ids:  # a cópia registrada foi apagada, mas existe outra
                estado.cambiar_estado(con, f["ruta"], SUBIDO, video_id=ids[0])
            elif datetime.now() - datetime.fromisoformat(f["actualizado"]) > timedelta(hours=1):
                perdidos.append(f["titulo"])  # o que acabou de ser enviado demora para aparecer na lista

    agregados, sin_cuota = completar_playlists(con, yt)
    reconciliar_carpeta(con)
    borrados = borrar_originales_subidos(con, yt)

    if borrados:
        log.info("Sincronização: %d originais apagados (já processados no YouTube).", borrados)
    if detectados:
        log.info("Sincronização: %d vídeos novos detectados no canal (enviados à mão ou pela API).", detectados)
    if agregados:
        log.info("Sincronização: %d vídeos adicionados à playlist.", agregados)
    if sin_cuota:
        log.info("Sincronização: %d vídeos ficam sem playlist até amanhã (cota do dia).", sin_cuota)
    if duplicados:
        log.warning("Há %d títulos duplicados no canal (apague as cópias que sobram no YouTube Studio):",
                    len(duplicados))
        for titulo, ids in duplicados:
            log.warning("  %s -> %s", titulo, ", ".join(f"https://youtu.be/{i}" for i in ids))
    if perdidos:
        log.warning("%d vídeos marcados como enviados não aparecem no canal (se acabaram de ser enviados, "
                    "pode ser demora do YouTube): %s", len(perdidos), ", ".join(perdidos[:10]))


def _conectar_youtube(con):
    from youtube import YouTube
    return YouTube(al_gastar=lambda unidades: estado.registrar_cuota(con, unidades=unidades))


def cmd_sincronizar(_args):
    from youtube import LimiteAlcanzado
    con = estado.conectar()
    try:
        sincronizar(con, _conectar_youtube(con), reiniciar_reservados=False)
    except LimiteAlcanzado:
        log.warning("A cota da API acabou por hoje; continua amanhã.")
    _imprimir_estado(con)


# --- enviar -----------------------------------------------------------------

def _siguiente_pendiente(con):
    # Do mais novo para o mais antigo (à mão convém ir ao contrário, assim não se cruzam).
    return con.execute("SELECT * FROM videos WHERE estado = ? ORDER BY fecha DESC, ruta DESC LIMIT 1",
                       (PENDIENTE,)).fetchone()


def _agregar_a_su_playlist(con, yt, fila, video_id):
    from googleapiclient.errors import HttpError
    from youtube import LimiteAlcanzado
    try:
        pl_id, _ = _playlist_id(con, yt, fila["playlist"], {})
        if pl_id and _hay_cuota(con, 50):
            yt.agregar_a_playlist(pl_id, video_id)
            estado.cambiar_estado(con, fila["ruta"], EN_PLAYLIST)
    except (LimiteAlcanzado, HttpError) as e:
        log.info("  Não deu para adicionar à playlist agora (%s); tenta de novo ao sincronizar.", e)


def _bloquear_otra_ejecucion():
    """Evita dois 'enviar' ao mesmo tempo (é liberado sozinho quando o processo termina)."""
    import msvcrt
    global _candado
    _candado = open(LOCK, "w")
    try:
        msvcrt.locking(_candado.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit("Já tem outro 'enviar' rodando (talvez a tarefa diária). Espere ele terminar.")


def cmd_subir(args):
    from googleapiclient.errors import HttpError
    from youtube import LimiteAlcanzado

    if not args.dry_run:
        _bloquear_otra_ejecucion()
    con = estado.conectar()
    if args.reintentar_errores:
        with con:
            con.execute("UPDATE videos SET estado = ?, error = NULL WHERE estado = ?", (PENDIENTE, ERROR))

    if args.dry_run:
        filas = con.execute("SELECT * FROM videos WHERE estado = ? ORDER BY fecha DESC, ruta DESC LIMIT ?",
                            (PENDIENTE, args.limite or LIMITE_SUBIDAS_DIA)).fetchall()
        for f in filas:
            print(f"{f['titulo']}  ->  playlist '{f['playlist']}'  ({f['tamano'] / 2**20:.0f} MB)")
        print(f"\n{len(filas)} vídeos seriam enviados (simulação, nada foi enviado).")
        return

    yt = _conectar_youtube(con)
    try:
        sincronizar(con, yt, reiniciar_reservados=True)
    except LimiteAlcanzado:
        log.warning("Cota geral esgotada: envia mesmo assim, as playlists são completadas amanhã.")

    cupo = LIMITE_SUBIDAS_DIA - estado.cuota_de_hoy(con)[1]
    if args.limite:
        cupo = min(cupo, args.limite)
    if cupo <= 0:
        log.info("Os %d envios pela API de hoje já foram usados. Rode de novo amanhã.", LIMITE_SUBIDAS_DIA)
        return

    hechos = 0
    while hechos < cupo and (fila := _siguiente_pendiente(con)):
        original = Path(fila["ruta"])
        if not original.exists():
            estado.cambiar_estado(con, fila["ruta"], ERROR, error="o arquivo original não existe mais")
            continue

        estado.cambiar_estado(con, fila["ruta"], RESERVADO_API)
        quitar_enlace(ruta_enlace(fila))  # assim não é enviado à mão ao mesmo tempo
        log.info("[%d/%d] %s (%s, %.0f MB)", hechos + 1, cupo, fila["titulo"], fila["playlist"],
                 fila["tamano"] / 2**20)

        def devolver(nuevo_estado, error=None):
            estado.cambiar_estado(con, fila["ruta"], nuevo_estado, error=error)
            crear_enlace(fila)

        try:
            video_id = yt.subir_video(
                original, fila["titulo"], f"Arquivo original: {original.name}",
                datetime.fromisoformat(fila["fecha"]).astimezone().isoformat(),
                lambda p: print(f"   {p:6.1%}", end="\r", flush=True),
            )
        except LimiteAlcanzado as e:
            devolver(PENDIENTE)
            if str(e) == "uploadLimitExceeded":
                log.info("O limite de envios do CANAL foi atingido (conta 24 h corridas desde que encheu, "
                         "e inclui o que foi enviado à mão). A tarefa tenta de novo mais tarde.")
            else:
                log.info("A cota diária da API acabou (%s); reinicia à meia-noite do horário do Pacífico "
                         "(4h ou 5h em Brasília).", e)
            break
        except KeyboardInterrupt:
            devolver(PENDIENTE)
            log.info("Interrompido à mão; o vídeo volta a ficar pendente.")
            raise
        except HttpError as e:
            estado.registrar_cuota(con, subidas=1)
            if e.resp.status == 400:  # problema deste arquivo em particular
                devolver(ERROR, error=str(e))
                log.error("  Erro com este arquivo, ele fica na pasta para enviar à mão: %s", e)
                continue
            devolver(PENDIENTE)
            log.error("  Erro do YouTube, a execução para aqui: %s", e)
            break
        except Exception as e:  # rede caiu depois de todas as tentativas, etc.
            devolver(PENDIENTE)
            log.error("  O envio falhou (%s); a execução para aqui.", e)
            break

        estado.registrar_cuota(con, subidas=1)
        estado.cambiar_estado(con, fila["ruta"], SUBIDO, video_id=video_id)
        hechos += 1
        log.info("   enviado: https://youtu.be/%s", video_id)
        _agregar_a_su_playlist(con, yt, fila, video_id)

        if hechos % SINCRONIZAR_CADA == 0 and hechos < cupo:
            try:
                sincronizar(con, yt, reiniciar_reservados=False)
            except LimiteAlcanzado:
                pass

    try:
        if borrados := borrar_originales_subidos(con, yt):
            log.info("%d originais apagados (já processados no YouTube).", borrados)
    except LimiteAlcanzado:
        pass
    log.info("Enviados pela API nesta execução: %d", hechos)
    _imprimir_estado(con)


# --- status -----------------------------------------------------------------

def _imprimir_estado(con):
    conteo = dict(con.execute("SELECT estado, COUNT(*) FROM videos GROUP BY estado").fetchall())
    total = sum(conteo.values())
    listos = conteo.get(SUBIDO, 0) + conteo.get(EN_PLAYLIST, 0)
    unidades, subidas = estado.cuota_de_hoy(con)
    print(f"\nProgresso: {listos}/{total} no canal ({conteo.get(EN_PLAYLIST, 0)} já na playlist)")
    print(f"  pendentes: {conteo.get(PENDIENTE, 0)}   com erro: {conteo.get(ERROR, 0)}   "
          f"sendo enviados pela API: {conteo.get(RESERVADO_API, 0)}")
    print(f"  cota de hoje: {subidas}/{LIMITE_SUBIDAS_DIA} envios, {unidades}/{LIMITE_UNIDADES_DIA} unidades")


def cmd_estado(_args):
    con = estado.conectar()
    _imprimir_estado(con)
    faltan = con.execute("SELECT playlist, COUNT(*) FROM videos WHERE estado IN (?, ?) GROUP BY playlist "
                         "ORDER BY 2 DESC", (PENDIENTE, ERROR)).fetchall()
    if faltan:
        print("\nFaltam por playlist:")
        for nombre, n in faltan:
            print(f"  {n:5}  {nombre}")
    errores = con.execute("SELECT titulo, error FROM videos WHERE estado = ?", (ERROR,)).fetchall()
    for f in errores:
        print(f"  ERRO {f['titulo']}: {f['error']}")


# --- tarefa agendada --------------------------------------------------------

NOMBRE_TAREA = "Enviar clips para o YouTube"


def _powershell(script: str) -> None:
    import subprocess
    r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"O PowerShell falhou:\n{r.stderr.strip()}")


def cmd_instalar_tarea(_args):
    if EMPAQUETADO:
        programa, argumentos = sys.executable, "enviar"
    else:
        programa, argumentos = sys.executable, f'"{Path(__file__).resolve()}" enviar'
    q = lambda s: "'" + str(s).replace("'", "''") + "'"  # aspas do PowerShell
    _powershell(f"""
        $accion = New-ScheduledTaskAction -Execute {q(programa)} -Argument {q(argumentos)} -WorkingDirectory {q(CARPETA_PROGRAMA)}
        $horarios = @((New-ScheduledTaskTrigger -Daily -At 10:00), (New-ScheduledTaskTrigger -Daily -At 22:00))
        $opciones = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Hours 20) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        $usuario = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\\$env:USERNAME" -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName {q(NOMBRE_TAREA)} -Action $accion -Trigger $horarios -Settings $opciones `
            -Principal $usuario -Force | Out-Null
    """)
    print(f"Tarefa '{NOMBRE_TAREA}' criada: roda 'enviar' todos os dias às 10:00 e às 22:00.")


def cmd_quitar_tarea(_args):
    _powershell(f"Unregister-ScheduledTask -TaskName '{NOMBRE_TAREA}' -Confirm:$false")
    print(f"Tarefa '{NOMBRE_TAREA}' removida deste PC.")


# --- menu (ao abrir o .exe com clique duplo) ------------------------------------

OPCIONES_MENU = [
    ("Ver o progresso", ["status"]),
    ("Enviar agora (até o limite do dia)", ["enviar"]),
    ("Sincronizar (detectar o que foi enviado à mão, playlists, apagar originais)", ["sincronizar"]),
    ("Escanear a pasta de clips", ["escanear"]),
    ("Preparar a pasta para envio manual", ["preparar"]),
    ("Instalar a tarefa diária neste PC", ["instalar-tarefa"]),
    ("Remover a tarefa diária deste PC", ["remover-tarefa"]),
    ("Configurar (pasta dos clips e opções)", ["configurar"]),
]

# Comandos que não precisam da pasta dos clips configurada.
SIN_CARPETA = {"configurar", "status", "instalar-tarefa", "remover-tarefa"}


def _menu() -> list[str] | None:
    print("Enviar clips para o YouTube\n")
    if config.RAIZ_CLIPS is None:
        print("Primeira vez: vamos configurar.\n")
        cmd_configurar(None)
        print("\nAgora use a opção 4 (Escanear) e depois a 5 (Preparar).\n")
    for i, (texto, _) in enumerate(OPCIONES_MENU, 1):
        print(f"  {i}. {texto}")
    eleccion = input("\nEscolha uma opção (Enter para sair): ").strip()
    if eleccion.isdigit() and 1 <= int(eleccion) <= len(OPCIONES_MENU):
        return OPCIONES_MENU[int(eleccion) - 1][1]
    return None


def main():
    _configurar_log()
    desde_menu = len(sys.argv) == 1
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("configurar", help="escolhe a pasta dos clips e as opções").set_defaults(func=cmd_configurar)
    sub.add_parser("escanear", help="monta a lista de vídeos e o manifesto.csv").set_defaults(func=cmd_escanear)
    sub.add_parser("preparar", help="cria a pasta de trabalho com hardlinks").set_defaults(func=cmd_preparar)
    sub.add_parser("sincronizar", help="detecta o que foi enviado à mão e monta playlists").set_defaults(
        func=cmd_sincronizar)
    p = sub.add_parser("enviar", help="envia pela API até o limite diário")
    p.add_argument("--limite", type=int, help="máximo de vídeos a enviar nesta execução")
    p.add_argument("--dry-run", action="store_true", help="mostra o que enviaria, sem enviar nada")
    p.add_argument("--repetir-erros", dest="reintentar_errores", action="store_true",
                   help="tenta de novo os que deram erro")
    p.set_defaults(func=cmd_subir)
    sub.add_parser("status", help="resumo do progresso").set_defaults(func=cmd_estado)
    sub.add_parser("instalar-tarefa", help="cria a tarefa diária neste PC").set_defaults(func=cmd_instalar_tarea)
    sub.add_parser("remover-tarefa", help="apaga a tarefa diária deste PC").set_defaults(func=cmd_quitar_tarea)
    argumentos = _menu() if desde_menu else None
    if desde_menu and argumentos is None:
        return
    try:
        args = parser.parse_args(argumentos)
        if args.comando not in SIN_CARPETA and config.RAIZ_CLIPS is None:
            raise SystemExit("Falta configurar a pasta dos clips: use a opção Configurar do menu "
                             "(ou o comando 'configurar').")
        args.func(args)
    except SystemExit as e:
        if isinstance(e.code, str):  # mensagem para o usuário: aparece uma vez e fica no log
            log.error("%s", e.code)
            raise SystemExit(1) from None
        raise
    except KeyboardInterrupt:
        raise
    except Exception:
        log.exception("Erro inesperado")
        raise
    finally:
        if desde_menu:
            input("\nPronto. Enter para fechar...")


if __name__ == "__main__":
    main()
