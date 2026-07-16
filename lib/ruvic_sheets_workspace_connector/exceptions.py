"""Excepciones propias del conector Sheets Workspace.

Separan los tres tipos de fallo que el usuario debe distinguir:
autenticación, red/servidor y datos. Nunca exponemos excepciones
crípticas del cliente HTTP subyacente.
"""


class SheetsWorkspaceConnectorError(Exception):
    """Error base del conector."""


class SheetsWorkspaceAuthError(SheetsWorkspaceConnectorError):
    """Credenciales inválidas o delegación de dominio no autorizada para el usuario impersonado."""


class SheetsWorkspaceNetworkError(SheetsWorkspaceConnectorError):
    """No se pudo alcanzar la API de Sheets (red, timeout, error temporal del servidor)."""


class SheetsWorkspaceDataError(SheetsWorkspaceConnectorError):
    """La operación es válida pero el recurso no existe o los datos son inválidos."""
