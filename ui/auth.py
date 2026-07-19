"""
Databricks Apps SSO — analyst identity extraction.

On Databricks Apps, every authenticated request carries the user's email
in standard forwarded headers set by the Apps proxy.  This module extracts
the email from a Gradio `gr.Request` object so tabs can auto-populate the
analyst identity without requiring manual entry.

In local dev (no Databricks proxy) these headers are absent and an empty
string is returned — the analyst types their email manually in the Session tab.

Header precedence:
    X-Forwarded-User  — primary (set by Databricks Apps)
    X-Forwarded-Email — secondary (fallback)
    X-Remote-User     — tertiary (some reverse proxies)
"""
from __future__ import annotations

import gradio as gr


_HEADERS = (
    "x-forwarded-user",
    "x-forwarded-email",
    "x-remote-user",
)


def extract_analyst_email(request: gr.Request) -> str:
    """
    Return the authenticated analyst's email from the request headers.

    Returns an empty string when running locally or when headers are absent.
    Callers should treat an empty return as "local dev — prompt user to type".
    """
    if request is None or not hasattr(request, "headers"):
        return ""
    headers = request.headers
    for key in _HEADERS:
        value = headers.get(key, "").strip()
        if value and "@" in value:
            return value
    return ""


def is_databricks_context(request: gr.Request) -> bool:
    """
    True when running inside Databricks Apps (SSO proxy present).
    Used to make the email field read-only (identity cannot be spoofed).
    """
    return bool(extract_analyst_email(request))
