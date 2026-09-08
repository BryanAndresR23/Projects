"""Pruebas del ETL: conversores, detección de encabezado y depuración."""
from __future__ import annotations

import pytest

from nucleo import etl
from tests.conftest import FILAS_BASE, escribir_excel


@pytest.mark.parametrize("valor,esperado", [
    ("1.234,56", 1234.56), ("1,234.56", 1234.56), ("$ 1 234,56", 1234.56),
    ("(1.234,56)", -1234.56), ("1234", 1234.0), ("", None), ("abc", None), (None, None),
])
def test_a_numero(valor, esperado):
    assert etl.a_numero(valor) == esperado


def test_a_numero_distingue_miles_de_decimales():
    assert etl.a_numero("1.500", punto_es_miles=True) == 1500.0   # importe
    assert etl.a_numero("1.085") == 1.085                          # tipo de cambio


@pytest.mark.parametrize("valor,iso", [
    ("15/06/2026", "2026-06-15"), ("2026-06-15", "2026-06-15"), ("15-06-2026", "2026-06-15"),
])
def test_a_fecha(valor, iso):
    assert etl.a_fecha(valor).isoformat() == iso


def test_a_fecha_rechaza_basura():
    assert etl.a_fecha("no es fecha") is None
    assert etl.a_fecha(None) is None


def test_normalizar_tipo_mensaje():
    assert etl.normalizar_tipo_mensaje("TF-01") == "TF"
    assert etl.normalizar_tipo_mensaje("mensaje gs") == "GS"


def test_encuentra_encabezado_bajo_titulos(archivo_al):
    resultado = etl.procesar(archivo_al, modulo="GIROS_AL", periodo="2026-06")
    assert resultado.fila_encabezado == 4
    assert resultado.filas_depuradas == 3
    assert not resultado.columnas_faltantes


def test_mapea_columnas_con_nombres_distintos(tmp_path):
    filas = []
    for fila in FILAS_BASE:
        filas.append({
            "Fecha valor": fila["FECHA DE OPERACIÓN"], "Referencia": fila["N° OPERACIÓN"],
            "Entidad": fila["BANCO CORRESPONSAL"], "Divisa": fila["MONEDA"],
            "Importe": fila["MONTO"], "Equivalente USD": fila["MONTO USD"],
        })
    ruta = escribir_excel(tmp_path / "otro.xlsx", filas, titulos=0)
    resultado = etl.procesar(ruta, modulo="GIROS_AL", periodo="2026-06")
    assert resultado.filas_depuradas == 3
    assert set(resultado.columnas_detectadas.values()) >= {"fecha", "referencia", "corresponsal",
                                                           "moneda", "monto", "monto_usd"}


def test_descarta_totales_y_filas_invalidas(tmp_path):
    filas = list(FILAS_BASE)
    filas.append({**FILAS_BASE[0], "FECHA DE OPERACIÓN": "TOTAL", "MONTO": 2250000})
    filas.append({**FILAS_BASE[0], "MONTO": None, "MONTO USD": None})
    ruta = escribir_excel(tmp_path / "con_ruido.xlsx", filas)
    resultado = etl.procesar(ruta, modulo="GIROS_AL", periodo="2026-06")
    assert resultado.filas_depuradas == 3
    motivos = set(resultado.descartes["motivo"])
    assert etl.MOTIVO_TOTALES in motivos
    assert etl.MOTIVO_MONTO in motivos or etl.MOTIVO_FECHA in motivos


def test_convierte_a_usd_con_tipo_de_cambio_de_la_fila(tmp_path):
    fila = {k: v for k, v in FILAS_BASE[1].items() if k != "MONTO USD"}
    ruta = escribir_excel(tmp_path / "sin_usd.xlsx", [fila])
    resultado = etl.procesar(ruta, modulo="GIROS_AL", periodo="2026-06")
    assert resultado.filas_depuradas == 1
    assert round(float(resultado.datos.iloc[0]["monto_usd"]), 2) == 216000.00


def test_descarta_moneda_sin_tipo_de_cambio(tmp_path):
    fila = {k: v for k, v in FILAS_BASE[1].items() if k not in ("MONTO USD", "TIPO DE CAMBIO")}
    ruta = escribir_excel(tmp_path / "sin_tc.xlsx", [fila])
    resultado = etl.procesar(ruta, modulo="GIROS_AL", periodo="2026-06")
    assert resultado.filas_depuradas == 0
    assert etl.MOTIVO_SIN_TC in set(resultado.descartes["motivo"])


def test_archivo_sin_encabezado_reconocible(tmp_path):
    ruta = escribir_excel(tmp_path / "vacio.xlsx", [{"A": 1, "B": 2}], titulos=0)
    resultado = etl.procesar(ruta, modulo="GIROS_AL", periodo="2026-06")
    assert resultado.fila_encabezado is None
    assert resultado.filas_depuradas == 0
