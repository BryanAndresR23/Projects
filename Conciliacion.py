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

# Estructura de cada hoja del BCE. Las hojas listan el DETALLE por préstamo y
# debajo una fila de SUBTOTAL por acreedor; la etiqueta de "Agrupación" solo
# aparece en la PRIMERA fila de cada grupo. Por eso se arrastra la agrupación
# hacia abajo y se suman únicamente las filas de detalle, que se reconocen
# porque tienen un identificador de préstamo:
#   col_grupo = columna "Agrupación"; col_ref = columna identificadora de detalle
#   (Giros del -> No. SIGADE col C; Giros al -> No. Referencia Préstamo col H)
HOJA_GRUPO = {DESEMBOLSO: col("E"), PAGO: col("I")}
HOJA_REF = {DESEMBOLSO: col("C"), PAGO: col("H")}

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


# -------------------------------------------------------------------------
# DETECCIÓN DE COLUMNAS POR TÍTULO (robusta: no depende de letras fijas)
# -------------------------------------------------------------------------
# Cada columna se ubica por su ENCABEZADO, no por su letra. Así, si el BCE o
# el MEF agregan/mueven columnas, la conciliación sigue tomando el rubro
# correcto. Si no se encuentra el título, se usa la letra fija como respaldo.
#   spec = (incluye[], excluye[])  -> la celda debe contener todos los de
#          'incluye' y ninguno de 'excluye' (comparación en minúsculas).
MEF_COLS = {
    "Desembolsos":          (["desembols"], []),
    "Amortizaciones":       (["amortiz"], []),
    "Intereses":            (["inter"], ["condon", "mora"]),
    "Comisiones":           (["comis"], []),
    "Intereses Condonados": (["condon"], []),
    "Interés por Mora":     (["mora"], []),
}
BCE_AL_COLS = {
    "Amortizaciones":       (["capital", "usd"], []),
    "Intereses":            (["inter", "usd"], ["mora", "comis", "condon"]),
    "Comisiones":           (["comis", "usd"], []),
    "Intereses Condonados": (["condonad", "usd"], []),   # "Condonado" o "Condonados"
    "Interés por Mora":     (["mora", "usd"], []),
}
BCE_DEL_VALOR = (["valor", "usd"], [])          # Desembolsos: "Valor (USD)"
SPEC_GRUPO    = (["agrupaci"], [])              # "Agrupación del/al Exterior"
SPEC_REF_DEL  = (["sigade"], [])                # "No. SIGADE"
SPEC_REF_AL   = (["referencia"], [])            # "No. Referencia Préstamo"


def _celdas_encabezado(hoja, hr):
    return [str(hoja.cell_value(hr, c)).lower() for c in range(hoja.ncols)]


def buscar_col(cabeceras, spec, respaldo=None):
    """Índice de la columna cuyo encabezado cumple spec; si no, 'respaldo'."""
    incluye, excluye = spec
    for i, txt in enumerate(cabeceras):
        if all(k in txt for k in incluye) and not any(k in txt for k in excluye):
            return i
    return respaldo


def leer_mef(ruta):
    """{ organismo_normalizado: {concepto: valor} } desde la hoja Resumen.
    Las columnas de cada rubro se localizan por su título."""
    libro = xlrd.open_workbook(ruta)
    hoja = abrir_hoja(libro, "Resumen")
    hr = fila_encabezado(hoja, "Organismo")
    cab = _celdas_encabezado(hoja, hr)
    c_org = buscar_col(cab, (["organismo"], []), col("A"))
    # Columna de cada concepto por título (respaldo: letra fija de CONCEPTOS)
    cols = {nom: buscar_col(cab, MEF_COLS[nom], cm) for nom, cm, _, _ in CONCEPTOS}
    datos = OrderedDict()
    for r in range(hr + 1, hoja.nrows):
        org = normaliza(hoja.cell_value(r, c_org))
        if not org or org.startswith("TOTAL"):
            continue
        datos[org] = {nom: num(hoja.cell_value(r, cols[nom])) for nom, *_ in CONCEPTOS}
    return datos


def leer_bce(ruta):
    """{ organismo_MEF: {concepto: valor} } agregando las hojas de Giros.

    Usa las filas de SUBTOTAL del propio BCE ('TOTAL <grupo>'), que es su
    clasificación oficial y separa correctamente casos ambiguos (p. ej. los
    Bonos Soberanos y el Bank of New York que comparten la agrupación 'OTROS',
    pero el BCE subtotaliza como 'TOTAL BONOS' y 'TOTAL BANCOS').
    Las columnas de cada rubro se ubican por TÍTULO, así no afecta que cambien
    de posición entre meses (p. ej. 'Condonado (USD)' que se desplaza de columna).
    Si no hubiera filas de subtotal, cae a sumar el detalle por agrupación."""
    libro = xlrd.open_workbook(ruta)
    agg = OrderedDict()

    def _fila_acreedor(destino):
        return agg.setdefault(destino, {nom: 0.0 for nom, *_ in CONCEPTOS})

    def acumula(hoja_pat, specs_valor, spec_ref, respaldo_ref):
        hoja = abrir_hoja(libro, hoja_pat)
        hr = fila_encabezado(hoja, "Agrupaci")
        cab = _celdas_encabezado(hoja, hr)
        cols_val = {nom: buscar_col(cab, spec, cb) for nom, spec, cb in specs_valor}

        # 1) Camino robusto: filas de subtotal 'TOTAL <grupo>'
        encontrados = 0
        for r in range(hr + 1, hoja.nrows):
            etiqueta = None
            for c in range(min(hoja.ncols, 3)):
                t = str(hoja.cell_value(r, c)).strip()
                if t.upper().startswith("TOTAL"):
                    resto = t[5:].strip(" -:")
                    etiqueta = resto  # vacío => gran total (se ignora)
                    break
            if not etiqueta:
                continue
            destino = acreedor_mef(etiqueta)
            fila = _fila_acreedor(destino)
            for nom, ci in cols_val.items():
                if ci is not None:
                    fila[nom] += num(hoja.cell_value(r, ci))
            encontrados += 1
        if encontrados:
            return

        # 2) Respaldo: sumar el detalle arrastrando la agrupación
        col_grupo = buscar_col(cab, SPEC_GRUPO, HOJA_GRUPO[hoja_pat])
        col_ref = buscar_col(cab, spec_ref, respaldo_ref)
        grupo = None
        for r in range(hr + 1, hoja.nrows):
            et = str(hoja.cell_value(r, col_grupo)).strip()
            if et:
                grupo = et
            ref = str(hoja.cell_value(r, col_ref)).strip()
            if not ref or grupo is None:
                continue
            fila = _fila_acreedor(acreedor_mef(grupo))
            for nom, ci in cols_val.items():
                if ci is not None:
                    fila[nom] += num(hoja.cell_value(r, ci))

    acumula(DESEMBOLSO,
            [(n, BCE_DEL_VALOR, cb) for n, _, h, cb in CONCEPTOS if h == DESEMBOLSO],
            SPEC_REF_DEL, HOJA_REF[DESEMBOLSO])
    acumula(PAGO,
            [(n, BCE_AL_COLS[n], cb) for n, _, h, cb in CONCEPTOS if h == PAGO],
            SPEC_REF_AL, HOJA_REF[PAGO])
    return agg


def leer_mef_detalle(ruta):
    """Lista de filas del Resumen MEF con sus valores y la Observación.
    Incluye TODAS las carteras (hijas y subtotales) para el diagnóstico."""
    libro = xlrd.open_workbook(ruta)
    hoja = abrir_hoja(libro, "Resumen")
    hr = fila_encabezado(hoja, "Organismo")
    cab = _celdas_encabezado(hoja, hr)
    c_org = buscar_col(cab, (["organismo"], []), col("A"))
    c_obs = buscar_col(cab, (["observ"], []), None)
    cols = {nom: buscar_col(cab, MEF_COLS[nom], cm) for nom, cm, _, _ in CONCEPTOS}
    filas = []
    for r in range(hr + 1, hoja.nrows):
        org = normaliza(hoja.cell_value(r, c_org))
        if not org or org.startswith("TOTAL"):
            continue
        valores = {nom: num(hoja.cell_value(r, cols[nom])) for nom, *_ in CONCEPTOS}
        obs = "" if c_obs is None else str(hoja.cell_value(r, c_obs)).strip()
        filas.append({"organismo": org, "valores": valores, "observacion": obs})
    return filas


# -------------------------------------------------------------------------
# TOTALES POR CARTERA  (MEF vs BCE)  —  para verificar el cuadre completo
# -------------------------------------------------------------------------
def totales_por_cartera(filas):
    """Suma de todos los rubros por acreedor: {acreedor: {mef,bce,dif,estado}}."""
    tot = OrderedDict()
    for ac, _concepto, vm, vb, _dif, _estado in filas:
        t = tot.setdefault(ac, {"mef": 0.0, "bce": 0.0})
        t["mef"] += vm
        t["bce"] += vb
    for ac, t in tot.items():
        t["mef"] = round(t["mef"], 2)
        t["bce"] = round(t["bce"], 2)
        t["dif"] = round(t["mef"] - t["bce"], 2) or 0.0
        t["estado"] = "CONCILIADO" if abs(t["dif"]) <= TOLERANCIA else "DIFERENCIA"
    return tot


# -------------------------------------------------------------------------
# DIAGNÓSTICO DE DIFERENCIAS  —  lee Observaciones del MEF y las clasifica
# -------------------------------------------------------------------------
def clasificar_observacion(texto):
    """Devuelve (tipo, por_que, accion) según la observación del MEF."""
    t = (texto or "").lower()
    if "pago directo" in t or "forma directa" in t or "de forma directa" in t or "directo" in t:
        return ("Pago Directo",
                'El desembolso se realizó directamente al proveedor sin pasar por el '
                'Banco Central, por lo que el BCE no lo registra.',
                'Agregar fila en "Giros del Exterior" del BCE con el valor del pago '
                'directo y adjuntar el respaldo (Quipux).')
    if "cambiario" in t or "moneda original" in t:
        return ("Diferencial Cambiario",
                'La moneda original del préstamo difiere del USD, generando variaciones '
                'por tipo de cambio.',
                'Diferencia por conversión de moneda. Registrar como observación; '
                'no requiere ajuste numérico.')
    return ("Otro",
            'Movimiento registrado en el MEF que requiere revisión con el BCE.',
            'Verificar documentación con el BCE y coordinar corrección.')


MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def detectar_periodo(ruta_mef=None, ruta_bce=None):
    """Detecta el periodo del reporte sin que el usuario lo elija.
    Devuelve {periodo:'AAAA-MM', mes:'Marzo', anio:'2026', texto:'Marzo 2026'}."""
    def desde_mef(ruta):
        try:
            hoja = abrir_hoja(xlrd.open_workbook(ruta), "Resumen")
        except Exception:
            return None
        for r in range(min(hoja.nrows, 6)):
            for c in range(min(hoja.ncols, 4)):
                t = str(hoja.cell_value(r, c)).lower()
                ma = re.search(r"(20\d{2})", t)
                for i, mes in enumerate(MESES_ES, 1):
                    if mes in t and ma:
                        return (ma.group(1), i)
        return None

    def desde_bce(ruta):
        try:
            wb = xlrd.open_workbook(ruta)
        except Exception:
            return None
        for hoja in wb.sheets():
            for r in range(min(hoja.nrows, 6)):
                for c in range(min(hoja.ncols, 6)):
                    t = str(hoja.cell_value(r, c))
                    m = re.search(r"(\d{2})/(20\d{2})", t)  # "Desde: 02/03/2026"
                    m2 = re.search(r"\d{2}/(\d{2})/(20\d{2})", t)
                    if m2:
                        return (m2.group(2), int(m2.group(1)))
        return None

    res = (desde_mef(ruta_mef) if ruta_mef else None) or \
          (desde_bce(ruta_bce) if ruta_bce else None)
    if not res:
        ahora = datetime.now()
        res = (str(ahora.year), ahora.month)
    anio, mnum = res
    return {"periodo": f"{anio}-{mnum:02d}", "mes": MESES_ES[mnum - 1].capitalize(),
            "anio": anio, "texto": f"{MESES_ES[mnum - 1].capitalize()} {anio}"}


def diagnostico(ruta_mef, filas=None):
    """Diagnóstico SOLO de las carteras que NO concilian.
    Para cada cartera con diferencia, busca la Observación del MEF que la
    explica y la clasifica. Si filas es None, se basa solo en observaciones.
    Item: {acreedor, tipo, observacion, por_que, accion, rubros{concepto:dif}}."""
    # Mapa de observaciones del MEF, resuelto al nivel de conciliación
    obs_por_cartera = {}
    for fila in leer_mef_detalle(ruta_mef):
        obs = fila["observacion"].strip()
        if not obs:
            continue
        dest = acreedor_mef(fila["organismo"])
        obs_por_cartera.setdefault(dest, []).append((fila["organismo"], obs))

    if filas is None:
        # Modo simple: todas las observaciones (compatibilidad)
        items = []
        for cart, lst in obs_por_cartera.items():
            for _org, obs in lst:
                tipo, por_que, accion = clasificar_observacion(obs)
                items.append({"acreedor": cart, "tipo": tipo, "observacion": obs,
                              "por_que": por_que, "accion": accion, "rubros": {}})
        return items

    # Diferencias reales por cartera (rubro -> |diferencia|)
    difs = OrderedDict()
    for ac, concepto, _vm, _vb, dif, estado in filas:
        if estado == "DIFERENCIA":
            difs.setdefault(ac, {})[concepto] = round(abs(dif), 2)

    items = []
    for cart, rubros in difs.items():
        obs_list = obs_por_cartera.get(cart, [])
        obs = "; ".join(f"{o}: {t}" for o, t in obs_list) if obs_list else ""
        tipo, por_que, accion = clasificar_observacion(obs)
        items.append({
            "acreedor": cart, "tipo": tipo, "observacion": obs,
            "por_que": por_que, "accion": accion, "rubros": rubros,
        })
    return items


# -------------------------------------------------------------------------
# PAGOS DIRECTOS  —  respaldos que el MEF envía por Quipux
# -------------------------------------------------------------------------
# Estos archivos sustentan desembolsos hechos directamente al proveedor que
# NO pasaron por el Banco Central, por lo que no constan en el reporte BCE.
# Al conciliar hay que agregarlos al lado del BCE con su nota de pago directo.
PD_LENDER = {
    "IBRD": "BIRF", "BIRF": "BIRF", "WORLD BANK": "BIRF",
    "IDB": "BID", "BID": "BID", "CAF": "CAF", "CFA": "CAF",
    "KFW": "GOBIERNOS", "EXIMBANK KOREA": "GOBIERNOS", "FIDA": "FIDA",
    "IFAD": "FIDA", "BEI": "BANCOS", "EIB": "BANCOS",
}


def leer_pago_directo(ruta):
    """Lee un respaldo de pago directo del MEF y extrae:
       {acreedor, prestamo, referencia, valor, moneda, nota, detalle[]}.
    El valor es el TOTAL del archivo (o la suma de los pagos si no hay total)."""
    libro = xlrd.open_workbook(ruta)
    hoja = libro.sheet_by_index(0)

    # 1) Préstamo / prestamista: celda contigua a "Loan"
    prestamo = ""
    for r in range(min(hoja.nrows, 10)):
        for c in range(hoja.ncols):
            if "loan" in str(hoja.cell_value(r, c)).lower():
                if c + 1 < hoja.ncols:
                    prestamo = str(hoja.cell_value(r, c + 1)).strip()
                break
        if prestamo:
            break
    texto = (prestamo + " " + os.path.basename(ruta)).upper()

    acreedor = None
    for clave, dest in PD_LENDER.items():
        if clave in texto:
            acreedor = dest
            break

    # Referencia (código de crédito): primer número del préstamo o del nombre
    m = re.search(r"(\d{3,8})", prestamo) or re.search(r"(\d{3,8})", os.path.basename(ruta))
    referencia = m.group(1) if m else ""

    # 2) Columna de monto: encabezado con "amt"/"amount"/"pymt"
    hr = c_monto = None
    for r in range(min(hoja.nrows, 15)):
        for c in range(hoja.ncols):
            t = str(hoja.cell_value(r, c)).lower()
            if "amt" in t or "amount" in t or "pago" in t or "valor" in t:
                hr, c_monto = r, c
                break
        if c_monto is not None:
            break
    if c_monto is None:
        c_monto = 6  # respaldo: columna típica "Appl Pymt Amt"
        hr = 0

    # 3) Detalle + total. El TOTAL viene en una fila con la palabra "TOTAL".
    detalle, total_fila, suma = [], None, 0.0
    moneda = "USD"
    for r in range(hr + 1, hoja.nrows):
        fila_txt = " ".join(str(hoja.cell_value(r, c)) for c in range(hoja.ncols)).upper()
        monto = num(hoja.cell_value(r, c_monto))
        if "TOTAL" in fila_txt:
            if monto:
                total_fila = monto
            continue
        if monto:
            # beneficiario = primera celda de texto larga de la fila
            benef = ""
            for c in range(hoja.ncols):
                v = str(hoja.cell_value(r, c)).strip()
                if len(v) > 4 and not v.replace(".", "").replace(",", "").isdigit():
                    benef = v
                    break
            for c in range(hoja.ncols):
                if str(hoja.cell_value(r, c)).strip().upper() in ("USD", "EUR", "JPY"):
                    moneda = str(hoja.cell_value(r, c)).strip().upper()
            detalle.append({"beneficiario": benef, "monto": round(monto, 2)})
            suma += monto

    valor = round(total_fila if total_fila is not None else suma, 2)
    cred = (referencia[:4] + "-" + referencia[4:]) if len(referencia) == 5 else referencia
    nota = (f'Considerar desembolso realizado a través de la modalidad '
            f'"pago directo", crédito {cred}') if cred else \
           'Considerar desembolso realizado a través de la modalidad "pago directo"'

    return {
        "acreedor": acreedor, "prestamo": prestamo, "referencia": cred or referencia,
        "valor": valor, "moneda": moneda, "nota": nota, "detalle": detalle,
        "concepto": "Desembolsos",
    }


def exportar_bce_ajustado(ruta_bce, ajustes, ruta_salida):
    """Copia la hoja 'Giros del Exterior' del reporte BCE y AGREGA al final las
    filas de pago directo (con su NOTA), resaltadas, para que el ajuste quede
    reflejado en el archivo. ajustes=[{acreedor,referencia,valor,nota,...}].
    Devuelve True si se generó el .xlsx."""
    if not OPENPYXL:
        return False
    libro = xlrd.open_workbook(ruta_bce)
    hoja = abrir_hoja(libro, DESEMBOLSO)  # Giros del Exterior
    hr = fila_encabezado(hoja, "Agrupaci")
    cab = _celdas_encabezado(hoja, hr)
    c_fecha = buscar_col(cab, (["fecha"], []), 1)
    c_sig = buscar_col(cab, SPEC_REF_DEL, 2)
    c_ref = buscar_col(cab, (["referencia"], []), 3)
    c_grp = buscar_col(cab, SPEC_GRUPO, 4)
    c_prest = buscar_col(cab, (["prestamista"], []), 5)
    c_val = buscar_col(cab, BCE_DEL_VALOR, 10)
    c_nota = buscar_col(cab, (["nota"], []), max(hoja.ncols - 1, 6))

    wb = Workbook()
    ws = wb.active
    ws.title = "Giros del Exterior"
    amarillo = PatternFill("solid", fgColor="FFF2CC")
    negrita = Font(bold=True)
    # Copiar todo el contenido original
    for r in range(hoja.nrows):
        ws.append([hoja.cell_value(r, c) for c in range(hoja.ncols)])
    # Marca de separación + filas de pago directo
    sep = ws.max_row + 2
    ws.cell(sep, 1, "AJUSTES POR PAGO DIRECTO (agregados en conciliación)").font = negrita
    fecha = datetime.now().strftime("%d/%m/%Y")
    for aj in ajustes:
        ws.append([])
        r = ws.max_row
        ws.cell(r, c_fecha + 1, fecha)
        ws.cell(r, c_sig + 1, aj.get("referencia", ""))
        ws.cell(r, c_ref + 1, aj.get("referencia", ""))
        ws.cell(r, c_grp + 1, aj.get("acreedor", ""))
        ws.cell(r, c_prest + 1, aj.get("prestamista", aj.get("acreedor", "")))
        ws.cell(r, c_val + 1, aj.get("valor", 0)).number_format = "#,##0.00"
        ws.cell(r, c_nota + 1, aj.get("nota", ""))
        for c in range(1, hoja.ncols + 1):
            ws.cell(r, c).fill = amarillo
    wb.save(ruta_salida)
    return True


# -------------------------------------------------------------------------
# DIAGNÓSTICO POR PRÉSTAMO  —  identifica el crédito exacto que no cuadra
# -------------------------------------------------------------------------
# El MEF entrega una hoja por cartera con el detalle por préstamo; el BCE trae
# el detalle por crédito en 'Giros al/del Exterior'. Se cruzan por número de
# crédito para señalar exactamente cuál descuadra y por cuánto.
MEF_HOJA = {"BID": "BID", "BIRF": "BIRF", "CAF": "CAF", "FMI": "FMI",
            "FLAR": "FLAR", "FIDA": "FIDA", "AIIB": "AIIB", "BANCOS": "BANCOS",
            "GOBIERNOS": "GOBIERNOS", "BONOS": "BONOS", "GPS": "GPS",
            "AMAZON DAC": "ADAC"}
# Columna del rubro en la hoja-cartera del MEF (por título de encabezado)
MEF_HOJA_COL = {
    "Desembolsos":          (["giros"], []),
    "Amortizaciones":       (["principal"], []),
    "Intereses":            (["intereses pagad", "interes pagad", "interes"], ["condon", "mora"]),
    "Comisiones":           (["comision"], []),
    "Intereses Condonados": (["condonad"], []),
}


def _numkey(ref):
    m = re.findall(r"\d{2,8}", str(ref))
    return m[0] if m else normaliza(ref)


def _hoja_mef_cartera(libro, cartera):
    objetivo = MEF_HOJA.get(cartera, cartera)
    for h in libro.sheets():
        if objetivo.lower() in h.name.lower():
            return h
    return None


def diagnostico_prestamos(ruta_mef, ruta_bce, pares, aplicados=None):
    """Para cada (cartera, concepto) con diferencia, cruza préstamo por
    préstamo (por número de crédito) y devuelve los que no cuadran.
    pares = lista de (cartera, concepto).
    aplicados = lista de ajustes ya agregados [{acreedor, concepto, referencia,
    valor}]; su valor se suma al lado del BCE para que el crédito ya gestionado
    desaparezca del diagnóstico aunque el archivo aún no se haya reescrito.
    Devuelve { 'cartera|concepto': [ {credito, referencia, mef, bce, dif} ] }."""
    lm = xlrd.open_workbook(ruta_mef)
    lb = xlrd.open_workbook(ruta_bce)
    salida = {}
    for cartera, concepto in pares:
        if concepto not in MEF_HOJA_COL:
            continue
        hoja_mef = _hoja_mef_cartera(lm, cartera)
        if hoja_mef is None:
            continue
        hrm = 0
        cabm = _celdas_encabezado(hoja_mef, hrm)
        c_ref_m = buscar_col(cabm, (["referencia"], []), 2)
        c_val_m = buscar_col(cabm, MEF_HOJA_COL[concepto], None)
        if c_val_m is None:
            continue
        mef = {}
        refs = {}   # numero de credito -> referencia completa (para mostrar)
        for r in range(hrm + 1, hoja_mef.nrows):
            ref = str(hoja_mef.cell_value(r, c_ref_m)).strip()
            if not ref:
                continue
            k = _numkey(ref)
            refs.setdefault(k, ref)
            v = num(hoja_mef.cell_value(r, c_val_m))
            if v:
                mef[k] = mef.get(k, 0.0) + v
        # BCE: hoja y columna según concepto
        es_des = concepto == "Desembolsos"
        hoja_bce = abrir_hoja(lb, DESEMBOLSO if es_des else PAGO)
        hrb = fila_encabezado(hoja_bce, "Agrupaci")
        cabb = _celdas_encabezado(hoja_bce, hrb)
        c_grp = buscar_col(cabb, SPEC_GRUPO, 4 if es_des else 8)
        c_ref_b = buscar_col(cabb, SPEC_REF_DEL if es_des else SPEC_REF_AL, 3 if es_des else 7)
        spec_val = BCE_DEL_VALOR if es_des else BCE_AL_COLS[concepto]
        c_val_b = buscar_col(cabb, spec_val, 10 if es_des else 20)
        bce = {}
        grupo = None
        for r in range(hrb + 1, hoja_bce.nrows):
            et = str(hoja_bce.cell_value(r, c_grp)).strip()
            if et:
                grupo = et
            ref = str(hoja_bce.cell_value(r, c_ref_b)).strip()
            if not ref or grupo is None:
                continue
            if acreedor_mef(grupo) != cartera:
                continue
            k = _numkey(ref)
            refs.setdefault(k, ref)
            v = num(hoja_bce.cell_value(r, c_val_b))
            if v:
                bce[k] = bce.get(k, 0.0) + v
        # Sumar al BCE los ajustes ya gestionados (para que desaparezcan)
        for ap in (aplicados or []):
            if ap.get("acreedor") == cartera and ap.get("concepto", "Desembolsos") == concepto:
                k = _numkey(ap.get("referencia", ""))
                bce[k] = bce.get(k, 0.0) + float(ap.get("valor", 0))
        items = []
        for k in sorted(set(mef) | set(bce)):
            m = round(mef.get(k, 0.0), 2)
            b = round(bce.get(k, 0.0), 2)
            if abs(m - b) > 0.01:
                # Referencia completa para mostrar (con prefijo de la cartera)
                raw = refs.get(k, k)
                ref_full = raw if cartera.upper() in raw.upper() else f"{cartera}-{raw}"
                items.append({"credito": k, "referencia": ref_full,
                              "mef": m, "bce": b, "dif": round(m - b, 2)})
        if items:
            salida[f"{cartera}|{concepto}"] = items
    return salida


def modificar_bce_xls(ruta_bce, ajustes):
    """Inserta los ajustes en el MISMO archivo .xls del BCE SIN alterar el
    formato: usa xlutils.copy (conserva intactos los estilos originales de
    todas las celdas existentes) y desplaza las filas hacia abajo para
    insertar. Las filas nuevas copian el estilo exacto (xf) de una fila
    plantilla del propio reporte: detalle, subtotal de prestamista o TOTAL.
    Los subtotales del grupo y el TOTAL general se actualizan modificando
    solo el número de la celda (el formato no se toca).
    Cada ajuste: {acreedor, concepto, referencia, valor, nota, prestamista}."""
    try:
        import xlwt  # noqa: F401  (requerido por xlutils)
        from xlutils.copy import copy as xl_copy
    except Exception:
        return False
    try:
        rb = xlrd.open_workbook(ruta_bce, formatting_info=True)
    except Exception:
        return False
    wb = xl_copy(rb)

    by_kind = {}
    for aj in ajustes:
        kind = "del" if aj.get("concepto", "Desembolsos") == "Desembolsos" else "al"
        by_kind.setdefault(kind, []).append(aj)

    fecha = datetime.now().strftime("%d/%m/%Y")
    algo = False

    for idx, sh in enumerate(rb.sheets()):
        nl = sh.name.lower()
        kind = "del" if " del" in nl else ("al" if " al" in nl else None)
        if not kind or not by_kind.get(kind):
            continue
        ws = wb.get_sheet(idx)
        rows = ws.rows  # dict {fila: Row} de xlwt con los xf originales

        hr = fila_encabezado(sh, "Agrupaci")
        cab = _celdas_encabezado(sh, hr)
        es_des = kind == "del"
        c_fecha = buscar_col(cab, (["fecha"], []), 1)
        c_ref = buscar_col(cab, (["referencia"], []), 3 if es_des else 7)
        c_grp = buscar_col(cab, SPEC_GRUPO, 4 if es_des else 8)
        c_prest = buscar_col(cab, (["prestamista"], []), 5 if es_des else 9)
        c_tot = buscar_col(cab, (["total", "usd"], []), None)
        c_nota = buscar_col(cab, (["nota"], []), sh.ncols - 1)

        # Copia lógica de valores para ubicar grupos (se mantiene en sincronía)
        vals = [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]

        def es_sub(fila):
            t = " ".join(str(fila[c]) for c in range(min(3, len(fila)))).upper()
            if "TOTAL" in t:
                return True
            refv = str(fila[c_ref]).strip() if c_ref < len(fila) else ""
            return (not refv) and any(isinstance(fila[c], (int, float)) and fila[c]
                                      for c in range(len(fila)))

        # Plantillas de estilo (xf por columna), capturadas ANTES de desplazar
        def xfs_de(r):
            row = rows.get(r)
            if not row:
                return {}
            return {cx: cel.xf_idx for cx, cel in row._Row__cells.items()
                    if cel is not None and hasattr(cel, "xf_idx")}

        tpl_det = tpl_sub = tpl_tot = None
        for r in range(hr + 1, sh.nrows):
            t0 = " ".join(str(vals[r][c]) for c in range(min(3, sh.ncols))).upper().strip()
            refv = str(vals[r][c_ref]).strip() if c_ref < sh.ncols else ""
            if refv and tpl_det is None:
                tpl_det = r
            elif t0.startswith("TOTAL") and tpl_tot is None:
                tpl_tot = r
            elif (not refv) and tpl_det is not None and tpl_sub is None and es_sub(vals[r]):
                tpl_sub = r
        XF_DET = xfs_de(tpl_det if tpl_det is not None else hr + 1)
        XF_TOT = xfs_de(tpl_tot if tpl_tot is not None else hr + 1)
        XF_SUB = xfs_de(tpl_sub) if tpl_sub is not None else XF_TOT

        def desplazar(ins, n=1):
            """Baja n posiciones todas las filas >= ins (formato intacto)."""
            for r in sorted([k for k in rows.keys() if k >= ins], reverse=True):
                row = rows.pop(r)
                row._Row__idx = r + n
                for cel in row._Row__cells.values():
                    if cel is not None and hasattr(cel, "rowx"):
                        cel.rowx = r + n
                rows[r + n] = row

        def escribir(r, valores, xfs):
            """Escribe una fila nueva aplicando el xf exacto de la plantilla."""
            for cx in range(sh.ncols):
                v = valores.get(cx, "")
                if cx not in xfs and v in ("", None):
                    continue
                ws.write(r, cx, "" if v is None else v)
                fila_ws = rows.get(r)
                cel = fila_ws._Row__cells.get(cx) if fila_ws else None
                if cel is not None and cx in xfs:
                    cel.xf_idx = xfs[cx]

        def sumar_num(r, cols, delta):
            """Suma delta al número de la celda sin tocar su formato."""
            row = rows.get(r)
            if not row:
                return
            for cx in cols:
                cel = row._Row__cells.get(cx)
                if cel is not None and type(cel).__name__ == "NumberCell":
                    cel.number = round(cel.number + delta, 2)

        def fila_gt():
            for i in range(len(vals) - 1, hr, -1):
                if any(str(vals[i][c]).strip().upper() == "TOTAL"
                       for c in range(min(3, len(vals[i])))):
                    return i
            return len(vals)

        for aj in by_kind[kind]:
            concepto = aj.get("concepto", "Desembolsos")
            spec = BCE_DEL_VALOR if es_des else BCE_AL_COLS.get(concepto, BCE_DEL_VALOR)
            c_val = buscar_col(cab, spec, 10 if es_des else 20)
            val = float(aj.get("valor", 0))
            cols_suma = {c for c in (c_val, c_tot) if c is not None}

            det = {}
            if c_fecha is not None:
                det[c_fecha] = fecha
            det[c_ref] = aj.get("referencia", "")
            det[c_grp] = aj.get("acreedor", "")
            det[c_prest] = aj.get("prestamista", aj.get("acreedor", ""))
            det[c_val] = val
            if c_tot is not None:
                det[c_tot] = val
            if c_nota is not None:
                det[c_nota] = aj.get("nota", "")

            # localizar 'TOTAL <grupo>' de la cartera en vals
            grp_total = None
            for i in range(hr + 1, len(vals)):
                t0 = " ".join(str(vals[i][c]) for c in range(min(3, len(vals[i])))).upper().strip()
                if t0.startswith("TOTAL ") and len(t0) > 6 and \
                        acreedor_mef(t0[6:].strip(" -:")) == aj.get("acreedor"):
                    grp_total = i
                    break

            gt = fila_gt()
            if grp_total is not None:
                ins = grp_total
                while ins - 1 > hr and es_sub(vals[ins - 1]):
                    ins -= 1
                desplazar(ins, 1)
                escribir(ins, det, XF_DET)
                # sincronizar copia lógica
                nueva = [""] * sh.ncols
                for cx, v in det.items():
                    nueva[cx] = v
                vals.insert(ins, nueva)
                # subir subtotales del grupo (ahora +1)
                for b in range(ins + 1, grp_total + 2):
                    sumar_num(b, cols_suma, val)
                    for cx in cols_suma:
                        if isinstance(vals[b][cx], (int, float)):
                            vals[b][cx] = round(vals[b][cx] + val, 2)
            else:
                # crear bloque completo antes del TOTAL general:
                # detalle + subtotal del prestamista + TOTAL cartera
                prest = aj.get("prestamista", aj.get("acreedor", ""))
                sub = {1 if sh.ncols > 1 else 0: prest}
                tot = {1 if sh.ncols > 1 else 0: "TOTAL " + prest}
                for cx in cols_suma:
                    sub[cx] = val
                    tot[cx] = val
                desplazar(gt, 3)
                escribir(gt, det, XF_DET)
                escribir(gt + 1, sub, XF_SUB)
                escribir(gt + 2, tot, XF_TOT)
                for pos, d in ((gt, det), (gt + 1, sub), (gt + 2, tot)):
                    nueva = [""] * sh.ncols
                    for cx, v in d.items():
                        nueva[cx] = v
                    vals.insert(pos, nueva)
            algo = True

        # ---- Recalcular el TOTAL GENERAL = suma de los 'TOTAL <grupo>' ----
        # (también repara totales que en el archivo original quedaron en #REF!)
        grupos, gt_fin = [], None
        for i in range(hr + 1, len(vals)):
            t0 = " ".join(str(vals[i][c]) for c in range(min(3, len(vals[i])))).upper().strip()
            if t0 == "TOTAL":
                gt_fin = i
            elif t0.startswith("TOTAL ") and len(t0) > 6:
                grupos.append(i)
        if gt_fin is not None and grupos:
            row_g = rows.get(gt_fin)
            for cx in range(sh.ncols):
                vv = [vals[g][cx] for g in grupos
                      if cx < len(vals[g]) and isinstance(vals[g][cx], (int, float))
                      and not isinstance(vals[g][cx], bool)]
                if not vv:
                    continue
                # solo columnas donde el gran total tenía celda numérica o #REF!
                cel = row_g._Row__cells.get(cx) if row_g else None
                if cel is None or type(cel).__name__ not in ("NumberCell", "ErrorCell"):
                    continue
                xf_prev = cel.xf_idx if hasattr(cel, "xf_idx") else None
                total = round(sum(vv), 2)
                ws.write(gt_fin, cx, total)
                nuevo = rows[gt_fin]._Row__cells.get(cx)
                if nuevo is not None and xf_prev is not None:
                    nuevo.xf_idx = xf_prev
                vals[gt_fin][cx] = total

    if not algo:
        return False
    wb.save(ruta_bce)
    return True


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
            dif = round(vm - vb, 2) or 0.0  # evita -0.0 por redondeo
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
def conciliar_archivos(ruta_bce, ruta_mef):
    """Punto de entrada reutilizable (CLI y app web): devuelve las filas de
    conciliación a partir de las rutas de los dos reportes."""
    mef = leer_mef(ruta_mef)
    bce = leer_bce(ruta_bce)
    return conciliar(mef, bce)


def main():
    log = [f"📅 Conciliación BCE vs MEF — {datetime.now():%Y-%m-%d %H:%M:%S}\n"]
    filas = conciliar_archivos(ARCHIVO_BCE, ARCHIVO_MEF)

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
