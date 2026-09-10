# ---------------------------------------------------------------------------
# Pantalla de inicio: qué está publicado y qué se movió últimamente.
# ---------------------------------------------------------------------------

mod_inicio_ui <- function(id) {
  ns <- shiny::NS(id)
  shiny::tagList(
    shiny::h2("Panel de Giros"),
    shiny::p(class = "text-muted",
             "Carga validada con histórico de versiones y tablero analítico de Giros AL y Giros DEL."),
    shiny::uiOutput(ns("sin_datos")),
    shiny::uiOutput(ns("contenido"))
  )
}

mod_inicio_server <- function(id, datos, cargas, bitacora_reciente) {
  shiny::moduleServer(id, function(input, output, session) {
    ns <- session$ns

    hay_cargas <- shiny::reactive(nrow(cargas()) > 0)

    output$sin_datos <- shiny::renderUI({
      if (hay_cargas()) return(NULL)
      bslib::card(
        bslib::card_body(
          shiny::h5("Todavía no hay cargas registradas"),
          shiny::p("Empiece en la pestaña ", shiny::strong("Cargar archivo"),
                   ": elija el módulo (Giros AL o Giros DEL), el período y suba el reporte.")
        )
      )
    })

    output$contenido <- shiny::renderUI({
      if (!hay_cargas()) return(NULL)
      shiny::tagList(
        shiny::h4("Situación general de lo publicado"),
        shiny::uiOutput(ns("kpis")),
        shiny::uiOutput(ns("resumen_cargas")),
        shiny::hr(),
        shiny::h4("Versión publicada por módulo y período"),
        shiny::p(class = "text-muted",
                 "Una sola versión queda activa por módulo y período; las demás se conservan en el histórico."),
        DT::DTOutput(ns("publicadas")),
        shiny::uiOutput(ns("faltantes")),
        shiny::hr(),
        bslib::layout_columns(
          col_widths = c(7, 5),
          bslib::card(bslib::card_header("Monto USD por período"),
                      plotly::plotlyOutput(ns("por_periodo"), height = "380px")),
          bslib::card(bslib::card_header("Últimos movimientos"),
                      DT::DTOutput(ns("movimientos")))
        )
      )
    })

    output$kpis <- shiny::renderUI(tarjetas_kpi(datos()))

    output$resumen_cargas <- shiny::renderUI({
      historico <- cargas()
      publicadas <- historico[historico$activa == 1, , drop = FALSE]
      bslib::layout_columns(
        fill = FALSE,
        tarjeta("Cargas en el histórico", formatear_numero(nrow(historico))),
        tarjeta("Períodos con datos publicados", formatear_numero(length(unique(publicadas$periodo)))),
        tarjeta("Cargas rechazadas", formatear_numero(sum(historico$estado == ESTADO_RECHAZADA)))
      )
    })

    tabla_publicadas <- shiny::reactive({
      publicadas <- cargas()[cargas()$activa == 1, , drop = FALSE]
      if (!nrow(publicadas)) return(NULL)
      # Se ordena por el período canónico (AAAA-MM), no por su etiqueta: si no,
      # "Mayo" quedaría antes que "Junio" por orden alfabético.
      publicadas <- publicadas[order(publicadas$periodo, publicadas$modulo,
                                     decreasing = c(TRUE, FALSE), method = "radix"), ]
      tabla <- data.frame(
        Módulo = etiqueta_modulo(publicadas$modulo),
        Período = etiqueta_periodo(publicadas$periodo),
        Versión = paste0("v", publicadas$version),
        Estado = paste(icono_estado(publicadas$estado), publicadas$estado),
        Operaciones = publicadas$operaciones,
        `Monto USD` = round(publicadas$monto_usd, 2),
        Validador = publicadas$usuario,
        `Fecha de carga` = format(as.POSIXct(publicadas$fecha_carga, format = "%Y-%m-%dT%H:%M:%S"),
                                  "%d/%m/%Y %H:%M"),
        Archivo = publicadas$nombre_original,
        check.names = FALSE, stringsAsFactors = FALSE
      )
      tabla
    })

    output$publicadas <- DT::renderDT({
      tabla <- tabla_publicadas()
      shiny::validate(shiny::need(!is.null(tabla), "Hay cargas registradas, pero ninguna publicada todavía."))
      tabla_dt(tabla, columnas_numericas = c("Monto USD"), alto = "300px", pagina = 8)
    })

    output$faltantes <- shiny::renderUI({
      historico <- cargas()
      publicadas <- historico[historico$activa == 1, , drop = FALSE]
      pendientes <- character(0)
      for (periodo in sort(unique(historico$periodo), decreasing = TRUE)) {
        con_datos <- publicadas$modulo[publicadas$periodo == periodo]
        for (modulo in setdiff(names(MODULOS), con_datos)) {
          pendientes <- c(pendientes, paste(etiqueta_modulo(modulo), "·", etiqueta_periodo(periodo)))
        }
      }
      if (!length(pendientes)) return(NULL)
      shiny::div(
        class = "alert alert-warning mt-3",
        shiny::strong("Sin versión publicada: "),
        paste(utils::head(pendientes, 8), collapse = " | ")
      )
    })

    output$por_periodo <- plotly::renderPlotly({
      grafico(agregar(datos(), "periodo", "monto_usd"), "monto_usd", "Columnas",
              "Monto publicado por período")
    })

    output$movimientos <- DT::renderDT({
      registros <- bitacora_reciente()
      shiny::validate(shiny::need(nrow(registros) > 0, "Sin movimientos registrados."))
      tabla_dt(data.frame(
        Fecha = format(as.POSIXct(registros$fecha, format = "%Y-%m-%dT%H:%M:%S"), "%d/%m %H:%M"),
        Acción = registros$accion,
        Módulo = ifelse(is.na(registros$modulo), "", etiqueta_modulo(registros$modulo)),
        Período = ifelse(is.na(registros$periodo), "", registros$periodo),
        v = ifelse(is.na(registros$version), "", paste0("v", registros$version)),
        Usuario = registros$usuario,
        check.names = FALSE, stringsAsFactors = FALSE
      ), alto = "290px", pagina = 8)
    })
  })
}
