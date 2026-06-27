import os
import re
import xlrd
from datetime import datetime
from collections import OrderedDict

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    OPENPYXL = True
except ImportError:
    OPENPYXL = False

# =========================================================================
# CONCILIACIÓN BCE  vs  MEF  —  Pagos de Deuda Externa Pública (mensual)
# =========================================================================
# Cruza, por cada acreedor, los valores que reporta el MEF (hoja "Resumen")
# contra los que reporta el BCE (hojas "Giros del/al Exterior"), para los
# seis conceptos de pago. Genera un reporte con MEF, BCE, Diferencia y Estado.
#
# Mapeo de conceptos (según lo solicitado):
#   Concepto              MEF Resumen   BCE hoja / columna
#   --------------------  -----------   ------------------------------------
#   Desembolsos             C           Giros del Exterior  -> K (Valor USD)
#   Amortizaciones          D           Giros al  Exterior  -> U (Capital USD)
#   Intereses               E           Giros al  Exterior  -> V (Interés USD)
#   Comisiones              F           Giros al  Exterior  -> W (Comisión USD)
#   Intereses Condonados    G           Giros al  Exterior  -> Y (Condonados USD)
#   Interés por Mora        H           Giros al  Exterior  -> X (Interés Mora USD)
# =========================================================================

# -------------------------------------------------------------------------
# CONFIGURACIÓN
# -------------------------------------------------------------------------
ARCHIVO_BCE = r"Reporte_Conciliacion_BCE.xls"
ARCHIVO_MEF = r"Reporte_Conciliacion_MEF.xls"

# Tolerancia (USD) para considerar dos valores como conciliados.
TOLERANCIA = 0.50

# Helpers de letra de columna de Excel -> índice base 0 (A=0, K=10, U=20...)
def col(letra: str) -> int:
    n = 0
    for ch in letra.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


# Conceptos: (nombre, columna MEF, hoja BCE, columna BCE)
DESEMBOLSO = "Giros del Exterior"
PAGO = "Giros al Exterior"
CONCEPTOS = [
    ("Desembolsos",          col("C"), DESEMBOLSO, col("K")),
    ("Amortizaciones",       col("D"), PAGO,       col("U")),
    ("Intereses",            col("E"), PAGO,       col("V")),
    ("Comisiones",           col("F"), PAGO,       col("W")),
    ("Intereses Condonados", col("G"), PAGO,       col("Y")),
    ("Interés por Mora",     col("H"), PAGO,       col("X")),
]

# -------------------------------------------------------------------------
# MAPEO DE ACREEDORES  BCE (agrupación) -> MEF (organismo)
# -------------------------------------------------------------------------
# La conciliación se hace al NIVEL en que el BCE desglosa, que es el nivel
# útil de cruce. El MEF reporta tanto subtotales padre (MULTILATERAL,
# INSTITUCIÓN FINANCIERA INTERNACIONAL) como sus hijos; comparar contra el
# padre duplicaría valores, así que cada agrupación del BCE se mapea a la
# fila exacta del MEF con la que se concilia:
#   - Multilaterales y agencias: fila propia del MEF (BID, CAF, FMI...).
#   - GOBIERNOS / BANCOS: el BCE no los desglosa -> subtotal del MEF.
#   - "OTROS" del BCE = intereses de THE BANK OF NEW YORK, que el MEF ubica
#     dentro de BANCOS  ->  se concilia a nivel BANCOS.
#   - AMAZON / GPS: el MEF los agrupa en INSTITUCIÓN FINANCIERA INTERNACIONAL.
ALIAS_MEF = {
    "BID": "BID", "BIRF": "BIRF", "CAF": "CAF", "FIDA": "FIDA",
    "FMI": "FMI", "FLAR": "FLAR", "AIIB": "AIIB",
    "AMAZON": "AMAZON DAC", "GPS": "GPS",
    "GOBIERNOS": "GOBIERNOS", "BANCOS": "BANCOS",
    "BONOS": "BONOS", "OTROS": "BANCOS",
    # Sub-prestamistas que el MEF agrupa en GOBIERNOS / BANCOS
    "KFW": "GOBIERNOS", "EXIMBANK KOREA": "GOBIERNOS",
    "BEI": "BANCOS",
}

# Universo de conciliación: el conjunto que el BCE efectivamente desglosa.
# Se itera en este orden y, por cada entidad, el valor MEF es el de su fila
# exacta y el valor BCE es la suma de sus agrupaciones mapeadas.
ENTIDADES = [
    "AIIB", "BID", "FMI", "FLAR", "BIRF", "CAF", "FIDA",
    "GOBIERNOS", "BANCOS", "AMAZON DAC", "GPS", "BONOS",
]


def normaliza(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().upper())


def acreedor_mef(agrupacion_bce: str) -> str:
    """Resuelve el organismo MEF a partir de la agrupación del BCE."""
    g = normaliza(agrupacion_bce)
    # 1) Sufijo " - XXX" (nombre largo del prestamista)
    if " - " in g:
        sufijo = g.rsplit(" - ", 1)[1].strip()
        if sufijo in ALIAS_MEF:
            return ALIAS_MEF[sufijo]
    # 2) Coincidencia directa o por palabra clave
    for clave, destino in ALIAS_MEF.items():
        if g == clave or re.search(rf"\b{re.escape(clave)}\b", g):
            return destino
    return g  # se reporta tal cual si no hay mapeo (queda visible como "sin par")


# -------------------------------------------------------------------------
# LECTURA DE HOJAS
# -------------------------------------------------------------------------
def abrir_hoja(libro, patron):
    """Devuelve la primera hoja cuyo nombre contiene 'patron'."""
    for hoja in libro.sheets():
        if patron.lower() in hoja.name.lower():
            return hoja
    raise ValueError(f"No se encontró una hoja que contenga '{patron}'")


def fila_encabezado(hoja, etiqueta):
    """Localiza la fila de encabezados buscando una celda con 'etiqueta'."""
    for r in range(min(hoja.nrows, 20)):
        for c in range(hoja.ncols):
            if etiqueta.lower() in str(hoja.cell_value(r, c)).lower():
                return r
    return 0


def num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def leer_mef(ruta):
    """{ organismo_normalizado: {concepto: valor} } desde la hoja Resumen."""
    libro = xlrd.open_workbook(ruta)
    hoja = abrir_hoja(libro, "Resumen")
    hr = fila_encabezado(hoja, "Organismo")
    datos = OrderedDict()
    for r in range(hr + 1, hoja.nrows):
        org = normaliza(hoja.cell_value(r, col("A")))
        if not org or org.startswith("TOTAL"):
            continue
        datos[org] = {nom: num(hoja.cell_value(r, cm)) for nom, cm, _, _ in CONCEPTOS}
    return datos


def leer_bce(ruta):
    """{ organismo_MEF: {concepto: valor} } agregando las hojas de Giros."""
    libro = xlrd.open_workbook(ruta)
    agg = OrderedDict()

    def acumula(hoja_pat, col_grupo, conceptos):
        hoja = abrir_hoja(libro, hoja_pat)
        hr = fila_encabezado(hoja, "Agrupaci")
        for r in range(hr + 1, hoja.nrows):
            grupo = str(hoja.cell_value(r, col_grupo)).strip()
            if not grupo:
                continue
            # Filas de detalle: alguna columna de concepto debe ser numérica
            valores = {nom: num(hoja.cell_value(r, cb)) for nom, cb in conceptos}
            if not any(valores.values()):
                continue
            destino = acreedor_mef(grupo)
            fila = agg.setdefault(destino, {nom: 0.0 for nom, *_ in CONCEPTOS})
            for nom, v in valores.items():
                fila[nom] += v

    # Desembolsos -> Giros del Exterior, agrupación en col E
    acumula(DESEMBOLSO, col("E"),
            [(n, cb) for n, _, h, cb in CONCEPTOS if h == DESEMBOLSO])
    # Pagos -> Giros al Exterior, agrupación en col I
    acumula(PAGO, col("I"),
            [(n, cb) for n, _, h, cb in CONCEPTOS if h == PAGO])
    return agg


# -------------------------------------------------------------------------
# CONCILIACIÓN
# -------------------------------------------------------------------------
def conciliar(mef, bce):
    # Se concilia sobre el universo que el BCE desglosa (ENTIDADES); cualquier
    # agrupación BCE no prevista se añade al final para no perder información.
    extra = [a for a in bce if a not in ENTIDADES]
    filas = []
    for ac in ENTIDADES + extra:
        m = mef.get(ac, {})
        b = bce.get(ac, {})
        if not m and not b:
            continue
        for nom, *_ in CONCEPTOS:
            vm = m.get(nom, 0.0)
            vb = b.get(nom, 0.0)
            if vm == 0.0 and vb == 0.0:
                continue
            dif = round(vm - vb, 2)
            estado = "CONCILIADO" if abs(dif) <= TOLERANCIA else "DIFERENCIA"
            filas.append((ac, nom, round(vm, 2), round(vb, 2), dif, estado))
    return filas


def reporte_xlsx(filas, ruta):
    if not OPENPYXL:
        return False
    wb = Workbook()
    ws = wb.active
    ws.title = "Conciliacion"
    enc = ["Acreedor", "Concepto", "MEF (USD)", "BCE (USD)", "Diferencia", "Estado"]
    ws.append(enc)
    azul = PatternFill("solid", fgColor="1F4E78")
    rojo = PatternFill("solid", fgColor="FFC7CE")
    verde = PatternFill("solid", fgColor="C6EFCE")
    borde = Border(*[Side(style="thin", color="D0D0D0")] * 4)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = azul
        c.alignment = Alignment(horizontal="center")
    for fila in filas:
        ws.append(fila)
        r = ws.max_row
        for cell in ws[r]:
            cell.border = borde
        for ci in (3, 4, 5):
            ws.cell(r, ci).number_format = "#,##0.00"
        est = ws.cell(r, 6)
        est.fill = verde if fila[5] == "CONCILIADO" else rojo
        est.alignment = Alignment(horizontal="center")
    anchos = [34, 22, 16, 16, 16, 14]
    for i, w in enumerate(anchos, 1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = "A2"
    wb.save(ruta)
    return True


# -------------------------------------------------------------------------
# PROCESO PRINCIPAL
# -------------------------------------------------------------------------
def main():
    log = [f"📅 Conciliación BCE vs MEF — {datetime.now():%Y-%m-%d %H:%M:%S}\n"]
    mef = leer_mef(ARCHIVO_MEF)
    bce = leer_bce(ARCHIVO_BCE)
    filas = conciliar(mef, bce)

    ancho = max((len(f[0]) for f in filas), default=10)
    conciliados = diferencias = 0
    print(f"\n{'ACREEDOR':<{ancho}}  {'CONCEPTO':<22} {'MEF':>16} {'BCE':>16} {'DIFERENCIA':>14}  ESTADO")
    print("─" * (ancho + 90))
    for ac, nom, vm, vb, dif, estado in filas:
        icono = "✅" if estado == "CONCILIADO" else "❌"
        linea = f"{ac:<{ancho}}  {nom:<22} {vm:>16,.2f} {vb:>16,.2f} {dif:>14,.2f}  {icono} {estado}"
        print(linea)
        log.append(linea)
        if estado == "CONCILIADO":
            conciliados += 1
        else:
            diferencias += 1

    resumen = f"\n📊 Resumen: {conciliados} conciliados | {diferencias} con diferencia"
    print(resumen)
    log.append(resumen)

    ruta_xlsx = os.path.join(os.getcwd(), "Conciliacion_BCE_MEF.xlsx")
    if reporte_xlsx(filas, ruta_xlsx):
        print(f"📄 Reporte Excel: {ruta_xlsx}")
        log.append(f"Reporte Excel: {ruta_xlsx}")
    else:
        print("⚠️ openpyxl no disponible: se omitió el reporte .xlsx")

    ruta_log = os.path.join(os.getcwd(), "log_conciliacion.txt")
    with open(ruta_log, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    print(f"📝 Log: {ruta_log}")


if __name__ == "__main__":
    main()
