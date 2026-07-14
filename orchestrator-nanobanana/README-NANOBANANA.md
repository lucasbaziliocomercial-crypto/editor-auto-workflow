# orchestrator-nanobanana — cópia para o comparativo Magnific × Nano Banana (grátis)

Esta pasta é uma **cópia da esteira `orchestrator/`** com **uma única mudança**: as **imagens do
corpo do vídeo (Etapa 5)** são geradas no **Nano Banana** (MCP grátis do Google Gemini) em vez do
**Magnific** (que cobra crédito por imagem via MCP).

Objetivo: rodar o **mesmo vídeo** nas duas esteiras e comparar **custo** e **qualidade/consistência**
lado a lado.

- **Versão OFICIAL (paga):** `orchestrator/` — Magnific, seedream-4, ~50 créd/img (~2.500/vídeo).
- **Versão GRÁTIS (esta):** `orchestrator-nanobanana/` — Nano Banana 2, R$0 dentro da cota grátis.

---

## O que MUDOU nesta cópia (e só isto)

| Arquivo | Mudança |
|---|---|
| `stages/nanobanana_seam.py` | **NOVO.** Backend do Nano Banana (espelha a interface do `magnific_seam` que a Etapa 5 usa: `modo`, `garantir_corpo_liberado`, `gerar`, `allowed_tools`). |
| `stages/s5_imagens.py` | Importa `nanobanana_seam as magnific_seam`; `_instrucao`/`_instrucao_regen` reescritas para a **API do Nano Banana** (síncrona, salva o PNG direto — sem upload→wait→download). |
| `config.py` | Novos defaults: `ROTEIRO_IMG_BACKEND=nanobanana`, `NANOBANANA_MCP=mcp__nanobanana`, `NANOBANANA_MODEL=nb2`. |

**O que NÃO mudou** (segue idêntico à esteira oficial): roteiro, narração, prompts, **QA visual das
imagens** (mesma lógica de detectar bug e regerar), capas de capítulo (Etapa 6 = vídeo Ken Burns
local, custo zero), montagem, entrega. A **abertura da P2** (image→video) continua no **Magnific**
(é vídeo, fora do escopo desta troca).

> **Por que só a Etapa 5?** Nesta esteira, a ÚNICA etapa que gasta crédito Magnific de imagem é a
> geração do corpo (Etapa 5). A "capa" (Etapa 6) é um vídeo montado localmente com ffmpeg, e a
> `thumb_ref.png` é **baixada do card** na Etapa 1 — nenhuma das duas custa crédito. Então migrar o
> corpo = migrar 100% do custo de imagem.

---

## ⚠️ ANTES do 1º lote — verifique 2 coisas

1. **MCP conectado + chave.** Conecte o MCP do Nano Banana (ex.: `zhongweili/nanobanana-mcp-server`)
   com o nome `mcp__nanobanana` e exporte a **`GEMINI_API_KEY`** — uma chave grátis do
   **Google AI Studio**, de uma conta **SEM billing** (assim o teto vira a cota grátis ~500 img/dia
   e **nunca** vira cobrança).
2. **Parâmetro de imagem de referência.** A doc do wrapper **não fixa** o nome do parâmetro que
   recebe as fotos de referência do casal. Rode **1 imagem de teste** e confirme, no schema real das
   tools `mcp__nanobanana__*`, como se passa a referência (provável: `reference_images` /
   `image_paths` / `input_images`, ou via `upload_file` + `edit_image`). A consistência do casal
   depende disso — a instrução já é adaptativa (manda o agente ler o schema), mas **confira o
   resultado do teste** antes de disparar as ~50 do lote.

---

## Como rodar o comparativo (A/B)

Use **o mesmo projeto** (mesmo `source.json`, mesmos prompts) nas duas esteiras, apontando cada uma
para uma pasta de saída diferente (ou copie o projeto). Rode **só a Etapa 5** em cada uma e compare:

```powershell
# 1) VERSÃO OFICIAL (Magnific) — precisa da trava de crédito liberada
$env:LONGFORM_MAGNIFIC_CORPO_OK = "1"
py -3 "orchestrator/editor-auto.py" "<projeto>" 5     # ajuste ao entrypoint real da sua esteira

# 2) VERSÃO GRÁTIS (Nano Banana) — precisa da chave grátis do Gemini
$env:GEMINI_API_KEY = "<sua-chave-google-ai-studio-sem-billing>"
py -3 "orchestrator-nanobanana/editor-auto.py" "<projeto>" 5
```

Depois compare: **custo** (a oficial some crédito Magnific; a grátis, R$0 dentro da cota) e
**qualidade** — abra `images/img_*.png` das duas e cheque rosto/cabelo/roupa do casal, anatomia e
ausência de texto queimado (o QA visual roda igual nas duas).

---

## Diferenças técnicas dos dois backends

| | Magnific (oficial) | Nano Banana (esta cópia) |
|---|---|---|
| Custo/img via MCP | ~50 créd (seedream-4) | R$0 até ~500 img/dia/chave |
| Fluxo por imagem | upload ref → generate (async) → `creations_wait` → `curl` download | **1 chamada síncrona** `generate_image` que já salva o PNG |
| Lock de personagem | `references[type=character]` (foto subida como creation) | fotos de referência passadas na geração (parâmetro a confirmar) |
| Rate limit | crédito é o limite | ~2–15 img/min (tier grátis) — a instrução re-tenta em 429 |
| Trava de segurança | `LONGFORM_MAGNIFIC_CORPO_OK=1` (evita queimar crédito) | `garantir_corpo_liberado()` só checa MCP+chave (é grátis) |

## Caveats honestos

- O tier grátis do Google **oscila** (já restringiu e restaurou em 2026) e tem termos de uso
  comercial a checar — não é um SLA como o Magnific.
- O MCP é **wrapper de terceiro** (1 dev). Mitigação: chave **sem billing** (risco financeiro = 0),
  fixar a versão do MCP, allowlist mínima. Alternativa mais durável: wrapper próprio da API oficial
  do Gemini, ou ComfyUI local (100% seu, sem cota).
- Esta cópia **não foi testada end-to-end** aqui (o MCP do Nano Banana não estava conectado na
  sessão que a criou). Os arquivos compilam; a validação real é o teste de 1 imagem acima.
