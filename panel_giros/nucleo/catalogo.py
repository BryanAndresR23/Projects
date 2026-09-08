"""Lectura de los catálogos configurables (columnas, módulos, monedas, TC)."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any

import yaml

from nucleo import config


def normalizar(texto: Any) -> str:
    """Deja un texto comparable: sin tildes, en mayúsculas y sin puntuación.

    "Área 750." -> "AREA 750"   |   "Tipo de Cambio" -> "TIPO DE CAMBIO"
    """
    if texto is None:
        return ""
    s = str(texto)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=1)
def _catalogo_crudo() -> dict:
    ruta = config.dir_catalogos() / "columnas.yaml"
    with open(ruta, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def recargar() -> None:
    """Vuelve a leer los catálogos desde disco (útil tras editarlos)."""
    _catalogo_crudo.cache_clear()
    tipos_cambio.cache_clear()


def campos() -> dict[str, dict]:
    return _catalogo_crudo().get("campos", {})


def campos_requeridos() -> list[str]:
    return [c for c, d in campos().items() if d.get("requerido")]


def etiqueta_campo(campo: str) -> str:
    return campos().get(campo, {}).get("etiqueta", campo)


@lru_cache(maxsize=1)
def _indice_alias() -> dict[str, str]:
    """Alias normalizado -> nombre canónico del campo."""
    indice: dict[str, str] = {}
    for campo, definicion in campos().items():
        indice.setdefault(normalizar(campo), campo)
        for alias in definicion.get("alias", []):
            indice.setdefault(normalizar(alias), campo)
    return indice


def campo_de_encabezado(encabezado: Any) -> str | None:
    """Devuelve el campo canónico al que corresponde un encabezado del archivo.

    Primero busca coincidencia exacta con algún alias; si no la hay, gana el
    alias cuyas palabras estén todas contenidas en el encabezado y que más
    palabras aporte. Así "Fecha de la operación" cae en ``fecha`` (por el alias
    "FECHA OPERACION") y no en ``referencia`` (por el alias "OPERACION").
    """
    clave = normalizar(encabezado)
    if not clave:
        return None
    indice = _indice_alias()
    if clave in indice:
        return indice[clave]

    palabras = set(clave.split())
    mejor: tuple[int, int, str] | None = None
    for alias, campo in indice.items():
        tokens = set(alias.split())
        if not tokens or not tokens.issubset(palabras):
            continue
        marca = (len(tokens), len(alias), campo)
        if mejor is None or marca[:2] > mejor[:2]:
            mejor = marca
    return mejor[2] if mejor else None


def modulos() -> dict[str, dict]:
    return _catalogo_crudo().get("modulos", {})


def marcadores_modulo(modulo: str) -> list[str]:
    return [normalizar(m) for m in modulos().get(modulo, {}).get("marcadores", [])]


def modulo_de_texto(texto: Any) -> str | None:
    """Deduce el módulo a partir de un texto (columna sentido o nombre de archivo).

    Se prefiere el marcador más largo para que "GIROS DEL" gane sobre "AL"
    cuando ambos aparecen en la misma cadena.
    """
    clave = normalizar(texto)
    if not clave:
        return None
    mejor: tuple[int, str] | None = None
    for modulo in modulos():
        for marcador in marcadores_modulo(modulo):
            if re.search(rf"(?<![A-Z0-9]){re.escape(marcador)}(?![A-Z0-9])", clave):
                if mejor is None or len(marcador) > mejor[0]:
                    mejor = (len(marcador), modulo)
    return mejor[1] if mejor else None


@lru_cache(maxsize=1)
def _indice_monedas() -> dict[str, str]:
    indice: dict[str, str] = {}
    for iso, nombres in (_catalogo_crudo().get("monedas") or {}).items():
        indice[normalizar(iso)] = iso
        for nombre in nombres:
            indice[normalizar(nombre)] = iso
    return indice


def monedas_conocidas() -> list[str]:
    return sorted(set(_indice_monedas().values()))


def normalizar_moneda(valor: Any) -> str:
    """Lleva la moneda a código ISO cuando se la reconoce; si no, la deja limpia."""
    clave = normalizar(valor)
    if not clave:
        return ""
    return _indice_monedas().get(clave, clave)


def filas_no_dato() -> list[str]:
    return [normalizar(t) for t in (_catalogo_crudo().get("filas_no_dato") or [])]


@lru_cache(maxsize=1)
def tipos_cambio() -> dict[str, dict[str, float]]:
    """Tipos de cambio a USD por moneda y período (catálogo opcional)."""
    ruta = config.dir_catalogos() / "tipos_cambio.yaml"
    if not ruta.exists():
        return {}
    with open(ruta, "r", encoding="utf-8") as f:
        crudo = yaml.safe_load(f) or {}
    tabla: dict[str, dict[str, float]] = {}
    for moneda, periodos in crudo.items():
        if not isinstance(periodos, dict):
            continue
        tabla[normalizar_moneda(moneda)] = {
            str(p): float(v) for p, v in periodos.items() if v is not None
        }
    return tabla


def tipo_cambio(moneda: str, periodo: str) -> float | None:
    return tipos_cambio().get(moneda, {}).get(periodo)
