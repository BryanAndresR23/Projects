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

test_that("lee una tabla HTML guardada con nombre .xls", {
  # Muchos sistemas de reportes exportan HTML con extensión .xls: Excel lo abre,
  # pero las librerías de lectura de Excel no.
  carpeta <- entorno_aislado()
  html <- paste0(
    "<html><body><h2>REPORTE DE GIROS AL</h2><table>",
    "<tr><th>FECHA DE OPERACI&Oacute;N</th><th>N&deg; OPERACI&Oacute;N</th><th>SENTIDO</th>",
    "<th>BANCO CORRESPONSAL</th><th>MONEDA</th><th>MONTO</th><th>MONTO USD</th></tr>",
    "<tr><td>05/06/2026</td><td>TF-01-7712600001</td><td>Giros AL</td>",
    "<td>CITIBANK N.A.</td><td>USD</td><td>1.500.000,50</td><td>1.500.000,50</td></tr>",
    "<tr><td>12/06/2026</td><td>GS-01-7712600002</td><td>Giros AL</td>",
    "<td>BBVA</td><td>USD</td><td>750.000,00</td><td>750.000,00</td></tr>",
    "<tr><td>TOTAL</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td>",
    "<td>&nbsp;</td><td>2.250.000,50</td></tr></table></body></html>")
  ruta <- file.path(carpeta, "GIROS AL - JUNIO.xls")
  writeLines(html, ruta, useBytes = TRUE)

  expect_equal(.formato_archivo(ruta, "GIROS AL - JUNIO.xls"), "html")
  resultado <- procesar(ruta, modulo = "GIROS_AL", periodo = "2026-06",
                        nombre = "GIROS AL - JUNIO.xls")
  expect_equal(resultado$filas_depuradas, 2)
  expect_equal(round(resultado$monto_usd, 2), 2250000.50)
  expect_true(MOTIVO_TOTALES %in% resultado$descartes$motivo)
  # Los acentos codificados no deben impedir reconocer las columnas.
  expect_true(all(c("fecha", "referencia", "corresponsal", "moneda", "monto")
                  %in% unname(resultado$columnas_detectadas)))
  expect_equal(resultado$datos$referencia[1], "TF-01-7712600001")
})

test_that("lee un texto separado por tabuladores guardado con nombre .xls", {
  carpeta <- entorno_aislado()
  ruta <- file.path(carpeta, "GIROS AL - JUNIO tsv.xls")
  writeLines(c(
    "FECHA DE OPERACIÓN\tN° OPERACIÓN\tSENTIDO\tBANCO CORRESPONSAL\tMONEDA\tMONTO\tMONTO USD",
    "05/06/2026\tTF-01-9001\tGiros AL\tBBVA\tUSD\t1000,50\t1000,50",
    "06/06/2026\tGS-01-9002\tGiros AL\tCITIBANK N.A.\tUSD\t2000,00\t2000,00"
  ), ruta, useBytes = TRUE)

  expect_equal(.formato_archivo(ruta, "GIROS AL - JUNIO tsv.xls"), "texto")
  resultado <- procesar(ruta, modulo = "GIROS_AL", periodo = "2026-06",
                        nombre = "GIROS AL - JUNIO tsv.xls")
  expect_equal(resultado$filas_depuradas, 2)
  expect_equal(round(resultado$monto_usd, 2), 3000.50)
})

test_that("traduce las entidades HTML de los encabezados", {
  entorno_aislado()
  expect_equal(.desescapar_html("N&deg; OPERACI&Oacute;N"), "N° OPERACIÓN")
  expect_equal(.desescapar_html("&Aacute;REA 750"), "ÁREA 750")
  expect_equal(.desescapar_html("A&#209;O"), "AÑO")
  expect_equal(.desescapar_html("&#xE1;rea"), "área")
})
