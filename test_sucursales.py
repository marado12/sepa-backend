"""Tests del catálogo estático. Sin red, sin parquet."""
import csv
import tempfile
from pathlib import Path

import sucursales as suc

JUNIN = (-34.58731, -60.93391)   # ubicación de prueba de CONTEXTO.md

FILAS = [
    # cadena, bandera, provincia, lat, lon, id_comercio, id_sucursal
    ("Día",        "Supermercados DIA", "AR-B", -34.5842, -60.9301, "9",  "1"),
    ("Vea",        "Vea",               "AR-B", -34.5895, -60.9412, "15", "2"),
    ("Chango Más", "SuperChangomas",    "AR-B", -34.5951, -60.9488, "12", "3"),
    ("Coto",       "Coto CICSA",        "AR-C", -34.6037, -58.3816, "11", "4"),  # CABA, lejos
]


def _csv_temp(filas=FILAS) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                    newline="", encoding="utf-8")
    w = csv.writer(f)
    w.writerow(["cadena", "bandera", "provincia", "lat", "lon",
                "id_comercio", "id_sucursal"])
    w.writerows(filas)
    f.close()
    suc.cargar.cache_clear()
    return f.name


def test_haversine_contra_distancia_conocida():
    # Obelisco -> Junín, ~235 km en línea recta
    d = suc.haversine_km(-34.6037, -58.3816, *JUNIN)
    assert 225 < d < 245, d


def test_haversine_mismo_punto_es_cero():
    assert suc.haversine_km(*JUNIN, *JUNIN) == 0.0


def test_cadenas_cerca_filtra_por_radio():
    ruta = _csv_temp()
    r = suc.cadenas_cerca(*JUNIN, radio_km=5, ruta=ruta)
    assert set(r) == {"Día", "Vea", "Chango Más"}
    assert "Coto" not in r                       # CABA queda afuera
    assert r["Día"] < r["Vea"] < r["Chango Más"]  # ordenado por distancia


def test_radio_grande_incluye_las_lejanas():
    ruta = _csv_temp()
    r = suc.cadenas_cerca(*JUNIN, radio_km=300, ruta=ruta)
    assert "Coto" in r


def test_radio_cero_no_devuelve_nada():
    ruta = _csv_temp()
    assert suc.cadenas_cerca(*JUNIN, radio_km=0, ruta=ruta) == {}


def test_devuelve_la_sucursal_mas_cercana_de_cada_cadena():
    """Con dos Día, gana el más cerca."""
    filas = FILAS + [("Día", "Supermercados DIA", "AR-B", -34.6500, -61.0500, "9", "9")]
    ruta = _csv_temp(filas)
    r = suc.cadenas_cerca(*JUNIN, radio_km=50, ruta=ruta)
    assert r["Día"] < 1.0                        # el de 0.4 km, no el lejano


def test_archivo_inexistente_no_explota():
    """Sin catálogo la app no puede filtrar, pero tampoco puede caerse."""
    suc.cargar.cache_clear()
    assert suc.cargar("/no/existe/sucursales.csv") == ()
    assert suc.cadenas_cerca(*JUNIN, ruta="/no/existe/sucursales.csv") == {}


def test_fila_corrupta_no_invalida_el_catalogo():
    filas = FILAS + [("Rota", "X", "AR-B", "no-es-un-numero", "tampoco", "0", "0")]
    ruta = _csv_temp(filas)
    assert len(suc.cargar(ruta)) == len(FILAS)   # la rota se saltea, el resto entra


def test_resumen_cuenta_por_cadena():
    ruta = _csv_temp()
    r = suc.resumen(ruta)
    assert r["total"] == 4
    assert r["por_cadena"]["Día"] == 1
    assert r["existe"] is True


def test_catalogo_real_tiene_las_cadenas_esperadas():
    """Contra data/sucursales.csv de verdad, si está presente."""
    suc.cargar.cache_clear()
    if not suc.RUTA_CSV.exists():
        return
    r = suc.resumen()
    assert r["total"] > 1000
    for c in ("Día", "Carrefour", "Vea", "Coto", "Chango Más"):
        assert c in r["por_cadena"], f"falta {c}"
    # Junín: el caso que motivó el problema abierto #2
    cerca = suc.cadenas_cerca(*JUNIN, radio_km=5)
    assert "Vea" in cerca, "Vea debería estar a ~1.5 km de Junín"
    assert cerca["Vea"] < 2.0


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn(); print(f"  PASS  {nombre}")
            except Exception as e:
                fallos += 1; print(f"  FAIL  {nombre}: {type(e).__name__}: {e}")
    print(f"\n{'TODO OK' if not fallos else str(fallos)+' FALLOS'}")
    raise SystemExit(1 if fallos else 0)
