# -*- coding: utf-8 -*-
"""Etapa 5 — Imagens do corpo (Magnific, refs DIRETO por imagem).

Consome prompts_imagens.txt (Etapa 4) e gera um PNG por prompt em images/img_NNN.png,
via MCP do Magnific (claude -p), no modelo econômico do canal (flux-2-klein por padrão),
no aspecto do vídeo (ROTEIRO_ASPECT = 16:9 por padrão).

REFERÊNCIA DE PERSONAGEM = decisão da equipe: usar as fotos de referencias/ DIRETO como
`reference type=character` (subindo cada PNG como creation), SEM criar Library na conta da
Heloyse. O character_bible.txt mapeia cada [Character N: NAME] à sua foto ("Ref image:
referencias/<arquivo>"), e a instrução manda anexar essa foto ao gerar cada cena.

OS DOIS PROTAGONISTAS EM TODA IMAGEM (2026-07-09): as fotos dos DOIS personagens principais
([Character 1] + [Character 2] — os do VEO) vão como referência em TODA imagem, sempre — mesmo
que a linha do prompt cite só um deles. É assim que a aparência dos dois fica idêntica às fotos
do VEO no vídeo inteiro (o casal conduz a história e recorre em todos os frames). Refs deduplicadas
por identifier (se os dois compartilham a mesma foto, anexa uma vez só).

Trava de crédito: a geração em lote fica bloqueada até LONGFORM_MAGNIFIC_CORPO_OK=1
(mesma trava do long-form — evita queimar crédito sem querer).

Contrato: run(proj, log, cancel, **kw). Idempotente (âncora = images/*.png).
"""

import os
import re

from common import ErroPipeline
from stages import magnific_seam
from stages import qa_imagens


# Regra ANTI-BUG injetada em TODA geração de imagem do corpo (1ª passada e regeneração).
# Ataca a causa-raiz do bug de character-merge: forçar as DUAS referências de personagem em
# TODA cena faz o modelo às vezes FUNDIR os dois (ex.: o reflexo no espelho da mulher saiu com
# o rosto do homem vestindo o vestido dela). Estas frases mantêm a regra dos 2 protagonistas
# como referência, mas travam a SEPARAÇÃO de identidade/roupa e a anatomia correta.
_ANTIBUG = (
    "REGRA ANTI-BUG (CRÍTICA — vale para TODA imagem, mesmo com as 2 referências anexadas):\n"
    "• CADA personagem mantém o PRÓPRIO rosto, cabelo e roupa. NUNCA troque nem misture a roupa "
    "ou o rosto de um personagem no outro. As duas fotos são REFERÊNCIA de aparência — não "
    "significa colar os dois em toda cena.\n"
    "• Se a linha do prompt descreve só UMA pessoa na cena, gere só UMA pessoa. NÃO duplique o "
    "outro protagonista nem funda os dois num corpo só. O 2º personagem entra como referência de "
    "identidade, não precisa aparecer na cena se o prompt não pede.\n"
    "• ESPELHO/REFLEXO/VIDRO: qualquer reflexo tem de mostrar a MESMA pessoa, com a MESMA roupa e "
    "pose de quem está na frente — nunca a outra pessoa nem outra roupa.\n"
    "• ANATOMIA HUMANA CORRETA: exatamente 2 braços, 2 pernas, 1 cabeça por pessoa, mãos com 5 "
    "dedos; sem membros/dedos a mais ou a menos, sem corpos fundidos, sem rosto derretido.\n"
    "• SEM texto, legenda, subtítulo, marca-d'água, logo ou número renderizado na imagem.\n"
)


def _aspect():
    return os.environ.get("ROTEIRO_ASPECT", "16:9").strip() or "16:9"


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
        "2) OS DOIS PROTAGONISTAS EM TODA IMAGEM: para CADA prompt, monte references SEMPRE com os "
        "DOIS personagens principais — [Character 1] e [Character 2] do character_bible.txt — "
        "ANEXANDO a foto de cada um, MESMO que a linha do prompt cite só um deles (ou nenhum). "
        "Some ainda qualquer outro [Character N: NAME] citado. Formato "
        "references=[{type:\"character\", identifier:<o creation daquela foto>}, ...], type:\"character\" "
        "pra cada. DEDUPLIQUE por identifier — se dois personagens apontam pra MESMA foto de "
        "referência, anexe essa foto uma vez só. Assim rosto/cabelo/roupa dos dois ficam idênticos "
        "às fotos do VEO em todas as cenas.\n\n"
        "GERAÇÃO (padrão Magnific verificado — EM LOTE, 3 fases, NUNCA uma de cada vez):\n"
        "FASE 1 — DISPARE TODAS: num único turno, chame mcp__magnific__images_generate para CADA "
        "prompt que falta (%s), cada uma com {prompt:<o prompt daquela linha>, mode:\"%s\", "
        "aspectRatio:\"%s\", count:1, references:<SEMPRE os dois protagonistas + secundários citados>}. "
        "aspectRatio TRAVADO em "
        "%s (formato do canal) — NÃO troque. Guarde o `identifier` de todos.\n"
        "FASE 2 — ESPERE TODAS: mcp__magnific__creations_wait até todas concluírem; pegue o webUrl.\n"
        "FASE 3 — BAIXE TODAS: `Bash curl -L -o images/img_NNN.png \"<webUrl>\"` — o NNN é o número "
        "da linha do prompt (img_001 -> images/img_001.png). Crie a pasta images/ se preciso.\n\n"
        "%s\n"
        "GERE SOMENTE as que faltam: %s. Se uma imagem já existe em images/, NÃO regere. "
        "Romance sensual mas platform-safe (sem nudez/sexo explícito). Se UMA falhar, re-tente só "
        "ela. No fim, imprima quantos PNGs salvou."
        % (total, faltam_txt, mode, aspect, aspect, _ANTIBUG, faltam_txt)
    )


def _instrucao_regen(proj, total, faltam, aspect, issues):
    """Instrução de REGENERAÇÃO das imagens que o QA reprovou por bug.

    Igual à `_instrucao`, mas (a) diz que estas FALHARAM no QA e por quê, e (b) manda evitar
    exatamente aquele bug. Os PNGs bugados já foram apagados, então `faltam` = as reprovadas."""
    base = _instrucao(proj, total, faltam, aspect)
    linhas_bug = "\n".join("  - img_%03d: %s" % (n, issues.get(str(n), "bug de geração"))
                           for n in faltam)
    aviso = (
        "\n\nATENÇÃO — REGERAÇÃO PÓS-QA: as imagens abaixo foram REPROVADAS por BUG de geração e "
        "APAGADAS. Gere-as DE NOVO evitando EXATAMENTE o bug apontado (aplique a REGRA ANTI-BUG "
        "acima com rigor máximo — separação de identidade/roupa entre os personagens, espelho "
        "coerente, anatomia correta, sem texto):\n%s"
        % linhas_bug
    )
    return base + aviso


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

    # QA VISUAL + REGENERAÇÃO: abre cada imagem, detecta bug de geração (identidade trocada,
    # espelho incoerente, anatomia quebrada, texto) e regera só as bugadas — até N rodadas.
    # Só checa o que foi (re)gerado AGORA (as que já existiam foram aprovadas em rodadas
    # anteriores). Vale P1 e P2 (a P2 roda esta mesma etapa na pasta irmã) e todas as categorias.
    _qa_regen(proj, log, cancel, total, aspect, checar=faltam)


def _qa_regen(proj, log, cancel, total, aspect, checar):
    """Loop QA→apaga bugadas→regera, até ROTEIRO_IMG_QA_MAX_ROUNDS rodadas. Nunca derruba a
    esteira: se sobrar bug após as rodadas, só avisa alto (imagem presente não bloqueia)."""
    try:
        max_rounds = int(float(os.environ.get("ROTEIRO_IMG_QA_MAX_ROUNDS", "2")))
    except (TypeError, ValueError):
        max_rounds = 2
    if max_rounds < 1 or not qa_imagens.ligado():
        return

    for rodada in range(1, max_rounds + 1):
        if cancel is not None and cancel.is_set():
            return
        veredito = qa_imagens.avaliar(proj, log, cancel, checar=checar)
        if veredito is None:              # QA errou / sem arquivo → degrada sem bloquear
            return
        bugadas = veredito.get("bugged") or []
        if not bugadas:                   # tudo limpo (ou QA desligado)
            return
        if rodada == max_rounds:
            log("    ⚠ %d imagem(ns) ainda bugada(s) após %d rodada(s) de regeneração: %s. "
                "Revise à mão ou rode a Etapa 5 de novo (ROTEIRO_IMG_QA_MAX_ROUNDS aumenta as "
                "tentativas)."
                % (len(bugadas), max_rounds, ", ".join("img_%03d" % n for n in bugadas)))
            return
        issues = veredito.get("issues") or {}
        for n in bugadas:                 # apaga as bugadas → viram 'faltando' pra regerar
            try:
                (proj.images_dir / ("img_%03d.png" % n)).unlink()
            except OSError:
                pass
        log("    ♻ Regenerando %d imagem(ns) bugada(s) (rodada %d/%d): %s"
            % (len(bugadas), rodada, max_rounds, ", ".join("img_%03d" % n for n in bugadas)))
        magnific_seam.gerar(proj, log, cancel,
                            _instrucao_regen(proj, total, bugadas, aspect, issues),
                            modelo="sonnet")
        refeitas = [n for n in bugadas
                    if (proj.images_dir / ("img_%03d.png" % n)).exists()]
        if not refeitas:
            log("    ⚠ Regeneração não produziu nenhum PNG novo — parando o loop de QA.")
            return
        checar = refeitas                 # na próxima rodada só re-checa o que foi regerado


# --- teste standalone: py -3 stages/s5_imagens.py <slug> ------------------------------
if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config  # noqa: F401
    from common import projeto_por_slug
    slug = sys.argv[1] if len(sys.argv) > 1 else "82-faxineira-bilionario"
    run(projeto_por_slug(slug), print, None)
