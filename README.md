# Gong MCP Server (V2)

A local MCP (Model Context Protocol) server that connects Claude to your Gong instance. Pull call transcripts by account name, list calls, and export full transcripts to files — all through natural conversation.

Works with **Claude Code**, **Claude Desktop**, and **Claude Cowork**.

---

## What's in This Repo

| File / Folder | Purpose |
|---|---|
| `gong_mcp.py` | The MCP server (V2 — concurrent fetching, flat tool params) |
| `requirements.txt` | Python dependencies |
| `claude_desktop_config.template.json` | Template config for Claude Desktop |
| `install-process.md` | Step-by-step setup guide (Claude-friendly — a Claude instance can follow it) |
| `skill/SKILL.md` | Cowork skill file — teaches Claude how to use the Gong tools effectively |
| `.gitignore` | Keeps credentials and venvs out of version control |

---

## Available Tools

| Tool | Description |
|---|---|
| `gong_list_calls` | List calls in a date range with basic metadata (ID, title, time, duration) |
| `gong_get_call` | Get detailed metadata for a specific call (participants, CRM context, media) |
| `gong_get_transcripts` | Pull transcripts for specific call IDs (subject to 25K char response limit) |
| `gong_list_calls_extensive` | List calls with full participant details, interaction stats, and CRM context |
| `gong_get_account_transcripts` | Find and return transcripts for a specific account (subject to 25K char response limit) |
| `gong_export_account_transcripts` | Export full, untruncated transcripts for an account to markdown files on disk |

The **export tool** is the primary workflow tool. It scans all calls in a date range, matches them to an account, and writes complete transcripts to disk.

## V2 Improvements

V2 is a significant performance and compatibility upgrade over V1:

- **Concurrent date-chunk fetching** — splits large date ranges into 10-day chunks and fetches them in parallel via `asyncio.gather`, resulting in ~95% faster discovery on wide ranges
- **Flat tool parameters** — all tool functions accept individual named parameters instead of wrapped Pydantic models, which fixes compatibility with Cowork and other MCP clients that pass arguments as flat key-value pairs
- **Title-first account matching** — checks call titles before CRM data (cheapest check first, most common match)
- **Connection pooling** — reuses a persistent `httpx.AsyncClient` across requests
- **Rate limiting** — `asyncio.Semaphore(3)` matches Gong's 3 requests/second limit

## Account Matching

The server matches calls to accounts using a three-tier approach (checked in this order):

1. **Call Title** — matches the account filter against the call title (e.g., "Acme <> YourCompany" matches "acme")
2. **CRM Account** — matches against HubSpot/Salesforce account name or website in Gong's CRM context
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
- Claude Code, Claude Desktop, or Claude Cowork
- Gong API credentials (Access Key + Access Key Secret) with scopes:
  - `api:calls:read:basic`
  - `api:calls:read:extensive`
  - `api:calls:read:transcript`

## Setup

See **[install-process.md](install-process.md)** for the full step-by-step guide. That doc is written so a Claude instance can follow it to set up the server on your machine.

**Quick version:**

1. Copy `gong_mcp.py` and `requirements.txt` to `~/Documents/MCP/`
2. Create a Python venv and install dependencies
3. Get your Gong API credentials from your Gong admin
4. Configure your Claude client (Claude Code, Desktop, or Cowork)
5. (Cowork only) Install the skill from `skill/SKILL.md`
6. Restart Claude

## Usage Examples

Once installed, just ask Claude naturally:

- "Pull the last 3 months of transcripts for Acme Corp"
- "Export all Notion call transcripts from February"
- "Save the last 5 TraceLink calls to my Desktop"
- "List all calls from last week"
- "Get the transcript for call ID 1234567890"

## Rate Limits

Gong API defaults: 3 requests/second, 10,000 requests/day. The server handles pagination automatically and chunks large date ranges into 10-day windows fetched concurrently.

## Troubleshooting

| Problem | Solution |
|---|---|
| Tool timeout errors | Date range is too large; try a shorter range |
| Authentication errors | Verify your Access Key and Secret in your config |
| No calls found | Try a broader account filter (company name, email domain, or partial match). Use email domain for precision. |
| Module not found | Re-run `pip install -r requirements.txt` inside the venv |
| Tools not appearing | Restart Claude Code/Desktop/Cowork; for Desktop and Cowork use `Cmd+Q` |
| Pydantic validation error | You may be running V1. Replace `gong_mcp.py` with the V2 file from this repo. |
| Same-day calls missing | Set `to_date_time` to tomorrow's date, not today |
