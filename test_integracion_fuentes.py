"""
Tests de integración de /api/comparar con la capa de fuentes. SIN RED.

Verifican la propiedad que motiva todo el cambio: que la app siga respondiendo
cuando el SEPA no está. Y la contraria, igual de importante: que con
FUENTE_PRECIOS=sepa el comportamiento sea el de antes.
"""
import importlib
import os
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

from fuentes import Oferta, Resultado

JUNIN = {"lat": -34.58731, "lon": -60.93391, "radio_km": 5.0}
CANASTA = [{"nombre": "aceite girasol", "cantidad": 1.5, "unidad": "l"}]


def cargar_main(modo: str):
    """Recarga main.py con FUENTE_PRECIOS en el modo pedido."""
    os.environ["FUENTE_PRECIOS"] = modo
    for m in ("main",):
        if m in sys.modules:
            del sys.modules[m]
    import main
    return importlib.reload(main) if "main" in sys.modules else main


def ofertas_falsas(cadenas):
    return [Oferta(cadena=c, producto="Aceite de girasol Cocinero 1.5 L",
                   precio=4890.0 + i * 100, precio_lista=5990.0, origen="vtex",
                   promos=["Tarjeta 15%"], ean="779007")
            for i, c in enumerate(cadenas)]


class FuenteFalsa:
    nombre = "falsa"
    def __init__(self, caidas=None): self.caidas = caidas or {}
    def cadenas_soportadas(self):
        return ["Carrefour", "Chango Más", "Coto", "Día", "Disco", "Jumbo", "Vea"]
    def buscar(self, query, cadenas=None):
        cs = [c for c in (cadenas or self.cadenas_soportadas()) if c not in self.caidas]
        return Resultado(ofertas=ofertas_falsas(cs), fallidas=dict(self.caidas),
                         consultadas=list(cadenas or self.cadenas_soportadas()))


def _pedir(main, **extra):
    cuerpo = {**JUNIN, "canasta": CANASTA, **extra}
    return TestClient(main.app).post("/api/comparar", json=cuerpo)


# ── El caso que motivó todo ──────────────────────────────────────
def test_sepa_caido_la_app_sigue_respondiendo():
    """SEPA inalcanzable + modo auto -> responde con precios online."""
    main = cargar_main("auto")
    with patch.object(main, "_obtener_datos",
                      side_effect=ConnectionError("TCP timeout a datos.produccion.gob.ar")), \
         patch.object(main, "_get_fuente_online", return_value=FuenteFalsa()):
        r = _pedir(main)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["origen_precios"] == "online"
    assert "sepa_error" in d["fuente"]
    assert d["ranking"], "sin ranking"
    assert d["optimo"]["total_optimo"] > 0
    assert d["optimo"]["items"][0]["ok"] is True
    assert d["optimo"]["items"][0]["cadena"] in ("Día", "Vea", "Chango Más")


def test_solo_consulta_las_cadenas_cercanas():
    """En Junín el catálogo da Día, Vea y Chango Más: no se pregunta a las otras."""
    main = cargar_main("online")
    fuente = FuenteFalsa()
    vistas = []
    orig = fuente.buscar
    fuente.buscar = lambda q, c=None: (vistas.append(list(c or [])), orig(q, c))[1]
    with patch.object(main, "_get_fuente_online", return_value=fuente):
        r = _pedir(main)
    assert r.status_code == 200, r.text
    assert vistas and set(vistas[0]) == {"Día", "Vea", "Chango Más"}
    assert "Carrefour" not in vistas[0]      # el más cercano está lejos de Junín


def test_jumbo_y_disco_quedan_fuera_pero_se_reportan():
    """No están en el catálogo: no se recomiendan a ciegas, pero se hacen visibles."""
    main = cargar_main("online")
    with patch.object(main, "_get_fuente_online", return_value=FuenteFalsa()):
        r = _pedir(main)
    geo = r.json()["fuente"]["geo"]
    assert set(geo["cadenas_sin_geo"]) == {"Jumbo", "Disco"}
    assert geo["cadenas_sin_geo_incluidas"] is False
    assert geo["filtro_geografico"] is True


def test_cadena_caida_da_resultado_parcial_no_error():
    main = cargar_main("online")
    with patch.object(main, "_get_fuente_online",
                      return_value=FuenteFalsa(caidas={"Día": "TimeoutError"})):
        r = _pedir(main)
    assert r.status_code == 200
    d = r.json()
    assert "Día" in d["fuente"]["cadenas_fallidas"]
    assert d["fuente"]["cobertura_cadenas_pct"] < 100
    assert "Día" in d["aviso_datos"]
    assert d["ranking"], "las otras cadenas tienen que seguir apareciendo"


def test_promos_y_descuento_llegan_a_la_respuesta():
    """Lo que el SEPA no podía dar: la oferta web y la promo bancaria."""
    main = cargar_main("online")
    with patch.object(main, "_get_fuente_online", return_value=FuenteFalsa()):
        r = _pedir(main)
    assert r.status_code == 200
    assert r.json()["fuente"]["detalle_match"]["aceite girasol"]["cadenas_con_precio"]


# ── No romper lo que andaba ──────────────────────────────────────
def test_modo_sepa_no_toca_la_fuente_online():
    """Con FUENTE_PRECIOS=sepa el fallback no existe: falla como antes."""
    main = cargar_main("sepa")
    llamada = {"online": False}
    def marcar():
        llamada["online"] = True
        return FuenteFalsa()
    with patch.object(main, "_obtener_datos", side_effect=ConnectionError("SEPA caído")), \
         patch.object(main, "_get_fuente_online", side_effect=marcar):
        r = _pedir(main)
    assert r.status_code == 503
    assert llamada["online"] is False, "modo sepa NO debe consultar online"


def test_modo_sepa_usa_buscar_precios_de_siempre():
    main = cargar_main("sepa")
    precios_sepa = {("Día", "aceite girasol"): {"precio_min": 5250.0, "precio_por_100u": None}}
    with patch.object(main, "_obtener_datos", return_value=(None, "/fake.parquet")), \
         patch.object(main, "_buscar_precios", return_value=precios_sepa):
        r = _pedir(main)
    assert r.status_code == 200
    d = r.json()
    assert d["origen_precios"] == "sepa"
    assert d["ranking"][0]["cadena"] == "Día"


def test_auto_prefiere_sepa_cuando_hay_datos():
    main = cargar_main("auto")
    precios_sepa = {("Día", "aceite girasol"): {"precio_min": 5250.0, "precio_por_100u": None}}
    with patch.object(main, "_obtener_datos", return_value=(None, "/fake.parquet")), \
         patch.object(main, "_buscar_precios", return_value=precios_sepa), \
         patch.object(main, "_get_fuente_online", side_effect=AssertionError("no debía llamarse")):
        r = _pedir(main)
    assert r.status_code == 200
    assert r.json()["origen_precios"] == "sepa"


def test_status_expone_la_configuracion():
    main = cargar_main("auto")
    d = TestClient(main.app).get("/api/status").json()
    assert d["fuente_precios"] == "auto"
    assert "Jumbo" in d["cadenas_online"]
    assert d["catalogo_sucursales"]["total"] > 1000
    # claves viejas intactas: el banner del frontend las usa
    for k in ("cache", "listo", "en_progreso", "aviso_datos"):
        assert k in d, f"se perdió la clave {k}"


def test_sin_precios_devuelve_404_con_motivo():
    main = cargar_main("online")
    class Vacia:
        nombre = "vacia"
        def cadenas_soportadas(self): return ["Día", "Vea", "Chango Más"]
        def buscar(self, q, c=None): return Resultado(consultadas=list(c or []))
    with patch.object(main, "_get_fuente_online", return_value=Vacia()):
        r = _pedir(main)
    assert r.status_code == 404
    assert "online" in r.json()["detail"]


# ── Promos del sitio (roadmap 4.1) ───────────────────────────────
class FuenteConPromos:
    """Ofertas con Teasers, como los devuelve VTEX."""
    nombre = "promos"
    def __init__(self, teasers): self.teasers = teasers
    def cadenas_soportadas(self):
        return ["Carrefour", "Chango Más", "Coto", "Día", "Disco", "Jumbo", "Vea"]
    def buscar(self, query, cadenas=None):
        cs = list(cadenas or self.cadenas_soportadas())
        return Resultado(
            ofertas=[Oferta(cadena=c, producto="Aceite de girasol Cocinero 1.5 L",
                            precio=10000.0, precio_lista=10000.0, origen="vtex",
                            promos=self.teasers.get(c, []))
                     for c in cs],
            consultadas=cs)


def test_promo_del_sitio_le_gana_a_la_hardcodeada():
    """
    Regla de precedencia: el sitio manda. Junín es miércoles-independiente, así
    que forzamos día 2 (miércoles), cuando PROMOS_DEFAULT tiene BNA 30% para
    Chango Más. Si el sitio dice 10%, gana el 10%: es el dato vigente.
    """
    main = cargar_main("online")
    fuente = FuenteConPromos({"Chango Más": ["10% Banco Galicia"]})
    with patch.object(main, "_get_fuente_online", return_value=fuente):
        r = _pedir(main, dia=2, bancos_seleccionados=[
            {"banco_id": "Banco Nación", "medios": ["credito"]}])
    assert r.status_code == 200, r.text
    fila = next(x for x in r.json()["ranking"] if x["cadena"] == "Chango Más")
    assert fila["origen_promo"] == "sitio"
    assert fila["mejor_promo"] == "10% Banco Galicia"
    assert fila["banco_promo"] == "Banco Galicia"
    # subtotal = 1 botella de 1.5 L a 10000 -> 10% = 1000.
    # Con la promo manual (BNA 30% miércoles) habrían sido 3000.
    # ✏️ Tarea 26 (18/09): decía "10000 * cantidad 1.5 = 15000 -> 1500". Cobraba una
    # botella y media para 1,5 L pedido con botellas de 1,5 L; ahora se cobran los
    # envases enteros que cubren lo pedido. La precedencia de promos no cambió.
    assert fila["reintegro"] == 1000.0


def test_cadena_sin_promo_del_sitio_usa_la_manual():
    """El fallback sigue vivo para las cadenas de las que el sitio no dice nada."""
    main = cargar_main("online")
    fuente = FuenteConPromos({"Chango Más": ["10% Banco Galicia"]})   # Día sin teaser
    with patch.object(main, "_get_fuente_online", return_value=fuente):
        r = _pedir(main, dia=4, bancos_seleccionados=[
            {"banco_id": "Banco Nación", "medios": ["credito"]}])
    fila = next(x for x in r.json()["ranking"] if x["cadena"] == "Día")
    assert fila["origen_promo"] == "manual"
    assert fila["reintegro"] > 0                # BNA 20% viernes Día


def test_segunda_unidad_no_infla_el_reintegro():
    """La trampa: '2da unidad 70%' no puede aplicarse al total."""
    main = cargar_main("online")
    fuente = FuenteConPromos({"Día": ["2da unidad 70%"]})
    with patch.object(main, "_get_fuente_online", return_value=fuente):
        r = _pedir(main, dia=0)                 # lunes: sin promo manual para Día
    fila = next(x for x in r.json()["ranking"] if x["cadena"] == "Día")
    assert fila["mejor_promo"] == "2da unidad 70%"
    assert fila["reintegro"] == 0.0, "70% del total serían $7.000 de ahorro falso"
    assert fila["total_final"] == fila["total_base"]


def test_promos_sitio_se_exponen_en_la_respuesta():
    main = cargar_main("online")
    fuente = FuenteConPromos({"Día": ["Tarjeta Carrefour 15%"]})
    with patch.object(main, "_get_fuente_online", return_value=fuente):
        r = _pedir(main)
    ps = r.json()["promos_sitio"]
    assert "Día" in ps
    assert ps["Día"][0]["descuento_pct"] == 15.0
    assert ps["Día"][0]["aplicable_al_total"] is True


def test_con_sepa_las_promos_del_sitio_no_existen():
    """Sin Teasers, el comportamiento de promos es exactamente el de antes."""
    main = cargar_main("sepa")
    precios = {("Día", "aceite girasol"): {"precio_min": 10000.0, "precio_por_100u": None}}
    with patch.object(main, "_obtener_datos", return_value=(None, "/fake.parquet")), \
         patch.object(main, "_buscar_precios", return_value=precios):
        r = _pedir(main, dia=4, bancos_seleccionados=[
            {"banco_id": "Banco Nación", "medios": ["credito"]}])
    d = r.json()
    assert d["promos_sitio"] == {}
    fila = d["ranking"][0]
    assert fila["origen_promo"] == "manual"
    assert fila["reintegro"] == 3000.0          # BNA 20% viernes Día sobre 15000



# ── El SEPA dejó de ser requisito del flujo normal ───────────────
def test_auto_no_descarga_el_sepa_dentro_del_request():
    """
    Un request de usuario NUNCA dispara la descarga del ZIP.

    El ZIP mide 330 MB y `_descargar_y_procesar` usa timeout de lectura 600s:
    en `auto` un comparar podía quedarse hasta 10 minutos esperando al SEPA
    antes de caer a online, y en Render no entra en los 512 MB. Ahora en `auto`
    el SEPA se usa solo si YA está cargado; bajarlo es tarea de POST /refresh.

    El contador se chequea aparte de la excepción a propósito: `_resolver_precios`
    atrapa `Exception`, así que un `raise` acá se lo tragaría y el test daría
    verde sin haber probado nada.
    """
    import tempfile
    os.environ["CACHE_DIR"] = tempfile.mkdtemp(prefix="sepa_sin_cache_")
    main = cargar_main("auto")
    main._cache.clear()

    llamadas = {"n": 0}
    def contar(dia):
        llamadas["n"] += 1
        raise ConnectionError("no debería llegar acá")

    with patch.object(main, "_descargar_y_procesar", side_effect=contar), \
         patch.object(main, "_get_fuente_online", return_value=FuenteFalsa()):
        r = _pedir(main)

    assert llamadas["n"] == 0, "el request de un usuario disparó la descarga del SEPA"
    assert r.status_code == 200, r.text
    assert r.json()["origen_precios"] == "online"


def test_el_error_del_sepa_no_llega_con_la_url_interna():
    """
    El texto de `requests` trae el dataset completo ("...for url: https://datos.
    produccion.gob.ar/dataset/6f47.../resource/d076...") y viajaba tal cual en
    `fuente.sepa_error`. No le dice nada al usuario y expone el detalle de una
    fuente que ya no es la principal: al log sí, al cliente no.
    """
    main = cargar_main("auto")
    boom = ConnectionError(
        "HTTPSConnectionPool(host='datos.produccion.gob.ar', port=443): Max retries "
        "exceeded with url: /dataset/6f47ec76/resource/0a9069a9/download/sepa_lunes.zip")
    with patch.object(main, "_obtener_datos", side_effect=boom), \
         patch.object(main, "_get_fuente_online", return_value=FuenteFalsa()):
        r = _pedir(main)

    assert r.status_code == 200, r.text
    assert "sepa_error" in r.json()["fuente"]        # el motivo se sigue reportando
    assert "datos.produccion.gob.ar" not in r.text   # pero sin la URL interna
    assert "sepa_lunes.zip" not in r.text


def test_una_cadena_fuera_de_cadenas_keywords_se_reporta():
    """
    Hallazgo 12. `_analizar` y `_canasta_optima` recorren `CADENAS_KEYWORDS`, la
    constante de la ruta SEPA, para consumir precios que hoy vienen de la ruta
    online. Un precio de una cadena que no esté en esa tabla se consulta, se
    matchea, cuenta en `n_precios`… y desaparece del ranking sin una sola línea
    de log.

    Acá Walmart es además el más barato de los dos: exactamente el caso en que
    el silencio le cuesta plata al usuario.
    """
    main = cargar_main("sepa")
    precios = {
        ("Día", "aceite girasol"):     {"precio_min": 5250.0, "precio_por_100u": None},
        ("Walmart", "aceite girasol"): {"precio_min": 10.0,   "precio_por_100u": None},
    }
    with patch.object(main, "_obtener_datos", return_value=(None, "/fake.parquet")), \
         patch.object(main, "_buscar_precios", return_value=precios):
        r = _pedir(main)

    assert r.status_code == 200, r.text
    assert r.json()["fuente"].get("cadenas_ignoradas") == ["Walmart"], \
        "una cadena con precio que el optimizador ignora tiene que reportarse"


# ── Fichas: el reemplazo del ranking (Bloque A paso 3) ───────────
def test_la_respuesta_trae_fichas_y_el_alias_ranking():
    """
    Mientras dure la ventana de deprecación los dos conviven. `ranking` no se
    puede sacar todavía: `App.jsx:205` lo lee sin optional chaining y el frontend
    reventaría entre el deploy del backend y el de la Tarea 14.
    """
    main = cargar_main("online")
    with patch.object(main, "_get_fuente_online", return_value=FuenteFalsa()):
        r = _pedir(main)
    d = r.json()
    assert r.status_code == 200, r.text
    assert "fichas" in d and d["fichas"]
    assert "ranking" in d, "el alias no puede desaparecer todavía"
    assert "sin_ninguna_cadena" in d and "n_pedidos" in d


def test_fichas_y_ranking_no_se_contradicen_en_la_plata():
    """
    Las dos salidas vienen del mismo `_analizar`, así que no pueden diferir en
    pesos. Si divergen, el alias está mal hecho y el usuario vería un número
    distinto según qué versión del frontend tenga.
    """
    main = cargar_main("online")
    with patch.object(main, "_get_fuente_online", return_value=FuenteFalsa()):
        d = _pedir(main).json()

    por_ranking = {f["cadena"]: f for f in d["ranking"]}
    for ficha in d["fichas"]:
        if ficha["cadena"] not in por_ranking:
            assert ficha["n_disponibles"] == 0, ficha["cadena"]
            continue
        viejo = por_ranking[ficha["cadena"]]
        assert ficha["total_final"] == viejo["total_final"], ficha["cadena"]
        assert ficha["reintegro"] == viejo["reintegro"], ficha["cadena"]
        assert ficha["n_disponibles"] == viejo["n_encontrados"], ficha["cadena"]


def test_las_fichas_se_ordenan_por_cobertura_no_por_precio():
    """No hay podio: el orden es cobertura desc y después alfabético."""
    main = cargar_main("online")
    with patch.object(main, "_get_fuente_online", return_value=FuenteFalsa()):
        fichas = _pedir(main).json()["fichas"]
    claves = [(-f["n_disponibles"], f["cadena"]) for f in fichas]
    assert claves == sorted(claves), [f["cadena"] for f in fichas]


def test_toda_cadena_online_esta_en_cadenas_keywords():
    """
    Guardia del hallazgo 12, y la parte que de verdad protege a futuro.

    Hoy pasa: los dos conjuntos coinciden en 7 cadenas. Está para fallar el día
    que alguien sume una cadena a la fuente online sin agregarla a
    CADENAS_KEYWORDS — que es cuando sus precios empezarían a descartarse.
    """
    import fuentes
    main = cargar_main("auto")
    soportadas = set(fuentes.VTEX_BASES) | {"Coto"}
    faltan = soportadas - set(main.CADENAS_KEYWORDS)
    assert not faltan, (
        f"{sorted(faltan)} se consulta(n) online pero no está(n) en CADENAS_KEYWORDS: "
        f"sus precios se descartarían del ranking y del óptimo")


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