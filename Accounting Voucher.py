import os
import shutil
import re
import PyPDF2
from datetime import datetime

# ⚙️ Configuración (un solo lugar para cambiar el mes)
anio = "2026"
mes_num = 6  # 👈 cambia SOLO este número cada mes
MESES = {1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
         7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"}
mes = MESES[mes_num]

carpeta_origen = r"Z:\GISI\SSFI\SWIFT\Carpeta Ingresadores\BRYAN"
carpeta_acks = rf"Z:\DSBI\dsbi_swift\PDF_DEUDA\{anio}\{mes_num:02d}"
base_destino = r"Z:\GISI\SSFI\GESTIÓN PAGOS INTERNACIONALES\2026\DEUDA EXTERNA PÚBLICA\Acreedores Internacionales"

log = []
log.append(f"📅 Fecha de ejecución: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

def registrar(msg):
    print(msg)
    log.append(msg)

def normalizar(texto):
    # Quita espacios, guiones y puntos; trata CFA y CAF como equivalentes
    t = re.sub(r"[\s\-\.]", "", texto.upper())
    return t.replace("CFA", "CAF")

registrar("🚀 VERSIÓN v3 — match por REF./NO./PRESTAMO")

# ──────────────────────────────────────────────────────────────────
# 🗂️ ÍNDICE DE CARPETAS DESTINO
# Recorre cada acreedor (AIIB, BID, CAF, ...) y registra toda carpeta
# de préstamo cuyo nombre contenga un código de 8 dígitos, p. ej.:
#   "BID-1761-OC-EC - 20268000 - Municipio ..."
# Busca en: acreedor\, acreedor\Pagos\2026\ y acreedor\Pagos\2026\<mes>\
# (las del mes tienen prioridad si un código aparece repetido)
# ──────────────────────────────────────────────────────────────────
indice_codigo = {}   # "20268000" -> ruta de la carpeta de préstamo
indice_nombre = {}   # nombre normalizado de la carpeta -> ruta (fallback)

def indexar_carpetas(ruta):
    if not os.path.isdir(ruta):
        return
    for nombre in os.listdir(ruta):
        ruta_carpeta = os.path.join(ruta, nombre)
        if not os.path.isdir(ruta_carpeta):
            continue
        codigos = re.findall(r"\b(\d{8})\b", nombre)
        if codigos:
            for cod in codigos:
                indice_codigo[cod] = ruta_carpeta
            indice_nombre[normalizar(nombre)] = ruta_carpeta

for acreedor in os.listdir(base_destino):
    ruta_acreedor = os.path.join(base_destino, acreedor)
    if not os.path.isdir(ruta_acreedor):
        continue
    indexar_carpetas(ruta_acreedor)
    ruta_pagos_anio = os.path.join(ruta_acreedor, "Pagos", anio)
    indexar_carpetas(ruta_pagos_anio)
    if os.path.isdir(ruta_pagos_anio):
        for carpeta_mes in os.listdir(ruta_pagos_anio):
            cm = carpeta_mes.upper().strip()
            if mes.upper() in cm or cm.startswith(f"{mes_num:02d}"):
                indexar_carpetas(os.path.join(ruta_pagos_anio, carpeta_mes))

if not indice_codigo:
    registrar("🛑 No se encontró ninguna carpeta de préstamo con código de 8 dígitos.")
    registrar("   Estructura encontrada por acreedor:")
    for acreedor in os.listdir(base_destino):
        ruta_acreedor = os.path.join(base_destino, acreedor)
        if os.path.isdir(ruta_acreedor):
            registrar(f"   {acreedor}: {os.listdir(ruta_acreedor)[:8]}")
else:
    registrar(f"🗂️ Carpetas de préstamo indexadas: {len(indice_codigo)} códigos")

copiados, omitidos, errores = 0, 0, 0

# ──────────────────────────────────────────────────────────────────
# 📄 PROCESAR CADA SUBCARPETA DE ORIGEN (2026-771-682, 2026-771-695, ...)
# En cada una: identificar el comprobante contable (771-NNN...-signed.pdf),
# leerlo, extraer Comp, fecha y REF./NO./PRESTAMO, y copiarlo renombrado
# a la carpeta de préstamo. Luego copiar el ACK desde carpeta_acks.
# ──────────────────────────────────────────────────────────────────
for subcarpeta in os.listdir(carpeta_origen):
    ruta_sub = os.path.join(carpeta_origen, subcarpeta)
    if not os.path.isdir(ruta_sub):
        continue

    # Códigos de operación TF/GS según los archivos de la subcarpeta
    # (p. ej. "TF-01-7712600437-signed.pdf" -> TF, 7712600437)
    codigos_ack = set()
    for archivo in os.listdir(ruta_sub):
        m = re.match(r"(TF|GS)-01-0*(7712600\d{3})", archivo, re.IGNORECASE)
        if m:
            codigos_ack.add((m.group(1).upper(), m.group(2)))

    for archivo in os.listdir(ruta_sub):
        ruta_pdf = os.path.join(ruta_sub, archivo)
        nombre_up = archivo.upper()

        # Solo el comprobante contable: PDF que no sea anexo ni mensaje TF/GS
        if os.path.isdir(ruta_pdf) or not archivo.lower().endswith(".pdf"):
            continue
        if "ANEXO" in nombre_up or nombre_up.startswith(("TF-", "GS-")):
            continue

        try:
            with open(ruta_pdf, 'rb') as f:
                lector = PyPDF2.PdfReader(f)
                texto = ""
                for pagina in lector.pages:
                    texto += (pagina.extract_text() or "")
            texto = texto.replace("\n", " ")

            if "MT202" in texto.upper():
                registrar(f"⏭️ Omitido (MT202): {archivo}")
                omitidos += 1
                continue

            # Número de comprobante: "Comp:695"
            match_comp = re.search(r"Comp[:\s\.]*(\d+)", texto, re.IGNORECASE)
            if not match_comp:
                match_comp = re.search(r"771[\-\s](\d{3})", archivo)
            if not match_comp:
                registrar(f"⚠️ Sin número de comprobante en: {archivo}")
                omitidos += 1
                continue
            num_comp = match_comp.group(1)

            # Fecha del comprobante (la del encabezado, p. ej. 05/06/2026)
            match_fecha = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", texto)
            if match_fecha:
                fecha = datetime.strptime(match_fecha.group(1), "%d/%m/%Y")
            else:
                fecha = datetime.now()
                registrar(f"⚠️ Sin fecha en {archivo}, usando fecha de hoy")
            ABREV = {1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
                     7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"}
            fecha_formateada = f"{fecha.day:02d}{ABREV[fecha.month]}{fecha.year}"

            # 🔑 Código de préstamo: campo "REF./NO./PRESTAMO: 20823000"
            match_refnum = re.search(r"REF[\.\s/]*NO[\.\s/]*PRESTAMO[:\s]*(\d{8})", texto, re.IGNORECASE)
            codigo_prestamo = match_refnum.group(1) if match_refnum else None

            # Referencia textual (CFA-8759, BID-1761...) como respaldo
            match_ref = re.search(r"(CFA|CAF|BID|BIRF|FIDA|FLAR|FMI|BEI|KFW|AIIB|AMAZON|ECR|GPS BLUE|ICO)[\-\s]?\d+", texto, re.IGNORECASE)
            referencia_txt = match_ref.group(0).upper() if match_ref else None

            # Buscar carpeta destino: primero por código exacto, luego por referencia
            carpeta_destino_final = None
            if codigo_prestamo and codigo_prestamo in indice_codigo:
                carpeta_destino_final = indice_codigo[codigo_prestamo]
            elif referencia_txt:
                ref_norm = normalizar(referencia_txt)
                candidatas = [r for n, r in indice_nombre.items() if ref_norm in n]
                if len(candidatas) == 1:
                    carpeta_destino_final = candidatas[0]
                elif len(candidatas) > 1:
                    registrar(f"⚠️ Referencia ambigua {referencia_txt} en {archivo}: "
                              f"{[os.path.basename(c) for c in candidatas]} — omitido")
                    omitidos += 1
                    continue

            if not carpeta_destino_final:
                registrar(f"❌ Sin carpeta destino para {archivo} "
                          f"(código: {codigo_prestamo or 'no hallado'}, ref: {referencia_txt or 'no hallada'})")
                omitidos += 1
                continue

            # Copiar comprobante renombrado (sin sobrescribir)
            nuevo_nombre = f"Comprobante Contable No. 771-{num_comp} {fecha_formateada}.pdf"
            ruta_destino_comprobante = os.path.join(carpeta_destino_final, nuevo_nombre)
            if os.path.exists(ruta_destino_comprobante):
                registrar(f"⏭️ Ya existe, no se sobrescribe: {nuevo_nombre}")
                omitidos += 1
                continue
            shutil.copy(ruta_pdf, ruta_destino_comprobante)
            copiados += 1

            # Copiar ACK(s) desde carpeta_acks según los códigos TF/GS de la subcarpeta
            acks_copiados = []
            if codigos_ack and os.path.isdir(carpeta_acks):
                for archivo_ack in os.listdir(carpeta_acks):
                    if not archivo_ack.lower().endswith(".pdf"):
                        continue
                    for prefijo, cod in codigos_ack:
                        if cod in archivo_ack and archivo_ack.upper().startswith(f"{prefijo}-01"):
                            destino_ack = os.path.join(carpeta_destino_final, archivo_ack)
                            if not os.path.exists(destino_ack):
                                shutil.copy(os.path.join(carpeta_acks, archivo_ack), destino_ack)
                                acks_copiados.append(archivo_ack)
                            break

            if acks_copiados:
                registrar(f"✅ {subcarpeta}: {nuevo_nombre} + ACK {acks_copiados} → {carpeta_destino_final}")
            elif codigos_ack:
                registrar(f"✅ {subcarpeta}: {nuevo_nombre} (ACK no hallado en {carpeta_acks}) → {carpeta_destino_final}")
            else:
                registrar(f"✅ {subcarpeta}: {nuevo_nombre} → {carpeta_destino_final}")

        except Exception as e:
            registrar(f"❌ Error procesando {archivo}: {e}")
            errores += 1

registrar(f"\n📊 Resumen: {copiados} copiados | {omitidos} omitidos | {errores} errores")

ruta_log = os.path.join(os.path.expanduser("~"), "Desktop", f"log_comprobantes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
with open(ruta_log, "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print(f"\n📝 Log guardado en: {ruta_log}")
