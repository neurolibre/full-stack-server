"""
Preprint (production) server API endpoints.

Handles Zenodo uploads, data/book synchronization, PDF generation,
and production repository setup.
"""

import os
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

# Import from parent api directory
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

# Import existing Celery tasks (will be refactored later)
from neurolibre_celery_tasks import (
    celery_app,
    sleep_task,
    zenodo_create_buckets_task,
    zenodo_upload_repository_task,
    zenodo_upload_book_task,
    zenodo_upload_data_task,
    zenodo_upload_docker_task,
    zenodo_publish_task,
    zenodo_flush_task,
    myst_upload_task,
    rsync_data_task,
    rsync_book_task,
    rsync_myst_prod_task,
    fork_configure_repository_task,
    binder_build_task,
    preprint_build_pdf_draft
)
from screening_client import ScreeningClient
from common import get_deposit_dir

from ..dependencies import verify_credentials
from ..models import (
    IDSchema,
    IdUrlSchema,
    BooksyncSchema,
    UploadSchema,
    ListSchema
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "/api/zenodo/buckets",
    summary="Create Zenodo deposit buckets",
    description="Create Zenodo deposit buckets for a preprint",
    tags=["Zenodo"],
    responses={
        200: {"description": "Bucket creation started"},
        422: {"description": "Invalid payload"}
    }
)
async def api_zenodo_create_buckets(
    data: IdUrlSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Create Zenodo deposit buckets.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="CREATE ZENODO BUCKETS",
        issue_id=data.id,
        target_repo_url=str(data.repository_url)
    )

    response = screening.start_celery_task(zenodo_create_buckets_task)
    return JSONResponse(content=response, status_code=200)


@router.post(
    "/api/zenodo/upload/repository",
    summary="Upload repository to Zenodo",
    description="Upload the forked repository to Zenodo",
    tags=["Zenodo"]
)
async def zenodo_upload_repository(
    data: IdUrlSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Upload repository to Zenodo.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="UPLOAD REPOSITORY TO ZENODO",
        issue_id=data.id,
        target_repo_url=str(data.repository_url)
    )

    response = screening.start_celery_task(zenodo_upload_repository_task)
    return JSONResponse(content=response, status_code=200)


@router.post(
    "/api/zenodo/upload/book",
    summary="Upload book to Zenodo",
    description="Upload the built Jupyter Book to Zenodo",
    tags=["Zenodo"]
)
async def zenodo_upload_book(
    data: IdUrlSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Upload Jupyter Book to Zenodo.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="UPLOAD BOOK TO ZENODO",
        issue_id=data.id,
        target_repo_url=str(data.repository_url)
    )

    response = screening.start_celery_task(zenodo_upload_book_task)
    return JSONResponse(content=response, status_code=200)


@router.post(
    "/api/zenodo/upload/data",
    summary="Upload data to Zenodo",
    description="Upload data files to Zenodo",
    tags=["Zenodo"]
)
async def zenodo_upload_data(
    data: IdUrlSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Upload data to Zenodo.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="UPLOAD DATA TO ZENODO",
        issue_id=data.id,
        target_repo_url=str(data.repository_url)
    )

    response = screening.start_celery_task(zenodo_upload_data_task)
    return JSONResponse(content=response, status_code=200)


@router.post(
    "/api/zenodo/upload/docker",
    summary="Upload Docker image to Zenodo",
    description="Upload Docker image to Zenodo",
    tags=["Zenodo"]
)
async def zenodo_upload_docker(
    data: IdUrlSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Upload Docker image to Zenodo.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="UPLOAD DOCKER TO ZENODO",
        issue_id=data.id,
        target_repo_url=str(data.repository_url)
    )

    response = screening.start_celery_task(zenodo_upload_docker_task)
    return JSONResponse(content=response, status_code=200)


@router.post(
    "/api/zenodo/upload/myst",
    summary="Upload MyST build to Zenodo",
    description="Upload MyST build artifacts to Zenodo",
    tags=["Zenodo"]
)
async def zenodo_upload_myst(
    data: IdUrlSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Upload MyST build to Zenodo.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="UPLOAD MYST TO ZENODO",
        issue_id=data.id,
        target_repo_url=str(data.repository_url)
    )

    response = screening.start_celery_task(myst_upload_task)
    return JSONResponse(content=response, status_code=200)


@router.post(
    "/api/zenodo/publish",
    summary="Publish Zenodo records",
    description="Publish all Zenodo deposits for a preprint",
    tags=["Zenodo"]
)
async def api_zenodo_publish(
    data: IdUrlSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Publish Zenodo records.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="PUBLISH ZENODO RECORDS",
        issue_id=data.id,
        target_repo_url=str(data.repository_url)
    )

    response = screening.start_celery_task(zenodo_publish_task)
    return JSONResponse(content=response, status_code=200)


@router.post(
    "/api/zenodo/status",
    summary="Get Zenodo status",
    description="Get the status of Zenodo deposits",
    tags=["Zenodo"]
)
async def api_zenodo_status(
    data: IDSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Get Zenodo deposit status.

    Requires authentication.
    """
    deposit_dir = get_deposit_dir(data.id)
    zenodo_status_file = os.path.join(deposit_dir, "zenodo_status.json")

    if not os.path.exists(zenodo_status_file):
        return JSONResponse(
            content={"message": "No Zenodo status file found"},
            status_code=404
        )

    with open(zenodo_status_file, 'r') as f:
        status_data = f.read()

    return JSONResponse(content=status_data, status_code=200)


@router.post(
    "/api/zenodo/list",
    summary="List Zenodo records",
    description="List all Zenodo deposits for a preprint",
    tags=["Zenodo"]
)
async def api_zenodo_list(
    data: ListSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    List Zenodo deposits.

    Requires authentication.
    """
    deposit_dir = get_deposit_dir(data.issue_id)

    if not os.path.exists(deposit_dir):
        return JSONResponse(
            content={"message": "No deposits found"},
            status_code=404
        )

    deposits = []
    for item in os.listdir(deposit_dir):
        item_path = os.path.join(deposit_dir, item)
        if os.path.isfile(item_path) and item.endswith('.json'):
            deposits.append(item)

    return JSONResponse(content={"deposits": deposits}, status_code=200)


@router.post(
    "/api/zenodo/flush",
    summary="Delete Zenodo records",
    description="Delete Zenodo deposit buckets and uploads",
    tags=["Zenodo"]
)
async def api_zenodo_flush(
    data: IdUrlSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Flush (delete) Zenodo deposits.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="FLUSH ZENODO RECORDS",
        issue_id=data.id,
        target_repo_url=str(data.repository_url)
    )

    response = screening.start_celery_task(zenodo_flush_task)
    return JSONResponse(content=response, status_code=200)


@router.post(
    "/api/zenodo/upload",
    summary="Upload item to Zenodo",
    description="Generic endpoint to upload any item (book, repository, data, docker) to Zenodo",
    tags=["Zenodo"]
)
async def api_upload(
    data: UploadSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Generic upload endpoint for Zenodo.

    Supports: book, repository, data, docker.
    Requires authentication.
    """
    # Map item types to tasks
    task_map = {
        "book": zenodo_upload_book_task,
        "repository": zenodo_upload_repository_task,
        "data": zenodo_upload_data_task,
        "docker": zenodo_upload_docker_task
    }

    task = task_map.get(data.item)
    if not task:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid item type: {data.item}"
        )

    screening = ScreeningClient(
        task_name=f"UPLOAD {data.item.upper()} TO ZENODO",
        issue_id=data.issue_id,
        target_repo_url=str(data.repository_address)
    )

    response = screening.start_celery_task(task)
    return JSONResponse(content=response, status_code=200)


@router.post(
    "/api/data/sync",
    summary="Sync data to production",
    description="Transfer data from preview to production server",
    tags=["Data"]
)
async def api_data_sync(
    data: IdUrlSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Sync data from preview to production.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="SYNC DATA TO PRODUCTION",
        issue_id=data.id,
        target_repo_url=str(data.repository_url)
    )

    response = screening.start_celery_task(rsync_data_task)
    return JSONResponse(content=response, status_code=200)


@router.post(
    "/api/book/sync",
    summary="Sync book to production",
    description="Transfer Jupyter Book from preview to production server",
    tags=["Books"]
)
async def api_book_sync(
    data: BooksyncSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Sync Jupyter Book from preview to production.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="SYNC BOOK TO PRODUCTION",
        issue_id=data.id,
        target_repo_url=str(data.repository_url)
    )

    response = screening.start_celery_task(rsync_book_task)
    return JSONResponse(content=response, status_code=200)


@router.post(
    "/api/sync/myst",
    summary="Sync MyST to production",
    description="Transfer MyST build from preview to production server",
    tags=["MyST"]
)
async def api_myst_sync(
    data: IdUrlSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Sync MyST build from preview to production.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="SYNC MYST TO PRODUCTION",
        issue_id=data.id,
        target_repo_url=str(data.repository_url)
    )

    response = screening.start_celery_task(rsync_myst_prod_task)
    return JSONResponse(content=response, status_code=200)


@router.post(
    "/api/production/start",
    summary="Start production setup",
    description="Fork and configure repository for production",
    tags=["Git"]
)
async def api_production_start(
    data: BooksyncSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Fork and configure repository for production.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="PRODUCTION SETUP",
        issue_id=data.id,
        target_repo_url=str(data.repository_url)
    )

    response = screening.start_celery_task(fork_configure_repository_task)
    return JSONResponse(content=response, status_code=200)


@router.post(
    "/api/binder/build",
    summary="Build on production BinderHub",
    description="Build Docker image on production BinderHub",
    tags=["Binder"]
)
async def api_binder_build(
    data: IdUrlSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Build on production BinderHub.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="BINDER BUILD",
        issue_id=data.id,
        target_repo_url=str(data.repository_url)
    )

    response = screening.start_celery_task(binder_build_task)
    return JSONResponse(content=response, status_code=200)


@router.post(
    "/api/pdf/draft",
    summary="Build extended PDF",
    description="Build extended PDF for submission",
    tags=["PDF"]
)
async def api_pdf_draft(
    data: IdUrlSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Build extended PDF for submission.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="BUILD PDF DRAFT",
        issue_id=data.id,
        target_repo_url=str(data.repository_url)
    )

    response = screening.start_celery_task(preprint_build_pdf_draft)
    return JSONResponse(content=response, status_code=200)


@router.get(
    "/api/test",
    summary="Authentication test",
    description="Test endpoint to verify authentication is working",
    tags=["Tests"],
    response_class=PlainTextResponse
)
async def api_preprint_test(user: Annotated[str, Depends(verify_credentials)]):
    """
    Test authentication.

    Returns authenticated username.
    """
    return PlainTextResponse(
        content=f"Authentication successful. User: {user}",
        status_code=200
    )


@router.get(
    "/api/celery/test",
    summary="Test Celery task",
    description="Trigger a test Celery task to verify worker is running",
    tags=["Tests"]
)
async def api_celery_test(user: Annotated[str, Depends(verify_credentials)]):
    """
    Test Celery worker.

    Triggers a simple sleep task and returns task ID.
    """
    task_result = sleep_task.apply_async(args=[5])
    return JSONResponse(
        content={'task_id': task_result.id, 'message': 'Test task started'},
        status_code=200
    )


@router.get(
    "/api/celery/test/{task_id}",
    summary="Get Celery task status",
    description="Get the status of a Celery task by ID",
    tags=["Tests"]
)
async def get_task_status_test(
    task_id: str,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Get Celery task status.

    Returns task state and result (if completed).
    """
    task_result = celery_app.AsyncResult(task_id)

    response = {
        'task_id': task_id,
        'state': task_result.state,
        'result': str(task_result.result) if task_result.result else None,
    }

    if task_result.state == 'FAILURE':
        response['error'] = str(task_result.info)

    return JSONResponse(content=response, status_code=200)
