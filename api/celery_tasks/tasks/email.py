"""
Email notification tasks.

Handles sending emails via AWS SES with optional HTML attachments.
"""

import os
import sys

# Add parent directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..'))

# Import tasks from original file for backward compatibility
# TODO: Migrate these tasks to this module
from neurolibre_celery_tasks import (
    send_email_celery,
    send_email_with_html_attachment_celery
)

__all__ = [
    "send_email_celery",
    "send_email_with_html_attachment_celery"
]
