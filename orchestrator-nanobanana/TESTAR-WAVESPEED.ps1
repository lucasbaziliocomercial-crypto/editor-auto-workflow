# TESTAR-WAVESPEED.ps1 — instala o MCP OFICIAL do WaveSpeed, registra no Claude Code e roda a
# Etapa 5 (imagens do corpo, modelo Seedream) no casal do P1.
#
# PRE-REQUISITO (passo seu, uma vez):
#   1) Conta em https://wavespeed.ai  + adicionar saldo (o WaveSpeed COBRA por imagem, ~$0,035
#      Seedream 5 Lite). Uns poucos dolares cobrem centenas de imagens de teste.
#   2) Pegar a API key no painel do WaveSpeed.
#
# USO:
#   $env:WAVESPEED_API_KEY = "SUA_KEY"
#   ./TESTAR-WAVESPEED.ps1
#
# O que faz: pip install wavespeed-mcp -> registra o MCP (escopo user, apontado pro Seedream)
# -> roda a Etapa 5 do clone no projeto _teste-nanobanana (que tem as fotos do casal).

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"

if (-not $env:WAVESPEED_API_KEY) {
  Write-Host "ERRO: exporte a chave antes:  `$env:WAVESPEED_API_KEY = 'SUA_KEY'" -ForegroundColor Red
  Write-Host "Conta + saldo + key: https://wavespeed.ai"
  exit 1
}

# ENDPOINTS DO SEEDREAM NO WAVESPEED — ⚠️ CONFIRME os slugs exatos em https://wavespeed.ai/models
# (o formato e /<provedor>/<modelo>; o default do MCP e /wavespeed-ai/flux-dev). Ajuste se preciso.
$T2I = "/bytedance/seedream-v5.0-lite"                 # text-to-image
$I2I = "/bytedance/seedream-v5.0-lite-sequential"      # image-to-image (referencia/consistencia)

Write-Host ">> 1/3 Instalando o MCP oficial (wavespeed-mcp)..." -ForegroundColor Cyan
py -3 -m pip install --upgrade wavespeed-mcp 2>&1 | Select-Object -Last 3

# Acha o executavel (o dir Scripts costuma NAO estar no PATH)
$exe = Get-ChildItem "$env:LOCALAPPDATA\Python\*\Scripts\wavespeed-mcp.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $exe) { $exe = (Get-Command wavespeed-mcp -ErrorAction SilentlyContinue).Source }
if (-not $exe) { Write-Host "Nao achei wavespeed-mcp.exe apos o pip install. Rode: py -3 -m pip show wavespeed-mcp" -ForegroundColor Red; exit 1 }
Write-Host ("   exe: "+$exe)

Write-Host ">> 2/3 Registrando o MCP no Claude Code (escopo user, apontado pro Seedream)..." -ForegroundColor Cyan
claude mcp remove wavespeed 2>$null
claude mcp add --scope user wavespeed -e WAVESPEED_API_KEY=$env:WAVESPEED_API_KEY -e WAVESPEED_API_TEXT_TO_IMAGE_ENDPOINT=$T2I -e WAVESPEED_API_IMAGE_TO_IMAGE_ENDPOINT=$I2I -e WAVESPEED_API_RESOURCE_MODE=url -- $exe
Write-Host "   (confira: claude mcp list  ->  'wavespeed' deve aparecer Connected)"

Write-Host ">> 3/3 Rodando a Etapa 5 (Seedream via WaveSpeed) no _teste-nanobanana..." -ForegroundColor Cyan
$env:ROTEIRO_ASPECT = "9:16"     # casa com os prompts verticais do P1 (troque p/ 16:9 se quiser)
py -3 "stages/s5_imagens.py" "_teste-nanobanana"

Write-Host ""
Write-Host ">> Pronto. Imagens em: projects/_teste-nanobanana/images/" -ForegroundColor Green
Write-Host "   Me avise que eu abro aqui e comparo qualidade/consistencia com o seedream do Magnific."
