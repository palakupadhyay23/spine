"""Sanitized subprocess execution shared by test and preflight adapters."""

from __future__ import annotations

import asyncio
import os
from typing import Protocol


class ExecCapture(Protocol):
    async def __call__(self, argv: tuple[str, ...], *, cwd: str, timeout: float) -> tuple[int, str]: ...


# Env prefixes stripped before running the worktree's tests. Two reasons:
# (1) SECURITY — generated code must never see the orchestrator's live
# credentials; (2) CORRECTNESS — repo tests assert "unconfigured adapter"
# behavior, and inherited CONFLUENCE_/JIRA_ vars make adapters look
# configured (run #7's failure mode).
_SECRET_ENV_PREFIXES = (
    "ANTHROPIC_",
    "OPENAI_",
    "CONFLUENCE_",
    "JIRA_",
    "GITHUB_",
    "AWS_",
    "ORCHESTRATOR_",
    "SDLC_",
    "MINIO_",
    "TEMPORAL_",
)


async def exec_capture(argv: tuple[str, ...], *, cwd: str, timeout: float) -> tuple[int, str]:
    """Run ``argv`` (no shell), returning ``(returncode, combined_output)``."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(_SECRET_ENV_PREFIXES)}
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, f"timed out: {' '.join(argv)}"
    rc = proc.returncode if proc.returncode is not None else -1
    return rc, stdout_bytes.decode("utf-8", "replace")
