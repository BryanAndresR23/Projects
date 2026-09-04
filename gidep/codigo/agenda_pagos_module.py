from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from flask import jsonify, request

import comprobantes_contables_engine as pdf_engine
from corresponsales_module import classify_correspondent, extract_beneficiary_bank


ARCHIVE_ROOT = Path(
    r"Z:\GISI\SSFI\GESTIÓN PAGOS INTERNACIONALES\2026\DEUDA EXTERNA PÚBLICA"
    r"\Acreedores Internacionales"
)
SCAN_INTERVAL_SECONDS = 5 * 60
LOOKAHEAD_DAYS = 14
OVERDUE_WINDOW_DAYS = 14
CACHE_FILE = Path(__file__).resolve().parent / "agenda_pagos_data" / "agenda_cache.json"
SNAPSHOT_FILE = Path(__file__).resolve().parent / "agenda_pagos_data" / "agenda_snapshot.json"
CACHE_LOCK = threading.RLock()
_candidate_cache: dict[str, Any] | None = None
_cache_dirty = False

OFFICE_DOCUMENT_RE = re.compile(r"\b(?:OFICIO|ANEXO\s*0*1)\b", re.IGNORECASE)
ACK_RE = re.compile(
    r"^(?P<reference>(?:TF|GS)-01-\d+)(?:-signed(?:-signed)?)?\.pdf$",
    re.IGNORECASE,
)
ACCOUNTING_DOCUMENT_RE = re.compile(
    r"(?:^\d{3}-+\d+(?:-signed)?\.pdf$|"
    r"^COMPROBANTE\s+CONTABLE\b.*\b\d{3}-\d+\b.*\.pdf$)",
    re.IGNORECASE,
)
CORRESPONDENT_SUPPORT_RE = re.compile(
    r"(?:ESTADO\s*(?:DE\s*)?CUENTA|ACCOUNT\s*STATEMENT|FORMULARIO.*TRANSFERENCIA|"
    r"TRANSFERENCIA.*EXTERIOR|INSTRUCCIONES?\s*(?:DE\s*)?PAGO|DATOS\s+BANCARIOS|"
    r"BANKING\s+INSTRUCTIONS|PAYMENT\s+INSTRUCTIONS)",
    re.IGNORECASE,
)
VOUCHER_RE = re.compile(r"\b(?P<voucher>771-\d+)\b", re.IGNORECASE)
# Fecha de membrete del oficio ("Quito, D.M., 15 de septiembre de 2026").
# Nunca es la fecha valor: los patrones laxos deben ignorarla.
LETTERHEAD_DATE_RE = re.compile(
    r"(?:QUITO|GUAYAQUIL|CUENCA|AMBATO|LOJA|MANTA|PORTOVIEJO)"
    r"[\s,.-]*(?:D\.?\s*M\.?)?[\s,.-]*$"
)
# Días que se conserva el descarte de un documento antes de reintentarlo.
NO_CANDIDATE_TTL_SECONDS = 7 * 24 * 60 * 60

SPANISH_MONTH_NUMBERS = {
    "ENERO": 1,
    "FEBRERO": 2,
    "MARZO": 3,
    "ABRIL": 4,
    "MAYO": 5,
    "JUNIO": 6,
    "JULIO": 7,
    "AGOSTO": 8,
    "SEPTIEMBRE": 9,
    "SETIEMBRE": 9,
    "OCTUBRE": 10,
    "NOVIEMBRE": 11,
    "DICIEMBRE": 12,
}
DATE_RE = re.compile(
    r"(?<!\d)(?:(?P<num_day>\d{1,2})\s*[/.-]\s*(?P<num_month>\d{1,2})"
    r"\s*[/.-]\s*(?P<num_year>\d(?:\s*\d){3})|"
    r"(?P<word_day>\d{1,2}|\d\s+\d)\s+(?:DE\s+)?"
    r"(?P<word_month>ENERO|FEBRERO|MARZO|ABRIL|MAYO|JUNIO|JULIO|AGOSTO|"
    r"SEPTIEMBRE|SETIEMBRE|OCTUBRE|NOVIEMBRE|DICIEMBRE)"
    r"\s+(?:(?:DE|DEL)\s+)?(?P<word_year>\d(?:\s*\d){3}))(?!\d)",
    re.IGNORECASE,
)
AMOUNT_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:[.,]\d{3})+[.,]\d{2}|\d+[.,]\d{2})(?!\d)"
)


@dataclass
class ScheduledPayment:
    payment_id: str
    responsible: str
    voucher_number: str
    source_folder: str
    source_document: str
    document_name: str
    office_reference: str | None
    issuer: str
    loan_reference: str | None
    loan_folder: str
    value_date: str
    date_type: str
    due_date: str | None
    amount_value: str | None
    currency: str | None
    status: str
    timing: str
    days_until: int
    completed: bool
    accounting_ready: bool
    operation_reference: str | None
    confidence: int
    ocr_used: bool
    note: str
    beneficiary_bank: str | None = None
    correspondent: str | None = None
    correspondent_full: str | None = None
    correspondent_method: str = ""
    correspondent_confidence: int = 0
    correspondent_evidence: list[str] = field(default_factory=list)
    account_statements_reviewed: int = 0


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.upper().replace("–", "-").replace("—", "-")
    value = " ".join(value.split())
    fragmented_words = {
        r"\bPREST\s+AMO\b": "PRESTAMO",
        r"\bDEBIT\s+O\b": "DEBITO",
        r"\bVENCIMIENT\s+O\b": "VENCIMIENTO",
        r"\bMONT\s+O\b": "MONTO",
        r"\bCONCEPT\s+O\b": "CONCEPTO",
        r"\bCUENT\s+A\b": "CUENTA",
        r"\bPROYECT\s+O\b": "PROYECTO",
        r"\bP\s+AGO\b": "PAGO",
    }
    for pattern, replacement in fragmented_words.items():
        value = re.sub(pattern, replacement, value)
    return value


def searchable_text(path: Path, use_ocr: bool = True) -> tuple[str, bool]:
    return pdf_engine.searchable_pdf_text(path, use_ocr=use_ocr)


@lru_cache(maxsize=2048)
def supporting_bank_evidence(
    path_string: str, modified_ns: int, size: int, use_ocr: bool
) -> tuple[str, bool]:
    """Extrae el banco una sola vez por versión del respaldo del expediente."""
    del modified_ns, size
    text, ocr_used = searchable_text(Path(path_string), use_ocr=use_ocr)
    return extract_beneficiary_bank(text), ocr_used


def candidate_cache() -> dict[str, Any]:
    global _candidate_cache
    with CACHE_LOCK:
        if _candidate_cache is not None:
            return _candidate_cache
        try:
            loaded = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            _candidate_cache = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            _candidate_cache = {}
        return _candidate_cache


def cached_candidate(path: Path) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    with CACHE_LOCK:
        entry = candidate_cache().get(str(path))
        if not isinstance(entry, dict):
            return None
        if entry.get("modified_ns") != stat.st_mtime_ns or entry.get("size") != stat.st_size:
            return None
        data = entry.get("candidate")
        if not isinstance(data, dict):
            return None
        result = dict(data)
        try:
            result["value_date"] = (
                date.fromisoformat(result["value_date"])
                if result.get("value_date")
                else None
            )
            result["due_date"] = (
                date.fromisoformat(result["due_date"])
                if result.get("due_date")
                else None
            )
        except (TypeError, ValueError):
            return None
        result["path"] = path
        return result


def cached_no_candidate(path: Path) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    with CACHE_LOCK:
        entry = candidate_cache().get(str(path))
        if not (
            isinstance(entry, dict)
            and entry.get("modified_ns") == stat.st_mtime_ns
            and entry.get("size") == stat.st_size
            and entry.get("ocr_complete") is True
            and entry.get("candidate") is None
        ):
            return False
        # El descarte caduca: un documento ilegible hoy puede volverse legible
        # tras mejorar el extractor. Las entradas sin marca de tiempo provienen
        # de versiones anteriores y se reintentan una vez.
        checked_at = entry.get("checked_at")
        if not isinstance(checked_at, (int, float)):
            return False
        return (time.time() - checked_at) < NO_CANDIDATE_TTL_SECONDS


def remember_candidate(path: Path, candidate: dict[str, Any]) -> None:
    global _cache_dirty
    try:
        stat = path.stat()
    except OSError:
        return
    serialized = {
        key: (
            value.isoformat()
            if isinstance(value, date)
            else value
        )
        for key, value in candidate.items()
        if key != "path"
    }
    with CACHE_LOCK:
        candidate_cache()[str(path)] = {
            "modified_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "candidate": serialized,
        }
        _cache_dirty = True


def remember_no_candidate(path: Path) -> None:
    global _cache_dirty
    try:
        stat = path.stat()
    except OSError:
        return
    with CACHE_LOCK:
        candidate_cache()[str(path)] = {
            "modified_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "ocr_complete": True,
            "checked_at": time.time(),
            "candidate": None,
        }
        _cache_dirty = True


def discarded_document_count() -> int:
    """Documentos que el analizador no logró interpretar y quedaron fuera.

    Se expone en el resumen para que un fallo de extracción sea visible en el
    Centro de Control en lugar de perderse en silencio.
    """
    with CACHE_LOCK:
        return sum(
            1
            for entry in candidate_cache().values()
            if isinstance(entry, dict)
            and entry.get("candidate") is None
            and entry.get("ocr_complete") is True
        )


def flush_candidate_cache() -> None:
    global _cache_dirty
    with CACHE_LOCK:
        if not _cache_dirty:
            return
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = CACHE_FILE.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(candidate_cache(), ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(CACHE_FILE)
        _cache_dirty = False


def load_agenda_snapshot() -> dict[str, Any] | None:
    try:
        payload = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return None
    # Una lectura completa puede tardar por el OCR y las carpetas de red. Antes
    # de mostrar la ultima fotografia, actualizamos inmediatamente las reglas
    # soberanas para que un resultado antiguo no siga presentando JPMORGAN.
    for item in payload["items"]:
        if not isinstance(item, dict):
            continue
        source_folder = normalize(str(item.get("source_folder") or ""))
        office_reference = normalize(str(item.get("office_reference") or ""))
        if re.match(r"^MDEP(?:\b|-)", office_reference):
            item["issuer"] = "MDEP"
        elif re.match(r"^MEF(?:\b|-)", office_reference):
            item["issuer"] = "MEF"
        issuer = normalize(str(item.get("issuer") or ""))
        sovereign = bool(
            re.search(r"\bREPUBLICA(?:\s+DEL)?\s+ECUADOR\b", source_folder)
            or issuer in {"MEF", "MDEP"}
        )
        if not sovereign or normalize(str(item.get("currency") or "")) != "USD":
            continue
        classification = classify_correspondent(
            loan_reference=item.get("loan_reference"),
            lender=item.get("responsible"),
            borrower=(
                "República del Ecuador"
                if "REPUBLICA" in source_folder
                else item.get("issuer")
            ),
            currency=item.get("currency"),
            beneficiary_bank=item.get("beneficiary_bank"),
            history={"by_loan": {}, "by_profile": {}, "by_lender": {}},
        )
        item["correspondent"] = classification.get("correspondent")
        item["correspondent_full"] = classification.get("correspondent_full")
        item["correspondent_method"] = classification.get("method")
        item["correspondent_confidence"] = classification.get("confidence")
        item["correspondent_evidence"] = classification.get("evidence") or []
    return payload


def save_agenda_snapshot(payload: dict[str, Any]) -> None:
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = SNAPSHOT_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(SNAPSHOT_FILE)


@lru_cache(maxsize=512)
def forced_ocr(raw_path: str, modified_ns: int, size: int) -> str:
    del modified_ns, size
    try:
        return pdf_engine.ocr_pdf_text(Path(raw_path), max_pages=3)
    except Exception:
        return ""


def date_from_match(match: re.Match[str]) -> date | None:
    try:
        if match.group("num_day"):
            day = int(match.group("num_day"))
            month = int(match.group("num_month"))
            year = int(re.sub(r"\s+", "", match.group("num_year")))
        else:
            day = int(re.sub(r"\s+", "", match.group("word_day")))
            month = SPANISH_MONTH_NUMBERS[match.group("word_month").upper()]
            year = int(re.sub(r"\s+", "", match.group("word_year")))
        return date(year, month, day)
    except (KeyError, TypeError, ValueError):
        return None


def dated_contexts(text: str) -> list[tuple[date, str, str]]:
    normalized = normalize(text)
    results: list[tuple[date, str, str]] = []
    for match in DATE_RE.finditer(normalized):
        parsed = date_from_match(match)
        if not parsed:
            continue
        before = normalized[max(0, match.start() - 90) : match.start()]
        after = normalized[match.end() : match.end() + 140]
        results.append((parsed, before, after))
    return results


def extract_value_date(text: str) -> tuple[date | None, str]:
    choices: list[tuple[int, date, str]] = []
    for parsed, before, after in dated_contexts(text):
        header = bool(LETTERHEAD_DATE_RE.search(before))
        if re.search(r"FECHA\s+VALOR(?:\s+AL)?\s*[:.-]?\s*$", before):
            choices.append((120, parsed, "Fecha valor"))
        elif re.search(r"FECHA\s+(?:DE\s+)?DEBITO\s*[:.-]?\s*$", before):
            choices.append((140, parsed, "Fecha de débito"))
        elif re.search(r"CON\s+FECHA\s*$", before) and re.search(
            r"\b(?:REALICE|EFECTUE|PROCEDA|APLIQUE)\b.{0,55}\bDEBIT", after
        ):
            choices.append((130, parsed, "Fecha de débito"))
        elif re.search(r"\bDEBIT", before[-70:]) and re.search(r"FECHA\s*$", before):
            choices.append((125, parsed, "Fecha de débito"))
        elif re.search(r"FECHA\s+(?:DE\s+)?(?:PAGO|TRANSFERENCIA)\s*[:.-]?\s*$", before):
            # Oficios MDEP/CFN que rotulan la fecha operativa como "fecha de pago".
            choices.append((110, parsed, "Fecha de pago"))
        elif not header and re.search(
            r"\b(?:SIRVASE|SOLICITO|SOLICITAMOS|AUTORIZO|AUTORIZAMOS|DISPONGO)\b"
            r".{0,90}?\bDEBIT\w*\b.{0,60}$",
            before,
        ):
            # "Sírvase debitar de la cuenta ... el 15 de septiembre de 2026".
            choices.append((105, parsed, "Fecha de débito"))
        elif not header and re.search(r"\bDEBIT\w*\b.{0,60}$", before):
            # Verbo de débito inmediatamente antes de la fecha, sin la palabra
            # "fecha" de por medio. Puntaje bajo: cede ante cualquier rótulo.
            choices.append((100, parsed, "Fecha de débito"))
        elif not header and re.search(
            r"\bEL\s+DIA\s*$", before
        ) and re.search(r"^.{0,60}?\bDEBIT", after):
            choices.append((100, parsed, "Fecha de débito"))
    if not choices:
        return None, ""
    _, selected, label = max(choices, key=lambda choice: choice[0])
    return selected, label


def extract_due_date(text: str) -> date | None:
    choices: list[tuple[int, date]] = []
    for parsed, before, _after in dated_contexts(text):
        if re.search(
            r"(?:FECHA\s+(?:DE\s+)?VENCIMIENTO|FECHA\s+VCTO|PAGO\s+VENCIMIENTO)\s*[:.-]?\s*$",
            before,
        ):
            choices.append((100, parsed))
        elif re.search(r"VENCIMIENTO\s+(?:DEL|AL|A)\s*$", before):
            choices.append((90, parsed))
    return max(choices, key=lambda choice: choice[0])[1] if choices else None


def canonical_loan(raw: str) -> str:
    value = normalize(raw).strip(" .,:;()")
    value = re.sub(r"\s*-\s*", "-", value)
    value = re.sub(r"\s+", "-", value)
    if re.fullmatch(r"C-?EC-?\d{3,6}-?\d{2}-?[A-Z]", value):
        return "-".join(re.findall(r"[A-Z]+|\d+", value))
    match = re.match(
        r"^(CFA|CAF|ICO|BIRF|BID|AFD|KFW|JICA|BEI|FONPLATA)-?0*(\d{3,9})(.*)$",
        value,
    )
    if match:
        prefix, number, suffix = match.groups()
        prefix = "CFA" if prefix == "CAF" else prefix
        suffix = suffix.strip("-")
        return f"{prefix}-{int(number)}" + (f"-{suffix}" if suffix else "")
    return value


def extract_loan(text: str) -> str | None:
    normalized = normalize(text)
    known_pattern = re.compile(
        r"\b(C\s+EC\s+\d{3,6}\s+\d{2}\s+[A-Z]|"
        r"(?:CFA|CAF|ICO|BIRF|BID|AFD|KFW|JICA|BEI|FONPLATA|FIDA|BCIE|"
        r"AIIB|CDB|KEXIM|OFID|OPEC|NDB)"
        r"\s*[- ]?\s*0*\d{3,9}(?:\s*-\s*[A-Z0-9]{1,8})*)\b"
    )
    labels = re.compile(
        r"\b(?:PRESTAMO|CREDITO|REF(?:ERENCIA)?(?:\s+PRESTAMO)?|OPERACION)\b"
    )
    for label in labels.finditer(normalized):
        match = known_pattern.search(normalized[label.end() : label.end() + 140])
        if match:
            return canonical_loan(match.group(1))
    match = known_pattern.search(normalized)
    if match:
        return canonical_loan(match.group(1))
    sigade = re.search(
        r"(?:PRESTAMO\s+SIGADE|REF\.?\s*/?\s*NO\.?\s*/?\s*PRESTAMO|"
        r"PRESTAMO)\s*[:#-]?\s*(\d{6,12})",
        normalized,
    )
    return sigade.group(1) if sigade else None


def extract_office(text: str) -> str | None:
    match = re.search(
        r"\bOFICIO\s+(?:NRO|NO)?\.?\s*[:#-]?\s*"
        r"([A-Z0-9.]+(?:\s*-\s*[A-Z0-9.]+){2,})",
        normalize(text),
    )
    return re.sub(r"\s*-\s*", "-", match.group(1)).strip(" .,:;") if match else None


def loan_from_folder(folder_name: str) -> str | None:
    value = re.split(r"\s*-\s*(?=\d{6,}\b)", folder_name, maxsplit=1)[0]
    value = re.sub(r"\s+\(\d+\)\s*$", "", value).strip(" -")
    return value or None


def loan_from_archive_path(folder: Path) -> tuple[str | None, str]:
    generic_names = {
        "PAGOS",
        "RETENCIONES",
        "DESEMBOLSOS",
        "CONTRATOS",
        "DOCUMENTOS",
        "ANEXOS",
        *SPANISH_MONTH_NUMBERS.keys(),
    }
    for current in (folder, *folder.parents):
        if current == ARCHIVE_ROOT:
            break
        normalized_name = normalize(current.name)
        if normalized_name in generic_names:
            continue
        if re.search(r"\d{4,}|\b(?:BONO|JBIC|GPS|EXIMBANK|AMAZON)\b", normalized_name):
            return loan_from_folder(current.name), current.name
    return None, folder.name


def voucher_from_documents(paths: list[Path]) -> str:
    for path in paths:
        if not ACCOUNTING_DOCUMENT_RE.search(path.name):
            continue
        match = VOUCHER_RE.search(path.name)
        if match:
            return match.group("voucher").upper()
    return "—"


def folder_needs_ocr(folder: Path, today: date) -> bool:
    parts = [normalize(part) for part in folder.parts]
    month_number = next(
        (SPANISH_MONTH_NUMBERS[part] for part in parts if part in SPANISH_MONTH_NUMBERS),
        None,
    )
    if month_number is None:
        return True
    return month_number >= max(1, today.month - 1)


def amount_number(value: str) -> float:
    value = value.strip()
    if "." in value and "," in value:
        decimal = "." if value.rfind(".") > value.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        value = value.replace(thousands, "").replace(decimal, ".")
    elif value.count(",") == 1 and len(value.rsplit(",", 1)[1]) == 2:
        value = value.replace(".", "").replace(",", ".")
    elif value.count(".") == 1 and len(value.rsplit(".", 1)[1]) == 2:
        value = value.replace(",", "")
    else:
        value = value.replace(",", "").replace(".", "")
    try:
        return float(value)
    except ValueError:
        return 0.0


def extract_amount(text: str) -> tuple[str | None, str | None]:
    normalized = normalize(text)
    currency_match = re.search(r"\b(USD|EUR|GBP|JPY|CHF|CNY|CAD)\b", normalized)
    currency = currency_match.group(1) if currency_match else None
    direct = re.search(
        r"(?:MONTO|VALOR)\s+(?:TOTAL\s+)?(?:DE\s+)?(?:USD|EUR)?\s*[:$]?\s*"
        + AMOUNT_RE.pattern,
        normalized,
    )
    if direct:
        return direct.groups()[-1], currency or "USD"
    for total in re.finditer(r"\bTOTAL\b", normalized):
        values = AMOUNT_RE.findall(normalized[total.end() : total.end() + 150])
        if values:
            return max(values, key=amount_number), currency
    return None, currency


def identify_issuer(text: str, office: str | None) -> str:
    normalized = normalize(text)
    office_normalized = normalize(office or "")
    if re.match(r"^MDEP(?:\b|-)", office_normalized) or (
        "MINISTERIO DE ECONOMIA Y FINANZAS" in normalized and "MDEP STN" in normalized
    ):
        return "MDEP"
    if re.match(r"^MEF(?:\b|-)", office_normalized) or (
        "MINISTERIO DE ECONOMIA Y FINANZAS" in normalized and "MEF STN" in normalized
    ):
        return "MEF"
    institutions = (
        ("CONAFIPS", "CORPORACION NACIONAL DE FINANZAS POPULARES"),
        ("GAD Portoviejo", "GOBIERNO AUTONOMO DESCENTRALIZADO MUNICIPAL DEL CANTON PORTOVIEJO"),
        ("CFN", "CORPORACION FINANCIERA NACIONAL"),
        ("GAD Guayaquil", "MUNICIPALIDAD DE GUAYAQUIL"),
    )
    for label, phrase in institutions:
        if phrase in normalized:
            return label
    return "Institución"


def office_candidate(path: Path, use_ocr: bool = True) -> dict[str, Any] | None:
    cached = cached_candidate(path)
    if cached:
        return cached
    if cached_no_candidate(path):
        return None
    try:
        text, ocr_used = searchable_text(path, use_ocr=use_ocr)
    except Exception:
        return None
    value_date, date_type = extract_value_date(text)
    likely_office = bool(re.search(r"\bOFICIO\b|\bANEXO\s*0*1\b", normalize(path.stem)))
    if not value_date and use_ocr and not ocr_used and likely_office:
        stat = path.stat()
        ocr_text = forced_ocr(str(path), stat.st_mtime_ns, stat.st_size)
        if ocr_text.strip():
            text = ocr_text
            ocr_used = True
            value_date, date_type = extract_value_date(text)
    loan = extract_loan(text)
    office = extract_office(text)
    due_date = extract_due_date(text)
    amount, currency = extract_amount(text)
    if not value_date:
        # Antes se descartaba el documento completo y quedaba en lista negra.
        # Si el oficio identifica el préstamo o el monto, la información es
        # demasiado valiosa para perderla: se emite sin fecha y el clasificador
        # lo envía a "Revisar" en lugar de dejarlo desaparecer en silencio.
        if not loan and not amount:
            if use_ocr:
                remember_no_candidate(path)
            return None
        candidate = {
            "path": path,
            "value_date": None,
            "date_type": "Sin fecha legible",
            "due_date": due_date,
            "loan": loan,
            "office": office,
            "issuer": identify_issuer(text, office),
            "amount": amount,
            "currency": currency,
            "beneficiary_bank": extract_beneficiary_bank(text),
            "score": 40 + (15 if loan else 0) + (5 if amount else 0),
            "ocr_used": ocr_used,
        }
        remember_candidate(path, candidate)
        return candidate
    normalized = normalize(text)
    score = 50 + (25 if loan else 0) + (10 if office else 0)
    score += 15 if "OFICIO" in path.name.upper() else 0
    score += 10 if "BANCO CENTRAL DEL ECUADOR" in normalized else 0
    score += 5 if amount else 0
    candidate = {
        "path": path,
        "value_date": value_date,
        "date_type": date_type,
        "due_date": due_date,
        "loan": loan,
        "office": office,
        "issuer": identify_issuer(text, office),
        "amount": amount,
        "currency": currency,
        "beneficiary_bank": extract_beneficiary_bank(text),
        "score": min(score, 100),
        "ocr_used": ocr_used,
    }
    remember_candidate(path, candidate)
    return candidate


def classify_folder(
    creditor_group: str,
    folder: Path,
    today: date | None = None,
    use_ocr: bool = True,
) -> ScheduledPayment | None:
    today = today or datetime.now().date()
    pdfs = [path for path in folder.glob("*.pdf") if path.is_file()]
    operational_folder = folder_needs_ocr(folder, today)
    allow_ocr = use_ocr and operational_folder
    office_paths = [
        path for path in pdfs if OFFICE_DOCUMENT_RE.search(normalize(path.stem))
    ]
    candidates = [
        candidate
        for path in office_paths
        for candidate in [cached_candidate(path)]
        if candidate
    ]
    uncached_paths = [
        path
        for path in office_paths
        if cached_candidate(path) is None and not cached_no_candidate(path)
    ]
    for path in uncached_paths:
        if not operational_folder:
            continue
        candidate = office_candidate(path, use_ocr=allow_ocr and not candidates)
        if candidate:
            candidates.append(candidate)
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda item: (
            item["score"],
            "ALCANCE" in normalize(item["path"].stem),
            "OFICIO" in item["path"].name.upper(),
            item["path"].stat().st_mtime_ns,
        ),
    )
    supporting_documents = sorted(
        (path for path in pdfs if ACCOUNTING_DOCUMENT_RE.search(path.name)),
        key=lambda path: ("-signed" not in path.stem.lower(), path.name.lower()),
    )
    path_loan, loan_folder = loan_from_archive_path(folder)
    loan = best["loan"] or path_loan
    supporting_texts: list[str] = []
    for document in supporting_documents:
        try:
            text, _ = searchable_text(document, use_ocr=False)
        except Exception:
            continue
        supporting_texts.append(text)
        if not loan:
            loan = extract_loan(text)
    beneficiary_bank = str(best.get("beneficiary_bank") or "").strip()
    bank_documents_reviewed = 0
    bank_document_ocr = False
    bank_evidence_document = ""
    bank_documents = [
        path
        for path in pdfs
        if path != best["path"] and CORRESPONDENT_SUPPORT_RE.search(normalize(path.stem))
    ]
    for document in sorted(bank_documents, key=lambda path: path.name.casefold()):
        try:
            stat = document.stat()
            bank_from_document, used_ocr = supporting_bank_evidence(
                str(document), stat.st_mtime_ns, stat.st_size, allow_ocr
            )
        except Exception:
            continue
        bank_documents_reviewed += 1
        bank_document_ocr = bank_document_ocr or used_ocr
        if not beneficiary_bank and bank_from_document:
            beneficiary_bank = bank_from_document
            bank_evidence_document = document.name
    issuer = str(best["issuer"])
    office_issuer = identify_issuer("", best.get("office"))
    if office_issuer in {"MEF", "MDEP"}:
        # Corrige también candidatos guardados por versiones anteriores del
        # analizador, cuando MDEP todavía figuraba como "Institución".
        issuer = office_issuer
    folder_is_republic = bool(
        re.search(r"\bREPUBLICA(?:\s+DEL)?\s+ECUADOR\b", normalize(folder.name))
    )
    correspondent = classify_correspondent(
        loan_reference=loan,
        lender=creditor_group,
        borrower="República del Ecuador" if folder_is_republic else issuer,
        currency=best["currency"],
        beneficiary_bank=beneficiary_bank,
    )
    correspondent_evidence = list(correspondent.get("evidence") or [])
    if bank_evidence_document:
        correspondent_evidence.insert(
            0, f"Banco beneficiario leído en {bank_evidence_document}."
        )
    markers = [
        match
        for path in pdfs
        for match in [ACK_RE.fullmatch(path.name)]
        if match
    ]
    accounting_ready = bool(supporting_documents)
    internal = any(
        "DTR100" in normalize(text)
        and "TRANSFERENCIA ENTRE CUENTAS" in normalize(text)
        and re.search(r"NC-01-\d+", normalize(text))
        for text in supporting_texts
    )
    completed = bool(markers) or (internal and accounting_ready)
    if markers:
        operation = markers[0].group("reference").upper()
    else:
        operation = next(
            (
                match.group(0)
                for text in supporting_texts
                for match in [re.search(r"NC-01-\d+", normalize(text))]
                if match
            ),
            None,
        )
    value_date = best["value_date"]
    undated = value_date is None
    days_until = 0 if undated else (value_date - today).days
    timing = (
        "undated"
        if undated
        else "today"
        if days_until == 0
        else "past"
        if days_until < 0
        else "upcoming"
        if days_until <= LOOKAHEAD_DAYS
        else "future"
    )
    if undated:
        status = "review"
        note = (
            "No se pudo leer la fecha valor/débito en el oficio. "
            "El préstamo y el monto sí se identificaron: verifique la fecha "
            "manualmente antes de procesar."
        )
    elif not loan:
        status = "review"
        note = "La fecha está identificada, pero falta la referencia del préstamo."
    elif completed:
        status = "paid"
        note = (
            "La transferencia interna ya tiene comprobante firmado."
            if internal
            else "El pago ya tiene mensaje TF/GS archivado."
        )
    elif days_until < 0:
        status = "overdue"
        note = "La fecha valor/débito ya pasó y falta el respaldo final."
    elif days_until == 0:
        status = "today"
        note = "Debe procesarse hoy."
    elif accounting_ready:
        status = "in_process"
        note = "El comprobante está firmado; falta completar el respaldo final."
    else:
        status = "scheduled"
        note = "Pago programado según el oficio."
    return ScheduledPayment(
        payment_id=f"{creditor_group}|{folder.parent.name}|{folder.name}",
        responsible=creditor_group,
        voucher_number=voucher_from_documents(pdfs),
        source_folder=str(folder),
        source_document=str(best["path"]),
        document_name=best["path"].name,
        office_reference=best["office"],
        issuer=issuer,
        loan_reference=loan,
        loan_folder=loan_folder,
        value_date="" if undated else value_date.isoformat(),
        date_type=best["date_type"],
        due_date=best["due_date"].isoformat() if best["due_date"] else None,
        amount_value=best["amount"],
        currency=best["currency"],
        status=status,
        timing=timing,
        days_until=days_until,
        completed=completed,
        accounting_ready=accounting_ready,
        operation_reference=operation,
        confidence=best["score"],
        ocr_used=best["ocr_used"] or bank_document_ocr,
        note=note,
        beneficiary_bank=beneficiary_bank or None,
        correspondent=correspondent.get("correspondent"),
        correspondent_full=correspondent.get("correspondent_full"),
        correspondent_method=str(correspondent.get("method") or ""),
        correspondent_confidence=int(correspondent.get("confidence") or 0),
        correspondent_evidence=correspondent_evidence,
        account_statements_reviewed=bank_documents_reviewed,
    )


def discover_folders(root: Path) -> list[Path]:
    if not root.is_dir():
        raise FileNotFoundError(f"No se encuentra la carpeta: {root}")
    folders: set[Path] = set()
    for document in root.rglob("*.pdf"):
        if document.is_file() and OFFICE_DOCUMENT_RE.search(normalize(document.stem)):
            folders.add(document.parent)
    return sorted(folders, key=lambda path: str(path).casefold())


def deduplicate_payments(items: list[ScheduledPayment]) -> list[ScheduledPayment]:
    """Evita duplicar un oficio guardado en carpetas COPY o numeradas."""
    selected: dict[tuple[str, ...], ScheduledPayment] = {}
    unkeyed: list[ScheduledPayment] = []
    for item in items:
        if not item.office_reference:
            unkeyed.append(item)
            continue
        key = (
            normalize(item.office_reference),
            normalize(item.loan_reference or ""),
            item.value_date,
            normalize(item.amount_value or ""),
            normalize(item.currency or ""),
        )
        current = selected.get(key)
        if current is None:
            selected[key] = item
            continue
        current_copy = bool(re.search(r"(?:-\s*COPY|\(\d+\))\s*$", current.loan_folder, re.I))
        item_copy = bool(re.search(r"(?:-\s*COPY|\(\d+\))\s*$", item.loan_folder, re.I))
        current_rank = (current.completed, current.confidence, not current_copy)
        item_rank = (item.completed, item.confidence, not item_copy)
        if item_rank > current_rank:
            selected[key] = item
    return [*selected.values(), *unkeyed]


def totals_by_currency(
    items: list[ScheduledPayment], *, today_only: bool = False, pending_only: bool = False
) -> dict[str, float]:
    totals: dict[str, float] = {}
    for item in items:
        if today_only and (item.days_until != 0 or item.timing == "undated"):
            continue
        if pending_only and item.completed:
            continue
        if not item.amount_value or not item.currency:
            continue
        value = amount_number(item.amount_value)
        if value <= 0:
            continue
        currency = item.currency.upper()
        totals[currency] = round(totals.get(currency, 0.0) + value, 2)
    return dict(sorted(totals.items()))


def scan_agenda(today: date | None = None, use_ocr: bool = True) -> dict[str, Any]:
    today = today or datetime.now().date()
    items: list[ScheduledPayment] = []
    errors: list[str] = []
    try:
        folders = discover_folders(ARCHIVE_ROOT)
    except Exception as exc:
        folders = []
        errors.append(str(exc))
    for folder in folders:
        try:
            relative_parts = folder.relative_to(ARCHIVE_ROOT).parts
            creditor_group = relative_parts[0] if relative_parts else "ACREEDOR"
            item = classify_folder(
                creditor_group, folder, today=today, use_ocr=use_ocr
            )
            if item:
                items.append(item)
        except Exception as exc:
            errors.append(f"{folder.name}: {exc}")
    items = deduplicate_payments(items)
    items.sort(
        key=lambda item: (
            item.value_date,
            item.completed,
            item.responsible.casefold(),
            item.loan_reference or "",
        )
    )
    detected_total = len(items)
    items = [
        item
        for item in items
        if item.timing == "undated"
        or -OVERDUE_WINDOW_DAYS <= item.days_until <= LOOKAHEAD_DAYS
    ]
    dated = [item for item in items if item.timing != "undated"]
    summary = {
        "total": len(items),
        "today": sum(item.days_until == 0 for item in dated),
        "today_pending": sum(item.days_until == 0 and not item.completed for item in dated),
        "next_7_days": sum(
            1 <= item.days_until <= 7 and not item.completed for item in dated
        ),
        "overdue": sum(item.status == "overdue" for item in dated),
        "paid": sum(item.completed for item in items),
        "review": sum(item.status == "review" for item in items),
        "undated": sum(item.timing == "undated" for item in items),
        "discarded_documents": discarded_document_count(),
        "historical_hidden": detected_total - len(items),
        "today_totals": totals_by_currency(items, today_only=True),
        "today_pending_totals": totals_by_currency(
            items, today_only=True, pending_only=True
        ),
    }
    result = {
        "scanned_at": now_iso(),
        "today": today.isoformat(),
        "interval_seconds": SCAN_INTERVAL_SECONDS,
        "lookahead_days": LOOKAHEAD_DAYS,
        "source": str(ARCHIVE_ROOT),
        "summary": summary,
        "items": [asdict(item) for item in items],
        "errors": errors,
    }
    flush_candidate_cache()
    return result


class AgendaMonitor:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.scan_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_scan: dict[str, Any] | None = load_agenda_snapshot()
        self.monitor = {
            "running": False,
            "busy": False,
            "last_run": self.last_scan.get("scanned_at") if self.last_scan else None,
            "next_run": None,
            "last_error": None,
            "message": "Agenda automática lista",
        }

    def start(self) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.monitor["running"] = True
            self.thread = threading.Thread(
                target=self._loop, name="agenda-pagos-monitor", daemon=True
            )
            self.thread.start()

    def scan(self) -> dict[str, Any]:
        with self.scan_lock:
            with self.lock:
                self.monitor["busy"] = True
                self.monitor["last_error"] = None
            try:
                result = scan_agenda(use_ocr=True)
            except Exception as exc:
                with self.lock:
                    self.monitor["last_error"] = f"{type(exc).__name__}: {exc}"
                    self.monitor["message"] = "No se pudo actualizar la agenda"
                raise
            else:
                save_agenda_snapshot(result)
                with self.lock:
                    self.last_scan = result
                    self.monitor["last_run"] = result["scanned_at"]
                    self.monitor["message"] = (
                        f"{result['summary']['total']} pago(s); "
                        f"{result['summary']['today_pending']} pendiente(s) para hoy"
                    )
                return result
            finally:
                with self.lock:
                    self.monitor["busy"] = False

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "ok": True,
                "automatic": True,
                "interval_seconds": SCAN_INTERVAL_SECONDS,
                "monitor": dict(self.monitor),
                "scan": self.last_scan,
            }

    def request_scan(self) -> bool:
        with self.lock:
            if self.monitor["busy"]:
                return False
            self.monitor["busy"] = True
            self.monitor["message"] = "Actualización solicitada"
        threading.Thread(
            target=self.scan, name="agenda-pagos-manual", daemon=True
        ).start()
        return True

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.time()
            try:
                self.scan()
            except Exception:
                pass
            next_run = started + SCAN_INTERVAL_SECONDS
            with self.lock:
                self.monitor["next_run"] = datetime.fromtimestamp(
                    next_run
                ).astimezone().isoformat(timespec="seconds")
            if self.stop_event.wait(max(1.0, next_run - time.time())):
                break
        with self.lock:
            self.monitor["running"] = False


AGENDA = AgendaMonitor()


def register_agenda_pagos_routes(app, base_dir) -> None:
    del base_dir

    @app.get("/api/agenda-pagos/status")
    def agenda_pagos_status():
        return jsonify(AGENDA.status())

    @app.post("/api/agenda-pagos/refresh")
    def agenda_pagos_refresh():
        started = AGENDA.request_scan()
        payload = AGENDA.status()
        payload["scan_started"] = started
        return jsonify(payload), (202 if started else 200)

    @app.post("/api/agenda-pagos/open-source")
    def agenda_pagos_open_source():
        if not ARCHIVE_ROOT.is_dir():
            return jsonify({"ok": False, "error": f"No existe: {ARCHIVE_ROOT}"}), 400
        try:
            os.startfile(str(ARCHIVE_ROOT))  # type: ignore[attr-defined]
        except OSError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "path": str(ARCHIVE_ROOT)})

    if os.environ.get("AGENDA_DISABLE_AUTOSTART") != "1":
        AGENDA.start()
