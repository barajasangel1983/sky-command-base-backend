import os
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import FileResponse

from .deps import get_current_user
from .schemas import FileEntry

router = APIRouter(prefix="/files", tags=["files"])

BASE_DIR = "/home/barajas_angel/data/dropbox"


def _ensure_base_dir() -> None:
    os.makedirs(BASE_DIR, exist_ok=True)


def _encode_id(rel_path: str) -> str:
    return rel_path.replace(os.sep, "__")


def _decode_id(file_id: str) -> str:
    rel = file_id.replace("__", os.sep)
    return rel


def _safe_join(folder: str, name: str) -> str:
    # folder is like "/research/papers" or "/"
    folder = folder or "/"
    if not folder.startswith("/"):
        folder = "/" + folder
    rel_folder = folder.lstrip("/")  # "" or "research/papers"
    base_path = os.path.join(BASE_DIR, rel_folder)
    norm = os.path.normpath(base_path)
    if not norm.startswith(os.path.abspath(BASE_DIR)):
        raise HTTPException(status_code=400, detail="Invalid folder path")
    os.makedirs(norm, exist_ok=True)
    # strip directory components from name
    name = os.path.basename(name)
    return os.path.join(norm, name)


def _list_files(folder: str) -> List[FileEntry]:
    _ensure_base_dir()
    folder = folder or "/"
    if not folder.startswith("/"):
        folder = "/" + folder
    rel_folder = folder.lstrip("/")
    base_path = os.path.join(BASE_DIR, rel_folder)
    norm = os.path.normpath(base_path)
    if not norm.startswith(os.path.abspath(BASE_DIR)):
        raise HTTPException(status_code=400, detail="Invalid folder path")
    if not os.path.exists(norm):
        return []

    entries: List[FileEntry] = []
    for name in os.listdir(norm):
        full_path = os.path.join(norm, name)
        if not os.path.isfile(full_path):
            continue
        stat = os.stat(full_path)
        rel_path = os.path.relpath(full_path, BASE_DIR)
        rel_dir = os.path.dirname(rel_path)
        folder_path = "/" + rel_dir.replace(os.sep, "/") if rel_dir and rel_dir != "." else "/"
        entries.append(
            FileEntry(
                id=_encode_id(rel_path),
                name=name,
                size=stat.st_size,
                type="application/octet-stream",
                uploadedAt=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                uploadedBy="unknown",
                folder=folder_path,
                url=f"/files/{_encode_id(rel_path)}/download",
            )
        )
    return entries


@router.get("", response_model=List[FileEntry])
async def list_files(
    folder: str = Query("/", description="Folder path, e.g. / or /research/papers"),
    _user = Depends(get_current_user),
) -> List[FileEntry]:
    return _list_files(folder)


@router.post("", response_model=FileEntry, status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    folder: str = Form("/"),
    user = Depends(get_current_user),
) -> FileEntry:
    _ensure_base_dir()
    target_path = _safe_join(folder, file.filename or "upload.bin")

    # Avoid overwriting: if exists, append timestamp
    if os.path.exists(target_path):
        root, ext = os.path.splitext(target_path)
        target_path = f"{root}-{int(datetime.utcnow().timestamp())}{ext}"

    with open(target_path, "wb") as f:
        content = await file.read()
        f.write(content)

    stat = os.stat(target_path)
    rel_path = os.path.relpath(target_path, BASE_DIR)
    rel_dir = os.path.dirname(rel_path)
    folder_path = "/" + rel_dir.replace(os.sep, "/") if rel_dir and rel_dir != "." else "/"

    return FileEntry(
        id=_encode_id(rel_path),
        name=os.path.basename(target_path),
        size=stat.st_size,
        type=file.content_type or "application/octet-stream",
        uploadedAt=datetime.fromtimestamp(stat.st_mtime).isoformat(),
        uploadedBy=user.username,
        folder=folder_path,
        url=f"/files/{_encode_id(rel_path)}/download",
    )


@router.delete("/{file_id}", status_code=204)
async def delete_file(
    file_id: str,
    _user = Depends(get_current_user),
) -> None:
    _ensure_base_dir()
    rel_path = _decode_id(file_id)
    full_path = os.path.normpath(os.path.join(BASE_DIR, rel_path))
    if not full_path.startswith(os.path.abspath(BASE_DIR)):
        raise HTTPException(status_code=400, detail="Invalid file id")
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="File not found")
    os.remove(full_path)
    return None


@router.get("/{file_id}/download")
async def download_file(
    file_id: str,
    _user = Depends(get_current_user),
) -> FileResponse:
    _ensure_base_dir()
    rel_path = _decode_id(file_id)
    full_path = os.path.normpath(os.path.join(BASE_DIR, rel_path))
    if not full_path.startswith(os.path.abspath(BASE_DIR)):
        raise HTTPException(status_code=400, detail="Invalid file id")
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(full_path, filename=os.path.basename(full_path))
