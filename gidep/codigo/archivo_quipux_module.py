from __future__ import annotations

"""Bandeja automática para respaldos de pagos descargados desde Quipux.

El módulo vigila una carpeta local, reconoce los documentos operativos de un
pago, encuentra la carpeta mensual del préstamo y copia el PDF con el nombre
institucional. Los originales se conservan y ningún destino se sobrescribe.
"""

import hashlib
import json
import logging
import os
import re
import sys
import threading
import unicodedata
from collections import Counter
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from flask import jsonify, request

import comprobantes_contables_engine as engine


DEFAULT_SOURCE = Path(r"C:\Users\bromo\Downloads\Deuda Quipux")
DEFAULT_DESTINATION = engine.DEFAULT_DESTINATION
CONFIG_FILENAME = "archivo_quipux_config.json"
STATE_FILENAME = "archivo_quipux_state.json"
MONITOR_INTERVAL_SECONDS = 60
STABLE_AGE_SECONDS = 8
BATCH_WINDOW_SECONDS = 10 * 60
SUPPORTED_TYPES = {"OFICIO", "MEMORANDO", "ESTADO CUENTA", "FORMULARIO"}
AUTO_MATCH_SCORE = 99
ANALYSIS_VERSION = "2026-08-27-birf-ibrd-auto-v2"

_base_dir: Path | None = None
_logger: logging.Logger | None = None
_scan_lock = threading.Lock()
_worker_lock = threading.Lock()
_wakeup = threading.Event()
_worker: threading.Thread | None = None
_runtime: dict[str, Any] = {
    "last_scan": None,
    "last_error": None,
    "monitor_status": "INICIANDO",
}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.upper()
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", value).split())


def default_config() -> dict[str, Any]:
    return {
        "source": str(DEFAULT_SOURCE),
        "destination": str(DEFAULT_DESTINATION),
        "use_ocr": True,
        "automatic": True,
    }


def _config_path(base_dir: str | Path) -> Path:
    return Path(base_dir) / CONFIG_FILENAME


def _state_path(base_dir: str | Path) -> Path:
    return Path(base_dir) / STATE_FILENAME


def load_config(base_dir: str | Path) -> dict[str, Any]:
    config = default_config()
    try:
        raw = json.loads(_config_path(base_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raw = {}
    if isinstance(raw, dict):
        for key in config:
            if key in raw:
                config[key] = raw[key]
    config["source"] = str(config.get("source") or DEFAULT_SOURCE).strip()
    config["destination"] = str(
        config.get("destination") or DEFAULT_DESTINATION
    ).strip()
    config["use_ocr"] = bool(config.get("use_ocr", True))
    config["automatic"] = bool(config.get("automatic", True))
    return config


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def save_config(base_dir: str | Path, updates: dict[str, Any]) -> dict[str, Any]:
    config = load_config(base_dir)
    for key in ("source", "destination"):
        if key in updates:
            value = str(updates.get(key) or "").strip()
            if not value:
                raise ValueError(f"La ruta {key} no puede quedar vacía.")
            config[key] = value
    for key in ("use_ocr", "automatic"):
        if key in updates:
            config[key] = bool(updates[key])
    _atomic_json_write(_config_path(base_dir), config)
    _wakeup.set()
    return config


def _empty_state() -> dict[str, Any]:
    return {
        "version": 1,
        "initialized_at": _now_iso(),
        "baseline_sources": [],
        "records": {},
    }


def load_state(base_dir: str | Path) -> dict[str, Any]:
    try:
        state = json.loads(_state_path(base_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        state = _empty_state()
    if not isinstance(state, dict):
        state = _empty_state()
    state.setdefault("version", 1)
    state.setdefault("initialized_at", _now_iso())
    state.setdefault("baseline_sources", [])
    state.setdefault("records", {})
    return state


def save_state(base_dir: str | Path, state: dict[str, Any]) -> None:
    _atomic_json_write(_state_path(base_dir), state)


def configure_logging(base_dir: str | Path) -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    directory = Path(base_dir) / "logs_archivo_quipux"
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("gidep.archivo_quipux")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(
            directory / "actividad.log",
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        )
        logger.addHandler(handler)
    _logger = logger
    return logger


def file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pdfs(source: Path) -> list[Path]:
    if not source.is_dir():
        raise FileNotFoundError(f"No se encuentra la bandeja Quipux: {source}")
    return sorted(
        (
            path
            for path in source.iterdir()
            if path.is_file()
            and path.suffix.casefold() == ".pdf"
            and not path.name.startswith(".")
        ),
        key=lambda path: (path.stat().st_ctime, path.name.casefold()),
    )


def initialize_baseline(base_dir: str | Path, source: Path) -> dict[str, Any]:
    """Protege los archivos existentes la primera vez que se vigila una ruta."""
    state = load_state(base_dir)
    source_key = str(source.resolve() if source.exists() else source).casefold()
    if source_key in state["baseline_sources"]:
        return state
    if source.is_dir():
        for path in _pdfs(source):
            try:
                if path.stat().st_size <= 0:
                    continue
                fingerprint = file_fingerprint(path)
            except OSError:
                continue
            record = state["records"].setdefault(
                fingerprint,
                {
                    "record_id": fingerprint[:16],
                    "fingerprint": fingerprint,
                    "source_name": path.name,
                    "source_path": str(path),
                    "source_aliases": [],
                    "size": path.stat().st_size,
                    "downloaded_at": datetime.fromtimestamp(
                        path.stat().st_ctime
                    ).astimezone().isoformat(timespec="seconds"),
                    "processed_at": _now_iso(),
                    "document_type": "HISTÓRICO",
                    "status": "HISTÓRICO PROTEGIDO",
                    "score": 0,
                    "evidence": ["Existía antes de activar el monitor"],
                    "notes": [
                        "No se procesó automáticamente para proteger el archivo histórico."
                    ],
                    "baseline": True,
                },
            )
            if str(path) != record.get("source_path"):
                aliases = record.setdefault("source_aliases", [])
                if str(path) not in aliases:
                    aliases.append(str(path))
        state["baseline_sources"].append(source_key)
        refresh_package_statuses(state)
        save_state(base_dir, state)
    return state


def classify_document(path: Path, text: str) -> str:
    name = _normalized(path.stem)
    head = _normalized(text[:3000])
    if re.search(r"\b(F SSFI 017|FORMULARIO|GIRO)\b", name) and re.search(
        r"\b(EXTERIOR|TRANSFERENCIA|GIRO)\b", name
    ):
        return "FORMULARIO"
    if re.search(r"\b(ESTADO CUENTA|ESTADO DE CUENTA|PAYMENT REQUEST|ORDEN DE PAGO)\b", name):
        return "ESTADO CUENTA"
    if re.search(r"\b(IBRD|BIRF|WORLD BANK)\b", name) and re.search(
        r"\b(STATEMENT|ACCOUNT|AMOUNTS DUE|PAYABLE|LOAN)\b", head
    ):
        return "ESTADO CUENTA"
    if re.search(r"\bMEMORANDO\b", head[:500]) or re.search(
        r"^(BCE )?[A-Z0-9]+ [A-Z0-9]+ 20\d{2} \d+ (M|TEMP)\b", name
    ):
        return "MEMORANDO"
    if re.search(r"\bOFICIO (NRO|NO)\b", head[:700]) or re.match(
        r"^(OF|OFICIO)\b", name
    ):
        return "OFICIO"
    if "ESTADO DE CUENTA" in head or "ACCOUNT STATEMENT" in head:
        return "ESTADO CUENTA"
    if "STATEMENT OF ACCOUNT" in head or "BILL FOR IBRD LOAN" in head:
        return "ESTADO CUENTA"
    if "AMOUNTS DUE" in head and re.search(r"\b(IBRD|BIRF)\s+(?:LOAN\s+)?\d{4,7}\b", head):
        return "ESTADO CUENTA"
    if (
        ("FORMULARIO" in head or "F SSFI 017" in head)
        and ("TRANSFERENCIA AL EXTERIOR" in head or "GIRO AL EXTERIOR" in head)
    ):
        return "FORMULARIO"
    return "NO RECONOCIDO"

def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        clean = re.sub(r"\s*[-–]\s*", "-", value.strip(" .,:;()[]"))
        if clean and clean not in result:
            result.append(clean)
    return result


def extract_office_references(text: str, path: Path) -> list[str]:
    flat = " ".join(text.split())
    patterns = [
        r"(?:OFICIO|MEMORANDO)\s+(?:NRO\.?|NO\.?)\s*[:#]?\s*([A-Z0-9][A-Z0-9.\-/]{5,})",
        r"\b((?:MDEP|MEF|BCE|CFN|CONAFIPS|GAD[A-Z0-9]*|PCG|EMAPAG)[A-Z0-9.\-/]*-20\d{2}-[A-Z0-9.\-/]+-[A-Z]{1,5})\b",
    ]
    values: list[str] = []
    for pattern in patterns:
        values.extend(re.findall(pattern, flat, flags=re.IGNORECASE))
    stem = re.sub(r"(?i)^(?:of|oficio)[-_ ]+", "", path.stem)
    copy_suffix = re.match(r"^(.*-\d{3,})-(\d{1,2})$", stem)
    if copy_suffix:
        stem = copy_suffix.group(1)
    if re.search(r"20\d{2}", stem) and len(stem) >= 12:
        values.append(stem)
    return [value.upper() for value in _unique(values)]


def extract_loan_references(text: str, path: Path) -> list[str]:
    corpus = f"{path.stem}\n{text}"
    values: list[str] = []
    # Formato frecuente de BEI: "Prestamo FI 84689 - BEI". El OCR puede
    # confundir FI con FL o F1, por lo que se normaliza al nombre de carpeta.
    for number, creditor in re.findall(
        r"PR[ÉE]STAMO\s+F(?:I|L|1)\s*(\d{4,7})\s*[-–]?\s*(BEI)\b",
        corpus,
        flags=re.IGNORECASE,
    ):
        values.append(f"{creditor.upper()}-{number}")
    # El Banco Mundial suele escribir IBRD Loan 95550; en las carpetas locales
    # se archiva como BIRF-9555. Se conservan ambas variantes cuando aplica.
    for number in re.findall(
        r"\b(?:IBRD|BIRF)\s+(?:LOAN\s+)?0*(\d{4,7})\b",
        corpus,
        flags=re.IGNORECASE,
    ):
        if len(number) >= 5 and number.endswith("0"):
            values.append(f"BIRF-{number[:-1]}")
        values.append(f"BIRF-{number}")
    patterns = [
        r"\b((?:CFA|CAF|BID|BIRF|IBRD|BEI|AFD|KFW|JICA|FONPLATA|AIIB)[- ]+[A-Z0-9][A-Z0-9./-]{2,})\b",
        r"\b(L\d{3,5}[A-Z])\b",
    ]
    for pattern in patterns:
        values.extend(re.findall(pattern, corpus, flags=re.IGNORECASE))
    cleaned = []
    for value in _unique(values):
        value = re.sub(r"\s+", "-", value.upper())
        value = re.sub(r"-+(?=-)", "", value)
        value = re.sub(r"^IBRD-", "BIRF-", value)
        value = re.sub(r"^BIRF-LOAN-", "BIRF-", value)
        if value.startswith("BIRF-"):
            match = re.match(r"^BIRF-(\d{5,7})$", value)
            if match and match.group(1).endswith("0"):
                cleaned.append(f"BIRF-{match.group(1)[:-1]}")
        # Evita falsos prestamos como "BEI-PAGO" producidos al leer la frase
        # "capital - BEI Pago de intereses".
        if re.search(r"\d{3,}", value):
            cleaned.append(value)
    return _unique(cleaned)

def extract_sigade_numbers(text: str, path: Path) -> list[str]:
    corpus = f"{path.stem}\n{text}"
    explicit = re.findall(
        r"(?:SIGADE|PRESTAMO|PRÉSTAMO|REF\.?/NO\.?/PRESTAMO)\s*[:#-]?\s*(\d{7,9})",
        corpus,
        flags=re.IGNORECASE,
    )
    generic = re.findall(r"(?<!\d)((?:20|23|28)\d{6})(?!\d)", corpus)
    return _unique([*explicit, *generic])


MONTHS = {
    "ENERO": 1,
    "FEBRERO": 2,
    "MARZO": 3,
    "ABRIL": 4,
    "MAYO": 5,
    "JUNIO": 6,
    "JULIO": 7,
    "AGOSTO": 8,
    "SEPTIEMBRE": 9,
    "OCTUBRE": 10,
    "NOVIEMBRE": 11,
    "DICIEMBRE": 12,
}


def extract_value_date(text: str) -> datetime | None:
    flat = " ".join(text.split())
    english_months = {
        "JANUARY": 1,
        "FEBRUARY": 2,
        "MARCH": 3,
        "APRIL": 4,
        "MAY": 5,
        "JUNE": 6,
        "JULY": 7,
        "AUGUST": 8,
        "SEPTEMBER": 9,
        "OCTOBER": 10,
        "NOVEMBER": 11,
        "DECEMBER": 12,
    }
    for match in re.finditer(
        r"\b(?:PAYABLE|DUE|DUE DATE|PAYMENT DATE)\s+"
        r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)"
        r"\s+(\d{1,2}),?\s+(20\d{2})\b",
        flat,
        flags=re.IGNORECASE,
    ):
        month = english_months.get(match.group(1).upper())
        if month:
            try:
                return datetime(int(match.group(3)), month, int(match.group(2)))
            except ValueError:
                pass
    patterns = [
        r"(?:VALOR\s+QUE\s+DEBER[ÁA]?\s+SER\s+PAGADO|DEBER[ÁA]?\s+SER\s+PAGADO|PAGAR)\s+(?:EL\s+)?D[IÍ]A\s+(\d{1,2}[./-]\d{1,2}[./-]20\d{2})",
        r"(?:FECHA\s+(?:VALOR|DE\s+D[ÉE]BITO)|VALUE\s+DATE)\s*[:#]?\s*(\d{1,2}[./-]\d{1,2}[./-]20\d{2})",
        r"(?:FECHA\s+(?:VALOR|DE\s+D[ÉE]BITO))\s*[:#]?\s*(\d{1,2}\s+DE\s+[A-ZÁÉÍÓÚÑ]+\s+DE\s+20\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, flat, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1)
        numeric = re.match(r"(\d{1,2})[./-](\d{1,2})[./-](20\d{2})", value)
        if numeric:
            try:
                return datetime(
                    int(numeric.group(3)), int(numeric.group(2)), int(numeric.group(1))
                )
            except ValueError:
                continue
        written = re.match(
            r"(\d{1,2})\s+DE\s+([A-ZÁÉÍÓÚÑ]+)\s+DE\s+(20\d{2})",
            value,
            flags=re.IGNORECASE,
        )
        if written:
            month = MONTHS.get(_normalized(written.group(2)))
            if month:
                try:
                    return datetime(int(written.group(3)), month, int(written.group(1)))
                except ValueError:
                    continue
    return None

def public_debt_payment_evidence(
    path: Path, text: str, document_type: str, metadata: dict[str, Any] | None = None
) -> list[str]:
    """Exige evidencia de deuda publica y de una operacion real de pago."""
    normalized = _normalized(f"{path.stem}\n{text}")
    metadata = metadata or {}
    evidence: list[str] = []
    public_debt_mentioned = bool(
        "DEUDA EXTERNA PUBLICA" in normalized
        or re.search(
            r"\bDEUDA\s+EXTERNA\s+P\s*U?\s*B\s*L\s*I?\s*C\s*A\b",
            normalized,
        )
    )
    debt_context = bool(
        public_debt_mentioned
        or "SERVICIO DE LA DEUDA EXTERNA" in normalized
        or "OBLIGACIONES DEL SERVICIO DE LA DEUDA EXTERNA" in normalized
        or "PRESUPUESTO GENERAL DEL ESTADO" in normalized
        or "MIN ECONOMIA CUENTA CORRIENTE UNICA" in normalized
    )
    world_bank_statement = bool(
        "INTERNATIONAL BANK FOR RECONSTRUCTION AND DEVELOPMENT" in normalized
        or "IBRD LOAN" in normalized
        or "BILL FOR IBRD LOAN" in normalized
        or "STATEMENT OF ACCOUNT FOR IBRD" in normalized
    )
    if public_debt_mentioned:
        evidence.append("mención explícita Deuda Externa Pública")
    elif debt_context:
        evidence.append("contexto de deuda externa pública")
    elif world_bank_statement:
        evidence.append("estado de cuenta BIRF/IBRD")
    payment_terms = (
        "PAGO DE INTERESES",
        "PAGO DE INTERES",
        "PAGO DE CAPITAL",
        "INTERESES Y CAPITAL",
        "TRANSFERENCIA AL EXTERIOR",
        "GIRO AL EXTERIOR",
        "TABLA DE AMORTIZACION",
        "VALOR QUE DEBERA SER PAGADO",
        "FECHA VALOR",
        "FECHA DE DEBITO",
        "AMOUNTS DUE",
        "PAYABLE",
        "PAYMENT INSTRUCTIONS",
        "PLEASE ARRANGE FOR THE ABOVE TO BE REMITTED",
    )
    if any(term in normalized for term in payment_terms):
        evidence.append("instrucción o soporte de pago")
    if metadata.get("loan_references") or metadata.get("sigade_numbers"):
        evidence.append("préstamo o SIGADE identificado")
    if metadata.get("value_date"):
        evidence.append("fecha valor identificada")
    if metadata.get("amounts"):
        evidence.append("monto identificado")
    if document_type in {"OFICIO", "MEMORANDO"}:
        has_context = any(
            item in evidence
            for item in (
                "mención explícita Deuda Externa Pública",
                "contexto de deuda externa pública",
            )
        )
        has_payment = "instrucción o soporte de pago" in evidence
        has_reference = "préstamo o SIGADE identificado" in evidence
        return evidence if has_context and has_payment and has_reference else []
    if document_type in {"ESTADO CUENTA", "FORMULARIO"}:
        has_payment = "instrucción o soporte de pago" in evidence
        has_reference = "préstamo o SIGADE identificado" in evidence
        has_statement = any(
            item in evidence
            for item in (
                "mención explícita Deuda Externa Pública",
                "contexto de deuda externa pública",
                "estado de cuenta BIRF/IBRD",
            )
        )
        return evidence if has_statement and has_payment and has_reference else []
    return []

def extract_metadata(path: Path, text: str, ocr_used: bool) -> dict[str, Any]:
    value_date = extract_value_date(text)
    fields = engine.extract_payment_fields(text)
    if value_date is None:
        value_date = fields.get("value_date")
    amounts = list(engine.canonical_amounts(text))
    currency_match = re.search(r"\b(USD|EUR|GBP|JPY|CHF|CNY|CAD)\b", text)
    document_type = classify_document(path, text)
    metadata = {
        "document_type": document_type,
        "office_references": extract_office_references(text, path),
        "loan_references": extract_loan_references(text, path),
        "sigade_numbers": extract_sigade_numbers(text, path),
        "value_date": value_date,
        "amounts": amounts[:8],
        "currency": currency_match.group(1).upper() if currency_match else "",
        "ocr_used": ocr_used,
        "text_length": len(text.strip()),
    }
    evidence = public_debt_payment_evidence(path, text, document_type, metadata)
    metadata["public_debt_payment"] = bool(evidence)
    metadata["public_debt_evidence"] = evidence
    return metadata


def _folder_file_names(folder: Path) -> str:
    try:
        return "\n".join(path.name for path in folder.iterdir() if path.is_file())
    except OSError:
        return ""


def _duplicate_family(path: Path) -> str:
    return _normalized(re.sub(r"\s*(?:-\s*COPY|\(\d+\))\s*$", "", path.name, flags=re.I))


def _choose_original(folders: list[Path]) -> Path | None:
    if not folders:
        return None
    if len({_duplicate_family(folder) for folder in folders}) != 1:
        return None
    return min(
        folders,
        key=lambda folder: (
            bool(re.search(r"(?:-\s*COPY|\(\d+\))\s*$", folder.name, re.I)),
            folder.name.casefold(),
        ),
    )


def _month_destination_from_template(
    template: Path, value_date: datetime
) -> Path | None:
    month_parent = template.parent
    pagos_parent = month_parent.parent
    if _normalized(pagos_parent.name) != "PAGOS":
        return None
    target_month = pagos_parent / engine.MONTH_NAMES[value_date.month]
    return target_month / template.name


def _historical_reference_templates(
    destination_root: Path, references: list[str]
) -> list[Path]:
    if not references:
        return []
    matches: list[Path] = []
    try:
        walker = os.walk(destination_root)
        for root, directories, _files in walker:
            current = Path(root)
            parts = [_normalized(part) for part in current.parts]
            if len(parts) >= 2 and parts[-2] == "PAGOS":
                for name in directories:
                    folder = current / name
                    if any(engine.contains_phrase(folder.name, reference) for reference in references):
                        matches.append(folder)
                directories[:] = []
    except OSError:
        return []
    return matches


def _unique_reference_template(
    destination_root: Path, references: list[str]
) -> Path | None:
    templates = _historical_reference_templates(destination_root, references)
    if not templates:
        return None
    families = {_duplicate_family(folder) for folder in templates}
    if len(families) != 1:
        return None
    return sorted(
        templates,
        key=lambda folder: (
            str(folder.parent.parent).casefold(),
            _normalized(folder.name),
            str(folder).casefold(),
        ),
    )[0]


def find_destination(
    metadata: dict[str, Any], destination_root: Path, fallback_date: datetime
) -> dict[str, Any]:
    value_date = metadata.get("value_date") or fallback_date
    try:
        folders = list(
            engine.indexed_month_directories(
                destination_root, value_date.year, value_date.month
            )
        )
    except OSError as exc:
        return {
            "destination": None,
            "score": 0,
            "method": "Archivo no disponible",
            "evidence": [],
            "notes": [str(exc)],
        }
    references = [
        *metadata.get("loan_references", []),
        *metadata.get("sigade_numbers", []),
    ]
    exact = [
        folder
        for folder in folders
        if any(engine.contains_phrase(folder.name, reference) for reference in references)
    ]
    if len(exact) == 1:
        matched = next(
            reference
            for reference in references
            if engine.contains_phrase(exact[0].name, reference)
        )
        return {
            "destination": exact[0],
            "score": 100,
            "method": "Préstamo o SIGADE exacto",
            "evidence": [f"referencia exacta {matched}"],
            "notes": [],
        }
    if len(exact) > 1:
        office_exact = []
        for folder in exact:
            names = _folder_file_names(folder)
            if any(
                engine.contains_phrase(names, office)
                for office in metadata.get("office_references", [])
            ):
                office_exact.append(folder)
        if len(office_exact) == 1:
            return {
                "destination": office_exact[0],
                "score": 100,
                "method": "Préstamo y oficio exactos",
                "evidence": ["préstamo exacto", "oficio exacto"],
                "notes": [],
            }
        preferred = _choose_original(office_exact or exact)
        if preferred and metadata.get("value_date"):
            return {
                "destination": preferred,
                "score": 99,
                "method": "Carpeta repetida resuelta por fecha",
                "evidence": ["referencia exacta", f"fecha {value_date:%d/%m/%Y}"],
                "notes": ["Se prefirió la carpeta original frente a COPY o numeradas."],
            }
        return {
            "destination": None,
            "score": 0,
            "method": "Coincidencia ambigua",
            "evidence": [f"{len(exact)} carpetas con la misma referencia"],
            "notes": ["Requiere seleccionar la carpeta correcta."],
            "candidate_paths": [str(folder) for folder in exact[:8]],
        }
    template = _unique_reference_template(destination_root, references)
    monthly_destination = (
        _month_destination_from_template(template, value_date) if template else None
    )
    if monthly_destination:
        matched = next(
            (
                reference
                for reference in references
                if engine.contains_phrase(template.name, reference)
            ),
            references[0] if references else "",
        )
        return {
            "destination": monthly_destination,
            "score": 99,
            "method": "Carpeta mensual creada por referencia histórica",
            "evidence": [
                f"referencia exacta {matched}",
                f"plantilla {template.parent.name}/{template.name}",
                f"fecha {value_date:%d/%m/%Y}",
            ],
            "notes": [
                "No existía la carpeta del mes; se crea con el nombre del expediente ya existente."
            ],
        }
    offices = metadata.get("office_references", [])
    office_matches = [
        folder
        for folder in folders
        if any(
            engine.contains_phrase(_folder_file_names(folder), office)
            for office in offices
        )
    ]
    if len(office_matches) == 1:
        return {
            "destination": office_matches[0],
            "score": 99,
            "method": "Número de oficio exacto",
            "evidence": [f"oficio exacto {offices[0]}"],
            "notes": [],
        }
    return {
        "destination": None,
        "score": 0,
        "method": "Sin coincidencia segura",
        "evidence": [],
        "notes": [
            f"No se encontró una carpeta única de {engine.MONTH_NAMES[value_date.month]}."
        ],
    }

def target_name(document_type: str, metadata: dict[str, Any]) -> str:
    office = (metadata.get("office_references") or [""])[0]
    if document_type == "OFICIO":
        return f"Oficio No. {office}.pdf" if office else "Oficio SN.pdf"
    if document_type == "MEMORANDO":
        return f"Memorando No. {office}.pdf" if office else "Memorando SN.pdf"
    if document_type == "ESTADO CUENTA":
        return "Estado Cuenta.pdf"
    if document_type == "FORMULARIO":
        return "Formulario Transferencia al Exterior.pdf"
    return ""


def _download_datetime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_ctime).astimezone()


def _analyze_path(path: Path, destination: Path, use_ocr: bool) -> dict[str, Any]:
    try:
        text, ocr_used = engine.searchable_pdf_text(path, use_ocr=use_ocr)
    except Exception as exc:
        return {
            "path": path,
            "metadata": {
                "document_type": "ERROR",
                "office_references": [],
                "loan_references": [],
                "sigade_numbers": [],
                "value_date": None,
                "amounts": [],
                "currency": "",
                "ocr_used": False,
                "text_length": 0,
            },
            "match": {
                "destination": None,
                "score": 0,
                "method": "Error de lectura",
                "evidence": [],
                "notes": [f"{type(exc).__name__}: {exc}"],
            },
        }
    metadata = extract_metadata(path, text, ocr_used)
    if metadata["document_type"] not in SUPPORTED_TYPES:
        match = {
            "destination": None,
            "score": 0,
            "method": "Documento ajeno al expediente",
            "evidence": [],
            "notes": [
                "No se reconoció como oficio, estado de cuenta o formulario de transferencia."
            ],
        }
    elif metadata.get("public_debt_payment"):
        match = find_destination(metadata, destination, _download_datetime(path))
    elif metadata["document_type"] in {"ESTADO CUENTA", "FORMULARIO"}:
        match = {
            "destination": None,
            "score": 0,
            "method": "Pendiente de validar con oficio",
            "evidence": [],
            "notes": [
                "Solo se aceptará si el mismo lote contiene un oficio de Deuda Externa Pública con destino exacto."
            ],
        }
    else:
        match = {
            "destination": None,
            "score": 0,
            "method": "No corresponde a Deuda Externa Pública",
            "evidence": [],
            "notes": [
                "No contiene simultáneamente la mención Deuda Externa Pública y una instrucción de pago."
            ],
        }
    return {"path": path, "metadata": metadata, "match": match}


def _cluster(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(items, key=lambda item: item["path"].stat().st_ctime)
    clusters: list[list[dict[str, Any]]] = []
    for item in ordered:
        if not clusters:
            clusters.append([item])
            continue
        previous = clusters[-1][-1]["path"].stat().st_ctime
        current = item["path"].stat().st_ctime
        if current - previous <= BATCH_WINDOW_SECONDS:
            clusters[-1].append(item)
        else:
            clusters.append([item])
    return clusters


def apply_batch_context(items: list[dict[str, Any]]) -> None:
    """Asocia soportes genéricos únicamente a un oficio exacto del mismo lote."""
    for cluster in _cluster(items):
        anchors = [
            item
            for item in cluster
            if item["metadata"]["document_type"] in {"OFICIO", "MEMORANDO"}
            and item["match"].get("destination")
            and item["match"].get("score", 0) >= AUTO_MATCH_SCORE
        ]
        destinations = {str(item["match"]["destination"]) for item in anchors}
        types = Counter(item["metadata"]["document_type"] for item in cluster)
        if len(anchors) != 1 or len(destinations) != 1:
            continue
        if any(types[kind] > 1 for kind in ("OFICIO", "MEMORANDO", "ESTADO CUENTA", "FORMULARIO")):
            continue
        anchor = anchors[0]
        for item in cluster:
            if item is anchor or item["match"].get("destination"):
                continue
            if item["metadata"]["document_type"] not in {"ESTADO CUENTA", "FORMULARIO"}:
                continue
            item["match"] = {
                "destination": anchor["match"]["destination"],
                "score": 99,
                "method": "Lote de descarga y oficio exacto",
                "evidence": [
                    f"oficio único {anchor['path'].name}",
                    "descarga dentro de 10 minutos",
                    "tipo documental único en el lote",
                ],
                "notes": [],
            }
            item["metadata"]["public_debt_payment"] = True
            item["metadata"]["public_debt_evidence"] = [
                "asociado a oficio de Deuda Externa Pública del mismo lote"
            ]


def _destination_completeness(destination: Path) -> tuple[list[str], str]:
    names = _normalized(_folder_file_names(destination))
    missing = []
    if "OFICIO" not in names and "MEMORANDO" not in names:
        missing.append("Oficio")
    if "ESTADO CUENTA" not in names:
        missing.append("Estado de cuenta")
    if "FORMULARIO TRANSFERENCIA AL EXTERIOR" not in names:
        missing.append("Formulario")
    return missing, "Completo 3/3" if not missing else f"Pendiente {3-len(missing)}/3"


def _sigade_from_destination(destination: Path | None) -> str:
    if not destination:
        return ""
    match = re.search(r"\b((?:20|23|28)\d{6})\b", destination.name)
    return match.group(1) if match else ""


def _sigade_for_record(destination: Path | None, metadata: dict[str, Any]) -> str:
    return _sigade_from_destination(destination) or (metadata.get("sigade_numbers") or [""])[0]


def _record_for(item: dict[str, Any], fingerprint: str) -> dict[str, Any]:
    path: Path = item["path"]
    metadata = item["metadata"]
    match = item["match"]
    destination: Path | None = match.get("destination")
    document_type = metadata["document_type"]
    record: dict[str, Any] = {
        "record_id": fingerprint[:16],
        "analysis_version": ANALYSIS_VERSION,
        "fingerprint": fingerprint,
        "source_name": path.name,
        "source_path": str(path),
        "source_aliases": [],
        "size": path.stat().st_size,
        "downloaded_at": _download_datetime(path).isoformat(timespec="seconds"),
        "processed_at": _now_iso(),
        "document_type": document_type,
        "office_reference": (metadata.get("office_references") or [""])[0],
        "loan_reference": (metadata.get("loan_references") or [""])[0],
        "sigade": _sigade_for_record(destination, metadata),
        "value_date": (
            metadata["value_date"].strftime("%d/%m/%Y")
            if metadata.get("value_date")
            else ""
        ),
        "amounts": metadata.get("amounts", []),
        "currency": metadata.get("currency", ""),
        "ocr_used": bool(metadata.get("ocr_used")),
        "destination": str(destination) if destination else "",
        "destination_name": destination.name if destination else "",
        "target_name": target_name(document_type, metadata),
        "match_method": match.get("method", ""),
        "score": int(match.get("score", 0)),
        "evidence": match.get("evidence", []),
        "notes": match.get("notes", []),
        "candidate_paths": match.get("candidate_paths", []),
        "baseline": False,
        "public_debt_payment": bool(metadata.get("public_debt_payment")),
        "public_debt_evidence": metadata.get("public_debt_evidence", []),
    }
    if document_type == "ERROR":
        record["status"] = "ERROR"
    elif document_type not in SUPPORTED_TYPES or not metadata.get("public_debt_payment"):
        record["status"] = "IGNORADO"
        record["target_name"] = ""
    elif not destination or record["score"] < AUTO_MATCH_SCORE:
        record["status"] = "REVISAR"
    else:
        target = destination / record["target_name"]
        try:
            destination.mkdir(parents=True, exist_ok=True)
            try:
                engine.indexed_month_directories.cache_clear()
            except AttributeError:
                pass
            identical = None
            try:
                for existing in destination.iterdir():
                    if (
                        existing.is_file()
                        and existing.stat().st_size == path.stat().st_size
                        and engine.same_file_content(path, existing)
                    ):
                        identical = existing
                        break
            except OSError:
                identical = None
            result = "same" if identical else engine.atomic_copy_without_overwrite(path, target)
            if result == "copied":
                record["status"] = "ARCHIVADO"
            elif result == "same":
                record["status"] = "YA ARCHIVADO"
                if identical and identical.name != target.name:
                    record["notes"].append(
                        f"El mismo contenido ya existe en destino como {identical.name}."
                    )
            else:
                record["status"] = "CONFLICTO"
                record["notes"].append(
                    f"Ya existe contenido diferente con el nombre {target.name}."
                )
            if result in {"copied", "same"}:
                missing, package = _destination_completeness(destination)
                folder_sigade = _sigade_from_destination(destination)
                if folder_sigade:
                    record["sigade"] = folder_sigade
                record["missing_documents"] = missing
                record["package_status"] = package
        except Exception as exc:
            record["status"] = "ERROR"
            record["notes"].append(f"Error al copiar: {type(exc).__name__}: {exc}")
    return record


def refresh_package_statuses(state: dict[str, Any]) -> bool:
    changed = False
    for record in state.get("records", {}).values():
        if not isinstance(record, dict):
            continue
        if record.get("status") not in {"ARCHIVADO", "YA ARCHIVADO"}:
            continue
        destination_text = str(record.get("destination") or "").strip()
        if not destination_text:
            continue
        destination = Path(destination_text)
        try:
            if not destination.exists():
                continue
            missing, package = _destination_completeness(destination)
            folder_sigade = _sigade_from_destination(destination)
        except OSError:
            continue
        if folder_sigade and record.get("sigade") != folder_sigade:
            record["sigade"] = folder_sigade
            changed = True
        if record.get("missing_documents") != missing:
            record["missing_documents"] = missing
            changed = True
        if record.get("package_status") != package:
            record["package_status"] = package
            changed = True
    return changed


def scan_once(base_dir: str | Path, origin: str = "AUTOMÁTICO") -> dict[str, Any]:
    logger = configure_logging(base_dir)
    if not _scan_lock.acquire(blocking=False):
        return status_payload(base_dir)
    try:
        config = load_config(base_dir)
        source = Path(config["source"])
        destination = Path(config["destination"])
        state = initialize_baseline(base_dir, source)
        now = datetime.now().astimezone().timestamp()
        try:
            initialized_timestamp = datetime.fromisoformat(
                state.get("initialized_at", "")
            ).timestamp()
        except (TypeError, ValueError):
            initialized_timestamp = now
        new_paths: list[Path] = []
        fingerprints: dict[str, str] = {}
        scheduled_fingerprints: set[str] = set()
        for path in _pdfs(source):
            try:
                stat = path.stat()
                if stat.st_size <= 0 or now - stat.st_mtime < STABLE_AGE_SECONDS:
                    continue
                fingerprint = file_fingerprint(path)
            except OSError:
                continue
            fingerprints[str(path)] = fingerprint
            previous = state["records"].get(fingerprint)
            if previous:
                # Si el mismo contenido existía al activar el módulo, una descarga
                # realmente nueva posterior a esa fecha sí debe poder procesarse.
                redownloaded_historical = bool(previous.get("baseline")) and (
                    stat.st_ctime > initialized_timestamp + 1
                )
                if redownloaded_historical:
                    if fingerprint not in scheduled_fingerprints:
                        new_paths.append(path)
                        scheduled_fingerprints.add(fingerprint)
                    continue
                rules_updated = (
                    previous.get("status")
                    in {"REVISAR", "IGNORADO", "ERROR", "YA ARCHIVADO"}
                    and previous.get("analysis_version") != ANALYSIS_VERSION
                )
                if rules_updated:
                    if fingerprint not in scheduled_fingerprints:
                        new_paths.append(path)
                        scheduled_fingerprints.add(fingerprint)
                    continue
                if str(path) != previous.get("source_path"):
                    aliases = previous.setdefault("source_aliases", [])
                    if str(path) not in aliases:
                        aliases.append(str(path))
                continue
            if fingerprint not in scheduled_fingerprints:
                new_paths.append(path)
                scheduled_fingerprints.add(fingerprint)

        items = [
            _analyze_path(path, destination, config["use_ocr"])
            for path in new_paths
        ]
        apply_batch_context(items)
        for item in items:
            path = item["path"]
            fingerprint = fingerprints[str(path)]
            record = _record_for(item, fingerprint)
            state["records"][fingerprint] = record
            logger.info(
                "origen=%s | archivo=%s | tipo=%s | estado=%s | destino=%s | match=%s%%",
                origin,
                path.name,
                record["document_type"],
                record["status"],
                record.get("destination", ""),
                record.get("score", 0),
            )
        save_state(base_dir, state)
        _runtime.update(
            {
                "last_scan": _now_iso(),
                "last_error": None,
                "monitor_status": "ACTIVO" if config["automatic"] else "PAUSADO",
            }
        )
    except Exception as exc:
        _runtime.update(
            {
                "last_scan": _now_iso(),
                "last_error": f"{type(exc).__name__}: {exc}",
                "monitor_status": "REINTENTANDO",
            }
        )
        logger.exception("Falló el monitor de Archivo Quipux")
    finally:
        _scan_lock.release()
    return status_payload(base_dir)


def status_payload(base_dir: str | Path) -> dict[str, Any]:
    config = load_config(base_dir)
    state = load_state(base_dir)
    if refresh_package_statuses(state):
        try:
            save_state(base_dir, state)
        except OSError:
            pass
    records = sorted(
        state["records"].values(),
        key=lambda record: record.get("processed_at", ""),
        reverse=True,
    )
    counts = Counter(record.get("status", "") for record in records)
    operational = [record for record in records if not record.get("baseline")]
    debt_records = [
        record
        for record in operational
        if record.get("public_debt_payment") and record.get("status") != "IGNORADO"
    ]
    missing = sum(bool(record.get("missing_documents")) for record in debt_records)
    review_statuses = {"REVISAR", "CONFLICTO", "ERROR"}
    return {
        "ok": _runtime.get("last_error") is None,
        "config": config,
        "generated_at": _now_iso(),
        "initialized_at": state.get("initialized_at"),
        "last_scan": _runtime.get("last_scan"),
        "last_error": _runtime.get("last_error"),
        "monitor_status": _runtime.get("monitor_status"),
        "monitor_interval_seconds": MONITOR_INTERVAL_SECONDS,
        "records": records[:250],
        "summary": {
            "detected": len(debt_records),
            "archived": counts["ARCHIVADO"] + counts["YA ARCHIVADO"],
            "review": sum(counts[status] for status in review_statuses),
            "ignored": counts["IGNORADO"],
            "historical": counts["HISTÓRICO PROTEGIDO"],
            "incomplete": missing,
        },
    }


def _worker_loop(base_dir: Path) -> None:
    while True:
        config = load_config(base_dir)
        if config["automatic"]:
            scan_once(base_dir, origin="AUTOMÁTICO")
        else:
            _runtime["monitor_status"] = "PAUSADO"
        _wakeup.wait(MONITOR_INTERVAL_SECONDS)
        _wakeup.clear()


def start_worker(base_dir: str | Path) -> None:
    global _worker
    if "unittest" in sys.modules:
        return
    with _worker_lock:
        if _worker and _worker.is_alive():
            return
        _worker = threading.Thread(
            target=_worker_loop,
            args=(Path(base_dir),),
            name="archivo-quipux-monitor",
            daemon=True,
        )
        _worker.start()


def register_archivo_quipux_routes(app, base_dir: str | Path) -> None:
    global _base_dir
    _base_dir = Path(base_dir)
    configure_logging(_base_dir)
    config = load_config(_base_dir)
    initialize_baseline(_base_dir, Path(config["source"]))

    @app.get("/api/archivo_quipux/status")
    def archivo_quipux_status():
        return jsonify(status_payload(_base_dir))

    @app.post("/api/archivo_quipux/scan")
    def archivo_quipux_scan():
        return jsonify(scan_once(_base_dir, origin="MANUAL"))

    @app.post("/api/archivo_quipux/config")
    def archivo_quipux_config():
        updates = request.get_json(silent=True) or {}
        previous = load_config(_base_dir)
        config = save_config(_base_dir, updates)
        if config["source"].casefold() != previous["source"].casefold():
            initialize_baseline(_base_dir, Path(config["source"]))
        return jsonify({"ok": True, "config": config})

    start_worker(_base_dir)
