# Enviar clips para o YouTube

Envia automaticamente os seus clips de jogos (ShadowPlay, Medal, OBS, Discord, Highlights) para o seu canal do YouTube:

- Todos os vídeos vão como **não listados**: não aparecem no canal nem na busca, mas quem tiver o link consegue ver. As playlists também.
- Cada jogo ganha a sua **playlist**.
- Os títulos ficam assim: `League of Legends - 2024-02-29 12.04`.
- Nada é enviado duas vezes: o programa confere o que já está no canal.

Dá para enviar de dois jeitos, e dá para usar os dois ao mesmo tempo:

- **Automático (pela API):** até 100 vídeos por dia, começando pelos **mais novos**.
- **Manual (pelo YouTube Studio):** você arrasta os arquivos de uma pasta pronta, começando pelos **mais antigos**.

Funciona só no Windows.

---

## 1. Baixar

1. Vá em [**Releases**](../../releases/latest) e baixe o `enviar_clips.exe`.
2. Crie uma pasta para o programa, por exemplo `C:\EnviarClips`, e coloque o `.exe` nela. O programa guarda os arquivos dele nessa mesma pasta.

Na primeira vez que você abrir, o Windows pode mostrar **"O Windows protegeu o computador"**. Clique em **Mais informações** e depois em **Executar assim mesmo**. Isso aparece porque o programa não tem assinatura digital. Se o antivírus reclamar, é o mesmo motivo: programas feitos em Python e empacotados com PyInstaller costumam gerar esse falso positivo.

## 2. Criar o acesso ao YouTube (uma vez só, ~10 minutos)

O programa precisa de um arquivo `client_secret.json`, que dá acesso ao **seu** canal. Cada pessoa cria o próprio:

1. Entre no [Google Cloud Console](https://console.cloud.google.com/) com a conta do seu canal e **crie um projeto novo** (qualquer nome).
2. Em **APIs e serviços → Biblioteca**, procure **YouTube Data API v3** e clique em **Ativar**.
3. Em **APIs e serviços → Tela de permissão OAuth** (pode aparecer como *Google Auth Platform*):
   - Preencha o nome do app (qualquer um) e o seu e-mail.
   - Em **Público-alvo**, escolha **Externo** e deixe no modo **Teste**.
   - Em **Usuários de teste**, adicione o e-mail da conta do seu canal.
4. Em **Clientes** (ou *Credenciais → Criar credenciais → ID do cliente OAuth*):
   - Escolha o tipo **App para computador**.
   - Baixe o JSON. No programa, você escolhe esse arquivo com o botão **Escolher arquivo...** (passo 4 abaixo).

> ⚠️ `client_secret.json` e `token.json` dão acesso ao seu canal. Não mande esses arquivos para ninguém.

## 3. Ajustes no YouTube (recomendado)

- Verifique o canal com o seu celular em <https://www.youtube.com/verify>. Assim o limite diário para envios manuais fica alto.
- No YouTube Studio, vá em **Configurações → Padrões de upload** e coloque visibilidade **Não listado** e categoria **Jogos**. Assim os vídeos que você enviar à mão ficam iguais aos enviados pelo programa.

## 4. Usar

Abra o `enviar_clips.exe` com clique duplo. Na primeira vez, a janela mostra o que falta:

1. **Escolher pasta...:** a pasta onde ficam os seus clips.
2. **Escolher arquivo...:** o JSON que você baixou no passo 2.

Depois é só clicar em **▶ Enviar clips**. O programa lê a pasta, coloca cada clip na playlist do jogo e envia até 100 por dia. Na primeira vez, o navegador abre para você entrar com a conta do canal. O Google vai avisar que o app não foi verificado: clique em **Continuar**. O app é seu, criado no passo 2.

Para não precisar abrir o programa todo dia, ligue **Enviar sozinho todo dia** na aba **Configurações**.

### Abas da janela

- **Início:** quantos clips já estão no YouTube, quantos faltam, e os botões **Enviar clips** e **Parar**. Se você parar no meio, o vídeo que estava sendo enviado volta para a fila.
- **Clips:** a lista de todos os clips, com o título que cada um vai ter no YouTube e a playlist.
  - **Renomear** (ou dois cliques): muda o nome do arquivo e mostra na hora como vai ficar no YouTube. Só funciona com os clips que ainda não foram enviados.
  - **Ver o clip:** abre o vídeo, para você saber qual é antes de dar um nome.
  - **Copiar link** / **Copiar link da playlist:** para mandar para alguém os clips que já estão no YouTube.
- **Configurações:** a pasta dos clips, o acesso ao YouTube (ou trocar de conta), o envio diário e a opção de apagar os clips do PC.

## Envio manual (opcional, para ir mais rápido)

A API só deixa enviar 100 vídeos por dia. Se você tem muitos clips, pode enviar à mão ao mesmo tempo:

1. Na aba **Configurações**, clique em **Abrir pasta de envio manual**. Ela fica ao lado da pasta dos clips: os clips de `D:\Clips` ficam prontos em `D:\Clips_para_enviar`, já com o nome certo.
2. Arraste os arquivos para o YouTube Studio, 15 por vez, começando pelos **mais antigos**.
3. **Não mude os títulos.** O YouTube usa o nome do arquivo como título, e é assim que o programa reconhece o vídeo. Se quiser outro nome, renomeie antes na aba **Clips**.
4. No próximo **Enviar clips**, o programa encontra o que você enviou à mão e coloca na playlist.

A pasta de trabalho é feita de *hardlinks*: não ocupa espaço extra, e apagar algo dela não apaga o original. Por isso ela precisa estar no mesmo disco que os clips.

## Apagar originais

Se você ativar essa opção (aba **Configurações**), o programa apaga o original quando o YouTube terminar de **processar o vídeo sem erro**. As cópias idênticas também são apagadas, depois de comparadas byte a byte com o original. Isso acontece a cada *Enviar clips*.

- Se o vídeo ainda estiver processando, o original é apagado na próxima vez.
- Se o YouTube não conseguir processar o vídeo, o original **não** é apagado, e o log avisa com o link.
- Tudo o que é apagado fica anotado no `envio.log`.
- As versões que estão em pastas com "comprimido" no nome nunca são apagadas.

## Como as playlists são escolhidas

- **ShadowPlay** (`Jogo_2024.02.29-12.04.mp4`), **App da NVIDIA** (`Jogo 2026.09.25 - 22.33.44.02.DVR.mp4`) e **Medal** (`MedalTVJogo20250206162129.mp4`): o jogo vem do nome do arquivo.
- **Nome escolhido por você** (`o forte leva - repo.mp4`, `oxe menina - dbd.mp4`): o jogo é o que vem depois do último ` - `, e cada jogo ganha a sua playlist com esse nome (`repo`, `dbd`...). O título no YouTube é o nome do arquivo inteiro (`o forte leva - repo`), e a data vem da data de modificação.
- **Highlights** e arquivos sem padrão: o jogo vem do nome da pasta onde o clip está.
- **Discord** (`Jogo_<código>.mp4`): o jogo vem do nome do arquivo e a data, da data de modificação.
- **OBS** (`2025-06-09 08-34-02.mkv`): vão para a playlist **Gravações OBS**.
- Quando não dá para saber o jogo, o clip vai para a playlist **Desconhecido**.

Para conferir antes de enviar, olhe a aba **Clips**. Se algum ficou com o jogo errado ou sem nome, é só renomear ali.

## Configuração avançada

A configuração fica no `config.json`, ao lado do programa:

```json
{
  "clips": "D:\\Clips",
  "apagar_originais": false,
  "para_enviar": "D:\\Clips_para_enviar",
  "pasta_historicos": "clips antigos"
}
```

- `para_enviar` (opcional): muda o lugar da pasta de envio manual. Precisa estar no mesmo disco que os clips.
- `pasta_historicos` (opcional): os clips de qualquer subpasta com esse nome vão para a playlist **Históricos**.

No `config.json`, as barras vão dobradas (`\\`).

## Observações

- A cota da API reinicia à meia-noite do horário do Pacífico: 4h ou 5h da manhã em Brasília, conforme o horário de verão dos EUA.
- A cota para colocar vídeos em playlists dá para uns 190 por dia no total. Se você enviar muitos à mão, alguns ficam sem playlist até o dia seguinte, e o próximo envio completa sozinho.
- No modo **Teste**, o Google pede para entrar de novo a cada 7 dias. Quando isso acontece, o navegador abre sozinho.
- A tarefa diária só roda com a sua sessão do Windows aberta. Se o PC estiver desligado no horário, ela roda assim que você ligar. Para mudar o horário, abra o **Agendador de Tarefas** e procure "Enviar clips para o YouTube".
- Nunca rodam dois envios ao mesmo tempo: se o envio diário já estiver rodando, a janela avisa.
- Se você mover o `enviar_clips.exe` para outra pasta, desligue e ligue de novo o **Enviar sozinho todo dia**.
- Nunca use o programa em dois PCs ao mesmo tempo, porque os vídeos saem duplicados.

## Linha de comando

Não precisa, mas também dá para usar pelo terminal, dentro da pasta do programa. No PowerShell, o nome vai com `.\` na frente e `| Out-Host` no fim (assim o PowerShell espera o programa terminar e mostra o que ele escreve):

```
.\enviar_clips.exe status | Out-Host
.\enviar_clips.exe enviar --limite 1 | Out-Host       # envia um só (bom para testar)
.\enviar_clips.exe enviar --dry-run | Out-Host        # mostra o que enviaria, sem enviar nada
.\enviar_clips.exe enviar --repetir-erros | Out-Host  # tenta de novo os que deram erro
```

Os outros comandos são `escanear`, `preparar`, `sincronizar`, `configurar`, `instalar-tarefa` e `remover-tarefa`.

Para rodar a partir do código-fonte, instale o Python 3.10 ou mais novo e rode:

```
pip install -r requirements.txt
python enviar_clips.py
```
