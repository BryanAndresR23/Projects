import os
import shutil
import re
import PyPDF2
from datetime import datetime

# =========================
# CONFIGURACIÓN
# =========================
carpeta_origen = r"Z:\GISI\SSFI\SWIFT\Carpeta Ingresadores\BRYAN"
carpeta_acks = r"Z:\DSBI\dsbi_swift\PDF_DEUDA\2026\06"
base_destino = r"Z:\GISI\SSFI\GESTIÓN PAGOS INTERNACIONALES\2026\DEUDA EXTERNA PÚBLICA\Acreedores Internacionales"

anio = "2026"
mes = "Junio"

log = []
log.append(f"📅 Fecha de ejecución: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


# =========================
# FUNCIONES
# =========================
def es_mensaje_swift(nombre_archivo: str) -> bool:
    """
    True si el archivo es un mensaje SWIFT TF/GS (no debe copiarse desde origen).
    Cubre nombres con o sin prefijo numérico:
      "TF-01-7712600437-signed.pdf"
      "684 TF-01-7712600436-signed-signed.pdf"
    """
    return bool(re.search(r"\b(TF|GS)-01-", nombre_archivo, re.IGNORECASE))


def leer_pdf(ruta_pdf: str) -> str:
    """Lee texto de un PDF (si es escaneado/protegido puede venir vacío)."""
    with open(ruta_pdf, "rb") as f:
        lector = PyPDF2.PdfReader(f)
        texto = ""
        for pagina in lector.pages:
            texto += (pagina.extract_text() or "")
    return texto.replace("\n", " ").replace("- ", "-")


def normalizar(s: str) -> str:
    """Normaliza strings para comparar (quita espacios, guiones, puntos y /)."""
    return re.sub(r"[\s\-/\.]", "", s.upper())


def extraer_fecha_formateada(texto: str) -> str:
    m = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", texto)
    if m:
        fecha_str = m.group(1)
        return datetime.strptime(fecha_str, "%d/%m/%Y").strftime("%d%b%Y")
    return datetime.now().strftime("%d%b%Y")


def extraer_codigo_prestamo(texto: str) -> str | None:
    """
    Código del campo "REF./NO./PRESTAMO: 20460000" del comprobante.
    Es el mismo código de 8 dígitos que llevan las carpetas destino:
      "BID-5858-GN-EC - 20460000 - CONAFIPS"
    """
    m = re.search(r"REF[\.\s/]*NO[\.\s/]*PRESTAMO[:\s]*([0-9]{7,9})", texto, re.IGNORECASE)
    return m.group(1) if m else None


def extraer_referencia(texto: str) -> str | None:
    """
    - BID en cualquier variante: BID-2487-OC-EC, BID-5858-GN-EC,
      BID-3188-CH-EC, BID-1923-BL-OC-EC... (se identifica por el número)
    - Otros: CAF-12375, CFA-11634, BIRF-8978, etc.
    """
    m_bid = re.search(r"\bBID[-\s]*([0-9]{3,6})\b", texto, re.IGNORECASE)
    if m_bid:
        return f"BID-{m_bid.group(1)}"

    m = re.search(
        r"\b(CFA|CAF|BIRF|FIDA|FLAR|FMI|BEI|KFW|AIIB|AMAZON|ECR|GPS\s*BLUE|ICO)\b[\-\s]*([0-9]{3,10})",
        texto,
        flags=re.IGNORECASE
    )
    if not m:
        return None

    codigo = m.group(1).upper().replace(" ", "")
    numero = m.group(2)
    return f"{codigo}-{numero}"


def extraer_operacion_tf_gs(texto: str):
    """
    Extrae prefijo (TF/GS) y cod_operacion.
    Ej: TF-01-7712600172 -> ("TF", "7712600172")
    """
    m = re.search(r"\b(TF|GS)-01-([0-9]{6,15})\b", texto, re.IGNORECASE)
    if not m:
        return None, None
    return m.group(1).upper(), m.group(2)


def extraer_num_comp(texto: str, nombre_archivo: str, cod_operacion: str | None) -> str:
    """
    Prioridad:
      1) Comp: ####
      2) 771-### en nombre
      3) últimos 3 de cod_operacion
    """
    m = re.search(r"\bComp[:\s]*([0-9]{1,6})\b", texto, re.IGNORECASE)
    if m:
        return m.group(1)

    m = re.search(r"\b771[-_ ]?([0-9]{1,6})\b", nombre_archivo, re.IGNORECASE)
    if m:
        return m.group(1)

    if cod_operacion and len(cod_operacion) >= 3:
        return cod_operacion[-3:]

    return "SINCOMP"


def carpetas_mes():
    """
    Genera todas las carpetas de préstamo del mes en:
      A) ...\\Acreedor\\Pagos\\2026\\Junio\\CARPETA
      B) ...\\Acreedor\\Pagos\\Junio\\CARPETA      <- estructura real vista
    """
    for acreedor in os.listdir(base_destino):
        ruta_A = os.path.join(base_destino, acreedor, "Pagos", anio, mes)
        ruta_B = os.path.join(base_destino, acreedor, "Pagos", mes)
        for ruta_base in (ruta_A, ruta_B):
            if not os.path.isdir(ruta_base):
                continue
            for carpeta_ref in os.listdir(ruta_base):
                ruta_carpeta = os.path.join(ruta_base, carpeta_ref)
                if os.path.isdir(ruta_carpeta):
                    yield carpeta_ref, ruta_carpeta


def buscar_carpeta_destino(referencia: str | None, codigo_prestamo: str | None) -> str | None:
    """
    1) Match por código de 8 dígitos (REF./NO./PRESTAMO) — exacto y universal.
    2) Si no, match por referencia (BID-5858, CAF-12375...) exigiendo que
       después del número no venga otro dígito (BID-585 no matchea BID-5858).
    """
    if codigo_prestamo:
        for carpeta_ref, ruta_carpeta in carpetas_mes():
            if re.search(rf"(?<!\d){codigo_prestamo}(?!\d)", carpeta_ref):
                return ruta_carpeta

    if referencia:
        ref_norm = normalizar(referencia)
        for carpeta_ref, ruta_carpeta in carpetas_mes():
            carp_norm = normalizar(carpeta_ref)
            if re.search(rf"{re.escape(ref_norm)}(?!\d)", carp_norm):
                return ruta_carpeta

    return None


# =========================
# PROCESO PRINCIPAL
# =========================
copiados, omitidos, errores = 0, 0, 0

for subcarpeta in os.listdir(carpeta_origen):
    ruta_sub = os.path.join(carpeta_origen, subcarpeta)
    if not os.path.isdir(ruta_sub):
        continue

    # Código TF/GS tomado de los NOMBRES de los archivos de la subcarpeta
    prefijo_sub, cod_sub = None, None
    for a in os.listdir(ruta_sub):
        m = re.search(r"\b(TF|GS)-01-([0-9]{6,15})\b", a, re.IGNORECASE)
        if m:
            prefijo_sub, cod_sub = m.group(1).upper(), m.group(2)
            break

    for archivo in os.listdir(ruta_sub):
        archivo_lower = archivo.lower()
        ruta_pdf = os.path.join(ruta_sub, archivo)

        # ✅ Solo PDFs (evita docx, subcarpetas y otros)
        if os.path.isdir(ruta_pdf) or not archivo_lower.endswith(".pdf"):
            continue

        # ✅ Omitir anexos
        if "anexo" in archivo_lower:
            continue

        # ✅ Omitir mensajes SWIFT TF/GS aunque tengan prefijo numérico
        if es_mensaje_swift(archivo):
            continue

        try:
            texto = leer_pdf(ruta_pdf)
            if not texto.strip():
                msg = f"⏭️ Omitido (PDF sin texto): {archivo}"
                print(msg)
                log.append(msg)
                omitidos += 1
                continue

            # Identificadores del préstamo
            codigo_prestamo = extraer_codigo_prestamo(texto)   # ej. 20460000
            referencia = extraer_referencia(texto)             # ej. BID-5858

            if not codigo_prestamo and not referencia:
                msg = f"❌ Sin código ni referencia de préstamo en: {archivo}"
                print(msg)
                log.append(msg)
                omitidos += 1
                continue

            # Carpeta destino
            carpeta_destino_final = buscar_carpeta_destino(referencia, codigo_prestamo)
            if not carpeta_destino_final:
                msg = (f"❌ Sin carpeta destino para {archivo} "
                       f"(código: {codigo_prestamo or '—'}, ref: {referencia or '—'}) en {mes}")
                print(msg)
                log.append(msg)
                omitidos += 1
                continue

            # Código de operación: del texto o de los archivos TF/GS hermanos
            prefijo_ack, cod_operacion = extraer_operacion_tf_gs(texto)
            if not cod_operacion and cod_sub:
                prefijo_ack, cod_operacion = prefijo_sub, cod_sub

            # Fecha y número de comprobante
            fecha_formateada = extraer_fecha_formateada(texto)
            num_comp = extraer_num_comp(texto, archivo, cod_operacion)

            # Guardar comprobante
            nuevo_nombre = f"Comprobante Contable No. 771-{num_comp} {fecha_formateada}.pdf"
            ruta_destino_comprobante = os.path.join(carpeta_destino_final, nuevo_nombre)

            if os.path.exists(ruta_destino_comprobante):
                estado_comp = "ya existía"
            else:
                shutil.copy(ruta_pdf, ruta_destino_comprobante)
                estado_comp = "copiado"
                copiados += 1

            # Buscar el ACK en carpeta_acks por TF/GS-01-cod_operacion
            ack_copiado = None
            if cod_operacion and os.path.isdir(carpeta_acks):
                for ack_file in os.listdir(carpeta_acks):
                    if not ack_file.lower().endswith(".pdf"):
                        continue
                    if cod_operacion in ack_file and (ack_file.startswith("TF-01") or ack_file.startswith("GS-01")):
                        ack_path = os.path.join(carpeta_acks, ack_file)
                        ruta_destino_ack = os.path.join(carpeta_destino_final, ack_file)
                        if not os.path.exists(ruta_destino_ack):
                            shutil.copy(ack_path, ruta_destino_ack)
                        ack_copiado = ack_file
                        break

            if ack_copiado:
                msg = f"✅ {nuevo_nombre} ({estado_comp}) + {ack_copiado} → {carpeta_destino_final}"
            else:
                msg = f"✅ {nuevo_nombre} ({estado_comp}, sin ACK encontrado) → {carpeta_destino_final}"

            print(msg)
            log.append("─" * 60)
            log.append(msg)

        except Exception as e:
            msg = f"❌ Error procesando {archivo}: {e}"
            print(msg)
            log.append("─" * 60)
            log.append(msg)
            errores += 1

resumen = f"\n📊 Resumen: {copiados} copiados | {omitidos} omitidos | {errores} errores"
print(resumen)
log.append(resumen)

# Guardar log
ruta_log = os.path.join(os.getcwd(), "log.txt")
with open(ruta_log, "w", encoding="utf-8") as f:
    f.write("\n".join(log))

print(f"\n📝 Log guardado en: {ruta_log}")
