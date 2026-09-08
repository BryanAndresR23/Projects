"""Utilidades comunes a las pruebas."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

FILAS_BASE = [
    {"FECHA DE OPERACIÓN": "05/06/2026", "N° OPERACIÓN": "TF-01-7712600001", "SENTIDO": "Giros AL",
     "BANCO CORRESPONSAL": "CITIBANK N.A.", "ÁREA 750": "750-01", "DEUDA 771": "771-100",
     "TIPO DE MENSAJE": "TF", "PROCESO": "PAGO DEUDA EXTERNA", "MONEDA": "USD",
     "MONTO": 1500000.50, "TIPO DE CAMBIO": 1, "MONTO USD": 1500000.50,
     "BENEFICIARIO": "ACREEDOR 101", "ESTADO": "PROCESADO"},
    {"FECHA DE OPERACIÓN": "12/06/2026", "N° OPERACIÓN": "GS-01-7712600002", "SENTIDO": "Giros AL",
     "BANCO CORRESPONSAL": "JP MORGAN CHASE", "ÁREA 750": "750-02", "DEUDA 771": "771-200",
     "TIPO DE MENSAJE": "GS", "PROCESO": "INTERESES", "MONEDA": "EUR",
     "MONTO": 200000, "TIPO DE CAMBIO": 1.08, "MONTO USD": 216000,
     "BENEFICIARIO": "ACREEDOR 202", "ESTADO": "PROCESADO"},
    {"FECHA DE OPERACIÓN": "20/06/2026", "N° OPERACIÓN": "TF-01-7712600003", "SENTIDO": "Giros AL",
     "BANCO CORRESPONSAL": "BBVA", "ÁREA 750": "750-01", "DEUDA 771": "771-300",
     "TIPO DE MENSAJE": "TF", "PROCESO": "AMORTIZACION", "MONEDA": "USD",
     "MONTO": 750000, "TIPO DE CAMBIO": 1, "MONTO USD": 750000,
     "BENEFICIARIO": "ACREEDOR 303", "ESTADO": "PENDIENTE"},
]


@pytest.fixture(autouse=True)
def entorno_aislado(tmp_path, monkeypatch):
    """Cada prueba usa su propia carpeta de datos y su propia base."""
    monkeypatch.setenv("GIROS_DIR_DATOS", str(tmp_path / "datos"))
    monkeypatch.setenv("GIROS_USUARIO", "pruebas")
    from nucleo import catalogo
    catalogo.recargar()
    yield


def escribir_excel(ruta: Path, filas: list[dict], titulos: int = 2) -> Path:
    """Crea un Excel con filas de título antes del encabezado, como los reportes reales."""
    tabla = pd.DataFrame(filas)
    with pd.ExcelWriter(ruta, engine="openpyxl") as escritor:
        if titulos:
            pd.DataFrame([["REPORTE DE GIROS"]] * titulos).to_excel(
                escritor, index=False, header=False, startrow=0
            )
        tabla.to_excel(escritor, index=False, startrow=titulos + 1)
    return ruta


@pytest.fixture
def archivo_al(tmp_path) -> Path:
    return escribir_excel(tmp_path / "Giros_AL_2026-06.xlsx", FILAS_BASE)


@pytest.fixture
def archivo_del(tmp_path) -> Path:
    filas = []
    for fila in FILAS_BASE:
        copia = dict(fila)
        copia["SENTIDO"] = "Giros DEL"
        filas.append(copia)
    return escribir_excel(tmp_path / "Giros_DEL_2026-06.xlsx", filas)
