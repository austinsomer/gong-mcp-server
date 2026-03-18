# Gong MCP Server - Installation Guide

This guide walks you through setting up the Gong MCP server on your machine. If you're reading this in Claude Cowork, Claude can help you run each step.

## Prerequisites

- macOS (the paths below assume Mac; adjust for Windows/Linux if needed)
- Python 3.10 or later installed on your machine
- Claude Desktop installed
- Gong API credentials (Access Key and Access Key Secret) - ask your Gong admin or get them from Gong > Settings > API

## Step 1: Create the Project Folder

Create a folder to hold the MCP server files:

```bash
mkdir -p ~/Documents/MCP
```

## Step 2: Copy Files

Copy the following files into `~/Documents/MCP/`:

- `gong_mcp.py` (the MCP server)
- `requirements.txt` (Python dependencies)

These files should have been provided to you alongside this guide. If you're in Claude Cowork, you can ask Claude to check if they're already in place.

## Step 3: Set Up Python Environment

Open Terminal and run:

```bash
cd ~/Documents/MCP
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

This creates an isolated Python environment and installs the three required packages (mcp, httpx, pydantic).

Verify it worked:

```bash
~/Documents/MCP/venv/bin/python3 -c "import httpx, pydantic, mcp; print('All dependencies installed successfully')"
```

## Step 4: Get Your Gong API Credentials

You need two values from Gong:

1. **Gong Base URL** - This is your Gong API base URL. It looks like `https://us-XXXX.api.gong.io` where XXXX is your Gong instance number. You can find this in Gong under Settings > API.
2. **Access Key** - A string like `BOPNQAYYXGJG5THPMXFFWE6Q7464W4OU`
3. **Access Key Secret** - A longer JWT-style string starting with `eyJ...`

If you don't have these, ask your Gong workspace admin to generate API credentials with the following scopes:
- `api:calls:read:basic`
- `api:calls:read:extensive`
- `api:calls:read:transcript`

## Step 5: Configure Claude Desktop

Open Claude Desktop's configuration file. On Mac, it's located at:

```
~/Library/Application Support/Claude/claude_desktop_config.json
```

You can open it in Terminal with:

```bash
open -e ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

If the file doesn't exist yet or is empty, create it with this content (replacing the three placeholder values with your actual credentials):

```json
{
  "mcpServers": {
    "gong": {
      "command": "/Users/YOUR_USERNAME/Documents/MCP/venv/bin/python3",
      "args": [
        "/Users/YOUR_USERNAME/Documents/MCP/gong_mcp.py"
      ],
      "env": {
        "GONG_BASE_URL": "YOUR_GONG_BASE_URL",
        "GONG_ACCESS_KEY": "YOUR_ACCESS_KEY",
        "GONG_ACCESS_KEY_SECRET": "YOUR_ACCESS_KEY_SECRET"
      }
    }
  }
}
```

**Important**: Replace `YOUR_USERNAME` with your macOS username (run `whoami` in Terminal if unsure). Replace the three `YOUR_*` values with your actual Gong credentials from Step 4.

If you already have other MCP servers configured, add the `"gong": { ... }` block inside the existing `"mcpServers"` object. Don't overwrite your existing config.

## Step 6: Restart Claude Desktop

Fully quit Claude Desktop (Cmd+Q, not just close the window) and reopen it. The Gong MCP tools should now be available.

## Step 7: Verify It Works

In Claude Desktop, try asking:

> "List the last week of Gong calls"

If you get a list of calls back, the setup is complete. If you get an authentication error, double-check your credentials in the config file.

## Step 8: Create the Transcripts Folder

The export tool saves transcripts to `~/Documents/Transcripts/` by default. Create it now:

```bash
mkdir -p ~/Documents/Transcripts
```

## You're Done

Here are some things you can now ask Claude:

- "Pull the last 2 months of transcripts for [client name]"
- "Export all [company] call transcripts from January"
- "List all calls from this week"
- "Get details on call ID [number]"

Transcripts are saved to `~/Documents/Transcripts/{client_name}/` as markdown files, with one file per call plus a combined file containing everything.

## Troubleshooting

**"No such tool available" error**
Claude Desktop didn't pick up the config. Make sure you fully quit (Cmd+Q) and reopened Claude Desktop after editing the config.

**Authentication errors**
Verify your Access Key and Secret are correct. Make sure there are no extra spaces or line breaks in the config file.

**"Module not found" errors**
The Python virtual environment may not have the dependencies. Run:
```bash
cd ~/Documents/MCP && source venv/bin/activate && pip install -r requirements.txt
```

**Tool timeout errors**
Large date ranges (3+ months) can be slow. The tool chunks requests into 2-week windows automatically, but very large ranges may still push up against the 60-second MCP timeout. Try a shorter date range.

**No calls found for an account**
The tool matches by CRM account name, call title, and participant email domain. Try variations: company name ("Acme"), domain ("acme.com"), or partial match ("acme"). Matching is case-insensitive.

**Python version issues**
Run `python3 --version` to check. You need 3.10 or later. If you have an older version, install a newer one via Homebrew: `brew install python@3.12`
