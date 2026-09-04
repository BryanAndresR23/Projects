from __future__ import annotations

import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from flask import jsonify, request, session


STOPWORDS = {
    "a", "al", "con", "de", "del", "el", "en", "es", "esta", "este",
    "la", "las", "lo", "los", "para", "por", "que", "se", "su", "un",
    "una", "y",
}


def _normalizar(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-zA-Z0-9\s]", " ", value.lower())
    return " ".join(value.split())


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in _normalizar(value).split()
        if len(token) > 2 and token not in STOPWORDS
    }


def _similitud(query: str, example: str) -> float:
    query_norm = _normalizar(query)
    example_norm = _normalizar(example)
    if not query_norm or not example_norm:
        return 0.0
    query_tokens = _tokens(query_norm)
    example_tokens = _tokens(example_norm)
    union = query_tokens | example_tokens
    jaccard = len(query_tokens & example_tokens) / len(union) if union else 0.0
    sequence = SequenceMatcher(None, query_norm, example_norm).ratio()
    return min(1.0, (jaccard * 0.72) + (sequence * 0.28))


def register_respuestas_correos_routes(app, base_dir: str) -> None:
    db_path = Path(base_dir) / "respuestas_correos.db"

    @contextmanager
    def connect():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ensure_db() -> None:
        with connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS ejemplos_respuesta (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    categoria TEXT NOT NULL DEFAULT 'General',
                    asunto TEXT NOT NULL DEFAULT '',
                    correo_recibido TEXT NOT NULL,
                    respuesta_aprobada TEXT NOT NULL,
                    observaciones TEXT NOT NULL DEFAULT '',
                    fecha_creacion TEXT NOT NULL,
                    usos INTEGER NOT NULL DEFAULT 0
                )"""
            )
            for statement in (
                "ALTER TABLE ejemplos_respuesta ADD COLUMN creado_por TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE ejemplos_respuesta ADD COLUMN aceptaciones INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE ejemplos_respuesta ADD COLUMN ediciones INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE ejemplos_respuesta ADD COLUMN rechazos INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE ejemplos_respuesta ADD COLUMN ultimo_uso TEXT NOT NULL DEFAULT ''",
            ):
                try:
                    connection.execute(statement)
                except sqlite3.OperationalError:
                    pass

    ensure_db()

    @app.get("/api/respuestas-correos/examples")
    def respuestas_correos_examples():
        ensure_db()
        with connect() as connection:
            rows = connection.execute(
                """SELECT id, categoria, asunto, correo_recibido,
                          respuesta_aprobada, observaciones, fecha_creacion, usos,
                          creado_por, aceptaciones, ediciones, rechazos, ultimo_uso
                   FROM ejemplos_respuesta ORDER BY id DESC"""
            ).fetchall()
        examples = [dict(row) for row in rows]
        return jsonify(
            {
                "ok": True,
                "local": True,
                "examples": examples,
                "total": len(examples),
            }
        )

    @app.post("/api/respuestas-correos/learn")
    def respuestas_correos_learn():
        payload = request.get_json(silent=True) or {}
        subject = str(payload.get("subject") or "").strip()
        incoming = str(payload.get("incoming") or "").strip()
        response = str(payload.get("response") or "").strip()
        category = str(payload.get("category") or "General").strip() or "General"
        notes = str(payload.get("notes") or "").strip()
        if not incoming:
            return jsonify({"ok": False, "error": "Ingrese el correo recibido."}), 400
        if not response:
            return jsonify({"ok": False, "error": "Ingrese la respuesta aprobada."}), 400
        ensure_db()
        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with connect() as connection:
            duplicate = connection.execute(
                """SELECT id FROM ejemplos_respuesta
                   WHERE lower(trim(correo_recibido))=lower(trim(?))
                     AND lower(trim(respuesta_aprobada))=lower(trim(?))
                   LIMIT 1""",
                (incoming, response),
            ).fetchone()
            if duplicate:
                return jsonify(
                    {
                        "ok": True,
                        "message": "El ejemplo ya existía; no se creó un duplicado.",
                        "example_id": duplicate["id"],
                        "duplicate": True,
                    }
                )
            cursor = connection.execute(
                """INSERT INTO ejemplos_respuesta
                   (categoria, asunto, correo_recibido, respuesta_aprobada,
                    observaciones, fecha_creacion, creado_por)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (category, subject, incoming, response, notes, created_at, session.get("username", "")),
            )
            example_id = cursor.lastrowid
        return jsonify(
            {
                "ok": True,
                "message": "Respuesta incorporada a la base de aprendizaje local.",
                "example_id": example_id,
                "created_at": created_at,
            }
        )

    @app.post("/api/respuestas-correos/suggest")
    def respuestas_correos_suggest():
        payload = request.get_json(silent=True) or {}
        subject = str(payload.get("subject") or "").strip()
        incoming = str(payload.get("incoming") or "").strip()
        category = str(payload.get("category") or "").strip()
        if not incoming:
            return jsonify({"ok": False, "error": "Ingrese el correo que desea analizar."}), 400
        ensure_db()
        with connect() as connection:
            rows = connection.execute(
                """SELECT id, categoria, asunto, correo_recibido,
                          respuesta_aprobada, observaciones, fecha_creacion, usos,
                          aceptaciones, ediciones, rechazos
                   FROM ejemplos_respuesta"""
            ).fetchall()
        query = f"{subject} {incoming}"
        ranked = []
        for row in rows:
            item = dict(row)
            example = f"{item['asunto']} {item['correo_recibido']}"
            score = _similitud(query, example)
            if category and _normalizar(category) == _normalizar(item["categoria"]):
                score = min(1.0, score + 0.08)
            item["confidence"] = round(score * 100)
            ranked.append(item)
        ranked.sort(key=lambda item: (item["confidence"], item["usos"], item["id"]), reverse=True)
        matches = ranked[:3]
        best = matches[0] if matches else None
        if best:
            raw_confidence = best["confidence"]
            if len(rows) < 3:
                best["confidence"] = min(raw_confidence, 65)
            elif len(rows) < 5:
                best["confidence"] = min(raw_confidence, 85)
        return jsonify(
            {
                "ok": True,
                "local": True,
                "model": "Aprendizaje local por respuestas aprobadas",
                "suggestion": best["respuesta_aprobada"] if best else "",
                "confidence": best["confidence"] if best else 0,
                "matches": matches,
                "message": (
                    (
                        "Sugerencia preliminar: la base todavía tiene menos de tres ejemplos aprobados."
                        if len(rows) < 3
                        else "Sugerencia preparada a partir de respuestas aprobadas similares."
                    )
                    if best
                    else "Aún no hay respuestas aprendidas. Registre el primer ejemplo aprobado."
                ),
            }
        )

    @app.post("/api/respuestas-correos/feedback")
    def respuestas_correos_feedback():
        payload = request.get_json(silent=True) or {}
        try:
            example_id = int(payload.get("example_id"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Ejemplo inválido."}), 400
        outcome = str(payload.get("outcome") or "").strip().lower()
        columns = {"accepted": "aceptaciones", "edited": "ediciones", "rejected": "rechazos"}
        column = columns.get(outcome)
        if not column:
            return jsonify({"ok": False, "error": "Resultado de aprendizaje inválido."}), 400
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with connect() as connection:
            cursor = connection.execute(
                f"UPDATE ejemplos_respuesta SET {column}={column}+1, usos=usos+1, ultimo_uso=? WHERE id=?",
                (now, example_id),
            )
            if not cursor.rowcount:
                return jsonify({"ok": False, "error": "El ejemplo ya no existe."}), 404
        return jsonify({"ok": True, "message": "Resultado registrado para mejorar futuras sugerencias."})
