"""
Preview server API endpoints.

Handles data downloads, book builds, MyST builds, and GitHub synchronization
for the preview/testing environment.
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
    preview_download_data,
    preview_build_book_task,
    preview_build_book_test_task,
    preview_build_myst_task,
    sync_fork_from_upstream_task
)
from screening_client import ScreeningClient

from ..dependencies import verify_credentials
from ..models import (
    DownloadSchema,
    BuildSchema,
    BuildTestSchema,
    IdUrlPreprintVersionSchema,
    MystBuildSchema,
    TaskSchema
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "/api/data/cache",
    summary="Download data via repo2data",
    description="Download data for a repository using repo2data",
    tags=["Data"],
    responses={
        200: {"description": "Data download started"},
        422: {"description": "Invalid payload"}
    }
)
async def api_download_data(
    data: DownloadSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Download data via repo2data.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="DOWNLOAD (CACHE) DATA",
        issue_id=data.id,
        target_repo_url=str(data.repository_url),
        email_address=data.email
    )

    response = screening.start_celery_task(preview_download_data)
    return JSONResponse(content=response, status_code=200)


@router.post(
    "/api/book/build",
    summary="Build Jupyter Book",
    description="Build a Jupyter Book on the preview server",
    tags=["Books"],
    responses={
        200: {"description": "Build started"},
        422: {"description": "Invalid payload"}
    }
)
async def api_book_build(
    data: BuildSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Build Jupyter Book on preview server.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="BUILD (JUPYTER BOOK)",
        issue_id=data.id,
        target_repo_url=str(data.repo_url)
    )

    response = screening.start_celery_task(preview_build_book_task)
    return JSONResponse(content=response, status_code=200)


@router.post(
    "/api/myst/build",
    summary="Build MyST article",
    description="Build a MyST format article on the preview server",
    tags=["MyST"],
    responses={
        200: {"description": "MyST build started"},
        422: {"description": "Invalid payload"}
    }
)
async def api_myst_build(
    data: MystBuildSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Build MyST article on preview server.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="BUILD (MYST)",
        issue_id=data.id,
        target_repo_url=str(data.repository_url)
    )

    response = screening.start_celery_task(preview_build_myst_task)
    return JSONResponse(content=response, status_code=200)


@router.post(
    "/api/book/build/test",
    summary="Build book from robo.neurolibre.org",
    description="Build Jupyter Book for test requests (without GitHub issue tracking)",
    tags=["Books"],
    responses={
        200: {"description": "Test build started"},
        422: {"description": "Invalid payload"}
    }
)
async def api_book_build_test(
    data: BuildTestSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Build Jupyter Book for test requests from robo.neurolibre.org.

    Requires authentication.
    """
    payload = {
        'repo_url': str(data.repo_url),
        'commit_hash': data.commit_hash,
        'email': data.email,
        'task_name': "TEST BUILD (JUPYTER BOOK)"
    }

    task_result = preview_build_book_test_task.apply_async(args=[payload])
    return JSONResponse(
        content={'task_id': task_result.id},
        status_code=200
    )


@router.post(
    "/api/sync/fork",
    summary="Sync fork from upstream",
    description="Synchronize forked repository from upstream",
    tags=["Git"],
    responses={
        200: {"description": "Fork sync started"},
        422: {"description": "Invalid payload"}
    }
)
async def api_sync_fork_from_upstream(
    data: IdUrlPreprintVersionSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Sync fork from upstream repository.

    Requires authentication.
    """
    screening = ScreeningClient(
        task_name="SYNC FORK",
        issue_id=data.id,
        target_repo_url=str(data.repository_url)
    )

    response = screening.start_celery_task(sync_fork_from_upstream_task)
    return JSONResponse(content=response, status_code=200)


@router.get(
    "/api/test",
    summary="Authentication test",
    description="Test endpoint to verify authentication is working",
    tags=["Tests"],
    response_class=PlainTextResponse
)
async def api_preview_test(user: Annotated[str, Depends(verify_credentials)]):
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
