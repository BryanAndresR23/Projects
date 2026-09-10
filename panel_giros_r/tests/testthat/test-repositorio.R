# Pruebas del histórico: versionado, publicación y restauración.

test_that("la primera carga se publica", {
  carpeta <- entorno_aislado()
  resultado <- registrar_carga(archivo_al(carpeta), modulo = "GIROS_AL", periodo = "2026-06")
  expect_equal(resultado$version, 1)
  expect_true(resultado$activa)
  expect_false(identical(resultado$estado, ESTADO_RECHAZADA))
  expect_equal(resultado$etl$filas_depuradas, 3)
})

test_that("conserva archivo original, depurado y validaciones", {
  carpeta <- entorno_aislado()
  resultado <- registrar_carga(archivo_al(carpeta), modulo = "GIROS_AL", periodo = "2026-06")
  expect_true(file.exists(file.path(resultado$carpeta, "depurado.csv")))
  expect_true(file.exists(file.path(resultado$carpeta, "descartes.csv")))
  expect_true(file.exists(file.path(resultado$carpeta, "validaciones.json")))
  expect_true(any(grepl("^original_", list.files(resultado$carpeta))))

  carga <- obtener_carga(resultado$carga_id)
  expect_equal(carga$usuario, "pruebas")
  expect_equal(carga$nombre_original, "Giros_AL_2026-06.xlsx")
  expect_true(nzchar(carga$fecha_carga))
})

test_that("la segunda carga crea una versión nueva y desplaza a la anterior", {
  carpeta <- entorno_aislado()
  primera <- registrar_carga(archivo_al(carpeta), modulo = "GIROS_AL", periodo = "2026-06")
  segunda <- registrar_carga(archivo_al(carpeta), modulo = "GIROS_AL", periodo = "2026-06",
                             comentario = "recarga")
  expect_equal(segunda$version, 2)
  historico <- listar_cargas(modulo = "GIROS_AL", periodo = "2026-06")
  expect_equal(nrow(historico), 2)
  expect_equal(historico$activa[historico$version == 2], 1)
  expect_equal(historico$activa[historico$version == 1], 0)
  expect_equal(segunda$duplicado_de, primera$carga_id)
})

test_that("solo una versión queda activa por módulo y período", {
  carpeta <- entorno_aislado()
  for (i in 1:3) registrar_carga(archivo_al(carpeta), modulo = "GIROS_AL", periodo = "2026-06")
  historico <- listar_cargas(modulo = "GIROS_AL", periodo = "2026-06")
  expect_equal(sum(historico$activa), 1)
})

test_that("se puede restaurar una versión anterior", {
  carpeta <- entorno_aislado()
  registrar_carga(archivo_al(carpeta), modulo = "GIROS_AL", periodo = "2026-06")
  registrar_carga(archivo_al(carpeta), modulo = "GIROS_AL", periodo = "2026-06")
  resultado <- activar_version("GIROS_AL", "2026-06", 1, motivo = "prueba")
  expect_equal(resultado$accion, "RESTAURACION")
  expect_equal(carga_activa("GIROS_AL", "2026-06")$version, 1)
  expect_true("RESTAURACION" %in% bitacora()$accion)
})

test_that("una carga rechazada se guarda pero no se publica", {
  carpeta <- entorno_aislado()
  resultado <- registrar_carga(archivo_del(carpeta), modulo = "GIROS_AL", periodo = "2026-06")
  expect_equal(resultado$estado, ESTADO_RECHAZADA)
  expect_false(resultado$activa)
  expect_null(carga_activa("GIROS_AL", "2026-06"))
  expect_equal(nrow(listar_cargas(modulo = "GIROS_AL")), 1)
  expect_equal(nrow(operaciones(solo_activas = TRUE)), 0)
})

test_that("no se puede publicar una versión rechazada", {
  carpeta <- entorno_aislado()
  registrar_carga(archivo_del(carpeta), modulo = "GIROS_AL", periodo = "2026-06")
  expect_error(activar_version("GIROS_AL", "2026-06", 1), "rechazada")
})

test_that("se puede guardar sin publicar", {
  carpeta <- entorno_aislado()
  registrar_carga(archivo_al(carpeta), modulo = "GIROS_AL", periodo = "2026-06")
  segunda <- registrar_carga(archivo_al(carpeta), modulo = "GIROS_AL", periodo = "2026-06",
                             activar = FALSE)
  expect_false(segunda$activa)
  expect_equal(carga_activa("GIROS_AL", "2026-06")$version, 1)
})

test_that("los módulos y períodos son independientes", {
  carpeta <- entorno_aislado()
  registrar_carga(archivo_al(carpeta), modulo = "GIROS_AL", periodo = "2026-06")
  registrar_carga(archivo_del(carpeta), modulo = "GIROS_DEL", periodo = "2026-06")
  activas <- listar_cargas(solo_activas = TRUE)
  expect_equal(nrow(activas), 2)
  expect_setequal(activas$modulo, c("GIROS_AL", "GIROS_DEL"))
})

test_that("comparar versiones muestra la diferencia", {
  carpeta <- entorno_aislado()
  registrar_carga(archivo_al(carpeta), modulo = "GIROS_AL", periodo = "2026-06")
  reducido <- escribir_excel_prueba(file.path(carpeta, "menos.xlsx"), tabla_base()[1:2, ])
  registrar_carga(reducido, modulo = "GIROS_AL", periodo = "2026-06")

  comparacion <- comparar_versiones("GIROS_AL", "2026-06", 1, 2)
  fila <- comparacion[comparacion$Indicador == "Operaciones depuradas", ]
  expect_equal(fila$v1, "3")
  expect_equal(fila$v2, "2")
  expect_equal(trimws(fila$Diferencia), "-1")
})

test_that("rechaza un módulo desconocido", {
  carpeta <- entorno_aislado()
  expect_error(registrar_carga(archivo_al(carpeta), modulo = "GIROS_XX", periodo = "2026-06"),
               "Módulo desconocido")
})

test_that("rechaza una versión inexistente", {
  entorno_aislado()
  expect_error(activar_version("GIROS_AL", "2026-06", 9), "No existe")
})

test_that("rechaza un período mal formado", {
  carpeta <- entorno_aislado()
  expect_error(registrar_carga(archivo_al(carpeta), modulo = "GIROS_AL", periodo = "junio"),
               "AAAA-MM")
})
