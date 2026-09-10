# ---------------------------------------------------------------------------
# Rutas, catálogos base y parámetros generales del panel.
# ---------------------------------------------------------------------------

# Módulos que el validador debe elegir de forma explícita al cargar.
MODULOS <- c(GIROS_AL = "Giros AL", GIROS_DEL = "Giros DEL")

MESES <- c("Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
           "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre")

# Estados posibles de una carga.
ESTADO_VALIDA       <- "VALIDA"
ESTADO_ADVERTENCIAS <- "CON ADVERTENCIAS"
ESTADO_RECHAZADA    <- "RECHAZADA"

SEVERIDAD_ERROR       <- "ERROR"
SEVERIDAD_ADVERTENCIA <- "ADVERTENCIA"
SEVERIDAD_INFO        <- "INFO"

#' Asegura un locale UTF-8.
#'
#' Los catálogos y los reportes traen tildes y la ñ. Si R arranca en locale C
#' (habitual en servidores Linux recién instalados) esos caracteres se leen mal.
#' Se intenta fijar un locale UTF-8; si no hay ninguno, se avisa en el log para
#' que el administrador lo instale (por ejemplo `locale-gen es_EC.UTF-8`).
configurar_locale <- function() {
  if (grepl("UTF-8", Sys.getlocale("LC_CTYPE"), ignore.case = TRUE)) return(invisible(TRUE))
  for (locale in c("es_EC.UTF-8", "es_ES.UTF-8", "en_US.UTF-8", "C.UTF-8", "C.utf8")) {
    fijado <- suppressWarnings(Sys.setlocale("LC_CTYPE", locale))
    if (nzchar(fijado)) return(invisible(TRUE))
  }
  warning("No se encontró un locale UTF-8; las tildes pueden verse mal.")
  invisible(FALSE)
}

#' Lee un archivo de texto siempre como UTF-8, sin depender del locale.
leer_texto_utf8 <- function(ruta) {
  contenido <- rawToChar(readBin(ruta, "raw", n = file.size(ruta)))
  Encoding(contenido) <- "UTF-8"
  contenido
}

#' Carpeta donde viven la base y los archivos de cada carga.
#'
#' En el servidor debe apuntar a una ruta compartida y con permiso de escritura
#' para el usuario que ejecuta Shiny. Se configura con la variable de entorno
#' GIROS_DIR_DATOS (por ejemplo en /etc/environment o en el Renviron.site).
dir_datos <- function() {
  ruta <- Sys.getenv("GIROS_DIR_DATOS")
  if (nzchar(ruta)) ruta else file.path(getwd(), "datos")
}

dir_cargas <- function() file.path(dir_datos(), "cargas")

ruta_bd <- function() file.path(dir_datos(), "giros.db")

dir_catalogos <- function() {
  ruta <- Sys.getenv("GIROS_DIR_CATALOGOS")
  if (nzchar(ruta)) ruta else file.path(getwd(), "catalogos")
}

#' Usuario que queda registrado en el histórico y en la bitácora.
#'
#' En un servidor con autenticación (Shiny Server Pro, Posit Connect o un proxy
#' que envíe la cabecera correspondiente) se toma el usuario de la sesión; así
#' el histórico registra quién cargó de verdad y no el usuario del servicio.
usuario_actual <- function(sesion = NULL) {
  if (!is.null(sesion)) {
    if (!is.null(sesion$user) && nzchar(sesion$user)) return(sesion$user)
    cabecera <- sesion$request$HTTP_X_FORWARDED_USER
    if (!is.null(cabecera) && nzchar(cabecera)) return(cabecera)
  }
  usuario <- Sys.getenv("GIROS_USUARIO")
  if (nzchar(usuario)) return(usuario)
  usuario <- tryCatch(unname(Sys.info()[["user"]]), error = function(e) "")
  if (!is.null(usuario) && nzchar(usuario)) usuario else "desconocido"
}

etiqueta_modulo <- function(modulo) {
  etiqueta <- unname(MODULOS[modulo])
  ifelse(is.na(etiqueta), as.character(modulo), etiqueta)
}

#' Período canónico "AAAA-MM".
periodo <- function(anio, mes) sprintf("%04d-%02d", as.integer(anio), as.integer(mes))

partes_periodo <- function(valor) {
  partes <- strsplit(as.character(valor), "-", fixed = TRUE)[[1]]
  if (length(partes) != 2) stop("El período debe tener el formato AAAA-MM: ", valor)
  anio <- suppressWarnings(as.integer(partes[1]))
  mes  <- suppressWarnings(as.integer(partes[2]))
  if (is.na(anio) || is.na(mes) || mes < 1 || mes > 12) {
    stop("El período debe tener el formato AAAA-MM: ", valor)
  }
  list(anio = anio, mes = mes)
}

etiqueta_periodo <- function(valor) {
  vapply(valor, function(v) {
    if (is.na(v) || !nzchar(as.character(v))) return("")
    p <- tryCatch(partes_periodo(v), error = function(e) NULL)
    if (is.null(p)) return(as.character(v))
    paste(MESES[p$mes], p$anio)
  }, character(1), USE.NAMES = FALSE)
}
