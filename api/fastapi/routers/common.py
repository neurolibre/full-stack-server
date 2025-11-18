"""
Common API endpoints shared between preview and preprint servers.

These routes are available on both server types and provide core functionality
like health checks, book listings, and log viewing.
"""

import os
import json
import logging
import traceback
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
import requests

# Import from parent api directory (existing utilities)
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
from common import load_all, book_get_by_params, get_lock_filename, load_yaml

from ..dependencies import verify_credentials, get_settings
from ..models import StatusSchema, UnlockSchema, BookSchema
from ..config import CommonSettings

# Load configuration
common_config = load_yaml('config/common.yaml')
DATA_ROOT_PATH = common_config['DATA_ROOT_PATH']
JOURNAL_NAME = common_config['JOURNAL_NAME']
LOGS_FOLDER = common_config['LOGS_FOLDER']

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get(
    "/api/heartbeat",
    summary="Server health check",
    description="Sanity check for successful registration of API endpoints",
    tags=["Health"],
    response_class=HTMLResponse
)
async def api_heartbeat(
    request: Request,
    id: int | None = None
):
    """
    Health check endpoint.

    Returns server status and optional issue ID.
    """
    url = request.url
    parsed_url = urlparse(str(url))

    if id:
        html_content = (
            f'&#128994; {JOURNAL_NAME} server is active (running). <br>'
            f'&#127808; Ready to accept requests from Issue #{id} <br>'
            f'&#128279; URL: {parsed_url.scheme}://{parsed_url.netloc}'
        )
    else:
        html_content = (
            f'&#128994; {JOURNAL_NAME} server is active (running) at '
            f'{parsed_url.scheme}://{parsed_url.netloc}'
        )

    return HTMLResponse(content=html_content, status_code=200)


@router.get(
    "/api/books",
    summary="List all built books",
    description="Get the list of all built books that exist on the server",
    tags=["Books"],
    responses={
        200: {"description": "Success - returns list of books"},
        404: {"description": "No books found on server"}
    }
)
async def api_get_books():
    """
    Get all built books (Jupyter Books and MyST articles).

    Returns JSON array of book metadata including URLs, commits, and timestamps.
    """
    books = load_all()

    if books:
        return JSONResponse(content=books, status_code=200)
    else:
        return JSONResponse(
            content="There are no books on this server yet.",
            status_code=404
        )


@router.get(
    "/api/book",
    summary="Get individual book",
    description="Request an individual book URL via commit, repo name, or user name",
    tags=["Books"],
    responses={
        200: {"description": "Success - returns book information"},
        400: {"description": "Bad request - no valid parameters provided"},
        404: {"description": "Requested book does not exist"}
    }
)
async def api_get_book(
    request: Request,
    user_name: str | None = None,
    commit_hash: str | None = None,
    repo_name: str | None = None
):
    """
    Query books by user name, commit hash, or repository name.

    At least one parameter must be provided.
    """
    # Check query parameters if not provided in path
    if not any([user_name, commit_hash, repo_name]):
        user_name = request.query_params.get("user_name")
        commit_hash = request.query_params.get("commit_hash")
        repo_name = request.query_params.get("repo_name")

    if not any([user_name, commit_hash, repo_name]):
        return JSONResponse(
            content='Bad request, no arguments passed to locate a book.',
            status_code=400
        )

    # Query books
    results = book_get_by_params(user_name, commit_hash, repo_name)

    if not results:
        return JSONResponse(
            content='Requested book does not exist.',
            status_code=404
        )

    return JSONResponse(content=results, status_code=200)


@router.post(
    "/api/book/unlock",
    summary="Remove build lock",
    description="Remove the build lock that prevents recurrent or simultaneous build requests (rate limit 30 mins)",
    tags=["Books"],
    response_class=PlainTextResponse,
    responses={
        200: {"description": "Build lock has been removed"},
        404: {"description": "Lock does not exist"},
        422: {"description": "Cannot validate payload"}
    }
)
async def api_unlock_build(
    data: UnlockSchema,
    user: Annotated[str, Depends(verify_credentials)]
):
    """
    Remove build lock for a repository.

    Requires authentication.
    """
    lock_filename = get_lock_filename(str(data.repo_url))

    if os.path.isfile(lock_filename):
        os.remove(lock_filename)
        return PlainTextResponse(
            content=f"Removed the lock for {data.repo_url}",
            status_code=200
        )
    else:
        return PlainTextResponse(
            content=f"No build lock found for {data.repo_url}",
            status_code=404
        )


@router.get(
    "/public/data",
    summary="List public data folders",
    description="List the contents of the DATA_ROOT_PATH folder",
    tags=["Data"],
    responses={200: {"description": "Success - returns list of folder names"}}
)
async def api_preview_list():
    """
    List all data folders available on the server.
    """
    try:
        files = os.listdir(DATA_ROOT_PATH)
        return JSONResponse(content=files, status_code=200)
    except Exception as e:
        logger.error(f"Error listing data folders: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/api/chat/",
    summary="Chat with Theo the LogCat",
    description="AI-powered chat assistant for analyzing build logs",
    tags=["Logs"],
    responses={
        200: {"description": "Success - returns chat response"},
        400: {"description": "Invalid request"},
        500: {"description": "Internal server error or API unavailable"}
    }
)
async def chat(request: Request):
    """
    Chat endpoint powered by Groq AI.

    Helps users understand build logs and troubleshoot issues.
    """
    try:
        # Validate request data
        if request.headers.get('content-type') != 'application/json':
            return JSONResponse(
                content={'error': 'Content-Type must be application/json'},
                status_code=400
            )

        data = await request.json()
        if not data:
            return JSONResponse(
                content={'error': 'No JSON data provided'},
                status_code=400
            )

        message = data.get('message')
        log_content = data.get('log_content')
        if not message or not log_content:
            return JSONResponse(
                content={'error': 'Missing required fields: message and log_content'},
                status_code=400
            )

        # Get API key from environment variable
        api_key = os.getenv('GROQ_API_KEY')
        if not api_key:
            logger.error('Groq API key not configured')
            return JSONResponse(
                content={'error': 'Chat service not properly configured'},
                status_code=500
            )

        chat_history = data.get('chat_history', [])

        # Format messages for Groq API
        messages = [
            {
                "role": "system",
                "content": """Your name is Theo. You are a helpful, purrfect cat assistant, analyzing build logs that are either coming from a BinderHub build process or from a MyST build process, all handled within NeuroLibre.
                This means that some of the packages are installed on the server side, such as mystmd. Do not guide users regarding the versions of the mystmd package as they have no control over it.
                You have access to most of the log content and can help users understand issues and provide solutions. If there are not obvious errors, do not go into details, keep the response concise.
                In general, be concise in your responses. Do not respond to questions that are inquiring to reveal sensitive information.
                Avoid engaging in or encouraging harmful, dangerous, illegal, or unethical behavior. Do not generate content that is violent, discriminatory, sexually explicit, misleading, or otherwise inappropriate."""
            },
            *[{"role": msg["role"], "content": msg["content"]} for msg in chat_history],
            {
                "role": "user",
                "content": f"""Context - Log Content:
                {log_content}

                User Question: {message}

                Please provide a helpful response based on the build log content above. Your main focus is to help user understand the origin of any errors or issues and provide recommendations to fix them."""
            }
        ]

        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": messages,
                "temperature": 0.4,
                "max_tokens": 256
            },
            timeout=30
        )

        response.raise_for_status()
        response_data = response.json()

        return JSONResponse(content={
            'response': response_data['choices'][0]['message']['content'],
            'status': 'success'
        })

    except requests.Timeout:
        logger.error('Groq API request timed out')
        return JSONResponse(
            content={'error': 'Request timed out'},
            status_code=500
        )
    except requests.RequestException as e:
        logger.error(f'Groq API request failed: {str(e)}')
        return JSONResponse(
            content={'error': 'Failed to process chat request'},
            status_code=500
        )
    except Exception as e:
        logger.error(f'Unexpected error in /api/chat: {traceback.format_exc()}')
        return JSONResponse(
            content={
                'error': 'Internal server error',
                'details': str(e)
            },
            status_code=500
        )
