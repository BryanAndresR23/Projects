"""Línea de comandos del panel: sirve para automatizar cargas sin abrir la web.

Ejemplos:
    python -m nucleo.cli cargar --archivo giros_al_junio.xlsx --modulo GIROS_AL --periodo 2026-06
    python -m nucleo.cli historico --modulo GIROS_AL
    python -m nucleo.cli publicar --modulo GIROS_AL --periodo 2026-06 --version 2
    python -m nucleo.cli resumen --periodo 2026-06
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nucleo import config, metricas, repositorio  # noqa: E402


def _cargar(args: argparse.Namespace) -> int:
    resultado = repositorio.registrar_carga(
        Path(args.archivo), modulo=args.modulo, periodo=args.periodo,
        usuario=args.usuario, comentario=args.comentario or "",
        hoja=args.hoja, activar=not args.sin_publicar,
    )
    print(f"Versión v{resultado.version} · estado {resultado.estado} · "
          f"{'publicada' if resultado.activa else 'no publicada'}")
    print(f"  operaciones: {resultado.etl.filas_depuradas} | "
          f"descartes: {resultado.etl.filas_descartadas} | "
          f"monto USD: {resultado.etl.monto_usd:,.2f}")
    for hallazgo in resultado.hallazgos:
        print(f"  [{hallazgo.severidad:11s}] {hallazgo.regla}: {hallazgo.mensaje}")
    if resultado.motivo_no_activada:
        print(f"  → {resultado.motivo_no_activada}")
    return 1 if resultado.estado == config.ESTADO_RECHAZADA else 0


def _historico(args: argparse.Namespace) -> int:
    cargas = repositorio.listar_cargas(modulo=args.modulo, periodo=args.periodo)
    if cargas.empty:
        print("Sin cargas registradas.")
        return 0
    columnas = ["modulo", "periodo", "version", "activa", "estado", "usuario",
                "fecha_carga", "operaciones", "monto_usd", "nombre_original"]
    print(cargas[columnas].to_string(index=False))
    return 0


def _publicar(args: argparse.Namespace) -> int:
    resultado = repositorio.activar_version(
        args.modulo, args.periodo, args.version, usuario=args.usuario, motivo=args.motivo or "",
    )
    print(f"{resultado['accion']}: {config.etiqueta_modulo(args.modulo)} "
          f"{config.etiqueta_periodo(args.periodo)} → v{resultado['version']} "
          f"(antes v{resultado['version_anterior']})")
    return 0


def _resumen(args: argparse.Namespace) -> int:
    datos = repositorio.operaciones(solo_activas=True)
    if args.modulo:
        datos = datos[datos["modulo"] == args.modulo]
    if args.periodo:
        datos = datos[datos["periodo"] == args.periodo]
    if datos.empty:
        print("Sin operaciones publicadas para ese filtro.")
        return 0
    indicadores = metricas.kpis(datos)
    print(f"Operaciones: {indicadores['operaciones']:,}")
    print(f"Monto USD:   {indicadores['monto_usd']:,.2f}")
    print(f"Promedio:    {indicadores['promedio_usd']:,.2f}")
    print(f"\nPor {metricas.DIMENSIONES[args.dimension].lower()}:")
    print(metricas.ranking(datos, args.dimension, top=args.top).to_string())
    return 0


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="panel-giros", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subcomandos = parser.add_subparsers(dest="comando", required=True)

    cargar = subcomandos.add_parser("cargar", help="Registra un archivo como nueva versión")
    cargar.add_argument("--archivo", required=True)
    cargar.add_argument("--modulo", required=True, choices=list(config.MODULOS))
    cargar.add_argument("--periodo", required=True, help="Formato AAAA-MM")
    cargar.add_argument("--usuario")
    cargar.add_argument("--comentario")
    cargar.add_argument("--hoja")
    cargar.add_argument("--sin-publicar", action="store_true",
                        help="Guarda en el histórico sin publicar en el tablero")
    cargar.set_defaults(funcion=_cargar)

    historico = subcomandos.add_parser("historico", help="Lista las versiones registradas")
    historico.add_argument("--modulo", choices=list(config.MODULOS))
    historico.add_argument("--periodo")
    historico.set_defaults(funcion=_historico)

    publicar = subcomandos.add_parser("publicar", help="Publica o restaura una versión")
    publicar.add_argument("--modulo", required=True, choices=list(config.MODULOS))
    publicar.add_argument("--periodo", required=True)
    publicar.add_argument("--version", required=True, type=int)
    publicar.add_argument("--usuario")
    publicar.add_argument("--motivo")
    publicar.set_defaults(funcion=_publicar)

    resumen = subcomandos.add_parser("resumen", help="Indicadores de lo publicado")
    resumen.add_argument("--modulo", choices=list(config.MODULOS))
    resumen.add_argument("--periodo")
    resumen.add_argument("--dimension", default="corresponsal", choices=list(metricas.DIMENSIONES))
    resumen.add_argument("--top", type=int, default=10)
    resumen.set_defaults(funcion=_resumen)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    return args.funcion(args)


if __name__ == "__main__":
    raise SystemExit(main())
