"""
Shared configuration for Celery tasks.

Loads configuration from YAML files for use across all tasks.
"""

import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from common import load_yaml

# Load configuration files
preview_config = load_yaml('config/preview.yaml')
preprint_config = load_yaml('config/preprint.yaml')
common_config = load_yaml('config/common.yaml')

# DOI configuration
DOI_PREFIX = common_config['DOI_PREFIX']
DOI_SUFFIX = common_config['DOI_SUFFIX']

# Journal configuration
JOURNAL_NAME = common_config['JOURNAL_NAME']
JOURNAL_SUBJECT = common_config['JOURNAL_SUBJECT']
JOURNAL_TWITTER = common_config['JOURNAL_TWITTER']

# Server configuration
BINDER_REGISTRY = common_config['BINDER_REGISTRY']
GH_ORGANIZATION = common_config['GH_ORGANIZATION']

# Path configuration
DATA_ROOT_PATH = common_config['DATA_ROOT_PATH']
JB_ROOT_FOLDER = common_config['JB_ROOT_FOLDER']
MYST_FOLDER = common_config['MYST_FOLDER']
DATA_NFS_PATH = common_config['DATA_NFS_PATH']
PAPERS_PATH = common_config['PAPERS_PATH']

# Container configuration
CONTAINER_MYST_SOURCE_PATH = common_config['CONTAINER_MYST_SOURCE_PATH']
CONTAINER_MYST_DATA_PATH = common_config['CONTAINER_MYST_DATA_PATH']
NOEXEC_CONTAINER_REPOSITORY = common_config['NOEXEC_CONTAINER_REPOSITORY']
NOEXEC_CONTAINER_COMMIT_HASH = common_config['NOEXEC_CONTAINER_COMMIT_HASH']

# Publishing configuration
PUBLISH_LICENSE = common_config['PUBLISH_LICENSE']
JB_INTERFACE_OVERRIDE = preprint_config.get('JB_INTERFACE_OVERRIDE', False)

# Derived URLs
PRODUCTION_BINDERHUB = f"https://{preprint_config['BINDER_NAME']}.{preprint_config['BINDER_DOMAIN']}"
PREVIEW_BINDERHUB = f"https://{preview_config['BINDER_NAME']}.{preview_config['BINDER_DOMAIN']}"
PREVIEW_SERVER = f"https://{preview_config['SERVER_SLUG']}.{common_config['SERVER_DOMAIN']}"
PREPRINT_SERVER = f"https://{preprint_config['SERVER_SLUG']}.{common_config['SERVER_DOMAIN']}"
