from __future__ import annotations

"""Integración del gestor de Comprobantes y ACK en Conciliación.

El motor de clasificación vive en ``comprobantes_contables_engine.py``. Este
módulo agrega configuración, estado compartido, monitor automático y las rutas
HTTP que consume la pantalla integrada.
"""

import json
import logging
import os
import threading
import uuid
from collections import Counter
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from flask import g, jsonify, request

import comprobantes_contables_engine as engine


AUTO_INTERVAL_SECONDS = 5 * 60
AUTO_READY_STATUSES = {"LISTO", "LISTO PARCIAL", "LISTO COMPROBANTE"}
CONFIG_FILENAME = "comprobantes_contables_config.json"
ACK_QUEUE_FILENAME = "comprobantes_ack_pendientes.json"
ACK_COMPLETED_RETENTION_DAYS = 30

_state_lock = threading.Lock()
_analysis_lock = threading.Lock()
_archive_lock = threading.Lock()
_queue_lock = threading.Lock()
_worker_lock = threading.Lock()
_auto_wakeup = threading.Event()
_auto_thread: threading.Thread | None = None
_logger: logging.Logger | None = None
_base_dir: Path | None = None
_state: dict[str, Any] = {
    "analysis_id": None,
    "generated_at": None,
    "payments": {},
    "analysis_origin": None,
    "auto_status": "INICIANDO",
    "auto_error": None,
}


def default_config() -> dict[str, Any]:
    return {
        "bryan_source": str(engine.DEFAULT_SOURCE),
        "steven_source": str(engine.DEFAULT_STEVEN_SOURCE),
        "ack_base": str(engine.DEFAULT_ACK_BASE),
        "destination": str(engine.DEFAULT_DESTINATION),
        "recursive": False,
        "use_ocr": True,
        "automatic": True,
    }


def config_path(base_dir: str | Path) -> Path:
    return Path(base_dir) / CONFIG_FILENAME


def ack_queue_path(base_dir: str | Path) -> Path:
    return Path(base_dir) / ACK_QUEUE_FILENAME


def _read_ack_queue_unlocked(base_dir: str | Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(ack_queue_path(base_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    items = payload.get("items", {}) if isinstance(payload, dict) else {}
    return {
        str(key): dict(value)
        for key, value in items.items()
        if isinstance(value, dict)
    }


def load_ack_queue(base_dir: str | Path) -> dict[str, dict[str, Any]]:
    with _queue_lock:
        return _read_ack_queue_unlocked(base_dir)


def _write_ack_queue_unlocked(
    base_dir: str | Path, items: dict[str, dict[str, Any]]
) -> None:
    path = ack_queue_path(base_dir)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "items": items,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_config(base_dir: str | Path) -> dict[str, Any]:
    current = default_config()
    path = config_path(base_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raw = {}

    if isinstance(raw, dict):
        # Migración transparente desde el módulo anterior (solo BRYAN).
        if raw.get("source_dir") and not raw.get("bryan_source"):
            raw["bryan_source"] = raw["source_dir"]
        if raw.get("acks_root") and not raw.get("ack_base"):
            raw["ack_base"] = raw["acks_root"]
        if raw.get("dest_root") and not raw.get("destination"):
            debt_subpath = raw.get(
                "debt_subpath",
                r"DEUDA EXTERNA PÚBLICA\Acreedores Internacionales",
            )
            raw["destination"] = str(Path(raw["dest_root"]) / "2026" / debt_subpath)
        if "recursive_scan" in raw and "recursive" not in raw:
            raw["recursive"] = bool(raw["recursive_scan"])

        for key in current:
            if key in raw:
                current[key] = raw[key]

    for key in ("bryan_source", "steven_source", "ack_base", "destination"):
        current[key] = str(current.get(key) or default_config()[key]).strip()
    for key in ("recursive", "use_ocr", "automatic"):
        current[key] = bool(current.get(key, default_config()[key]))
    return current


def save_config(base_dir: str | Path, updates: dict[str, Any]) -> dict[str, Any]:
    current = load_config(base_dir)
    for key in ("bryan_source", "steven_source", "ack_base", "destination"):
        if key in updates:
            value = str(updates.get(key) or "").strip()
            if not value:
                raise ValueError(f"La ruta {key} no puede quedar vacía.")
            current[key] = value
    for key in ("recursive", "use_ocr", "automatic"):
        if key in updates:
            current[key] = bool(updates[key])

    path = config_path(base_dir)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(current, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    _auto_wakeup.set()
    return current


def configure_logging(base_dir: str | Path) -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    log_directory = Path(base_dir) / "logs_comprobantes_contables"
    log_directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("conciliacion.comprobantes_contables")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(
            log_directory / "actividad.log",
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


def payment_key(payment: engine.Payment) -> str:
    return (
        f"{payment.responsible.upper()}|{payment.voucher_number}|"
        f"{payment.operation_reference}"
    )


def path_string(path: Path | None) -> str:
    return str(path) if path else ""


def payment_to_dict(payment: engine.Payment) -> dict[str, Any]:
    ready = payment.status in {
        "LISTO",
        "LISTO PARCIAL",
        "LISTO COMPROBANTE",
        "COINCIDENCIA PROBABLE",
    }
    return {
        "payment_id": payment_key(payment),
        "responsible": payment.responsible,
        "operation_reference": payment.operation_reference,
        "voucher_number": payment.voucher_number,
        "document_type": payment.document_type,
        "requires_ack": payment.requires_ack,
        "status": payment.status,
        "ready": ready,
        "automatic": (
            payment.match_score >= 99
            and not payment.probable_match
            and payment.status in AUTO_READY_STATUSES
        ),
        "value_date": (
            payment.value_date.strftime("%d/%m/%Y")
            if payment.value_date
            else ""
        ),
        "loan_reference": payment.loan_reference,
        "office_reference": payment.office_reference,
        "concept": payment.concept,
        "beneficiary": payment.beneficiary,
        "order_number": payment.order_number,
        "currency": payment.currency,
        "amounts": list(payment.amounts),
        "source_folder": path_string(payment.source_folder),
        "marker": path_string(payment.marker),
        "accounting_source": path_string(payment.accounting_source),
        "ack_source": path_string(payment.ack_source),
        "destination": path_string(payment.destination),
        "destination_name": payment.destination.name if payment.destination else "",
        "accounting_target": path_string(payment.accounting_target),
        "ack_target": path_string(payment.ack_target),
        "match_method": payment.match_method,
        "match_score": payment.match_score,
        "match_evidence": payment.match_evidence,
        "probable_match": payment.probable_match,
        "ocr_used": payment.ocr_used,
        "notes": payment.notes,
    }


def payment_to_ack_record(payment: engine.Payment) -> dict[str, Any]:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "payment_id": payment_key(payment),
        "responsible": payment.responsible,
        "voucher_number": payment.voucher_number,
        "operation_reference": payment.operation_reference,
        "document_type": payment.document_type,
        "source_folder": path_string(payment.source_folder),
        "marker": path_string(payment.marker),
        "accounting_source": path_string(payment.accounting_source),
        "ack_source": path_string(payment.ack_source),
        "value_date": payment.value_date.isoformat() if payment.value_date else "",
        "loan_reference": payment.loan_reference,
        "office_reference": payment.office_reference,
        "concept": payment.concept,
        "beneficiary": payment.beneficiary,
        "order_number": payment.order_number,
        "currency": payment.currency,
        "amounts": list(payment.amounts),
        "destination": path_string(payment.destination),
        "accounting_target": path_string(payment.accounting_target),
        "ack_target": path_string(payment.ack_target),
        "match_method": payment.match_method,
        "match_score": payment.match_score,
        "match_evidence": list(payment.match_evidence),
        "ocr_used": payment.ocr_used,
        "status": "ACK PENDIENTE",
        "created_at": now,
        "updated_at": now,
        "completed_at": "",
        "last_error": "",
    }


def ack_record_to_payment(record: dict[str, Any]) -> engine.Payment:
    value_date = None
    try:
        if record.get("value_date"):
            value_date = datetime.fromisoformat(str(record["value_date"]))
    except ValueError:
        value_date = None
    payment = engine.Payment(
        marker=Path(str(record.get("marker") or record.get("source_folder") or ".")),
        source_folder=Path(str(record.get("source_folder") or ".")),
        operation_reference=str(record.get("operation_reference") or ""),
        voucher_number=str(record.get("voucher_number") or ""),
        responsible=str(record.get("responsible") or ""),
        document_type=str(record.get("document_type") or "PAGO EXTERIOR"),
        requires_ack=True,
        accounting_source=(
            Path(str(record["accounting_source"]))
            if record.get("accounting_source")
            else None
        ),
        ack_source=Path(str(record["ack_source"])) if record.get("ack_source") else None,
        value_date=value_date,
        loan_reference=str(record.get("loan_reference") or ""),
        office_reference=str(record.get("office_reference") or ""),
        concept=str(record.get("concept") or ""),
        beneficiary=str(record.get("beneficiary") or ""),
        order_number=str(record.get("order_number") or ""),
        currency=str(record.get("currency") or ""),
        amounts=tuple(str(value) for value in record.get("amounts", [])),
        destination=Path(str(record["destination"])) if record.get("destination") else None,
        accounting_target=(
            Path(str(record["accounting_target"]))
            if record.get("accounting_target")
            else None
        ),
        ack_target=Path(str(record["ack_target"])) if record.get("ack_target") else None,
        match_method=str(record.get("match_method") or "Seguimiento persistente de ACK"),
        match_score=int(record.get("match_score") or 100),
        match_evidence=[str(value) for value in record.get("match_evidence", [])],
        ocr_used=bool(record.get("ocr_used")),
        status=str(record.get("status") or "ACK PENDIENTE"),
    )
    payment.notes.append(
        "Seguimiento persistente: el pago permanece visible aunque Firma EC cambie los archivos del origen."
    )
    if record.get("last_error"):
        payment.notes.append(str(record["last_error"]))
    return payment


def register_pending_ack_payments(
    base_dir: str | Path, payments: list[engine.Payment]
) -> None:
    pending = [
        payment
        for payment in payments
        if payment.requires_ack
        and payment.status == "ACK PENDIENTE"
        and payment.ack_source
        and payment.ack_target
    ]
    if not pending:
        return
    with _queue_lock:
        items = _read_ack_queue_unlocked(base_dir)
        for payment in pending:
            key = payment_key(payment)
            record = payment_to_ack_record(payment)
            previous = items.get(key, {})
            record["created_at"] = previous.get("created_at") or record["created_at"]
            items[key] = record
        _write_ack_queue_unlocked(base_dir, items)


def process_pending_ack_queue(
    base_dir: str | Path, logger: logging.Logger
) -> list[engine.Payment]:
    now = datetime.now().astimezone()
    retained: dict[str, dict[str, Any]] = {}
    with _queue_lock:
        items = _read_ack_queue_unlocked(base_dir)
        for key, record in items.items():
            completed_at = None
            try:
                if record.get("completed_at"):
                    completed_at = datetime.fromisoformat(str(record["completed_at"]))
            except ValueError:
                completed_at = None
            if (
                completed_at
                and now - completed_at > timedelta(days=ACK_COMPLETED_RETENTION_DAYS)
            ):
                continue

            source = Path(str(record.get("ack_source") or ""))
            target = Path(str(record.get("ack_target") or ""))
            record["updated_at"] = now.isoformat(timespec="seconds")
            if record.get("status") != "ARCHIVADO":
                record["status"] = "ACK PENDIENTE"
                record["last_error"] = ""
                if source.is_file() and record.get("ack_target"):
                    try:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with _archive_lock:
                            result = engine.atomic_copy_without_overwrite(source, target)
                        if result == "conflict":
                            record["status"] = "CONFLICTO"
                            record["last_error"] = (
                                f"Existe un ACK diferente en destino: {target}"
                            )
                        else:
                            record["status"] = "ARCHIVADO"
                            record["completed_at"] = now.isoformat(timespec="seconds")
                            logger.info(
                                "ACK COMPLETADO | responsable=%s | comprobante=%s | "
                                "referencia=%s | resultado=%s | destino=%s",
                                record.get("responsible"),
                                record.get("voucher_number"),
                                record.get("operation_reference"),
                                result,
                                target,
                            )
                    except Exception as exc:  # noqa: BLE001
                        record["status"] = "ERROR"
                        record["last_error"] = (
                            f"Error al completar ACK: {type(exc).__name__}: {exc}"
                        )
                        logger.exception(
                            "Falló el seguimiento del ACK %s",
                            record.get("operation_reference"),
                        )
            retained[key] = record
        _write_ack_queue_unlocked(base_dir, retained)
    return [ack_record_to_payment(record) for record in retained.values()]


def merge_ack_queue_payments(
    current: list[engine.Payment], queued: list[engine.Payment]
) -> list[engine.Payment]:
    merged = {payment_key(payment): payment for payment in current}
    status_rank = {
        "ERROR": 1,
        "CONFLICTO": 2,
        "ACK PENDIENTE": 3,
        "ARCHIVADO": 4,
        "YA ARCHIVADO": 5,
    }
    for payment in queued:
        key = payment_key(payment)
        existing = merged.get(key)
        if existing is None or status_rank.get(payment.status, 0) > status_rank.get(
            existing.status, 0
        ):
            merged[key] = payment
    result = list(merged.values())
    result.sort(
        key=lambda payment: (
            payment.value_date or datetime.min,
            payment.responsible.casefold(),
            payment.voucher_number,
        ),
        reverse=True,
    )
    return result


def summarize(payments: list[engine.Payment]) -> dict[str, int]:
    counts = Counter(payment.status for payment in payments)
    return {
        "detected": len(payments),
        "ready": (
            counts["LISTO"]
            + counts["LISTO PARCIAL"]
            + counts["LISTO COMPROBANTE"]
            + counts["COINCIDENCIA PROBABLE"]
        ),
        "archived": counts["ARCHIVADO"] + counts["YA ARCHIVADO"],
        "ack_pending": counts["ACK PENDIENTE"],
        "review": counts["ERROR"] + counts["REVISAR"] + counts["CONFLICTO"],
    }


def flag_duplicate_payments(payments: list[engine.Payment]) -> None:
    by_operation: dict[str, list[engine.Payment]] = {}
    by_target: dict[str, list[engine.Payment]] = {}
    for payment in payments:
        by_operation.setdefault(payment.operation_reference, []).append(payment)
        for target in (payment.accounting_target, payment.ack_target):
            if target:
                by_target.setdefault(str(target).casefold(), []).append(payment)

    duplicate_groups = [group for group in by_operation.values() if len(group) > 1]
    duplicate_groups.extend(group for group in by_target.values() if len(group) > 1)
    handled: set[tuple[str, ...]] = set()
    for group in duplicate_groups:
        unique = {payment_key(payment): payment for payment in group}
        if len(unique) < 2:
            continue
        signature = tuple(sorted(unique))
        if signature in handled:
            continue
        handled.add(signature)
        owners = ", ".join(
            f"{payment.responsible}: {payment.voucher_number}"
            for payment in unique.values()
        )
        for payment in unique.values():
            payment.status = "CONFLICTO"
            payment.notes.append(
                f"Referencia o archivo destino duplicado entre ingresadores ({owners})."
            )


def analyze(
    sources: list[tuple[str, Path]],
    ack_base: Path,
    destination: Path,
    recursive: bool,
    use_ocr: bool,
) -> list[engine.Payment]:
    for label, path in [
        *((f"origen {name}", path) for name, path in sources),
        ("base de ACK", ack_base),
        ("destino", destination),
    ]:
        if not path.exists():
            raise FileNotFoundError(f"No existe la ruta de {label}: {path}")

    engine.indexed_month_directories.cache_clear()
    engine.cached_searchable_pdf_text.cache_clear()
    payments: list[engine.Payment] = []
    for responsible, source in sources:
        for marker in engine.discover_markers(source, recursive):
            try:
                payment = engine.make_payment(
                    marker,
                    ack_base,
                    destination,
                    use_ocr=use_ocr,
                    responsible=responsible,
                )
                engine.classify(payment)
            except Exception as exc:  # noqa: BLE001
                folder_match = engine.SOURCE_FOLDER_RE.fullmatch(marker.parent.name)
                marker_match = engine.DOUBLE_SIGNED_RE.fullmatch(marker.name)
                voucher = folder_match.group("voucher") if folder_match else "DESCONOCIDO"
                operation = (
                    f"{marker_match.group('kind').upper()}-01-{marker_match.group('operation')}"
                    if marker_match
                    else marker.stem
                )
                payment = engine.Payment(
                    marker,
                    marker.parent,
                    operation,
                    voucher,
                    responsible=responsible,
                )
                payment.status = "ERROR"
                payment.notes.append(f"{type(exc).__name__}: {exc}")
            payments.append(payment)

        for accounting_source in engine.discover_internal_accounting_documents(
            source, recursive, use_ocr=use_ocr
        ):
            try:
                payment = engine.make_internal_payment(
                    accounting_source,
                    destination,
                    use_ocr=use_ocr,
                    responsible=responsible,
                )
                engine.classify(payment)
            except Exception as exc:  # noqa: BLE001
                folder_match = engine.SOURCE_FOLDER_RE.fullmatch(
                    accounting_source.parent.name
                )
                voucher = (
                    folder_match.group("voucher") if folder_match else "DESCONOCIDO"
                )
                payment = engine.Payment(
                    accounting_source,
                    accounting_source.parent,
                    "TRANSFERENCIA INTERNA",
                    voucher,
                    responsible=responsible,
                    document_type="TRANSFERENCIA INTERNA",
                    requires_ack=False,
                )
                payment.status = "ERROR"
                payment.notes.append(f"{type(exc).__name__}: {exc}")
            payments.append(payment)

    flag_duplicate_payments(payments)
    payments.sort(
        key=lambda payment: (
            payment.responsible.casefold(),
            payment.value_date or datetime.min,
            payment.voucher_number,
        ),
        reverse=True,
    )
    return payments


def publish_analysis(
    payments: list[engine.Payment], analysis_id: str, generated_at: str, origin: str
) -> None:
    with _state_lock:
        _state["analysis_id"] = analysis_id
        _state["generated_at"] = generated_at
        _state["payments"] = {
            payment_key(payment): payment for payment in payments
        }
        _state["analysis_origin"] = origin
        _state["auto_error"] = None


def state_response(base_dir: str | Path) -> dict[str, Any]:
    with _state_lock:
        payment_map = _state.get("payments")
        payments = list(payment_map.values()) if isinstance(payment_map, dict) else []
        return {
            "ok": True,
            "analysis_id": _state.get("analysis_id"),
            "generated_at": _state.get("generated_at"),
            "analysis_origin": _state.get("analysis_origin"),
            "auto_status": _state.get("auto_status"),
            "auto_error": _state.get("auto_error"),
            "auto_interval_seconds": AUTO_INTERVAL_SECONDS,
            "config": load_config(base_dir),
            "summary": summarize(payments),
            "payments": [payment_to_dict(payment) for payment in payments],
        }


def is_safe_for_automatic_archive(payment: engine.Payment) -> bool:
    return (
        payment.status in AUTO_READY_STATUSES
        and payment.match_score >= 99
        and not payment.probable_match
    )


def archive_safe_matches(
    payments: list[engine.Payment], analysis_id: str, logger: logging.Logger
) -> list[engine.Payment]:
    processed: list[engine.Payment] = []
    with _archive_lock:
        for payment in payments:
            if not is_safe_for_automatic_archive(payment):
                continue
            try:
                engine.execute(payment)
            except Exception as exc:  # noqa: BLE001
                payment.status = "ERROR"
                payment.notes.append(
                    f"Error automático al copiar: {type(exc).__name__}: {exc}"
                )
                logger.exception(
                    "Falló el archivo automático de %s",
                    payment.operation_reference,
                )
            logger.info(
                "AUTOARCHIVO | id=%s | responsable=%s | comprobante=%s | "
                "referencia=%s | estado=%s | destino=%s",
                analysis_id,
                payment.responsible,
                payment.voucher_number,
                payment.operation_reference,
                payment.status,
                payment.destination,
            )
            processed.append(payment)
    return processed


def sources_from_config(config: dict[str, Any]) -> list[tuple[str, Path]]:
    return [
        ("BRYAN", Path(config["bryan_source"])),
        ("STEVEN", Path(config["steven_source"])),
    ]


def run_automatic_cycle(base_dir: str | Path) -> None:
    config = load_config(base_dir)
    if not config["automatic"]:
        with _state_lock:
            _state["auto_status"] = "PAUSADO"
            _state["auto_error"] = None
        return

    logger = configure_logging(base_dir)
    with _state_lock:
        _state["auto_status"] = "ANALIZANDO"
        _state["auto_error"] = None

    with _analysis_lock:
        payments = analyze(
            sources_from_config(config),
            Path(config["ack_base"]),
            Path(config["destination"]),
            bool(config["recursive"]),
            bool(config["use_ocr"]),
        )
        analysis_id = uuid.uuid4().hex
        generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        processed = archive_safe_matches(payments, analysis_id, logger)
        # Registra tanto los que se acaban de copiar como los que ya tenian el
        # comprobante en destino y siguen esperando su ACK.
        register_pending_ack_payments(base_dir, payments)
        queue_payments = process_pending_ack_queue(base_dir, logger)
        payments = merge_ack_queue_payments(payments, queue_payments)
        publish_analysis(payments, analysis_id, generated_at, "AUTOMÁTICO")

    with _state_lock:
        _state["auto_status"] = "ACTIVO"
    logger.info(
        "ANÁLISIS AUTOMÁTICO | id=%s | pagos=%s | acciones=%s | intervalo=%ss",
        analysis_id,
        len(payments),
        len(processed),
        AUTO_INTERVAL_SECONDS,
    )


def automatic_worker(base_dir: str | Path) -> None:
    logger = configure_logging(base_dir)
    while True:
        try:
            run_automatic_cycle(base_dir)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Falló el ciclo automático de comprobantes")
            with _state_lock:
                _state["auto_status"] = "REINTENTANDO"
                _state["auto_error"] = f"{type(exc).__name__}: {exc}"
        _auto_wakeup.wait(AUTO_INTERVAL_SECONDS)
        _auto_wakeup.clear()


def start_automatic_worker(base_dir: str | Path) -> None:
    global _auto_thread
    with _worker_lock:
        if _auto_thread and _auto_thread.is_alive():
            return
        _auto_thread = threading.Thread(
            target=automatic_worker,
            args=(base_dir,),
            name="conciliacion-comprobantes-automatico",
            daemon=True,
        )
        _auto_thread.start()


def register_comprobantes_contables_routes(app, base_dir):
    global _base_dir
    _base_dir = Path(base_dir)
    configure_logging(_base_dir)

    @app.route("/api/comprobantes_contables/config", methods=["GET", "POST"])
    def comprobantes_contables_config():
        try:
            if request.method == "POST":
                config = save_config(_base_dir, request.get_json(silent=True) or {})
            else:
                config = load_config(_base_dir)
            return jsonify({"ok": True, "config": config})
        except (OSError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.get("/api/comprobantes_contables/status")
    def comprobantes_contables_status():
        return jsonify(state_response(_base_dir))

    @app.get("/api/comprobantes_contables/summary")
    def comprobantes_contables_summary():
        # Alias de compatibilidad con la versión anterior del apartado.
        return jsonify(state_response(_base_dir))

    @app.post("/api/comprobantes_contables/analyze")
    def comprobantes_contables_analyze():
        payload = request.get_json(silent=True) or {}
        g.audit_detail = "Análisis manual de comprobantes y ACK"
        config = load_config(_base_dir)
        for key in (
            "bryan_source",
            "steven_source",
            "ack_base",
            "destination",
            "recursive",
            "use_ocr",
        ):
            if key in payload:
                config[key] = payload[key]

        sources = sources_from_config(config)
        try:
            with _analysis_lock:
                payments = analyze(
                    sources,
                    Path(config["ack_base"]),
                    Path(config["destination"]),
                    bool(config["recursive"]),
                    bool(config["use_ocr"]),
                )
        except Exception as exc:  # noqa: BLE001
            configure_logging(_base_dir).exception("Falló el análisis manual")
            return jsonify({"ok": False, "error": str(exc)}), 400

        analysis_id = uuid.uuid4().hex
        generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        logger = configure_logging(_base_dir)
        register_pending_ack_payments(_base_dir, payments)
        queue_payments = process_pending_ack_queue(_base_dir, logger)
        payments = merge_ack_queue_payments(payments, queue_payments)
        publish_analysis(payments, analysis_id, generated_at, "MANUAL")
        logger.info(
            "ANÁLISIS MANUAL | id=%s | pagos=%s | orígenes=%s",
            analysis_id,
            len(payments),
            ", ".join(f"{name}={path}" for name, path in sources),
        )
        return jsonify(
            {
                "ok": True,
                "analysis_id": analysis_id,
                "generated_at": generated_at,
                "analysis_origin": "MANUAL",
                "auto_status": _state.get("auto_status"),
                "config": config,
                "summary": summarize(payments),
                "payments": [payment_to_dict(payment) for payment in payments],
            }
        )

    @app.post("/api/comprobantes_contables/archive")
    def comprobantes_contables_archive():
        payload = request.get_json(silent=True) or {}
        if payload.get("confirmation") != "ARCHIVAR":
            return jsonify({"ok": False, "error": "Confirmación inválida."}), 400

        analysis_id = payload.get("analysis_id")
        references = payload.get("references") or []
        if not isinstance(references, list) or not references:
            return jsonify(
                {"ok": False, "error": "Seleccione al menos un pago."}
            ), 400
        g.audit_detail = f"Archivo manual solicitado para {len(references)} pago(s)"

        with _state_lock:
            if analysis_id != _state.get("analysis_id"):
                return jsonify(
                    {
                        "ok": False,
                        "error": "La vista previa expiró. Analice nuevamente.",
                    }
                ), 409
            payment_map = _state.get("payments")
            assert isinstance(payment_map, dict)
            selected = [payment_map.get(reference) for reference in references]

        if any(payment is None for payment in selected):
            return jsonify(
                {
                    "ok": False,
                    "error": "La selección no coincide con la vista previa.",
                }
            ), 400

        logger = configure_logging(_base_dir)
        results: list[engine.Payment] = []
        for payment in selected:
            assert isinstance(payment, engine.Payment)
            engine.classify(payment)
            if payment.status in {
                "LISTO",
                "LISTO PARCIAL",
                "LISTO COMPROBANTE",
                "COINCIDENCIA PROBABLE",
            }:
                try:
                    with _archive_lock:
                        engine.execute(payment, allow_probable=True)
                except Exception as exc:  # noqa: BLE001
                    payment.status = "ERROR"
                    payment.notes.append(
                        f"Error al copiar: {type(exc).__name__}: {exc}"
                    )
                    logger.exception(
                        "Falló el archivo manual de %s",
                        payment.operation_reference,
                    )
            logger.info(
                "ARCHIVO MANUAL | id=%s | responsable=%s | comprobante=%s | "
                "referencia=%s | estado=%s | destino=%s",
                analysis_id,
                payment.responsible,
                payment.voucher_number,
                payment.operation_reference,
                payment.status,
                payment.destination,
            )
            results.append(payment)

        register_pending_ack_payments(_base_dir, results)
        queue_payments = process_pending_ack_queue(_base_dir, logger)
        with _state_lock:
            all_payments = list(payment_map.values())
            all_payments = merge_ack_queue_payments(all_payments, queue_payments)
            _state["payments"] = {
                payment_key(payment): payment for payment in all_payments
            }
        return jsonify(
            {
                "ok": True,
                "summary": summarize(all_payments),
                "payments": [payment_to_dict(payment) for payment in all_payments],
                "processed": [payment_to_dict(payment) for payment in results],
            }
        )

    @app.get("/api/comprobantes_contables/health")
    def comprobantes_contables_health():
        return jsonify(
            {
                "ok": True,
                "automatic": True,
                "interval_seconds": AUTO_INTERVAL_SECONDS,
                "match_engine_version": engine.MATCH_ENGINE_VERSION,
                "match_engine_file": str(Path(engine.__file__).resolve()),
            }
        )

    if os.environ.get("COMPROBANTES_DISABLE_AUTOSTART") != "1":
        start_automatic_worker(_base_dir)
