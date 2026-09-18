"""Character listing on the migrated host, where labs ``projectInitialData`` is retired.

Measured 2026-09-18: ``flow.projectInitialData`` answers 404 "Flow RPCs have been
deprecated and disabled", which broke ``gflow character list`` AND every prompt
``@mention`` of a character (the mention index reads the same route → exit 29). The
flow.google.com app loads the same facts on project open, over batchexecute rpcid
``Zzl0ze``; its payload holds one row per character:

    [project_id, entity_id, null, [1, name, [[[wf]...], [[wf]...], personality], true],
     thumbnail_media_id, [w, h], created, updated]
"""

from __future__ import annotations

from typing import Any

import pytest

from gflow_cli.api.character import Character, parse_migrated_characters
from gflow_cli.api.client import FlowApiClient
from gflow_cli.errors import WireFormatError

PID = "39ba2135-4f80-461b-bc58-bb96af59d294"
EID = "42a8618b-c8c2-4cbc-8c50-193d68690fbd"
FACE = "4ce98279-2dc8-4fe0-99d8-8645050023fe"
FACE2 = "6bda9116-d2f8-4f49-a0f1-be6b68e9617b"
BODY = "a0c390b8-bc2e-47b3-b16a-5409197bd323"
THUMB = "35e7bfe5-ac7a-486c-8936-e74e71448c1b"


def _payload(*char_rows: list[Any]) -> list[Any]:
    """The Zzl0ze payload's shape: workflows, records, voices, …, characters, …, avatars."""
    workflow = [FACE, None, None, ["x_1.jpg", [1, 2], None, None, THUMB], PID, EID]
    record = ["wf-1", PID, "media-1", "CAE", None, [], None, []]
    voice = ["achird", 3, "Achird", ["achird"]]
    avatar = ["c7e01962-8bb7-0f4f-0000-000000000000", "me", [1, 2], "iVBOR"]
    return [None, [workflow], [record], [voice], None, list(char_rows), None, [], [avatar]]


def _char_row(eid: str = EID, name: str = "Tun") -> list[Any]:
    return [
        PID,
        eid,
        None,
        [1, name, [[[FACE], [FACE2]], [[BODY]], "Stoic agent"], True],
        THUMB,
        [768, 1376],
        [1788343559, 24280000],
        [1788455483, 721914000],
    ]


@pytest.mark.unit
def test_parses_the_character_row() -> None:
    chars = parse_migrated_characters(_payload(_char_row()), PID)
    assert chars == [
        Character(
            entity_id=EID,
            display_name="Tun",
            project_id=PID,
            workflow_ids=(FACE, FACE2, BODY),
            voice=None,
            personality="Stoic agent",
            thumbnail_media_id=THUMB,
        )
    ]


@pytest.mark.unit
def test_rows_are_found_by_shape_not_position() -> None:
    """A wrapper change must not blind the parser; other sections never match."""
    payload = [[[_payload(_char_row(), _char_row("11111111-2222-3333-4444-555555555555", "Kael"))]]]
    assert [c.display_name for c in parse_migrated_characters(payload, PID)] == ["Tun", "Kael"]


@pytest.mark.unit
def test_other_projects_and_non_character_rows_are_ignored() -> None:
    other = _char_row()
    other[0] = "00000000-0000-0000-0000-000000000000"
    assert parse_migrated_characters(_payload(other), PID) == []
    assert parse_migrated_characters(_payload(), PID) == []


@pytest.mark.unit
def test_a_character_without_reference_images_has_no_workflow_ids() -> None:
    row = _char_row()
    row[3] = [1, "Bare", None, True]
    (char,) = parse_migrated_characters(_payload(row), PID)
    assert char.workflow_ids == ()
    assert char.personality is None


def _bare_client() -> FlowApiClient:
    return FlowApiClient.__new__(FlowApiClient)


@pytest.mark.unit
async def test_list_characters_falls_back_to_the_migrated_host_on_the_retired_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = _bare_client()

    async def retired(*_: Any, **__: Any) -> Any:
        raise WireFormatError(
            detail="Flow RPCs have been deprecated and disabled.",
            status=404,
            route="projectInitialData",
        )

    async def migrated(project_id: str) -> Any:
        assert project_id == PID
        return _payload(_char_row())

    monkeypatch.setattr(c, "_get_json", retired)
    monkeypatch.setattr(c, "_fetch_migrated_project_data", migrated)
    chars = await c.list_characters(PID)
    assert [(ch.entity_id, ch.display_name) for ch in chars] == [(EID, "Tun")]


@pytest.mark.unit
async def test_other_labs_failures_are_not_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _bare_client()

    async def broken(*_: Any, **__: Any) -> Any:
        raise WireFormatError(detail="unexpected shape", status=200, route="projectInitialData")

    async def must_not_run(_pid: str) -> Any:
        raise AssertionError("fallback is only for the retired route")

    monkeypatch.setattr(c, "_get_json", broken)
    monkeypatch.setattr(c, "_fetch_migrated_project_data", must_not_run)
    with pytest.raises(WireFormatError, match="unexpected shape"):
        await c.list_characters(PID)


class _Resp:
    def __init__(self, url: str, body: str) -> None:
        self.url, self._body = url, body

    async def text(self) -> str:
        return self._body


class _RespInfo:
    def __init__(self, resp: _Resp) -> None:
        self._resp = resp

    @property
    async def value(self) -> _Resp:
        return self._resp


class _Expect:
    def __init__(self, page: _Page, predicate: Any) -> None:
        self.page, self.predicate = page, predicate

    async def __aenter__(self) -> _RespInfo:
        assert self.predicate(self.page.reply)
        return _RespInfo(self.page.reply)

    async def __aexit__(self, *_: Any) -> None:
        return None


class _Page:
    def __init__(self, reply: _Resp) -> None:
        self.reply, self.gotos, self.closed = reply, [], False

    def expect_response(self, predicate: Any, **_: Any) -> _Expect:
        return _Expect(self, predicate)

    async def goto(self, url: str, **_: Any) -> None:
        self.gotos.append(url)

    async def close(self) -> None:
        self.closed = True


class _Ctx:
    def __init__(self, page: _Page) -> None:
        self.page = page

    async def new_page(self) -> _Page:
        return self.page


def _envelope(rpcid: str, payload: Any) -> str:
    import json

    frame = json.dumps([["wrb.fr", rpcid, json.dumps(payload), None, None, None, "generic"]])
    return f")]}}'\n\n{len(frame)}\n{frame}\n"


_ZZL0ZE_URL = "https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=Zzl0ze"


@pytest.mark.unit
async def test_fetch_keeps_the_apps_own_project_load_reply() -> None:
    page = _Page(_Resp(_ZZL0ZE_URL, _envelope("Zzl0ze", _payload(_char_row()))))
    c = _bare_client()
    c._context = _Ctx(page)  # type: ignore[assignment]
    payload = await c._fetch_migrated_project_data(PID)
    assert [ch.display_name for ch in parse_migrated_characters(payload, PID)] == ["Tun"]
    assert page.gotos == [f"https://flow.google.com/project/{PID}"]
    assert page.closed


@pytest.mark.unit
async def test_fetch_without_a_zzl0ze_frame_is_a_wire_format_error() -> None:
    page = _Page(_Resp(_ZZL0ZE_URL, _envelope("other", [])))
    c = _bare_client()
    c._context = _Ctx(page)  # type: ignore[assignment]
    with pytest.raises(WireFormatError, match="Zzl0ze"):
        await c._fetch_migrated_project_data(PID)
    assert page.closed


@pytest.mark.unit
async def test_fetch_refuses_a_non_uuid_project() -> None:
    with pytest.raises(ValueError, match="project_id"):
        await _bare_client()._fetch_migrated_project_data("../etc")
