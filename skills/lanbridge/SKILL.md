---
name: lanbridge
description: Manage LanBridge local website mappings, Cloudflare Tunnel routes, Turnstile policies and Windows connector setup through its CLI, local API or MCP bridge.
---

# LanBridge

Locate the LanBridge checkout and use its `.venv/Scripts/python.exe`. Inspect `run.py capabilities` to discover CLI commands. Read the project's README for API and MCP configuration.

- When the platform is running, use its authenticated loopback API or LanBridge MCP tools. CLI writes require stopping the platform because it owns an exclusive runtime lock. `run.py status` remains available for local configuration inspection.
- Check `cloudflare_setup` before adding a site. Account ID, Zone ID, Zone name and a saved write API token are required. Site hostnames must be subdomains of the configured Zone. Missing credentials should lead to the local configuration page, rather than collecting a site form that cannot be saved.
- Enter Cloudflare tokens and visitor passwords in the local management interface or the CLI hidden prompt. Do not put them in tool arguments, output, logs, command lines or repository files. MCP administrator credentials use the host's secure environment configuration.
- `run.py ensure-connector` or MCP `lanbridge_prepare_connector` checks the configured executable, then PATH and project bin, then downloads the official Windows release when needed. The management page offers this action next to the executable path.
- Register sites before syncing Turnstile domains. Preview Cloudflare changes, then apply the exact returned revision. If the preview becomes stale, refresh it. After a partial or uncertain write, inspect state and obtain a new preview rather than repeating a write blindly.
- Starting the connector enables public reachability. Apply the user's requested access policy before publishing; retain existing Cloudflare routes and reject conflicting DNS entries. Check the edge status and validate the site from an external network before claiming public availability.

The project skill is distributable with the checkout. Install this folder in a supported skill directory only when the user asks to enable it in their agent.
