from __future__ import annotations

"""Archivo automático de oficios de Contratos de Agencia Fiscal.

El monitor lee únicamente PDF de la bandeja local de Quipux. Solo copia un
documento cuando su encabezado propio es un oficio, el asunto corresponde al
flujo de Contrato de Agencia Fiscal, el PDF evidencia firma electrónica y la
carpeta del contrato se identifica de forma única. Los originales se conservan
y ningún archivo existente se sobrescribe.
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
from typing import Any, Iterable

from flask import jsonify, request

import comprobantes_contables_engine as engine


DEFAULT_SOURCE = Path(r"C:\Users\bromo\Downloads\Deuda Quipux")
DEFAULT_DESTINATION = Path(
    r"Z:\GISI\SSFI\GESTIÓN PAGOS INTERNACIONALES\2026\DEUDA EXTERNA PÚBLICA"
    r"\Acreedores Internacionales"
)
CONFIG_FILENAME = "contratos_agencia_fiscal_config.json"
STATE_FILENAME = "contratos_agencia_fiscal_state.json"
MONITOR_INTERVAL_SECONDS = 60
STABLE_AGE_SECONDS = 8
AUTO_MATCH_SCORE = 99
ANALYSIS_VERSION = "2026-08-28-contratos-agencia-v2"

REPOSITORIES = (
    ("BANCOS", Path("BANCOS") / "Contratos Agencia Fiscal" / "Contratos Agencia Fiscal"),
    ("BID", Path("BID") / "Contratos Agencia Fiscal" / "Contratos Agencia Fiscal"),
    ("BIRF", Path("BIRF") / "Contratos Agencia Fiscal" / "Contratos Agencia Fiscal"),
    ("CAF", Path("CAF") / "Contratos Agencia Fiscal" / "Contratos Agencia Fiscal"),
    ("GOBIERNOS", Path("GOBIERNOS") / "Contratos" / "Contratos Agencia Fiscal"),
)

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


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value or "").strip(" .,:;()[]").split())
        key = _normalized(cleaned)
        if cleaned and key and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


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


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


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
    state.setdefault("records", {})
    return state


def save_state(base_dir: str | Path, state: dict[str, Any]) -> None:
    _atomic_json_write(_state_path(base_dir), state)


def configure_logging(base_dir: str | Path) -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    directory = Path(base_dir) / "logs_contratos_agencia_fiscal"
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("gidep.contratos_agencia_fiscal")
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


def _download_datetime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_ctime).astimezone()


def extract_subject(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    captured: list[str] = []
    collecting = False
    stop = re.compile(
        r"^(?:SEÑOR(?:A)?|SEÑORES|DE MI CONSIDERACI[ÓO]N|ESTIMAD[OA]S?|REFERENCIAS?)\b",
        re.IGNORECASE,
    )
    for line in lines[:90]:
        if not line:
            if collecting and captured:
                break
            continue
        if not collecting:
            match = re.search(r"\bASUNTO\s*:\s*(.*)$", line, flags=re.IGNORECASE)
            if not match:
                continue
            collecting = True
            if match.group(1).strip():
                captured.append(match.group(1).strip())
            continue
        if stop.search(line):
            break
        captured.append(line)
        if len(" ".join(captured)) > 700:
            break
    return " ".join(captured).strip(" .")


def extract_own_office_reference(text: str) -> str:
    # Solo se toma el primer encabezado documental. Un memorando puede citar o
    # anexar un oficio más adelante y no debe convertirse por ello en candidato.
    head = " ".join(text[:5000].split())
    match = re.search(
        r"\b(OFICIO|MEMORANDO)\s+(?:NRO\.?|NO\.?)\s*[:#]?\s*"
        r"([A-Z0-9][A-Z0-9.\-/]{7,80})",
        head,
        flags=re.IGNORECASE,
    )
    if not match or match.group(1).upper() != "OFICIO":
        return ""
    return match.group(2).strip(" .,:;)").upper()


def extract_referenced_offices(text: str, own_reference: str) -> list[str]:
    flat = " ".join(text.split())
    matches = re.findall(
        r"\b((?:BCE|MEF|MDEP|CFN|CONAFIPS|EMAPAG|GAD[A-Z0-9]*|PCG)"
        r"[A-Z0-9.\-/]*-20\d{2}-[A-Z0-9.\-/]+-[A-Z]{1,6})\b",
        flat,
        flags=re.IGNORECASE,
    )
    return [
        value.upper()
        for value in _unique(matches)
        if _normalized(value) != _normalized(own_reference)
    ]


def extract_contract_references(text: str) -> list[str]:
    flat = " ".join(text.split())
    values: list[str] = []
    patterns = (
        r"\b((?:BMZ\s*[- ]?NO\.?|KFW\s*[- ]?(?:BMZ\s*)?(?:NO\.?)?)\s*[:#-]?\s*\d[\d.\-/]{4,})\b",
        r"\b((?:BID|BIRF|IBRD|CFA|CAF|BEI|AFD|JICA|FONPLATA|AIIB)\s*[- ]+[A-Z0-9][A-Z0-9.\-/ ]{2,35})\b",
        r"\b([A-Z]{1,4}\s*[- ]?P\d{1,3})\b",
    )
    for pattern in patterns:
        for value in re.findall(pattern, flat, flags=re.IGNORECASE):
            cleaned = re.sub(
                r"\s+(?:DE|DEL|PARA|CON|POR|Y|A\s+TRAV[EÉ]S)\b.*$",
                "",
                value,
                flags=re.IGNORECASE,
            ).strip(" .,:;")
            if re.search(r"\d{3,}", cleaned):
                values.append(cleaned.upper())
    return _unique(values)


def extract_sigade_numbers(text: str) -> list[str]:
    flat = " ".join(text.split())
    explicit = re.findall(
        r"(?:SIGADE|PR[ÉE]STAMO)\s*(?:NRO\.?|NO\.?)?\s*[:#-]?\s*(\d{7,9})",
        flat,
        flags=re.IGNORECASE,
    )
    generic = re.findall(r"(?<!\d)((?:20|23|28)\d{6})(?!\d)", flat)
    return _unique([*explicit, *generic])


def extract_entities(text: str) -> list[str]:
    normalized = _normalized(text)
    known = (
        "CFN",
        "CONAFIPS",
        "BDE",
        "EMAPAG EP",
        "CELEC",
        "GAD MANTA",
        "GAD MONTECRISTI",
        "GAD PICHINCHA",
        "GAD MANABI",
        "REPUBLICA ECUADOR",
    )
    return [entity for entity in known if _normalized(entity) in normalized]


def extract_creditors(text: str) -> list[str]:
    normalized = _normalized(text)
    aliases = {
        "KFW": ("KFW", "KREDITANSTALT FUR WIEDERAUFBAU"),
        "AFD": ("AFD", "AGENCE FRANCAISE DE DEVELOPPEMENT"),
        "JICA": ("JICA", "JAPAN INTERNATIONAL COOPERATION AGENCY"),
        "BID": ("BID", "BANCO INTERAMERICANO DE DESARROLLO"),
        "BIRF": ("BIRF", "IBRD", "BANCO INTERNACIONAL DE RECONSTRUCCION"),
        "CAF": ("CAF", "CFA", "BANCO DE DESARROLLO DE AMERICA LATINA"),
        "BEI": ("BEI", "BANCO EUROPEO DE INVERSIONES"),
    }
    return [
        creditor
        for creditor, variants in aliases.items()
        if any(re.search(rf"\b{re.escape(_normalized(alias))}\b", normalized) for alias in variants)
    ]


def _is_signed(text: str) -> bool:
    normalized = _normalized(text)
    return bool(
        "DOCUMENTO FIRMADO ELECTRONICAMENTE" in normalized
        or "FIRMADO ELECTRONICAMENTE POR" in normalized
        or "FIRMAS ELECTRONICAS" in normalized
    )


def classify_contract_office(path: Path, text: str) -> dict[str, Any]:
    own_reference = extract_own_office_reference(text)
    subject = extract_subject(text)
    normalized_subject = _normalized(subject)
    normalized_head = _normalized(text[:12000])
    is_office = bool(own_reference)
    contract_in_subject = bool(
        re.search(r"\bCONTRATO(?:\s+DE)?\s+AGENCIA\s+FISCAL\b", normalized_subject)
    )
    workflow_in_subject = "CONTRATO" in normalized_subject and any(
        term in normalized_subject
        for term in (
            "AGENCIA FISCAL",
            "ELABORACION",
            "BORRADOR",
            "SUSCRIPCION",
            "CORRECCION",
            "OBSERVACION",
            "REVISION",
            "REMISION",
            "ENVIO",
        )
    )
    contract_in_head = bool(
        re.search(r"\bCONTRATO(?:\s+DE)?\s+AGENCIA\s+FISCAL\b", normalized_head)
    )
    contract_relevant = contract_in_subject or (
        workflow_in_subject and contract_in_head
    )
    is_temp = "TEMP" in _normalized(path.stem) or "TEMP" in _normalized(own_reference)
    signed = _is_signed(text)
    candidate = is_office and contract_relevant and signed and not is_temp
    potential = contract_relevant or (
        contract_in_head
        and bool(re.search(r"\b(?:OFICIO|MEMORANDO)\b", normalized_head[:1000]))
    )
    reasons: list[str] = []
    if not is_office:
        reasons.append("El encabezado propio no es un oficio")
    if not contract_relevant:
        reasons.append("El asunto no corresponde al flujo de Contrato de Agencia Fiscal")
    if is_temp:
        reasons.append("Es un borrador TEMP de Quipux")
    if not signed:
        reasons.append("No se comprobó la firma electrónica del documento final")
    return {
        "candidate": candidate,
        "potential": potential,
        "is_office": is_office,
        "contract_relevant": contract_relevant,
        "signature_verified": signed,
        "is_temp": is_temp,
        "office_reference": own_reference,
        "subject": subject,
        "reasons": reasons,
    }


def _immediate_file_names(folder: Path) -> list[str]:
    try:
        return [path.name for path in folder.iterdir() if path.is_file()]
    except OSError:
        return []


def discover_contract_folders(destination_root: Path) -> list[dict[str, Any]]:
    folders: list[dict[str, Any]] = []
    for group, relative in REPOSITORIES:
        repository = destination_root / relative
        if not repository.is_dir():
            continue
        try:
            first_level = [path for path in repository.iterdir() if path.is_dir()]
        except OSError:
            continue
        if group == "GOBIERNOS":
            candidates: list[tuple[str, Path]] = []
            for creditor_folder in first_level:
                try:
                    candidates.extend(
                        (creditor_folder.name.upper(), path)
                        for path in creditor_folder.iterdir()
                        if path.is_dir()
                    )
                except OSError:
                    continue
        else:
            candidates = [(group, path) for path in first_level]
        for creditor, folder in candidates:
            file_names = _immediate_file_names(folder)
            corpus = "\n".join([folder.name, *file_names])
            folders.append(
                {
                    "path": folder,
                    "name": folder.name,
                    "group": group,
                    "creditor": creditor,
                    "file_names": file_names,
                    "corpus": corpus,
                    "normalized_corpus": _normalized(corpus),
                }
            )
    return folders


def learned_descriptor_patterns(folders: list[dict[str, Any]]) -> Counter[str]:
    patterns: Counter[str] = Counter()
    for folder in folders:
        for name in folder["file_names"]:
            match = re.search(r"\(([^()]{3,100})\)\s*\.pdf$", name, flags=re.IGNORECASE)
            if match:
                patterns[match.group(1).strip()] += 1
    return patterns


def _semantic_descriptor(subject: str, text: str) -> tuple[str, list[str]]:
    subject_norm = _normalized(subject)
    corpus = _normalized(f"{subject}\n{text[:14000]}")
    evidence: list[str] = []
    if "BORRADOR" in corpus and any(
        term in corpus for term in ("REVISION", "REVISAR", "ADJUNTO", "REMITE", "REMITO")
    ):
        evidence.append("el oficio remite un borrador para revisión")
        return "draft", evidence
    if any(term in subject_norm for term in ("CORRECCION", "SUBSANACION")):
        evidence.append("el asunto trata correcciones")
        return "corrections", evidence
    if "OBSERVACION" in subject_norm:
        evidence.append("el asunto trata observaciones")
        return "observations", evidence
    if any(
        term in corpus
        for term in (
            "CONTRATO DEBIDAMENTE SUSCRITO",
            "CONTRATO FIRMADO",
            "EJEMPLARES SUSCRITOS",
            "EJEMPLAR SUSCRITO",
        )
    ):
        evidence.append("se remite o recibe el contrato suscrito")
        return "signed", evidence
    if "SUSCRIPCION" in subject_norm or "SUSCRIBIR" in subject_norm:
        evidence.append("el asunto solicita o comunica la suscripción")
        return "subscription", evidence
    if "BORRADOR" in subject_norm:
        evidence.append("el asunto identifica un borrador")
        return "draft", evidence
    if "REVISION" in subject_norm:
        evidence.append("el asunto solicita revisión")
        return "review", evidence
    if "ELABORACION" in subject_norm:
        # En la práctica institucional, un oficio de elaboración que adjunta el
        # proyecto para revisión se archiva como Envío Borrador Contrato.
        if "BORRADOR" in corpus or "PROYECTO DE CONTRATO" in corpus:
            evidence.append("el oficio de elaboración adjunta el proyecto o borrador")
            return "draft", evidence
        evidence.append("el asunto solicita elaboración")
        return "elaboration", evidence
    if any(term in subject_norm for term in ("ENVIO", "REMISION", "REMITE")):
        evidence.append("el asunto remite el contrato")
        return "send", evidence
    return "", evidence


DEFAULT_DESCRIPTORS = {
    "draft": "Envío Borrador Contrato",
    "corrections": "Correcciones del Contrato de Agencia Fiscal",
    "observations": "Observaciones Contrato Agencia Fiscal",
    "signed": "Envío Contrato Firmado",
    "subscription": "Suscripción Contrato",
    "review": "Revisión Contrato Agencia Fiscal",
    "elaboration": "Elaboración Contrato Agencia Fiscal",
    "send": "Envío Contrato Agencia Fiscal",
}


def choose_descriptor(
    subject: str, text: str, learned: Counter[str]
) -> tuple[str, str, list[str]]:
    semantic, evidence = _semantic_descriptor(subject, text)
    if not semantic:
        return "", "Sin patrón semántico seguro", evidence
    predicates = {
        "draft": lambda n: "BORRADOR" in n and "CONTRATO" in n,
        "corrections": lambda n: "CORRECCION" in n and "CONTRATO" in n,
        "observations": lambda n: "OBSERVACION" in n and "CONTRATO" in n,
        "signed": lambda n: "CONTRATO" in n and ("FIRMADO" in n or "SUSCRITO" in n),
        "subscription": lambda n: "SUSCRIPCION" in n and "CONTRATO" in n,
        "review": lambda n: "REVISION" in n and "CONTRATO" in n,
        "elaboration": lambda n: "ELABORACION" in n and "CONTRATO" in n,
        "send": lambda n: "CONTRATO" in n and ("ENVIO" in n or "REMISION" in n),
    }
    options = [
        (count, descriptor)
        for descriptor, count in learned.items()
        if predicates[semantic](_normalized(descriptor))
    ]
    if options:
        count, descriptor = max(
            options,
            key=lambda item: (
                item[0],
                _normalized(item[1]) == _normalized(DEFAULT_DESCRIPTORS[semantic]),
                -len(item[1]),
            ),
        )
        evidence.append(f"patrón aprendido de {count} archivo(s) existente(s)")
        return descriptor, f"Patrón aprendido: {semantic}", evidence
    evidence.append("se aplicó el patrón institucional base")
    return DEFAULT_DESCRIPTORS[semantic], f"Patrón institucional: {semantic}", evidence


def extract_metadata(path: Path, text: str, ocr_used: bool) -> dict[str, Any]:
    classification = classify_contract_office(path, text)
    own_reference = classification["office_reference"]
    return {
        **classification,
        "referenced_offices": extract_referenced_offices(text, own_reference),
        "contract_references": extract_contract_references(text),
        "sigade_numbers": extract_sigade_numbers(text),
        "entities": extract_entities(text),
        "creditors": extract_creditors(text),
        "ocr_used": ocr_used,
        "text_length": len(text.strip()),
    }


def _contains_reference(corpus: str, reference: str) -> bool:
    reference_norm = _normalized(reference)
    corpus_norm = _normalized(corpus)
    if not reference_norm:
        return False
    if reference_norm in corpus_norm:
        return True
    reference_digits = "".join(re.findall(r"\d", reference))
    if len(reference_digits) >= 7:
        # Separadores y prefijos varían, pero la secuencia contractual completa
        # debe estar en nombres ya archivados; no se acepta una coincidencia corta.
        corpus_compact = re.sub(r"[^A-Z0-9]", "", corpus.upper())
        return reference_digits in corpus_compact
    return False


def find_destination(
    metadata: dict[str, Any], folders: list[dict[str, Any]]
) -> dict[str, Any]:
    ranked: list[dict[str, Any]] = []
    for folder in folders:
        exact_contracts = [
            reference
            for reference in metadata.get("contract_references", [])
            if _contains_reference(folder["corpus"], reference)
        ]
        exact_sigade = [
            sigade
            for sigade in metadata.get("sigade_numbers", [])
            if _contains_reference(folder["name"], sigade)
        ]
        office_hits = [
            office
            for office in metadata.get("referenced_offices", [])
            if _contains_reference("\n".join(folder["file_names"]), office)
        ]
        entity_hits = [
            entity
            for entity in metadata.get("entities", [])
            if _contains_reference(folder["name"], entity)
        ]
        creditor_hits = [
            creditor
            for creditor in metadata.get("creditors", [])
            if _contains_reference(
                f"{folder['group']} {folder['creditor']} {folder['name']}", creditor
            )
        ]
        score = 0
        method = ""
        evidence: list[str] = []
        if exact_contracts or exact_sigade:
            score = 100
            method = "Contrato o SIGADE exacto"
            evidence.extend(f"contrato exacto {value}" for value in exact_contracts)
            evidence.extend(f"SIGADE exacto {value}" for value in exact_sigade)
        elif office_hits and (entity_hits or creditor_hits):
            score = 100 if len(office_hits) >= 2 else 99
            method = "Antecedente y contraparte exactos"
            evidence.extend(f"antecedente exacto {value}" for value in office_hits)
        elif len(office_hits) >= 2:
            score = 99
            method = "Dos antecedentes exactos"
            evidence.extend(f"antecedente exacto {value}" for value in office_hits)
        elif entity_hits and creditor_hits:
            score = 94
            method = "Acreedor y entidad coinciden"
        if entity_hits:
            evidence.extend(f"entidad {value}" for value in entity_hits)
        if creditor_hits:
            evidence.extend(f"acreedor {value}" for value in creditor_hits)
        if score:
            ranked.append({**folder, "score": score, "method": method, "evidence": evidence})

    ranked.sort(key=lambda item: (item["score"], len(item["evidence"])), reverse=True)
    if not ranked:
        return {
            "destination": None,
            "score": 0,
            "method": "Sin coincidencia segura",
            "evidence": [],
            "notes": ["No se encontró una carpeta contractual vinculada al oficio."],
            "candidate_paths": [],
        }
    best_score = ranked[0]["score"]
    best = [item for item in ranked if item["score"] == best_score]
    if len(best) != 1:
        return {
            "destination": None,
            "score": 0,
            "method": "Coincidencia ambigua",
            "evidence": [f"{len(best)} carpetas obtuvieron el mismo puntaje"],
            "notes": ["No se archivó: debe revisarse la carpeta correcta."],
            "candidate_paths": [str(item["path"]) for item in best[:10]],
        }
    selected = best[0]
    return {
        "destination": selected["path"],
        "destination_name": selected["name"],
        "creditor": selected["creditor"],
        "group": selected["group"],
        "score": selected["score"],
        "method": selected["method"],
        "evidence": selected["evidence"],
        "notes": [],
        "candidate_paths": [],
    }


def _safe_target_name(office_reference: str, descriptor: str) -> str:
    office = re.sub(r"[<>:\"/\\|?*]+", "-", office_reference).strip(" .-")
    label = re.sub(r"[<>:\"/\\|?*]+", "-", descriptor).strip(" .-")
    if not office or not label:
        return ""
    return f"{office} ({label}).pdf"


def _find_identical(destination: Path, source: Path) -> Path | None:
    try:
        for existing in destination.iterdir():
            if (
                existing.is_file()
                and existing.suffix.casefold() == ".pdf"
                and existing.stat().st_size == source.stat().st_size
                and engine.same_file_content(source, existing)
            ):
                return existing
    except OSError:
        return None
    return None


def analyze_path(
    path: Path,
    folders: list[dict[str, Any]],
    learned: Counter[str],
    use_ocr: bool,
) -> dict[str, Any]:
    try:
        text, ocr_used = engine.searchable_pdf_text(path, use_ocr=use_ocr)
    except Exception as exc:
        return {
            "path": path,
            "metadata": {
                "candidate": False,
                "potential": bool(re.search(r"(?:OF|OFICIO|TEMP)", path.stem, re.I)),
                "office_reference": "",
                "subject": "",
                "signature_verified": False,
                "contract_references": [],
                "sigade_numbers": [],
                "ocr_used": False,
                "reasons": [f"No se pudo leer el PDF: {type(exc).__name__}: {exc}"],
            },
            "descriptor": "",
            "descriptor_method": "Error de lectura",
            "descriptor_evidence": [],
            "match": {
                "destination": None,
                "score": 0,
                "method": "Error de lectura",
                "evidence": [],
                "notes": [f"{type(exc).__name__}: {exc}"],
                "candidate_paths": [],
            },
        }
    metadata = extract_metadata(path, text, ocr_used)
    descriptor = ""
    descriptor_method = "No aplica"
    descriptor_evidence: list[str] = []
    if metadata["candidate"]:
        descriptor, descriptor_method, descriptor_evidence = choose_descriptor(
            metadata["subject"], text, learned
        )
        match = find_destination(metadata, folders)
        if not descriptor:
            match = {
                "destination": None,
                "score": 0,
                "method": "Nombre final por revisar",
                "evidence": [],
                "notes": ["No se identificó con seguridad la etapa del contrato."],
                "candidate_paths": [],
            }
    else:
        match = {
            "destination": None,
            "score": 0,
            "method": "Documento excluido por las reglas",
            "evidence": [],
            "notes": list(metadata.get("reasons", [])),
            "candidate_paths": [],
        }
    return {
        "path": path,
        "metadata": metadata,
        "descriptor": descriptor,
        "descriptor_method": descriptor_method,
        "descriptor_evidence": descriptor_evidence,
        "match": match,
    }


def _record_for(item: dict[str, Any], fingerprint: str) -> dict[str, Any]:
    path: Path = item["path"]
    metadata = item["metadata"]
    match = item["match"]
    destination: Path | None = match.get("destination")
    descriptor = item.get("descriptor", "")
    target_name = _safe_target_name(metadata.get("office_reference", ""), descriptor)
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
        "candidate": bool(metadata.get("candidate")),
        "potential": bool(metadata.get("potential")),
        "office_reference": metadata.get("office_reference", ""),
        "subject": metadata.get("subject", ""),
        "signature_verified": bool(metadata.get("signature_verified")),
        "is_temp": bool(metadata.get("is_temp")),
        "contract_references": metadata.get("contract_references", []),
        "sigade_numbers": metadata.get("sigade_numbers", []),
        "referenced_offices": metadata.get("referenced_offices", []),
        "entities": metadata.get("entities", []),
        "creditors_detected": metadata.get("creditors", []),
        "ocr_used": bool(metadata.get("ocr_used")),
        "descriptor": descriptor,
        "descriptor_method": item.get("descriptor_method", ""),
        "destination": str(destination) if destination else "",
        "destination_name": match.get("destination_name", ""),
        "creditor": match.get("creditor", ""),
        "group": match.get("group", ""),
        "target_name": target_name,
        "match_method": match.get("method", ""),
        "score": int(match.get("score", 0)),
        "evidence": [
            *match.get("evidence", []),
            *item.get("descriptor_evidence", []),
        ],
        "notes": _unique([*metadata.get("reasons", []), *match.get("notes", [])]),
        "candidate_paths": match.get("candidate_paths", []),
    }
    if not metadata.get("candidate"):
        record["status"] = "IGNORADO"
        record["target_name"] = ""
    elif not descriptor or not destination or record["score"] < AUTO_MATCH_SCORE:
        record["status"] = "REVISAR"
    else:
        target = destination / target_name
        try:
            identical = _find_identical(destination, path)
            result = "same" if identical else engine.atomic_copy_without_overwrite(path, target)
            if result == "copied":
                record["status"] = "ARCHIVADO"
            elif result == "same":
                record["status"] = "YA ARCHIVADO"
                if identical and identical.name != target.name:
                    record["notes"].append(
                        f"El mismo contenido ya existe como {identical.name}."
                    )
            else:
                record["status"] = "CONFLICTO"
                record["notes"].append(
                    f"Ya existe contenido diferente con el nombre {target.name}."
                )
        except Exception as exc:
            record["status"] = "ERROR"
            record["notes"].append(f"Error al copiar: {type(exc).__name__}: {exc}")
    return record


def scan_once(base_dir: str | Path, origin: str = "AUTOMÁTICO") -> dict[str, Any]:
    logger = configure_logging(base_dir)
    if not _scan_lock.acquire(blocking=False):
        return status_payload(base_dir)
    try:
        config = load_config(base_dir)
        source = Path(config["source"])
        destination_root = Path(config["destination"])
        state = load_state(base_dir)
        folders = discover_contract_folders(destination_root)
        if not folders:
            raise FileNotFoundError(
                "No se encontraron carpetas de Contratos de Agencia Fiscal en el archivo de acreedores."
            )
        learned = learned_descriptor_patterns(folders)
        now = datetime.now().astimezone().timestamp()
        scheduled: list[tuple[Path, str]] = []
        fingerprints_seen: set[str] = set()
        for path in _pdfs(source):
            try:
                stat = path.stat()
                if stat.st_size <= 0 or now - stat.st_mtime < STABLE_AGE_SECONDS:
                    continue
                fingerprint = file_fingerprint(path)
            except OSError:
                continue
            previous = state["records"].get(fingerprint)
            if previous and previous.get("analysis_version") == ANALYSIS_VERSION:
                if str(path) != previous.get("source_path"):
                    aliases = previous.setdefault("source_aliases", [])
                    if str(path) not in aliases:
                        aliases.append(str(path))
                continue
            if fingerprint not in fingerprints_seen:
                fingerprints_seen.add(fingerprint)
                scheduled.append((path, fingerprint))

        for path, fingerprint in scheduled:
            item = analyze_path(path, folders, learned, config["use_ocr"])
            record = _record_for(item, fingerprint)
            state["records"][fingerprint] = record
            logger.info(
                "origen=%s | archivo=%s | candidato=%s | estado=%s | destino=%s | match=%s%%",
                origin,
                path.name,
                record["candidate"],
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
        logger.exception("Falló el monitor de Contratos de Agencia Fiscal")
    finally:
        _scan_lock.release()
    return status_payload(base_dir)


def status_payload(base_dir: str | Path) -> dict[str, Any]:
    config = load_config(base_dir)
    state = load_state(base_dir)
    all_records = sorted(
        state["records"].values(),
        key=lambda record: record.get("processed_at", ""),
        reverse=True,
    )
    relevant_records = [
        record
        for record in all_records
        if record.get("candidate") or record.get("potential")
    ]
    counts = Counter(record.get("status", "") for record in all_records)
    review_statuses = {"REVISAR", "CONFLICTO", "ERROR"}
    try:
        folders = discover_contract_folders(Path(config["destination"]))
        learned = learned_descriptor_patterns(folders)
    except OSError:
        folders = []
        learned = Counter()
    return {
        "ok": _runtime.get("last_error") is None,
        "config": config,
        "generated_at": _now_iso(),
        "initialized_at": state.get("initialized_at"),
        "last_scan": _runtime.get("last_scan"),
        "last_error": _runtime.get("last_error"),
        "monitor_status": _runtime.get("monitor_status"),
        "monitor_interval_seconds": MONITOR_INTERVAL_SECONDS,
        "records": relevant_records[:250],
        "learned_patterns": [
            {"descriptor": descriptor, "count": count}
            for descriptor, count in learned.most_common(10)
        ],
        "summary": {
            "candidates": sum(bool(record.get("candidate")) for record in all_records),
            "archived": counts["ARCHIVADO"] + counts["YA ARCHIVADO"],
            "review": sum(counts[status] for status in review_statuses),
            "excluded": counts["IGNORADO"],
            "folders": len(folders),
            "patterns": sum(learned.values()),
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
            name="contratos-agencia-fiscal-monitor",
            daemon=True,
        )
        _worker.start()


def register_contratos_agencia_fiscal_routes(app, base_dir: str | Path) -> None:
    global _base_dir
    _base_dir = Path(base_dir)
    configure_logging(_base_dir)

    @app.get("/api/contratos_agencia_fiscal/status")
    def contratos_agencia_fiscal_status():
        return jsonify(status_payload(_base_dir))

    @app.post("/api/contratos_agencia_fiscal/scan")
    def contratos_agencia_fiscal_scan():
        return jsonify(scan_once(_base_dir, origin="MANUAL"))

    @app.post("/api/contratos_agencia_fiscal/config")
    def contratos_agencia_fiscal_config():
        updates = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "config": save_config(_base_dir, updates)})

    start_worker(_base_dir)
