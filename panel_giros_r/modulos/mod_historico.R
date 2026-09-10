# ---------------------------------------------------------------------------
# Histórico de cargas: consultar, comparar, descargar y restaurar versiones.
# ---------------------------------------------------------------------------

mod_historico_ui <- function(id) {
  ns <- shiny::NS(id)
  shiny::tagList(
    shiny::h2("Histórico de cargas"),
    shiny::p(class = "text-muted",
             paste("Todas las versiones se conservan con su archivo original, su resultado depurado",
                   "y sus validaciones. Cualquiera puede consultarse y volver a publicarse.")),
    shiny::uiOutput(ns("sin_datos")),
    shiny::uiOutput(ns("cuerpo"))
  )
}

mod_historico_server <- function(id, cargas, usuario, actualizacion, bitacora_reciente) {
  shiny::moduleServer(id, function(input, output, session) {
    ns <- session$ns

    hay_cargas <- shiny::reactive(nrow(cargas()) > 0)

    output$sin_datos <- shiny::renderUI({
      if (hay_cargas()) return(NULL)
      shiny::div(class = "alert alert-info", "Todavía no hay cargas registradas.")
    })

    output$cuerpo <- shiny::renderUI({
      if (!hay_cargas()) return(NULL)
      historico <- cargas()
      shiny::tagList(
        bslib::layout_columns(
          col_widths = c(4, 4, 4), fill = FALSE,
          shiny::selectizeInput(ns("f_modulo"), "Módulo",
                                choices = stats::setNames(names(MODULOS), unname(MODULOS)),
                                multiple = TRUE, options = list(placeholder = "Todos")),
          shiny::selectizeInput(ns("f_periodo"), "Período",
                                choices = stats::setNames(sort(unique(historico$periodo), decreasing = TRUE),
                                                          etiqueta_periodo(sort(unique(historico$periodo), decreasing = TRUE))),
                                multiple = TRUE, options = list(placeholder = "Todos")),
          shiny::selectizeInput(ns("f_estado"), "Estado",
                                choices = sort(unique(historico$estado)),
                                multiple = TRUE, options = list(placeholder = "Todos"))
        ),
        DT::DTOutput(ns("tabla")),
        shiny::uiOutput(ns("conteo")),
        shiny::hr(),
        shiny::h4("Detalle de una versión"),
        shiny::uiOutput(ns("selector")),
        shiny::uiOutput(ns("tarjetas")),
        shiny::uiOutput(ns("ficha")),
        bslib::navset_tab(
          bslib::nav_panel("Validaciones", shiny::uiOutput(ns("validaciones"))),
          bslib::nav_panel("Archivos", shiny::uiOutput(ns("archivos"))),
          bslib::nav_panel("Restaurar", shiny::uiOutput(ns("restaurar"))),
          bslib::nav_panel("Comparar versiones", shiny::uiOutput(ns("comparar")))
        ),
        shiny::hr(),
        shiny::h4("Bitácora de auditoría"),
        DT::DTOutput(ns("bitacora")),
        shiny::downloadButton(ns("descargar"), "Descargar histórico y bitácora en Excel",
                              class = "btn-outline-primary mt-2")
      )
    })

    vista <- shiny::reactive({
      historico <- cargas()
      if (!is.null(input$f_modulo) && length(input$f_modulo)) {
        historico <- historico[historico$modulo %in% input$f_modulo, , drop = FALSE]
      }
      if (!is.null(input$f_periodo) && length(input$f_periodo)) {
        historico <- historico[historico$periodo %in% input$f_periodo, , drop = FALSE]
      }
      if (!is.null(input$f_estado) && length(input$f_estado)) {
        historico <- historico[historico$estado %in% input$f_estado, , drop = FALSE]
      }
      historico
    })

    tabla_vista <- shiny::reactive({
      historico <- vista()
      if (!nrow(historico)) return(NULL)
      data.frame(
        ` ` = ifelse(historico$activa == 1, "✅ Publicada", ""),
        Módulo = etiqueta_modulo(historico$modulo),
        Período = etiqueta_periodo(historico$periodo),
        Versión = paste0("v", historico$version),
        Estado = paste(icono_estado(historico$estado), historico$estado),
        Operaciones = historico$operaciones,
        `Monto USD` = round(historico$monto_usd, 2),
        Descartes = historico$filas_descartadas,
        Errores = historico$errores,
        Advertencias = historico$advertencias,
        Validador = historico$usuario,
        `Fecha de carga` = format(as.POSIXct(historico$fecha_carga, format = "%Y-%m-%dT%H:%M:%S"),
                                  "%d/%m/%Y %H:%M"),
        Archivo = historico$nombre_original,
        Comentario = ifelse(is.na(historico$comentario), "", historico$comentario),
        check.names = FALSE, stringsAsFactors = FALSE
      )
    })

    output$tabla <- DT::renderDT({
      tabla <- tabla_vista()
      shiny::validate(shiny::need(!is.null(tabla), "Ninguna versión cumple los filtros."))
      tabla_dt(tabla, columnas_numericas = c("Monto USD"), alto = "360px", pagina = 10)
    })

    output$conteo <- shiny::renderUI(
      shiny::p(class = "text-muted small", sprintf("%d versiones en el histórico.", nrow(vista())))
    )

    output$selector <- shiny::renderUI({
      historico <- vista()
      if (!nrow(historico)) historico <- cargas()
      etiquetas <- sprintf("%s%s · %s · v%s · %s · %s",
                           ifelse(historico$activa == 1, "✅ ", ""),
                           etiqueta_modulo(historico$modulo),
                           etiqueta_periodo(historico$periodo),
                           historico$version, historico$estado, historico$usuario)
      shiny::selectInput(ns("carga_id"), "Versión",
                         choices = stats::setNames(historico$id, etiquetas), width = "100%")
    })

    carga_elegida <- shiny::reactive({
      shiny::req(input$carga_id)
      obtener_carga(as.integer(input$carga_id))
    })

    output$tarjetas <- shiny::renderUI({
      carga <- carga_elegida()
      shiny::req(carga)
      bslib::layout_columns(
        fill = FALSE,
        tarjeta("Estado", paste(icono_estado(carga$estado), estado_corto(carga$estado))),
        tarjeta("Operaciones", formatear_numero(carga$operaciones)),
        tarjeta("Monto USD", formatear_usd_compacto(carga$monto_usd)),
        tarjeta("Filas descartadas", formatear_numero(carga$filas_descartadas)),
        tarjeta("Publicada", if (carga$activa == 1) "Sí" else "No")
      )
    })

    output$ficha <- shiny::renderUI({
      carga <- carga_elegida()
      shiny::req(carga)
      shiny::tagList(
        shiny::p(class = "text-muted small",
                 sprintf("Archivo original: %s · hoja: %s · encabezado en la fila %s · cargada por %s el %s",
                         carga$nombre_original,
                         if (is.na(carga$hoja)) "—" else carga$hoja,
                         if (is.na(carga$fila_encabezado)) "—" else carga$fila_encabezado,
                         carga$usuario, sub("T", " ", carga$fecha_carga))),
        if (!is.na(carga$comentario) && nzchar(carga$comentario)) {
          shiny::p(class = "text-muted small fst-italic", paste("Comentario:", carga$comentario))
        }
      )
    })

    output$validaciones <- shiny::renderUI({
      carga <- carga_elegida()
      shiny::req(carga)
      registros <- validaciones_de(carga$id)
      if (!nrow(registros)) return(shiny::p(class = "text-muted", "Sin validaciones registradas."))
      lapply(seq_len(nrow(registros)), function(i) {
        fila <- registros[i, ]
        clase <- switch(fila$severidad,
                        ERROR = "alert alert-danger py-2",
                        ADVERTENCIA = "alert alert-warning py-2",
                        "text-muted small")
        shiny::div(
          class = clase,
          unname(SEVERIDAD_ICONO[fila$severidad]), " ",
          shiny::strong(fila$regla), " — ", fila$mensaje,
          if (!is.na(fila$detalle) && nzchar(fila$detalle)) {
            shiny::tags$details(
              shiny::tags$summary("Ver detalle técnico"),
              shiny::tags$pre(style = "white-space:pre-wrap;font-size:.8rem",
                              jsonlite::prettify(fila$detalle))
            )
          }
        )
      })
    })

    output$archivos <- shiny::renderUI({
      carga <- carga_elegida()
      shiny::req(carga)
      descriptores <- list(
        list("Archivo original", carga$ruta_original, "original"),
        list("Resultado depurado (CSV)", carga$ruta_depurado, "depurado"),
        list("Filas descartadas (CSV)", carga$ruta_descartes, "descartes")
      )
      shiny::tagList(lapply(descriptores, function(d) {
        existe <- !is.na(d[[2]]) && file.exists(d[[2]])
        if (!existe) return(shiny::p(class = "text-muted small", paste0(d[[1]], ": no disponible.")))
        shiny::div(class = "mb-2",
                   shiny::downloadButton(ns(paste0("bajar_", d[[3]])),
                                         paste("Descargar", tolower(d[[1]])),
                                         class = "btn-outline-primary btn-sm"))
      }))
    })

    descarga_archivo <- function(columna) {
      shiny::downloadHandler(
        filename = function() {
          carga <- carga_elegida()
          basename(carga[[columna]])
        },
        content = function(ruta) file.copy(carga_elegida()[[columna]], ruta, overwrite = TRUE)
      )
    }
    output$bajar_original <- descarga_archivo("ruta_original")
    output$bajar_depurado <- descarga_archivo("ruta_depurado")
    output$bajar_descartes <- descarga_archivo("ruta_descartes")

    output$restaurar <- shiny::renderUI({
      carga <- carga_elegida()
      shiny::req(carga)
      if (carga$activa == 1) {
        return(shiny::div(class = "alert alert-success",
                          "Esta versión es la que está publicada en el tablero."))
      }
      if (identical(carga$estado, ESTADO_RECHAZADA)) {
        return(shiny::div(class = "alert alert-danger",
                          paste("Esta versión fue rechazada por validación y no puede publicarse.",
                                "Se conserva únicamente como evidencia del intento de carga.")))
      }
      publicada <- carga_activa(carga$modulo, carga$periodo)
      shiny::tagList(
        if (!is.null(publicada)) {
          shiny::div(class = "alert alert-warning",
                     sprintf(paste("Al restaurar la v%s, la v%s dejará de estar publicada",
                                   "(pero seguirá en el histórico)."),
                             carga$version, publicada$version))
        },
        shiny::textInput(ns("motivo"), "Motivo de la restauración",
                         placeholder = "Ej.: la versión publicada tenía el archivo equivocado",
                         width = "100%"),
        shiny::actionButton(ns("restaurar_ahora"),
                            sprintf("Restaurar y publicar la v%s", carga$version),
                            class = "btn-primary")
      )
    })

    shiny::observeEvent(input$restaurar_ahora, {
      carga <- carga_elegida()
      shiny::req(carga)
      tryCatch({
        activar_version(carga$modulo, carga$periodo, carga$version,
                        usuario = usuario(), motivo = input$motivo %||% "")
        actualizacion(actualizacion() + 1)
        shiny::showNotification(sprintf("Se publicó la v%s.", carga$version), type = "message")
      }, error = function(e) {
        shiny::showNotification(conditionMessage(e), type = "error", duration = NULL)
      })
    })

    output$comparar <- shiny::renderUI({
      carga <- carga_elegida()
      shiny::req(carga)
      historico <- cargas()
      hermanas <- historico[historico$modulo == carga$modulo & historico$periodo == carga$periodo, ]
      versiones <- sort(hermanas$version)
      if (length(versiones) < 2) {
        return(shiny::p(class = "text-muted",
                        "Se necesita más de una versión del mismo módulo y período para comparar."))
      }
      shiny::tagList(
        bslib::layout_columns(
          col_widths = c(6, 6), fill = FALSE,
          shiny::selectInput(ns("version_a"), "Versión A", choices = versiones, selected = versiones[1]),
          shiny::selectInput(ns("version_b"), "Versión B", choices = versiones,
                             selected = versiones[length(versiones)])
        ),
        DT::DTOutput(ns("comparacion"))
      )
    })

    output$comparacion <- DT::renderDT({
      carga <- carga_elegida()
      shiny::req(carga, input$version_a, input$version_b)
      shiny::validate(shiny::need(input$version_a != input$version_b, "Elija dos versiones distintas."))
      tabla_dt(comparar_versiones(carga$modulo, carga$periodo,
                                  as.integer(input$version_a), as.integer(input$version_b)),
               alto = "340px", pagina = 10)
    })

    output$bitacora <- DT::renderDT({
      registros <- bitacora_reciente()
      shiny::validate(shiny::need(nrow(registros) > 0, "Sin movimientos registrados."))
      tabla_dt(data.frame(
        Fecha = format(as.POSIXct(registros$fecha, format = "%Y-%m-%dT%H:%M:%S"), "%d/%m/%Y %H:%M"),
        Usuario = registros$usuario,
        Acción = registros$accion,
        Módulo = ifelse(is.na(registros$modulo), "", etiqueta_modulo(registros$modulo)),
        Período = ifelse(is.na(registros$periodo), "", registros$periodo),
        Versión = ifelse(is.na(registros$version), "", paste0("v", registros$version)),
        Detalle = ifelse(is.na(registros$detalle), "", registros$detalle),
        check.names = FALSE, stringsAsFactors = FALSE
      ), alto = "340px", pagina = 10)
    })

    output$descargar <- shiny::downloadHandler(
      filename = function() sprintf("historico_cargas_%s.xlsx", format(Sys.Date(), "%Y%m%d")),
      content = function(ruta) {
        escribir_excel(list(Histórico = tabla_vista(), Bitácora = bitacora_reciente()), ruta)
      }
    )
  })
}
