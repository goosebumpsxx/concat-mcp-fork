# Concat MCP bridge

This stdio MCP server exposes common project, media, edit, preview and export
operations by forwarding them to Concat's **authenticated loopback JSON-RPC
API**. It reuses the editor/CLI API rather than reimplementing edit behavior.

## Requirements

- Concat editor (Settings → Remote) or `concat-cli serve` running on loopback.
- Python 3.10+.
- Python MCP SDK v1: `python -m pip install 'mcp>=1,<2'`.

The server does not start Concat or open network listeners. It connects only
when its first tool is called. Export waits for the terminal event belonging
to the returned job id.

## Run by hand

```sh
CONCAT_HOST=127.0.0.1 CONCAT_PORT=7420 CONCAT_API_TOKEN='<token>' python scripts/concat_mcp.py
```

Use the token shown in Settings → Remote or printed by `concat-cli serve`.
Keep the Concat API bound to loopback; its token protects read/write access to
your local files and is not a substitute for network encryption.

## Hermes configuration

Add this entry under `mcp_servers` in your Hermes `config.yaml` (replace the script path with its absolute path):

```yaml
mcp_servers:
  concat:
    command: "python"
    args: ["/absolute/path/to/concat-mcp-fork/scripts/concat_mcp.py"]
    env:
      CONCAT_HOST: "127.0.0.1"
      CONCAT_PORT: "7420"
      CONCAT_API_TOKEN: "<token>"
```

For a generic MCP client, see [`scripts/mcp-config.example.json`](../scripts/mcp-config.example.json).

Restart Hermes after adding the server. The API token is a secret; do not commit
it or put it in a shared configuration. For a shared config, use an
environment-variable expansion mechanism provided by your host if available.

## Tools exposed

`version`, `project_list`, `project_open`, `project_create`, `project_get`,
`project_save`, `media_probe`, `media_import`, `edit_apply`, `edit_undo`,
`edit_redo`, `catalogue_list`, `export_run`, `preview_frame`, and
`preview_frame_to_file`.

`edit_apply` accepts the native command object documented in Concat's edit API.
Create/open a project first; project operations require an open folder. Export
returns when its own `export.done` or `export.failed` event arrives.
