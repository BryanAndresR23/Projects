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
    """Normaliza strings para comparar (quita espacios, guiones y /)."""
    return re.sub(r"[\s\-/]", "", s.upper())


def extraer_fecha_formateada(texto: str) -> str:
    m = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", texto)
    if m:
        fecha_str = m.group(1)
        return datetime.strptime(fecha_str, "%d/%m/%Y").strftime("%d%b%Y")
    return datetime.now().strftime("%d%b%Y")


def extraer_referencia(texto: str) -> str | None:
    """
    - BID: BID-2487-OC-EC
    - Otros: CAF-12375, CFA-11634, BIRF-8978, etc.
    """
    m_bid = re.search(r"\bBID[-\s]*([0-9]{3,6})[-\s]*/?\s*OC[-\s]*EC\b", texto, re.IGNORECASE)
    if m_bid:
        return f"BID-{m_bid.group(1)}-OC-EC"

    m = re.search(
        r"\b(CFA|CAF|BIRF|FIDA|FLAR|FMI|BEI|KFW|AIIB|AMAZON|ECR|GPS\s*BLUE|ICO|ECR-04)\b[\-\s]*([0-9]{3,10})(?:\s*(EC))?",
        texto,
        flags=re.IGNORECASE
    )
    if not m:
        return None

    codigo = m.group(1).upper().replace(" ", "")
    numero = m.group(2)
    ec = m.group(3)
    return f"{codigo}-{numero}" + (" EC" if ec else "")


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


def buscar_carpeta_destino(referencia: str) -> str | None:
    """
    Busca carpeta en:
      A) ...\\Acreedor\\Pagos\\2026\\Junio\\CARPETA
      B) ...\\Acreedor\\Pagos\\Junio\\CARPETA
    """
    ref_norm = normalizar(referencia)

    alternativas = {ref_norm}
    if referencia.upper().startswith("BID-"):
        num = re.sub(r"[^0-9]", "", referencia)
        if num:
            alternativas.add(normalizar(f"BID-{num}"))

    for acreedor in os.listdir(base_destino):
        ruta_A = os.path.join(base_destino, acreedor, "Pagos", anio, mes)
        ruta_B = os.path.join(base_destino, acreedor, "Pagos", mes)

        for ruta_base in (ruta_A, ruta_B):
            if not os.path.isdir(ruta_base):
                continue

            for carpeta_ref in os.listdir(ruta_base):
                carp_norm = normalizar(carpeta_ref)
                if any(a in carp_norm for a in alternativas):
                    return os.path.join(ruta_base, carpeta_ref)

    return None


# =========================
# PROCESO PRINCIPAL
# =========================
for subcarpeta in os.listdir(carpeta_origen):
    ruta_sub = os.path.join(carpeta_origen, subcarpeta)
    if not os.path.isdir(ruta_sub):
        continue

    # Código TF/GS tomado de los NOMBRES de los archivos de la subcarpeta
    # (sirve para buscar el ACK aunque el comprobante no lo mencione en su texto)
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
        # ("684 TF-01-7712600436-signed.pdf" también queda excluido)
        if es_mensaje_swift(archivo):
            continue

        try:
            texto = leer_pdf(ruta_pdf)
            if not texto.strip():
                msg = f"⏭️ Omitido (PDF sin texto): {archivo}"
                print(msg)
                log.append(msg)
                continue

            # Referencia de préstamo (sirve para todos)
            referencia = extraer_referencia(texto)
            if not referencia:
                msg = f"❌ No se encontró referencia de préstamo en: {archivo}"
                print(msg)
                log.append(msg)
                continue

            # Carpeta destino (con año o sin año)
            carpeta_destino_final = buscar_carpeta_destino(referencia)
            if not carpeta_destino_final:
                msg = f"❌ No se encontró carpeta que contenga: {referencia} en {mes} {anio}"
                print(msg)
                log.append(msg)
                continue

            # Código de operación: primero del texto del comprobante;
            # si no aparece, usar el de los archivos TF/GS hermanos
            prefijo_ack, cod_operacion = extraer_operacion_tf_gs(texto)
            if not cod_operacion and cod_sub:
                prefijo_ack, cod_operacion = prefijo_sub, cod_sub

            # Fecha para el nombre del comprobante
            fecha_formateada = extraer_fecha_formateada(texto)

            # Número comprobante
            num_comp = extraer_num_comp(texto, archivo, cod_operacion)

            # Guardar comprobante
            nuevo_nombre = f"Comprobante Contable No. 771-{num_comp} {fecha_formateada}.pdf"
            ruta_destino_comprobante = os.path.join(carpeta_destino_final, nuevo_nombre)

            if not os.path.exists(ruta_destino_comprobante):
                shutil.copy(ruta_pdf, ruta_destino_comprobante)

            # Buscar el ACK en carpeta_acks por TF/GS-01-cod_operacion
            ack_copiado = None
            if cod_operacion:
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
                msg = f"✅ Copiados: {nuevo_nombre} + {ack_copiado} → {carpeta_destino_final}"
            else:
                msg = f"✅ Copiado comprobante ({referencia}): {nuevo_nombre} (sin ACK encontrado) → {carpeta_destino_final}"

            print(msg)
            log.append("─" * 60)
            log.append(msg)

        except Exception as e:
            msg = f"❌ Error procesando {archivo}: {e}"
            print(msg)
            log.append("─" * 60)
            log.append(msg)

# Guardar log
ruta_log = os.path.join(os.getcwd(), "log.txt")
with open(ruta_log, "w", encoding="utf-8") as f:
    f.write("\n".join(log))

print(f"\n📝 Log guardado en: {ruta_log}")
