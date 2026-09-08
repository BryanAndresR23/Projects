# Panel de Giros

Sistema de carga validada, **histórico de versiones** y **tablero analítico** para
**Giros AL** y **Giros DEL**.

Cada archivo que se carga queda registrado con su archivo original, el resultado
depurado, las validaciones aplicadas, el usuario, la fecha y un número de versión.
Solo una versión está publicada por módulo y período; todas las demás se conservan
y pueden consultarse o restaurarse en cualquier momento.

---

## 1. Instalación (una sola vez)

```bash
cd panel_giros
python -m pip install -r requirements.txt
```

## 2. Poner en marcha el panel

```bash
streamlit run Inicio.py
```

Se abre en el navegador (por defecto `http://localhost:8501`) con cuatro pantallas:

| Pantalla | Para qué sirve |
|---|---|
| **Inicio** | Qué versión está publicada en cada módulo y período, y últimos movimientos. |
| **Cargar archivo** | Subir el reporte, elegir módulo y período, ver el resultado de la validación. |
| **Tablero** | Analizar: se elige la métrica y el rubro, y los gráficos se redibujan. |
| **Histórico** | Consultar, comparar, descargar y restaurar cualquier versión anterior. |

Para probarlo sin datos reales:

```bash
python herramientas/generar_datos_demo.py
```

Genera seis archivos de ejemplo en `datos/demo/` (Giros AL y DEL de abril, mayo y
junio) con errores intencionales para ver las validaciones funcionando.

---

## 3. Cómo funciona la carga

1. **El validador elige el módulo** (Giros AL o Giros DEL) y el período. No se deduce
   solo: es una declaración responsable que queda registrada.
2. **El ETL comprueba que el archivo corresponda a esa selección.** Si el contenido
   dice lo contrario, la carga se **rechaza** y no llega al tablero.
3. **Se depura el archivo**: se localiza la fila de encabezados aunque haya títulos
   arriba, se descartan totales y filas inválidas, se normalizan fechas, montos,
   monedas y códigos, y se calcula el equivalente en USD.
4. **Se guarda todo** en `datos/cargas/<MODULO>/<PERIODO>/vNNN/`:
   `original_<archivo>`, `depurado.csv`, `descartes.csv` y `validaciones.json`.
5. **Se publica** en el tablero si no hubo errores bloqueantes.

### Estados de una carga

| Estado | Qué significa |
|---|---|
| VÁLIDA | Sin observaciones. Se publica. |
| CON ADVERTENCIAS | Se publica, pero hay puntos a revisar (filas descartadas, duplicados, fechas fuera de período). |
| RECHAZADA | No se publica. Queda en el histórico como evidencia del intento. |

### Reglas de validación

| Regla | Severidad | Qué revisa |
|---|---|---|
| `ESTRUCTURA_ENCABEZADO` | Error | Que exista una fila de encabezados reconocible. |
| `ESTRUCTURA_COLUMNAS` | Error | Que estén las columnas obligatorias (fecha, referencia, corresponsal, moneda, monto). |
| `MODULO_NO_COINCIDE` | Error | Que el contenido corresponda al módulo elegido. |
| `MODULO_MEZCLADO` | Advertencia | Que el archivo no mezcle giros AL y DEL. |
| `MODULO_NOMBRE_ARCHIVO` | Advertencia | Que el nombre del archivo no contradiga la selección. |
| `PERIODO_NO_COINCIDE` | Error | Que las fechas pertenezcan al período declarado. |
| `PERIODO_PARCIAL` | Advertencia | Filas fuera del período. |
| `TIPO_CAMBIO_FALTANTE` | Error | Moneda distinta de USD sin factor de conversión. |
| `FILAS_DESCARTADAS` | Advertencia | Filas sin fecha o sin monto utilizable. |
| `REFERENCIAS_DUPLICADAS` | Advertencia | Posible doble carga. |
| `MONTOS_NEGATIVOS` / `MONTOS_EN_CERO` | Advertencia | Reversos o registros incompletos. |
| `MONEDA_NO_CATALOGADA` | Advertencia | Monedas fuera del catálogo. |

---

## 4. El histórico

- Cada carga de un mismo módulo y período crea una **versión nueva** (`v1`, `v2`, ...).
- **Solo una versión está publicada** a la vez; la base lo garantiza con un índice único.
- Cualquier versión anterior se puede **restaurar** desde *Histórico -> Restaurar*,
  dejando constancia del motivo.
- Una versión rechazada **nunca** puede publicarse: hay que corregir el archivo y
  cargar una versión nueva.
- La pestaña **Comparar versiones** muestra, lado a lado, filas, operaciones,
  montos y observaciones de dos versiones.
- La **bitácora** registra cada carga, publicación y restauración con usuario y fecha.

---

## 5. El tablero

Funciona como un tablero de BI: nada es fijo.

- **Métricas**: n.º de operaciones, monto USD, promedio por operación y participación %.
- **Rubros** para abrir cualquier métrica: módulo (AL/DEL), corresponsal, área 750,
  deuda 771, TF/GS, mes, período, moneda, proceso, estado y beneficiario.
- **Tipos de gráfico**: barras, columnas, línea, área, torta y treemap.
- **Filtros combinables** en el panel lateral, incluidos rango de fechas y monto mínimo.
- **Matriz cruzada** (cualquier rubro contra cualquier otro) y **comparación AL vs DEL**.
- **Detalle de respaldo** siempre disponible, con descarga a Excel y CSV.

Todas las cifras provienen únicamente de las **versiones publicadas**.

---

## 6. Adaptar el sistema a los reportes reales

El ETL no exige nombres de columna exactos: usa el catálogo
`catalogos/columnas.yaml`, donde cada campo tiene una lista de alias. Si el reporte
llama a una columna de otra forma, se agrega el nombre real a la lista,
sin tocar el código.

```yaml
corresponsal:
  etiqueta: Corresponsal
  requerido: true
  alias:
    - CORRESPONSAL
    - BANCO CORRESPONSAL
    - NOMBRE DEL BANCO      # <- nuevo alias
```

El mismo archivo define los **marcadores de módulo** (cómo se reconoce un giro AL de
uno DEL) y las **monedas** aceptadas.

`catalogos/tipos_cambio.yaml` guarda los tipos de cambio a USD por período, y solo se
usa cuando el archivo no trae ni columna de monto en USD ni tipo de cambio propio.

---

## 7. Uso por línea de comandos

Pensado para automatizar cargas (por ejemplo, desde el futuro bot de descarga):

```bash
python -m nucleo.cli cargar --archivo giros_al_junio.xlsx --modulo GIROS_AL --periodo 2026-06
python -m nucleo.cli historico --modulo GIROS_AL
python -m nucleo.cli publicar --modulo GIROS_AL --periodo 2026-06 --version 1 --motivo "reverso"
python -m nucleo.cli resumen --periodo 2026-06 --dimension corresponsal
```

`cargar` devuelve código de salida `1` si la carga fue rechazada, para encadenarlo
en un script.

---

## 8. Dónde se guardan los datos

Por defecto en `panel_giros/datos/`:

```
datos/
├── giros.db                     base SQLite (histórico, validaciones, operaciones, bitácora)
└── cargas/
    └── GIROS_AL/2026-06/v001/
        ├── original_Giros_AL_2026-06.xlsx
        ├── depurado.csv
        ├── descartes.csv
        └── validaciones.json
```

Para dejarlo en una carpeta compartida (recomendado si lo usa más de una persona),
se define la variable de entorno antes de arrancar:

```
set GIROS_DIR_DATOS=Z:\GISI\SSFI\PANEL_GIROS\datos     (Windows)
export GIROS_DIR_DATOS=/ruta/compartida/datos          (Linux / Mac)
```

`GIROS_USUARIO` permite fijar el nombre que se registra en el histórico; si no se
define, se toma el usuario del sistema operativo.

---

## 9. Estructura del proyecto

```
panel_giros/
├── Inicio.py                  pantalla de inicio (punto de entrada)
├── pages/
│   ├── 1_Cargar_archivo.py
│   ├── 2_Tablero.py
│   └── 3_Historico.py
├── nucleo/
│   ├── config.py              rutas, módulos, períodos, estados
│   ├── catalogo.py            lectura de los catálogos configurables
│   ├── etl.py                 lectura, mapeo de columnas y depuración
│   ├── validaciones.py        reglas de validación
│   ├── repositorio.py         histórico, versiones, publicación y restauración
│   ├── metricas.py            medidas, dimensiones y agregaciones
│   ├── bd.py                  esquema SQLite
│   ├── ui.py                  piezas compartidas de la interfaz
│   └── cli.py                 línea de comandos
├── catalogos/
│   ├── columnas.yaml
│   └── tipos_cambio.yaml
├── herramientas/
│   └── generar_datos_demo.py
└── tests/                     58 pruebas automáticas
```

Para ejecutar las pruebas:

```bash
python -m pytest tests/ -q
```

---

## 10. Pendiente

El **bot de descarga automática** del sistema externo de reportes todavía no está
construido: falta definir cómo se autentica ese sistema. La recomendación es una
cuenta institucional autorizada con las credenciales en un almacén seguro
(nunca dentro del código ni en archivos del repositorio). Una vez definido, el bot
puede reutilizar `python -m nucleo.cli cargar` para registrar lo descargado.
