"""
Celery App Configuration

This module initializes the Celery app and loads all the necessary configurations.
"""

from celery import Celery
import os
import logging
from dotenv import load_dotenv
import yaml
from common import load_yaml

# Load configuration files
preview_config = load_yaml('config/preview.yaml')
preprint_config = load_yaml('config/preprint.yaml')
common_config = load_yaml('config/common.yaml')

celery_config = {
    'DOI_PREFIX': common_config['DOI_PREFIX'],
    'DOI_SUFFIX': common_config['DOI_SUFFIX'],
    
    'JOURNAL_NAME': common_config['JOURNAL_NAME'],
    'JOURNAL_SUBJECT': common_config['JOURNAL_SUBJECT'],
    'JOURNAL_TWITTER': common_config['JOURNAL_TWITTER'],
    
    'BINDER_REGISTRY': common_config['BINDER_REGISTRY'],
    'GH_ORGANIZATION': common_config['GH_ORGANIZATION'],
    
    'DATA_ROOT_PATH': common_config['DATA_ROOT_PATH'],
    'JB_ROOT_FOLDER': common_config['JB_ROOT_FOLDER'],
    'MYST_FOLDER': common_config['MYST_FOLDER'],
    'DATA_NFS_PATH': common_config['DATA_NFS_PATH'],
    'PAPERS_PATH': common_config['PAPERS_PATH'],
    
    'CONTAINER_MYST_SOURCE_PATH': common_config['CONTAINER_MYST_SOURCE_PATH'],
    'CONTAINER_MYST_DATA_PATH': common_config['CONTAINER_MYST_DATA_PATH'],
    'NOEXEC_CONTAINER_REPOSITORY': common_config['NOEXEC_CONTAINER_REPOSITORY'],
    'NOEXEC_CONTAINER_COMMIT_HASH': common_config['NOEXEC_CONTAINER_COMMIT_HASH'],
    
    'PUBLISH_LICENSE': common_config['PUBLISH_LICENSE'],
    'JB_INTERFACE_OVERRIDE': preprint_config['JB_INTERFACE_OVERRIDE'],
    
    # Review repository from common config
    'REVIEW_REPOSITORY': common_config['REVIEW_REPOSITORY'],
    
    # Global variables from the mix of common, preprint and preview configs
    'PRODUCTION_BINDERHUB': f"https://{preprint_config['BINDER_NAME']}.{preprint_config['BINDER_DOMAIN']}",
    'PREVIEW_BINDERHUB': f"https://{preview_config['BINDER_NAME']}.{preview_config['BINDER_DOMAIN']}",
    'PREVIEW_SERVER': f"https://{preview_config['SERVER_SLUG']}.{common_config['SERVER_DOMAIN']}",
    'PREPRINT_SERVER': f"https://{preprint_config['SERVER_SLUG']}.{common_config['SERVER_DOMAIN']}",
}

# Load environment variables
load_dotenv()

# Zenodo API tokens and servers
ZENODO_SERVER = "https://zenodo.org/api"
#ZENODO_TOKEN = os.getenv('ZENODO_API')
ZENODO_SANDBOX_SERVER = "https://sandbox.zenodo.org/api"
#ZENODO_SANDBOX_TOKEN = os.getenv('ZENODO_SANDBOX_API')

# GitHub bot token
#GH_BOT_TOKEN = os.getenv('GH_BOT')

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Initialize Celery app
celery_app = Celery('neurolibre_celery_tasks', 
                   backend='redis://localhost:6379/1', 
                   broker='redis://localhost:6379/0')

# Configure Celery
celery_app.conf.update(task_track_started=True)

# Import all tasks to ensure they are registered with Celery
import importlib
import pkgutil

# Dynamically import all modules in the celery_tasks package
def import_submodules(package_name):
    """Import all submodules of a module, recursively."""
    package = importlib.import_module(package_name)
    for _, name, is_pkg in pkgutil.walk_packages(package.__path__, package.__name__ + '.'):
        if not is_pkg and name != __name__:
            importlib.import_module(name)

# This will be uncommented after all modules are created
import_submodules('api.celery.tasks') 


def get_zenodo_token():
    """Get Zenodo API token only when needed"""
    return os.getenv('ZENODO_API')

def get_zenodo_sandbox_token():
    """Get Zenodo Sandbox API token only when needed"""
    return os.getenv('ZENODO_SANDBOX_API')

def get_github_bot_token():
    """Get GitHub bot token only when needed"""
    return os.getenv('GH_BOT')