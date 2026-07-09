# Editor Auto / roteiro-auto

Esteira Python **headless** de produção de vídeos de romance (long-form + short-form) que substitui o app Romance Maker. Pega um card do ClickUp e vai até a entrega do vídeo montado.

> Memória durável da operação (arquitetura, decisões, aprendizados, IDs) vive fora deste repo, em `~/.claude/SEGUNDO-CEREBRO/`. Comece por lá.

## Fluxo (stages)

ClickUp → roteiro → personagens → narração (TTS) → prompts → imagens (Magnific) → capas → montagem → entrega.

Cada etapa é um módulo em `orchestrator/stages/` (`s1_clickup` … `s8_entrega`).

## Estrutura

| Pasta | O que é |
| --- | --- |
| `orchestrator/` | Núcleo da esteira: `pipeline.py`, `runner.py`, `config.py`, `stages/` (s1–s8), montagem/capas/TTS. |
| `panel/` | Painel (`app.py`). |
| `launcher/` | Launcher do executável (`launch.py`, `make_icon.py`). |
| `assets/` | Fontes das capas + ícone. |
| `materiais/` | Configs por canal (`<canal>/modelos.json`) + materiais de referência (mídia é git-ignored). |
| `projects/` | Pastas-projeto (um card = uma pasta). Só os artefatos de **texto** são versionados; renders/áudio/imagens ficam de fora. |

## Setup

1. Requisitos: **Windows**, Python via `py -3` (nunca `python`).
2. Copie a config e preencha os valores reais (o `.env` real é git-ignored):
   ```powershell
   Copy-Item editor-auto.env.example editor-auto.env
   ```
   Preencha ao menos `LONGFORM_CLICKUP_TOKEN` (token pessoal do ClickUp).
3. Rode a esteira pelo orchestrator (ver `orchestrator/editor-auto.py` / `runner.py`).

## O que NÃO está no Git

Segredos (`editor-auto.env`), o binário `Editor Auto.exe`, `__pycache__/`, toda mídia (`.mp4/.mp3/.png/.jpeg/...`) e as pastas de saída `ENTREGAS/` e `VIDEOS-PRONTOS/`. Ver `.gitignore`.
