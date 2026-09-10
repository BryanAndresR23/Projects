# ---------------------------------------------------------------------------
# Medidas, dimensiones y agregaciones que alimentan el tablero.
# ---------------------------------------------------------------------------

SIN_DATO <- "Sin dato"

# Rubros por los que se puede abrir cualquier métrica.
DIMENSIONES <- c(
  modulo       = "Módulo (Giros AL / DEL)",
  corresponsal = "Corresponsal",
  area_750     = "Área 750",
  deuda_771    = "Deuda 771",
  tipo_mensaje = "TF / GS",
  nombre_mes   = "Mes",
  periodo      = "Período",
  moneda       = "Moneda",
  proceso      = "Proceso",
  estado       = "Estado de la operación",
  beneficiario = "Beneficiario / Ordenante"
)

# Métricas disponibles. El gráfico se redibuja con la que elija el usuario.
MEDIDAS <- c(
  operaciones   = "N.° de operaciones",
  monto_usd     = "Monto USD",
  promedio_usd  = "Promedio USD por operación",
  participacion = "Participación % del monto"
)

DIMENSIONES_TEMPORALES <- c("nombre_mes", "periodo", "anio")

# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------

#' Serie de etiquetas legibles de una dimensión (sin vacíos ni nulos).
.etiquetas <- function(datos, dimension) {
  valores <- datos[[dimension]]
  if (identical(dimension, "modulo")) return(etiqueta_modulo(as.character(valores)))
  if (identical(dimension, "periodo")) {
    etiquetas <- etiqueta_periodo(as.character(valores))
    etiquetas[!nzchar(etiquetas)] <- SIN_DATO
    return(etiquetas)
  }
  texto <- trimws(as.character(valores))
  texto[is.na(texto) | texto %in% c("", "NA", "SIN CORRESPONSAL", "SIN MONEDA")] <- SIN_DATO
  texto
}

#' Aplica los filtros del panel lateral. Un vector vacío significa "todos".
aplicar_filtros <- function(datos, filtros) {
  if (!nrow(datos) || is.null(filtros) || !length(filtros)) return(datos)
  conservar <- rep(TRUE, nrow(datos))

  for (campo in names(filtros)) {
    valor <- filtros[[campo]]
    if (is.null(valor) || !length(valor)) next

    if (identical(campo, "rango_fechas") && length(valor) == 2) {
      desde <- as.Date(valor[1]); hasta <- as.Date(valor[2])
      conservar <- conservar & !is.na(datos$fecha) & datos$fecha >= desde & datos$fecha <= hasta
      next
    }
    if (identical(campo, "monto_minimo")) {
      conservar <- conservar & !is.na(datos$monto_usd) & datos$monto_usd >= as.numeric(valor)
      next
    }
    if (!campo %in% names(datos)) next
    conservar <- conservar & .etiquetas(datos, campo) %in% valor
  }
  datos[conservar, , drop = FALSE]
}

#' Valores disponibles de una dimensión, listos para un selector.
opciones <- function(datos, dimension) {
  if (!nrow(datos) || !dimension %in% names(datos)) return(character(0))
  sort(unique(.etiquetas(datos, dimension)))
}

# ---------------------------------------------------------------------------
# Medidas
# ---------------------------------------------------------------------------

#' Tarjetas principales: siempre operaciones y monto, como se definió.
kpis <- function(datos) {
  if (!nrow(datos)) {
    return(list(operaciones = 0L, monto_usd = 0, promedio_usd = 0,
                corresponsales = 0L, monedas = 0L, ticket_maximo = 0))
  }
  list(
    operaciones = nrow(datos),
    monto_usd = sum(datos$monto_usd, na.rm = TRUE),
    promedio_usd = mean(datos$monto_usd, na.rm = TRUE),
    corresponsales = length(unique(.etiquetas(datos, "corresponsal"))),
    monedas = length(unique(.etiquetas(datos, "moneda"))),
    ticket_maximo = max(datos$monto_usd, na.rm = TRUE)
  )
}

.calcular <- function(datos, etiquetas, medida) {
  if (identical(medida, "operaciones")) {
    return(tapply(rep(1L, nrow(datos)), etiquetas, sum))
  }
  if (identical(medida, "promedio_usd")) {
    return(tapply(datos$monto_usd, etiquetas, mean, na.rm = TRUE))
  }
  sumas <- tapply(datos$monto_usd, etiquetas, sum, na.rm = TRUE)
  if (identical(medida, "participacion")) {
    total <- sum(datos$monto_usd, na.rm = TRUE)
    return(if (total != 0) sumas * 100 / total else sumas * 0)
  }
  sumas
}

.es_temporal <- function(dimension) dimension %in% DIMENSIONES_TEMPORALES

.ordenar <- function(tabla, datos, dimension) {
  if (identical(dimension, "nombre_mes")) {
    orden <- match(tabla$etiqueta, MESES)
    orden[is.na(orden)] <- 99L
    return(tabla[order(orden), , drop = FALSE])
  }
  if (identical(dimension, "periodo")) {
    crudos <- stats::setNames(as.character(datos$periodo), .etiquetas(datos, "periodo"))
    return(tabla[order(crudos[tabla$etiqueta]), , drop = FALSE])
  }
  tabla[order(-tabla$valor), , drop = FALSE]
}

#' Agrupa por una dimensión y calcula la medida elegida.
#'
#' Devuelve las columnas `etiqueta` y `valor`, ordenadas de mayor a menor
#' (o cronológicamente si la dimensión es temporal).
agregar <- function(datos, dimension, medida = "monto_usd", top = NULL, agrupar_resto = TRUE) {
  vacio <- data.frame(etiqueta = character(0), valor = numeric(0), stringsAsFactors = FALSE)
  if (!nrow(datos) || !dimension %in% names(datos)) return(vacio)

  etiquetas <- .etiquetas(datos, dimension)
  valores <- .calcular(datos, etiquetas, medida)
  tabla <- data.frame(etiqueta = names(valores), valor = as.numeric(valores), stringsAsFactors = FALSE)
  tabla <- .ordenar(tabla, datos, dimension)

  if (!is.null(top) && nrow(tabla) > top && !.es_temporal(dimension)) {
    cabeza <- utils::head(tabla, top)
    if (agrupar_resto) {
      resto <- tabla[(top + 1):nrow(tabla), , drop = FALSE]
      valor_resto <- if (identical(medida, "promedio_usd")) mean(resto$valor) else sum(resto$valor)
      cabeza <- rbind(cabeza, data.frame(
        etiqueta = sprintf("Otros (%d)", nrow(resto)), valor = valor_resto, stringsAsFactors = FALSE
      ))
    }
    tabla <- cabeza
  }
  rownames(tabla) <- NULL
  tabla
}

#' Evolución en el tiempo: por día o por mes.
serie_temporal <- function(datos, medida = "monto_usd", por = "mes") {
  vacio <- data.frame(etiqueta = character(0), valor = numeric(0), stringsAsFactors = FALSE)
  if (!nrow(datos) || !"fecha" %in% names(datos)) return(vacio)
  marco <- datos[!is.na(datos$fecha), , drop = FALSE]
  if (!nrow(marco)) return(vacio)

  clave <- format(marco$fecha, if (identical(por, "mes")) "%Y-%m" else "%Y-%m-%d")
  valores <- .calcular(marco, clave, medida)
  tabla <- data.frame(etiqueta = names(valores), valor = as.numeric(valores), stringsAsFactors = FALSE)
  tabla <- tabla[order(tabla$etiqueta), , drop = FALSE]
  rownames(tabla) <- NULL
  tabla
}

#' Matriz dimensión x dimensión, con totales por fila y columna.
tabla_cruzada <- function(datos, filas, columnas, medida = "monto_usd") {
  if (!nrow(datos) || !filas %in% names(datos) || !columnas %in% names(datos)) return(NULL)
  etq_filas <- .etiquetas(datos, filas)
  etq_columnas <- .etiquetas(datos, columnas)

  matriz <- if (identical(medida, "operaciones")) {
    tapply(rep(1L, nrow(datos)), list(etq_filas, etq_columnas), sum)
  } else if (identical(medida, "promedio_usd")) {
    tapply(datos$monto_usd, list(etq_filas, etq_columnas), mean, na.rm = TRUE)
  } else {
    tapply(datos$monto_usd, list(etq_filas, etq_columnas), sum, na.rm = TRUE)
  }
  matriz[is.na(matriz)] <- 0

  if (identical(medida, "participacion")) {
    total <- sum(datos$monto_usd, na.rm = TRUE)
    matriz <- if (total != 0) matriz * 100 / total else matriz * 0
  }
  if (!identical(medida, "promedio_usd")) {
    matriz <- cbind(matriz, Total = rowSums(matriz))
    matriz <- rbind(matriz, Total = colSums(matriz))
  }
  as.data.frame(matriz, check.names = FALSE)
}

#' Tabla de apoyo: operaciones, monto, promedio y participación por rubro.
ranking <- function(datos, dimension, top = 10) {
  if (!nrow(datos) || !dimension %in% names(datos)) return(NULL)
  etiquetas <- .etiquetas(datos, dimension)
  operaciones <- tapply(rep(1L, nrow(datos)), etiquetas, sum)
  montos <- tapply(datos$monto_usd, etiquetas, sum, na.rm = TRUE)
  promedios <- tapply(datos$monto_usd, etiquetas, mean, na.rm = TRUE)
  total <- sum(datos$monto_usd, na.rm = TRUE)

  tabla <- data.frame(
    Rubro = names(operaciones),
    Operaciones = as.integer(operaciones),
    `Monto USD` = round(as.numeric(montos), 2),
    `Promedio USD` = round(as.numeric(promedios), 2),
    `Participación %` = if (total != 0) round(as.numeric(montos) * 100 / total, 2) else 0,
    check.names = FALSE, stringsAsFactors = FALSE
  )
  names(tabla)[1] <- unname(DIMENSIONES[dimension])
  tabla <- tabla[order(-tabla$`Monto USD`), , drop = FALSE]
  rownames(tabla) <- NULL
  utils::head(tabla, top)
}

# ---------------------------------------------------------------------------
# Presentación
# ---------------------------------------------------------------------------

ENCABEZADOS_DETALLE <- c(
  fecha = "Fecha", modulo = "Módulo", periodo = "Período", referencia = "Referencia",
  corresponsal = "Corresponsal", area_750 = "Área 750", deuda_771 = "Deuda 771",
  tipo_mensaje = "TF/GS", proceso = "Proceso", moneda = "Moneda", monto = "Monto origen",
  tipo_cambio = "Tipo de cambio", monto_usd = "Monto USD", beneficiario = "Beneficiario",
  estado = "Estado"
)

#' Tabla de respaldo: las filas que sustentan cada cifra del tablero.
detalle <- function(datos) {
  if (!nrow(datos)) {
    vacio <- as.data.frame(matrix(character(0), ncol = length(ENCABEZADOS_DETALLE)))
    names(vacio) <- unname(ENCABEZADOS_DETALLE)
    return(vacio)
  }
  presentes <- names(ENCABEZADOS_DETALLE)[names(ENCABEZADOS_DETALLE) %in% names(datos)]
  tabla <- datos[, presentes, drop = FALSE]
  if ("modulo" %in% names(tabla)) tabla$modulo <- etiqueta_modulo(as.character(tabla$modulo))
  if ("periodo" %in% names(tabla)) tabla$periodo <- etiqueta_periodo(as.character(tabla$periodo))
  if ("fecha" %in% names(tabla)) tabla$fecha <- format(tabla$fecha, "%d/%m/%Y")
  names(tabla) <- unname(ENCABEZADOS_DETALLE[presentes])
  rownames(tabla) <- NULL
  tabla
}

formatear <- function(valor, medida) {
  if (identical(medida, "operaciones")) return(formatear_numero(valor))
  if (identical(medida, "participacion")) return(paste0(formatC(valor, format = "f", digits = 1), " %"))
  formatear_usd(valor)
}

formatear_usd <- function(valor) paste0("USD ", formatC(valor, format = "f", digits = 2, big.mark = ","))

#' Abrevia las cifras grandes para que quepan en las tarjetas del tablero.
formatear_usd_compacto <- function(valor) {
  magnitud <- abs(valor)
  if (!is.finite(magnitud)) return("USD 0.00")
  if (magnitud >= 1e9) return(paste0("USD ", formatC(valor / 1e9, format = "f", digits = 2, big.mark = ","), " MM"))
  if (magnitud >= 1e6) return(paste0("USD ", formatC(valor / 1e6, format = "f", digits = 2, big.mark = ","), " M"))
  formatear_usd(valor)
}

formatear_numero <- function(valor) {
  formatC(as.numeric(valor), format = "d", big.mark = ".", decimal.mark = ",")
}
