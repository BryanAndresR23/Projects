# Pruebas de las agregaciones que alimentan el tablero.

datos_publicados <- function() {
  carpeta <- entorno_aislado()
  registrar_carga(archivo_al(carpeta), modulo = "GIROS_AL", periodo = "2026-06")
  registrar_carga(archivo_del(carpeta), modulo = "GIROS_DEL", periodo = "2026-06")
  operaciones(solo_activas = TRUE)
}

test_that("los KPI cuadran con las filas publicadas", {
  datos <- datos_publicados()
  indicadores <- kpis(datos)
  expect_equal(indicadores$operaciones, 6)
  esperado <- (1500000.50 + 216000 + 750000) * 2  # las mismas filas en AL y en DEL
  expect_equal(round(indicadores$monto_usd, 2), round(esperado, 2))
  expect_equal(indicadores$corresponsales, 3)
})

test_that("agregar por número de operaciones", {
  tabla <- agregar(datos_publicados(), "tipo_mensaje", "operaciones")
  valores <- stats::setNames(tabla$valor, tabla$etiqueta)
  expect_equal(unname(valores[["TF"]]), 4)
  expect_equal(unname(valores[["GS"]]), 2)
})

test_that("agregar por monto conserva el total", {
  datos <- datos_publicados()
  tabla <- agregar(datos, "modulo", "monto_usd")
  expect_setequal(tabla$etiqueta, c("Giros AL", "Giros DEL"))
  expect_equal(round(sum(tabla$valor), 2), round(sum(datos$monto_usd), 2))
})

test_that("la participación suma cien", {
  tabla <- agregar(datos_publicados(), "corresponsal", "participacion")
  expect_equal(round(sum(tabla$valor), 6), 100)
})

test_that("el promedio coincide con el de las filas", {
  datos <- datos_publicados()
  tabla <- agregar(datos, "modulo", "promedio_usd")
  esperado <- mean(datos$monto_usd[datos$modulo == "GIROS_AL"])
  expect_equal(round(tabla$valor[tabla$etiqueta == "Giros AL"], 2), round(esperado, 2))
})

test_that("el top agrupa el resto sin perder el total", {
  datos <- datos_publicados()
  tabla <- agregar(datos, "corresponsal", "monto_usd", top = 2)
  expect_equal(nrow(tabla), 3)
  expect_true(grepl("^Otros", tabla$etiqueta[nrow(tabla)]))
  expect_equal(round(sum(tabla$valor), 2), round(sum(datos$monto_usd), 2))
})

test_that("los filtros se combinan", {
  datos <- datos_publicados()
  filtrado <- aplicar_filtros(datos, list(modulo = "Giros AL", moneda = "USD"))
  expect_equal(nrow(filtrado), 2)
  expect_setequal(filtrado$modulo, "GIROS_AL")
})

test_that("filtro por monto mínimo", {
  filtrado <- aplicar_filtros(datos_publicados(), list(monto_minimo = 1000000))
  expect_true(all(filtrado$monto_usd >= 1000000))
})

test_that("filtro por rango de fechas", {
  filtrado <- aplicar_filtros(datos_publicados(),
                              list(rango_fechas = c("2026-06-10", "2026-06-15")))
  expect_equal(nrow(filtrado), 2)
})

test_that("un filtro vacío no altera los datos", {
  datos <- datos_publicados()
  expect_equal(nrow(aplicar_filtros(datos, list(modulo = character(0)))), nrow(datos))
})

test_that("la tabla cruzada trae totales que cuadran", {
  datos <- datos_publicados()
  matriz <- tabla_cruzada(datos, "modulo", "tipo_mensaje", "monto_usd")
  expect_true("Total" %in% names(matriz))
  expect_true("Total" %in% rownames(matriz))
  expect_equal(round(matriz["Total", "Total"], 2), round(sum(datos$monto_usd), 2))
})

test_that("la serie temporal agrupa por mes", {
  serie <- serie_temporal(datos_publicados(), "operaciones", por = "mes")
  expect_equal(serie$etiqueta, "2026-06")
  expect_equal(serie$valor, 6)
})

test_that("el ranking trae las cuatro columnas y suma cien", {
  tabla <- ranking(datos_publicados(), "corresponsal", top = 3)
  expect_true(all(c("Operaciones", "Monto USD", "Promedio USD", "Participación %") %in% names(tabla)))
  expect_equal(round(sum(tabla$`Participación %`), 1), 100)
})

test_that("el detalle usa encabezados legibles", {
  datos <- datos_publicados()
  tabla <- detalle(datos)
  expect_true(all(c("Monto USD", "Corresponsal", "Módulo") %in% names(tabla)))
  expect_equal(nrow(tabla), nrow(datos))
})

test_that("sin datos no rompe nada", {
  entorno_aislado()
  vacio <- operaciones(solo_activas = TRUE)
  expect_equal(nrow(agregar(vacio, "modulo", "monto_usd")), 0)
  expect_equal(kpis(vacio)$operaciones, 0)
  expect_equal(nrow(serie_temporal(vacio)), 0)
})

test_that("el formato de cifras es el del panel", {
  expect_equal(formatear(1234, "operaciones"), "1.234")
  expect_equal(formatear_usd(1234567.891), "USD 1,234,567.89")
  expect_equal(formatear_usd_compacto(1525417757.14), "USD 1.53 MM")
  expect_equal(formatear(12.345, "participacion"), "12.3 %")
})
