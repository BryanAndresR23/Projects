from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:  # Compatibilidad con instalaciones que todavía usan PyPDF2.
    try:
        from PyPDF2 import PdfReader
    except ImportError as exc:
        raise SystemExit(
            "Falta la librería para leer PDF. Instale pypdf con: python -m pip install pypdf"
        ) from exc


DEFAULT_SOURCE = Path(r"Z:\GISI\SSFI\SWIFT\Carpeta Ingresadores\BRYAN")
DEFAULT_STEVEN_SOURCE = Path(r"Z:\GISI\SSFI\SWIFT\Carpeta Ingresadores\STEVEN")
DEFAULT_ACK_BASE = Path(r"Z:\DSBI\dsbi_swift\PDF_DEUDA")
MATCH_ENGINE_VERSION = "2026-08-13-fecha-duplicados-v1"
WINDOWS_OCR_SCRIPT = Path(__file__).resolve().with_name("windows_ocr.ps1")
DEFAULT_DESTINATION = Path(
    r"Z:\GISI\SSFI\GESTIÓN PAGOS INTERNACIONALES\2026\DEUDA EXTERNA PÚBLICA"
    r"\Acreedores Internacionales"
)

DOUBLE_SIGNED_RE = re.compile(
    r"^(?:\d{1,4}[\s_-]+)?(?P<kind>TF|GS)-01-"
    r"(?P<operation>\d+)-signed-signed\.pdf$",
    re.IGNORECASE,
)
SOURCE_FOLDER_RE = re.compile(r"^20\d{2}-(?P<voucher>\d{3}-\d+)$", re.IGNORECASE)
# Algunos PDF concatenan la etiqueta y el valor: COD.OPERACIONNC-01-...
INTERNAL_OPERATION_RE = re.compile(r"(NC-01-\d+)", re.IGNORECASE)
DUPLICATE_FOLDER_SUFFIX_RE = re.compile(
    r"\s*(?:-\s*COPY|\(\d+\))\s*$", re.IGNORECASE
)

TESSERACT_CANDIDATES = (
    Path(r"C:\Program Files\PDF24\tesseract\tesseract.exe"),
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
)

PDFTOPPM_CANDIDATES = (
    Path(
        r"C:\Users\bromo\.cache\codex-runtimes\codex-primary-runtime\dependencies"
        r"\native\poppler\Library\bin\pdftoppm.exe"
    ),
)

MATCH_STOPWORDS = {
    "BANCO",
    "CENTRAL",
    "ECUADOR",
    "PAGO",
    "DEUDA",
    "EXTERNA",
    "CUENTA",
    "CUENTAS",
    "MINISTERIO",
    "ECONOMIA",
    "FINANZAS",
    "USD",
    "EUR",
    "THE",
    "OF",
    "AND",
    "DEL",
    "DE",
    "LA",
    "EL",
    "LOS",
    "LAS",
    "POR",
    "PARA",
    "CON",
    "PRESTAMO",
    "BENEFICIARIO",
    "CONCEPTO",
}

MONTH_NAMES = {
    1: "Enero",
    2: "Febrero",
    3: "Marzo",
    4: "Abril",
    5: "Mayo",
    6: "Junio",
    7: "Julio",
    8: "Agosto",
    9: "Septiembre",
    10: "Octubre",
    11: "Noviembre",
    12: "Diciembre",
}

MONTH_ABBR = {
    1: "Ene",
    2: "Feb",
    3: "Mar",
    4: "Abr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Ago",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dic",
}


@dataclass
class Payment:
    marker: Path
    source_folder: Path
    operation_reference: str
    voucher_number: str
    responsible: str = ""
    document_type: str = "PAGO EXTERIOR"
    requires_ack: bool = True
    accounting_source: Path | None = None
    ack_source: Path | None = None
    value_date: datetime | None = None
    loan_reference: str = ""
    office_reference: str = ""
    concept: str = ""
    beneficiary: str = ""
    order_number: str = ""
    currency: str = ""
    amounts: tuple[str, ...] = ()
    destination: Path | None = None
    accounting_target: Path | None = None
    ack_target: Path | None = None
    match_method: str = ""
    match_score: int = 0
    match_evidence: list[str] = field(default_factory=list)
    probable_match: bool = False
    ocr_used: bool = False
    status: str = "PENDIENTE"
    notes: list[str] = field(default_factory=list)


def normalized_words(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.upper()
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return " ".join(value.split())


def contains_phrase(haystack: str, needle: str) -> bool:
    normalized_haystack = f" {normalized_words(haystack)} "
    normalized_needle = normalized_words(needle)
    return bool(normalized_needle) and f" {normalized_needle} " in normalized_haystack


def contains_value_date(corpus: str, value_date: datetime) -> bool:
    """Reconoce la fecha tanto numérica como escrita en español."""
    normalized = normalized_words(corpus)
    day = value_date.day
    month = value_date.month
    year = value_date.year
    numeric_patterns = (
        rf"\b0?{day}\s+0?{month}\s+{year}\b",
        rf"\b{year}\s+0?{month}\s+0?{day}\b",
    )
    if any(re.search(pattern, normalized) for pattern in numeric_patterns):
        return True
    month_name = normalized_words(MONTH_NAMES[month])
    return bool(
        re.search(
            rf"\b0?{day}(?:\s+DE)?\s+{month_name}"
            rf"(?:\s+(?:DE|DEL))?\s+{year}\b",
            normalized,
        )
    )


def duplicate_folder_family(folder: Path) -> str:
    base_name = DUPLICATE_FOLDER_SUFFIX_RE.sub("", folder.name).strip()
    return normalized_words(base_name)


def duplicate_folder_rank(folder: Path) -> tuple[int, int, str]:
    """Prefiere la carpeta original; luego (2), (3), y por último - Copy."""
    name = folder.name.strip()
    numbered = re.search(r"\((\d+)\)\s*$", name)
    if numbered:
        return (1, int(numbered.group(1)), name.casefold())
    if re.search(r"-\s*COPY\s*$", name, re.IGNORECASE):
        return (2, 0, name.casefold())
    return (0, 0, name.casefold())


def extract_pdf_text(path: Path, max_pages: int | None = None) -> str:
    reader = PdfReader(path)
    pages = reader.pages if max_pages is None else reader.pages[:max_pages]
    return "\n".join((page.extract_text() or "") for page in pages)


def find_tesseract() -> Path | None:
    executable = shutil.which("tesseract")
    if executable:
        return Path(executable)
    return next((path for path in TESSERACT_CANDIDATES if path.is_file()), None)


def find_pdftoppm() -> Path | None:
    bundled = next((path for path in PDFTOPPM_CANDIDATES if path.is_file()), None)
    if bundled:
        return bundled
    executable = shutil.which("pdftoppm") or shutil.which("pdftoppm.exe")
    if executable:
        return Path(executable)
    return None


def ocr_pdf_text(path: Path, max_pages: int = 2) -> str:
    output: list[str] = []
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with tempfile.TemporaryDirectory(prefix="gestor_pagos_ocr_") as temporary:
        try:
            import fitz
        except ImportError:
            fitz = None

        images: list[Path] = []
        if fitz is not None:
            document = fitz.open(path)
            try:
                for index in range(min(max_pages, len(document))):
                    image_path = Path(temporary) / f"page-{index + 1}.png"
                    page = document[index]
                    pixmap = page.get_pixmap(dpi=160, alpha=False)
                    pixmap.save(image_path)
                    images.append(image_path)
            finally:
                document.close()
        else:
            pdftoppm = find_pdftoppm()
            if not pdftoppm:
                return ""
            prefix = Path(temporary) / "page"
            render = subprocess.run(
                [
                    str(pdftoppm),
                    "-f",
                    "1",
                    "-l",
                    str(max_pages),
                    "-r",
                    "160",
                    "-png",
                    str(path),
                    str(prefix),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                creationflags=creation_flags,
            )
            if render.returncode != 0:
                return ""
            images = sorted(Path(temporary).glob("page-*.png"))

        if WINDOWS_OCR_SCRIPT.is_file() and os.name == "nt":
            powershell = Path(
                os.environ.get(
                    "SystemRoot", r"C:\Windows"
                )
            ) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            for image_path in images:
                process = subprocess.run(
                    [
                        str(powershell),
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(WINDOWS_OCR_SCRIPT),
                        "-ImagePath",
                        str(image_path),
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=90,
                    check=False,
                    creationflags=creation_flags,
                )
                if process.returncode == 0 and process.stdout.strip():
                    output.append(process.stdout)
            if output:
                return "\n".join(output)

        tesseract = find_tesseract()
        if not tesseract:
            return ""
        for image_path in images:
            try:
                process = subprocess.run(
                    [
                        str(tesseract),
                        str(image_path),
                        "stdout",
                        "-l",
                        "spa+eng",
                        "--psm",
                        "6",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=90,
                    check=False,
                    creationflags=creation_flags,
                )
            except OSError:
                continue
            if process.returncode == 0 and process.stdout.strip():
                output.append(process.stdout)
    return "\n".join(output)


@lru_cache(maxsize=1024)
def cached_searchable_pdf_text(
    raw_path: str, modified_ns: int, size: int, use_ocr: bool
) -> tuple[str, bool]:
    del modified_ns, size
    path = Path(raw_path)
    try:
        text = extract_pdf_text(path, max_pages=4)
    except Exception:
        text = ""
    if use_ocr and len(normalized_words(text)) < 50:
        try:
            ocr_text = ocr_pdf_text(path)
        except Exception:
            ocr_text = ""
        if ocr_text.strip():
            return ocr_text, True
    return text, False


def searchable_pdf_text(path: Path, use_ocr: bool) -> tuple[str, bool]:
    stat = path.stat()
    return cached_searchable_pdf_text(
        str(path), stat.st_mtime_ns, stat.st_size, use_ocr
    )


def first_group(patterns: list[str], text: str) -> str:
    flat = " ".join(text.split())
    for pattern in patterns:
        match = re.search(pattern, flat, flags=re.IGNORECASE)
        if match:
            return " ".join(match.group(1).strip().split())
    return ""


def extract_fields(text: str) -> tuple[datetime | None, str, str, str]:
    flat = " ".join(text.split())

    date_text = first_group(
        [
            r"FECHA\s+VALOR:\s*(\d{2}/\d{2}/\d{4})",
            r"\b(\d{2}/\d{2}/\d{4})\b",
        ],
        flat,
    )
    value_date = None
    if date_text:
        try:
            value_date = datetime.strptime(date_text, "%d/%m/%Y")
        except ValueError:
            pass

    loan = first_group(
        [
            r"REF\.?/NO\.?/PR[ÉE]STAMO:\s*(.+?)(?=\s+CONCEPTO:|\s+CODIGOS\s+CTAS|\s+OTRAS\s+ESPECIFICACIONES)",
            r"\bPR[ÉE]STAMO\s+([A-Z]{2,12}\s*-\s*[A-Z0-9][A-Z0-9./-]*)",
        ],
        flat,
    )
    loan = re.sub(r"\s*-\s*", "-", loan).strip(".,;:").upper()
    office = first_group(
        [
            r"NO\.?\s*DE\s*OFICIO/TR[ÁA]MITE/SOLICITUD:\s*(.+?)(?=\s+ORD(?:ENANTE)?/|\s+ORDENANTE:|\s+INSTITUCION|\s+BENEFICIARIO:)",
        ],
        flat,
    )
    office = re.sub(r"\s+\d{2}/\d{2}/\d{4}$", "", office).strip()
    concept = first_group(
        [
            r"CONCEPTO:\s*(.+?)(?=\s+CODIGOS\s+CTAS|\s+REF\.?/NO\.?/PR[ÉE]STAMO:|\s+OTRAS\s+ESPECIFICACIONES|\s+INSTITUCION\s+DEPENDENCIA|\s+COD\.?OPERACION)",
        ],
        flat,
    )
    return value_date, loan, office, concept


def canonical_amounts(value: str) -> tuple[str, ...]:
    amounts: list[str] = []
    for match in re.findall(r"(?<!\d)(\d{1,3}(?:,\d{3})*\.\d{2})(?!\d)", value):
        canonical = match.replace(",", "")
        if canonical not in amounts:
            amounts.append(canonical)
    return tuple(amounts)


def extract_payment_fields(text: str) -> dict[str, object]:
    value_date, loan, office, concept = extract_fields(text)
    flat = " ".join(text.split())
    beneficiary = first_group(
        [
            r"(?:^|\s)BENEFICIARIO:\s*(.+?)(?=\s+CODIGOS\s+CTAS|\s+NO\.?\s*DE\s*OFICIO|\s+FECHA\s+VALOR|\s+CONCEPTO:)",
            r"BENEF/PRESTAT:\s*(.+?)(?=\s+CONCEPTO:|\s+COD/TRANS/)",
        ],
        flat,
    )
    order_number = first_group(
        [r"PEDIDO\s+N(?:RO|ÚM|UM)\.?\s*(\d+)"],
        flat,
    )
    currency_match = re.search(r"\b(USD|EUR|GBP|JPY|CHF|CNY|CAD)\b", concept or flat)
    currency = currency_match.group(1).upper() if currency_match else ""
    amounts = canonical_amounts(concept)
    return {
        "value_date": value_date,
        "loan_reference": loan,
        "office_reference": office,
        "concept": concept,
        "beneficiary": beneficiary,
        "order_number": order_number,
        "currency": currency,
        "amounts": amounts,
    }


def extract_internal_loan_reference(text: str) -> str:
    """Obtiene una referencia de préstamo explícita de una transferencia interna."""
    flat = " ".join(text.split())
    reference = first_group(
        [
            r"\bREFERENCIA\s+([A-Z]{2,12}\s*-\s*[A-Z0-9][A-Z0-9./-]*)",
            r"\b((?:CFA|CAF|BID|BIRF|KFW|AFD|FONPLATA|BEI|JICA)\s*(?:-\s*|\s+)[A-Z0-9][A-Z0-9./-]*)\b",
        ],
        flat,
    )
    reference = re.sub(
        r"^(CFA|CAF|BID|BIRF|KFW|AFD|FONPLATA|BEI|JICA)\s+",
        r"\1-",
        reference,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s*-\s*", "-", reference).strip(".,;:").upper()


def extract_internal_operation(text: str) -> str:
    match = INTERNAL_OPERATION_RE.search(" ".join(text.split()))
    return match.group(1).upper() if match else ""


def is_internal_transfer(text: str) -> bool:
    normalized = normalized_words(text)
    return (
        "DTR100" in normalized.split()
        and contains_phrase(normalized, "TRANSFERENCIA ENTRE CUENTAS")
        and bool(extract_internal_operation(text))
        and bool(extract_internal_loan_reference(text))
    )


def meaningful_tokens(value: str) -> set[str]:
    return {
        token
        for token in normalized_words(value).split()
        if len(token) >= 3 and token not in MATCH_STOPWORDS and not token.isdigit()
    }


def token_coverage(needle: str, haystack: str) -> float:
    needle_tokens = meaningful_tokens(needle)
    if not needle_tokens:
        return 0.0
    haystack_tokens = set(normalized_words(haystack).split())
    return len(needle_tokens & haystack_tokens) / len(needle_tokens)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def same_file_content(left: Path, right: Path) -> bool:
    try:
        return left.stat().st_size == right.stat().st_size and sha256(left) == sha256(right)
    except OSError:
        return False


def discover_source_folders(source: Path, recursive: bool) -> list[Path]:
    folders: list[Path]
    if recursive:
        folders = [path for path in source.rglob("20??-771-*") if path.is_dir()]
    else:
        folders = [path for path in source.glob("20??-771-*") if path.is_dir()]
        # Las carpetas mensuales 01..12 son parte del flujo actual, no históricos.
        for month_folder in source.iterdir():
            if not month_folder.is_dir() or not re.fullmatch(
                r"(?:0[1-9]|1[0-2])", month_folder.name
            ):
                continue
            folders.extend(
                path
                for path in month_folder.glob("20??-771-*")
                if path.is_dir()
            )
    return sorted(set(folders))


def discover_markers(source: Path, recursive: bool) -> list[Path]:
    folders = discover_source_folders(source, recursive)

    markers: list[Path] = []
    for folder in sorted(folders):
        for path in folder.iterdir():
            if path.is_file() and DOUBLE_SIGNED_RE.fullmatch(path.name):
                markers.append(path)
    return markers


def find_accounting_source(folder: Path, voucher_number: str) -> Path | None:
    """Admite el nombre normal y errores de digitación con guiones repetidos."""
    exact = folder / f"{voucher_number}-signed.pdf"
    if exact.is_file():
        return exact

    voucher_parts = voucher_number.split("-", maxsplit=1)
    if len(voucher_parts) != 2:
        return None
    flexible_name = re.compile(
        rf"^{re.escape(voucher_parts[0])}-+{re.escape(voucher_parts[1])}-signed\.pdf$",
        re.IGNORECASE,
    )
    candidates = sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and flexible_name.fullmatch(path.name)
    )
    return candidates[0] if len(candidates) == 1 else None


def discover_internal_accounting_documents(
    source: Path, recursive: bool, use_ocr: bool = True
) -> list[Path]:
    documents: list[Path] = []
    for folder in discover_source_folders(source, recursive):
        folder_match = SOURCE_FOLDER_RE.fullmatch(folder.name)
        if not folder_match:
            continue
        try:
            if any(
                path.is_file() and DOUBLE_SIGNED_RE.fullmatch(path.name)
                for path in folder.iterdir()
            ):
                # Los pagos exteriores ya se procesan mediante su marcador TF/GS.
                continue
        except OSError:
            continue
        accounting_source = find_accounting_source(
            folder, folder_match.group("voucher")
        )
        if not accounting_source:
            continue
        try:
            text, _ocr_used = searchable_pdf_text(accounting_source, use_ocr=use_ocr)
        except (OSError, ValueError):
            continue
        if is_internal_transfer(text):
            documents.append(accounting_source)
    return documents


def resolve_ack_directory(ack_base: Path, value_date: datetime) -> Path:
    year = f"{value_date.year:04d}"
    month = f"{value_date.month:02d}"
    if ack_base.name == month and ack_base.parent.name == year:
        return ack_base
    return ack_base / year / month


@lru_cache(maxsize=None)
def indexed_month_directories(
    destination_root: Path, year: int, month_number: int
) -> tuple[Path, ...]:
    month = normalized_words(MONTH_NAMES[month_number])
    results: list[Path] = []
    for root, directories, _files in os.walk(destination_root):
        current = Path(root)
        parts = [normalized_words(part) for part in current.parts]
        if len(parts) >= 2 and parts[-2] == "PAGOS" and parts[-1] == month:
            results.extend(current / name for name in directories)
            directories[:] = []
    return tuple(results)


def legacy_candidate_score(folder: Path, payment: Payment) -> int:
    score = 0
    if contains_phrase(folder.name, payment.loan_reference):
        score += 100

    try:
        file_names = [path.name for path in folder.iterdir() if path.is_file()]
    except OSError:
        file_names = []

    if payment.office_reference and any(
        contains_phrase(name, payment.office_reference) for name in file_names
    ):
        score += 1000
    return score


def legacy_choose_destination(payment: Payment, destination_root: Path) -> None:
    if not payment.value_date:
        payment.notes.append("No se pudo leer la fecha valor del comprobante.")
        return

    folders = indexed_month_directories(
        destination_root, payment.value_date.year, payment.value_date.month
    )
    loan_candidates = [
        folder for folder in folders if contains_phrase(folder.name, payment.loan_reference)
    ]

    if not loan_candidates and payment.office_reference:
        for folder in folders:
            try:
                if any(
                    path.is_file() and contains_phrase(path.name, payment.office_reference)
                    for path in folder.iterdir()
                ):
                    loan_candidates.append(folder)
            except OSError:
                continue

    if not loan_candidates:
        payment.notes.append(
            f"No se encontró carpeta de {MONTH_NAMES[payment.value_date.month]} "
            f"con préstamo {payment.loan_reference}."
        )
        return

    scored = sorted(
        ((legacy_candidate_score(folder, payment), folder) for folder in loan_candidates),
        key=lambda item: (-item[0], str(item[1]).casefold()),
    )
    best_score = scored[0][0]
    best = [folder for score, folder in scored if score == best_score]
    if len(best) != 1:
        payment.notes.append(
            "Coincidencia ambigua: " + " | ".join(str(folder) for folder in best)
        )
        return

    payment.destination = best[0]


@dataclass
class CandidateMatch:
    folder: Path
    score: int
    evidence: list[str]
    strong_exact: bool
    ocr_used: bool


def candidate_corpus(folder: Path, use_ocr: bool) -> tuple[str, bool]:
    try:
        files = [path for path in folder.iterdir() if path.is_file()]
    except OSError:
        return folder.name, False

    parts = [folder.name, *(path.name for path in files)]
    pdfs = [path for path in files if path.suffix.casefold() == ".pdf"]
    pdfs.sort(
        key=lambda path: (
            0
            if any(
                keyword in normalized_words(path.name)
                for keyword in ("OFICIO", "ESTADO CUENTA", "FORMULARIO", "CARATULA")
            )
            else 1,
            path.name.casefold(),
        )
    )
    ocr_used = False
    ocr_budget = 2
    for path in pdfs[:10]:
        try:
            text, used = searchable_pdf_text(
                path, use_ocr=use_ocr and ocr_budget > 0
            )
        except OSError:
            continue
        if used:
            ocr_budget -= 1
            ocr_used = True
        if text.strip():
            parts.append(text)
    return "\n".join(parts), ocr_used


def score_candidate_text(
    payment: Payment, folder: Path, corpus: str, ocr_used: bool = False
) -> CandidateMatch:
    score = 0
    evidence: list[str] = []
    strong_exact = False

    if payment.office_reference and contains_phrase(corpus, payment.office_reference):
        score += 60
        evidence.append(f"oficio exacto {payment.office_reference}")
        strong_exact = True

    if contains_phrase(corpus, payment.operation_reference):
        score += 60
        evidence.append(f"referencia SWIFT exacta {payment.operation_reference}")
        strong_exact = True

    normalized_corpus = f" {normalized_words(corpus)} "
    if payment.order_number:
        order_patterns = (
            f" PEDIDO NRO {payment.order_number} ",
            f" PEDIDO {payment.order_number} ",
        )
        if any(pattern in normalized_corpus for pattern in order_patterns):
            score += 15
            evidence.append(f"pedido {payment.order_number}")

    if payment.value_date and contains_value_date(corpus, payment.value_date):
        score += 10
        evidence.append(f"fecha valor {payment.value_date:%d/%m/%Y}")

    corpus_amounts = set(canonical_amounts(corpus))
    matched_amounts = [amount for amount in payment.amounts if amount in corpus_amounts]
    if matched_amounts:
        score += min(25, 20 + 5 * (len(matched_amounts) - 1))
        evidence.append(
            "monto " + ", ".join(f"{float(amount):,.2f}" for amount in matched_amounts)
        )

    beneficiary_coverage = token_coverage(payment.beneficiary, corpus)
    if beneficiary_coverage >= 0.75:
        score += 18
        evidence.append(f"beneficiario {beneficiary_coverage:.0%}")
    elif beneficiary_coverage >= 0.50:
        score += 10
        evidence.append(f"beneficiario parcial {beneficiary_coverage:.0%}")

    concept_coverage = token_coverage(payment.concept, corpus)
    if concept_coverage >= 0.60:
        score += 18
        evidence.append(f"concepto {concept_coverage:.0%}")
    elif concept_coverage >= 0.40:
        score += 10
        evidence.append(f"concepto parcial {concept_coverage:.0%}")

    if payment.currency and contains_phrase(corpus, payment.currency) and evidence:
        score += 4
        evidence.append(f"moneda {payment.currency}")

    return CandidateMatch(
        folder=folder,
        score=min(score, 100),
        evidence=evidence,
        strong_exact=strong_exact,
        ocr_used=ocr_used,
    )


def score_candidate_folder(
    payment: Payment, folder: Path, use_ocr: bool
) -> CandidateMatch:
    corpus, ocr_used = candidate_corpus(folder, use_ocr)
    return score_candidate_text(payment, folder, corpus, ocr_used)


def set_destination_match(
    payment: Payment,
    folder: Path,
    method: str,
    score: int,
    evidence: list[str],
    probable: bool = False,
    ocr_used: bool = False,
) -> None:
    payment.destination = folder
    payment.match_method = method
    payment.match_score = score
    payment.match_evidence = evidence
    payment.probable_match = probable
    payment.ocr_used = ocr_used
    if probable:
        payment.status = "COINCIDENCIA PROBABLE"
        payment.notes.append(
            "Destino sugerido por validación multifactor; requiere selección manual."
        )


def choose_best_scored_candidate(
    payment: Payment,
    folders: list[Path] | tuple[Path, ...],
    use_ocr: bool,
) -> bool:
    scored = sorted(
        (score_candidate_folder(payment, folder, use_ocr) for folder in folders),
        key=lambda item: (-item.score, str(item.folder).casefold()),
    )
    if not scored:
        return False

    best = scored[0]
    second_score = scored[1].score if len(scored) > 1 else 0
    margin = best.score - second_score
    tied = len(scored) > 1 and best.score == second_score

    dated_exact = [
        candidate
        for candidate in scored
        if candidate.strong_exact
        and any(
            item.startswith("fecha valor ") for item in candidate.evidence
        )
    ]
    if len(dated_exact) == 1:
        resolved = dated_exact[0]
        set_destination_match(
            payment,
            resolved.folder,
            "Fecha valor y referencia exactas",
            max(99, resolved.score),
            resolved.evidence,
            ocr_used=resolved.ocr_used,
        )
        return True
    if len(dated_exact) > 1:
        dated_families = {
            duplicate_folder_family(candidate.folder) for candidate in dated_exact
        }
        if len(dated_families) == 1:
            preferred = min(
                dated_exact, key=lambda item: duplicate_folder_rank(item.folder)
            )
            ignored = [
                candidate.folder.name
                for candidate in dated_exact
                if candidate.folder != preferred.folder
            ]
            evidence = list(preferred.evidence)
            evidence.append(f"carpeta original {preferred.folder.name}")
            set_destination_match(
                payment,
                preferred.folder,
                "Carpeta repetida resuelta por fecha y evidencia",
                max(99, preferred.score),
                evidence,
                ocr_used=preferred.ocr_used,
            )
            payment.notes.append(
                "El oficio y la fecha valor coinciden en carpetas repetidas; "
                f"se usó la carpeta original y se ignoró: {', '.join(ignored)}."
            )
            return True

    if tied:
        tied_best = [candidate for candidate in scored if candidate.score == best.score]
        same_duplicate_family = len(
            {duplicate_folder_family(candidate.folder) for candidate in tied_best}
        ) == 1
        decisive_evidence = [
            item
            for item in best.evidence
            if item.startswith(
                (
                    "oficio exacto ",
                    "referencia SWIFT exacta ",
                    "fecha valor ",
                    "monto ",
                )
            )
        ]
        has_date_or_operation = any(
            item.startswith(("fecha valor ", "referencia SWIFT exacta "))
            for item in decisive_evidence
        )
        if (
            same_duplicate_family
            and best.strong_exact
            and best.score >= 70
            and len(decisive_evidence) >= 2
            and has_date_or_operation
        ):
            preferred = min(tied_best, key=lambda item: duplicate_folder_rank(item.folder))
            ignored = [
                candidate.folder.name
                for candidate in tied_best
                if candidate.folder != preferred.folder
            ]
            evidence = list(preferred.evidence)
            evidence.append(f"carpeta original {preferred.folder.name}")
            set_destination_match(
                payment,
                preferred.folder,
                "Carpeta repetida resuelta por fecha y evidencia",
                max(99, preferred.score),
                evidence,
                ocr_used=preferred.ocr_used,
            )
            payment.notes.append(
                "Se encontraron carpetas repetidas con la misma evidencia; "
                f"se usó la carpeta original y se ignoró: {', '.join(ignored)}."
            )
            return True

    if best.strong_exact and best.score >= 60 and not tied and margin >= 10:
        set_destination_match(
            payment,
            best.folder,
            "Referencia exacta dentro de documentos",
            max(98, best.score),
            best.evidence,
            ocr_used=best.ocr_used,
        )
        return True

    independent_evidence = [
        item
        for item in best.evidence
        if not item.startswith("moneda ") and not item.startswith("concepto parcial")
    ]
    if (
        best.score >= 55
        and len(independent_evidence) >= 3
        and not tied
        and margin >= 15
    ):
        set_destination_match(
            payment,
            best.folder,
            "Contenido multifactor",
            best.score,
            best.evidence,
            probable=True,
            ocr_used=best.ocr_used,
        )
        return True

    if best.score:
        payment.notes.append(
            f"Mejor candidato insuficiente: {best.folder.name} "
            f"({best.score}%, margen {margin} puntos)."
        )
    return False


def choose_destination(
    payment: Payment, destination_root: Path, use_ocr: bool = True
) -> None:
    if not payment.value_date:
        payment.notes.append("No se pudo leer la fecha valor del comprobante.")
        return

    folders = indexed_month_directories(
        destination_root, payment.value_date.year, payment.value_date.month
    )
    loan_candidates: list[Path] = [
        folder for folder in folders if contains_phrase(folder.name, payment.loan_reference)
    ]

    if len(loan_candidates) == 1:
        set_destination_match(
            payment,
            loan_candidates[0],
            "Referencia de préstamo exacta",
            100,
            [f"préstamo exacto {payment.loan_reference}"],
        )
        return

    if len(loan_candidates) > 1 and payment.office_reference:
        office_candidates: list[Path] = []
        for folder in loan_candidates:
            try:
                if any(
                    path.is_file() and contains_phrase(path.name, payment.office_reference)
                    for path in folder.iterdir()
                ):
                    office_candidates.append(folder)
            except OSError:
                continue
        if len(office_candidates) == 1:
            set_destination_match(
                payment,
                office_candidates[0],
                "Préstamo y oficio exactos",
                100,
                [
                    f"préstamo exacto {payment.loan_reference}",
                    f"oficio exacto {payment.office_reference}",
                ],
            )
            return

    if not loan_candidates and payment.office_reference:
        office_candidates = []
        for folder in folders:
            try:
                if any(
                    path.is_file() and contains_phrase(path.name, payment.office_reference)
                    for path in folder.iterdir()
                ):
                    office_candidates.append(folder)
            except OSError:
                continue
        if len(office_candidates) == 1:
            set_destination_match(
                payment,
                office_candidates[0],
                "Número de oficio exacto",
                99,
                [f"oficio exacto {payment.office_reference}"],
            )
            return

    if (
        payment.document_type == "TRANSFERENCIA INTERNA"
        and payment.loan_reference
        and not loan_candidates
    ):
        payment.notes.append(
            f"No se encontró una carpeta exacta para el préstamo interno "
            f"{payment.loan_reference} ni un oficio único en "
            f"{MONTH_NAMES[payment.value_date.month]}."
        )
        return

    scored_scope = loan_candidates if loan_candidates else folders
    if choose_best_scored_candidate(payment, scored_scope, use_ocr):
        return

    if loan_candidates:
        payment.notes.append(
            "Coincidencia ambigua por préstamo: "
            + " | ".join(str(folder) for folder in loan_candidates)
        )
    else:
        searched_by = []
        if payment.loan_reference:
            searched_by.append(f"préstamo {payment.loan_reference}")
        if payment.office_reference:
            searched_by.append(f"oficio {payment.office_reference}")
        searched_by.append("contenido multifactor")
        payment.notes.append(
            f"No se encontró una carpeta segura de {MONTH_NAMES[payment.value_date.month]} "
            f"mediante {', '.join(searched_by)}."
        )


def make_payment(
    marker: Path,
    ack_base: Path,
    destination_root: Path,
    use_ocr: bool = True,
    responsible: str = "",
) -> Payment:
    marker_match = DOUBLE_SIGNED_RE.fullmatch(marker.name)
    folder_match = SOURCE_FOLDER_RE.fullmatch(marker.parent.name)
    if not marker_match or not folder_match:
        raise ValueError(f"Nombre no reconocido: {marker}")

    operation_reference = (
        f"{marker_match.group('kind').upper()}-01-{marker_match.group('operation')}"
    )
    voucher_number = folder_match.group("voucher")
    payment = Payment(
        marker=marker,
        source_folder=marker.parent,
        operation_reference=operation_reference,
        voucher_number=voucher_number,
        responsible=responsible or marker.parent.parent.name.upper(),
    )

    accounting_source = find_accounting_source(marker.parent, voucher_number)
    if not accounting_source:
        payment.notes.append(
            f"Falta un comprobante firmado que corresponda a {voucher_number}."
        )
        payment.status = "ERROR"
        return payment
    payment.accounting_source = accounting_source

    try:
        text = extract_pdf_text(accounting_source)
        fields = extract_payment_fields(text)
        payment.value_date = fields["value_date"]
        payment.loan_reference = str(fields["loan_reference"])
        payment.office_reference = str(fields["office_reference"])
        payment.concept = str(fields["concept"])
        payment.beneficiary = str(fields["beneficiary"])
        payment.order_number = str(fields["order_number"])
        payment.currency = str(fields["currency"])
        payment.amounts = tuple(fields["amounts"])
    except Exception as exc:
        payment.notes.append(f"No se pudo leer el comprobante: {type(exc).__name__}: {exc}")
        payment.status = "ERROR"
        return payment

    if not payment.value_date:
        payment.notes.append("No se encontró FECHA VALOR.")
    if payment.notes:
        payment.status = "ERROR"
        return payment

    ack_directory = resolve_ack_directory(ack_base, payment.value_date)
    payment.ack_source = ack_directory / f"{operation_reference}.pdf"
    if not payment.ack_source.is_file():
        payment.notes.append(f"ACK pendiente: {payment.ack_source}.")

    choose_destination(payment, destination_root, use_ocr=use_ocr)
    if not payment.destination:
        payment.status = "REVISAR"
        return payment

    date_suffix = (
        f"{payment.value_date.day:02d}{MONTH_ABBR[payment.value_date.month]}"
        f"{payment.value_date.year:04d}"
    )
    payment.accounting_target = payment.destination / (
        f"Comprobante Contable No. {voucher_number} {date_suffix}.pdf"
    )
    payment.ack_target = payment.destination / f"{operation_reference}.pdf"
    return payment


def make_internal_payment(
    accounting_source: Path,
    destination_root: Path,
    use_ocr: bool = True,
    responsible: str = "",
) -> Payment:
    folder_match = SOURCE_FOLDER_RE.fullmatch(accounting_source.parent.name)
    if not folder_match:
        raise ValueError(f"Carpeta no reconocida: {accounting_source.parent}")

    voucher_number = folder_match.group("voucher")
    text, ocr_used = searchable_pdf_text(accounting_source, use_ocr=use_ocr)
    operation_reference = extract_internal_operation(text)
    loan_reference = extract_internal_loan_reference(text)
    payment = Payment(
        marker=accounting_source,
        source_folder=accounting_source.parent,
        operation_reference=operation_reference or "TRANSFERENCIA INTERNA",
        voucher_number=voucher_number,
        responsible=responsible or accounting_source.parent.parent.name.upper(),
        document_type="TRANSFERENCIA INTERNA",
        requires_ack=False,
        accounting_source=accounting_source,
        ocr_used=ocr_used,
    )

    fields = extract_payment_fields(text)
    payment.value_date = fields["value_date"]
    payment.loan_reference = loan_reference
    payment.office_reference = str(fields["office_reference"])
    payment.concept = str(fields["concept"])
    payment.beneficiary = str(fields["beneficiary"])
    payment.order_number = str(fields["order_number"])
    payment.currency = str(fields["currency"])
    payment.amounts = tuple(fields["amounts"])

    if not is_internal_transfer(text):
        payment.notes.append(
            "No se confirmó DTR100, operación NC-01 y referencia explícita de préstamo."
        )
    if not payment.value_date:
        payment.notes.append("No se encontró la fecha del comprobante interno.")
    if payment.notes:
        payment.status = "ERROR"
        return payment

    choose_destination(payment, destination_root, use_ocr=use_ocr)
    if not payment.destination:
        payment.status = "REVISAR"
        return payment

    date_suffix = (
        f"{payment.value_date.day:02d}{MONTH_ABBR[payment.value_date.month]}"
        f"{payment.value_date.year:04d}"
    )
    payment.accounting_target = payment.destination / (
        f"Comprobante Contable No. {voucher_number} {date_suffix}.pdf"
    )
    return payment


def classify(payment: Payment) -> None:
    if payment.status in {"ERROR", "REVISAR"}:
        return
    assert payment.accounting_source and payment.accounting_target

    def document_state(source: Path, target: Path) -> str:
        if not target.exists():
            return "missing"
        return "same" if same_file_content(source, target) else "conflict"

    accounting_state = document_state(
        payment.accounting_source, payment.accounting_target
    )
    states = [accounting_state]
    conflicts = ["comprobante"] if accounting_state == "conflict" else []

    ack_available = not payment.requires_ack
    if payment.requires_ack:
        assert payment.ack_source and payment.ack_target
        ack_available = payment.ack_source.is_file()
        if ack_available:
            ack_state = document_state(payment.ack_source, payment.ack_target)
            states.append(ack_state)
            if ack_state == "conflict":
                conflicts.append("ACK")

    if conflicts:
        payment.status = "CONFLICTO"
        payment.notes.append(
            "Ya existe contenido diferente en destino: " + ", ".join(conflicts) + "."
        )
    elif ack_available and all(state == "same" for state in states):
        payment.status = "YA ARCHIVADO"
    elif payment.probable_match:
        payment.status = "COINCIDENCIA PROBABLE"
    elif payment.requires_ack and not ack_available:
        payment.status = (
            "ACK PENDIENTE" if accounting_state == "same" else "LISTO COMPROBANTE"
        )
    elif "same" in states:
        payment.status = "LISTO PARCIAL"
    else:
        payment.status = "LISTO"


def atomic_copy_without_overwrite(source: Path, target: Path) -> str:
    if target.exists():
        return "same" if same_file_content(source, target) else "conflict"

    # No incluimos el nombre final dentro del temporal: en carpetas de red largas
    # podía llevar la ruta a 260 caracteres y Windows devolvía WinError 3.
    temporary = target.with_name(f".gestor-{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        if not same_file_content(source, temporary):
            raise OSError(f"Falló la verificación SHA-256 de {target.name}")
        os.rename(temporary, target)  # En Windows no reemplaza un destino existente.
        if not same_file_content(source, target):
            raise OSError(f"Falló la verificación final SHA-256 de {target.name}")
        return "copied"
    finally:
        if temporary.exists():
            temporary.unlink()


def execute(payment: Payment, allow_probable: bool = False) -> None:
    allowed = {"LISTO", "LISTO PARCIAL", "LISTO COMPROBANTE"}
    if allow_probable:
        allowed.add("COINCIDENCIA PROBABLE")
    if payment.status not in allowed:
        return
    assert payment.accounting_source and payment.accounting_target

    results = []
    documents = [("comprobante", payment.accounting_source, payment.accounting_target)]
    if payment.requires_ack:
        assert payment.ack_source and payment.ack_target
        if payment.ack_source.is_file():
            documents.append(("ACK", payment.ack_source, payment.ack_target))
    for label, source, target in documents:
        result = atomic_copy_without_overwrite(source, target)
        results.append(f"{label}={result}")
        if result == "conflict":
            payment.status = "CONFLICTO"
            payment.notes.append(f"Conflicto al copiar {label}: {target}")
            return

    if payment.requires_ack and payment.ack_source and not payment.ack_source.is_file():
        payment.status = "ACK PENDIENTE"
    else:
        payment.status = "ARCHIVADO"
    payment.notes.append(", ".join(results))


def print_report(payments: list[Payment], did_execute: bool) -> None:
    print()
    print("RESULTADO DE ARCHIVO" if did_execute else "VISTA PREVIA - NO SE COPIÓ NINGÚN ARCHIVO")
    print("=" * 100)
    for payment in payments:
        destination = str(payment.destination) if payment.destination else "-"
        loan = payment.loan_reference or "-"
        print(
            f"{payment.voucher_number:<9}  {payment.operation_reference:<20}  "
            f"{loan:<16}  {payment.status}"
        )
        print(f"  Destino: {destination}")
        for note in payment.notes:
            print(f"  Nota: {note}")
    print("=" * 100)
    counts: dict[str, int] = {}
    for payment in payments:
        counts[payment.status] = counts.get(payment.status, 0) + 1
    print("Resumen: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Empareja pagos con doble firma, comprobantes contables y ACK, y los copia "
            "a la carpeta correcta. Por defecto solo muestra una vista previa."
        )
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--ack-base", type=Path, default=DEFAULT_ACK_BASE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Incluye subcarpetas históricas como 01. Sin esta opción solo revisa pagos recientes.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Copia los archivos LISTO. Sin esta opción no modifica las carpetas de red.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for label, path in (
        ("origen", args.source),
        ("base de ACK", args.ack_base),
        ("destino", args.destination),
    ):
        if not path.exists():
            raise SystemExit(f"No existe la ruta de {label}: {path}")

    markers = discover_markers(args.source, args.recursive)
    internal_documents = discover_internal_accounting_documents(
        args.source, args.recursive
    )
    if not markers and not internal_documents:
        print("No se encontraron pagos firmados ni transferencias internas vinculadas.")
        return 0

    payments: list[Payment] = []
    for marker in markers:
        try:
            payment = make_payment(marker, args.ack_base, args.destination)
        except Exception as exc:
            folder_match = SOURCE_FOLDER_RE.fullmatch(marker.parent.name)
            voucher = folder_match.group("voucher") if folder_match else "DESCONOCIDO"
            operation_match = DOUBLE_SIGNED_RE.fullmatch(marker.name)
            operation = (
                f"{operation_match.group('kind').upper()}-01-{operation_match.group('operation')}"
                if operation_match
                else marker.stem
            )
            payment = Payment(marker, marker.parent, operation, voucher)
            payment.status = "ERROR"
            payment.notes.append(f"{type(exc).__name__}: {exc}")
        classify(payment)
        payments.append(payment)

    for accounting_source in internal_documents:
        try:
            payment = make_internal_payment(accounting_source, args.destination)
        except Exception as exc:
            folder_match = SOURCE_FOLDER_RE.fullmatch(accounting_source.parent.name)
            voucher = folder_match.group("voucher") if folder_match else "DESCONOCIDO"
            payment = Payment(
                accounting_source,
                accounting_source.parent,
                "TRANSFERENCIA INTERNA",
                voucher,
                document_type="TRANSFERENCIA INTERNA",
                requires_ack=False,
            )
            payment.status = "ERROR"
            payment.notes.append(f"{type(exc).__name__}: {exc}")
        classify(payment)
        payments.append(payment)

    if args.execute:
        for payment in payments:
            execute(payment)

    print_report(payments, args.execute)
    unsafe = {"ERROR", "REVISAR", "CONFLICTO"}
    return 2 if any(payment.status in unsafe for payment in payments) else 0


if __name__ == "__main__":
    raise SystemExit(main())
