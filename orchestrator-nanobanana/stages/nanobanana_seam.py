# -*- coding: utf-8 -*-
"""SEAM do Nano Banana (geração de imagem via MCP GRÁTIS do Google Gemini).

Substituto do `magnific_seam.py` SÓ para a geração de imagem do CORPO (Etapa 5) nesta
cópia experimental (`orchestrator-nanobanana`). A esteira ORIGINAL (`orchestrator/`) segue
100% no Magnific — esta pasta existe para o comparativo A/B "Magnific (oficial) × Nano Banana
(grátis)".

POR QUE: o Magnific COBRA crédito por imagem via MCP mesmo na conta "unlimited" (o ilimitado
só vale no painel web). O seedream-4 usado hoje custa 50 créd/img (~2.500/vídeo). O Nano
Banana 2, via API grátis do Google (Google AI Studio), gera com consistência de personagem
de ponta a CUSTO ZERO dentro da cota grátis (~500 img/dia por chave). 8 vídeos/dia (~400 img)
cabem numa chave só.

CONTRATO DO MCP (servidor `mcp__nanobanana`, wrapper do Gemini — ex.: zhongweili/
nanobanana-mcp-server):
  - mcp__nanobanana__generate_image(prompt, model_tier, aspect_ratio, resolution,
        output_path, n) -> SÍNCRONO: gera e SALVA o PNG direto no `output_path`.
        NÃO há upload/wait/download como no Magnific — é 1 chamada por imagem.
  - mcp__nanobanana__edit_image(...)  -> edição / geração guiada por imagem de referência.
  - mcp__nanobanana__upload_file(...) -> sobe arquivo (Gemini Files API) p/ usar como ref.
  Autenticação: env GEMINI_API_KEY (chave grátis do Google AI Studio, SEM billing — assim o
  teto é a cota grátis e NUNCA vira cobrança).

⚠️ A VERIFICAR NA 1ª CONEXÃO (a doc do wrapper não fixa o nome do parâmetro de imagem de
referência): confirme, com `mcp__nanobanana__` no schema real, COMO se passa as 2 fotos de
referência do casal (provável: `reference_images`/`image_paths`/`input_images`, ou via
`upload_file` + `edit_image`). A instrução abaixo é ADAPTATIVA — manda o sub-agente ler o
schema e usar o parâmetro certo — mas rode 1 imagem de teste e confira a consistência antes
do lote. Ver README-NANOBANANA.md.

Ative com env NANOBANANA_MCP=mcp__nanobanana (config.py desta cópia já faz de fábrica).
"""

import os

from common import ErroPipeline
from runner import rodar_claude

# Model tier do Nano Banana (nb2 = Gemini 3.1 Flash Image, o workhorse; 'pro' = Nano Banana
# Pro, mais caro em cota; 'flash'/'auto' existem). nb2 é o sweet spot de consistência × cota.
DEFAULT_MODEL = "nb2"

# Ferramentas do MCP do Nano Banana liberadas no --allowedTools. Como é wrapper de terceiro e
# o nome exato das tools pode variar por versão, liberamos o SERVIDOR INTEIRO pelo prefixo
# (o Claude Code aceita `mcp__nanobanana` p/ liberar todas as tools daquele MCP) + Read/Write/
# Bash (Bash cria a pasta images/ se preciso e confere os arquivos).


def _prefixo():
    pref = os.environ.get("NANOBANANA_MCP", "").strip()
    if not pref:
        raise ErroPipeline(
            "Nano Banana não conectado. Conecte o MCP do Nano Banana (wrapper do Gemini) e "
            "exporte NANOBANANA_MCP (ex.: 'mcp__nanobanana'), além de GEMINI_API_KEY (chave "
            "grátis do Google AI Studio, SEM billing). Depois rode a etapa de novo."
        )
    return pref


def modo():
    return os.environ.get("NANOBANANA_MODEL", DEFAULT_MODEL)


def garantir_corpo_liberado():
    """Simétrico ao magnific_seam.garantir_corpo_liberado(), MAS o Nano Banana é GRÁTIS dentro
    da cota — então NÃO há trava de crédito. Só valida que o MCP e a chave estão configurados.

    Mantido o mesmo nome/contrato para o s5_imagens desta cópia poder chamar o seam sem saber
    qual backend está por trás. Se um dia você quiser uma trava simbólica (ex.: confirmar que a
    chave é sem-billing antes de rodar em lote), é aqui que ela entraria."""
    _prefixo()
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        raise ErroPipeline(
            "GEMINI_API_KEY ausente. O MCP do Nano Banana precisa da chave grátis do Google AI "
            "Studio para gerar. Use uma chave de conta Google SEM billing (o teto vira a cota "
            "grátis ~500 img/dia e nunca vira cobrança). Exporte GEMINI_API_KEY e rode de novo."
        )


def allowed_tools():
    """--allowedTools liberando Read/Write/Bash + o servidor inteiro do Nano Banana."""
    pref = _prefixo()
    # Prefixo do servidor libera todas as tools dele (generate_image, edit_image, upload_file…);
    # como o wrapper é de terceiro e pode renomear tools entre versões, isto é mais robusto que
    # enumerar cada uma.
    return "Read Write Bash " + pref


def gerar(proj, log, cancel, instrucoes, modelo="sonnet"):
    """Roda um claude -p com o MCP do Nano Banana seguindo `instrucoes`.

    Mesma assinatura de magnific_seam.gerar — o s5_imagens desta cópia chama sem distinção."""
    return rodar_claude(instrucoes, proj.dir, log, cancel,
                        modelo=modelo, allowed_tools=allowed_tools())
