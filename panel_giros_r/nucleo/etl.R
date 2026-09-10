# ---------------------------------------------------------------------------
# ETL: lectura del archivo cargado, mapeo de columnas y depuración.
# ---------------------------------------------------------------------------

# Columnas del modelo depurado que alimenta el tablero.
CAMPOS_SALIDA <- c("fecha", "anio", "mes", "nombre_mes", "referencia", "corresponsal",
                   "area_750", "deuda_771", "tipo_mensaje", "proceso", "moneda", "monto",
                   "tipo_cambio", "monto_usd", "beneficiario", "estado", "sentido")

MOTIVO_TOTALES <- "Fila de totales o subtotales"
MOTIVO_FECHA   <- "Fecha ausente o no reconocida"
MOTIVO_MONTO   <- "Monto ausente o no numérico"
MOTIVO_SIN_TC  <- "Sin tipo de cambio para convertir a USD"

# ---------------------------------------------------------------------------
# Conversores
# ---------------------------------------------------------------------------

#' Texto limpio; los nulos y los "NA" quedan como cadena vacía.
a_texto <- function(valor) {
  if (is.null(valor) || length(valor) == 0) return("")
  valor <- valor[[1]]
  if (is.null(valor) || (length(valor) == 1 && is.na(valor))) return("")
  if (inherits(valor, "POSIXct") || inherits(valor, "Date")) {
    return(format(valor, "%Y-%m-%d"))
  }
  texto <- trimws(as.character(valor))
  if (tolower(texto) %in% c("nan", "na", "none", "null", "-", "--")) return("")
  gsub("[[:space:]]+", " ", texto)
}

a_texto_vec <- function(celdas) vapply(celdas, a_texto, character(1), USE.NAMES = FALSE)

#' Convierte a número tolerando formatos mixtos.
#'
#' Acepta "1.234,56" (europeo), "1,234.56" (anglosajón), "$ 1 234,56",
#' "(1.234,56)" como negativo y "1234" a secas.
#'
#' `punto_es_miles` resuelve el caso ambiguo de un único punto seguido de tres
#' dígitos: en importes "1.500" son mil quinientos, mientras que en un tipo de
#' cambio "1.085" es un decimal. Se usa TRUE solo para montos.
a_numero <- function(valor, punto_es_miles = FALSE) {
  if (is.null(valor) || length(valor) == 0) return(NA_real_)
  valor <- valor[[1]]
  if (is.null(valor) || (length(valor) == 1 && is.na(valor))) return(NA_real_)
  if (is.logical(valor)) return(NA_real_)
  if (is.numeric(valor)) {
    numero <- as.numeric(valor)
    return(if (is.finite(numero)) numero else NA_real_)
  }

  texto <- a_texto(valor)
  if (!nzchar(texto)) return(NA_real_)

  negativo <- grepl("^\\(.*\\)$", texto)
  texto <- gsub("^\\(|\\)$", "", texto)
  texto <- gsub("[^0-9,.+-]", "", texto)
  if (!nzchar(texto) || texto %in% c("-", "+", ".", ",")) return(NA_real_)

  tiene_coma <- grepl(",", texto, fixed = TRUE)
  tiene_punto <- grepl(".", texto, fixed = TRUE)

  if (tiene_coma && tiene_punto) {
    # El separador decimal es el que aparece más a la derecha.
    if (.ultima_posicion(texto, ",") > .ultima_posicion(texto, ".")) {
      texto <- gsub(",", ".", gsub(".", "", texto, fixed = TRUE), fixed = TRUE)
    } else {
      texto <- gsub(",", "", texto, fixed = TRUE)
    }
  } else if (tiene_coma) {
    decimales <- .cola_tras(texto, ",")
    # "1,234" con 3 dígitos finales es separador de miles, no decimal.
    texto <- if (nchar(decimales) != 3) {
      .reemplazar_ultimo(texto, ",", ".")
    } else {
      gsub(",", "", texto, fixed = TRUE)
    }
  } else if (tiene_punto) {
    decimales <- .cola_tras(texto, ".")
    puntos <- lengths(regmatches(texto, gregexpr(".", texto, fixed = TRUE)))
    if (nchar(decimales) == 3 && (puntos >= 2 || punto_es_miles)) {
      texto <- gsub(".", "", texto, fixed = TRUE)
    }
  }

  numero <- suppressWarnings(as.numeric(texto))
  if (!is.finite(numero)) return(NA_real_)
  if (negativo) -numero else numero
}

a_numero_vec <- function(celdas, punto_es_miles = FALSE) {
  vapply(celdas, a_numero, numeric(1), punto_es_miles = punto_es_miles, USE.NAMES = FALSE)
}

.ultima_posicion <- function(texto, caracter) {
  posiciones <- gregexpr(caracter, texto, fixed = TRUE)[[1]]
  if (posiciones[1] == -1) 0L else max(posiciones)
}

.cola_tras <- function(texto, caracter) {
  posicion <- .ultima_posicion(texto, caracter)
  if (posicion == 0L) "" else substring(texto, posicion + 1)
}

.reemplazar_ultimo <- function(texto, caracter, reemplazo) {
  posicion <- .ultima_posicion(texto, caracter)
  if (posicion == 0L) return(texto)
  paste0(substring(texto, 1, posicion - 1), reemplazo, substring(texto, posicion + 1))
}

FORMATOS_FECHA <- c("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d.%m.%Y",
                    "%Y/%m/%d", "%d %b %Y", "%d%b%Y", "%m/%d/%Y", "%d-%b-%Y")

#' Convierte a fecha textos, fechas de Excel y seriales numéricos.
a_fecha <- function(valor) {
  vacia <- as.Date(NA)
  if (is.null(valor) || length(valor) == 0) return(vacia)
  valor <- valor[[1]]
  if (is.null(valor) || (length(valor) == 1 && is.na(valor))) return(vacia)
  if (inherits(valor, "POSIXct")) return(as.Date(valor))
  if (inherits(valor, "Date")) return(valor)

  if (is.numeric(valor)) {
    numero <- as.numeric(valor)
    # Serial de Excel (1900-01-01 = 1, 2100 ~ 73050).
    if (is.finite(numero) && numero >= 20000 && numero <= 80000) {
      return(as.Date(numero, origin = "1899-12-30"))
    }
    return(vacia)
  }

  texto <- a_texto(valor)
  if (!nzchar(texto)) return(vacia)
  if (grepl(" ", texto, fixed = TRUE) && grepl(":", texto, fixed = TRUE)) {
    texto <- strsplit(texto, " ", fixed = TRUE)[[1]][1]
  }
  for (formato in FORMATOS_FECHA) {
    convertida <- suppressWarnings(as.Date(texto, format = formato))
    if (!is.na(convertida)) return(convertida)
  }
  vacia
}

a_fecha_vec <- function(celdas) {
  as.Date(vapply(celdas, function(c) as.numeric(a_fecha(c)), numeric(1), USE.NAMES = FALSE),
          origin = "1970-01-01")
}

#' Deja el mensaje SWIFT como "TF" o "GS"; si no lo reconoce, devuelve el texto.
normalizar_tipo_mensaje <- function(valor) {
  texto <- normalizar(valor)
  patron <- "(^|[^A-Z0-9])(TF|GS)($|[^A-Z0-9])"
  resultado <- texto
  hay <- grepl(patron, texto)
  if (any(hay)) {
    resultado[hay] <- vapply(texto[hay], function(t) {
      coincidencia <- regmatches(t, regexpr(patron, t))
      gsub("[^A-Z]", "", coincidencia)
    }, character(1), USE.NAMES = FALSE)
  }
  resultado
}

# ---------------------------------------------------------------------------
# Lectura del archivo
# ---------------------------------------------------------------------------

.es_csv <- function(nombre) {
  tolower(tools::file_ext(nombre)) %in% c("csv", "txt", "tsv")
}

#' Deduce el formato real del archivo por su contenido, no por su nombre.
#'
#' Los sistemas de reportes suelen entregar archivos llamados ".xls" que por
#' dentro son otra cosa: una tabla HTML o un texto separado por tabuladores.
#' Excel los abre igual, pero las librerías de lectura no, así que hay que
#' mirar el contenido:
#'   - un .xlsx es un ZIP            -> empieza con "PK"
#'   - un .xls antiguo es un OLE2    -> empieza con D0 CF 11 E0
#'   - una tabla HTML                -> trae <table> o <tr>
#'   - cualquier otra cosa legible   -> texto delimitado
.formato_archivo <- function(ruta, nombre = "") {
  cabecera <- tryCatch(readBin(ruta, "raw", n = 8), error = function(e) raw(0))
  if (length(cabecera) >= 4) {
    inicio <- as.integer(cabecera[1:4])
    if (identical(inicio, c(0x50L, 0x4BL, 0x03L, 0x04L))) return("xlsx")
    if (identical(inicio, c(0xD0L, 0xCFL, 0x11L, 0xE0L))) return("xls")
  }

  muestra <- tryCatch(
    rawToChar(readBin(ruta, "raw", n = 8192)),
    error = function(e) ""
  )
  if (nzchar(muestra) && grepl("<\\s*(table|tr|html)\\b", muestra, ignore.case = TRUE)) {
    return("html")
  }

  extension <- tolower(tools::file_ext(nombre))
  if (extension %in% c("csv", "txt", "tsv")) return("texto")
  if (nzchar(muestra)) return("texto")
  NA_character_
}

# Se conserva el nombre anterior por compatibilidad con las pruebas y el resto
# del código: para un Excel de verdad devuelve lo mismo que antes.
.formato_excel <- function(ruta, nombre = "") {
  formato <- .formato_archivo(ruta, nombre)
  if (identical(formato, "xlsx") || identical(formato, "xls")) formato else NA_character_
}

#' Devuelve una ruta cuya extensión coincide con el formato real.
#'
#' Shiny guarda los archivos subidos con un nombre temporal ("0.xls") cuya
#' extensión no siempre corresponde al archivo original, y readxl elige el
#' lector por la extensión. Sin esto, un .xlsx cargado desde el navegador se
#' intenta leer como .xls y falla con "libxls error: Unable to open file".
.ruta_legible <- function(ruta, nombre = basename(ruta)) {
  formato <- .formato_excel(ruta, nombre)
  if (is.na(formato)) return(ruta)
  if (identical(tolower(tools::file_ext(ruta)), formato)) return(ruta)
  destino <- tempfile(fileext = paste0(".", formato))
  file.copy(ruta, destino, overwrite = TRUE)
  destino
}

# Entidades HTML habituales en los reportes en español. Sin traducirlas,
# un encabezado como "N&deg; OPERACI&Oacute;N" no se reconoce como columna.
ENTIDADES_HTML <- c(
  nbsp = " ", amp = "&", lt = "<", gt = ">", quot = "\"", apos = "'",
  aacute = "á", eacute = "é", iacute = "í", oacute = "ó", uacute = "ú",
  Aacute = "Á", Eacute = "É", Iacute = "Í", Oacute = "Ó", Uacute = "Ú",
  ntilde = "ñ", Ntilde = "Ñ", uuml = "ü", Uuml = "Ü",
  deg = "°", ordm = "º", ordf = "ª", middot = "·", bull = "•",
  laquo = "«", raquo = "»", hellip = "…", ndash = "–", mdash = "—",
  euro = "€", pound = "£", yen = "¥", cent = "¢",
  copy = "©", reg = "®", trade = "™"
)

#' Traduce las entidades HTML (&Oacute;, &#243;, &#xF3;) a su carácter real.
.desescapar_html <- function(texto) {
  for (nombre in names(ENTIDADES_HTML)) {
    texto <- gsub(paste0("&", nombre, ";"), ENTIDADES_HTML[[nombre]], texto, fixed = TRUE)
  }
  vapply(texto, .entidades_numericas, character(1), USE.NAMES = FALSE)
}

.entidades_numericas <- function(texto) {
  patron <- "&#(x?)([0-9A-Fa-f]+);"
  while (regexpr(patron, texto, perl = TRUE) != -1) {
    coincidencia <- regmatches(texto, regexpr(patron, texto, perl = TRUE))
    partes <- regmatches(coincidencia, regexec(patron, coincidencia, perl = TRUE))[[1]]
    codigo <- if (nzchar(partes[2])) strtoi(partes[3], 16L) else suppressWarnings(as.integer(partes[3]))
    reemplazo <- if (is.na(codigo) || codigo < 1) "" else intToUtf8(codigo)
    texto <- sub(patron, reemplazo, texto, perl = TRUE)
  }
  texto
}

.texto_de_celda_html <- function(celda) {
  texto <- gsub("(?is)<[^>]*>", " ", celda, perl = TRUE)
  texto <- .desescapar_html(texto)
  trimws(gsub("[[:space:]]+", " ", texto))
}

#' Extrae la tabla más grande de un archivo HTML disfrazado de Excel.
.leer_tabla_html <- function(ruta) {
  contenido <- leer_texto_utf8(ruta)
  tablas <- regmatches(contenido, gregexpr("(?is)<table.*?</table>", contenido, perl = TRUE))[[1]]
  bloque <- if (length(tablas)) tablas[which.max(nchar(tablas))] else contenido

  filas <- regmatches(bloque, gregexpr("(?is)<tr.*?</tr>", bloque, perl = TRUE))[[1]]
  if (!length(filas)) return(NULL)

  celdas <- lapply(filas, function(fila) {
    trozos <- regmatches(fila, gregexpr("(?is)<t[dh][^>]*>.*?</t[dh]>", fila, perl = TRUE))[[1]]
    vapply(trozos, .texto_de_celda_html, character(1), USE.NAMES = FALSE)
  })
  celdas <- Filter(function(f) length(f) > 0, celdas)
  if (!length(celdas)) return(NULL)

  ancho <- max(lengths(celdas))
  matriz <- t(vapply(celdas, function(f) c(f, rep("", ancho - length(f))), character(ancho)))
  lapply(seq_len(ancho), function(j) as.list(matriz[, j]))
}

hojas_disponibles <- function(ruta, nombre = basename(ruta)) {
  formato <- .formato_archivo(ruta, nombre)
  if (is.na(formato) || !formato %in% c("xlsx", "xls")) return(character(0))
  readxl::excel_sheets(.ruta_legible(ruta, nombre))
}

.detectar_separador <- function(contenido) {
  lineas <- utils::head(Filter(nzchar, strsplit(contenido, "\r?\n")[[1]]), 10)
  if (!length(lineas)) return(",")
  candidatos <- c(";", ",", "\t", "|")
  conteos <- vapply(candidatos, function(sep) {
    sum(vapply(lineas, function(l) lengths(regmatches(l, gregexpr(sep, l, fixed = TRUE))), numeric(1)))
  }, numeric(1))
  if (max(conteos) == 0) "," else candidatos[which.max(conteos)]
}

#' Lee el archivo sin interpretar encabezados.
#'
#' Devuelve las columnas como listas de celdas, conservando el tipo original de
#' cada valor (fecha, número o texto), igual que se ve en Excel.
leer_crudo <- function(ruta, nombre = basename(ruta), hoja = NULL) {
  formato <- .formato_archivo(ruta, nombre)

  # Tabla HTML con nombre de Excel: se extrae la tabla y se sigue igual.
  if (identical(formato, "html")) {
    columnas <- .leer_tabla_html(ruta)
    if (is.null(columnas) || !length(columnas)) {
      stop("El archivo parece una página web, pero no contiene ninguna tabla legible.")
    }
    return(list(columnas = columnas, n_filas = length(columnas[[1]]), hoja = NA_character_))
  }

  if (identical(formato, "texto") || is.na(formato)) {
    contenido <- leer_texto_utf8(ruta)
    separador <- .detectar_separador(contenido)
    tabla <- utils::read.table(
      text = contenido, sep = separador, header = FALSE, quote = "\"",
      colClasses = "character", fill = TRUE, comment.char = "",
      check.names = FALSE, stringsAsFactors = FALSE, na.strings = character(0)
    )
    return(list(columnas = lapply(tabla, as.list), n_filas = nrow(tabla), hoja = NA_character_))
  }

  legible <- .ruta_legible(ruta, nombre)
  hojas <- readxl::excel_sheets(legible)
  hoja_usar <- if (!is.null(hoja) && length(hoja) == 1 && hoja %in% hojas) hoja else hojas[1]
  crudo <- suppressMessages(readxl::read_excel(
    legible, sheet = hoja_usar, col_names = FALSE, col_types = "list"
  ))
  list(columnas = as.list(crudo), n_filas = nrow(crudo), hoja = hoja_usar)
}

#' Ubica la fila de encabezados: la que reconoce más campos del catálogo.
detectar_encabezado <- function(columnas, n_filas, filas_a_revisar = 25) {
  if (!length(columnas) || n_filas == 0) return(NA_integer_)
  mejor_fila <- NA_integer_
  mejor_puntaje <- 0L
  for (i in seq_len(min(filas_a_revisar, n_filas))) {
    fila <- vapply(columnas, function(col) a_texto(col[[i]]), character(1), USE.NAMES = FALSE)
    encontrados <- unique(campo_de_encabezado(fila))
    encontrados <- encontrados[!is.na(encontrados)]
    if (length(encontrados) > mejor_puntaje) {
      mejor_puntaje <- length(encontrados)
      mejor_fila <- i
    }
  }
  if (mejor_puntaje >= 2) mejor_fila else NA_integer_
}

#' Encabezado real -> campo canónico. Devuelve también los ignorados.
mapear_columnas <- function(encabezados) {
  campos_detectados <- campo_de_encabezado(encabezados)
  detectadas <- character(0)
  ignoradas <- character(0)
  posiciones <- list()

  for (i in seq_along(encabezados)) {
    campo <- campos_detectados[i]
    titulo <- encabezados[i]
    if (!is.na(campo) && is.null(posiciones[[campo]])) {
      posiciones[[campo]] <- i
      detectadas[if (nzchar(titulo)) titulo else campo] <- campo
    } else if (nzchar(titulo)) {
      ignoradas <- c(ignoradas, titulo)
    }
  }
  list(detectadas = detectadas, ignoradas = ignoradas, posiciones = posiciones)
}

# ---------------------------------------------------------------------------
# Depuración
# ---------------------------------------------------------------------------

.datos_vacios <- function() {
  vacio <- data.frame(fila_origen = integer(0), stringsAsFactors = FALSE)
  vacio$fecha <- as.Date(character(0))
  for (campo in setdiff(CAMPOS_SALIDA, "fecha")) {
    vacio[[campo]] <- if (campo %in% c("anio", "mes")) integer(0)
                      else if (campo %in% c("monto", "tipo_cambio", "monto_usd")) numeric(0)
                      else character(0)
  }
  vacio
}

.resultado_vacio <- function(lectura, filas_origen = 0) {
  list(
    datos = .datos_vacios(),
    descartes = data.frame(fila_origen = integer(0), motivo = character(0), stringsAsFactors = FALSE),
    columnas_detectadas = character(0),
    columnas_ignoradas = character(0),
    columnas_faltantes = campos_requeridos(),
    filas_origen = filas_origen,
    fila_encabezado = NA_integer_,
    hoja = lectura$hoja,
    filas_depuradas = 0L,
    filas_descartadas = 0L,
    monto_usd = 0
  )
}

#' Determina el equivalente en USD y el factor aplicado.
.resolver_usd <- function(monto, monto_usd, moneda, tipo_cambio, periodo) {
  n <- length(monto)
  usd <- monto_usd
  factor <- tipo_cambio

  # El archivo ya trae el monto en USD: se respeta y se deduce el factor.
  trae_usd <- !is.na(monto_usd)
  sin_factor <- trae_usd & is.na(factor) & !is.na(monto) & monto != 0
  factor[sin_factor] <- round(monto_usd[sin_factor] / monto[sin_factor], 8)

  # Operaciones en dólares: el monto ya está en la moneda del tablero.
  en_dolares <- !trae_usd & !is.na(monto) & (is.na(moneda) | moneda %in% c("USD", "", "SIN MONEDA"))
  usd[en_dolares] <- monto[en_dolares]
  factor[en_dolares] <- 1

  # Con tipo de cambio en la propia fila.
  con_tc <- !trae_usd & !en_dolares & !is.na(monto) & !is.na(tipo_cambio) & tipo_cambio > 0
  usd[con_tc] <- monto[con_tc] * tipo_cambio[con_tc]

  # Último recurso: el catálogo de tipos de cambio del período.
  pendientes <- which(is.na(usd) & !is.na(monto))
  for (i in pendientes) {
    del_catalogo <- tipo_cambio(moneda[i], periodo)
    if (!is.na(del_catalogo) && del_catalogo > 0) {
      usd[i] <- monto[i] * del_catalogo
      factor[i] <- del_catalogo
    }
  }
  list(monto_usd = usd, tipo_cambio = factor)
}

#' Lee, mapea y depura un archivo para el módulo y período indicados.
procesar <- function(ruta, modulo, periodo, nombre = basename(ruta), hoja = NULL) {
  lectura <- leer_crudo(ruta, nombre = nombre, hoja = hoja)
  columnas <- lectura$columnas
  n_filas <- lectura$n_filas

  fila_encabezado <- detectar_encabezado(columnas, n_filas)
  if (is.na(fila_encabezado)) return(.resultado_vacio(lectura, n_filas))

  encabezados <- vapply(columnas, function(col) a_texto(col[[fila_encabezado]]),
                        character(1), USE.NAMES = FALSE)
  mapa <- mapear_columnas(encabezados)
  faltantes <- setdiff(campos_requeridos(), names(mapa$posiciones))

  indices <- seq_len(n_filas)[-seq_len(fila_encabezado)]
  if (!length(indices)) {
    vacio <- .resultado_vacio(lectura, 0)
    vacio$columnas_detectadas <- mapa$detectadas
    vacio$columnas_ignoradas <- mapa$ignoradas
    vacio$columnas_faltantes <- faltantes
    vacio$fila_encabezado <- fila_encabezado
    return(vacio)
  }

  textos <- lapply(columnas, function(col) a_texto_vec(col[indices]))
  numero_fila <- indices

  celdas <- function(campo) {
    posicion <- mapa$posiciones[[campo]]
    if (is.null(posicion)) NULL else columnas[[posicion]][indices]
  }
  texto_de <- function(campo) {
    posicion <- mapa$posiciones[[campo]]
    if (is.null(posicion)) rep("", length(indices)) else textos[[posicion]]
  }

  fecha <- if (is.null(celdas("fecha"))) rep(as.Date(NA), length(indices)) else a_fecha_vec(celdas("fecha"))
  monto <- if (is.null(celdas("monto"))) rep(NA_real_, length(indices)) else a_numero_vec(celdas("monto"), punto_es_miles = TRUE)
  monto_usd <- if (is.null(celdas("monto_usd"))) rep(NA_real_, length(indices)) else a_numero_vec(celdas("monto_usd"), punto_es_miles = TRUE)
  tipo_cambio_fila <- if (is.null(celdas("tipo_cambio"))) rep(NA_real_, length(indices)) else a_numero_vec(celdas("tipo_cambio"))
  moneda <- normalizar_moneda(texto_de("moneda"))

  convertido <- .resolver_usd(monto, monto_usd, moneda, tipo_cambio_fila, periodo)

  # Clasificación de cada fila, en orden de prioridad.
  vacia <- !Reduce(`|`, lapply(textos, nzchar))
  primeras <- textos[seq_len(min(3, length(textos)))]
  primeros_textos <- normalizar(do.call(paste, c(primeras, list(sep = " "))))
  marcadores_total <- filas_no_dato()
  es_total <- if (length(marcadores_total)) {
    grepl(paste0("^(", paste(.escapar_regex(marcadores_total), collapse = "|"), ")"), primeros_textos)
  } else {
    rep(FALSE, length(indices))
  }

  motivo <- rep(NA_character_, length(indices))
  motivo[is.na(motivo) & es_total] <- MOTIVO_TOTALES
  motivo[is.na(motivo) & is.na(fecha)] <- MOTIVO_FECHA
  motivo[is.na(motivo) & is.na(monto) & is.na(monto_usd)] <- MOTIVO_MONTO
  motivo[is.na(motivo) & is.na(convertido$monto_usd)] <- MOTIVO_SIN_TC

  validas <- !vacia & is.na(motivo)
  descartadas <- !vacia & !is.na(motivo)

  mayusculas <- function(campo) toupper(texto_de(campo))
  corresponsal <- mayusculas("corresponsal")
  corresponsal[!nzchar(corresponsal)] <- "SIN CORRESPONSAL"
  moneda_final <- moneda
  moneda_final[!nzchar(moneda_final)] <- "SIN MONEDA"
  monto_final <- ifelse(is.na(monto), convertido$monto_usd, monto)

  datos <- data.frame(
    fila_origen  = numero_fila[validas],
    fecha        = fecha[validas],
    anio         = as.integer(format(fecha[validas], "%Y")),
    mes          = as.integer(format(fecha[validas], "%m")),
    stringsAsFactors = FALSE
  )
  datos$nombre_mes   <- MESES[datos$mes]
  datos$referencia   <- texto_de("referencia")[validas]
  datos$corresponsal <- corresponsal[validas]
  datos$area_750     <- mayusculas("area_750")[validas]
  datos$deuda_771    <- mayusculas("deuda_771")[validas]
  datos$tipo_mensaje <- normalizar_tipo_mensaje(texto_de("tipo_mensaje"))[validas]
  datos$proceso      <- mayusculas("proceso")[validas]
  datos$moneda       <- moneda_final[validas]
  datos$monto        <- monto_final[validas]
  datos$tipo_cambio  <- convertido$tipo_cambio[validas]
  datos$monto_usd    <- convertido$monto_usd[validas]
  datos$beneficiario <- texto_de("beneficiario")[validas]
  datos$estado       <- mayusculas("estado")[validas]
  datos$sentido      <- texto_de("sentido")[validas]

  descartes <- data.frame(
    fila_origen = numero_fila[descartadas],
    motivo = motivo[descartadas],
    stringsAsFactors = FALSE
  )
  if (any(descartadas)) {
    titulos <- make.unique(ifelse(nzchar(encabezados), encabezados, paste0("col_", seq_along(encabezados))))
    for (j in seq_along(textos)) {
      if (nzchar(encabezados[j])) descartes[[titulos[j]]] <- textos[[j]][descartadas]
    }
  }

  list(
    datos = datos,
    descartes = descartes,
    columnas_detectadas = mapa$detectadas,
    columnas_ignoradas = mapa$ignoradas,
    columnas_faltantes = faltantes,
    filas_origen = length(indices),
    fila_encabezado = fila_encabezado,
    hoja = lectura$hoja,
    filas_depuradas = nrow(datos),
    filas_descartadas = nrow(descartes),
    monto_usd = if (nrow(datos)) sum(datos$monto_usd, na.rm = TRUE) else 0
  )
}
