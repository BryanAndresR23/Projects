# ---------------------------------------------------------------------------
# Lectura de los catálogos configurables (columnas, módulos, monedas, TC).
# ---------------------------------------------------------------------------

.catalogo <- new.env(parent = emptyenv())

#' Deja un texto comparable: sin tildes, en mayúsculas y sin puntuación.
#'
#' "Área 750." -> "AREA 750"   |   "Tipo de Cambio" -> "TIPO DE CAMBIO"
normalizar <- function(texto) {
  if (length(texto) == 0) return(character(0))
  s <- as.character(texto)
  s[is.na(s)] <- ""
  s <- stringi::stri_trans_general(s, "Latin-ASCII")
  s <- toupper(s)
  s <- gsub("[^A-Z0-9]+", " ", s)
  trimws(gsub("[[:space:]]+", " ", s))
}

.escapar_regex <- function(texto) gsub("([.|()\\^{}+$*?\\[\\]])", "\\\\\\1", texto)

#' Vuelve a leer los catálogos desde disco (útil tras editarlos).
recargar_catalogos <- function() {
  rm(list = ls(envir = .catalogo), envir = .catalogo)
  invisible(TRUE)
}

.catalogo_crudo <- function() {
  if (is.null(.catalogo$crudo)) {
    ruta <- file.path(dir_catalogos(), "columnas.yaml")
    if (!file.exists(ruta)) stop("No se encontró el catálogo de columnas: ", ruta)
    .catalogo$crudo <- yaml::yaml.load(leer_texto_utf8(ruta))
  }
  .catalogo$crudo
}

campos <- function() {
  campos <- .catalogo_crudo()$campos
  if (is.null(campos)) list() else campos
}

campos_requeridos <- function() {
  definiciones <- campos()
  nombres <- names(definiciones)
  nombres[vapply(definiciones, function(d) isTRUE(d$requerido), logical(1))]
}

etiqueta_campo <- function(campo) {
  definicion <- campos()[[campo]]
  if (!is.null(definicion$etiqueta)) definicion$etiqueta else campo
}

#' Alias normalizado -> nombre canónico del campo.
.indice_alias <- function() {
  if (is.null(.catalogo$alias)) {
    indice <- character(0)
    for (campo in names(campos())) {
      claves <- c(campo, campos()[[campo]]$alias)
      for (clave in normalizar(claves)) {
        if (nzchar(clave) && is.na(indice[clave])) indice[clave] <- campo
      }
    }
    .catalogo$alias <- indice
    .catalogo$alias_tokens <- lapply(names(indice), function(a) strsplit(a, " ", fixed = TRUE)[[1]])
    names(.catalogo$alias_tokens) <- names(indice)
  }
  .catalogo$alias
}

#' Devuelve el campo canónico al que corresponde cada encabezado del archivo.
#'
#' Primero busca coincidencia exacta con algún alias; si no la hay, gana el
#' alias cuyas palabras estén todas contenidas en el encabezado y que más
#' palabras aporte. Así "Fecha de la operación" cae en `fecha` (por el alias
#' "FECHA OPERACION") y no en `referencia` (por el alias "OPERACION").
campo_de_encabezado <- function(encabezado) {
  claves <- normalizar(encabezado)
  indice <- .indice_alias()
  tokens_alias <- .catalogo$alias_tokens
  resultado <- rep(NA_character_, length(claves))

  for (i in seq_along(claves)) {
    clave <- claves[i]
    if (!nzchar(clave)) next
    exacto <- indice[clave]
    if (!is.na(exacto)) {
      resultado[i] <- unname(exacto)
      next
    }
    palabras <- strsplit(clave, " ", fixed = TRUE)[[1]]
    mejor_tokens <- 0L
    mejor_largo <- 0L
    for (alias in names(indice)) {
      tokens <- tokens_alias[[alias]]
      if (!all(tokens %in% palabras)) next
      cuantos <- length(tokens)
      largo <- nchar(alias)
      if (cuantos > mejor_tokens || (cuantos == mejor_tokens && largo > mejor_largo)) {
        mejor_tokens <- cuantos
        mejor_largo <- largo
        resultado[i] <- unname(indice[alias])
      }
    }
  }
  resultado
}

modulos_catalogo <- function() {
  modulos <- .catalogo_crudo()$modulos
  if (is.null(modulos)) list() else modulos
}

marcadores_modulo <- function(modulo) normalizar(modulos_catalogo()[[modulo]]$marcadores)

#' Deduce el módulo a partir de un texto (columna sentido o nombre de archivo).
#'
#' Se prefiere el marcador más largo para que "GIROS DEL" gane sobre "AL"
#' cuando ambos aparecen en la misma cadena.
modulo_de_texto <- function(texto) {
  claves <- normalizar(texto)
  resultado <- rep(NA_character_, length(claves))
  marcadores <- lapply(names(modulos_catalogo()), marcadores_modulo)
  names(marcadores) <- names(modulos_catalogo())

  for (i in seq_along(claves)) {
    clave <- claves[i]
    if (!nzchar(clave)) next
    mejor_largo <- 0L
    for (modulo in names(marcadores)) {
      for (marcador in marcadores[[modulo]]) {
        patron <- sprintf("(^|[^A-Z0-9])%s($|[^A-Z0-9])", .escapar_regex(marcador))
        if (grepl(patron, clave) && nchar(marcador) > mejor_largo) {
          mejor_largo <- nchar(marcador)
          resultado[i] <- modulo
        }
      }
    }
  }
  resultado
}

.indice_monedas <- function() {
  if (is.null(.catalogo$monedas)) {
    indice <- character(0)
    monedas <- .catalogo_crudo()$monedas
    for (iso in names(monedas)) {
      for (clave in normalizar(c(iso, monedas[[iso]]))) {
        if (nzchar(clave)) indice[clave] <- iso
      }
    }
    .catalogo$monedas <- indice
  }
  .catalogo$monedas
}

monedas_conocidas <- function() sort(unique(unname(.indice_monedas())))

#' Lleva la moneda a código ISO cuando se la reconoce; si no, la deja limpia.
normalizar_moneda <- function(valor) {
  claves <- normalizar(valor)
  indice <- .indice_monedas()
  encontradas <- unname(indice[claves])
  ifelse(is.na(encontradas), claves, encontradas)
}

filas_no_dato <- function() normalizar(.catalogo_crudo()$filas_no_dato)

#' Tipos de cambio a USD por moneda y período (catálogo opcional).
tipos_cambio <- function() {
  if (is.null(.catalogo$tc)) {
    ruta <- file.path(dir_catalogos(), "tipos_cambio.yaml")
    crudo <- if (file.exists(ruta)) yaml::yaml.load(leer_texto_utf8(ruta)) else NULL
    tabla <- list()
    if (!is.null(crudo)) {
      for (moneda in names(crudo)) {
        periodos <- crudo[[moneda]]
        if (is.list(periodos) && length(periodos)) {
          tabla[[normalizar_moneda(moneda)]] <- lapply(periodos, as.numeric)
        }
      }
    }
    .catalogo$tc <- tabla
  }
  .catalogo$tc
}

tipo_cambio <- function(moneda, periodo) {
  tabla <- tipos_cambio()[[moneda]]
  if (is.null(tabla)) return(NA_real_)
  factor <- tabla[[as.character(periodo)]]
  if (is.null(factor)) NA_real_ else as.numeric(factor)
}
