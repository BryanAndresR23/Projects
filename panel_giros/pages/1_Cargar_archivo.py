"""Carga de archivos: el validador elige el módulo y el ETL comprueba que coincida."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import streamlit as st

from nucleo import config, etl, metricas, repositorio, ui

ui.configurar_pagina("Cargar archivo", "📥")
ui.encabezado(
    "Cargar archivo",
    "Cada carga queda registrada con su archivo original, el resultado depurado, "
    "las validaciones, el usuario, la fecha y su número de versión.",
)

with st.sidebar:
    st.markdown(f"**Validador:** {config.usuario_actual()}")
    st.caption("El nombre queda registrado en el histórico y en la bitácora.")

# ---------------------------------------------------------------------------
# 1. Qué se va a cargar
# ---------------------------------------------------------------------------
st.subheader("1. Identifique la carga")

izquierda, derecha = st.columns(2)
with izquierda:
    modulo = st.radio(
        "Módulo",
        list(config.MODULOS),
        format_func=config.etiqueta_modulo,
        horizontal=True,
        help="Selección obligatoria. El ETL verifica que el contenido del archivo corresponda "
             "al módulo elegido y rechaza la carga si no coincide.",
    )
with derecha:
    periodo = ui.selector_periodo("carga")

st.caption(f"Se cargará **{config.etiqueta_modulo(modulo)}** del período "
           f"**{config.etiqueta_periodo(periodo)}**.")

activa = repositorio.carga_activa(modulo, periodo)
if activa:
    st.info(
        f"Ya hay una versión publicada para este módulo y período: **v{activa['version']}** "
        f"({activa['operaciones']} operaciones, {activa['nombre_original']}, "
        f"cargada por {activa['usuario']}). Si continúa, se creará la **v{activa['version'] + 1}** "
        "y la anterior quedará en el histórico, disponible para restaurarla.",
        icon="ℹ️",
    )

# ---------------------------------------------------------------------------
# 2. Archivo
# ---------------------------------------------------------------------------
st.subheader("2. Seleccione el archivo")
archivo = st.file_uploader(
    "Reporte de giros (Excel o CSV)",
    type=["xlsx", "xlsm", "xls", "csv", "txt"],
    help="El sistema busca solo la fila de encabezados: no importa si el reporte trae títulos arriba.",
)

hoja = None
if archivo is not None and Path(archivo.name).suffix.lower() != ".csv":
    try:
        hojas = etl.hojas_disponibles(archivo.getvalue(), nombre=archivo.name)
        if len(hojas) > 1:
            hoja = st.selectbox("Hoja del libro", hojas)
    except Exception as error:
        st.error(f"No se pudo leer el archivo: {error}")

comentario = st.text_input(
    "Comentario de la carga (opcional)",
    placeholder="Ej.: recarga por corrección de corresponsales en 12 operaciones",
)
publicar = st.checkbox(
    "Publicar en el tablero si supera la validación", value=True,
    help="Si la desmarca, la carga se guarda en el histórico pero el tablero sigue mostrando "
         "la versión publicada actual.",
)

procesar = st.button("Procesar y validar", type="primary", disabled=archivo is None,
                     width="stretch")

# ---------------------------------------------------------------------------
# 3. Resultado
# ---------------------------------------------------------------------------
if procesar and archivo is not None:
    with st.spinner("Depurando y validando…"):
        try:
            resultado = repositorio.registrar_carga(
                archivo.getvalue(), modulo=modulo, periodo=periodo,
                nombre=archivo.name, comentario=comentario, hoja=hoja, activar=publicar,
            )
        except Exception as error:  # el archivo puede estar dañado o protegido
            st.error(f"No se pudo procesar el archivo: {error}")
            st.stop()
    ui.refrescar()
    st.session_state["ultima_carga"] = resultado.carga_id

    st.divider()
    st.subheader("3. Resultado de la validación")

    if resultado.estado == config.ESTADO_RECHAZADA:
        st.error(
            f"**Carga rechazada** — guardada como v{resultado.version} en el histórico, "
            "pero no publicada. Corrija el archivo y vuelva a cargarlo.", icon="🔴",
        )
    elif resultado.activa:
        st.success(
            f"**Carga publicada como v{resultado.version}** de "
            f"{config.etiqueta_modulo(modulo)} · {config.etiqueta_periodo(periodo)}. "
            "El tablero ya muestra estos datos.", icon="🟢",
        )
    else:
        st.warning(
            f"Carga guardada como v{resultado.version} sin publicar. {resultado.motivo_no_activada}",
            icon="🟠",
        )

    if resultado.duplicado_de:
        anterior = repositorio.obtener_carga(resultado.duplicado_de)
        if anterior:
            st.info(
                f"El archivo es idéntico al de la v{anterior['version']} "
                f"(cargada el {anterior['fecha_carga'][:10]}). Verifique que no sea una doble carga.",
                icon="♻️",
            )

    tarjetas = st.columns(5)
    tarjetas[0].metric("Filas del archivo", resultado.etl.filas_origen)
    tarjetas[1].metric("Operaciones depuradas", resultado.etl.filas_depuradas)
    tarjetas[2].metric("Filas descartadas", resultado.etl.filas_descartadas)
    tarjetas[3].metric("Monto USD", metricas.formatear_usd_compacto(resultado.etl.monto_usd),
                       help=metricas.formatear_usd(resultado.etl.monto_usd))
    tarjetas[4].metric("Versión", f"v{resultado.version}")

    st.markdown("#### Validaciones aplicadas")
    for hallazgo in resultado.hallazgos:
        icono = ui.SEVERIDAD_ICONO.get(hallazgo.severidad, "•")
        linea = f"{icono} **{hallazgo.regla}** — {hallazgo.mensaje}"
        if hallazgo.severidad == config.SEVERIDAD_ERROR:
            st.error(linea, icon="🔴")
        elif hallazgo.severidad == config.SEVERIDAD_ADVERTENCIA:
            st.warning(linea, icon="🟠")
        else:
            st.caption(linea)

    with st.expander("Columnas reconocidas del archivo"):
        detectadas = resultado.etl.columnas_detectadas
        if detectadas:
            st.dataframe(
                pd.DataFrame(
                    {"Columna del archivo": list(detectadas), "Campo del sistema": list(detectadas.values())}
                ),
                hide_index=True,
            )
        if resultado.etl.columnas_ignoradas:
            st.caption("No utilizadas: " + ", ".join(resultado.etl.columnas_ignoradas[:30]))
        st.caption("¿Falta alguna? Agregue el nombre real en `catalogos/columnas.yaml`.")

    if not resultado.etl.descartes.empty:
        with st.expander(f"Filas descartadas ({len(resultado.etl.descartes)})"):
            st.dataframe(resultado.etl.descartes, hide_index=True)

    with st.expander(f"Vista previa del resultado depurado ({resultado.etl.filas_depuradas} filas)"):
        st.dataframe(resultado.etl.datos.head(200), hide_index=True)

    st.caption(f"Archivos de esta versión guardados en: `{resultado.carpeta}`")

    if resultado.publicable and not resultado.activa:
        if st.button(f"Publicar la v{resultado.version} ahora", type="primary"):
            repositorio.activar_version(modulo, periodo, resultado.version,
                                        motivo="Publicación manual tras revisar validaciones")
            ui.refrescar()
            st.rerun()

ui.pie_de_pagina()
