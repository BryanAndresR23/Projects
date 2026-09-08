"""Pruebas del histórico: versionado, publicación y restauración."""
from __future__ import annotations

from pathlib import Path

import pytest

from nucleo import config, repositorio
from tests.conftest import FILAS_BASE, escribir_excel


def test_primera_carga_se_publica(archivo_al):
    resultado = repositorio.registrar_carga(archivo_al, modulo="GIROS_AL", periodo="2026-06")
    assert resultado.version == 1
    assert resultado.activa is True
    assert resultado.estado != config.ESTADO_RECHAZADA
    assert resultado.etl.filas_depuradas == 3


def test_conserva_archivo_original_depurado_y_validaciones(archivo_al):
    resultado = repositorio.registrar_carga(archivo_al, modulo="GIROS_AL", periodo="2026-06")
    carpeta = resultado.carpeta
    assert (carpeta / "depurado.csv").exists()
    assert (carpeta / "descartes.csv").exists()
    assert (carpeta / "validaciones.json").exists()
    assert any(p.name.startswith("original_") for p in carpeta.iterdir())

    carga = repositorio.obtener_carga(resultado.carga_id)
    assert carga["usuario"] == "pruebas"
    assert carga["nombre_original"] == archivo_al.name
    assert carga["fecha_carga"]


def test_segunda_carga_crea_version_nueva_y_desplaza_a_la_anterior(archivo_al):
    primera = repositorio.registrar_carga(archivo_al, modulo="GIROS_AL", periodo="2026-06")
    segunda = repositorio.registrar_carga(archivo_al, modulo="GIROS_AL", periodo="2026-06",
                                          comentario="recarga")
    assert segunda.version == 2
    historico = repositorio.listar_cargas(modulo="GIROS_AL", periodo="2026-06")
    assert len(historico) == 2
    assert int(historico[historico["version"] == 2]["activa"].iloc[0]) == 1
    assert int(historico[historico["version"] == 1]["activa"].iloc[0]) == 0
    assert segunda.duplicado_de == primera.carga_id


def test_solo_una_version_activa_por_modulo_y_periodo(archivo_al):
    for _ in range(3):
        repositorio.registrar_carga(archivo_al, modulo="GIROS_AL", periodo="2026-06")
    historico = repositorio.listar_cargas(modulo="GIROS_AL", periodo="2026-06")
    assert int(historico["activa"].sum()) == 1


def test_restaurar_version_anterior(archivo_al):
    repositorio.registrar_carga(archivo_al, modulo="GIROS_AL", periodo="2026-06")
    repositorio.registrar_carga(archivo_al, modulo="GIROS_AL", periodo="2026-06")
    resultado = repositorio.activar_version("GIROS_AL", "2026-06", 1, motivo="prueba")
    assert resultado["accion"] == "RESTAURACION"
    assert repositorio.carga_activa("GIROS_AL", "2026-06")["version"] == 1

    bitacora = repositorio.bitacora()
    assert "RESTAURACION" in set(bitacora["accion"])


def test_carga_rechazada_se_guarda_pero_no_se_publica(archivo_del):
    resultado = repositorio.registrar_carga(archivo_del, modulo="GIROS_AL", periodo="2026-06")
    assert resultado.estado == config.ESTADO_RECHAZADA
    assert resultado.activa is False
    assert repositorio.carga_activa("GIROS_AL", "2026-06") is None
    assert len(repositorio.listar_cargas(modulo="GIROS_AL")) == 1
    assert repositorio.operaciones(solo_activas=True).empty


def test_no_se_puede_publicar_una_version_rechazada(archivo_del):
    repositorio.registrar_carga(archivo_del, modulo="GIROS_AL", periodo="2026-06")
    with pytest.raises(ValueError):
        repositorio.activar_version("GIROS_AL", "2026-06", 1)


def test_guardar_sin_publicar(archivo_al):
    repositorio.registrar_carga(archivo_al, modulo="GIROS_AL", periodo="2026-06")
    segunda = repositorio.registrar_carga(archivo_al, modulo="GIROS_AL", periodo="2026-06",
                                          activar=False)
    assert segunda.activa is False
    assert repositorio.carga_activa("GIROS_AL", "2026-06")["version"] == 1


def test_modulos_y_periodos_son_independientes(archivo_al, archivo_del):
    repositorio.registrar_carga(archivo_al, modulo="GIROS_AL", periodo="2026-06")
    repositorio.registrar_carga(archivo_del, modulo="GIROS_DEL", periodo="2026-06")
    activas = repositorio.listar_cargas(solo_activas=True)
    assert len(activas) == 2
    assert set(activas["modulo"]) == {"GIROS_AL", "GIROS_DEL"}


def test_comparar_versiones(archivo_al, tmp_path):
    repositorio.registrar_carga(archivo_al, modulo="GIROS_AL", periodo="2026-06")
    reducido = escribir_excel(tmp_path / "menos_filas.xlsx", FILAS_BASE[:2])
    repositorio.registrar_carga(reducido, modulo="GIROS_AL", periodo="2026-06")
    comparacion = repositorio.comparar_versiones("GIROS_AL", "2026-06", 1, 2)
    fila = comparacion[comparacion["Indicador"] == "Operaciones depuradas"].iloc[0]
    assert fila["v1"] == 3 and fila["v2"] == 2
    assert fila["Diferencia"] == -1


def test_modulo_invalido(archivo_al):
    with pytest.raises(ValueError):
        repositorio.registrar_carga(archivo_al, modulo="GIROS_XX", periodo="2026-06")


def test_version_inexistente():
    with pytest.raises(LookupError):
        repositorio.activar_version("GIROS_AL", "2026-06", 9)
