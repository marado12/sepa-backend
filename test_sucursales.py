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


def _csv_con_encabezados(encabezados: list) -> str:
    """Mismo contenido que _csv_temp, pero con otros nombres de columna."""
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                    newline="", encoding="utf-8")
    w = csv.writer(f)
    w.writerow(encabezados)
    w.writerows(FILAS)
    f.close()
    suc.cargar.cache_clear()
    return f.name


def test_encabezados_cambiados_no_se_ven_igual_que_un_catalogo_vacio():
    """
    Hallazgo 5. Si el CSV se regenera con otros encabezados, TODAS las filas
    fallan con KeyError, `cargar()` devuelve vacío y el log dice "0 cargadas" en
    nivel INFO. Río abajo el usuario recibe "no hay cadenas cerca, probá aumentar
    el radio" — y por más que lo aumente nunca va a funcionar, porque el radio
    nunca fue el problema.

    El descarte por fila es deliberado (ver el test de arriba). Lo que no puede
    pasar es que nadie cuente cuántas se descartaron.
    """
    ruta = _csv_con_encabezados(["chain", "flag", "prov", "latitude", "longitude",
                                 "cid", "sid"])
    r = suc.resumen(ruta)

    assert r["total"] == 0
    assert r["filas_descartadas"] == len(FILAS), \
        "las filas descartadas tienen que contarse, no desaparecer"
    assert r["problema"], "un catálogo que no cargó ninguna fila tiene que decirlo"
    assert "encabezado" in r["problema"].lower()


def test_un_catalogo_sano_no_reporta_problema():
    """El contador no puede ensuciar el caso normal."""
    r = suc.resumen(_csv_temp())
    assert r["total"] == len(FILAS)
    assert r["filas_descartadas"] == 0
    assert r["problema"] is None


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
