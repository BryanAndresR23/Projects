# ---------------------------------------------------------------------------
# Histórico de cargas: versionado, publicación y restauración.
# ---------------------------------------------------------------------------

COLUMNAS_OPERACION <- c("modulo", "periodo", "fecha", "anio", "mes", "nombre_mes", "referencia",
                        "corresponsal", "area_750", "deuda_771", "tipo_mensaje", "proceso",
                        "moneda", "monto", "tipo_cambio", "monto_usd", "beneficiario", "estado")

#' Procesa un archivo y lo guarda como una nueva versión del histórico.
#'
#' La carga se conserva siempre —incluso si es rechazada— junto con el archivo
#' original, el depurado y sus validaciones. Solo se publica (queda activa) si
#' no tiene errores y `activar` es TRUE.
registrar_carga <- function(ruta, modulo, periodo, nombre = basename(ruta),
                            usuario = NULL, comentario = "", hoja = NULL, activar = TRUE) {
  if (!modulo %in% names(MODULOS)) {
    stop("Módulo desconocido: ", modulo, ". Use uno de: ", paste(names(MODULOS), collapse = ", "))
  }
  partes_periodo(periodo)  # valida el formato AAAA-MM
  if (!file.exists(ruta)) stop("No se encontró el archivo: ", ruta)

  usuario <- if (is.null(usuario)) usuario_actual() else usuario
  huella <- digest::digest(file = ruta, algo = "sha256")

  resultado_etl <- procesar(ruta, modulo = modulo, periodo = periodo, nombre = nombre, hoja = hoja)
  hallazgos <- validar(resultado_etl, modulo = modulo, periodo = periodo, nombre_archivo = nombre)
  estado <- estado_de(hallazgos)
  conteo <- resumen_hallazgos(hallazgos)

  carpeta <- NULL
  registro <- con_bd_transaccion(function(con) {
    duplicado <- .buscar_duplicado(con, modulo, periodo, huella)
    version <- .siguiente_version(con, modulo, periodo)
    carpeta <<- .carpeta_version(modulo, periodo, version)
    dir.create(carpeta, recursive = TRUE, showWarnings = FALSE)

    ruta_original <- file.path(carpeta, paste0("original_", .nombre_seguro(nombre)))
    file.copy(ruta, ruta_original, overwrite = TRUE)
    ruta_depurado <- file.path(carpeta, "depurado.csv")
    ruta_descartes <- file.path(carpeta, "descartes.csv")
    .guardar_csv(resultado_etl$datos, ruta_depurado)
    .guardar_csv(resultado_etl$descartes, ruta_descartes)
    .guardar_validaciones(file.path(carpeta, "validaciones.json"), hallazgos, resultado_etl)

    DBI::dbExecute(
      con,
      "INSERT INTO cargas (
          modulo, periodo, version, activa, estado, usuario, fecha_carga,
          nombre_original, hash_original, ruta_original, ruta_depurado,
          ruta_descartes, hoja, fila_encabezado, filas_origen, filas_depuradas,
          filas_descartadas, operaciones, monto_usd, errores, advertencias, comentario
       ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      params = list(
        modulo, periodo, version, estado, usuario,
        format(Sys.time(), "%Y-%m-%dT%H:%M:%S"),
        nombre, huella, ruta_original, ruta_depurado, ruta_descartes,
        .nulo_si_na(resultado_etl$hoja), .nulo_si_na(resultado_etl$fila_encabezado),
        resultado_etl$filas_origen, resultado_etl$filas_depuradas,
        resultado_etl$filas_descartadas, resultado_etl$filas_depuradas,
        resultado_etl$monto_usd, conteo$errores, conteo$advertencias, comentario
      )
    )
    carga_id <- as.integer(DBI::dbGetQuery(con, "SELECT last_insert_rowid() AS id")$id[1])

    if (nrow(hallazgos)) {
      validaciones <- data.frame(
        carga_id = carga_id,
        regla = hallazgos$regla,
        severidad = hallazgos$severidad,
        mensaje = hallazgos$mensaje,
        filas_afectadas = hallazgos$filas_afectadas,
        detalle = hallazgos$detalle,
        stringsAsFactors = FALSE
      )
      DBI::dbAppendTable(con, "validaciones", validaciones)
    }
    .insertar_operaciones(con, carga_id, modulo, periodo, resultado_etl$datos)

    activada <- FALSE
    motivo <- ""
    if (identical(estado, ESTADO_RECHAZADA)) {
      motivo <- "La carga tiene errores bloqueantes: se conserva en el histórico pero no se publica."
    } else if (!activar) {
      motivo <- "El validador pidió guardarla sin publicar."
    } else {
      .activar(con, carga_id, modulo, periodo)
      activada <- TRUE
    }

    registrar_bitacora(
      con, usuario = usuario, accion = "CARGA", modulo = modulo, periodo = periodo,
      version = version, carga_id = carga_id,
      detalle = as.character(jsonlite::toJSON(list(
        archivo = nombre, estado = estado, activada = activada,
        operaciones = resultado_etl$filas_depuradas,
        monto_usd = round(resultado_etl$monto_usd, 2),
        duplicado_de = duplicado
      ), auto_unbox = TRUE, null = "null"))
    )

    list(carga_id = carga_id, version = version, activa = activada,
         motivo_no_activada = motivo, duplicado_de = duplicado)
  })

  c(registro, list(
    modulo = modulo, periodo = periodo, estado = estado, hallazgos = hallazgos,
    etl = resultado_etl, carpeta = carpeta,
    publicable = !identical(estado, ESTADO_RECHAZADA)
  ))
}

#' Publica (o restaura) una versión concreta del histórico.
activar_version <- function(modulo, periodo, version, usuario = NULL, motivo = "") {
  usuario <- if (is.null(usuario)) usuario_actual() else usuario
  con_bd_transaccion(function(con) {
    fila <- DBI::dbGetQuery(
      con, "SELECT * FROM cargas WHERE modulo = ? AND periodo = ? AND version = ?",
      params = list(modulo, periodo, as.integer(version))
    )
    if (!nrow(fila)) {
      stop(sprintf("No existe la versión %s de %s %s.", version,
                   etiqueta_modulo(modulo), etiqueta_periodo(periodo)))
    }
    if (identical(fila$estado[1], ESTADO_RECHAZADA)) {
      stop(paste("Esa versión fue rechazada por validación y no puede publicarse.",
                 "Corrija el archivo y cargue una nueva versión."))
    }

    anterior <- DBI::dbGetQuery(
      con, "SELECT version FROM cargas WHERE modulo = ? AND periodo = ? AND activa = 1",
      params = list(modulo, periodo)
    )
    version_anterior <- if (nrow(anterior)) as.integer(anterior$version[1]) else NA_integer_
    .activar(con, as.integer(fila$id[1]), modulo, periodo)

    accion <- if (!is.na(version_anterior) && version_anterior != version) "RESTAURACION" else "PUBLICACION"
    registrar_bitacora(
      con, usuario = usuario, accion = accion, modulo = modulo, periodo = periodo,
      version = as.integer(version), carga_id = as.integer(fila$id[1]),
      detalle = as.character(jsonlite::toJSON(
        list(version_anterior = version_anterior, motivo = motivo),
        auto_unbox = TRUE, null = "null"
      ))
    )

    list(carga_id = as.integer(fila$id[1]), modulo = modulo, periodo = periodo,
         version = as.integer(version), version_anterior = version_anterior, accion = accion)
  })
}

#' Deja una sola versión activa por módulo y período.
.activar <- function(con, carga_id, modulo, periodo) {
  DBI::dbExecute(con, "UPDATE cargas SET activa = 0 WHERE modulo = ? AND periodo = ?",
                 params = list(modulo, periodo))
  DBI::dbExecute(con, "UPDATE cargas SET activa = 1 WHERE id = ?", params = list(carga_id))
}

# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------

listar_cargas <- function(modulo = NULL, periodo = NULL, solo_activas = FALSE) {
  condiciones <- character(0)
  parametros <- list()
  if (!is.null(modulo)) { condiciones <- c(condiciones, "modulo = ?"); parametros <- c(parametros, modulo) }
  if (!is.null(periodo)) { condiciones <- c(condiciones, "periodo = ?"); parametros <- c(parametros, periodo) }
  if (solo_activas) condiciones <- c(condiciones, "activa = 1")
  donde <- if (length(condiciones)) paste("WHERE", paste(condiciones, collapse = " AND ")) else ""

  con_bd(function(con) {
    DBI::dbGetQuery(
      con,
      paste("SELECT * FROM cargas", donde, "ORDER BY modulo, periodo DESC, version DESC"),
      params = if (length(parametros)) parametros else NULL
    )
  })
}

obtener_carga <- function(carga_id) {
  fila <- con_bd(function(con) {
    DBI::dbGetQuery(con, "SELECT * FROM cargas WHERE id = ?", params = list(as.integer(carga_id)))
  })
  if (nrow(fila)) as.list(fila[1, ]) else NULL
}

carga_activa <- function(modulo, periodo) {
  fila <- con_bd(function(con) {
    DBI::dbGetQuery(con, "SELECT * FROM cargas WHERE modulo = ? AND periodo = ? AND activa = 1",
                    params = list(modulo, periodo))
  })
  if (nrow(fila)) as.list(fila[1, ]) else NULL
}

validaciones_de <- function(carga_id) {
  con_bd(function(con) {
    DBI::dbGetQuery(
      con,
      "SELECT regla, severidad, mensaje, filas_afectadas, detalle
         FROM validaciones WHERE carga_id = ?
        ORDER BY CASE severidad WHEN 'ERROR' THEN 0 WHEN 'ADVERTENCIA' THEN 1 ELSE 2 END, regla",
      params = list(as.integer(carga_id))
    )
  })
}

#' Hechos para el tablero: por defecto, solo los de las versiones publicadas.
operaciones <- function(solo_activas = TRUE, cargas = NULL) {
  consulta <- "SELECT o.* FROM operaciones o JOIN cargas c ON c.id = o.carga_id"
  condiciones <- character(0)
  parametros <- list()
  if (solo_activas) condiciones <- c(condiciones, "c.activa = 1")
  if (!is.null(cargas) && length(cargas)) {
    condiciones <- c(condiciones, paste0("o.carga_id IN (", paste(rep("?", length(cargas)), collapse = ","), ")"))
    parametros <- c(parametros, as.list(as.integer(cargas)))
  }
  if (length(condiciones)) consulta <- paste(consulta, "WHERE", paste(condiciones, collapse = " AND "))

  datos <- con_bd(function(con) {
    DBI::dbGetQuery(con, consulta, params = if (length(parametros)) parametros else NULL)
  })
  if (!nrow(datos)) {
    vacio <- data.frame(id = integer(0), carga_id = integer(0), stringsAsFactors = FALSE)
    for (columna in COLUMNAS_OPERACION) vacio[[columna]] <- character(0)
    vacio$fecha <- as.Date(character(0))
    for (columna in c("anio", "mes")) vacio[[columna]] <- integer(0)
    for (columna in c("monto", "tipo_cambio", "monto_usd")) vacio[[columna]] <- numeric(0)
    return(vacio)
  }
  datos$fecha <- as.Date(datos$fecha)
  datos
}

bitacora <- function(limite = 200) {
  con_bd(function(con) {
    DBI::dbGetQuery(con, "SELECT * FROM bitacora ORDER BY id DESC LIMIT ?",
                    params = list(as.integer(limite)))
  })
}

periodos_disponibles <- function() {
  con_bd(function(con) {
    DBI::dbGetQuery(con, "SELECT DISTINCT periodo FROM cargas ORDER BY periodo DESC")$periodo
  })
}

#' Compara dos versiones del mismo módulo y período, indicador por indicador.
comparar_versiones <- function(modulo, periodo, version_a, version_b) {
  filas <- con_bd(function(con) {
    DBI::dbGetQuery(con, "SELECT * FROM cargas WHERE modulo = ? AND periodo = ? AND version IN (?, ?)",
                    params = list(modulo, periodo, as.integer(version_a), as.integer(version_b)))
  })
  faltan <- setdiff(c(version_a, version_b), filas$version)
  if (length(faltan)) stop("No existe(n) la(s) versión(es): ", paste(faltan, collapse = ", "))

  a <- as.list(filas[filas$version == version_a, ][1, ])
  b <- as.list(filas[filas$version == version_b, ][1, ])
  indicadores <- list(
    list("Estado", a$estado, b$estado),
    list("Archivo", a$nombre_original, b$nombre_original),
    list("Usuario", a$usuario, b$usuario),
    list("Fecha de carga", a$fecha_carga, b$fecha_carga),
    list("Filas del archivo", a$filas_origen, b$filas_origen),
    list("Operaciones depuradas", a$operaciones, b$operaciones),
    list("Filas descartadas", a$filas_descartadas, b$filas_descartadas),
    list("Monto USD", round(a$monto_usd, 2), round(b$monto_usd, 2)),
    list("Errores", a$errores, b$errores),
    list("Advertencias", a$advertencias, b$advertencias)
  )

  tabla <- data.frame(
    Indicador = vapply(indicadores, function(i) i[[1]], character(1)),
    A = vapply(indicadores, function(i) as.character(i[[2]]), character(1)),
    B = vapply(indicadores, function(i) as.character(i[[3]]), character(1)),
    stringsAsFactors = FALSE
  )
  tabla$Diferencia <- vapply(indicadores, function(i) {
    if (is.numeric(i[[2]]) && is.numeric(i[[3]])) format(round(i[[3]] - i[[2]], 2))
    else if (identical(as.character(i[[2]]), as.character(i[[3]]))) "=" else "≠"
  }, character(1))
  names(tabla)[2:3] <- c(paste0("v", version_a), paste0("v", version_b))
  tabla
}

# ---------------------------------------------------------------------------
# Apoyo
# ---------------------------------------------------------------------------

.nombre_seguro <- function(nombre) {
  limpio <- trimws(gsub("[^A-Za-z0-9._ -]", "_", nombre))
  if (nzchar(limpio)) limpio else "archivo.xlsx"
}

.carpeta_version <- function(modulo, periodo, version) {
  file.path(dir_cargas(), modulo, periodo, sprintf("v%03d", as.integer(version)))
}

.siguiente_version <- function(con, modulo, periodo) {
  fila <- DBI::dbGetQuery(
    con, "SELECT COALESCE(MAX(version), 0) AS ultima FROM cargas WHERE modulo = ? AND periodo = ?",
    params = list(modulo, periodo)
  )
  as.integer(fila$ultima[1]) + 1L
}

.buscar_duplicado <- function(con, modulo, periodo, huella) {
  fila <- DBI::dbGetQuery(
    con, "SELECT id FROM cargas WHERE modulo = ? AND periodo = ? AND hash_original = ?
          ORDER BY version DESC LIMIT 1",
    params = list(modulo, periodo, huella)
  )
  if (nrow(fila)) as.integer(fila$id[1]) else NA_integer_
}

#' Guarda un CSV con BOM para que Excel respete las tildes al abrirlo.
.guardar_csv <- function(datos, ruta) {
  temporal <- tempfile(fileext = ".csv")
  on.exit(unlink(temporal), add = TRUE)
  utils::write.csv(datos, temporal, row.names = FALSE, fileEncoding = "UTF-8", na = "")
  contenido <- readBin(temporal, "raw", n = file.size(temporal))
  destino <- file(ruta, open = "wb")
  on.exit(close(destino), add = TRUE)
  writeBin(c(as.raw(c(0xEF, 0xBB, 0xBF)), contenido), destino)
}

.guardar_validaciones <- function(ruta, hallazgos, resultado) {
  contenido <- list(
    generado = format(Sys.time(), "%Y-%m-%dT%H:%M:%S"),
    hoja = resultado$hoja,
    fila_encabezado = resultado$fila_encabezado,
    columnas_detectadas = as.list(resultado$columnas_detectadas),
    columnas_ignoradas = resultado$columnas_ignoradas,
    filas_origen = resultado$filas_origen,
    filas_depuradas = resultado$filas_depuradas,
    filas_descartadas = resultado$filas_descartadas,
    hallazgos = lapply(seq_len(nrow(hallazgos)), function(i) list(
      regla = hallazgos$regla[i], severidad = hallazgos$severidad[i],
      mensaje = hallazgos$mensaje[i], filas_afectadas = hallazgos$filas_afectadas[i],
      detalle = if (is.na(hallazgos$detalle[i])) NULL else jsonlite::fromJSON(hallazgos$detalle[i])
    ))
  )
  escrito <- jsonlite::toJSON(contenido, auto_unbox = TRUE, pretty = TRUE, null = "null")
  destino <- file(ruta, open = "wb")
  on.exit(close(destino), add = TRUE)
  writeBin(charToRaw(enc2utf8(as.character(escrito))), destino)
}

.insertar_operaciones <- function(con, carga_id, modulo, periodo, datos) {
  if (!nrow(datos)) return(invisible(NULL))
  registros <- data.frame(
    carga_id = as.integer(carga_id),
    modulo = modulo,
    periodo = periodo,
    fecha = format(datos$fecha, "%Y-%m-%d"),
    anio = as.integer(datos$anio),
    mes = as.integer(datos$mes),
    nombre_mes = datos$nombre_mes,
    referencia = datos$referencia,
    corresponsal = datos$corresponsal,
    area_750 = datos$area_750,
    deuda_771 = datos$deuda_771,
    tipo_mensaje = datos$tipo_mensaje,
    proceso = datos$proceso,
    moneda = datos$moneda,
    monto = as.numeric(datos$monto),
    tipo_cambio = as.numeric(datos$tipo_cambio),
    monto_usd = as.numeric(datos$monto_usd),
    beneficiario = datos$beneficiario,
    estado = datos$estado,
    stringsAsFactors = FALSE
  )
  DBI::dbAppendTable(con, "operaciones", registros)
}
