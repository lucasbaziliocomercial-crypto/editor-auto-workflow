# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **SEGUNDO CÉREBRO (leia primeiro):** a memória durável de toda a operação vive em `~/.claude/SEGUNDO-CEREBRO/` (INDEX + arquitetura + changelog + aprendizados + configs-ids). Leia o `INDEX.md` no início e **atualize-o em toda mudança** — protocolo completo em `~/.claude/CLAUDE.md`. Esta pasta hoje hospeda o build do **Editor Auto / roteiro-auto** (`orchestrator/`, `panel/`, `projects/`, `materiais/`) — a esteira Python que substitui o Romance Maker; ver `SEGUNDO-CEREBRO/arquitetura.md`. As seções abaixo descrevem o fluxo **novela-en** (short-form) que também roda a partir de pastas-projeto.

## What this folder is

This is a **working folder for the `novela-en` production workflow** — not an app and not source code. There is no build, lint, or test setup. It is the folder Claude runs *inside* to produce all the text artifacts (`.txt`) for one short romance "novela" video (romance / dark romance / milionário / máfia) aimed at a 3–5 min TikTok "vídeo isca" (hook video) plus in-app chapters.

The folder is normally **empty until a novela project is started**. Each novela lives in its own subfolder (e.g. `VÍDEO NN - NOME CURTO/`); the workflow always operates on **the current folder Claude is running in**, and every produced file lands there.

The creative logic is **not here**. It lives outside this folder:
- **Skill** — `~/.claude/skills/novela-en/SKILL.md` (the master flow, style rules, content triage).
- **Slash commands** — `~/.claude/commands/novela-en-*.md` and `~/.claude/commands/longform-*.md` (one per pipeline step).
- **Mechanical Python scripts** — `C:\Users\Lucas Bazilio\TINAGO AUTOMAÇÃO\` (transcription, SRT mapping, video/CapCut assembly). That repo has its own detailed `CLAUDE.md` — read it before changing any script.

When you need to change *how* an artifact is written, edit the relevant skill/command `.md`, not this folder.

## Two workflows

There are two parallel flows sharing the same folder-per-project model:

- **`novela-en`** (short-form, the main flow) — 3–5 min isca video, "isca-first" ordering: the video artifacts (Cap 01 → bible → hooks → SRT/audio → frames) are produced first; app chapters 02–06 are opt-in at the end. Driven by `novela-en-*` slash commands.
- **`longform-*`** — long-form video (~5,000-word single story, Alpha King/Werewolf "Helô Stories" style, or máfia/LENA variant). Driven by `longform-*` slash commands (roteiro → validar → humanizar-narracao → prompts-img → prompts_imagens).

Pick the flow from what the user asks; don't mix their commands.

## The `novela-en` pipeline (isca-first order)

Each step consumes the previous ones. **Fixed order after Cap 01 is BIBLE → HOOKS.**

```
1   Parte 01 base    /novela-en-parte01-traduzir  or  /novela-en-parte01-premissa
2   Suggestions      /novela-en-sugestoes          (5 outlines, presented in PT-BR)
3   Cap 01           /novela-en-iniciar-historia   or  /novela-en-validar-texto (user pasted it)
4   Seam audit       /novela-en-revisar-intersecao (optional)
3.4 Character bible  /novela-en-character-bible    -> character_bible.txt  (RUNS BEFORE HOOKS)
3.5 Hooks            /novela-en-prompts-hook       -> prompts_hook_manual.txt (0-15s, N=7-12)
6   SRT + audio      (no slash command — see below)
7   Mapping          /novela-en-mapeamento         -> mapeamento_capcut.txt
9   Ref photos       /novela-en-imagens-referencia
10  Frame prompts    /novela-en-prompts-video      -> prompts_frames_tiktok.txt
── PIPELINE AUTO-STOPS HERE ──
10.5 Viral gate      /novela-en-avaliar-viral      (opt-in, pre-render, free)
11  Publishing       /novela-en-publicacao         (OPT-IN only)
5   App chapters     /novela-en-proximo-capitulo   (OPT-IN loop, Caps 02-06)
```

**Critical gates and rules (from the skill — enforce them):**
- **Cap 01 = 5-minute gate.** Sweet spot 3,800–4,500 chars; hard cap **5,000** (~5 min at ~155 WPM EN-US). Over cap → trim from the middle, keep opening + cliffhanger. Under 2,800 → warn the user. Count chars and report.
- **BIBLE before HOOKS**, always. Hooks consume the bible's visual DNA; generating hooks first is an anti-pattern.
- **Hooks have a double validation gate.** (1) Claude auto-evaluates each hook against the command's checklist (G1–G11, mirroring the 7 Virality Predictor dimensions) and only writes the file after declaring `=== HOOKS APROVADOS ===`. (2) Then **wait for the human to say "validado"** before moving to audio. This is the make-or-break TikTok gate.
- **The pipeline auto-stops after step 10** (`/novela-en-prompts-video`). Never fire step 11 (publicação) or step 5 (próximo-capitulo) on your own — announce the isca is done and list those two as options.
- **Pause and confirm** at the end of every big step (Parte 01, Cap 01, each chapter, mapping, bible, each 30-prompt batch).
- **Content triage FIRST** on any writing/translation step; ask for OK in PT-BR before producing. See the platform-safe substitution table in `SKILL.md`.
- **Auto-detect by audio on entry:** if the folder contains `tiktok.mp3` **and no** `cap_01.txt`, auto-transcribe and reconstruct `cap_01.txt`, then resume from step 7. If `cap_01.txt` already exists, don't touch audio.

## Step 6 (SRT + audio) — no slash command, SRT-first

1. Paste `cap_01.txt` into the internal SRT site, set ~40–60 chars/block, save as `tiktok.srt`.
2. In CapCut, import `tiktok.srt`, pick a dramatic EN-US voice matching the narrator's gender, export as `tiktok.mp3`.
3. **Re-sync recommended:** the site's timestamps are estimates. Once you have `tiktok.mp3`, regenerate `tiktok.srt` with real timestamps via Whisper:
   ```powershell
   py -3 "C:\Users\Lucas Bazilio\TINAGO AUTOMAÇÃO\gerar-srt-en.py" "<novela folder>\tiktok.mp3"
   ```
   Always use the **`py -3`** launcher, never `python`. Fallback prereq: `pip install faster-whisper`.

## File conventions

- All **production files are plain `.txt`** — no markdown, no headers, no emojis (they feed a TTS/voice generator directly). The `.md` command/skill files are the only markdown.
- Standard names in the working folder: `cap_01.txt`…`cap_06.txt`, `historia_completa.txt`, `tiktok.srt`, `tiktok.mp3`, `mapeamento_capcut.txt`, `character_bible.txt`, `prompts_hook_manual.txt`, `prompts_frames_tiktok.txt`, `description_tiktok.txt`, `thumbnail_plataforma.txt`, `summary_tiktok.txt`.
- **Voice markers are the one exception** to "plain text" — Caps 02–06 (app) may carry them (the app strips them before TTS): 1st person uses a `✦ NOME` line per POV block; 3rd person marks only male dialogue with `[M]…[/M]`. Never mix the two formats in one script. Without markers, Part 2 renders in a single (female) voice.
- `prompts_hook_manual.txt` **must start with** `# N_HOOKS=<n>` — downstream scripts parse it.
- `mapeamento_capcut.txt` is the timing source-of-truth. **No take may exceed 8s.** Format is `NNN | HH:MM:SS.mmm -> HH:MM:SS.mmm | D.Ds` then `VOZ <ref> | <literal line>` / `IMG <ref> | <visual>`, one blank line between takes; a `=== TABELA DE CAPITULOS ===` block is appended at the end.

## Writing style (all text steps)

- **First person**, American English, natural and colloquial (contractions, idioms) — never British, never formal, never poetic/purple prose or philosophical asides. Narrator gender is flexible.
- Every story is a **romance** with a clear romantic pair driving the central conflict; ending = poetic justice + romantic recognition, not dry revenge. Sensual tension required; explicit sex forbidden (platform-safe).
- **Never** use financial-fraud or public-exposure as the central twist mechanism (the audience rejects it).
- **Communicate with the user in PT-BR**; the produced artifacts are in English. The only exception noted in the flow: the 5 outlines in step 2 are presented in PT-BR.

## Mechanical scripts (in `TINAGO AUTOMAÇÃO`, run with `py -3`)

You rarely run these from this folder, but the pipeline depends on them:

```powershell
py -3 gerar-tudo.py "PROJECT FOLDER"                 # one-click GUI orchestrator: audio+photos -> all artifacts
py -3 novela_orquestra.py "PROJECT FOLDER" [steps]   # same pipeline, headless
py -3 gerar-srt-en.py "PROJECT/tiktok.mp3"           # EN transcription (Whisper)
py -3 mapear-srt.py "PROJECT/tiktok.srt" --dynamic-first-minute --voz-img   # SRT -> takes
py -3 montar-video.py "PROJECT FOLDER"               # assembly: pairs clips to takes -> CapCut project or MP4
```

The orchestrator invokes the creative skills headless via `claude -p` (subscription login, not the paid API). See `TINAGO AUTOMAÇÃO\CLAUDE.md` for orchestrator/assembly/GPU internals.
