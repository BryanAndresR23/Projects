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

    # Agregar la fila al MISMO archivo .xls del BCE que el usuario subió
    info = PERIODO_FILES.setdefault(periodo, {"bce": None, "mef": None, "ajustes": []})
    ajuste = {"acreedor": acreedor, "referencia": data.get("referencia", ""),
              "valor": valor, "nota": nota,
              "prestamista": data.get("prestamista", acreedor)}
    info["ajustes"].append(ajuste)
    if info.get("bce") and os.path.exists(info["bce"]):
        try:
            C.modificar_bce_xls(info["bce"], [ajuste])  # sobrescribe el mismo archivo
        except Exception:
            pass

    return jsonify(_resultado_periodo(periodo))


@app.route("/api/descargar_bce_ajustado")
def api_descargar_bce_ajustado():
    """Descarga el MISMO archivo del BCE ya modificado con los pagos directos."""
    periodo = (request.args.get("periodo") or "").strip()
    info = PERIODO_FILES.get(periodo) or {}
    ruta = info.get("bce")
    if not ruta or not os.path.exists(ruta):
        return jsonify({"ok": False, "error": "No hay archivo BCE para este periodo"})
    return send_file(ruta, as_attachment=True, download_name=os.path.basename(ruta))


# =============================================================================
# FRONT-END (una sola página, estilo BCE oscuro)
# =============================================================================
PANEL_HTML = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sistema de Conciliacion de Deuda Externa Publica</title>
<style>
  :root{--bg:#0a0f1f;--bg2:#0e1530;--panel:#121b36cc;--panel2:#0f1730;--line:#24314f;
        --txt:#e9eefb;--muted:#94a3c4;--gold:#d9b572;--gold2:#f0d49a;--cyan:#5ad1e6;
        --ok:#34d399;--okbg:#0e2b22;--bad:#f87171;--badbg:#2c1620;--blue:#3b82f6;}
  *{box-sizing:border-box}html,body{margin:0;height:100%}
  body{display:flex;min-height:100vh;color:var(--txt);font-family:"Segoe UI",system-ui,sans-serif;font-size:14px;
       background:radial-gradient(1200px 700px at 0% -10%,#16224a 0%,transparent 55%),
                  radial-gradient(1000px 600px at 100% 0%,#0c2a3a 0%,transparent 50%),
                  linear-gradient(180deg,var(--bg),var(--bg2));}
  .side{width:266px;flex:none;background:linear-gradient(180deg,#0c1430,#0a0f22);border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:100vh}
  .brand{padding:24px 22px 20px;border-bottom:1px solid var(--line);text-align:center}
  .brand .crest{width:50px;height:50px;margin:0 auto 10px;border-radius:14px;display:grid;place-items:center;font-size:26px;
        background:linear-gradient(135deg,#1a2murl);background:linear-gradient(135deg,#26345e,#16203f);border:1px solid var(--gold);box-shadow:0 6px 20px #0006}
  .brand h2{margin:0;font-size:14px;letter-spacing:1.5px;color:var(--gold2);font-weight:700}
  .brand .ln{height:2px;width:46px;margin:8px auto;background:linear-gradient(90deg,transparent,var(--gold),transparent)}
  .brand small{color:var(--muted);font-size:11px;letter-spacing:.3px}
  .nav{padding:16px 12px;flex:1}
  .nav .it{display:flex;gap:13px;align-items:center;padding:13px 13px;border-radius:12px;cursor:pointer;color:#b9c4e0;margin-bottom:6px;transition:.15s;border:1px solid transparent}
  .nav .it:hover{background:#ffffff0a}
  .nav .it.act{background:linear-gradient(90deg,#1b2banchor,#16203f00);background:#16203f;border-color:var(--line);color:#fff}
  .nav .it.act{box-shadow:inset 3px 0 0 var(--gold)}
  .nav .it.dis{opacity:.4;cursor:not-allowed}
  .nav .it .num{width:30px;height:30px;border-radius:9px;flex:none;display:grid;place-items:center;font-size:13px;font-weight:800;background:#1c2950;color:var(--gold2);border:1px solid var(--line)}
  .nav .it.act .num{background:var(--gold);color:#10182f;border-color:transparent}
  .nav .it .tt{font-weight:600;font-size:13.5px}.nav .it .ss{font-size:10.5px;color:var(--muted)}
  .perbox{margin:8px 16px;padding:11px;border:1px solid var(--gold);border-radius:11px;text-align:center;color:var(--gold2);font-weight:700;font-size:13px;background:#1a223f;display:none}
  .estado{padding:16px 20px;border-top:1px solid var(--line);font-size:12px}
  .estado .h{color:var(--gold2);text-transform:uppercase;letter-spacing:1px;margin-bottom:9px;font-size:10px}
  .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px}.dot.ok{background:var(--ok);box-shadow:0 0 6px var(--ok)}.dot.bad{background:var(--bad);box-shadow:0 0 6px var(--bad)}
  .estado .e{margin-bottom:6px;color:#c3cde6}
  .main{flex:1;min-width:0;display:flex;flex-direction:column}
  .top{padding:18px 30px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);background:#0b122699}
  .top h1{margin:0;font-size:16px;font-weight:600;letter-spacing:.3px}.top h1 b{color:var(--gold2)}
  .top .right{display:flex;gap:14px;align-items:center}
  .badge{padding:6px 14px;border-radius:999px;font-size:12px;font-weight:700;border:1px solid var(--line);color:var(--muted)}
  .badge.set{border-color:var(--gold);color:var(--gold2);background:#1a223f}.badge.ok{border-color:var(--ok);color:var(--ok);background:var(--okbg)}
  .uchip{display:flex;gap:9px;align-items:center;color:var(--muted)}.uav{width:32px;height:32px;border-radius:50%;background:linear-gradient(135deg,var(--gold),#b8924a);color:#10182f;display:grid;place-items:center;font-weight:800}
  .content{padding:28px 30px;overflow:auto}
  h3.sec{display:flex;align-items:center;gap:11px;margin:0 0 5px;font-size:19px;font-weight:600}
  h3.sec .bar{width:5px;height:22px;border-radius:3px;background:linear-gradient(180deg,var(--gold),#9c7b3e)}
  .sub{color:var(--muted);margin:0 0 22px;font-size:13px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:24px;margin-bottom:20px;backdrop-filter:blur(8px);box-shadow:0 12px 30px #00000035}
  .drop{border:2px dashed #35466e;border-radius:16px;padding:46px 22px;text-align:center;cursor:pointer;transition:.18s;background:#0e1730}
  .drop:hover{border-color:var(--gold);background:#121c3a}.drop.over{border-color:var(--cyan);background:#10243a}
  .drop.set{border-style:solid;border-color:var(--ok)}
  .drop .ic{font-size:42px;margin-bottom:6px}.drop .ti{font-size:17px;font-weight:600;color:var(--gold2)}.drop .de{color:var(--muted);font-size:13px;margin-top:6px}
  .drop .fl{margin-top:14px;display:flex;flex-direction:column;gap:6px;align-items:center}
  .chipf{background:#16203f;border:1px solid var(--line);border-radius:999px;padding:6px 14px;font-size:12.5px;color:#cdd8f0}
  .chipf.mef{border-color:#5aa9e6}.chipf.bce{border-color:var(--gold)}
  input[type=file]{display:none}
  .footbar{display:flex;align-items:center;justify-content:space-between;gap:16px}
  .btn{background:linear-gradient(135deg,var(--gold),#c69a4f);color:#10182f;border:0;border-radius:11px;padding:13px 26px;font-size:14px;font-weight:800;cursor:pointer;box-shadow:0 8px 22px #d9b57240;letter-spacing:.3px}
  .btn:hover{filter:brightness(1.07)}.btn:disabled{opacity:.4;cursor:not-allowed;box-shadow:none}
  .btn.alt{background:transparent;color:var(--gold2);border:1px solid var(--gold)}
  .btn.gh{background:transparent;color:var(--txt);border:1px solid var(--line)}
  .info{border-left:3px solid var(--gold);background:#121c3a;border-radius:10px;padding:12px 16px;margin-bottom:20px;font-size:12.5px;color:#c7d2ea}
  .frow{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-end}.fld label{display:block;font-size:11px;color:var(--muted);font-weight:700;margin-bottom:6px;text-transform:uppercase;letter-spacing:.4px}
  input[type=text],input[type=number]{background:#0c1428;border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:10px 12px;font-size:14px}
  /* Matriz */
  .scrollx{overflow:auto;border:1px solid var(--line);border-radius:14px;max-height:620px;background:#0c1326}
  .mtable{width:100%;border-collapse:separate;border-spacing:0;font-size:12.5px;min-width:980px}
  .mtable th,.mtable td{padding:11px 12px;white-space:nowrap;border-bottom:1px solid #1c2746}
  .mtable thead th{position:sticky;top:0;background:#101a36;color:var(--gold2);text-align:center;font-size:11px;text-transform:uppercase;letter-spacing:.6px;z-index:2}
  .mtable thead tr.sub th{top:38px;background:#0e1730;color:#9fb0d6;font-size:10px;font-weight:600;letter-spacing:.3px}
  .mtable thead th.acr,.mtable thead th.est{left:0;text-align:left;vertical-align:middle;background:#101a36}
  .mtable th.acr{left:0;z-index:3}
  .mtable td.acr{font-weight:700;color:#fff;position:sticky;left:0;background:#0e1730;z-index:1}
  .mtable td.num{text-align:right;font-variant-numeric:tabular-nums;color:#dbe3f5}
  .mtable td.d{text-align:right;font-weight:700;color:var(--bad)}.mtable td.d.ok{color:var(--ok)}
  .mtable td.sep{border-left:1px solid #1c2746}
  .mtable tbody tr:hover td{background:#13203f}
  .mtable tbody tr:hover td.acr{background:#16213f}
  .mtable tr.bad td.acr{box-shadow:inset 3px 0 0 var(--bad)}.mtable tr.ok td.acr{box-shadow:inset 3px 0 0 var(--ok)}
  .mtable tr.tot td{background:#101a36;color:var(--gold2);font-weight:800;border-top:2px solid var(--gold);position:sticky;bottom:0}
  .mtable tr.tot td.acr{background:#101a36}.mtable tr.tot td.d{color:#ffd9d9}.mtable tr.tot td.d.ok{color:var(--ok)}
  .est-pill{padding:4px 11px;border-radius:999px;font-size:10.5px;font-weight:800;letter-spacing:.3px}
  .est-pill.ok{background:var(--okbg);color:var(--ok);border:1px solid #1c5b44}.est-pill.bad{background:var(--badbg);color:var(--bad);border:1px solid #5b2230}
  .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}@media(max-width:860px){.stats{grid-template-columns:repeat(2,1fr)}}
  .stat{background:#101a36;border:1px solid var(--line);border-radius:13px;padding:15px 17px}.stat .n{font-size:24px;font-weight:800}.stat .l{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
  .stat.ok .n{color:var(--ok)}.stat.bad .n{color:var(--bad)}.stat.gold .n{color:var(--gold2)}
  .note-ok{background:linear-gradient(135deg,#0e2b22,#10243a);border:1px solid #1c5b44;color:#8ef0c4;border-radius:14px;padding:24px;text-align:center;font-size:15px;font-weight:600}
  .note-warn{background:#241a10;border:1px solid #6b4e1f;color:#f0cd8f;border-radius:14px;padding:18px;font-size:13.5px}
  .hidden{display:none}#msg{font-size:13px;margin-top:10px}
  .diaghead{background:#101a36;border:1px solid var(--line);border-radius:13px;padding:14px 17px;margin-bottom:14px;font-weight:700;color:var(--gold2)}
  .diaghead small{display:block;color:var(--muted);font-weight:400;font-size:11.5px;margin-top:2px}
  .dcard{background:#101a36;border:1px solid var(--line);border-radius:13px;padding:16px;margin-bottom:12px}
  .dcard.pd{border-color:#6b4e1f}.dcard.dc{border-color:#28507a}
  .dcard .dh{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:9px}.dcard .dname{font-weight:800;color:#fff}
  .dtag{padding:3px 10px;border-radius:999px;font-size:10.5px;font-weight:700}.dtag.pd{background:#3a2c10;color:#f0cd8f}.dtag.dc{background:#10283f;color:#7fc2ec}.dtag.ot{background:#1c2746;color:#a7b4d4}
  .dmonto{margin-left:auto;color:var(--bad);font-weight:800;font-size:12.5px}
  .dobs{background:#0c1428;border:1px solid var(--line);border-radius:9px;padding:9px 12px;font-size:12px;margin-bottom:10px;color:#c7d2ea}
  .dgrid{display:grid;grid-template-columns:1fr 1fr;gap:11px}@media(max-width:760px){.dgrid{grid-template-columns:1fr}}
  .dbox{background:#0c1428;border:1px solid var(--line);border-radius:9px;padding:10px 12px}.dbox .bt{font-size:10px;font-weight:800;text-transform:uppercase;margin-bottom:4px;letter-spacing:.4px}.dbox.why .bt{color:#f0cd8f}.dbox.act .bt{color:var(--ok)}.dbox .bd{font-size:12px;color:#c7d2ea}
  textarea{width:100%;height:440px;background:#0c1428;border:1px solid var(--line);border-radius:11px;padding:14px;font-family:Consolas,monospace;font-size:12.5px;color:#dbe3f5}
  .muted{color:var(--muted)}
</style></head>
<body>
  <aside class="side">
    <div class="brand"><div class="crest">&#127963;&#65039;</div>
      <h2>CONCILIACION DE DEUDA</h2><div class="ln"></div>
      <small>Banco Central del Ecuador<br>Servicios Financieros Internacionales</small></div>
    <nav class="nav" id="nav">
      <div class="it act" data-v="cargar"><div class="num">I</div><div><div class="tt">Recepcion de Reportes</div><div class="ss">MEF &middot; BCE</div></div></div>
      <div class="it" data-v="resultados"><div class="num">II</div><div><div class="tt">Conciliacion de Carteras</div><div class="ss">Cruce y resultados</div></div></div>
      <div class="it" data-v="ajustes"><div class="num">III</div><div><div class="tt">Gestion de Observaciones</div><div class="ss">Ajustes y pagos directos</div></div></div>
      <div class="it" data-v="quipux"><div class="num">IV</div><div><div class="tt">Emision del Oficio</div><div class="ss">Quipux de respuesta</div></div></div>
    </nav>
    <div class="perbox" id="perBox"></div>
    <div class="estado"><div class="h">Estado de carteras</div><div id="estadoSide" style="color:var(--muted)">Sin datos</div></div>
  </aside>
  <div class="main">
    <div class="top"><h1>Sistema de Conciliacion de <b>Deuda Externa Publica</b></h1>
      <div class="right"><span class="badge" id="periodoBadge">Periodo no detectado</span>
        <span class="uchip"><span class="uav">U</span>Usuario</span></div></div>
    <div class="content">

      <section id="v-cargar">
        <h3 class="sec"><span class="bar"></span>Recepcion de Reportes</h3>
        <p class="sub">Cargue los reportes de conciliacion del MEF y del BCE en una sola zona. El periodo se reconoce automaticamente y los calculos se realizan sin depender del orden de columnas o filas.</p>
        <div class="card">
          <div class="drop" id="dz" onclick="document.getElementById('files').click()" ondrop="onDrop(event)" ondragover="onOver(event)" ondragleave="onLeave(event)">
            <div class="ic">&#128228;</div>
            <div class="ti">Arrastre aqui los dos reportes &mdash; MEF y BCE</div>
            <div class="de">o haga clic para seleccionarlos &middot; no importa el orden, el sistema reconoce cada uno &middot; .xls .xlsx</div>
            <div class="fl" id="fnList"></div>
            <input type="file" id="files" accept=".xls,.xlsx" multiple onchange="recibir(this.files)">
          </div>
        </div>
        <div class="card footbar"><span class="muted" id="cargarMsg">Cargue ambos reportes para continuar.</span>
          <button class="btn" id="btnRun" onclick="ejecutar()" disabled>Ejecutar Conciliacion &rarr;</button></div>
        <div id="msg"></div>
      </section>

      <section id="v-resultados" class="hidden">
        <h3 class="sec"><span class="bar"></span>Conciliacion de Carteras</h3>
        <p class="sub" id="resSub">&mdash;</p>
        <div id="resAlert"></div>
        <div class="card"><div class="stats" id="stats"></div>
          <div class="scrollx"><table class="mtable" id="mtable"></table></div></div>
      </section>

      <section id="v-ajustes" class="hidden">
        <h3 class="sec"><span class="bar"></span>Gestion de Observaciones</h3>
        <p class="sub">Se habilita unicamente cuando una o mas carteras no concilian.</p>
        <div id="ajContenido"></div>
      </section>

      <section id="v-quipux" class="hidden">
        <h3 class="sec"><span class="bar"></span>Emision del Oficio de Respuesta</h3>
        <div id="quipuxContenido"></div>
      </section>
    </div>
  </div>
<script>
const CONCEPTOS=["Desembolsos","Amortizaciones","Intereses","Comisiones","Intereses Condonados","Interés por Mora"];
const RUBRO_TXT={"Desembolsos":"desembolsos","Amortizaciones":"amortizaciones","Intereses":"intereses","Comisiones":"comisiones","Intereses Condonados":"condonados","Interés por Mora":"intereses por mora"};
const CARTERA_TXT={"AMAZON DAC":"AMAZON"};
const ORDEN_QUIPUX=["AIIB","AMAZON DAC","BANCOS","BID","BIRF","BONOS","CAF","FIDA","FLAR","FMI","GOBIERNOS","GPS"];
let FILES=[], DATA=[], TOTALES=[], GTOT=null, DIAG=[], INFO=null, RES=null, PERIODO="";
const fmt=n=>(n||0).toLocaleString("es-EC",{minimumFractionDigits:2,maximumFractionDigits:2});
function msg(t,err){const m=document.getElementById("msg");m.textContent=t;m.style.color=err?"#f87171":"#34d399";}
const valido=f=>f&&/\.(xls|xlsx)$/i.test(f.name);
function recibir(files){[...files].forEach(f=>{if(valido(f)&&FILES.length<2&&!FILES.some(x=>x.name===f.name))FILES.push(f);});pintarFiles();}
function pintarFiles(){
  const c=document.getElementById("fnList");
  c.innerHTML=FILES.map((f,i)=>`<span class="chipf">&#128196; ${f.name} <a onclick="quitar(${i});event.stopPropagation()" style="cursor:pointer;color:#f87171">&times;</a></span>`).join("");
  document.getElementById("dz").classList.toggle("set",FILES.length>=2);
  document.getElementById("btnRun").disabled=FILES.length<2;
  document.getElementById("cargarMsg").textContent=FILES.length>=2?"Listo para conciliar.":"Cargue ambos reportes para continuar.";
}
function quitar(i){FILES.splice(i,1);pintarFiles();}
function onOver(e){e.preventDefault();e.currentTarget.classList.add("over");}
function onLeave(e){e.currentTarget.classList.remove("over");}
function onDrop(e){e.preventDefault();e.currentTarget.classList.remove("over");recibir(e.dataTransfer.files);}

async function ejecutar(){
  if(FILES.length<2)return;
  const fd=new FormData();FILES.forEach(f=>fd.append("archivos",f));
  const btn=document.getElementById("btnRun");btn.disabled=true;msg("Procesando...");
  try{const j=await (await fetch("/api/conciliar",{method:"POST",body:fd})).json();
    if(!j.ok){msg(j.error||"Error",true);btn.disabled=false;return;}
    aplicarResultado(j);msg("Conciliacion ejecutada para "+(INFO?INFO.texto:PERIODO)+".");irA("resultados");
  }catch(e){msg(e.message,true);}btn.disabled=false;
}
function aplicarResultado(j){
  RES=j;DATA=j.registros;TOTALES=j.totales||[];GTOT=j.gran_total;DIAG=j.diagnostico||[];INFO=j.info_periodo||INFO;PERIODO=j.periodo;
  const pb=document.getElementById("periodoBadge");pb.textContent=INFO?INFO.texto:PERIODO;pb.className="badge "+(j.conciliado_total?"ok":"set");
  const px=document.getElementById("perBox");px.style.display="block";px.textContent=INFO?INFO.texto:PERIODO;
  pintarSidebar();pintarMatriz();pintarAjustes();pintarQuipux();
  document.querySelector('.nav .it[data-v="quipux"]').classList.toggle("dis",!(RES&&RES.conciliado_total));
}
function pintarSidebar(){const es=document.getElementById("estadoSide");
  es.innerHTML=TOTALES.length?TOTALES.map(t=>`<div class="e"><span class="dot ${t.estado==='CONCILIADO'?'ok':'bad'}"></span>${CARTERA_TXT[t.acreedor]||t.acreedor}</div>`).join(""):"Sin datos";}
function pintarMatriz(){
  const piv={};DATA.forEach(r=>{(piv[r.acreedor]=piv[r.acreedor]||{})[r.concepto]={mef:r.mef,bce:r.bce,dif:r.diferencia};});
  const estado={};TOTALES.forEach(t=>estado[t.acreedor]=t.estado);
  let h=`<thead><tr><th class="acr" rowspan="2">Acreedor</th><th class="est" rowspan="2">Estado</th>`;
  CONCEPTOS.forEach(c=>h+=`<th colspan="3" class="sep">${c}</th>`);
  h+=`</tr><tr class="sub">`;CONCEPTOS.forEach(()=>h+=`<th class="sep">MEF</th><th>BCE</th><th>&Delta;</th>`);
  h+=`</tr></thead><tbody>`;
  TOTALES.map(t=>t.acreedor).forEach(ac=>{const ok=estado[ac]==="CONCILIADO";
    h+=`<tr class="${ok?'ok':'bad'}"><td class="acr">${CARTERA_TXT[ac]||ac}</td><td><span class="est-pill ${ok?'ok':'bad'}">${ok?'CONCILIADO':'DIFERENCIA'}</span></td>`;
    CONCEPTOS.forEach(c=>{const d=piv[ac]&&piv[ac][c];
      if(!d){h+=`<td class="num sep muted">&mdash;</td><td class="num muted">&mdash;</td><td class="d ok">&#10003;</td>`;}
      else{const z=Math.abs(d.dif)<0.005;h+=`<td class="num sep">${fmt(d.mef)}</td><td class="num">${fmt(d.bce)}</td><td class="d ${z?'ok':''}">${z?'&#10003;':'+'+fmt(Math.abs(d.dif))}</td>`;}});
    h+=`</tr>`;});
  const tot={};CONCEPTOS.forEach(c=>tot[c]={mef:0,bce:0});DATA.forEach(r=>{tot[r.concepto].mef+=r.mef;tot[r.concepto].bce+=r.bce;});
  h+=`<tr class="tot"><td class="acr">TOTAL GENERAL</td><td></td>`;
  CONCEPTOS.forEach(c=>{const m=tot[c].mef,b=tot[c].bce,d=Math.round((m-b)*100)/100,z=Math.abs(d)<0.005;
    h+=`<td class="num sep">${fmt(m)}</td><td class="num">${fmt(b)}</td><td class="d ${z?'ok':''}">${z?'&#10003;':'+'+fmt(Math.abs(d))}</td>`;});
  h+=`</tr></tbody>`;document.getElementById("mtable").innerHTML=h;
  const dif=RES.diferencias;
  document.getElementById("stats").innerHTML=
    `<div class="stat"><div class="n">${RES.total}</div><div class="l">Conceptos comparados</div></div>
     <div class="stat ok"><div class="n">${RES.conciliados}</div><div class="l">Conciliados</div></div>
     <div class="stat bad"><div class="n">${dif}</div><div class="l">Con diferencia</div></div>
     <div class="stat gold"><div class="n">${fmt(GTOT.mef)}</div><div class="l">Total general (USD)</div></div>`;
  document.getElementById("resSub").textContent=`Periodo ${INFO?INFO.texto:PERIODO} · ${RES.conciliados} de ${RES.total} conceptos conciliados`;
  document.getElementById("resAlert").innerHTML = dif===0
    ? `<div class="note-ok">&#10004; Conciliacion completa: todas las carteras cuadran al centavo. Puede emitir el oficio de respuesta.</div>`
    : `<div class="note-warn">&#9888; ${dif} concepto(s) sin conciliar. Gestione las observaciones antes de emitir el oficio.</div>`;
}
function pintarAjustes(){
  const cont=document.getElementById("ajContenido");
  if(RES&&RES.conciliado_total){cont.innerHTML=`<div class="note-ok">&#10004; No existen observaciones. El MEF remitio la informacion completamente conciliada; no se requieren ajustes.</div>`;return;}
  const cls=t=>t==="Pago Directo"?"pd":t==="Diferencial Cambiario"?"dc":"ot";
  let h=`<div class="card"><h3 class="sec" style="font-size:15px"><span class="bar"></span>Respaldo de pago directo (MEF)</h3>
    <p class="sub" style="margin:8px 0 14px">Cargue el respaldo que el MEF remite por Quipux. El valor se agrega en el mismo archivo del BCE y podra descargarlo modificado.</p>
    <div class="drop" id="dzPd" onclick="document.getElementById('filePd').click()" ondrop="onDropPd(event)" ondragover="onOver(event)" ondragleave="onLeave(event)">
      <div class="ic">&#128196;</div><div class="ti">Respaldo(s) de pago directo</div><div class="de">Arrastre aqui o haga clic</div>
      <input type="file" id="filePd" accept=".xls,.xlsx" multiple onchange="subirPd(this.files)"></div>
    <div id="pdPrev"></div><div id="bceDl" style="margin-top:12px"></div></div>`;
  h+=`<div class="diaghead">Carteras no conciliadas<small>${DIAG.length} cartera(s) con diferencia &middot; explicacion segun Observaciones del MEF</small></div>`;
  h+= DIAG.length ? DIAG.map(d=>{const c=cls(d.tipo);
    const m=Object.entries(d.rubros||{}).map(([k,v])=>`&Delta; ${k}: ${fmt(v)}`).join(" &middot; ");
    const obs=d.observacion?`<div class="dobs">&#128221; <b>Observacion MEF:</b> ${d.observacion}</div>`:`<div class="dobs muted">Sin observacion del MEF.</div>`;
    return `<div class="dcard ${c}"><div class="dh"><span class="dname">${CARTERA_TXT[d.acreedor]||d.acreedor}</span><span class="dtag ${c}">${d.tipo}</span><span class="dmonto">${m}</span></div>${obs}
      <div class="dgrid"><div class="dbox why"><div class="bt">Por que no concilia</div><div class="bd">${d.por_que}</div></div>
      <div class="dbox act"><div class="bt">Accion</div><div class="bd">${d.accion}</div></div></div></div>`;}).join("") : `<div class="note-ok">No hay carteras con diferencia.</div>`;
  cont.innerHTML=h;if(RES&&RES.bce_ajustado)mostrarDescargaBce();
}
function onDropPd(e){e.preventDefault();e.currentTarget.classList.remove("over");if(e.dataTransfer.files.length)subirPd(e.dataTransfer.files);}
async function subirPd(files){
  if(!PERIODO){alert("Ejecute primero la conciliacion.");return;}
  const fd=new FormData();[...files].forEach(f=>fd.append("archivos",f));
  const prev=document.getElementById("pdPrev");prev.innerHTML="<p class='muted'>Leyendo respaldo...</p>";
  const j=await (await fetch("/api/pago_directo",{method:"POST",body:fd})).json();
  if(!j.ok){prev.innerHTML="<p style='color:#f87171'>Error</p>";return;}
  prev.innerHTML=j.previos.map((p,i)=>{if(p.error)return `<div class="dcard"><b>${p.archivo}</b>: <span style="color:#f87171">${p.error}</span></div>`;
    const det=(p.detalle||[]).map(d=>`<div class="muted" style="font-size:11.5px">&middot; ${d.beneficiario}: ${fmt(d.monto)}</div>`).join("");
    return `<div class="dcard pd" id="pd${i}"><div class="dh"><span class="dname">${p.acreedor||'?'}</span><span class="dtag pd">Pago Directo</span><span class="dmonto" style="color:var(--gold2)">${p.prestamo||''}</span></div>${det}
      <div class="frow" style="margin-top:10px">
        <div class="fld"><label>Cartera</label><input type="text" id="pdac${i}" value="${p.acreedor||''}" style="width:120px"></div>
        <div class="fld"><label>Referencia</label><input type="text" id="pdref${i}" value="${p.referencia||''}" style="width:110px"></div>
        <div class="fld"><label>Valor USD</label><input type="number" id="pdval${i}" value="${p.valor||0}" step="0.01" style="width:150px"></div>
        <div class="fld" style="flex:1;min-width:230px"><label>Nota</label><input type="text" id="pdnota${i}" value="${(p.nota||'').replace(/"/g,'&quot;')}" style="width:100%"></div>
        <button class="btn" onclick="aplicarPd(${i})">Agregar al BCE</button></div></div>`;}).join("");
}
async function aplicarPd(i){
  const acreedor=document.getElementById("pdac"+i).value.trim(),valor=parseFloat(document.getElementById("pdval"+i).value)||0;
  const nota=document.getElementById("pdnota"+i).value.trim(),referencia=document.getElementById("pdref"+i).value.trim();
  if(!acreedor||!valor){alert("Falta cartera o valor");return;}
  const j=await (await fetch("/api/aplicar_pago_directo",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({periodo:PERIODO,acreedor,concepto:"Desembolsos",valor,nota,referencia})})).json();
  if(!j.ok){alert(j.error||"Error");return;}
  aplicarResultado({...j,info_periodo:INFO});mostrarDescargaBce();
  alert("Pago directo agregado a "+acreedor+" en el mismo archivo del BCE.");
}
function mostrarDescargaBce(){const el=document.getElementById("bceDl");if(!el)return;
  el.innerHTML=`<button class="btn alt" onclick="window.location='/api/descargar_bce_ajustado?periodo=${encodeURIComponent(PERIODO)}'">&#11015; Descargar reporte BCE modificado</button>`;}
function pintarQuipux(){
  const cont=document.getElementById("quipuxContenido");
  if(!(RES&&RES.conciliado_total)){const pend=DIAG.map(d=>CARTERA_TXT[d.acreedor]||d.acreedor).join(", ")||(RES?RES.diferencias+" concepto(s)":"");
    cont.innerHTML=`<div class="note-warn"><b>El oficio aun no puede emitirse.</b><br>La conciliacion no esta completa. Concilie primero las carteras pendientes${pend?": "+pend:""} en "Gestion de Observaciones".</div>`;return;}
  cont.innerHTML=`<div class="card">
    <div class="frow"><div class="fld"><label>Nro. Oficio BCE</label><input type="text" id="qOfBce" value="BCE-SSFI-${INFO?INFO.anio:''}-XXXX-OF" style="width:200px"></div>
      <div class="fld"><label>Responde Oficio MEF</label><input type="text" id="qOfMef" value="MEF-DNS-${INFO?INFO.anio:''}-XXXX-O" style="width:200px"></div>
      <div class="fld"><label>Fecha Oficio MEF</label><input type="text" id="qFechaMef" value="" style="width:170px"></div></div>
    <div class="frow" style="margin-top:12px"><div class="fld" style="flex:1;min-width:240px"><label>Destinatario</label><input type="text" id="qDest" value="Ana Maria Vallejo Cabezas - Directora Nacional de Seguimiento" style="width:100%"></div>
      <div class="fld" style="flex:1;min-width:240px"><label>Firmante (BCE)</label><input type="text" id="qFirma" value="Mgs. Luis Santiago Vargas Bautista - Subgerente de Servicios Financieros Internacionales" style="width:100%"></div></div>
    <div style="margin-top:14px;display:flex;gap:10px;flex-wrap:wrap"><button class="btn" onclick="generarQuipux()">Generar oficio</button>
      <button class="btn gh" onclick="copiarQuipux()">Copiar</button><button class="btn gh" onclick="descargarQuipux()">Descargar .txt</button></div>
    <textarea id="qText" style="margin-top:14px" placeholder="Pulse 'Generar oficio'."></textarea></div>`;
}
function unirRubros(a){if(!a.length)return"";if(a.length===1)return a[0];const u=a[a.length-1];const c=/^h?i/i.test(u)?"e":"y";return a.slice(0,-1).join(", ")+" "+c+" "+u;}
function lineaCartera(cart){const rows=DATA.filter(r=>r.acreedor===cart);const nom=CARTERA_TXT[cart]||cart;
  if(!rows.length)return `${nom}: No existen movimientos.`;
  const con=rows.filter(r=>r.estado==="CONCILIADO").map(r=>RUBRO_TXT[r.concepto]||r.concepto.toLowerCase());
  return `${nom}: No existen observaciones en los rubros de ${unirRubros(con)}.`;}
function qv(id){const el=document.getElementById(id);return el?el.value.trim():"";}
function generarQuipux(){if(!(RES&&RES.conciliado_total)){alert("La conciliacion no esta completa.");return;}
  const present=new Set(DATA.map(r=>r.acreedor));const orden=ORDEN_QUIPUX.concat([...present].filter(a=>!ORDEN_QUIPUX.includes(a)));
  const lineas=orden.filter(c=>present.has(c)||["AIIB","GPS"].includes(c)).map(lineaCartera);
  const hoy=new Date().toLocaleDateString("es-EC",{day:"numeric",month:"long",year:"numeric"});const fMef=qv("qFechaMef");
  const txt=`Oficio Nro. ${qv("qOfBce")}
Quito, D.M., ${hoy}

Asunto: Conciliacion mensual de valores del Servicio de la Deuda a ${INFO?INFO.texto:PERIODO}

Senora Magister
${qv("qDest")}
MINISTERIO DE ECONOMIA Y FINANZAS
En su Despacho

De mi consideracion:

Me refiero al Oficio Nro. ${qv("qOfMef")}${fMef?` de ${fMef}`:""}, mediante el cual su Despacho solicito la ratificacion o rectificacion de la informacion sobre los movimientos de la deuda externa publica correspondiente al mes de ${(INFO?INFO.mes:"").toLowerCase()} de ${INFO?INFO.anio:""}.

Al respecto, me permito indicar lo siguiente:

${lineas.join("\n")}

Por lo expuesto, me permito remitir para su revision la matriz de conciliacion del Banco Central del Ecuador en formato Excel. Este archivo contiene la informacion completa y necesaria para el proceso de conciliacion.

Con sentimientos de distinguida consideracion.

Atentamente,

Documento firmado electronicamente
${qv("qFirma")}`;
  document.getElementById("qText").value=txt;}
function copiarQuipux(){const t=document.getElementById("qText");if(!t.value){alert("Genere el oficio primero.");return;}t.select();document.execCommand("copy");alert("Texto copiado.");}
function descargarQuipux(){const t=document.getElementById("qText").value;if(!t){alert("Genere el oficio primero.");return;}
  const b=new Blob([t],{type:"text/plain;charset=utf-8"}),u=URL.createObjectURL(b),a=document.createElement("a");a.href=u;a.download="Oficio_Respuesta_"+PERIODO+".txt";a.click();URL.revokeObjectURL(u);}
function irA(v){document.querySelectorAll(".nav .it").forEach(it=>it.classList.toggle("act",it.dataset.v===v));
  ["cargar","resultados","ajustes","quipux"].forEach(s=>document.getElementById("v-"+s).classList.toggle("hidden",s!==v));}
document.getElementById("nav").addEventListener("click",e=>{const it=e.target.closest(".it");if(it&&!it.classList.contains("dis"))irA(it.dataset.v);});
</script></body></html>"""


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
