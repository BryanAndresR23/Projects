# ---------------------------------------------------------------------------
# Panel de Giros — aplicación Shiny.
#
# Local:     shiny::runApp()   (o el botón "Run App" de RStudio)
# Servidor:  copiar esta carpeta bajo /srv/shiny-server/panel_giros
#            y definir GIROS_DIR_DATOS en /etc/R/Renviron.site
# ---------------------------------------------------------------------------

suppressPackageStartupMessages({
  library(shiny)
  library(bslib)
  library(DT)
  library(plotly)
})

# El locale se fija ANTES de leer los archivos: el código trae tildes y la ñ,
# y en locale C (habitual en servidores Linux) R no puede interpretarlos.
if (!grepl("UTF-8", Sys.getlocale("LC_CTYPE"), ignore.case = TRUE)) {
  for (locale in c("es_EC.UTF-8", "es_ES.UTF-8", "en_US.UTF-8", "C.UTF-8", "C.utf8")) {
    if (nzchar(suppressWarnings(Sys.setlocale("LC_CTYPE", locale)))) break
  }
}

# El núcleo y las pantallas viven en archivos aparte para poder probarlos
# sin levantar la interfaz.
for (archivo in list.files("nucleo", pattern = "\\.R$", full.names = TRUE)) {
  source(archivo, encoding = "UTF-8")
}
for (archivo in list.files("modulos", pattern = "\\.R$", full.names = TRUE)) {
  source(archivo, encoding = "UTF-8")
}

# Los reportes mensuales pueden pesar bastante.
options(shiny.maxRequestSize = 200 * 1024^2)

#' Comprueba que la carpeta de datos exista y se pueda escribir.
#'
#' En el servidor es el error más común: el usuario que ejecuta Shiny no tiene
#' permiso sobre la ruta compartida, y las cargas fallan sin explicación.
verificar_almacenamiento <- function() {
  ruta <- dir_datos()
  if (!dir.exists(ruta)) {
    creada <- dir.create(ruta, recursive = TRUE, showWarnings = FALSE)
    if (!creada) {
      return(sprintf("No se pudo crear la carpeta de datos: %s", ruta))
    }
  }
  if (file.access(ruta, mode = 2) != 0) {
    return(sprintf("La carpeta de datos existe pero no tiene permiso de escritura: %s", ruta))
  }
  NULL
}

problema_almacenamiento <- verificar_almacenamiento()

ui <- bslib::page_navbar(
  title = "Panel de Giros",
  theme = tema_panel(),
  id = "navegacion",
  window_title = "Panel de Giros",
  # Las pantallas son largas y se leen desplazándose: sin esto, bslib intenta
  # encajarlas en el alto de la ventana y aplasta las tarjetas.
  fillable = FALSE,
  header = if (!is.null(problema_almacenamiento)) {
    shiny::div(class = "alert alert-danger m-3",
               shiny::strong("Problema de configuración: "), problema_almacenamiento,
               shiny::br(),
               shiny::tags$small("Defina la variable GIROS_DIR_DATOS con una ruta escribible."))
  },
  bslib::nav_panel("Inicio", icon = shiny::icon("house"), mod_inicio_ui("inicio")),
  bslib::nav_panel("Cargar archivo", icon = shiny::icon("upload"), mod_cargar_ui("cargar")),
  bslib::nav_panel("Tablero", icon = shiny::icon("chart-column"), mod_tablero_ui("tablero")),
  bslib::nav_panel("Histórico", icon = shiny::icon("clock-rotate-left"), mod_historico_ui("historico")),
  bslib::nav_spacer(),
  bslib::nav_item(shiny::uiOutput("identidad"))
)

server <- function(input, output, session) {
  # Se incrementa tras cada carga o restauración: obliga a releer la base.
  actualizacion <- shiny::reactiveVal(0)

  usuario <- shiny::reactive(usuario_actual(session))

  datos <- shiny::reactive({
    actualizacion()
    operaciones(solo_activas = TRUE)
  })

  cargas <- shiny::reactive({
    actualizacion()
    listar_cargas()
  })

  bitacora_reciente <- shiny::reactive({
    actualizacion()
    bitacora(limite = 300)
  })

  output$identidad <- shiny::renderUI({
    shiny::span(class = "navbar-text small pe-3", shiny::icon("user"), " ", usuario())
  })

  mod_inicio_server("inicio", datos, cargas, bitacora_reciente)
  mod_cargar_server("cargar", usuario, actualizacion)
  mod_tablero_server("tablero", datos)
  mod_historico_server("historico", cargas, usuario, actualizacion, bitacora_reciente)
}

shinyApp(ui, server)
