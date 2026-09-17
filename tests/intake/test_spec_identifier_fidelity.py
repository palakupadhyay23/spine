"""NSS-1231: the identifiers a ticket names must survive the spec writer.

The extractor keeps them verbatim in ``Intent.description``/``scope``; the spec writer's
prompt asks the same of ``summary`` — and a real run ignored it, deleting the file the ticket
named in its first sentence. So the spec carries the intent's fields through unchanged, and a
deterministic backstop appends whatever the source named and the model dropped. Exercised
through ``SpecWriter.write`` with a scripted model, plus the pure helper on its own.
"""

from __future__ import annotations

import json
from typing import Any

from orchestrator.core.llm import CompletionResult, LLMClient, Message, ToolSpec
from orchestrator.intake.intents import Intent
from orchestrator.intake.specs import FeatureSpec, SpecWriter, _identifiers

_TICKET = (
    "Replace the current HTTP Basic Auth (`EBS_API_USERNAME`/`EBS_API_PASSWORD`, used in "
    "`EBSOrderApiClient.cs`) with OAuth2 client-credentials flow against the IDCS token endpoint: "
    "`https://idcs-example.identity.oraclecloud.com:443/oauth2/v1/token`. Implement token "
    "acquisition in the EBS/OIC API client. Attach the bearer token to the OIC order-submission call."
)
_PARAPHRASE = (
    "Implement OAuth2 client-credentials authentication for OIC integration to ensure secure, "
    "standards-based authentication when accessing OIC endpoints. This will allow the system to "
    "authenticate using client credentials, improving security and compliance for OIC interactions."
)


class _ScriptedLLM:
    def __init__(self, payload: dict[str, Any] | str) -> None:
        self._text = payload if isinstance(payload, str) else json.dumps(payload)

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str = "",
        temperature: float = 0.0,
        tools: list[ToolSpec] | None = None,
        tool_choice: str | None = None,
        **_: Any,
    ) -> CompletionResult:
        return CompletionResult(
            text=self._text,
            model=model or "fake",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            latency_ms=0.0,
        )


def _writer(payload: dict[str, Any] | str) -> SpecWriter:
    llm: LLMClient = _ScriptedLLM(payload)  # type: ignore[assignment]
    return SpecWriter(llm, model="fake-model")


def _intent() -> Intent:
    return Intent(
        id="intent-oauth2",
        title="Implement OAuth2 client-credentials auth",
        description=_TICKET,
        scope="EBS/OIC only",
    )


def test_feature_spec_carries_description_and_scope_defaulting_to_empty() -> None:
    spec = FeatureSpec(intent_id="i-1", title="T")
    assert spec.description == "" and spec.scope == ""
    assert {"description", "scope"} <= spec.model_dump().keys()


def test_identifiers_finds_what_the_ticket_names_and_not_its_prose() -> None:
    found = _identifiers(_TICKET)
    for expected in (
        "EBS_API_USERNAME",
        "EBS_API_PASSWORD",
        "EBSOrderApiClient.cs",
        "https://idcs-example.identity.oraclecloud.com:443/oauth2/v1/token",
    ):
        assert expected in found
    assert "EBSOrderApiClient" not in found  # a substring of the file it names — one fact, not two
    for word in ("Replace", "Basic", "Auth", "Implement", "HTTP", "OIC", "OAuth2"):
        assert word not in found


def test_identifiers_is_ordered_by_first_appearance_and_deterministic() -> None:
    text = "See `Zeta.cs` then ALPHA_KEY then BetaHelper then `Zeta.cs` again."
    assert _identifiers(text) == ["Zeta.cs", "ALPHA_KEY", "BetaHelper"]
    assert _identifiers(text) == _identifiers(text)


async def test_nss_1231_identifiers_the_model_dropped_are_carried_into_the_spec() -> None:
    """The measured failure: a generic summary, an empty technical_notes, every identifier gone."""
    spec = await _writer({"summary": _PARAPHRASE, "acceptance_criteria": []}).write(_intent())

    assert spec.description == _TICKET and spec.scope == "EBS/OIC only"
    assert spec.summary == _PARAPHRASE  # the model's prose is not rewritten, only supplemented
    assert "Identifiers the source names, carried verbatim:" in spec.technical_notes
    for expected in ("EBSOrderApiClient.cs", "EBS_API_USERNAME", "EBS_API_PASSWORD"):
        assert expected in spec.technical_notes


async def test_identifiers_the_model_kept_are_not_carried_a_second_time() -> None:
    kept = (
        "Swap Basic Auth in EBSOrderApiClient.cs for a token; retire EBS_API_USERNAME and EBS_API_PASSWORD."
    )
    spec = await _writer(
        {
            "summary": kept,
            "technical_notes": "Token endpoint: https://idcs-example.identity.oraclecloud.com:443/oauth2/v1/token",
        }
    ).write(_intent())

    assert "carried verbatim" not in spec.technical_notes
    assert spec.technical_notes.startswith("Token endpoint:")


async def test_unparseable_output_still_carries_description_and_scope() -> None:
    spec = await _writer("not json at all").write(_intent())
    assert spec.description == _TICKET and spec.scope == "EBS/OIC only"
    assert spec.summary == _TICKET  # the minimal spec already fell back to the description


async def test_the_carried_line_is_bounded_and_says_what_it_left_out() -> None:
    names = " ".join(f"`FILE_{i:02d}_KEY`" for i in range(30))
    intent = Intent(id="i", title="T", description=names)
    spec = await _writer({"summary": "nothing named", "acceptance_criteria": []}).write(intent)
    assert "(+6 more)" in spec.technical_notes
    assert spec.technical_notes.count("FILE_") == 24
