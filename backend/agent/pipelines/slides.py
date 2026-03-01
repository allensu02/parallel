"""Google Slides pipeline — create and edit presentations via the Slides API.

Operations are thin async wrappers around the Google Slides API.
The LLM plans which operations to call; the gsuite_executor runs them.
Slide creation has sequential delays between title/body insertion so
the browser screencast shows content appearing gradually.
"""

from __future__ import annotations

import asyncio
from typing import Any
import uuid

from backend.agent.pipelines.base import Pipeline
from backend.agent.gsuite_executor import plan_and_execute, OpDef
from backend.services.google_api import get_slides_service, get_drive_service, _run_sync


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Slides API uses EMU (English Metric Units): 1 inch = 914400 EMU
_EMU_PER_INCH = 914400


def _inches_to_emu(inches: float) -> int:
    return int(inches * _EMU_PER_INCH)


# Standard 10x7.5 inch slide
_SLIDE_WIDTH_EMU = _inches_to_emu(10)
_SLIDE_HEIGHT_EMU = _inches_to_emu(7.5)


# ---------------------------------------------------------------------------
# API Operations
# ---------------------------------------------------------------------------


async def create_presentation(title: str = "Untitled Presentation") -> dict:
    """Create a new Google Slides presentation. Returns {presentationId, title, url}."""
    service = await get_slides_service()
    if not service:
        raise RuntimeError("Slides API not available — not authenticated")

    def _create():
        return service.presentations().create(
            body={"title": title}
        ).execute()

    pres = await _run_sync(_create)
    pres_id = pres["presentationId"]
    return {
        "presentationId": pres_id,
        "title": pres.get("title", title),
        "url": f"https://docs.google.com/presentation/d/{pres_id}/edit",
    }


async def get_presentation(presentation_id: str) -> dict:
    """Read presentation metadata. Returns {presentationId, title, slideCount, slides: [{objectId, title}]}."""
    service = await get_slides_service()
    if not service:
        raise RuntimeError("Slides API not available — not authenticated")

    def _get():
        return service.presentations().get(presentationId=presentation_id).execute()

    pres = await _run_sync(_get)
    slides = []
    for slide in pres.get("slides", []):
        # Try to extract title from page elements
        title_text = ""
        for pe in slide.get("pageElements", []):
            shape = pe.get("shape", {})
            placeholder = shape.get("placeholder", {})
            if placeholder.get("type") in ("TITLE", "CENTERED_TITLE"):
                text_elements = shape.get("text", {}).get("textElements", [])
                for te in text_elements:
                    tr = te.get("textRun", {})
                    if tr.get("content", "").strip():
                        title_text = tr["content"].strip()
                        break

        slides.append({
            "objectId": slide.get("objectId", ""),
            "title": title_text,
        })

    return {
        "presentationId": presentation_id,
        "title": pres.get("title", ""),
        "slideCount": len(slides),
        "slides": slides,
    }


async def add_slide(
    presentation_id: str,
    layout: str = "BLANK",
    title: str = "",
    body: str = "",
) -> dict:
    """Add a new slide. Optionally set title and body text.

    Layout options: BLANK, TITLE, TITLE_AND_BODY, TITLE_AND_TWO_COLUMNS,
    TITLE_ONLY, SECTION_HEADER, SECTION_TITLE_AND_DESCRIPTION, ONE_COLUMN_TEXT,
    MAIN_POINT, BIG_NUMBER.

    Returns {presentationId, slideObjectId}.
    """
    service = await get_slides_service()
    if not service:
        raise RuntimeError("Slides API not available — not authenticated")

    slide_id = f"slide_{uuid.uuid4().hex[:12]}"

    # Map friendly layout names to predefined layout types
    layout_map = {
        "BLANK": "BLANK",
        "TITLE": "TITLE",
        "TITLE_AND_BODY": "TITLE_AND_BODY",
        "TITLE_AND_TWO_COLUMNS": "TITLE_AND_TWO_COLUMNS",
        "TITLE_ONLY": "TITLE_ONLY",
        "SECTION_HEADER": "SECTION_HEADER",
        "MAIN_POINT": "MAIN_POINT",
        "BIG_NUMBER": "BIG_NUMBER",
    }
    layout_type = layout_map.get(layout.upper(), "BLANK")

    requests: list[dict] = [
        {
            "createSlide": {
                "objectId": slide_id,
                "slideLayoutReference": {"predefinedLayout": layout_type},
            }
        }
    ]

    def _create():
        return service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": requests},
        ).execute()

    await _run_sync(_create)

    # If title or body provided, insert text into the slide's placeholders
    # Title and body are inserted in separate API calls with a delay between
    # them so the browser screencast shows content appearing sequentially.
    if title or body:
        def _get_slide():
            return service.presentations().pages().get(
                presentationId=presentation_id, pageObjectId=slide_id
            ).execute()

        try:
            slide = await _run_sync(_get_slide)

            title_obj_id = None
            body_obj_id = None
            for pe in slide.get("pageElements", []):
                shape = pe.get("shape", {})
                placeholder = shape.get("placeholder", {})
                p_type = placeholder.get("type", "")
                obj_id = pe.get("objectId", "")

                if p_type in ("TITLE", "CENTERED_TITLE") and title and not title_obj_id:
                    title_obj_id = obj_id
                elif p_type in ("BODY", "SUBTITLE") and body and not body_obj_id:
                    body_obj_id = obj_id

            # Insert title first
            if title_obj_id:
                def _insert_title():
                    return service.presentations().batchUpdate(
                        presentationId=presentation_id,
                        body={"requests": [{
                            "insertText": {
                                "objectId": title_obj_id,
                                "text": title,
                                "insertionIndex": 0,
                            }
                        }]},
                    ).execute()
                await _run_sync(_insert_title)

            # Delay so screencast shows title before body appears
            if title_obj_id and body_obj_id:
                await asyncio.sleep(0.5)

            # Insert body
            if body_obj_id:
                def _insert_body():
                    return service.presentations().batchUpdate(
                        presentationId=presentation_id,
                        body={"requests": [{
                            "insertText": {
                                "objectId": body_obj_id,
                                "text": body,
                                "insertionIndex": 0,
                            }
                        }]},
                    ).execute()
                await _run_sync(_insert_body)

        except Exception as e:
            print(f"[Slides] Warning: Could not set title/body text: {e}")

    return {"presentationId": presentation_id, "slideObjectId": slide_id}


async def update_slide_text(
    presentation_id: str,
    slide_object_id: str,
    placeholder_type: str = "TITLE",
    text: str = "",
) -> dict:
    """Update text in a specific placeholder on a slide. Returns {presentationId, updated}."""
    service = await get_slides_service()
    if not service:
        raise RuntimeError("Slides API not available — not authenticated")

    # Get the slide to find the placeholder
    def _get_slide():
        return service.presentations().pages().get(
            presentationId=presentation_id, pageObjectId=slide_object_id
        ).execute()

    slide = await _run_sync(_get_slide)

    target_id = None
    for pe in slide.get("pageElements", []):
        shape = pe.get("shape", {})
        placeholder = shape.get("placeholder", {})
        if placeholder.get("type", "").upper() == placeholder_type.upper():
            target_id = pe.get("objectId")
            break

    if not target_id:
        return {"presentationId": presentation_id, "updated": False, "error": f"No {placeholder_type} placeholder found"}

    # Delete existing text, then insert new text
    requests = [
        {
            "deleteText": {
                "objectId": target_id,
                "textRange": {"type": "ALL"},
            }
        },
        {
            "insertText": {
                "objectId": target_id,
                "text": text,
                "insertionIndex": 0,
            }
        },
    ]

    def _update():
        return service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": requests},
        ).execute()

    await _run_sync(_update)
    return {"presentationId": presentation_id, "updated": True}


async def delete_slide(presentation_id: str, slide_object_id: str) -> dict:
    """Remove a slide from the presentation. Returns {presentationId, deleted}."""
    service = await get_slides_service()
    if not service:
        raise RuntimeError("Slides API not available — not authenticated")

    def _delete():
        return service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={
                "requests": [
                    {"deleteObject": {"objectId": slide_object_id}}
                ]
            },
        ).execute()

    await _run_sync(_delete)
    return {"presentationId": presentation_id, "deleted": True}


async def add_text_box(
    presentation_id: str,
    slide_object_id: str,
    text: str,
    x_inches: float = 1.0,
    y_inches: float = 1.0,
    width_inches: float = 5.0,
    height_inches: float = 1.0,
) -> dict:
    """Add a text box at a specific position on a slide. Returns {presentationId, textBoxId}."""
    service = await get_slides_service()
    if not service:
        raise RuntimeError("Slides API not available — not authenticated")

    text_box_id = f"textbox_{uuid.uuid4().hex[:12]}"

    requests = [
        {
            "createShape": {
                "objectId": text_box_id,
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_object_id,
                    "size": {
                        "width": {"magnitude": _inches_to_emu(width_inches), "unit": "EMU"},
                        "height": {"magnitude": _inches_to_emu(height_inches), "unit": "EMU"},
                    },
                    "transform": {
                        "scaleX": 1,
                        "scaleY": 1,
                        "translateX": _inches_to_emu(x_inches),
                        "translateY": _inches_to_emu(y_inches),
                        "unit": "EMU",
                    },
                },
            }
        },
        {
            "insertText": {
                "objectId": text_box_id,
                "text": text,
                "insertionIndex": 0,
            }
        },
    ]

    def _create():
        return service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": requests},
        ).execute()

    await _run_sync(_create)
    return {"presentationId": presentation_id, "textBoxId": text_box_id}


async def list_recent_presentations(max_results: int = 10) -> list[dict]:
    """List recent Google Slides presentations via Drive API."""
    service = await get_drive_service()
    if not service:
        raise RuntimeError("Drive API not available — not authenticated")

    def _list():
        return service.files().list(
            q="mimeType='application/vnd.google-apps.presentation'",
            orderBy="modifiedTime desc",
            pageSize=max_results,
            fields="files(id,name,modifiedTime,webViewLink)",
        ).execute()

    result = await _run_sync(_list)
    return [
        {
            "id": f["id"],
            "name": f.get("name", ""),
            "url": f.get("webViewLink", f"https://docs.google.com/presentation/d/{f['id']}/edit"),
            "modifiedTime": f.get("modifiedTime", ""),
        }
        for f in result.get("files", [])
    ]


# ---------------------------------------------------------------------------
# Operation definitions for the LLM planner
# ---------------------------------------------------------------------------

SLIDES_OPERATIONS: list[OpDef] = [
    {
        "name": "create_presentation",
        "description": "Create a new Google Slides presentation",
        "parameters": "title: str",
        "fn": create_presentation,
    },
    {
        "name": "get_presentation",
        "description": "Read presentation metadata (slide count, titles)",
        "parameters": "presentation_id: str",
        "fn": get_presentation,
    },
    {
        "name": "add_slide",
        "description": "Add a new slide with optional title and body text. Layout: BLANK, TITLE, TITLE_AND_BODY, TITLE_ONLY, SECTION_HEADER, MAIN_POINT",
        "parameters": "presentation_id: str, layout: str = 'BLANK', title: str = '', body: str = ''",
        "fn": add_slide,
    },
    {
        "name": "update_slide_text",
        "description": "Update text in a slide's placeholder (TITLE, BODY, SUBTITLE)",
        "parameters": "presentation_id: str, slide_object_id: str, placeholder_type: str = 'TITLE', text: str",
        "fn": update_slide_text,
    },
    {
        "name": "delete_slide",
        "description": "Remove a slide from the presentation",
        "parameters": "presentation_id: str, slide_object_id: str",
        "fn": delete_slide,
    },
    {
        "name": "add_text_box",
        "description": "Add a text box at a specific position (in inches) on a slide",
        "parameters": "presentation_id: str, slide_object_id: str, text: str, x_inches: float = 1.0, y_inches: float = 1.0, width_inches: float = 5.0, height_inches: float = 1.0",
        "fn": add_text_box,
    },
    {
        "name": "list_recent_presentations",
        "description": "List recent Google Slides presentations",
        "parameters": "max_results: int = 10",
        "fn": list_recent_presentations,
    },
]

SLIDES_OP_MAP = {op["name"]: op["fn"] for op in SLIDES_OPERATIONS}


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------


class SlidesPipeline(Pipeline):
    pipeline_type = "slides"
    display_name = "Google Slides"
    description = (
        "Create and edit Google Slides presentations. Can create new presentations, "
        "add slides, edit text, and format content."
    )
    uses_local_browser = False

    def can_handle(self, task_description: str) -> bool:
        import re
        keywords = [
            r"\bgoogle slides?\b", r"\bpresentation\b", r"\bslide ?deck\b",
            r"\bslideshow\b", r"\bcreate.{0,10}slides?\b",
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
            pipeline_type="slides",
            service_name="Google Slides",
            task_description=task_description,
            available_ops=SLIDES_OPERATIONS,
            op_map=SLIDES_OP_MAP,
        )
