from __future__ import annotations

from pathlib import Path

from flask import jsonify, send_file


VALIDATOR_SWIFT_FILE = Path(
    r"C:\Users\bromo\Desktop\08 Otros\Validador Swift FINAL.HTML"
)


def register_activaciones_cuentas_routes(app, base_dir: str) -> None:
    @app.get("/activaciones-cuentas/validador")
    def activaciones_cuentas_validador():
        if not VALIDATOR_SWIFT_FILE.is_file():
            return (
                "<main style='font-family:Segoe UI;padding:32px'>"
                "<h2>Validador Swift no disponible</h2>"
                f"<p>No se encontró el archivo: {VALIDATOR_SWIFT_FILE}</p>"
                "</main>",
                404,
            )
        return send_file(VALIDATOR_SWIFT_FILE, mimetype="text/html")

    @app.get("/api/activaciones-cuentas/status")
    def activaciones_cuentas_status():
        return jsonify(
            {
                "ok": True,
                "available": VALIDATOR_SWIFT_FILE.is_file(),
                "path": str(VALIDATOR_SWIFT_FILE),
            }
        )

