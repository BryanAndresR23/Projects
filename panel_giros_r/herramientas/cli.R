#!/usr/bin/env Rscript
# ---------------------------------------------------------------------------
# Línea de comandos del panel: automatiza cargas sin abrir la web.
#
#   Rscript herramientas/cli.R cargar --archivo giros.xlsx --modulo GIROS_AL --periodo 2026-06
#   Rscript herramientas/cli.R historico --modulo GIROS_AL
#   Rscript herramientas/cli.R publicar --modulo GIROS_AL --periodo 2026-06 --version 2
#   Rscript herramientas/cli.R resumen --periodo 2026-06 --dimension corresponsal
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

#' Lee argumentos con la forma --clave valor.
leer_argumentos <- function(argumentos) {
  valores <- list()
  i <- 1
  while (i <= length(argumentos)) {
    actual <- argumentos[i]
    if (startsWith(actual, "--")) {
      clave <- sub("^--", "", actual)
      siguiente <- if (i < length(argumentos)) argumentos[i + 1] else NA_character_
      if (is.na(siguiente) || startsWith(siguiente, "--")) {
        valores[[clave]] <- TRUE
        i <- i + 1
      } else {
        valores[[clave]] <- siguiente
        i <- i + 2
      }
    } else {
      i <- i + 1
    }
  }
  valores
}

ayuda <- function() {
  cat("Uso: Rscript herramientas/cli.R <comando> [opciones]\n\n")
  cat("Comandos:\n")
  cat("  cargar     --archivo RUTA --modulo GIROS_AL|GIROS_DEL --periodo AAAA-MM\n")
  cat("             [--usuario X] [--comentario \"...\"] [--hoja NOMBRE] [--sin-publicar]\n")
  cat("  historico  [--modulo M] [--periodo P]\n")
  cat("  publicar   --modulo M --periodo P --version N [--usuario X] [--motivo \"...\"]\n")
  cat("  resumen    [--modulo M] [--periodo P] [--dimension corresponsal] [--top 10]\n")
  invisible(NULL)
}

cmd_cargar <- function(opciones) {
  obligatorias <- c("archivo", "modulo", "periodo")
  faltan <- setdiff(obligatorias, names(opciones))
  if (length(faltan)) stop("Faltan opciones: --", paste(faltan, collapse = " --"))

  resultado <- registrar_carga(
    opciones$archivo, modulo = opciones$modulo, periodo = opciones$periodo,
    usuario = opciones$usuario, comentario = if (is.null(opciones$comentario)) "" else opciones$comentario,
    hoja = opciones$hoja, activar = !isTRUE(opciones$`sin-publicar`)
  )
  cat(sprintf("Versión v%d · estado %s · %s\n", resultado$version, resultado$estado,
              if (resultado$activa) "publicada" else "no publicada"))
  cat(sprintf("  operaciones: %d | descartes: %d | monto USD: %s\n",
              resultado$etl$filas_depuradas, resultado$etl$filas_descartadas,
              formatC(resultado$etl$monto_usd, format = "f", digits = 2, big.mark = ",")))
  for (i in seq_len(nrow(resultado$hallazgos))) {
    cat(sprintf("  [%-11s] %s: %s\n", resultado$hallazgos$severidad[i],
                resultado$hallazgos$regla[i], resultado$hallazgos$mensaje[i]))
  }
  if (nzchar(resultado$motivo_no_activada)) cat("  →", resultado$motivo_no_activada, "\n")
  if (identical(resultado$estado, ESTADO_RECHAZADA)) 1L else 0L
}

cmd_historico <- function(opciones) {
  historico <- listar_cargas(modulo = opciones$modulo, periodo = opciones$periodo)
  if (!nrow(historico)) {
    cat("Sin cargas registradas.\n")
    return(0L)
  }
  columnas <- c("modulo", "periodo", "version", "activa", "estado", "usuario",
                "fecha_carga", "operaciones", "monto_usd", "nombre_original")
  print(historico[, columnas], row.names = FALSE)
  0L
}

cmd_publicar <- function(opciones) {
  obligatorias <- c("modulo", "periodo", "version")
  faltan <- setdiff(obligatorias, names(opciones))
  if (length(faltan)) stop("Faltan opciones: --", paste(faltan, collapse = " --"))

  resultado <- activar_version(opciones$modulo, opciones$periodo, as.integer(opciones$version),
                               usuario = opciones$usuario,
                               motivo = if (is.null(opciones$motivo)) "" else opciones$motivo)
  cat(sprintf("%s: %s %s → v%d (antes v%s)\n", resultado$accion,
              etiqueta_modulo(opciones$modulo), etiqueta_periodo(opciones$periodo),
              resultado$version,
              if (is.na(resultado$version_anterior)) "—" else resultado$version_anterior))
  0L
}

cmd_resumen <- function(opciones) {
  datos <- operaciones(solo_activas = TRUE)
  if (!is.null(opciones$modulo)) datos <- datos[datos$modulo == opciones$modulo, , drop = FALSE]
  if (!is.null(opciones$periodo)) datos <- datos[datos$periodo == opciones$periodo, , drop = FALSE]
  if (!nrow(datos)) {
    cat("Sin operaciones publicadas para ese filtro.\n")
    return(0L)
  }
  dimension <- if (is.null(opciones$dimension)) "corresponsal" else opciones$dimension
  top <- if (is.null(opciones$top)) 10 else as.integer(opciones$top)

  indicadores <- kpis(datos)
  cat(sprintf("Operaciones: %s\n", formatear_numero(indicadores$operaciones)))
  cat(sprintf("Monto USD:   %s\n", formatC(indicadores$monto_usd, format = "f", digits = 2, big.mark = ",")))
  cat(sprintf("Promedio:    %s\n", formatC(indicadores$promedio_usd, format = "f", digits = 2, big.mark = ",")))
  cat(sprintf("\nPor %s:\n", tolower(unname(DIMENSIONES[dimension]))))
  print(ranking(datos, dimension, top = top), row.names = FALSE)
  0L
}

main <- function() {
  argumentos <- commandArgs(trailingOnly = TRUE)
  if (!length(argumentos) || argumentos[1] %in% c("-h", "--help", "ayuda")) {
    ayuda()
    return(0L)
  }
  comando <- argumentos[1]
  opciones <- leer_argumentos(argumentos[-1])
  funciones <- list(cargar = cmd_cargar, historico = cmd_historico,
                    publicar = cmd_publicar, resumen = cmd_resumen)
  if (is.null(funciones[[comando]])) {
    cat("Comando desconocido:", comando, "\n\n")
    ayuda()
    return(2L)
  }
  funciones[[comando]](opciones)
}

quit(status = main(), save = "no")
