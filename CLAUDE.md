# Project: epub-translator (TranDatDT fork)

This is a **personal fork** of [oomol-lab/epub-translator](https://github.com/oomol-lab/epub-translator) that adds CLI-backend support (codex / claude / gemini) on top of the original HTTP API backend. The HTTP API path is unchanged from upstream — fork value is concentrated in the CLI executor and the `format.json` `options` block.

Read [README.md](./README.md) for the user-facing pitch. This file is for AI assistants editing the code.

## Layout

```
epub_translator/
  llm/
    core.py            # LLM facade — builds executor based on `kind`
    executor.py        # OpenAI HTTP backend (UNCHANGED FROM UPSTREAM)
    cli_executor.py    # Fork addition: Codex/Claude/Gemini CLI subprocess backends
    context.py         # Caching + temperature/top_p increment around executor.request()
    statistics.py, types.py, error.py, increasable.py
  translation/         # EPUB-level translate() entry point (UNCHANGED)
  xml_translator/      # Per-chapter XML translation + filling (UNCHANGED)
  epub/, xml/, segment/, serial/, data/, template.py  # Internals (UNCHANGED)

scripts/
  translate_epub.py    # CLI entry: reads format.json, runs translate()
  translate_xml.py, translate_challenge.py, check_duplicate_ids.py
  utils.py             # Fork addition: parses `options` block, returns TranslateOptions

format.template.json      # Default template (HTTP API mode)
format.template.cli.json  # Fork addition: CLI-mode template
format.json               # User-local config (gitignored)
```

## Key contracts

- `LLMExecutor.request(messages, max_tokens, temperature, top_p, cache_key) -> str` — the contract `LLMContext` calls. CLI executors implement the same signature; ignore `temperature`/`top_p`/`max_tokens`.
- `LLM.__init__(kind=...)` dispatches via `_build_executor`. Valid kinds: `"openai"` (default), `"codex"`, `"claude"`, `"gemini"`. Unknown kind → `ValueError`. Missing CLI binary → `RuntimeError` at construction time.
- `format.json` schema: top-level LLM kwargs + nested `options` (script-level: `submit`/`concurrency`/`user_prompt`) + nested `translation`/`fill` (per-LLM kwargs merged into the LLM constructor).
- `submit` aliases accepted: `"replace"`, `"append_block"`, `"append-block"`, `"append_text"`, `"append-text"` — see `scripts/utils.py:_SUBMIT_ALIASES`.

## When editing

- **Don't break the OpenAI path.** The HTTP backend is upstream's surface area; if you touch `LLMExecutor` or `LLMContext`, run the full test suite (221 tests) before committing.
- **CLI executors send the prompt verbatim.** No JSON envelope, no instruction wrapping. The translator's existing prompts (`translate.jinja`, `fill.jinja`) already specify their own output format (plain text or ```xml```...```). Adding wrapping breaks `fill.jinja` because it contradicts the template's `<xml>...</xml>` instruction.
- **CLI executors don't expose token usage.** `Statistics` stays at zero for CLI mode — that's expected, don't try to fake it.
- **Cache key includes the rendered prompt** (`LLMContext._compute_messages_hash`), so changing `user_prompt` invalidates all cache. Changing `submit` mode does NOT — submit only affects post-translate XML assembly, not the LLM call.
- Keep upstream files (`epub/`, `xml/`, `xml_translator/`, `translation/`, `segment/`, `serial/`, `data/`, `template.py`, `executor.py`) free of fork-specific changes when possible — easier to merge upstream updates.
- **MIT License compliance:** preserve the original `Copyright (c) 2025 OOMOL Lab` line. Adding your own copyright alongside is fine; replacing or removing is not.

## Running

```bash
# Setup (once)
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Tests (full suite, ~1s)
pytest tests/ -x -q

# Translate
python scripts/translate_epub.py path/to/book.epub -l Vietnamese
```

Output: `temp/translated.epub`. Cache: `cache/`. Logs: `temp/logs/`. Note `temp/` is wiped on each run; `cache/` is preserved (resume support).

## Common pitfalls

- The user is on macOS (darwin) with `/tmp/eptv` venv created during initial development — that path may not exist on a fresh clone, always use `.venv` instead.
- `format.json` is gitignored — never assume its content; read it if needed.
- The progress bar in tqdm buffers when stdout is captured; `cache/` file count is a more reliable indicator of actual progress.
- gpt-5.4-mini / claude-sonnet-4-5 / gemini-2.5-pro etc. are the canonical model names as of 2026-05; if a name fails, check the CLI's own `--list-models` (or equivalent) before assuming a code bug.