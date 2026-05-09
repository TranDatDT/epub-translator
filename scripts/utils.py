import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from epub_translator import LLM, SubmitKind


_SUBMIT_ALIASES: dict[str, SubmitKind] = {
    "replace": SubmitKind.REPLACE,
    "append_text": SubmitKind.APPEND_TEXT,
    "append-text": SubmitKind.APPEND_TEXT,
    "append_block": SubmitKind.APPEND_BLOCK,
    "append-block": SubmitKind.APPEND_BLOCK,
}


@dataclass
class TranslateOptions:
    submit: SubmitKind = SubmitKind.APPEND_BLOCK
    concurrency: int = 4
    user_prompt: str | None = None
    extra: dict = field(default_factory=dict)


def load_llm(**args) -> tuple[LLM, LLM, TranslateOptions]:
    config = read_format_json()
    options = _pop_translate_options(config)
    translation_config = config.pop("translation", {})
    fill_config = config.pop("fill", {})
    translate_llm = LLM(
        **config,
        **translation_config,
        **args,
    )
    fill_llm = LLM(
        **config,
        **fill_config,
        **args,
    )
    return translate_llm, fill_llm, options


def _pop_translate_options(config: dict) -> TranslateOptions:
    raw = config.pop("options", {}) or {}
    submit_raw = raw.pop("submit", config.pop("submit", "append_block"))
    submit = _parse_submit(submit_raw)
    concurrency = int(raw.pop("concurrency", config.pop("concurrency", 4)))
    user_prompt = raw.pop("user_prompt", config.pop("user_prompt", None))
    return TranslateOptions(
        submit=submit,
        concurrency=concurrency,
        user_prompt=user_prompt,
        extra=raw,
    )


def _parse_submit(value) -> SubmitKind:
    if isinstance(value, SubmitKind):
        return value
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _SUBMIT_ALIASES:
            return _SUBMIT_ALIASES[key]
    raise ValueError(
        f"Invalid submit mode {value!r}. Expected one of: "
        + ", ".join(sorted(set(_SUBMIT_ALIASES)))
    )


def read_format_json() -> dict:
    path = Path(__file__).parent / ".." / "format.json"
    path = path.resolve()
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def read_and_clean_temp() -> Path:
    temp_path = Path(__file__).parent / ".." / "temp"
    shutil.rmtree(temp_path, ignore_errors=True)
    temp_path.mkdir(parents=True, exist_ok=True)
    return temp_path.resolve()
