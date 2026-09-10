# ---------------------------------------------------------------------------
# Reglas de validación que se aplican a cada carga antes de publicarla.
# ---------------------------------------------------------------------------

.hallazgo <- function(regla, severidad, mensaje, filas = 0, detalle = NULL) {
  list(
    regla = regla,
    severidad = severidad,
    mensaje = mensaje,
    filas_afectadas = as.integer(filas),
    detalle = if (is.null(detalle)) NA_character_
              else as.character(jsonlite::toJSON(detalle, auto_unbox = TRUE, null = "null"))
  )
}

.error       <- function(regla, mensaje, filas = 0, detalle = NULL) .hallazgo(regla, SEVERIDAD_ERROR, mensaje, filas, detalle)
.advertencia <- function(regla, mensaje, filas = 0, detalle = NULL) .hallazgo(regla, SEVERIDAD_ADVERTENCIA, mensaje, filas, detalle)
.info        <- function(regla, mensaje, filas = 0, detalle = NULL) .hallazgo(regla, SEVERIDAD_INFO, mensaje, filas, detalle)

#' Aplica todas las reglas y devuelve los hallazgos ordenados por severidad.
validar <- function(resultado, modulo, periodo, nombre_archivo = "") {
  hallazgos <- c(
    .regla_estructura(resultado),
    .regla_contenido(resultado),
    .regla_modulo(resultado, modulo, nombre_archivo),
    .regla_periodo(resultado, periodo),
    .regla_descartes(resultado),
    .regla_montos(resultado),
    .regla_monedas(resultado),
    .regla_duplicados(resultado),
    .regla_catalogos(resultado)
  )
  if (!length(hallazgos)) return(.hallazgos_vacios())

  tabla <- do.call(rbind, lapply(hallazgos, function(h) as.data.frame(h, stringsAsFactors = FALSE)))
  orden <- match(tabla$severidad, c(SEVERIDAD_ERROR, SEVERIDAD_ADVERTENCIA, SEVERIDAD_INFO))
  tabla[order(orden, tabla$regla), , drop = FALSE]
}

.hallazgos_vacios <- function() {
  data.frame(regla = character(0), severidad = character(0), mensaje = character(0),
             filas_afectadas = integer(0), detalle = character(0), stringsAsFactors = FALSE)
}

# ---------------------------------------------------------------------------
# Reglas
# ---------------------------------------------------------------------------

.regla_estructura <- function(resultado) {
  if (is.na(resultado$fila_encabezado)) {
    return(list(.error(
      "ESTRUCTURA_ENCABEZADO",
      paste("No se encontró una fila de encabezados reconocible. Revise que el archivo",
            "sea el reporte de giros y no un resumen o un archivo protegido.")
    )))
  }

  hallazgos <- list()
  if (length(resultado$columnas_faltantes)) {
    etiquetas <- paste(vapply(resultado$columnas_faltantes, etiqueta_campo, character(1)), collapse = ", ")
    hallazgos <- c(hallazgos, list(.error(
      "ESTRUCTURA_COLUMNAS",
      sprintf(paste("Faltan columnas obligatorias: %s. Agregue el nombre real de esas columnas",
                    "en catalogos/columnas.yaml si el reporte las llama de otro modo."), etiquetas),
      detalle = list(faltantes = resultado$columnas_faltantes)
    )))
  } else {
    hallazgos <- c(hallazgos, list(.info(
      "ESTRUCTURA_COLUMNAS",
      sprintf("Se reconocieron %d columnas (encabezado en la fila %d).",
              length(resultado$columnas_detectadas), resultado$fila_encabezado),
      detalle = list(columnas = as.list(resultado$columnas_detectadas))
    )))
  }

  if (length(resultado$columnas_ignoradas)) {
    hallazgos <- c(hallazgos, list(.info(
      "COLUMNAS_NO_MAPEADAS",
      sprintf("%d columnas del archivo no se usan en el tablero.", length(resultado$columnas_ignoradas)),
      detalle = list(columnas = utils::head(resultado$columnas_ignoradas, 40))
    )))
  }
  hallazgos
}

.regla_contenido <- function(resultado) {
  if (resultado$filas_origen == 0) {
    return(list(.error("SIN_FILAS", "El archivo no contiene filas de datos debajo del encabezado.")))
  }
  if (resultado$filas_depuradas == 0) {
    return(list(.error(
      "SIN_FILAS_VALIDAS",
      sprintf("Ninguna de las %d filas superó la depuración. Revise el detalle de descartes.",
              resultado$filas_origen),
      filas = resultado$filas_origen
    )))
  }
  list()
}

#' Comprueba que el archivo corresponda al módulo elegido por el validador.
.regla_modulo <- function(resultado, modulo, nombre_archivo) {
  esperado <- etiqueta_modulo(modulo)
  datos <- resultado$datos

  if (nrow(datos) && "sentido" %in% names(datos)) {
    deducidos <- modulo_de_texto(datos$sentido)
    deducidos <- deducidos[!is.na(deducidos)]
    if (length(deducidos)) {
      conteo <- table(deducidos)
      total <- sum(conteo)
      propios <- if (modulo %in% names(conteo)) as.integer(conteo[[modulo]]) else 0L
      ajenos <- total - propios

      if (propios == 0) {
        otro <- etiqueta_modulo(names(conteo)[which.max(conteo)])
        return(list(.error(
          "MODULO_NO_COINCIDE",
          sprintf(paste("Seleccionó %s, pero el archivo corresponde a %s (%d filas con ese sentido).",
                        "Cambie la selección o el archivo."), esperado, otro, total),
          filas = total,
          detalle = list(esperado = modulo, encontrado = as.list(conteo))
        )))
      }
      if (ajenos > 0) {
        return(list(.advertencia(
          "MODULO_MEZCLADO",
          sprintf(paste("%d de %d filas no corresponden a %s. Se conservan todas,",
                        "pero conviene revisar el origen del reporte."), ajenos, total, esperado),
          filas = ajenos,
          detalle = list(esperado = modulo, encontrado = as.list(conteo))
        )))
      }
      return(list(.info(
        "MODULO_COINCIDE",
        sprintf("El sentido de las %d filas coincide con %s.", total, esperado),
        filas = total
      )))
    }
  }

  del_nombre <- modulo_de_texto(nombre_archivo)
  if (!is.na(del_nombre) && del_nombre != modulo) {
    return(list(.advertencia(
      "MODULO_NOMBRE_ARCHIVO",
      sprintf(paste("Seleccionó %s, pero el nombre del archivo sugiere %s.",
                    "Confirme antes de publicar."), esperado, etiqueta_modulo(del_nombre)),
      detalle = list(archivo = nombre_archivo, deducido = del_nombre)
    )))
  }
  if (!is.na(del_nombre) && del_nombre == modulo) {
    return(list(.info("MODULO_COINCIDE", sprintf("El nombre del archivo confirma %s.", esperado))))
  }

  list(.advertencia(
    "MODULO_SIN_EVIDENCIA",
    sprintf(paste("El archivo no trae una columna de sentido ni un nombre que permita confirmar %s.",
                  "Se registra la selección del validador como declaración responsable."), esperado),
    detalle = list(modulo = modulo)
  ))
}

.regla_periodo <- function(resultado, periodo) {
  datos <- resultado$datos
  if (!nrow(datos)) return(list())
  partes <- partes_periodo(periodo)
  fuera <- datos$anio != partes$anio | datos$mes != partes$mes
  fuera[is.na(fuera)] <- TRUE

  if (!any(fuera)) {
    return(list(.info("PERIODO_COINCIDE",
                      sprintf("Todas las fechas pertenecen a %s.", etiqueta_periodo(periodo)))))
  }

  periodos <- sort(unique(sprintf("%04d-%02d", datos$anio[fuera], datos$mes[fuera])))
  if (all(fuera)) {
    return(list(.error(
      "PERIODO_NO_COINCIDE",
      sprintf("Ninguna fila pertenece a %s. El archivo contiene: %s.",
              etiqueta_periodo(periodo), paste(utils::head(periodos, 6), collapse = ", ")),
      filas = sum(fuera), detalle = list(periodos = periodos)
    )))
  }
  list(.advertencia(
    "PERIODO_PARCIAL",
    sprintf("%d de %d filas están fuera de %s (%s).",
            sum(fuera), nrow(datos), etiqueta_periodo(periodo),
            paste(utils::head(periodos, 6), collapse = ", ")),
    filas = sum(fuera), detalle = list(periodos = periodos)
  ))
}

.regla_descartes <- function(resultado) {
  descartes <- resultado$descartes
  if (!nrow(descartes)) return(list())

  hallazgos <- list()
  for (motivo in unique(descartes$motivo)) {
    afectadas <- descartes$fila_origen[descartes$motivo == motivo]
    cantidad <- length(afectadas)
    filas <- as.integer(utils::head(afectadas, 50))

    if (identical(motivo, MOTIVO_SIN_TC)) {
      hallazgos <- c(hallazgos, list(.error(
        "TIPO_CAMBIO_FALTANTE",
        sprintf(paste("%d filas en moneda distinta de USD no tienen tipo de cambio.",
                      "Cargue el factor en catalogos/tipos_cambio.yaml o incluya la columna Monto USD."),
                cantidad),
        filas = cantidad, detalle = list(filas = filas)
      )))
    } else if (identical(motivo, MOTIVO_TOTALES)) {
      hallazgos <- c(hallazgos, list(.info(
        "FILAS_TOTALES",
        sprintf("Se excluyeron %d filas de totales o subtotales.", cantidad),
        filas = cantidad, detalle = list(filas = filas)
      )))
    } else {
      hallazgos <- c(hallazgos, list(.advertencia(
        "FILAS_DESCARTADAS",
        sprintf("%d filas descartadas: %s.", cantidad, tolower(motivo)),
        filas = cantidad, detalle = list(filas = filas)
      )))
    }
  }
  hallazgos
}

.regla_montos <- function(resultado) {
  datos <- resultado$datos
  if (!nrow(datos)) return(list())
  hallazgos <- list()

  negativos <- which(!is.na(datos$monto_usd) & datos$monto_usd < 0)
  if (length(negativos)) {
    hallazgos <- c(hallazgos, list(.advertencia(
      "MONTOS_NEGATIVOS",
      sprintf("%d operaciones tienen monto negativo (reversos o notas de crédito).", length(negativos)),
      filas = length(negativos),
      detalle = list(filas = as.integer(utils::head(datos$fila_origen[negativos], 50)))
    )))
  }
  ceros <- which(!is.na(datos$monto_usd) & datos$monto_usd == 0)
  if (length(ceros)) {
    hallazgos <- c(hallazgos, list(.advertencia(
      "MONTOS_EN_CERO",
      sprintf("%d operaciones tienen monto USD igual a cero.", length(ceros)),
      filas = length(ceros),
      detalle = list(filas = as.integer(utils::head(datos$fila_origen[ceros], 50)))
    )))
  }
  hallazgos
}

.regla_monedas <- function(resultado) {
  datos <- resultado$datos
  if (!nrow(datos)) return(list())
  conocidas <- monedas_conocidas()
  presentes <- unique(datos$moneda[nzchar(datos$moneda) & datos$moneda != "SIN MONEDA"])
  desconocidas <- sort(setdiff(presentes, conocidas))
  if (!length(desconocidas)) return(list())

  list(.advertencia(
    "MONEDA_NO_CATALOGADA",
    sprintf(paste("Monedas fuera del catálogo: %s.",
                  "Agréguelas en catalogos/columnas.yaml si son válidas."),
            paste(utils::head(desconocidas, 10), collapse = ", ")),
    filas = sum(datos$moneda %in% desconocidas),
    detalle = list(monedas = desconocidas)
  ))
}

.regla_duplicados <- function(resultado) {
  datos <- resultado$datos
  if (!nrow(datos) || !"referencia" %in% names(datos)) return(list())
  hallazgos <- list()

  sin_referencia <- which(!nzchar(datos$referencia))
  if (length(sin_referencia)) {
    hallazgos <- c(hallazgos, list(.advertencia(
      "REFERENCIA_VACIA",
      sprintf("%d operaciones no tienen número de referencia.", length(sin_referencia)),
      filas = length(sin_referencia),
      detalle = list(filas = as.integer(utils::head(datos$fila_origen[sin_referencia], 50)))
    )))
  }

  con_referencia <- datos$referencia[nzchar(datos$referencia)]
  repetidas <- con_referencia[duplicated(con_referencia) | duplicated(con_referencia, fromLast = TRUE)]
  if (length(repetidas)) {
    hallazgos <- c(hallazgos, list(.advertencia(
      "REFERENCIAS_DUPLICADAS",
      sprintf(paste("%d referencias se repiten (%d filas).",
                    "Verifique que no sea una doble carga."),
              length(unique(repetidas)), length(repetidas)),
      filas = length(repetidas),
      detalle = list(referencias = sort(unique(repetidas))[seq_len(min(30, length(unique(repetidas))))])
    )))
  }
  hallazgos
}

.regla_catalogos <- function(resultado) {
  datos <- resultado$datos
  if (!nrow(datos)) return(list())
  reglas <- c(corresponsal = "CORRESPONSAL_VACIO", area_750 = "AREA_750_VACIA",
              deuda_771 = "DEUDA_771_VACIA", tipo_mensaje = "TF_GS_VACIO")
  hallazgos <- list()

  for (campo in names(reglas)) {
    if (!campo %in% names(datos)) next
    valores <- datos[[campo]]
    vacios <- sum(!nzchar(valores) | valores == "SIN CORRESPONSAL")
    if (vacios > 0) {
      hallazgos <- c(hallazgos, list(.advertencia(
        reglas[[campo]],
        sprintf("%d operaciones sin %s; se agruparán como “Sin dato” en el tablero.",
                vacios, etiqueta_campo(campo)),
        filas = vacios
      )))
    }
  }
  hallazgos
}

#' Traduce los hallazgos al estado de la carga.
estado_de <- function(hallazgos) {
  if (!nrow(hallazgos)) return(ESTADO_VALIDA)
  if (any(hallazgos$severidad == SEVERIDAD_ERROR)) return(ESTADO_RECHAZADA)
  if (any(hallazgos$severidad == SEVERIDAD_ADVERTENCIA)) return(ESTADO_ADVERTENCIAS)
  ESTADO_VALIDA
}

resumen_hallazgos <- function(hallazgos) {
  list(
    errores = sum(hallazgos$severidad == SEVERIDAD_ERROR),
    advertencias = sum(hallazgos$severidad == SEVERIDAD_ADVERTENCIA),
    informativos = sum(hallazgos$severidad == SEVERIDAD_INFO)
  )
}
