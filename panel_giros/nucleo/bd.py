"""Base SQLite: esquema del histórico de cargas y de los hechos depurados."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from nucleo import config

ESQUEMA = """
PRAGMA foreign_keys = ON;

-- Una fila por cada intento de carga. Nunca se borra: es el histórico.
CREATE TABLE IF NOT EXISTS cargas (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    modulo             TEXT    NOT NULL,
    periodo            TEXT    NOT NULL,
    version            INTEGER NOT NULL,
    activa             INTEGER NOT NULL DEFAULT 0,
    estado             TEXT    NOT NULL,
    usuario            TEXT    NOT NULL,
    fecha_carga        TEXT    NOT NULL,
    nombre_original    TEXT    NOT NULL,
    hash_original      TEXT    NOT NULL,
    ruta_original      TEXT    NOT NULL,
    ruta_depurado      TEXT,
    ruta_descartes     TEXT,
    hoja               TEXT,
    fila_encabezado    INTEGER,
    filas_origen       INTEGER NOT NULL DEFAULT 0,
    filas_depuradas    INTEGER NOT NULL DEFAULT 0,
    filas_descartadas  INTEGER NOT NULL DEFAULT 0,
    operaciones        INTEGER NOT NULL DEFAULT 0,
    monto_usd          REAL    NOT NULL DEFAULT 0,
    errores            INTEGER NOT NULL DEFAULT 0,
    advertencias       INTEGER NOT NULL DEFAULT 0,
    comentario         TEXT,
    UNIQUE (modulo, periodo, version)
);

-- Regla del negocio: una sola versión activa por módulo y período.
CREATE UNIQUE INDEX IF NOT EXISTS ux_cargas_activa
    ON cargas (modulo, periodo) WHERE activa = 1;

-- Resultado de cada regla de validación aplicada a una carga.
CREATE TABLE IF NOT EXISTS validaciones (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    carga_id        INTEGER NOT NULL REFERENCES cargas (id) ON DELETE CASCADE,
    regla           TEXT    NOT NULL,
    severidad       TEXT    NOT NULL,
    mensaje         TEXT    NOT NULL,
    filas_afectadas INTEGER NOT NULL DEFAULT 0,
    detalle         TEXT
);
CREATE INDEX IF NOT EXISTS ix_validaciones_carga ON validaciones (carga_id);

-- Hechos depurados que alimentan el tablero.
CREATE TABLE IF NOT EXISTS operaciones (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    carga_id      INTEGER NOT NULL REFERENCES cargas (id) ON DELETE CASCADE,
    modulo        TEXT    NOT NULL,
    periodo       TEXT    NOT NULL,
    fecha         TEXT,
    anio          INTEGER,
    mes           INTEGER,
    nombre_mes    TEXT,
    referencia    TEXT,
    corresponsal  TEXT,
    area_750      TEXT,
    deuda_771     TEXT,
    tipo_mensaje  TEXT,
    proceso       TEXT,
    moneda        TEXT,
    monto         REAL,
    tipo_cambio   REAL,
    monto_usd     REAL,
    beneficiario  TEXT,
    estado        TEXT
);
CREATE INDEX IF NOT EXISTS ix_operaciones_carga ON operaciones (carga_id);
CREATE INDEX IF NOT EXISTS ix_operaciones_modulo ON operaciones (modulo, periodo);

-- Bitácora de auditoría: quién cargó, activó o restauró qué y cuándo.
CREATE TABLE IF NOT EXISTS bitacora (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha    TEXT NOT NULL,
    usuario  TEXT NOT NULL,
    accion   TEXT NOT NULL,
    modulo   TEXT,
    periodo  TEXT,
    version  INTEGER,
    carga_id INTEGER,
    detalle  TEXT
);
"""


def conectar(ruta: Path | None = None) -> sqlite3.Connection:
    """Abre la base (creándola si hace falta) con el esquema al día."""
    destino = Path(ruta) if ruta else config.ruta_bd()
    destino.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(destino)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(ESQUEMA)
    return con


@contextmanager
def sesion(ruta: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Conexión con commit automático y rollback ante error."""
    con = conectar(ruta)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def registrar_bitacora(
    con: sqlite3.Connection,
    *,
    usuario: str,
    accion: str,
    modulo: str | None = None,
    periodo: str | None = None,
    version: int | None = None,
    carga_id: int | None = None,
    detalle: str | None = None,
) -> None:
    from datetime import datetime

    con.execute(
        """
        INSERT INTO bitacora (fecha, usuario, accion, modulo, periodo, version, carga_id, detalle)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now().isoformat(timespec="seconds"),
            usuario,
            accion,
            modulo,
            periodo,
            version,
            carga_id,
            detalle,
        ),
    )
