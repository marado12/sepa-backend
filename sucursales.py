"""
Catálogo estático de sucursales.

Responde "¿qué cadenas tengo cerca?" sin tocar la red. Es la mitad lenta del
dato: las sucursales cambian dos veces por año, los precios todos los días.
Separarlas es lo que permite que la app degrade en vez de morir cuando se cae
la fuente de precios.

Se carga de data/sucursales.csv, generado por scripts/generar_sucursales.py.
"""

from __future__ import annotations

import csv
import logging
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

RUTA_CSV = Path(os.environ.get(
    "SUCURSALES_PATH", Path(__file__).resolve().parent / "data" / "sucursales.csv"))


# Qué se descartó en el último parseo, por ruta. `cargar` está bajo @lru_cache,
# así que su cuerpo corre una sola vez por archivo: justo cuando hay algo que
# registrar. Los lee `resumen()`, que es lo que viaja en /api/status.
_STATS: dict[str, dict] = {}


def _stats(ruta) -> dict:
    return _STATS.get(str(ruta),
                      {"filas_leidas": 0, "sin_columna": 0, "dato_invalido": 0})


def _problema(p: Path, cargadas: int, st: dict) -> Optional[str]:
    """
    Por qué el catálogo no sirve, en una frase. None si está sano.

    Análogo a `precios_vtex._aviso()`: el dato crudo está en los contadores, esto
    es lo que se le puede mostrar a alguien.
    """
    if not p.exists():
        return f"No existe el archivo de sucursales ({p})."
    if st["filas_leidas"] and not cargadas:
        if st["sin_columna"]:
            return (f"El catálogo tiene {st['filas_leidas']} filas y no cargó ninguna: "
                    f"le faltan columnas. ¿Cambiaron los encabezados del CSV?")
        return (f"El catálogo tiene {st['filas_leidas']} filas y no cargó ninguna: "
                f"todas traen datos inválidos.")
    descartadas = st["sin_columna"] + st["dato_invalido"]
    if descartadas:
        return f"Se descartaron {descartadas} de {st['filas_leidas']} filas del catálogo."
    return None


@dataclass(frozen=True)
class Sucursal:
    cadena: str
    bandera: str
    provincia: str
    lat: float
    lon: float
    id_comercio: str
    id_sucursal: str


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


@lru_cache(maxsize=1)
def cargar(ruta: Optional[str] = None) -> tuple[Sucursal, ...]:
    """
    Lee el CSV una sola vez por proceso. Son ~2.200 filas y ~123 KB: entra sin
    problema en los 512 MB de Render, y evita releer en cada request.

    Con csv de stdlib a propósito, no pandas: esto tiene que poder cargar aunque
    el pipeline de parquet esté roto, que es justamente el escenario que este
    módulo existe para cubrir.
    """
    p = Path(ruta) if ruta else RUTA_CSV
    if not p.exists():
        log.error("[sucursales] no existe %s — el filtro geográfico queda vacío", p)
        return ()

    out: list[Sucursal] = []
    leidas = sin_columna = dato_invalido = 0
    with p.open(encoding="utf-8", newline="") as f:
        for fila in csv.DictReader(f):
            leidas += 1
            try:
                out.append(Sucursal(
                    cadena=fila["cadena"],
                    bandera=fila.get("bandera", ""),
                    provincia=fila.get("provincia", ""),
                    lat=float(fila["lat"]),
                    lon=float(fila["lon"]),
                    id_comercio=fila.get("id_comercio", ""),
                    id_sucursal=fila.get("id_sucursal", ""),
                ))
            # Una fila corrupta no invalida el catálogo entero — pero se cuenta.
            # Descartar en silencio hacía que "el CSV cambió de encabezados" se
            # viera igual que "no hay sucursales cerca tuyo".
            except KeyError:
                sin_columna += 1
            except (ValueError, TypeError):
                dato_invalido += 1

    _STATS[str(p)] = {"filas_leidas": leidas, "sin_columna": sin_columna,
                      "dato_invalido": dato_invalido}
    descartadas = sin_columna + dato_invalido
    if leidas and not out:
        log.error("[sucursales] %s: se leyeron %d filas y NO cargó ninguna "
                  "(%d sin columna, %d con dato inválido) — el filtro geográfico "
                  "queda vacío", p, leidas, sin_columna, dato_invalido)
    elif descartadas:
        log.error("[sucursales] %s: %d de %d filas descartadas "
                  "(%d sin columna, %d con dato inválido)",
                  p, descartadas, leidas, sin_columna, dato_invalido)
    log.info("[sucursales] %d cargadas de %s", len(out), p)
    return tuple(out)


def cercanas(lat: float, lon: float, radio_km: float = 5.0,
             ruta: Optional[str] = None) -> list[tuple[Sucursal, float]]:
    """Sucursales dentro del radio, ordenadas de más cerca a más lejos."""
    out = []
    for s in cargar(ruta):
        d = haversine_km(lat, lon, s.lat, s.lon)
        if d <= radio_km:
            out.append((s, d))
    out.sort(key=lambda x: x[1])
    return out


def cadenas_cerca(lat: float, lon: float, radio_km: float = 5.0,
                  ruta: Optional[str] = None) -> dict[str, float]:
    """
    {cadena: km a su sucursal más cercana}.

    Es lo que consume el adaptador de precios: le dice a qué cadenas preguntarles
    y a cuáles no. En Junín eso son 3 requests en vez de 7.
    """
    out: dict[str, float] = {}
    for s, d in cercanas(lat, lon, radio_km, ruta):
        if s.cadena not in out or d < out[s.cadena]:
            out[s.cadena] = round(d, 2)
    return dict(sorted(out.items(), key=lambda kv: kv[1]))


def resumen(ruta: Optional[str] = None) -> dict:
    """Para exponer en /api/status: qué catálogo se está usando."""
    subs = cargar(ruta)
    por_cadena: dict[str, int] = {}
    for s in subs:
        por_cadena[s.cadena] = por_cadena.get(s.cadena, 0) + 1
    p = Path(ruta) if ruta else RUTA_CSV
    st = _stats(p)
    return {
        "total": len(subs),
        "por_cadena": dict(sorted(por_cadena.items(), key=lambda kv: -kv[1])),
        "archivo": str(p),
        "existe": p.exists(),
        "filas_descartadas": st["sin_columna"] + st["dato_invalido"],
        "problema": _problema(p, len(subs), st),
    }
