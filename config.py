import json
import sys
from pathlib import Path

# Empacotado como .exe, os arquivos do programa ficam ao lado do .exe (não na pasta temporária).
EMPAQUETADO = getattr(sys, "frozen", False)
CARPETA_PROGRAMA = Path(sys.executable).parent if EMPAQUETADO else Path(__file__).resolve().parent

# Configuração em config.json ao lado do programa (o comando "configurar" cria o arquivo):
#   {"clips": "D:\\Clips", "apagar_originais": false,
#    "para_enviar": "D:\\Clips_para_enviar", "pasta_historicos": "clips antigos"}
CONFIG = CARPETA_PROGRAMA / "config.json"

RAIZ_CLIPS: Path | None = None
# Tem que estar no mesmo disco que RAIZ_CLIPS para poder usar hardlinks.
CARPETA_PARA_SUBIR: Path | None = None
# Apagar o original depois que o YouTube termina de processar o vídeo.
APAGAR_ORIGINAIS = False
# Nome de uma subpasta cujos clips vão para a playlist "Históricos" (vazio = nenhuma).
PASTA_HISTORICOS = ""


def _ler() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}


def carregar():
    global RAIZ_CLIPS, CARPETA_PARA_SUBIR, APAGAR_ORIGINAIS, PASTA_HISTORICOS
    dados = _ler()
    RAIZ_CLIPS = Path(dados["clips"]) if dados.get("clips") else None
    if dados.get("para_enviar"):
        CARPETA_PARA_SUBIR = Path(dados["para_enviar"])
    elif RAIZ_CLIPS:
        CARPETA_PARA_SUBIR = RAIZ_CLIPS.parent / f"{RAIZ_CLIPS.name or 'Clips'}_para_enviar"
    else:
        CARPETA_PARA_SUBIR = None
    APAGAR_ORIGINAIS = bool(dados.get("apagar_originais", False))
    PASTA_HISTORICOS = dados.get("pasta_historicos", "").strip().lower()


def salvar(**cambios):
    """Grava as chaves indicadas (ex.: clips=Path(...), apagar_originais=True), mantendo as outras."""
    dados = _ler()
    dados.update({clave: str(v) if isinstance(v, Path) else v for clave, v in cambios.items()})
    CONFIG.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    carregar()


carregar()

DB = CARPETA_PROGRAMA / "estado.db"
MANIFIESTO = CARPETA_PROGRAMA / "manifesto.csv"
LOG = CARPETA_PROGRAMA / "envio.log"
LOCK = CARPETA_PROGRAMA / "enviar.lock"
CLIENT_SECRET = CARPETA_PROGRAMA / "client_secret.json"
TOKEN = CARPETA_PROGRAMA / "token.json"

# Não listado: não aparece no canal nem na busca, mas quem tiver o link consegue ver.
PRIVACIDAD = "unlisted"
URL_AJUDA = "https://github.com/Sebasjkk/Programa-util#readme"
CATEGORIA_GAMING = "20"

# Limite de videos.insert por projeto (desde junho de 2026). Se o Google aumentar a cota, mudar aqui.
LIMITE_SUBIDAS_DIA = 100
# Cota geral de 10.000 unidades, com margem.
LIMITE_UNIDADES_DIA = 9500
# A cada quantos envios pela API o canal é verificado de novo (para detectar envios manuais).
SINCRONIZAR_CADA = 10
