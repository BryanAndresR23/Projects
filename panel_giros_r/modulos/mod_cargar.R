# ---------------------------------------------------------------------------
# Carga de archivos: el validador elige el módulo y el ETL comprueba que coincida.
# ---------------------------------------------------------------------------

mod_cargar_ui <- function(id) {
  ns <- shiny::NS(id)
  shiny::tagList(
    shiny::h2("Cargar archivo"),
    shiny::p(class = "text-muted",
             paste("Cada carga queda registrada con su archivo original, el resultado depurado,",
                   "las validaciones, el usuario, la fecha y su número de versión.")),
    bslib::card(
      bslib::card_header("1. Identifique la carga"),
      bslib::card_body(
        bslib::layout_columns(
          col_widths = c(6, 6),
          shiny::radioButtons(
            ns("modulo"), "Módulo",
            choices = stats::setNames(names(MODULOS), unname(MODULOS)),
            inline = TRUE
          ),
          selector_periodo_ui("anio", "mes", ns)
        ),
        shiny::helpText(
          paste("La selección del módulo es obligatoria: el ETL verifica que el contenido del archivo",
                "corresponda al módulo elegido y rechaza la carga si no coincide.")
        ),
        shiny::uiOutput(ns("aviso_version"))
      )
    ),
    bslib::card(
      bslib::card_header("2. Seleccione el archivo"),
      bslib::card_body(
        shiny::fileInput(
          ns("archivo"), "Reporte de giros (Excel o CSV)",
          accept = c(".xlsx", ".xlsm", ".xls", ".csv", ".txt"),
          buttonLabel = "Examinar", placeholder = "Ningún archivo seleccionado"
        ),
        shiny::helpText("El sistema busca solo la fila de encabezados: no importa si el reporte trae títulos arriba."),
        shiny::uiOutput(ns("selector_hoja")),
        shiny::textInput(ns("comentario"), "Comentario de la carga (opcional)",
                         placeholder = "Ej.: recarga por corrección de corresponsales en 12 operaciones"),
        shiny::checkboxInput(ns("publicar"), "Publicar en el tablero si supera la validación", value = TRUE),
        shiny::actionButton(ns("procesar"), "Procesar y validar", class = "btn-primary w-100")
      )
    ),
    shiny::uiOutput(ns("resultado"))
  )
}

mod_cargar_server <- function(id, usuario, actualizacion) {
  shiny::moduleServer(id, function(input, output, session) {
    ns <- session$ns
    carga <- shiny::reactiveVal(NULL)

    periodo_elegido <- shiny::reactive({
      shiny::req(input$anio, input$mes)
      periodo(input$anio, input$mes)
    })

    output$aviso_version <- shiny::renderUI({
      shiny::req(input$modulo, periodo_elegido())
      activa <- carga_activa(input$modulo, periodo_elegido())
      if (is.null(activa)) {
        return(shiny::div(class = "text-muted",
                          sprintf("Se cargará %s del período %s.",
                                  etiqueta_modulo(input$modulo), etiqueta_periodo(periodo_elegido()))))
      }
      shiny::div(
        class = "alert alert-info",
        sprintf(paste("Ya hay una versión publicada para este módulo y período: v%s (%s operaciones, %s,",
                      "cargada por %s). Si continúa, se creará la v%s y la anterior quedará en el",
                      "histórico, disponible para restaurarla."),
                activa$version, activa$operaciones, activa$nombre_original,
                activa$usuario, activa$version + 1)
      )
    })

    hojas <- shiny::reactive({
      archivo <- input$archivo
      if (is.null(archivo)) return(character(0))
      tryCatch(hojas_disponibles(archivo$datapath, nombre = archivo$name),
               error = function(e) character(0))
    })

    output$selector_hoja <- shiny::renderUI({
      disponibles <- hojas()
      if (length(disponibles) < 2) return(NULL)
      shiny::selectInput(ns("hoja"), "Hoja del libro", choices = disponibles)
    })

    shiny::observeEvent(input$procesar, {
      archivo <- input$archivo
      if (is.null(archivo)) {
        shiny::showNotification("Seleccione primero un archivo.", type = "warning")
        return()
      }
      shiny::withProgress(message = "Depurando y validando…", value = 0.5, {
        resultado <- tryCatch(
          registrar_carga(
            archivo$datapath, modulo = input$modulo, periodo = periodo_elegido(),
            nombre = archivo$name, usuario = usuario(),
            comentario = input$comentario %||% "",
            hoja = if (length(hojas()) > 1) input$hoja else NULL,
            activar = isTRUE(input$publicar)
          ),
          error = function(e) e
        )
        if (inherits(resultado, "error")) {
          shiny::showNotification(paste("No se pudo procesar el archivo:", conditionMessage(resultado)),
                                  type = "error", duration = NULL)
          return()
        }
        carga(resultado)
        actualizacion(actualizacion() + 1)
      })
    })

    output$resultado <- shiny::renderUI({
      resultado <- carga()
      if (is.null(resultado)) return(NULL)

      encabezado <- if (identical(resultado$estado, ESTADO_RECHAZADA)) {
        shiny::div(class = "alert alert-danger",
                   shiny::strong(sprintf("Carga rechazada — guardada como v%s en el histórico, pero no publicada. ",
                                         resultado$version)),
                   "Corrija el archivo y vuelva a cargarlo.")
      } else if (isTRUE(resultado$activa)) {
        shiny::div(class = "alert alert-success",
                   shiny::strong(sprintf("Carga publicada como v%s de %s · %s. ", resultado$version,
                                         etiqueta_modulo(resultado$modulo), etiqueta_periodo(resultado$periodo))),
                   "El tablero ya muestra estos datos.")
      } else {
        shiny::div(class = "alert alert-warning",
                   shiny::strong(sprintf("Carga guardada como v%s sin publicar. ", resultado$version)),
                   resultado$motivo_no_activada)
      }

      duplicado <- if (!is.na(resultado$duplicado_de)) {
        anterior <- obtener_carga(resultado$duplicado_de)
        if (!is.null(anterior)) {
          shiny::div(class = "alert alert-secondary",
                     sprintf(paste("El archivo es idéntico al de la v%s (cargada el %s).",
                                   "Verifique que no sea una doble carga."),
                             anterior$version, substr(anterior$fecha_carga, 1, 10)))
        }
      }

      shiny::tagList(
        shiny::hr(),
        shiny::h4("3. Resultado de la validación"),
        encabezado,
        duplicado,
        bslib::layout_columns(
          fill = FALSE,
          tarjeta("Filas del archivo", formatear_numero(resultado$etl$filas_origen)),
          tarjeta("Operaciones depuradas", formatear_numero(resultado$etl$filas_depuradas)),
          tarjeta("Filas descartadas", formatear_numero(resultado$etl$filas_descartadas)),
          tarjeta("Monto USD", formatear_usd_compacto(resultado$etl$monto_usd)),
          tarjeta("Versión", paste0("v", resultado$version))
        ),
        shiny::h5("Validaciones aplicadas"),
        lapply(seq_len(nrow(resultado$hallazgos)), function(i) {
          fila <- resultado$hallazgos[i, ]
          clase <- switch(fila$severidad,
                          ERROR = "alert alert-danger py-2",
                          ADVERTENCIA = "alert alert-warning py-2",
                          "text-muted small")
          shiny::div(class = clase,
                     unname(SEVERIDAD_ICONO[fila$severidad]), " ",
                     shiny::strong(fila$regla), " — ", fila$mensaje)
        }),
        bslib::accordion(
          open = FALSE,
          bslib::accordion_panel(
            "Columnas reconocidas del archivo",
            DT::renderDT({
              detectadas <- resultado$etl$columnas_detectadas
              shiny::validate(shiny::need(length(detectadas) > 0, "No se reconoció ninguna columna."))
              tabla_dt(data.frame(
                `Columna del archivo` = names(detectadas),
                `Campo del sistema` = unname(detectadas),
                check.names = FALSE, stringsAsFactors = FALSE
              ), alto = "260px", pagina = 8)
            }),
            shiny::helpText(
              if (length(resultado$etl$columnas_ignoradas)) {
                paste("No utilizadas:", paste(utils::head(resultado$etl$columnas_ignoradas, 30), collapse = ", "))
              } else "",
              " ¿Falta alguna? Agregue el nombre real en catalogos/columnas.yaml."
            )
          ),
          bslib::accordion_panel(
            sprintf("Filas descartadas (%d)", resultado$etl$filas_descartadas),
            DT::renderDT({
              shiny::validate(shiny::need(nrow(resultado$etl$descartes) > 0, "No se descartó ninguna fila."))
              tabla_dt(resultado$etl$descartes, alto = "300px")
            })
          ),
          bslib::accordion_panel(
            sprintf("Vista previa del resultado depurado (%d filas)", resultado$etl$filas_depuradas),
            DT::renderDT({
              shiny::validate(shiny::need(nrow(resultado$etl$datos) > 0, "Sin filas depuradas."))
              tabla_dt(utils::head(resultado$etl$datos, 200),
                       columnas_numericas = c("monto", "monto_usd"), alto = "320px")
            })
          )
        ),
        shiny::helpText(sprintf("Archivos de esta versión guardados en: %s", resultado$carpeta)),
        if (isTRUE(resultado$publicable) && !isTRUE(resultado$activa)) {
          shiny::actionButton(ns("publicar_ahora"),
                              sprintf("Publicar la v%s ahora", resultado$version),
                              class = "btn-primary")
        }
      )
    })

    shiny::observeEvent(input$publicar_ahora, {
      resultado <- carga()
      shiny::req(resultado)
      tryCatch({
        activar_version(resultado$modulo, resultado$periodo, resultado$version,
                        usuario = usuario(),
                        motivo = "Publicación manual tras revisar validaciones")
        actualizado <- resultado
        actualizado$activa <- TRUE
        actualizado$motivo_no_activada <- ""
        carga(actualizado)
        actualizacion(actualizacion() + 1)
        shiny::showNotification(sprintf("Se publicó la v%s.", resultado$version), type = "message")
      }, error = function(e) {
        shiny::showNotification(conditionMessage(e), type = "error", duration = NULL)
      })
    })

    carga
  })
}

`%||%` <- function(x, y) if (is.null(x) || !length(x) || identical(x, "")) y else x
