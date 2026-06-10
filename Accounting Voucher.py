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

registrar("🚀 VERSIÓN CORREGIDA v2")

# 🗂️ Construir UNA sola vez el índice de carpetas destino del mes
# (tolerante al nombre: acepta "Junio", "06 Junio", "06. JUNIO", etc.)
indice_destinos = {}   # nombre normalizado -> ruta completa
for acreedor in os.listdir(base_destino):
    ruta_anio = os.path.join(base_destino, acreedor, "Pagos", anio)
    if not os.path.isdir(ruta_anio):
        continue
    for carpeta_mes in os.listdir(ruta_anio):
        cm = carpeta_mes.upper()
        if mes.upper() in cm or cm.strip().startswith(f"{mes_num:02d}"):
            ruta_mes = os.path.join(ruta_anio, carpeta_mes)
            if os.path.isdir(ruta_mes):
                for carpeta_ref in os.listdir(ruta_mes):
                    ruta_ref = os.path.join(ruta_mes, carpeta_ref)
                    if os.path.isdir(ruta_ref):
                        indice_destinos[normalizar(carpeta_ref)] = ruta_ref

if not indice_destinos:
    registrar(f"🛑 No se encontró NINGUNA carpeta de {mes} {anio} en destino. Estructura real:")
    for acreedor in os.listdir(base_destino):
        ruta_anio = os.path.join(base_destino, acreedor, "Pagos", anio)
        if os.path.isdir(ruta_anio):
            registrar(f"   {acreedor}: {os.listdir(ruta_anio)}")
        else:
            registrar(f"   {acreedor}: ⚠️ no existe Pagos\\{anio}")
else:
    registrar(f"🗂️ Carpetas destino encontradas para {mes} {anio}: {len(indice_destinos)}")

copiados, omitidos, errores = 0, 0, 0

for subcarpeta in os.listdir(carpeta_origen):
    ruta_sub = os.path.join(carpeta_origen, subcarpeta)
    if not os.path.isdir(ruta_sub):
        continue

    for archivo in os.listdir(ruta_sub):
        ruta_pdf = os.path.join(ruta_sub, archivo)

        # 🚫 Solo PDFs (ignora subcarpetas, .docx, etc.) y salta anexos
        if os.path.isdir(ruta_pdf) or not archivo.lower().endswith(".pdf"):
            continue
        if "ANEXO" in archivo.upper():
            continue

        try:
            with open(ruta_pdf, 'rb') as f:
                lector = PyPDF2.PdfReader(f)
                texto = ""
                for pagina in lector.pages:
                    texto += (pagina.extract_text() or "")
            texto = texto.replace("\n", " ").replace("- ", "-")

            if "MT202" in texto.upper():
                registrar(f"⏭️ Omitido (MT202): {archivo}")
                omitidos += 1
                continue

            # Tipo ACK
            match_ack = re.search(r"(TF|GS)-01-0*(7712600\d{3})", texto)
            if match_ack:
                prefijo_ack = match_ack.group(1)
                cod_operacion = match_ack.group(2)
                match_comp = re.search(r"Comp[:\s]*([0-9]+)", texto, re.IGNORECASE)
                num_comp = match_comp.group(1) if match_comp else cod_operacion[-3:]
            else:
                # Tipo NC
                match_nc = re.search(r"NC-01-00000007712600000\d{3}", texto)
                if not match_nc:
                    registrar(f"⚠️ No se encontró código de operación en: {archivo}")
                    omitidos += 1
                    continue
                match_comp = re.search(r"Comp[:\s]*([0-9]+)", texto, re.IGNORECASE)
                if not match_comp:
                    registrar(f"⚠️ No se encontró número de comprobante en: {archivo}")
                    omitidos += 1
                    continue
                num_comp = match_comp.group(1)
                cod_operacion = num_comp

            # Fecha
            match_fecha = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", texto)
            if match_fecha:
                fecha = datetime.strptime(match_fecha.group(1), "%d/%m/%Y")
            else:
                fecha = datetime.now()
                registrar(f"⚠️ Sin fecha en {archivo}, usando fecha de hoy")
            ABREV = {1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
                     7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"}
            fecha_formateada = f"{fecha.day:02d}{ABREV[fecha.month]}{fecha.year}"

            # Referencia préstamo
            match_ref = re.search(r"(CFA|CAF|BID|BIRF|FIDA|FLAR|FMI|BEI|KFW|AIIB|AMAZON|ECR|GPS BLUE|ICO)[\-\s]?\d+", texto, re.IGNORECASE)
            if not match_ref:
                registrar(f"❌ No se encontró referencia de préstamo en: {archivo}")
                omitidos += 1
                continue
            referencia_prestamo = match_ref.group(0).upper()
            referencia_normalizada = normalizar(referencia_prestamo)

            # Buscar en el índice (coincidencia exacta primero, luego parcial)
            carpeta_destino_final = indice_destinos.get(referencia_normalizada)
            if not carpeta_destino_final:
                candidatas = [r for n, r in indice_destinos.items() if referencia_normalizada in n]
                if len(candidatas) == 1:
                    carpeta_destino_final = candidatas[0]
                elif len(candidatas) > 1:
                    registrar(f"⚠️ Referencia ambigua {referencia_prestamo}: {[os.path.basename(c) for c in candidatas]} — omitido")
                    omitidos += 1
                    continue

            if not carpeta_destino_final:
                registrar(f"❌ No se encontró carpeta para: {referencia_prestamo} en {mes} {anio}")
                omitidos += 1
                continue

            # Guardar comprobante (sin sobrescribir)
            nuevo_nombre = f"Comprobante Contable No. 771-{num_comp} {fecha_formateada}.pdf"
            ruta_destino_comprobante = os.path.join(carpeta_destino_final, nuevo_nombre)
            if os.path.exists(ruta_destino_comprobante):
                registrar(f"⏭️ Ya existe, no se sobrescribe: {nuevo_nombre}")
                omitidos += 1
                continue
            shutil.copy(ruta_pdf, ruta_destino_comprobante)
            copiados += 1

            # Copiar ACK si aplica
            if match_ack:
                ack_encontrado = None
                for archivo_ack in os.listdir(carpeta_acks):
                    if cod_operacion in archivo_ack and archivo_ack.startswith(f"{prefijo_ack}-01") and archivo_ack.lower().endswith(".pdf"):
                        ack_encontrado = os.path.join(carpeta_acks, archivo_ack)
                        break
                if ack_encontrado:
                    shutil.copy(ack_encontrado, os.path.join(carpeta_destino_final, os.path.basename(ack_encontrado)))
                    registrar(f"✅ Copiados: {nuevo_nombre} + {os.path.basename(ack_encontrado)} → {carpeta_destino_final}")
                else:
                    registrar(f"✅ Copiado solo comprobante (ACK no hallado en {carpeta_acks}): {nuevo_nombre}")
            else:
                registrar(f"✅ Copiado comprobante tipo NC: {nuevo_nombre} → {carpeta_destino_final}")

        except Exception as e:
            registrar(f"❌ Error procesando {archivo}: {e}")
            errores += 1

registrar(f"\n📊 Resumen: {copiados} copiados | {omitidos} omitidos | {errores} errores")

ruta_log = os.path.join(os.path.expanduser("~"), "Desktop", f"log_comprobantes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
with open(ruta_log, "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print(f"\n📝 Log guardado en: {ruta_log}")
