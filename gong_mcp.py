#!/usr/bin/env python3
"""
MCP Server for Gong API v2 — Optimized (V2).

Provides tools to interact with Gong's conversation intelligence platform,
including listing calls, retrieving call metadata, and pulling transcripts.
Supports filtering by account via CRM context, call title, and participant matching.

Optimizations over V1:
  1. Persistent HTTP client (connection pooling) — eliminates per-request TCP handshake
  2. Concurrent date-chunk fetching — asyncio.gather with Semaphore(3) rate limiting
  3. Leaner payloads — context: "Basic" where possible, no contextTiming
  4. 10-day chunks — optimal balance of parallelism vs API call overhead
  5. Title-first matching — most common match checked first
  6. Flat tool signatures — individual args instead of wrapped Pydantic model

Authentication: Basic Auth (Access Key + Access Key Secret)
Base URL: https://api.gong.io/v2
Rate Limits: 3 calls/sec, 10,000 calls/day (default)
"""

import asyncio
import os
import json
import base64
import re
from typing import Optional, List, Dict, Any
from pathlib import Path
from datetime import datetime, timedelta, timezone

import httpx
from mcp.server.fastmcp import FastMCP

# ─── Initialize MCP Server ───────────────────────────────────────────────────

mcp = FastMCP("gong_mcp")

# ─── Constants ────────────────────────────────────────────────────────────────

API_BASE_URL = os.environ.get("GONG_BASE_URL", "https://api.gong.io") + "/v2"
CHARACTER_LIMIT = 25000
DEFAULT_PAGE_SIZE = 100
REQUEST_TIMEOUT = 120.0
CHUNK_DAYS = 10          # Optimized: 10-day chunks for concurrent fetching
RATE_LIMIT_CONCURRENT = 3  # Gong rate limit: 3 requests/second


# ─── Auth Helpers ─────────────────────────────────────────────────────────────

def _get_auth_header() -> Dict[str, str]:
    """
    Build the Basic Auth header from environment variables.

    Gong uses HTTP Basic Auth where:
      - Username = GONG_ACCESS_KEY
      - Password = GONG_ACCESS_KEY_SECRET
    """
    access_key = os.environ.get("GONG_ACCESS_KEY", "")
    access_secret = os.environ.get("GONG_ACCESS_KEY_SECRET", "")

    if not access_key or not access_secret:
        raise ValueError(
            "Missing Gong API credentials. Set GONG_ACCESS_KEY and "
            "GONG_ACCESS_KEY_SECRET environment variables."
        )

    credentials = f"{access_key}:{access_secret}"
    encoded = base64.b64encode(credentials.encode()).decode()

    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ─── Shared API Client ───────────────────────────────────────────────────────

async def _make_api_request(
    endpoint: str,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """
    Reusable function for all Gong API calls.

    Accepts an optional pre-existing httpx.AsyncClient for connection reuse.
    Falls back to creating a new client if none provided.
    """
    headers = _get_auth_header()
    url = f"{API_BASE_URL}/{endpoint.lstrip('/')}"

    if client:
        response = await client.request(
            method, url, headers=headers, json=body, params=params, timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    else:
        async with httpx.AsyncClient() as new_client:
            response = await new_client.request(
                method, url, headers=headers, json=body, params=params, timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()


async def _make_api_request_with_semaphore(
    endpoint: str,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    client: Optional[httpx.AsyncClient] = None,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> Dict[str, Any]:
    """API request with optional rate-limit semaphore."""
    if semaphore:
        async with semaphore:
            return await _make_api_request(endpoint, method, body, params, client)
    return await _make_api_request(endpoint, method, body, params, client)


async def _paginated_post(
    endpoint: str,
    body: Dict[str, Any],
    results_key: str,
    client: Optional[httpx.AsyncClient] = None,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> List[Dict[str, Any]]:
    """
    Handle Gong's cursor-based pagination for POST endpoints.

    Uses persistent client and optional semaphore for rate limiting.
    """
    all_results: List[Dict[str, Any]] = []
    cursor: Optional[str] = None

    while True:
        request_body = {**body}
        if cursor:
            request_body["cursor"] = cursor

        data = await _make_api_request_with_semaphore(
            endpoint, method="POST", body=request_body, client=client, semaphore=semaphore,
        )
        page_results = data.get(results_key, [])
        all_results.extend(page_results)

        records = data.get("records", {})
        cursor = records.get("cursor")

        if not cursor:
            break

    return all_results


# ─── Error Handling ───────────────────────────────────────────────────────────

def _handle_api_error(e: Exception) -> str:
    """Consistent error formatting across all tools."""
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        if status == 400:
            return "Error: Bad request. Check your filter parameters (dates must be ISO-8601 format)."
        elif status == 401:
            return "Error: Authentication failed. Verify your GONG_ACCESS_KEY and GONG_ACCESS_KEY_SECRET."
        elif status == 403:
            return "Error: Permission denied. Your API key may lack the required scope for this endpoint."
        elif status == 404:
            return "Error: Resource not found. Check that the call ID is correct."
        elif status == 429:
            retry_after = e.response.headers.get("Retry-After", "a few seconds")
            return f"Error: Rate limit exceeded. Wait {retry_after} before retrying. Default limits: 3 req/sec, 10K req/day."
        return f"Error: Gong API returned status {status}."
    elif isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out. Gong may be slow or your date range may be too broad. Try a narrower range."
    elif isinstance(e, ValueError):
        return f"Error: {str(e)}"
    return f"Error: Unexpected error: {type(e).__name__}: {str(e)}"


# ─── Formatting Helpers ──────────────────────────────────────────────────────

def _truncate_response(text: str) -> str:
    """Truncate response if it exceeds the character limit."""
    if len(text) > CHARACTER_LIMIT:
        return text[:CHARACTER_LIMIT] + "\n\n... [Response truncated. Use more specific filters or date ranges to reduce results.]"
    return text


def _format_datetime(iso_str: Optional[str]) -> str:
    """Convert ISO datetime to a human-readable format."""
    if not iso_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M %Z")
    except (ValueError, AttributeError):
        return iso_str


# ─── File Export Helpers ─────────────────────────────────────────────────────

def _sanitize_filename(name: str) -> str:
    """Convert a string into a safe filename."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'[\s_]+', '_', name)
    name = name.strip('_.')
    return name[:100] if name else "untitled"


def _chunk_date_range(from_dt: str, to_dt: str, chunk_days: int = CHUNK_DAYS) -> List[tuple]:
    """
    Split a date range into smaller chunks for concurrent fetching.

    Returns a list of (from_iso, to_iso) string tuples. Each chunk spans
    up to chunk_days days. Using 10-day chunks enables 3 concurrent
    requests for a 30-day range, matching the Gong rate limit.
    """
    start = datetime.fromisoformat(from_dt.replace("Z", "+00:00"))
    end = datetime.fromisoformat(to_dt.replace("Z", "+00:00"))
    chunks = []

    current = start
    while current < end:
        chunk_end = min(current + timedelta(days=chunk_days), end)
        chunks.append((current.isoformat(), chunk_end.isoformat()))
        current = chunk_end

    return chunks


def _format_transcript_markdown(
    call_meta: Dict[str, Any],
    resolved_transcript: List[Dict[str, Any]],
    parties: List[Dict[str, Any]],
) -> str:
    """Format a single call's transcript as a complete markdown document."""
    title = call_meta.get("title", "Untitled Call")
    call_id = call_meta.get("call_id", "N/A")
    scheduled = call_meta.get("scheduled", "N/A")
    duration_secs = call_meta.get("duration", 0)
    duration_min = duration_secs // 60
    duration_sec = duration_secs % 60
    matched_by = call_meta.get("matched_by", "N/A")
    gong_url = f"https://us-5730.app.gong.io/call?id={call_id}"

    lines = [
        f"# {title}",
        "",
        "## Call Details",
        f"- **Call ID**: {call_id}",
        f"- **Date**: {_format_datetime(scheduled)}",
        f"- **Duration**: {duration_min}m {duration_sec}s",
        f"- **Matched By**: {matched_by}",
        f"- **Gong Link**: {gong_url}",
        "",
        "## Participants",
    ]

    for p in parties:
        name = p.get("name", "Unknown")
        email = p.get("emailAddress", "")
        affiliation = p.get("affiliation", "")
        parts = [name]
        if email:
            parts.append(f"({email})")
        if affiliation:
            parts.append(f"[{affiliation}]")
        lines.append(f"- {' '.join(parts)}")

    lines.extend(["", "## Transcript", ""])

    current_speaker = None
    for entry in resolved_transcript:
        speaker = entry.get("speaker", "Unknown")
        text = entry.get("text", "")

        if speaker != current_speaker:
            if current_speaker is not None:
                lines.append("")
            lines.append(f"**{speaker}**:")
            current_speaker = speaker

        lines.append(f"{text}")

    return "\n".join(lines)


# ─── Account Matching ────────────────────────────────────────────────────────

def _matches_account(call: Dict[str, Any], account_filter_lower: str) -> Optional[str]:
    """
    Check if a call matches the account filter. Returns match reason or None.

    Optimized: checks title first (most common match, cheapest to evaluate),
    then CRM context, then participant email/company.
    """
    # Tier 1 — call title (cheapest check)
    call_title = (call.get("metaData", {}).get("title") or "").lower()
    if account_filter_lower in call_title:
        return "call_title"

    # Tier 2 — CRM Account context
    for ctx in call.get("context", []):
        for obj in ctx.get("objects", []):
            if obj.get("objectType") == "Account":
                for f in obj.get("fields", []):
                    fname = str(f.get("name") or "").lower()
                    fvalue = str(f.get("value") or "").lower()
                    if fname in ("name", "website") and account_filter_lower in fvalue:
                        return f"crm_account_{fname}"

    # Tier 3 — participant email / company
    for party in call.get("parties", []):
        email = str(party.get("emailAddress") or "").lower()
        company = str(party.get("company") or "").lower()
        if account_filter_lower in email:
            return "participant_email"
        if account_filter_lower in company:
            return "participant_company"

    return None


# ─── Concurrent Discovery ────────────────────────────────────────────────────

async def _discover_chunk(
    client: httpx.AsyncClient,
    chunk_from: str,
    chunk_to: str,
    content_selector: Dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> List[Dict[str, Any]]:
    """Fetch all calls in one date chunk via paginated extensive endpoint."""
    all_calls: List[Dict[str, Any]] = []
    cursor: Optional[str] = None

    while True:
        request_body: Dict[str, Any] = {
            "filter": {
                "fromDateTime": chunk_from,
                "toDateTime": chunk_to,
            },
            "contentSelector": content_selector,
        }
        if cursor:
            request_body["cursor"] = cursor

        data = await _make_api_request_with_semaphore(
            "calls/extensive", method="POST", body=request_body,
            client=client, semaphore=semaphore,
        )
        all_calls.extend(data.get("calls", []))
        cursor = data.get("records", {}).get("cursor")
        if not cursor:
            break

    return all_calls


async def _discover_calls_concurrent(
    client: httpx.AsyncClient,
    from_dt: str,
    to_dt: str,
    content_selector: Dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> List[Dict[str, Any]]:
    """
    Discover calls across date range using concurrent chunk fetching.

    Splits the range into CHUNK_DAYS-sized chunks and fetches them
    in parallel, respecting the rate limit via semaphore.
    """
    date_chunks = _chunk_date_range(from_dt, to_dt)

    tasks = [
        _discover_chunk(client, chunk_from, chunk_to, content_selector, semaphore)
        for chunk_from, chunk_to in date_chunks
    ]
    chunk_results = await asyncio.gather(*tasks)
    return [call for chunk in chunk_results for call in chunk]


# ─── Tool Definitions ────────────────────────────────────────────────────────

@mcp.tool(
    name="gong_list_calls",
    annotations={
        "title": "List Gong Calls",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def gong_list_calls(
    from_date_time: str,
    to_date_time: str,
    workspace_id: Optional[str] = None,
    cursor: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """
    List calls from Gong within a date range.

    Retrieves basic call records (ID, title, scheduled time, duration, URL)
    for calls that occurred between from_date_time and to_date_time.
    Supports pagination via cursor.

    Args:
        from_date_time: Start of date range in ISO-8601 format (e.g., '2025-01-01T00:00:00Z'). Required.
        to_date_time: End of date range in ISO-8601 format (exclusive). Required.
        workspace_id: Optional workspace ID to filter calls by a specific Gong workspace.
        cursor: Pagination cursor from a previous response. Omit for the first page.
        response_format: Output format — 'markdown' (human-readable) or 'json' (machine-readable).

    Endpoint: GET /v2/calls
    Required scope: api:calls:read:basic
    """
    try:
        query_params: Dict[str, Any] = {
            "fromDateTime": from_date_time,
            "toDateTime": to_date_time,
        }
        if workspace_id:
            query_params["workspaceId"] = workspace_id
        if cursor:
            query_params["cursor"] = cursor

        data = await _make_api_request("calls", method="GET", params=query_params)

        calls = data.get("calls", [])
        records = data.get("records", {})
        total = records.get("totalRecords", len(calls))
        next_cursor = records.get("cursor")

        if not calls:
            return "No calls found in the specified date range."

        if response_format.lower() == "markdown":
            lines = [
                f"# Gong Calls ({from_date_time} to {to_date_time})",
                f"Showing {len(calls)} of {total} calls",
                "",
            ]
            for call in calls:
                title = call.get("title", "Untitled")
                call_id = call.get("id", "N/A")
                scheduled = _format_datetime(call.get("scheduled"))
                duration = call.get("duration", 0)
                url = call.get("url", "")
                lines.append(f"## {title}")
                lines.append(f"- **Call ID**: {call_id}")
                lines.append(f"- **Scheduled**: {scheduled}")
                lines.append(f"- **Duration**: {duration}s ({duration // 60}m {duration % 60}s)")
                if url:
                    lines.append(f"- **URL**: {url}")
                lines.append("")

            if next_cursor:
                lines.append(f"*More results available. Use cursor: `{next_cursor}`*")

            return _truncate_response("\n".join(lines))
        else:
            result = {
                "total_records": total,
                "cursor": next_cursor,
                "count": len(calls),
                "calls": calls,
            }
            return _truncate_response(json.dumps(result, indent=2))

    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="gong_get_call",
    annotations={
        "title": "Get Gong Call Details",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def gong_get_call(
    call_id: str,
    response_format: str = "markdown",
) -> str:
    """
    Retrieve detailed metadata for a specific Gong call by ID.

    Returns call details including title, participants, scheduled time,
    duration, media info, and CRM associations.

    Args:
        call_id: Gong's unique numeric call ID (up to 20 digits). Required.
        response_format: Output format — 'markdown' or 'json'.

    Endpoint: GET /v2/calls/{id}
    Required scope: api:calls:read:basic
    """
    try:
        data = await _make_api_request(f"calls/{call_id}", method="GET")

        call = data.get("call", data)

        if response_format.lower() == "markdown":
            title = call.get("title", "Untitled")
            lines = [
                f"# {title}",
                f"- **Call ID**: {call.get('id', 'N/A')}",
                f"- **Scheduled**: {_format_datetime(call.get('scheduled'))}",
                f"- **Started**: {_format_datetime(call.get('started'))}",
                f"- **Duration**: {call.get('duration', 0)}s",
                f"- **Direction**: {call.get('direction', 'N/A')}",
                f"- **Language**: {call.get('language', 'N/A')}",
            ]
            url = call.get("url")
            if url:
                lines.append(f"- **URL**: {url}")

            parties = call.get("parties", [])
            if parties:
                lines.append("")
                lines.append("## Participants")
                for party in parties:
                    name = party.get("name", "Unknown")
                    email = party.get("emailAddress", "N/A")
                    affiliation = party.get("affiliation", "N/A")
                    lines.append(f"- {name} ({email}) - {affiliation}")

            return "\n".join(lines)
        else:
            return json.dumps(call, indent=2)

    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="gong_get_transcripts",
    annotations={
        "title": "Get Gong Call Transcripts",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def gong_get_transcripts(
    from_date_time: str,
    to_date_time: str,
    call_ids: Optional[List[str]] = None,
    workspace_id: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """
    Retrieve call transcripts from Gong within a date range.

    Returns full transcript text for calls, with optional filtering by
    specific call IDs. Handles pagination automatically.

    Args:
        from_date_time: Start of date range in ISO-8601 format. Required.
        to_date_time: End of date range in ISO-8601 format (exclusive). Required.
        call_ids: Optional list of specific call IDs to retrieve transcripts for.
        workspace_id: Optional workspace ID filter.
        response_format: Output format — 'markdown' or 'json'.

    Endpoint: POST /v2/calls/transcript
    Required scope: api:calls:read:transcript
    """
    try:
        filter_body: Dict[str, Any] = {
            "filter": {
                "fromDateTime": from_date_time,
                "toDateTime": to_date_time,
            }
        }
        if call_ids:
            filter_body["filter"]["callIds"] = call_ids
        if workspace_id:
            filter_body["filter"]["workspaceId"] = workspace_id

        async with httpx.AsyncClient() as client:
            transcripts = await _paginated_post(
                "calls/transcript", filter_body, "callTranscripts", client=client,
            )

        if not transcripts:
            return "No transcripts found for the specified filters."

        if response_format.lower() == "markdown":
            lines = [
                f"# Gong Call Transcripts",
                f"Retrieved {len(transcripts)} transcript(s)",
                "",
            ]
            for t in transcripts:
                cid = t.get("callId", "Unknown")
                lines.append(f"## Call ID: {cid}")
                lines.append("")

                transcript_entries = t.get("transcript", [])
                for entry in transcript_entries:
                    speaker = entry.get("speakerId", "Unknown Speaker")
                    topic = entry.get("topic")
                    if topic:
                        lines.append(f"**[Topic: {topic}]**")

                    sentences = entry.get("sentences", [])
                    for sentence in sentences:
                        text = sentence.get("text", "")
                        lines.append(f"**{speaker}**: {text}")

                lines.append("")
                lines.append("---")
                lines.append("")

            return _truncate_response("\n".join(lines))
        else:
            result = {
                "total_transcripts": len(transcripts),
                "transcripts": transcripts,
            }
            return _truncate_response(json.dumps(result, indent=2))

    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="gong_list_calls_extensive",
    annotations={
        "title": "List Gong Calls (Extensive)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def gong_list_calls_extensive(
    from_date_time: str,
    to_date_time: str,
    call_ids: Optional[List[str]] = None,
    response_format: str = "markdown",
) -> str:
    """
    Retrieve detailed call data from Gong, including participants,
    interaction stats, content info, and CRM context.

    Optimized: Uses concurrent chunk fetching for large date ranges.

    Args:
        from_date_time: Start of date range in ISO-8601 format. Required.
        to_date_time: End of date range in ISO-8601 format (exclusive). Required.
        call_ids: Optional list of specific call IDs to retrieve.
        response_format: Output format — 'markdown' or 'json'.

    Endpoint: POST /v2/calls/extensive
    Required scope: api:calls:read:extensive
    """
    try:
        content_selector = {
            "context": "Extended",
            "exposedFields": {
                "parties": True,
            },
        }

        if call_ids:
            # Specific call IDs: no chunking needed
            filter_body: Dict[str, Any] = {
                "filter": {
                    "fromDateTime": from_date_time,
                    "toDateTime": to_date_time,
                    "callIds": call_ids,
                },
                "contentSelector": content_selector,
            }
            async with httpx.AsyncClient() as client:
                calls = await _paginated_post(
                    "calls/extensive", filter_body, "calls", client=client,
                )
        else:
            # Full date range: use concurrent fetching
            semaphore = asyncio.Semaphore(RATE_LIMIT_CONCURRENT)
            async with httpx.AsyncClient() as client:
                calls = await _discover_calls_concurrent(
                    client, from_date_time, to_date_time,
                    content_selector, semaphore,
                )

        if not calls:
            return "No calls found for the specified filters."

        if response_format.lower() == "markdown":
            lines = [
                f"# Gong Calls (Extensive)",
                f"Retrieved {len(calls)} call(s)",
                "",
            ]
            for call in calls:
                meta = call.get("metaData", {})
                title = meta.get("title", "Untitled")
                call_id = meta.get("id", "N/A")
                scheduled = _format_datetime(meta.get("scheduled"))
                duration = meta.get("duration", 0)

                lines.append(f"## {title}")
                lines.append(f"- **Call ID**: {call_id}")
                lines.append(f"- **Scheduled**: {scheduled}")
                lines.append(f"- **Duration**: {duration}s ({duration // 60}m {duration % 60}s)")

                parties = call.get("parties", [])
                if parties:
                    lines.append("- **Participants**:")
                    for p in parties:
                        name = p.get("name", "Unknown")
                        email = p.get("emailAddress", "")
                        affiliation = p.get("affiliation", "")
                        lines.append(f"  - {name} ({email}) [{affiliation}]")

                context_list = call.get("context", [])
                for ctx in context_list:
                    for obj in ctx.get("objects", []):
                        obj_type = obj.get("objectType", "")
                        if obj_type == "Account":
                            fields = obj.get("fields", [])
                            for f in fields:
                                if f.get("name") == "Name":
                                    lines.append(f"- **CRM Account**: {f.get('value', 'N/A')}")

                lines.append("")

            return _truncate_response("\n".join(lines))
        else:
            result = {
                "total_calls": len(calls),
                "calls": calls,
            }
            return _truncate_response(json.dumps(result, indent=2))

    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="gong_get_account_transcripts",
    annotations={
        "title": "Get Transcripts by Account",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def gong_get_account_transcripts(
    from_date_time: str,
    to_date_time: str,
    account_filter: str,
    response_format: str = "json",
) -> str:
    """
    Workflow tool: Find all calls for a specific account and retrieve transcripts.

    Optimized V2: Uses concurrent chunk fetching + Basic context (significantly faster).

    Steps:
      1. Concurrently fetches calls across 10-day date chunks
      2. Filters by title, CRM context, and participant matching (case-insensitive)
      3. Retrieves transcripts for all matched call IDs

    Args:
        from_date_time: Start of date range in ISO-8601 format. Required.
        to_date_time: End of date range in ISO-8601 format (exclusive). Required.
        account_filter: Filter string to match against call titles, CRM account names,
            and participant email domains. Examples: 'acme.com', 'Acme Corp'. Case-insensitive.
        response_format: Output format — 'markdown' or 'json'.
    """
    if not account_filter or not account_filter.strip():
        return "Error: account_filter cannot be empty."

    try:
        semaphore = asyncio.Semaphore(RATE_LIMIT_CONCURRENT)
        content_selector = {
            "context": "Basic",
            "exposedFields": {"parties": True},
        }

        async with httpx.AsyncClient() as client:
            # Step 1: Concurrent discovery
            all_calls = await _discover_calls_concurrent(
                client, from_date_time, to_date_time,
                content_selector, semaphore,
            )

            if not all_calls:
                return f"No calls found in the date range {from_date_time} to {to_date_time}."

            # Step 2: Filter by account
            account_filter_lower = account_filter.strip().lower()
            matched_calls: List[Dict[str, Any]] = []

            for call in all_calls:
                matched_by = _matches_account(call, account_filter_lower)

                if matched_by:
                    speaker_map: Dict[str, Dict[str, str]] = {}
                    parties = call.get("parties", [])
                    for party in parties:
                        sid = str(party.get("speakerId", ""))
                        if sid:
                            speaker_map[sid] = {
                                "name": party.get("name", "Unknown"),
                                "email": party.get("emailAddress", ""),
                                "affiliation": party.get("affiliation", ""),
                            }

                    matched_participants = []
                    if "participant" in matched_by:
                        for party in parties:
                            email = str(party.get("emailAddress") or "").lower()
                            company = str(party.get("company") or "").lower()
                            if account_filter_lower in email or account_filter_lower in company:
                                display = party.get("name", party.get("emailAddress", "Unknown"))
                                email_display = party.get("emailAddress", "")
                                matched_participants.append(
                                    f"{display} ({email_display})" if email_display else display
                                )

                    meta = call.get("metaData", {})
                    matched_calls.append({
                        "call_id": str(meta.get("id", "")),
                        "title": meta.get("title", "Untitled"),
                        "scheduled": meta.get("scheduled"),
                        "duration": meta.get("duration", 0),
                        "matched_by": matched_by,
                        "matched_participants": matched_participants,
                        "speaker_map": speaker_map,
                        "parties": parties,
                    })

            if not matched_calls:
                return (
                    f"No calls found matching '{account_filter}' in the date range. "
                    f"Scanned {len(all_calls)} total calls. "
                    f"Try a broader filter (e.g., just the email domain like 'acme.com')."
                )

            # Step 3: Retrieve transcripts
            matched_call_ids = [c["call_id"] for c in matched_calls]
            transcript_body: Dict[str, Any] = {
                "filter": {
                    "fromDateTime": from_date_time,
                    "toDateTime": to_date_time,
                    "callIds": matched_call_ids,
                }
            }
            transcripts = await _paginated_post(
                "calls/transcript", transcript_body, "callTranscripts",
                client=client, semaphore=semaphore,
            )

        # Build lookup and resolve speaker IDs to names
        transcript_map: Dict[str, Any] = {}
        for t in transcripts:
            transcript_map[str(t.get("callId", ""))] = t.get("transcript", [])

        for call in matched_calls:
            raw_transcript = transcript_map.get(call["call_id"], [])
            smap = call.get("speaker_map", {})

            resolved_transcript: List[Dict[str, Any]] = []
            for segment in raw_transcript:
                sid = str(segment.get("speakerId", ""))
                speaker_info = smap.get(sid, {})
                speaker_name = speaker_info.get("name", f"Speaker {sid}")
                speaker_email = speaker_info.get("email", "")

                for sentence in segment.get("sentences", []):
                    resolved_transcript.append({
                        "speaker": speaker_name,
                        "email": speaker_email,
                        "text": sentence.get("text", ""),
                        "start": sentence.get("start"),
                        "end": sentence.get("end"),
                    })

            call["transcript"] = raw_transcript
            call["resolvedTranscript"] = resolved_transcript

        if response_format.lower() == "markdown":
            lines = [
                f"# Transcripts for '{account_filter}'",
                f"Found {len(matched_calls)} matching calls out of {len(all_calls)} scanned",
                f"Date range: {from_date_time} to {to_date_time}",
                "",
            ]
            for call in matched_calls:
                lines.append(f"## {call['title']}")
                lines.append(f"- **Call ID**: {call['call_id']}")
                lines.append(f"- **Scheduled**: {_format_datetime(call.get('scheduled'))}")
                lines.append(f"- **Duration**: {call['duration']}s")
                lines.append(f"- **Matched By**: {call['matched_by']}")
                if call['matched_participants']:
                    lines.append(f"- **Matched Participants**: {', '.join(call['matched_participants'])}")
                lines.append("")

                resolved = call.get("resolvedTranscript", [])
                if resolved:
                    lines.append("### Transcript")
                    for entry in resolved:
                        speaker = entry.get("speaker", "Unknown")
                        text = entry.get("text", "")
                        lines.append(f"**{speaker}**: {text}")
                    lines.append("")
                else:
                    lines.append("*No transcript available for this call.*")
                    lines.append("")

                lines.append("---")
                lines.append("")

            return _truncate_response("\n".join(lines))
        else:
            output_calls = []
            for call in matched_calls:
                output_calls.append({
                    "call_id": call["call_id"],
                    "title": call["title"],
                    "scheduled": call["scheduled"],
                    "duration": call["duration"],
                    "matched_by": call["matched_by"],
                    "matched_participants": call["matched_participants"],
                    "participants": [
                        {"name": p.get("name"), "email": p.get("emailAddress"), "affiliation": p.get("affiliation")}
                        for p in call.get("parties", [])
                    ],
                    "resolvedTranscript": call.get("resolvedTranscript", []),
                })
            result = {
                "account_filter": account_filter,
                "date_range": {
                    "from": from_date_time,
                    "to": to_date_time,
                },
                "matched_calls": len(matched_calls),
                "total_calls_scanned": len(all_calls),
                "calls": output_calls,
            }
            return _truncate_response(json.dumps(result, indent=2))

    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="gong_export_account_transcripts",
    annotations={
        "title": "Export Account Transcripts to Files",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def gong_export_account_transcripts(
    from_date_time: str,
    to_date_time: str,
    account_filter: str,
    output_dir: str = "~/Documents/Transcripts",
) -> str:
    """
    Export full call transcripts for a specific account to markdown files.

    Optimized V2: Concurrent discovery significantly faster than V1.

    Output structure:
        {output_dir}/{account}/
            YYYY-MM-DD_{call_title}.md   — individual transcript per call
            _combined_transcripts.md     — all transcripts in one file

    Args:
        from_date_time: Start of date range in ISO-8601 format (e.g., '2025-01-01T00:00:00Z'). Required.
        to_date_time: End of date range in ISO-8601 format (exclusive). Required.
        account_filter: Account name, email domain, or keyword to match against CRM data,
            call titles, and participants. Examples: 'acme.com', 'Acme Corp'. Case-insensitive.
        output_dir: Base directory where transcript files will be saved. Defaults to ~/Documents/Transcripts.
    """
    if not account_filter or not account_filter.strip():
        return "Error: account_filter cannot be empty."

    try:
        semaphore = asyncio.Semaphore(RATE_LIMIT_CONCURRENT)
        content_selector = {
            "context": "Basic",
            "exposedFields": {"parties": True},
        }

        async with httpx.AsyncClient() as client:
            # ── Step 1: Concurrent discovery ──────────────────────────────
            all_calls = await _discover_calls_concurrent(
                client, from_date_time, to_date_time,
                content_selector, semaphore,
            )

            if not all_calls:
                return f"No calls found in the date range {from_date_time} to {to_date_time}."

            # ── Step 2: Filter by account ─────────────────────────────────
            account_filter_lower = account_filter.strip().lower()
            matched_calls: List[Dict[str, Any]] = []

            for call in all_calls:
                matched_by = _matches_account(call, account_filter_lower)

                if matched_by:
                    speaker_map: Dict[str, Dict[str, str]] = {}
                    parties = call.get("parties", [])
                    for party in parties:
                        sid = str(party.get("speakerId", ""))
                        if sid:
                            speaker_map[sid] = {
                                "name": party.get("name", "Unknown"),
                                "email": party.get("emailAddress", ""),
                                "affiliation": party.get("affiliation", ""),
                            }

                    matched_participants = []
                    if "participant" in matched_by:
                        for party in parties:
                            email = str(party.get("emailAddress") or "").lower()
                            company = str(party.get("company") or "").lower()
                            if account_filter_lower in email or account_filter_lower in company:
                                display = party.get("name", party.get("emailAddress", "Unknown"))
                                email_display = party.get("emailAddress", "")
                                matched_participants.append(
                                    f"{display} ({email_display})" if email_display else display
                                )

                    meta = call.get("metaData", {})
                    matched_calls.append({
                        "call_id": str(meta.get("id", "")),
                        "title": meta.get("title", "Untitled"),
                        "scheduled": meta.get("scheduled"),
                        "duration": meta.get("duration", 0),
                        "matched_by": matched_by,
                        "matched_participants": matched_participants,
                        "speaker_map": speaker_map,
                        "parties": parties,
                    })

            if not matched_calls:
                return (
                    f"No calls found matching '{account_filter}' in the date range. "
                    f"Scanned {len(all_calls)} total calls."
                )

            # ── Step 3: Retrieve transcripts ──────────────────────────────
            matched_call_ids = [c["call_id"] for c in matched_calls]
            transcript_body: Dict[str, Any] = {
                "filter": {
                    "fromDateTime": from_date_time,
                    "toDateTime": to_date_time,
                    "callIds": matched_call_ids,
                }
            }
            transcripts = await _paginated_post(
                "calls/transcript", transcript_body, "callTranscripts",
                client=client, semaphore=semaphore,
            )

        # ── Step 4: Resolve speaker IDs ───────────────────────────────────
        transcript_map: Dict[str, Any] = {}
        for t in transcripts:
            transcript_map[str(t.get("callId", ""))] = t.get("transcript", [])

        for call in matched_calls:
            raw_transcript = transcript_map.get(call["call_id"], [])
            smap = call.get("speaker_map", {})

            resolved_transcript: List[Dict[str, Any]] = []
            for segment in raw_transcript:
                sid = str(segment.get("speakerId", ""))
                speaker_info = smap.get(sid, {})
                speaker_name = speaker_info.get("name", f"Speaker {sid}")
                speaker_email = speaker_info.get("email", "")

                for sentence in segment.get("sentences", []):
                    resolved_transcript.append({
                        "speaker": speaker_name,
                        "email": speaker_email,
                        "text": sentence.get("text", ""),
                        "start": sentence.get("start"),
                        "end": sentence.get("end"),
                    })

            call["resolved_transcript"] = resolved_transcript

        # ── Step 5: Write files ───────────────────────────────────────────
        base_dir = Path(output_dir).expanduser()
        folder_name = _sanitize_filename(account_filter.strip())
        output_path = base_dir / folder_name
        output_path.mkdir(parents=True, exist_ok=True)

        written_files: List[Dict[str, Any]] = []
        combined_lines: List[str] = [
            f"# All Transcripts: {account_filter}",
            f"Date range: {from_date_time} to {to_date_time}",
            f"Total calls: {len(matched_calls)}",
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "---",
            "",
        ]

        matched_calls.sort(key=lambda c: c.get("scheduled") or "")

        for call in matched_calls:
            call_meta = {
                "title": call["title"],
                "call_id": call["call_id"],
                "scheduled": call.get("scheduled"),
                "duration": call.get("duration", 0),
                "matched_by": call.get("matched_by", ""),
            }

            content = _format_transcript_markdown(
                call_meta,
                call.get("resolved_transcript", []),
                call.get("parties", []),
            )

            date_str = "undated"
            if call.get("scheduled"):
                try:
                    dt = datetime.fromisoformat(
                        call["scheduled"].replace("Z", "+00:00")
                    )
                    date_str = dt.strftime("%Y-%m-%d")
                except (ValueError, AttributeError):
                    pass

            safe_title = _sanitize_filename(call["title"])
            filename = f"{date_str}_{safe_title}.md"
            filepath = output_path / filename

            filepath.write_text(content, encoding="utf-8")

            written_files.append({
                "filename": filename,
                "path": str(filepath),
                "call_id": call["call_id"],
                "title": call["title"],
                "date": date_str,
                "size_bytes": len(content.encode("utf-8")),
                "transcript_entries": len(call.get("resolved_transcript", [])),
            })

            combined_lines.append(content)
            combined_lines.extend(["", "---", ""])

        combined_path = output_path / "_combined_transcripts.md"
        combined_content = "\n".join(combined_lines)
        combined_path.write_text(combined_content, encoding="utf-8")

        # ── Step 6: Return summary ────────────────────────────────────────
        total_size = sum(f["size_bytes"] for f in written_files)
        total_entries = sum(f["transcript_entries"] for f in written_files)

        summary_lines = [
            f"# Export Complete: {account_filter}",
            "",
            f"- **Calls exported**: {len(written_files)}",
            f"- **Total calls scanned**: {len(all_calls)}",
            f"- **Total transcript entries**: {total_entries}",
            f"- **Total size**: {total_size / 1024:.1f} KB",
            f"- **Output folder**: {output_path}",
            "",
            "## Files Created",
            "",
        ]

        for f in written_files:
            summary_lines.append(
                f"- **{f['title']}** ({f['date']}) — "
                f"{f['transcript_entries']} entries, "
                f"{f['size_bytes'] / 1024:.1f} KB"
            )
            summary_lines.append(f"  `{f['path']}`")

        summary_lines.extend([
            "",
            f"- **Combined file**: `{combined_path}`"
            f" ({len(combined_content.encode('utf-8')) / 1024:.1f} KB)",
        ])

        return "\n".join(summary_lines)

    except Exception as e:
        return _handle_api_error(e)


# ─── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
