# TESTAR-NANOBANANA.ps1 — roda SÓ a Etapa 5 (imagens do corpo) do clone no Nano Banana grátis.
#
# PRÉ-REQUISITOS (uma vez só — passos interativos SEUS, não dá pra automatizar daqui):
#   1) Chave grátis do Gemini (conta Google SEM billing):  https://aistudio.google.com/app/apikey
#   2) Registrar o MCP grátis no Claude Code com o nome 'nanobanana' (vira o prefixo mcp__nanobanana):
#        claude mcp add nanobanana -e GEMINI_API_KEY=SUACHAVE -- uvx nanobanana-mcp-server@latest
#      (precisa do 'uv': pip install uv. Alternativa sem uv:
#        pip install nanobanana-mcp-server
#        claude mcp add nanobanana -e GEMINI_API_KEY=SUACHAVE -- python -m nanobanana_mcp_server.server )
#   3) Confirme que apareceu:  claude mcp list   (tem que listar 'nanobanana')
#
# USO:
#   $env:GEMINI_API_KEY = "SUACHAVE"
#   ./TESTAR-NANOBANANA.ps1
#
# Gera projects/_teste-nanobanana/images/img_001.png e img_002.png (reflexo no vidro + os dois juntos).

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not $env:GEMINI_API_KEY) {
  Write-Host "ERRO: exporte a chave antes:  `$env:GEMINI_API_KEY = 'SUACHAVE'" -ForegroundColor Red
  Write-Host "Chave grátis (conta SEM billing): https://aistudio.google.com/app/apikey"
  exit 1
}

# Backend do clone = Nano Banana (config.py já traz os defaults, reforçamos aqui por clareza).
$env:NANOBANANA_MCP   = "mcp__nanobanana"
$env:NANOBANANA_MODEL = "nb2"          # nb2 = Nano Banana 2 (o que o tier grátis entrega)
$env:ROTEIRO_ASPECT   = "9:16"         # casa com os prompts verticais do P1 (troque p/ 16:9 se quiser)

Write-Host ">> Rodando a Etapa 5 do clone (Nano Banana) no projeto _teste-nanobanana..." -ForegroundColor Cyan
py -3 "stages/s5_imagens.py" "_teste-nanobanana"

Write-Host ""
Write-Host ">> Pronto. Veja o resultado em:  projects/_teste-nanobanana/images/" -ForegroundColor Green
Write-Host "   Cheque: rosto/cabelo/roupa do casal batem com as fotos? O reflexo (img_001) é a MESMA pessoa da frente?"
