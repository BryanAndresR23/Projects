from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import unicodedata
from pathlib import Path
from typing import Any

from flask import jsonify, request


GUIDE_PATH = Path(
    r"Z:\GISI\SSFI\GESTIÓN PAGOS INTERNACIONALES\2026\DEUDA EXTERNA PÚBLICA"
    r"\Guía Acreedores Internacionales\Guía Bancos Corresponsales 22Abr2026.pdf"
)
MATRIX_PATH = Path(
    r"C:\Users\bromo\OneDrive - BANCO CENTRAL DEL ECUADOR\Prestamo Realizados.xlsx"
)
DATA_DIR = Path(__file__).resolve().parent / "corresponsales_data"
HISTORY_FILE = DATA_DIR / "matrix_history.json"
READER_FILE = Path(__file__).resolve().parent / "correspondent_history_reader.mjs"
_history_lock = threading.Lock()

CORRESPONDENTS = {
    "CITIBANK": {"short": "CITIBANK", "full": "CITIBANK N.A. NEW YORK"},
    "FEDERAL": {"short": "FEDERAL", "full": "FEDERAL RESERVE BANK OF NEW YORK"},
    "JPMORGAN": {"short": "JPMORGAN", "full": "J.P. MORGAN CHASE BANK N.A."},
    "FLAR": {"short": "FLAR", "full": "FONDO LATINOAMERICANO DE RESERVAS"},
    "BANCO DE ESPAÑA": {"short": "BANCO DE ESPAÑA", "full": "BANCO DE ESPAÑA"},
    "COMMERZBANK": {"short": "COMMERZBANK", "full": "COMMERZ BANK A.G. FRANKFURT"},
}


def normalize(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", text.upper()).split())


def loan_key(value: str | None) -> str:
    key = normalize(value)
    key = re.sub(r"^CAF\s+", "CFA ", key)
    return re.sub(r"\s+EC$", "", key)


def canonical_correspondent(value: str | None) -> str:
    text = normalize(value)
    if "CITI" in text:
        return "CITIBANK"
    if "FED" in text:
        return "FEDERAL"
    if re.search(r"J\s*P\s*MORGAN|JP MORGAN|JPMORGAN", text):
        return "JPMORGAN"
    if "ESPANA" in text:
        return "BANCO DE ESPAÑA"
    if "COMMERZ" in text:
        return "COMMERZBANK"
    if "FLAR" in text:
        return "FLAR"
    return text if text in CORRESPONDENTS else ""


def extract_beneficiary_bank(text: str | None) -> str:
    normalized = normalize(text)
    patterns = (
        (r"CITIBANK|CITIUS33|CITI BANK", "CITIBANK N.A."),
        (r"JPMORGAN|J P MORGAN|CHASUS33", "J.P. MORGAN CHASE BANK N.A."),
        (r"BANK OF AMERICA|BOFAUS", "BANK OF AMERICA"),
        (r"BANK OF NEW YORK|BNYM|IRVTUS", "THE BANK OF NEW YORK MELLON"),
        (r"COMMERZBANK|COBADE", "COMMERZBANK A.G."),
        (r"BANCO DE ESPANA|ESPBES", "BANCO DE ESPAÑA"),
    )
    for pattern, bank in patterns:
        if re.search(pattern, normalized):
            return bank
    label = re.search(
        r"(?:BCO DEL BENEF|BANCO DEL BENEFICIARIO|BENEFICIARY BANK)\s*:?\s*([A-Z0-9 .,&-]{4,70})",
        normalized,
    )
    return label.group(1).strip(" .,-") if label else ""


def _node_executable() -> str:
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    if bundled.is_file():
        return str(bundled)
    found = shutil.which("node")
    if found:
        return found
    raise FileNotFoundError("No se encontró Node.js para consultar la matriz histórica.")


def refresh_history(force: bool = False) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not MATRIX_PATH.is_file():
        return {"rows": 0, "totals": {}, "by_loan": {}, "by_profile": {}, "by_lender": {}, "error": f"No se encuentra {MATRIX_PATH}"}
    matrix_mtime = MATRIX_PATH.stat().st_mtime
    if not force and HISTORY_FILE.is_file() and HISTORY_FILE.stat().st_mtime >= matrix_mtime:
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    with _history_lock:
        if not force and HISTORY_FILE.is_file() and HISTORY_FILE.stat().st_mtime >= matrix_mtime:
            try:
                return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        completed = subprocess.run(
            [_node_executable(), str(READER_FILE), str(MATRIX_PATH), str(HISTORY_FILE)],
            cwd=str(Path(__file__).resolve().parent), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode or not HISTORY_FILE.is_file():
            message = completed.stderr.strip() or completed.stdout.strip() or "No se pudo consultar la matriz histórica."
            return {"rows": 0, "totals": {}, "by_loan": {}, "by_profile": {}, "by_lender": {}, "error": message}
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))


def _distribution(history: dict[str, Any], section: str, key: str) -> dict[str, int]:
    raw = history.get(section, {}).get(key, {}) if isinstance(history, dict) else {}
    return {canonical_correspondent(name): int(count) for name, count in raw.items() if canonical_correspondent(name)}


def _dominant(distribution: dict[str, int]) -> tuple[str, int, int]:
    if not distribution:
        return "", 0, 0
    bank, count = max(distribution.items(), key=lambda item: item[1])
    return bank, count, sum(distribution.values())


def classify_correspondent(
    *, loan_reference: str | None, lender: str | None, borrower: str | None,
    currency: str | None, beneficiary_bank: str | None = None,
    source_text: str | None = None, history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    loan = loan_key(loan_reference)
    lender_n = normalize(lender)
    borrower_n = normalize(borrower)
    currency_n = normalize(currency)
    bank = str(beneficiary_bank or "").strip() or extract_beneficiary_bank(source_text)
    bank_n = normalize(bank)
    sovereign_borrower = (
        borrower_n in {"MEF", "MDEP", "REPUBLICA ECUADOR", "REPUBLICA DEL ECUADOR"}
        or "MINISTERIO DE ECONOMIA Y FINANZAS" in borrower_n
        or "REPUBLICA DEL ECUADOR" in borrower_n
        or "REPUBLICA ECUADOR" in borrower_n
    )
    exact: dict[str, int] = {}
    profile: dict[str, int] = {}
    profile_key = "|".join((lender_n, borrower_n, currency_n))
    matrix_evidence = {"exact_loan": exact, "profile": profile}

    def result(key: str, confidence: int, method: str, reason: str) -> dict[str, Any]:
        info = CORRESPONDENTS[key]
        final_evidence = [reason]
        if exact:
            final_evidence.append("Matriz, préstamo: " + ", ".join(f"{name} {count}" for name, count in sorted(exact.items())))
        elif profile:
            final_evidence.append("Matriz, perfil: " + ", ".join(f"{name} {count}" for name, count in sorted(profile.items())))
        return {
            "key": key, "correspondent": info["short"], "correspondent_full": info["full"],
            "confidence": confidence, "method": method, "evidence": final_evidence,
            "beneficiary_bank": bank, "matrix_evidence": matrix_evidence,
            "guide": str(GUIDE_PATH),
        }

    # Regla indicada por el usuario y confirmada por la guía: Citibank prevalece.
    if "CITI" in bank_n:
        return result("CITIBANK", 100, "Banco beneficiario + guía", "El banco beneficiario identificado es Citibank.")
    if currency_n == "EUR":
        return result("BANCO DE ESPAÑA", 100, "Moneda + guía", "Todo pago de deuda al exterior en EUR se canaliza por Banco de España.")
    if currency_n and currency_n not in {"USD", "EUR"}:
        return result("COMMERZBANK", 100, "Moneda + guía", f"La moneda {currency_n} es distinta de USD y EUR.")
    if lender_n == "FLAR" or "FLAR" in loan:
        return result("FLAR", 100, "Acreedor + guía", "La deuda del MEF a favor de FLAR se canaliza por FLAR.")
    history = history if history is not None else refresh_history()
    exact = _distribution(history, "by_loan", loan)
    profile = _distribution(history, "by_profile", profile_key)
    matrix_evidence["exact_loan"] = exact
    matrix_evidence["profile"] = profile
    exact_bank, exact_count, exact_total = _dominant(exact)
    profile_bank, profile_count, profile_total = _dominant(profile)

    # Un préstamo histórico uniforme captura excepciones por banco beneficiario.
    if exact_bank and exact_total and exact_count / exact_total >= 0.75:
        if currency_n == "USD" and sovereign_borrower and exact_bank != "FEDERAL":
            return result(
                "FEDERAL",
                100,
                "República del Ecuador + guía",
                "El prestatario es la República del Ecuador (MEF/MDEP); la regla soberana en USD prevalece sobre un antecedente histórico distinto.",
            )
        confidence = 96 if exact_total >= 2 else 92
        return result(exact_bank, confidence, "Préstamo exacto en matriz", f"El mismo préstamo registra {exact_count} de {exact_total} operación(es) con {CORRESPONDENTS[exact_bank]['short']}.")
    if currency_n == "USD" and sovereign_borrower:
        return result(
            "FEDERAL",
            100,
            "República del Ecuador + guía",
            "El prestatario es la República del Ecuador (MEF/MDEP); el pago soberano en USD se canaliza por la Reserva Federal.",
        )
    if profile_bank and profile_total >= 2 and profile_count / profile_total >= 0.8:
        return result(profile_bank, 89, "Perfil histórico en matriz", f"El perfil acreedor/prestatario/moneda registra {profile_count} de {profile_total} operación(es) con {CORRESPONDENTS[profile_bank]['short']}.")

    if currency_n == "USD":
        if borrower_n in {"MEF", "MDEP"}:
            federal_tokens = ("BID", "BIRF", "CAF", "CFA", "FIDA", "FMI", "BONO", "BNYM", "BANK OF NEW YORK", "BEI", "BANK OF CHINA", "DEUTSCHE")
            if any(token in lender_n or token in loan for token in federal_tokens):
                return result("FEDERAL", 90, "Prestatario y acreedor + guía", "Pago USD del MEF a favor de BID/bancos/BIRF/bonos/CAF/FIDA/FMI.")
            return result("JPMORGAN", 88, "Regla general MEF + guía", "Pago USD del MEF; no se identificó beneficiario Citibank ni acreedor asignado a FED.")
        if borrower_n:
            return result("JPMORGAN", 90, "Entidad pública/GAD + guía", "Pago de deuda en USD de entidad pública o GAD.")

    return {
        "key": "", "correspondent": "REVISAR", "correspondent_full": "Sin clasificación segura",
        "confidence": 0, "method": "Datos insuficientes", "evidence": ["Falta moneda o evidencia suficiente."],
        "beneficiary_bank": bank, "matrix_evidence": matrix_evidence, "guide": str(GUIDE_PATH),
    }


def register_corresponsales_routes(app, _base_dir: str) -> None:
    @app.get("/api/corresponsales/status")
    def correspondent_status():
        history = refresh_history()
        return jsonify({
            "ok": True, "guide": str(GUIDE_PATH), "guide_exists": GUIDE_PATH.is_file(),
            "matrix": str(MATRIX_PATH), "matrix_exists": MATRIX_PATH.is_file(),
            "history_rows": history.get("rows", 0), "history_totals": history.get("totals", {}),
            "history_error": history.get("error"),
        })

    @app.post("/api/corresponsales/classify")
    def correspondent_classify():
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "classification": classify_correspondent(
            loan_reference=payload.get("loan_reference"), lender=payload.get("lender"),
            borrower=payload.get("borrower"), currency=payload.get("currency"),
            beneficiary_bank=payload.get("beneficiary_bank"), source_text=payload.get("source_text"),
        )})
