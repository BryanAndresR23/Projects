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
    if not periodo:
        periodo = datetime.now().strftime("%Y-%m")
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Reemplazar corridas previas del mismo periodo
    db_q("DELETE FROM conciliaciones WHERE periodo = ?", (periodo,))
    registros = []
    conciliados = diferencias = 0
    total_dif = 0.0
    for ac, concepto, vm, vb, dif, estado in filas:
        db_q("""INSERT INTO conciliaciones
                (periodo, acreedor, concepto, mef, bce, diferencia, estado, fecha_corrida)
                VALUES (?,?,?,?,?,?,?,?)""",
             (periodo, ac, concepto, vm, vb, dif, estado, ahora))
        registros.append({"acreedor": ac, "concepto": concepto, "mef": vm,
                          "bce": vb, "diferencia": dif, "estado": estado})
        if estado == "CONCILIADO":
            conciliados += 1
        else:
            diferencias += 1
            total_dif += abs(dif)

    # Totales por cartera (MEF vs BCE) y diagnóstico de observaciones del MEF
    totales = [{"acreedor": ac, **t} for ac, t in C.totales_por_cartera(filas).items()]
    try:
        diag = C.diagnostico(ruta_mef)
    except Exception:
        diag = []

    return jsonify({
        "ok": True, "periodo": periodo, "fecha": ahora,
        "registros": registros, "total": len(registros),
        "conciliados": conciliados, "diferencias": diferencias,
        "total_diferencia": round(total_dif, 2),
        "ruta_bce": ruta_bce, "ruta_mef": ruta_mef,
        "archivo_bce": os.path.basename(ruta_bce),
        "archivo_mef": os.path.basename(ruta_mef),
        "avisos": avisos, "totales": totales, "diagnostico": diag,
    })


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
    return {
        "ok": True, "periodo": periodo, "registros": rows,
        "total": len(rows), "conciliados": conciliados, "diferencias": diferencias,
        "total_diferencia": total_dif, "totales": totales,
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

    return jsonify(_resultado_periodo(periodo))


# =============================================================================
# FRONT-END (una sola página, estilo BCE oscuro)
# =============================================================================
PANEL_HTML = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Conciliacion Deuda Externa Publica</title>
<style>
  :root{
    --navy:#16315a; --blue:#2563eb; --blue2:#1d4ed8;
    --bg:#eef1f6; --card:#fff; --line:#e3e8f0; --txt:#1f2a3d; --muted:#64748b;
    --ok:#16a34a; --okbg:#e9f8ef; --bad:#dc2626; --badbg:#fdeaea; --gold:#d97706; --yellow:#ffd100;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{display:flex;background:var(--bg);color:var(--txt);font-family:"Segoe UI",system-ui,sans-serif;font-size:14px}
  .side{width:248px;flex:none;background:linear-gradient(180deg,var(--navy),#102241);color:#dce5f5;display:flex;flex-direction:column;min-height:100vh}
  .brand{padding:22px 20px 18px;border-bottom:1px solid #ffffff1a}
  .brand .flag{font-size:26px}
  .brand h2{margin:8px 0 2px;font-size:16px;letter-spacing:.5px;color:#fff}
  .brand small{color:#9fb3d4;font-size:11.5px}
  .nav{padding:14px 12px;flex:1}
  .nav .it{display:flex;gap:12px;align-items:center;padding:11px 12px;border-radius:10px;cursor:pointer;color:#c6d4ec;transition:.15s;margin-bottom:4px}
  .nav .it:hover{background:#ffffff12}
  .nav .it.act{background:#ffffff1a;color:#fff}
  .nav .it .num{width:26px;height:26px;border-radius:50%;flex:none;display:grid;place-items:center;font-size:12px;font-weight:800;background:#ffffff1f;color:#fff}
  .nav .it.act .num{background:var(--yellow);color:#16315a}
  .nav .it .tt{font-weight:600;font-size:13.5px}
  .nav .it .ss{font-size:11px;color:#9fb3d4}
  .side .estado{padding:16px 20px;border-top:1px solid #ffffff1a;font-size:11.5px}
  .side .estado .h{color:#9fb3d4;text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px}
  .main{flex:1;min-width:0;display:flex;flex-direction:column}
  .top{background:#fff;border-bottom:1px solid var(--line);padding:14px 28px;display:flex;align-items:center;justify-content:space-between}
  .top h1{margin:0;font-size:17px;color:var(--navy)}
  .top .right{display:flex;gap:12px;align-items:center}
  .badge{background:#dbe5fb;color:var(--blue2);padding:5px 12px;border-radius:999px;font-size:12px;font-weight:700}
  .badge.ok{background:var(--okbg);color:var(--ok)}
  .content{padding:26px 28px;overflow:auto}
  h3.sec{display:flex;align-items:center;gap:10px;color:var(--navy);margin:0 0 4px}
  .sub{color:var(--muted);margin:0 0 18px;font-size:13px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px;margin-bottom:20px;box-shadow:0 1px 3px #0f172a0d}
  .info{border-left:4px solid var(--blue);background:#f3f7ff;border-radius:8px;padding:12px 16px;margin-bottom:20px;font-size:13px}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
  @media(max-width:820px){.grid2{grid-template-columns:1fr}}
  .drop{border:2px dashed var(--line);border-radius:14px;padding:26px 18px;text-align:center;transition:.18s;background:#fafbfe}
  .drop.over{border-color:var(--blue);background:#eef4ff}
  .drop.set{border-color:var(--ok);background:var(--okbg)}
  .drop .ic{font-size:34px}
  .drop .ti{font-weight:700;font-size:16px;margin-top:8px;color:var(--navy)}
  .drop .de{color:var(--muted);font-size:12.5px;margin-top:2px}
  .drop .dz{color:var(--muted);font-size:12px;margin-top:10px}
  .drop .fn{margin-top:8px;font-size:12.5px;color:var(--ok);font-weight:600;word-break:break-all}
  .drop .btn-sel{margin-top:16px;border-top:1px solid var(--line);padding-top:14px}
  .lnk{color:var(--blue);font-weight:700;cursor:pointer;font-size:14px;background:none;border:0}
  .lnk:hover{text-decoration:underline}
  input[type=file]{display:none}
  .frow{display:flex;gap:18px;flex-wrap:wrap}
  .fld label{display:block;font-size:11.5px;color:var(--muted);font-weight:700;margin-bottom:6px}
  select,input[type=text],input[type=number]{border:1px solid var(--line);border-radius:9px;padding:9px 11px;font-size:14px;background:#fff;color:var(--txt)}
  .footbar{display:flex;align-items:center;justify-content:space-between;gap:16px}
  .btn{background:linear-gradient(135deg,var(--blue),var(--blue2));color:#fff;border:0;border-radius:10px;padding:12px 22px;font-size:14px;font-weight:800;cursor:pointer;box-shadow:0 6px 16px #2563eb40}
  .btn:hover{filter:brightness(1.07)} .btn:disabled{opacity:.5;cursor:not-allowed;box-shadow:none}
  .btn.alt{background:#fff;color:var(--blue);border:1px solid #c7d6f5;box-shadow:none}
  .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}
  @media(max-width:820px){.stats{grid-template-columns:repeat(2,1fr)}}
  .stat{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 16px}
  .stat .n{font-size:24px;font-weight:800} .stat .l{font-size:11.5px;color:var(--muted)}
  .stat.ok .n{color:var(--ok)} .stat.bad .n{color:var(--bad)} .stat.gold .n{color:var(--gold)}
  .donutwrap{display:flex;gap:22px;align-items:center;flex-wrap:wrap}
  .donut{width:140px;height:140px;border-radius:50%;display:grid;place-items:center;position:relative}
  .donut::after{content:"";position:absolute;inset:16px;border-radius:50%;background:#fff}
  .donut .ct{position:relative;text-align:center;z-index:1}
  .donut .pct{font-size:28px;font-weight:800} .donut .lb{font-size:10.5px;color:var(--muted);text-transform:uppercase}
  table{width:100%;border-collapse:collapse}
  th,td{padding:10px 12px;text-align:left;border-bottom:1px solid var(--line)}
  th{font-size:10.5px;text-transform:uppercase;color:var(--muted);letter-spacing:.4px;position:sticky;top:0;background:#f7f9fc}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  tbody tr:hover td{background:#f7f9fc}
  tr.grp td{background:#eef3fb;font-weight:800;color:var(--navy);text-transform:uppercase;font-size:12px}
  tr.dif td{background:#fdf3f3} tr.con td{background:#f3fbf6}
  .pill{padding:4px 10px;border-radius:999px;font-size:10.5px;font-weight:800}
  .pill.con{background:var(--okbg);color:var(--ok)} .pill.dif{background:var(--badbg);color:var(--bad)}
  .chip{padding:7px 13px;border-radius:999px;font-size:12.5px;cursor:pointer;border:1px solid var(--line);background:#fff;color:var(--muted)}
  .chip.act{background:var(--blue);color:#fff;border-color:transparent}
  .toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:6px 0 14px}
  .scroll{max-height:540px;overflow:auto;border:1px solid var(--line);border-radius:12px}
  .hidden{display:none}
  .hist a{display:block;padding:10px 12px;border:1px solid var(--line);border-radius:10px;margin-bottom:8px;cursor:pointer;color:var(--navy);text-decoration:none;background:#fff}
  .hist a:hover{background:#f3f7ff}
  .alert{border-radius:10px;padding:12px 14px;font-size:13px;margin-bottom:16px}
  #msg{font-size:13px;margin-top:6px}
  tr.tot td{background:#eaf1fb;font-weight:800;color:var(--navy);border-top:2px solid #cdd9ef}
  .diaghead{background:var(--navy);color:#fff;border-radius:12px;padding:14px 18px;margin-bottom:16px;font-weight:700}
  .diaghead small{display:block;color:#9fb3d4;font-weight:400;font-size:12px;margin-top:2px}
  .dcard{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:14px}
  .dcard.pd{background:#fffbeb;border-color:#fde68a}
  .dcard.dc{background:#eff6ff;border-color:#bfdbfe}
  .dcard .dh{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px}
  .dcard .dname{font-weight:800;color:var(--navy);font-size:15px}
  .dtag{padding:3px 10px;border-radius:999px;font-size:11px;font-weight:700;border:1px solid var(--line)}
  .dtag.pd{background:#fef3c7;color:#92400e;border-color:#fcd34d}
  .dtag.dc{background:#dbeafe;color:#1e40af;border-color:#bfdbfe}
  .dtag.ot{background:#f1f5f9;color:#475569}
  .dcard .dmonto{margin-left:auto;color:var(--bad);font-weight:800;font-size:13px}
  .dobs{background:#fff;border:1px solid var(--line);border-radius:8px;padding:9px 12px;font-size:12.5px;margin-bottom:10px}
  .dgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  @media(max-width:760px){.dgrid{grid-template-columns:1fr}}
  .dbox{background:#fff;border:1px solid var(--line);border-radius:8px;padding:10px 12px}
  .dbox .bt{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.4px;margin-bottom:4px}
  .dbox.why .bt{color:#b45309} .dbox.act .bt{color:var(--ok)}
  .dbox .bd{font-size:12.5px;color:#334155}
</style>
</head>
<body>
  <aside class="side">
    <div class="brand">
      <div class="flag">&#127466;&#127464;</div>
      <h2>CONCILIACION<br>DEUDA EXTERNA</h2>
      <small>Subgerencia de Deuda Publica</small>
    </div>
    <nav class="nav" id="nav">
      <div class="it act" data-v="cargar"><div class="num">1</div><div><div class="tt">Cargar Reportes</div><div class="ss">MEF + BCE</div></div></div>
      <div class="it" data-v="resultados"><div class="num">2</div><div><div class="tt">Resultados</div><div class="ss">Cruce automatico</div></div></div>
      <div class="it" data-v="ajustes"><div class="num">3</div><div><div class="tt">Ajustes</div><div class="ss">Pagos directos</div></div></div>
      <div class="it" data-v="cert"><div class="num">4</div><div><div class="tt">Certificacion</div><div class="ss">Reporte oficial</div></div></div>
      <div class="it" data-v="historial"><div class="num">5</div><div><div class="tt">Historial</div><div class="ss">Periodos anteriores</div></div></div>
    </nav>
    <div class="estado">
      <div class="h">Estado de carteras</div>
      <div id="estadoSide" style="color:#9fb3d4">Sin datos</div>
    </div>
  </aside>

  <div class="main">
    <div class="top">
      <h1>Sistema de Conciliacion &ndash; Deuda Externa Publica</h1>
      <div class="right">
        <span class="badge" id="periodoBadge">Sin periodo</span>
        <span class="muted" id="analistaTop">Analista MEF</span>
      </div>
    </div>
    <div class="content">

      <section id="v-cargar">
        <h3 class="sec">&#128193; Cargar Reportes</h3>
        <p class="sub">Arrastra los archivos sobre las tarjetas o usa los botones. Soporta .xls (Excel 97-2003) y .xlsx</p>
        <div class="info"><b>Mapeo:</b> Desembolsos (MEF Col C &harr; BCE Col K) &middot; Amortizaciones (D &harr; U) &middot; Intereses (E &harr; V) &middot; Comisiones (F &harr; W) &middot; Int.Condonados (G &harr; Y) &middot; Int.Mora (H &harr; X)</div>
        <div class="grid2">
          <div class="drop" id="dzMef" ondrop="onDrop(event,'mef')" ondragover="onOver(event)" ondragleave="onLeave(event)">
            <div class="ic">&#127963;&#65039;</div>
            <div class="ti">Reporte MEF</div>
            <div class="de">Ministerio de Economia y Finanzas</div>
            <div class="dz">&#128229; Arrastra aqui o haz clic &middot; .xls .xlsx</div>
            <div class="fn" id="fnMef"></div>
            <div class="btn-sel"><button class="lnk" onclick="document.getElementById('fileMef').click()">&#128193; Seleccionar archivo MEF</button></div>
            <input type="file" id="fileMef" accept=".xls,.xlsx" onchange="onPick(this,'mef')">
          </div>
          <div class="drop" id="dzBce" ondrop="onDrop(event,'bce')" ondragover="onOver(event)" ondragleave="onLeave(event)">
            <div class="ic">&#127974;</div>
            <div class="ti">Reporte BCE</div>
            <div class="de">Banco Central del Ecuador</div>
            <div class="dz">&#128229; Arrastra aqui o haz clic &middot; .xls .xlsx</div>
            <div class="fn" id="fnBce"></div>
            <div class="btn-sel"><button class="lnk" onclick="document.getElementById('fileBce').click()">&#128193; Seleccionar archivo BCE</button></div>
            <input type="file" id="fileBce" accept=".xls,.xlsx" onchange="onPick(this,'bce')">
          </div>
        </div>
        <div class="card">
          <h3 class="sec" style="font-size:15px">&#128197; Periodo</h3>
          <div class="frow" style="margin-top:10px">
            <div class="fld"><label>Mes</label>
              <select id="mes">
                <option>Enero</option><option>Febrero</option><option>Marzo</option><option>Abril</option>
                <option>Mayo</option><option>Junio</option><option>Julio</option><option>Agosto</option>
                <option>Septiembre</option><option>Octubre</option><option>Noviembre</option><option>Diciembre</option>
              </select></div>
            <div class="fld"><label>Anio</label><input type="number" id="anio" value="2026" style="width:110px"></div>
            <div class="fld"><label>Analista</label><input type="text" id="analista" value="Analista MEF" style="width:200px"></div>
          </div>
        </div>
        <div class="card footbar">
          <span class="muted" id="cargarMsg">Carga ambos archivos para continuar.</span>
          <button class="btn" id="btnRun" onclick="ejecutar()" disabled>&#9654; Ejecutar Conciliacion</button>
        </div>
        <div id="msg"></div>
      </section>

      <section id="v-resultados" class="hidden">
        <h3 class="sec">&#128202; Resultados de la Conciliacion</h3>
        <p class="sub" id="resSub">&mdash;</p>
        <div id="resAlert"></div>
        <div class="card">
          <div class="donutwrap">
            <div class="donut" id="donut"><div class="ct"><div class="pct" id="pct">0%</div><div class="lb">Conciliado</div></div></div>
            <div style="flex:1;min-width:260px"><div class="stats" id="stats"></div></div>
          </div>
          <div class="toolbar">
            <button class="chip act" data-f="all" onclick="setFiltro('all',this)">Todos</button>
            <button class="chip" data-f="dif" onclick="setFiltro('dif',this)">Solo diferencias</button>
            <button class="chip" data-f="con" onclick="setFiltro('con',this)">Solo conciliados</button>
            <span style="flex:1"></span>
            <button class="btn alt" onclick="exportar()">&#11015; Exportar Excel</button>
          </div>
          <div class="scroll">
            <table><thead><tr>
              <th>Acreedor</th><th>Concepto</th>
              <th style="text-align:right">MEF (USD)</th><th style="text-align:right">BCE (USD)</th>
              <th style="text-align:right">Diferencia</th><th>Estado</th>
            </tr></thead><tbody id="tbody"></tbody></table>
          </div>
        </div>
      </section>

      <section id="v-ajustes" class="hidden">
        <h3 class="sec">&#9881;&#65039; Ajustes y Pagos Directos</h3>
        <p class="sub">El sistema detecta las observaciones del MEF y explica cada diferencia o inconsistencia.</p>

        <div class="card">
          <h3 class="sec" style="font-size:15px">&#128228; Cargar respaldo de pago directo (MEF)</h3>
          <p class="sub" style="margin:6px 0 12px">Sube el archivo que el MEF envia por Quipux (ej. <i>pagos_directos_ibrd_9722.xls</i>). La app extrae el valor y lo agrega al lado del BCE con su nota.</p>
          <div class="drop" id="dzPd" ondrop="onDropPd(event)" ondragover="onOver(event)" ondragleave="onLeave(event)">
            <div class="ic">&#128196;</div>
            <div class="ti">Respaldo(s) de pago directo</div>
            <div class="dz">&#128229; Arrastra aqui o haz clic &middot; .xls .xlsx</div>
            <div class="btn-sel"><button class="lnk" onclick="document.getElementById('filePd').click()">&#128193; Seleccionar archivo(s)</button></div>
            <input type="file" id="filePd" accept=".xls,.xlsx" multiple onchange="subirPd(this.files)">
          </div>
          <div id="pdPrev"></div>
        </div>

        <div class="diaghead" id="diagHead">Ejecuta una conciliacion para ver el diagnostico.</div>
        <div id="diagList"></div>
      </section>

      <section id="v-cert" class="hidden">
        <h3 class="sec">&#128220; Certificacion / Quipux de respuesta</h3>
        <p class="sub">Genera el texto del oficio de respuesta BCE &rarr; MEF segun el resultado de la conciliacion.</p>
        <div class="card">
          <div class="frow">
            <div class="fld"><label>Nro. Oficio BCE</label><input type="text" id="qOfBce" value="BCE-SSFI-2026-XXXX-OF" style="width:200px"></div>
            <div class="fld"><label>Responde al Oficio MEF</label><input type="text" id="qOfMef" value="MEF-DNS-2026-0011-O" style="width:200px"></div>
            <div class="fld"><label>Fecha Oficio MEF</label><input type="text" id="qFechaMef" value="14 de mayo de 2026" style="width:180px"></div>
          </div>
          <div class="frow" style="margin-top:12px">
            <div class="fld" style="flex:1;min-width:260px"><label>Destinatario</label><input type="text" id="qDest" value="Ana Maria Vallejo Cabezas - Directora Nacional de Seguimiento" style="width:100%"></div>
            <div class="fld" style="flex:1;min-width:260px"><label>Firmante (BCE)</label><input type="text" id="qFirma" value="Mgs. Luis Santiago Vargas Bautista - Subgerente de Servicios Financieros Internacionales" style="width:100%"></div>
          </div>
          <div style="margin-top:14px;display:flex;gap:10px;flex-wrap:wrap">
            <button class="btn" onclick="generarQuipux()">&#9881;&#65039; Generar texto</button>
            <button class="btn alt" onclick="copiarQuipux()">&#128203; Copiar</button>
            <button class="btn alt" onclick="descargarQuipux()">&#11015; Descargar .txt</button>
            <button class="btn alt" onclick="window.print()">&#128424;&#65039; Imprimir</button>
          </div>
          <textarea id="qText" style="width:100%;height:460px;margin-top:14px;border:1px solid var(--line);border-radius:10px;padding:14px;font-family:Consolas,monospace;font-size:12.5px;color:var(--txt);background:#fcfdff" placeholder="Ejecuta una conciliacion y pulsa 'Generar texto'."></textarea>
        </div>
      </section>

      <section id="v-historial" class="hidden">
        <h3 class="sec">&#128451;&#65039; Historial</h3>
        <p class="sub">Periodos conciliados guardados en el servidor.</p>
        <div class="card"><div id="hist" class="hist"></div></div>
      </section>

    </div>
  </div>

<script>
const MESES=["Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"];
let FILES={mef:null,bce:null}, DATA=[], PERIODO="", FILTRO="all", RESUMEN=null, TOTALES=[], DIAG=[];
const fmt=n=>(n||0).toLocaleString("es-EC",{minimumFractionDigits:2,maximumFractionDigits:2});
function msg(t,err){const m=document.getElementById("msg");m.textContent=t;m.style.color=err?"#dc2626":"#16a34a";}
const valido=f=>f&&/\.(xls|xlsx)$/i.test(f.name);

function recibir(f,lado){
  if(!valido(f)){alert("Solo .xls o .xlsx");return;}
  FILES[lado]=f;
  document.getElementById(lado==="mef"?"fnMef":"fnBce").textContent="✓ "+f.name;
  document.getElementById(lado==="mef"?"dzMef":"dzBce").classList.add("set");
  const listo=FILES.mef&&FILES.bce;
  document.getElementById("btnRun").disabled=!listo;
  document.getElementById("cargarMsg").textContent=listo?"Listo para conciliar.":"Carga ambos archivos para continuar.";
}
function onPick(inp,lado){if(inp.files[0])recibir(inp.files[0],lado);}
function onOver(e){e.preventDefault();e.currentTarget.classList.add("over");}
function onLeave(e){e.currentTarget.classList.remove("over");}
function onDrop(e,lado){e.preventDefault();e.currentTarget.classList.remove("over");const f=e.dataTransfer.files[0];if(f)recibir(f,lado);}

async function ejecutar(){
  if(!(FILES.mef&&FILES.bce))return;
  const mes=document.getElementById("mes").value, anio=document.getElementById("anio").value;
  const periodo=anio+"-"+String(MESES.indexOf(mes)+1).padStart(2,"0");
  const fd=new FormData();
  fd.append("archivos",FILES.mef); fd.append("archivos",FILES.bce);
  fd.append("periodo",periodo);
  const btn=document.getElementById("btnRun");btn.disabled=true;msg("Procesando...");
  try{
    const j=await (await fetch("/api/conciliar",{method:"POST",body:fd})).json();
    if(!j.ok){msg(j.error||"Error",true);btn.disabled=false;return;}
    DATA=j.registros;PERIODO=j.periodo;TOTALES=j.totales||[];DIAG=j.diagnostico||[];
    RESUMEN={total:j.total,con:j.conciliados,dif:j.diferencias,sum:j.total_diferencia,
             mes,anio,analista:document.getElementById("analista").value,fecha:j.fecha};
    pintarResultados();pintarCert();pintarDiag();cargarHistorial();
    document.getElementById("periodoBadge").textContent=mes+" "+anio;
    document.getElementById("periodoBadge").className="badge "+(j.diferencias===0?"ok":"");
    document.getElementById("analistaTop").textContent=RESUMEN.analista;
    document.getElementById("estadoSide").innerHTML=
      `<span style="color:#7ee2a8">${j.conciliados} conciliadas</span><br><span style="color:#f3a9a9">${j.diferencias} con diferencia</span>`;
    let det="BCE = "+j.archivo_bce+"  &middot;  MEF = "+j.archivo_mef;
    if(j.avisos&&j.avisos.length)det+="  ! "+j.avisos.join(" / ");
    msg("Cruce listo. "+det);
    irA("resultados");
  }catch(e){msg(e.message,true);}
  btn.disabled=false;
}

function pintarResultados(){
  const {total,con,dif,sum}=RESUMEN;
  const pct=total?Math.round(con*100/total):0;
  const color=pct>=100?"#16a34a":pct>=60?"#d97706":"#dc2626";
  const d=document.getElementById("donut");
  d.style.background=`conic-gradient(${color} ${pct*3.6}deg,#e5e9f0 0deg)`;
  document.getElementById("pct").textContent=pct+"%";
  document.getElementById("stats").innerHTML=
    `<div class="stat"><div class="n">${total}</div><div class="l">Comparaciones</div></div>
     <div class="stat ok"><div class="n">${con}</div><div class="l">Conciliados</div></div>
     <div class="stat bad"><div class="n">${dif}</div><div class="l">Con diferencia</div></div>
     <div class="stat gold"><div class="n">${fmt(sum)}</div><div class="l">&Sigma;|Diferencia| USD</div></div>`;
  document.getElementById("resSub").textContent=`Periodo ${RESUMEN.mes} ${RESUMEN.anio} &middot; ${con} de ${total} conceptos conciliados`.replace("&middot;","·");
  document.getElementById("resAlert").innerHTML = dif===0
    ? `<div class="alert" style="background:#ecfdf3;border:1px solid #bbf7d0;color:#166534">✅ Todas las carteras concilian exactamente. No hay diferencias.</div>`:"";
  render();
}
function setFiltro(f,el){FILTRO=f;document.querySelectorAll(".chip").forEach(c=>c.classList.remove("act"));el.classList.add("act");render();}
function render(){
  let rows=DATA;
  if(FILTRO==="dif")rows=DATA.filter(r=>r.estado==="DIFERENCIA");
  if(FILTRO==="con")rows=DATA.filter(r=>r.estado==="CONCILIADO");
  const tb=document.getElementById("tbody");tb.innerHTML="";
  if(!rows.length){tb.innerHTML=`<tr><td colspan="6" class="muted" style="text-align:center;padding:24px">Sin filas para este filtro.</td></tr>`;return;}
  const totMap={}; TOTALES.forEach(t=>totMap[t.acreedor]=t);
  let actual=null;
  const cerrarTotal=ac=>{ const t=totMap[ac]; if(!t) return;
    const tr=document.createElement("tr"); tr.className="tot";
    tr.innerHTML=`<td></td><td>TOTAL ${ac}</td><td class="num">${fmt(t.mef)}</td>
      <td class="num">${fmt(t.bce)}</td><td class="num">${fmt(t.dif)}</td>
      <td><span class="pill ${t.estado==='DIFERENCIA'?'dif':'con'}">${t.estado}</span></td>`;
    tb.appendChild(tr); };
  rows.forEach(r=>{
    if(r.acreedor!==actual){ if(actual!==null) cerrarTotal(actual); actual=r.acreedor;
      const g=document.createElement("tr");g.className="grp";g.innerHTML=`<td colspan="6">${r.acreedor}</td>`;tb.appendChild(g);}
    const tr=document.createElement("tr");tr.className=r.estado==="DIFERENCIA"?"dif":"con";
    tr.innerHTML=`<td></td><td>${r.concepto}</td><td class="num">${fmt(r.mef)}</td>
      <td class="num">${fmt(r.bce)}</td><td class="num">${fmt(r.diferencia)}</td>
      <td><span class="pill ${r.estado==='DIFERENCIA'?'dif':'con'}">${r.estado}</span></td>`;
    tb.appendChild(tr);
  });
  if(actual!==null) cerrarTotal(actual);
}

function pintarDiag(){
  const head=document.getElementById("diagHead"), list=document.getElementById("diagList");
  if(!DIAG.length){ head.innerHTML="No hay observaciones del MEF en este periodo."; list.innerHTML=""; return; }
  const npd=DIAG.filter(d=>d.tipo==="Pago Directo").length;
  head.innerHTML=`Diagnostico Automatico &ndash; Observaciones del MEF
    <small>Detectadas ${DIAG.length} cartera(s) con observacion &middot; ${npd} pago(s) directo(s) &middot; fuente: columna Observaciones del Resumen MEF</small>`;
  const cls=t=>t==="Pago Directo"?"pd":t==="Diferencial Cambiario"?"dc":"ot";
  list.innerHTML=DIAG.map(d=>{
    const c=cls(d.tipo);
    const montos=Object.entries(d.rubros||{}).map(([k,v])=>`&Delta; ${k}: ${fmt(v)}`).join(" &middot; ");
    const obs=d.observacion?`<div class="dobs">&#128221; <b>Observacion MEF:</b> ${d.observacion}</div>`:"";
    return `<div class="dcard ${c}">
      <div class="dh"><span class="dname">${d.acreedor}</span>
        <span class="dtag ${c}">${d.tipo}</span>
        <span class="dmonto">${montos}</span></div>
      ${obs}
      <div class="dgrid">
        <div class="dbox why"><div class="bt">&#191;Por que existe?</div><div class="bd">${d.por_que}</div></div>
        <div class="dbox act"><div class="bt">&#9989; Accion requerida</div><div class="bd">${d.accion}</div></div>
      </div></div>`;
  }).join("");
}

// ---- Pagos directos: subir respaldo, previsualizar y aplicar ----
function onDropPd(e){e.preventDefault();e.currentTarget.classList.remove("over");if(e.dataTransfer.files.length)subirPd(e.dataTransfer.files);}
async function subirPd(files){
  if(!PERIODO){alert("Primero ejecuta una conciliacion (necesito el periodo).");return;}
  const fd=new FormData();[...files].forEach(f=>fd.append("archivos",f));
  const prev=document.getElementById("pdPrev");prev.innerHTML="<p class='muted'>Leyendo respaldo...</p>";
  const j=await (await fetch("/api/pago_directo",{method:"POST",body:fd})).json();
  if(!j.ok){prev.innerHTML="<p style='color:#dc2626'>Error al leer</p>";return;}
  prev.innerHTML=j.previos.map((p,i)=>{
    if(p.error)return `<div class="dcard"><b>${p.archivo}</b>: <span style="color:#dc2626">${p.error}</span></div>`;
    const det=(p.detalle||[]).map(d=>`<div class="muted" style="font-size:12px">&middot; ${d.beneficiario}: ${fmt(d.monto)}</div>`).join("");
    return `<div class="dcard pd" id="pd${i}">
      <div class="dh"><span class="dname">${p.acreedor||'?'}</span><span class="dtag pd">Pago Directo</span>
        <span class="dmonto" style="color:var(--navy)">${p.prestamo||''}</span></div>
      ${det}
      <div class="frow" style="margin-top:10px;align-items:flex-end">
        <div class="fld"><label>Cartera</label><input type="text" id="pdac${i}" value="${p.acreedor||''}" style="width:130px"></div>
        <div class="fld"><label>Valor (USD)</label><input type="number" id="pdval${i}" value="${p.valor||0}" step="0.01" style="width:160px"></div>
        <div class="fld" style="flex:1;min-width:260px"><label>Nota</label><input type="text" id="pdnota${i}" value="${(p.nota||'').replace(/"/g,'&quot;')}" style="width:100%"></div>
        <button class="btn" onclick="aplicarPd(${i})">&#10133; Agregar al BCE</button>
      </div></div>`;
  }).join("");
}
async function aplicarPd(i){
  const acreedor=document.getElementById("pdac"+i).value.trim();
  const valor=parseFloat(document.getElementById("pdval"+i).value)||0;
  const nota=document.getElementById("pdnota"+i).value.trim();
  if(!acreedor||!valor){alert("Falta cartera o valor");return;}
  const j=await (await fetch("/api/aplicar_pago_directo",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({periodo:PERIODO,acreedor,concepto:"Desembolsos",valor,nota})})).json();
  if(!j.ok){alert(j.error||"Error");return;}
  DATA=j.registros;TOTALES=j.totales||[];
  RESUMEN={...RESUMEN,total:j.total,con:j.conciliados,dif:j.diferencias,sum:j.total_diferencia};
  document.getElementById("pd"+i).style.opacity=".5";
  document.getElementById("estadoSide").innerHTML=
    `<span style="color:#7ee2a8">${j.conciliados} conciliadas</span><br><span style="color:#f3a9a9">${j.diferencias} con diferencia</span>`;
  pintarResultados();pintarCert();
  alert("Pago directo agregado a "+acreedor+". Revisa Resultados: la cartera deberia cuadrar.");
}

async function exportar(){
  if(!PERIODO)return;
  const r=await fetch("/api/exportar",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({periodo:PERIODO})});
  if((r.headers.get("content-type")||"").includes("application/json")){const j=await r.json();msg(j.error||"No se pudo exportar",true);return;}
  const b=await r.blob(),u=URL.createObjectURL(b),a=document.createElement("a");
  a.href=u;a.download="Conciliacion_BCE_MEF_"+PERIODO+".xlsx";a.click();URL.revokeObjectURL(u);
}

// ---- Generador del Quipux de respuesta BCE -> MEF ----
const RUBRO_TXT={"Desembolsos":"desembolsos","Amortizaciones":"amortizaciones","Intereses":"intereses",
  "Comisiones":"comisiones","Intereses Condonados":"condonados","Interés por Mora":"intereses por mora"};
const CARTERA_TXT={"AMAZON DAC":"AMAZON"};
const ORDEN_QUIPUX=["AIIB","AMAZON DAC","BANCOS","BID","BIRF","BONOS","CAF","FIDA","FLAR","FMI","GOBIERNOS","GPS"];

function unirRubros(arr){
  if(!arr.length) return "";
  if(arr.length===1) return arr[0];
  const ult=arr[arr.length-1];
  const conj=/^h?i/i.test(ult)?"e":"y";   // "comisiones e intereses", "intereses y comisiones"
  return arr.slice(0,-1).join(", ")+" "+conj+" "+ult;
}

function lineaCartera(cart){
  const rows=DATA.filter(r=>r.acreedor===cart);
  const nombre=CARTERA_TXT[cart]||cart;
  if(!rows.length) return `${nombre}: No existen movimientos.`;
  const con=rows.filter(r=>r.estado==="CONCILIADO").map(r=>RUBRO_TXT[r.concepto]||r.concepto.toLowerCase());
  const dif=rows.filter(r=>r.estado==="DIFERENCIA");
  let s="";
  if(con.length) s=`${nombre}: No existen observaciones en los rubros de ${unirRubros(con)}.`;
  else s=`${nombre}:`;
  if(dif.length){
    const d=dif.map(r=>`${RUBRO_TXT[r.concepto]||r.concepto.toLowerCase()} (diferencia USD ${fmt(Math.abs(r.diferencia))})`);
    s+=` Existe(n) diferencia(s) por revisar en: ${unirRubros(d)}.`;
  }
  return s;
}

function generarQuipux(){
  if(!DATA.length){alert("Primero ejecuta una conciliacion.");return;}
  const {mes,anio,dif}=RESUMEN;
  const ofBce=document.getElementById("qOfBce").value.trim();
  const ofMef=document.getElementById("qOfMef").value.trim();
  const fMef=document.getElementById("qFechaMef").value.trim();
  const dest=document.getElementById("qDest").value.trim();
  const firma=document.getElementById("qFirma").value.trim();
  const presentes=new Set(DATA.map(r=>r.acreedor));
  const orden=ORDEN_QUIPUX.concat([...presentes].filter(a=>!ORDEN_QUIPUX.includes(a)));
  const lineas=orden.filter(c=>presentes.has(c)||["AIIB","GPS"].includes(c)).map(lineaCartera);
  const cierre=dif
    ? "Una vez subsanadas las diferencias señaladas, se procedera a ratificar la informacion. Adjunto la matriz de conciliacion del Banco Central del Ecuador en formato Excel."
    : "Por lo expuesto, me permito remitir para su revision la matriz de conciliacion del Banco Central del Ecuador en formato Excel. Este archivo contiene la informacion completa y necesaria para el proceso de conciliacion.";
  const txt=
`Oficio Nro. ${ofBce}
Quito, D.M., ${new Date().toLocaleDateString("es-EC",{day:"numeric",month:"long",year:"numeric"})}

Asunto: Conciliacion mensual de valores del Servicio de la Deuda a ${mes} ${anio}

Senora Magister
${dest}
MINISTERIO DE ECONOMIA Y FINANZAS
En su Despacho

De mi consideracion:

Me refiero al Oficio Nro. ${ofMef} de ${fMef}, mediante el cual su Despacho solicito la ratificacion o rectificacion de la informacion sobre los movimientos de la deuda externa publica correspondiente al mes de ${(mes||"").toLowerCase()} de ${anio}.

Al respecto, me permito indicar lo siguiente:

${lineas.join("\n")}

${cierre}

Con sentimientos de distinguida consideracion.

Atentamente,

Documento firmado electronicamente
${firma}`;
  document.getElementById("qText").value=txt;
}
function pintarCert(){ if(RESUMEN && DATA.length) generarQuipux(); }
function copiarQuipux(){ const t=document.getElementById("qText"); t.select(); document.execCommand("copy");
  msg("Texto del Quipux copiado al portapapeles."); }
function descargarQuipux(){ const t=document.getElementById("qText").value;
  if(!t){alert("Genera el texto primero.");return;}
  const b=new Blob([t],{type:"text/plain;charset=utf-8"}),u=URL.createObjectURL(b),a=document.createElement("a");
  a.href=u;a.download="Quipux_Respuesta_"+(PERIODO||"conciliacion")+".txt";a.click();URL.revokeObjectURL(u); }

async function cargarHistorial(){
  const j=await (await fetch("/api/historial")).json();
  const h=document.getElementById("hist");
  if(!j.periodos||!j.periodos.length){h.innerHTML=`<span class="muted">Aun no hay conciliaciones.</span>`;return;}
  h.innerHTML=j.periodos.map(p=>`<a onclick="verHist('${p.periodo}')">&#128197; <b>${p.periodo}</b> &nbsp; <span class="muted">${p.conciliados}✓ / ${p.diferencias}✗ &middot; ${p.ultima||''}</span></a>`).join("");
}
async function verHist(p){
  const j=await (await fetch("/api/periodo/"+encodeURIComponent(p))).json();
  if(!j.ok)return;DATA=j.registros;PERIODO=p;DIAG=[];
  // Recalcular totales por cartera desde los registros guardados
  const tm={}; DATA.forEach(r=>{const t=tm[r.acreedor]||(tm[r.acreedor]={acreedor:r.acreedor,mef:0,bce:0});t.mef+=r.mef;t.bce+=r.bce;});
  TOTALES=Object.values(tm).map(t=>{const d=Math.round((t.mef-t.bce)*100)/100||0;return {...t,mef:Math.round(t.mef*100)/100,bce:Math.round(t.bce*100)/100,dif:d,estado:Math.abs(d)<=0.5?"CONCILIADO":"DIFERENCIA"};});
  const dif=DATA.filter(r=>r.estado==="DIFERENCIA").length,con=DATA.length-dif;
  const sum=DATA.filter(r=>r.estado==="DIFERENCIA").reduce((a,r)=>a+Math.abs(r.diferencia),0);
  const part=p.split("-");
  RESUMEN={total:DATA.length,con,dif,sum,mes:MESES[parseInt(part[1])-1]||"",anio:part[0],analista:document.getElementById("analista").value};
  pintarResultados();pintarCert();pintarDiag();irA("resultados");
}

function irA(v){
  document.querySelectorAll(".nav .it").forEach(it=>it.classList.toggle("act",it.dataset.v===v));
  ["cargar","resultados","ajustes","cert","historial"].forEach(s=>document.getElementById("v-"+s).classList.toggle("hidden",s!==v));
}
document.getElementById("nav").addEventListener("click",e=>{const it=e.target.closest(".it");if(it)irA(it.dataset.v);});
cargarHistorial();
</script>
</body>
</html>"""


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
