# 📊 Conciliación BCE ↔ MEF · Deuda Externa Pública

Automatización del cruce mensual de **pagos de deuda externa pública** entre el
reporte del **BCE** (Banco Central) y el del **MEF** (Ministerio de Economía y
Finanzas). Por cada acreedor compara los seis conceptos de pago y marca qué
concilia y qué presenta diferencia.

> **Solo lee los reportes `.xls` — nunca modifica ni elimina los originales.**
> Los resultados y exportaciones se guardan aparte (BD local y carpeta propia).
>
> 🕒 Última actualización: **27/06/2026**. Este README se mantiene al día con cada cambio.

## 🚀 Cómo arrancar
- App web: `python conciliacion_app.py` → abre http://127.0.0.1:5001/
  (puerto **5001** para no chocar con el Panel SSFI, que usa el 5000).
- Script de consola: `python Conciliacion.py` (lee dos `.xls` y genera Excel + log).
- Dependencias: `pip install -r requirements.txt` → **flask, xlrd, openpyxl**.
- Si ves **"Failed to fetch"** = el servidor se cerró → vuelve a lanzar `python conciliacion_app.py`.

## 🧩 Conceptos del cruce (por acreedor)
| Concepto | MEF · hoja *Resumen* | BCE · hoja / columna |
|----------|----------------------|----------------------|
| **Desembolsos** | Col **C** | *Giros del Exterior* → **K** (Valor USD) |
| **Amortizaciones** | Col **D** | *Giros al Exterior* → **U** (Capital USD) |
| **Intereses** | Col **E** | *Giros al Exterior* → **V** (Interés USD) |
| **Comisiones** | Col **F** | *Giros al Exterior* → **W** (Comisión USD) |
| **Intereses Condonados** | Col **G** | *Giros al Exterior* → **Y** (Condonados USD) |
| **Interés por Mora** | Col **H** | *Giros al Exterior* → **X** (Interés Mora USD) |

## 🧠 Lógica de conciliación
- **Nivel del cruce = lo que el BCE desglosa.** El MEF reporta subtotales padre
  (`MULTILATERAL`, `INSTITUCIÓN FINANCIERA INTERNACIONAL`) y sus hijos; se concilia
  contra la fila que corresponde, **sin** sumar el padre (evita doble conteo).
- **Se agregan las transacciones del BCE** por agrupación y se enfrentan a la fila
  del MEF. El BCE usa nombre largo del prestamista (`"... - CAF"`) y el MEF el código
  corto (`CAF`): el mapeo está en `ALIAS_MEF` dentro de `Conciliacion.py`.
- **Sub-prestamistas** que el MEF agrupa: `KFW` y `EXIMBANK KOREA` → `GOBIERNOS`,
  `BEI` → `BANCOS`, `OTROS` (BCE) → `BANCOS`.
- **Estado**: `CONCILIADO` si |MEF − BCE| ≤ `TOLERANCIA` (0.50 USD), si no `DIFERENCIA`.
- Las diferencias reales suelen explicarse por las **notas del MEF** (p. ej. *"pago
  directo"*, diferencial cambiario) o por *timing* del mes.

## 🖥️ App web — qué hace
| Endpoint | Función |
|----------|---------|
| `GET /` | Panel de una sola página (estilo BCE oscuro): **arrastrar y soltar los dos `.xls` juntos** (la app reconoce solo cuál es BCE y cuál MEF por sus hojas), ver tabla con semáforo, filtrar "solo diferencias", exportar e historial |
| `POST /api/conciliar` | Recibe los dos archivos, ejecuta el cruce y guarda el periodo en SQLite |
| `POST /api/exportar` | Genera y descarga el Excel con semáforo del periodo |
| `GET /api/historial` | Lista los periodos conciliados y su resumen |
| `GET /api/periodo/<periodo>` | Devuelve el detalle guardado de un periodo |

## 📁 Dónde se guarda todo
Todo vive en la carpeta del proyecto (`/home/user/Projects`, tu repo **BryanAndresR23/Projects**):

| Ruta | Qué es | ¿Va a Git? |
|------|--------|-----------|
| `conciliacion_app.py` | Aplicación web (Flask) | ✅ Sí |
| `Conciliacion.py` | Lógica del cruce + script de consola | ✅ Sí |
| `Accounting Voucher.py` | Script de comprobantes contables/ACKs | ✅ Sí |
| `requirements.txt` | Dependencias | ✅ Sí |
| `README.md` | Este archivo | ✅ Sí |
| `conciliacion.db` | **Base de datos SQLite local** (historial de periodos) | 🚫 No (`.gitignore`) |
| `uploads_conciliacion/` | Copias de los `.xls` que cargas en la app | 🚫 No (`.gitignore`) |
| `reportes_conciliacion/` | Excels exportados (`Conciliacion_BCE_MEF_AAAA-MM.xlsx`) | 🚫 No (`.gitignore`) |
| `log_conciliacion.txt` | Log del script de consola | 🚫 No |

- **Base de datos:** `conciliacion.db` (SQLite, sin servidor, todo en la PC). Cada
  corrida **reemplaza** la del mismo periodo; el historial queda consultable.
- **Respaldo principal = Git.** El código se versiona en GitHub, rama
  `claude/brave-noether-o44tpf` de `BryanAndresR23/Projects`. Cada commit es un
  respaldo con fecha y descripción.
- Los datos operativos (BD, `.xls` cargados, exportaciones) **no** se suben a Git
  por privacidad; si quieres respaldarlos, cópialos a tu carpeta de red.

## ⚠️ Reglas de oro
- Nunca modificar/eliminar los reportes originales del BCE y MEF (solo leer/copiar).
- Si agregas o cambia el nombre de un acreedor, edita **solo** `ALIAS_MEF` en `Conciliacion.py`.
- La app es **local** (127.0.0.1): no expone datos a la red.
- Para respaldar el código: `git add -A && git commit -m "..." && git push`.
