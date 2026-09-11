#!/usr/bin/env python3
"""
Genera el catálogo estático de sucursales a partir de un parquet del SEPA.

Las sucursales cambian dos veces por año; los precios, todos los días. Este
script congela la parte lenta para que el filtro geográfico deje de depender
de que el SEPA esté vivo.

    python scripts/generar_sucursales.py ~/.sepa_cache/sepa_dia6_2026-05-03_v9_suc.parquet

Escribe data/sucursales.csv (versionado en el repo: es chico y diffeable, así
que en un PR se ve exactamente qué sucursal se agregó o se fue).
"""
import argparse
import os
import re
import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "data" / "sucursales.csv"

# Umbrales del guard de escritura. Son los mismos que ya valida el CI en
# .github/workflows/tests.yml — si el CI los considera el mínimo aceptable,
# no tiene sentido escribir un archivo que no los cumple.
MIN_FILAS = 1000
CADENAS_ESPERADAS = ("Día", "Carrefour", "Vea", "Coto", "Chango Más")


def verificar_catalogo(out, min_filas: int = MIN_FILAS,
                       esperadas=CADENAS_ESPERADAS) -> list[str]:
    """
    Problemas que impiden escribir el catálogo. Lista vacía = se puede escribir.

    Existe porque este script PISA `data/sucursales.csv`, que es de lo que
    depende todo el "cerca tuyo". Una corrida degenerada —cambió el esquema del
    parquet, se rompió la clasificación de cadenas— dejaba un CSV casi vacío
    encima del bueno y salía con código 0. El usuario terminaba viendo "no hay
    cadenas cerca, probá aumentar el radio", que no tiene nada que ver.
    """
    problemas = []
    if len(out) < min_filas:
        problemas.append(f"quedaron {len(out):,} filas y el mínimo es {min_filas:,}")
    faltan = [c for c in esperadas if c not in set(out["cadena"])]
    if faltan:
        problemas.append(f"faltan cadenas esperadas: {', '.join(faltan)}")
    return problemas


def cargar_clasificador(main_py: Path):
    """
    Reutiliza asignar_cadena() de main.py en vez de duplicar la lógica.
    Si se duplicara, el día que cambie una keyword el catálogo estático y el
    pipeline del SEPA clasificarían distinto y nadie se enteraría.
    """
    src = main_py.read_text(encoding="utf-8")
    ns = {"re": re, "Optional": __import__("typing").Optional}
    for tabla in ("CADENAS_KEYWORDS", "CADENAS_BANDERA_KEYWORDS"):
        m = re.search(rf"^{tabla}\s*=\s*\{{.*?^\}}", src, re.S | re.M)
        if not m:
            sys.exit(f"No encontré {tabla} en {main_py}")
        exec(m.group(0), ns)
    for fn in ("normalizar", "asignar_cadena"):
        m = re.search(rf"^def {fn}\(.*?(?=^def |\Z)", src, re.S | re.M)
        if m:
            exec(m.group(0), ns)
    return ns["asignar_cadena"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("parquet", help="ruta a un *_suc.parquet del SEPA")
    ap.add_argument("--main", default=str(RAIZ / "main.py"))
    args = ap.parse_args()

    asignar_cadena = cargar_clasificador(Path(args.main))
    df = pd.read_parquet(args.parquet)
    print(f"Leídas {len(df):,} sucursales de {os.path.basename(args.parquet)}")

    df["cadena"] = [
        asignar_cadena(r or "", b or "")
        for r, b in zip(df["comercio_razon_social"], df["comercio_bandera_nombre"])
    ]

    out = df[df["cadena"].notna()].copy()
    descartadas = len(df) - len(out)

    # Sin coordenadas no sirve para el filtro geográfico.
    antes = len(out)
    out = out.dropna(subset=["sucursales_latitud", "sucursales_longitud"])
    sin_coords = antes - len(out)

    # Reparar signo invertido antes de descartar. Visto en el snapshot del
    # 2026-05-03: un Carrefour de Buenos Aires con lon=+58.79 en vez de -58.79,
    # que cae en Uzbekistán. Es un error de carga del comercio, no un dato ausente:
    # si el valor absoluto entra en el bounding box, se corrige el signo.
    reparadas = 0
    for col, (lo, hi) in (("sucursales_latitud", (-56, -21)),
                          ("sucursales_longitud", (-74, -53))):
        mal = ~out[col].between(lo, hi)
        arreglable = mal & (-out[col]).between(lo, hi)
        reparadas += int(arreglable.sum())
        out.loc[arreglable, col] = -out.loc[arreglable, col]

    out = out[(out["sucursales_latitud"].between(-56, -21))
              & (out["sucursales_longitud"].between(-74, -53))]   # bounding box de Argentina
    fuera = antes - sin_coords - len(out)

    out = out.rename(columns={
        "sucursales_latitud": "lat",
        "sucursales_longitud": "lon",
        "sucursales_provincia": "provincia",
        "comercio_bandera_nombre": "bandera",
    })[["cadena", "bandera", "provincia", "lat", "lon", "id_comercio", "id_sucursal"]]
    out = out.sort_values(["cadena", "provincia", "lat"]).reset_index(drop=True)

    # El desglose va ANTES de escribir: si el guard aborta, estos números son
    # justamente el diagnóstico de por qué.
    print(f"  descartadas por cadena no reconocida: {descartadas:,}")
    print(f"  descartadas por no reportar coordenadas: {sin_coords:,}")
    print(f"  con signo invertido, REPARADAS: {reparadas:,}")
    print(f"  descartadas por coordenadas fuera de Argentina: {fuera:,}")

    problemas = verificar_catalogo(out)
    if problemas:
        print(f"\n✗ NO se escribió {SALIDA.name}:", file=sys.stderr)
        for p in problemas:
            print(f"    - {p}", file=sys.stderr)
        print(f"  El catálogo anterior quedó intacto. Revisá el parquet de entrada\n"
              f"  y la clasificación de cadenas antes de volver a correr.", file=sys.stderr)
        raise SystemExit(1)

    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(SALIDA, index=False, float_format="%.6f")

    print(f"\nEscritas {len(out):,} sucursales en {SALIDA.relative_to(RAIZ)}")
    print(f"  {SALIDA.stat().st_size/1024:.0f} KB\n")
    print(out["cadena"].value_counts().to_string())


if __name__ == "__main__":
    main()
