---
name: gong-transcripts
description: "Pull, export, and save Gong call transcripts by account. Use this skill whenever the user asks to fetch, pull, download, or export Gong transcripts, find calls for a specific account or company, get the last N calls for an account, save transcripts to a folder (Desktop, Documents, etc.), or search Gong by account name, date range, or participant. Always invoke this skill for any Gong transcript or call export request. The Gong MCP tools are available as mcp__gong__* and must always be used instead of direct API calls."
---

# Gong Transcript MCP — Skill Guide

## Tool Selection Guide

| User request | Tool to use |
|---|---|
| "Pull transcripts for [account]" | `gong_export_account_transcripts` |
| "Save transcripts to [folder]" | `gong_export_account_transcripts` with `output_dir` set |
| "Last N calls for [account]" | `gong_get_account_transcripts` (JSON) to identify top N, then `gong_export_account_transcripts` with narrowed range |
| "What calls happened with [account]?" | `gong_get_account_transcripts` (markdown) |
| "List all calls this week" | `gong_list_calls` |
| "Get details on call ID XXXX" | `gong_get_call` |

**Key rule:** `gong_export_account_transcripts` writes full files to disk and bypasses the 25K character response limit. Always prefer it when the goal is saving transcripts. `gong_get_account_transcripts` is better for quick in-conversation review.

---

## Date Range Best Practices

**Always set `to_date_time` to tomorrow's date**, not today. Gong timestamps calls in the user's local timezone. A range ending at midnight UTC on today's date will miss calls that happened today in afternoon US timezones.

```
from_date_time: "2026-02-19T00:00:00Z"   ← 30 days back
to_date_time:   "2026-03-21T00:00:00Z"   ← tomorrow (captures today's calls)
```

For "last month" requests, use 30-35 days back with tomorrow as the end.
For "last N calls," start with 90 days back.

---

## Account Filter Strategies

Matching is case-insensitive. The server checks in this order: call title first, then CRM account name, then participant emails/companies.

**Recommended order to try:**

1. **Email domain** — most precise. Examples: `"gomotive.com"`, `"tracelink.com"`. Avoids partial-name false positives (e.g., "motiv" matching "automotivemastermind.com").
2. **Company name** — good default. Examples: `"Motive"`, `"TraceLink"`, `"ITX Corp"`.
3. **Partial match** — broadest. Use when name variants are unknown.

If a search returns 0 results, try the email domain next before widening the date range.

---

## "Last N Calls" Workflow

The export tool doesn't have a built-in limit — it exports all matching calls in a date range. To get exactly the last N calls:

**Step 1:** Run `gong_get_account_transcripts` with JSON format over a broad range (90 days) to see how many calls exist and their dates.

**Step 2:** Note the date of the Nth most recent call. Set `from_date_time` to just before that date.

**Step 3:** Run `gong_export_account_transcripts` with that narrowed range. If you get N+1 due to same-day calls, delete the oldest file.

---

## Saving to Specific Locations

The `output_dir` parameter accepts any path:

```
output_dir: "~/Desktop"              → ~/Desktop/{account_name}/
output_dir: "~/Documents/Gong"       → ~/Documents/Gong/{account_name}/
output_dir: "~/Documents/Transcripts" → default location
```

The tool creates a subfolder named after the `account_filter` value automatically.

In Cowork, saving to the Desktop requires folder access. Use `mcp__cowork__request_cowork_directory` with `path: "~/Desktop"` if not already mounted.

---

## After Updating gong_mcp.py

Any changes to the MCP server file require a full restart of Claude Desktop or Cowork:
1. `Cmd+Q` to fully quit (do not just close the window)
2. Relaunch the application
3. The updated server loads automatically

---

## Common Patterns

**Pull all transcripts for an account over the past month:**
```
tool: gong_export_account_transcripts
from_date_time: [30 days ago]T00:00:00Z
to_date_time: [tomorrow]T00:00:00Z
account_filter: [company name or email domain]
output_dir: ~/Documents/Transcripts
```

**Pull last 5 calls for an account to the Desktop:**
```
1. gong_get_account_transcripts (json, 90 days) → count calls, note dates
2. gong_export_account_transcripts with narrowed range → output_dir: ~/Desktop
3. Remove oldest file if N+1 results
```

**Quick transcript review in conversation:**
```
tool: gong_get_account_transcripts
response_format: markdown
[use narrow date range — wide ranges hit 25K char truncation]
```
