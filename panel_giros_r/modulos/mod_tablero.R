# ---------------------------------------------------------------------------
# Tablero analítico: el usuario elige la métrica y los gráficos se redibujan.
# ---------------------------------------------------------------------------

DIMENSIONES_FILTRO <- c("modulo", "periodo", "corresponsal", "area_750", "deuda_771",
                        "tipo_mensaje", "moneda", "proceso", "estado")

mod_tablero_ui <- function(id) {
  ns <- shiny::NS(id)
  bslib::layout_sidebar(
    sidebar = bslib::sidebar(
      width = 300, title = "Filtros",
      shiny::actionButton(ns("limpiar"), "Limpiar filtros", class = "btn-outline-secondary btn-sm"),
      shiny::uiOutput(ns("filtros"))
    ),
    shiny::h2("Tablero analítico"),
    shiny::p(class = "text-muted",
             paste("Elija la métrica y el rubro: los gráficos, la matriz y el detalle se recalculan",
                   "al instante sobre las versiones publicadas.")),
    shiny::uiOutput(ns("sin_datos")),
    shiny::uiOutput(ns("cuerpo"))
  )
}

mod_tablero_server <- function(id, datos) {
  shiny::moduleServer(id, function(input, output, session) {
    ns <- session$ns

    hay_datos <- shiny::reactive(nrow(datos()) > 0)

    output$sin_datos <- shiny::renderUI({
      if (hay_datos()) return(NULL)
      shiny::div(class = "alert alert-info",
                 "Aún no hay ninguna carga publicada. Cargue un archivo para ver el tablero.")
    })

    # --- Filtros -----------------------------------------------------------
    output$filtros <- shiny::renderUI({
      universo <- datos()
      if (!nrow(universo)) return(NULL)
      controles <- lapply(DIMENSIONES_FILTRO, function(dimension) {
        valores <- opciones(universo, dimension)
        if (length(valores) < 2) return(NULL)
        shiny::selectizeInput(
          ns(paste0("f_", dimension)), unname(DIMENSIONES[dimension]),
          choices = valores, multiple = TRUE,
          options = list(placeholder = "Todos")
        )
      })
      fechas <- range(universo$fecha, na.rm = TRUE)
      control_fechas <- if (fechas[1] < fechas[2]) {
        shiny::dateRangeInput(ns("f_fechas"), "Rango de fechas",
                              start = fechas[1], end = fechas[2],
                              min = fechas[1], max = fechas[2],
                              format = "dd/mm/yyyy", language = "es", separator = " a ")
      }
      tope <- max(universo$monto_usd, na.rm = TRUE)
      control_monto <- if (is.finite(tope) && tope > 0) {
        shiny::numericInput(ns("f_monto"), "Monto USD mínimo", value = 0, min = 0, max = tope,
                            step = max(tope / 100, 1))
      }
      shiny::tagList(controles, control_fechas, control_monto)
    })

    shiny::observeEvent(input$limpiar, {
      universo <- datos()
      for (dimension in DIMENSIONES_FILTRO) {
        shiny::updateSelectizeInput(session, paste0("f_", dimension), selected = character(0))
      }
      if (nrow(universo)) {
        fechas <- range(universo$fecha, na.rm = TRUE)
        shiny::updateDateRangeInput(session, "f_fechas", start = fechas[1], end = fechas[2])
      }
      shiny::updateNumericInput(session, "f_monto", value = 0)
    })

    filtros <- shiny::reactive({
      universo <- datos()
      lista <- list()
      for (dimension in DIMENSIONES_FILTRO) {
        valor <- input[[paste0("f_", dimension)]]
        if (!is.null(valor) && length(valor)) lista[[dimension]] <- valor
      }
      if (!is.null(input$f_fechas) && length(input$f_fechas) == 2 && nrow(universo)) {
        completo <- range(universo$fecha, na.rm = TRUE)
        if (!identical(as.Date(input$f_fechas), completo)) lista$rango_fechas <- input$f_fechas
      }
      if (!is.null(input$f_monto) && !is.na(input$f_monto) && input$f_monto > 0) {
        lista$monto_minimo <- input$f_monto
      }
      lista
    })

    filtrados <- shiny::reactive(aplicar_filtros(datos(), filtros()))

    # --- Cuerpo ------------------------------------------------------------
    output$cuerpo <- shiny::renderUI({
      if (!hay_datos()) return(NULL)
      shiny::tagList(
        shiny::uiOutput(ns("resumen_filtros")),
        shiny::uiOutput(ns("kpis")),
        shiny::hr(),
        bslib::layout_columns(
          col_widths = c(3, 3, 3, 3), fill = FALSE,
          shiny::selectInput(ns("medida"), "Métrica",
                             choices = stats::setNames(names(MEDIDAS), unname(MEDIDAS))),
          shiny::selectInput(ns("dimension"), "Abrir por",
                             choices = stats::setNames(names(DIMENSIONES), unname(DIMENSIONES)),
                             selected = "corresponsal"),
          shiny::selectInput(ns("tipo"), "Tipo de gráfico", choices = TIPOS_GRAFICO),
          shiny::numericInput(ns("top"), "Top", value = 10, min = 3, max = 50, step = 1)
        ),
        plotly::plotlyOutput(ns("principal"), height = "460px"),
        bslib::layout_columns(
          col_widths = c(6, 6),
          bslib::card(
            bslib::card_header("Evolución en el tiempo"),
            shiny::radioButtons(ns("granularidad"), NULL, choices = c("mes", "día"), inline = TRUE),
            plotly::plotlyOutput(ns("evolucion"), height = "400px")
          ),
          bslib::card(
            bslib::card_header("Segunda apertura"),
            shiny::selectInput(ns("dimension2"), NULL,
                               choices = stats::setNames(names(DIMENSIONES), unname(DIMENSIONES)),
                               selected = "proceso"),
            plotly::plotlyOutput(ns("secundario"), height = "400px")
          )
        ),
        shiny::uiOutput(ns("bloque_comparacion")),
        shiny::hr(),
        shiny::h4("Matriz cruzada"),
        bslib::layout_columns(
          col_widths = c(4, 4, 4), fill = FALSE,
          shiny::selectInput(ns("matriz_filas"), "Filas",
                             choices = stats::setNames(names(DIMENSIONES), unname(DIMENSIONES)),
                             selected = "corresponsal"),
          shiny::selectInput(ns("matriz_columnas"), "Columnas",
                             choices = stats::setNames(names(DIMENSIONES), unname(DIMENSIONES)),
                             selected = "nombre_mes"),
          shiny::selectInput(ns("matriz_medida"), "Métrica de la matriz",
                             choices = stats::setNames(names(MEDIDAS), unname(MEDIDAS)))
        ),
        DT::DTOutput(ns("matriz")),
        shiny::hr(),
        shiny::h4(shiny::textOutput(ns("titulo_ranking"), inline = TRUE)),
        DT::DTOutput(ns("ranking")),
        shiny::hr(),
        bslib::accordion(
          open = FALSE,
          bslib::accordion_panel(
            # El título es dinámico, así que el identificador del panel va aparte.
            title = shiny::textOutput(ns("titulo_detalle"), inline = TRUE),
            value = "detalle",
            DT::DTOutput(ns("detalle"))
          )
        ),
        bslib::layout_columns(
          col_widths = c(6, 6), fill = FALSE,
          shiny::downloadButton(ns("descargar_excel"), "Descargar tablero en Excel",
                                class = "btn-outline-primary w-100"),
          shiny::downloadButton(ns("descargar_csv"), "Descargar detalle en CSV",
                                class = "btn-outline-primary w-100")
        )
      )
    })

    output$resumen_filtros <- shiny::renderUI({
      activos <- filtros()
      if (!length(activos)) return(NULL)
      partes <- vapply(names(activos), function(campo) {
        valor <- activos[[campo]]
        etiqueta <- if (campo %in% names(DIMENSIONES)) unname(DIMENSIONES[campo])
                    else if (campo == "rango_fechas") "Fechas" else "Monto mínimo"
        texto <- if (campo == "rango_fechas") paste(format(as.Date(valor), "%d/%m/%Y"), collapse = " a ")
                 else paste(valor, collapse = ", ")
        paste0(etiqueta, ": ", texto)
      }, character(1))
      shiny::p(class = "text-muted small", paste("Filtros activos →", paste(partes, collapse = " · ")))
    })

    output$kpis <- shiny::renderUI({
      marco <- filtrados()
      if (!nrow(marco)) {
        return(shiny::div(class = "alert alert-warning",
                          "Ninguna operación cumple los filtros seleccionados."))
      }
      tarjetas_kpi(marco, datos())
    })

    output$principal <- plotly::renderPlotly({
      shiny::req(input$medida, input$dimension)
      tabla <- agregar(filtrados(), input$dimension, input$medida, top = input$top)
      grafico(tabla, input$medida, input$tipo,
              sprintf("%s por %s", unname(MEDIDAS[input$medida]), unname(DIMENSIONES[input$dimension])))
    })

    output$evolucion <- plotly::renderPlotly({
      shiny::req(input$medida, input$granularidad)
      serie <- serie_temporal(filtrados(), input$medida, por = input$granularidad)
      tipo <- if (identical(input$granularidad, "día")) "Línea" else "Columnas"
      grafico(serie, input$medida, tipo,
              sprintf("Evolución de %s", tolower(unname(MEDIDAS[input$medida]))))
    })

    output$secundario <- plotly::renderPlotly({
      shiny::req(input$medida, input$dimension2)
      tabla <- agregar(filtrados(), input$dimension2, input$medida, top = 8)
      grafico(tabla, input$medida, "Torta",
              sprintf("%s por %s", unname(MEDIDAS[input$medida]), unname(DIMENSIONES[input$dimension2])))
    })

    output$bloque_comparacion <- shiny::renderUI({
      if (length(unique(filtrados()$modulo)) < 2) return(NULL)
      shiny::tagList(
        shiny::hr(),
        shiny::h4("Giros AL frente a Giros DEL"),
        shiny::selectInput(
          ns("dimension_comp"), "Comparar por",
          choices = stats::setNames(
            c("nombre_mes", "corresponsal", "proceso", "moneda", "tipo_mensaje", "area_750"),
            unname(DIMENSIONES[c("nombre_mes", "corresponsal", "proceso", "moneda", "tipo_mensaje", "area_750")])
          )
        ),
        plotly::plotlyOutput(ns("comparacion"), height = "430px")
      )
    })

    output$comparacion <- plotly::renderPlotly({
      shiny::req(input$dimension_comp, input$medida)
      marco <- filtrados()
      categorias <- character(0)
      tablas <- list()
      for (modulo in names(MODULOS)) {
        parcial <- marco[marco$modulo == modulo, , drop = FALSE]
        tabla <- agregar(parcial, input$dimension_comp, input$medida, top = 10)
        categorias <- unique(c(categorias, tabla$etiqueta))
        tablas[[etiqueta_modulo(modulo)]] <- tabla
      }
      series <- lapply(tablas, function(tabla) {
        vapply(categorias, function(c) {
          valor <- tabla$valor[tabla$etiqueta == c]
          if (length(valor)) sum(valor) else 0
        }, numeric(1))
      })
      grafico_comparado(categorias, series,
                        sprintf("%s por %s", unname(MEDIDAS[input$medida]),
                                unname(DIMENSIONES[input$dimension_comp])))
    })

    matriz_actual <- shiny::reactive({
      shiny::req(input$matriz_filas, input$matriz_columnas, input$matriz_medida)
      tabla_cruzada(filtrados(), input$matriz_filas, input$matriz_columnas, input$matriz_medida)
    })

    output$matriz <- DT::renderDT({
      matriz <- matriz_actual()
      shiny::validate(shiny::need(!is.null(matriz) && nrow(matriz) > 0, "Sin datos para esa combinación."))
      presentacion <- cbind(Rubro = rownames(matriz), matriz)
      names(presentacion)[1] <- unname(DIMENSIONES[input$matriz_filas])
      digitos <- if (identical(input$matriz_medida, "operaciones")) 0 else 2
      DT::formatCurrency(
        tabla_dt(presentacion, alto = "380px", pagina = 12),
        names(matriz), currency = "", digits = digitos, mark = ","
      )
    })

    ranking_actual <- shiny::reactive({
      shiny::req(input$dimension)
      ranking(filtrados(), input$dimension, top = input$top)
    })

    output$titulo_ranking <- shiny::renderText({
      shiny::req(input$dimension)
      sprintf("Ranking por %s", tolower(unname(DIMENSIONES[input$dimension])))
    })

    output$ranking <- DT::renderDT({
      tabla <- ranking_actual()
      shiny::validate(shiny::need(!is.null(tabla) && nrow(tabla) > 0, "Sin datos."))
      tabla_dt(tabla, columnas_numericas = c("Monto USD", "Promedio USD", "Participación %"),
               alto = "320px", pagina = 10)
    })

    output$titulo_detalle <- shiny::renderText({
      sprintf("Detalle que respalda las cifras (%s operaciones)", formatear_numero(nrow(filtrados())))
    })

    output$detalle <- DT::renderDT({
      tabla <- detalle(filtrados())
      shiny::validate(shiny::need(nrow(tabla) > 0, "Sin operaciones."))
      tabla_dt(tabla, columnas_numericas = c("Monto origen", "Monto USD"), alto = "400px", pagina = 15)
    })

    output$descargar_excel <- shiny::downloadHandler(
      filename = function() sprintf("tablero_giros_%s.xlsx", format(Sys.Date(), "%Y%m%d")),
      content = function(ruta) {
        resumen <- agregar(filtrados(), input$dimension, input$medida, top = input$top)
        names(resumen) <- c(unname(DIMENSIONES[input$dimension]), unname(MEDIDAS[input$medida]))
        escribir_excel(list(
          Resumen = resumen,
          Ranking = ranking_actual(),
          Matriz = matriz_actual(),
          Detalle = detalle(filtrados())
        ), ruta)
      }
    )

    output$descargar_csv <- shiny::downloadHandler(
      filename = function() sprintf("detalle_giros_%s.csv", format(Sys.Date(), "%Y%m%d")),
      content = function(ruta) {
        temporal <- tempfile(fileext = ".csv")
        utils::write.csv(detalle(filtrados()), temporal, row.names = FALSE,
                         fileEncoding = "UTF-8", na = "")
        contenido <- readBin(temporal, "raw", n = file.size(temporal))
        destino <- file(ruta, open = "wb"); on.exit(close(destino), add = TRUE)
        writeBin(c(as.raw(c(0xEF, 0xBB, 0xBF)), contenido), destino)
        unlink(temporal)
      }
    )
  })
}
