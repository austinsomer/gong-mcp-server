# Gong MCP Server

A local MCP (Model Context Protocol) server that connects Claude Desktop to your Gong instance. Pull call transcripts by account name, list calls, and export full transcripts to files, all through natural conversation in Claude.

## What It Does

This server gives Claude access to six Gong tools:

- **gong_list_calls** - List calls in a date range with basic metadata
- **gong_get_call** - Get detailed metadata for a specific call
- **gong_get_transcripts** - Pull transcripts for specific calls (subject to 25K character response limit)
- **gong_list_calls_extensive** - List calls with full participant and CRM context
- **gong_get_account_transcripts** - Find and return transcripts for a specific account (subject to 25K character response limit)
- **gong_export_account_transcripts** - Find and export full, untruncated transcripts for a specific account to markdown files on your computer

The export tool is the primary workflow tool. It scans all calls in a date range, matches them to an account by CRM data, call title, or participant email, then writes complete transcripts to `~/Documents/Transcripts/{account_name}/`.

## Account Matching

The server matches calls to accounts using a three-tier approach:

1. **CRM Account** - Matches against HubSpot/Salesforce account name or website in Gong's CRM context
2. **Call Title** - Matches the account name against the call title (e.g., "Rocketlane <> Tracelink" matches "tracelink")
3. **Participant Email/Company** - Matches against participant email domains or company names

All matching is case-insensitive. You can use a company name ("Tracelink"), domain ("tracelink.com"), or partial match ("notion").

## Output Structure

When using the export tool, transcripts are saved to:

```
~/Documents/Transcripts/
  └── {account_name}/
      ├── 2026-03-02_Call_Title_Here.md
      ├── 2026-03-10_Another_Call.md
      └── _combined_transcripts.md
```

Each file includes call metadata (date, duration, participants, Gong link) and the full speaker-labeled transcript. The combined file contains all transcripts in a single document.

## Requirements

- Python 3.10+
- Claude Desktop (with MCP support)
- Gong API credentials (Access Key + Secret)

## Files

- `gong_mcp.py` - The MCP server
- `requirements.txt` - Python dependencies
- `claude_desktop_config.json` - Template config for Claude Desktop
- `gong.env` - Where to store your Gong API credentials (not committed to version control)
- `install-instructions.md` - Step-by-step setup guide (can be used with Claude Cowork for guided installation)

## Quick Start

See `install-instructions.md` for the full setup walkthrough. The short version:

1. Copy files to `~/Documents/MCP/`
2. Create a Python virtual environment and install dependencies
3. Add your Gong API credentials to `claude_desktop_config.json`
4. Merge the config into Claude Desktop's config file
5. Restart Claude Desktop

## Usage Examples

Once installed, just ask Claude naturally:

- "Pull the last 3 months of transcripts for Acme Corp"
- "Export all Notion call transcripts from February"
- "List all calls from last week"
- "Get the transcript for call ID 1234567890"

## Rate Limits

Gong API defaults: 3 requests/second, 10,000 requests/day. The server handles pagination automatically and chunks large date ranges into 2-week windows to stay within timeout limits.

## Troubleshooting

- **Tool timeout errors**: Usually means the date range is too large. Try a shorter range or the server will chunk it automatically into 2-week windows.
- **Authentication errors**: Verify your Access Key and Secret in the Claude Desktop config.
- **No calls found**: Try a broader account filter. Use just the company name or email domain rather than a full string.
- **"float has no attribute lower"**: This was a known bug that's been fixed. Make sure you're running the latest version of `gong_mcp.py`.
