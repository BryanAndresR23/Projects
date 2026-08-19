# -*- coding: utf-8 -*-
"""
Renombrado de Anexos — Contratos de Agencia Fiscal.

Dos modos (variable MODO):

  "explorar"   -> No renombra nada. Hace dos cosas:
                  1) Recorre las carpetas de "Contratos Agencia Fiscal" y
                     reporta CÓMO están nombrados los anexos hoy (formatos
                     reales + cuántas veces aparece cada uno + ejemplos).
                  2) Lista los archivos de la carpeta objetivo, incluyendo
                     el contenido de los comprimidos SIN extraerlos.

  "renombrar"  -> Extrae los comprimidos a una subcarpeta (el .zip original
                  NO se toca) y renombra a:
                     "Anexo 1"                 si el número es único
                     "Anexo 1 (a)", "Anexo 1 (b)", ...  si se repite

Seguridad:
  - MODO_PRUEBA = True muestra el plan sin escribir en disco.
  - Cada ejecución real deja "deshacer_renombres.csv" para revertir.
  - Los archivos sin número de anexo detectable NO se renombran: se reportan.
"""

import os
import re
import csv
import sys
import fnmatch
import zipfile
import unicodedata
from datetime import datetime

# =========================
# CONFIGURACIÓN
# =========================
carpeta_objetivo = r"Z:\GISI\SSFI\GESTIÓN PAGOS INTERNACIONALES\2026\DEUDA EXTERNA PÚBLICA\Acreedores Internacionales\GOBIERNOS\Contratos\Contratos Agencia Fiscal\KFW\KFW-BMZ-02165173-XXXXXXX-CFN"
raiz_contratos = r"Z:\GISI\SSFI\GESTIÓN PAGOS INTERNACIONALES\2026\DEUDA EXTERNA PÚBLICA\Acreedores Internacionales\GOBIERNOS\Contratos\Contratos Agencia Fiscal"

INTERACTIVO = True         # True = pregunta qué hacer al arrancar (no hay que editar el código)
MODO = "explorar"          # "explorar" | "renombrar"  (se usa si INTERACTIVO = False)
MODO_PRUEBA = True         # True = solo muestra el plan, no escribe nada
EXTRAER_COMPRIMIDOS = True # extrae los .zip a una subcarpeta y renombra ahí
RECURSIVO = True           # numera los anexos por cada subcarpeta encontrada

EXTENSIONES_VALIDAS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".msg", ".txt"
)

# Para los archivos donde el número de anexo no se puede deducir del nombre.
# Ej: ORDEN_MANUAL = {"Vertrag_KFW_final.pdf": 1, "Beilage_2.pdf": 2}
ORDEN_MANUAL = {}

log = []
log.append(f"📅 Fecha de ejecución: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
log.append(f"⚙️  MODO={MODO} | MODO_PRUEBA={MODO_PRUEBA}\n")


def registrar(msg: str):
    print(msg)
    log.append(msg)


# =========================
# UTILIDADES DE TEXTO
# =========================
ROMANOS = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

PALABRAS_ANEXO = (
    r"(?:ANEXOS?|ANEJOS?|ANNEX(?:ES)?|ANLAGEN?|BEILAGEN?|APENDICES?|APPENDI(?:X|CES)|ADJUNTOS?)"
)


def quitar_tildes(s: str) -> str:
    """'APÉNDICE' -> 'APENDICE' para que el patrón no dependa de acentos."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def romano_a_int(s: str):
    """'VIII' -> 8. Devuelve None si no es un romano válido."""
    s = s.upper()
    if not s or any(c not in ROMANOS for c in s):
        return None
    total, previo = 0, 0
    for c in reversed(s):
        valor = ROMANOS[c]
        total = total - valor if valor < previo else total + valor
        previo = max(previo, valor)
    return total if 0 < total <= 40 else None


def clave_natural(s: str):
    """Orden natural: 'Anexo 2' antes que 'Anexo 10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def letra_indice(i: int) -> str:
    """0 -> 'a', 1 -> 'b', ... 25 -> 'z', 26 -> 'aa'."""
    letras = ""
    i += 1
    while i > 0:
        i, resto = divmod(i - 1, 26)
        letras = chr(97 + resto) + letras
    return letras


# =========================
# DETECCIÓN DEL ANEXO
# =========================
# "Anexo 1", "ANEXO No. 3", "Anexo 1a", "Anexo 1 (b)", "Anexo 2.1",
# "Anlage 4", "Annex III", "Apéndice N° 5"
PATRON_ANEXO = re.compile(
    rf"{PALABRAS_ANEXO}"
    r"[\s\-_\.:]*"
    r"(?:N[°ºO]?\.?|NRO\.?|NO\.?|#)?"
    r"[\s\-_\.:]*"
    r"(?:(\d{1,2})(?:[\.\-](\d{1,2}))?|([IVXLCDM]{1,6})(?![A-Za-z]))"
    r"(?:[\s\-_\.]*\(?([A-Za-z])\)?(?![A-Za-z0-9]))?",
    re.IGNORECASE,
)


def detectar_anexo(nombre_archivo: str):
    """
    Devuelve {"numero": int, "sub": int|None, "letra": str|None} o None.
    'sub' es el decimal de "Anexo 2.1"; 'letra' el sufijo de "Anexo 1a".
    """
    base = quitar_tildes(os.path.splitext(nombre_archivo)[0])
    m = PATRON_ANEXO.search(base)
    if not m:
        return None

    arabigo, sub, romano, letra = m.groups()
    if arabigo:
        numero = int(arabigo)
    else:
        numero = romano_a_int(romano)
        if numero is None:
            return None

    return {
        "numero": numero,
        "sub": int(sub) if sub else None,
        "letra": letra.lower() if letra else None,
    }


def formato_generico(nombre_archivo: str) -> str:
    """'Anexo 12 (b).pdf' -> 'Anexo N (x)' para poder contar formatos."""
    base = os.path.splitext(nombre_archivo)[0]
    f = re.sub(r"\(\s*[A-Za-z]\s*\)", "(x)", base)
    f = re.sub(r"\d+", "N", f)
    return re.sub(r"\s+", " ", f).strip()


# =========================
# LECTURA DE COMPRIMIDOS
# =========================
def nombre_zip_corregido(info: zipfile.ZipInfo) -> str:
    """Corrige acentos de los .zip creados por el Explorador de Windows."""
    nombre = info.filename
    if info.flag_bits & 0x800:
        return nombre
    try:
        crudo = nombre.encode("cp437")
    except UnicodeEncodeError:
        return nombre
    for codificacion in ("utf-8", "cp850", "latin-1"):
        try:
            probable = crudo.decode(codificacion)
        except UnicodeDecodeError:
            continue
        if "\ufffd" not in probable:
            return probable
    return nombre


def listar_comprimidos(carpeta: str):
    for archivo in sorted(os.listdir(carpeta), key=clave_natural):
        ruta = os.path.join(carpeta, archivo)
        if os.path.isfile(ruta) and archivo.lower().endswith((".zip", ".rar", ".7z")):
            yield archivo, ruta


def simular_comprimidos(carpeta: str):
    """Muestra el plan de renombrado leyendo los .zip SIN extraerlos."""
    for archivo, ruta in listar_comprimidos(carpeta):
        registrar(f"\n📦 {archivo}")
        if not archivo.lower().endswith(".zip"):
            registrar("   ⚠️  Formato no legible desde Python; descomprímelo a mano.")
            continue
        try:
            with zipfile.ZipFile(ruta) as zf:
                entradas = [nombre_zip_corregido(i) for i in zf.infolist() if not i.is_dir()]
        except Exception as e:
            registrar(f"   ❌ No se pudo leer: {e}")
            continue

        # Cada subcarpeta del .zip se numera por separado, igual que al renombrar.
        por_carpeta = {}
        for entrada in entradas:
            entrada = entrada.replace("\\", "/")
            if not entrada.lower().endswith(EXTENSIONES_VALIDAS):
                continue
            sub, nombre = os.path.split(entrada)
            por_carpeta.setdefault(sub, []).append(nombre)

        for sub in sorted(por_carpeta):
            if sub:
                registrar(f"   └ {sub}/")
            nombres = sorted(por_carpeta[sub], key=clave_natural)
            plan, sin_numero = construir_plan_desde_nombres(nombres)
            for actual, nuevo in plan:
                registrar(f"   🧪 {actual}  →  {nuevo}")
            for pendiente in sin_numero:
                registrar(f"   ⏭️  Sin número de anexo detectable: {pendiente}")
            sin_cambio = len(nombres) - len(plan) - len(sin_numero)
            if sin_cambio:
                registrar(f"   ✔️  {sin_cambio} ya tienen el nombre correcto")


def extraer_comprimidos(carpeta: str):
    """Extrae cada .zip a una subcarpeta con su mismo nombre. No borra el .zip."""
    extraidas = []
    for archivo, ruta in listar_comprimidos(carpeta):
        destino = os.path.join(carpeta, os.path.splitext(archivo)[0])

        if not archivo.lower().endswith(".zip"):
            registrar(f"⚠️  {archivo}: formato {os.path.splitext(archivo)[1]} "
                      f"no soportado por Python. Descomprímelo a mano en «{os.path.basename(destino)}» "
                      f"y vuelve a ejecutar.")
            if os.path.isdir(destino):
                extraidas.append(destino)
            continue

        if os.path.isdir(destino):
            registrar(f"↩️  Ya extraído: {archivo} → {os.path.basename(destino)}")
            extraidas.append(destino)
            continue

        if MODO_PRUEBA:
            registrar(f"🧪 [prueba] Se extraería: {archivo} → {os.path.basename(destino)}")
            continue

        try:
            with zipfile.ZipFile(ruta) as zf:
                os.makedirs(destino, exist_ok=True)
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    nombre = nombre_zip_corregido(info).replace("\\", "/")
                    partes = [p for p in nombre.split("/") if p not in ("", ".", "..")]
                    if not partes:
                        continue
                    ruta_salida = os.path.join(destino, *partes)
                    os.makedirs(os.path.dirname(ruta_salida), exist_ok=True)
                    with zf.open(info) as origen, open(ruta_salida, "wb") as salida:
                        salida.write(origen.read())
            registrar(f"📦 Extraído: {archivo} → {os.path.basename(destino)}")
            extraidas.append(destino)
        except Exception as e:
            registrar(f"❌ Error extrayendo {archivo}: {e}")

    return extraidas


# =========================
# PLAN DE RENOMBRADO
# =========================
def archivos_de(carpeta: str):
    return sorted(
        (a for a in os.listdir(carpeta)
         if os.path.isfile(os.path.join(carpeta, a))
         and a.lower().endswith(EXTENSIONES_VALIDAS)),
        key=clave_natural,
    )


def construir_plan_desde_nombres(nombres):
    """
    Devuelve (plan, sin_numero):
      plan       -> lista de (nombre_actual, nombre_nuevo)
      sin_numero -> archivos que no se pudieron identificar
    """
    grupos, sin_numero = {}, []

    for archivo in nombres:
        if archivo in ORDEN_MANUAL:
            info = {"numero": int(ORDEN_MANUAL[archivo]), "sub": None, "letra": None}
        else:
            info = detectar_anexo(archivo)

        if info is None:
            sin_numero.append(archivo)
            continue

        grupos.setdefault(info["numero"], []).append((archivo, info))

    plan = []
    for numero in sorted(grupos):
        # Dentro del mismo número manda la letra, luego el subnúmero, luego el nombre.
        integrantes = sorted(
            grupos[numero],
            key=lambda par: (
                par[1]["letra"] or "",
                par[1]["sub"] if par[1]["sub"] is not None else 0,
                clave_natural(par[0]),
            ),
        )

        for i, (archivo, _) in enumerate(integrantes):
            extension = os.path.splitext(archivo)[1]
            if len(integrantes) == 1:
                nuevo = f"Anexo {numero}{extension}"
            else:
                nuevo = f"Anexo {numero} ({letra_indice(i)}){extension}"
            if nuevo != archivo:
                plan.append((archivo, nuevo))

    return plan, sin_numero


def construir_plan(carpeta: str):
    return construir_plan_desde_nombres(archivos_de(carpeta))


def aplicar_plan(carpeta: str, plan):
    """Renombra en dos pasos (nombre temporal) para evitar choques de nombres."""
    if not plan:
        return 0

    if MODO_PRUEBA:
        for actual, nuevo in plan:
            registrar(f"   🧪 {actual}  →  {nuevo}")
        return 0

    temporales, hechos = [], 0
    for i, (actual, nuevo) in enumerate(plan):
        temporal = f"__renombrando_{i}__{os.path.splitext(actual)[1]}"
        os.rename(os.path.join(carpeta, actual), os.path.join(carpeta, temporal))
        temporales.append((temporal, actual, nuevo))

    for temporal, actual, nuevo in temporales:
        destino = os.path.join(carpeta, nuevo)
        if os.path.exists(destino):
            base, extension = os.path.splitext(nuevo)
            nuevo = f"{base} (duplicado){extension}"
            destino = os.path.join(carpeta, nuevo)
        os.rename(os.path.join(carpeta, temporal), destino)
        registrar(f"   ✅ {actual}  →  {nuevo}")
        deshacer.append([carpeta, nuevo, actual])
        hechos += 1

    return hechos


# =========================
# EXPLORACIÓN DE LA CONVENCIÓN
# =========================
def explorar_convencion(raiz: str, tope_ejemplos: int = 4):
    """Recorre las demás carpetas de contratos y reporta los formatos reales."""
    if not os.path.isdir(raiz):
        registrar(f"❌ No se encuentra la raíz de contratos: {raiz}")
        return

    formatos, total, carpetas_vistas = {}, 0, 0

    for actual, _, archivos in os.walk(raiz):
        if os.path.normcase(actual).startswith(os.path.normcase(carpeta_objetivo)):
            continue  # la carpeta a renombrar no sirve como referencia
        carpetas_vistas += 1
        for archivo in archivos:
            if not archivo.lower().endswith(EXTENSIONES_VALIDAS):
                continue
            if detectar_anexo(archivo) is None:
                continue
            clave = formato_generico(archivo)
            registro = formatos.setdefault(clave, {"conteo": 0, "ejemplos": []})
            registro["conteo"] += 1
            if len(registro["ejemplos"]) < tope_ejemplos:
                registro["ejemplos"].append(os.path.join(os.path.basename(actual), archivo))
            total += 1

    registrar("═" * 70)
    registrar(f"🔎 CONVENCIÓN DE NOMBRES — {carpetas_vistas} carpetas revisadas, "
              f"{total} anexos encontrados")
    registrar("═" * 70)

    if not formatos:
        registrar("⚠️  No se encontró ningún archivo con nombre de anexo.")
        return

    for clave, registro in sorted(formatos.items(), key=lambda kv: -kv[1]["conteo"]):
        registrar(f"\n   [{registro['conteo']:>4}]  «{clave}»")
        for ejemplo in registro["ejemplos"]:
            registrar(f"           · {ejemplo}")


def inventario_objetivo(carpeta: str):
    """Lista lo que hay en la carpeta objetivo, incluido el interior de los .zip."""
    registrar("\n" + "═" * 70)
    registrar(f"📂 INVENTARIO — {carpeta}")
    registrar("═" * 70)

    if not os.path.isdir(carpeta):
        registrar("❌ La carpeta objetivo no existe o no hay acceso.")
        return

    sueltos = archivos_de(carpeta)
    if sueltos:
        registrar(f"\n   Archivos sueltos ({len(sueltos)}):")
        for archivo in sueltos:
            info = detectar_anexo(archivo)
            detalle = f"Anexo {info['numero']}" if info else "sin número detectado"
            registrar(f"      · {archivo}   [{detalle}]")

    for archivo, ruta in listar_comprimidos(carpeta):
        registrar(f"\n   📦 {archivo}")
        if not archivo.lower().endswith(".zip"):
            registrar("      (formato no legible desde Python; descomprímelo a mano)")
            continue
        try:
            with zipfile.ZipFile(ruta) as zf:
                entradas = [nombre_zip_corregido(i) for i in zf.infolist() if not i.is_dir()]
            for entrada in sorted(entradas, key=clave_natural):
                info = detectar_anexo(os.path.basename(entrada))
                detalle = f"Anexo {info['numero']}" if info else "sin número detectado"
                registrar(f"      · {entrada}   [{detalle}]")
        except Exception as e:
            registrar(f"      ❌ No se pudo leer: {e}")


def hay_terminal() -> bool:
    """True si el script corre en una consola capaz de leer respuestas."""
    try:
        return bool(INTERACTIVO) and sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def resolver_carpeta(ruta: str):
    """
    Devuelve la carpeta real. Si la ruta trae un comodín tipo
    "KFW-BMZ-02165173-XXXXXXX-CFN", busca la carpeta que encaje.
    """
    ruta = ruta.rstrip("\\/")
    if os.path.isdir(ruta):
        return ruta

    padre, patron = os.path.split(ruta)
    if not os.path.isdir(padre):
        registrar(f"❌ No existe la carpeta contenedora: {padre}")
        return None

    patron_glob = re.sub(r"X{2,}", "*", patron, flags=re.IGNORECASE)
    if patron_glob == patron:
        registrar(f"❌ No existe la carpeta: {ruta}")
        return None

    candidatos = [
        d for d in sorted(os.listdir(padre), key=clave_natural)
        if os.path.isdir(os.path.join(padre, d))
        and fnmatch.fnmatch(d.upper(), patron_glob.upper())
    ]

    if not candidatos:
        registrar(f"❌ Ninguna carpeta encaja con «{patron}» dentro de {padre}")
        return None

    if len(candidatos) == 1:
        elegida = os.path.join(padre, candidatos[0])
        registrar(f"📌 Carpeta objetivo resuelta: {candidatos[0]}")
        return elegida

    registrar(f"⚠️  Varias carpetas encajan con «{patron}»:")
    for i, c in enumerate(candidatos, 1):
        registrar(f"      {i}) {c}")
    if not hay_terminal():
        registrar("   Ajusta 'carpeta_objetivo' con el nombre exacto y vuelve a ejecutar.")
        return None
    try:
        eleccion = int(input("\n   Elige el número de la carpeta: ").strip())
        return os.path.join(padre, candidatos[eleccion - 1])
    except (ValueError, IndexError, EOFError, KeyboardInterrupt):
        registrar("❌ Selección no válida.")
        return None


def elegir_modo():
    """Menú de arranque: evita tener que editar el código para cambiar de modo."""
    print("\n" + "=" * 60)
    print("  RENOMBRADO DE ANEXOS — CONTRATOS DE AGENCIA FISCAL")
    print("=" * 60)
    print("  1) Explorar        — no toca nada; muestra cómo se nombran")
    print("                       los anexos en las demás carpetas y qué")
    print("                       hay dentro de los comprimidos")
    print("  2) Simular         — muestra el plan de renombrado sin aplicarlo")
    print("  3) Renombrar       — aplica los cambios (deja CSV para revertir)")
    print("  0) Salir")
    print("=" * 60)
    opciones = {"1": ("explorar", True), "2": ("renombrar", True), "3": ("renombrar", False)}
    while True:
        try:
            eleccion = input("  Opción: ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)
        if eleccion == "0":
            sys.exit(0)
        if eleccion in opciones:
            return opciones[eleccion]
        print("  ⚠️  Escribe 1, 2, 3 o 0.")

# =========================
# PROCESO PRINCIPAL
# =========================
deshacer = []

if hay_terminal():
    MODO, MODO_PRUEBA = elegir_modo()
    log.append(f"⚙️  Selección del menú: MODO={MODO} | MODO_PRUEBA={MODO_PRUEBA}")

carpeta_objetivo = resolver_carpeta(carpeta_objetivo) or carpeta_objetivo

if MODO == "explorar":
    explorar_convencion(raiz_contratos)
    inventario_objetivo(carpeta_objetivo)
    registrar("\n💡 Revisa los formatos de arriba. Si coinciden con «Anexo N» / "
              "«Anexo N (x)», cambia MODO a \"renombrar\" y ejecuta de nuevo.")

elif MODO == "renombrar":
    if not os.path.isdir(carpeta_objetivo):
        registrar(f"❌ La carpeta objetivo no existe o no hay acceso: {carpeta_objetivo}")
    else:
        carpetas = [carpeta_objetivo]
        if EXTRAER_COMPRIMIDOS:
            if MODO_PRUEBA:
                simular_comprimidos(carpeta_objetivo)
            carpetas += extraer_comprimidos(carpeta_objetivo)

        if RECURSIVO:
            adicionales = []
            for base in list(carpetas):
                for actual, _, _ in os.walk(base):
                    if actual not in carpetas and actual not in adicionales:
                        adicionales.append(actual)
            carpetas += adicionales

        renombrados, pendientes = 0, []
        for carpeta in carpetas:
            plan, sin_numero = construir_plan(carpeta)
            if not plan and not sin_numero:
                continue

            registrar(f"\n📁 {carpeta}")
            renombrados += aplicar_plan(carpeta, plan)

            for archivo in sin_numero:
                registrar(f"   ⏭️  Sin número de anexo detectable: {archivo}")
                pendientes.append(os.path.join(carpeta, archivo))

        if MODO_PRUEBA:
            registrar("\n📊 Simulación terminada: NO se cambió ningún archivo.")
            registrar("   Si el plan de arriba es correcto, vuelve a ejecutar y elige la opción 3.")
        else:
            registrar(f"\n📊 Resumen: {renombrados} renombrados | "
                      f"{len(pendientes)} sin identificar")

        if pendientes:
            registrar("\n💡 Para los pendientes, agrégalos a ORDEN_MANUAL así:")
            registrar("   ORDEN_MANUAL = {")
            for ruta in pendientes:
                registrar(f'       "{os.path.basename(ruta)}": 0,')
            registrar("   }")

        if deshacer:
            ruta_deshacer = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "deshacer_renombres.csv")
            with open(ruta_deshacer, "w", newline="", encoding="utf-8-sig") as f:
                escritor = csv.writer(f, delimiter=";")
                escritor.writerow(["carpeta", "nombre_actual", "nombre_original"])
                escritor.writerows(deshacer)
            registrar(f"↩️  Para revertir: {ruta_deshacer}")

else:
    registrar(f"❌ MODO no válido: {MODO}. Usa \"explorar\" o \"renombrar\".")

carpeta_script = os.path.dirname(os.path.abspath(__file__))
ruta_log = os.path.join(carpeta_script, "log_anexos.txt")
with open(ruta_log, "w", encoding="utf-8") as f:
    f.write("\n".join(log))

print(f"\n📝 Log guardado en: {ruta_log}")
