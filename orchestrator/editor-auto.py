# -*- coding: utf-8 -*-
"""gerar-roteiro.py — painel (GUI) da esteira roteiro-auto.

Escolhe a categoria → o card aparece num dropdown (listado do ClickUp) → escolhe a parte
(P1/P2/ambas) e as etapas → Rodar. Roda pipeline.pipeline() numa thread (uma vez por parte)
e transmite o log na tela. Abrir com o atalho "ABRIR ROTEIRO-AUTO.bat" (ou pyw -3 este arquivo).

Esteira completa: as 8 etapas (ClickUp → roteiro → personagens → narração → prompts →
imagens → capas → montagem → entrega) estão implementadas.
"""

import threading
import queue
import traceback

import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox

import config  # noqa: F401  (liga as env vars da esteira)
import pipeline
import categorias_roteiro as cats
from common import (PROJECTS_DIR, slugify, ler_modelos, salvar_modelo,
                    voz_do_canal, materiais_canal, IMG_EXTS)
from stages import magnific_seam

# Modelos do canal: (chave, rótulo, tipos de arquivo).
# capa_ref = UM vídeo de troca de capítulo já pronto (a referência do canal). A automação
# replica a MESMA animação/formato/fonte a cada capítulo, só trocando o título + a imagem.
# Substituiu os antigos campos separados de fundo e fonte (a fonte é embutida e o fundo é
# dinâmico por capítulo — não há mais nada pra o editor escolher além da referência).
MODELOS_UI = [
    ("capa_ref",  "Vídeo de referência (troca de capítulo)", [("Vídeo", "*.mp4 *.mov")]),
    ("cta_final", "CTA final (vídeo)",      [("Vídeo", "*.mp4 *.mov")]),
    ("resumo_p2", "Resumo da Parte 2 (vídeo)", [("Vídeo", "*.mp4 *.mov")]),
]

# Teaser (V.O.) — clipes drop-in POR VÍDEO (ficam em projects/<slug>/teaser/, nunca
# numa pasta compartilhada do canal — assim dois vídeos nunca misturam clipes).
TEASER_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")


def _clips_teaser(d):
    if not d or not d.is_dir():
        return []
    return [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in TEASER_EXTS]


ETAPAS = {
    1: "ClickUp + Roteiro",
    2: "Personagens + Bible",
    3: "Narração + SRT",
    4: "Prompts (imagem/capa)",
    5: "Imagens (Magnific)",
    6: "Capas de capítulo",
    7: "Montagem",
    8: "Entrega",
}
PRONTAS = {1, 2, 3, 4, 5, 6, 7, 8}

BG = "#14141c"; FG = "#ececf4"; SUB = "#9aa0b8"; ACC = "#8a7bd8"; INP = "#24242f"
STOP = "#d05555"; GO = "#3e9e57"
F_BASE = ("Segoe UI", 10); F_TIT = ("Segoe UI", 15, "bold"); F_SUB = ("Segoe UI", 9)
F_BTN = ("Segoe UI", 10, "bold"); F_LOG = ("Consolas", 9)


class Cancel:
    """Sinal de cancelamento que satisfaz os DOIS contratos da esteira:
    - pipeline.pipeline() usa como callable  -> `if cancel and cancel():`
    - runner/panel usam como Event-like      -> `cancel.is_set()`
    Um threading.Event puro quebra o primeiro; uma função pura quebra o segundo."""
    def __init__(self):
        self._e = threading.Event()

    def set(self):
        self._e.set()

    def clear(self):
        self._e.clear()

    def is_set(self):
        return self._e.is_set()

    def __call__(self):
        return self._e.is_set()


class App:
    def __init__(self, root):
        self.root = root
        self.fila = queue.Queue()
        self.cards = {}   # display -> id
        self.cancel = None   # sinal de cancelamento da run atual (Cancel() enquanto roda)
        self.ultima_run = None   # params da última rodada (p/ o botão Continuar)
        self._teaser_pendente = None   # pasta de teaser escolhida ANTES do card (aplica ao escolher o card)
        self._personagens_pendente = None   # fotos de personagem escolhidas ANTES do card (aplica ao escolher o card)
        root.title("Editor Auto — Edição Automática de Vídeo")
        root.configure(bg=BG)
        root.geometry("1000x900"); root.minsize(860, 640)

        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=BG, foreground=FG, font=F_BASE)
        st.configure("TCheckbutton", background=BG, foreground=FG, font=F_BASE)
        st.configure("TRadiobutton", background=BG, foreground=FG, font=F_BASE)
        st.map("TCheckbutton", background=[("active", BG)]); st.map("TRadiobutton", background=[("active", BG)])
        st.configure("Sub.TLabel", background=BG, foreground=SUB, font=F_SUB)
        # Combobox: no tema 'clam' o estado 'readonly' ignora fieldbackground — força via map.
        st.configure("TCombobox", fieldbackground=INP, background=INP, foreground=FG,
                     arrowsize=18, padding=(6, 4))
        st.map("TCombobox",
               fieldbackground=[("readonly", INP), ("disabled", INP), ("focus", INP), ("active", INP)],
               foreground=[("readonly", FG), ("disabled", SUB)],
               background=[("readonly", "#2c2c3c"), ("active", "#2c2c3c")],
               selectbackground=[("readonly", INP)], selectforeground=[("readonly", FG)],
               arrowcolor=[("!disabled", FG)])
        # Lista suspensa (é um Tk Listbox, não estilizado pelo ttk) — cores via option_add.
        root.option_add("*TCombobox*Listbox.background", "#1c1c28")
        root.option_add("*TCombobox*Listbox.foreground", FG)
        root.option_add("*TCombobox*Listbox.selectBackground", ACC)
        root.option_add("*TCombobox*Listbox.selectForeground", "#0e0e16")
        root.option_add("*TCombobox*Listbox.font", F_BASE)
        st.configure("Run.TButton", font=F_BTN, padding=(16, 8), background=ACC, foreground="#0e0e16")
        st.map("Run.TButton", background=[("active", "#a394f0")])
        st.configure("Stop.TButton", font=F_BTN, padding=(16, 8), background=STOP, foreground="#0e0e16")
        st.map("Stop.TButton", background=[("active", "#e06a6a"), ("disabled", "#3a2b2b")],
               foreground=[("disabled", SUB)])
        st.configure("Cont.TButton", font=F_BTN, padding=(16, 8), background=GO, foreground="#0e0e16")
        st.map("Cont.TButton", background=[("active", "#4bb268"), ("disabled", "#26372c")],
               foreground=[("disabled", SUB)])
        st.configure("Big.TButton", font=("Segoe UI", 9), padding=(10, 6), background="#2c2c3c", foreground=FG)
        st.map("Big.TButton", background=[("active", "#3a3a4e")])

        PADX = 18

        # ===== layout: botões fixos no rodapé · log com altura garantida · controles roláveis =====
        # O painel cresceu (seletor de modelo etc.) e o Tk espremia o log (widget com expand=True
        # perde espaço quando o conteúdo fixo estoura a janela). Agora os CONTROLES vivem num
        # canvas rolável (barra à direita + roda do mouse) e o LOG tem altura fixa — sempre visível
        # e rolável por dentro. Assim dá pra "subir e descer" pra ver o momento da esteira.
        wrap = tk.Frame(root, bg=BG)
        wrap.pack(side="top", fill="both", expand=True)
        canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=BG)
        _win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(_win, width=e.width))

        def _on_wheel(e):
            # roda do mouse rola os CONTROLES — exceto quando o ponteiro está sobre o log
            # (aí o próprio ScrolledText rola o histórico).
            w = getattr(e, "widget", None)
            try:
                if w is self.log or str(w).startswith(str(self.log)):
                    return
            except (AttributeError, tk.TclError):
                pass
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        root.bind_all("<MouseWheel>", _on_wheel)

        top = tk.Frame(inner, bg=BG); top.pack(fill="x", padx=PADX, pady=(16, 4))
        tk.Label(top, text="Editor Auto", bg=BG, fg=FG, font=F_TIT).pack(anchor="w")
        ttk.Label(top, text="ClickUp → Roteiro → Personagens → Narração → Imagens → Capas → Montagem → Entrega",
                  style="Sub.TLabel").pack(anchor="w", pady=(2, 0))

        ent = tk.LabelFrame(inner, text=" Entrada ", bg=BG, fg=SUB, font=F_SUB, bd=1, relief="groove")
        ent.pack(fill="x", padx=PADX, pady=10, ipady=6)
        ent.grid_columnconfigure(1, weight=1)

        tk.Label(ent, text="Categoria:", bg=BG, fg=FG, font=F_BASE).grid(row=0, column=0, sticky="w", padx=12, pady=8)
        catrow = tk.Frame(ent, bg=BG); catrow.grid(row=0, column=1, sticky="ew", padx=12, pady=8)
        self.cb_cat = ttk.Combobox(catrow, values=cats.nomes(), state="readonly", font=F_BASE, height=8)
        self.cb_cat.pack(side="left", fill="x", expand=True); self.cb_cat.current(0)
        self.cb_cat.bind("<<ComboboxSelected>>", lambda e: self._on_cat())
        ttk.Button(catrow, text="↻", width=3, style="Big.TButton", command=self.carregar_cards).pack(side="left", padx=(8, 0))

        tk.Label(ent, text="Card:", bg=BG, fg=FG, font=F_BASE).grid(row=1, column=0, sticky="w", padx=12, pady=8)
        self.cb_card = ttk.Combobox(ent, values=[], state="readonly", font=F_BASE, height=14)
        self.cb_card.grid(row=1, column=1, sticky="ew", padx=12, pady=8)
        self.cb_card.bind("<<ComboboxSelected>>", lambda e: self._on_card())

        self.v_voz = tk.BooleanVar(value=False)
        ttk.Checkbutton(ent, text="Mostrar só os cards em “voz/edição” (oficial)", variable=self.v_voz,
                        command=self.carregar_cards).grid(row=2, column=1, sticky="w", padx=10)

        tk.Label(ent, text="Slug (pasta):", bg=BG, fg=FG, font=F_BASE).grid(row=3, column=0, sticky="w", padx=12, pady=8)
        self.e_slug = tk.Entry(ent, bg=INP, fg=FG, insertbackground=FG, font=F_BASE, relief="flat")
        self.e_slug.grid(row=3, column=1, sticky="ew", padx=12, pady=8, ipady=6)

        tk.Label(ent, text="Parte:", bg=BG, fg=FG, font=F_BASE).grid(row=4, column=0, sticky="w", padx=12, pady=8)
        pf = tk.Frame(ent, bg=BG); pf.grid(row=4, column=1, sticky="w", padx=8, pady=6)
        self.v_parte = tk.StringVar(value="ambas")
        for val, txt in (("p1", "Parte 1 (isca / YouTube)"), ("p2", "Parte 2 (extensão)"), ("ambas", "Ambas")):
            ttk.Radiobutton(pf, text=txt, value=val, variable=self.v_parte).pack(side="left", padx=(4, 14))

        tk.Label(ent, text="Voz:", bg=BG, fg=FG, font=F_BASE).grid(row=5, column=0, sticky="w", padx=12, pady=(2, 8))
        self.lbl_voz = ttk.Label(ent, text="—", style="Sub.TLabel")
        self.lbl_voz.grid(row=5, column=1, sticky="w", padx=12, pady=(2, 8))

        # ---- Modelos do canal (arquivos que o editor anexa 1x por categoria) ----
        mod = tk.LabelFrame(inner, text=" Modelos do canal (por categoria) ", bg=BG, fg=SUB,
                            font=F_SUB, bd=1, relief="groove")
        mod.pack(fill="x", padx=PADX, pady=6, ipady=6)
        mod.grid_columnconfigure(1, weight=1)
        self.mod_lbl = {}
        for r, (chave, rotulo, tipos) in enumerate(MODELOS_UI):
            tk.Label(mod, text=rotulo + ":", bg=BG, fg=FG, font=F_BASE, width=22, anchor="w")\
                .grid(row=r, column=0, sticky="w", padx=12, pady=5)
            lbl = tk.Label(mod, text="—", bg=BG, fg=SUB, font=F_SUB, anchor="w")
            lbl.grid(row=r, column=1, sticky="ew", padx=6); self.mod_lbl[chave] = lbl
            ttk.Button(mod, text="Escolher…", style="Big.TButton",
                       command=lambda k=chave, t=tipos: self._escolher_modelo(k, t))\
                .grid(row=r, column=2, sticky="e", padx=(6, 4), pady=4)
            ttk.Button(mod, text="Limpar", style="Big.TButton",
                       command=lambda k=chave: self._limpar_modelo(k))\
                .grid(row=r, column=3, sticky="e", padx=(0, 12), pady=4)
        tf = tk.Frame(mod, bg=BG); tf.grid(row=len(MODELOS_UI), column=0, columnspan=4, sticky="ew", padx=12, pady=(6, 2))
        tk.Label(tf, text="Teaser (V.O.) deste vídeo:", bg=BG, fg=FG, font=F_BASE).pack(side="left")
        self.lbl_teaser = tk.Label(tf, text="—", bg=BG, fg=SUB, font=F_SUB, anchor="w")
        self.lbl_teaser.pack(side="left", padx=8)
        ttk.Button(tf, text="Limpar", style="Big.TButton",
                   command=self._limpar_teaser).pack(side="right", padx=(6, 0))
        ttk.Button(tf, text="\U0001F4C1 Abrir pasta", style="Big.TButton",
                   command=self._abrir_teaser).pack(side="right", padx=(6, 0))
        ttk.Button(tf, text="Escolher pasta…", style="Big.TButton",
                   command=self._escolher_teaser).pack(side="right", padx=(6, 0))

        # Personagens (referência) deste vídeo: as fotos dos personagens que a editora larga
        # JUNTO com o teaser. Por VÍDEO (projects/<slug>/personagens/) — as mesmas fotos que o
        # MCP do Magnific usa de referência nas imagens do corpo (Etapa 5). Prioridade sobre os
        # anexos do card na Etapa 2.
        pf = tk.Frame(mod, bg=BG); pf.grid(row=len(MODELOS_UI) + 1, column=0, columnspan=4, sticky="ew", padx=12, pady=(2, 2))
        tk.Label(pf, text="Personagens (referência):", bg=BG, fg=FG, font=F_BASE).pack(side="left")
        self.lbl_personagens = tk.Label(pf, text="—", bg=BG, fg=SUB, font=F_SUB, anchor="w")
        self.lbl_personagens.pack(side="left", padx=8)
        ttk.Button(pf, text="Limpar", style="Big.TButton",
                   command=self._limpar_personagens).pack(side="right", padx=(6, 0))
        ttk.Button(pf, text="\U0001F4C1 Abrir pasta", style="Big.TButton",
                   command=self._abrir_personagens).pack(side="right", padx=(6, 0))
        ttk.Button(pf, text="Escolher imagens…", style="Big.TButton",
                   command=self._escolher_personagens).pack(side="right", padx=(6, 0))

        # QR Code da ISCA (P1): imagem FIXA por canal, queimada no vídeo P1 inteiro. É por
        # CANAL (usa a Categoria, não o card) — o editor escolhe 1x e vale pra todo vídeo do canal.
        qf = tk.Frame(mod, bg=BG); qf.grid(row=len(MODELOS_UI) + 2, column=0, columnspan=4, sticky="ew", padx=12, pady=(2, 2))
        tk.Label(qf, text="QR Code (só Parte 1):", bg=BG, fg=FG, font=F_BASE).pack(side="left")
        self.lbl_qr = tk.Label(qf, text="—", bg=BG, fg=SUB, font=F_SUB, anchor="w")
        self.lbl_qr.pack(side="left", padx=8)
        ttk.Button(qf, text="Limpar", style="Big.TButton",
                   command=self._limpar_qr).pack(side="right", padx=(6, 0))
        ttk.Button(qf, text="\U0001F4C1 Abrir pasta", style="Big.TButton",
                   command=self._abrir_qr).pack(side="right", padx=(6, 0))
        ttk.Button(qf, text="Escolher imagem…", style="Big.TButton",
                   command=self._escolher_qr).pack(side="right", padx=(6, 0))

        # ---- Modelo de imagem do Magnific (Etapa 5) — custo vs. qualidade ----
        # SÓ afeta as imagens do CORPO (Etapa 5). A capa é vídeo (Etapa 6) e não gasta crédito.
        # A escolha grava LONGFORM_MAGNIFIC_MODE no editor-auto.env (config.salvar_env) e vale já
        # na próxima rodada — o pipeline lê a env ao vivo em magnific_seam.modo().
        img = tk.LabelFrame(inner, text=" Modelo de imagem (Magnific · Etapa 5) ", bg=BG, fg=SUB,
                            font=F_SUB, bd=1, relief="groove")
        img.pack(fill="x", padx=PADX, pady=6, ipady=6)
        img.grid_columnconfigure(1, weight=1)
        tk.Label(img, text="Modelo:", bg=BG, fg=FG, font=F_BASE).grid(row=0, column=0, sticky="w", padx=12, pady=8)
        self._img_por_label = {}
        labels = []
        atual = magnific_seam.modo()
        idents = set()
        for alias, ident, lat, nota in magnific_seam.listar_modos_body():
            rot = "%s  ·  %s" % (nota, lat)
            self._img_por_label[rot] = ident
            labels.append(rot); idents.add(ident)
        # modelo atual fora da whitelist (setado à mão no .env) → mostra como "(custom)".
        if atual not in idents:
            rot = "%s  ·  (custom — fora da lista curada)" % atual
            self._img_por_label[rot] = atual
            labels.insert(0, rot)
        self.cb_modelo_img = ttk.Combobox(img, values=labels, state="readonly", font=F_BASE, height=8)
        self.cb_modelo_img.grid(row=0, column=1, sticky="ew", padx=12, pady=8)
        sel = next((l for l, i in self._img_por_label.items() if i == atual), labels[0] if labels else "")
        if sel:
            self.cb_modelo_img.set(sel)
        self.cb_modelo_img.bind("<<ComboboxSelected>>", lambda e: self._on_modelo_img())
        ttk.Label(img, text="Só as imagens do corpo (Etapa 5). A capa é vídeo (Etapa 6) e não usa "
                  "crédito. Custo do vídeo ≈ nº de imagens × créd/img.", style="Sub.TLabel")\
            .grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 4))

        et = tk.LabelFrame(inner, text=" Etapas do pipeline ", bg=BG, fg=SUB, font=F_SUB, bd=1, relief="groove")
        et.pack(fill="x", padx=PADX, pady=6, ipady=6)
        et.grid_columnconfigure(0, weight=1); et.grid_columnconfigure(1, weight=1)
        self.vars = {}
        for i, (n, nome) in enumerate(ETAPAS.items()):
            v = tk.BooleanVar(value=(n in PRONTAS)); self.vars[n] = v
            rot = "%d · %s%s" % (n, nome, "" if n in PRONTAS else "   (em construção)")
            ttk.Checkbutton(et, text=rot, variable=v).grid(row=i // 2, column=i % 2, sticky="w", padx=14, pady=6)

        opt = tk.Frame(inner, bg=BG); opt.pack(fill="x", padx=PADX + 4)
        self.v_gates = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Modo automático (sem gates)", variable=self.v_gates).pack(anchor="w", pady=2)
        self.v_refazer = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="Refazer (limpa o artefato das etapas marcadas antes de rodar)",
                        variable=self.v_refazer).pack(anchor="w", pady=2)

        # Barra de ações + log: FORA da área rolável (parent=root), pinados no rodapé com
        # side="bottom". Ordem: bar primeiro → fica na borda de baixo; log depois → logo acima.
        bar = tk.Frame(root, bg=BG); bar.pack(side="bottom", fill="x", padx=PADX, pady=(6, 10))
        self.b_rodar = ttk.Button(bar, text="▶  Rodar", style="Run.TButton", command=self.rodar)
        self.b_rodar.pack(side="left")
        self.b_parar = ttk.Button(bar, text="⏹  Parar", style="Stop.TButton",
                                  command=self.parar, state="disabled")
        self.b_parar.pack(side="left", padx=10)
        self.b_continuar = ttk.Button(bar, text="▶▶  Continuar", style="Cont.TButton",
                                     command=self.continuar, state="disabled")
        self.b_continuar.pack(side="left")
        # "Refazer do zero": regenera o card INTEIRO (1–8, refazer=ON) num clique — o vídeo vem com
        # TODAS as mudanças de código/opções. Pede confirmação (regenera imagens = crédito Magnific).
        self.b_refazer = ttk.Button(bar, text="♻  Refazer do zero", style="Big.TButton",
                                    command=self.refazer_do_zero)
        self.b_refazer.pack(side="left", padx=10)
        ttk.Button(bar, text="\U0001F4C1  Abrir pasta do projeto", style="Big.TButton",
                   command=self.abrir_pasta).pack(side="left", padx=10)

        self.log = scrolledtext.ScrolledText(root, bg="#0e0e16", fg="#cfd2e0",
                                             insertbackground=FG, font=F_LOG, height=14, relief="flat", bd=0)
        self.log.pack(side="bottom", fill="x", padx=PADX, pady=(0, 8))
        self._log("Pronto. Escolha a categoria (carrega os cards) e clique Rodar. As 8 etapas estão prontas.")
        self.root.after(120, self._drenar)
        self._atualizar_voz_ui()
        self._atualizar_modelos_ui()
        self._atualizar_teaser_ui()
        self._atualizar_personagens_ui()
        self._atualizar_qr_ui()
        self.root.after(300, self.carregar_cards)  # carrega os cards da 1ª categoria ao abrir

    # --- log via fila (thread-safe) ---
    def _push(self, msg): self.fila.put(str(msg))

    def _drenar(self):
        try:
            while True:
                self.log.insert("end", self.fila.get_nowait() + "\n"); self.log.see("end")
        except queue.Empty:
            pass
        self.root.after(120, self._drenar)

    def _log(self, msg): self.log.insert("end", str(msg) + "\n"); self.log.see("end")

    def _on_cat(self):
        self._atualizar_voz_ui()
        self._atualizar_modelos_ui()
        self._atualizar_qr_ui()
        self.carregar_cards()

    def _atualizar_voz_ui(self):
        cat = self.cb_cat.get().strip()
        if not cat:
            return
        canal = cats.canal_de(cat)
        # Narração de VOZ ÚNICA (os Docs não têm marcador de locutor) → P1 e P2 usam a
        # mesma voz do canal (a feminina da tabela).
        nome, vid = voz_do_canal(canal, "f")
        falta = "" if vid else "  ⚠ id ainda não capturado (cai em Joanne)"
        self.lbl_voz.config(text="P1 e P2: %s%s   (canal: %s)" % (nome.title(), falta, canal))

    # --- modelos do canal (por categoria) ---
    def _atualizar_modelos_ui(self):
        cat = self.cb_cat.get().strip()
        if not cat:
            return
        d = ler_modelos(cats.canal_de(cat))
        import os
        for chave, lbl in self.mod_lbl.items():
            cam = d.get(chave, "")
            lbl.config(text=(os.path.basename(cam) if cam else "—"),
                       fg=(FG if cam else SUB))

    def _escolher_modelo(self, chave, tipos):
        cat = self.cb_cat.get().strip()
        if not cat:
            self._log("Escolha a categoria primeiro."); return
        caminho = filedialog.askopenfilename(title="Escolher modelo", filetypes=tipos + [("Todos", "*.*")])
        if not caminho:
            return
        salvar_modelo(cats.canal_de(cat), chave, caminho)
        self._atualizar_modelos_ui()
        self._push("Modelo '%s' do canal '%s' definido: %s" % (chave, cats.canal_de(cat), caminho))

    def _limpar_modelo(self, chave):
        cat = self.cb_cat.get().strip()
        if not cat:
            return
        salvar_modelo(cats.canal_de(cat), chave, "")
        self._atualizar_modelos_ui()

    # --- modelo de imagem do Magnific (Etapa 5) ---
    def _aplicar_modelo_img(self):
        """Grava a escolha do combobox em LONGFORM_MAGNIFIC_MODE (env + editor-auto.env).
        Devolve o identifier aplicado (ou None se nada selecionado)."""
        ident = self._img_por_label.get(self.cb_modelo_img.get())
        if ident:
            config.salvar_env("LONGFORM_MAGNIFIC_MODE", ident)
        return ident

    def _on_modelo_img(self):
        ident = self._aplicar_modelo_img()
        if ident:
            self._push("Modelo de imagem (Etapa 5) definido: %s (vale na próxima rodada)." % ident)

    # --- teaser (V.O.) POR VÍDEO: projects/<slug>/teaser/ ---
    def _teaser_dir(self, silencioso=False):
        """Pasta de teaser DESTE vídeo (isolada por slug). None se o slug ainda não foi definido."""
        slug = self.e_slug.get().strip()
        if not slug:
            if not silencioso:
                self._log("Escolha um card (define o slug) antes de mexer no teaser."); return None
            return None
        d = PROJECTS_DIR / slug / "teaser"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _atualizar_teaser_ui(self):
        slug = self.e_slug.get().strip()
        d = PROJECTS_DIR / slug / "teaser" if slug else None
        if not slug:
            if self._teaser_pendente:
                import os
                self.lbl_teaser.config(
                    text="pasta '%s' escolhida — aplica ao escolher o card" % os.path.basename(self._teaser_pendente),
                    fg=ACC)
            else:
                self.lbl_teaser.config(text="escolha a pasta do teaser (ou um card primeiro)", fg=SUB)
            return
        n = len(_clips_teaser(d))
        if n:
            self.lbl_teaser.config(text="%d clipe(s) neste vídeo" % n, fg=FG)
        else:
            self.lbl_teaser.config(text="nenhum clipe — escolha a pasta ou largue na pasta do vídeo", fg=SUB)

    def _copiar_teaser_de(self, pasta):
        """Copia todos os vídeos da pasta de origem (ex.: download do Vio) para a pasta de teaser
        DESTE vídeo. Requer card definido; devolve o nº de clipes copiados (ou None se sem card)."""
        d = self._teaser_dir()
        if not d:
            return None
        import os
        import shutil
        clipes = _clips_teaser(Path(pasta))
        if not clipes:
            self._push("Nenhum vídeo (%s) na pasta escolhida: %s" % ("/".join(TEASER_EXTS), pasta))
            return 0
        n = 0
        for a in clipes:
            try:
                shutil.copy2(str(a), d / a.name); n += 1
            except Exception as e:  # noqa: BLE001
                self._push("  não copiei %s: %s" % (a.name, e))
        self._push("Teaser: %d clipe(s) copiado(s) de %s → %s" % (n, pasta, d))
        self._atualizar_teaser_ui()
        return n

    def _pasta_inicial_teaser(self):
        """Abre o seletor de pasta no Downloads (onde caem os downloads do Vio), não na pasta do card."""
        import os
        home = Path(os.path.expanduser("~"))
        dl = home / "Downloads"
        return str(dl if dl.is_dir() else home)

    def _escolher_teaser(self):
        # parent=self.root: sem isso o seletor de PASTA do Windows (SHBrowseForFolder) abre
        # ATRÁS da janela do painel e parece que o botão "não faz nada".
        pasta = filedialog.askdirectory(
            parent=self.root,
            title="Escolher a PASTA do teaser (ex.: o download do Vio)",
            initialdir=self._pasta_inicial_teaser(), mustexist=True)
        if not pasta:
            return
        # Sem card ainda? guarda a escolha e aplica quando o card for selecionado.
        if not self.e_slug.get().strip():
            self._teaser_pendente = pasta
            self._push("Pasta do teaser guardada: %s — será aplicada ao escolher o card." % pasta)
            self._atualizar_teaser_ui()
            return
        self._teaser_pendente = None
        self._copiar_teaser_de(pasta)

    def _aplicar_teaser_pendente(self):
        """Chamado quando um card é escolhido: se havia pasta de teaser pendente, copia agora."""
        if self._teaser_pendente and self.e_slug.get().strip():
            pasta = self._teaser_pendente
            self._teaser_pendente = None
            self._copiar_teaser_de(pasta)

    def _limpar_teaser(self):
        self._teaser_pendente = None
        d = self._teaser_dir()
        if not d:
            self._atualizar_teaser_ui(); return
        for p in _clips_teaser(d):
            try:
                p.unlink()
            except Exception as e:  # noqa: BLE001
                self._push("  não apaguei %s: %s" % (p.name, e))
        self._push("Teaser deste vídeo esvaziado.")
        self._atualizar_teaser_ui()

    def _abrir_teaser(self):
        import os
        d = self._teaser_dir()
        if not d:
            return
        try:
            os.startfile(str(d))  # noqa: S606
        except Exception as e:  # noqa: BLE001
            self._log("Não consegui abrir a pasta: %s" % e)

    # --- Personagens (referência) POR VÍDEO: projects/<slug>/personagens/ ---
    def _personagens_dir(self, silencioso=False):
        """Pasta das fotos de personagem DESTE vídeo (isolada por slug). None se sem slug."""
        slug = self.e_slug.get().strip()
        if not slug:
            if not silencioso:
                self._log("Escolha um card (define o slug) antes de mexer nos personagens.")
            return None
        d = PROJECTS_DIR / slug / "personagens"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _imgs_personagens(d):
        if not d or not d.is_dir():
            return []
        return [p for p in sorted(d.iterdir()) if p.is_file() and p.suffix.lower() in IMG_EXTS]

    def _atualizar_personagens_ui(self):
        slug = self.e_slug.get().strip()
        d = PROJECTS_DIR / slug / "personagens" if slug else None
        if not slug:
            if self._personagens_pendente:
                self.lbl_personagens.config(
                    text="%d foto(s) escolhida(s) — aplica ao escolher o card" % len(self._personagens_pendente),
                    fg=ACC)
            else:
                self.lbl_personagens.config(text="escolha as fotos dos personagens (ou um card primeiro)", fg=SUB)
            return
        n = len(self._imgs_personagens(d))
        if n:
            self.lbl_personagens.config(text="%d foto(s) neste vídeo" % n, fg=FG)
        else:
            self.lbl_personagens.config(text="nenhuma foto — Etapa 2 cai nos anexos do card", fg=SUB)

    def _copiar_personagens(self, arquivos):
        """Copia as imagens escolhidas para a pasta de personagens DESTE vídeo.
        Requer card definido; devolve o nº copiado (ou None se sem card)."""
        d = self._personagens_dir()
        if not d:
            return None
        import os
        import shutil
        n = 0
        for a in arquivos:
            src = Path(a)
            if src.suffix.lower() not in IMG_EXTS:
                self._push("  ignorei %s (não é imagem)" % src.name); continue
            try:
                shutil.copy2(str(src), d / src.name); n += 1
            except Exception as e:  # noqa: BLE001
                self._push("  não copiei %s: %s" % (src.name, e))
        self._push("Personagens: %d foto(s) copiada(s) → %s" % (n, d))
        self._atualizar_personagens_ui()
        return n

    def _escolher_personagens(self):
        import os
        home = Path(os.path.expanduser("~")); dl = home / "Downloads"
        arquivos = filedialog.askopenfilenames(
            parent=self.root,
            title="Escolher as FOTOS dos personagens (as mesmas do teaser/VEO)",
            initialdir=str(dl if dl.is_dir() else home),
            filetypes=[("Imagem", "*.png *.webp *.jpg *.jpeg *.bmp"), ("Todos", "*.*")])
        if not arquivos:
            return
        arquivos = list(arquivos)
        # Sem card ainda? guarda a escolha e aplica quando o card for selecionado.
        if not self.e_slug.get().strip():
            self._personagens_pendente = arquivos
            self._push("%d foto(s) de personagem guardada(s) — serão aplicadas ao escolher o card." % len(arquivos))
            self._atualizar_personagens_ui()
            return
        self._personagens_pendente = None
        self._copiar_personagens(arquivos)

    def _aplicar_personagens_pendente(self):
        """Chamado ao escolher um card: se havia fotos pendentes, copia agora."""
        if self._personagens_pendente and self.e_slug.get().strip():
            arquivos = self._personagens_pendente
            self._personagens_pendente = None
            self._copiar_personagens(arquivos)

    def _limpar_personagens(self):
        self._personagens_pendente = None
        d = self._personagens_dir()
        if not d:
            self._atualizar_personagens_ui(); return
        for p in self._imgs_personagens(d):
            try:
                p.unlink()
            except Exception as e:  # noqa: BLE001
                self._push("  não apaguei %s: %s" % (p.name, e))
        self._push("Personagens deste vídeo esvaziados.")
        self._atualizar_personagens_ui()

    def _abrir_personagens(self):
        import os
        d = self._personagens_dir()
        if not d:
            return
        try:
            os.startfile(str(d))  # noqa: S606
        except Exception as e:  # noqa: BLE001
            self._log("Não consegui abrir a pasta: %s" % e)

    # --- QR Code da ISCA (P1): imagem FIXA por canal em materiais/<canal>/qr/ ---
    def _qr_dir(self, silencioso=False):
        """Pasta do QR do canal da categoria selecionada. None se não houver categoria."""
        cat = self.cb_cat.get().strip()
        if not cat:
            if not silencioso:
                self._log("Escolha a categoria antes de mexer no QR.")
            return None
        return materiais_canal(cats.canal_de(cat)) / "qr"

    @staticmethod
    def _qr_imgs(d):
        if not d or not d.is_dir():
            return []
        return [p for p in sorted(d.iterdir()) if p.is_file() and p.suffix.lower() in IMG_EXTS]

    def _atualizar_qr_ui(self):
        d = self._qr_dir(silencioso=True)
        imgs = self._qr_imgs(d)
        if imgs:
            self.lbl_qr.config(text=imgs[0].name if len(imgs) == 1
                               else "%s (+%d)" % (imgs[0].name, len(imgs) - 1), fg=FG)
        else:
            self.lbl_qr.config(text="nenhum QR neste canal — escolha a imagem (PNG enquadrado)", fg=SUB)

    def _escolher_qr(self):
        d = self._qr_dir()
        if not d:
            return
        import os
        import shutil
        home = Path(os.path.expanduser("~")); dl = home / "Downloads"
        caminho = filedialog.askopenfilename(
            title="Escolher a imagem do QR (PNG/webp já enquadrado, fundo transparente)",
            initialdir=str(dl if dl.is_dir() else home),
            filetypes=[("Imagem", "*.png *.webp *.jpg *.jpeg"), ("Todos", "*.*")])
        if not caminho:
            return
        src = Path(caminho)
        if src.suffix.lower() not in IMG_EXTS:
            self._push("Isso não é uma imagem (%s). Use PNG/webp." % src.suffix); return
        d.mkdir(parents=True, exist_ok=True)
        # QR é ÚNICO por canal: remove os anteriores antes de copiar o novo.
        for antigo in self._qr_imgs(d):
            try:
                antigo.unlink()
            except Exception as e:  # noqa: BLE001
                self._push("  não apaguei o QR antigo %s: %s" % (antigo.name, e))
        try:
            shutil.copy2(str(src), d / ("qr" + src.suffix.lower()))
        except Exception as e:  # noqa: BLE001
            self._push("Não copiei o QR: %s" % e); return
        self._push("QR do canal '%s' definido: %s (vale em toda Parte 1 deste canal)."
                   % (cats.canal_de(self.cb_cat.get().strip()), src.name))
        self._atualizar_qr_ui()

    def _limpar_qr(self):
        d = self._qr_dir()
        if not d:
            return
        for p in self._qr_imgs(d):
            try:
                p.unlink()
            except Exception as e:  # noqa: BLE001
                self._push("  não apaguei %s: %s" % (p.name, e))
        self._push("QR do canal removido.")
        self._atualizar_qr_ui()

    def _abrir_qr(self):
        import os
        d = self._qr_dir()
        if not d:
            return
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(d))  # noqa: S606
        except Exception as e:  # noqa: BLE001
            self._log("Não consegui abrir a pasta: %s" % e)

    # --- carregar cards da categoria (thread) ---
    def carregar_cards(self):
        cat = self.cb_cat.get().strip()
        so_voz = self.v_voz.get()
        self.cb_card.set(""); self.cb_card["values"] = []; self.cards = {}
        self._push("Carregando cards de '%s'%s…" % (cat, " (voz/edição)" if so_voz else ""))

        def worker():
            try:
                lst = cats.listar_cards(cat, so_voz_edicao=so_voz)
            except Exception as e:  # noqa: BLE001
                self._push("  não consegui listar: %s" % e); return
            disp = {}
            for c in lst:
                disp["[%s] %s" % (c["status"] or "?", c["name"])] = c["id"]
            def aplicar():
                self.cards = disp
                self.cb_card["values"] = list(disp.keys())
                self._push("  %d card(s)." % len(disp))
                if len(disp) == 1:
                    self.cb_card.current(0); self._on_card()
            self.root.after(0, aplicar)

        threading.Thread(target=worker, daemon=True).start()

    def _on_card(self):
        disp = self.cb_card.get()
        nome = disp.split("] ", 1)[-1] if "] " in disp else disp
        self.e_slug.delete(0, "end"); self.e_slug.insert(0, slugify(nome))
        self._aplicar_teaser_pendente()
        self._atualizar_teaser_ui()
        self._aplicar_personagens_pendente()
        self._atualizar_personagens_ui()

    def abrir_pasta(self):
        import os
        p = PROJECTS_DIR / (self.e_slug.get().strip() or "_")
        p.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(p))  # noqa: S606
        except Exception as e:  # noqa: BLE001
            self._log("Não consegui abrir a pasta: %s" % e)

    def rodar(self):
        etapas = sorted(n for n, v in self.vars.items() if v.get())
        if not etapas:
            self._log("Marque ao menos uma etapa."); return
        card = self.cards.get(self.cb_card.get())
        slug = self.e_slug.get().strip()
        cat = self.cb_cat.get().strip() or None
        if not card:
            self._log("Escolha um card no dropdown (Categoria → Card)."); return
        if not slug:
            self._log("Slug vazio."); return
        # Garante que a escolha do seletor de modelo esteja aplicada na env desta rodada.
        self._aplicar_modelo_img()
        partes = ["p1", "p2"] if self.v_parte.get() == "ambas" else [self.v_parte.get()]
        self._lancar(dict(etapas=etapas, partes=partes, card=card, slug=slug, categoria=cat,
                          pular_gates=self.v_gates.get(), refazer=self.v_refazer.get()))

    def refazer_do_zero(self):
        """Botão 'Refazer do zero': regenera o card INTEIRO (etapas 1–8, refazer=ON) num clique, no
        card/partes selecionados. Diferente do 'Rodar' normal (que só refaz sozinho as etapas
        locais 3/6/7 e reusa o resto): aqui apaga TUDO, inclusive as imagens do corpo — que voltam
        a queimar crédito no Magnific. Por isso confirma antes."""
        card = self.cards.get(self.cb_card.get())
        slug = self.e_slug.get().strip()
        cat = self.cb_cat.get().strip() or None
        if not card:
            self._log("Escolha um card no dropdown (Categoria → Card)."); return
        if not slug:
            self._log("Slug vazio."); return
        partes_lbl = "P1 + P2" if self.v_parte.get() == "ambas" else self.v_parte.get().upper()
        if not messagebox.askyesno(
                "Refazer do zero?",
                "Vai REGERAR o card inteiro (%s), etapas 1–8, apagando os artefatos atuais:\n\n"
                "• roteiro, personagens, narração, capas e montagem — reaplica TODAS as mudanças "
                "novas do código;\n"
                "• IMAGENS do corpo — regeradas no Magnific (consome CRÉDITO).\n\n"
                "Continuar?" % partes_lbl):
            return
        self._aplicar_modelo_img()
        partes = ["p1", "p2"] if self.v_parte.get() == "ambas" else [self.v_parte.get()]
        etapas = sorted(n for n in self.vars if n in PRONTAS)
        self._log("♻ Refazer do zero: card=%s | etapas %s | %s | refazer=ON" % (slug, etapas, partes))
        self._lancar(dict(etapas=etapas, partes=partes, card=card, slug=slug, categoria=cat,
                          pular_gates=self.v_gates.get(), refazer=True))

    def continuar(self):
        """Retoma a última rodada de onde parou. A esteira é idempotente por etapa
        (pula o artefato-âncora já existente), então isto NUNCA refaz o que ficou pronto."""
        if not self.ultima_run:
            self._log("Nada para continuar — rode uma vez primeiro."); return
        params = dict(self.ultima_run)
        params["refazer"] = False   # continuar jamais limpa artefatos já concluídos
        self._log("▶▶ Continuando de onde parou (etapas já concluídas serão puladas)…")
        self._lancar(params)

    def _lancar(self, params):
        self.ultima_run = dict(params)
        self.cancel = Cancel()
        self.b_rodar.config(state="disabled")
        self.b_continuar.config(state="disabled")
        self.b_refazer.config(state="disabled")
        self.b_parar.config(state="normal")
        partes = params["partes"]

        def worker():
            interrompido = False
            proj_final = None
            try:
                for pt in partes:
                    if self.cancel.is_set():
                        interrompido = True; break
                    self._push("\n===== %s | etapas %s | %s | card=%s | slug=%s ====="
                               % (pt.upper(), params["etapas"], params["categoria"],
                                  params["card"], params["slug"]))
                    proj_final = pipeline.pipeline(
                                      etapas=params["etapas"], log=self._push, cancel=self.cancel,
                                      slug=params["slug"], card_id=params["card"],
                                      categoria=params["categoria"], parte=pt,
                                      pular_gates=params["pular_gates"], refazer=params["refazer"])
            except Exception as e:  # noqa: BLE001
                interrompido = True
                if self.cancel is not None and self.cancel.is_set():
                    self._push("⏹ Esteira parada. Clique “Continuar” para retomar de onde parou.")
                else:
                    self._push("ERRO: %s" % e); self._push(traceback.format_exc())
                    self._push("↻ Clique “Continuar” para retomar após corrigir o problema.")
            finally:
                self.root.after(0, lambda i=interrompido, p=proj_final, e=list(params["etapas"]):
                                self._fim_run(i, p, e))

        threading.Thread(target=worker, daemon=True).start()

    def parar(self):
        if self.cancel is not None:
            self.cancel.set()
            self.b_parar.config(state="disabled")
            self._log("⏹ Parando… a esteira encerra ao terminar a operação atual (pode levar alguns segundos).")

    def _fim_run(self, interrompido=False, proj=None, etapas=()):
        self.b_rodar.config(state="normal")
        self.b_refazer.config(state="normal")
        self.b_parar.config(state="disabled")
        # "Continuar" só faz sentido quando a rodada NÃO terminou inteira (parada ou erro).
        self.b_continuar.config(state=("normal" if interrompido else "disabled"))
        # Só notifica quando a rodada REALMENTE fechou um vídeo (Etapa 7/8) e o final existe —
        # não incomoda quando a editora rodou só uma etapa solta (ex.: só narração).
        if interrompido or proj is None:
            return
        if not (7 in etapas or 8 in etapas):
            return
        try:
            if not proj.existe(proj.final_mp4):
                return
        except Exception:
            return
        self._notificar_pronto(proj)

    def _notificar_pronto(self, proj):
        """Vídeo montado: som + traz o painel pra frente + oferece abrir a pasta de entrega."""
        import os
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
        try:
            self.root.lift(); self.root.attributes("-topmost", True)
            self.root.after(1500, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass
        pasta = self._pasta_entrega(proj)
        if pasta and messagebox.askyesno(
                "Vídeo pronto ✓",
                "A esteira terminou a montagem do vídeo.\n\n"
                "Abrir a pasta de entrega agora?\n\n%s" % pasta):
            try:
                os.startfile(str(pasta))
            except Exception as e:  # noqa: BLE001
                self._log("Não consegui abrir a pasta: %s" % e)

    @staticmethod
    def _pasta_entrega(proj):
        """Melhor pasta pra 'abrir': ENTREGAS/<card> (P1+P2 juntas) → VIDEOS-PRONTOS → projeto."""
        try:
            from stages.s8_entrega import _card_e_parte, ENTREGAS_DIR, VIDEOS_PRONTOS_DIR
            base, _ = _card_e_parte(proj)
            d = ENTREGAS_DIR / base
            if d.is_dir():
                return d
            if VIDEOS_PRONTOS_DIR.is_dir():
                return VIDEOS_PRONTOS_DIR
        except Exception:
            pass
        try:
            return proj.dir
        except Exception:
            return None


def main():
    root = tk.Tk(); App(root); root.mainloop()


if __name__ == "__main__":
    main()
