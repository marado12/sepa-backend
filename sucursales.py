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
    with p.open(encoding="utf-8", newline="") as f:
        for fila in csv.DictReader(f):
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
            except (KeyError, ValueError, TypeError):
                continue      # una fila corrupta no invalida el catálogo entero
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
    return {
        "total": len(subs),
        "por_cadena": dict(sorted(por_cadena.items(), key=lambda kv: -kv[1])),
        "archivo": str(p),
        "existe": p.exists(),
    }
