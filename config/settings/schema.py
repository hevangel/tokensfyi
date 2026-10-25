"""Documented names consumed by the external settings loader.

This module intentionally contains no secret values. The example file is the only
place that exposes safe placeholder names for local startup.
"""

from token_budget_platform.config.environment import (
    EXAMPLE_CONFIG_REFERENCE,
    REQUIRED_PRODUCTION_SETTINGS,
)

__all__ = ["EXAMPLE_CONFIG_REFERENCE", "REQUIRED_PRODUCTION_SETTINGS"]
