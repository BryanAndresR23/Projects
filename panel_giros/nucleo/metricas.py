"""Medidas, dimensiones y agregaciones que alimentan el tablero."""
from __future__ import annotations

from typing import Any

import pandas as pd

from nucleo import config

SIN_DATO = "Sin dato"

# Rubros por los que se puede abrir cualquier métrica.
DIMENSIONES: dict[str, str] = {
    "modulo": "Módulo (Giros AL / DEL)",
    "corresponsal": "Corresponsal",
    "area_750": "Área 750",
    "deuda_771": "Deuda 771",
    "tipo_mensaje": "TF / GS",
    "nombre_mes": "Mes",
    "periodo": "Período",
    "moneda": "Moneda",
    "proceso": "Proceso",
    "estado": "Estado de la operación",
    "beneficiario": "Beneficiario / Ordenante",
}

# Métricas disponibles. El gráfico se redibuja con la que elija el usuario.
MEDIDAS: dict[str, str] = {
    "operaciones": "N.° de operaciones",
    "monto_usd": "Monto USD",
    "promedio_usd": "Promedio USD por operación",
    "participacion": "Participación % del monto",
}

DIMENSIONES_TEMPORALES = {"nombre_mes", "periodo", "anio"}


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------
def aplicar_filtros(datos: pd.DataFrame, filtros: dict[str, Any] | None) -> pd.DataFrame:
    """Aplica los filtros del panel lateral. Una lista vacía significa 'todos'."""
    if datos.empty or not filtros:
        return datos
    filtrado = datos
    for campo, valor in filtros.items():
        if valor in (None, "", [], ()):
            continue
        if campo == "rango_fechas" and isinstance(valor, (tuple, list)) and len(valor) == 2:
            desde, hasta = pd.to_datetime(valor[0]), pd.to_datetime(valor[1])
            filtrado = filtrado[(filtrado["fecha"] >= desde) & (filtrado["fecha"] <= hasta)]
            continue
        if campo == "monto_minimo":
            filtrado = filtrado[filtrado["monto_usd"] >= float(valor)]
            continue
        if campo not in filtrado.columns:
            continue
        seleccion = list(valor) if isinstance(valor, (list, tuple, set)) else [valor]
        filtrado = filtrado[_etiquetas(filtrado, campo).isin(seleccion)]
    return filtrado


def opciones(datos: pd.DataFrame, dimension: str) -> list[str]:
    """Valores disponibles de una dimensión, listos para un selector."""
    if datos.empty or dimension not in datos.columns:
        return []
    return sorted(_etiquetas(datos, dimension).unique().tolist())


def _etiquetas(datos: pd.DataFrame, dimension: str) -> pd.Series:
    """Serie de etiquetas legibles de una dimensión (sin vacíos ni nulos)."""
    serie = datos[dimension]
    if dimension == "modulo":
        return serie.map(lambda v: config.etiqueta_modulo(str(v)))
    if dimension == "periodo":
        return serie.map(lambda v: config.etiqueta_periodo(str(v)) if v else SIN_DATO)
    texto = serie.fillna("").astype(str).str.strip()
    return texto.replace({"": SIN_DATO, "SIN CORRESPONSAL": SIN_DATO, "nan": SIN_DATO})


# ---------------------------------------------------------------------------
# Medidas
# ---------------------------------------------------------------------------
def kpis(datos: pd.DataFrame) -> dict[str, float]:
    """Tarjetas principales: siempre operaciones y monto, como se definió."""
    if datos.empty:
        return {"operaciones": 0, "monto_usd": 0.0, "promedio_usd": 0.0,
                "corresponsales": 0, "monedas": 0, "ticket_maximo": 0.0}
    return {
        "operaciones": int(len(datos)),
        "monto_usd": float(datos["monto_usd"].sum()),
        "promedio_usd": float(datos["monto_usd"].mean()),
        "corresponsales": int(_etiquetas(datos, "corresponsal").nunique()),
        "monedas": int(_etiquetas(datos, "moneda").nunique()),
        "ticket_maximo": float(datos["monto_usd"].max()),
    }


def agregar(
    datos: pd.DataFrame,
    dimension: str,
    medida: str = "monto_usd",
    *,
    top: int | None = None,
    agrupar_resto: bool = True,
) -> pd.DataFrame:
    """Agrupa por una dimensión y calcula la medida elegida.

    Devuelve columnas ``etiqueta`` y ``valor``, ordenadas de mayor a menor
    (o cronológicamente si la dimensión es temporal).
    """
    columnas = ["etiqueta", "valor"]
    if datos.empty or dimension not in datos.columns:
        return pd.DataFrame(columns=columnas)

    grupos = datos.groupby(_etiquetas(datos, dimension), dropna=False)
    if medida == "operaciones":
        valores = grupos.size()
    elif medida == "promedio_usd":
        valores = grupos["monto_usd"].mean()
    elif medida == "participacion":
        total = float(datos["monto_usd"].sum())
        valores = grupos["monto_usd"].sum() * 100 / total if total else grupos["monto_usd"].sum() * 0
    else:
        valores = grupos["monto_usd"].sum()

    tabla = valores.reset_index()
    tabla.columns = columnas
    tabla = _ordenar(tabla, datos, dimension)

    if top and len(tabla) > top and not _es_temporal(dimension):
        cabeza = tabla.head(top)
        if agrupar_resto:
            resto = tabla.iloc[top:]
            valor_resto = (
                resto["valor"].sum() if medida != "promedio_usd" else resto["valor"].mean()
            )
            cabeza = pd.concat(
                [cabeza, pd.DataFrame([{"etiqueta": f"Otros ({len(resto)})", "valor": valor_resto}])],
                ignore_index=True,
            )
        tabla = cabeza
    return tabla.reset_index(drop=True)


def _es_temporal(dimension: str) -> bool:
    return dimension in DIMENSIONES_TEMPORALES


def _ordenar(tabla: pd.DataFrame, datos: pd.DataFrame, dimension: str) -> pd.DataFrame:
    if dimension == "nombre_mes":
        orden = {nombre: numero for numero, nombre in config.MESES.items()}
        return tabla.sort_values("etiqueta", key=lambda s: s.map(orden).fillna(99))
    if dimension == "periodo":
        crudo = dict(zip(_etiquetas(datos, "periodo"), datos["periodo"]))
        return tabla.sort_values("etiqueta", key=lambda s: s.map(crudo).fillna(""))
    return tabla.sort_values("valor", ascending=False)


def serie_temporal(datos: pd.DataFrame, medida: str = "monto_usd", por: str = "mes") -> pd.DataFrame:
    """Evolución en el tiempo: por día o por mes."""
    if datos.empty or "fecha" not in datos.columns:
        return pd.DataFrame(columns=["etiqueta", "valor"])
    marco = datos.copy()
    marco["fecha"] = pd.to_datetime(marco["fecha"], errors="coerce")
    marco = marco.dropna(subset=["fecha"])
    if marco.empty:
        return pd.DataFrame(columns=["etiqueta", "valor"])

    clave = marco["fecha"].dt.strftime("%Y-%m" if por == "mes" else "%Y-%m-%d")
    grupos = marco.groupby(clave)
    if medida == "operaciones":
        valores = grupos.size()
    elif medida == "promedio_usd":
        valores = grupos["monto_usd"].mean()
    elif medida == "participacion":
        total = float(marco["monto_usd"].sum())
        valores = grupos["monto_usd"].sum() * 100 / total if total else grupos["monto_usd"].sum() * 0
    else:
        valores = grupos["monto_usd"].sum()

    tabla = valores.reset_index()
    tabla.columns = ["etiqueta", "valor"]
    return tabla.sort_values("etiqueta").reset_index(drop=True)


def tabla_cruzada(
    datos: pd.DataFrame, filas: str, columnas: str, medida: str = "monto_usd"
) -> pd.DataFrame:
    """Matriz dimensión x dimensión, con totales por fila y columna."""
    if datos.empty or filas not in datos.columns or columnas not in datos.columns:
        return pd.DataFrame()

    marco = datos.copy()
    marco["_filas"] = _etiquetas(marco, filas)
    marco["_columnas"] = _etiquetas(marco, columnas)

    if medida == "operaciones":
        matriz = marco.pivot_table(index="_filas", columns="_columnas", values="monto_usd",
                                   aggfunc="size", fill_value=0)
    elif medida == "promedio_usd":
        matriz = marco.pivot_table(index="_filas", columns="_columnas", values="monto_usd",
                                   aggfunc="mean", fill_value=0)
    else:
        matriz = marco.pivot_table(index="_filas", columns="_columnas", values="monto_usd",
                                   aggfunc="sum", fill_value=0)
        if medida == "participacion":
            total = float(marco["monto_usd"].sum())
            matriz = matriz * 100 / total if total else matriz * 0

    if medida != "promedio_usd" and not matriz.empty:
        matriz["Total"] = matriz.sum(axis=1)
        matriz.loc["Total"] = matriz.sum(axis=0)
    matriz.index.name = DIMENSIONES.get(filas, filas)
    matriz.columns.name = DIMENSIONES.get(columnas, columnas)
    return matriz


def ranking(datos: pd.DataFrame, dimension: str, top: int = 10) -> pd.DataFrame:
    """Tabla de apoyo: operaciones, monto, promedio y participación por rubro."""
    if datos.empty or dimension not in datos.columns:
        return pd.DataFrame()
    grupos = datos.groupby(_etiquetas(datos, dimension), dropna=False)
    tabla = pd.DataFrame({
        "Operaciones": grupos.size(),
        "Monto USD": grupos["monto_usd"].sum(),
        "Promedio USD": grupos["monto_usd"].mean(),
    })
    total = float(tabla["Monto USD"].sum())
    tabla["Participación %"] = tabla["Monto USD"] * 100 / total if total else 0.0
    tabla = tabla.sort_values("Monto USD", ascending=False).head(top)
    tabla.index.name = DIMENSIONES.get(dimension, dimension)
    return tabla.round(2)


# ---------------------------------------------------------------------------
# Presentación
# ---------------------------------------------------------------------------
ENCABEZADOS_DETALLE = {
    "fecha": "Fecha", "modulo": "Módulo", "periodo": "Período", "referencia": "Referencia",
    "corresponsal": "Corresponsal", "area_750": "Área 750", "deuda_771": "Deuda 771",
    "tipo_mensaje": "TF/GS", "proceso": "Proceso", "moneda": "Moneda", "monto": "Monto origen",
    "tipo_cambio": "Tipo de cambio", "monto_usd": "Monto USD", "beneficiario": "Beneficiario",
    "estado": "Estado",
}


def detalle(datos: pd.DataFrame) -> pd.DataFrame:
    """Tabla de respaldo: las filas que sustentan cada cifra del tablero."""
    if datos.empty:
        return pd.DataFrame(columns=list(ENCABEZADOS_DETALLE.values()))
    presentes = [c for c in ENCABEZADOS_DETALLE if c in datos.columns]
    tabla = datos[presentes].copy()
    if "modulo" in tabla.columns:
        tabla["modulo"] = tabla["modulo"].map(lambda v: config.etiqueta_modulo(str(v)))
    if "fecha" in tabla.columns:
        tabla["fecha"] = pd.to_datetime(tabla["fecha"], errors="coerce").dt.strftime("%d/%m/%Y")
    return tabla.rename(columns=ENCABEZADOS_DETALLE)


def formatear(valor: float, medida: str) -> str:
    if medida == "operaciones":
        return f"{int(valor):,}".replace(",", ".")
    if medida == "participacion":
        return f"{valor:,.1f} %"
    return "USD " + f"{valor:,.2f}"


def formatear_usd(valor: float) -> str:
    return "USD " + f"{valor:,.2f}"


def formatear_usd_compacto(valor: float) -> str:
    """Abrevia las cifras grandes para que quepan en las tarjetas del tablero."""
    magnitud = abs(valor)
    if magnitud >= 1_000_000_000:
        return f"USD {valor / 1_000_000_000:,.2f} MM"
    if magnitud >= 1_000_000:
        return f"USD {valor / 1_000_000:,.2f} M"
    return formatear_usd(valor)
