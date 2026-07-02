# 🎯 Guion para Presentación en PowerPoint
## Sistema de Conciliación de Deuda Externa Pública — BCE

> Guía lista para copiar a PowerPoint. Cada bloque = **una diapositiva**.
> Incluye título, contenido sugerido y notas del expositor. Colores
> institucionales: azul profundo `#16315a`, dorado `#D9B572`, blanco.

---

## Diapositiva 1 — Portada
**Título:** Sistema de Conciliación de Deuda Externa Pública
**Subtítulo:** Automatización del cruce mensual BCE ↔ MEF
**Pie:** Banco Central del Ecuador · Subgerencia de Servicios Financieros Internacionales
- Logo del BCE centrado.
- Fecha y nombre del expositor.

*Notas:* Presentar el propósito: automatizar una conciliación que hoy es manual.

---

## Diapositiva 2 — El problema (situación actual)
**Título:** ¿Qué conciliamos hoy y por qué es complejo?
- Cada mes se cruzan los **pagos de deuda externa pública** entre:
  - **MEF** (Ministerio de Economía y Finanzas)
  - **BCE** (Banco Central, como Agente Oficial)
- Se comparan **6 rubros** por cada cartera (acreedor).
- Proceso **manual**: revisar Excel enormes, sumar préstamo por préstamo,
  identificar diferencias y redactar el oficio de respuesta (Quipux).
- Riesgos: errores de suma, columnas que cambian de posición, horas de trabajo.

*Notas:* Enfatizar el volumen (miles de préstamos) y el riesgo de error humano.

---

## Diapositiva 3 — Los actores y los reportes
**Título:** Insumos del proceso
| Fuente | Reporte | Contenido |
|--------|---------|-----------|
| **MEF** | Reporte Conciliación MEF | Hoja "Resumen" + hoja por cartera con detalle por préstamo |
| **BCE** | Reporte Conciliación BCE | "Giros del Exterior" (desembolsos) y "Giros al Exterior" (pagos) |

- **6 rubros:** Desembolsos, Amortizaciones, Intereses, Comisiones,
  Intereses Condonados, Interés por Mora.

*Notas:* Explicar que el MEF solicita "ratificar o rectificar" por oficio.

---

## Diapositiva 4 — La solución
**Título:** Un sistema que concilia en segundos
- Aplicación **local y segura** (corre en la PC, no expone datos).
- **Arrastras los dos reportes** → detecta el período automáticamente →
  cruza todo → muestra resultado con semáforo.
- Identifica **el préstamo exacto** que no cuadra.
- Permite **gestionar las diferencias** (pagos directos, observaciones).
- Genera el **oficio de respuesta (Quipux)** automáticamente.

*Notas:* De horas a minutos, sin errores de suma.

---

## Diapositiva 5 — Los 4 pasos del sistema
**Título:** Flujo de trabajo en 4 pasos
1. **Recepción de Reportes** — cargar MEF + BCE (una sola zona, arrastrar).
2. **Conciliación de Carteras** — matriz de resultados con TOTAL general.
3. **Gestión de Observaciones** — resolver lo que no cuadra (pagos directos).
4. **Emisión del Oficio** — generar el Quipux de respuesta.

*Notas:* Mostrar la barra lateral con los 4 pasos numerados (captura).

---

## Diapositiva 6 — Paso 1: Recepción de Reportes
**Título:** Carga inteligente
- Una sola zona para **arrastrar o cargar** ambos archivos (.xls / .xlsx).
- **No importa el orden**: el sistema reconoce cuál es MEF y cuál BCE.
- El **período se detecta solo** (ej. "Marzo 2026") leyéndolo del archivo.
- 📸 *Captura sugerida:* pantalla de "Recepción de Reportes" con los dos archivos cargados.

---

## Diapositiva 7 — Paso 2: Conciliación de Carteras
**Título:** Resultado con semáforo
- **Matriz por cartera** con los 6 rubros: MEF · BCE · Diferencia.
- Estado por cartera: **CONCILIADO** (verde) / **DIFERENCIA** (rojo).
- Fila **TOTAL GENERAL** de todas las carteras.
- Semáforo de carteras en la barra lateral.
- 📸 *Captura sugerida:* la matriz de resultados.

*Notas clave (aclarar el "27"):* No son 27 carteras. Son los **rubros con
movimiento**: 10 carteras × hasta 6 rubros, contando solo los que tienen
valor ese mes = 27 comparaciones.

---

## Diapositiva 8 — Cálculo robusto (diferencial)
**Título:** Suma préstamo por préstamo, a prueba de cambios
- **No** se copian subtotales: se **suma cada crédito** (capital, interés,
  comisión, condonados, mora).
- Ubica cada columna por su **título**, no por su letra → funciona aunque el
  BCE **mueva o renombre columnas** entre meses.
- Usa los subtotales oficiales del BCE ("TOTAL BANCOS", "TOTAL BONOS") para
  separar casos ambiguos (ej. Bonos vs Bank of New York).

*Notas:* Este es el corazón técnico. Ejemplo real: Enero cuadró 22/22.

---

## Diapositiva 9 — Paso 3: Gestión de Observaciones
**Título:** Identifica el préstamo exacto que no cuadra
- Por cada cartera con diferencia, muestra **el crédito específico**:
  - Ej. *BIRF-8888 · Desembolsos · MEF 2.099.029,62 | BCE 0,00 | falta 2.099.029,62*
- Clasifica la causa según la **Observación del MEF**:
  - 🟡 **Pago Directo** · 🔵 **Diferencial Cambiario** · ⚪ Revisar
- Para pagos directos: **subir el respaldo** del MEF y **agregarlo al BCE**.
- Cada ajuste agregado **desaparece de la lista**.
- 📸 *Captura sugerida:* tarjeta de diagnóstico de BIRF.

---

## Diapositiva 10 — Pagos directos (caso típico BIRF)
**Título:** El caso del "pago directo"
- El MEF paga directo al proveedor → **no pasa por el BCE** → no consta en su reporte.
- El sistema:
  1. Detecta la diferencia y la marca como **Pago Directo**.
  2. Permite **cargar el respaldo** (Quipux) del MEF.
  3. **Inserta la fila** en el reporte del BCE con su nota:
     *"Considerar desembolso realizado a través de la modalidad pago directo, crédito 9722-0"*.

*Notas:* Es la observación más frecuente. Antes se hacía a mano.

---

## Diapositiva 11 — Paso 4: Emisión del Oficio (Quipux)
**Título:** El oficio de respuesta, automático
- Solo se habilita cuando **todas las carteras concilian**.
- Genera el texto del oficio BCE → MEF **cartera por cartera**:
  - *"BIRF: No existen observaciones en los rubros de desembolsos, amortizaciones, intereses, comisiones e intereses por mora."*
- Campos editables: Nro. de oficio, destinatario, firmante y **lista de copia**.
- **Copiar** / **Descargar .txt** para pegar en Quipux.
- 📸 *Captura sugerida:* el texto generado del oficio.

*Notas:* El texto coincide palabra por palabra con el oficio real.

---

## Diapositiva 12 — Beneficios
**Título:** ¿Qué ganamos?
| Antes (manual) | Con el sistema |
|----------------|----------------|
| Horas por conciliación | **Minutos** |
| Riesgo de error de suma | **Cálculo exacto al centavo** |
| Buscar diferencias a mano | **Señala el crédito exacto** |
| Redactar el oficio a mano | **Oficio generado automáticamente** |
| Se rompe si cambian columnas | **Se adapta solo** |

---

## Diapositiva 13 — Seguridad y despliegue
**Título:** Local, seguro y sin dependencias externas
- Corre en `http://127.0.0.1:5001/` — **solo en la PC**, no sube datos a internet.
- Base de datos local (SQLite) para el historial.
- Se inicia con **doble clic** (`Iniciar_Conciliacion.bat`).
- Respaldo del código en repositorio Git.

---

## Diapositiva 14 — Alcance y siguientes pasos
**Título:** Próximas mejoras
- Insertar los ajustes conservando el **formato original exacto** del reporte BCE.
- Guardar la lista de copia entre meses.
- Reportes históricos y tableros de indicadores.
- (Agrega aquí lo que priorice el área.)

---

## Diapositiva 15 — Cierre
**Título:** Conciliación exacta, rápida y trazable
- Una herramienta hecha a la medida del proceso del BCE.
- Reduce tiempo y riesgo; deja evidencia y genera el oficio.
- **Gracias.**
- Contacto / equipo.

---

## 📎 Anexos sugeridos (capturas a incluir)
1. Pantalla de carga con los dos reportes.
2. Matriz de resultados (con alguna diferencia y con todo conciliado).
3. Tarjeta de diagnóstico de una cartera (BIRF pago directo).
4. Texto del oficio (Quipux) generado.
5. Comparación "Antes vs Después" (tabla de la diapositiva 12).

## 🎨 Tips de diseño
- Fondo azul institucional o blanco con acentos dorados.
- Tipografía sobria (Calibri, Segoe UI o Archivo).
- Máximo 5–6 líneas por diapositiva; que las capturas hablen.
- Usa íconos ✅ ⚠️ 🟡 🔵 para los estados, como en el sistema.
</content>
