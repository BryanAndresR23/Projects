# Ejecutar con:  Rscript tests/testthat.R
library(testthat)

raiz <- normalizePath(file.path(dirname(sys.frame(1)$ofile %||% "."), ".."), mustWork = FALSE)
if (!dir.exists(file.path(raiz, "nucleo"))) raiz <- normalizePath(".")
Sys.setenv(GIROS_RAIZ = raiz)

testthat::test_dir(file.path(raiz, "tests", "testthat"), stop_on_failure = TRUE)
