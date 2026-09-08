"""Histórico de cargas: consultar, comparar, descargar y restaurar versiones."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import streamlit as st

from nucleo import config, metricas, repositorio, ui

ui.configurar_pagina("Histórico", "🗂️")
ui.encabezado(
    "Histórico de cargas",
    "Todas las versiones se conservan con su archivo original, su resultado depurado y sus "
    "validaciones. Cualquiera puede consultarse y volver a publicarse.",
)

cargas = repositorio.listar_cargas()
if cargas.empty:
    st.info("Todavía no hay cargas registradas.", icon="🗂️")
    st.page_link("pages/1_Cargar_archivo.py", label="Ir a Cargar archivo", icon="📥")
    st.stop()

# ---------------------------------------------------------------------------
# Filtros del histórico
# ---------------------------------------------------------------------------
filtro = st.columns(3)
modulos = filtro[0].multiselect("Módulo", list(config.MODULOS),
                                format_func=config.etiqueta_modulo, placeholder="Todos")
periodos = filtro[1].multiselect("Período", sorted(cargas["periodo"].unique(), reverse=True),
                                 format_func=config.etiqueta_periodo, placeholder="Todos")
estados = filtro[2].multiselect("Estado", sorted(cargas["estado"].unique()), placeholder="Todos")

vista = cargas.copy()
if modulos:
    vista = vista[vista["modulo"].isin(modulos)]
if periodos:
    vista = vista[vista["periodo"].isin(periodos)]
if estados:
    vista = vista[vista["estado"].isin(estados)]

tabla = pd.DataFrame({
    "": vista["activa"].map(lambda a: "✅ Publicada" if a else ""),
    "Módulo": vista["modulo"].map(config.etiqueta_modulo),
    "Período": vista["periodo"].map(config.etiqueta_periodo),
    "Versión": vista["version"].map(lambda v: f"v{v}"),
    "Estado": [f"{ui.color_estado(e)} {e}" for e in vista["estado"]],
    "Operaciones": vista["operaciones"],
    "Monto USD": vista["monto_usd"].round(2),
    "Descartes": vista["filas_descartadas"],
    "Errores": vista["errores"],
    "Advertencias": vista["advertencias"],
    "Validador": vista["usuario"],
    "Fecha de carga": pd.to_datetime(vista["fecha_carga"]).dt.strftime("%d/%m/%Y %H:%M"),
    "Archivo": vista["nombre_original"],
    "Comentario": vista["comentario"].fillna(""),
})
st.dataframe(
    tabla, hide_index=True, height=min(420, 60 + 35 * len(tabla)),
    column_config=ui.columnas_numericas("Operaciones", "Monto USD", "Descartes",
                                        "Errores", "Advertencias"),
)
st.caption(f"{len(vista)} versiones en el histórico.")

st.divider()

# ---------------------------------------------------------------------------
# Detalle de una versión
# ---------------------------------------------------------------------------
st.subheader("Detalle de una versión")


def _rotulo(carga_id: int) -> str:
    fila = cargas[cargas["id"] == carga_id].iloc[0]
    marca = "✅ " if fila["activa"] else ""
    return (f"{marca}{config.etiqueta_modulo(fila['modulo'])} · "
            f"{config.etiqueta_periodo(fila['periodo'])} · v{fila['version']} · "
            f"{fila['estado']} · {fila['usuario']}")


ids = vista["id"].tolist() or cargas["id"].tolist()
indice_inicial = 0
if "ultima_carga" in st.session_state and st.session_state["ultima_carga"] in ids:
    indice_inicial = ids.index(st.session_state["ultima_carga"])
carga_id = st.selectbox("Versión", ids, index=indice_inicial, format_func=_rotulo)

carga = repositorio.obtener_carga(int(carga_id))
if carga is None:
    st.error("No se encontró la versión seleccionada.")
    st.stop()

tarjetas = st.columns(5)
tarjetas[0].metric("Estado", f"{ui.color_estado(carga['estado'])} {ui.estado_corto(carga['estado'])}",
                   help=carga["estado"])
tarjetas[1].metric("Operaciones", carga["operaciones"])
tarjetas[2].metric("Monto USD", metricas.formatear_usd_compacto(carga["monto_usd"]),
                   help=metricas.formatear_usd(carga["monto_usd"]))
tarjetas[3].metric("Filas descartadas", carga["filas_descartadas"])
tarjetas[4].metric("Publicada", "Sí" if carga["activa"] else "No")

st.caption(
    f"Archivo original: **{carga['nombre_original']}** · hoja: {carga['hoja'] or '—'} · "
    f"encabezado en la fila {carga['fila_encabezado'] or '—'} · "
    f"cargada por **{carga['usuario']}** el {carga['fecha_carga'].replace('T', ' ')}"
)
if carga["comentario"] and str(carga["comentario"]).strip():
    st.caption(f"Comentario: _{carga['comentario']}_")

pestañas = st.tabs(["Validaciones", "Archivos", "Restaurar", "Comparar versiones"])

# --- Validaciones ---
with pestañas[0]:
    validaciones = repositorio.validaciones_de(int(carga_id))
    if validaciones.empty:
        st.caption("Sin validaciones registradas.")
    else:
        for _, fila in validaciones.iterrows():
            icono = ui.SEVERIDAD_ICONO.get(fila["severidad"], "•")
            linea = f"{icono} **{fila['regla']}** — {fila['mensaje']}"
            if fila["severidad"] == config.SEVERIDAD_ERROR:
                st.error(linea, icon="🔴")
            elif fila["severidad"] == config.SEVERIDAD_ADVERTENCIA:
                st.warning(linea, icon="🟠")
            else:
                st.caption(linea)
            detalle_tecnico = fila["detalle"]
            if pd.notna(detalle_tecnico) and str(detalle_tecnico).strip():
                with st.expander("Ver detalle técnico"):
                    st.json(json.loads(detalle_tecnico))

# --- Archivos ---
with pestañas[1]:
    for etiqueta, columna, mime in (
        ("Archivo original", "ruta_original", "application/octet-stream"),
        ("Resultado depurado (CSV)", "ruta_depurado", "text/csv"),
        ("Filas descartadas (CSV)", "ruta_descartes", "text/csv"),
    ):
        ruta = Path(carga[columna]) if carga[columna] else None
        if ruta and ruta.exists():
            st.download_button(
                f"Descargar {etiqueta.lower()}", data=ruta.read_bytes(),
                file_name=ruta.name, mime=mime, key=f"descarga_{columna}_{carga_id}",
            )
        else:
            st.caption(f"{etiqueta}: no disponible.")

    depurado = Path(carga["ruta_depurado"]) if carga["ruta_depurado"] else None
    if depurado and depurado.exists():
        with st.expander("Vista previa del depurado"):
            st.dataframe(pd.read_csv(depurado).head(200), hide_index=True)

# --- Restaurar ---
with pestañas[2]:
    if carga["activa"]:
        st.success("Esta versión es la que está publicada en el tablero.", icon="✅")
    elif carga["estado"] == config.ESTADO_RECHAZADA:
        st.error(
            "Esta versión fue rechazada por validación y no puede publicarse. "
            "Se conserva únicamente como evidencia del intento de carga.", icon="🔴",
        )
    else:
        publicada = repositorio.carga_activa(carga["modulo"], carga["periodo"])
        if publicada:
            st.warning(
                f"Al restaurar la v{carga['version']}, la v{publicada['version']} dejará de estar "
                "publicada (pero seguirá en el histórico).", icon="⚠️",
            )
        motivo = st.text_input("Motivo de la restauración",
                               placeholder="Ej.: la versión publicada tenía el archivo equivocado")
        if st.button(f"Restaurar y publicar la v{carga['version']}", type="primary"):
            repositorio.activar_version(
                carga["modulo"], carga["periodo"], int(carga["version"]), motivo=motivo,
            )
            ui.refrescar()
            st.success(f"Se publicó la v{carga['version']}.")
            st.rerun()

# --- Comparar ---
with pestañas[3]:
    hermanas = cargas[(cargas["modulo"] == carga["modulo"]) & (cargas["periodo"] == carga["periodo"])]
    versiones = sorted(hermanas["version"].tolist())
    if len(versiones) < 2:
        st.caption("Se necesita más de una versión del mismo módulo y período para comparar.")
    else:
        columnas = st.columns(2)
        version_a = columnas[0].selectbox("Versión A", versiones, index=0, key="cmp_a")
        version_b = columnas[1].selectbox("Versión B", versiones, index=len(versiones) - 1, key="cmp_b")
        if version_a != version_b:
            comparacion = repositorio.comparar_versiones(
                carga["modulo"], carga["periodo"], int(version_a), int(version_b)
            )
            # Las celdas mezclan textos y números: se muestran como texto formateado.
            st.dataframe(
                comparacion.map(lambda v: f"{v:,.2f}" if isinstance(v, float) else str(v)),
                hide_index=True,
            )
        else:
            st.caption("Elija dos versiones distintas.")

st.divider()

# ---------------------------------------------------------------------------
# Bitácora
# ---------------------------------------------------------------------------
st.subheader("Bitácora de auditoría")
bitacora = repositorio.bitacora(limite=300)
if bitacora.empty:
    st.caption("Sin movimientos registrados.")
else:
    st.dataframe(
        pd.DataFrame({
            "Fecha": pd.to_datetime(bitacora["fecha"]).dt.strftime("%d/%m/%Y %H:%M"),
            "Usuario": bitacora["usuario"],
            "Acción": bitacora["accion"],
            "Módulo": bitacora["modulo"].map(lambda m: config.etiqueta_modulo(m) if m else ""),
            "Período": bitacora["periodo"].fillna(""),
            "Versión": bitacora["version"].fillna(0).astype(int).map(lambda v: f"v{v}" if v else ""),
            "Detalle": bitacora["detalle"].fillna(""),
        }),
        hide_index=True, height=360,
    )
    ui.boton_descarga_excel({"Histórico": tabla, "Bitácora": bitacora},
                            nombre="historico_cargas.xlsx",
                            etiqueta="Descargar histórico y bitácora en Excel",
                            clave="descarga_historico")

ui.pie_de_pagina()
