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

# Reutilizamos la lógica de conciliación ya validada
import Conciliacion as C

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


def _guardar_subida(campo):
    """Guarda un archivo subido y devuelve su ruta, o None si no vino."""
    f = request.files.get(campo)
    if not f or not f.filename:
        return None
    nombre = secure_filename(f.filename)
    ruta = os.path.join(UPLOAD_DIR, nombre)
    f.save(ruta)
    return ruta


@app.route("/api/conciliar", methods=["POST"])
def api_conciliar():
    """Recibe los dos .xls (multipart) o rutas (json), ejecuta el cruce,
    guarda el resultado en BD y lo devuelve."""
    if request.files:
        ruta_bce = _guardar_subida("bce")
        ruta_mef = _guardar_subida("mef")
        periodo = request.form.get("periodo", "").strip()
    else:
        data = request.json or {}
        ruta_bce = data.get("ruta_bce")
        ruta_mef = data.get("ruta_mef")
        periodo = (data.get("periodo") or "").strip()

    if not ruta_bce or not os.path.exists(ruta_bce):
        return jsonify({"ok": False, "error": "Falta el reporte del BCE (.xls)"})
    if not ruta_mef or not os.path.exists(ruta_mef):
        return jsonify({"ok": False, "error": "Falta el reporte del MEF (.xls)"})

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

    return jsonify({
        "ok": True, "periodo": periodo, "fecha": ahora,
        "registros": registros, "total": len(registros),
        "conciliados": conciliados, "diferencias": diferencias,
        "total_diferencia": round(total_dif, 2),
        "ruta_bce": ruta_bce, "ruta_mef": ruta_mef,
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
    rows = db_q("""SELECT acreedor, concepto, mef, bce, diferencia, estado
                   FROM conciliaciones WHERE periodo = ? ORDER BY id""",
                (periodo,), fetch=True)
    return jsonify({"ok": True, "periodo": periodo, "registros": rows or []})


# =============================================================================
# FRONT-END (una sola página, estilo BCE oscuro)
# =============================================================================
PANEL_HTML = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Conciliación BCE vs MEF</title>
<style>
  :root{--bg:#0f1419;--panel:#1a212b;--line:#2b3543;--txt:#e8ecef;
        --muted:#8b98a8;--ok:#1f8a4c;--okbg:#13301f;--bad:#c0392b;--badbg:#33181a;
        --accent:#2d7dd2;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
       font-family:"Segoe UI",system-ui,sans-serif;font-size:14px}
  header{background:linear-gradient(90deg,#10243b,#0f1419);padding:18px 26px;
         border-bottom:1px solid var(--line)}
  header h1{margin:0;font-size:19px;letter-spacing:.3px}
  header p{margin:4px 0 0;color:var(--muted);font-size:12px}
  .wrap{max-width:1180px;margin:0 auto;padding:22px 26px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
        padding:18px 20px;margin-bottom:20px}
  .row{display:flex;gap:18px;flex-wrap:wrap;align-items:flex-end}
  label{display:block;font-size:12px;color:var(--muted);margin-bottom:6px}
  input[type=text]{background:#0d1218;border:1px solid var(--line);
        color:var(--txt);border-radius:7px;padding:9px 11px;width:100%}
  .fld{flex:1;min-width:220px}
  .drop{flex:1;min-width:260px;background:#0d1218;border:2px dashed var(--line);
        border-radius:10px;padding:22px 16px;text-align:center;cursor:pointer;
        transition:.15s;color:var(--muted)}
  .drop:hover{border-color:var(--accent);color:var(--txt)}
  .drop.over{border-color:var(--accent);background:#102132;color:var(--txt)}
  .drop.set{border-style:solid;border-color:var(--ok);background:#10241a;color:var(--txt)}
  .drop .big{font-size:26px;line-height:1;margin-bottom:8px}
  .drop .ttl{font-weight:600;font-size:13px}
  .drop .fn{margin-top:7px;font-size:12px;color:#36c172;word-break:break-all}
  .drop input{display:none}
  button{background:var(--accent);color:#fff;border:0;border-radius:7px;
         padding:10px 18px;font-size:14px;cursor:pointer;font-weight:600}
  button:hover{filter:brightness(1.1)} button:disabled{opacity:.5;cursor:wait}
  button.ghost{background:transparent;border:1px solid var(--line);color:var(--txt)}
  .stats{display:flex;gap:14px;flex-wrap:wrap;margin:4px 0 0}
  .stat{background:#0d1218;border:1px solid var(--line);border-radius:9px;
        padding:12px 18px;min-width:130px}
  .stat .n{font-size:24px;font-weight:700} .stat .l{font-size:11px;color:var(--muted)}
  .stat.ok .n{color:#36c172} .stat.bad .n{color:#e15b4c}
  table{width:100%;border-collapse:collapse;margin-top:8px}
  th,td{padding:9px 11px;border-bottom:1px solid var(--line);text-align:left}
  th{font-size:11px;text-transform:uppercase;color:var(--muted);
     position:sticky;top:0;background:var(--panel)}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  tr.dif{background:var(--badbg)} tr.con{background:var(--okbg)}
  .pill{padding:3px 9px;border-radius:20px;font-size:11px;font-weight:700}
  .pill.con{background:var(--ok);color:#fff} .pill.dif{background:var(--bad);color:#fff}
  .acr{font-weight:600}
  .toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:8px}
  .muted{color:var(--muted)} .hidden{display:none}
  .chk{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:13px}
  .hist a{color:var(--accent);cursor:pointer;text-decoration:none;margin-right:14px}
  #msg{margin-top:10px;font-size:13px}
</style>
</head>
<body>
<header>
  <h1>Conciliación BCE&nbsp;vs&nbsp;MEF — Deuda Externa Pública</h1>
  <p>Cruce mensual por acreedor · Desembolsos · Amortizaciones · Intereses · Comisiones · Condonados · Mora</p>
</header>
<div class="wrap">

  <div class="card">
    <div class="row">
      <div class="drop" id="dzBce" onclick="document.getElementById('bce').click()">
        <div class="big">🏦</div>
        <div class="ttl">Reporte Conciliación <b>BCE</b></div>
        <div class="muted" style="font-size:12px">Arrastra el .xls aquí o haz clic</div>
        <div class="fn" id="fnBce"></div>
        <input type="file" id="bce" accept=".xls,.xlsx">
      </div>
      <div class="drop" id="dzMef" onclick="document.getElementById('mef').click()">
        <div class="big">🏛️</div>
        <div class="ttl">Reporte Conciliación <b>MEF</b></div>
        <div class="muted" style="font-size:12px">Arrastra el .xls aquí o haz clic</div>
        <div class="fn" id="fnMef"></div>
        <input type="file" id="mef" accept=".xls,.xlsx">
      </div>
    </div>
    <div class="row" style="margin-top:16px">
      <div class="fld" style="max-width:180px">
        <label>Periodo (AAAA-MM)</label>
        <input type="text" id="periodo" placeholder="2026-03">
      </div>
      <button id="btn" onclick="conciliar()">Cruzar reportes</button>
    </div>
    <div id="msg"></div>
  </div>

  <div class="card hidden" id="resCard">
    <div class="stats" id="stats"></div>
    <div class="toolbar" style="margin-top:16px">
      <label class="chk"><input type="checkbox" id="soloDif" onchange="render()"> Solo diferencias</label>
      <span style="flex:1"></span>
      <span class="muted" id="periodoLbl"></span>
      <button class="ghost" onclick="exportar()">⬇ Exportar Excel</button>
    </div>
    <div style="max-height:560px;overflow:auto">
    <table>
      <thead><tr>
        <th>Acreedor</th><th>Concepto</th>
        <th style="text-align:right">MEF (USD)</th>
        <th style="text-align:right">BCE (USD)</th>
        <th style="text-align:right">Diferencia</th>
        <th>Estado</th>
      </tr></thead>
      <tbody id="tbody"></tbody>
    </table>
    </div>
  </div>

  <div class="card hist">
    <label>Historial de periodos conciliados</label>
    <div id="hist" class="muted">Cargando…</div>
  </div>

</div>
<script>
let DATA = [];
let PERIODO = "";

const fmt = n => (n||0).toLocaleString("es-EC",{minimumFractionDigits:2,maximumFractionDigits:2});

function msg(t, err){ const m=document.getElementById("msg");
  m.textContent=t; m.style.color=err?"#e15b4c":"#36c172"; }

// ── Drag & drop: enlaza una zona con su <input file> ──────────────────────
function setupDrop(dzId, inputId, fnId){
  const dz=document.getElementById(dzId), input=document.getElementById(inputId),
        fn=document.getElementById(fnId);
  const valido=f=>f && /\.(xls|xlsx)$/i.test(f.name);
  const mostrar=()=>{ const f=input.files[0];
    if(f){ dz.classList.add("set"); fn.textContent="✓ "+f.name; }
    else { dz.classList.remove("set"); fn.textContent=""; } };
  input.addEventListener("change",mostrar);
  ["dragenter","dragover"].forEach(ev=>dz.addEventListener(ev,e=>{
    e.preventDefault(); e.stopPropagation(); dz.classList.add("over"); }));
  ["dragleave","drop"].forEach(ev=>dz.addEventListener(ev,e=>{
    e.preventDefault(); e.stopPropagation(); dz.classList.remove("over"); }));
  dz.addEventListener("drop",e=>{
    const f=e.dataTransfer.files[0];
    if(!valido(f)){ msg("Solo se aceptan archivos .xls o .xlsx",true); return; }
    const dt=new DataTransfer(); dt.items.add(f); input.files=dt.files; mostrar();
  });
}
setupDrop("dzBce","bce","fnBce");
setupDrop("dzMef","mef","fnMef");

async function conciliar(){
  const bce=document.getElementById("bce").files[0];
  const mef=document.getElementById("mef").files[0];
  if(!bce||!mef){ msg("Carga ambos reportes (BCE y MEF) arrastrándolos o con clic.",true); return; }
  const fd=new FormData();
  fd.append("bce",bce); fd.append("mef",mef);
  fd.append("periodo",document.getElementById("periodo").value.trim());
  const btn=document.getElementById("btn"); btn.disabled=true; msg("Procesando…");
  try{
    const r=await fetch("/api/conciliar",{method:"POST",body:fd});
    const j=await r.json();
    if(!j.ok){ msg(j.error||"Error",true); btn.disabled=false; return; }
    DATA=j.registros; PERIODO=j.periodo;
    document.getElementById("periodoLbl").textContent="Periodo "+j.periodo+" · "+j.fecha;
    document.getElementById("stats").innerHTML=
      `<div class="stat"><div class="n">${j.total}</div><div class="l">Comparaciones</div></div>
       <div class="stat ok"><div class="n">${j.conciliados}</div><div class="l">Conciliados</div></div>
       <div class="stat bad"><div class="n">${j.diferencias}</div><div class="l">Con diferencia</div></div>
       <div class="stat bad"><div class="n">${fmt(j.total_diferencia)}</div><div class="l">Σ |Diferencia| USD</div></div>`;
    document.getElementById("resCard").classList.remove("hidden");
    render(); cargarHistorial();
    msg("Conciliación lista: "+j.conciliados+" conciliados, "+j.diferencias+" con diferencia.");
  }catch(e){ msg(e.message,true); }
  btn.disabled=false;
}

function render(){
  const solo=document.getElementById("soloDif").checked;
  const tb=document.getElementById("tbody"); tb.innerHTML="";
  DATA.filter(r=>!solo||r.estado==="DIFERENCIA").forEach(r=>{
    const tr=document.createElement("tr");
    tr.className=r.estado==="DIFERENCIA"?"dif":"con";
    tr.innerHTML=`<td class="acr">${r.acreedor}</td><td>${r.concepto}</td>
      <td class="num">${fmt(r.mef)}</td><td class="num">${fmt(r.bce)}</td>
      <td class="num">${fmt(r.diferencia)}</td>
      <td><span class="pill ${r.estado==='DIFERENCIA'?'dif':'con'}">${r.estado}</span></td>`;
    tb.appendChild(tr);
  });
}

async function exportar(){
  if(!PERIODO){ return; }
  const r=await fetch("/api/exportar",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({periodo:PERIODO})});
  if(r.headers.get("content-type").includes("application/json")){
    const j=await r.json(); msg(j.error||"No se pudo exportar",true); return; }
  const blob=await r.blob(); const url=URL.createObjectURL(blob);
  const a=document.createElement("a"); a.href=url;
  a.download="Conciliacion_BCE_MEF_"+PERIODO+".xlsx"; a.click();
  URL.revokeObjectURL(url);
}

async function cargarHistorial(){
  const j=await (await fetch("/api/historial")).json();
  const h=document.getElementById("hist");
  if(!j.periodos||!j.periodos.length){ h.textContent="Aún no hay conciliaciones."; return; }
  h.innerHTML=j.periodos.map(p=>
    `<a onclick="verPeriodo('${p.periodo}')">${p.periodo}</a>
     <span class="muted">(${p.conciliados}✓ / ${p.diferencias}✗ · ${p.ultima||''})</span><br>`
  ).join("");
}

async function verPeriodo(p){
  const j=await (await fetch("/api/periodo/"+encodeURIComponent(p))).json();
  if(!j.ok) return;
  DATA=j.registros; PERIODO=p;
  const dif=DATA.filter(r=>r.estado==="DIFERENCIA").length;
  const con=DATA.length-dif;
  document.getElementById("periodoLbl").textContent="Periodo "+p+" (historial)";
  document.getElementById("stats").innerHTML=
    `<div class="stat"><div class="n">${DATA.length}</div><div class="l">Comparaciones</div></div>
     <div class="stat ok"><div class="n">${con}</div><div class="l">Conciliados</div></div>
     <div class="stat bad"><div class="n">${dif}</div><div class="l">Con diferencia</div></div>`;
  document.getElementById("resCard").classList.remove("hidden");
  render();
  window.scrollTo({top:0,behavior:"smooth"});
}

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
