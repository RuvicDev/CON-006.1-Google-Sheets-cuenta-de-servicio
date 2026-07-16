"""Conector Ruvic para Google Sheets (Google Workspace) vía cuenta de
servicio con delegación de dominio."""

from .client import SheetsWorkspaceClient
from .config import ENV_PREFIX, SheetsWorkspaceConfig
from .exceptions import (
    SheetsWorkspaceAuthError,
    SheetsWorkspaceConnectorError,
    SheetsWorkspaceDataError,
    SheetsWorkspaceNetworkError,
)
from .logging_utils import setup_logging

__all__ = [
    "ENV_PREFIX",
    "SheetsWorkspaceAuthError",
    "SheetsWorkspaceClient",
    "SheetsWorkspaceConfig",
    "SheetsWorkspaceConnectorError",
    "SheetsWorkspaceDataError",
    "SheetsWorkspaceNetworkError",
    "setup_logging",
]

__version__ = "1.0.0"
