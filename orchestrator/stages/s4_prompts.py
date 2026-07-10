# -*- coding: utf-8 -*-
"""Etapa 4 — Prompts de imagem (por capítulo) + títulos das capas.

Gera dois artefatos:

  prompts_imagens.txt  (ÂNCORA da etapa) — N prompts por capítulo (ROTEIRO_IMAGES_PER_CHAPTER),
      na ORDEM da narração. UMA linha por imagem, no formato:
          C<cap>|[Character 1: NAME] [Character 2: NAME] <descrição visual da cena>
      O prefixo `C<cap>|` carrega o capítulo da imagem (a Etapa 5 o remove antes de gerar; a
      Etapa 7 o usa pra saber a que capítulo cada imagem pertence). TODA linha começa SEMPRE com
      as duas tags dos DOIS PROTAGONISTAS ([Character 1] e [Character 2] — os personagens do
      VEO/refs, nomes idênticos ao do character_bible) — é assim que a Etapa 5 anexa a referência
      dos DOIS em cada imagem e trava a aparência deles no vídeo inteiro. O casal aparece em todo
      frame; secundários entram só pela descrição em prosa, nunca sozinhos.

  prompts_capas.txt — um título por capítulo (determinístico, do roteiro): a Etapa 6 usa como
      texto da capa de cada capítulo.

Ancoragem: os prompts são derivados do character_bible.txt (DNA visual, Etapa 2), das fotos de
referência (referencias/, o thumb HORIZONTAL = personagens) e do roteiro. NUNCA da capa vertical.
Formato do vídeo (horizontal 16:9 por padrão) vem de ROTEIRO_ASPECT.

Contrato: run(proj, log, cancel, **kw). Idempotente (âncora = prompts_imagens.txt).
"""

import os
import re
import json

from common import ErroPipeline
from runner import rodar_claude, PREAMBULO, MODELO_IMG_PROMPTS
import roteiro_estrutura


def _n_por_cap():
    try:
        return max(1, int(os.environ.get("ROTEIRO_IMAGES_PER_CHAPTER", "8")))
    except ValueError:
        return 8


def _aspect():
    return os.environ.get("ROTEIRO_ASPECT", "16:9").strip() or "16:9"


def _capitulos(proj):
    """Lista de capítulos (n, titulo, primeira_frase). Prefere capitulos.json (Etapa 3);
    cai no parse direto do roteiro.txt."""
    p = proj.dir / "capitulos.json"
    if proj.existe(p):
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("capitulos", [])
        except (OSError, ValueError):
            pass
    est = roteiro_estrutura.parse_roteiro(proj.roteiro.read_text(encoding="utf-8", errors="replace"))
    return [{"n": c["n"], "titulo": c["titulo"], "primeira_frase": c["primeira_frase"]}
            for c in est["chapters"]]


def _escrever_capas(proj, log):
    """prompts_capas.txt = um título por capítulo (determinístico, do roteiro)."""
    titulos = roteiro_estrutura.titulos_capas(
        proj.roteiro.read_text(encoding="utf-8", errors="replace"))
    proj.prompts_capas.write_text("\n".join(titulos) + "\n", encoding="utf-8")
    log("  prompts_capas.txt: %d título(s) de capa." % len(titulos))
    return titulos


def _prompt_llm(proj, caps, n_por_cap):
    """Instrução (PT) que faz o Claude ler bible+roteiro+refs e escrever prompts_imagens.txt."""
    aspect = _aspect()
    total = len(caps) * n_por_cap
    linhas_caps = "\n".join(
        "  - C%d (%s): âncora \"%s…\"" % (c["n"], c["titulo"] or ("Chapter %d" % c["n"]),
                                          (c.get("primeira_frase") or "")[:50])
        for c in caps)
    return PREAMBULO + """(sem skill — siga estas instruções diretamente)

TAREFA: escrever `prompts_imagens.txt` — os prompts de imagem que ILUSTRAM este vídeo de
romance, capítulo a capítulo, na ORDEM da narração.

ENTRADAS (leia com Read):
  - character_bible.txt  → o DNA visual e as fichas dos personagens (VERDADE de aparência).
  - roteiro.txt          → a história (gancho + capítulos "Chapter N — Título").
  - as imagens em referencias/*.png (Read cada uma) → as fotos dos PERSONAGENS (verdade de
    rosto/cabelo/roupa). NÃO existe capa vertical aqui; ancore SÓ nos personagens + bible.

CAPÍTULOS (gere EXATAMENTE %d imagens por capítulo, nesta ordem):
%s

FORMATO DE SAÍDA — `prompts_imagens.txt`, UMA linha por imagem, sem cabeçalho, sem numeração
própria, na ordem C1 (as %d imagens), depois C2, etc. Cada linha:

    C<cap>|[Character 1: NAME] [Character 2: NAME] <descrição visual da cena em inglês>

REGRAS:
- Comece SEMPRE cada linha com o prefixo do capítulo `C<cap>|` (ex.: C1|, C2|). É obrigatório.
- OS DOIS PROTAGONISTAS EM TODA IMAGEM (regra dura): logo após o prefixo, TODA linha traz SEMPRE
  as duas tags dos personagens principais — [Character 1: NAME] e [Character 2: NAME] (os mesmos
  nomes exatos do character_bible.txt; são os dois personagens do VEO/refs). Eles são o casal que
  conduz a história e a esteira ANEXA as fotos dos dois como referência em toda imagem, pra travar
  a aparência deles no vídeo inteiro. NUNCA gere um frame sem os dois — nem paisagem pura, nem
  close de um só. Se a cena é intimista de um deles, componha o outro junto (ao lado, ao fundo,
  no reflexo, entrando no quadro, over-the-shoulder) — mas os DOIS sempre visíveis.
- Personagens SECUNDÁRIOS (ex.: melhor amiga, braço-direito, ex) entram só pela DESCRIÇÃO em prosa,
  SEM tag entre colchetes (eles não têm foto de referência), e NUNCA sozinhos: o casal principal
  continua em quadro na mesma cena.
- Descrição EM INGLÊS, concreta e cinematográfica: ambiente, ação, emoção, luz, enquadramento.
  Vídeo VERTICAL %s — pense em enquadramento vertical (retrato, close, plano médio) que caiba os DOIS.
- As %d imagens de um capítulo devem COBRIR a progressão daquele capítulo (começo→clímax→fim),
  variando cenário/ação, mantendo a aparência dos dois protagonistas travada nas fichas.
- Romance sensual mas platform-safe: SEM nudez, SEM sexo explícito. Tensão, olhares, quase-beijos.
- SEM TEXTO NA IMAGEM (regra dura — a editora pegou um frame com letras na tela): a cena NUNCA pode
  conter texto/palavras/letras legíveis, legenda, título, marca d'água, logotipo, número, relógio
  digital, placa/letreiro escrito, capa de livro/jornal com título, tela de celular/computador com
  texto, ou qualquer UI. Descreva SEMPRE cenas puramente visuais. Se o ambiente pediria uma placa
  ou tela, torne-a em branco/desfocada/fora de quadro. Encerre cada linha reforçando: "no text,
  no letters, no captions, no watermark, no logos".
- NÃO gere imagens agora (nem chame Magnific); só ESCREVA o arquivo prompts_imagens.txt.
- Total esperado: %d linhas.

No resumo final, informe quantas linhas gravou e a distribuição por capítulo.
""" % (n_por_cap, linhas_caps, n_por_cap, aspect, n_por_cap, total)


def _validar(proj, caps, n_por_cap, log):
    """Confere prompts_imagens.txt: formato C<cap>|, contagem por capítulo. Loga divergências
    (não derruba — a montagem distribui o que houver)."""
    linhas = [l for l in proj.prompts_imagens.read_text(encoding="utf-8", errors="replace").splitlines()
              if l.strip()]
    por_cap = {}
    ruins = 0
    sem_dupla = 0  # linhas que não trazem os DOIS protagonistas ([Character 1] + [Character 2])
    for l in linhas:
        m = re.match(r"\s*C(\d+)\s*\|", l)
        if not m:
            ruins += 1
            continue
        por_cap[int(m.group(1))] = por_cap.get(int(m.group(1)), 0) + 1
        if not (re.search(r"\[Character\s*1\s*:", l) and re.search(r"\[Character\s*2\s*:", l)):
            sem_dupla += 1
    log("  prompts_imagens.txt: %d linha(s) | por capítulo: %s%s%s"
        % (len(linhas),
           ", ".join("C%d=%d" % (c["n"], por_cap.get(c["n"], 0)) for c in caps),
           (" | %d linha(s) sem prefixo C<n>| (serão puladas)" % ruins) if ruins else "",
           (" | ⚠ %d linha(s) SEM os dois protagonistas (a Etapa 5 anexa os dois mesmo assim)"
            % sem_dupla) if sem_dupla else ""))
    faltando = [c["n"] for c in caps if por_cap.get(c["n"], 0) == 0]
    if faltando:
        raise ErroPipeline(
            "prompts_imagens.txt não tem imagem para o(s) capítulo(s): %s. Rode a Etapa 4 de novo."
            % ", ".join("C%d" % n for n in faltando))


def run(proj, log, cancel=None, **_):
    if not proj.existe(proj.roteiro):
        raise ErroPipeline("Falta roteiro.txt (Etapa 1) para gerar prompts.")
    if not proj.existe(proj.character_bible):
        raise ErroPipeline("Falta character_bible.txt (Etapa 2) — os prompts ancoram nele.")

    caps = _capitulos(proj)
    if not caps:
        raise ErroPipeline("Nenhum capítulo achado no roteiro (linhas 'Chapter N —').")
    n_por_cap = _n_por_cap()

    _escrever_capas(proj, log)

    if proj.existe(proj.prompts_imagens):
        log("    prompts_imagens.txt já existe — geração pulada.")
        _validar(proj, caps, n_por_cap, log)
        return

    log("▶ Etapa 4 — prompts de imagem (%d por capítulo × %d caps = %d, %s, %s)..."
        % (n_por_cap, len(caps), n_por_cap * len(caps), _aspect(), MODELO_IMG_PROMPTS))
    rodar_claude(_prompt_llm(proj, caps, n_por_cap), proj.dir, log, cancel,
                 modelo=MODELO_IMG_PROMPTS, allowed_tools="Read Write")
    if not proj.existe(proj.prompts_imagens):
        raise ErroPipeline("A Etapa 4 não gerou prompts_imagens.txt. Veja o log do Claude acima.")
    _validar(proj, caps, n_por_cap, log)
    log("    ✓ prompts_imagens.txt + prompts_capas.txt prontos.")


# --- teste standalone: py -3 stages/s4_prompts.py <slug> ------------------------------
if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config  # noqa: F401
    from common import projeto_por_slug
    slug = sys.argv[1] if len(sys.argv) > 1 else "82-faxineira-bilionario"
    run(projeto_por_slug(slug), print, None)
