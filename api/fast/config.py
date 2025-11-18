"""
Configuration management using Pydantic Settings.

Replaces Flask's YAML-based configuration with type-safe Pydantic models.
"""

import os
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings
import yaml


def load_yaml_file(file_path: str) -> dict:
    """Load YAML configuration file"""
    with open(file_path, 'r') as f:
        return yaml.safe_load(f)


class CommonSettings(BaseSettings):
    """Common configuration shared by preview and preprint servers"""

    # Server configuration
    server_domain: str = Field(default="neurolibre.org", description="Main server domain")
    gh_organization: str = Field(default="roboneurolibre", description="GitHub organization")
    review_repository: str = Field(default="neurolibre/neurolibre-reviews", description="Review repository")

    # DOI configuration
    doi_prefix: str = Field(default="10.55458", description="DOI prefix")
    doi_suffix: str = Field(default="neurolibre", description="DOI suffix")

    # Journal information
    journal_name: str = Field(default="NeuroLibre", description="Journal name")
    journal_subject: str = Field(default="Neuroscience", description="Journal subject")
    journal_twitter: str = Field(default="@neurolibre", description="Journal Twitter handle")

    # Paths
    data_root_path: str = Field(default="/DATA", description="Root data directory")
    jb_root_folder: str = Field(default="book-artifacts", description="Jupyter Book artifacts folder")
    myst_folder: str = Field(default="myst", description="MyST artifacts folder")
    logs_folder: str = Field(default="logs", description="Logs folder")
    data_nfs_path: str = Field(default="/nfs/DATA", description="NFS data path")
    papers_path: str = Field(default="/papers", description="Papers directory")

    # Binder configuration
    binder_registry: str = Field(default="conp", description="Binder registry name")

    # Container configuration
    container_myst_source_path: str = Field(default="/home/jovyan/source", description="Container MyST source path")
    container_myst_data_path: str = Field(default="/home/jovyan/data", description="Container MyST data path")
    noexec_container_repository: str = Field(default="", description="No-exec container repository")
    noexec_container_commit_hash: str = Field(default="", description="No-exec container commit hash")

    # Publishing
    publish_license: str = Field(default="CC BY 4.0", description="Publishing license")

    # Secrets (from environment variables)
    auth_key: str = Field(..., description="Path to htpasswd authentication file")
    gh_bot: str = Field(..., description="GitHub bot token")
    oai_token: str | None = Field(None, description="OpenAI API token")
    groq_api_key: str | None = Field(None, description="Groq API key for chat")

    # AWS SES for emails
    aws_access_key_id: str | None = Field(None, description="AWS access key")
    aws_secret_access_key: str | None = Field(None, description="AWS secret key")
    aws_region: str = Field(default="us-east-1", description="AWS region")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "allow"  # Allow extra fields from YAML

    @classmethod
    def from_yaml(cls, *yaml_files: str, **env_overrides):
        """Load configuration from YAML files and environment variables"""
        config = {}

        # Load and merge YAML files
        for yaml_file in yaml_files:
            if os.path.exists(yaml_file):
                yaml_data = load_yaml_file(yaml_file)
                # Convert to lowercase keys for case-insensitive matching
                config.update({k.lower(): v for k, v in yaml_data.items()})

        # Override with environment variables
        config.update(env_overrides)

        return cls(**config)


class PreviewSettings(CommonSettings):
    """Preview server specific configuration"""

    server_slug: str = Field(default="preview", description="Server slug")
    binder_name: str = Field(default="binder-preview", description="Binder name for preview")
    binder_domain: str = Field(default="conp.cloud", description="Binder domain for preview")

    @classmethod
    def load(cls):
        """Load preview configuration from YAML files"""
        base_path = os.path.join(os.path.dirname(__file__), '..', 'config')
        return cls.from_yaml(
            os.path.join(base_path, 'common.yaml'),
            os.path.join(base_path, 'preview.yaml')
        )


class PreprintSettings(CommonSettings):
    """Preprint (production) server specific configuration"""

    server_slug: str = Field(default="preprint", description="Server slug")
    binder_name: str = Field(default="binder-mcgill", description="Binder name for production")
    binder_domain: str = Field(default="conp.cloud", description="Binder domain for production")

    # Zenodo (production only)
    zenodo_api: str = Field(..., description="Zenodo API token")

    # Production-specific
    jb_interface_override: bool = Field(default=False, description="Override Jupyter Book interface")

    # Docker registry
    docker_private_registry_url: str | None = Field(None, description="Docker private registry URL")
    docker_private_registry_user: str | None = Field(None, description="Docker private registry user")
    docker_private_registry_pass: str | None = Field(None, description="Docker private registry password")

    @classmethod
    def load(cls):
        """Load preprint configuration from YAML files"""
        base_path = os.path.join(os.path.dirname(__file__), '..', 'config')
        return cls.from_yaml(
            os.path.join(base_path, 'common.yaml'),
            os.path.join(base_path, 'preprint.yaml')
        )


# Convenience functions for derived URLs
def get_binderhub_url(settings: CommonSettings) -> str:
    """Get BinderHub URL from settings"""
    return f"https://{settings.binder_name}.{settings.binder_domain}"


def get_server_url(settings: CommonSettings) -> str:
    """Get server URL from settings"""
    return f"https://{settings.server_slug}.{settings.server_domain}"
