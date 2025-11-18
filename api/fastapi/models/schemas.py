"""
Pydantic schemas for API request/response validation.

These schemas replace the Marshmallow schemas from api/schema.py,
providing type-safe validation with better performance and IDE support.
"""

from pydantic import BaseModel, Field, HttpUrl, EmailStr
from typing import Literal


# ============================================================================
# Common Schemas (shared between preview and preprint)
# ============================================================================

class StatusSchema(BaseModel):
    """Review issue ID for forwarded requests"""

    id: int | None = Field(
        None,
        description="Review issue ID if request is forwarded through robo.neurolibre.org"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{"id": 42}]
        }
    }


class UnlockSchema(BaseModel):
    """Remove build lock for a target repository"""

    repo_url: HttpUrl = Field(
        ...,
        description="Full URL of the target repository"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{"repo_url": "https://github.com/user/repo"}]
        }
    }


class TaskSchema(BaseModel):
    """Celery task ID"""

    task_id: str = Field(
        ...,
        description="Celery task ID"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{"task_id": "1234-5678-abcd-efgh"}]
        }
    }


class BookSchema(BaseModel):
    """Query parameters for retrieving NeuroLibre reproducible preprints"""

    user_name: str | None = Field(
        None,
        description="Filter by user (owner) name (suggested to be used with repo_name)"
    )
    commit_hash: str | None = Field(
        None,
        description="Filter by commit hash"
    )
    repo_name: str | None = Field(
        None,
        description="Filter by repository name (suggested to be used with user_name)"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"user_name": "johndoe", "repo_name": "my-paper"},
                {"commit_hash": "abc123"}
            ]
        }
    }


# ============================================================================
# Preview Server Schemas
# ============================================================================

class DownloadSchema(BaseModel):
    """Schema for repo2data download requests"""

    repository_url: HttpUrl = Field(
        ...,
        description="Full URL of a NeuroLibre compatible repository"
    )
    id: int | None = Field(
        None,
        description="Issue number of the technical screening. If provided, response will be posted to GitHub issue."
    )
    email: EmailStr | None = Field(
        None,
        description="Email address to receive the result"
    )
    is_overwrite: bool = Field(
        False,
        description="Whether to overwrite existing data"
    )
    external_repo: str | None = Field(
        None,
        description="A non-review repository"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "repository_url": "https://github.com/user/repo",
                "id": 42,
                "is_overwrite": False
            }]
        }
    }


class BuildSchema(BaseModel):
    """Schema for Jupyter Book build requests"""

    id: int = Field(
        ...,
        description="Issue number of the technical screening"
    )
    repo_url: HttpUrl = Field(
        ...,
        description="Full URL of a NeuroLibre compatible repository"
    )
    commit_hash: str = Field(
        "HEAD",
        description="Commit SHA to checkout for building. Defaults to HEAD."
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "id": 42,
                "repo_url": "https://github.com/user/repo",
                "commit_hash": "abc123def456"
            }]
        }
    }


class MystBuildSchema(BaseModel):
    """Schema for MyST format article build requests"""

    id: int = Field(
        ...,
        description="Issue number of the technical screening"
    )
    repository_url: HttpUrl = Field(
        ...,
        description="Full URL of a NeuroLibre compatible repository"
    )
    commit_hash: str = Field(
        "HEAD",
        description="Commit SHA to checkout for building. Defaults to HEAD."
    )
    binder_hash: str = Field(
        "HEAD",
        description="Commit SHA at which a binder image was built successfully"
    )
    is_prod: bool = Field(
        False,
        description="Whether the build is intended for production"
    )
    build_cache: bool = Field(
        True,
        description="Whether to use the MyST build cache"
    )
    prod_version: str = Field(
        "v1",
        description="Version suffix to define the build directory"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "id": 42,
                "repository_url": "https://github.com/user/repo",
                "commit_hash": "abc123",
                "is_prod": False,
                "build_cache": True
            }]
        }
    }


class BuildTestSchema(BaseModel):
    """Schema for book build requests from robo.neurolibre.org"""

    repo_url: HttpUrl = Field(
        ...,
        description="Full URL of a NeuroLibre compatible repository"
    )
    commit_hash: str = Field(
        "HEAD",
        description="Commit SHA to checkout for building. Defaults to HEAD."
    )
    email: EmailStr = Field(
        ...,
        description="Email address to send the response"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "repo_url": "https://github.com/user/repo",
                "commit_hash": "abc123",
                "email": "user@example.com"
            }]
        }
    }


class IDSchema(BaseModel):
    """Simple issue ID schema"""

    id: int = Field(
        ...,
        description="Issue number of the technical screening"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{"id": 42}]
        }
    }


# ============================================================================
# Preprint Server Schemas
# ============================================================================

class UploadSchema(BaseModel):
    """Schema for Zenodo upload requests"""

    issue_id: int = Field(
        ...,
        description="Issue number of the technical screening"
    )
    repository_address: HttpUrl = Field(
        ...,
        description="Full URL of the repository submitted by the author"
    )
    item: Literal["book", "repository", "data", "docker"] = Field(
        ...,
        description="Type of item to upload: book, repository, data, or docker"
    )
    item_arg: str = Field(
        ...,
        description="Additional information to locate the item on the server"
    )
    fork_url: HttpUrl = Field(
        ...,
        description="Full URL of the forked (roboneurolibre) repository"
    )
    commit_fork: str = Field(
        ...,
        description="Commit SHA at which the forked repository will be deposited"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "issue_id": 42,
                "repository_address": "https://github.com/author/repo",
                "item": "book",
                "item_arg": "v1",
                "fork_url": "https://github.com/roboneurolibre/repo",
                "commit_fork": "abc123"
            }]
        }
    }


class ListSchema(BaseModel):
    """Schema for listing Zenodo records"""

    issue_id: int = Field(
        ...,
        description="Issue number of the technical screening"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{"issue_id": 42}]
        }
    }


class IdUrlSchema(BaseModel):
    """Schema combining issue ID and repository URL"""

    id: int = Field(
        ...,
        description="Issue number of the technical screening"
    )
    repository_url: HttpUrl = Field(
        ...,
        description="Full URL of the target repository"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "id": 42,
                "repository_url": "https://github.com/user/repo"
            }]
        }
    }


class IdUrlPreprintVersionSchema(BaseModel):
    """Schema with issue ID, repository URL, and preprint version"""

    id: int = Field(
        ...,
        description="Issue number of the technical screening"
    )
    repository_url: HttpUrl = Field(
        ...,
        description="Full URL of the target repository"
    )
    preprint_version: str = Field(
        "v2",
        description="Preprint version"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "id": 42,
                "repository_url": "https://github.com/user/repo",
                "preprint_version": "v2"
            }]
        }
    }


class BooksyncSchema(BaseModel):
    """Schema for book synchronization requests"""

    id: int = Field(
        ...,
        description="Issue number of the technical screening"
    )
    repository_url: HttpUrl = Field(
        ...,
        description="Full URL of the target repository"
    )
    commit_hash: str = Field(
        "HEAD",
        description="Commit hash to sync"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "id": 42,
                "repository_url": "https://github.com/user/repo",
                "commit_hash": "abc123"
            }]
        }
    }
