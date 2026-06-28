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

    return jsonify({
        "ok": True, "periodo": periodo, "fecha": ahora,
        "registros": registros, "total": len(registros),
        "conciliados": conciliados, "diferencias": diferencias,
        "total_diferencia": round(total_dif, 2),
        "ruta_bce": ruta_bce, "ruta_mef": ruta_mef,
        "archivo_bce": os.path.basename(ruta_bce),
        "archivo_mef": os.path.basename(ruta_mef),
        "avisos": avisos,
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
<title>Conciliacion BCE vs MEF</title>
<style>
  :root{
    --bg:#0b1020; --bg2:#0f1630; --panel:rgba(255,255,255,.04);
    --line:rgba(255,255,255,.10); --txt:#eef2f7; --muted:#9aa6bd;
    --ok:#22c55e; --okd:#0f3d24; --bad:#ef4444; --badd:#40161a;
    --accent:#6366f1; --accent2:#22d3ee; --gold:#f59e0b;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{background:
        radial-gradient(1100px 600px at 12% -8%, #1b2a6b33, transparent 60%),
        radial-gradient(900px 500px at 100% 0%, #0e749035, transparent 55%),
        linear-gradient(180deg,var(--bg),var(--bg2));
        color:var(--txt);font-family:"Segoe UI",system-ui,sans-serif;font-size:14px}
  .wrap{max-width:1240px;margin:0 auto;padding:0 26px 40px}

  header{padding:26px 0 18px;display:flex;align-items:center;gap:16px}
  .logo{width:48px;height:48px;border-radius:14px;flex:none;display:grid;place-items:center;
        font-size:24px;background:linear-gradient(135deg,var(--accent),var(--accent2));
        box-shadow:0 8px 24px #6366f155}
  header h1{margin:0;font-size:22px;letter-spacing:.2px}
  header p{margin:3px 0 0;color:var(--muted);font-size:12.5px}

  .card{background:var(--panel);border:1px solid var(--line);border-radius:18px;
        padding:22px;margin-bottom:22px;backdrop-filter:blur(8px);
        box-shadow:0 10px 30px #00000040}
  .glow{position:relative;overflow:hidden}
  .glow::before{content:"";position:absolute;inset:-1px;border-radius:18px;padding:1px;
        background:linear-gradient(120deg,#6366f155,#22d3ee44,transparent);
        -webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);
        -webkit-mask-composite:xor;mask-composite:exclude;pointer-events:none}

  /* Dropzone */
  .drop{border:2px dashed var(--line);border-radius:16px;padding:30px 18px;text-align:center;
        cursor:pointer;transition:.18s;color:var(--muted);background:rgba(255,255,255,.02)}
  .drop:hover{border-color:var(--accent);color:var(--txt);transform:translateY(-1px)}
  .drop.over{border-color:var(--accent2);background:#22d3ee14;color:var(--txt)}
  .drop.set{border-style:solid;border-color:var(--ok);background:#22c55e12;color:var(--txt)}
  .drop .big{font-size:34px;line-height:1;margin-bottom:10px}
  .drop .ttl{font-weight:600;font-size:15px} .drop .fn{margin-top:9px;font-size:12.5px;color:#34d399;word-break:break-all}
  .drop input{display:none}

  label{display:block;font-size:11.5px;color:var(--muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:.4px}
  input[type=text]{background:#0a1024;border:1px solid var(--line);color:var(--txt);
        border-radius:10px;padding:11px 13px;width:100%;font-size:14px}
  .row{display:flex;gap:18px;flex-wrap:wrap;align-items:flex-end}
  button{border:0;border-radius:11px;padding:12px 22px;font-size:14px;font-weight:700;cursor:pointer;
        color:#fff;background:linear-gradient(135deg,var(--accent),#4f46e5);
        box-shadow:0 8px 22px #6366f155;transition:.15s}
  button:hover{filter:brightness(1.08);transform:translateY(-1px)}
  button:disabled{opacity:.55;cursor:wait}
  button.ghost{background:transparent;border:1px solid var(--line);color:var(--txt);box-shadow:none}

  /* Resumen: donut + stats */
  .resumen{display:grid;grid-template-columns:200px 1fr;gap:24px;align-items:center}
  @media(max-width:720px){.resumen{grid-template-columns:1fr}}
  .donut{width:180px;height:180px;border-radius:50%;margin:auto;position:relative;
         display:grid;place-items:center;transition:.6s}
  .donut::after{content:"";position:absolute;inset:18px;border-radius:50%;background:var(--bg2)}
  .donut .ct{position:relative;text-align:center;z-index:1}
  .donut .pct{font-size:34px;font-weight:800;line-height:1}
  .donut .lb{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
  .stats{display:flex;gap:14px;flex-wrap:wrap}
  .stat{flex:1;min-width:150px;background:rgba(255,255,255,.03);border:1px solid var(--line);
        border-radius:14px;padding:16px 18px}
  .stat .ic{font-size:18px} .stat .n{font-size:26px;font-weight:800;margin-top:4px}
  .stat .l{font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
  .stat.ok .n{color:#4ade80} .stat.bad .n{color:#f87171} .stat.gold .n{color:#fbbf24}

  /* Toolbar / chips */
  .toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:4px 0 14px}
  .chip{padding:7px 14px;border-radius:999px;font-size:12.5px;cursor:pointer;
        border:1px solid var(--line);color:var(--muted);background:transparent;transition:.15s}
  .chip:hover{color:var(--txt)} .chip.act{background:var(--accent);border-color:transparent;color:#fff}
  .spacer{flex:1}

  table{width:100%;border-collapse:collapse}
  th,td{padding:11px 12px;text-align:left;border-bottom:1px solid var(--line)}
  th{font-size:10.5px;text-transform:uppercase;color:var(--muted);letter-spacing:.5px;
     position:sticky;top:0;background:#0e1430}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  tbody tr{transition:.12s} tbody tr:hover{background:rgba(255,255,255,.04)}
  tr.dif td{background:#ef444410} tr.con td{background:#22c55e0d}
  .acr{font-weight:700}
  .grp td{background:#11183a;font-weight:800;font-size:12px;letter-spacing:.5px;color:#c7d2fe;
          text-transform:uppercase;border-top:1px solid var(--line)}
  .pill{padding:4px 11px;border-radius:999px;font-size:11px;font-weight:800;letter-spacing:.3px}
  .pill.con{background:#22c55e22;color:#4ade80;border:1px solid #22c55e55}
  .pill.dif{background:#ef444422;color:#f87171;border:1px solid #ef444455}
  .bar{height:7px;border-radius:6px;background:#ffffff14;overflow:hidden;margin-top:14px}
  .bar > i{display:block;height:100%;background:linear-gradient(90deg,var(--ok),#16a34a);transition:.6s}

  .hidden{display:none}
  .hist a{color:#a5b4fc;cursor:pointer;text-decoration:none;display:inline-block;margin:0 12px 8px 0}
  .hist a:hover{color:#fff}
  #msg{margin-top:12px;font-size:13px}
  .scroll{max-height:560px;overflow:auto;border-radius:12px;border:1px solid var(--line)}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">🤝</div>
    <div>
      <h1>Conciliacion BCE &times; MEF</h1>
      <p>Deuda Externa Publica &middot; cruce mensual por acreedor &middot; 6 conceptos de pago</p>
    </div>
  </header>

  <div class="card glow">
    <div class="drop" id="dzAmbos" onclick="document.getElementById('ambos').click()">
      <div class="big">📥</div>
      <div class="ttl">Arrastra aqui los <b>dos reportes</b> &mdash; BCE y MEF</div>
      <div class="muted" style="font-size:12.5px;margin-top:4px">o haz clic para elegirlos &middot; no importa el orden, la app reconoce cada uno</div>
      <div class="fn" id="fnAmbos"></div>
      <input type="file" id="ambos" accept=".xls,.xlsx" multiple>
    </div>
    <div class="row" style="margin-top:18px">
      <div style="max-width:200px;flex:1">
        <label>Periodo (AAAA-MM)</label>
        <input type="text" id="periodo" placeholder="2026-03">
      </div>
      <button id="btn" onclick="conciliar()">⚡ Cruzar reportes</button>
    </div>
    <div id="msg"></div>
  </div>

  <div class="card glow hidden" id="resCard">
    <div class="resumen">
      <div>
        <div class="donut" id="donut">
          <div class="ct"><div class="pct" id="pct">0%</div><div class="lb">Conciliado</div></div>
        </div>
      </div>
      <div>
        <div class="stats" id="stats"></div>
        <div class="bar"><i id="barFill" style="width:0%"></i></div>
        <div class="toolbar" style="margin-top:16px">
          <button class="chip act" data-f="all" onclick="setFiltro('all',this)">Todos</button>
          <button class="chip" data-f="dif" onclick="setFiltro('dif',this)">Solo diferencias</button>
          <button class="chip" data-f="con" onclick="setFiltro('con',this)">Solo conciliados</button>
          <span class="spacer"></span>
          <span class="muted" id="periodoLbl" style="font-size:12px"></span>
          <button class="ghost" onclick="exportar()">⬇ Exportar Excel</button>
        </div>
      </div>
    </div>
    <div class="scroll" style="margin-top:18px">
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
    <div id="hist" class="muted">Cargando&hellip;</div>
  </div>
</div>

<script>
let DATA=[], PERIODO="", FILTRO="all";
const fmt=n=>(n||0).toLocaleString("es-EC",{minimumFractionDigits:2,maximumFractionDigits:2});
function msg(t,err){const m=document.getElementById("msg");m.textContent=t;m.style.color=err?"#f87171":"#34d399";}

// Drag & drop (una zona, dos archivos, cualquier orden)
const dz=document.getElementById("dzAmbos"),input=document.getElementById("ambos"),fn=document.getElementById("fnAmbos");
const valido=f=>f&&/\.(xls|xlsx)$/i.test(f.name);
function mostrarAmbos(){const fs=[...input.files];
  if(fs.length){dz.classList.add("set");fn.innerHTML=fs.map(f=>"✓ "+f.name).join("<br>");}
  else{dz.classList.remove("set");fn.textContent="";}}
input.addEventListener("change",mostrarAmbos);
["dragenter","dragover"].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();e.stopPropagation();dz.classList.add("over");}));
["dragleave","drop"].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();e.stopPropagation();dz.classList.remove("over");}));
dz.addEventListener("drop",e=>{const fs=[...e.dataTransfer.files].filter(valido);
  if(!fs.length){msg("Solo se aceptan archivos .xls o .xlsx",true);return;}
  const dt=new DataTransfer();fs.slice(0,2).forEach(f=>dt.items.add(f));input.files=dt.files;mostrarAmbos();});

async function conciliar(){
  const fs=[...input.files];
  if(fs.length<2){msg("Carga los DOS reportes (BCE y MEF) en la zona de arriba.",true);return;}
  const fd=new FormData();fs.forEach(f=>fd.append("archivos",f));
  fd.append("periodo",document.getElementById("periodo").value.trim());
  const btn=document.getElementById("btn");btn.disabled=true;msg("Procesando…");
  try{
    const j=await (await fetch("/api/conciliar",{method:"POST",body:fd})).json();
    if(!j.ok){msg(j.error||"Error",true);btn.disabled=false;return;}
    DATA=j.registros;PERIODO=j.periodo;
    pintarResumen(j.total,j.conciliados,j.diferencias,j.total_diferencia);
    document.getElementById("periodoLbl").textContent="Periodo "+j.periodo+" · "+j.fecha;
    document.getElementById("resCard").classList.remove("hidden");
    render();cargarHistorial();
    let det="BCE = "+j.archivo_bce+"  ·  MEF = "+j.archivo_mef;
    if(j.avisos&&j.avisos.length)det+="  ⚠ "+j.avisos.join(" / ");
    msg((j.diferencias===0?"✅ Todo concilia. ":"")+ "Cruce listo ("+j.conciliados+" conciliados, "+j.diferencias+" con diferencia). "+det);
  }catch(e){msg(e.message,true);}
  btn.disabled=false;
}

function pintarResumen(total,con,dif,sumdif){
  const pct=total?Math.round(con*100/total):0;
  const color=pct>=100?"#22c55e":pct>=60?"#f59e0b":"#ef4444";
  const d=document.getElementById("donut");
  d.style.background=`conic-gradient(${color} ${pct*3.6}deg, #ffffff14 0deg)`;
  document.getElementById("pct").textContent=pct+"%";
  document.getElementById("barFill").style.width=pct+"%";
  document.getElementById("stats").innerHTML=
    `<div class="stat"><div class="ic">📊</div><div class="n">${total}</div><div class="l">Comparaciones</div></div>
     <div class="stat ok"><div class="ic">✅</div><div class="n">${con}</div><div class="l">Conciliados</div></div>
     <div class="stat bad"><div class="ic">⚠️</div><div class="n">${dif}</div><div class="l">Con diferencia</div></div>
     <div class="stat gold"><div class="ic">Σ</div><div class="n">${fmt(sumdif)}</div><div class="l">|Diferencia| USD</div></div>`;
}

function setFiltro(f,el){FILTRO=f;document.querySelectorAll(".chip").forEach(c=>c.classList.remove("act"));el.classList.add("act");render();}

function render(){
  const tb=document.getElementById("tbody");tb.innerHTML="";
  let rows=DATA;
  if(FILTRO==="dif")rows=DATA.filter(r=>r.estado==="DIFERENCIA");
  if(FILTRO==="con")rows=DATA.filter(r=>r.estado==="CONCILIADO");
  let actual=null;
  rows.forEach(r=>{
    if(r.acreedor!==actual){actual=r.acreedor;
      const g=document.createElement("tr");g.className="grp";
      g.innerHTML=`<td colspan="6">${r.acreedor}</td>`;tb.appendChild(g);}
    const tr=document.createElement("tr");
    tr.className=r.estado==="DIFERENCIA"?"dif":"con";
    tr.innerHTML=`<td class="acr"></td><td>${r.concepto}</td>
      <td class="num">${fmt(r.mef)}</td><td class="num">${fmt(r.bce)}</td>
      <td class="num">${fmt(r.diferencia)}</td>
      <td><span class="pill ${r.estado==='DIFERENCIA'?'dif':'con'}">${r.estado}</span></td>`;
    tb.appendChild(tr);
  });
  if(!rows.length)tb.innerHTML=`<tr><td colspan="6" class="muted" style="text-align:center;padding:26px">Sin filas para este filtro.</td></tr>`;
}

async function exportar(){
  if(!PERIODO)return;
  const r=await fetch("/api/exportar",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({periodo:PERIODO})});
  if((r.headers.get("content-type")||"").includes("application/json")){const j=await r.json();msg(j.error||"No se pudo exportar",true);return;}
  const b=await r.blob(),u=URL.createObjectURL(b),a=document.createElement("a");
  a.href=u;a.download="Conciliacion_BCE_MEF_"+PERIODO+".xlsx";a.click();URL.revokeObjectURL(u);
}

async function cargarHistorial(){
  const j=await (await fetch("/api/historial")).json();
  const h=document.getElementById("hist");
  if(!j.periodos||!j.periodos.length){h.textContent="Aun no hay conciliaciones.";return;}
  h.innerHTML=j.periodos.map(p=>`<a onclick="verPeriodo('${p.periodo}')">📅 ${p.periodo} <span class="muted">(${p.conciliados}✓/${p.diferencias}✗)</span></a>`).join("");
}

async function verPeriodo(p){
  const j=await (await fetch("/api/periodo/"+encodeURIComponent(p))).json();
  if(!j.ok)return;DATA=j.registros;PERIODO=p;
  const dif=DATA.filter(r=>r.estado==="DIFERENCIA").length,con=DATA.length-dif;
  const sum=DATA.filter(r=>r.estado==="DIFERENCIA").reduce((a,r)=>a+Math.abs(r.diferencia),0);
  pintarResumen(DATA.length,con,dif,sum);
  document.getElementById("periodoLbl").textContent="Periodo "+p+" (historial)";
  document.getElementById("resCard").classList.remove("hidden");
  render();window.scrollTo({top:0,behavior:"smooth"});
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
