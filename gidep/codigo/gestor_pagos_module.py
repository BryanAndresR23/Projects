# -*- coding: utf-8 -*-
"""Modulo Flask para automatizar la preparacion de pagos y anexos Quipux.

Se integra con conciliacion_app.py mediante register_gestor_pagos_routes().
"""
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from flask import jsonify, request

try:
    import PyPDF2
except ImportError:  # pragma: no cover
    PyPDF2 = None


FRASE_EXACTA_FORMULARIO = "FORMULARIO PARA SOLICITAR TRANSFERENCIAS AL EXTERIOR"
MESES_ESPANOL = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]
MESES_ABREV = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

DEFAULT_CONFIG = {
    "base_origen": "Z:\\GISI\\SSFI\\GESTIÓN PAGOS INTERNACIONALES\\2025\\DEUDA EXTERNA PÚBLICA\\Acreedores Internacionales",
    "ruta_raiz_destino": "Z:\\GISI\\SSFI\\GESTIÓN PAGOS INTERNACIONALES",
    "carpeta_puente": str(Path.home() / "Desktop" / "Puente Pagos Quipux"),
    "anio_busqueda": 2025,
}

RESULT_CACHE = {}
CONFIG_PATH = None


@dataclass(frozen=True)
class OpcionesPago:
    mes_siguiente: bool = False
    solo_estructura: bool = False
    agencia_fiscal: bool = False


def corregir_ruta_red(ruta):
    if not ruta:
        return ruta
    candidatos = [
        ruta,
        ruta.replace("GESTION PAGOS INTERNACIONALES", "GESTIÓN PAGOS INTERNACIONALES")
        .replace("DEUDA EXTERNA PUBLICA", "DEUDA EXTERNA PÚBLICA"),
        ruta.replace("GESTIÃ“N PAGOS INTERNACIONALES", "GESTIÓN PAGOS INTERNACIONALES")
        .replace("DEUDA EXTERNA PÃšBLICA", "DEUDA EXTERNA PÚBLICA"),
        ruta.replace("GESTIÃƒâ€œN PAGOS INTERNACIONALES", "GESTIÓN PAGOS INTERNACIONALES")
        .replace("DEUDA EXTERNA PÃƒÅ¡BLICA", "DEUDA EXTERNA PÚBLICA"),
    ]
    for candidato in candidatos:
        if os.path.exists(candidato):
            return candidato
    return candidatos[1]


def normalizar_configuracion(config):
    config = dict(config or {})
    config["base_origen"] = corregir_ruta_red(config.get("base_origen", ""))
    config["ruta_raiz_destino"] = corregir_ruta_red(config.get("ruta_raiz_destino", ""))
    config.setdefault("carpeta_puente", DEFAULT_CONFIG["carpeta_puente"])
    config.setdefault("anio_busqueda", DEFAULT_CONFIG["anio_busqueda"])
    return config


def cargar_configuracion():
    if CONFIG_PATH is None:
        return normalizar_configuracion(DEFAULT_CONFIG)
    if not CONFIG_PATH.exists():
        return guardar_configuracion(DEFAULT_CONFIG)
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as file:
            config = json.load(file)
    except (OSError, json.JSONDecodeError):
        config = {}
    resultado = DEFAULT_CONFIG.copy()
    resultado.update({key: value for key, value in config.items() if value})
    return normalizar_configuracion(resultado)


def guardar_configuracion(config):
    config = normalizar_configuracion(config)
    if CONFIG_PATH is not None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_PATH.open("w", encoding="utf-8") as file:
            json.dump(config, file, indent=2, ensure_ascii=False)
    return config


def obtener_mes_y_anio_destino(mes_siguiente=False):
    fecha_actual = datetime.now()
    mes_idx = fecha_actual.month - 1
    anio_destino = fecha_actual.year
    if mes_siguiente:
        mes_idx += 1
        if mes_idx > 11:
            mes_idx = 0
            anio_destino += 1
    return MESES_ESPANOL[mes_idx], anio_destino


def construir_ruta_destino(config, cartera, carpeta_sigade, mes_siguiente=False):
    config = normalizar_configuracion(config)
    mes_destino, anio_destino = obtener_mes_y_anio_destino(mes_siguiente)
    return os.path.join(
        config["ruta_raiz_destino"], str(anio_destino), "DEUDA EXTERNA PÚBLICA",
        "Acreedores Internacionales", cartera, "Pagos", mes_destino, carpeta_sigade,
    )


def filtro_dinamico_copia(opciones):
    def _filtro(directorio, contenidos):
        ignorados = []
        if opciones.agencia_fiscal:
            for elemento in contenidos:
                ruta_completa = os.path.join(directorio, elemento)
                if not os.path.isdir(ruta_completa) and not elemento.lower().endswith((".xls", ".xlsx", ".xlsm")):
                    ignorados.append(elemento)
        elif opciones.solo_estructura:
            for elemento in contenidos:
                ruta_completa = os.path.join(directorio, elemento)
                if not os.path.isdir(ruta_completa):
                    ignorados.append(elemento)
        else:
            for elemento in contenidos:
                if "MT202" in elemento.upper():
                    ignorados.append(elemento)
        return ignorados
    return _filtro


def extraer_texto_pdf(ruta_pdf, paginas=2):
    if PyPDF2 is None:
        raise RuntimeError("Falta instalar PyPDF2. Ejecuta: python -m pip install PyPDF2")
    texto = ""
    with open(ruta_pdf, "rb") as file:
        lector = PyPDF2.PdfReader(file)
        for pagina in lector.pages[:paginas]:
            texto += pagina.extract_text() or ""
    return texto


def validar_contingente_agencia(carpeta_puente):
    if not os.path.exists(carpeta_puente):
        return False
    for archivo in os.listdir(carpeta_puente):
        if not archivo.lower().endswith(".pdf"):
            continue
        try:
            if FRASE_EXACTA_FORMULARIO in extraer_texto_pdf(os.path.join(carpeta_puente, archivo)).upper():
                return True
        except Exception:
            continue
    return False


def actualizar_nombre_excel(ruta_destino):
    hoy = datetime.now()
    nueva_fecha = f"{hoy.day:02d}{MESES_ABREV[hoy.month - 1]}{hoy.year}"
    cambios = []
    for root, _dirs, files in os.walk(ruta_destino):
        for file in files:
            if not file.lower().endswith((".xls", ".xlsx", ".xlsm")):
                continue
            nuevo_nombre = re.sub(r"\d{2}[A-Za-z]{3}\d{4}", nueva_fecha, file)
            if nuevo_nombre != file:
                os.rename(os.path.join(root, file), os.path.join(root, nuevo_nombre))
                cambios.append(nuevo_nombre)
    return cambios


def procesar_carpeta_puente(carpeta_puente, ruta_destino_final, opciones):
    os.makedirs(carpeta_puente, exist_ok=True)
    archivos_pdf = [file for file in os.listdir(carpeta_puente) if file.lower().endswith(".pdf")]
    procesados = []
    for archivo in archivos_pdf:
        ruta_pdf_origen = os.path.join(carpeta_puente, archivo)
        try:
            texto = extraer_texto_pdf(ruta_pdf_origen)
        except Exception:
            texto = ""
        if opciones.agencia_fiscal and FRASE_EXACTA_FORMULARIO in texto.upper():
            nuevo_nombre = "Formulario Transferencia al Exterior.pdf"
        else:
            match_oficio = re.search(r"Oficio\s*(?:Nro|No)[\.\s]*([A-Za-z0-9\-]+)", texto, re.IGNORECASE)
            nuevo_nombre = f"Oficio No. {match_oficio.group(1).upper()}.pdf" if match_oficio else "Estado Cuenta.pdf"
        ruta_final = os.path.join(ruta_destino_final, nuevo_nombre)
        contador = 1
        while os.path.exists(ruta_final):
            nombre_sin_ext = nuevo_nombre.removesuffix(".pdf")
            ruta_final = os.path.join(ruta_destino_final, f"{nombre_sin_ext} ({contador}).pdf")
            contador += 1
        shutil.move(ruta_pdf_origen, ruta_final)
        procesados.append(os.path.basename(ruta_final))
    return procesados


def buscar_coincidencias(codigo_sigade, config, mes_siguiente=False):
    config = normalizar_configuracion(config)
    base_origen = config["base_origen"]
    anio_busqueda = int(config.get("anio_busqueda", 2025))
    resultados = []
    if not os.path.exists(base_origen):
        raise FileNotFoundError(f"No se puede acceder a: {base_origen}")
    for cartera in os.listdir(base_origen):
        ruta_cartera = os.path.join(base_origen, cartera)
        if not os.path.isdir(ruta_cartera):
            continue
        for categoria in os.listdir(ruta_cartera):
            ruta_categoria = os.path.join(ruta_cartera, categoria)
            if not os.path.isdir(ruta_categoria):
                continue
            ruta_base_meses = os.path.join(ruta_categoria, str(anio_busqueda))
            if not os.path.exists(ruta_base_meses):
                ruta_base_meses = ruta_categoria
            if not os.path.exists(ruta_base_meses):
                continue
            for mes_historico in os.listdir(ruta_base_meses):
                ruta_mes = os.path.join(ruta_base_meses, mes_historico)
                if not os.path.isdir(ruta_mes):
                    continue
                for carpeta_sigade in os.listdir(ruta_mes):
                    if codigo_sigade not in carpeta_sigade.upper():
                        continue
                    item_id = f"r{len(resultados) + 1}"
                    ruta_origen = os.path.join(ruta_mes, carpeta_sigade)
                    resultados.append({
                        "id": item_id,
                        "cartera": cartera,
                        "categoria": categoria,
                        "mes_historico": mes_historico,
                        "carpeta_sigade": carpeta_sigade,
                        "ruta_origen": ruta_origen,
                        "ruta_destino": construir_ruta_destino(config, cartera, carpeta_sigade, mes_siguiente),
                        "texto": f"[{cartera}] - {categoria} ({mes_historico}) -> {carpeta_sigade}",
                    })
    return resultados


def procesar_pago(item, config, opciones):
    config = normalizar_configuracion(config)
    ruta_origen = item["ruta_origen"]
    ruta_destino = construir_ruta_destino(config, item["cartera"], item["carpeta_sigade"], opciones.mes_siguiente)
    if os.path.exists(ruta_destino):
        raise FileExistsError(f"La carpeta ya existe y no se sobrescribira: {ruta_destino}")
    eventos = [f"Origen: {ruta_origen}", f"Destino: {ruta_destino}"]
    if opciones.agencia_fiscal:
        eventos.append("Validando formulario de Agencia Fiscal.")
        if not validar_contingente_agencia(config["carpeta_puente"]):
            raise ValueError("No se detecto el formulario para solicitar transferencias al exterior en la carpeta puente Quipux.")
    os.makedirs(os.path.dirname(ruta_destino), exist_ok=True)
    shutil.copytree(ruta_origen, ruta_destino, ignore=filtro_dinamico_copia(opciones))
    eventos.append("Estructura copiada correctamente.")
    excels = []
    if opciones.agencia_fiscal:
        excels = actualizar_nombre_excel(ruta_destino)
        eventos.append(f"Excels renombrados: {len(excels)}.")
    anexos = procesar_carpeta_puente(config["carpeta_puente"], ruta_destino, opciones)
    eventos.append(f"Anexos integrados desde Quipux: {len(anexos)}.")
    return {"ruta_destino": ruta_destino, "eventos": eventos, "excels": excels, "anexos": anexos}


def register_gestor_pagos_routes(app, base_dir):
    global CONFIG_PATH
    CONFIG_PATH = Path(base_dir) / "gestor_pagos_config.json"

    @app.get("/api/gestor_pagos/config")
    def gestor_pagos_config():
        return jsonify({"ok": True, "config": cargar_configuracion()})

    @app.post("/api/gestor_pagos/config")
    def gestor_pagos_guardar_config():
        data = request.get_json(force=True)
        config = cargar_configuracion()
        for key in ("base_origen", "ruta_raiz_destino", "carpeta_puente", "anio_busqueda"):
            if key in data:
                config[key] = data[key]
        try:
            config["anio_busqueda"] = int(config["anio_busqueda"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "El anio de busqueda debe ser numerico."}), 400
        return jsonify({"ok": True, "config": guardar_configuracion(config)})

    @app.post("/api/gestor_pagos/search")
    def gestor_pagos_search():
        data = request.get_json(force=True)
        codigo = (data.get("codigo") or "").strip().upper()
        if not codigo:
            return jsonify({"ok": False, "error": "Ingresa el codigo SIGADE o referencia."}), 400
        try:
            resultados = buscar_coincidencias(codigo, cargar_configuracion(), bool(data.get("mes_siguiente")))
        except FileNotFoundError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Error al buscar: {exc}"}), 500
        RESULT_CACHE.clear()
        for item in resultados:
            RESULT_CACHE[item["id"]] = item
        return jsonify({"ok": True, "resultados": resultados, "total": len(resultados)})

    @app.post("/api/gestor_pagos/preview")
    def gestor_pagos_preview():
        data = request.get_json(force=True)
        item = RESULT_CACHE.get(data.get("id"))
        if not item:
            return jsonify({"ok": False, "error": "Seleccion invalida. Realiza la busqueda nuevamente."}), 400
        ruta_destino = construir_ruta_destino(cargar_configuracion(), item["cartera"], item["carpeta_sigade"], bool(data.get("mes_siguiente")))
        item["ruta_destino"] = ruta_destino
        return jsonify({"ok": True, "ruta_destino": ruta_destino})

    @app.post("/api/gestor_pagos/process")
    def gestor_pagos_process():
        data = request.get_json(force=True)
        item = RESULT_CACHE.get(data.get("id"))
        if not item:
            return jsonify({"ok": False, "error": "Seleccion invalida. Realiza la busqueda nuevamente."}), 400
        raw = data.get("opciones") or {}
        opciones = OpcionesPago(
            mes_siguiente=bool(raw.get("mes_siguiente")),
            solo_estructura=bool(raw.get("solo_estructura")),
            agencia_fiscal=bool(raw.get("agencia_fiscal")),
        )
        try:
            resultado = procesar_pago(item, cargar_configuracion(), opciones)
        except FileExistsError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Ocurrio un error critico durante el flujo: {exc}"}), 500
        return jsonify({"ok": True, **resultado})
