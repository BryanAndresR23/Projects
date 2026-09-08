"""Rutas, catálogos base y parámetros generales del panel."""
from __future__ import annotations

import getpass
import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

# Módulos que el validador debe elegir de forma explícita al cargar.
MODULOS = {
    "GIROS_AL": "Giros AL",
    "GIROS_DEL": "Giros DEL",
}

MESES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre",
    12: "Diciembre",
}

# Estados posibles de una carga.
ESTADO_VALIDA = "VALIDA"
ESTADO_ADVERTENCIAS = "CON ADVERTENCIAS"
ESTADO_RECHAZADA = "RECHAZADA"

SEVERIDAD_ERROR = "ERROR"
SEVERIDAD_ADVERTENCIA = "ADVERTENCIA"
SEVERIDAD_INFO = "INFO"


def dir_datos() -> Path:
    """Carpeta donde viven la base y los archivos de cada carga.

    Se puede reubicar (por ejemplo a una unidad de red) con la variable de
    entorno ``GIROS_DIR_DATOS``.
    """
    ruta = os.environ.get("GIROS_DIR_DATOS")
    return Path(ruta) if ruta else RAIZ / "datos"


def dir_cargas() -> Path:
    return dir_datos() / "cargas"


def ruta_bd() -> Path:
    return dir_datos() / "giros.db"


def dir_catalogos() -> Path:
    ruta = os.environ.get("GIROS_DIR_CATALOGOS")
    return Path(ruta) if ruta else RAIZ / "catalogos"


def usuario_actual() -> str:
    """Usuario que queda registrado en el histórico y en la bitácora."""
    usuario = os.environ.get("GIROS_USUARIO")
    if usuario:
        return usuario.strip()
    try:
        return getpass.getuser()
    except Exception:
        return "desconocido"


def etiqueta_modulo(modulo: str) -> str:
    return MODULOS.get(modulo, modulo)


def periodo(anio: int, mes: int) -> str:
    """Período canónico ``AAAA-MM``."""
    return f"{int(anio):04d}-{int(mes):02d}"


def partes_periodo(valor: str) -> tuple[int, int]:
    anio, mes = valor.split("-")
    return int(anio), int(mes)


def etiqueta_periodo(valor: str) -> str:
    anio, mes = partes_periodo(valor)
    return f"{MESES[mes]} {anio}"
