#!/usr/bin/env Rscript
# ---------------------------------------------------------------------------
# Genera archivos de ejemplo para probar el panel sin datos reales.
#
#   Rscript herramientas/generar_datos_demo.R [carpeta_destino]
#
# Crea un Excel por módulo y período con la estructura que espera el ETL,
# incluyendo a propósito filas imperfectas (totales, fechas fuera de período,
# montos vacíos) para que se vean las validaciones en acción.
# ---------------------------------------------------------------------------

if (!grepl("UTF-8", Sys.getlocale("LC_CTYPE"), ignore.case = TRUE)) {
  for (locale in c("es_EC.UTF-8", "es_ES.UTF-8", "en_US.UTF-8", "C.UTF-8", "C.utf8")) {
    if (nzchar(suppressWarnings(Sys.setlocale("LC_CTYPE", locale)))) break
  }
}

RAIZ <- local({
  argumentos <- commandArgs(trailingOnly = FALSE)
  ruta <- sub("^--file=", "", argumentos[grepl("^--file=", argumentos)])
  if (length(ruta)) normalizePath(file.path(dirname(ruta), "..")) else normalizePath(".")
})
for (archivo in list.files(file.path(RAIZ, "nucleo"), pattern = "\\.R$", full.names = TRUE)) {
  source(archivo, encoding = "UTF-8")
}

CORRESPONSALES <- c("CITIBANK N.A.", "JP MORGAN CHASE", "BANCO SANTANDER", "BBVA",
                    "DEUTSCHE BANK", "BANK OF AMERICA", "COMMERZBANK", "WELLS FARGO")
AREAS_750 <- c("750-01", "750-02", "750-03", "750-04")
DEUDAS_771 <- c("771-100", "771-200", "771-300", "771-400")
PROCESOS <- c("PAGO DEUDA EXTERNA", "DESEMBOLSO", "COMISIONES", "INTERESES", "AMORTIZACION")
MONEDAS <- list(c("USD", 1), c("EUR", 1.08), c("GBP", 1.27), c("JPY", 0.0064))

fila_demo <- function(indice, modulo, anio, mes) {
  moneda <- if (stats::runif(1) > 0.55) MONEDAS[[sample.int(length(MONEDAS), 1)]] else c("USD", 1)
  factor <- as.numeric(moneda[2])
  monto <- round(stats::runif(1, 5000, 4500000), 2)
  prefijo <- if (stats::runif(1) > 0.4) "TF" else "GS"
  data.frame(
    `FECHA DE OPERACIÓN` = format(as.Date(sprintf("%04d-%02d-%02d", anio, mes, sample.int(28, 1))), "%d/%m/%Y"),
    `N° OPERACIÓN` = sprintf("%s-01-%.0f", prefijo, 7712600000 + indice),
    SENTIDO = unname(MODULOS[modulo]),
    `BANCO CORRESPONSAL` = sample(CORRESPONSALES, 1),
    `ÁREA 750` = sample(AREAS_750, 1),
    `DEUDA 771` = sample(DEUDAS_771, 1),
    `TIPO DE MENSAJE` = prefijo,
    PROCESO = sample(PROCESOS, 1),
    MONEDA = moneda[1],
    MONTO = monto,
    `TIPO DE CAMBIO` = factor,
    `MONTO USD` = round(monto * factor, 2),
    BENEFICIARIO = sprintf("ACREEDOR %d", sample(100:999, 1)),
    ESTADO = sample(c("PROCESADO", "PROCESADO", "PROCESADO", "PENDIENTE"), 1),
    check.names = FALSE, stringsAsFactors = FALSE
  )
}

generar <- function(modulo, anio, mes, filas, semilla) {
  set.seed(semilla)
  tabla <- do.call(rbind, lapply(seq_len(filas), function(i) fila_demo(i, modulo, anio, mes)))

  # Ruido deliberado para ejercitar las validaciones.
  fuera <- fila_demo(9001, modulo, anio, mes)
  fuera[["FECHA DE OPERACIÓN"]] <- format(as.Date(sprintf("%04d-%02d-01", anio, mes)) - 20, "%d/%m/%Y")
  sin_monto <- fila_demo(9002, modulo, anio, mes)
  sin_monto[["MONTO"]] <- NA
  sin_monto[["MONTO USD"]] <- NA
  sin_corresponsal <- fila_demo(9003, modulo, anio, mes)
  sin_corresponsal[["BANCO CORRESPONSAL"]] <- ""

  tabla <- rbind(tabla, fuera, sin_monto, sin_corresponsal)
  total <- tabla[1, ]
  total[1, ] <- ""
  total[["FECHA DE OPERACIÓN"]] <- "TOTAL"
  total[["MONTO USD"]] <- sum(tabla[["MONTO USD"]], na.rm = TRUE)
  rbind(tabla, total)
}

escribir <- function(destino, modulo, anio, mes, filas, semilla) {
  tabla <- generar(modulo, anio, mes, filas, semilla)
  periodo_actual <- periodo(anio, mes)
  ruta <- file.path(destino, sprintf("%s_%s.xlsx", gsub(" ", "_", MODULOS[modulo]), periodo_actual))

  libro <- openxlsx::createWorkbook()
  openxlsx::addWorksheet(libro, "Hoja1")
  # Dos filas de título antes del encabezado: el ETL debe encontrarlo solo.
  openxlsx::writeData(libro, "Hoja1",
                      data.frame(c(sprintf("REPORTE DE %s", toupper(MODULOS[modulo])),
                                   etiqueta_periodo(periodo_actual)), stringsAsFactors = FALSE),
                      startRow = 1, colNames = FALSE)
  openxlsx::writeData(libro, "Hoja1", tabla, startRow = 4, colNames = TRUE)
  openxlsx::saveWorkbook(libro, ruta, overwrite = TRUE)
  ruta
}

main <- function() {
  argumentos <- commandArgs(trailingOnly = TRUE)
  destino <- if (length(argumentos)) argumentos[1] else file.path(dir_datos(), "demo")
  dir.create(destino, recursive = TRUE, showWarnings = FALSE)

  generadas <- character(0)
  periodos <- list(c(2026, 4), c(2026, 5), c(2026, 6))
  for (i in seq_along(periodos)) {
    for (modulo in names(MODULOS)) {
      generadas <- c(generadas, escribir(destino, modulo, periodos[[i]][1], periodos[[i]][2],
                                         filas = 120, semilla = i * 10 + nchar(modulo)))
    }
  }
  cat("Archivos de ejemplo generados en", destino, ":\n")
  for (ruta in generadas) cat("  -", basename(ruta), "\n")
}

main()
