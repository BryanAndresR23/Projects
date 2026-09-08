"""Panel de Giros — pantalla de inicio.

Ejecutar con:  streamlit run Inicio.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import streamlit as st

from nucleo import config, metricas, repositorio, ui

ui.configurar_pagina("Inicio", "🌐")
ui.encabezado(
    "Panel de Giros",
    "Carga validada con histórico de versiones y tablero analítico de Giros AL y Giros DEL.",
)

cargas = repositorio.listar_cargas()
datos = ui.datos_publicados()

with st.sidebar:
    st.markdown(f"**Usuario:** {config.usuario_actual()}")
    st.caption(f"Datos en: `{config.dir_datos()}`")
    if st.button("Actualizar datos", width="stretch"):
        ui.refrescar()
        st.rerun()

if cargas.empty:
    st.info(
        "Todavía no hay cargas registradas. Empiece en **Cargar archivo**: elija el módulo "
        "(Giros AL o Giros DEL), el período y suba el reporte.",
        icon="📥",
    )
    st.page_link("pages/1_Cargar_archivo.py", label="Ir a Cargar archivo", icon="📥")
    ui.pie_de_pagina()
    st.stop()

# ---------------------------------------------------------------------------
# Situación general
# ---------------------------------------------------------------------------
st.subheader("Situación general de lo publicado")
ui.tarjetas(datos)

columnas = st.columns(3)
columnas[0].metric("Cargas en el histórico", int(len(cargas)))
columnas[1].metric("Períodos con datos publicados", int(cargas[cargas["activa"] == 1]["periodo"].nunique()))
columnas[2].metric("Cargas rechazadas", int((cargas["estado"] == config.ESTADO_RECHAZADA).sum()))

st.divider()

# ---------------------------------------------------------------------------
# Qué versión está publicada en cada módulo y período
# ---------------------------------------------------------------------------
st.subheader("Versión publicada por módulo y período")
st.caption("Una sola versión queda activa por módulo y período; las demás se conservan en el histórico.")

activas = cargas[cargas["activa"] == 1]
if activas.empty:
    st.warning("Hay cargas registradas, pero ninguna publicada todavía.", icon="⚠️")
else:
    resumen = pd.DataFrame({
        "Módulo": activas["modulo"].map(config.etiqueta_modulo),
        "Período": activas["periodo"].map(config.etiqueta_periodo),
        "Versión": activas["version"].map(lambda v: f"v{v}"),
        "Estado": [f"{ui.color_estado(e)} {e}" for e in activas["estado"]],
        "Operaciones": activas["operaciones"],
        "Monto USD": activas["monto_usd"].round(2),
        "Validador": activas["usuario"],
        "Fecha de carga": pd.to_datetime(activas["fecha_carga"]).dt.strftime("%d/%m/%Y %H:%M"),
        "Archivo": activas["nombre_original"],
    }).sort_values(["Período", "Módulo"], ascending=[False, True])
    st.dataframe(resumen, hide_index=True,
                 column_config=ui.columnas_numericas("Operaciones", "Monto USD"))

# Períodos cargados en un módulo pero no en el otro.
faltantes = []
for periodo in sorted(cargas["periodo"].unique(), reverse=True):
    publicados = set(activas[activas["periodo"] == periodo]["modulo"]) if not activas.empty else set()
    for modulo in config.MODULOS:
        if modulo not in publicados:
            faltantes.append(f"{config.etiqueta_modulo(modulo)} · {config.etiqueta_periodo(periodo)}")
if faltantes:
    st.warning("Sin versión publicada: " + " | ".join(faltantes[:8]), icon="⚠️")

st.divider()

# ---------------------------------------------------------------------------
# Vistazo rápido y últimos movimientos
# ---------------------------------------------------------------------------
izquierda, derecha = st.columns([3, 2])

with izquierda:
    st.subheader("Monto USD por período")
    st.plotly_chart(
        ui.grafico(metricas.agregar(datos, "periodo", "monto_usd"), "monto_usd",
                   "Columnas", "Monto publicado por período"),
        key="inicio_periodos",
    )

with derecha:
    st.subheader("Últimos movimientos")
    bitacora = repositorio.bitacora(limite=12)
    if bitacora.empty:
        st.caption("Sin movimientos registrados.")
    else:
        st.dataframe(
            pd.DataFrame({
                "Fecha": pd.to_datetime(bitacora["fecha"]).dt.strftime("%d/%m %H:%M"),
                "Acción": bitacora["accion"],
                "Módulo": bitacora["modulo"].map(lambda m: config.etiqueta_modulo(m) if m else ""),
                "Período": bitacora["periodo"].fillna(""),
                "v": bitacora["version"].fillna(0).astype(int).map(lambda v: f"v{v}" if v else ""),
                "Usuario": bitacora["usuario"],
            }),
            hide_index=True, height=330,
        )

st.divider()
enlaces = st.columns(3)
with enlaces[0]:
    st.page_link("pages/1_Cargar_archivo.py", label="Cargar archivo", icon="📥")
with enlaces[1]:
    st.page_link("pages/2_Tablero.py", label="Tablero analítico", icon="📊")
with enlaces[2]:
    st.page_link("pages/3_Historico.py", label="Histórico de cargas", icon="🗂️")

ui.pie_de_pagina()
