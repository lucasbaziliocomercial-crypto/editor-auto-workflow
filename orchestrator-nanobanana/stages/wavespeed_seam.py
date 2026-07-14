# -*- coding: utf-8 -*-
"""SEAM do WaveSpeed (Seedream via MCP OFICIAL) — substituto do backend de imagem.

WaveSpeed tem MCP OFICIAL (pip `wavespeed-mcp`, servidor da própria empresa — muito mais
confiável que wrappers de terceiro) + API REST. O modelo é configurável por env, então
apontamos pro SEEDREAM (a qualidade que a operação já aprova) em vez do Flux default.

CONTRATO (servidor `mcp__wavespeed`, oficial):
  - Ferramentas de imagem (text-to-image / image-to-image). O endpoint do modelo vem das envs
    do próprio MCP: WAVESPEED_API_TEXT_TO_IMAGE_ENDPOINT / WAVESPEED_API_IMAGE_TO_IMAGE_ENDPOINT
    (default flux-dev / flux-kontext-pro) — nós sobrescrevemos pro Seedream no registro do MCP.
  - Auth: env WAVESPEED_API_KEY (do painel WaveSpeed). Cobra por imagem (~$0,035 Seedream 5 Lite).
  - WAVESPEED_API_RESOURCE_MODE=url|local|base64 controla como o resultado volta.

⚠️ A VERIFICAR na 1ª conexão (a doc não fixa 100%): (a) os NOMES exatos das tools de imagem do
mcp__wavespeed; (b) como se passa a FOTO de referência do casal (image-to-image); (c) o path
exato do endpoint Seedream. A instrução do s5 é adaptativa (manda o agente ler o schema real),
mas rode 1 imagem de teste e confira antes do lote. Ver TESTAR-WAVESPEED.ps1.

Ativa com env WAVESPEED_MCP=mcp__wavespeed (config.py desta cópia já traz o default).
"""

import os

from common import ErroPipeline
from runner import rodar_claude

DEFAULT_MODEL = "seedream-v5.0-lite"  # informativo (o endpoint real vem das envs do MCP)


def _prefixo():
    pref = os.environ.get("WAVESPEED_MCP", "").strip()
    if not pref:
        raise ErroPipeline(
            "WaveSpeed não conectado. Instale e registre o MCP OFICIAL: "
            "`pip install wavespeed-mcp` e `claude mcp add --scope user wavespeed ...` "
            "(ver TESTAR-WAVESPEED.ps1). Exporte WAVESPEED_MCP (ex.: 'mcp__wavespeed') e "
            "WAVESPEED_API_KEY. Depois rode a etapa de novo."
        )
    return pref


def modo():
    return os.environ.get("WAVESPEED_MODEL", DEFAULT_MODEL)


def garantir_corpo_liberado():
    """Simétrico ao magnific_seam — mas o WaveSpeed cobra por imagem (~$0,035 Seedream 5 Lite),
    então só validamos MCP + chave (o controle de gasto é o saldo da conta WaveSpeed)."""
    _prefixo()
    if not os.environ.get("WAVESPEED_API_KEY", "").strip():
        raise ErroPipeline(
            "WAVESPEED_API_KEY ausente. O WaveSpeed cobra por imagem (~$0,035 Seedream 5 Lite). "
            "Confirme saldo/plano na conta WaveSpeed, exporte WAVESPEED_API_KEY e rode de novo."
        )


def allowed_tools():
    """--allowedTools liberando Read/Write/Bash + o servidor inteiro do WaveSpeed."""
    pref = _prefixo()
    return "Read Write Bash " + pref


def gerar(proj, log, cancel, instrucoes, modelo="sonnet"):
    """Roda um claude -p com o MCP oficial do WaveSpeed seguindo `instrucoes`.
    Mesma assinatura de magnific_seam.gerar — o s5_imagens chama sem distinção."""
    return rodar_claude(instrucoes, proj.dir, log, cancel,
                        modelo=modelo, allowed_tools=allowed_tools())
