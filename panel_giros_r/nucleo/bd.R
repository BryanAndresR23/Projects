# ---------------------------------------------------------------------------
# Base SQLite: esquema del histórico de cargas y de los hechos depurados.
# ---------------------------------------------------------------------------

ESQUEMA <- c(
  # Una fila por cada intento de carga. Nunca se borra: es el histórico.
  "CREATE TABLE IF NOT EXISTS cargas (
      id                 INTEGER PRIMARY KEY AUTOINCREMENT,
      modulo             TEXT    NOT NULL,
      periodo            TEXT    NOT NULL,
      version            INTEGER NOT NULL,
      activa             INTEGER NOT NULL DEFAULT 0,
      estado             TEXT    NOT NULL,
      usuario            TEXT    NOT NULL,
      fecha_carga        TEXT    NOT NULL,
      nombre_original    TEXT    NOT NULL,
      hash_original      TEXT    NOT NULL,
      ruta_original      TEXT    NOT NULL,
      ruta_depurado      TEXT,
      ruta_descartes     TEXT,
      hoja               TEXT,
      fila_encabezado    INTEGER,
      filas_origen       INTEGER NOT NULL DEFAULT 0,
      filas_depuradas    INTEGER NOT NULL DEFAULT 0,
      filas_descartadas  INTEGER NOT NULL DEFAULT 0,
      operaciones        INTEGER NOT NULL DEFAULT 0,
      monto_usd          REAL    NOT NULL DEFAULT 0,
      errores            INTEGER NOT NULL DEFAULT 0,
      advertencias       INTEGER NOT NULL DEFAULT 0,
      comentario         TEXT,
      UNIQUE (modulo, periodo, version)
   )",
  # Regla del negocio: una sola versión activa por módulo y período.
  "CREATE UNIQUE INDEX IF NOT EXISTS ux_cargas_activa
      ON cargas (modulo, periodo) WHERE activa = 1",
  # Resultado de cada regla de validación aplicada a una carga.
  "CREATE TABLE IF NOT EXISTS validaciones (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      carga_id        INTEGER NOT NULL REFERENCES cargas (id) ON DELETE CASCADE,
      regla           TEXT    NOT NULL,
      severidad       TEXT    NOT NULL,
      mensaje         TEXT    NOT NULL,
      filas_afectadas INTEGER NOT NULL DEFAULT 0,
      detalle         TEXT
   )",
  "CREATE INDEX IF NOT EXISTS ix_validaciones_carga ON validaciones (carga_id)",
  # Hechos depurados que alimentan el tablero.
  "CREATE TABLE IF NOT EXISTS operaciones (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      carga_id      INTEGER NOT NULL REFERENCES cargas (id) ON DELETE CASCADE,
      modulo        TEXT    NOT NULL,
      periodo       TEXT    NOT NULL,
      fecha         TEXT,
      anio          INTEGER,
      mes           INTEGER,
      nombre_mes    TEXT,
      referencia    TEXT,
      corresponsal  TEXT,
      area_750      TEXT,
      deuda_771     TEXT,
      tipo_mensaje  TEXT,
      proceso       TEXT,
      moneda        TEXT,
      monto         REAL,
      tipo_cambio   REAL,
      monto_usd     REAL,
      beneficiario  TEXT,
      estado        TEXT
   )",
  "CREATE INDEX IF NOT EXISTS ix_operaciones_carga ON operaciones (carga_id)",
  "CREATE INDEX IF NOT EXISTS ix_operaciones_modulo ON operaciones (modulo, periodo)",
  # Bitácora de auditoría: quién cargó, publicó o restauró qué y cuándo.
  "CREATE TABLE IF NOT EXISTS bitacora (
      id       INTEGER PRIMARY KEY AUTOINCREMENT,
      fecha    TEXT NOT NULL,
      usuario  TEXT NOT NULL,
      accion   TEXT NOT NULL,
      modulo   TEXT,
      periodo  TEXT,
      version  INTEGER,
      carga_id INTEGER,
      detalle  TEXT
   )"
)

#' Abre la base (creándola si hace falta) con el esquema al día.
#'
#' En un servidor la usan varias personas a la vez: WAL permite que unos lean
#' mientras otro escribe, y busy_timeout hace que una carga simultánea espere
#' su turno en lugar de fallar con "database is locked".
conectar_bd <- function(ruta = ruta_bd()) {
  dir.create(dirname(ruta), recursive = TRUE, showWarnings = FALSE)
  con <- DBI::dbConnect(RSQLite::SQLite(), ruta)
  DBI::dbExecute(con, "PRAGMA foreign_keys = ON")
  DBI::dbExecute(con, "PRAGMA busy_timeout = 15000")
  try(DBI::dbGetQuery(con, "PRAGMA journal_mode = WAL"), silent = TRUE)
  for (sentencia in ESQUEMA) DBI::dbExecute(con, sentencia)
  con
}

#' Ejecuta una expresión con una conexión abierta y la cierra siempre.
#'
#' Uso:  con_bd(function(con) DBI::dbGetQuery(con, "SELECT ..."))
con_bd <- function(accion, ruta = ruta_bd()) {
  con <- conectar_bd(ruta)
  on.exit(DBI::dbDisconnect(con), add = TRUE)
  accion(con)
}

#' Igual que con_bd, pero dentro de una transacción (revierte ante error).
con_bd_transaccion <- function(accion, ruta = ruta_bd()) {
  con <- conectar_bd(ruta)
  on.exit(DBI::dbDisconnect(con), add = TRUE)
  DBI::dbBegin(con)
  resultado <- tryCatch(
    {
      valor <- accion(con)
      DBI::dbCommit(con)
      valor
    },
    error = function(e) {
      try(DBI::dbRollback(con), silent = TRUE)
      stop(e)
    }
  )
  resultado
}

registrar_bitacora <- function(con, usuario, accion, modulo = NA, periodo = NA,
                               version = NA, carga_id = NA, detalle = NA) {
  DBI::dbExecute(
    con,
    "INSERT INTO bitacora (fecha, usuario, accion, modulo, periodo, version, carga_id, detalle)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    params = list(
      format(Sys.time(), "%Y-%m-%dT%H:%M:%S"), usuario, accion,
      .nulo_si_na(modulo), .nulo_si_na(periodo),
      .nulo_si_na(version), .nulo_si_na(carga_id), .nulo_si_na(detalle)
    )
  )
  invisible(TRUE)
}

.nulo_si_na <- function(valor) {
  if (length(valor) == 0 || is.na(valor)) NA else valor
}
