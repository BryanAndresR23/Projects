# Panel de Giros (R / Shiny)

Sistema de carga validada, **histórico de versiones** y **tablero analítico** para
**Giros AL** y **Giros DEL**, pensado para publicarse en un servidor Shiny y que
varias personas lo usen desde el navegador.

Cada archivo que se carga queda registrado con su archivo original, el resultado
depurado, las validaciones aplicadas, el usuario, la fecha y un número de versión.
Solo una versión está publicada por módulo y período; todas las demás se conservan
y pueden consultarse o restaurarse en cualquier momento.

---

## 1. Probarlo en su computador

```r
# Desde RStudio, con el proyecto abierto en esta carpeta:
source("deploy/instalar_dependencias.R")   # solo la primera vez
shiny::runApp()
```

Para verlo con datos de ejemplo antes de tener archivos reales:

```bash
Rscript herramientas/generar_datos_demo.R
```

Genera seis archivos en `datos/demo/` (Giros AL y DEL de abril, mayo y junio) con
errores intencionales, para ver las validaciones funcionando.

## 2. Publicarlo en el servidor

```bash
# 1. Copiar la aplicación
sudo cp -r panel_giros_r /srv/shiny-server/panel_giros
sudo chown -R shiny:shiny /srv/shiny-server/panel_giros

# 2. Crear la carpeta de datos FUERA de la aplicación
sudo mkdir -p /var/lib/panel_giros/datos
sudo chown -R shiny:shiny /var/lib/panel_giros

# 3. Definir las variables de entorno
sudo cp /srv/shiny-server/panel_giros/deploy/Renviron.site /etc/R/Renviron.site

# 4. Configurar Shiny Server (ver deploy/shiny-server.conf) y reiniciar
sudo systemctl restart shiny-server
```

Tres cosas que conviene revisar en el servidor:

| Punto | Por qué importa |
|---|---|
| **`GIROS_DIR_DATOS`** | Si no se define, el panel intenta escribir dentro de la aplicación, que suele ser de solo lectura. La pantalla avisa en rojo si la carpeta no es escribible. |
| **Identidad del usuario** | El histórico registra quién cargó cada archivo. El panel toma `session$user` y, si no existe, la cabecera `X-Forwarded-User`. Sin autenticación delante, todas las cargas quedan a nombre del usuario del servicio. |
| **Locale UTF-8** | Los reportes traen tildes y la ñ. `deploy/Renviron.site` fija `LANG`; si el servidor no tiene el locale, generarlo con `locale-gen es_EC.UTF-8`. |

La base es SQLite en modo WAL con espera de 15 segundos: varias personas pueden
consultar el tablero mientras otra carga un archivo. Es adecuada para el volumen
de este panel (decenas de miles de operaciones por período).

---

## 3. Cómo funciona la carga

1. **El validador elige el módulo** (Giros AL o Giros DEL) y el período. No se
   deduce solo: es una declaración responsable que queda registrada.
2. **El ETL comprueba que el archivo corresponda a esa selección.** Si el
   contenido dice lo contrario, la carga se **rechaza** y no llega al tablero.
3. **Se depura el archivo**: se localiza la fila de encabezados aunque haya
   títulos arriba, se descartan totales y filas inválidas, se normalizan fechas,
   montos, monedas y códigos, y se calcula el equivalente en USD.
4. **Se guarda todo** en `<GIROS_DIR_DATOS>/cargas/<MODULO>/<PERIODO>/vNNN/`:
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
- **Solo una versión está publicada** a la vez; la base lo garantiza con un
  índice único, no con una convención.
- Cualquier versión anterior se puede **restaurar** desde *Histórico → Restaurar*,
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
`catalogos/columnas.yaml`, donde cada campo tiene una lista de alias. Si el
reporte llama a una columna de otra forma, se agrega el nombre real a la lista,
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

El mismo archivo define los **marcadores de módulo** (cómo se reconoce un giro AL
de uno DEL) y las **monedas** aceptadas.

`catalogos/tipos_cambio.yaml` guarda los tipos de cambio a USD por período, y solo
se usa cuando el archivo no trae ni columna de monto en USD ni tipo de cambio propio.

---

## 7. Uso por línea de comandos

Pensado para automatizar cargas (por ejemplo, desde el futuro bot de descarga):

```bash
Rscript herramientas/cli.R cargar --archivo giros_al_junio.xlsx --modulo GIROS_AL --periodo 2026-06
Rscript herramientas/cli.R historico --modulo GIROS_AL
Rscript herramientas/cli.R publicar --modulo GIROS_AL --periodo 2026-06 --version 1 --motivo "reverso"
Rscript herramientas/cli.R resumen --periodo 2026-06 --dimension corresponsal
```

`cargar` devuelve código de salida `1` si la carga fue rechazada, para encadenarlo
en un script o en una tarea programada.

---

## 8. Estructura del proyecto

```
panel_giros_r/
├── app.R                      aplicación Shiny (punto de entrada)
├── modulos/
│   ├── mod_inicio.R           pantalla de inicio
│   ├── mod_cargar.R           carga y validación
│   ├── mod_tablero.R          tablero analítico
│   └── mod_historico.R        histórico, comparación y restauración
├── nucleo/
│   ├── config.R               rutas, módulos, períodos, estados, usuario
│   ├── catalogo.R             lectura de los catálogos configurables
│   ├── etl.R                  lectura, mapeo de columnas y depuración
│   ├── validaciones.R         reglas de validación
│   ├── repositorio.R          histórico, versiones, publicación y restauración
│   ├── metricas.R             medidas, dimensiones y agregaciones
│   ├── bd.R                   esquema SQLite
│   └── ui_utiles.R            tema, tarjetas, gráficos y tablas
├── catalogos/
│   ├── columnas.yaml
│   └── tipos_cambio.yaml
├── herramientas/
│   ├── cli.R                  línea de comandos
│   └── generar_datos_demo.R
├── deploy/
│   ├── instalar_dependencias.R
│   ├── shiny-server.conf
│   └── Renviron.site
└── tests/                     119 pruebas automáticas
```

El núcleo está separado de la interfaz a propósito: se puede probar y automatizar
sin levantar Shiny.

Para ejecutar las pruebas:

```bash
Rscript tests/testthat.R
```

---

## 9. Pendiente

El **bot de descarga automática** del sistema externo de reportes todavía no está
construido: falta definir cómo se autentica ese sistema. La recomendación es una
cuenta institucional autorizada con las credenciales en un almacén seguro
(nunca dentro del código ni en archivos del repositorio). Una vez definido, el bot
puede reutilizar `Rscript herramientas/cli.R cargar` para registrar lo descargado.
