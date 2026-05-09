import json
import logging
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from io import StringIO
from logging import Logger
from pathlib import Path
from typing import Any

from .statistics import Statistics
from .types import Message, MessageRole


def _parse_json_payload(payload: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        start = payload.find("{")
        end = payload.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(payload[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _split_system_user(messages: list[Message]) -> tuple[str, str]:
    system_buf = StringIO()
    user_buf = StringIO()
    for msg in messages:
        target = system_buf if msg.role == MessageRole.SYSTEM else user_buf
        if target.tell() > 0:
            target.write("\n\n")
        target.write(msg.message)
    return system_buf.getvalue(), user_buf.getvalue()


def _build_prompt(system_prompt: str, user_prompt: str) -> str:
    body = ""
    if system_prompt:
        body += system_prompt + "\n\n"
    body += user_prompt
    return body


def _log_request(logger: Logger | None, parameters: dict[str, Any], prompt: str) -> None:
    if logger is None:
        return
    parts = [f"\t\n{key}={value}" for key, value in parameters.items()]
    logger.debug(f"[[Parameters]]:{''.join(parts)}\n")
    logger.debug(f"[[Request]]:\n{prompt}\n")


def _log_response(logger: Logger | None, response: str) -> None:
    if logger is None:
        return
    logger.debug(f"[[Response]]:\n{response}\n")


class _BaseCLIExecutor:
    """Common machinery for subprocess-based CLI executors."""

    name: str = "cli"
    max_empty_retries: int = 3

    def __init__(
        self,
        model: str,
        timeout: float | None,
        retry_times: int,
        retry_interval_seconds: float,
        create_logger: Callable[[], Logger | None],
        statistics: Statistics,
    ) -> None:
        self._model_name: str = model
        self._timeout: float | None = timeout
        self._retry_times: int = retry_times
        self._retry_interval_seconds: float = retry_interval_seconds
        self._create_logger = create_logger
        self._statistics = statistics

    def request(
        self,
        messages: list[Message],
        max_tokens: int | None,
        temperature: float | None,
        top_p: float | None,
        cache_key: str | None,
    ) -> str:
        _ = max_tokens, temperature, top_p
        logger = self._create_logger()
        system_prompt, user_prompt = _split_system_user(messages)
        prompt = _build_prompt(system_prompt, user_prompt)

        params: dict[str, Any] = {"backend": self.name, "model": self._model_name}
        if cache_key is not None:
            params["cache_key"] = cache_key
        _log_request(logger, params, prompt)

        last_error = ""
        for attempt in range(1, max(self.max_empty_retries, 1) + 1):
            try:
                stdout, stderr = self._invoke_cli(prompt)
            except subprocess.TimeoutExpired as exc:
                last_error = f"{self.name} CLI timed out: {exc} (attempt {attempt}/{self.max_empty_retries})"
                if logger is not None:
                    logger.warning(last_error)
                self._sleep_for_retry(attempt)
                continue
            except FileNotFoundError as exc:
                raise RuntimeError(f"{self.name} CLI not found: {exc}") from exc

            translation = self._extract_translation(stdout, stderr)
            if translation:
                _log_response(logger, translation)
                return translation

            last_error = (
                f"{self.name} CLI returned an empty translation "
                f"(attempt {attempt}/{self.max_empty_retries}). "
                f"stdout: {stdout[-400:]} stderr: {stderr[-400:]}"
            )
            if logger is not None:
                logger.warning(last_error)
            self._sleep_for_retry(attempt)

        raise RuntimeError(last_error or f"{self.name} CLI returned an empty translation.")

    def _sleep_for_retry(self, attempt: int) -> None:
        if attempt >= self.max_empty_retries:
            return
        if self._retry_interval_seconds > 0.0:
            time.sleep(self._retry_interval_seconds)

    def _invoke_cli(self, prompt: str) -> tuple[str, str]:
        raise NotImplementedError

    def _extract_translation(self, stdout: str, stderr: str) -> str:
        raise NotImplementedError

    @staticmethod
    def _resolve_command(name: str, hint: str) -> str:
        path = shutil.which(name)
        if path is None:
            raise RuntimeError(
                f"{name!r} CLI was not found in PATH. {hint}"
            )
        return path


class CodexCLIExecutor(_BaseCLIExecutor):
    name = "codex"

    def __init__(
        self,
        model: str,
        timeout: float | None,
        retry_times: int,
        retry_interval_seconds: float,
        create_logger: Callable[[], Logger | None],
        statistics: Statistics,
        reasoning_effort: str | None = None,
    ) -> None:
        super().__init__(
            model=model,
            timeout=timeout,
            retry_times=retry_times,
            retry_interval_seconds=retry_interval_seconds,
            create_logger=create_logger,
            statistics=statistics,
        )
        self._reasoning_effort = reasoning_effort
        self._codex_command = self._resolve_command(
            "codex", "Install Codex CLI and run `codex login` first."
        )

    def _invoke_cli(self, prompt: str) -> tuple[str, str]:
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as handle:
            output_path = Path(handle.name)

        cmd = [
            self._codex_command,
            "exec",
            "-s",
            "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--color",
            "never",
            "-o",
            str(output_path),
            "-",
        ]
        if self._model_name:
            cmd[2:2] = ["-m", self._model_name]
        if self._reasoning_effort:
            cmd[2:2] = ["-c", f'model_reasoning_effort="{self._reasoning_effort}"']

        try:
            output_path.write_text("", encoding="utf-8")
            completed = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                capture_output=True,
                check=False,
                timeout=self._timeout,
            )
            stdout = (completed.stdout or "").strip()
            stderr = (completed.stderr or "").strip()
            if completed.returncode != 0:
                raise RuntimeError(
                    "Codex CLI translation failed. Ensure `codex login` works in your shell. "
                    f"Exit code: {completed.returncode}. Details: {stderr[-1200:]}"
                )
            raw_payload = output_path.read_text(encoding="utf-8", errors="ignore").strip()
            return raw_payload or stdout, stderr
        finally:
            output_path.unlink(missing_ok=True)

    def _extract_translation(self, stdout: str, stderr: str) -> str:
        _ = stderr
        return stdout.strip()


class ClaudeCodeCLIExecutor(_BaseCLIExecutor):
    name = "claude"

    def __init__(
        self,
        model: str,
        timeout: float | None,
        retry_times: int,
        retry_interval_seconds: float,
        create_logger: Callable[[], Logger | None],
        statistics: Statistics,
    ) -> None:
        super().__init__(
            model=model,
            timeout=timeout,
            retry_times=retry_times,
            retry_interval_seconds=retry_interval_seconds,
            create_logger=create_logger,
            statistics=statistics,
        )
        self._claude_command = self._resolve_command(
            "claude",
            "Install Claude Code (https://docs.anthropic.com/en/docs/claude-code) first.",
        )

    def _invoke_cli(self, prompt: str) -> tuple[str, str]:
        cmd = [
            self._claude_command,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--max-turns",
            "1",
        ]
        if self._model_name:
            cmd.extend(["--model", self._model_name])
        completed = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            timeout=self._timeout,
        )
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        if completed.returncode != 0:
            raise RuntimeError(
                "Claude Code translation failed. "
                f"Exit code: {completed.returncode}. Details: {stderr[-1200:]}"
            )
        return stdout, stderr

    def _extract_translation(self, stdout: str, stderr: str) -> str:
        _ = stderr
        if not stdout:
            return ""
        envelope = _parse_json_payload(stdout)
        if not envelope:
            return ""
        if envelope.get("is_error"):
            return ""
        return str(envelope.get("result", "")).strip()


class GeminiCLIExecutor(_BaseCLIExecutor):
    name = "gemini"

    _log = logging.getLogger("epub_translator.gemini")

    def __init__(
        self,
        model: str,
        timeout: float | None,
        retry_times: int,
        retry_interval_seconds: float,
        create_logger: Callable[[], Logger | None],
        statistics: Statistics,
    ) -> None:
        super().__init__(
            model=model,
            timeout=timeout,
            retry_times=retry_times,
            retry_interval_seconds=retry_interval_seconds,
            create_logger=create_logger,
            statistics=statistics,
        )
        self.max_empty_retries = max(retry_times, 1)
        self._gemini_command = self._resolve_command(
            "gemini",
            "Install Gemini CLI (https://github.com/google-gemini/gemini-cli) first.",
        )

    def _invoke_cli(self, prompt: str) -> tuple[str, str]:
        cmd = [self._gemini_command, "-p", "", "-o", "json"]
        if self._model_name:
            cmd.extend(["-m", self._model_name])
        completed = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            check=False,
            timeout=self._timeout,
        )
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        if completed.returncode != 0:
            raise RuntimeError(
                f"Gemini CLI failed (exit {completed.returncode}): {stderr[-800:]}"
            )
        return stdout, stderr

    def _extract_translation(self, stdout: str, stderr: str) -> str:
        _ = stderr
        if not stdout:
            return ""
        envelope = _parse_json_payload(stdout)
        if not envelope:
            return ""
        return str(envelope.get("response", "")).strip()


def build_cli_executor(
    kind: str,
    model: str,
    timeout: float | None,
    retry_times: int,
    retry_interval_seconds: float,
    create_logger: Callable[[], Logger | None],
    statistics: Statistics,
    reasoning_effort: str | None = None,
) -> _BaseCLIExecutor:
    common = {
        "model": model,
        "timeout": timeout,
        "retry_times": retry_times,
        "retry_interval_seconds": retry_interval_seconds,
        "create_logger": create_logger,
        "statistics": statistics,
    }
    if kind == "codex":
        return CodexCLIExecutor(reasoning_effort=reasoning_effort, **common)
    if kind == "claude":
        return ClaudeCodeCLIExecutor(**common)
    if kind == "gemini":
        return GeminiCLIExecutor(**common)
    raise ValueError(f"Unknown CLI backend kind: {kind!r}")
