"""Pruebas de las agregaciones que alimentan el tablero."""
from __future__ import annotations

import pandas as pd
import pytest

from nucleo import metricas, repositorio


@pytest.fixture
def datos(archivo_al, archivo_del):
    repositorio.registrar_carga(archivo_al, modulo="GIROS_AL", periodo="2026-06")
    repositorio.registrar_carga(archivo_del, modulo="GIROS_DEL", periodo="2026-06")
    return repositorio.operaciones(solo_activas=True)


def test_kpis(datos):
    indicadores = metricas.kpis(datos)
    assert indicadores["operaciones"] == 6
    esperado = (1500000.50 + 216000 + 750000) * 2  # las mismas filas en AL y en DEL
    assert round(indicadores["monto_usd"], 2) == round(esperado, 2)
    assert indicadores["corresponsales"] == 3


def test_agregar_por_operaciones(datos):
    tabla = metricas.agregar(datos, "tipo_mensaje", "operaciones")
    assert dict(zip(tabla["etiqueta"], tabla["valor"])) == {"TF": 4, "GS": 2}


def test_agregar_por_monto(datos):
    tabla = metricas.agregar(datos, "modulo", "monto_usd")
    assert set(tabla["etiqueta"]) == {"Giros AL", "Giros DEL"}
    assert round(float(tabla["valor"].sum()), 2) == round(float(datos["monto_usd"].sum()), 2)


def test_participacion_suma_cien(datos):
    tabla = metricas.agregar(datos, "corresponsal", "participacion")
    assert round(float(tabla["valor"].sum()), 6) == 100.0


def test_promedio(datos):
    tabla = metricas.agregar(datos, "modulo", "promedio_usd")
    esperado = float(datos[datos["modulo"] == "GIROS_AL"]["monto_usd"].mean())
    valor = float(tabla[tabla["etiqueta"] == "Giros AL"]["valor"].iloc[0])
    assert round(valor, 2) == round(esperado, 2)


def test_top_agrupa_en_otros(datos):
    tabla = metricas.agregar(datos, "corresponsal", "monto_usd", top=2)
    assert len(tabla) == 3
    assert tabla["etiqueta"].iloc[-1].startswith("Otros")
    assert round(float(tabla["valor"].sum()), 2) == round(float(datos["monto_usd"].sum()), 2)


def test_filtros_combinables(datos):
    filtrado = metricas.aplicar_filtros(datos, {"modulo": ["Giros AL"], "moneda": ["USD"]})
    assert len(filtrado) == 2
    assert set(filtrado["modulo"]) == {"GIROS_AL"}


def test_filtro_por_monto_minimo(datos):
    filtrado = metricas.aplicar_filtros(datos, {"monto_minimo": 1000000})
    assert (filtrado["monto_usd"] >= 1000000).all()


def test_filtro_por_rango_de_fechas(datos):
    filtrado = metricas.aplicar_filtros(datos, {"rango_fechas": ("2026-06-10", "2026-06-15")})
    assert len(filtrado) == 2


def test_filtro_vacio_no_altera(datos):
    assert len(metricas.aplicar_filtros(datos, {"modulo": []})) == len(datos)


def test_tabla_cruzada_con_totales(datos):
    matriz = metricas.tabla_cruzada(datos, "modulo", "tipo_mensaje", "monto_usd")
    assert "Total" in matriz.columns and "Total" in matriz.index
    assert round(float(matriz.loc["Total", "Total"]), 2) == round(float(datos["monto_usd"].sum()), 2)


def test_serie_temporal_por_mes(datos):
    serie = metricas.serie_temporal(datos, "operaciones", por="mes")
    assert list(serie["etiqueta"]) == ["2026-06"]
    assert int(serie["valor"].iloc[0]) == 6


def test_ranking(datos):
    tabla = metricas.ranking(datos, "corresponsal", top=3)
    assert list(tabla.columns) == ["Operaciones", "Monto USD", "Promedio USD", "Participación %"]
    assert round(float(tabla["Participación %"].sum()), 1) == 100.0


def test_detalle_tiene_encabezados_legibles(datos):
    detalle = metricas.detalle(datos)
    assert "Monto USD" in detalle.columns and "Corresponsal" in detalle.columns
    assert len(detalle) == len(datos)


def test_sin_datos_no_rompe():
    vacio = pd.DataFrame(columns=["modulo", "monto_usd", "fecha", "corresponsal"])
    assert metricas.agregar(vacio, "modulo", "monto_usd").empty
    assert metricas.kpis(vacio)["operaciones"] == 0
    assert metricas.serie_temporal(vacio).empty
