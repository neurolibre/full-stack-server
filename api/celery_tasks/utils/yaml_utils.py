"""
YAML utilities for Celery tasks.

Provides YAML comparison and validation functions.
"""

import yaml
import logging

logger = logging.getLogger(__name__)


def compare_yaml_files(fork_yaml_content, upstream_yaml_content, file_path):
    """
    Compare YAML files and return differences.

    Args:
        fork_yaml_content: YAML content from fork
        upstream_yaml_content: YAML content from upstream
        file_path: Path to the YAML file (for logging)

    Returns:
        Tuple of (has_differences, message)
    """
    try:
        fork_data = yaml.safe_load(fork_yaml_content)
        upstream_data = yaml.safe_load(upstream_yaml_content)

        if fork_data == upstream_data:
            return False, f"No differences in {file_path}"

        differences = []

        # Compare keys
        fork_keys = set(fork_data.keys()) if isinstance(fork_data, dict) else set()
        upstream_keys = set(upstream_data.keys()) if isinstance(upstream_data, dict) else set()

        added_keys = upstream_keys - fork_keys
        removed_keys = fork_keys - upstream_keys
        common_keys = fork_keys & upstream_keys

        if added_keys:
            differences.append(f"Added keys: {added_keys}")
        if removed_keys:
            differences.append(f"Removed keys: {removed_keys}")

        # Compare values for common keys
        for key in common_keys:
            if fork_data[key] != upstream_data[key]:
                differences.append(
                    f"Key '{key}': fork={fork_data[key]}, upstream={upstream_data[key]}"
                )

        message = f"Differences in {file_path}:\n" + "\n".join(differences)
        return True, message

    except yaml.YAMLError as e:
        logger.error(f"Error parsing YAML in {file_path}: {e}")
        return True, f"YAML parsing error in {file_path}: {e}"
