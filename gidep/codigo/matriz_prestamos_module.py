from __future__ import annotations

"""Captura asistida de operaciones SGI para la matriz Prestamo Realizados.

La captura nunca se escribe directamente. Primero se ejecuta OCR local sobre
regiones conocidas, luego el usuario revisa los campos y finalmente confirma
una escritura transaccional con respaldo y control de duplicados.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import g, jsonify, request, send_file, session
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from werkzeug.utils import secure_filename

from corresponsales_module import classify_correspondent, extract_beneficiary_bank


DEFAULT_WORKBOOK = Path(
    r"C:\Users\bromo\OneDrive - BANCO CENTRAL DEL ECUADOR\Prestamo Realizados.xlsx"
)
ALLOWED_IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
STATE_FILENAME = "matriz_prestamos_state.json"
CONFIG_FILENAME = "matriz_prestamos_config.json"

REGIONS = {
    "01_cabecera": (0.00, 0.05, 0.76, 0.18),
    "02_prestamo": (0.00, 0.16, 0.73, 0.29),
    "03_orden": (0.00, 0.27, 0.73, 0.48),
    "04_beneficiario": (0.00, 0.47, 0.73, 0.76),
    "05_banco": (0.00, 0.67, 0.73, 0.77),
    "06_debitos": (0.00, 0.76, 1.00, 1.00),
    "10_pedido": (0.38, 0.055, 0.47, 0.105),
    "11_refer_swift": (0.51, 0.115, 0.73, 0.175),
    "12_prestamo_ref": (0.17, 0.170, 0.45, 0.230),
    "13_vencimiento": (0.49, 0.170, 0.60, 0.230),
    "14_oficio": (0.035, 0.280, 0.26, 0.340),
    "15_fecha_oficio": (0.255, 0.280, 0.40, 0.340),
    "16_total_giro": (0.035, 0.325, 0.26, 0.390),
    "17_fecha_valor": (0.255, 0.325, 0.40, 0.390),
    "18_moneda": (0.42, 0.280, 0.56, 0.340),
    "19_beneficiario": (0.03, 0.510, 0.53, 0.575),
    "20_corresponsal": (0.03, 0.670, 0.47, 0.735),
}

_base_dir: Path | None = None
_write_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _state_path(base_dir: str | Path) -> Path:
    return Path(base_dir) / STATE_FILENAME


def _config_path(base_dir: str | Path) -> Path:
    return Path(base_dir) / CONFIG_FILENAME


def load_state(base_dir: str | Path) -> dict[str, Any]:
    try:
        state = json.loads(_state_path(base_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    state.setdefault("drafts", {})
    state.setdefault("history", [])
    return state


def save_state(base_dir: str | Path, state: dict[str, Any]) -> None:
    _atomic_json(_state_path(base_dir), state)


def load_config(base_dir: str | Path) -> dict[str, Any]:
    config = {"workbook": str(DEFAULT_WORKBOOK)}
    try:
        raw = json.loads(_config_path(base_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raw = {}
    if isinstance(raw, dict) and str(raw.get("workbook") or "").strip():
        config["workbook"] = str(raw["workbook"]).strip()
    return config


def save_config(base_dir: str | Path, workbook: str) -> dict[str, Any]:
    workbook = str(workbook or "").strip()
    if not workbook:
        raise ValueError("La ruta del Excel no puede quedar vacía.")
    if Path(workbook).suffix.casefold() != ".xlsx":
        raise ValueError("La matriz debe ser un archivo .xlsx.")
    config = {"workbook": workbook}
    _atomic_json(_config_path(base_dir), config)
    return config


def _plain_digits(value: str) -> str:
    translation = str.maketrans({"O": "0", "I": "1", "L": "1", "S": "5"})
    return re.sub(r"\D", "", str(value or "").upper().translate(translation))


def _money(value: str) -> float | None:
    cleaned = re.sub(r"[^0-9,.-]", "", str(value or ""))
    if not re.search(r"\d", cleaned):
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif cleaned.count(",") == 1 and len(cleaned.rsplit(",", 1)[-1]) == 2:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def _year_hint(text: str, refer_swift: str) -> int:
    match = re.search(r"\b(20\d{2})\b", text)
    if match:
        return int(match.group(1))
    body = _plain_digits(refer_swift)
    if len(body) >= 5:
        return 2000 + int(body[3:5])
    return datetime.now().year


def _date_from_text(text: str, year: int) -> str:
    match = re.search(r"\b([0-3]?\d)[/.-]([01]?\d)(?:[/.-](\d{2,4}))?\b", text or "")
    if not match:
        return ""
    day, month = int(match.group(1)), int(match.group(2))
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return ""


def _extract_swift(text: str, pedido: str) -> tuple[str, bool]:
    match = re.search(r"\b(TF|GS)\s*[- ]\s*0[1IL]\s*[- ]\s*([0-9OILS]{8,12})", text.upper())
    if not match:
        return "", False
    prefix = match.group(1)
    body = _plain_digits(match.group(2))
    inferred = False
    if pedido and len(body) < 10 and len(body) >= 5:
        body = body[:5] + pedido.zfill(5)
        inferred = True
    return (f"{prefix}-01-{body}" if len(body) >= 9 else ""), inferred


def _extract_loan(text: str) -> str:
    patterns = (
        r"\b(?:BIRF|BID|BEI|CAF|AFD|KFW|JICA|FMI|FLAR)[- ][A-Z0-9][A-Z0-9-]{2,24}\b",
        r"\b\d{8,10}\b",
    )
    for pattern in patterns:
        matches = re.findall(pattern, text.upper())
        if matches:
            value = matches[-1].strip(" -")
            return re.sub(r"^(BIRF|BID|BEI|CAF|AFD|KFW|JICA|FMI|FLAR)\s+", r"\1-", value)
    return ""


def _extract_office(text: str) -> str:
    match = re.search(r"\b[A-Z]{2,12}-[A-Z]{2,12}-20\d{2}-\d{3,5}-[A-Z]\b", text.upper())
    return match.group(0) if match else ""


def _extract_amount(records: dict[str, str]) -> tuple[float | None, str]:
    order = records.get("03_orden", "")
    components = []
    for label in (r"(?:INT|INTER[EÉ]S)", r"(?:COM|COMISI[OÓ]N)", r"(?:CAP|CAPITAL)"):
        match = re.search(label + r"\s*([0-9][0-9,.]*[0-9])", order, re.I)
        amount = _money(match.group(1)) if match else None
        if amount is not None and amount > 0:
            components.append(amount)
    if components:
        return round(sum(components), 2), "Suma de capital/interés/comisión"
    total = records.get("16_total_giro", "")
    match = re.search(r"([0-9][0-9,.]+)", total)
    return (_money(match.group(1)), "Total del giro") if match else (None, "No identificado")


def parse_ocr_records(
    rows: list[dict[str, Any]], upload_time: datetime | None = None, username: str = ""
) -> dict[str, Any]:
    records = {str(row.get("name") or ""): str(row.get("text") or "") for row in rows}
    full = " ".join(records.values())
    pedido_match = re.search(r"\b(?:PEDIDO|p)\s*:?\s*(\d{1,5})\b", full, re.I)
    pedido = pedido_match.group(1) if pedido_match else ""
    swift, swift_inferred = _extract_swift(
        records.get("11_refer_swift", "") + " " + records.get("01_cabecera", ""), pedido
    )
    loan = _extract_loan(records.get("12_prestamo_ref", "") + " " + records.get("03_orden", ""))
    office = _extract_office(records.get("14_oficio", "") + " " + records.get("03_orden", ""))
    year = _year_hint(office + " " + full, swift)
    value_date = _date_from_text(records.get("17_fecha_valor", ""), year)
    if not value_date:
        value_date = _date_from_text(records.get("13_vencimiento", ""), year)
    amount, amount_method = _extract_amount(records)
    currency_match = re.search(r"\b(USD|EUR|JPY|CNY|CHF|GBP)\b", records.get("18_moneda", "") + " " + full)
    currency = currency_match.group(1) if currency_match else "USD"
    prefix = loan.split("-", 1)[0] if loan else ""
    lender = prefix if prefix in {"BIRF", "BID", "BEI", "CAF", "AFD", "KFW", "JICA", "FMI", "FLAR"} else ""
    borrower = "MEF" if re.search(r"MINISTERIO\s+DE\s+ECON", full, re.I) else ""
    bank_text = " ".join(
        (
            records.get("04_beneficiario", ""),
            records.get("05_banco", ""),
            records.get("19_beneficiario", ""),
        )
    )
    beneficiary_bank = extract_beneficiary_bank(bank_text)
    classification = classify_correspondent(
        loan_reference=loan,
        lender=lender,
        borrower=borrower,
        currency=currency,
        beneficiary_bank=beneficiary_bank,
        source_text=full,
    )
    correspondent = str(classification.get("correspondent") or "")
    uploaded = upload_time or datetime.now().astimezone()
    warnings: list[str] = []
    if swift_inferred:
        warnings.append("La última parte de la referencia SWIFT se completó con el número de pedido; revísela.")
    if records.get("17_fecha_valor") and str(year) not in records.get("17_fecha_valor", ""):
        warnings.append(f"El año de la fecha valor se normalizó a {year} usando el oficio/referencia.")
    required_found = sum(bool(value) for value in (loan, swift, lender, borrower, amount, currency, value_date))
    confidence = round(required_found / 7 * 100)
    if confidence < 100:
        warnings.append("Hay campos obligatorios sin identificar; complételos antes de guardar.")
    if correspondent == "REVISAR":
        warnings.append("No se identificó un corresponsal seguro; revise el banco beneficiario y la moneda.")
    notes = " · ".join(filter(None, (f"Oficio {office}" if office else "", f"Pedido {pedido}" if pedido else "")))
    return {
        "entry": {
            "entry_date": uploaded.date().isoformat(),
            "reference_loan": loan,
            "refer_swift": swift,
            "local_operation": "",
            "lender": lender,
            "borrower": borrower,
            "amount_usd": amount if currency == "USD" else None,
            "amount_other": amount if currency != "USD" else None,
            "currency": currency,
            "correspondent": correspondent,
            "beneficiary_bank": beneficiary_bank,
            "correspondent_method": classification.get("method", ""),
            "correspondent_confidence": classification.get("confidence", 0),
            "correspondent_evidence": classification.get("evidence", []),
            "other_currency_accounting_date": "",
            "value_date": value_date,
            "status": "Ingresado",
            "notes": notes,
            "user": username.lower(),
            "office_reference": office,
            "order_number": pedido,
        },
        "confidence": confidence,
        "warnings": warnings,
        "evidence": {
            "amount_method": amount_method,
            "swift_inferred_from_order": swift_inferred,
            "year_hint": year,
        },
        "raw_ocr": records,
    }


def _prepare_crops(image_path: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as source:
        rgb = source.convert("RGB")
        width, height = rgb.size
        if width < 700 or height < 400:
            raise ValueError("La captura tiene muy poca resolución. Use una captura completa de la pantalla SGI.")
        for name, (left, top, right, bottom) in REGIONS.items():
            crop = rgb.crop((round(width * left), round(height * top), round(width * right), round(height * bottom)))
            scale = 4 if name.startswith(("1", "2")) else 2
            crop = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
            gray = ImageOps.grayscale(crop)
            gray = ImageOps.autocontrast(gray, cutoff=1)
            gray = ImageEnhance.Contrast(gray).enhance(1.35).filter(ImageFilter.SHARPEN)
            gray.save(output / f"{name}.png")


def _run_ocr(base_dir: Path, crops: Path) -> list[dict[str, Any]]:
    script = base_dir / "matriz_prestamos_ocr.ps1"
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    completed = None
    for attempt in range(3):
        completed = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), "-ImageDirectory", str(crops)],
            cwd=str(base_dir), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode == 0 and completed.stdout.strip():
            break
        if attempt < 2:
            time.sleep(1.0)
    if completed is None or completed.returncode:
        detail = (completed.stderr if completed else "").strip()
        code = completed.returncode if completed else "sin proceso"
        raise RuntimeError(detail or f"No fue posible ejecutar el OCR local (código {code}).")
    payload = json.loads(completed.stdout.lstrip("\ufeff").strip() or "[]")
    return payload if isinstance(payload, list) else [payload]


def analyze_image(base_dir: str | Path, image_path: Path, username: str) -> dict[str, Any]:
    base = Path(base_dir)
    crop_dir = base / "capturas_matriz_prestamos" / "ocr" / image_path.stem
    _prepare_crops(image_path, crop_dir)
    parsed = parse_ocr_records(_run_ocr(base, crop_dir), datetime.fromtimestamp(image_path.stat().st_ctime).astimezone(), username)
    draft_id = uuid.uuid4().hex
    state = load_state(base)
    state["drafts"][draft_id] = {
        "id": draft_id, "created_at": _now_iso(), "username": username,
        "capture": str(image_path), **parsed,
    }
    state["drafts"] = dict(list(state["drafts"].items())[-40:])
    save_state(base, state)
    return state["drafts"][draft_id]


def _node_executable() -> str:
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    if bundled.is_file():
        return str(bundled)
    found = shutil.which("node")
    if found:
        return found
    raise FileNotFoundError("No se encontró Node.js para actualizar la matriz.")


def _validate_entry(entry: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(entry)
    required = ("entry_date", "reference_loan", "refer_swift", "lender", "borrower", "currency", "value_date", "status")
    missing = [key for key in required if not str(cleaned.get(key) or "").strip()]
    if missing:
        raise ValueError("Complete los campos obligatorios: " + ", ".join(missing))
    if not re.fullmatch(r"(?:TF|GS)-01-\d{9,12}", str(cleaned["refer_swift"]).strip().upper()):
        raise ValueError("La referencia SWIFT debe tener el formato TF-01-… o GS-01-…")
    for key in ("entry_date", "value_date"):
        try:
            datetime.strptime(str(cleaned[key]), "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"{key} no contiene una fecha válida.") from exc
    currency = str(cleaned["currency"]).strip().upper()
    amount_key = "amount_usd" if currency == "USD" else "amount_other"
    try:
        amount = float(cleaned.get(amount_key))
    except (TypeError, ValueError) as exc:
        raise ValueError("Ingrese un monto numérico mayor a cero.") from exc
    if amount <= 0:
        raise ValueError("El monto debe ser mayor a cero.")
    cleaned[amount_key] = round(amount, 2)
    cleaned["amount_other" if amount_key == "amount_usd" else "amount_usd"] = None
    return cleaned


def append_to_workbook(base_dir: str | Path, draft_id: str, entry: dict[str, Any], username: str) -> dict[str, Any]:
    base = Path(base_dir)
    state = load_state(base)
    draft = state["drafts"].get(draft_id)
    if not draft:
        raise ValueError("La captura ya no está disponible. Vuelva a analizarla.")
    if draft.get("username") != username:
        raise PermissionError("La captura pertenece a otra sesión.")
    entry = _validate_entry(entry)
    entry["user"] = username.lower()
    entry.pop("office_reference", None)
    entry.pop("order_number", None)
    entry.pop("beneficiary_bank", None)
    entry.pop("correspondent_method", None)
    entry.pop("correspondent_confidence", None)
    entry.pop("correspondent_evidence", None)
    workbook = Path(load_config(base)["workbook"])
    if not workbook.is_file():
        raise FileNotFoundError(f"No se encuentra la matriz: {workbook}")
    writer = base / "matriz_prestamos_writer.mjs"
    backup_dir = base / "backups_matriz_prestamos"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = backup_dir / f"Prestamo Realizados_{stamp}.xlsx"
    temporary = workbook.with_name(f".{workbook.stem}.gidep-{uuid.uuid4().hex}.xlsx")
    inspect_sidecar = Path(str(temporary) + ".inspect.ndjson")
    receipt_file = Path(str(temporary) + ".receipt.json")
    entry_file = base / "capturas_matriz_prestamos" / f"entry-{uuid.uuid4().hex}.json"
    entry_file.parent.mkdir(parents=True, exist_ok=True)
    entry_file.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    with _write_lock:
        shutil.copy2(workbook, backup)
        try:
            completed = subprocess.run(
                [_node_executable(), str(writer), "append", str(workbook), str(entry_file), str(temporary)],
                cwd=str(base), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode:
                try:
                    detail = json.loads(completed.stderr or "{}")
                    message = detail.get("error") or completed.stderr or completed.stdout
                except ValueError:
                    message = completed.stderr or completed.stdout
                raise RuntimeError((message or f"No se pudo actualizar el Excel (código {completed.returncode}).").strip())
            if not temporary.is_file() or temporary.stat().st_size < 1_000:
                raise RuntimeError("El escritor terminó sin generar una matriz válida; se conservó el original.")
            result: dict[str, Any] = {}
            if receipt_file.is_file():
                try:
                    result = json.loads(receipt_file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    result = {}
            elif completed.stdout.strip():
                try:
                    result = json.loads(completed.stdout.strip().splitlines()[-1])
                except ValueError:
                    result = {}
            os.replace(temporary, workbook)
        finally:
            entry_file.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
            inspect_sidecar.unlink(missing_ok=True)
            receipt_file.unlink(missing_ok=True)
    history = {
        "saved_at": _now_iso(), "username": username, "reference_loan": entry["reference_loan"],
        "refer_swift": entry["refer_swift"], "value_date": entry["value_date"],
        "amount": entry["amount_usd"] if entry.get("amount_usd") is not None else entry.get("amount_other"),
        "currency": entry["currency"], "row": result.get("row"), "backup": str(backup),
        "capture": draft.get("capture"),
    }
    state = load_state(base)
    state["drafts"].pop(draft_id, None)
    state["history"] = ([history] + state.get("history", []))[:100]
    save_state(base, state)
    return {"ok": True, "saved": history, "workbook": str(workbook)}


def _status(base_dir: str | Path) -> dict[str, Any]:
    base = Path(base_dir)
    config = load_config(base)
    workbook = Path(config["workbook"])
    state = load_state(base)
    return {
        "ok": True, "config": config, "workbook_exists": workbook.is_file(),
        "workbook_modified": datetime.fromtimestamp(workbook.stat().st_mtime).astimezone().isoformat(timespec="seconds") if workbook.is_file() else None,
        "history": state.get("history", [])[:20],
    }


def register_matriz_prestamos_routes(app, base_dir: str) -> None:
    global _base_dir
    _base_dir = Path(base_dir)
    upload_dir = _base_dir / "capturas_matriz_prestamos"
    upload_dir.mkdir(parents=True, exist_ok=True)

    @app.get("/api/matriz_prestamos/status")
    def matriz_prestamos_status():
        return jsonify(_status(_base_dir))

    @app.post("/api/matriz_prestamos/config")
    def matriz_prestamos_config():
        payload = request.get_json(silent=True) or {}
        config = save_config(_base_dir, payload.get("workbook"))
        return jsonify({"ok": True, "config": config})

    @app.post("/api/matriz_prestamos/analyze")
    def matriz_prestamos_analyze():
        storage = request.files.get("capture")
        if not storage or not storage.filename:
            return jsonify({"ok": False, "error": "Seleccione una captura de pantalla."}), 400
        suffix = Path(storage.filename).suffix.casefold()
        if suffix not in ALLOWED_IMAGES:
            return jsonify({"ok": False, "error": "Use una imagen PNG, JPG, WEBP o BMP."}), 400
        filename = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}_{secure_filename(storage.filename)}"
        image_path = upload_dir / filename
        storage.save(image_path)
        draft = analyze_image(_base_dir, image_path, str(session.get("username") or ""))
        return jsonify({"ok": True, "draft": draft})

    @app.post("/api/matriz_prestamos/save")
    def matriz_prestamos_save():
        payload = request.get_json(silent=True) or {}
        entry = payload.get("entry") or {}
        g.audit_detail = f"Actualización de matriz: {entry.get('refer_swift') or 'sin referencia'}"
        try:
            result = append_to_workbook(_base_dir, str(payload.get("draft_id") or ""), entry, str(session.get("username") or ""))
        except PermissionError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 403
        except (ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify(result)

    @app.get("/api/matriz_prestamos/capture/<draft_id>")
    def matriz_prestamos_capture(draft_id: str):
        draft = load_state(_base_dir).get("drafts", {}).get(draft_id)
        if not draft or draft.get("username") != session.get("username"):
            return jsonify({"ok": False, "error": "Captura no disponible."}), 404
        path = Path(draft.get("capture") or "")
        if not path.is_file() or upload_dir not in path.parents:
            return jsonify({"ok": False, "error": "Captura no disponible."}), 404
        return send_file(path)
