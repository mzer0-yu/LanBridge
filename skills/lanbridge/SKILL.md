---
name: lanbridge
description: Manage LanBridge local website mappings, Cloudflare Tunnel routes, Turnstile policies and Cloudflare connector setup on Windows through its CLI, local API or MCP bridge.
---

# LanBridge

Locate the LanBridge checkout; the administrator UI is `/admin`, and the read-only local route directory is `/client`. Use its `.venv/Scripts/python.exe`. Inspect `run.py capabilities` to discover CLI commands. Read the project's README for API and MCP configuration.

- When the platform is running, use its authenticated loopback API or LanBridge MCP tools. CLI writes require stopping the platform because it owns an exclusive runtime lock. `run.py status` remains available for local configuration inspection.
- Check `cloudflare_setup` before adding a site. Account ID, Zone ID, Zone name and a saved write credential (API token or browser OAuth) are required. Site hostnames must be subdomains of the configured Zone. Missing credentials should lead to the local configuration page, rather than collecting a site form that cannot be saved.
- Enter Cloudflare tokens and visitor passwords in the local management interface or the CLI hidden prompt. Do not put them in tool arguments, output, logs, command lines or repository files. MCP administrator credentials use the host's secure environment configuration.
- `run.py ensure-connector` or MCP `lanbridge_prepare_connector` checks the configured executable, then PATH and project bin, then downloads the official Windows release when needed. The management page offers this action next to the executable path.
- Saving an enabled human-check site through the UI/API/CLI/MCP automatically prepares its managed Turnstile Widget before committing the site. Do not require a second manual domain-sync step. Browser Cloudflare authorization uses OAuth directly; API Tokens Write is only needed for the separate token-management flow. Preview Cloudflare changes, then apply the exact returned revision. If the preview becomes stale, refresh it. After a partial or uncertain write, inspect state and obtain a new preview rather than repeating a write blindly.
- Pause/resume forwarding through authenticated `POST /api/sites/{id}/pause` with a boolean `paused`. This preserves cloud routes and needs no publish step; it does not start the connector or publish an unregistered route. The website-save schema also accepts `paused`; there is no dedicated pause CLI command or MCP tool. Editing unrelated fields must preserve pause state.
- Starting the connector enables public reachability. Apply the user's requested access policy before publishing; retain existing Cloudflare routes and reject conflicting DNS entries. Check the edge status and validate the site from an external network before claiming public availability.

The project skill is distributable with the checkout. Install this folder in a supported skill directory only when the user asks to enable it in their agent.
