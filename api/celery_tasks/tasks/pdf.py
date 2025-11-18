"""
PDF generation tasks.

Handles building extended PDFs for submission.
"""

import os
import sys

# Add parent directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..'))

# Import tasks from original file for backward compatibility
# TODO: Migrate these tasks to this module
from neurolibre_celery_tasks import preprint_build_pdf_draft

__all__ = ["preprint_build_pdf_draft"]
