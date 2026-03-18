# Gong MCP Server - Installation Process

> **Audience**: This document is designed for a Claude Code instance to follow step-by-step when setting up the Gong MCP server on a colleague's machine. It can also be followed manually.

---

## Prerequisites

Before starting, confirm the following:

1. **macOS** (paths assume Mac — adjust for Linux if needed)
2. **Python 3.10+** installed (`python3 --version` to check; install via `brew install python@3.12` if missing)
3. **Claude Code** or **Claude Desktop** installed
4. **Gong API credentials** — the colleague needs these three values:
   - `GONG_BASE_URL` — e.g., `https://us-XXXX.api.gong.io` (find in Gong > Settings > API)
   - `GONG_ACCESS_KEY` — alphanumeric string
   - `GONG_ACCESS_KEY_SECRET` — longer JWT-style string starting with `eyJ...`

   If the colleague doesn't have credentials, their Gong workspace admin can generate them under **Gong > Settings > API** with these scopes:
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
|------|---------|
| `gong_mcp.py` | The MCP server (main script) |
| `requirements.txt` | Python dependencies (mcp, httpx, pydantic) |
| `claude_desktop_config.template.json` | Template config for Claude Desktop |

These files should be provided alongside this guide. If you're a Claude Code instance, verify they exist:

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

**Ask the colleague for their three credential values.** Do not proceed without them.

You need:
1. **GONG_BASE_URL** — their Gong API base URL (e.g., `https://us-5730.api.gong.io`)
2. **GONG_ACCESS_KEY** — their access key
3. **GONG_ACCESS_KEY_SECRET** — their access key secret

> **Claude Code agents**: Use `AskUserQuestion` to prompt the colleague for these values. Do not guess or reuse credentials from other installations.

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

> **Claude Code agents**: Write this file to `~/Documents/MCP/.mcp.json` using the `Write` tool, substituting in the real username (from `whoami`) and the credentials the colleague provided.

## Step 6 (Optional): Configure for Claude Desktop

If the colleague also uses Claude Desktop, update its config file at:

```
~/Library/Application Support/Claude/claude_desktop_config.json
```

If the file doesn't exist, create it with the same structure as `.mcp.json` above.

If it already exists and has other MCP servers configured, merge the `"gong"` entry into the existing `"mcpServers"` object. **Do not overwrite the existing config.**

To read the current config:

```bash
cat ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

> **Claude Code agents**: Read the file first with the `Read` tool. If it has existing entries, use `Edit` to add the gong server block inside the `mcpServers` object. If the file is empty or doesn't exist, use `Write` to create it.

After editing, fully quit Claude Desktop (`Cmd+Q`) and reopen it.

## Step 7: Create the Transcripts Output Directory

The export tool saves transcripts to `~/Documents/Transcripts/` by default:

```bash
mkdir -p ~/Documents/Transcripts
```

## Step 8: Verify the Installation

### For Claude Code

Restart Claude Code (exit and re-enter the `~/Documents/MCP` directory), then test by asking:

> "List Gong calls from the last 7 days"

The `gong_list_calls` tool should be available and return results.

### For Claude Desktop

After restarting Claude Desktop, test the same prompt. The Gong tools should appear in the tools list.

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
|---------|----------|
| **"No such tool available"** | Restart Claude Code/Desktop. For Desktop, use `Cmd+Q` (not just close window). |
| **Authentication errors** | Double-check all three credential values. No extra spaces or line breaks. |
| **"Module not found" errors** | Re-run: `cd ~/Documents/MCP && source venv/bin/activate && pip install -r requirements.txt` |
| **Tool timeout errors** | Use a shorter date range. The server auto-chunks into 2-week windows, but very large ranges may still timeout. |
| **No calls found for an account** | Try variations: company name ("Acme"), domain ("acme.com"), partial match ("acme"). Matching is case-insensitive. |
| **Python version issues** | Run `python3 --version`. Need 3.10+. Install via `brew install python@3.12`. |
| **`.mcp.json` not picked up** | Make sure it's in the project root (`~/Documents/MCP/.mcp.json`) and Claude Code is launched from that directory. |

---

## Files Checklist

After a successful install, `~/Documents/MCP/` should contain:

```
~/Documents/MCP/
  |- .mcp.json                 # Claude Code config (with real credentials)
  |- gong_mcp.py               # MCP server script
  |- requirements.txt          # Python dependencies
  |- claude_desktop_config.template.json  # Template for Claude Desktop
  |- venv/                     # Python virtual environment
```

And `~/Documents/Transcripts/` should exist for transcript exports.
