"""Shared helper for all nine adapters. EnterpriseRAG-Bench's own per-document
metadata (`title_field_name`, `content_field_names`) tells you which fields hold the
real title/body for THAT document — it is not a fixed `body`/`content` key. Confirmed
by sampling the actual corpus: a single source (e.g. confluence) mixes plain
`{title, body}` pages with 20-field RFC/runbook-style documents
(`summary, goals, architecture_overview, rollout_plan, ...`), and every one of the
nine sources shows the same pattern (slack: `messages` vs `text`; linear:
`description, acceptance_criteria, comments, ...`; jira: `description, comments,
investigation_notes, resolution, ...`). Hardcoding a `body`/`content` field name
silently drops most of the corpus's actual text — see PROJECT.md decision #22.
"""

import json


def get_title(record: dict) -> str | None:
    title_field = record.get("title_field_name")
    if title_field and title_field in record:
        val = record[title_field]
        return val if isinstance(val, str) else json.dumps(val, default=str)
    return record.get("title")


def _stringify(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("body") or item.get("message") or item.get("content")
                if text:
                    author = item.get("author") or item.get("user") or item.get("sender") or item.get("from")
                    parts.append(f"{author}: {text}" if author else str(text))
                else:
                    parts.append(json.dumps(item, default=str))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if isinstance(value, dict):
        return json.dumps(value, default=str)
    return str(value) if value is not None else ""


_FALLBACK_FIELDS = ("body", "content", "text", "transcript", "description", "messages")


def assemble_body(record: dict) -> str:
    """Concatenate every field named in content_field_names, in the order given
    (that order is itself meaningful — it's the document's own section order)."""
    field_names = record.get("content_field_names") or []
    if not field_names:
        field_names = [f for f in _FALLBACK_FIELDS if f in record][:1]

    multi = len(field_names) > 1
    sections = []
    for name in field_names:
        if name not in record or record[name] is None:
            continue
        text = _stringify(record[name])
        if not text:
            continue
        sections.append(f"## {name}\n{text}" if multi else text)
    return "\n\n".join(sections)
