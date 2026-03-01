"""Google Docs pipeline — create and edit documents via the Docs API.

Operations are thin async wrappers around the Google Docs API.
The LLM plans which operations to call; the gsuite_executor runs them.
Text insertion operations support incremental animation — splitting
content into sentence chunks with delays so the browser screencast
shows text appearing gradually.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from backend.agent.pipelines.base import Pipeline
from backend.agent.gsuite_executor import plan_and_execute, OpDef
from backend.services.google_api import get_docs_service, get_drive_service, _run_sync


# ---------------------------------------------------------------------------
# API Operations
# ---------------------------------------------------------------------------


async def create_document(title: str = "Untitled Document") -> dict:
    """Create a new Google Doc. Returns {documentId, title, url}."""
    service = await get_docs_service()
    if not service:
        raise RuntimeError("Docs API not available — not authenticated")

    def _create():
        return service.documents().create(body={"title": title}).execute()

    doc = await _run_sync(_create)
    doc_id = doc["documentId"]
    return {
        "documentId": doc_id,
        "title": doc.get("title", title),
        "url": f"https://docs.google.com/document/d/{doc_id}/edit",
    }


async def get_document(doc_id: str) -> dict:
    """Read a document's full text content. Returns {documentId, title, text}."""
    service = await get_docs_service()
    if not service:
        raise RuntimeError("Docs API not available — not authenticated")

    def _get():
        return service.documents().get(documentId=doc_id).execute()

    doc = await _run_sync(_get)

    # Extract plain text from the document body
    text_parts = []
    body = doc.get("body", {})
    for element in body.get("content", []):
        paragraph = element.get("paragraph", {})
        for pe in paragraph.get("elements", []):
            text_run = pe.get("textRun", {})
            content = text_run.get("content", "")
            if content:
                text_parts.append(content)

    return {
        "documentId": doc_id,
        "title": doc.get("title", ""),
        "text": "".join(text_parts),
    }


def _split_into_chunks(text: str) -> list[str]:
    """Split text into sentence-sized chunks for animated insertion.

    Preserves ALL original whitespace (newlines, indentation, etc.).
    Chunks are split at newline boundaries so formatting is maintained.
    Invariant: ''.join(chunks) == text.
    """
    # Split on newlines, keeping the newline at the end of each chunk
    raw_lines = re.split(r'(?<=\n)', text)
    # Merge short lines together so we don't make too many API calls
    chunks: list[str] = []
    buf = ""
    for line in raw_lines:
        if not line:
            continue
        buf += line
        # Flush when we have enough text for a visible update
        if len(buf) >= 60 or buf.endswith("\n\n"):
            chunks.append(buf)
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks if chunks else [text]


async def _insert_text_at(doc_id: str, index: int, text: str) -> None:
    """Low-level single insertText call."""
    service = await get_docs_service()
    if not service:
        raise RuntimeError("Docs API not available — not authenticated")

    requests = [{"insertText": {"location": {"index": index}, "text": text}}]

    def _update():
        return service.documents().batchUpdate(
            documentId=doc_id, body={"requests": requests}
        ).execute()

    await _run_sync(_update)


async def write_content(doc_id: str, content: str, index: int = 1, _context: dict | None = None) -> dict:
    """Insert text at a specific index (default: beginning).

    When _context is provided (injected by the executor) and content is long,
    text is inserted in sentence-sized chunks with short delays so the
    browser screencast shows text appearing gradually.

    Returns {documentId, endIndex} — endIndex is where the cursor sits after
    insertion, so subsequent operations can chain via $RESULT_N.
    """
    if _context and len(content) > 50:
        # Animated insertion — chunk by sentence
        chunks = _split_into_chunks(content)
        cursor = index
        for chunk in chunks:
            await _insert_text_at(doc_id, cursor, chunk)
            cursor += len(chunk)
            # Small delay so the live browser view updates between chunks
            await asyncio.sleep(0.3)
        return {"documentId": doc_id, "endIndex": index + len(content)}

    # Fast path — single API call
    await _insert_text_at(doc_id, index, content)
    return {"documentId": doc_id, "endIndex": index + len(content)}


async def append_content(doc_id: str, content: str, _context: dict | None = None) -> dict:
    """Append text to the end of the document. Returns {documentId, endIndex}."""
    service = await get_docs_service()
    if not service:
        raise RuntimeError("Docs API not available — not authenticated")

    def _get():
        return service.documents().get(documentId=doc_id).execute()

    doc = await _run_sync(_get)
    body = doc.get("body", {})
    body_content = body.get("content", [])

    end_index = 1
    if body_content:
        last_element = body_content[-1]
        end_index = last_element.get("endIndex", 1) - 1
    if end_index < 1:
        end_index = 1

    return await write_content(doc_id, content, index=end_index, _context=_context)


async def clear_and_rewrite(doc_id: str, content: str, _context: dict | None = None) -> dict:
    """Clear ALL content from a document and write new content.

    This is the best operation for editing/rewriting an entire document.
    It removes everything and writes fresh content, with animated insertion
    when _context is provided.

    Returns {documentId, endIndex}.
    """
    service = await get_docs_service()
    if not service:
        raise RuntimeError("Docs API not available — not authenticated")

    # Read current doc to find end index
    def _get():
        return service.documents().get(documentId=doc_id).execute()

    doc = await _run_sync(_get)
    body = doc.get("body", {})
    body_content = body.get("content", [])

    if body_content:
        end_index = body_content[-1].get("endIndex", 1) - 1
        if end_index > 1:
            # Delete all content (index 1 to end)
            requests = [{"deleteContentRange": {"range": {"startIndex": 1, "endIndex": end_index}}}]
            def _clear():
                return service.documents().batchUpdate(
                    documentId=doc_id, body={"requests": requests}
                ).execute()
            await _run_sync(_clear)

    # Now write the new content (with animation if context is provided)
    return await write_content(doc_id, content, index=1, _context=_context)


async def replace_text(doc_id: str, find: str, replace: str) -> dict:
    """Find and replace text in the document. Returns {documentId, occurrences}."""
    service = await get_docs_service()
    if not service:
        raise RuntimeError("Docs API not available — not authenticated")

    requests = [
        {
            "replaceAllText": {
                "containsText": {"text": find, "matchCase": True},
                "replaceText": replace,
            }
        }
    ]

    def _update():
        return service.documents().batchUpdate(
            documentId=doc_id, body={"requests": requests}
        ).execute()

    result = await _run_sync(_update)
    replies = result.get("replies", [{}])
    occurrences = replies[0].get("replaceAllText", {}).get("occurrencesChanged", 0) if replies else 0
    return {"documentId": doc_id, "occurrences": occurrences}


async def format_text(
    doc_id: str,
    start_index: int,
    end_index: int,
    bold: bool | None = None,
    italic: bool | None = None,
    font_size: int | None = None,
    font_family: str | None = None,
) -> dict:
    """Apply formatting to a text range. Returns {documentId, formatted}."""
    service = await get_docs_service()
    if not service:
        raise RuntimeError("Docs API not available — not authenticated")

    text_style: dict[str, Any] = {}
    fields = []

    if bold is not None:
        text_style["bold"] = bold
        fields.append("bold")
    if italic is not None:
        text_style["italic"] = italic
        fields.append("italic")
    if font_size is not None:
        text_style["fontSize"] = {"magnitude": font_size, "unit": "PT"}
        fields.append("fontSize")
    if font_family is not None:
        text_style["weightedFontFamily"] = {"fontFamily": font_family}
        fields.append("weightedFontFamily")

    if not fields:
        return {"documentId": doc_id, "formatted": False}

    requests = [
        {
            "updateTextStyle": {
                "range": {"startIndex": start_index, "endIndex": end_index},
                "textStyle": text_style,
                "fields": ",".join(fields),
            }
        }
    ]

    def _update():
        return service.documents().batchUpdate(
            documentId=doc_id, body={"requests": requests}
        ).execute()

    await _run_sync(_update)
    return {"documentId": doc_id, "formatted": True}


async def insert_heading(
    doc_id: str,
    text: str,
    level: int = 1,
    index: int = 1,
) -> dict:
    """Insert a heading (H1–H6) at a position. Adds a trailing newline automatically.

    Returns {documentId, endIndex} — endIndex is where the cursor is after the heading,
    useful for subsequent write_content calls.
    """
    service = await get_docs_service()
    if not service:
        raise RuntimeError("Docs API not available — not authenticated")

    heading_text = text.rstrip("\n") + "\n"
    level = max(1, min(6, level))
    named_style = f"HEADING_{level}"

    requests = [
        {
            "insertText": {
                "location": {"index": index},
                "text": heading_text,
            }
        },
        {
            "updateParagraphStyle": {
                "range": {
                    "startIndex": index,
                    "endIndex": index + len(heading_text),
                },
                "paragraphStyle": {"namedStyleType": named_style},
                "fields": "namedStyleType",
            }
        },
    ]

    def _update():
        return service.documents().batchUpdate(
            documentId=doc_id, body={"requests": requests}
        ).execute()

    await _run_sync(_update)
    end_index = index + len(heading_text)
    return {"documentId": doc_id, "endIndex": end_index}


async def delete_document(doc_id: str) -> dict:
    """Delete (trash) a Google Doc via the Drive API. Returns {fileId, trashed}."""
    service = await get_drive_service()
    if not service:
        raise RuntimeError("Drive API not available — not authenticated")

    def _trash():
        return service.files().update(
            fileId=doc_id,
            body={"trashed": True},
            fields="id,trashed",
        ).execute()

    result = await _run_sync(_trash)
    return {"fileId": result["id"], "trashed": result.get("trashed", True)}


async def list_recent_docs(max_results: int = 10) -> list[dict]:
    """List recent Google Docs via Drive API. Returns list of {id, name, url, modifiedTime}."""
    service = await get_drive_service()
    if not service:
        raise RuntimeError("Drive API not available — not authenticated")

    def _list():
        return service.files().list(
            q="mimeType='application/vnd.google-apps.document'",
            orderBy="modifiedTime desc",
            pageSize=max_results,
            fields="files(id,name,modifiedTime,webViewLink)",
        ).execute()

    result = await _run_sync(_list)
    return [
        {
            "id": f["id"],
            "name": f.get("name", ""),
            "url": f.get("webViewLink", f"https://docs.google.com/document/d/{f['id']}/edit"),
            "modifiedTime": f.get("modifiedTime", ""),
        }
        for f in result.get("files", [])
    ]


async def search_docs(query: str, max_results: int = 10) -> list[dict]:
    """Search Google Docs by content or name. Returns list of {id, name, url}."""
    service = await get_drive_service()
    if not service:
        raise RuntimeError("Drive API not available — not authenticated")

    def _search():
        return service.files().list(
            q=f"mimeType='application/vnd.google-apps.document' and (name contains '{query}' or fullText contains '{query}')",
            orderBy="modifiedTime desc",
            pageSize=max_results,
            fields="files(id,name,modifiedTime,webViewLink)",
        ).execute()

    result = await _run_sync(_search)
    return [
        {
            "id": f["id"],
            "name": f.get("name", ""),
            "url": f.get("webViewLink", f"https://docs.google.com/document/d/{f['id']}/edit"),
            "modifiedTime": f.get("modifiedTime", ""),
        }
        for f in result.get("files", [])
    ]


# ---------------------------------------------------------------------------
# Operation definitions for the LLM planner
# ---------------------------------------------------------------------------

DOCS_OPERATIONS: list[OpDef] = [
    {
        "name": "create_document",
        "description": "Create a new empty Google Doc",
        "parameters": "title: str",
        "fn": create_document,
    },
    {
        "name": "get_document",
        "description": "Read a document's full text content",
        "parameters": "doc_id: str",
        "fn": get_document,
    },
    {
        "name": "insert_heading",
        "description": (
            "Insert a styled heading (H1-H6) at a position. Returns {documentId, endIndex}. "
            "CRITICAL: To build a document top-to-bottom, ALWAYS chain index=$RESULT_N from the "
            "previous insert_heading or write_content. Example: action 0 insert_heading at index=1, "
            "action 1 write_content at index=$RESULT_0, action 2 insert_heading at index=$RESULT_1, etc."
        ),
        "parameters": "doc_id: str, text: str, level: int = 1, index: int = 1",
        "fn": insert_heading,
    },
    {
        "name": "write_content",
        "description": (
            "Insert plain body text at a specific position. Returns {documentId, endIndex}. "
            "CRITICAL: Use index=$RESULT_N from the PREVIOUS action's endIndex to insert text "
            "AFTER prior content. Do NOT use index=1 for every call — that inserts at the TOP "
            "and reverses the document order. Chain: heading→$RESULT→body→$RESULT→heading→..."
        ),
        "parameters": "doc_id: str, content: str, index: int = 1",
        "fn": write_content,
    },
    {
        "name": "append_content",
        "description": "Append plain body text to the end of the document. Returns {documentId, endIndex}. "
                       "Simpler alternative to write_content when you just want to add to the bottom.",
        "parameters": "doc_id: str, content: str",
        "fn": append_content,
    },
    {
        "name": "clear_and_rewrite",
        "description": "Clear ALL content from a document and replace it with new content. "
                       "Use this for editing, rewriting, or revising an existing document. "
                       "This is the BEST operation when asked to edit/improve/rewrite a doc.",
        "parameters": "doc_id: str, content: str",
        "fn": clear_and_rewrite,
    },
    {
        "name": "replace_text",
        "description": "Find and replace a specific phrase throughout the document. Only for small targeted edits.",
        "parameters": "doc_id: str, find: str, replace: str",
        "fn": replace_text,
    },
    {
        "name": "format_text",
        "description": "Apply formatting (bold, italic, font size, font family) to a text range",
        "parameters": "doc_id: str, start_index: int, end_index: int, bold: bool = None, italic: bool = None, font_size: int = None, font_family: str = None",
        "fn": format_text,
    },
    {
        "name": "delete_document",
        "description": "Delete (trash) a Google Doc. Use search_docs or list_recent_docs first to find the document ID.",
        "parameters": "doc_id: str",
        "fn": delete_document,
    },
    {
        "name": "list_recent_docs",
        "description": "List recent Google Docs documents",
        "parameters": "max_results: int = 10",
        "fn": list_recent_docs,
    },
    {
        "name": "search_docs",
        "description": "Search Google Docs by name or content",
        "parameters": "query: str, max_results: int = 10",
        "fn": search_docs,
    },
]

DOCS_OP_MAP = {op["name"]: op["fn"] for op in DOCS_OPERATIONS}


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------


class DocsPipeline(Pipeline):
    pipeline_type = "docs"
    display_name = "Google Docs"
    description = (
        "Create and edit Google Docs documents. Can create new documents, "
        "write content, format text, add headings, and edit existing documents."
    )
    uses_local_browser = False  # Uses API, not browser

    def can_handle(self, task_description: str) -> bool:
        import re
        keywords = [
            r"\bgoogle docs?\b", r"\bgdocs?\b", r"\bwrite a doc\b",
            r"\bedit (the|my) doc\b", r"\bdelete the doc\b", r"\brewrite the doc\b",
            r"\bcreate a document\b", r"\bedit.{0,10}document\b",
        ]
        desc_lower = task_description.lower()
        return any(re.search(kw, desc_lower) for kw in keywords)

    async def execute(
        self,
        run_id: str,
        job_id: str,
        params: dict[str, Any],
    ) -> None:
        task_description = params.get("instruction", params.get("description", ""))
        await plan_and_execute(
            run_id=run_id,
            job_id=job_id,
            pipeline_type="docs",
            service_name="Google Docs",
            task_description=task_description,
            available_ops=DOCS_OPERATIONS,
            op_map=DOCS_OP_MAP,
        )
