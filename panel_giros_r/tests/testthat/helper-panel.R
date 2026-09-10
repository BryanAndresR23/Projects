# ---------------------------------------------------------------------------
# Carga el panel para las pruebas, con el mismo arranque que usa app.R.
# ---------------------------------------------------------------------------

if (!grepl("UTF-8", Sys.getlocale("LC_CTYPE"), ignore.case = TRUE)) {
  for (locale in c("es_EC.UTF-8", "es_ES.UTF-8", "en_US.UTF-8", "C.UTF-8", "C.utf8")) {
    if (nzchar(suppressWarnings(Sys.setlocale("LC_CTYPE", locale)))) break
  }
}

# La raíz del proyecto es la carpeta que contiene "nucleo".
RAIZ_PANEL <- local({
  candidatos <- c(Sys.getenv("GIROS_RAIZ"), getwd(), file.path(getwd(), ".."),
                  file.path(getwd(), "..", ".."))
  for (candidato in candidatos) {
    if (nzchar(candidato) && dir.exists(file.path(candidato, "nucleo"))) {
      return(normalizePath(candidato))
    }
  }
  stop("No se encontró la raíz del panel (la carpeta que contiene 'nucleo').")
})

for (archivo in list.files(file.path(RAIZ_PANEL, "nucleo"), pattern = "\\.R$", full.names = TRUE)) {
  source(archivo, encoding = "UTF-8")
}

#' Cada prueba corre sobre su propia carpeta de datos y su propia base.
entorno_aislado <- function() {
  carpeta <- tempfile("giros_prueba_")
  dir.create(carpeta, recursive = TRUE, showWarnings = FALSE)
  Sys.setenv(GIROS_DIR_DATOS = carpeta)
  Sys.setenv(GIROS_USUARIO = "pruebas")
  Sys.setenv(GIROS_DIR_CATALOGOS = file.path(RAIZ_PANEL, "catalogos"))
  recargar_catalogos()
  carpeta
}

FILAS_BASE <- list(
  list("05/06/2026", "TF-01-7712600001", "Giros AL", "CITIBANK N.A.", "750-01", "771-100",
       "TF", "PAGO DEUDA EXTERNA", "USD", 1500000.50, 1, 1500000.50, "ACREEDOR 101", "PROCESADO"),
  list("12/06/2026", "GS-01-7712600002", "Giros AL", "JP MORGAN CHASE", "750-02", "771-200",
       "GS", "INTERESES", "EUR", 200000, 1.08, 216000, "ACREEDOR 202", "PROCESADO"),
  list("20/06/2026", "TF-01-7712600003", "Giros AL", "BBVA", "750-01", "771-300",
       "TF", "AMORTIZACION", "USD", 750000, 1, 750000, "ACREEDOR 303", "PENDIENTE")
)

COLUMNAS_BASE <- c("FECHA DE OPERACIÓN", "N° OPERACIÓN", "SENTIDO", "BANCO CORRESPONSAL",
                   "ÁREA 750", "DEUDA 771", "TIPO DE MENSAJE", "PROCESO", "MONEDA",
                   "MONTO", "TIPO DE CAMBIO", "MONTO USD", "BENEFICIARIO", "ESTADO")

#' Arma el data.frame de prueba a partir de FILAS_BASE.
tabla_base <- function(filas = FILAS_BASE, columnas = COLUMNAS_BASE) {
  marco <- as.data.frame(do.call(rbind, lapply(filas, function(f) {
    fila <- as.data.frame(f, stringsAsFactors = FALSE)
    names(fila) <- columnas
    fila
  })), stringsAsFactors = FALSE)
  names(marco) <- columnas
  marco
}

#' Crea un Excel con filas de título antes del encabezado, como los reportes reales.
escribir_excel_prueba <- function(ruta, tabla, titulos = 2) {
  libro <- openxlsx::createWorkbook()
  openxlsx::addWorksheet(libro, "Hoja1")
  if (titulos > 0) {
    openxlsx::writeData(libro, "Hoja1",
                        data.frame(rep("REPORTE DE GIROS", titulos), stringsAsFactors = FALSE),
                        startRow = 1, colNames = FALSE)
  }
  openxlsx::writeData(libro, "Hoja1", tabla, startRow = titulos + 2, colNames = TRUE)
  openxlsx::saveWorkbook(libro, ruta, overwrite = TRUE)
  ruta
}

archivo_al <- function(carpeta = tempdir()) {
  escribir_excel_prueba(file.path(carpeta, "Giros_AL_2026-06.xlsx"), tabla_base())
}

archivo_del <- function(carpeta = tempdir()) {
  tabla <- tabla_base()
  tabla$SENTIDO <- "Giros DEL"
  escribir_excel_prueba(file.path(carpeta, "Giros_DEL_2026-06.xlsx"), tabla)
}
