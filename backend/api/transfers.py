"""File backup transfer endpoints: upload, list, inspect, retry, and download."""

from __future__ import annotations

from typing import AsyncIterator

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse

from backend.schemas import TransferResponse
from backend.services.storage_service import FileTooLargeError
from backend.services.transfer_service import TransferConflictError, TransferNotFoundError

router = APIRouter(prefix="/api/transfers", tags=["transfers"])


async def _upload_chunk_iter(upload: UploadFile, chunk_size: int) -> AsyncIterator[bytes]:
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        yield chunk


@router.post("", response_model=TransferResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_transfer(
    request: Request,
    file: UploadFile = File(...),
) -> TransferResponse:
    transfer_service = request.app.state.transfer_service
    settings = request.app.state.settings

    try:
        record = await transfer_service.create_transfer_from_stream(
            raw_filename=file.filename,
            chunks=_upload_chunk_iter(file, settings.chunk_size_bytes),
        )
    except FileTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc
    finally:
        await file.close()

    return TransferResponse(**record)


@router.get("", response_model=list[TransferResponse])
async def list_transfers(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[TransferResponse]:
    transfer_service = request.app.state.transfer_service
    return [TransferResponse(**row) for row in transfer_service.list_transfers(limit=limit)]


@router.get("/{transfer_id}", response_model=TransferResponse)
async def get_transfer(request: Request, transfer_id: str) -> TransferResponse:
    transfer_service = request.app.state.transfer_service
    try:
        row = transfer_service.get_transfer(transfer_id)
    except TransferNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TransferResponse(**row)


@router.post("/{transfer_id}/retry", response_model=TransferResponse, status_code=status.HTTP_202_ACCEPTED)
async def retry_transfer(request: Request, transfer_id: str) -> TransferResponse:
    transfer_service = request.app.state.transfer_service
    try:
        row = transfer_service.retry_transfer(transfer_id)
    except TransferNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TransferConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return TransferResponse(**row)


@router.get("/{transfer_id}/download")
async def download_transfer(request: Request, transfer_id: str) -> FileResponse:
    transfer_service = request.app.state.transfer_service
    try:
        backup_path, original_filename = transfer_service.get_download_info(transfer_id)
    except TransferNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TransferConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return FileResponse(
        path=backup_path,
        filename=original_filename,
        media_type="application/octet-stream",
    )
