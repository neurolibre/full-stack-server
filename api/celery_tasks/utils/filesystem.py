"""
Filesystem utilities for Celery tasks.

Provides optimized file operations for large directories.
"""

import os
import shutil
import tempfile
import zipfile


def fast_copytree(src, dst):
    """
    Fast copy of large directory trees using zip compression.

    This is faster than shutil.copytree for large directories because
    it compresses, transfers, and decompresses in one operation.

    Args:
        src: Source directory path
        dst: Destination directory path
    """
    # Create a temporary directory for the zip file
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, 'archive.zip')

        # Zip the source directory
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(src, os.path.basename(src))
            for root, _, files in os.walk(src):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, src)
                    zipf.write(file_path, arcname)

        # Copy the zip file to the destination
        dst_zip = os.path.join(dst, 'archive.zip')
        shutil.copy2(zip_path, dst_zip)

        # Extract the zip file at the destination
        with zipfile.ZipFile(dst_zip, 'r') as zipf:
            zipf.extractall(dst)

        # Remove the zip file from the destination
        os.remove(dst_zip)
