"""Tests for the Concat stdio MCP bridge client."""
import asyncio
import json

import pytest

from concat_mcp import ConcatClient, _client_from_environment, build_server


class FakeSocket:
    def __init__(self, incoming):
        self.incoming = iter(incoming)
        self.sent = []
        self.closed = False

    def settimeout(self, _timeout):
        pass

    def makefile(self, *_args, **_kwargs):
        return self

    def readline(self):
        return next(self.incoming, "")

    def sendall(self, data):
        self.sent.append(json.loads(data))

    def close(self):
        self.closed = True


def wire(*messages):
    return iter(json.dumps(message) + "\n" for message in messages)


def test_mcp_server_exposes_only_the_selected_core_tool_surface():
    server = build_server(object())
    tools = asyncio.run(server.list_tools())
    names = {tool.name for tool in tools}
    assert names == {
        "version", "project_list", "project_open", "project_create",
        "project_get", "project_save", "media_probe", "media_import",
        "edit_apply", "edit_undo", "edit_redo", "catalogue_list",
        "export_run", "preview_frame", "preview_frame_to_file",
    }


def test_read_only_tools_are_annotated():
    server = build_server(object())
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    for name in ("version", "project_list", "project_get", "media_probe", "catalogue_list"):
        assert tools[name].annotations.readOnlyHint is True
        assert tools[name].annotations.destructiveHint is False


def test_client_authenticates_and_preserves_jsonrpc_results(monkeypatch):
    fake = FakeSocket(wire(
        {"jsonrpc": "2.0", "id": 0, "result": {}},
        {"jsonrpc": "2.0", "id": 1, "result": {"apiVersion": "0.2"}},
        {"jsonrpc": "2.0", "id": 2, "result": {"project": {"path": "/project"}}},
    ))
    monkeypatch.setattr("concat_mcp.socket.create_connection", lambda *_a, **_kw: fake)
    client = ConcatClient("127.0.0.1", 7420, "test-token")
    assert client.call("version") == {"apiVersion": "0.2"}
    server = build_server(client)
    asyncio.run(server.call_tool("project_open", {"path": "/project"}))
    assert fake.sent[-1] == {
        "jsonrpc": "2.0", "id": 2, "method": "project.open",
        "params": {"path": "/project"},
    }
    client.close()


def test_client_requires_token_from_environment(monkeypatch):
    monkeypatch.delenv("CONCAT_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="CONCAT_API_TOKEN"):
        _client_from_environment()


def test_client_raises_api_errors_with_the_server_reason(monkeypatch):
    fake = FakeSocket(wire(
        {"jsonrpc": "2.0", "id": 0, "result": {}},
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32001, "message": "/missing is not open - open it first", "data": {"code": "notOpen"}}},
    ))
    monkeypatch.setattr("concat_mcp.socket.create_connection", lambda *_a, **_kw: fake)
    client = ConcatClient("127.0.0.1", 7420, "test-token")
    with pytest.raises(RuntimeError, match="notOpen.*open it first"):
        client.call("project.get", path="/missing")
    client.close()


def test_export_completion_is_matched_by_job_id(monkeypatch):
    fake = FakeSocket(wire(
        {"jsonrpc": "2.0", "id": 0, "result": {}},
        {"jsonrpc": "2.0", "id": 1, "result": {"job": "j-test", "path": "/project", "output": "/out.mp4"}},
        {"jsonrpc": "2.0", "method": "export.done", "params": {"job": "j-other", "path": "/other", "output": "/other.mp4"}},
        {"jsonrpc": "2.0", "method": "export.progress", "params": {"job": "j-test", "frame": 1}},
        {"jsonrpc": "2.0", "method": "export.done", "params": {"job": "j-test", "path": "/project", "output": "/out.mp4", "width": 640, "height": 360}},
    ))
    monkeypatch.setattr("concat_mcp.socket.create_connection", lambda *_a, **_kw: fake)
    client = ConcatClient("127.0.0.1", 7420, "test-token")
    started = client.call("export.run", path="/project", output="/out.mp4")
    assert started["job"] == "j-test"
    assert client.wait_for_job("j-test") == {
        "method": "export.done",
        "params": {"job": "j-test", "path": "/project", "output": "/out.mp4", "width": 640, "height": 360},
    }
    assert client._events[0]["params"]["job"] == "j-other"
    client.close()
