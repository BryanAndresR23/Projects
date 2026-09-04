# -*- coding: utf-8 -*-
"""Firma EC: monitor y copia controlada de mensajes SWIFT.

Flujos independientes:
- Firma Deuda Externa: carpetas de pagos de Bryan.
- Firmas Automaticos: mensajes digitalizados por fecha.
- Firma Bancos: mensajes de bancos por fecha.

La lectura y las alertas son automaticas. La copia exige seleccion y
confirmacion, nunca sobrescribe archivos y registra cada operacion. Tras
verificar el contenido copiado, conserva en el origen tanto la version con
una firma como la version con dos firmas.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from flask import g, jsonify, request


ARCHIVE_ROOT = Path(r"Z:\GISI\SSFI\SWIFT\MENSAJES SWIFT FIRMADOS")
SOURCE_ROOT = Path(r"Z:\GISI\SSFI\SWIFT\Carpeta Ingresadores\BRYAN")
DIGITALIZED_ROOT = ARCHIVE_ROOT / "MENSAJES DIGITALIZADOS"
BANKS_ROOT = ARCHIVE_ROOT / "BANCOS"
FLOW_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "deuda_externa",
        "label": "Firma Deuda Externa",
        "source": SOURCE_ROOT,
        "strategy": "payment_folders",
        "description": "Mensajes de pagos de deuda externa ingresados por Bryan.",
    },
    {
        "id": "automaticos",
        "label": "Firmas Automáticos",
        "source": DIGITALIZED_ROOT,
        "strategy": "dated_folders",
        "description": "Mensajes automáticos digitalizados pendientes de segunda firma.",
    },
    {
        "id": "bancos",
        "label": "Firma Bancos",
        "source": BANKS_ROOT,
        "strategy": "dated_folders",
        "description": "Mensajes remitidos por bancos pendientes de segunda firma.",
    },
)
FLOW_MAP = {flow["id"]: flow for flow in FLOW_DEFINITIONS}
SPANISH_MONTHS = {
    1: "ENERO",
    2: "FEBRERO",
    3: "MARZO",
    4: "ABRIL",
    5: "MAYO",
    6: "JUNIO",
    7: "JULIO",
    8: "AGOSTO",
    9: "SEPTIEMBRE",
    10: "OCTUBRE",
    11: "NOVIEMBRE",
    12: "DICIEMBRE",
}
SOURCE_FOLDER_RE = re.compile(r"^20\d{2}-(?P<voucher>\d{3}-\d+)$", re.IGNORECASE)
DATED_FOLDER_RE = re.compile(r"^(?P<year>20\d{2})-(?P<month>\d{2})-(?P<day>\d{2})$")
MESSAGE_STATE_RE = re.compile(
    r"^(?P<prefix>.*?)(?P<kind>TF|GS)-01-(?P<operation>\d+)"
    r"(?P<signatures>(?:-signed){0,2})\.pdf$",
    re.IGNORECASE,
)
DAILY_SOURCE_LOOKBACK_DAYS = 7
ARCHIVE_LOOKBACK_DAYS = 21
ARCHIVE_CACHE_SECONDS = 300
# Consulta casi en tiempo real. Tres segundos evita cargar en exceso la unidad Z:
# y sigue detectando un archivo nuevo prácticamente al terminar de copiarse.
AUTOMATIC_REFRESH_SECONDS = 3
move_lock = threading.Lock()
archive_cache_lock = threading.Lock()
archive_cache: dict[str, tuple[float, set[str]]] = {}
_status_monitor: "SignatureStatusMonitor | None" = None


def local_today() -> date:
    return datetime.now().astimezone().date()


def daily_target_root(
    target_date: date | None = None, archive_root: Path | None = None
) -> Path:
    selected_date = target_date or local_today()
    root = archive_root or ARCHIVE_ROOT
    month = f"{selected_date.month:02d}. {SPANISH_MONTHS[selected_date.month]}"
    return root / str(selected_date.year) / month / f"{selected_date.day:02d}"


def ensure_target_root(target_root: Path | None = None) -> Path:
    resolved = Path(target_root) if target_root is not None else daily_target_root()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def target_date_label(target_date: date | None = None) -> str:
    selected_date = target_date or local_today()
    month = SPANISH_MONTHS[selected_date.month].lower()
    return f"{selected_date.day:02d} de {month} de {selected_date.year}"


def target_folder_label(target_root: Path) -> str:
    try:
        return str(target_root.relative_to(ARCHIVE_ROOT))
    except ValueError:
        return str(target_root)


def audit_log(base_dir: str | os.PathLike[str]) -> Path:
    return Path(base_dir) / "logs_mensajes_firmados" / "movimientos.log"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def message_id(path: Path, flow_id: str, source_root: Path) -> str:
    relative = str(path.relative_to(source_root)).replace("\\", "/").casefold()
    return hashlib.sha256(f"{flow_id}|{relative}".encode("utf-8")).hexdigest()[:24]


def signature_level(match: re.Match[str]) -> int:
    return match.group("signatures").lower().count("-signed")


def operation_reference(match: re.Match[str]) -> str:
    return f"{match.group('kind').upper()}-01-{match.group('operation')}"


def corresponding_single_signature(
    double_signed: Path, match: re.Match[str]
) -> Path | None:
    """Devuelve solo la pareja exacta -signed.pdf del mismo mensaje y carpeta."""
    if signature_level(match) != 2:
        return None
    single_name = re.sub(
        r"-signed-signed\.pdf$", "-signed.pdf", double_signed.name, flags=re.IGNORECASE
    )
    if single_name == double_signed.name:
        return None
    candidate = double_signed.with_name(single_name)
    candidate_match = MESSAGE_STATE_RE.fullmatch(candidate.name)
    if (
        candidate_match
        and signature_level(candidate_match) == 1
        and operation_reference(candidate_match) == operation_reference(match)
    ):
        return candidate
    return None


def folder_date(path: Path) -> date | None:
    match = DATED_FOLDER_RE.fullmatch(path.name)
    if not match:
        return None
    try:
        return date(
            int(match.group("year")), int(match.group("month")), int(match.group("day"))
        )
    except ValueError:
        return None


def source_containers(flow: dict[str, Any], today: date | None = None) -> list[Path]:
    source_root = Path(flow["source"])
    if not source_root.is_dir():
        raise FileNotFoundError(f"No existe la carpeta de origen: {source_root}")
    if flow["strategy"] == "payment_folders":
        return sorted(
            (
                path
                for path in source_root.iterdir()
                if path.is_dir() and SOURCE_FOLDER_RE.fullmatch(path.name)
            ),
            key=lambda path: path.name.casefold(),
        )
    selected_today = today or local_today()
    # Las fuentes diarias pueden acumular cientos de carpetas. Construir las
    # siete rutas esperadas evita enumerar toda la unidad de red en cada pulso.
    dated = [
        source_root / (selected_today - timedelta(days=offset)).isoformat()
        for offset in range(DAILY_SOURCE_LOOKBACK_DAYS + 1)
    ]
    return [path for path in dated if path.is_dir()]


def container_label(flow: dict[str, Any], container: Path) -> str:
    if flow["strategy"] == "payment_folders":
        match = SOURCE_FOLDER_RE.fullmatch(container.name)
        return match.group("voucher") if match else container.name
    return container.name


def iter_message_files(container: Path):
    for path in sorted(container.rglob("*.pdf"), key=lambda item: item.name.casefold()):
        if path.is_file():
            match = MESSAGE_STATE_RE.fullmatch(path.name)
            if match:
                yield path, match


def collect_message_groups(
    containers: list[Path],
) -> list[tuple[Path, dict[str, list[tuple[int, Path, re.Match[str]]]]]]:
    grouped: list[
        tuple[Path, dict[str, list[tuple[int, Path, re.Match[str]]]]]
    ] = []
    for container in containers:
        operations: dict[str, list[tuple[int, Path, re.Match[str]]]] = {}
        for path, match in iter_message_files(container):
            operation = operation_reference(match)
            operations.setdefault(operation, []).append(
                (signature_level(match), path, match)
            )
        grouped.append((container, operations))
    return grouped


def archived_operations(target_root: Path) -> set[str]:
    cache_key = str(target_root).casefold()
    with archive_cache_lock:
        cached = archive_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < ARCHIVE_CACHE_SECONDS:
            return set(cached[1])
    scan_folders = [target_root]
    try:
        target_root.relative_to(ARCHIVE_ROOT)
        today = local_today()
        cutoff = today - timedelta(days=ARCHIVE_LOOKBACK_DAYS)
        scan_folders = [
            daily_target_root(today - timedelta(days=offset), ARCHIVE_ROOT)
            for offset in range(ARCHIVE_LOOKBACK_DAYS + 1)
        ]
    except (ValueError, OSError):
        pass
    operations: set[str] = set()
    for folder in scan_folders:
        if not folder.is_dir():
            continue
        for path in folder.rglob("*.pdf"):
            if not path.is_file():
                continue
            match = MESSAGE_STATE_RE.fullmatch(path.name)
            if match and signature_level(match) == 2:
                operations.add(operation_reference(match))
    with archive_cache_lock:
        archive_cache[cache_key] = (time.monotonic(), set(operations))
    return operations


def invalidate_archive_cache() -> None:
    with archive_cache_lock:
        archive_cache.clear()


def discover_signature_alerts(
    flow: dict[str, Any], target_root: Path,
    grouped: list[
        tuple[Path, dict[str, list[tuple[int, Path, re.Match[str]]]]]
    ] | None = None,
    archived: set[str] | None = None,
) -> list[dict[str, Any]]:
    source_root = Path(flow["source"])
    archived = archived if archived is not None else archived_operations(target_root)
    alerts: list[dict[str, Any]] = []
    grouped = grouped if grouped is not None else collect_message_groups(source_containers(flow))
    for container, groups in grouped:
        for operation, versions in groups.items():
            if operation in archived:
                continue
            highest_level, highest_path, _ = max(
                versions, key=lambda item: (item[0], item[1].stat().st_mtime)
            )
            if highest_level >= 2:
                continue
            signature_status = "first_signature" if highest_level == 0 else "second_signature"
            signature_label = (
                "Pendiente de primera firma"
                if highest_level == 0
                else "Pendiente de segunda firma"
            )
            stat = highest_path.stat()
            relative = str(highest_path.relative_to(source_root)).replace("\\", "/")
            alerts.append(
                {
                    "alert_id": hashlib.sha256(
                        f"{flow['id']}|{relative}|{operation}|{highest_level}".encode("utf-8")
                    ).hexdigest()[:24],
                    "flow_id": flow["id"],
                    "flow_label": flow["label"],
                    "voucher_number": container_label(flow, container),
                    "operation_reference": operation,
                    "filename": highest_path.name,
                    "source_path": str(highest_path),
                    "signature_status": signature_status,
                    "signature_label": signature_label,
                    "signature_count": highest_level,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime)
                    .astimezone()
                    .isoformat(timespec="seconds"),
                }
            )
    alerts.sort(key=lambda item: (item["modified_at"], item["operation_reference"]), reverse=True)
    return alerts


def discover_messages(
    flow: dict[str, Any], target_root: Path,
    grouped: list[
        tuple[Path, dict[str, list[tuple[int, Path, re.Match[str]]]]]
    ] | None = None,
) -> list[dict[str, Any]]:
    source_root = Path(flow["source"])
    messages: list[dict[str, Any]] = []
    grouped = grouped if grouped is not None else collect_message_groups(source_containers(flow))
    for container, groups in grouped:
        for operation, versions in groups.items():
            for level, source, match in versions:
                if level != 2:
                    continue
                target = target_root / source.name
                item_status = "ready"
                note = "Listo para mover"
                if target.exists():
                    identical = False
                    if target.is_file() and source.stat().st_size == target.stat().st_size:
                        try:
                            identical = file_hash(source) == file_hash(target)
                        except OSError:
                            identical = False
                    if identical:
                        item_status = "already_exists"
                        note = "El mismo archivo ya existe en el destino"
                    else:
                        item_status = "conflict"
                        note = "Existe otro archivo con el mismo nombre"
                stat = source.stat()
                messages.append(
                    {
                        "message_id": message_id(source, flow["id"], source_root),
                        "flow_id": flow["id"],
                        "flow_label": flow["label"],
                        "voucher_number": container_label(flow, container),
                        "operation_reference": operation,
                        "filename": source.name,
                        "source_path": str(source),
                        "target_path": str(target),
                        "size": stat.st_size,
                        "modified_at": datetime.fromtimestamp(stat.st_mtime)
                        .astimezone()
                        .isoformat(timespec="seconds"),
                        "status": item_status,
                        "note": note,
                        "selectable": item_status == "ready",
                    }
                )
    messages.sort(key=lambda item: (item["modified_at"], item["operation_reference"]), reverse=True)
    return messages


def build_summary(
    messages: list[dict[str, Any]], alerts: list[dict[str, Any]]
) -> dict[str, int]:
    return {
        "detected": len(messages),
        "ready": sum(item["status"] == "ready" for item in messages),
        "already_exists": sum(item["status"] == "already_exists" for item in messages),
        "conflicts": sum(item["status"] == "conflict" for item in messages),
        "pending_signatures": len(alerts),
        "first_signature": sum(
            item["signature_status"] == "first_signature" for item in alerts
        ),
        "second_signature": sum(
            item["signature_status"] == "second_signature" for item in alerts
        ),
    }


def empty_summary() -> dict[str, int]:
    return build_summary([], [])


def flow_status(flow: dict[str, Any], target_root: Path) -> dict[str, Any]:
    source_root = Path(flow["source"])
    try:
        containers = source_containers(flow)
        grouped = collect_message_groups(containers)
        archived = archived_operations(target_root)
        messages = discover_messages(flow, target_root, grouped)
        alerts = discover_signature_alerts(flow, target_root, grouped, archived)
        return {
            "id": flow["id"],
            "label": flow["label"],
            "description": flow["description"],
            "ok": True,
            "source": str(source_root),
            "scanned_folders": [path.name for path in containers],
            "summary": build_summary(messages, alerts),
            "messages": messages,
            "signature_alerts": alerts,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "id": flow["id"],
            "label": flow["label"],
            "description": flow["description"],
            "ok": False,
            "source": str(source_root),
            "scanned_folders": [],
            "summary": empty_summary(),
            "messages": [],
            "signature_alerts": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def merge_summaries(flows: list[dict[str, Any]]) -> dict[str, int]:
    aggregate = empty_summary()
    for flow in flows:
        for key in aggregate:
            aggregate[key] += int(flow["summary"].get(key, 0))
    return aggregate


def status(
    flows: tuple[dict[str, Any], ...] | list[dict[str, Any]] = FLOW_DEFINITIONS,
    target_root: Path | None = None,
) -> dict[str, Any]:
    resolved_target = Path(target_root) if target_root is not None else daily_target_root()
    try:
        resolved_target = ensure_target_root(resolved_target)
        flow_payloads = [flow_status(flow, resolved_target) for flow in flows]
        return {
            "ok": True,
            "automatic_refresh": True,
            "automatic_refresh_seconds": AUTOMATIC_REFRESH_SECONDS,
            "target": str(resolved_target),
            "target_date_label": target_date_label(),
            "target_folder_label": target_folder_label(resolved_target),
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "summary": merge_summaries(flow_payloads),
            "flows": flow_payloads,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "automatic_refresh": True,
            "automatic_refresh_seconds": AUTOMATIC_REFRESH_SECONDS,
            "target": str(resolved_target),
            "target_date_label": target_date_label(),
            "target_folder_label": str(resolved_target),
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "summary": empty_summary(),
            "flows": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def warming_status() -> dict[str, Any]:
    flows = [
        {
            "id": flow["id"],
            "label": flow["label"],
            "description": flow["description"],
            "ok": True,
            "source": str(flow["source"]),
            "scanned_folders": [],
            "summary": empty_summary(),
            "messages": [],
            "signature_alerts": [],
            "error": None,
        }
        for flow in FLOW_DEFINITIONS
    ]
    target = daily_target_root()
    return {
        "ok": True,
        "automatic_refresh": True,
        "automatic_refresh_seconds": AUTOMATIC_REFRESH_SECONDS,
        "monitor_warming": True,
        "target": str(target),
        "target_date_label": target_date_label(),
        "target_folder_label": target_folder_label(target),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": empty_summary(),
        "flows": flows,
        "error": None,
    }


class SignatureStatusMonitor:
    """Mantiene un resultado listo para que la interfaz nunca espere a la unidad Z:."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot = warming_status()
        self._started = False

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(
            target=self._run,
            name="gidep-firma-ec-realtime",
            daemon=True,
        ).start()

    def _run(self) -> None:
        while True:
            try:
                payload = status()
                payload["monitor_warming"] = False
            except Exception as exc:  # noqa: BLE001
                payload = warming_status()
                payload["monitor_warming"] = False
                payload["ok"] = False
                payload["error"] = f"{type(exc).__name__}: {exc}"
            with self._lock:
                self._snapshot = payload
            time.sleep(AUTOMATIC_REFRESH_SECONDS)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot


def write_audit(record: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def move_messages(
    flow_id: str,
    selected_ids: list[str],
    audit_path: Path,
    target_root: Path | None = None,
    flow_map: dict[str, dict[str, Any]] = FLOW_MAP,
) -> list[dict[str, Any]]:
    if flow_id not in flow_map:
        raise ValueError("Flujo de firmas no autorizado.")
    if not selected_ids:
        raise ValueError("Seleccione al menos un mensaje.")
    flow = flow_map[flow_id]
    source_root = Path(flow["source"])
    resolved_target = ensure_target_root(target_root)
    requested = set(selected_ids)
    with move_lock:
        available = {
            item["message_id"]: item for item in discover_messages(flow, resolved_target)
        }
        unknown = sorted(requested - set(available))
        if unknown:
            raise ValueError("La seleccion cambio; actualice la vista e intente nuevamente.")
        results: list[dict[str, Any]] = []
        for selected_id in selected_ids:
            item = available[selected_id]
            if item["status"] != "ready":
                results.append({**item, "result": "skipped", "result_note": item["note"]})
                continue
            source = Path(item["source_path"])
            target = Path(item["target_path"])
            try:
                relative_source = source.relative_to(source_root)
            except ValueError as exc:
                raise ValueError("El archivo seleccionado esta fuera del origen autorizado.") from exc
            if len(relative_source.parts) < 2 or target.parent != resolved_target:
                raise ValueError("El archivo seleccionado esta fuera de las rutas autorizadas.")
            match = MESSAGE_STATE_RE.fullmatch(source.name)
            if not match or signature_level(match) != 2:
                raise ValueError(f"{source.name} ya no conserva el sufijo institucional de dos firmas.")
            if not source.is_file() or target.exists():
                raise ValueError(f"El estado de {item['filename']} cambio; actualice la vista.")
            single_signature = corresponding_single_signature(source, match)
            if single_signature is not None:
                try:
                    relative_single = single_signature.relative_to(source_root)
                except ValueError as exc:
                    raise ValueError(
                        "La version de una firma esta fuera del origen autorizado."
                    ) from exc
                if (
                    len(relative_single.parts) < 2
                    or single_signature.parent != source.parent
                ):
                    raise ValueError(
                        "La version de una firma no pertenece a la carpeta seleccionada."
                    )
            source_hash = file_hash(source)
            temporary = target.with_name(f".firma-{uuid.uuid4().hex}.tmp")
            try:
                shutil.copy2(source, temporary)
                if (
                    not temporary.is_file()
                    or temporary.stat().st_size != item["size"]
                    or file_hash(temporary) != source_hash
                ):
                    raise OSError(
                        f"Fallo la verificacion SHA-256 de la copia temporal de {source.name}."
                    )
                os.rename(temporary, target)
                transfer_verified = (
                    target.is_file()
                    and target.stat().st_size == item["size"]
                    and file_hash(target) == source_hash
                    and source.is_file()
                    and file_hash(source) == source_hash
                )
                if not transfer_verified:
                    raise OSError(
                        f"Fallo la verificacion final de contenido de {source.name}."
                    )
            finally:
                if temporary.exists():
                    temporary.unlink()
            single_signature_deleted = False
            cleanup_note = (
                "Se conservaron en origen las versiones de una y dos firmas."
                if single_signature is not None and single_signature.is_file()
                else "Se conservo en origen la version de dos firmas; no se encontro una version con una firma."
            )
            single_signature_path = (
                str(single_signature) if single_signature is not None else None
            )
            invalidate_archive_cache()
            record = {
                "copied_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "action": "copied",
                "flow_id": flow["id"],
                "flow_label": flow["label"],
                "voucher_number": item["voucher_number"],
                "operation_reference": item["operation_reference"],
                "filename": item["filename"],
                "source_path": item["source_path"],
                "target_path": item["target_path"],
                "size": item["size"],
                "transfer_verified": transfer_verified,
                "source_preserved": source.is_file(),
                "single_signature_path": single_signature_path,
                "single_signature_preserved": bool(
                    single_signature is not None and single_signature.is_file()
                ),
                "single_signature_deleted": single_signature_deleted,
                "cleanup_note": cleanup_note,
            }
            write_audit(record, audit_path)
            result_note = f"Mensaje copiado y verificado correctamente. {cleanup_note}"
            results.append(
                {
                    **item,
                    "result": "copied" if transfer_verified else "verification_error",
                    "result_note": result_note,
                    "transfer_verified": transfer_verified,
                    "source_preserved": source.is_file(),
                    "single_signature_path": single_signature_path,
                    "single_signature_preserved": bool(
                        single_signature is not None and single_signature.is_file()
                    ),
                    "single_signature_deleted": single_signature_deleted,
                    "cleanup_note": cleanup_note,
                }
            )
        return results


def open_authorized_folder(flow_id: str, folder_type: str) -> Path:
    if flow_id not in FLOW_MAP:
        raise ValueError("Flujo de firmas no autorizado.")
    if folder_type == "source":
        folder = Path(FLOW_MAP[flow_id]["source"])
    elif folder_type == "destination":
        folder = ensure_target_root()
    else:
        raise ValueError("Carpeta no autorizada.")
    if not folder.is_dir():
        raise FileNotFoundError(f"No existe la carpeta: {folder}")
    os.startfile(str(folder))  # type: ignore[attr-defined]
    return folder


def register_mensajes_firmados_routes(app, base_dir):
    global _status_monitor
    base_path = Path(base_dir)
    if os.environ.get("FIRM_DISABLE_AUTOSTART") != "1":
        if _status_monitor is None:
            _status_monitor = SignatureStatusMonitor()
        _status_monitor.start()

    @app.get("/api/mensajes-firmados/status")
    def mensajes_firmados_status():
        payload = _status_monitor.snapshot() if _status_monitor is not None else status()
        return jsonify(payload), (200 if payload.get("ok") else 500)

    @app.post("/api/mensajes-firmados/move")
    def mensajes_firmados_move():
        payload = request.get_json(silent=True) or {}
        if payload.get("confirmation") not in {"COPIAR", "MOVER"}:
            return jsonify({"ok": False, "error": "Confirmacion invalida."}), 400
        selected = payload.get("message_ids") or []
        if not isinstance(selected, list):
            return jsonify({"ok": False, "error": "Seleccion invalida."}), 400
        g.audit_detail = f"Copia de {len(selected)} mensaje(s) del flujo {payload.get('flow_id') or ''}"
        try:
            moved = move_messages(
                str(payload.get("flow_id") or ""),
                [str(item) for item in selected],
                audit_log(base_path),
            )
        except (OSError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        refreshed = status()
        refreshed["moved"] = moved
        return jsonify(refreshed)

    @app.post("/api/mensajes-firmados/open-folder")
    def mensajes_firmados_open_folder():
        payload = request.get_json(silent=True) or {}
        try:
            folder = open_authorized_folder(
                str(payload.get("flow_id") or ""), str(payload.get("folder") or "")
            )
            return jsonify({"ok": True, "path": str(folder)})
        except (OSError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
