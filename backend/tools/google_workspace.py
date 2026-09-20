"""Google Calendar + Gmail API execution (user OAuth access token).

Used by the Google Workspace agent; not a remote MCP server — same capabilities surface
(Calendar events CRUD, Gmail list/read/send/modify) driven by structured ops.
"""
from __future__ import annotations

import base64
import json
from email.message import EmailMessage
from typing import Any, Optional
from urllib.parse import quote

import httpx

_CAL = "https://www.googleapis.com/calendar/v3"
_GMAIL = "https://gmail.googleapis.com/gmail/v1"


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return {"raw": (resp.text or "")[:2000]}


def calendar_list(token: str) -> dict[str, Any]:
    r = httpx.get(
        f"{_CAL}/users/me/calendarList",
        headers=_h(token),
        params={"maxResults": 250},
        timeout=30.0,
    )
    return {"ok": r.is_success, "status": r.status_code, "body": _json(r) if r.content else {}}


def events_list(
    token: str,
    calendar_id: str = "primary",
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 25,
) -> dict[str, Any]:
    cid = quote(calendar_id, safe="@")
    params: dict[str, Any] = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": max(1, min(int(max_results or 25), 50)),
    }
    if time_min:
        params["timeMin"] = time_min
    if time_max:
        params["timeMax"] = time_max
    r = httpx.get(
        f"{_CAL}/calendars/{cid}/events",
        headers=_h(token),
        params=params,
        timeout=30.0,
    )
    return {"ok": r.is_success, "status": r.status_code, "body": _json(r) if r.content else {}}


def event_create(
    token: str,
    calendar_id: str,
    summary: str,
    start_datetime: str,
    end_datetime: str,
    time_zone: str = "UTC",
    description: str = "",
    attendees: Optional[list[str]] = None,
) -> dict[str, Any]:
    cid = quote(calendar_id, safe="@")
    body: dict[str, Any] = {
        "summary": summary,
        "description": description or "",
        "start": {"dateTime": start_datetime, "timeZone": time_zone},
        "end": {"dateTime": end_datetime, "timeZone": time_zone},
    }
    if attendees:
        body["attendees"] = [{"email": e.strip()} for e in attendees if (e or "").strip()]
    r = httpx.post(
        f"{_CAL}/calendars/{cid}/events",
        headers=_h(token),
        json=body,
        timeout=30.0,
    )
    return {"ok": r.is_success, "status": r.status_code, "body": _json(r) if r.content else {}}


def event_update(
    token: str,
    calendar_id: str,
    event_id: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    cid = quote(calendar_id, safe="@")
    eid = quote(event_id, safe="")
    r = httpx.patch(
        f"{_CAL}/calendars/{cid}/events/{eid}",
        headers=_h(token),
        json=fields,
        timeout=30.0,
    )
    return {"ok": r.is_success, "status": r.status_code, "body": _json(r) if r.content else {}}


def event_delete(token: str, calendar_id: str, event_id: str) -> dict[str, Any]:
    cid = quote(calendar_id, safe="@")
    eid = quote(event_id, safe="")
    r = httpx.delete(
        f"{_CAL}/calendars/{cid}/events/{eid}",
        headers=_h(token),
        timeout=30.0,
    )
    if r.status_code == 204:
        return {"ok": True, "status": 204, "body": {"deleted": True}}
    return {"ok": False, "status": r.status_code, "body": _json(r) if r.content else {}}


def gmail_list_messages(token: str, query: str = "", max_results: int = 15) -> dict[str, Any]:
    params: dict[str, Any] = {"maxResults": max(1, min(int(max_results or 15), 30))}
    if (query or "").strip():
        params["q"] = query.strip()
    r = httpx.get(
        f"{_GMAIL}/users/me/messages",
        headers=_h(token),
        params=params,
        timeout=30.0,
    )
    return {"ok": r.is_success, "status": r.status_code, "body": _json(r) if r.content else {}}


def gmail_get_message(token: str, message_id: str, fmt: str = "metadata") -> dict[str, Any]:
    mid = quote(message_id, safe="")
    r = httpx.get(
        f"{_GMAIL}/users/me/messages/{mid}",
        headers=_h(token),
        params={"format": fmt if fmt in ("minimal", "full", "metadata", "raw") else "metadata"},
        timeout=30.0,
    )
    return {"ok": r.is_success, "status": r.status_code, "body": _json(r) if r.content else {}}


def _encode_mime_raw(to: str, subject: str, body_text: str) -> str:
    msg = EmailMessage()
    msg["To"] = to.strip()
    msg["Subject"] = subject.strip()
    msg.set_content(body_text or "")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def gmail_send_message(token: str, to: str, subject: str, body_text: str) -> dict[str, Any]:
    raw = _encode_mime_raw(to, subject, body_text)
    r = httpx.post(
        f"{_GMAIL}/users/me/messages/send",
        headers=_h(token),
        json={"raw": raw},
        timeout=30.0,
    )
    return {"ok": r.is_success, "status": r.status_code, "body": _json(r) if r.content else {}}


def gmail_modify_labels(
    token: str,
    message_id: str,
    add_label_ids: Optional[list[str]] = None,
    remove_label_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    mid = quote(message_id, safe="")
    body: dict[str, Any] = {}
    if add_label_ids:
        body["addLabelIds"] = [x for x in add_label_ids if x]
    if remove_label_ids:
        body["removeLabelIds"] = [x for x in remove_label_ids if x]
    if not body.get("addLabelIds") and not body.get("removeLabelIds"):
        return {"ok": False, "status": 0, "body": {"error": "add_label_ids or remove_label_ids required"}}
    r = httpx.post(
        f"{_GMAIL}/users/me/messages/{mid}/modify",
        headers=_h(token),
        json=body,
        timeout=30.0,
    )
    return {"ok": r.is_success, "status": r.status_code, "body": _json(r) if r.content else {}}


def gmail_labels_list(token: str) -> dict[str, Any]:
    r = httpx.get(f"{_GMAIL}/users/me/labels", headers=_h(token), timeout=30.0)
    return {"ok": r.is_success, "status": r.status_code, "body": _json(r) if r.content else {}}


def run_google_op(token: str, op: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one planner op; returns structured result (never raises)."""
    op = (op or "").strip().lower().replace("-", "_")
    a = args or {}
    try:
        if op in ("calendar_list", "calendars_list", "list_calendars"):
            return calendar_list(token)
        if op in ("events_list", "list_events", "calendar_events_list"):
            return events_list(
                token,
                calendar_id=str(a.get("calendar_id") or "primary"),
                time_min=a.get("time_min") or a.get("timeMin"),
                time_max=a.get("time_max") or a.get("timeMax"),
                max_results=int(a.get("max_results") or a.get("maxResults") or 25),
            )
        if op in ("event_create", "create_event", "calendar_event_create"):
            start_dt = str(a.get("start_datetime") or a.get("start") or "").strip()
            end_dt = str(a.get("end_datetime") or a.get("end") or "").strip()
            if not start_dt or not end_dt:
                return {"ok": False, "error": "event_create requires start_datetime and end_datetime"}
            return event_create(
                token,
                calendar_id=str(a.get("calendar_id") or "primary"),
                summary=str(a.get("summary") or "Event"),
                start_datetime=start_dt,
                end_datetime=end_dt,
                time_zone=str(a.get("time_zone") or a.get("timeZone") or "UTC"),
                description=str(a.get("description") or ""),
                attendees=a.get("attendees") if isinstance(a.get("attendees"), list) else None,
            )
        if op in ("event_update", "update_event", "calendar_event_update"):
            return event_update(
                token,
                calendar_id=str(a.get("calendar_id") or "primary"),
                event_id=str(a.get("event_id") or a.get("eventId") or ""),
                fields=a.get("fields") if isinstance(a.get("fields"), dict) else {},
            )
        if op in ("event_delete", "delete_event", "calendar_event_delete"):
            return event_delete(
                token,
                calendar_id=str(a.get("calendar_id") or "primary"),
                event_id=str(a.get("event_id") or a.get("eventId") or ""),
            )
        if op in ("gmail_list", "list_messages", "gmail_messages_list"):
            return gmail_list_messages(
                token,
                query=str(a.get("query") or a.get("q") or ""),
                max_results=int(a.get("max_results") or a.get("maxResults") or 15),
            )
        if op in ("gmail_get", "get_message", "gmail_message_get"):
            return gmail_get_message(
                token,
                message_id=str(a.get("message_id") or a.get("messageId") or a.get("id") or ""),
                fmt=str(a.get("format") or "metadata"),
            )
        if op in ("gmail_send", "send_email", "send_message"):
            return gmail_send_message(
                token,
                to=str(a.get("to") or ""),
                subject=str(a.get("subject") or ""),
                body_text=str(a.get("body") or a.get("body_text") or ""),
            )
        if op in ("gmail_modify", "modify_message", "gmail_labels_modify"):
            return gmail_modify_labels(
                token,
                message_id=str(a.get("message_id") or a.get("messageId") or ""),
                add_label_ids=a.get("add_label_ids") if isinstance(a.get("add_label_ids"), list) else None,
                remove_label_ids=a.get("remove_label_ids") if isinstance(a.get("remove_label_ids"), list) else None,
            )
        if op in ("gmail_labels_list", "list_labels"):
            return gmail_labels_list(token)
        return {"ok": False, "error": f"unknown_op:{op}", "args": a}
    except Exception as e:
        return {"ok": False, "error": str(e), "op": op}


def truncate_for_llm(obj: Any, max_chars: int = 12000) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 20] + "\n…[truncated]"
