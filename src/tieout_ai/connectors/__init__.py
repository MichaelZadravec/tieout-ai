"""Connectors fetch data with the engine's own credentials and hash every extract."""

from tieout_ai.connectors.base import Connector, ConnectorError, UnsafeQueryError
from tieout_ai.connectors.files import FileConnector
from tieout_ai.connectors.sql import SQLConnector
from tieout_ai.models import Connection, ConnectionType

_CONNECTORS: dict[ConnectionType, type[Connector]] = {
    ConnectionType.FILE: FileConnector,
    ConnectionType.SQL: SQLConnector,
}


def connector_for(connection: Connection) -> Connector:
    try:
        return _CONNECTORS[connection.type](connection)
    except KeyError:
        raise ConnectorError(f"No connector for '{connection.type}' connections yet") from None


__all__ = [
    "Connector",
    "ConnectorError",
    "FileConnector",
    "SQLConnector",
    "UnsafeQueryError",
    "connector_for",
]
