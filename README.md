# Gong MCP Server

A local MCP (Model Context Protocol) server that connects Claude to your Gong instance. Pull call transcripts by account name, list calls, and export full transcripts to files — all through natural conversation.

Works with both **Claude Code** and **Claude Desktop**.

---

## Available Tools

| Tool | Description |
|------|-------------|
| `gong_list_calls` | List calls in a date range with basic metadata (ID, title, time, duration) |
| `gong_get_call` | Get detailed metadata for a specific call (participants, CRM context, media info) |
| `gong_get_transcripts` | Pull transcripts for specific call IDs (subject to 25K character response limit) |
| `gong_list_calls_extensive` | List calls with full participant details, interaction stats, and CRM context |
| `gong_get_account_transcripts` | Find and return transcripts for a specific account (subject to 25K character response limit) |
| `gong_export_account_transcripts` | Export full, untruncated transcripts for an account to markdown files on disk |

The **export tool** is the primary workflow tool. It scans all calls in a date range, matches them to an account, and writes complete transcripts to `~/Documents/Transcripts/{account_name}/`.

## Account Matching

The server matches calls to accounts using a three-tier approach:

1. **CRM Account** — matches against HubSpot/Salesforce account name or website in Gong's CRM context
2. **Call Title** — matches the account filter against the call title (e.g., "Acme <> YourCompany" matches "acme")
3. **Participant Email/Company** — matches against participant email domains or company names

All matching is case-insensitive. You can use a company name (`"Acme"`), domain (`"acme.com"`), or partial match (`"acme"`).

## Output Structure

Exported transcripts are saved as:

```
~/Documents/Transcripts/
  +-- {account_name}/
      |-- 2026-03-02_Call_Title_Here.md
      |-- 2026-03-10_Another_Call.md
      +-- _combined_transcripts.md
```

Each file includes call metadata (date, duration, participants, Gong link) followed by the full speaker-labeled transcript. The combined file contains all transcripts in one document.

## Requirements

- Python 3.10+
- Claude Code or Claude Desktop
- Gong API credentials (Access Key + Access Key Secret) with scopes:
  - `api:calls:read:basic`
  - `api:calls:read:extensive`
  - `api:calls:read:transcript`

## Setup

See **[install-process.md](install-process.md)** for the full step-by-step guide. That doc is written so a Claude Code instance can follow it to set up the server on your machine.

**Quick version:**

1. Copy `gong_mcp.py` and `requirements.txt` to `~/Documents/MCP/`
2. Create a Python venv and install dependencies
3. Get your Gong API credentials from your Gong admin
4. Create `.mcp.json` (Claude Code) or update `claude_desktop_config.json` (Claude Desktop)
5. Restart Claude

## Usage Examples

Once installed, just ask Claude naturally:

- "Pull the last 3 months of transcripts for Acme Corp"
- "Export all Notion call transcripts from February"
- "List all calls from last week"
- "Get the transcript for call ID 1234567890"
- "Show me details for all calls with example.com participants"

## Files

| File | Purpose |
|------|---------|
| `gong_mcp.py` | The MCP server (main script) |
| `requirements.txt` | Python dependencies |
| `claude_desktop_config.template.json` | Template config for Claude Desktop |
| `install-process.md` | Step-by-step setup guide (Claude Code-friendly) |
| `.mcp.json` | Claude Code MCP config (created during setup, contains credentials) |

## Rate Limits

Gong API defaults: 3 requests/second, 10,000 requests/day. The server handles pagination automatically and chunks large date ranges into 2-week windows to stay within timeout limits.

## Troubleshooting

- **Tool timeout errors** — date range is too large; try a shorter range
- **Authentication errors** — verify your Access Key and Secret in your config
- **No calls found** — try a broader account filter (company name, email domain, or partial match)
- **Module not found** — re-run `pip install -r requirements.txt` inside the venv
- **Tools not appearing** — restart Claude Code/Desktop; for Desktop use `Cmd+Q`
