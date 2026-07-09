# -*- coding: utf-8 -*-
"""Etapa 5 — Imagens do corpo (Magnific, refs DIRETO por imagem).

Consome prompts_imagens.txt (Etapa 4) e gera um PNG por prompt em images/img_NNN.png,
via MCP do Magnific (claude -p), no modelo econômico do canal (flux-2-klein por padrão),
no aspecto do vídeo (ROTEIRO_ASPECT = 9:16 por padrão).

REFERÊNCIA DE PERSONAGEM = decisão da equipe: usar as fotos de referencias/ DIRETO como
`reference type=character` (subindo cada PNG como creation), SEM criar Library na conta da
Heloyse. O character_bible.txt mapeia cada [Character N: NAME] à sua foto ("Ref image:
referencias/<arquivo>"), e a instrução manda anexar essa foto ao gerar cada cena.

Trava de crédito: a geração em lote fica bloqueada até LONGFORM_MAGNIFIC_CORPO_OK=1
(mesma trava do long-form — evita queimar crédito sem querer).

Contrato: run(proj, log, cancel, **kw). Idempotente (âncora = images/*.png).
"""

import os
import re

from common import ErroPipeline
from stages import magnific_seam


def _aspect():
    return os.environ.get("ROTEIRO_ASPECT", "9:16").strip() or "9:16"


def _ler_prompts(proj):
    """Lê prompts_imagens.txt -> lista [(cap:int|None, prompt_limpo:str)] na ordem.

    Cada linha vem como `C<cap>|<prompt>`; separa o prefixo do capítulo do prompt real."""
    linhas = [l for l in proj.prompts_imagens.read_text(encoding="utf-8", errors="replace").splitlines()
              if l.strip()]
    out = []
    for l in linhas:
        m = re.match(r"\s*C(\d+)\s*\|(.*)$", l, re.DOTALL)
        if m:
            out.append((int(m.group(1)), m.group(2).strip()))
        else:
            out.append((None, l.strip()))
    return out


def _escrever_prompts_limpos(proj, prompts):
    """Grava _img_prompts.txt (numerado, sem o prefixo C<n>|) — é o que o agente Magnific lê.

    Formato: `img_NNN :: <prompt>` por linha. Determinístico e sem ambiguidade de parse."""
    p = proj.dir / "_img_prompts.txt"
    linhas = ["img_%03d :: %s" % (i, pr) for i, (_cap, pr) in enumerate(prompts, 1)]
    p.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return p


def _faltando(proj, total):
    """Lista dos números de imagem (1-based) que ainda não existem em images/."""
    return [i for i in range(1, total + 1) if not (proj.images_dir / ("img_%03d.png" % i)).exists()]


def _instrucao(proj, total, faltam, aspect):
    mode = magnific_seam.modo()
    faltam_txt = ", ".join("img_%03d" % i for i in faltam)
    return (
        "Você é a Etapa 5 (imagens do corpo do vídeo) de uma esteira de romance. NÃO peça "
        "confirmação — gere e salve os arquivos.\n\n"
        "ENTRADAS (leia com Read):\n"
        "  - `_img_prompts.txt` — %d prompts numerados `img_NNN :: <prompt>` (a ORDEM do vídeo).\n"
        "  - `character_bible.txt` — cada personagem tem uma linha `Ref image: referencias/<arquivo>` "
        "que liga o NOME à sua FOTO de referência.\n"
        "  - as fotos em `referencias/*.png` — a VERDADE de rosto/cabelo/roupa dos personagens.\n\n"
        "REFERÊNCIA DE PERSONAGEM (lock por imagem, SEM Library):\n"
        "1) UMA VEZ no início, para CADA foto em referencias/ que for usada, SUBA-a como creation e "
        "guarde o `identifier` (cache nome->identifier):\n"
        "   (a) mcp__magnific__creations_request_upload {filename:\"referencias/<arq>\", "
        "contentType:\"image/png\"} -> guarde uploadUrl e identifier;\n"
        "   (b) `Bash curl -X PUT -H \"Content-Type: image/png\" --data-binary @referencias/<arq> "
        "\"<uploadUrl>\"`;\n"
        "   (c) mcp__magnific__creations_finalize_upload {identifier:<o do passo a>}.\n"
        "2) Para CADA prompt, detecte as tags [Character N: NAME] presentes, ache no character_bible.txt "
        "a foto de cada um, e monte references=[{type:\"character\", identifier:<o creation daquela foto>}] "
        "com TODOS os personagens citados. Assim rosto/cabelo/roupa ficam idênticos às fotos.\n\n"
        "GERAÇÃO (padrão Magnific verificado — EM LOTE, 3 fases, NUNCA uma de cada vez):\n"
        "FASE 1 — DISPARE TODAS: num único turno, chame mcp__magnific__images_generate para CADA "
        "prompt que falta (%s), cada uma com {prompt:<o prompt daquela linha>, mode:\"%s\", "
        "aspectRatio:\"%s\", count:1, references:<os personagens da cena>}. aspectRatio TRAVADO em "
        "%s (vídeo vertical) — NÃO troque. Guarde o `identifier` de todos.\n"
        "FASE 2 — ESPERE TODAS: mcp__magnific__creations_wait até todas concluírem; pegue o webUrl.\n"
        "FASE 3 — BAIXE TODAS: `Bash curl -L -o images/img_NNN.png \"<webUrl>\"` — o NNN é o número "
        "da linha do prompt (img_001 -> images/img_001.png). Crie a pasta images/ se preciso.\n\n"
        "GERE SOMENTE as que faltam: %s. Se uma imagem já existe em images/, NÃO regere. "
        "Romance sensual mas platform-safe (sem nudez/sexo explícito). Se UMA falhar, re-tente só "
        "ela. No fim, imprima quantos PNGs salvou."
        % (total, faltam_txt, mode, aspect, aspect, faltam_txt)
    )


def run(proj, log, cancel=None, **_):
    if not proj.existe(proj.prompts_imagens):
        raise ErroPipeline("Falta prompts_imagens.txt (Etapa 4) para gerar as imagens.")
    prompts = _ler_prompts(proj)
    total = len(prompts)
    if total == 0:
        raise ErroPipeline("prompts_imagens.txt está vazio.")

    proj.images_dir.mkdir(parents=True, exist_ok=True)
    faltam = _faltando(proj, total)
    if not faltam:
        log("    %d imagens já existem em images/ — Etapa 5 pulada." % total)
        return

    # Trava de crédito (opt-in explícito) — evita queimar crédito Magnific sem querer.
    magnific_seam.garantir_corpo_liberado()

    _escrever_prompts_limpos(proj, prompts)
    aspect = _aspect()
    log("▶ Etapa 5 — imagens do corpo (%d total, %d faltando, modelo=%s, %s)..."
        % (total, len(faltam), magnific_seam.modo(), aspect))
    magnific_seam.gerar(proj, log, cancel, _instrucao(proj, total, faltam, aspect), modelo="sonnet")

    ainda = _faltando(proj, total)
    if ainda:
        raise ErroPipeline(
            "Etapa 5 não gerou %d imagem(ns): %s. Veja o log do Magnific acima e rode de novo "
            "(as prontas são reaproveitadas)."
            % (len(ainda), ", ".join("img_%03d" % i for i in ainda[:10])))
    log("    ✓ %d imagens em images/ (img_001..img_%03d.png)." % (total, total))


# --- teste standalone: py -3 stages/s5_imagens.py <slug> ------------------------------
if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config  # noqa: F401
    from common import projeto_por_slug
    slug = sys.argv[1] if len(sys.argv) > 1 else "82-faxineira-bilionario"
    run(projeto_por_slug(slug), print, None)
