"""Google Drive pipeline — file management and organization via the Drive API.

Operations are thin async wrappers around the Google Drive API.
The LLM plans which operations to call; the gsuite_executor runs them.
"""

from __future__ import annotations

from typing import Any

from backend.agent.pipelines.base import Pipeline
from backend.agent.gsuite_executor import plan_and_execute, OpDef
from backend.services.google_api import get_drive_service, _run_sync


# ---------------------------------------------------------------------------
# API Operations
# ---------------------------------------------------------------------------


async def list_files(
    query: str = "",
    folder_id: str = "",
    page_size: int = 20,
) -> dict:
    """List/search files in Drive. Returns {files: [{id, name, mimeType, url, modifiedTime}]}."""
    service = await get_drive_service()
    if not service:
        raise RuntimeError("Drive API not available — not authenticated")

    q_parts = []
    if query:
        q_parts.append(f"(name contains '{query}' or fullText contains '{query}')")
    if folder_id:
        q_parts.append(f"'{folder_id}' in parents")
    q_parts.append("trashed = false")
    q_str = " and ".join(q_parts)

    def _list():
        return service.files().list(
            q=q_str,
            orderBy="modifiedTime desc",
            pageSize=page_size,
            fields="files(id,name,mimeType,modifiedTime,webViewLink,parents)",
        ).execute()

    result = await _run_sync(_list)
    return {
        "files": [
            {
                "id": f["id"],
                "name": f.get("name", ""),
                "mimeType": f.get("mimeType", ""),
                "url": f.get("webViewLink", ""),
                "modifiedTime": f.get("modifiedTime", ""),
                "parents": f.get("parents", []),
            }
            for f in result.get("files", [])
        ]
    }


async def create_folder(name: str, parent_id: str = "") -> dict:
    """Create a folder in Drive. Returns {fileId, name, url}."""
    service = await get_drive_service()
    if not service:
        raise RuntimeError("Drive API not available — not authenticated")

    metadata: dict[str, Any] = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    def _create():
        return service.files().create(
            body=metadata,
            fields="id,name,webViewLink",
        ).execute()

    result = await _run_sync(_create)
    return {
        "fileId": result["id"],
        "name": result.get("name", name),
        "url": result.get("webViewLink", f"https://drive.google.com/drive/folders/{result['id']}"),
    }


async def move_file(file_id: str, new_parent_id: str) -> dict:
    """Move a file to a different folder. Returns {fileId, parents}."""
    service = await get_drive_service()
    if not service:
        raise RuntimeError("Drive API not available — not authenticated")

    # Get current parents
    def _get():
        return service.files().get(fileId=file_id, fields="parents").execute()

    file_info = await _run_sync(_get)
    current_parents = ",".join(file_info.get("parents", []))

    def _move():
        return service.files().update(
            fileId=file_id,
            addParents=new_parent_id,
            removeParents=current_parents,
            fields="id,parents",
        ).execute()

    result = await _run_sync(_move)
    return {
        "fileId": result["id"],
        "parents": result.get("parents", []),
    }


async def rename_file(file_id: str, new_name: str) -> dict:
    """Rename a file. Returns {fileId, name}."""
    service = await get_drive_service()
    if not service:
        raise RuntimeError("Drive API not available — not authenticated")

    def _rename():
        return service.files().update(
            fileId=file_id,
            body={"name": new_name},
            fields="id,name",
        ).execute()

    result = await _run_sync(_rename)
    return {
        "fileId": result["id"],
        "name": result.get("name", new_name),
    }


async def share_file(file_id: str, email: str, role: str = "reader") -> dict:
    """Share a file with a user. Role: reader, writer, commenter. Returns {fileId, permissionId}."""
    service = await get_drive_service()
    if not service:
        raise RuntimeError("Drive API not available — not authenticated")

    permission = {
        "type": "user",
        "role": role,
        "emailAddress": email,
    }

    def _share():
        return service.permissions().create(
            fileId=file_id,
            body=permission,
            sendNotificationEmail=True,
            fields="id",
        ).execute()

    result = await _run_sync(_share)
    return {
        "fileId": file_id,
        "permissionId": result.get("id", ""),
        "sharedWith": email,
        "role": role,
    }


async def get_file_info(file_id: str) -> dict:
    """Get file metadata. Returns {fileId, name, mimeType, size, url, owners, modifiedTime}."""
    service = await get_drive_service()
    if not service:
        raise RuntimeError("Drive API not available — not authenticated")

    def _get():
        return service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,size,webViewLink,owners,modifiedTime,createdTime,shared",
        ).execute()

    f = await _run_sync(_get)
    owners = [o.get("emailAddress", "") for o in f.get("owners", [])]
    return {
        "fileId": f["id"],
        "name": f.get("name", ""),
        "mimeType": f.get("mimeType", ""),
        "size": f.get("size", "0"),
        "url": f.get("webViewLink", ""),
        "owners": owners,
        "modifiedTime": f.get("modifiedTime", ""),
        "createdTime": f.get("createdTime", ""),
        "shared": f.get("shared", False),
    }


async def copy_file(file_id: str, new_name: str = "") -> dict:
    """Create a copy of a file. Returns {fileId, name, url}."""
    service = await get_drive_service()
    if not service:
        raise RuntimeError("Drive API not available — not authenticated")

    body: dict[str, Any] = {}
    if new_name:
        body["name"] = new_name

    def _copy():
        return service.files().copy(
            fileId=file_id,
            body=body,
            fields="id,name,webViewLink",
        ).execute()

    result = await _run_sync(_copy)
    return {
        "fileId": result["id"],
        "name": result.get("name", ""),
        "url": result.get("webViewLink", ""),
    }


async def delete_file(file_id: str) -> dict:
    """Move a file to trash. Returns {fileId, trashed}."""
    service = await get_drive_service()
    if not service:
        raise RuntimeError("Drive API not available — not authenticated")

    def _trash():
        return service.files().update(
            fileId=file_id,
            body={"trashed": True},
            fields="id,trashed",
        ).execute()

    result = await _run_sync(_trash)
    return {
        "fileId": result["id"],
        "trashed": result.get("trashed", True),
    }


async def search_files(query: str, max_results: int = 20) -> dict:
    """Full-text search across Drive. Returns {files: [{id, name, mimeType, url}]}."""
    return await list_files(query=query, page_size=max_results)


# ---------------------------------------------------------------------------
# Operation definitions for the LLM planner
# ---------------------------------------------------------------------------

DRIVE_OPERATIONS: list[OpDef] = [
    {
        "name": "list_files",
        "description": "List files in Drive, optionally filtered by text query and/or folder",
        "parameters": "query: str = '', folder_id: str = '', page_size: int = 20",
        "fn": list_files,
    },
    {
        "name": "create_folder",
        "description": "Create a new folder in Drive",
        "parameters": "name: str, parent_id: str = ''",
        "fn": create_folder,
    },
    {
        "name": "move_file",
        "description": "Move a file to a different folder",
        "parameters": "file_id: str, new_parent_id: str",
        "fn": move_file,
    },
    {
        "name": "rename_file",
        "description": "Rename a file",
        "parameters": "file_id: str, new_name: str",
        "fn": rename_file,
    },
    {
        "name": "share_file",
        "description": "Share a file with a user by email. Roles: reader, writer, commenter",
        "parameters": "file_id: str, email: str, role: str = 'reader'",
        "fn": share_file,
    },
    {
        "name": "get_file_info",
        "description": "Get detailed file metadata (name, type, size, owners, sharing)",
        "parameters": "file_id: str",
        "fn": get_file_info,
    },
    {
        "name": "copy_file",
        "description": "Create a copy of a file with optional new name",
        "parameters": "file_id: str, new_name: str = ''",
        "fn": copy_file,
    },
    {
        "name": "delete_file",
        "description": "Move a file to trash",
        "parameters": "file_id: str",
        "fn": delete_file,
    },
    {
        "name": "search_files",
        "description": "Full-text search across all Drive files",
        "parameters": "query: str, max_results: int = 20",
        "fn": search_files,
    },
]

DRIVE_OP_MAP = {op["name"]: op["fn"] for op in DRIVE_OPERATIONS}


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------


class DrivePipeline(Pipeline):
    pipeline_type = "drive"
    display_name = "Google Drive"
    description = (
        "Manage files in Google Drive. Can create folders, organize files, "
        "set sharing permissions, search for files, and manage storage."
    )
    uses_local_browser = False

    def can_handle(self, task_description: str) -> bool:
        import re
        keywords = [
            r"\bgoogle drive\b", r"\bfile management\b", r"\bshare.{0,10}file\b",
            r"\bupload.{0,10}(file|folder)\b",
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
            pipeline_type="drive",
            service_name="Google Drive",
            task_description=task_description,
            available_ops=DRIVE_OPERATIONS,
            op_map=DRIVE_OP_MAP,
        )
