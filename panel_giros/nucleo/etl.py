"""ETL: lectura del archivo cargado, mapeo de columnas y depuración."""
from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from nucleo import catalogo, config

# Columnas del modelo depurado que alimenta el tablero.
CAMPOS_SALIDA = [
    "fecha", "anio", "mes", "nombre_mes", "referencia", "corresponsal",
    "area_750", "deuda_771", "tipo_mensaje", "proceso", "moneda", "monto",
    "tipo_cambio", "monto_usd", "beneficiario", "estado", "sentido",
]

MOTIVO_VACIA = "Fila vacía"
MOTIVO_TOTALES = "Fila de totales o subtotales"
MOTIVO_FECHA = "Fecha ausente o no reconocida"
MOTIVO_MONTO = "Monto ausente o no numérico"
MOTIVO_SIN_TC = "Sin tipo de cambio para convertir a USD"


@dataclass
class ResultadoETL:
    """Todo lo que el ETL produce a partir de un archivo."""

    datos: pd.DataFrame                      # filas depuradas, listas para el tablero
    descartes: pd.DataFrame                  # filas excluidas, con su motivo
    columnas_detectadas: dict[str, str] = field(default_factory=dict)
    columnas_ignoradas: list[str] = field(default_factory=list)
    columnas_faltantes: list[str] = field(default_factory=list)
    filas_origen: int = 0
    fila_encabezado: int | None = None
    hoja: str | None = None

    @property
    def filas_depuradas(self) -> int:
        return len(self.datos)

    @property
    def filas_descartadas(self) -> int:
        return len(self.descartes)

    @property
    def monto_usd(self) -> float:
        if self.datos.empty:
            return 0.0
        return float(self.datos["monto_usd"].fillna(0).sum())


# ---------------------------------------------------------------------------
# Conversores
# ---------------------------------------------------------------------------
def a_texto(valor: Any) -> str:
    """Texto limpio; los nulos y los 'nan' de pandas quedan como cadena vacía."""
    if valor is None:
        return ""
    if isinstance(valor, float) and math.isnan(valor):
        return ""
    texto = str(valor).strip()
    if texto.lower() in {"nan", "nat", "none", "null", "-", "--"}:
        return ""
    return re.sub(r"\s+", " ", texto)


def a_numero(valor: Any, *, punto_es_miles: bool = False) -> float | None:
    """Convierte a número tolerando formatos mixtos.

    Acepta "1.234,56" (europeo), "1,234.56" (anglosajón), "$ 1 234,56",
    "(1.234,56)" como negativo y "1234" a secas.

    ``punto_es_miles`` resuelve el caso ambiguo de un único punto seguido de
    tres dígitos: en importes "1.500" son mil quinientos, mientras que en un
    tipo de cambio "1.085" es un decimal. Se usa True solo para montos.
    """
    if valor is None:
        return None
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        numero = float(valor)
        return None if math.isnan(numero) else numero

    texto = a_texto(valor)
    if not texto:
        return None

    negativo = texto.startswith("(") and texto.endswith(")")
    texto = texto.strip("()")
    texto = re.sub(r"[^\d,.\-+]", "", texto)
    if not texto or texto in {"-", "+", ".", ","}:
        return None

    if "," in texto and "." in texto:
        # El separador decimal es el que aparece más a la derecha.
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        entero, _, decimales = texto.rpartition(",")
        # "1,234" con 3 dígitos finales es separador de miles, no decimal.
        texto = f"{entero}.{decimales}" if len(decimales) != 3 else texto.replace(",", "")
    else:
        entero, punto, decimales = texto.rpartition(".")
        if punto and len(decimales) == 3 and (entero.count(".") >= 1 or punto_es_miles):
            texto = texto.replace(".", "")

    try:
        numero = float(texto)
    except ValueError:
        return None
    if math.isnan(numero) or math.isinf(numero):
        return None
    return -numero if negativo else numero


_FORMATOS_FECHA = (
    "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d.%m.%Y",
    "%Y/%m/%d", "%d %b %Y", "%d%b%Y", "%m/%d/%Y",
)


def a_fecha(valor: Any) -> date | None:
    """Convierte a fecha textos, datetimes y seriales de Excel."""
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, pd.Timestamp):
        return valor.date()

    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        if math.isnan(valor):
            return None
        # Serial de Excel (1900-01-01 ≈ 1, 2100 ≈ 73050).
        if 20000 <= float(valor) <= 80000:
            base = datetime(1899, 12, 30)
            return (base + pd.Timedelta(days=float(valor))).date()
        return None

    texto = a_texto(valor)
    if not texto:
        return None
    texto_corto = texto.split(" ")[0] if " " in texto and ":" in texto else texto
    for formato in _FORMATOS_FECHA:
        try:
            return datetime.strptime(texto_corto, formato).date()
        except ValueError:
            continue
    try:
        convertida = pd.to_datetime(texto, dayfirst=True, errors="coerce")
    except Exception:
        return None
    return None if pd.isna(convertida) else convertida.date()


def normalizar_tipo_mensaje(valor: Any) -> str:
    """Deja el mensaje SWIFT como 'TF' o 'GS'; si no lo reconoce, devuelve el texto."""
    texto = catalogo.normalizar(valor)
    if not texto:
        return ""
    m = re.search(r"\b(TF|GS)\b", texto)
    return m.group(1) if m else texto


# ---------------------------------------------------------------------------
# Lectura del archivo
# ---------------------------------------------------------------------------
def leer_crudo(origen: Any, nombre: str | None = None, hoja: Any = None) -> tuple[pd.DataFrame, str | None]:
    """Lee el archivo sin interpretar encabezados. Acepta ruta, bytes o buffer."""
    nombre_ref = nombre or (str(origen) if isinstance(origen, (str, Path)) else "")
    extension = Path(nombre_ref).suffix.lower()

    if isinstance(origen, bytes):
        origen = io.BytesIO(origen)

    if extension in {".csv", ".txt", ".tsv"}:
        if hasattr(origen, "seek"):
            origen.seek(0)
        crudo = pd.read_csv(
            origen, header=None, dtype=object, sep=None, engine="python",
            encoding="utf-8-sig", keep_default_na=True, on_bad_lines="skip",
        )
        return crudo, None

    if hasattr(origen, "seek"):
        origen.seek(0)
    libro = pd.ExcelFile(origen)
    nombre_hoja = hoja if hoja in libro.sheet_names else libro.sheet_names[0]
    crudo = libro.parse(nombre_hoja, header=None, dtype=object)
    return crudo, str(nombre_hoja)


def hojas_disponibles(origen: Any, nombre: str | None = None) -> list[str]:
    nombre_ref = nombre or (str(origen) if isinstance(origen, (str, Path)) else "")
    if Path(nombre_ref).suffix.lower() in {".csv", ".txt", ".tsv"}:
        return []
    if isinstance(origen, bytes):
        origen = io.BytesIO(origen)
    if hasattr(origen, "seek"):
        origen.seek(0)
    return list(pd.ExcelFile(origen).sheet_names)


def detectar_encabezado(crudo: pd.DataFrame, filas_a_revisar: int = 25) -> int | None:
    """Ubica la fila de encabezados: la que reconoce más campos del catálogo."""
    mejor_fila, mejor_puntaje = None, 0
    for indice in range(min(filas_a_revisar, len(crudo))):
        fila = crudo.iloc[indice]
        campos = {catalogo.campo_de_encabezado(v) for v in fila}
        campos.discard(None)
        if len(campos) > mejor_puntaje:
            mejor_fila, mejor_puntaje = indice, len(campos)
    return mejor_fila if mejor_puntaje >= 2 else None


def mapear_columnas(encabezados: list[Any]) -> tuple[dict[str, str], list[str]]:
    """Encabezado real -> campo canónico. Devuelve también los ignorados."""
    detectadas: dict[str, str] = {}
    ignoradas: list[str] = []
    usados: set[str] = set()
    for encabezado in encabezados:
        texto = a_texto(encabezado)
        campo = catalogo.campo_de_encabezado(encabezado)
        if campo and campo not in usados:
            detectadas[texto or campo] = campo
            usados.add(campo)
        elif texto:
            ignoradas.append(texto)
    return detectadas, ignoradas


# ---------------------------------------------------------------------------
# Depuración
# ---------------------------------------------------------------------------
def procesar(
    origen: Any,
    *,
    modulo: str,
    periodo: str,
    nombre: str | None = None,
    hoja: Any = None,
) -> ResultadoETL:
    """Lee, mapea y depura un archivo para el módulo y período indicados."""
    crudo, nombre_hoja = leer_crudo(origen, nombre=nombre, hoja=hoja)
    fila_encabezado = detectar_encabezado(crudo)

    if fila_encabezado is None:
        vacio = pd.DataFrame(columns=CAMPOS_SALIDA)
        return ResultadoETL(
            datos=vacio,
            descartes=pd.DataFrame(columns=["_fila", "motivo"]),
            columnas_faltantes=catalogo.campos_requeridos(),
            filas_origen=len(crudo),
            hoja=nombre_hoja,
        )

    encabezados = list(crudo.iloc[fila_encabezado])
    detectadas, ignoradas = mapear_columnas(encabezados)
    cuerpo = crudo.iloc[fila_encabezado + 1:].reset_index(drop=True)
    cuerpo.columns = [a_texto(e) or f"col_{i}" for i, e in enumerate(encabezados)]

    posicion_de_campo = {
        campo: indice
        for indice, encabezado in enumerate(encabezados)
        for campo in [catalogo.campo_de_encabezado(encabezado)]
        if campo and campo not in {
            c for i, e in enumerate(encabezados[:indice])
            for c in [catalogo.campo_de_encabezado(e)] if c
        }
    }
    faltantes = [c for c in catalogo.campos_requeridos() if c not in posicion_de_campo]

    filas: list[dict] = []
    descartes: list[dict] = []
    textos_no_dato = catalogo.filas_no_dato()

    for indice in range(len(cuerpo)):
        valores = list(cuerpo.iloc[indice])
        numero_fila = fila_encabezado + indice + 2  # 1-based, como lo ve el usuario

        def bruto(campo: str) -> Any:
            posicion = posicion_de_campo.get(campo)
            return valores[posicion] if posicion is not None and posicion < len(valores) else None

        if all(not a_texto(v) for v in valores):
            continue  # fila totalmente vacía: ni siquiera se reporta

        primeros = " ".join(a_texto(v) for v in valores[:3])
        if any(catalogo.normalizar(primeros).startswith(t) for t in textos_no_dato):
            descartes.append(_descarte(numero_fila, MOTIVO_TOTALES, valores, encabezados))
            continue

        fecha = a_fecha(bruto("fecha"))
        monto = a_numero(bruto("monto"), punto_es_miles=True)
        moneda = catalogo.normalizar_moneda(bruto("moneda"))
        tipo_cambio = a_numero(bruto("tipo_cambio"))
        monto_usd = a_numero(bruto("monto_usd"), punto_es_miles=True)

        if fecha is None:
            descartes.append(_descarte(numero_fila, MOTIVO_FECHA, valores, encabezados))
            continue
        if monto is None and monto_usd is None:
            descartes.append(_descarte(numero_fila, MOTIVO_MONTO, valores, encabezados))
            continue

        monto_usd, tipo_cambio = _resolver_usd(monto, monto_usd, moneda, tipo_cambio, periodo)
        if monto_usd is None:
            descartes.append(_descarte(numero_fila, MOTIVO_SIN_TC, valores, encabezados))
            continue

        filas.append({
            "_fila": numero_fila,
            "fecha": fecha,
            "anio": fecha.year,
            "mes": fecha.month,
            "nombre_mes": config.MESES[fecha.month],
            "referencia": a_texto(bruto("referencia")),
            "corresponsal": a_texto(bruto("corresponsal")).upper() or "SIN CORRESPONSAL",
            "area_750": a_texto(bruto("area_750")).upper(),
            "deuda_771": a_texto(bruto("deuda_771")).upper(),
            "tipo_mensaje": normalizar_tipo_mensaje(bruto("tipo_mensaje")),
            "proceso": a_texto(bruto("proceso")).upper(),
            "moneda": moneda or "SIN MONEDA",
            "monto": monto if monto is not None else monto_usd,
            "tipo_cambio": tipo_cambio,
            "monto_usd": monto_usd,
            "beneficiario": a_texto(bruto("beneficiario")),
            "estado": a_texto(bruto("estado")).upper(),
            "sentido": a_texto(bruto("sentido")),
        })

    datos = pd.DataFrame(filas, columns=["_fila"] + CAMPOS_SALIDA) if filas else pd.DataFrame(columns=["_fila"] + CAMPOS_SALIDA)
    tabla_descartes = pd.DataFrame(descartes) if descartes else pd.DataFrame(columns=["_fila", "motivo"])

    return ResultadoETL(
        datos=datos,
        descartes=tabla_descartes,
        columnas_detectadas=detectadas,
        columnas_ignoradas=ignoradas,
        columnas_faltantes=faltantes,
        filas_origen=int(len(cuerpo)),
        fila_encabezado=fila_encabezado + 1,
        hoja=nombre_hoja,
    )


def _resolver_usd(
    monto: float | None,
    monto_usd: float | None,
    moneda: str,
    tipo_cambio: float | None,
    periodo: str,
) -> tuple[float | None, float | None]:
    """Determina el equivalente en USD y el factor aplicado."""
    if monto_usd is not None:
        factor = tipo_cambio
        if factor is None and monto not in (None, 0):
            factor = round(monto_usd / monto, 8)
        return monto_usd, factor
    if monto is None:
        return None, None
    if moneda in ("USD", "", "SIN MONEDA"):
        return monto, 1.0
    if tipo_cambio is not None and tipo_cambio > 0:
        return monto * tipo_cambio, tipo_cambio
    factor_catalogo = catalogo.tipo_cambio(moneda, periodo)
    if factor_catalogo:
        return monto * factor_catalogo, factor_catalogo
    return None, None


def _descarte(numero_fila: int, motivo: str, valores: list, encabezados: list) -> dict:
    registro: dict[str, Any] = {"_fila": numero_fila, "motivo": motivo}
    for encabezado, valor in zip(encabezados, valores):
        clave = a_texto(encabezado)
        if clave:
            registro[clave] = a_texto(valor)
    return registro
