"""Pruebas de las reglas de validación, en especial la coherencia de módulo."""
from __future__ import annotations

from nucleo import config, etl, validaciones
from tests.conftest import FILAS_BASE, escribir_excel


def _hallazgos(ruta, modulo="GIROS_AL", periodo="2026-06", nombre=""):
    resultado = etl.procesar(ruta, modulo=modulo, periodo=periodo)
    return validaciones.validar(resultado, modulo=modulo, periodo=periodo,
                                nombre_archivo=nombre or ruta.name)


def _reglas(hallazgos, severidad=None):
    return {h.regla for h in hallazgos if severidad is None or h.severidad == severidad}


def test_archivo_correcto_no_tiene_errores(archivo_al):
    hallazgos = _hallazgos(archivo_al)
    assert not _reglas(hallazgos, config.SEVERIDAD_ERROR)
    assert "MODULO_COINCIDE" in _reglas(hallazgos)
    assert validaciones.estado_de(hallazgos) != config.ESTADO_RECHAZADA


def test_rechaza_archivo_del_cargado_como_al(archivo_del):
    hallazgos = _hallazgos(archivo_del, modulo="GIROS_AL")
    assert "MODULO_NO_COINCIDE" in _reglas(hallazgos, config.SEVERIDAD_ERROR)
    assert validaciones.estado_de(hallazgos) == config.ESTADO_RECHAZADA


def test_rechaza_archivo_al_cargado_como_del(archivo_al):
    hallazgos = _hallazgos(archivo_al, modulo="GIROS_DEL")
    assert "MODULO_NO_COINCIDE" in _reglas(hallazgos, config.SEVERIDAD_ERROR)


def test_advierte_cuando_el_archivo_mezcla_sentidos(tmp_path):
    filas = [dict(f) for f in FILAS_BASE]
    filas[0]["SENTIDO"] = "Giros DEL"
    ruta = escribir_excel(tmp_path / "mezclado.xlsx", filas)
    hallazgos = _hallazgos(ruta, modulo="GIROS_AL")
    assert "MODULO_MEZCLADO" in _reglas(hallazgos, config.SEVERIDAD_ADVERTENCIA)


def test_sin_columna_sentido_usa_el_nombre_del_archivo(tmp_path):
    filas = [{k: v for k, v in f.items() if k != "SENTIDO"} for f in FILAS_BASE]
    ruta = escribir_excel(tmp_path / "Giros_DEL_junio.xlsx", filas)
    hallazgos = _hallazgos(ruta, modulo="GIROS_AL")
    assert "MODULO_NOMBRE_ARCHIVO" in _reglas(hallazgos, config.SEVERIDAD_ADVERTENCIA)


def test_sin_evidencia_de_modulo_se_registra_la_declaracion(tmp_path):
    filas = [{k: v for k, v in f.items() if k != "SENTIDO"} for f in FILAS_BASE]
    ruta = escribir_excel(tmp_path / "reporte_mensual.xlsx", filas)
    hallazgos = _hallazgos(ruta, modulo="GIROS_AL")
    assert "MODULO_SIN_EVIDENCIA" in _reglas(hallazgos)


def test_rechaza_periodo_equivocado(archivo_al):
    hallazgos = _hallazgos(archivo_al, periodo="2026-03")
    assert "PERIODO_NO_COINCIDE" in _reglas(hallazgos, config.SEVERIDAD_ERROR)


def test_advierte_periodo_parcial(tmp_path):
    filas = [dict(f) for f in FILAS_BASE]
    filas[0]["FECHA DE OPERACIÓN"] = "28/05/2026"
    ruta = escribir_excel(tmp_path / "parcial.xlsx", filas)
    hallazgos = _hallazgos(ruta)
    assert "PERIODO_PARCIAL" in _reglas(hallazgos, config.SEVERIDAD_ADVERTENCIA)


def test_detecta_referencias_duplicadas(tmp_path):
    filas = [dict(f) for f in FILAS_BASE]
    filas[1]["N° OPERACIÓN"] = filas[0]["N° OPERACIÓN"]
    ruta = escribir_excel(tmp_path / "duplicados.xlsx", filas)
    hallazgos = _hallazgos(ruta)
    assert "REFERENCIAS_DUPLICADAS" in _reglas(hallazgos, config.SEVERIDAD_ADVERTENCIA)


def test_faltan_columnas_obligatorias(tmp_path):
    filas = [{k: v for k, v in f.items() if k not in ("MONEDA", "MONTO", "MONTO USD")}
             for f in FILAS_BASE]
    ruta = escribir_excel(tmp_path / "incompleto.xlsx", filas)
    hallazgos = _hallazgos(ruta)
    assert "ESTRUCTURA_COLUMNAS" in _reglas(hallazgos, config.SEVERIDAD_ERROR)


def test_estado_segun_severidad():
    error = [validaciones.Hallazgo("X", config.SEVERIDAD_ERROR, "m")]
    aviso = [validaciones.Hallazgo("X", config.SEVERIDAD_ADVERTENCIA, "m")]
    info = [validaciones.Hallazgo("X", config.SEVERIDAD_INFO, "m")]
    assert validaciones.estado_de(error) == config.ESTADO_RECHAZADA
    assert validaciones.estado_de(aviso) == config.ESTADO_ADVERTENCIAS
    assert validaciones.estado_de(info) == config.ESTADO_VALIDA
