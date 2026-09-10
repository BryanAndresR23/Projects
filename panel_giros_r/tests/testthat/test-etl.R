# Pruebas del ETL: conversores, detección de encabezado y depuración.

test_that("a_numero tolera los formatos mixtos de los reportes", {
  expect_equal(a_numero("1.234,56"), 1234.56)
  expect_equal(a_numero("1,234.56"), 1234.56)
  expect_equal(a_numero("$ 1 234,56"), 1234.56)
  expect_equal(a_numero("(1.234,56)"), -1234.56)
  expect_equal(a_numero("1234"), 1234)
  expect_true(is.na(a_numero("")))
  expect_true(is.na(a_numero("abc")))
  expect_true(is.na(a_numero(NA)))
})

test_that("a_numero distingue separador de miles de decimal", {
  expect_equal(a_numero("1.500", punto_es_miles = TRUE), 1500)  # importe
  expect_equal(a_numero("1.085"), 1.085)                        # tipo de cambio
})

test_that("a_fecha reconoce textos, seriales de Excel y basura", {
  expect_equal(format(a_fecha("15/06/2026")), "2026-06-15")
  expect_equal(format(a_fecha("2026-06-15")), "2026-06-15")
  expect_equal(format(a_fecha("15-06-2026")), "2026-06-15")
  expect_equal(format(a_fecha(45823)), "2025-06-15")
  expect_true(is.na(a_fecha("no es fecha")))
  expect_true(is.na(a_fecha(NA)))
})

test_that("normalizar_tipo_mensaje deja TF o GS", {
  expect_equal(normalizar_tipo_mensaje("TF-01"), "TF")
  expect_equal(normalizar_tipo_mensaje("mensaje gs"), "GS")
  expect_equal(normalizar_tipo_mensaje("otro"), "OTRO")
})

test_that("encuentra el encabezado aunque el reporte traiga títulos arriba", {
  carpeta <- entorno_aislado()
  resultado <- procesar(archivo_al(carpeta), modulo = "GIROS_AL", periodo = "2026-06")
  expect_equal(resultado$fila_encabezado, 4)
  expect_equal(resultado$filas_depuradas, 3)
  expect_length(resultado$columnas_faltantes, 0)
})

test_that("mapea columnas aunque el reporte las llame de otro modo", {
  carpeta <- entorno_aislado()
  tabla <- tabla_base()
  names(tabla) <- c("Fecha valor", "Referencia", "Sentido", "Entidad", "Área 750", "Deuda 771",
                    "Tipo mensaje", "Proceso", "Divisa", "Importe", "Cotización",
                    "Equivalente USD", "Beneficiario", "Estado")
  ruta <- escribir_excel_prueba(file.path(carpeta, "otro.xlsx"), tabla, titulos = 0)
  resultado <- procesar(ruta, modulo = "GIROS_AL", periodo = "2026-06")
  expect_equal(resultado$filas_depuradas, 3)
  expect_true(all(c("fecha", "referencia", "corresponsal", "moneda", "monto", "monto_usd")
                  %in% unname(resultado$columnas_detectadas)))
})

test_that("descarta totales y filas inválidas", {
  carpeta <- entorno_aislado()
  tabla <- tabla_base()
  total <- tabla[1, ]; total[["FECHA DE OPERACIÓN"]] <- "TOTAL"
  sin_monto <- tabla[1, ]; sin_monto[["MONTO"]] <- NA; sin_monto[["MONTO USD"]] <- NA
  ruta <- escribir_excel_prueba(file.path(carpeta, "ruido.xlsx"), rbind(tabla, total, sin_monto))
  resultado <- procesar(ruta, modulo = "GIROS_AL", periodo = "2026-06")

  expect_equal(resultado$filas_depuradas, 3)
  expect_true(MOTIVO_TOTALES %in% resultado$descartes$motivo)
  expect_true(MOTIVO_MONTO %in% resultado$descartes$motivo)
})

test_that("convierte a USD con el tipo de cambio de la propia fila", {
  carpeta <- entorno_aislado()
  tabla <- tabla_base()[2, ]           # la fila en euros
  tabla[["MONTO USD"]] <- NA
  ruta <- escribir_excel_prueba(file.path(carpeta, "sin_usd.xlsx"), tabla)
  resultado <- procesar(ruta, modulo = "GIROS_AL", periodo = "2026-06")
  expect_equal(resultado$filas_depuradas, 1)
  expect_equal(round(resultado$datos$monto_usd[1], 2), 216000)
})

test_that("descarta la moneda que no se puede convertir a USD", {
  carpeta <- entorno_aislado()
  tabla <- tabla_base()[2, ]
  tabla[["MONTO USD"]] <- NA
  tabla[["TIPO DE CAMBIO"]] <- NA
  ruta <- escribir_excel_prueba(file.path(carpeta, "sin_tc.xlsx"), tabla)
  resultado <- procesar(ruta, modulo = "GIROS_AL", periodo = "2026-06")
  expect_equal(resultado$filas_depuradas, 0)
  expect_true(MOTIVO_SIN_TC %in% resultado$descartes$motivo)
})

test_that("un archivo sin encabezado reconocible no rompe el ETL", {
  carpeta <- entorno_aislado()
  ruta <- escribir_excel_prueba(file.path(carpeta, "vacio.xlsx"),
                                data.frame(A = 1, B = 2), titulos = 0)
  resultado <- procesar(ruta, modulo = "GIROS_AL", periodo = "2026-06")
  expect_true(is.na(resultado$fila_encabezado))
  expect_equal(resultado$filas_depuradas, 0)
})

test_that("lee CSV con separador de punto y coma", {
  carpeta <- entorno_aislado()
  ruta <- file.path(carpeta, "giros.csv")
  utils::write.table(tabla_base(), ruta, sep = ";", row.names = FALSE,
                     fileEncoding = "UTF-8", quote = TRUE)
  resultado <- procesar(ruta, modulo = "GIROS_AL", periodo = "2026-06")
  expect_equal(resultado$filas_depuradas, 3)
})

test_that("lee el archivo aunque la extensión no corresponda al contenido", {
  # Shiny guarda lo que se sube con un nombre temporal como "0.xls", sin
  # importar el formato real. El ETL debe guiarse por el contenido.
  carpeta <- entorno_aislado()
  original <- archivo_al(carpeta)
  disfrazado <- file.path(carpeta, "0.xls")
  file.copy(original, disfrazado, overwrite = TRUE)

  expect_equal(.formato_excel(disfrazado, "Giros_AL_2026-06.xlsx"), "xlsx")
  resultado <- procesar(disfrazado, modulo = "GIROS_AL", periodo = "2026-06",
                        nombre = "Giros_AL_2026-06.xlsx")
  expect_equal(resultado$filas_depuradas, 3)
  expect_equal(hojas_disponibles(disfrazado, "Giros_AL_2026-06.xlsx"), "Hoja1")
})

test_that("un archivo que no es Excel ni CSV no se confunde con uno válido", {
  carpeta <- entorno_aislado()
  basura <- file.path(carpeta, "0.xls")
  writeLines("esto no es un excel", basura)
  expect_true(is.na(.formato_excel(basura, "cualquier.cosa")))
})
