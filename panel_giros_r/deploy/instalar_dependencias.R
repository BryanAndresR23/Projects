# ---------------------------------------------------------------------------
# Instala los paquetes que necesita el panel.
#
#   Rscript deploy/instalar_dependencias.R
#
# En un servidor Debian/Ubuntu suele ser más rápido y estable instalarlos como
# paquetes del sistema (no requieren compilar):
#
#   sudo apt-get install -y r-cran-shiny r-cran-bslib r-cran-dt r-cran-plotly \
#        r-cran-dbi r-cran-rsqlite r-cran-readxl r-cran-yaml r-cran-writexl \
#        r-cran-openxlsx r-cran-digest r-cran-jsonlite r-cran-stringi r-cran-testthat
# ---------------------------------------------------------------------------

PAQUETES <- c("shiny", "bslib", "DT", "plotly", "DBI", "RSQLite", "readxl",
              "yaml", "writexl", "openxlsx", "digest", "jsonlite", "stringi", "testthat")

faltantes <- PAQUETES[!vapply(PAQUETES, requireNamespace, logical(1), quietly = TRUE)]

if (!length(faltantes)) {
  cat("Todos los paquetes ya están instalados.\n")
} else {
  cat("Faltan:", paste(faltantes, collapse = ", "), "\n")
  install.packages(faltantes, repos = "https://cloud.r-project.org")
  aun_faltan <- faltantes[!vapply(faltantes, requireNamespace, logical(1), quietly = TRUE)]
  if (length(aun_faltan)) {
    stop("No se pudieron instalar: ", paste(aun_faltan, collapse = ", "))
  }
  cat("Instalación completa.\n")
}

for (paquete in PAQUETES) {
  cat(sprintf("  %-10s %s\n", paquete, as.character(utils::packageVersion(paquete))))
}
