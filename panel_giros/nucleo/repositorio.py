"""Histórico de cargas: versionado, publicación y restauración."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from nucleo import bd, config, etl as etl_mod, validaciones as val_mod

COLUMNAS_OPERACION = [
    "modulo", "periodo", "fecha", "anio", "mes", "nombre_mes", "referencia",
    "corresponsal", "area_750", "deuda_771", "tipo_mensaje", "proceso",
    "moneda", "monto", "tipo_cambio", "monto_usd", "beneficiario", "estado",
]


@dataclass
class ResultadoCarga:
    """Lo que devuelve el registro de una carga, para mostrar al validador."""

    carga_id: int
    modulo: str
    periodo: str
    version: int
    estado: str
    activa: bool
    hallazgos: list[val_mod.Hallazgo] = field(default_factory=list)
    etl: etl_mod.ResultadoETL | None = None
    carpeta: Path | None = None
    motivo_no_activada: str = ""
    duplicado_de: int | None = None

    @property
    def publicable(self) -> bool:
        return self.estado != config.ESTADO_RECHAZADA


# ---------------------------------------------------------------------------
# Alta de cargas
# ---------------------------------------------------------------------------
def registrar_carga(
    origen: Any,
    *,
    modulo: str,
    periodo: str,
    nombre: str | None = None,
    usuario: str | None = None,
    comentario: str = "",
    hoja: Any = None,
    activar: bool = True,
    ruta_bd: Path | None = None,
) -> ResultadoCarga:
    """Procesa un archivo y lo guarda como una nueva versión del histórico.

    La carga se conserva siempre —incluso si es rechazada— junto con el archivo
    original, el depurado y sus validaciones. Solo se publica (queda activa) si
    no tiene errores y ``activar`` es True.
    """
    if modulo not in config.MODULOS:
        raise ValueError(f"Módulo desconocido: {modulo}. Use uno de {list(config.MODULOS)}")
    config.partes_periodo(periodo)  # valida el formato AAAA-MM

    usuario = usuario or config.usuario_actual()
    contenido, nombre_archivo = _leer_origen(origen, nombre)
    huella = hashlib.sha256(contenido).hexdigest()

    resultado_etl = etl_mod.procesar(
        contenido, modulo=modulo, periodo=periodo, nombre=nombre_archivo, hoja=hoja
    )
    hallazgos = val_mod.validar(
        resultado_etl, modulo=modulo, periodo=periodo, nombre_archivo=nombre_archivo
    )
    estado = val_mod.estado_de(hallazgos)
    conteo = val_mod.resumen(hallazgos)

    with bd.sesion(ruta_bd) as con:
        duplicado = _buscar_duplicado(con, modulo, periodo, huella)
        version = _siguiente_version(con, modulo, periodo)
        carpeta = _carpeta_version(modulo, periodo, version)
        carpeta.mkdir(parents=True, exist_ok=True)

        ruta_original = carpeta / f"original_{_nombre_seguro(nombre_archivo)}"
        ruta_original.write_bytes(contenido)
        ruta_depurado = carpeta / "depurado.csv"
        ruta_descartes = carpeta / "descartes.csv"
        _guardar_csv(resultado_etl.datos, ruta_depurado)
        _guardar_csv(resultado_etl.descartes, ruta_descartes)
        _guardar_validaciones(carpeta / "validaciones.json", hallazgos, resultado_etl)

        cursor = con.execute(
            """
            INSERT INTO cargas (
                modulo, periodo, version, activa, estado, usuario, fecha_carga,
                nombre_original, hash_original, ruta_original, ruta_depurado,
                ruta_descartes, hoja, fila_encabezado, filas_origen, filas_depuradas,
                filas_descartadas, operaciones, monto_usd, errores, advertencias, comentario
            ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                modulo, periodo, version, estado, usuario,
                datetime.now().isoformat(timespec="seconds"),
                nombre_archivo, huella, str(ruta_original), str(ruta_depurado),
                str(ruta_descartes), resultado_etl.hoja, resultado_etl.fila_encabezado,
                resultado_etl.filas_origen, resultado_etl.filas_depuradas,
                resultado_etl.filas_descartadas, resultado_etl.filas_depuradas,
                resultado_etl.monto_usd, conteo["errores"], conteo["advertencias"],
                comentario,
            ),
        )
        carga_id = int(cursor.lastrowid)

        con.executemany(
            """
            INSERT INTO validaciones (carga_id, regla, severidad, mensaje, filas_afectadas, detalle)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [(carga_id, h.regla, h.severidad, h.mensaje, h.filas_afectadas, h.detalle_json())
             for h in hallazgos],
        )
        _insertar_operaciones(con, carga_id, modulo, periodo, resultado_etl.datos)

        activada = False
        motivo = ""
        if estado == config.ESTADO_RECHAZADA:
            motivo = "La carga tiene errores bloqueantes: se conserva en el histórico pero no se publica."
        elif not activar:
            motivo = "El validador pidió guardarla sin publicar."
        else:
            _activar(con, carga_id, modulo, periodo)
            activada = True

        bd.registrar_bitacora(
            con, usuario=usuario, accion="CARGA", modulo=modulo, periodo=periodo,
            version=version, carga_id=carga_id,
            detalle=json.dumps({
                "archivo": nombre_archivo, "estado": estado, "activada": activada,
                "operaciones": resultado_etl.filas_depuradas,
                "monto_usd": round(resultado_etl.monto_usd, 2),
                "duplicado_de": duplicado,
            }, ensure_ascii=False),
        )

    return ResultadoCarga(
        carga_id=carga_id, modulo=modulo, periodo=periodo, version=version,
        estado=estado, activa=activada, hallazgos=hallazgos, etl=resultado_etl,
        carpeta=carpeta, motivo_no_activada=motivo, duplicado_de=duplicado,
    )


def activar_version(
    modulo: str,
    periodo: str,
    version: int,
    *,
    usuario: str | None = None,
    motivo: str = "",
    ruta_bd: Path | None = None,
) -> dict:
    """Publica (o restaura) una versión concreta del histórico."""
    usuario = usuario or config.usuario_actual()
    with bd.sesion(ruta_bd) as con:
        fila = con.execute(
            "SELECT * FROM cargas WHERE modulo = ? AND periodo = ? AND version = ?",
            (modulo, periodo, version),
        ).fetchone()
        if fila is None:
            raise LookupError(
                f"No existe la versión {version} de {config.etiqueta_modulo(modulo)} "
                f"{config.etiqueta_periodo(periodo)}."
            )
        if fila["estado"] == config.ESTADO_RECHAZADA:
            raise ValueError(
                "Esa versión fue rechazada por validación y no puede publicarse. "
                "Corrija el archivo y cargue una nueva versión."
            )

        anterior = con.execute(
            "SELECT version FROM cargas WHERE modulo = ? AND periodo = ? AND activa = 1",
            (modulo, periodo),
        ).fetchone()
        _activar(con, int(fila["id"]), modulo, periodo)

        accion = "RESTAURACION" if anterior and anterior["version"] != version else "PUBLICACION"
        bd.registrar_bitacora(
            con, usuario=usuario, accion=accion, modulo=modulo, periodo=periodo,
            version=version, carga_id=int(fila["id"]),
            detalle=json.dumps(
                {"version_anterior": anterior["version"] if anterior else None, "motivo": motivo},
                ensure_ascii=False,
            ),
        )
        return {
            "carga_id": int(fila["id"]), "modulo": modulo, "periodo": periodo,
            "version": version, "version_anterior": anterior["version"] if anterior else None,
            "accion": accion,
        }


def _activar(con: sqlite3.Connection, carga_id: int, modulo: str, periodo: str) -> None:
    """Deja una sola versión activa por módulo y período."""
    con.execute("UPDATE cargas SET activa = 0 WHERE modulo = ? AND periodo = ?", (modulo, periodo))
    con.execute("UPDATE cargas SET activa = 1 WHERE id = ?", (carga_id,))


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
def listar_cargas(
    *,
    modulo: str | None = None,
    periodo: str | None = None,
    solo_activas: bool = False,
    ruta_bd: Path | None = None,
) -> pd.DataFrame:
    condiciones, parametros = [], []
    if modulo:
        condiciones.append("modulo = ?")
        parametros.append(modulo)
    if periodo:
        condiciones.append("periodo = ?")
        parametros.append(periodo)
    if solo_activas:
        condiciones.append("activa = 1")
    donde = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    consulta = f"SELECT * FROM cargas {donde} ORDER BY modulo, periodo DESC, version DESC"
    with bd.sesion(ruta_bd) as con:
        return pd.DataFrame([dict(f) for f in con.execute(consulta, parametros)])


def obtener_carga(carga_id: int, *, ruta_bd: Path | None = None) -> dict | None:
    with bd.sesion(ruta_bd) as con:
        fila = con.execute("SELECT * FROM cargas WHERE id = ?", (carga_id,)).fetchone()
        return dict(fila) if fila else None


def carga_activa(modulo: str, periodo: str, *, ruta_bd: Path | None = None) -> dict | None:
    with bd.sesion(ruta_bd) as con:
        fila = con.execute(
            "SELECT * FROM cargas WHERE modulo = ? AND periodo = ? AND activa = 1",
            (modulo, periodo),
        ).fetchone()
        return dict(fila) if fila else None


def validaciones_de(carga_id: int, *, ruta_bd: Path | None = None) -> pd.DataFrame:
    with bd.sesion(ruta_bd) as con:
        filas = con.execute(
            "SELECT regla, severidad, mensaje, filas_afectadas, detalle "
            "FROM validaciones WHERE carga_id = ? ORDER BY "
            "CASE severidad WHEN 'ERROR' THEN 0 WHEN 'ADVERTENCIA' THEN 1 ELSE 2 END, regla",
            (carga_id,),
        ).fetchall()
    return pd.DataFrame([dict(f) for f in filas])


def operaciones(
    *,
    solo_activas: bool = True,
    cargas: list[int] | None = None,
    ruta_bd: Path | None = None,
) -> pd.DataFrame:
    """Hechos para el tablero: por defecto, solo los de las versiones publicadas."""
    consulta = "SELECT o.* FROM operaciones o JOIN cargas c ON c.id = o.carga_id"
    parametros: list[Any] = []
    condiciones = []
    if solo_activas:
        condiciones.append("c.activa = 1")
    if cargas:
        condiciones.append(f"o.carga_id IN ({','.join('?' * len(cargas))})")
        parametros.extend(cargas)
    if condiciones:
        consulta += f" WHERE {' AND '.join(condiciones)}"
    with bd.sesion(ruta_bd) as con:
        datos = pd.DataFrame([dict(f) for f in con.execute(consulta, parametros)])
    if datos.empty:
        return pd.DataFrame(columns=["id", "carga_id"] + COLUMNAS_OPERACION)
    datos["fecha"] = pd.to_datetime(datos["fecha"], errors="coerce")
    return datos


def bitacora(*, limite: int = 200, ruta_bd: Path | None = None) -> pd.DataFrame:
    with bd.sesion(ruta_bd) as con:
        filas = con.execute(
            "SELECT * FROM bitacora ORDER BY id DESC LIMIT ?", (limite,)
        ).fetchall()
    return pd.DataFrame([dict(f) for f in filas])


def periodos_disponibles(*, ruta_bd: Path | None = None) -> list[str]:
    with bd.sesion(ruta_bd) as con:
        return [f[0] for f in con.execute(
            "SELECT DISTINCT periodo FROM cargas ORDER BY periodo DESC"
        )]


def comparar_versiones(
    modulo: str, periodo: str, version_a: int, version_b: int, *, ruta_bd: Path | None = None
) -> pd.DataFrame:
    """Compara dos versiones del mismo módulo y período, indicador por indicador."""
    with bd.sesion(ruta_bd) as con:
        filas = {
            v: con.execute(
                "SELECT * FROM cargas WHERE modulo = ? AND periodo = ? AND version = ?",
                (modulo, periodo, v),
            ).fetchone()
            for v in (version_a, version_b)
        }
    faltan = [v for v, f in filas.items() if f is None]
    if faltan:
        raise LookupError(f"No existe(n) la(s) versión(es): {faltan}")

    a, b = filas[version_a], filas[version_b]
    indicadores = [
        ("Estado", a["estado"], b["estado"]),
        ("Archivo", a["nombre_original"], b["nombre_original"]),
        ("Usuario", a["usuario"], b["usuario"]),
        ("Fecha de carga", a["fecha_carga"], b["fecha_carga"]),
        ("Filas del archivo", a["filas_origen"], b["filas_origen"]),
        ("Operaciones depuradas", a["operaciones"], b["operaciones"]),
        ("Filas descartadas", a["filas_descartadas"], b["filas_descartadas"]),
        ("Monto USD", round(a["monto_usd"], 2), round(b["monto_usd"], 2)),
        ("Errores", a["errores"], b["errores"]),
        ("Advertencias", a["advertencias"], b["advertencias"]),
    ]
    tabla = pd.DataFrame(indicadores, columns=["Indicador", f"v{version_a}", f"v{version_b}"])
    tabla["Diferencia"] = [
        round(y - x, 2) if isinstance(x, (int, float)) and isinstance(y, (int, float))
        else ("=" if x == y else "≠")
        for x, y in zip(tabla[f"v{version_a}"], tabla[f"v{version_b}"])
    ]
    return tabla


# ---------------------------------------------------------------------------
# Apoyo
# ---------------------------------------------------------------------------
def _leer_origen(origen: Any, nombre: str | None) -> tuple[bytes, str]:
    if isinstance(origen, (str, Path)):
        ruta = Path(origen)
        return ruta.read_bytes(), nombre or ruta.name
    if isinstance(origen, bytes):
        return origen, nombre or "archivo.xlsx"
    if hasattr(origen, "read"):
        if hasattr(origen, "seek"):
            origen.seek(0)
        contenido = origen.read()
        return contenido, nombre or getattr(origen, "name", "archivo.xlsx")
    raise TypeError("El origen debe ser una ruta, bytes o un archivo abierto.")


def _nombre_seguro(nombre: str) -> str:
    limpio = "".join(c if c.isalnum() or c in "._- " else "_" for c in nombre).strip()
    return limpio or "archivo.xlsx"


def _carpeta_version(modulo: str, periodo: str, version: int) -> Path:
    return config.dir_cargas() / modulo / periodo / f"v{version:03d}"


def _siguiente_version(con: sqlite3.Connection, modulo: str, periodo: str) -> int:
    fila = con.execute(
        "SELECT COALESCE(MAX(version), 0) AS ultima FROM cargas WHERE modulo = ? AND periodo = ?",
        (modulo, periodo),
    ).fetchone()
    return int(fila["ultima"]) + 1


def _buscar_duplicado(con: sqlite3.Connection, modulo: str, periodo: str, huella: str) -> int | None:
    fila = con.execute(
        "SELECT id FROM cargas WHERE modulo = ? AND periodo = ? AND hash_original = ? "
        "ORDER BY version DESC LIMIT 1",
        (modulo, periodo, huella),
    ).fetchone()
    return int(fila["id"]) if fila else None


def _guardar_csv(datos: pd.DataFrame, ruta: Path) -> None:
    # utf-8-sig para que Excel respete las tildes al abrir el archivo.
    datos.to_csv(ruta, index=False, encoding="utf-8-sig")


def _guardar_validaciones(
    ruta: Path, hallazgos: list[val_mod.Hallazgo], resultado: etl_mod.ResultadoETL
) -> None:
    contenido = {
        "generado": datetime.now().isoformat(timespec="seconds"),
        "hoja": resultado.hoja,
        "fila_encabezado": resultado.fila_encabezado,
        "columnas_detectadas": resultado.columnas_detectadas,
        "columnas_ignoradas": resultado.columnas_ignoradas,
        "filas_origen": resultado.filas_origen,
        "filas_depuradas": resultado.filas_depuradas,
        "filas_descartadas": resultado.filas_descartadas,
        "hallazgos": [
            {"regla": h.regla, "severidad": h.severidad, "mensaje": h.mensaje,
             "filas_afectadas": h.filas_afectadas, "detalle": h.detalle}
            for h in hallazgos
        ],
    }
    ruta.write_text(json.dumps(contenido, ensure_ascii=False, indent=2), encoding="utf-8")


def _insertar_operaciones(
    con: sqlite3.Connection, carga_id: int, modulo: str, periodo: str, datos: pd.DataFrame
) -> None:
    if datos.empty:
        return
    registros = []
    for fila in datos.to_dict("records"):
        registros.append((
            carga_id, modulo, periodo,
            fila["fecha"].isoformat() if fila.get("fecha") else None,
            fila.get("anio"), fila.get("mes"), fila.get("nombre_mes"),
            fila.get("referencia"), fila.get("corresponsal"), fila.get("area_750"),
            fila.get("deuda_771"), fila.get("tipo_mensaje"), fila.get("proceso"),
            fila.get("moneda"), fila.get("monto"), fila.get("tipo_cambio"),
            fila.get("monto_usd"), fila.get("beneficiario"), fila.get("estado"),
        ))
    con.executemany(
        f"INSERT INTO operaciones (carga_id, {', '.join(COLUMNAS_OPERACION)}) "
        f"VALUES ({', '.join('?' * (len(COLUMNAS_OPERACION) + 1))})",
        registros,
    )
