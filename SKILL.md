---
name: sheets-workspace
description: >
  Usa la librería ruvic_sheets_workspace_connector para leer y escribir
  hojas de cálculo accesibles por uno o varios usuarios de un dominio
  Google Workspace, vía cuenta de servicio con delegación de dominio
  (impersonación) - leer un rango (read_range), escribir/sobrescribir
  valores en un rango (update_range) y agregar filas nuevas al final de
  una tabla (append_rows). Úsala cuando el usuario pida leer, consultar,
  actualizar o agregar datos a una hoja de cálculo de Google Sheets en
  un contexto de Google Workspace, especialmente si menciona varios
  usuarios/buzones del mismo dominio sin volver a autorizar cada uno
  por separado. Para una cuenta personal o un solo usuario autorizado
  individualmente, usa en su lugar el conector `sheets` (OAuth2).
triggers:
- google sheets
- hoja de cálculo
- hoja de calculo
- spreadsheet
- excel de google
- planilla
- workspace
- delegación de dominio
- múltiples buzones
---

# Conector Google Sheets Workspace (ruvic_sheets_workspace_connector)

Librería Python para leer y escribir hojas de cálculo accesibles por usuarios de un dominio **Google Workspace**, usando una **cuenta de servicio con delegación de dominio**. Está **preinstalada en el runtime** cuando el conector está configurado (si no, instálala con `pip install git+https://github.com/Dgirto/Sheets-Workspace.git#subdirectory=lib`).

## Cuándo usar este conector vs. el de OAuth2

Ruvic tiene dos conectores de Sheets; elige según el caso:

| Situación | Conector a usar |
|-----------|------------------|
| Cuenta de Google personal, o un solo usuario corporativo autorizado individualmente | `sheets` (OAuth2) — ver su `SKILL.md` |
| **Google Workspace, una sola credencial para impersonar varios usuarios del dominio** (ej. `facturacion@empresa.com`, `soporte@empresa.com`), sin re-autorizar cada uno | **`sheets_workspace` (este)** |

Este conector **no funciona con cuentas Gmail/Google personales** — Google no permite delegación de dominio fuera de Workspace.

## Regla crítica de credenciales

El código generado **NUNCA hardcodea credenciales**. Siempre se leen de variables de entorno, disponibles cuando el conector `sheets_workspace` está configurado:

| Variable | Contenido |
|----------|-----------|
| `RUVIC_SHEETS_WORKSPACE_SERVICE_ACCOUNT_JSON` | JSON completo de la cuenta de servicio de Google Cloud |
| `RUVIC_SHEETS_WORKSPACE_DEFAULT_USER` | Usuario usado solo para el test de conexión |
| `RUVIC_SHEETS_WORKSPACE_REQUEST_TIMEOUT` | (opcional) timeout en segundos, default 30 |

Si estas variables NO existen, el conector no está configurado: no generes código que lo use; indica al usuario que lo configure en **Settings → Conectores**. El código generado **NUNCA** usa nombres con segmento de alias (`_DEFAULT_INSTANCIA_`, `_PRODUCCION_`, etc.) salvo que aparezcan en una sección autogenerada `## Variables en tu entorno` al final de este skill.

## Autenticación / conexión (siempre igual)

El usuario a impersonar se pasa en el **constructor**, no en cada método:

```python
from ruvic_sheets_workspace_connector import SheetsWorkspaceClient

client = SheetsWorkspaceClient(user_email="facturacion@tuempresa.com")
```

Para trabajar con varios usuarios, crea una instancia por usuario:

```python
usuarios = ["facturacion@tuempresa.com", "soporte@tuempresa.com"]
clientes = {u: SheetsWorkspaceClient(user_email=u) for u in usuarios}
```

## Identificar la hoja de cálculo

El `spreadsheet_id` es el que aparece en la URL de la hoja:
`https://docs.google.com/spreadsheets/d/`**`ESTE_ID`**`/edit`. Si el usuario
pega la URL completa, extrae solo esa parte.

## Capacidad 1 — Leer un rango

```python
rows = client.read_range("1AbC...xyz", "Hoja1!A1:D20")
for row in rows:
    print(row)
```

## Capacidad 2 — Escribir/sobrescribir un rango

```python
result = client.update_range(
    "1AbC...xyz", "Hoja1!A2:B2", [["ACME", "Bogotá"]]
)
```

## Capacidad 3 — Agregar filas al final

```python
result = client.append_rows(
    "1AbC...xyz", "Hoja1", [["Nuevo cliente", "Medellín"]]
)
```

Detecta automáticamente el final de la tabla de datos y agrega ahí, sin
sobrescribir filas existentes.

## Manejo de errores

```python
from ruvic_sheets_workspace_connector import (
    SheetsWorkspaceAuthError, SheetsWorkspaceDataError, SheetsWorkspaceNetworkError,
)

try:
    client.append_rows("1AbC...xyz", "Hoja1", [["dato"]])
except SheetsWorkspaceAuthError:
    print("Delegación de dominio no autorizada para este usuario — revisa el Admin Console")
except SheetsWorkspaceNetworkError:
    print("Sheets API no respondió — puede ser un límite de cuota temporal")
except SheetsWorkspaceDataError as e:
    print(f"Error de datos: {e}")  # ej. la hoja no existe o no es accesible
```

`SheetsWorkspaceAuthError` incluye en su mensaje el Client ID exacto que debe estar autorizado en el Admin Console, y qué revisar (Client ID, scope, dominio del usuario) — muéstraselo tal cual al usuario cuando ocurra.

## Buenas prácticas al generar código

1. Lee credenciales SOLO de las variables `RUVIC_SHEETS_WORKSPACE_*` (el constructor de `SheetsWorkspaceConfig` ya lo hace).
2. Nunca imprimas `RUVIC_SHEETS_WORKSPACE_SERVICE_ACCOUNT_JSON` en logs ni en la salida.
3. El `user_email` del constructor puede ser **cualquier** usuario del dominio autorizado, no solo el de prueba (`DEFAULT_USER`).
4. El conector solo puede acceder a hojas que estén **compartidas con el usuario impersonado** (o de su propiedad). Si falla con "no existe o no es accesible", el usuario debe compartir la hoja con esa cuenta.
5. Usa `append_rows` para agregar datos nuevos; usa `update_range` solo cuando necesitas sobrescribir una posición específica.
6. El conector no crea ni elimina hojas de cálculo ni pestañas — solo lee y escribe valores dentro de hojas existentes.
