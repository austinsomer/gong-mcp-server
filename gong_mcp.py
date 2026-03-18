#!/usr/bin/env python3
"""
MCP Server for Gong API v2.

Provides tools to interact with Gong's conversation intelligence platform,
including listing calls, retrieving call metadata, and pulling transcripts.
Supports filtering by account via participant email domain matching.

Authentication: Basic Auth (Access Key + Access Key Secret)
Base URL: https://api.gong.io/v2
Rate Limits: 3 calls/sec, 10,000 calls/day (default)
"""

import os
import json
import base64
import re
from typing import Optional, List, Dict, Any
from enum import Enum
from pathlib import Path
from datetime import datetime, timedelta, timezone

import httpx
from pydantic import BaseModel, Field, field_validator, ConfigDict
from mcp.server.fastmcp import FastMCP

# ─── Initialize MCP Server ───────────────────────────────────────────────────

mcp = FastMCP("gong_mcp")

# ─── Constants ────────────────────────────────────────────────────────────────

API_BASE_URL = os.environ.get("GONG_BASE_URL", "https://api.gong.io") + "/v2"
CHARACTER_LIMIT = 25000
DEFAULT_PAGE_SIZE = 100
REQUEST_TIMEOUT = 120.0


# ─── Enums ────────────────────────────────────────────────────────────────────

class ResponseFormat(str, Enum):
    """Output format for tool responses."""
    MARKDOWN = "markdown"
    JSON = "json"


# ─── Auth Helpers ─────────────────────────────────────────────────────────────

def _get_auth_header() -> Dict[str, str]:
    """
    Build the Basic Auth header from environment variables.

    Gong uses HTTP Basic Auth where:
      - Username = GONG_ACCESS_KEY
      - Password = GONG_ACCESS_KEY_SECRET

    The credentials are base64-encoded as 'key:secret' and sent
    in the Authorization header.
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
) -> Dict[str, Any]:
    """
    Reusable function for all Gong API calls.

    Handles auth header injection, timeout, and basic error raising.
    All Gong v2 endpoints are relative to https://api.gong.io/v2.
    """
    headers = _get_auth_header()
    url = f"{API_BASE_URL}/{endpoint.lstrip('/')}"

    async with httpx.AsyncClient() as client:
        response = await client.request(
            method,
            url,
            headers=headers,
            json=body,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()


async def _paginated_post(
    endpoint: str,
    body: Dict[str, Any],
    results_key: str,
) -> List[Dict[str, Any]]:
    """
    Handle Gong's cursor-based pagination for POST endpoints.

    Gong POST endpoints (like /calls/transcript and /calls/extensive)
    return a 'records' object with 'cursor' for pagination. This helper
    follows the cursor until all pages are collected.

    Args:
        endpoint: The API path (e.g., 'calls/transcript')
        body: The JSON request body including filter params
        results_key: The top-level key in the response that holds the data
                     (e.g., 'callTranscripts' or 'calls')

    Returns:
        Aggregated list of all records across all pages.
    """
    all_results: List[Dict[str, Any]] = []
    cursor: Optional[str] = None

    while True:
        request_body = {**body}
        if cursor:
            request_body["cursor"] = cursor

        data = await _make_api_request(endpoint, method="POST", body=request_body)
        page_results = data.get(results_key, [])
        all_results.extend(page_results)

        records = data.get("records", {})
        cursor = records.get("cursor")
        total = records.get("totalRecords", len(all_results))

        # If no cursor returned, we've hit the last page
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


# ─── Pydantic Input Models ───────────────────────────────────────────────────

class ListCallsInput(BaseModel):
    """Input for listing calls by date range."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    from_date_time: str = Field(
        ...,
        description="Start of date range in ISO-8601 format (e.g., '2025-01-01T00:00:00Z'). Required.",
    )
    to_date_time: str = Field(
        ...,
        description="End of date range in ISO-8601 format (exclusive). Required.",
    )
    workspace_id: Optional[str] = Field(
        default=None,
        description="Optional workspace ID to filter calls by a specific Gong workspace.",
    )
    cursor: Optional[str] = Field(
        default=None,
        description="Pagination cursor from a previous response. Omit for the first page.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' (human-readable) or 'json' (machine-readable).",
    )


class GetCallInput(BaseModel):
    """Input for retrieving a specific call's details."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    call_id: str = Field(
        ...,
        description="Gong's unique numeric call ID (up to 20 digits).",
        min_length=1,
        max_length=20,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'.",
    )


class GetTranscriptsInput(BaseModel):
    """Input for pulling call transcripts."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    from_date_time: str = Field(
        ...,
        description="Start of date range in ISO-8601 format. Required.",
    )
    to_date_time: str = Field(
        ...,
        description="End of date range in ISO-8601 format (exclusive). Required.",
    )
    call_ids: Optional[List[str]] = Field(
        default=None,
        description="Optional list of specific call IDs to retrieve transcripts for. If provided, only these calls (within the date range) are returned.",
    )
    workspace_id: Optional[str] = Field(
        default=None,
        description="Optional workspace ID filter.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'.",
    )


class GetCallsExtensiveInput(BaseModel):
    """Input for retrieving detailed call data with metadata."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    from_date_time: str = Field(
        ...,
        description="Start of date range in ISO-8601 format. Required.",
    )
    to_date_time: str = Field(
        ...,
        description="End of date range in ISO-8601 format (exclusive). Required.",
    )
    call_ids: Optional[List[str]] = Field(
        default=None,
        description="Optional list of specific call IDs to retrieve.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'.",
    )


class GetAccountTranscriptsInput(BaseModel):
    """
    Input for the workflow tool that finds calls associated with
    an account and pulls their transcripts in one operation.
    """
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    from_date_time: str = Field(
        ...,
        description="Start of date range in ISO-8601 format. Required.",
    )
    to_date_time: str = Field(
        ...,
        description="End of date range in ISO-8601 format (exclusive). Required.",
    )
    account_filter: str = Field(
        ...,
        description=(
            "Filter string to match against participant email domains or names. "
            "Examples: 'acme.com' to match all @acme.com participants, or 'Acme Corp' "
            "to match against participant/company names. Case-insensitive."
        ),
        min_length=1,
        max_length=200,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'markdown' or 'json'. Defaults to JSON for this workflow tool.",
    )

    @field_validator("account_filter")
    @classmethod
    def validate_account_filter(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("account_filter cannot be empty.")
        return v.strip()


class ExportAccountTranscriptsInput(BaseModel):
    """
    Input for exporting full account transcripts to files.

    Unlike gong_get_account_transcripts which returns text (subject to
    character limits), this tool writes complete transcripts directly
    to disk as markdown files with no truncation.
    """
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    from_date_time: str = Field(
        ...,
        description="Start of date range in ISO-8601 format (e.g., '2025-01-01T00:00:00Z'). Required.",
    )
    to_date_time: str = Field(
        ...,
        description="End of date range in ISO-8601 format (exclusive). Required.",
    )
    account_filter: str = Field(
        ...,
        description=(
            "Filter string to match against CRM account names, call titles, "
            "and participant email domains. Examples: 'acme.com', 'Acme Corp', "
            "'tracelink'. Case-insensitive."
        ),
        min_length=1,
        max_length=200,
    )
    output_dir: str = Field(
        default="~/Documents/Transcripts",
        description=(
            "Base directory where transcript files will be saved. Defaults to ~/Documents/Transcripts. "
            "A subfolder named after the account/company is created automatically."
        ),
    )

    @field_validator("account_filter")
    @classmethod
    def validate_account_filter(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("account_filter cannot be empty.")
        return v.strip()


# ─── File Export Helpers ─────────────────────────────────────────────────────

def _sanitize_filename(name: str) -> str:
    """Convert a string into a safe filename."""
    # Replace common separators with underscores
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Collapse multiple underscores/spaces
    name = re.sub(r'[\s_]+', '_', name)
    # Strip leading/trailing underscores and dots
    name = name.strip('_.')
    # Truncate to reasonable length
    return name[:100] if name else "untitled"


def _chunk_date_range(from_dt: str, to_dt: str, chunk_days: int = 14) -> List[tuple]:
    """
    Split a date range into smaller chunks to keep API calls fast.

    Returns a list of (from_iso, to_iso) string tuples. Each chunk spans
    up to chunk_days days. This prevents timeouts when scanning large
    date ranges via Gong's extensive calls endpoint.
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
    """
    Format a single call's transcript as a complete markdown document.

    Includes metadata header, participant list, and full speaker-labeled
    transcript with no character limit.
    """
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
                lines.append("")  # Blank line between speaker changes
            lines.append(f"**{speaker}**:")
            current_speaker = speaker

        lines.append(f"{text}")

    return "\n".join(lines)


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
async def gong_list_calls(params: ListCallsInput) -> str:
    """
    List calls from Gong within a date range.

    Retrieves basic call records (ID, title, scheduled time, duration, URL)
    for calls that occurred between from_date_time and to_date_time.
    Supports pagination via cursor.

    Endpoint: GET /v2/calls
    Required scope: api:calls:read:basic

    Args:
        params (ListCallsInput):
            - from_date_time (str): ISO-8601 start date
            - to_date_time (str): ISO-8601 end date (exclusive)
            - workspace_id (Optional[str]): Filter by workspace
            - cursor (Optional[str]): Pagination cursor
            - response_format (ResponseFormat): 'markdown' or 'json'

    Returns:
        str: Call list with pagination info. JSON schema:
        {
            "total_records": int,
            "cursor": str | null,
            "calls": [
                {
                    "id": str,
                    "title": str,
                    "scheduled": str,
                    "started": str,
                    "duration": int,
                    "url": str,
                    "direction": str
                }
            ]
        }
    """
    try:
        query_params: Dict[str, Any] = {
            "fromDateTime": params.from_date_time,
            "toDateTime": params.to_date_time,
        }
        if params.workspace_id:
            query_params["workspaceId"] = params.workspace_id
        if params.cursor:
            query_params["cursor"] = params.cursor

        data = await _make_api_request("calls", method="GET", params=query_params)

        calls = data.get("calls", [])
        records = data.get("records", {})
        total = records.get("totalRecords", len(calls))
        next_cursor = records.get("cursor")

        if not calls:
            return "No calls found in the specified date range."

        if params.response_format == ResponseFormat.MARKDOWN:
            lines = [
                f"# Gong Calls ({params.from_date_time} to {params.to_date_time})",
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
async def gong_get_call(params: GetCallInput) -> str:
    """
    Retrieve detailed metadata for a specific Gong call by ID.

    Returns call details including title, participants, scheduled time,
    duration, media info, and CRM associations.

    Endpoint: GET /v2/calls/{id}
    Required scope: api:calls:read:basic

    Args:
        params (GetCallInput):
            - call_id (str): Gong's numeric call identifier
            - response_format (ResponseFormat): 'markdown' or 'json'

    Returns:
        str: Call details. JSON schema:
        {
            "id": str,
            "title": str,
            "scheduled": str,
            "started": str,
            "duration": int,
            "url": str,
            "parties": [...],
            "media": str,
            "language": str,
            "direction": str,
            "scope": str
        }
    """
    try:
        data = await _make_api_request(f"calls/{params.call_id}", method="GET")

        call = data.get("call", data)

        if params.response_format == ResponseFormat.MARKDOWN:
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
async def gong_get_transcripts(params: GetTranscriptsInput) -> str:
    """
    Retrieve call transcripts from Gong within a date range.

    Returns full transcript text for calls, with optional filtering by
    specific call IDs. Handles pagination automatically to collect all
    matching transcripts.

    Endpoint: POST /v2/calls/transcript
    Required scope: api:calls:read:transcript

    Args:
        params (GetTranscriptsInput):
            - from_date_time (str): ISO-8601 start date
            - to_date_time (str): ISO-8601 end date (exclusive)
            - call_ids (Optional[List[str]]): Specific call IDs to retrieve
            - workspace_id (Optional[str]): Workspace filter
            - response_format (ResponseFormat): 'markdown' or 'json'

    Returns:
        str: Transcripts data. JSON schema:
        {
            "total_transcripts": int,
            "transcripts": [
                {
                    "callId": str,
                    "transcript": [
                        {
                            "speakerId": str,
                            "topic": str,
                            "sentences": [
                                {
                                    "start": float,
                                    "end": float,
                                    "text": str
                                }
                            ]
                        }
                    ]
                }
            ]
        }
    """
    try:
        filter_body: Dict[str, Any] = {
            "filter": {
                "fromDateTime": params.from_date_time,
                "toDateTime": params.to_date_time,
            }
        }
        if params.call_ids:
            filter_body["filter"]["callIds"] = params.call_ids
        if params.workspace_id:
            filter_body["filter"]["workspaceId"] = params.workspace_id

        transcripts = await _paginated_post(
            "calls/transcript", filter_body, "callTranscripts"
        )

        if not transcripts:
            return "No transcripts found for the specified filters."

        if params.response_format == ResponseFormat.MARKDOWN:
            lines = [
                f"# Gong Call Transcripts",
                f"Retrieved {len(transcripts)} transcript(s)",
                "",
            ]
            for t in transcripts:
                call_id = t.get("callId", "Unknown")
                lines.append(f"## Call ID: {call_id}")
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
async def gong_list_calls_extensive(params: GetCallsExtensiveInput) -> str:
    """
    Retrieve detailed call data from Gong, including participants,
    interaction stats, content info, and CRM context.

    This endpoint returns richer data than gong_list_calls, including
    participant details (names, emails, affiliations) which enables
    filtering by account/company. Handles pagination automatically.

    Endpoint: POST /v2/calls/extensive
    Required scope: api:calls:read:extensive

    Args:
        params (GetCallsExtensiveInput):
            - from_date_time (str): ISO-8601 start date
            - to_date_time (str): ISO-8601 end date (exclusive)
            - call_ids (Optional[List[str]]): Specific call IDs
            - response_format (ResponseFormat): 'markdown' or 'json'

    Returns:
        str: Detailed call data. JSON schema:
        {
            "total_calls": int,
            "calls": [
                {
                    "metaData": { "id": str, "title": str, ... },
                    "parties": [
                        { "name": str, "emailAddress": str, "affiliation": str }
                    ],
                    "content": { ... },
                    "interaction": { ... },
                    "context": [ ... ]
                }
            ]
        }
    """
    try:
        filter_body: Dict[str, Any] = {
            "filter": {
                "fromDateTime": params.from_date_time,
                "toDateTime": params.to_date_time,
            },
            "contentSelector": {
                "context": "Extended",
                "contextTiming": ["Now", "TimeOfCall"],
                "exposedFields": {
                    "parties": True,
                },
            },
        }
        if params.call_ids:
            filter_body["filter"]["callIds"] = params.call_ids

        calls = await _paginated_post("calls/extensive", filter_body, "calls")

        if not calls:
            return "No calls found for the specified filters."

        if params.response_format == ResponseFormat.MARKDOWN:
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

                # Show CRM context if available
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
async def gong_get_account_transcripts(params: GetAccountTranscriptsInput) -> str:
    """
    Workflow tool: Find all calls associated with a specific account
    and retrieve their transcripts in one operation.

    Since Gong's API does not support filtering calls by account name
    directly, this tool performs a two-step workflow:

    1. Pulls all calls (extensive) in the date range with participant data
    2. Filters calls where any participant's email domain or name matches
       the account_filter string (case-insensitive)
    3. Retrieves transcripts for all matching call IDs

    This is the recommended tool when you need transcripts for a specific
    company/account.

    Args:
        params (GetAccountTranscriptsInput):
            - from_date_time (str): ISO-8601 start date
            - to_date_time (str): ISO-8601 end date (exclusive)
            - account_filter (str): Email domain (e.g., 'acme.com') or
              company name to match against participants. Case-insensitive.
            - response_format (ResponseFormat): 'markdown' or 'json'

    Returns:
        str: Matched calls and their transcripts. JSON schema:
        {
            "account_filter": str,
            "date_range": { "from": str, "to": str },
            "matched_calls": int,
            "total_calls_scanned": int,
            "calls": [
                {
                    "call_id": str,
                    "title": str,
                    "scheduled": str,
                    "duration": int,
                    "matched_participants": [str],
                    "transcript": [...]
                }
            ]
        }
    """
    try:
        # Step 1: Discovery via POST /v2/calls/extensive
        # Uses context: "Extended" to get CRM Account objects for matching
        filter_body: Dict[str, Any] = {
            "filter": {
                "fromDateTime": params.from_date_time,
                "toDateTime": params.to_date_time,
            },
            "contentSelector": {
                "context": "Extended",
                "contextTiming": ["Now", "TimeOfCall"],
                "exposedFields": {
                    "parties": True,
                },
            },
        }

        all_calls = await _paginated_post("calls/extensive", filter_body, "calls")

        if not all_calls:
            return f"No calls found in the date range {params.from_date_time} to {params.to_date_time}."

        # Step 2: Filter by account using CRM context + participant fallback
        account_filter_lower = params.account_filter.lower()
        matched_calls: List[Dict[str, Any]] = []

        for call in all_calls:
            matched_by: Optional[str] = None
            matched_participants: List[str] = []

            # Build speaker map: speakerId -> {name, email}
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

            # Primary match: CRM Account objects in context[]
            context_list = call.get("context", [])
            for ctx in context_list:
                for obj in ctx.get("objects", []):
                    obj_type = obj.get("objectType", "")
                    if obj_type == "Account":
                        fields = obj.get("fields", [])
                        for f in fields:
                            fname = str(f.get("name") or "").lower()
                            fvalue = str(f.get("value") or "").lower()
                            if fname in ("name", "website") and account_filter_lower in fvalue:
                                matched_by = f"crm_account_{fname}"

            # Secondary match: call title contains account name
            if not matched_by:
                call_title = (call.get("metaData", {}).get("title") or "").lower()
                if account_filter_lower in call_title:
                    matched_by = "call_title"

            # Fallback match: participant email domain or company name
            if not matched_by:
                for party in parties:
                    email = str(party.get("emailAddress") or "").lower()
                    name = str(party.get("name") or "").lower()
                    company = str(party.get("company") or "").lower()

                    if (
                        account_filter_lower in email
                        or account_filter_lower in company
                    ):
                        matched_by = "participant_email_domain" if account_filter_lower in email else "participant_company"
                        display = party.get("name", party.get("emailAddress", "Unknown"))
                        email_display = party.get("emailAddress", "")
                        matched_participants.append(
                            f"{display} ({email_display})" if email_display else display
                        )

            if matched_by:
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
                f"No calls found matching '{params.account_filter}' in the date range. "
                f"Scanned {len(all_calls)} total calls. "
                f"Try a broader filter (e.g., just the email domain like 'acme.com')."
            )

        # Step 3: Retrieve transcripts for matched call IDs
        matched_call_ids = [c["call_id"] for c in matched_calls]

        transcript_body: Dict[str, Any] = {
            "filter": {
                "fromDateTime": params.from_date_time,
                "toDateTime": params.to_date_time,
                "callIds": matched_call_ids,
            }
        }

        transcripts = await _paginated_post(
            "calls/transcript", transcript_body, "callTranscripts"
        )

        # Build lookup: call_id -> raw transcript
        transcript_map: Dict[str, Any] = {}
        for t in transcripts:
            transcript_map[str(t.get("callId", ""))] = t.get("transcript", [])

        # Merge transcripts and resolve speaker IDs to names
        for call in matched_calls:
            raw_transcript = transcript_map.get(call["call_id"], [])
            smap = call.get("speaker_map", {})

            resolved_transcript: List[Dict[str, Any]] = []
            for segment in raw_transcript:
                sid = str(segment.get("speakerId", ""))
                speaker_info = smap.get(sid, {})
                speaker_name = speaker_info.get("name", f"Speaker {sid}")
                speaker_email = speaker_info.get("email", "")

                resolved_sentences = []
                for sentence in segment.get("sentences", []):
                    resolved_sentences.append({
                        "speaker": speaker_name,
                        "email": speaker_email,
                        "text": sentence.get("text", ""),
                        "start": sentence.get("start"),
                        "end": sentence.get("end"),
                    })
                resolved_transcript.extend(resolved_sentences)

            call["transcript"] = raw_transcript
            call["resolvedTranscript"] = resolved_transcript

        if params.response_format == ResponseFormat.MARKDOWN:
            lines = [
                f"# Transcripts for '{params.account_filter}'",
                f"Found {len(matched_calls)} matching calls out of {len(all_calls)} scanned",
                f"Date range: {params.from_date_time} to {params.to_date_time}",
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
            # Clean up internal fields before JSON output
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
                "account_filter": params.account_filter,
                "date_range": {
                    "from": params.from_date_time,
                    "to": params.to_date_time,
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
async def gong_export_account_transcripts(params: ExportAccountTranscriptsInput) -> str:
    """
    Export full call transcripts for a specific account to markdown files.

    This tool solves the truncation problem by writing transcripts directly
    to disk instead of returning them as text. It performs the same account
    matching as gong_get_account_transcripts (CRM context, call titles,
    participant emails) but outputs complete, untruncated transcript files.

    Output structure:
        {output_dir}/{account}_gong_transcripts/
            YYYY-MM-DD_{call_title}.md          # Individual transcript per call
            _combined_transcripts.md            # All transcripts in one file

    Each file includes call metadata (date, duration, participants, Gong link)
    followed by the full speaker-labeled transcript.

    Args:
        params (ExportAccountTranscriptsInput):
            - from_date_time (str): ISO-8601 start date
            - to_date_time (str): ISO-8601 end date (exclusive)
            - account_filter (str): Account name, email domain, or keyword
              to match against CRM data, call titles, and participants.
            - output_dir (str): Base directory for output (default: ~/Documents)

    Returns:
        str: Summary of exported files including paths, call count, and
        total transcript size. Does NOT return transcript content directly.
    """
    try:
        # ── Step 1: Discover calls via POST /v2/calls/extensive ──────────
        # Chunk the date range into 2-week windows to avoid timeouts
        # on large date ranges (each chunk is a separate paginated API call)
        date_chunks = _chunk_date_range(
            params.from_date_time, params.to_date_time, chunk_days=14
        )

        all_calls: List[Dict[str, Any]] = []
        for chunk_from, chunk_to in date_chunks:
            filter_body: Dict[str, Any] = {
                "filter": {
                    "fromDateTime": chunk_from,
                    "toDateTime": chunk_to,
                },
                "contentSelector": {
                    "context": "Extended",
                    "contextTiming": ["Now", "TimeOfCall"],
                    "exposedFields": {
                        "parties": True,
                    },
                },
            }
            chunk_calls = await _paginated_post("calls/extensive", filter_body, "calls")
            all_calls.extend(chunk_calls)

        if not all_calls:
            return f"No calls found in the date range {params.from_date_time} to {params.to_date_time}."

        # ── Step 2: Filter by account ────────────────────────────────────
        account_filter_lower = params.account_filter.lower()
        matched_calls: List[Dict[str, Any]] = []

        for call in all_calls:
            matched_by: Optional[str] = None
            matched_participants: List[str] = []

            # Build speaker map: speakerId -> {name, email, affiliation}
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

            # Primary match: CRM Account objects
            context_list = call.get("context", [])
            for ctx in context_list:
                for obj in ctx.get("objects", []):
                    obj_type = obj.get("objectType", "")
                    if obj_type == "Account":
                        fields = obj.get("fields", [])
                        for f in fields:
                            fname = str(f.get("name") or "").lower()
                            fvalue = str(f.get("value") or "").lower()
                            if fname in ("name", "website") and account_filter_lower in fvalue:
                                matched_by = f"crm_account_{fname}"

            # Secondary match: call title
            if not matched_by:
                call_title = (call.get("metaData", {}).get("title") or "").lower()
                if account_filter_lower in call_title:
                    matched_by = "call_title"

            # Fallback match: participant email domain or company
            if not matched_by:
                for party in parties:
                    email = str(party.get("emailAddress") or "").lower()
                    company = str(party.get("company") or "").lower()

                    if account_filter_lower in email or account_filter_lower in company:
                        matched_by = "participant_email_domain" if account_filter_lower in email else "participant_company"
                        display = party.get("name", party.get("emailAddress", "Unknown"))
                        email_display = party.get("emailAddress", "")
                        matched_participants.append(
                            f"{display} ({email_display})" if email_display else display
                        )

            if matched_by:
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
                f"No calls found matching '{params.account_filter}' in the date range. "
                f"Scanned {len(all_calls)} total calls. "
                f"Try a broader filter (e.g., just the domain like 'acme.com') "
                f"or check the account name spelling."
            )

        # ── Step 3: Retrieve transcripts ─────────────────────────────────
        matched_call_ids = [c["call_id"] for c in matched_calls]

        transcript_body: Dict[str, Any] = {
            "filter": {
                "fromDateTime": params.from_date_time,
                "toDateTime": params.to_date_time,
                "callIds": matched_call_ids,
            }
        }

        transcripts = await _paginated_post(
            "calls/transcript", transcript_body, "callTranscripts"
        )

        # Build lookup: call_id -> raw transcript
        transcript_map: Dict[str, Any] = {}
        for t in transcripts:
            transcript_map[str(t.get("callId", ""))] = t.get("transcript", [])

        # ── Step 4: Resolve speaker IDs to names ────────────────────────
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

        # ── Step 5: Write files ──────────────────────────────────────────
        base_dir = Path(params.output_dir).expanduser()
        folder_name = _sanitize_filename(params.account_filter)
        output_path = base_dir / folder_name
        output_path.mkdir(parents=True, exist_ok=True)

        written_files: List[Dict[str, Any]] = []
        combined_lines: List[str] = [
            f"# All Transcripts: {params.account_filter}",
            f"Date range: {params.from_date_time} to {params.to_date_time}",
            f"Total calls: {len(matched_calls)}",
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "---",
            "",
        ]

        # Sort calls by scheduled date
        matched_calls.sort(key=lambda c: c.get("scheduled") or "")

        for call in matched_calls:
            # Build the markdown content for this call
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

            # Determine filename from date + title
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

            # Append to combined file
            combined_lines.append(content)
            combined_lines.extend(["", "---", ""])

        # Write combined file
        combined_path = output_path / "_combined_transcripts.md"
        combined_content = "\n".join(combined_lines)
        combined_path.write_text(combined_content, encoding="utf-8")

        # ── Step 6: Return summary ───────────────────────────────────────
        total_size = sum(f["size_bytes"] for f in written_files)
        total_entries = sum(f["transcript_entries"] for f in written_files)

        summary_lines = [
            f"# Export Complete: {params.account_filter}",
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
                f"- **{f['title']}** ({f['date']}) - "
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
