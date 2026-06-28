# -*- coding: utf-8 -*-
"""
PANEL CONCILIACIÓN BCE vs MEF
Deuda Externa Pública — cruce mensual por acreedor

Aplicación web local (Flask) que reutiliza la lógica probada de
Conciliacion.py. Permite cargar los dos reportes (.xls), ejecutar el
cruce de los 6 conceptos por acreedor, ver el resultado con semáforo,
exportar el Excel y guardar el historial en SQLite.

Uso:
    pip install flask xlrd openpyxl
    python conciliacion_app.py
    -> abre http://127.0.0.1:5001/
"""
import os
import sqlite3
import webbrowser
from datetime import datetime
from threading import Timer

from flask import (Flask, request, jsonify, render_template_string,
                   send_file)
from werkzeug.utils import secure_filename

# Reutilizamos la lógica de conciliación ya validada.
# Debe estar Conciliacion.py en la MISMA carpeta que este archivo.
try:
    import Conciliacion as C
except ModuleNotFoundError:
    import sys
    print("=" * 60)
    print("  ERROR: falta 'Conciliacion.py' en esta carpeta.")
    print("  Copia conciliacion_app.py y Conciliacion.py JUNTOS,")
    print("  en la misma carpeta, y vuelve a ejecutar.")
    print("=" * 60)
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "conciliacion.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads_conciliacion")
EXPORT_DIR = os.path.join(BASE_DIR, "reportes_conciliacion")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)

# =============================================================================
# BASE DE DATOS
# =============================================================================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS conciliaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        periodo TEXT,
        acreedor TEXT,
        concepto TEXT,
        mef REAL,
        bce REAL,
        diferencia REAL,
        estado TEXT,
        fecha_corrida TEXT
    )''')
    # Migración: columna 'nota' (para pagos directos agregados manualmente)
    try:
        c.execute("ALTER TABLE conciliaciones ADD COLUMN nota TEXT DEFAULT ''")
    except Exception:
        pass
    conn.commit()
    conn.close()


def db_q(sql, params=(), fetch=False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(sql, params)
    rows = [dict(r) for r in c.fetchall()] if fetch else None
    conn.commit()
    conn.close()
    return rows


# =============================================================================
# FLASK
# =============================================================================
app = Flask(__name__)

# Archivos cargados por periodo (para regenerar el BCE ajustado con pagos directos)
PERIODO_FILES = {}


@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    tb = traceback.format_exc()
    print("=" * 60); print("ERROR NO MANEJADO:"); print(tb); print("=" * 60)
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": str(e), "traceback": tb}), 200
    return (f"<pre style='padding:20px;background:#0f1419;color:#e8ecef;'>"
            f"ERROR\n\n{tb}</pre>", 200)


def _guardar_subida(storage):
    """Guarda un FileStorage y devuelve su ruta, o None si no vino."""
    if not storage or not storage.filename:
        return None
    nombre = secure_filename(storage.filename)
    ruta = os.path.join(UPLOAD_DIR, nombre)
    storage.save(ruta)
    return ruta


def _origen_reporte(ruta):
    """Detecta si un .xls es del BCE o del MEF mirando sus hojas.
    BCE -> tiene hojas 'Giros del/al Exterior'; MEF -> tiene hoja 'Resumen'."""
    try:
        import xlrd
        nombres = [h.name.lower() for h in xlrd.open_workbook(ruta).sheets()]
    except Exception:
        return None
    if any("giros" in n for n in nombres):
        return "bce"
    if any("resumen" in n for n in nombres):
        return "mef"
    return None


def _clasificar_subidas():
    """Toma TODOS los archivos subidos (en cualquier campo) y los reparte en
    BCE/MEF por su contenido. Así el usuario puede arrastrarlos en cualquier
    orden o los dos juntos. Devuelve (ruta_bce, ruta_mef, advertencias)."""
    storages = []
    for campo in request.files:
        storages.extend(request.files.getlist(campo))
    ruta_bce = ruta_mef = None
    avisos = []
    for st in storages:
        ruta = _guardar_subida(st)
        if not ruta:
            continue
        origen = _origen_reporte(ruta)
        if origen == "bce" and not ruta_bce:
            ruta_bce = ruta
        elif origen == "mef" and not ruta_mef:
            ruta_mef = ruta
        elif origen is None:
            avisos.append(f"No reconocí '{os.path.basename(ruta)}' (sin hojas Giros/Resumen)")
        else:
            avisos.append(f"Dos reportes del mismo origen ({origen.upper()}): ignoré '{os.path.basename(ruta)}'")
    return ruta_bce, ruta_mef, avisos


@app.route("/api/conciliar", methods=["POST"])
def api_conciliar():
    """Recibe los dos .xls (multipart) o rutas (json), ejecuta el cruce,
    guarda el resultado en BD y lo devuelve."""
    avisos = []
    if request.files:
        # Detección automática por contenido: no importa en qué zona se soltó
        # cada archivo, ni el orden, ni si se arrastraron los dos juntos.
        ruta_bce, ruta_mef, avisos = _clasificar_subidas()
        periodo = request.form.get("periodo", "").strip()
    else:
        data = request.json or {}
        ruta_bce = data.get("ruta_bce")
        ruta_mef = data.get("ruta_mef")
        periodo = (data.get("periodo") or "").strip()

    if not ruta_bce or not os.path.exists(ruta_bce):
        return jsonify({"ok": False, "error": "No identifiqué el reporte del BCE "
                        "(debe tener hojas 'Giros del/al Exterior'). " + " ".join(avisos)})
    if not ruta_mef or not os.path.exists(ruta_mef):
        return jsonify({"ok": False, "error": "No identifiqué el reporte del MEF "
                        "(debe tener hoja 'Resumen'). " + " ".join(avisos)})

    filas = C.conciliar_archivos(ruta_bce, ruta_mef)
    # Periodo detectado automáticamente del archivo (el usuario no lo elige)
    info_periodo = C.detectar_periodo(ruta_mef, ruta_bce)
    periodo = info_periodo["periodo"]
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Recordar los archivos de este periodo para los ajustes posteriores
    PERIODO_FILES[periodo] = {"bce": ruta_bce, "mef": ruta_mef, "ajustes": []}

    # Reemplazar corridas previas del mismo periodo
    db_q("DELETE FROM conciliaciones WHERE periodo = ?", (periodo,))
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for ac, concepto, vm, vb, dif, estado in filas:
        db_q("""INSERT INTO conciliaciones
                (periodo, acreedor, concepto, mef, bce, diferencia, estado, fecha_corrida, nota)
                VALUES (?,?,?,?,?,?,?,?,?)""",
             (periodo, ac, concepto, vm, vb, dif, estado, ahora, ""))

    res = _resultado_periodo(periodo)
    res.update({
        "fecha": ahora, "info_periodo": info_periodo,
        "archivo_bce": os.path.basename(ruta_bce),
        "archivo_mef": os.path.basename(ruta_mef), "avisos": avisos,
    })
    return jsonify(res)


@app.route("/api/exportar", methods=["POST"])
def api_exportar():
    """Genera el Excel con semáforo del periodo indicado y lo descarga."""
    data = request.json or {}
    periodo = (data.get("periodo") or "").strip()
    if not periodo:
        return jsonify({"ok": False, "error": "Falta el periodo"})
    rows = db_q("""SELECT acreedor, concepto, mef, bce, diferencia, estado
                   FROM conciliaciones WHERE periodo = ?
                   ORDER BY id""", (periodo,), fetch=True)
    if not rows:
        return jsonify({"ok": False, "error": "No hay datos para ese periodo"})
    filas = [(r["acreedor"], r["concepto"], r["mef"], r["bce"],
              r["diferencia"], r["estado"]) for r in rows]
    nombre = f"Conciliacion_BCE_MEF_{periodo.replace('/', '-')}.xlsx"
    ruta = os.path.join(EXPORT_DIR, nombre)
    if not C.reporte_xlsx(filas, ruta):
        return jsonify({"ok": False, "error": "openpyxl no disponible"})
    return send_file(ruta, as_attachment=True, download_name=nombre)


@app.route("/api/historial")
def api_historial():
    """Lista los periodos conciliados y su resumen."""
    rows = db_q("""SELECT periodo,
                          COUNT(*) AS total,
                          SUM(CASE WHEN estado='CONCILIADO' THEN 1 ELSE 0 END) AS conciliados,
                          SUM(CASE WHEN estado='DIFERENCIA' THEN 1 ELSE 0 END) AS diferencias,
                          MAX(fecha_corrida) AS ultima
                   FROM conciliaciones
                   GROUP BY periodo ORDER BY periodo DESC""", fetch=True)
    return jsonify({"ok": True, "periodos": rows or []})


@app.route("/api/periodo/<periodo>")
def api_periodo(periodo):
    rows = db_q("""SELECT acreedor, concepto, mef, bce, diferencia, estado, nota
                   FROM conciliaciones WHERE periodo = ? ORDER BY id""",
                (periodo,), fetch=True)
    return jsonify({"ok": True, "periodo": periodo, "registros": rows or []})


# -------------------------------------------------------------------------
# Resultado consolidado de un periodo desde la BD (reutilizado por ajustes)
# -------------------------------------------------------------------------
def _resultado_periodo(periodo):
    rows = db_q("""SELECT acreedor, concepto, mef, bce, diferencia, estado, nota
                   FROM conciliaciones WHERE periodo = ? ORDER BY id""",
                (periodo,), fetch=True) or []
    filas = [(r["acreedor"], r["concepto"], r["mef"], r["bce"],
              r["diferencia"], r["estado"]) for r in rows]
    conciliados = sum(1 for r in rows if r["estado"] == "CONCILIADO")
    diferencias = len(rows) - conciliados
    total_dif = round(sum(abs(r["diferencia"]) for r in rows if r["estado"] == "DIFERENCIA"), 2)
    totales = [{"acreedor": ac, **t} for ac, t in C.totales_por_cartera(filas).items()]
    # Gran total (todas las carteras)
    g_mef = round(sum(t["mef"] for t in totales), 2)
    g_bce = round(sum(t["bce"] for t in totales), 2)
    g_dif = round(g_mef - g_bce, 2) or 0.0
    # ¿Todo concilia? (gobierna habilitación de Quipux)
    conciliado_total = diferencias == 0 and len(rows) > 0
    # Diagnóstico SOLO de lo que no concilia
    diag = []
    mef_path = (PERIODO_FILES.get(periodo) or {}).get("mef")
    if mef_path and os.path.exists(mef_path):
        try:
            diag = C.diagnostico(mef_path, filas)
        except Exception:
            diag = []
    bce_aj = bool((PERIODO_FILES.get(periodo) or {}).get("ajustes"))
    return {
        "ok": True, "periodo": periodo, "registros": rows,
        "total": len(rows), "conciliados": conciliados, "diferencias": diferencias,
        "total_diferencia": total_dif, "totales": totales,
        "gran_total": {"mef": g_mef, "bce": g_bce, "dif": g_dif},
        "conciliado_total": conciliado_total, "diagnostico": diag,
        "bce_ajustado": bce_aj,
    }


@app.route("/api/pago_directo", methods=["POST"])
def api_pago_directo():
    """Previsualiza los respaldos de pago directo del MEF (sin aplicar)."""
    previos = []
    storages = []
    for campo in request.files:
        storages.extend(request.files.getlist(campo))
    for st in storages:
        ruta = _guardar_subida(st)
        if not ruta:
            continue
        try:
            pd = C.leer_pago_directo(ruta)
            pd["archivo"] = os.path.basename(ruta)
            previos.append(pd)
        except Exception as e:
            previos.append({"archivo": st.filename, "error": str(e)})
    return jsonify({"ok": True, "previos": previos})


@app.route("/api/aplicar_pago_directo", methods=["POST"])
def api_aplicar_pago_directo():
    """Agrega un pago directo al lado del BCE (Desembolsos) y recalcula."""
    data = request.json or {}
    periodo = (data.get("periodo") or "").strip()
    acreedor = (data.get("acreedor") or "").strip().upper()
    concepto = (data.get("concepto") or "Desembolsos").strip()
    valor = float(data.get("valor") or 0)
    nota = (data.get("nota") or "").strip()
    if not periodo or not acreedor or valor == 0:
        return jsonify({"ok": False, "error": "Faltan periodo, acreedor o valor"})

    fila = db_q("""SELECT * FROM conciliaciones
                   WHERE periodo=? AND acreedor=? AND concepto=?""",
                (periodo, acreedor, concepto), fetch=True)
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if fila:
        r = fila[0]
        nuevo_bce = round((r["bce"] or 0) + valor, 2)
        dif = round((r["mef"] or 0) - nuevo_bce, 2) or 0.0
        estado = "CONCILIADO" if abs(dif) <= C.TOLERANCIA else "DIFERENCIA"
        nota_final = (r["nota"] + " | " + nota).strip(" |") if r["nota"] else nota
        db_q("""UPDATE conciliaciones SET bce=?, diferencia=?, estado=?, nota=?
                WHERE id=?""", (nuevo_bce, dif, estado, nota_final, r["id"]))
    else:
        # No existía fila de Desembolsos para esa cartera: se crea
        dif = round(0 - valor, 2)
        estado = "CONCILIADO" if abs(dif) <= C.TOLERANCIA else "DIFERENCIA"
        db_q("""INSERT INTO conciliaciones
                (periodo, acreedor, concepto, mef, bce, diferencia, estado, fecha_corrida, nota)
                VALUES (?,?,?,?,?,?,?,?,?)""",
             (periodo, acreedor, concepto, 0.0, valor, dif, estado, ahora, nota))

    # Registrar el ajuste y regenerar el reporte BCE ajustado (descargable)
    info = PERIODO_FILES.setdefault(periodo, {"bce": None, "mef": None, "ajustes": []})
    info["ajustes"].append({"acreedor": acreedor, "referencia": data.get("referencia", ""),
                            "valor": valor, "nota": nota,
                            "prestamista": data.get("prestamista", acreedor)})
    if info.get("bce") and os.path.exists(info["bce"]):
        try:
            salida = os.path.join(EXPORT_DIR, f"Reporte_Conciliacion_BCE_{periodo}_ajustado.xlsx")
            C.exportar_bce_ajustado(info["bce"], info["ajustes"], salida)
        except Exception:
            pass

    return jsonify(_resultado_periodo(periodo))


@app.route("/api/descargar_bce_ajustado")
def api_descargar_bce_ajustado():
    """Descarga el reporte BCE con las filas de pago directo agregadas."""
    periodo = (request.args.get("periodo") or "").strip()
    ruta = os.path.join(EXPORT_DIR, f"Reporte_Conciliacion_BCE_{periodo}_ajustado.xlsx")
    if not os.path.exists(ruta):
        return jsonify({"ok": False, "error": "Aún no hay un reporte BCE ajustado para este periodo"})
    return send_file(ruta, as_attachment=True,
                     download_name=f"Reporte_Conciliacion_BCE_{periodo}_ajustado.xlsx")


# =============================================================================
# FRONT-END (una sola página, estilo BCE oscuro)
# =============================================================================
PANEL_HTML = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Conciliacion Deuda Externa Publica</title>
<style>
  :root{--navy:#16315a;--blue:#2563eb;--blue2:#1d4ed8;--bg:#eef1f6;--card:#fff;--line:#e3e8f0;
        --txt:#1f2a3d;--muted:#64748b;--ok:#16a34a;--okbg:#e9f8ef;--bad:#dc2626;--badbg:#fdeaea;
        --gold:#d97706;--yellow:#ffd100;}
  *{box-sizing:border-box}html,body{margin:0;height:100%}
  body{display:flex;background:var(--bg);color:var(--txt);font-family:"Segoe UI",system-ui,sans-serif;font-size:14px}
  .side{width:250px;flex:none;background:linear-gradient(180deg,var(--navy),#102241);color:#dce5f5;display:flex;flex-direction:column;min-height:100vh}
  .brand{padding:20px;border-bottom:1px solid #ffffff1a}.brand .flag{font-size:26px}
  .brand h2{margin:8px 0 2px;font-size:15px;letter-spacing:.5px;color:#fff}.brand small{color:#9fb3d4;font-size:11px}
  .nav{padding:12px 10px}.nav .it{display:flex;gap:11px;align-items:center;padding:11px;border-radius:10px;cursor:pointer;color:#c6d4ec;margin-bottom:4px}
  .nav .it:hover{background:#ffffff12}.nav .it.act{background:#ffffff1a;color:#fff}
  .nav .it.dis{opacity:.45;cursor:not-allowed}
  .nav .it .num{width:25px;height:25px;border-radius:50%;flex:none;display:grid;place-items:center;font-size:12px;font-weight:800;background:#ffffff1f;color:#fff}
  .nav .it.act .num{background:var(--yellow);color:#16315a}.nav .it .tt{font-weight:600;font-size:13px}.nav .it .ss{font-size:10.5px;color:#9fb3d4}
  .perbox{margin:6px 14px;padding:9px 12px;background:#ffffff14;border-radius:8px;text-align:center;font-weight:700;color:#fff;font-size:13px}
  .estado{padding:14px 18px;border-top:1px solid #ffffff1a;font-size:12px;margin-top:auto}
  .estado .h{color:#9fb3d4;text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px;font-size:10.5px}
  .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px}
  .dot.ok{background:#34d399}.dot.bad{background:#f87171}
  .estado .e{margin-bottom:5px}
  .main{flex:1;min-width:0;display:flex;flex-direction:column}
  .top{background:#fff;border-bottom:1px solid var(--line);padding:13px 26px;display:flex;align-items:center;justify-content:space-between}
  .top h1{margin:0;font-size:16px;color:var(--navy)}.top .right{display:flex;gap:12px;align-items:center}
  .badge{background:#dbe5fb;color:var(--blue2);padding:5px 12px;border-radius:999px;font-size:12px;font-weight:700}
  .badge.ok{background:var(--okbg);color:var(--ok)}
  .uchip{display:flex;align-items:center;gap:8px}.uav{width:30px;height:30px;border-radius:50%;background:var(--navy);color:#fff;display:grid;place-items:center;font-size:12px;font-weight:800}
  .content{padding:24px 26px;overflow:auto}
  h3.sec{display:flex;align-items:center;gap:10px;color:var(--navy);margin:0 0 4px}.sub{color:var(--muted);margin:0 0 18px;font-size:13px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;margin-bottom:18px;box-shadow:0 1px 3px #0f172a0d}
  .info{border-left:4px solid var(--blue);background:#f3f7ff;border-radius:8px;padding:11px 15px;margin-bottom:18px;font-size:12.5px}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}@media(max-width:820px){.grid2{grid-template-columns:1fr}}
  .drop{border:2px dashed var(--line);border-radius:14px;padding:24px 16px;text-align:center;background:#fafbfe}
  .drop.over{border-color:var(--blue);background:#eef4ff}.drop.set{border-color:var(--ok);background:var(--okbg)}
  .drop .ic{font-size:32px}.drop .ti{font-weight:700;font-size:15px;margin-top:8px;color:var(--navy)}.drop .de{color:var(--muted);font-size:12px}
  .drop .dz{color:var(--muted);font-size:12px;margin-top:9px}.drop .fn{margin-top:8px;font-size:12px;color:var(--ok);font-weight:600;word-break:break-all}
  .drop .btn-sel{margin-top:14px;border-top:1px solid var(--line);padding-top:12px}.lnk{color:var(--blue);font-weight:700;cursor:pointer;font-size:14px;background:none;border:0}.lnk:hover{text-decoration:underline}
  input[type=file]{display:none}
  .footbar{display:flex;align-items:center;justify-content:space-between;gap:16px}
  .btn{background:linear-gradient(135deg,var(--blue),var(--blue2));color:#fff;border:0;border-radius:10px;padding:11px 20px;font-size:14px;font-weight:800;cursor:pointer;box-shadow:0 6px 16px #2563eb40}
  .btn:hover{filter:brightness(1.07)}.btn:disabled{opacity:.5;cursor:not-allowed;box-shadow:none}
  .btn.alt{background:#fff;color:var(--blue);border:1px solid #c7d6f5;box-shadow:none}
  .frow{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-end}.fld label{display:block;font-size:11px;color:var(--muted);font-weight:700;margin-bottom:6px}
  input[type=text],input[type=number]{border:1px solid var(--line);border-radius:9px;padding:9px 11px;font-size:14px;background:#fff;color:var(--txt)}
  .mtable{width:100%;border-collapse:collapse;font-size:12.5px}
  .mtable th,.mtable td{padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
  .mtable thead th{background:var(--navy);color:#fff;text-align:center;font-size:11px;position:sticky;top:0}
  .mtable thead tr.sub th{background:#22416e;font-weight:600;font-size:10px;color:#cfe0f5}
  .mtable thead th.acr,.mtable thead th.est{background:#0f2545;text-align:left;vertical-align:middle}
  .mtable td.acr{font-weight:700;color:var(--navy)}.mtable td.num{text-align:right;font-variant-numeric:tabular-nums}
  .mtable td.d{color:var(--bad);font-weight:700;text-align:right}.mtable td.d.ok{color:var(--ok)}
  .mtable tr.ok td.acr,.mtable tr.ok td.est{background:#f0fbf4}.mtable tr.bad td.acr,.mtable tr.bad td.est{background:#fdf2f2}
  .mtable td.sep{border-left:1px solid #e7ecf4}
  .mtable tr.tot td{background:var(--navy);color:#fff;font-weight:800;border:0}
  .mtable tr.tot td.d{color:#ffd9d9}.mtable tr.tot td.d.ok{color:#9af0c0}
  .est-pill{padding:3px 9px;border-radius:999px;font-size:10.5px;font-weight:800}
  .est-pill.ok{background:var(--okbg);color:var(--ok)}.est-pill.bad{background:#fef3c7;color:#92400e}
  .scrollx{overflow:auto;border:1px solid var(--line);border-radius:12px;max-height:600px}
  .hidden{display:none}#msg{font-size:13px;margin-top:8px}
  .note-ok{background:#ecfdf3;border:1px solid #bbf7d0;color:#166534;border-radius:12px;padding:22px;text-align:center;font-size:15px;font-weight:700}
  .note-warn{background:#fff7ed;border:1px solid #fed7aa;color:#9a3412;border-radius:12px;padding:18px;font-size:13.5px}
  .diaghead{background:var(--navy);color:#fff;border-radius:12px;padding:13px 16px;margin-bottom:14px;font-weight:700}
  .diaghead small{display:block;color:#9fb3d4;font-weight:400;font-size:11.5px;margin-top:2px}
  .dcard{background:#fff;border:1px solid var(--line);border-radius:12px;padding:15px;margin-bottom:12px}
  .dcard.pd{background:#fffbeb;border-color:#fde68a}.dcard.dc{background:#eff6ff;border-color:#bfdbfe}
  .dcard .dh{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:9px}.dcard .dname{font-weight:800;color:var(--navy)}
  .dtag{padding:3px 9px;border-radius:999px;font-size:10.5px;font-weight:700}.dtag.pd{background:#fef3c7;color:#92400e}.dtag.dc{background:#dbeafe;color:#1e40af}.dtag.ot{background:#f1f5f9;color:#475569}
  .dmonto{margin-left:auto;color:var(--bad);font-weight:800;font-size:12.5px}
  .dobs{background:#fff;border:1px solid var(--line);border-radius:8px;padding:8px 11px;font-size:12px;margin-bottom:9px}
  .dgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px}@media(max-width:760px){.dgrid{grid-template-columns:1fr}}
  .dbox{background:#fff;border:1px solid var(--line);border-radius:8px;padding:9px 11px}.dbox .bt{font-size:10.5px;font-weight:800;text-transform:uppercase;margin-bottom:3px}.dbox.why .bt{color:#b45309}.dbox.act .bt{color:var(--ok)}.dbox .bd{font-size:12px;color:#334155}
  textarea{width:100%;height:440px;border:1px solid var(--line);border-radius:10px;padding:13px;font-family:Consolas,monospace;font-size:12.5px;color:var(--txt);background:#fcfdff}
</style></head>
<body>
  <aside class="side">
    <div class="brand"><div class="flag">&#127466;&#127464;</div><h2>CONCILIACION<br>DEUDA EXTERNA</h2><small>Subgerencia de Deuda Publica</small></div>
    <nav class="nav" id="nav">
      <div class="it act" data-v="cargar"><div class="num">1</div><div><div class="tt">Cargar Reportes</div><div class="ss">MEF + BCE</div></div></div>
      <div class="it" data-v="resultados"><div class="num">2</div><div><div class="tt">Resultados de Conciliacion</div><div class="ss">Detalle por cartera</div></div></div>
      <div class="it" data-v="ajustes"><div class="num">3</div><div><div class="tt">Ajuste - Observaciones</div><div class="ss">Solo si no concilia</div></div></div>
      <div class="it" data-v="quipux"><div class="num">4</div><div><div class="tt">Generar Quipux</div><div class="ss">Si todo concilia</div></div></div>
    </nav>
    <div class="perbox" id="perBox" style="display:none"></div>
    <div class="estado"><div class="h">Estado de carteras</div><div id="estadoSide" style="color:#9fb3d4">Sin datos</div></div>
  </aside>
  <div class="main">
    <div class="top"><h1>Sistema de Conciliacion &ndash; Deuda Externa Publica</h1>
      <div class="right"><span class="badge" id="periodoBadge">Sin periodo</span>
        <span class="uchip"><span class="uav">U</span><span class="muted">Usuario</span></span></div></div>
    <div class="content">

      <section id="v-cargar">
        <h3 class="sec">&#128193; Cargar Reportes MEF + BCE</h3>
        <p class="sub">Arrastra los reportes o usa los botones. El periodo se detecta automaticamente del archivo. Soporta .xls y .xlsx</p>
        <div class="info"><b>Mapeo:</b> Desembolsos (MEF C &harr; BCE K) &middot; Amortizaciones (D &harr; U) &middot; Intereses (E &harr; V) &middot; Comisiones (F &harr; W) &middot; Condonados (G &harr; Y) &middot; Mora (H &harr; X)</div>
        <div class="grid2">
          <div class="drop" id="dzMef" ondrop="onDrop(event,'mef')" ondragover="onOver(event)" ondragleave="onLeave(event)">
            <div class="ic">&#127963;&#65039;</div><div class="ti">Reporte MEF</div><div class="de">Ministerio de Economia y Finanzas</div>
            <div class="dz">&#128229; Arrastra aqui o haz clic</div><div class="fn" id="fnMef"></div>
            <div class="btn-sel"><button class="lnk" onclick="document.getElementById('fileMef').click()">&#128193; Seleccionar archivo MEF</button></div>
            <input type="file" id="fileMef" accept=".xls,.xlsx" onchange="onPick(this,'mef')"></div>
          <div class="drop" id="dzBce" ondrop="onDrop(event,'bce')" ondragover="onOver(event)" ondragleave="onLeave(event)">
            <div class="ic">&#127974;</div><div class="ti">Reporte BCE</div><div class="de">Banco Central del Ecuador</div>
            <div class="dz">&#128229; Arrastra aqui o haz clic</div><div class="fn" id="fnBce"></div>
            <div class="btn-sel"><button class="lnk" onclick="document.getElementById('fileBce').click()">&#128193; Seleccionar archivo BCE</button></div>
            <input type="file" id="fileBce" accept=".xls,.xlsx" onchange="onPick(this,'bce')"></div>
        </div>
        <div class="card footbar"><span class="muted" id="cargarMsg">Carga ambos archivos para continuar.</span>
          <button class="btn" id="btnRun" onclick="ejecutar()" disabled>&#9654; Ejecutar Conciliacion</button></div>
        <div id="msg"></div>
      </section>

      <section id="v-resultados" class="hidden">
        <h3 class="sec">&#128202; Resultados de Conciliacion</h3>
        <p class="sub" id="resSub">&mdash;</p>
        <div id="resAlert"></div>
        <div class="card"><div class="scrollx"><table class="mtable" id="mtable"></table></div></div>
      </section>

      <section id="v-ajustes" class="hidden">
        <h3 class="sec">&#9881;&#65039; Ajuste - Observaciones Conciliacion</h3>
        <p class="sub">Solo aplica cuando una o mas carteras no concilian.</p>
        <div id="ajContenido"></div>
      </section>

      <section id="v-quipux" class="hidden">
        <h3 class="sec">&#128220; Generar Quipux de respuesta</h3>
        <div id="quipuxContenido"></div>
      </section>
    </div>
  </div>
<script>
const CONCEPTOS=["Desembolsos","Amortizaciones","Intereses","Comisiones","Intereses Condonados","Interés por Mora"];
const COLMAP={"Desembolsos":["C","K"],"Amortizaciones":["D","U"],"Intereses":["E","V"],"Comisiones":["F","W"],"Intereses Condonados":["G","Y"],"Interés por Mora":["H","X"]};
const RUBRO_TXT={"Desembolsos":"desembolsos","Amortizaciones":"amortizaciones","Intereses":"intereses","Comisiones":"comisiones","Intereses Condonados":"condonados","Interés por Mora":"intereses por mora"};
const CARTERA_TXT={"AMAZON DAC":"AMAZON"};
const ORDEN_QUIPUX=["AIIB","AMAZON DAC","BANCOS","BID","BIRF","BONOS","CAF","FIDA","FLAR","FMI","GOBIERNOS","GPS"];
let FILES={mef:null,bce:null}, DATA=[], TOTALES=[], GTOT=null, DIAG=[], INFO=null, RES=null, PERIODO="";
const fmt=n=>(n||0).toLocaleString("es-EC",{minimumFractionDigits:2,maximumFractionDigits:2});
function msg(t,err){const m=document.getElementById("msg");m.textContent=t;m.style.color=err?"#dc2626":"#16a34a";}
const valido=f=>f&&/\.(xls|xlsx)$/i.test(f.name);
function recibir(f,lado){if(!valido(f)){alert("Solo .xls o .xlsx");return;}FILES[lado]=f;
  document.getElementById(lado==="mef"?"fnMef":"fnBce").textContent="✓ "+f.name;
  document.getElementById(lado==="mef"?"dzMef":"dzBce").classList.add("set");
  const ok=FILES.mef&&FILES.bce;document.getElementById("btnRun").disabled=!ok;
  document.getElementById("cargarMsg").textContent=ok?"Listo para conciliar.":"Carga ambos archivos para continuar.";}
function onPick(i,l){if(i.files[0])recibir(i.files[0],l);}
function onOver(e){e.preventDefault();e.currentTarget.classList.add("over");}
function onLeave(e){e.currentTarget.classList.remove("over");}
function onDrop(e,l){e.preventDefault();e.currentTarget.classList.remove("over");const f=e.dataTransfer.files[0];if(f)recibir(f,l);}

async function ejecutar(){
  if(!(FILES.mef&&FILES.bce))return;
  const fd=new FormData();fd.append("archivos",FILES.mef);fd.append("archivos",FILES.bce);
  const btn=document.getElementById("btnRun");btn.disabled=true;msg("Procesando...");
  try{const j=await (await fetch("/api/conciliar",{method:"POST",body:fd})).json();
    if(!j.ok){msg(j.error||"Error",true);btn.disabled=false;return;}
    aplicarResultado(j);msg("Conciliacion lista para "+(INFO?INFO.texto:PERIODO)+".");irA("resultados");
  }catch(e){msg(e.message,true);}
  btn.disabled=false;
}
function aplicarResultado(j){
  RES=j;DATA=j.registros;TOTALES=j.totales||[];GTOT=j.gran_total;DIAG=j.diagnostico||[];INFO=j.info_periodo||INFO;PERIODO=j.periodo;
  document.getElementById("periodoBadge").textContent=INFO?INFO.texto:PERIODO;
  document.getElementById("periodoBadge").className="badge "+(j.conciliado_total?"ok":"");
  const pb=document.getElementById("perBox");pb.style.display="block";pb.textContent=(INFO?INFO.texto:PERIODO);
  pintarSidebar();pintarMatriz();pintarAjustes();pintarQuipux();actualizarNav();
}
function actualizarNav(){
  const it3=document.querySelector('.nav .it[data-v="ajustes"]');
  const it4=document.querySelector('.nav .it[data-v="quipux"]');
  it4.classList.toggle("dis",!(RES&&RES.conciliado_total));
}
function pintarSidebar(){
  const es=document.getElementById("estadoSide");
  if(!TOTALES.length){es.textContent="Sin datos";return;}
  es.innerHTML=TOTALES.map(t=>`<div class="e"><span class="dot ${t.estado==='CONCILIADO'?'ok':'bad'}"></span>${CARTERA_TXT[t.acreedor]||t.acreedor}</div>`).join("");
}
function pintarMatriz(){
  const piv={};DATA.forEach(r=>{(piv[r.acreedor]=piv[r.acreedor]||{})[r.concepto]={mef:r.mef,bce:r.bce,dif:r.diferencia,estado:r.estado};});
  const estado={};TOTALES.forEach(t=>estado[t.acreedor]=t.estado);
  const orden=TOTALES.map(t=>t.acreedor);
  let h=`<thead><tr><th class="acr" rowspan="2">Acreedor</th><th class="est" rowspan="2">Estado</th>`;
  CONCEPTOS.forEach(c=>h+=`<th colspan="3" class="sep">${c}</th>`);
  h+=`</tr><tr class="sub">`;
  CONCEPTOS.forEach(c=>{h+=`<th class="sep">MEF<br>Col ${COLMAP[c][0]}</th><th>BCE<br>Col ${COLMAP[c][1]}</th><th>&Delta;</th>`;});
  h+=`</tr></thead><tbody>`;
  orden.forEach(ac=>{
    const ok=estado[ac]==="CONCILIADO";
    h+=`<tr class="${ok?'ok':'bad'}"><td class="acr">${CARTERA_TXT[ac]||ac}</td>
        <td class="est"><span class="est-pill ${ok?'ok':'bad'}">${ok?'✔ OK':'⚠ DIF'}</span></td>`;
    CONCEPTOS.forEach(c=>{const d=piv[ac]&&piv[ac][c];
      if(!d){h+=`<td class="num sep muted">—</td><td class="num muted">—</td><td class="d ok">✓</td>`;}
      else{const z=Math.abs(d.dif)<0.005;
        h+=`<td class="num sep">${fmt(d.mef)}</td><td class="num">${fmt(d.bce)}</td>
            <td class="d ${z?'ok':''}">${z?'✓':'+'+fmt(Math.abs(d.dif))}</td>`;}});
    h+=`</tr>`;
  });
  // Gran total por rubro
  const tot={};CONCEPTOS.forEach(c=>tot[c]={mef:0,bce:0});
  DATA.forEach(r=>{tot[r.concepto].mef+=r.mef;tot[r.concepto].bce+=r.bce;});
  h+=`<tr class="tot"><td>TOTAL</td><td></td>`;
  CONCEPTOS.forEach(c=>{const m=tot[c].mef,b=tot[c].bce,d=Math.round((m-b)*100)/100;const z=Math.abs(d)<0.005;
    h+=`<td class="num sep">${fmt(m)}</td><td class="num">${fmt(b)}</td><td class="d ${z?'ok':''}">${z?'✓':'+'+fmt(Math.abs(d))}</td>`;});
  h+=`</tr></tbody>`;
  document.getElementById("mtable").innerHTML=h;
  const dif=RES.diferencias;
  document.getElementById("resSub").textContent=`Periodo ${INFO?INFO.texto:PERIODO} · ${RES.conciliados} de ${RES.total} conceptos conciliados · TOTAL MEF ${fmt(GTOT.mef)} vs BCE ${fmt(GTOT.bce)}`;
  document.getElementById("resAlert").innerHTML = dif===0
    ? `<div class="note-ok">✅ Todas las carteras concilian. Puedes generar el Quipux.</div>`
    : `<div class="note-warn">⚠ ${dif} concepto(s) sin conciliar. Revisa "Ajuste - Observaciones" antes de generar el Quipux.</div>`;
}

// ---- Ajustes / Observaciones (solo no conciliado) ----
function pintarAjustes(){
  const cont=document.getElementById("ajContenido");
  if(RES && RES.conciliado_total){
    cont.innerHTML=`<div class="note-ok">✅ No existen observaciones. El MEF remitio la informacion completamente conciliada; no se requieren ajustes.</div>`;
    return;
  }
  const cls=t=>t==="Pago Directo"?"pd":t==="Diferencial Cambiario"?"dc":"ot";
  let h=`<div class="card">
    <h3 class="sec" style="font-size:15px">&#128228; Cargar respaldo de pago directo (MEF)</h3>
    <p class="sub" style="margin:6px 0 12px">Sube el respaldo que el MEF envia por Quipux (ej. pagos_directos_ibrd_9722.xls). Se agrega al reporte BCE y podras descargarlo.</p>
    <div class="drop" id="dzPd" ondrop="onDropPd(event)" ondragover="onOver(event)" ondragleave="onLeave(event)">
      <div class="ic">&#128196;</div><div class="ti">Respaldo(s) de pago directo</div><div class="dz">&#128229; Arrastra aqui o haz clic</div>
      <div class="btn-sel"><button class="lnk" onclick="document.getElementById('filePd').click()">&#128193; Seleccionar archivo(s)</button></div>
      <input type="file" id="filePd" accept=".xls,.xlsx" multiple onchange="subirPd(this.files)"></div>
    <div id="pdPrev"></div>
    <div id="bceDl" style="margin-top:12px"></div>
  </div>`;
  h+=`<div class="diaghead">Diagnostico de carteras NO conciliadas<small>${DIAG.length} cartera(s) con diferencia &middot; explicacion segun Observaciones del MEF</small></div>`;
  if(!DIAG.length){ h+=`<div class="note-ok">No hay carteras con diferencia.</div>`; }
  else h+=DIAG.map(d=>{const c=cls(d.tipo);
    const montos=Object.entries(d.rubros||{}).map(([k,v])=>`&Delta; ${k}: ${fmt(v)}`).join(" &middot; ");
    const obs=d.observacion?`<div class="dobs">&#128221; <b>Observacion MEF:</b> ${d.observacion}</div>`:`<div class="dobs muted">Sin observacion del MEF para esta cartera.</div>`;
    return `<div class="dcard ${c}"><div class="dh"><span class="dname">${CARTERA_TXT[d.acreedor]||d.acreedor}</span>
      <span class="dtag ${c}">${d.tipo}</span><span class="dmonto">${montos}</span></div>${obs}
      <div class="dgrid"><div class="dbox why"><div class="bt">&#191;Por que no concilia?</div><div class="bd">${d.por_que}</div></div>
      <div class="dbox act"><div class="bt">&#9989; Accion</div><div class="bd">${d.accion}</div></div></div></div>`;
  }).join("");
  cont.innerHTML=h;
  if(RES && RES.bce_ajustado) mostrarDescargaBce();
}
function onDropPd(e){e.preventDefault();e.currentTarget.classList.remove("over");if(e.dataTransfer.files.length)subirPd(e.dataTransfer.files);}
async function subirPd(files){
  if(!PERIODO){alert("Primero ejecuta la conciliacion.");return;}
  const fd=new FormData();[...files].forEach(f=>fd.append("archivos",f));
  const prev=document.getElementById("pdPrev");prev.innerHTML="<p class='muted'>Leyendo respaldo...</p>";
  const j=await (await fetch("/api/pago_directo",{method:"POST",body:fd})).json();
  if(!j.ok){prev.innerHTML="<p style='color:#dc2626'>Error</p>";return;}
  prev.innerHTML=j.previos.map((p,i)=>{
    if(p.error)return `<div class="dcard"><b>${p.archivo}</b>: <span style="color:#dc2626">${p.error}</span></div>`;
    const det=(p.detalle||[]).map(d=>`<div class="muted" style="font-size:11.5px">&middot; ${d.beneficiario}: ${fmt(d.monto)}</div>`).join("");
    return `<div class="dcard pd" id="pd${i}"><div class="dh"><span class="dname">${p.acreedor||'?'}</span><span class="dtag pd">Pago Directo</span><span class="dmonto" style="color:var(--navy)">${p.prestamo||''}</span></div>${det}
      <div class="frow" style="margin-top:10px">
        <div class="fld"><label>Cartera</label><input type="text" id="pdac${i}" value="${p.acreedor||''}" style="width:120px"></div>
        <div class="fld"><label>Referencia</label><input type="text" id="pdref${i}" value="${p.referencia||''}" style="width:110px"></div>
        <div class="fld"><label>Valor USD</label><input type="number" id="pdval${i}" value="${p.valor||0}" step="0.01" style="width:150px"></div>
        <div class="fld" style="flex:1;min-width:240px"><label>Nota</label><input type="text" id="pdnota${i}" value="${(p.nota||'').replace(/"/g,'&quot;')}" style="width:100%"></div>
        <button class="btn" onclick="aplicarPd(${i})">&#10133; Agregar al BCE</button></div></div>`;
  }).join("");
}
async function aplicarPd(i){
  const acreedor=document.getElementById("pdac"+i).value.trim();
  const valor=parseFloat(document.getElementById("pdval"+i).value)||0;
  const nota=document.getElementById("pdnota"+i).value.trim();
  const referencia=document.getElementById("pdref"+i).value.trim();
  if(!acreedor||!valor){alert("Falta cartera o valor");return;}
  const j=await (await fetch("/api/aplicar_pago_directo",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({periodo:PERIODO,acreedor,concepto:"Desembolsos",valor,nota,referencia})})).json();
  if(!j.ok){alert(j.error||"Error");return;}
  aplicarResultado({...j,info_periodo:INFO});
  document.getElementById("pd"+i).style.opacity=".5";
  mostrarDescargaBce();
  alert("Pago directo agregado a "+acreedor+" y reflejado en el reporte BCE.");
}
function mostrarDescargaBce(){
  const el=document.getElementById("bceDl");if(!el)return;
  el.innerHTML=`<button class="btn alt" onclick="window.location='/api/descargar_bce_ajustado?periodo=${encodeURIComponent(PERIODO)}'">&#11015; Descargar Reporte BCE ajustado (.xlsx)</button>`;
}

// ---- Quipux (solo si todo concilia) ----
function pintarQuipux(){
  const cont=document.getElementById("quipuxContenido");
  if(!(RES&&RES.conciliado_total)){
    const pend=DIAG.map(d=>CARTERA_TXT[d.acreedor]||d.acreedor).join(", ")||(RES?RES.diferencias+" concepto(s)":"");
    cont.innerHTML=`<div class="note-warn"><b>No se puede generar el Quipux todavia.</b><br>La conciliacion no esta completa. Concilia primero las carteras pendientes${pend?": "+pend:""} en la pestaña "Ajuste - Observaciones".</div>`;
    return;
  }
  cont.innerHTML=`<div class="card">
    <div class="frow">
      <div class="fld"><label>Nro. Oficio BCE</label><input type="text" id="qOfBce" value="BCE-SSFI-${INFO?INFO.anio:''}-XXXX-OF" style="width:200px"></div>
      <div class="fld"><label>Responde al Oficio MEF</label><input type="text" id="qOfMef" value="MEF-DNS-${INFO?INFO.anio:''}-XXXX-O" style="width:200px"></div>
      <div class="fld"><label>Fecha Oficio MEF</label><input type="text" id="qFechaMef" value="" style="width:170px"></div>
    </div>
    <div class="frow" style="margin-top:12px">
      <div class="fld" style="flex:1;min-width:240px"><label>Destinatario</label><input type="text" id="qDest" value="Ana Maria Vallejo Cabezas - Directora Nacional de Seguimiento" style="width:100%"></div>
      <div class="fld" style="flex:1;min-width:240px"><label>Firmante (BCE)</label><input type="text" id="qFirma" value="Mgs. Luis Santiago Vargas Bautista - Subgerente de Servicios Financieros Internacionales" style="width:100%"></div>
    </div>
    <div style="margin-top:14px;display:flex;gap:10px;flex-wrap:wrap">
      <button class="btn" onclick="generarQuipux()">&#9881;&#65039; Generar texto</button>
      <button class="btn alt" onclick="copiarQuipux()">&#128203; Copiar</button>
      <button class="btn alt" onclick="descargarQuipux()">&#11015; Descargar .txt</button>
    </div>
    <textarea id="qText" style="margin-top:14px" placeholder="Pulsa 'Generar texto'."></textarea></div>`;
}
function unirRubros(a){if(!a.length)return"";if(a.length===1)return a[0];const u=a[a.length-1];const c=/^h?i/i.test(u)?"e":"y";return a.slice(0,-1).join(", ")+" "+c+" "+u;}
function lineaCartera(cart){const rows=DATA.filter(r=>r.acreedor===cart);const nom=CARTERA_TXT[cart]||cart;
  if(!rows.length)return `${nom}: No existen movimientos.`;
  const con=rows.filter(r=>r.estado==="CONCILIADO").map(r=>RUBRO_TXT[r.concepto]||r.concepto.toLowerCase());
  return `${nom}: No existen observaciones en los rubros de ${unirRubros(con)}.`;}
function generarQuipux(){
  if(!(RES&&RES.conciliado_total)){alert("La conciliacion no esta completa.");return;}
  const ofBce=qv("qOfBce"),ofMef=qv("qOfMef"),fMef=qv("qFechaMef"),dest=qv("qDest"),firma=qv("qFirma");
  const present=new Set(DATA.map(r=>r.acreedor));
  const orden=ORDEN_QUIPUX.concat([...present].filter(a=>!ORDEN_QUIPUX.includes(a)));
  const lineas=orden.filter(c=>present.has(c)||["AIIB","GPS"].includes(c)).map(lineaCartera);
  const hoy=new Date().toLocaleDateString("es-EC",{day:"numeric",month:"long",year:"numeric"});
  const txt=`Oficio Nro. ${ofBce}
Quito, D.M., ${hoy}

Asunto: Conciliacion mensual de valores del Servicio de la Deuda a ${INFO?INFO.texto:PERIODO}

Senora Magister
${dest}
MINISTERIO DE ECONOMIA Y FINANZAS
En su Despacho

De mi consideracion:

Me refiero al Oficio Nro. ${ofMef}${fMef?` de ${fMef}`:""}, mediante el cual su Despacho solicito la ratificacion o rectificacion de la informacion sobre los movimientos de la deuda externa publica correspondiente al mes de ${(INFO?INFO.mes:"").toLowerCase()} de ${INFO?INFO.anio:""}.

Al respecto, me permito indicar lo siguiente:

${lineas.join("\n")}

Por lo expuesto, me permito remitir para su revision la matriz de conciliacion del Banco Central del Ecuador en formato Excel. Este archivo contiene la informacion completa y necesaria para el proceso de conciliacion.

Con sentimientos de distinguida consideracion.

Atentamente,

Documento firmado electronicamente
${firma}`;
  document.getElementById("qText").value=txt;
}
function qv(id){const el=document.getElementById(id);return el?el.value.trim():"";}
function copiarQuipux(){const t=document.getElementById("qText");if(!t.value){alert("Genera el texto primero.");return;}t.select();document.execCommand("copy");alert("Texto copiado.");}
function descargarQuipux(){const t=document.getElementById("qText").value;if(!t){alert("Genera el texto primero.");return;}
  const b=new Blob([t],{type:"text/plain;charset=utf-8"}),u=URL.createObjectURL(b),a=document.createElement("a");a.href=u;a.download="Quipux_Respuesta_"+PERIODO+".txt";a.click();URL.revokeObjectURL(u);}

function irA(v){
  if(v==="quipux" && !(RES&&RES.conciliado_total)){ /* permitido: muestra el bloqueo */ }
  document.querySelectorAll(".nav .it").forEach(it=>it.classList.toggle("act",it.dataset.v===v));
  ["cargar","resultados","ajustes","quipux"].forEach(s=>document.getElementById("v-"+s).classList.toggle("hidden",s!==v));
}
document.getElementById("nav").addEventListener("click",e=>{const it=e.target.closest(".it");if(it&&!it.classList.contains("dis"))irA(it.dataset.v);});
</script>
</body></html>"""


@app.route("/")
def home():
    return render_template_string(PANEL_HTML)


def abrir_navegador():
    webbrowser.open_new("http://127.0.0.1:5001/")


if __name__ == "__main__":
    init_db()
    print("=" * 60)
    print("  PANEL CONCILIACIÓN BCE vs MEF")
    print("=" * 60)
    print(f"  BD:  {DB_PATH}")
    print(f"  URL: http://127.0.0.1:5001/")
    print("=" * 60)
    Timer(1.5, abrir_navegador).start()
    app.run(host="127.0.0.1", port=5001, debug=False)
