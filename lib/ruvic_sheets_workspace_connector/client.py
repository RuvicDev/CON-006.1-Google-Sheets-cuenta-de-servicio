"""Cliente de Google Sheets (Google Workspace) vía cuenta de servicio con
delegación de dominio.

Mismo patrón de autenticación que ruvic_drive_connector (CON-007) y
ruvic_gmail_workspace_connector (CON-005.2): el constructor recibe el
usuario del dominio a impersonar, y una sola credencial (esta cuenta de
servicio) puede actuar como cualquier usuario autorizado del dominio
sin volver a autorizar por usuario ni gestionar expiración de tokens.

Capacidades:
- read_range():   leer los valores de un rango de una hoja.
- update_range():  escribir/sobrescribir valores en un rango existente.
- append_rows():  agregar filas nuevas al final de una hoja.

Las credenciales SIEMPRE provienen de variables de entorno
RUVIC_SHEETS_WORKSPACE_* (ver config.SheetsWorkspaceConfig.from_env).
Prohibido hardcodearlas.
"""

from __future__ import annotations

import re
import socket
from typing import Any

from google.auth.exceptions import GoogleAuthError, RefreshError
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .config import SheetsWorkspaceConfig
from .exceptions import (
    SheetsWorkspaceAuthError,
    SheetsWorkspaceConnectorError,
    SheetsWorkspaceDataError,
    SheetsWorkspaceNetworkError,
)
from .logging_utils import get_logger

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_VALID_VALUE_INPUT_OPTIONS = {"USER_ENTERED", "RAW"}


def _service_disabled_info(exc: HttpError) -> tuple[str, str] | None:
    """Detecta el caso 403 SERVICE_DISABLED (la API no está habilitada en
    el proyecto de Google Cloud) y retorna (nombre de la API, URL de
    activación), o None si el error es de otro tipo."""
    try:
        details = exc.error_details or []
    except Exception:
        details = []
    for detail in details:
        if isinstance(detail, dict) and detail.get("reason") == "SERVICE_DISABLED":
            meta = detail.get("metadata", {})
            return (
                meta.get("serviceTitle", "API de Google"),
                meta.get(
                    "activationUrl",
                    "Google Cloud Console -> APIs y servicios -> Biblioteca",
                ),
            )
    return None


def _wrap_http_error(exc: HttpError) -> SheetsWorkspaceConnectorError:
    """Traduce un error HTTP de la API de Sheets a una excepción propia,
    sin dejar escapar nunca el tipo crudo del cliente HTTP."""
    status = exc.resp.status if getattr(exc, "resp", None) is not None else None
    if status == 403:
        disabled = _service_disabled_info(exc)
        if disabled is not None:
            service_title, activation_url = disabled
            return SheetsWorkspaceAuthError(
                f"La {service_title} no está habilitada en el proyecto de "
                f"Google Cloud del Client ID configurado. Habilítala en "
                f"{activation_url} y espera 1-2 minutos antes de reintentar."
            )
    if status in (401, 403):
        return SheetsWorkspaceAuthError(
            "Credenciales inválidas o sin permiso suficiente sobre esta hoja. "
            "Verifica que el Client ID de la cuenta de servicio esté "
            "autorizado en el Admin Console de Workspace con el scope "
            "spreadsheets, y que el usuario impersonado tenga acceso a la "
            "hoja de cálculo."
        )
    if status == 404:
        return SheetsWorkspaceDataError(
            "La hoja de cálculo o el rango solicitado no existe, o el "
            "usuario impersonado no tiene acceso a ella."
        )
    if status == 429 or (status is not None and status >= 500):
        return SheetsWorkspaceNetworkError(
            f"Sheets API no respondió correctamente (HTTP {status}). "
            "Puede ser un límite de cuota temporal; reintenta en unos segundos."
        )
    return SheetsWorkspaceDataError(f"Error de Sheets API (HTTP {status}): {exc}")


def _validate_spreadsheet_id(spreadsheet_id: str) -> str:
    if not isinstance(spreadsheet_id, str):
        raise SheetsWorkspaceDataError(
            f"spreadsheet_id debe ser un string, no {type(spreadsheet_id).__name__}."
        )
    spreadsheet_id = spreadsheet_id.strip()
    if not spreadsheet_id:
        raise SheetsWorkspaceDataError("spreadsheet_id no puede estar vacío.")
    return spreadsheet_id


def _validate_range(range_name: str) -> str:
    if not isinstance(range_name, str):
        raise SheetsWorkspaceDataError(
            f"range debe ser un string, no {type(range_name).__name__}."
        )
    range_name = range_name.strip()
    if not range_name:
        raise SheetsWorkspaceDataError(
            "range no puede estar vacío (ej. 'Hoja1!A1:D20' o 'Hoja1')."
        )
    return range_name


def _validate_value_input_option(value_input_option: str) -> str:
    if value_input_option not in _VALID_VALUE_INPUT_OPTIONS:
        raise SheetsWorkspaceDataError(
            f"value_input_option={value_input_option!r} inválido. "
            f"Valores permitidos: {sorted(_VALID_VALUE_INPUT_OPTIONS)}."
        )
    return value_input_option


class SheetsWorkspaceClient:
    """Cliente de Google Sheets para un usuario específico de un dominio
    Google Workspace, vía cuenta de servicio con delegación de dominio.

    Args:
        user_email: correo del usuario del dominio a impersonar (ej.
            "facturacion@tuempresa.com"). La cuenta de servicio debe estar
            autorizada en el Admin Console para el dominio de ese correo.
            El conector opera sobre las hojas de cálculo a las que ese
            usuario tenga acceso (propias o compartidas con él).
        config: configuración de conexión. Si se omite, se lee de las
            variables de entorno RUVIC_SHEETS_WORKSPACE_* (comportamiento
            estándar en el runtime de la plataforma).

    Ejemplo:
        >>> client = SheetsWorkspaceClient(user_email="facturacion@tuempresa.com")
        >>> client.read_range("1AbC...xyz", "Hoja1!A1:C10")
        [['nombre', 'ciudad'], ['ACME', 'Bogotá']]
    """

    def __init__(self, user_email: str, config: SheetsWorkspaceConfig | None = None) -> None:
        if not _EMAIL_RE.match(user_email):
            raise SheetsWorkspaceDataError(
                f"user_email={user_email!r} no parece un correo válido "
                "(ej. 'facturacion@tuempresa.com')."
            )
        self.user_email = user_email
        self.config = config or SheetsWorkspaceConfig.from_env()
        self._logger = get_logger()
        self._service: Any = None
        self._credentials: service_account.Credentials | None = None

    # ------------------------------------------------------------------ #
    # Conexión
    # ------------------------------------------------------------------ #

    def _get_credentials(self) -> service_account.Credentials:
        if self._credentials is not None:
            return self._credentials
        try:
            self._credentials = service_account.Credentials.from_service_account_info(
                self.config.service_account_info,
                scopes=_SCOPES,
                subject=self.user_email,
            )
        except (ValueError, KeyError) as exc:
            raise SheetsWorkspaceAuthError(
                f"El JSON de la cuenta de servicio es inválido o le faltan campos: {exc}"
            ) from exc
        return self._credentials

    def _get_service(self) -> Any:
        if self._service is not None:
            return self._service
        credentials = self._get_credentials()
        try:
            self._service = build(
                "sheets",
                "v4",
                credentials=credentials,
                cache_discovery=False,
                static_discovery=False,
            )
        except (GoogleAuthError, OSError) as exc:
            raise SheetsWorkspaceNetworkError(
                f"No se pudo inicializar el cliente de Sheets: {exc}"
            ) from exc
        return self._service

    def ping(self) -> bool:
        """Verifica la delegación de dominio refrescando el token de
        acceso para el usuario impersonado (no requiere una hoja específica).

        Returns:
            True si la conexión y la delegación funcionan.

        Raises:
            SheetsWorkspaceAuthError / SheetsWorkspaceNetworkError / SheetsWorkspaceDataError.
        """
        credentials = self._get_credentials()
        try:
            credentials.refresh(Request())
        except RefreshError as exc:
            client_id = self.config.service_account_info.get("client_id", "?")
            raise SheetsWorkspaceAuthError(
                f"No se pudo autenticar como {self.user_email!r} (unauthorized_client). "
                "Revisa en el Admin Console de Google Workspace (Seguridad -> Control "
                "de acceso a las API -> Delegación en todo el dominio) que: "
                f"1) el Client ID '{client_id}' esté agregado, "
                "2) los scopes autorizados incluyan exactamente "
                "'https://www.googleapis.com/auth/spreadsheets', y 3) el usuario "
                "pertenezca a ese mismo dominio."
            ) from exc
        except (socket.error, TimeoutError, OSError) as exc:
            raise SheetsWorkspaceNetworkError(
                f"No se pudo conectar con Sheets API: {exc}"
            ) from exc
        self._logger.info("Ping exitoso, usuario: %s", self.user_email)
        return True

    # ------------------------------------------------------------------ #
    # Capacidad 1 — Leer un rango
    # ------------------------------------------------------------------ #

    def read_range(self, spreadsheet_id: str, range_name: str) -> list[list[Any]]:
        """Lee los valores de un rango de una hoja de cálculo.

        Args:
            spreadsheet_id: ID de la hoja (de la URL:
                docs.google.com/spreadsheets/d/{spreadsheet_id}/edit).
            range_name: rango en notación A1 (ej. "Hoja1!A1:D20", o solo
                "Hoja1" para toda la hoja).

        Returns:
            Lista de filas; cada fila es una lista de valores de celda.

        Ejemplo:
            >>> client.read_range("1AbC...xyz", "Hoja1!A1:C3")
            [['nombre', 'ciudad'], ['ACME', 'Bogotá'], ['Globex', 'Medellín']]
        """
        spreadsheet_id = _validate_spreadsheet_id(spreadsheet_id)
        range_name = _validate_range(range_name)
        service = self._get_service()
        try:
            result = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=spreadsheet_id, range=range_name)
                .execute(num_retries=1)
            )
        except (RefreshError, GoogleAuthError) as exc:
            raise SheetsWorkspaceAuthError(
                f"No se pudo autenticar: el token OAuth/credenciales pueden "
                f"estar revocados, vencidos o ser inválidos. Vuelve a "
                f"autorizar el conector. Detalle: {exc}"
            ) from exc
        except HttpError as exc:
            raise _wrap_http_error(exc) from exc
        values = result.get("values", [])
        self._logger.info(
            "Leídas %d filas de %s!%s (usuario %s)",
            len(values), spreadsheet_id, range_name, self.user_email,
        )
        return values

    # ------------------------------------------------------------------ #
    # Capacidad 2 — Escribir/actualizar un rango
    # ------------------------------------------------------------------ #

    def update_range(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[Any]],
        value_input_option: str = "USER_ENTERED",
    ) -> dict[str, Any]:
        """Escribe (sobrescribe) valores en un rango existente.

        Args:
            spreadsheet_id: ID de la hoja.
            range_name: rango en notación A1 donde empezar a escribir.
            values: lista de filas a escribir.
            value_input_option: "USER_ENTERED" (default) o "RAW".

        Returns:
            Dict con: updated_range, updated_rows, updated_cells.

        Ejemplo:
            >>> client.update_range("1AbC...xyz", "Hoja1!A2", [["ACME", "Bogotá"]])
            {'updated_range': 'Hoja1!A2:B2', 'updated_rows': 1, 'updated_cells': 2}
        """
        spreadsheet_id = _validate_spreadsheet_id(spreadsheet_id)
        range_name = _validate_range(range_name)
        value_input_option = _validate_value_input_option(value_input_option)
        service = self._get_service()
        try:
            result = (
                service.spreadsheets()
                .values()
                .update(
                    spreadsheetId=spreadsheet_id,
                    range=range_name,
                    valueInputOption=value_input_option,
                    body={"values": values},
                )
                .execute(num_retries=1)
            )
        except (RefreshError, GoogleAuthError) as exc:
            raise SheetsWorkspaceAuthError(
                f"No se pudo autenticar: el token OAuth/credenciales pueden "
                f"estar revocados, vencidos o ser inválidos. Vuelve a "
                f"autorizar el conector. Detalle: {exc}"
            ) from exc
        except HttpError as exc:
            raise _wrap_http_error(exc) from exc
        self._logger.info(
            "Actualizadas %d celdas en %s!%s (usuario %s)",
            result.get("updatedCells", 0), spreadsheet_id, range_name, self.user_email,
        )
        return {
            "updated_range": result.get("updatedRange"),
            "updated_rows": result.get("updatedRows"),
            "updated_cells": result.get("updatedCells"),
        }

    # ------------------------------------------------------------------ #
    # Capacidad 3 — Agregar filas al final
    # ------------------------------------------------------------------ #

    def append_rows(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[Any]],
        value_input_option: str = "USER_ENTERED",
    ) -> dict[str, Any]:
        """Agrega filas nuevas al final de la tabla de datos existente.

        Args:
            spreadsheet_id: ID de la hoja.
            range_name: rango/hoja donde buscar el final de la tabla.
            values: lista de filas a agregar.
            value_input_option: "USER_ENTERED" (default) o "RAW".

        Returns:
            Dict con: updated_range, updated_rows, updated_cells.

        Ejemplo:
            >>> client.append_rows("1AbC...xyz", "Hoja1", [["Nuevo", "Cliente"]])
            {'updated_range': 'Hoja1!A15:B15', 'updated_rows': 1, 'updated_cells': 2}
        """
        spreadsheet_id = _validate_spreadsheet_id(spreadsheet_id)
        range_name = _validate_range(range_name)
        value_input_option = _validate_value_input_option(value_input_option)
        service = self._get_service()
        try:
            result = (
                service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=spreadsheet_id,
                    range=range_name,
                    valueInputOption=value_input_option,
                    insertDataOption="INSERT_ROWS",
                    body={"values": values},
                )
                .execute(num_retries=1)
            )
        except (RefreshError, GoogleAuthError) as exc:
            raise SheetsWorkspaceAuthError(
                f"No se pudo autenticar: el token OAuth/credenciales pueden "
                f"estar revocados, vencidos o ser inválidos. Vuelve a "
                f"autorizar el conector. Detalle: {exc}"
            ) from exc
        except HttpError as exc:
            raise _wrap_http_error(exc) from exc
        updates = result.get("updates", {})
        self._logger.info(
            "Agregadas %d filas a %s!%s (usuario %s)",
            updates.get("updatedRows", 0), spreadsheet_id, range_name, self.user_email,
        )
        return {
            "updated_range": updates.get("updatedRange"),
            "updated_rows": updates.get("updatedRows"),
            "updated_cells": updates.get("updatedCells"),
        }
