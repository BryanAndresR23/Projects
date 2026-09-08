"""Genera archivos de ejemplo para probar el panel sin datos reales.

Uso:  python herramientas/generar_datos_demo.py [carpeta_destino]

Crea un Excel por módulo y período con la estructura que espera el ETL,
incluyendo a propósito filas imperfectas (totales, fechas fuera de período,
montos vacíos) para que se vean las validaciones en acción.
"""
from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from nucleo import config  # noqa: E402

CORRESPONSALES = [
    "CITIBANK N.A.", "JP MORGAN CHASE", "BANCO SANTANDER", "BBVA",
    "DEUTSCHE BANK", "BANK OF AMERICA", "COMMERZBANK", "WELLS FARGO",
]
AREAS_750 = ["750-01", "750-02", "750-03", "750-04"]
DEUDAS_771 = ["771-100", "771-200", "771-300", "771-400"]
PROCESOS = ["PAGO DEUDA EXTERNA", "DESEMBOLSO", "COMISIONES", "INTERESES", "AMORTIZACION"]
MONEDAS = [("USD", 1.0), ("EUR", 1.08), ("GBP", 1.27), ("JPY", 0.0064)]


def _fila(indice: int, modulo: str, anio: int, mes: int, aleatorio: random.Random) -> dict:
    moneda, factor = aleatorio.choice(MONEDAS) if aleatorio.random() > 0.55 else ("USD", 1.0)
    monto = round(aleatorio.uniform(5_000, 4_500_000), 2)
    dia = aleatorio.randint(1, 28)
    prefijo = "TF" if aleatorio.random() > 0.4 else "GS"
    return {
        "FECHA DE OPERACIÓN": date(anio, mes, dia).strftime("%d/%m/%Y"),
        "N° OPERACIÓN": f"{prefijo}-01-{7712600000 + indice}",
        "SENTIDO": config.MODULOS[modulo],
        "BANCO CORRESPONSAL": aleatorio.choice(CORRESPONSALES),
        "ÁREA 750": aleatorio.choice(AREAS_750),
        "DEUDA 771": aleatorio.choice(DEUDAS_771),
        "TIPO DE MENSAJE": prefijo,
        "PROCESO": aleatorio.choice(PROCESOS),
        "MONEDA": moneda,
        "MONTO": monto,
        "TIPO DE CAMBIO": factor,
        "MONTO USD": round(monto * factor, 2),
        "BENEFICIARIO": f"ACREEDOR {aleatorio.randint(100, 999)}",
        "ESTADO": aleatorio.choice(["PROCESADO", "PROCESADO", "PROCESADO", "PENDIENTE"]),
    }


def generar(modulo: str, anio: int, mes: int, filas: int, semilla: int) -> pd.DataFrame:
    aleatorio = random.Random(semilla)
    registros = [_fila(i, modulo, anio, mes, aleatorio) for i in range(filas)]

    # Ruido deliberado para ejercitar las validaciones.
    fuera = _fila(9001, modulo, anio, mes, aleatorio)
    fuera["FECHA DE OPERACIÓN"] = (date(anio, mes, 1) - timedelta(days=20)).strftime("%d/%m/%Y")
    registros.append(fuera)

    sin_monto = _fila(9002, modulo, anio, mes, aleatorio)
    sin_monto["MONTO"] = None
    sin_monto["MONTO USD"] = None
    registros.append(sin_monto)

    sin_corresponsal = _fila(9003, modulo, anio, mes, aleatorio)
    sin_corresponsal["BANCO CORRESPONSAL"] = ""
    registros.append(sin_corresponsal)

    tabla = pd.DataFrame(registros)
    total = {c: "" for c in tabla.columns}
    total["FECHA DE OPERACIÓN"] = "TOTAL"
    total["MONTO USD"] = tabla["MONTO USD"].dropna().sum()
    return pd.concat([tabla, pd.DataFrame([total])], ignore_index=True)


def escribir(destino: Path, modulo: str, anio: int, mes: int, filas: int, semilla: int) -> Path:
    tabla = generar(modulo, anio, mes, filas, semilla)
    periodo = config.periodo(anio, mes)
    ruta = destino / f"{config.MODULOS[modulo].replace(' ', '_')}_{periodo}.xlsx"
    with pd.ExcelWriter(ruta, engine="openpyxl") as escritor:
        # Dos filas de título antes del encabezado: el ETL debe encontrarlo solo.
        encabezado = pd.DataFrame(
            [[f"REPORTE DE {config.MODULOS[modulo].upper()}"], [config.etiqueta_periodo(periodo)]]
        )
        encabezado.to_excel(escritor, index=False, header=False, startrow=0)
        tabla.to_excel(escritor, index=False, startrow=3)
    return ruta


def main() -> None:
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else config.dir_datos() / "demo"
    destino.mkdir(parents=True, exist_ok=True)
    generadas = []
    for indice, (anio, mes) in enumerate([(2026, 4), (2026, 5), (2026, 6)]):
        for modulo in ("GIROS_AL", "GIROS_DEL"):
            generadas.append(escribir(destino, modulo, anio, mes, 120, semilla=indice * 10 + len(modulo)))
    print(f"Archivos de ejemplo generados en {destino}:")
    for ruta in generadas:
        print(f"  - {ruta.name}")


if __name__ == "__main__":
    main()
