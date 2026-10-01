"""Janela do programa: abre com clique duplo no .exe."""

import json
import logging
import os
import queue
import shutil
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import config
import escaneo
import estado
from estado import EN_PLAYLIST, ERROR, PENDIENTE, RESERVADO_API, SUBIDO

SITUACAO = {
    PENDIENTE: "Falta enviar",
    ERROR: "Erro",
    RESERVADO_API: "Enviando...",
    SUBIDO: "No YouTube",
    EN_PLAYLIST: "No YouTube",
}
VERDE, VERMELHO, CINZA = "#1a7f37", "#c62828", "#666666"
DICA_NOME = 'Para escolher o jogo, termine o nome com " - jogo".  Ex.: o forte leva - repo'


class _Saida:
    """Recebe os print() dos comandos e manda para a área de registro da janela."""

    def __init__(self, fila: queue.Queue):
        self.fila = fila

    def write(self, texto: str):
        if texto.strip():
            self.fila.put(("texto", texto.rstrip("\r\n") + "\n", None))

    def flush(self):
        pass


class _LogNaJanela(logging.Handler):
    def __init__(self, fila: queue.Queue):
        super().__init__(logging.INFO)
        self.fila = fila
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        self.fila.put(("texto", self.format(record) + "\n", record.levelname))


class Janela:
    def __init__(self, app):
        self.app = app
        self.fila = queue.Queue()
        self.ocupado = False
        self.iids = {}  # iid da lista -> linha da base
        self.aviso = (None, "")  # (caminho do clip, mensagem) mostrada enquanto ele estiver selecionado
        self.passos_resumo = 0

        sys.stdout = _Saida(self.fila)
        app.log.addHandler(_LogNaJanela(self.fila))
        app.AL_PROGRESAR = lambda p: self.fila.put(("progresso", p))

        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # letra nítida em telas com zoom
        except Exception:
            pass
        self.root = tk.Tk()
        self.root.title("Enviar clips para o YouTube")
        self.root.geometry("980x680")
        self.root.minsize(820, 560)
        self.root.protocol("WM_DELETE_WINDOW", self._fechar)
        self._estilos()

        self.abas = ttk.Notebook(self.root)
        self.abas.pack(fill="both", expand=True, padx=10, pady=10)
        self._montar_inicio()
        self._montar_clips()
        self._montar_config()

        self.root.after(100, self._ler_fila)
        self._atualizar_tudo()
        self._em_segundo_plano(app.tarea_instalada, self.var_tarefa.set)
        if self._pasta_ok():
            self._rodar("Lendo a pasta de clips...", self._ler_pasta)

    # --- aparência ----------------------------------------------------------

    def _estilos(self):
        estilo = ttk.Style()
        if "vista" in estilo.theme_names():
            estilo.theme_use("vista")
        estilo.configure(".", font=("Segoe UI", 10))
        estilo.configure("Titulo.TLabel", font=("Segoe UI", 15, "bold"))
        estilo.configure("Grande.TLabel", font=("Segoe UI", 13))
        estilo.configure("Cinza.TLabel", foreground=CINZA)
        estilo.configure("Ok.TLabel", foreground=VERDE, font=("Segoe UI", 10, "bold"))
        estilo.configure("Falta.TLabel", foreground=VERMELHO, font=("Segoe UI", 10, "bold"))
        estilo.configure("Enviar.TButton", font=("Segoe UI", 12, "bold"), padding=(24, 10))
        estilo.configure("Treeview", rowheight=26)

    # --- aba Início ---------------------------------------------------------

    def _montar_inicio(self):
        aba = ttk.Frame(self.abas, padding=16)
        self.abas.add(aba, text="   Início   ")

        ttk.Label(aba, text="Enviar clips para o YouTube", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(aba, style="Cinza.TLabel",
                  text="Lê a pasta dos seus clips, coloca cada um na playlist do jogo e envia como não listado "
                       "(só vê quem tiver o link).").pack(anchor="w", pady=(2, 12))

        # Passos que faltam (só aparece enquanto faltar algo)
        self.quadro_passos = ttk.LabelFrame(aba, text=" Antes de começar ", padding=12)
        self.lbl_passo1 = ttk.Label(self.quadro_passos)
        self.lbl_passo1.grid(row=0, column=0, sticky="w", pady=3)
        ttk.Button(self.quadro_passos, text="Escolher pasta...",
                   command=self._escolher_pasta).grid(row=0, column=1, padx=6)
        self.lbl_passo2 = ttk.Label(self.quadro_passos)
        self.lbl_passo2.grid(row=1, column=0, sticky="w", pady=3)
        ttk.Button(self.quadro_passos, text="Escolher arquivo...",
                   command=self._escolher_credencial).grid(row=1, column=1, padx=6)
        ttk.Button(self.quadro_passos, text="Como criar?",
                   command=lambda: webbrowser.open(config.URL_AJUDA)).grid(row=1, column=2)
        self.quadro_passos.columnconfigure(0, weight=1)

        # Resumo
        self.quadro_resumo = ttk.Frame(aba)
        self.quadro_resumo.pack(fill="x")
        self.lbl_resumo = ttk.Label(self.quadro_resumo, style="Grande.TLabel")
        self.lbl_resumo.pack(anchor="w")
        self.barra_total = ttk.Progressbar(self.quadro_resumo, maximum=100)
        self.barra_total.pack(fill="x", pady=6)
        self.lbl_detalhe = ttk.Label(self.quadro_resumo, style="Cinza.TLabel")
        self.lbl_detalhe.pack(anchor="w")

        # Botões
        botoes = ttk.Frame(aba)
        botoes.pack(fill="x", pady=14)
        self.btn_enviar = ttk.Button(botoes, text="▶  Enviar clips", style="Enviar.TButton", command=self._enviar)
        self.btn_enviar.pack(side="left")
        self.btn_parar = ttk.Button(botoes, text="■  Parar", command=self._parar)
        self.btn_parar.pack(side="left", padx=10, ipady=6)
        self.lbl_limite = ttk.Label(botoes, style="Cinza.TLabel", justify="left",
                                    text="O YouTube deixa enviar até 100 por dia.\n"
                                         "Se faltar mais, continue amanhã (ou ligue o envio diário).")
        self.lbl_limite.pack(side="left", padx=14)

        # Atividade atual
        self.lbl_atividade = ttk.Label(aba)
        self.lbl_atividade.pack(anchor="w")
        self.barra_video = ttk.Progressbar(aba, maximum=100)
        self.barra_video.pack(fill="x", pady=(4, 10))

        ttk.Label(aba, text="Registro", style="Cinza.TLabel").pack(anchor="w")
        self.txt = ScrolledText(aba, height=10, font=("Consolas", 9), state="disabled", wrap="word",
                                relief="solid", borderwidth=1)
        self.txt.tag_configure("WARNING", foreground="#b26a00")
        self.txt.tag_configure("ERROR", foreground=VERMELHO)
        self.txt.pack(fill="both", expand=True)

    def _escrever(self, texto: str, nivel: str | None):
        self.txt.configure(state="normal")
        self.txt.insert("end", texto, nivel or "")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    # --- aba Clips ----------------------------------------------------------

    def _montar_clips(self):
        aba = ttk.Frame(self.abas, padding=12)
        self.abas.add(aba, text="   Clips   ")

        topo = ttk.Frame(aba)
        topo.pack(fill="x")
        ttk.Label(topo, text="Buscar:").pack(side="left")
        self.var_busca = tk.StringVar()
        self.var_busca.trace_add("write", lambda *_: self._preencher_lista())
        ttk.Entry(topo, textvariable=self.var_busca, width=34).pack(side="left", padx=6)
        self.var_mostrar_enviados = tk.BooleanVar(value=True)
        ttk.Checkbutton(topo, text="Mostrar os que já estão no YouTube", variable=self.var_mostrar_enviados,
                        command=self._preencher_lista).pack(side="left", padx=10)
        self.btn_atualizar = ttk.Button(topo, text="Atualizar lista",
                                        command=lambda: self._rodar("Lendo a pasta de clips...", self._ler_pasta))
        self.btn_atualizar.pack(side="right")
        self.lbl_qtd = ttk.Label(topo, style="Cinza.TLabel")
        self.lbl_qtd.pack(side="right", padx=10)

        quadro = ttk.Frame(aba)
        quadro.pack(fill="both", expand=True, pady=8)
        colunas = ("titulo", "playlist", "data", "situacao")
        self.lista = ttk.Treeview(quadro, columns=colunas, show="headings", selectmode="browse")
        for col, texto, largura, estica in [("titulo", "Título no YouTube", 380, True),
                                            ("playlist", "Playlist (jogo)", 170, False),
                                            ("data", "Data", 130, False),
                                            ("situacao", "Situação", 110, False)]:
            self.lista.heading(col, text=texto, anchor="w")
            self.lista.column(col, width=largura, stretch=estica, anchor="w")
        self.lista.tag_configure("enviado", foreground=CINZA)
        self.lista.tag_configure("erro", foreground=VERMELHO)
        rolagem = ttk.Scrollbar(quadro, orient="vertical", command=self.lista.yview)
        self.lista.configure(yscrollcommand=rolagem.set)
        self.lista.pack(side="left", fill="both", expand=True)
        rolagem.pack(side="right", fill="y")
        self.lista.bind("<<TreeviewSelect>>", lambda _: self._atualizar_botoes())
        self.lista.bind("<Double-1>", self._duplo_clique)

        self.lbl_info = ttk.Label(aba, style="Cinza.TLabel", wraplength=900, justify="left")
        self.lbl_info.pack(anchor="w")

        botoes = ttk.Frame(aba)
        botoes.pack(fill="x", pady=(8, 0))
        self.btn_renomear = ttk.Button(botoes, text="Renomear...", command=self._renomear)
        self.btn_ver = ttk.Button(botoes, text="Ver o clip", command=self._ver_clip)
        self.btn_link = ttk.Button(botoes, text="Copiar link", command=self._copiar_link)
        self.btn_link_playlist = ttk.Button(botoes, text="Copiar link da playlist", command=self._copiar_link_playlist)
        self.btn_youtube = ttk.Button(botoes, text="Abrir no YouTube", command=self._abrir_youtube)
        for b in (self.btn_renomear, self.btn_ver, self.btn_link, self.btn_link_playlist, self.btn_youtube):
            b.pack(side="left", padx=(0, 6))
        ttk.Label(aba, style="Cinza.TLabel",
                  text="Dê dois cliques num clip para renomear (ou, se já estiver no YouTube, para abrir).  "
                       + DICA_NOME).pack(anchor="w", pady=(8, 0))

    def _linhas_da_base(self):
        con = estado.conectar()
        return con.execute("SELECT ruta, titulo, playlist, fecha, estado, video_id, error FROM videos "
                           "ORDER BY fecha DESC, ruta DESC").fetchall()

    def _preencher_lista(self, selecionar: str | None = None):
        selecionado = {"ruta": selecionar} if selecionar else self._selecionada()
        busca = self.var_busca.get().strip().casefold()
        mostrar_enviados = self.var_mostrar_enviados.get()
        self.lista.delete(*self.lista.get_children())
        self.iids.clear()
        manter = None
        for i, f in enumerate(self._linhas_da_base()):
            enviado = f["estado"] in (SUBIDO, EN_PLAYLIST)
            if enviado and not mostrar_enviados:
                continue
            if busca and busca not in f"{f['titulo']} {f['playlist']} {Path(f['ruta']).name}".casefold():
                continue
            iid = str(i)
            tag = "enviado" if enviado else "erro" if f["estado"] == ERROR else ""
            self.lista.insert("", "end", iid=iid, tags=(tag,), values=(
                f["titulo"], f["playlist"], _data(f["fecha"]), SITUACAO.get(f["estado"], f["estado"])))
            self.iids[iid] = f
            if selecionado and f["ruta"] == selecionado["ruta"]:
                manter = iid
        if manter:
            self.lista.selection_set(manter)
            self.lista.see(manter)
        self.lbl_qtd["text"] = f"{len(self.iids)} clips"
        self._atualizar_botoes()

    def _atualizar_situacoes(self):
        """Durante um envio, atualiza só a coluna Situação (sem perder a rolagem da lista)."""
        por_ruta = {f["ruta"]: f for f in self._linhas_da_base()}
        for iid, antiga in list(self.iids.items()):
            nova = por_ruta.get(antiga["ruta"])
            if nova and nova["estado"] != antiga["estado"]:
                enviado = nova["estado"] in (SUBIDO, EN_PLAYLIST)
                self.lista.item(iid, tags=("enviado" if enviado else "erro" if nova["estado"] == ERROR else "",))
                self.lista.set(iid, "situacao", SITUACAO.get(nova["estado"], nova["estado"]))
                self.iids[iid] = nova
        self._atualizar_botoes()

    def _selecionada(self):
        sel = self.lista.selection() if hasattr(self, "lista") else ()
        return self.iids.get(sel[0]) if sel else None

    def _duplo_clique(self, _evento):
        f = self._selecionada()
        if not f:
            return
        if f["video_id"] and f["estado"] in (SUBIDO, EN_PLAYLIST):
            self._abrir_youtube()
        else:
            self._renomear()

    def _ver_clip(self):
        f = self._selecionada()
        if f and Path(f["ruta"]).exists():
            os.startfile(f["ruta"])

    def _copiar_link(self):
        f = self._selecionada()
        if f and f["video_id"]:
            self._copiar(f"https://youtu.be/{f['video_id']}", "Link do vídeo copiado! É só colar onde quiser.")

    def _copiar_link_playlist(self):
        f = self._selecionada()
        pl = self._playlist_id(f)
        if pl:
            self._copiar(f"https://www.youtube.com/playlist?list={pl}",
                         f"Link da playlist \"{f['playlist']}\" copiado! É só colar onde quiser.")

    def _playlist_id(self, f) -> str | None:
        if not f:
            return None
        fila = estado.conectar().execute("SELECT playlist_id FROM playlists WHERE nombre = ?",
                                          (f["playlist"],)).fetchone()
        return fila["playlist_id"] if fila else None

    def _copiar(self, texto: str, aviso: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(texto)
        self._avisar(self._selecionada()["ruta"], aviso)

    def _avisar(self, ruta: str, mensagem: str):
        self.aviso = (ruta, mensagem)
        self._atualizar_botoes()

    def _abrir_youtube(self):
        f = self._selecionada()
        if f and f["video_id"]:
            webbrowser.open(f"https://youtu.be/{f['video_id']}")

    def _renomear(self):
        f = self._selecionada()
        if not f:
            return
        if f["estado"] not in (PENDIENTE, ERROR):
            messagebox.showinfo("Renomear", "Esse clip já está no YouTube.\n"
                                            "Para mudar o título, use o YouTube Studio.", parent=self.root)
            return
        ruta = Path(f["ruta"])
        if not ruta.exists():
            messagebox.showerror("Renomear", "O arquivo não existe mais. Clique em \"Atualizar lista\".",
                                 parent=self.root)
            return
        DialogoRenomear(self, ruta)

    # --- aba Configurações --------------------------------------------------

    def _montar_config(self):
        aba = ttk.Frame(self.abas, padding=16)
        self.abas.add(aba, text="   Configurações   ")

        q1 = ttk.LabelFrame(aba, text=" Pasta dos clips ", padding=12)
        q1.pack(fill="x")
        self.var_pasta = tk.StringVar()
        ttk.Entry(q1, textvariable=self.var_pasta, state="readonly").grid(row=0, column=0, sticky="we")
        ttk.Button(q1, text="Escolher...", command=self._escolher_pasta).grid(row=0, column=1, padx=6)
        ttk.Button(q1, text="Abrir", command=lambda: self._abrir_pasta(config.RAIZ_CLIPS)).grid(row=0, column=2)
        self.lbl_pasta_manual = ttk.Label(q1, style="Cinza.TLabel")
        self.lbl_pasta_manual.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(q1, text="Abrir pasta de envio manual",
                   command=lambda: self._abrir_pasta(config.CARPETA_PARA_SUBIR)).grid(
            row=1, column=1, columnspan=2, pady=(8, 0), sticky="e")
        q1.columnconfigure(0, weight=1)

        q2 = ttk.LabelFrame(aba, text=" Acesso ao YouTube ", padding=12)
        q2.pack(fill="x", pady=12)
        self.lbl_acesso = ttk.Label(q2)
        self.lbl_acesso.grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Button(q2, text="Escolher client_secret.json...",
                   command=self._escolher_credencial).grid(row=1, column=0, pady=(8, 0), sticky="w")
        ttk.Button(q2, text="Como criar?", command=lambda: webbrowser.open(config.URL_AJUDA)).grid(
            row=1, column=1, padx=6, pady=(8, 0))
        ttk.Button(q2, text="Trocar de conta", command=self._trocar_conta).grid(row=1, column=2, pady=(8, 0))

        q3 = ttk.LabelFrame(aba, text=" Opções ", padding=12)
        q3.pack(fill="x")
        self.var_tarefa = tk.BooleanVar(value=False)
        ttk.Checkbutton(q3, text="Enviar sozinho todo dia (às 10:00 e às 22:00, com o PC ligado)",
                        variable=self.var_tarefa, command=self._mudar_tarefa).pack(anchor="w")
        ttk.Label(q3, style="Cinza.TLabel",
                  text="Funciona mesmo com esta janela fechada. Se o PC estiver desligado no horário, "
                       "envia quando você ligar.").pack(anchor="w", padx=22, pady=(0, 10))
        self.var_apagar = tk.BooleanVar(value=config.APAGAR_ORIGINAIS)
        ttk.Checkbutton(q3, text="Apagar o clip do PC depois que o YouTube terminar de processar",
                        variable=self.var_apagar, command=self._mudar_apagar).pack(anchor="w")
        ttk.Label(q3, style="Cinza.TLabel",
                  text="Libera espaço, mas o YouTube passa a ser o único lugar com o clip.").pack(
            anchor="w", padx=22)

        q4 = ttk.Frame(aba)
        q4.pack(fill="x", pady=12)
        ttk.Button(q4, text="Abrir pasta do programa",
                   command=lambda: self._abrir_pasta(config.CARPETA_PROGRAMA)).pack(side="left")
        ttk.Button(q4, text="Ver registro completo (envio.log)",
                   command=lambda: config.LOG.exists() and os.startfile(config.LOG)).pack(side="left", padx=6)

    def _abrir_pasta(self, pasta):
        if pasta and Path(pasta).is_dir():
            os.startfile(pasta)
        else:
            messagebox.showinfo("Abrir pasta", "Essa pasta ainda não existe.", parent=self.root)

    def _escolher_pasta(self):
        pasta = filedialog.askdirectory(title="Escolha a pasta onde ficam os seus clips", mustexist=True,
                                        parent=self.root)
        if not pasta:
            return
        config.salvar(clips=Path(pasta))
        self._atualizar_tudo()
        self._rodar("Lendo a pasta de clips...", self._ler_pasta)

    def _escolher_credencial(self):
        arquivo = filedialog.askopenfilename(
            title="Escolha o arquivo baixado do Google Cloud (client_secret...json)",
            filetypes=[("Arquivo JSON", "*.json"), ("Todos os arquivos", "*.*")], parent=self.root)
        if not arquivo:
            return
        try:
            dados = json.loads(Path(arquivo).read_text(encoding="utf-8"))
        except Exception:
            dados = {}
        if "installed" not in dados:
            if "web" in dados:
                motivo = ("Esse acesso foi criado como \"Aplicativo da Web\".\n"
                          "Crie outro do tipo \"App para computador\" (veja \"Como criar?\").")
            else:
                motivo = "Esse não é o arquivo baixado do Google Cloud (veja \"Como criar?\")."
            messagebox.showerror("Acesso ao YouTube", motivo, parent=self.root)
            return
        if Path(arquivo).resolve() != config.CLIENT_SECRET.resolve():
            shutil.copyfile(arquivo, config.CLIENT_SECRET)
            if config.TOKEN.exists():
                config.TOKEN.unlink()  # o login antigo era de outro acesso
        self._atualizar_tudo()
        messagebox.showinfo("Acesso ao YouTube",
                            "Pronto! Na primeira vez que você enviar, o navegador vai abrir para você "
                            "entrar com a conta do canal.", parent=self.root)

    def _trocar_conta(self):
        if not config.TOKEN.exists():
            messagebox.showinfo("Trocar de conta", "Você ainda não entrou com nenhuma conta.", parent=self.root)
            return
        if messagebox.askyesno("Trocar de conta", "Sair da conta atual? Na próxima vez que enviar, o navegador "
                                                  "vai abrir para você entrar de novo.", parent=self.root):
            config.TOKEN.unlink()
            self._atualizar_tudo()

    def _mudar_tarefa(self):
        ligar = self.var_tarefa.get()
        funcao = self.app.cmd_instalar_tarea if ligar else self.app.cmd_quitar_tarea
        self._em_segundo_plano(lambda: (funcao(None), self.app.tarea_instalada())[1], self.var_tarefa.set)

    def _mudar_apagar(self):
        apagar = self.var_apagar.get()
        if apagar and not messagebox.askyesno(
                "Apagar clips do PC",
                "Depois que o YouTube terminar de processar cada vídeo, o arquivo vai ser APAGADO do PC.\n\n"
                "O YouTube passa a ser o único lugar com o clip. Ativar mesmo assim?", parent=self.root):
            self.var_apagar.set(False)
            return
        config.salvar(apagar_originais=apagar)

    # --- tarefas em segundo plano -------------------------------------------

    def _rodar(self, texto: str, funcao):
        """Roda uma tarefa demorada sem travar a janela (uma de cada vez)."""
        if self.ocupado:
            return
        self.ocupado = True
        self.lbl_atividade["text"] = texto
        self._atualizar_botoes()

        def trabalho():
            erro = None
            try:
                funcao()
            except self.app.Interrumpido:
                self.app.log.info("Parado.")
            except SystemExit as e:
                erro = e.code if isinstance(e.code, str) else None
                if erro:
                    self.app.log.error("%s", erro)
            except Exception as e:
                self.app.log.exception("Erro inesperado")
                erro = f"Erro inesperado: {e}\n\nOs detalhes ficam no envio.log."
            self.fila.put(("fim", erro))

        threading.Thread(target=trabalho, daemon=True).start()

    def _em_segundo_plano(self, funcao, depois):
        """Para coisas rápidas que não podem travar a janela (ex.: consultar a tarefa diária)."""
        def trabalho():
            try:
                resultado = funcao()
            except SystemExit as e:
                self.fila.put(("aviso", e.code if isinstance(e.code, str) else "Não deu certo."))
                resultado = None
            except Exception as e:
                self.fila.put(("aviso", str(e)))
                resultado = None
            self.fila.put(("chamar", depois, resultado))
        threading.Thread(target=trabalho, daemon=True).start()

    def _ler_pasta(self):
        con = estado.conectar()
        filas, _, _ = self.app.escanear_clips(con)
        if self.app.mesmo_disco():
            self.app.reconciliar_carpeta(con)
        self.app.log.info("Pasta lida: %d clips.", len(filas))

    def _ler_fila(self):
        try:
            while True:
                msg = self.fila.get_nowait()
                if msg[0] == "texto":
                    self._escrever(msg[1], msg[2])
                elif msg[0] == "progresso":
                    self.barra_video["value"] = msg[1] * 100
                elif msg[0] == "chamar":
                    if msg[2] is not None:
                        msg[1](msg[2])
                elif msg[0] == "aviso":
                    messagebox.showerror("Erro", msg[1], parent=self.root)
                elif msg[0] == "fim":
                    self.ocupado = False
                    self.lbl_atividade["text"] = ""
                    self.barra_video["value"] = 0
                    self._atualizar_tudo()
                    if msg[1]:
                        messagebox.showerror("Atenção", msg[1], parent=self.root)
        except queue.Empty:
            pass
        if self.ocupado:
            self.passos_resumo += 1
            if self.passos_resumo % 30 == 0:  # a cada ~3 segundos
                self._atualizar_resumo()
                self._atualizar_situacoes()
        self.root.after(100, self._ler_fila)

    # --- ações principais ---------------------------------------------------

    def _enviar(self):
        if not self._pronto():
            self.abas.select(0)
            return
        self.app.PARAR.clear()
        self.barra_video["value"] = 0
        self._rodar("Enviando... (pode usar o PC normalmente; dá para fechar a janela no fim)",
                    self.app.enviar_tudo)

    def _parar(self):
        self.app.PARAR.set()
        self.lbl_atividade["text"] = "Parando... (o vídeo que estava sendo enviado volta para a fila)"
        self.btn_parar.state(["disabled"])

    def _fechar(self):
        if self.ocupado and not messagebox.askyesno(
                "Fechar", "Ainda está trabalhando. Fechar mesmo assim?\n"
                          "O vídeo que estava sendo enviado volta para a fila.", parent=self.root):
            return
        self.app.PARAR.set()
        self.root.destroy()

    # --- atualizar a tela ---------------------------------------------------

    def _pasta_ok(self) -> bool:
        return config.RAIZ_CLIPS is not None and config.RAIZ_CLIPS.is_dir()

    def _pronto(self) -> bool:
        return self._pasta_ok() and config.CLIENT_SECRET.exists()

    def _atualizar_tudo(self):
        pasta_ok, acesso_ok = self._pasta_ok(), config.CLIENT_SECRET.exists()

        if config.RAIZ_CLIPS is None:
            passo1, ok1 = "1. Escolha a pasta onde ficam os seus clips", False
        elif not pasta_ok:
            passo1, ok1 = f"1. A pasta {config.RAIZ_CLIPS} não foi encontrada (o disco está conectado?)", False
        else:
            passo1, ok1 = f"1. Pasta dos clips: {config.RAIZ_CLIPS}", True
        self.lbl_passo1.configure(text=("✔  " if ok1 else "✖  ") + passo1, style="Ok.TLabel" if ok1 else "Falta.TLabel")
        passo2 = ("2. Acesso ao YouTube configurado" if acesso_ok
                  else "2. Adicione o arquivo de acesso ao YouTube (client_secret.json)")
        self.lbl_passo2.configure(text=("✔  " if acesso_ok else "✖  ") + passo2,
                                  style="Ok.TLabel" if acesso_ok else "Falta.TLabel")
        if self._pronto():
            self.quadro_passos.pack_forget()
        else:
            self.quadro_passos.pack(fill="x", pady=(0, 14), before=self.quadro_resumo)

        self.var_pasta.set(str(config.RAIZ_CLIPS or ""))
        self.lbl_pasta_manual["text"] = (f"Pasta de envio manual: {config.CARPETA_PARA_SUBIR}"
                                         if config.CARPETA_PARA_SUBIR else "")
        if not acesso_ok:
            self.lbl_acesso.configure(text="✖  Falta o client_secret.json", style="Falta.TLabel")
        elif config.TOKEN.exists():
            self.lbl_acesso.configure(text="✔  Configurado e conectado à conta do canal", style="Ok.TLabel")
        else:
            self.lbl_acesso.configure(text="✔  Configurado (o navegador vai abrir para entrar na conta no "
                                           "primeiro envio)", style="Ok.TLabel")
        self.var_apagar.set(config.APAGAR_ORIGINAIS)

        self._atualizar_resumo()
        self._preencher_lista()

    def _atualizar_resumo(self):
        r = self.app.resumen()
        if r["total"] == 0:
            self.lbl_resumo["text"] = "Nenhum clip na lista ainda."
            self.barra_total["value"] = 0
        else:
            self.lbl_resumo["text"] = f"{r['no_canal']} de {r['total']} clips já estão no YouTube"
            self.barra_total["value"] = r["no_canal"] / r["total"] * 100
        faltam = r["pendentes"] + r["erros"] + r["enviando"]
        detalhe = f"Faltam {faltam}"
        if r["erros"]:
            detalhe += f" ({r['erros']} com erro: veja na aba Clips)"
        detalhe += f"   ·   Hoje: {r['envios_hoje']} de {self.app.LIMITE_SUBIDAS_DIA} envios automáticos"
        self.lbl_detalhe["text"] = detalhe

    def _atualizar_botoes(self):
        def ligar(botao, sim):
            botao.state(["!disabled"] if sim else ["disabled"])

        ligar(self.btn_enviar, not self.ocupado and self._pronto())
        ligar(self.btn_parar, self.ocupado)
        ligar(self.btn_atualizar, not self.ocupado and self._pasta_ok())

        f = self._selecionada()
        enviado = bool(f and f["video_id"] and f["estado"] in (SUBIDO, EN_PLAYLIST))
        ligar(self.btn_renomear, bool(f) and f["estado"] in (PENDIENTE, ERROR))
        ligar(self.btn_ver, bool(f) and Path(f["ruta"]).exists())
        ligar(self.btn_link, enviado)
        ligar(self.btn_youtube, enviado)
        ligar(self.btn_link_playlist, bool(self._playlist_id(f)))
        if f:
            info = f"Arquivo: {f['ruta']}"
            if f["estado"] == ERROR and f["error"]:
                info += f"\nErro: {f['error'][:300]}"
            if self.aviso[0] == f["ruta"]:
                info = f"{self.aviso[1]}\n{info}"
            self.lbl_info["text"] = info
        else:
            self.lbl_info["text"] = ""


class DialogoRenomear:
    def __init__(self, janela: Janela, ruta: Path):
        self.janela, self.ruta = janela, ruta
        self.top = top = tk.Toplevel(janela.root)
        top.title("Renomear clip")
        top.transient(janela.root)
        top.resizable(False, False)
        quadro = ttk.Frame(top, padding=16)
        quadro.pack(fill="both", expand=True)

        ttk.Label(quadro, text=f"Arquivo atual:  {ruta.name}", style="Cinza.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(quadro, text="Novo nome:").grid(row=1, column=0, columnspan=2, sticky="w", pady=(12, 2))
        self.var = tk.StringVar(value=ruta.stem)
        entrada = ttk.Entry(quadro, textvariable=self.var, width=64, font=("Segoe UI", 11))
        entrada.grid(row=2, column=0, sticky="we")
        ttk.Label(quadro, text=ruta.suffix).grid(row=2, column=1, sticky="w", padx=4)
        ttk.Label(quadro, text=DICA_NOME, style="Cinza.TLabel").grid(row=3, column=0, columnspan=2, sticky="w",
                                                                    pady=(4, 10))
        self.previa = ttk.Label(quadro, justify="left", wraplength=560)
        self.previa.grid(row=4, column=0, columnspan=2, sticky="w")

        botoes = ttk.Frame(quadro)
        botoes.grid(row=5, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(botoes, text="Ver o clip", command=lambda: os.startfile(ruta)).pack(side="left", padx=(0, 20))
        ttk.Button(botoes, text="Cancelar", command=top.destroy).pack(side="left", padx=6)
        self.btn_salvar = ttk.Button(botoes, text="Salvar", command=self.salvar)
        self.btn_salvar.pack(side="left")

        self.var.trace_add("write", lambda *_: self.atualizar_previa())
        top.bind("<Return>", lambda _: self.salvar())
        top.bind("<Escape>", lambda _: top.destroy())
        self.atualizar_previa()
        entrada.focus_set()
        entrada.select_range(0, "end")
        top.grab_set()

    def atualizar_previa(self) -> bool:
        nome = self.var.get()
        motivo = self.janela.app.validar_nombre(self.ruta, nome)
        if motivo:
            self.previa.configure(text=motivo, foreground=VERMELHO)
            self.btn_salvar.state(["disabled"])
            return False
        clip = escaneo.previa(self.ruta, nome, config.RAIZ_CLIPS)
        self.previa.configure(text=f"Título no YouTube:  {clip.titulo_base}\nPlaylist:  {clip.playlist}",
                              foreground="")
        self.btn_salvar.state(["!disabled"])
        return True

    def salvar(self):
        if not self.atualizar_previa():
            return
        nome = self.var.get()
        if nome == self.ruta.stem:
            self.top.destroy()
            return
        try:
            titulo = self.janela.app.renomear_clip(str(self.ruta), nome)
        except SystemExit as e:
            messagebox.showerror("Renomear", str(e.code), parent=self.top)
            return
        self.top.destroy()
        nueva = str(self.ruta.with_name(nome + self.ruta.suffix))
        self.janela.aviso = (nueva, f"✔ Renomeado. No YouTube vai aparecer como: {titulo}")
        self.janela._preencher_lista(selecionar=nueva)


def _data(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return iso


def abrir(app):
    Janela(app).root.mainloop()
