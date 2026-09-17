"""Block B.1 unit tests: Jira source adapter (read side) + URI/factory wiring.

The adapter is driven against an httpx MockTransport that routes the Jira v3
``/issue/{key}`` and ``/search`` endpoints. No network.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from orchestrator.intake.jira import IssueTrackerError, JiraConfig
from orchestrator.intake.jira_source import JiraSourceAdapter, _adf_to_text, _description_text


def _config() -> JiraConfig:
    return JiraConfig(base_url="https://acme.atlassian.net", email="bot@acme.io", api_token="tok")


_ADF = {
    "type": "doc",
    "version": 1,
    "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Crashes on login."}]},
        {
            "type": "bulletList",
            "content": [
                {
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "repro step"}]}],
                }
            ],
        },
    ],
}


def _fields(summary: str, *, itype: str = "Story", status: str = "To Do", desc: Any = None) -> dict[str, Any]:
    return {
        "summary": summary,
        "issuetype": {"name": itype},
        "status": {"name": status},
        "priority": {"name": "High"},
        "labels": ["backend"],
        "description": desc,
    }


class _JiraMock:
    def __init__(
        self,
        issues: dict[str, dict[str, Any]],
        *,
        children: dict[str, list[str]] | None = None,
        project_issues: dict[str, list[str]] | None = None,
        total: int | None = None,
    ) -> None:
        self.issues = issues
        self.children = children or {}
        self.project_issues = project_issues or {}
        self.total = total
        self.searched: list[str] = []

    def _payload(self, key: str) -> dict[str, Any]:
        return {"id": key, "key": key, "fields": self.issues.get(key, {})}

    def _resolve_jql(self, jql: str) -> list[str]:
        jql = jql.strip()
        if jql.startswith("parent ="):
            return self.children.get(jql.split("=", 1)[1].strip(), [])
        if jql.startswith("project ="):
            return self.project_issues.get(jql.split("=", 1)[1].split()[0].strip(), [])
        return list(self.issues.keys())  # raw-jql fallback

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # `/search/jql`, not `/search`: Atlassian removed the latter (CHANGE-2046) and it
        # now answers 410 Gone. The replacement returns no `total` — truncation is signalled
        # by `isLast`/`nextPageToken` instead.
        if path.endswith("/search/jql"):
            jql = request.url.params.get("jql", "")
            self.searched.append(jql)
            keys = self._resolve_jql(jql)
            issues = [self._payload(k) for k in keys]
            body: dict[str, object] = {"issues": issues, "isLast": True}
            if self.total is not None and self.total > len(issues):
                body["isLast"] = False
            return httpx.Response(200, json=body)
        if "/issue/" in path:
            key = path.split("/issue/")[1]
            if key not in self.issues:
                return httpx.Response(404, json={"errorMessages": ["not found"]})
            return httpx.Response(200, json=self._payload(key))
        return httpx.Response(404, json={})


def _adapter(mock: _JiraMock) -> tuple[JiraSourceAdapter, httpx.AsyncClient]:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(mock.handler), base_url="https://acme.atlassian.net"
    )
    return JiraSourceAdapter(_config(), http_client=http), http


# ---- ADF → text ----------------------------------------------------------
def test_adf_to_text_flattens_paragraphs_and_lists() -> None:
    text = _adf_to_text(_ADF)
    assert "Crashes on login." in text
    assert "- repro step" in text


def test_description_text_handles_plain_and_none() -> None:
    assert _description_text("just text") == "just text"
    assert _description_text(None) == ""
    assert "Crashes on login." in _description_text(_ADF)


# ---- the thread, the links, the attachments ------------------------------


def test_comments_are_newest_first_and_say_what_was_left_out() -> None:
    """The decision is usually the last word, not the first.

    A real ticket reads "send list of columns or enable filter on all columns"; which
    columns, for which users, is settled three comments down. Pulling the description alone
    produced specs that restated a summary.
    """
    from orchestrator.intake.jira_source import _comments_text

    comments = [
        {"author": {"displayName": f"P{i}"}, "created": f"2026-09-{i + 1:02d}T10:00:00", "body": f"c{i}"}
        for i in range(14)
    ]
    text = _comments_text({"comment": {"total": 23, "comments": comments}})
    assert text.startswith("Comments (10 most recent of 23):")
    assert "P13" in text  # newest kept
    assert "P0" not in text  # oldest elided, and the count says so


def test_a_long_comment_is_marked_where_it_was_cut() -> None:
    """A comment stopping mid-sentence with no marker reads as a comment that ended there."""
    from orchestrator.intake.jira_source import _comments_text

    long = "x" * 3000
    text = _comments_text({"comment": {"total": 1, "comments": [{"body": long}]}})
    assert "…[truncated, 3000 chars]" in text


def test_links_carry_the_sideways_relations_the_child_walk_never_reaches() -> None:
    """`fetch_tree` follows `parent`. Blocks/relates-to is the half it cannot see."""
    from orchestrator.intake.jira_source import _links_text

    text = _links_text(
        {
            "parent": {"key": "NSS-900", "fields": {"summary": "Action tracker"}},
            "issuelinks": [
                {
                    "type": {"outward": "blocks"},
                    "outwardIssue": {"key": "NSS-1300", "fields": {"summary": "Grid rollout"}},
                }
            ],
        }
    )
    assert "- parent NSS-900 — Action tracker" in text
    assert "- blocks NSS-1300 — Grid rollout" in text


def test_attachments_are_named_but_never_claimed_to_be_read() -> None:
    """Silence reads as "nothing was attached"; a filename says where to look."""
    from orchestrator.intake.jira_source import _attachment_names

    text = _attachment_names({"attachment": [{"filename": "columns.xlsx"}]})
    assert "columns.xlsx" in text
    assert "contents not read" in text


def test_a_bare_ticket_gains_no_empty_sections() -> None:
    """A ticket with no thread must read exactly as it did before this change."""
    from orchestrator.intake.jira_source import _attachment_names, _comments_text, _links_text

    assert _comments_text({}) == "" and _links_text({}) == "" and _attachment_names({}) == ""


# ---- fetch_document -------------------------------------------------------
async def test_fetch_document_maps_issue_to_document() -> None:
    mock = _JiraMock({"PROJ-1": _fields("Login fails", itype="Bug", desc=_ADF)})
    adapter, http = _adapter(mock)
    try:
        doc = await adapter.fetch_document("PROJ-1")
    finally:
        await http.aclose()
    assert doc.id == "PROJ-1" and doc.title == "Login fails"
    assert doc.space == "PROJ" and doc.labels == ("backend",)
    assert doc.url == "https://acme.atlassian.net/browse/PROJ-1"
    # metadata header + description prose both land in the body
    assert "Bug" in doc.body and "status: To Do" in doc.body
    assert "Crashes on login." in doc.body and "- repro step" in doc.body
    # …and the type also travels as data. The header is prose for the extractor; only the
    # field can select a workflow profile or decide whether the ticket must localize.
    assert doc.issue_type == "Bug"


async def test_the_meta_header_and_the_issue_type_field_cannot_disagree() -> None:
    """Two answers to "what type is this?" would be a defect visible from neither alone."""
    mock = _JiraMock({"PROJ-1": _fields("Login fails", itype="New Feature")})
    adapter, http = _adapter(mock)
    try:
        doc = await adapter.fetch_document("PROJ-1")
    finally:
        await http.aclose()
    assert doc.issue_type == "New Feature"
    assert doc.body.startswith("New Feature"), "same value, one helper, both consumers"


async def test_an_untyped_issue_carries_an_empty_type_rather_than_a_guess() -> None:
    mock = _JiraMock({"PROJ-1": _fields("Login fails", itype="")})
    adapter, http = _adapter(mock)
    try:
        doc = await adapter.fetch_document("PROJ-1")
    finally:
        await http.aclose()
    assert doc.issue_type == ""


# ---- children + tree walk -------------------------------------------------
async def test_list_children_uses_parent_jql() -> None:
    mock = _JiraMock(
        {"PROJ-2": _fields("Sub A"), "PROJ-3": _fields("Sub B")},
        children={"PROJ-1": ["PROJ-2", "PROJ-3"]},
    )
    adapter, http = _adapter(mock)
    try:
        refs = await adapter.list_children("PROJ-1")
    finally:
        await http.aclose()
    assert [r.id for r in refs] == ["PROJ-2", "PROJ-3"]
    assert all(r.kind == "issue" for r in refs)
    assert mock.searched == ["parent = PROJ-1"]


async def test_fetch_tree_issue_root_walks_children() -> None:
    mock = _JiraMock(
        {"PROJ-1": _fields("Epic"), "PROJ-2": _fields("Sub A"), "PROJ-3": _fields("Sub B")},
        children={"PROJ-1": ["PROJ-2", "PROJ-3"]},
    )
    adapter, http = _adapter(mock)
    try:
        result = await adapter.fetch_tree("PROJ-1")
    finally:
        await http.aclose()
    assert [d.id for d in result.documents] == ["PROJ-1", "PROJ-2", "PROJ-3"]
    assert result.truncated is False


async def test_fetch_tree_issue_root_raises_when_root_missing() -> None:
    adapter, http = _adapter(_JiraMock({}))
    try:
        with pytest.raises(IssueTrackerError):
            await adapter.fetch_tree("PROJ-404")
    finally:
        await http.aclose()


async def test_fetch_tree_project_root_runs_search() -> None:
    mock = _JiraMock(
        {"ENG-1": _fields("One"), "ENG-2": _fields("Two")},
        project_issues={"ENG": ["ENG-1", "ENG-2"]},
    )
    adapter, http = _adapter(mock)
    try:
        result = await adapter.fetch_tree("ENG")
    finally:
        await http.aclose()
    assert [d.id for d in result.documents] == ["ENG-1", "ENG-2"]
    assert mock.searched == ["project = ENG ORDER BY created"]


async def test_fetch_tree_project_root_marks_truncation() -> None:
    mock = _JiraMock(
        {"ENG-1": _fields("One"), "ENG-2": _fields("Two"), "ENG-3": _fields("Three")},
        project_issues={"ENG": ["ENG-1", "ENG-2", "ENG-3"]},
    )
    adapter, http = _adapter(mock)
    try:
        result = await adapter.fetch_tree("ENG", max_docs=2)
    finally:
        await http.aclose()
    assert len(result.documents) == 2 and result.truncated is True


async def test_fetch_tree_jql_root() -> None:
    mock = _JiraMock({"ENG-9": _fields("Only")})
    adapter, http = _adapter(mock)
    try:
        result = await adapter.fetch_tree("jql/status = Done")
    finally:
        await http.aclose()
    assert [d.id for d in result.documents] == ["ENG-9"]
    assert mock.searched == ["status = Done"]


async def test_unconfigured_adapter_raises() -> None:
    # The shared fixture clears process credentials; also disable the local .env.
    adapter = JiraSourceAdapter(JiraConfig(_env_file=None))  # type: ignore[call-arg]
    with pytest.raises(IssueTrackerError):
        await adapter.fetch_document("PROJ-1")


# ---- URI + factory wiring -------------------------------------------------
def test_parse_source_uri_variants() -> None:
    from orchestrator.intake.service import SourceUriError, parse_source_uri

    assert parse_source_uri("jira://PROJ-123") == ("jira", "PROJ-123")
    assert parse_source_uri("jira://ENG") == ("jira", "ENG")
    assert parse_source_uri("jira://jql/project = ENG") == ("jira", "jql/project = ENG")
    with pytest.raises(SourceUriError):
        parse_source_uri("jira://")


def test_jira_is_a_supported_source_kind() -> None:
    from orchestrator.intake.factory import SUPPORTED_SOURCE_KINDS

    assert "jira" in SUPPORTED_SOURCE_KINDS


def test_jira_builder_unconfigured_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.intake import factory

    monkeypatch.setattr(factory, "JiraConfig", lambda: JiraConfig(base_url="", email="", api_token=""))
    with pytest.raises(factory.IntakeNotConfiguredError, match="Jira source not configured"):
        factory.build_jira_service(dry_run=True)
