"""
Tests de `scripts/snapshot_catalogo.py`. Sin red, como el resto de la suite.

Cubren lo que se puede romper en silencio: que se recorra el nivel más profundo del
árbol (para no chocar contra el techo de 2500 por consulta), que la paginación
respete la ventana de 50 y corte con el total del header `resources`, y que no se
dupliquen productos que aparecen en más de una categoría.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import snapshot_catalogo as S  # noqa: E402

ARBOL = [
    {"id": 161, "name": "Almacén", "children": [
        {"id": 176, "name": "Harinas", "children": [
            {"id": 177, "name": "Harina de trigo", "children": []}]},
        {"id": 168, "name": "Pastas secas", "children": []}]},
    {"id": 3, "name": "Electro y tecnología", "children": [
        {"id": 4, "name": "Televisores", "children": []}]},
]


def _producto(i: int) -> dict:
    return {"productName": f"Producto {i}", "brand": "Marca",
            "items": [{"ean": f"779{i:010d}", "sellers": [{"commertialOffer": {
                "Price": 100 + i, "ListPrice": 100 + i, "IsAvailable": True,
                "AvailableQuantity": 10, "Teasers": []}}]}]}


class _Resp:
    def __init__(self, data, total=0):
        self.status_code = 200
        self._data = data
        self.headers = {"resources": f"0-49/{total}"}

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _falso(total: int, registro: list):
    def get(url, params=None, timeout=None, **kw):
        if "category/tree" in url:
            return _Resp(ARBOL)
        desde = params["_from"]
        registro.append((params["fq"], desde, params["_to"]))
        return _Resp([_producto(i) for i in range(desde, min(desde + 50, total))], total)
    return get


def test_recorre_la_hoja_mas_profunda_del_arbol():
    """
    Consultar "/161/" traería 6373 productos y el techo de paginación es 2500.
    Por eso se baja por la categoría más específica disponible.
    """
    hojas = [h for c in ARBOL for h in S._hojas(c)]
    assert ("/161/176/177", "Harina de trigo") in hojas
    assert ("/161/168", "Pastas secas") in hojas
    assert ("/161", "Almacén") not in hojas       # nunca el padre si tiene hijos


def test_saltea_las_categorias_que_no_son_de_supermercado():
    quedan = [c["name"] for c in ARBOL
              if c["name"].strip().lower() not in S.NO_SUPERMERCADO]
    assert quedan == ["Almacén"]


def test_pagina_de_a_50_y_corta_con_el_total_del_header():
    registro = []
    with patch.object(S, "PAUSA", 0), \
         patch.object(S.requests.Session, "get",
                      lambda self, *a, **k: _falso(120, registro)(*a, **k)):
        filas = S.bajar_vtex("Carrefour", "https://x", S.Contador(), todo=False)

    ventanas = [(d, h) for _, d, h in registro]
    assert (0, 49) in ventanas and (50, 99) in ventanas and (100, 149) in ventanas
    assert all(h - d + 1 <= S.VENTANA for d, h in ventanas)
    assert max(d for d, _ in ventanas) < S.TOPE_CONSULTA
    assert len(filas) == 120


def test_no_duplica_productos_que_estan_en_dos_categorias():
    """El mismo EAN en Harinas y en Pastas secas entra una sola vez."""
    registro = []
    with patch.object(S, "PAUSA", 0), \
         patch.object(S.requests.Session, "get",
                      lambda self, *a, **k: _falso(30, registro)(*a, **k)):
        filas = S.bajar_vtex("Carrefour", "https://x", S.Contador(), todo=False)

    categorias_visitadas = {fq for fq, _, _ in registro}
    assert len(categorias_visitadas) == 2          # se consultaron las dos
    assert len(filas) == 30                        # pero los productos no se duplican
    assert len({f.ean for f in filas}) == 30


def test_una_categoria_que_falla_no_tumba_la_cadena():
    llamadas = {"n": 0}

    def get(url, params=None, timeout=None, **kw):
        if "category/tree" in url:
            return _Resp(ARBOL)
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise TimeoutError("categoría lenta")
        return _Resp([_producto(1)], 1)

    with patch.object(S, "PAUSA", 0), \
         patch.object(S.requests.Session, "get", lambda self, *a, **k: get(*a, **k)):
        cont = S.Contador()
        filas = S.bajar_vtex("Carrefour", "https://x", cont, todo=False)

    assert cont.errores == 1
    assert len(filas) == 1        # la segunda categoría se bajó igual
