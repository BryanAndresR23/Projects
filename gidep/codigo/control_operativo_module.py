"""Resumen transversal y no destructivo del estado operativo de GIDEP."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from flask import jsonify

import agenda_pagos_module as agenda
import archivo_quipux_module as quipux
import comprobantes_contables_module as vouchers
import contratos_agencia_fiscal_module as contracts
import mensajes_firmados_module as signatures


def _number(mapping: dict[str, Any] | None, *keys: str) -> int:
    value: Any = mapping or {}
    for key in keys:
        if not isinstance(value, dict):
            return 0
        value = value.get(key)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_status(label: str, function) -> tuple[dict[str, Any], str | None]:
    try:
        payload = function() or {}
        return payload, None
    except Exception as exc:  # el tablero nunca debe detener los motores
        return {}, f"{label}: {type(exc).__name__}: {exc}"


def build_control_status(base_dir: str | Path) -> dict[str, Any]:
    base_dir = Path(base_dir)
    agenda_status, agenda_error = _safe_status("Agenda", agenda.AGENDA.status)
    quipux_status, quipux_error = _safe_status(
        "Archivo Quipux", lambda: quipux.status_payload(base_dir)
    )
    contract_status, contract_error = _safe_status(
        "Contratos", lambda: contracts.status_payload(base_dir)
    )
    voucher_status, voucher_error = _safe_status(
        "Comprobantes", lambda: vouchers.state_response(base_dir)
    )

    def signature_snapshot():
        monitor = getattr(signatures, "_status_monitor", None)
        return monitor.snapshot() if monitor is not None else signatures.warming_status()

    signature_status, signature_error = _safe_status("Firma EC", signature_snapshot)

    agenda_summary = (agenda_status.get("scan") or {}).get("summary") or {}
    quipux_summary = quipux_status.get("summary") or {}
    contract_summary = contract_status.get("summary") or {}
    voucher_summary = voucher_status.get("summary") or {}
    signature_summary = signature_status.get("summary") or {}

    cards = [
        {
            "id": "today",
            "label": "Pagos pendientes hoy",
            "value": _number(agenda_summary, "today_pending"),
            "view": "agenda",
            "severity": "urgent" if _number(agenda_summary, "today_pending") else "ok",
        },
        {
            "id": "overdue",
            "label": "Pagos vencidos",
            "value": _number(agenda_summary, "overdue"),
            "view": "agenda",
            "severity": "danger" if _number(agenda_summary, "overdue") else "ok",
        },
        {
            "id": "documents",
            "label": "Documentos por revisar",
            "value": _number(quipux_summary, "review"),
            "view": "archivoquipux",
            "severity": "warning" if _number(quipux_summary, "review") else "ok",
        },
        {
            "id": "incomplete",
            "label": "Expedientes incompletos",
            "value": _number(quipux_summary, "incomplete"),
            "view": "archivoquipux",
            "severity": "warning" if _number(quipux_summary, "incomplete") else "ok",
        },
        {
            "id": "signatures",
            "label": "Firmas pendientes",
            "value": _number(signature_summary, "pending_signatures"),
            "view": "firmados",
            "severity": "urgent" if _number(signature_summary, "pending_signatures") else "ok",
        },
        {
            "id": "vouchers",
            "label": "Comprobantes por revisar",
            "value": _number(voucher_summary, "review"),
            "view": "comprobantes",
            "severity": "warning" if _number(voucher_summary, "review") else "ok",
        },
        {
            "id": "acks",
            "label": "ACK pendientes",
            "value": _number(voucher_summary, "ack_pending"),
            "view": "comprobantes",
            "severity": "urgent" if _number(voucher_summary, "ack_pending") else "ok",
        },
        {
            "id": "contracts",
            "label": "Contratos por revisar",
            "value": _number(contract_summary, "review"),
            "view": "contratosagencia",
            "severity": "warning" if _number(contract_summary, "review") else "ok",
        },
    ]

    issues: list[dict[str, Any]] = []
    for card in cards:
        if card["value"]:
            issues.append(
                {
                    "module": card["label"],
                    "message": f"{card['value']} elemento(s) requieren atención.",
                    "view": card["view"],
                    "severity": card["severity"],
                }
            )
    module_errors = {
        "Agenda": agenda_error or (agenda_status.get("monitor") or {}).get("last_error"),
        "Archivo Quipux": quipux_error or quipux_status.get("last_error"),
        "Contratos": contract_error or contract_status.get("last_error"),
        "Comprobantes": voucher_error or voucher_status.get("auto_error"),
        "Firma EC": signature_error or signature_status.get("error"),
    }
    errors = [f"{module}: {error}" for module, error in module_errors.items() if error]
    for error in errors:
        issues.insert(0, {
            "module": "Monitor",
            "message": str(error),
            "view": "control",
            "severity": "danger",
        })

    sources = {
        "agenda": (agenda_status.get("scan") or {}).get("scanned_at"),
        "quipux": quipux_status.get("last_scan"),
        "contratos": contract_status.get("last_scan"),
        "firmas": signature_status.get("generated_at"),
        "comprobantes": voucher_status.get("generated_at"),
    }
    return {
        "ok": not errors,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "cards": cards,
        "issues": issues,
        "errors": errors,
        "last_updates": sources,
        "healthy_modules": 5 - sum(bool(value) for value in module_errors.values()),
        "total_modules": 5,
    }


def register_control_operativo_routes(app, base_dir: str | Path) -> None:
    @app.get("/api/control-operativo/status")
    def control_operativo_status():
        payload = build_control_status(base_dir)
        # El tablero puede mostrar estados parciales; un monitor caído no oculta los demás.
        return jsonify(payload)
