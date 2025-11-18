"""
UI routes for template-based endpoints.

Provides HTML interfaces for log viewing and repository validation.
"""

import os
import json
import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

# Import from parent api directory
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
from common import load_yaml

# Load configuration
common_config = load_yaml('config/common.yaml')
DATA_ROOT_PATH = common_config['DATA_ROOT_PATH']
LOGS_FOLDER = common_config['LOGS_FOLDER']

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get(
    "/api/logs/{file_path:path}",
    summary="View log files",
    description="View log files with syntax highlighting in Monaco Editor",
    tags=["UI"],
    response_class=HTMLResponse
)
async def view_logs(request: Request, file_path: str):
    """
    Serve log viewer UI with Monaco Editor.

    Provides syntax highlighting and AI-powered chat assistance for analyzing logs.
    """
    try:
        log_file_path = os.path.join(DATA_ROOT_PATH, LOGS_FOLDER, file_path)

        with open(log_file_path, 'r') as f:
            content = f.read()

        # Safely encode content for JavaScript
        safe_content = json.dumps(content)

        # Get templates from app state
        templates: Jinja2Templates = request.app.state.templates

        return templates.TemplateResponse(
            "logs.html",
            {"request": request, "content": safe_content}
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Log file not found: {file_path}"
        )
    except Exception as e:
        logger.error(f"Error reading log file {file_path}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error reading log file: {str(e)}"
        )


@router.get(
    "/api/validate",
    summary="Repository validation UI",
    description="Interactive UI for validating NeuroLibre-compatible repositories",
    tags=["UI"],
    response_class=HTMLResponse
)
async def api_validate(request: Request):
    """
    Serve repository validation UI.

    Allows users to validate their repository structure for Jupyter Book or MyST format.
    """
    try:
        templates: Jinja2Templates = request.app.state.templates
        return templates.TemplateResponse(
            "validate.html",
            {"request": request}
        )
    except Exception as e:
        logger.error(f"Error rendering validation UI: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error loading validation UI: {str(e)}"
        )


# TODO: Implement /api/process endpoint
# This requires the validate_repository_structure function
# which needs to be ported from the Flask version

# @router.get(
#     "/api/process",
#     summary="Repository validation processor",
#     description="Server-Sent Events endpoint for real-time repository validation",
#     tags=["UI"]
# )
# async def api_process(
#     repository_url: str,
#     format_style: str = "jupyter-book"
# ):
#     """
#     Process repository validation with Server-Sent Events (SSE).
#     
#     TODO: Implement this endpoint
#     """
#     pass