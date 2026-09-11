"""
Tests de las promos tomadas del sitio (roadmap 4.1). Sin red.

El riesgo acá no es fallar: es sobreestimar el ahorro. Una promo mal
interpretada le hace elegir mal al usuario y no tiene forma de notarlo.
"""
from promos_sitio import (PromoSitio, a_json, extraer, parsear_teaser,
                          reintegro_para)


# ── Parseo ───────────────────────────────────────────────────────
def test_teaser_real_de_carrefour():
    """El que verifiqué en la API el 09/09/2026."""
    p = parsear_teaser("Tarjeta Carrefour 15%", "Carrefour")
    assert p.descuento_pct == 15.0
    assert p.tipo == "descuento"
    assert p.aplicable_al_total is True


def test_detecta_el_banco():
    assert parsear_teaser("20% Banco Nación", "Coto").banco == "Banco Nación"
    assert parsear_teaser("25% con Galicia", "Día").banco == "Banco Galicia"
    assert parsear_teaser("15% MODO", "Vea").banco == "MODO"
    assert parsear_teaser("10% Mercado Pago", "Día").banco == "Mercado Pago"


def test_sin_banco_reconocible_queda_none():
    assert parsear_teaser("Descuento especial 10%", "Día").banco is None


# ── La trampa: promos por unidad ─────────────────────────────────
def test_segunda_unidad_no_es_descuento_sobre_el_total():
    """'2da unidad 70%' no es 70% off. Se informa, no se aplica."""
    p = parsear_teaser("2da unidad 70%", "Día")
    assert p.tipo == "multi_unidad"
    assert p.aplicable_al_total is False, "aplicarla inflaría el ahorro"


def test_variantes_de_multi_unidad():
    for texto in ("2da unidad 70%", "Segunda unidad al 50%", "3x2",
                  "2x1 en toda la línea", "Llevando 3 pagás 2",
                  "3er unidad 80%"):
        p = parsear_teaser(texto, "Día")
        assert p is not None, texto
        assert p.tipo == "multi_unidad", f"{texto} -> {p.tipo}"
        assert p.aplicable_al_total is False, texto


def test_cuotas_sin_interes_no_dan_reintegro():
    p = parsear_teaser("6 cuotas sin interés", "Carrefour")
    assert p.tipo == "cuotas"
    assert p.cuotas_sin_interes == 6
    assert p.aplicable_al_total is False


def test_descuento_absurdo_se_descarta():
    """100% o más es dato corrupto, no una oferta."""
    assert parsear_teaser("100% de descuento", "Día") is None
    assert parsear_teaser("150%", "Día") is None


def test_texto_inutil_devuelve_none():
    for t in ("", "   ", "Envío gratis", None):
        assert parsear_teaser(t, "Día") is None


# ── Agregación ───────────────────────────────────────────────────
def _precios(*filas):
    return {(c, p): {"precio_min": pr, "promos": tz} for c, p, pr, tz in filas}


def test_extrae_y_agrupa_por_cadena():
    precios = _precios(
        ("Carrefour", "aceite", 4890.0, ["Tarjeta Carrefour 15%"]),
        ("Carrefour", "yerba",  3200.0, ["Tarjeta Carrefour 15%"]),
        ("Día",       "aceite", 5250.0, ["20% Banco Nación"]),
    )
    r, meta = extraer(precios)
    assert set(r) == {"Carrefour", "Día"}
    assert len(r["Carrefour"]) == 1                       # una promo, dos productos
    assert sorted(r["Carrefour"][0].productos) == ["aceite", "yerba"]
    assert meta["teasers_descartados"] == 0


def test_precios_del_sepa_no_traen_promos():
    """Sin la clave `promos` el sistema cae al comportamiento anterior."""
    r, meta = extraer({("Día", "aceite"): {"precio_min": 5250.0}})
    assert r == {}
    assert meta["teasers_vistos"] == 0
    assert meta["aviso"] is None      # no hay nada que avisar, no es una falla


# ── Cálculo del reintegro ────────────────────────────────────────
def test_reintegro_solo_sobre_los_productos_con_la_promo():
    """
    Canasta de $10.000 donde solo un ítem de $4.000 tiene 15%.
    El reintegro es 600, no 1500.
    """
    promos = [PromoSitio(nombre="Tarjeta 15%", cadena="Carrefour",
                         descuento_pct=15.0, productos=["aceite"])]
    detalle = [{"producto": "aceite", "subtotal": 4000.0, "ok": True},
               {"producto": "yerba",  "subtotal": 6000.0, "ok": True}]
    nombre, r, cuotas, banco = reintegro_para("Carrefour", promos, detalle)
    assert r == 600.0, f"{r} — no debe aplicarse sobre el total de la canasta"


def test_gana_la_promo_con_mayor_reintegro_no_el_mayor_porcentaje():
    """30% sobre $1.000 pierde contra 10% sobre $9.000."""
    promos = [PromoSitio(nombre="30% chico", cadena="Día", descuento_pct=30.0,
                         productos=["yerba"]),
              PromoSitio(nombre="10% grande", cadena="Día", descuento_pct=10.0,
                         productos=["aceite"])]
    detalle = [{"producto": "yerba", "subtotal": 1000.0, "ok": True},
               {"producto": "aceite", "subtotal": 9000.0, "ok": True}]
    nombre, r, _, _ = reintegro_para("Día", promos, detalle)
    assert nombre == "10% grande" and r == 900.0


def test_multi_unidad_se_informa_con_reintegro_cero():
    promos = [PromoSitio(nombre="2da unidad 70%", cadena="Día",
                         descuento_pct=70.0, tipo="multi_unidad", productos=["aceite"])]
    detalle = [{"producto": "aceite", "subtotal": 5000.0, "ok": True}]
    nombre, r, _, _ = reintegro_para("Día", promos, detalle)
    assert nombre == "2da unidad 70%"
    assert r == 0.0, "no se puede aplicar al total"


def test_producto_sin_precio_no_suma_al_reintegro():
    promos = [PromoSitio(nombre="15%", cadena="Día", descuento_pct=15.0,
                         productos=["aceite", "fantasma"])]
    detalle = [{"producto": "aceite", "subtotal": 4000.0, "ok": True},
               {"producto": "fantasma", "subtotal": 0, "ok": False}]
    _, r, _, _ = reintegro_para("Día", promos, detalle)
    assert r == 600.0


def test_teasers_que_no_se_entienden_se_cuentan_y_se_reportan():
    """
    Hallazgo 3. Un teaser que el parser no entiende salía por un `continue` sin
    log ni contador. Tiene que quedar contado: es la diferencia entre "esta
    cadena no tiene promos hoy" y "dejamos de entender lo que manda el sitio".
    """
    precios = _precios(
        ("Carrefour", "aceite", 4890.0, ["Tarjeta Carrefour 15%"]),
        ("Carrefour", "yerba",  3200.0, ["Envío gratis", "Beneficio exclusivo"]),
    )
    r, meta = extraer(precios)

    assert len(r["Carrefour"]) == 1                  # la que sí se entendió
    assert meta["teasers_vistos"] == 3
    assert meta["teasers_parseados"] == 1
    assert meta["teasers_descartados"] == 2
    assert meta["aviso"], "descartar 2 de 3 teasers no puede ser silencioso"


def test_si_no_se_parsea_ningun_teaser_el_aviso_lo_dice():
    """
    El caso que ya pasó con `<Name>k__BackingField`: el sitio cambia el formato,
    el parser deja de entender TODO, `promos_sitio` queda vacío y la app cae a
    PROMOS_DEFAULT — que puede estar vencida — sin que nada lo note.
    """
    precios = _precios(("Día", "aceite", 5250.0, ["Beneficio exclusivo", "Envío gratis"]))
    r, meta = extraer(precios)

    assert r == {}
    assert meta["teasers_vistos"] == 2
    assert meta["teasers_parseados"] == 0
    assert "formato" in (meta["aviso"] or "").lower(), \
        "cero de N parseados es el síntoma de un cambio de formato, hay que nombrarlo"


def test_sin_promos_devuelve_vacio():
    assert reintegro_para("Día", [], []) == (None, 0.0, 0, None)


def test_serializacion_para_la_api():
    j = a_json({"Carrefour": [PromoSitio(nombre="Tarjeta 15%", cadena="Carrefour",
                                         descuento_pct=15.0, banco=None,
                                         productos=["aceite", "aceite"])]})
    e = j["Carrefour"][0]
    assert e["descuento_pct"] == 15.0
    assert e["aplicable_al_total"] is True
    assert e["productos"] == ["aceite"]        # deduplicado


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
