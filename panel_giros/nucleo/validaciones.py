"""Reglas de validación que se aplican a cada carga antes de publicarla."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from nucleo import catalogo, config, etl as etl_mod


@dataclass
class Hallazgo:
    """Resultado de una regla. ERROR bloquea la publicación; ADVERTENCIA no."""

    regla: str
    severidad: str
    mensaje: str
    filas_afectadas: int = 0
    detalle: dict[str, Any] = field(default_factory=dict)

    def detalle_json(self) -> str | None:
        return json.dumps(self.detalle, ensure_ascii=False) if self.detalle else None


def _error(regla: str, mensaje: str, filas: int = 0, **detalle) -> Hallazgo:
    return Hallazgo(regla, config.SEVERIDAD_ERROR, mensaje, filas, detalle)


def _advertencia(regla: str, mensaje: str, filas: int = 0, **detalle) -> Hallazgo:
    return Hallazgo(regla, config.SEVERIDAD_ADVERTENCIA, mensaje, filas, detalle)


def _info(regla: str, mensaje: str, filas: int = 0, **detalle) -> Hallazgo:
    return Hallazgo(regla, config.SEVERIDAD_INFO, mensaje, filas, detalle)


def validar(
    resultado: etl_mod.ResultadoETL,
    *,
    modulo: str,
    periodo: str,
    nombre_archivo: str = "",
) -> list[Hallazgo]:
    """Aplica todas las reglas y devuelve los hallazgos en orden de severidad."""
    hallazgos: list[Hallazgo] = []
    hallazgos += _regla_estructura(resultado)
    hallazgos += _regla_contenido(resultado)
    hallazgos += _regla_modulo(resultado, modulo, nombre_archivo)
    hallazgos += _regla_periodo(resultado, periodo)
    hallazgos += _regla_descartes(resultado)
    hallazgos += _regla_montos(resultado)
    hallazgos += _regla_monedas(resultado)
    hallazgos += _regla_duplicados(resultado)
    hallazgos += _regla_catalogos(resultado)

    orden = {config.SEVERIDAD_ERROR: 0, config.SEVERIDAD_ADVERTENCIA: 1, config.SEVERIDAD_INFO: 2}
    return sorted(hallazgos, key=lambda h: (orden.get(h.severidad, 9), h.regla))


# ---------------------------------------------------------------------------
# Reglas
# ---------------------------------------------------------------------------
def _regla_estructura(resultado: etl_mod.ResultadoETL) -> list[Hallazgo]:
    if resultado.fila_encabezado is None:
        return [_error(
            "ESTRUCTURA_ENCABEZADO",
            "No se encontró una fila de encabezados reconocible. Revise que el archivo "
            "sea el reporte de giros y no un resumen o un archivo protegido.",
        )]

    hallazgos: list[Hallazgo] = []
    if resultado.columnas_faltantes:
        etiquetas = ", ".join(catalogo.etiqueta_campo(c) for c in resultado.columnas_faltantes)
        hallazgos.append(_error(
            "ESTRUCTURA_COLUMNAS",
            f"Faltan columnas obligatorias: {etiquetas}. "
            "Agregue el nombre real de esas columnas en catalogos/columnas.yaml si el reporte las llama de otro modo.",
            detalle={"faltantes": resultado.columnas_faltantes},
        ))
    else:
        hallazgos.append(_info(
            "ESTRUCTURA_COLUMNAS",
            f"Se reconocieron {len(resultado.columnas_detectadas)} columnas "
            f"(encabezado en la fila {resultado.fila_encabezado}).",
            detalle={"columnas": resultado.columnas_detectadas},
        ))

    if resultado.columnas_ignoradas:
        hallazgos.append(_info(
            "COLUMNAS_NO_MAPEADAS",
            f"{len(resultado.columnas_ignoradas)} columnas del archivo no se usan en el tablero.",
            detalle={"columnas": resultado.columnas_ignoradas[:40]},
        ))
    return hallazgos


def _regla_contenido(resultado: etl_mod.ResultadoETL) -> list[Hallazgo]:
    if resultado.filas_origen == 0:
        return [_error("SIN_FILAS", "El archivo no contiene filas de datos debajo del encabezado.")]
    if resultado.filas_depuradas == 0:
        return [_error(
            "SIN_FILAS_VALIDAS",
            f"Ninguna de las {resultado.filas_origen} filas superó la depuración. "
            "Revise el detalle de descartes.",
            filas=resultado.filas_origen,
        )]
    return []


def _regla_modulo(resultado: etl_mod.ResultadoETL, modulo: str, nombre_archivo: str) -> list[Hallazgo]:
    """Comprueba que el archivo corresponda al módulo elegido por el validador."""
    esperado = config.etiqueta_modulo(modulo)
    datos = resultado.datos

    if not datos.empty and "sentido" in datos.columns:
        deducidos = [catalogo.modulo_de_texto(v) for v in datos["sentido"]]
        conteo = Counter(m for m in deducidos if m)
        if conteo:
            total = sum(conteo.values())
            propios = conteo.get(modulo, 0)
            ajenos = total - propios
            if propios == 0:
                otro = config.etiqueta_modulo(conteo.most_common(1)[0][0])
                return [_error(
                    "MODULO_NO_COINCIDE",
                    f"Seleccionó {esperado}, pero el archivo corresponde a {otro} "
                    f"({total} filas con ese sentido). Cambie la selección o el archivo.",
                    filas=total,
                    detalle={"esperado": modulo, "encontrado": dict(conteo)},
                )]
            if ajenos:
                return [_advertencia(
                    "MODULO_MEZCLADO",
                    f"{ajenos} de {total} filas no corresponden a {esperado}. "
                    "Se conservan todas, pero conviene revisar el origen del reporte.",
                    filas=ajenos,
                    detalle={"esperado": modulo, "encontrado": dict(conteo)},
                )]
            return [_info(
                "MODULO_COINCIDE",
                f"El sentido de las {total} filas coincide con {esperado}.",
                filas=total,
            )]

    deducido_nombre = catalogo.modulo_de_texto(nombre_archivo)
    if deducido_nombre and deducido_nombre != modulo:
        return [_advertencia(
            "MODULO_NOMBRE_ARCHIVO",
            f"Seleccionó {esperado}, pero el nombre del archivo sugiere "
            f"{config.etiqueta_modulo(deducido_nombre)}. Confirme antes de publicar.",
            detalle={"archivo": nombre_archivo, "deducido": deducido_nombre},
        )]
    if deducido_nombre == modulo:
        return [_info("MODULO_COINCIDE", f"El nombre del archivo confirma {esperado}.")]

    return [_advertencia(
        "MODULO_SIN_EVIDENCIA",
        f"El archivo no trae una columna de sentido ni un nombre que permita confirmar {esperado}. "
        "Se registra la selección del validador como declaración responsable.",
        detalle={"modulo": modulo},
    )]


def _regla_periodo(resultado: etl_mod.ResultadoETL, periodo: str) -> list[Hallazgo]:
    datos = resultado.datos
    if datos.empty:
        return []
    anio, mes = config.partes_periodo(periodo)
    fuera = datos[(datos["anio"] != anio) | (datos["mes"] != mes)]
    if fuera.empty:
        return [_info("PERIODO_COINCIDE", f"Todas las fechas pertenecen a {config.etiqueta_periodo(periodo)}.")]

    periodos = sorted({f"{a:04d}-{m:02d}" for a, m in zip(fuera["anio"], fuera["mes"])})
    if len(fuera) == len(datos):
        return [_error(
            "PERIODO_NO_COINCIDE",
            f"Ninguna fila pertenece a {config.etiqueta_periodo(periodo)}. "
            f"El archivo contiene: {', '.join(periodos[:6])}.",
            filas=len(fuera),
            detalle={"periodos": periodos},
        )]
    return [_advertencia(
        "PERIODO_PARCIAL",
        f"{len(fuera)} de {len(datos)} filas están fuera de {config.etiqueta_periodo(periodo)} "
        f"({', '.join(periodos[:6])}).",
        filas=len(fuera),
        detalle={"periodos": periodos},
    )]


def _regla_descartes(resultado: etl_mod.ResultadoETL) -> list[Hallazgo]:
    if resultado.descartes.empty:
        return []
    hallazgos: list[Hallazgo] = []
    conteo = Counter(resultado.descartes["motivo"])
    bloqueantes = {etl_mod.MOTIVO_SIN_TC}
    for motivo, cantidad in conteo.items():
        filas = [int(f) for f in resultado.descartes.loc[resultado.descartes["motivo"] == motivo, "_fila"][:50]]
        if motivo in bloqueantes:
            hallazgos.append(_error(
                "TIPO_CAMBIO_FALTANTE",
                f"{cantidad} filas en moneda distinta de USD no tienen tipo de cambio. "
                "Cargue el factor en catalogos/tipos_cambio.yaml o incluya la columna Monto USD.",
                filas=cantidad, detalle={"filas": filas},
            ))
        elif motivo == etl_mod.MOTIVO_TOTALES:
            hallazgos.append(_info(
                "FILAS_TOTALES", f"Se excluyeron {cantidad} filas de totales o subtotales.",
                filas=cantidad, detalle={"filas": filas},
            ))
        else:
            hallazgos.append(_advertencia(
                "FILAS_DESCARTADAS", f"{cantidad} filas descartadas: {motivo.lower()}.",
                filas=cantidad, detalle={"filas": filas},
            ))
    return hallazgos


def _regla_montos(resultado: etl_mod.ResultadoETL) -> list[Hallazgo]:
    datos = resultado.datos
    if datos.empty:
        return []
    hallazgos: list[Hallazgo] = []
    negativos = datos[datos["monto_usd"] < 0]
    if not negativos.empty:
        hallazgos.append(_advertencia(
            "MONTOS_NEGATIVOS",
            f"{len(negativos)} operaciones tienen monto negativo (reversos o notas de crédito).",
            filas=len(negativos), detalle={"filas": [int(f) for f in negativos["_fila"][:50]]},
        ))
    ceros = datos[datos["monto_usd"] == 0]
    if not ceros.empty:
        hallazgos.append(_advertencia(
            "MONTOS_EN_CERO", f"{len(ceros)} operaciones tienen monto USD igual a cero.",
            filas=len(ceros), detalle={"filas": [int(f) for f in ceros["_fila"][:50]]},
        ))
    return hallazgos


def _regla_monedas(resultado: etl_mod.ResultadoETL) -> list[Hallazgo]:
    datos = resultado.datos
    if datos.empty:
        return []
    conocidas = set(catalogo.monedas_conocidas())
    desconocidas = sorted({m for m in datos["moneda"] if m and m not in conocidas})
    if not desconocidas:
        return []
    return [_advertencia(
        "MONEDA_NO_CATALOGADA",
        f"Monedas fuera del catálogo: {', '.join(desconocidas[:10])}. "
        "Agréguelas en catalogos/columnas.yaml si son válidas.",
        filas=int(datos["moneda"].isin(desconocidas).sum()),
        detalle={"monedas": desconocidas},
    )]


def _regla_duplicados(resultado: etl_mod.ResultadoETL) -> list[Hallazgo]:
    datos = resultado.datos
    if datos.empty or "referencia" not in datos.columns:
        return []
    hallazgos: list[Hallazgo] = []
    sin_referencia = datos[datos["referencia"] == ""]
    if not sin_referencia.empty:
        hallazgos.append(_advertencia(
            "REFERENCIA_VACIA",
            f"{len(sin_referencia)} operaciones no tienen número de referencia.",
            filas=len(sin_referencia),
            detalle={"filas": [int(f) for f in sin_referencia["_fila"][:50]]},
        ))
    con_referencia = datos[datos["referencia"] != ""]
    repetidas = con_referencia[con_referencia.duplicated("referencia", keep=False)]
    if not repetidas.empty:
        hallazgos.append(_advertencia(
            "REFERENCIAS_DUPLICADAS",
            f"{repetidas['referencia'].nunique()} referencias se repiten "
            f"({len(repetidas)} filas). Verifique que no sea una doble carga.",
            filas=len(repetidas),
            detalle={"referencias": sorted(repetidas["referencia"].unique())[:30]},
        ))
    return hallazgos


def _regla_catalogos(resultado: etl_mod.ResultadoETL) -> list[Hallazgo]:
    datos = resultado.datos
    if datos.empty:
        return []
    hallazgos: list[Hallazgo] = []
    for campo, regla in (("corresponsal", "CORRESPONSAL_VACIO"), ("area_750", "AREA_750_VACIA"),
                         ("deuda_771", "DEUDA_771_VACIA"), ("tipo_mensaje", "TF_GS_VACIO")):
        if campo not in datos.columns:
            continue
        vacios = datos[(datos[campo] == "") | (datos[campo] == "SIN CORRESPONSAL")]
        if not vacios.empty:
            hallazgos.append(_advertencia(
                regla,
                f"{len(vacios)} operaciones sin {catalogo.etiqueta_campo(campo)}; "
                "se agruparán como “Sin dato” en el tablero.",
                filas=len(vacios),
            ))
    return hallazgos


def estado_de(hallazgos: list[Hallazgo]) -> str:
    """Traduce los hallazgos al estado de la carga."""
    if any(h.severidad == config.SEVERIDAD_ERROR for h in hallazgos):
        return config.ESTADO_RECHAZADA
    if any(h.severidad == config.SEVERIDAD_ADVERTENCIA for h in hallazgos):
        return config.ESTADO_ADVERTENCIAS
    return config.ESTADO_VALIDA


def resumen(hallazgos: list[Hallazgo]) -> dict[str, int]:
    conteo = Counter(h.severidad for h in hallazgos)
    return {
        "errores": conteo.get(config.SEVERIDAD_ERROR, 0),
        "advertencias": conteo.get(config.SEVERIDAD_ADVERTENCIA, 0),
        "informativos": conteo.get(config.SEVERIDAD_INFO, 0),
    }


def a_dataframe(hallazgos: list[Hallazgo]) -> pd.DataFrame:
    return pd.DataFrame([
        {"Regla": h.regla, "Severidad": h.severidad, "Mensaje": h.mensaje, "Filas": h.filas_afectadas}
        for h in hallazgos
    ])
