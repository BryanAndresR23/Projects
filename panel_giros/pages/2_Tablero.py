"""Tablero analítico: el usuario elige la métrica y los gráficos se redibujan."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import streamlit as st

from nucleo import config, metricas, ui

ui.configurar_pagina("Tablero", "📊")
ui.encabezado(
    "Tablero analítico",
    "Elija la métrica y el rubro: los gráficos, la matriz y el detalle se recalculan al instante "
    "sobre las versiones publicadas.",
)

universo = ui.datos_publicados()

with st.sidebar:
    if st.button("Actualizar datos", width="stretch", key="tablero_refrescar"):
        ui.refrescar()
        st.rerun()

if universo.empty:
    st.info("Aún no hay ninguna carga publicada. Cargue un archivo para ver el tablero.", icon="📥")
    st.page_link("pages/1_Cargar_archivo.py", label="Ir a Cargar archivo", icon="📥")
    st.stop()

filtros = ui.panel_filtros(universo, clave="tablero")
datos = metricas.aplicar_filtros(universo, filtros)

if filtros:
    resumen_filtros = " · ".join(
        f"{metricas.DIMENSIONES.get(k, k)}: {', '.join(map(str, v)) if isinstance(v, (list, tuple)) else v}"
        for k, v in filtros.items()
    )
    st.caption(f"Filtros activos → {resumen_filtros}")

if datos.empty:
    st.warning("Ninguna operación cumple los filtros seleccionados.", icon="⚠️")
    st.stop()

ui.tarjetas(datos, universo)
st.divider()

# ---------------------------------------------------------------------------
# Gráfico principal, gobernado por la métrica y el rubro elegidos
# ---------------------------------------------------------------------------
controles = st.columns([2, 2, 2, 1])
medida = controles[0].selectbox(
    "Métrica", list(metricas.MEDIDAS), format_func=lambda m: metricas.MEDIDAS[m],
    key="tablero_medida",
)
dimension = controles[1].selectbox(
    "Abrir por", list(metricas.DIMENSIONES), format_func=lambda d: metricas.DIMENSIONES[d],
    index=list(metricas.DIMENSIONES).index("corresponsal"), key="tablero_dimension",
)
tipo = controles[2].selectbox("Tipo de gráfico", ui.TIPOS_GRAFICO, key="tablero_tipo")
tope = controles[3].number_input("Top", min_value=3, max_value=50, value=10, step=1,
                                 key="tablero_top", help="Los demás se agrupan en «Otros».")

tabla_principal = metricas.agregar(datos, dimension, medida, top=int(tope))
st.plotly_chart(
    ui.grafico(tabla_principal, medida, tipo,
               f"{metricas.MEDIDAS[medida]} por {metricas.DIMENSIONES[dimension]}"),
    key="grafico_principal",
)

# ---------------------------------------------------------------------------
# Evolución y segunda apertura
# ---------------------------------------------------------------------------
izquierda, derecha = st.columns(2)

with izquierda:
    granularidad = st.radio("Evolución por", ["mes", "día"], horizontal=True, key="tablero_granularidad")
    serie = metricas.serie_temporal(datos, medida, por=granularidad)
    st.plotly_chart(
        ui.grafico(serie, medida, "Línea" if granularidad == "día" else "Columnas",
                   f"Evolución de {metricas.MEDIDAS[medida].lower()}"),
        key="grafico_evolucion",
    )

with derecha:
    dimension_2 = st.selectbox(
        "Segunda apertura", list(metricas.DIMENSIONES),
        format_func=lambda d: metricas.DIMENSIONES[d],
        index=list(metricas.DIMENSIONES).index("proceso"), key="tablero_dimension2",
    )
    st.plotly_chart(
        ui.grafico(metricas.agregar(datos, dimension_2, medida, top=8), medida, "Torta",
                   f"{metricas.MEDIDAS[medida]} por {metricas.DIMENSIONES[dimension_2]}"),
        key="grafico_secundario",
    )

# ---------------------------------------------------------------------------
# Comparación Giros AL vs Giros DEL
# ---------------------------------------------------------------------------
if datos["modulo"].nunique() > 1:
    st.subheader("Giros AL frente a Giros DEL")
    dimension_comp = st.selectbox(
        "Comparar por", ["nombre_mes", "corresponsal", "proceso", "moneda", "tipo_mensaje", "area_750"],
        format_func=lambda d: metricas.DIMENSIONES[d], key="tablero_comparacion",
    )
    categorias, series = [], {}
    for modulo in config.MODULOS:
        parcial = datos[datos["modulo"] == modulo]
        tabla = metricas.agregar(parcial, dimension_comp, medida, top=10)
        for etiqueta in tabla["etiqueta"]:
            if etiqueta not in categorias:
                categorias.append(etiqueta)
        series[config.etiqueta_modulo(modulo)] = tabla
    valores = {
        nombre: [float(t.loc[t["etiqueta"] == c, "valor"].sum()) for c in categorias]
        for nombre, t in series.items()
    }
    st.plotly_chart(
        ui.grafico_comparado(categorias, valores,
                             f"{metricas.MEDIDAS[medida]} por {metricas.DIMENSIONES[dimension_comp]}"),
        key="grafico_comparado",
    )

st.divider()

# ---------------------------------------------------------------------------
# Matriz cruzada
# ---------------------------------------------------------------------------
st.subheader("Matriz cruzada")
matriz_controles = st.columns(3)
dim_filas = matriz_controles[0].selectbox(
    "Filas", list(metricas.DIMENSIONES), format_func=lambda d: metricas.DIMENSIONES[d],
    index=list(metricas.DIMENSIONES).index("corresponsal"), key="tablero_matriz_filas",
)
dim_columnas = matriz_controles[1].selectbox(
    "Columnas", list(metricas.DIMENSIONES), format_func=lambda d: metricas.DIMENSIONES[d],
    index=list(metricas.DIMENSIONES).index("nombre_mes"), key="tablero_matriz_columnas",
)
medida_matriz = matriz_controles[2].selectbox(
    "Métrica de la matriz", list(metricas.MEDIDAS), format_func=lambda m: metricas.MEDIDAS[m],
    key="tablero_matriz_medida",
)

matriz = metricas.tabla_cruzada(datos, dim_filas, dim_columnas, medida_matriz)
if matriz.empty:
    st.caption("Sin datos para esa combinación.")
else:
    formato = "{:,.0f}" if medida_matriz == "operaciones" else "{:,.2f}"
    st.dataframe(matriz.style.format(formato), height=420)

# ---------------------------------------------------------------------------
# Ranking y detalle de respaldo
# ---------------------------------------------------------------------------
st.subheader(f"Ranking por {metricas.DIMENSIONES[dimension].lower()}")
ranking = metricas.ranking(datos, dimension, top=int(tope))
st.dataframe(ranking.style.format({
    "Operaciones": "{:,.0f}", "Monto USD": "{:,.2f}",
    "Promedio USD": "{:,.2f}", "Participación %": "{:,.1f}",
}))

with st.expander(f"Detalle que respalda las cifras ({len(datos)} operaciones)", expanded=False):
    detalle = metricas.detalle(datos)
    st.dataframe(detalle, hide_index=True, height=430)

st.divider()
descargas = st.columns(2)
with descargas[0]:
    ui.boton_descarga_excel(
        {"Resumen": tabla_principal.rename(columns={"etiqueta": metricas.DIMENSIONES[dimension],
                                                    "valor": metricas.MEDIDAS[medida]}),
         "Ranking": ranking,
         "Matriz": matriz,
         "Detalle": metricas.detalle(datos)},
        nombre="tablero_giros.xlsx",
        etiqueta="Descargar tablero en Excel",
        clave="descarga_tablero",
    )
with descargas[1]:
    st.download_button(
        "Descargar detalle en CSV",
        data=metricas.detalle(datos).to_csv(index=False).encode("utf-8-sig"),
        file_name="detalle_giros.csv", mime="text/csv", width="stretch",
    )

ui.pie_de_pagina()
