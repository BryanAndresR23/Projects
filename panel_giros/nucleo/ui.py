"""Piezas compartidas por las páginas de Streamlit."""
from __future__ import annotations

import io
from datetime import date
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from nucleo import bd, config, metricas, repositorio

PALETA = [
    "#1f4e79", "#2e75b6", "#4ea1d3", "#7fc4d8", "#c55a11",
    "#ed7d31", "#f4b183", "#548235", "#a9d18e", "#7030a0",
    "#b48ead", "#843c0c",
]
COLOR_TEXTO = "#1f2933"
COLOR_SUAVE = "#f4f6f9"

SEVERIDAD_ICONO = {"ERROR": "🔴", "ADVERTENCIA": "🟠", "INFO": "🔵"}


def columnas_numericas(*nombres: str) -> dict:
    """Formato contable (con separador de miles) para las columnas indicadas."""
    return {
        nombre: st.column_config.NumberColumn(nombre, format="accounting")
        for nombre in nombres
    }


def configurar_pagina(titulo: str, icono: str = "🌐") -> None:
    st.set_page_config(page_title=f"{titulo} · Panel de Giros", page_icon=icono, layout="wide")
    st.markdown(
        """
        <style>
          .block-container {padding-top: 2.2rem; padding-bottom: 3rem;}
          div[data-testid="stMetric"] {
              background: #f4f6f9; border: 1px solid #e2e8f0;
              border-radius: 10px; padding: 0.9rem 1rem;
          }
          div[data-testid="stMetricValue"] {font-size: 1.3rem; white-space: nowrap;}
          div[data-testid="stMetricLabel"] p {font-size: 0.82rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def encabezado(titulo: str, descripcion: str) -> None:
    st.title(titulo)
    st.caption(descripcion)


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------
def firma_datos() -> str:
    """Cambia cuando hay una carga o una restauración: invalida la caché."""
    with bd.sesion() as con:
        fila = con.execute(
            "SELECT COALESCE(MAX(id), 0) AS ultimo, COUNT(*) AS total FROM bitacora"
        ).fetchone()
        activas = con.execute(
            "SELECT COALESCE(GROUP_CONCAT(id), '') AS ids FROM cargas WHERE activa = 1"
        ).fetchone()
    return f"{fila['ultimo']}-{fila['total']}-{activas['ids']}"


@st.cache_data(show_spinner="Cargando operaciones publicadas…")
def operaciones_publicadas(firma: str) -> pd.DataFrame:
    return repositorio.operaciones(solo_activas=True)


def datos_publicados() -> pd.DataFrame:
    return operaciones_publicadas(firma_datos())


def refrescar() -> None:
    st.cache_data.clear()


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------
def panel_filtros(datos: pd.DataFrame, clave: str = "dash") -> dict[str, Any]:
    """Filtros combinables. Todo lo que se elige aquí afecta a todo el tablero."""
    filtros: dict[str, Any] = {}
    with st.sidebar:
        st.subheader("Filtros")
        if st.button("Limpiar filtros", width="stretch", key=f"{clave}_limpiar"):
            for llave in list(st.session_state.keys()):
                if llave.startswith(f"{clave}_f_"):
                    del st.session_state[llave]
            st.rerun()

        for dimension in ("modulo", "periodo", "corresponsal", "area_750", "deuda_771",
                          "tipo_mensaje", "moneda", "proceso", "estado"):
            valores = metricas.opciones(datos, dimension)
            if not valores or len(valores) < 2:
                continue
            elegidos = st.multiselect(
                metricas.DIMENSIONES.get(dimension, dimension),
                valores,
                key=f"{clave}_f_{dimension}",
                placeholder="Todos",
            )
            if elegidos:
                filtros[dimension] = elegidos

        if not datos.empty and datos["fecha"].notna().any():
            minima = pd.to_datetime(datos["fecha"]).min().date()
            maxima = pd.to_datetime(datos["fecha"]).max().date()
            if minima < maxima:
                rango = st.date_input(
                    "Rango de fechas", value=(minima, maxima),
                    min_value=minima, max_value=maxima,
                    format="DD/MM/YYYY", key=f"{clave}_f_fechas",
                )
                if isinstance(rango, (tuple, list)) and len(rango) == 2:
                    if (rango[0], rango[1]) != (minima, maxima):
                        filtros["rango_fechas"] = rango

        if not datos.empty:
            tope = float(datos["monto_usd"].max())
            if tope > 0:
                minimo = st.number_input(
                    "Monto USD mínimo", min_value=0.0, max_value=tope, value=0.0,
                    step=max(tope / 100, 1.0), format="%.2f", key=f"{clave}_f_monto",
                )
                if minimo > 0:
                    filtros["monto_minimo"] = minimo
    return filtros


# ---------------------------------------------------------------------------
# Indicadores y gráficos
# ---------------------------------------------------------------------------
def tarjetas(datos: pd.DataFrame, universo: pd.DataFrame | None = None) -> None:
    """Las dos métricas principales (operaciones y monto) más contexto."""
    indicadores = metricas.kpis(datos)
    referencia = metricas.kpis(universo) if universo is not None and not universo.empty else None

    columnas = st.columns(5)
    columnas[0].metric(
        "N.° de operaciones", f"{indicadores['operaciones']:,}".replace(",", "."),
        delta=_delta_pct(indicadores["operaciones"], referencia["operaciones"] if referencia else None),
        help="Cantidad de giros que cumplen los filtros seleccionados.",
    )
    columnas[1].metric(
        "Monto USD", metricas.formatear_usd_compacto(indicadores["monto_usd"]),
        delta=_delta_pct(indicadores["monto_usd"], referencia["monto_usd"] if referencia else None),
        help=f"Suma del equivalente en dólares de las operaciones filtradas: "
             f"{metricas.formatear_usd(indicadores['monto_usd'])}.",
    )
    columnas[2].metric(
        "Promedio por operación", metricas.formatear_usd_compacto(indicadores["promedio_usd"]),
        help=metricas.formatear_usd(indicadores["promedio_usd"]),
    )
    columnas[3].metric("Corresponsales", indicadores["corresponsales"])
    columnas[4].metric(
        "Operación mayor", metricas.formatear_usd_compacto(indicadores["ticket_maximo"]),
        help=metricas.formatear_usd(indicadores["ticket_maximo"]),
    )


def _delta_pct(valor: float, total: float | None) -> str | None:
    if not total:
        return None
    return f"{valor * 100 / total:.1f} % del total"


TIPOS_GRAFICO = ["Barras horizontales", "Columnas", "Línea", "Área", "Torta", "Treemap"]


def grafico(
    tabla: pd.DataFrame, medida: str, tipo: str = "Barras horizontales", titulo: str = ""
) -> go.Figure:
    """Construye el gráfico según la métrica y el tipo elegidos por el usuario."""
    figura = go.Figure()
    if tabla.empty:
        figura.add_annotation(text="Sin datos para los filtros aplicados",
                              showarrow=False, font=dict(size=15, color="#6b7280"))
        figura.update_layout(height=420, xaxis=dict(visible=False), yaxis=dict(visible=False))
        return figura

    etiquetas = tabla["etiqueta"].astype(str).tolist()
    valores = tabla["valor"].astype(float).tolist()
    textos = [metricas.formatear(v, medida) for v in valores]
    nombre_medida = metricas.MEDIDAS.get(medida, medida)

    if tipo == "Barras horizontales":
        figura.add_trace(go.Bar(
            x=valores, y=etiquetas, orientation="h", text=textos, textposition="auto",
            marker_color=PALETA[0], hovertemplate="%{y}<br>%{text}<extra></extra>",
        ))
        figura.update_layout(yaxis=dict(autorange="reversed"))
    elif tipo == "Columnas":
        figura.add_trace(go.Bar(
            x=etiquetas, y=valores, text=textos, textposition="auto",
            marker_color=PALETA[1], hovertemplate="%{x}<br>%{text}<extra></extra>",
        ))
    elif tipo == "Línea":
        figura.add_trace(go.Scatter(
            x=etiquetas, y=valores, mode="lines+markers+text", text=textos,
            textposition="top center", line=dict(color=PALETA[0], width=3),
            marker=dict(size=8), hovertemplate="%{x}<br>%{text}<extra></extra>",
        ))
    elif tipo == "Área":
        figura.add_trace(go.Scatter(
            x=etiquetas, y=valores, mode="lines+markers", fill="tozeroy",
            line=dict(color=PALETA[1], width=2.5),
            hovertemplate="%{x}<br>%{y:,.2f}<extra></extra>",
        ))
    elif tipo == "Torta":
        figura.add_trace(go.Pie(
            labels=etiquetas, values=[abs(v) for v in valores], hole=0.45,
            marker=dict(colors=PALETA * 3), textinfo="label+percent",
            hovertemplate="%{label}<br>%{value:,.2f}<extra></extra>",
        ))
    else:  # Treemap
        figura.add_trace(go.Treemap(
            labels=etiquetas, parents=[""] * len(etiquetas), values=[abs(v) for v in valores],
            marker=dict(colors=PALETA * 3), texttemplate="%{label}<br>%{value:,.0f}",
            hovertemplate="%{label}<br>%{value:,.2f}<extra></extra>",
        ))

    figura.update_layout(
        title=dict(text=titulo or nombre_medida, font=dict(size=17, color=COLOR_TEXTO)),
        height=460, margin=dict(l=10, r=10, t=60, b=40),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(family="Segoe UI, Roboto, sans-serif", color=COLOR_TEXTO),
        showlegend=tipo == "Torta",
        xaxis=dict(gridcolor="#eef2f7", zeroline=False),
        yaxis=dict(gridcolor="#eef2f7", zeroline=False),
    )
    return figura


def grafico_comparado(
    tabla: pd.DataFrame, series: dict[str, list[float]], titulo: str
) -> go.Figure:
    """Barras agrupadas para comparar dos módulos sobre la misma dimensión."""
    figura = go.Figure()
    for indice, (nombre, valores) in enumerate(series.items()):
        figura.add_trace(go.Bar(
            name=nombre, x=tabla, y=valores, marker_color=PALETA[indice % len(PALETA)]
        ))
    figura.update_layout(
        barmode="group", title=titulo, height=430,
        margin=dict(l=10, r=10, t=60, b=40), plot_bgcolor="white",
        font=dict(family="Segoe UI, Roboto, sans-serif", color=COLOR_TEXTO),
        xaxis=dict(gridcolor="#eef2f7"), yaxis=dict(gridcolor="#eef2f7"),
    )
    return figura


# ---------------------------------------------------------------------------
# Exportación
# ---------------------------------------------------------------------------
def a_excel(hojas: dict[str, pd.DataFrame]) -> bytes:
    """Empaqueta varios DataFrames en un solo Excel descargable."""
    memoria = io.BytesIO()
    with pd.ExcelWriter(memoria, engine="xlsxwriter") as escritor:
        for nombre, tabla in hojas.items():
            if tabla is None or tabla.empty:
                continue
            indice = tabla.index.name is not None
            tabla.to_excel(escritor, sheet_name=nombre[:31], index=indice)
    return memoria.getvalue()


def boton_descarga_excel(hojas: dict[str, pd.DataFrame], nombre: str, etiqueta: str, clave: str) -> None:
    contenido = a_excel(hojas)
    st.download_button(
        etiqueta, data=contenido, file_name=nombre,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=clave, width="stretch",
    )


# ---------------------------------------------------------------------------
# Varios
# ---------------------------------------------------------------------------
def selector_periodo(clave: str, valor: date | None = None) -> str:
    """Año y mes del período que se está cargando."""
    hoy = valor or date.today()
    columnas = st.columns(2)
    anio = columnas[0].number_input("Año", min_value=2015, max_value=2100, value=hoy.year,
                                    step=1, key=f"{clave}_anio")
    mes = columnas[1].selectbox("Mes", list(config.MESES), index=hoy.month - 1,
                                format_func=lambda m: config.MESES[m], key=f"{clave}_mes")
    return config.periodo(int(anio), int(mes))


def estado_corto(estado: str) -> str:
    """Etiqueta breve para las tarjetas, donde el texto completo no entra."""
    return {
        config.ESTADO_VALIDA: "Válida",
        config.ESTADO_ADVERTENCIAS: "Con advertencias",
        config.ESTADO_RECHAZADA: "Rechazada",
    }.get(estado, estado)


def color_estado(estado: str) -> str:
    return {
        config.ESTADO_VALIDA: "🟢",
        config.ESTADO_ADVERTENCIAS: "🟠",
        config.ESTADO_RECHAZADA: "🔴",
    }.get(estado, "⚪")


def pie_de_pagina() -> None:
    st.divider()
    st.caption(
        "Panel de Giros · cada cifra proviene de la versión publicada de una carga validada. "
        "El histórico completo queda en la pestaña «Histórico de cargas»."
    )
