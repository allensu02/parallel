"""Google Forms pipeline — create and edit forms via the Forms API.

Operations are thin async wrappers around the Google Forms API.
The LLM plans which operations to call; the gsuite_executor runs them.
"""

from __future__ import annotations

from typing import Any

from backend.agent.pipelines.base import Pipeline
from backend.agent.gsuite_executor import plan_and_execute, OpDef
from backend.services.google_api import get_forms_service, get_drive_service, _run_sync


# ---------------------------------------------------------------------------
# Question type mapping
# ---------------------------------------------------------------------------

_QUESTION_TYPE_MAP = {
    "SHORT_ANSWER": "TEXT",
    "PARAGRAPH": "TEXT",
    "MULTIPLE_CHOICE": "RADIO",
    "CHECKBOX": "CHECKBOX",
    "DROPDOWN": "DROP_DOWN",
    "SCALE": "SCALE",
    "DATE": "DATE",
    "TIME": "TIME",
}


# ---------------------------------------------------------------------------
# API Operations
# ---------------------------------------------------------------------------


async def create_form(title: str = "Untitled Form", description: str = "") -> dict:
    """Create a new Google Form. Returns {formId, title, url}."""
    service = await get_forms_service()
    if not service:
        raise RuntimeError("Forms API not available — not authenticated")

    body: dict[str, Any] = {"info": {"title": title}}
    if description:
        body["info"]["description"] = description

    def _create():
        return service.forms().create(body=body).execute()

    form = await _run_sync(_create)
    form_id = form["formId"]
    return {
        "formId": form_id,
        "title": form.get("info", {}).get("title", title),
        "url": f"https://docs.google.com/forms/d/{form_id}/edit",
        "responderUrl": form.get("responderUri", f"https://docs.google.com/forms/d/e/{form_id}/viewform"),
    }


async def add_question(
    form_id: str,
    title: str,
    question_type: str = "SHORT_ANSWER",
    options: list[str] | None = None,
    required: bool = False,
    description: str = "",
    low_label: str = "",
    high_label: str = "",
) -> dict:
    """Add a question to the form.

    Types: SHORT_ANSWER, PARAGRAPH, MULTIPLE_CHOICE, CHECKBOX, DROPDOWN, SCALE, DATE, TIME.
    For MULTIPLE_CHOICE/CHECKBOX/DROPDOWN, provide options list.
    For SCALE, optionally provide low_label and high_label.

    Returns {formId, questionId}.
    """
    service = await get_forms_service()
    if not service:
        raise RuntimeError("Forms API not available — not authenticated")

    q_type_upper = question_type.upper()

    # Build the question item
    question_item: dict[str, Any] = {"required": required}

    if q_type_upper in ("SHORT_ANSWER", "PARAGRAPH"):
        question_item["textQuestion"] = {
            "paragraph": q_type_upper == "PARAGRAPH"
        }
    elif q_type_upper in ("MULTIPLE_CHOICE", "CHECKBOX", "DROPDOWN"):
        choice_type = _QUESTION_TYPE_MAP.get(q_type_upper, "RADIO")
        choice_options = [{"value": opt} for opt in (options or ["Option 1"])]
        question_item["choiceQuestion"] = {
            "type": choice_type,
            "options": choice_options,
        }
    elif q_type_upper == "SCALE":
        question_item["scaleQuestion"] = {
            "low": 1,
            "high": 5,
        }
        if low_label:
            question_item["scaleQuestion"]["lowLabel"] = low_label
        if high_label:
            question_item["scaleQuestion"]["highLabel"] = high_label
    elif q_type_upper == "DATE":
        question_item["dateQuestion"] = {}
    elif q_type_upper == "TIME":
        question_item["timeQuestion"] = {}

    item: dict[str, Any] = {
        "title": title,
        "questionItem": {"question": question_item},
    }
    if description:
        item["description"] = description

    request = {
        "requests": [
            {
                "createItem": {
                    "item": item,
                    "location": {"index": 0},
                }
            }
        ]
    }

    def _add():
        return service.forms().batchUpdate(formId=form_id, body=request).execute()

    result = await _run_sync(_add)
    replies = result.get("replies", [{}])
    created = replies[0].get("createItem", {}) if replies else {}
    return {
        "formId": form_id,
        "questionId": created.get("questionId", [""])[0] if isinstance(created.get("questionId"), list) else created.get("questionId", ""),
    }


async def update_form_info(form_id: str, title: str = "", description: str = "") -> dict:
    """Update form title and/or description. Returns {formId, updated}."""
    service = await get_forms_service()
    if not service:
        raise RuntimeError("Forms API not available — not authenticated")

    update_mask_parts = []
    info: dict[str, Any] = {}

    if title:
        info["title"] = title
        update_mask_parts.append("title")
    if description:
        info["description"] = description
        update_mask_parts.append("description")

    if not update_mask_parts:
        return {"formId": form_id, "updated": False}

    request = {
        "requests": [
            {
                "updateFormInfo": {
                    "info": info,
                    "updateMask": ",".join(update_mask_parts),
                }
            }
        ]
    }

    def _update():
        return service.forms().batchUpdate(formId=form_id, body=request).execute()

    await _run_sync(_update)
    return {"formId": form_id, "updated": True}


async def get_form(form_id: str) -> dict:
    """Read form structure. Returns {formId, title, description, items: [{title, type}]}."""
    service = await get_forms_service()
    if not service:
        raise RuntimeError("Forms API not available — not authenticated")

    def _get():
        return service.forms().get(formId=form_id).execute()

    form = await _run_sync(_get)
    items = []
    for item in form.get("items", []):
        q_item = item.get("questionItem", {})
        question = q_item.get("question", {})

        # Determine question type
        q_type = "UNKNOWN"
        if "textQuestion" in question:
            q_type = "PARAGRAPH" if question["textQuestion"].get("paragraph") else "SHORT_ANSWER"
        elif "choiceQuestion" in question:
            choice = question["choiceQuestion"]
            type_val = choice.get("type", "RADIO")
            reverse_map = {"RADIO": "MULTIPLE_CHOICE", "CHECKBOX": "CHECKBOX", "DROP_DOWN": "DROPDOWN"}
            q_type = reverse_map.get(type_val, "MULTIPLE_CHOICE")
        elif "scaleQuestion" in question:
            q_type = "SCALE"
        elif "dateQuestion" in question:
            q_type = "DATE"
        elif "timeQuestion" in question:
            q_type = "TIME"

        items.append({
            "title": item.get("title", ""),
            "type": q_type,
            "required": question.get("required", False),
        })

    return {
        "formId": form_id,
        "title": form.get("info", {}).get("title", ""),
        "description": form.get("info", {}).get("description", ""),
        "items": items,
    }


async def get_responses(form_id: str) -> dict:
    """Read form responses. Returns {formId, responseCount, responses: [...]]}."""
    service = await get_forms_service()
    if not service:
        raise RuntimeError("Forms API not available — not authenticated")

    def _get():
        return service.forms().responses().list(formId=form_id).execute()

    result = await _run_sync(_get)
    responses = result.get("responses", [])
    parsed = []
    for resp in responses:
        answers = {}
        for q_id, answer in resp.get("answers", {}).items():
            text_answers = answer.get("textAnswers", {}).get("answers", [])
            answers[q_id] = [a.get("value", "") for a in text_answers]
        parsed.append({
            "responseId": resp.get("responseId", ""),
            "createTime": resp.get("createTime", ""),
            "answers": answers,
        })

    return {
        "formId": form_id,
        "responseCount": len(parsed),
        "responses": parsed,
    }


async def list_recent_forms(max_results: int = 10) -> list[dict]:
    """List recent Google Forms via Drive API."""
    service = await get_drive_service()
    if not service:
        raise RuntimeError("Drive API not available — not authenticated")

    def _list():
        return service.files().list(
            q="mimeType='application/vnd.google-apps.form'",
            orderBy="modifiedTime desc",
            pageSize=max_results,
            fields="files(id,name,modifiedTime,webViewLink)",
        ).execute()

    result = await _run_sync(_list)
    return [
        {
            "id": f["id"],
            "name": f.get("name", ""),
            "url": f.get("webViewLink", f"https://docs.google.com/forms/d/{f['id']}/edit"),
            "modifiedTime": f.get("modifiedTime", ""),
        }
        for f in result.get("files", [])
    ]


# ---------------------------------------------------------------------------
# Operation definitions for the LLM planner
# ---------------------------------------------------------------------------

FORMS_OPERATIONS: list[OpDef] = [
    {
        "name": "create_form",
        "description": "Create a new Google Form",
        "parameters": "title: str, description: str = ''",
        "fn": create_form,
    },
    {
        "name": "add_question",
        "description": "Add a question to the form. Types: SHORT_ANSWER, PARAGRAPH, MULTIPLE_CHOICE, CHECKBOX, DROPDOWN, SCALE, DATE, TIME. For choice types, provide options list.",
        "parameters": "form_id: str, title: str, question_type: str = 'SHORT_ANSWER', options: list[str] = None, required: bool = False, description: str = '', low_label: str = '', high_label: str = ''",
        "fn": add_question,
    },
    {
        "name": "update_form_info",
        "description": "Update the form's title and/or description",
        "parameters": "form_id: str, title: str = '', description: str = ''",
        "fn": update_form_info,
    },
    {
        "name": "get_form",
        "description": "Read the form's structure (questions, types)",
        "parameters": "form_id: str",
        "fn": get_form,
    },
    {
        "name": "get_responses",
        "description": "Read all submitted responses for the form",
        "parameters": "form_id: str",
        "fn": get_responses,
    },
    {
        "name": "list_recent_forms",
        "description": "List recent Google Forms",
        "parameters": "max_results: int = 10",
        "fn": list_recent_forms,
    },
]

FORMS_OP_MAP = {op["name"]: op["fn"] for op in FORMS_OPERATIONS}


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------


class FormsPipeline(Pipeline):
    pipeline_type = "forms"
    display_name = "Google Forms"
    description = (
        "Create and edit Google Forms. Can create new forms, add questions, "
        "set response types, configure settings, and read responses."
    )
    uses_local_browser = False

    def can_handle(self, task_description: str) -> bool:
        import re
        # Use word-boundary matching to avoid false positives like
        # "information" matching "form" or "platform" matching "form"
        keywords = [r"\bgoogle forms?\b", r"\bsurvey\b", r"\bquestionnaire\b",
                    r"\bcreate a form\b", r"\bmake a form\b", r"\bbuild a form\b",
                    r"\bedit the form\b", r"\bfill out.{0,10}form\b"]
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
            pipeline_type="forms",
            service_name="Google Forms",
            task_description=task_description,
            available_ops=FORMS_OPERATIONS,
            op_map=FORMS_OP_MAP,
        )
