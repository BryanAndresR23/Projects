"""Purga los descartes atascados de ``agenda_cache.json``.

Motivo
------
Las versiones anteriores del analizador marcaban un documento ilegible como
``{"ocr_complete": true, "candidate": null}`` sin marca de tiempo. Esa marca
era permanente: el documento no se reintentaba nunca, ni siquiera al pulsar
"Refrescar" en la agenda. Así desapareció el pago del préstamo BID-1740.

El módulo corregido ya reintenta automáticamente las entradas heredadas (las
que no tienen ``checked_at``). Este script sirve para forzar el reintento de
inmediato, sin esperar al siguiente ciclo, y para ver qué documentos estaban
afectados.

Uso
---
    python herramientas\\reparar_agenda_descartados.py            # informe
    python herramientas\\reparar_agenda_descartados.py --aplicar  # purga

Siempre crea un respaldo con fecha y hora antes de escribir.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1]
CACHE_FILE = PACKAGE_DIR / "codigo" / "agenda_pagos_data" / "agenda_cache.json"


def descartados(cache: dict) -> dict[str, dict]:
    return {
        ruta: entrada
        for ruta, entrada in cache.items()
        if isinstance(entrada, dict)
        and entrada.get("candidate") is None
        and entrada.get("ocr_complete") is True
    }


def mes_de(ruta: str) -> str:
    encontrado = re.search(r"Pagos[\\/]([^\\/]+)", ruta)
    return encontrado.group(1) if encontrado else "(sin mes)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Escribe los cambios. Sin esta bandera solo muestra el informe.",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=CACHE_FILE,
        help=f"Ruta de agenda_cache.json (por omisión: {CACHE_FILE})",
    )
    parser.add_argument(
        "--mes",
        action="append",
        default=[],
        help="Purgar solo estos meses (repetible). Por omisión, todos.",
    )
    args = parser.parse_args()

    if not args.cache.is_file():
        print(f"No se encuentra el caché: {args.cache}")
        return 1

    cache = json.loads(args.cache.read_text(encoding="utf-8"))
    afectados = descartados(cache)

    if args.mes:
        meses = {mes.casefold() for mes in args.mes}
        afectados = {
            ruta: entrada
            for ruta, entrada in afectados.items()
            if mes_de(ruta).casefold() in meses
        }

    print(f"Caché            : {args.cache}")
    print(f"Entradas totales : {len(cache)}")
    print(f"Descartadas      : {len(afectados)}")
    print()

    if not afectados:
        print("No hay documentos descartados que reparar.")
        return 0

    por_mes: dict[str, list[str]] = {}
    for ruta in afectados:
        por_mes.setdefault(mes_de(ruta), []).append(ruta)

    for mes in sorted(por_mes):
        print(f"[{mes}]  {len(por_mes[mes])} documento(s)")
        for ruta in sorted(por_mes[mes]):
            partes = ruta.replace("/", "\\").split("\\")
            carpeta = partes[-2] if len(partes) > 1 else "?"
            print(f"    {carpeta}")
            print(f"      -> {partes[-1]}")
        print()

    if not args.aplicar:
        print("Informe únicamente. Vuelva a ejecutar con --aplicar para purgar.")
        return 0

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    respaldo = args.cache.with_name(f"{args.cache.stem}.{marca}.bak.json")
    shutil.copy2(args.cache, respaldo)
    print(f"Respaldo creado  : {respaldo}")

    for ruta in afectados:
        cache.pop(ruta, None)

    temporal = args.cache.with_suffix(".tmp")
    temporal.write_text(
        json.dumps(cache, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporal.replace(args.cache)

    print(f"Purgadas         : {len(afectados)} entrada(s)")
    print()
    print("Siguiente paso: reinicie el servicio y pulse Refrescar en la agenda.")
    print("Los documentos se volverán a leer con el extractor corregido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
