# Gong MCP Server — Installation Process

> **Audience**: This document is designed for a Claude instance (Claude Code, Desktop, or Cowork) to follow step-by-step when setting up the Gong MCP server. It can also be followed manually.

---

## Prerequisites

Before starting, confirm the following:

1. **macOS** (paths assume Mac — adjust for Linux if needed)
2. **Python 3.10+** installed (`python3 --version` to check; install via `brew install python@3.12` if missing)
3. **Claude Code**, **Claude Desktop**, or **Claude Cowork** installed
4. **Gong API credentials** — the user needs these three values:
   - `GONG_BASE_URL` — e.g., `https://us-XXXX.api.gong.io` (find in Gong > Settings > API)
   - `GONG_ACCESS_KEY` — alphanumeric string
   - `GONG_ACCESS_KEY_SECRET` — longer JWT-style string starting with `eyJ...`

   If the user doesn't have credentials, their Gong workspace admin can generate them under **Gong > Settings > API** with these scopes:
   - `api:calls:read:basic`
   - `api:calls:read:extensive`
   - `api:calls:read:transcript`

---

## Step 1: Create the Project Directory

```bash
mkdir -p ~/Documents/MCP
```

## Step 2: Copy the Server Files

Copy these files into `~/Documents/MCP/`:

| File | Purpose |
|---|---|
| `gong_mcp.py` | The MCP server (V2 — concurrent fetching, flat tool params) |
| `requirements.txt` | Python dependencies (mcp, httpx, pydantic) |
| `claude_desktop_config.template.json` | Template config for Claude Desktop |

These files should be provided alongside this guide. If you're a Claude instance, verify they exist:

```bash
ls ~/Documents/MCP/gong_mcp.py ~/Documents/MCP/requirements.txt
```

## Step 3: Create the Python Virtual Environment

```bash
cd ~/Documents/MCP
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Verify all dependencies installed correctly:

```bash
~/Documents/MCP/venv/bin/python3 -c "import httpx, pydantic, mcp; print('All dependencies installed successfully')"
```

This should print `All dependencies installed successfully`. If it fails, check that `requirements.txt` contains:

```
mcp>=1.0.0
httpx[socks]>=0.27.0
pydantic>=2.0.0
```

## Step 4: Collect Gong API Credentials

**Ask the user for their three credential values.** Do not proceed without them.

You need:
1. **GONG_BASE_URL** — their Gong API base URL (e.g., `https://us-5730.api.gong.io`)
2. **GONG_ACCESS_KEY** — their access key
3. **GONG_ACCESS_KEY_SECRET** — their access key secret

> **Claude agents**: Use `AskUserQuestion` or ask directly to prompt the user for these values. Do not guess or reuse credentials from other installations.

## Step 5: Configure for Claude Code

Create or update the `.mcp.json` file in the project directory. Replace `YOUR_USERNAME`, `YOUR_GONG_BASE_URL`, `YOUR_ACCESS_KEY`, and `YOUR_ACCESS_KEY_SECRET` with real values.

Get the macOS username:

```bash
whoami
```

Then create `.mcp.json`:

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

> **Claude agents**: Write this file to `~/Documents/MCP/.mcp.json` using the `Write` tool, substituting in the real username (from `whoami`) and the credentials the user provided.

## Step 6 (Optional): Configure for Claude Desktop

If the user also uses Claude Desktop, update its config file at:

```
~/Library/Application Support/Claude/claude_desktop_config.json
```

If the file doesn't exist, create it with the same structure as `.mcp.json` above.

If it already exists and has other MCP servers configured, merge the `"gong"` entry into the existing `"mcpServers"` object. **Do not overwrite the existing config.**

To read the current config:

```bash
cat ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

> **Claude agents**: Read the file first with the `Read` tool. If it has existing entries, use `Edit` to add the gong server block inside the `mcpServers` object. If the file is empty or doesn't exist, use `Write` to create it.

After editing, fully quit Claude Desktop (`Cmd+Q`) and reopen it.

## Step 7 (Optional): Configure for Claude Cowork

If the user uses Claude Cowork, the MCP server configuration is managed through the Cowork app's settings (not a JSON file). The user will need to add the Gong MCP server through the Cowork interface.

Additionally, install the **Gong Transcripts skill** to give Claude operational knowledge of how to use the tools effectively:

1. Navigate to the `skill/` folder in this repo
2. The `SKILL.md` file contains the skill definition
3. Package it as a `.skill` file (zip the `skill/` folder contents with a `.skill` extension) or copy `SKILL.md` into the Cowork skills directory

The skill teaches Claude which tool to use for each type of request, date range best practices (always use tomorrow as `to_date_time`), account filter strategies, and the "last N calls" workflow.

> **Claude agents**: If you have access to the `skill-creator` skill or `present_files` tool, package and present the `.skill` file for one-click installation. Otherwise, guide the user through manual installation.

## Step 8: Create the Transcripts Output Directory

The export tool saves transcripts to `~/Documents/Transcripts/` by default:

```bash
mkdir -p ~/Documents/Transcripts
```

## Step 9: Verify the Installation

### For Claude Code

Restart Claude Code (exit and re-enter the `~/Documents/MCP` directory), then test by asking:

> "List Gong calls from the last 7 days"

The `gong_list_calls` tool should be available and return results.

### For Claude Desktop / Cowork

After restarting Claude Desktop or Cowork, test the same prompt. The Gong tools should appear in the tools list.

### Quick Verification Commands

If the tools aren't appearing, verify the server starts correctly:

```bash
cd ~/Documents/MCP
GONG_BASE_URL="YOUR_GONG_BASE_URL" \
GONG_ACCESS_KEY="YOUR_ACCESS_KEY" \
GONG_ACCESS_KEY_SECRET="YOUR_ACCESS_KEY_SECRET" \
~/Documents/MCP/venv/bin/python3 -c "import gong_mcp; print('Server module loads successfully')"
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| **"No such tool available"** | Restart Claude Code/Desktop/Cowork. For Desktop and Cowork, use `Cmd+Q` (not just close window). |
| **Authentication errors** | Double-check all three credential values. No extra spaces or line breaks. |
| **"Module not found" errors** | Re-run: `cd ~/Documents/MCP && source venv/bin/activate && pip install -r requirements.txt` |
| **Tool timeout errors** | Use a shorter date range. The server chunks into 10-day windows, but very large ranges may still timeout. |
| **No calls found for an account** | Try variations: company name ("Acme"), domain ("acme.com"), partial match ("acme"). Matching is case-insensitive. Use email domain for precision. |
| **Python version issues** | Run `python3 --version`. Need 3.10+. Install via `brew install python@3.12`. |
| **`.mcp.json` not picked up** | Make sure it's in the project root (`~/Documents/MCP/.mcp.json`) and Claude Code is launched from that directory. |
| **Pydantic validation error** | You may be running V1 with wrapped model params. Replace `gong_mcp.py` with the V2 file from this repo. |
| **Same-day calls missing** | Set `to_date_time` to tomorrow's date. A range ending at midnight UTC today misses afternoon calls in US timezones. |
| **Server changes not taking effect** | Fully quit (`Cmd+Q`) and relaunch Claude Desktop/Cowork. Closing the window is not enough. |

---

## Files Checklist

After a successful install, `~/Documents/MCP/` should contain:

```
~/Documents/MCP/
  |- .mcp.json                 # Claude Code config (with real credentials)
  |- gong_mcp.py               # MCP server script (V2)
  |- requirements.txt          # Python dependencies
  |- claude_desktop_config.template.json  # Template for Claude Desktop
  |- venv/                     # Python virtual environment
```

And `~/Documents/Transcripts/` should exist for transcript exports.

If using Cowork, the `gong-transcripts` skill should also be installed.
