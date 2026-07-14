# -*- coding: utf-8 -*-
"""entrega.py — empacota o resultado final de cada vídeo numa pasta limpa.

Quando a Etapa 8 termina, este módulo cria DUAS visões dos entregáveis:

1) `longform/VIDEOS-PRONTOS/<tema>.mp4` — **pasta plana**, um arquivo por projeto,
   com nome do tema do card (ex.: "01 - (VC) Gravida do alpha.mp4"). É a forma rápida
   de bater o olho e ver todos os vídeos finalizados. Usa hardlink (NTFS) quando der pra
   não duplicar gigabytes no disco; cai pra cópia se o link falhar.

2) `longform/ENTREGAS/<slug>/` — bundle completo do projeto, com nomes em pt-BR:

    ENTREGAS/<slug>/
      video_final.mp4
      thumb_capa.png         (thumb_selected.png — a escolhida no Gate 2)
      thumb_referencia.png   (thumb_ref.png — anexo do card do ClickUp)
      roteiro.pdf
      roteiro.docx
      narracao.mp3
      imagens/img_000.png                 (a capa — 1ª imagem do vídeo)
      imagens/img_001.png … img_008.png   (corpo do vídeo)
      README.txt             (título, data, link pro project original)

A Área de Trabalho ganha um atalho "Vídeos Prontos" apontando direto pra
`VIDEOS-PRONTOS/` (é o que o usuário quer ver), e um atalho "Entregas (bundle)"
apontando pra `ENTREGAS/` (pra quando precisar dos artefatos extras).

Tudo aqui é idempotente: copia por cima sem reclamar, e arquivos que faltam só
geram um aviso no log (o vídeo final é o único obrigatório).
"""

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from common import LONGFORM_DIR, SUBPROCESS_FLAGS

ENTREGAS_DIR = LONGFORM_DIR / "ENTREGAS"
VIDEOS_PRONTOS_DIR = LONGFORM_DIR / "VIDEOS-PRONTOS"

_INVALIDOS_WIN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _copy(src: Path, dst: Path, log) -> bool:
    if not src.exists() or (src.is_file() and src.stat().st_size == 0):
        log("    [entrega] pulando %s (não existe ou vazio)." % src.name)
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return True


def _nome_amigavel(proj) -> str:
    """Devolve um nome de arquivo legível ("01 - (VC) Gravida do alpha") pro vídeo plano.

    Usa o NOME LITERAL do card no ClickUp (`card_nome`, ex.: "01 - Grávida do Alpha") — é o
    pedido do usuário: o vídeo final leva o número + título exatamente como está no card.
    Cai pro `tema` (texto digitado pra lançar) e depois pro slug. Sanitiza só caracteres
    ilegais no Windows — preserva acentos e espaços.
    """
    nome = proj.dir.name
    try:
        if proj.source.exists():
            src = json.loads(proj.source.read_text(encoding="utf-8", errors="replace"))
            nome = (src.get("card_nome") or src.get("tema") or src.get("titulo")
                    or src.get("title") or nome)
    except Exception:
        pass
    nome = _INVALIDOS_WIN.sub("_", str(nome)).strip().rstrip(".")
    return nome or proj.dir.name


def _publicar_video_plano(proj, log) -> Path | None:
    """Coloca `out/final.mp4` em VIDEOS-PRONTOS/<tema>.mp4. Hardlink quando der."""
    if not proj.existe(proj.final_mp4):
        log("    [entrega] sem final.mp4 — pulando VIDEOS-PRONTOS.")
        return None
    VIDEOS_PRONTOS_DIR.mkdir(parents=True, exist_ok=True)
    destino = VIDEOS_PRONTOS_DIR / (_nome_amigavel(proj) + ".mp4")
    if destino.exists():
        try:
            destino.unlink()
        except Exception:
            pass
    try:
        os.link(proj.final_mp4, destino)
        log("    🎬 VIDEOS-PRONTOS/%s (hardlink, 0 MB extra)." % destino.name)
    except OSError:
        shutil.copyfile(proj.final_mp4, destino)
        log("    🎬 VIDEOS-PRONTOS/%s (cópia)." % destino.name)
    return destino


def _escrever_readme(proj, destino: Path) -> None:
    titulo = proj.dir.name
    try:
        if proj.source.exists():
            src = json.loads(proj.source.read_text(encoding="utf-8", errors="replace"))
            titulo = src.get("titulo") or src.get("title") or titulo
    except Exception:
        pass
    agora = datetime.now().strftime("%Y-%m-%d %H:%M")
    linhas = [
        "TÍTULO: %s" % titulo,
        "SLUG  : %s" % proj.dir.name,
        "DATA  : %s" % agora,
        "",
        "Conteúdo desta pasta:",
        "  - video_final.mp4        → o vídeo pronto (out/final.mp4 do projeto).",
        "  - thumb_capa.png         → a thumb escolhida no Gate 2.",
        "  - thumb_referencia.png   → a thumb anexada ao card do ClickUp (referência).",
        "  - roteiro.pdf / .docx    → roteiro entregue à equipe.",
        "  - narracao.mp3           → áudio de narração (TTS).",
        "  - imagens/               → a capa (img_000, 1ª imagem) + as 8 do corpo (img_001..008).",
        "",
        "Projeto original (com todos os artefatos intermediários):",
        "  %s" % str(proj.dir),
    ]
    (destino / "README.txt").write_text("\n".join(linhas) + "\n", encoding="utf-8")


def montar_entrega(proj, log) -> Path:
    """Monta `ENTREGAS/<slug>/` com os entregáveis do projeto. Retorna o destino."""
    destino = ENTREGAS_DIR / proj.dir.name
    destino.mkdir(parents=True, exist_ok=True)

    _copy(proj.final_mp4, destino / "video_final.mp4", log)
    _copy(proj.thumb_selected, destino / "thumb_capa.png", log)
    _copy(proj.thumb_ref, destino / "thumb_referencia.png", log)
    _copy(proj.roteiro_pdf, destino / "roteiro.pdf", log)
    _copy(proj.roteiro_docx, destino / "roteiro.docx", log)
    _copy(proj.narration_mp3, destino / "narracao.mp3", log)

    dst_img = destino / "imagens"
    dst_img.mkdir(exist_ok=True)
    n_img = 0
    for img in sorted(proj.images_dir.glob("img_*.png")):
        shutil.copyfile(img, dst_img / img.name)
        n_img += 1

    _escrever_readme(proj, destino)
    log("    📦 Entrega pronta: %s (%d imagens)." % (destino, n_img))

    _publicar_video_plano(proj, log)
    return destino


def _criar_atalho(nome_lnk: str, alvo: Path, descricao: str, log=None) -> Path | None:
    if os.name != "nt":
        return None
    alvo.mkdir(parents=True, exist_ok=True)
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    if not desktop.is_dir():
        desktop = Path.home() / "Desktop"
    if not desktop.is_dir():
        return None
    lnk = desktop / nome_lnk
    if lnk.exists():
        return lnk
    try:
        import subprocess
        ps = (
            "$ws = New-Object -ComObject WScript.Shell;"
            "$sc = $ws.CreateShortcut('%s');"
            "$sc.TargetPath = '%s';"
            "$sc.IconLocation = 'imageres.dll,3';"
            "$sc.Description = '%s';"
            "$sc.Save();"
        ) % (
            str(lnk).replace("'", "''"),
            str(alvo).replace("'", "''"),
            descricao.replace("'", "''"),
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False,
                       capture_output=True, timeout=15, **SUBPROCESS_FLAGS)
        if lnk.exists():
            if log:
                log("    🔗 Atalho criado: %s" % lnk)
            return lnk
    except Exception as e:
        if log:
            log("    [entrega] não consegui criar atalho %s: %s" % (nome_lnk, e))
    return None


def criar_atalho_desktop(log=None) -> Path | None:
    """Cria DOIS atalhos na Área de Trabalho: VIDEOS-PRONTOS (principal) e ENTREGAS (bundle).

    Idempotente: se o .lnk já existe, não recria.
    """
    principal = _criar_atalho(
        "Vídeos Prontos.lnk", VIDEOS_PRONTOS_DIR,
        "Pasta plana com todos os vídeos finalizados (long-form)", log)
    _criar_atalho(
        "Vídeos de Romance — Entregas.lnk", ENTREGAS_DIR,
        "Bundle completo por projeto (vídeo + thumb + roteiro + imagens)", log)
    return principal
