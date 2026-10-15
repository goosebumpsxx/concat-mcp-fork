"""Concat JSON-RPC to MCP bridge.

Run as a stdio MCP server. The editor must be running with its Remote API
listening on loopback, or `concat-cli serve` must be running locally.
"""
from __future__ import annotations

import itertools
import json
import os
import socket
import threading
from typing import Any


class ConcatClient:
    """Small synchronous client for Concat's authenticated JSON-RPC line API."""

    def __init__(self, host: str, port: int, token: str, timeout: float = 600.0):
        self._socket = socket.create_connection((host, port), timeout=timeout)
        self._socket.settimeout(timeout)
        self._reader = self._socket.makefile("r", encoding="utf-8", newline="\n")
        self._ids = itertools.count(1)
        self._events: list[dict[str, Any]] = []
        self._io_lock = threading.RLock()
        self._send({"jsonrpc": "2.0", "id": 0, "method": "auth", "params": {"token": token}})
        welcome = self._receive()
        if welcome.get("id") != 0 or "error" in welcome:
            self.close()
            raise RuntimeError(welcome.get("error", {}).get("message", "Concat auth failed"))

    def call(self, method: str, **params: Any) -> Any:
        with self._io_lock:
            request_id = next(self._ids)
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            return self._wait_for_reply(request_id)

    def _wait_for_reply(self, request_id: int) -> Any:
        while True:
            message = self._receive()
            if "method" in message and "id" not in message:
                self._events.append(message)
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                code = error.get("data", {}).get("code", error.get("code", "error"))
                raise RuntimeError(f"{code}: {error.get('message', 'Concat API call failed')}")
            return message.get("result")

    def wait_for_job(self, job: str) -> dict[str, Any]:
        """Wait for the terminal export event for this job (not another export)."""
        with self._io_lock:
            while True:
                for index, event in enumerate(self._events):
                    if event.get("params", {}).get("job") == job and event.get("method") in ("export.done", "export.failed"):
                        return {key: value for key, value in self._events.pop(index).items() if key != "jsonrpc"}
                message = self._receive()
                if "method" in message and "id" not in message:
                    self._events.append(message)
                elif "id" in message:
                    raise RuntimeError(f"unexpected JSON-RPC reply while waiting for export job {job}")

    def _send(self, value: dict[str, Any]) -> None:
        self._socket.sendall((json.dumps(value, separators=(",", ":")) + "\n").encode("utf-8"))

    def _receive(self) -> dict[str, Any]:
        line = self._reader.readline()
        if not line:
            raise ConnectionError("Concat API closed the connection")
        return json.loads(line)

    def close(self) -> None:
        try:
            self._reader.close()
        finally:
            self._socket.close()


def _client_from_environment() -> ConcatClient:
    host = os.environ.get("CONCAT_HOST", "127.0.0.1")
    port = int(os.environ.get("CONCAT_PORT", "7420"))
    token = os.environ.get("CONCAT_API_TOKEN", "")
    if not token:
        raise RuntimeError("Set CONCAT_API_TOKEN to the token shown by Concat Settings > Remote or concat-cli serve")
    return ConcatClient(host, port, token)



def build_server(client: ConcatClient | None = None):
    """Build the MCP server; injecting a client keeps tool discovery testable."""
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.types import ImageContent, ToolAnnotations
    except ImportError as error:
        raise RuntimeError("Install Python MCP SDK v1 with `python -m pip install 'mcp>=1,<2'`") from error

    active_client = client

    def api_client() -> ConcatClient:
        nonlocal active_client
        if active_client is None:
            active_client = _client_from_environment()
        return active_client

    mcp = FastMCP("concat", instructions="Concat editor MCP bridge over its authenticated local JSON-RPC API.")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    def version() -> dict[str, Any]:
        """Get the Concat API version and capabilities. Call this first."""
        return api_client().call("version")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    def project_list() -> list[dict[str, Any]]:
        """List recently opened Concat projects."""
        return api_client().call("project.list")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    def project_open(path: str) -> dict[str, Any]:
        """Open a Concat project folder and return its editor state."""
        return api_client().call("project.open", path=path)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    def project_create(location: str, name: str) -> dict[str, Any]:
        """Create a project folder under location and open it."""
        return api_client().call("project.create", location=location, name=name)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    def project_get(path: str) -> dict[str, Any]:
        """Read the current editor state for an open project."""
        return api_client().call("project.get", path=path)

    @mcp.tool()
    def project_save(path: str, name: str | None = None) -> dict[str, Any]:
        """Save an open project, optionally renaming it."""
        return api_client().call("project.save", path=path, **({"name": name} if name else {}))

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    def media_probe(path: str) -> dict[str, Any]:
        """Inspect a media file without importing it."""
        return api_client().call("media.probe", path=path)

    @mcp.tool()
    def media_import(path: str, file: str) -> dict[str, Any]:
        """Probe and import a media file into the open project's bin."""
        return api_client().call("media.import", path=path, file=file)

    @mcp.tool()
    def edit_apply(path: str, command: dict[str, Any]) -> dict[str, Any]:
        """Apply one native Concat edit command to the open project."""
        return api_client().call("edit.apply", path=path, command=command)

    @mcp.tool()
    def edit_undo(path: str) -> dict[str, Any]:
        """Undo the most recent project edit."""
        return api_client().call("edit.undo", path=path)

    @mcp.tool()
    def edit_redo(path: str) -> dict[str, Any]:
        """Redo the last undone project edit."""
        return api_client().call("edit.redo", path=path)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    def catalogue_list(kind: str | None = None) -> list[dict[str, Any]]:
        """List available effects, filters, audio effects, transitions, or generators."""
        return api_client().call("catalogue.list", **({"kind": kind} if kind else {}))

    @mcp.tool()
    def export_run(path: str, output: str) -> dict[str, Any]:
        """Export the project's active timeline; wait for its matching completion event."""
        started = api_client().call("export.run", path=path, output=output)
        return api_client().wait_for_job(started["job"])

    @mcp.tool()
    def preview_frame(path: str, time: float, width: int | None = None, height: int | None = None):
        """Render a PNG preview frame and return it as MCP image content."""
        import base64

        frame = api_client().call("preview.frame", path=path, time=time, width=width, height=height)
        return ImageContent(
            type="image", data=base64.b64decode(frame["png"]), mimeType="image/png"
        )

    @mcp.tool()
    def preview_frame_to_file(path: str, time: float, output: str, width: int | None = None, height: int | None = None) -> dict[str, Any]:
        """Render a preview frame to an output file inside a permitted root."""
        return api_client().call("preview.frame", path=path, time=time, output=output, width=width, height=height)

    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
