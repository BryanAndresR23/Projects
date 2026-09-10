# ---------------------------------------------------------------------------
# Piezas compartidas por las pantallas de Shiny.
# ---------------------------------------------------------------------------

PALETA <- c("#1f4e79", "#2e75b6", "#4ea1d3", "#7fc4d8", "#c55a11",
            "#ed7d31", "#f4b183", "#548235", "#a9d18e", "#7030a0",
            "#b48ead", "#843c0c")
COLOR_TEXTO <- "#1f2933"

SEVERIDAD_ICONO <- c(ERROR = "🔴", ADVERTENCIA = "🟠", INFO = "🔵")

TIPOS_GRAFICO <- c("Barras horizontales", "Columnas", "Línea", "Área", "Torta", "Treemap")

tema_panel <- function() {
  # Tipografía del sistema: el servidor institucional puede no tener salida a
  # internet, y una fuente de Google que no carga deja la página en serif.
  tema <- bslib::bs_theme(
    version = 5,
    primary = "#1f4e79",
    "body-bg" = "#ffffff",
    "body-color" = COLOR_TEXTO,
    "font-family-base" = "'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
    "headings-font-weight" = "600"
  )
  bslib::bs_add_rules(tema, "
    .value-box-value { font-size: 1.35rem !important; line-height: 1.25; }
    .value-box-title { font-size: .82rem !important; opacity: .85; }
    .bslib-value-box .value-box-area { padding: .85rem 1rem; }
    .card { border-color: #e2e8f0; }
    h2 { font-size: 1.7rem; margin-bottom: .25rem; }
    h4 { font-size: 1.15rem; margin-top: .5rem; }
    table.dataTable { font-size: .9rem; }
  ")
}

icono_estado <- function(estado) {
  iconos <- c("VALIDA" = "🟢", "CON ADVERTENCIAS" = "🟠", "RECHAZADA" = "🔴")
  resultado <- unname(iconos[estado])
  ifelse(is.na(resultado), "⚪", resultado)
}

estado_corto <- function(estado) {
  cortos <- c("VALIDA" = "Válida", "CON ADVERTENCIAS" = "Con advertencias", "RECHAZADA" = "Rechazada")
  resultado <- unname(cortos[estado])
  ifelse(is.na(resultado), estado, resultado)
}

#' Tarjeta de indicador: valor grande, etiqueta y aclaración opcional.
tarjeta <- function(titulo, valor, nota = NULL) {
  bslib::value_box(
    title = titulo,
    value = valor,
    if (!is.null(nota)) htmltools::tags$span(nota, style = "font-size:.8rem;opacity:.85"),
    theme = bslib::value_box_theme(bg = "#f4f6f9", fg = COLOR_TEXTO)
  )
}

#' Las dos métricas principales (operaciones y monto) más contexto.
tarjetas_kpi <- function(datos, universo = NULL) {
  indicadores <- kpis(datos)
  referencia <- if (!is.null(universo) && nrow(universo)) kpis(universo) else NULL
  proporcion <- function(valor, total) {
    if (is.null(total) || is.na(total) || total == 0) return(NULL)
    sprintf("%.1f %% del total", valor * 100 / total)
  }

  bslib::layout_columns(
    fill = FALSE,
    tarjeta("N.° de operaciones", formatear_numero(indicadores$operaciones),
            proporcion(indicadores$operaciones, referencia$operaciones)),
    tarjeta("Monto USD", formatear_usd_compacto(indicadores$monto_usd),
            proporcion(indicadores$monto_usd, referencia$monto_usd)),
    tarjeta("Promedio por operación", formatear_usd_compacto(indicadores$promedio_usd)),
    tarjeta("Corresponsales", formatear_numero(indicadores$corresponsales)),
    tarjeta("Operación mayor", formatear_usd_compacto(indicadores$ticket_maximo))
  )
}

#' Construye el gráfico según la métrica y el tipo elegidos por el usuario.
grafico <- function(tabla, medida, tipo = "Barras horizontales", titulo = "") {
  disposicion <- function(figura, ...) {
    plotly::layout(
      figura,
      title = list(text = titulo, font = list(size = 17, color = COLOR_TEXTO), x = 0),
      margin = list(l = 10, r = 10, t = 60, b = 40),
      paper_bgcolor = "white", plot_bgcolor = "white",
      font = list(family = "Source Sans 3, Segoe UI, sans-serif", color = COLOR_TEXTO),
      ...
    )
  }

  if (is.null(tabla) || !nrow(tabla)) {
    return(disposicion(
      plotly::plot_ly(type = "scatter", mode = "markers", x = 0, y = 0, visible = FALSE),
      xaxis = list(visible = FALSE), yaxis = list(visible = FALSE),
      annotations = list(list(text = "Sin datos para los filtros aplicados", showarrow = FALSE,
                              font = list(size = 15, color = "#6b7280")))
    ))
  }

  etiquetas <- as.character(tabla$etiqueta)
  valores <- as.numeric(tabla$valor)
  textos <- vapply(valores, formatear, character(1), medida = medida)
  ejes <- list(gridcolor = "#eef2f7", zeroline = FALSE)

  figura <- switch(
    tipo,
    "Barras horizontales" = disposicion(
      plotly::plot_ly(x = valores, y = etiquetas, type = "bar", orientation = "h",
                      text = textos, textposition = "auto", marker = list(color = PALETA[1]),
                      hovertemplate = "%{y}<br>%{text}<extra></extra>"),
      xaxis = ejes,
      yaxis = c(ejes, list(categoryorder = "array", categoryarray = rev(etiquetas)))
    ),
    "Columnas" = disposicion(
      plotly::plot_ly(x = etiquetas, y = valores, type = "bar",
                      text = textos, textposition = "auto", marker = list(color = PALETA[2]),
                      hovertemplate = "%{x}<br>%{text}<extra></extra>"),
      xaxis = c(ejes, list(categoryorder = "array", categoryarray = etiquetas)), yaxis = ejes
    ),
    "Línea" = disposicion(
      plotly::plot_ly(x = etiquetas, y = valores, type = "scatter", mode = "lines+markers",
                      line = list(color = PALETA[1], width = 3), marker = list(size = 8),
                      text = textos, hovertemplate = "%{x}<br>%{text}<extra></extra>"),
      xaxis = c(ejes, list(categoryorder = "array", categoryarray = etiquetas)), yaxis = ejes
    ),
    "Área" = disposicion(
      plotly::plot_ly(x = etiquetas, y = valores, type = "scatter", mode = "lines+markers",
                      fill = "tozeroy", line = list(color = PALETA[2], width = 2.5),
                      text = textos, hovertemplate = "%{x}<br>%{text}<extra></extra>"),
      xaxis = c(ejes, list(categoryorder = "array", categoryarray = etiquetas)), yaxis = ejes
    ),
    "Torta" = disposicion(
      plotly::plot_ly(labels = etiquetas, values = abs(valores), type = "pie", hole = 0.45,
                      marker = list(colors = rep(PALETA, 3)), textinfo = "label+percent",
                      hovertemplate = "%{label}<br>%{value:,.2f}<extra></extra>"),
      showlegend = TRUE
    ),
    disposicion(
      plotly::plot_ly(labels = etiquetas, parents = rep("", length(etiquetas)),
                      values = abs(valores), type = "treemap",
                      marker = list(colors = rep(PALETA, 3)),
                      texttemplate = "%{label}<br>%{value:,.0f}",
                      hovertemplate = "%{label}<br>%{value:,.2f}<extra></extra>")
    )
  )
  plotly::config(figura, displaylogo = FALSE, locale = "es",
                 modeBarButtonsToRemove = c("lasso2d", "select2d", "autoScale2d"))
}

#' Barras agrupadas para comparar los dos módulos sobre la misma dimensión.
grafico_comparado <- function(categorias, series, titulo) {
  figura <- plotly::plot_ly()
  for (i in seq_along(series)) {
    figura <- plotly::add_trace(
      figura, x = categorias, y = series[[i]], type = "bar", name = names(series)[i],
      marker = list(color = PALETA[(i - 1) %% length(PALETA) + 1])
    )
  }
  figura <- plotly::layout(
    figura, barmode = "group",
    title = list(text = titulo, font = list(size = 17, color = COLOR_TEXTO), x = 0),
    margin = list(l = 10, r = 10, t = 60, b = 40),
    paper_bgcolor = "white", plot_bgcolor = "white",
    font = list(family = "Source Sans 3, Segoe UI, sans-serif", color = COLOR_TEXTO),
    xaxis = list(gridcolor = "#eef2f7", categoryorder = "array", categoryarray = categorias),
    yaxis = list(gridcolor = "#eef2f7")
  )
  plotly::config(figura, displaylogo = FALSE, locale = "es")
}

#' Tabla estándar del panel: en español, con buscador y descarga.
tabla_dt <- function(datos, columnas_numericas = character(0), alto = "420px", pagina = 12) {
  if (is.null(datos)) datos <- data.frame()
  tabla <- DT::datatable(
    datos,
    rownames = FALSE,
    selection = "none",
    extensions = "Scroller",
    options = list(
      pageLength = pagina,
      scrollY = alto,
      scrollX = TRUE,
      dom = "ftip",
      language = list(
        search = "Buscar:", lengthMenu = "Mostrar _MENU_ filas",
        info = "_START_ a _END_ de _TOTAL_ filas",
        infoEmpty = "Sin filas", zeroRecords = "Sin coincidencias",
        paginate = list(previous = "Anterior", `next` = "Siguiente")
      )
    )
  )
  presentes <- intersect(columnas_numericas, names(datos))
  if (length(presentes)) {
    tabla <- DT::formatCurrency(tabla, presentes, currency = "", digits = 2, mark = ",")
  }
  tabla
}

#' Selector de año y mes que arma el período canónico.
selector_periodo_ui <- function(id_anio, id_mes, ns) {
  hoy <- Sys.Date()
  bslib::layout_columns(
    col_widths = c(4, 8),
    shiny::numericInput(ns(id_anio), "Año", value = as.integer(format(hoy, "%Y")),
                        min = 2015, max = 2100, step = 1),
    shiny::selectInput(ns(id_mes), "Mes",
                       choices = stats::setNames(seq_along(MESES), MESES),
                       selected = as.integer(format(hoy, "%m")))
  )
}

#' Escribe varias tablas en un Excel descargable.
escribir_excel <- function(hojas, ruta) {
  utiles <- Filter(function(t) !is.null(t) && nrow(t) > 0, hojas)
  if (!length(utiles)) utiles <- list(Vacio = data.frame(Aviso = "Sin datos para exportar"))
  utiles <- lapply(utiles, function(t) {
    if (!is.null(rownames(t)) && !identical(rownames(t), as.character(seq_len(nrow(t))))) {
      cbind(Detalle = rownames(t), t)
    } else t
  })
  names(utiles) <- substr(names(utiles), 1, 31)
  writexl::write_xlsx(utiles, ruta)
}
