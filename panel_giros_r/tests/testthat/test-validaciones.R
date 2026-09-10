# Pruebas de las reglas de validación, en especial la coherencia de módulo.

hallazgos_de <- function(ruta, modulo = "GIROS_AL", periodo = "2026-06", nombre = NULL) {
  resultado <- procesar(ruta, modulo = modulo, periodo = periodo)
  validar(resultado, modulo = modulo, periodo = periodo,
          nombre_archivo = if (is.null(nombre)) basename(ruta) else nombre)
}

reglas <- function(hallazgos, severidad = NULL) {
  if (is.null(severidad)) return(hallazgos$regla)
  hallazgos$regla[hallazgos$severidad == severidad]
}

test_that("un archivo correcto no tiene errores", {
  carpeta <- entorno_aislado()
  hallazgos <- hallazgos_de(archivo_al(carpeta))
  expect_length(reglas(hallazgos, SEVERIDAD_ERROR), 0)
  expect_true("MODULO_COINCIDE" %in% reglas(hallazgos))
  expect_false(identical(estado_de(hallazgos), ESTADO_RECHAZADA))
})

test_that("rechaza un archivo DEL cargado como AL", {
  carpeta <- entorno_aislado()
  hallazgos <- hallazgos_de(archivo_del(carpeta), modulo = "GIROS_AL")
  expect_true("MODULO_NO_COINCIDE" %in% reglas(hallazgos, SEVERIDAD_ERROR))
  expect_equal(estado_de(hallazgos), ESTADO_RECHAZADA)
})

test_that("rechaza un archivo AL cargado como DEL", {
  carpeta <- entorno_aislado()
  hallazgos <- hallazgos_de(archivo_al(carpeta), modulo = "GIROS_DEL")
  expect_true("MODULO_NO_COINCIDE" %in% reglas(hallazgos, SEVERIDAD_ERROR))
})

test_that("advierte cuando el archivo mezcla sentidos", {
  carpeta <- entorno_aislado()
  tabla <- tabla_base()
  tabla$SENTIDO[1] <- "Giros DEL"
  ruta <- escribir_excel_prueba(file.path(carpeta, "mezclado.xlsx"), tabla)
  hallazgos <- hallazgos_de(ruta, modulo = "GIROS_AL")
  expect_true("MODULO_MEZCLADO" %in% reglas(hallazgos, SEVERIDAD_ADVERTENCIA))
})

test_that("sin columna de sentido usa el nombre del archivo", {
  carpeta <- entorno_aislado()
  tabla <- tabla_base()[, setdiff(names(tabla_base()), "SENTIDO")]
  ruta <- escribir_excel_prueba(file.path(carpeta, "Giros_DEL_junio.xlsx"), tabla)
  hallazgos <- hallazgos_de(ruta, modulo = "GIROS_AL")
  expect_true("MODULO_NOMBRE_ARCHIVO" %in% reglas(hallazgos, SEVERIDAD_ADVERTENCIA))
})

test_that("sin evidencia de módulo se registra la declaración del validador", {
  carpeta <- entorno_aislado()
  tabla <- tabla_base()[, setdiff(names(tabla_base()), "SENTIDO")]
  ruta <- escribir_excel_prueba(file.path(carpeta, "reporte_mensual.xlsx"), tabla)
  hallazgos <- hallazgos_de(ruta, modulo = "GIROS_AL")
  expect_true("MODULO_SIN_EVIDENCIA" %in% reglas(hallazgos))
})

test_that("rechaza el período equivocado", {
  carpeta <- entorno_aislado()
  hallazgos <- hallazgos_de(archivo_al(carpeta), periodo = "2026-03")
  expect_true("PERIODO_NO_COINCIDE" %in% reglas(hallazgos, SEVERIDAD_ERROR))
})

test_that("advierte el período parcial", {
  carpeta <- entorno_aislado()
  tabla <- tabla_base()
  tabla[["FECHA DE OPERACIÓN"]][1] <- "28/05/2026"
  ruta <- escribir_excel_prueba(file.path(carpeta, "parcial.xlsx"), tabla)
  hallazgos <- hallazgos_de(ruta)
  expect_true("PERIODO_PARCIAL" %in% reglas(hallazgos, SEVERIDAD_ADVERTENCIA))
})

test_that("detecta referencias duplicadas", {
  carpeta <- entorno_aislado()
  tabla <- tabla_base()
  tabla[["N° OPERACIÓN"]][2] <- tabla[["N° OPERACIÓN"]][1]
  ruta <- escribir_excel_prueba(file.path(carpeta, "duplicados.xlsx"), tabla)
  hallazgos <- hallazgos_de(ruta)
  expect_true("REFERENCIAS_DUPLICADAS" %in% reglas(hallazgos, SEVERIDAD_ADVERTENCIA))
})

test_that("detecta la falta de columnas obligatorias", {
  carpeta <- entorno_aislado()
  tabla <- tabla_base()
  tabla <- tabla[, setdiff(names(tabla), c("MONEDA", "MONTO", "MONTO USD"))]
  ruta <- escribir_excel_prueba(file.path(carpeta, "incompleto.xlsx"), tabla)
  hallazgos <- hallazgos_de(ruta)
  expect_true("ESTRUCTURA_COLUMNAS" %in% reglas(hallazgos, SEVERIDAD_ERROR))
})

test_that("el estado se deduce de la severidad", {
  uno <- function(severidad) {
    data.frame(regla = "X", severidad = severidad, mensaje = "m",
               filas_afectadas = 0L, detalle = NA_character_, stringsAsFactors = FALSE)
  }
  expect_equal(estado_de(uno(SEVERIDAD_ERROR)), ESTADO_RECHAZADA)
  expect_equal(estado_de(uno(SEVERIDAD_ADVERTENCIA)), ESTADO_ADVERTENCIAS)
  expect_equal(estado_de(uno(SEVERIDAD_INFO)), ESTADO_VALIDA)
})
