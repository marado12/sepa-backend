"""
Tests de la capa de fuentes. Corren SIN RED (igual que test_resiliencia.py).

Lo que se verifica no es "trae precios" sino la propiedad que motiva el módulo:
que una cadena caída no arrastre a las demás.

    python -m pytest test_fuentes.py -v
    python test_fuentes.py            (sin pytest instalado)
"""

import json
from unittest.mock import patch

from fuentes import (FuenteCompuesta, FuenteCoto, FuenteVTEX, Oferta, Resultado)


# ── Fixtures: forma real verificada contra la API el 09/09/2026 ──
PROD_VTEX_CON_OFERTA = {
    "productId": "721950",
    "productName": "Aceite de girasol Cocinero 1.5 l",
    "brand": "Cocinero",
    "items": [{
        "ean": "7790070410016",
        "sellers": [{"commertialOffer": {
            "Price": 4890.0,
            "ListPrice": 5990.0,
            "PriceWithoutDiscount": 5990.0,
            "AvailableQuantity": 100,
            "IsAvailable": True,
            "Teasers": [{"name": "Tarjeta Carrefour 15%",
                         "bins": [507858, 858110]}],
        }}],
    }],
}

PROD_VTEX_SIN_OFERTA = {
    "productName": "Papas fritas Lays clásicas 85 g.",
    "brand": "Lays",
    "items": [{"ean": "7790310985458", "sellers": [{"commertialOffer": {
        "Price": 3844.62, "ListPrice": 3844.62,
        "AvailableQuantity": 100, "IsAvailable": True, "Teasers": [],
    }}]}],
}

# Casos borde que rompen adaptadores en producción
PROD_SIN_ITEMS    = {"productName": "Fantasma", "items": []}
PROD_SIN_SELLERS  = {"productName": "Fantasma 2", "items": [{"ean": "1"}]}
PROD_PRECIO_CERO  = {"productName": "Agotado", "items": [{"ean": "2", "sellers": [
    {"commertialOffer": {"Price": 0, "ListPrice": 0}}]}]}


class _RespFalsa:
    def __init__(self, data, status=200, headers=None):
        self._data, self.status_code = data, status
        self.headers = headers or {}
    def json(self): return self._data
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _ok(*productos):
    return lambda *a, **k: _RespFalsa(list(productos))


# ─────────────────────────────────────────────────────────────
#  Parseo
# ─────────────────────────────────────────────────────────────

def test_parsea_oferta_completa():
    of = FuenteVTEX._a_oferta("Carrefour", PROD_VTEX_CON_OFERTA)
    assert of is not None
    assert of.cadena == "Carrefour"
    assert of.precio == 4890.0
    assert of.precio_lista == 5990.0
    assert of.ean == "7790070410016"
    assert of.marca == "Cocinero"
    assert of.promos == ["Tarjeta Carrefour 15%"]
    assert of.origen == "vtex"


def test_detecta_oferta_y_calcula_descuento():
    of = FuenteVTEX._a_oferta("Carrefour", PROD_VTEX_CON_OFERTA)
    assert of.tiene_oferta is True
    assert of.descuento_pct == 18.4      # 1 - 4890/5990


def test_precio_igual_a_lista_no_es_oferta():
    of = FuenteVTEX._a_oferta("Carrefour", PROD_VTEX_SIN_OFERTA)
    assert of.tiene_oferta is False
    assert of.descuento_pct is None


def test_productos_malformados_devuelven_none_sin_explotar():
    for p in (PROD_SIN_ITEMS, PROD_SIN_SELLERS, PROD_PRECIO_CERO, {}):
        assert FuenteVTEX._a_oferta("X", p) is None


# ─────────────────────────────────────────────────────────────
#  Aislamiento de fallos — el motivo del módulo
# ─────────────────────────────────────────────────────────────

def test_una_cadena_caida_no_tumba_las_demas():
    """Carrefour timeoutea; las otras cinco tienen que responder igual."""
    def get_falso(url, **kw):
        if "carrefour" in url:
            raise TimeoutError("connect timeout")
        return _RespFalsa([PROD_VTEX_CON_OFERTA])

    with patch("fuentes.requests.get", side_effect=get_falso):
        r = FuenteVTEX(max_reintentos=1).buscar("aceite")

    assert "Carrefour" in r.fallidas
    assert len(r.ofertas) == 5                    # las otras cinco
    assert r.parcial is True
    assert r.cobertura_pct == 83.3                # 5 de 6
    assert "Carrefour" in r.aviso


def test_todas_caidas_devuelve_resultado_vacio_no_excepcion():
    with patch("fuentes.requests.get", side_effect=ConnectionError("sin red")):
        r = FuenteVTEX(max_reintentos=1).buscar("aceite")
    assert r.ofertas == []
    assert len(r.fallidas) == 6
    assert r.cobertura_pct == 0.0
    assert r.parcial is True


def test_sin_fallos_no_hay_aviso():
    with patch("fuentes.requests.get", side_effect=_ok(PROD_VTEX_CON_OFERTA)):
        r = FuenteVTEX().buscar("aceite")
    assert r.parcial is False
    assert r.aviso is None
    assert r.cobertura_pct == 100.0


def test_respeta_429_y_lo_reporta():
    con_429 = _RespFalsa([], status=429, headers={"Retry-After": "1"})
    with patch("fuentes.requests.get", return_value=con_429), \
         patch("fuentes.time.sleep"):                      # no dormir en tests
        r = FuenteVTEX(max_reintentos=2).buscar("aceite", cadenas=["Jumbo"])
    assert "Jumbo" in r.fallidas
    assert "429" in r.fallidas["Jumbo"]


def test_json_no_lista_se_reporta_como_fallo():
    with patch("fuentes.requests.get", return_value=_RespFalsa({"error": "boom"})):
        r = FuenteVTEX(max_reintentos=1).buscar("aceite", cadenas=["Día"])
    assert "Día" in r.fallidas


# ─────────────────────────────────────────────────────────────
#  Filtro de cadenas — lo que ahorra requests
# ─────────────────────────────────────────────────────────────

def test_solo_consulta_las_cadenas_pedidas():
    """El filtro geográfico manda 3 cadenas -> se hacen 3 requests, no 6."""
    llamadas = []
    def espia(url, **kw):
        llamadas.append(url)
        return _RespFalsa([PROD_VTEX_CON_OFERTA])

    with patch("fuentes.requests.get", side_effect=espia):
        r = FuenteVTEX().buscar("aceite", cadenas=["Carrefour", "Día", "Chango Más"])

    assert len(llamadas) == 3
    assert r.consultadas == ["Carrefour", "Día", "Chango Más"]


def test_cadena_desconocida_se_ignora():
    with patch("fuentes.requests.get", side_effect=_ok(PROD_VTEX_CON_OFERTA)):
        r = FuenteVTEX().buscar("aceite", cadenas=["Carrefour", "Walmart"])
    assert r.consultadas == ["Carrefour"]


# ─────────────────────────────────────────────────────────────
#  Coto y composición
# ─────────────────────────────────────────────────────────────

def test_coto_encuentra_precios_anidados():
    """El parseo busca en profundidad, no asume una ruta fija."""
    payload = {"contents": [{"Main": [{"records": [
        {"sku": {"description": "Aceite Natura 900ml",
                 "activePrice": 3990.0, "listPrice": 4500.0}}
    ]}]}]}
    with patch("fuentes.requests.get", return_value=_RespFalsa(payload)):
        r = FuenteCoto().buscar("aceite")
    assert len(r.ofertas) == 1
    assert r.ofertas[0].precio == 3990.0
    assert r.ofertas[0].cadena == "Coto"
    assert r.ofertas[0].tiene_oferta is True


def test_compuesta_suma_fuentes_y_acumula_fallos():
    def get_falso(url, **kw):
        if "coto" in url:
            raise TimeoutError("coto caído")
        return _RespFalsa([PROD_VTEX_CON_OFERTA])

    with patch("fuentes.requests.get", side_effect=get_falso):
        r = FuenteCompuesta(FuenteVTEX(max_reintentos=1),
                            FuenteCoto()).buscar("aceite")

    assert len(r.ofertas) == 6            # 6 VTEX, Coto caído
    assert "Coto" in r.fallidas
    assert len(r.consultadas) == 7        # se intentaron las 7
    assert r.parcial is True


def test_compuesta_sobrevive_a_fuente_que_explota():
    """Si una Fuente entera levanta excepción, la otra sigue."""
    class FuenteRota:
        nombre = "rota"
        def cadenas_soportadas(self): return ["X"]
        def buscar(self, q, c=None): raise RuntimeError("bug interno")

    with patch("fuentes.requests.get", side_effect=_ok(PROD_VTEX_CON_OFERTA)):
        r = FuenteCompuesta(FuenteRota(), FuenteVTEX()).buscar("aceite")
    assert len(r.ofertas) == 6


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {nombre}")
            except Exception as e:
                fallos += 1
                print(f"  FAIL  {nombre}: {type(e).__name__}: {e}")
    print(f"\n{'TODO OK' if not fallos else str(fallos) + ' FALLOS'}")
    raise SystemExit(1 if fallos else 0)
