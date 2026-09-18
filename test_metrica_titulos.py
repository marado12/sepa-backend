"""
La métrica de los títulos "N × M", contra títulos REALES de las 5 cadenas. Sin red.

POR QUÉ EXISTE (Tarea 19). `_extraer_cantidades_desc` solo multiplicaba "N [u.|un|unid|rollos]
x M". Con cualquier otro orden —"30 Mts x 4 Un" (Vea), "80mts 2 Ud." (Día), "30m 4u" (Chango
Más), "x4 30 mts" (Carrefour), "4 rollos de 30m" (Coto)— tomaba el largo de UN rollo: de 135
papeles "N u. × M" disponibles daba el total en 29, y en Día en 0 de 16. La API de VTEX no lo
tapa: manda measurementUnit="un" en los 601 productos no pesables medidos el 13/09.

Fuera del papel multiplicar no alcanza: el mismo Danette x4 se publica "pack x4 95 g." (M por
pote) y "Pack x 4 380 Gr." (M total). Decisión de Santiago (13/09): donde la forma del título no
deja decidir, NO se publica métrica. Un hueco es recuperable; un precio equivocado no.
Excepción decidida el mismo día: el "N x M" pelado ("2x1.75L", "4x115 Gr") sigue multiplicando.

Datos: la captura del 13/09 19:02 ART (tests/capturas/metrica_vivo_2026-09-13.json), leída con
los parsers de producción. La referencia es `leer_titulo` del instrumento: regex propias,
independientes del parser que se prueba. En el papel la convención "M es por rollo" no tiene
contraejemplos: 135 títulos, y coincide con el cociente de Coto en todos los cruces.

Alcance honesto: una captura, un solo punto de medición (Argentina), 7 consultas.
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import main
from precios_vtex import _UNIDAD_SIN_CONTENIDO, _precio_por_100u
from tests import medir_metrica_vivo as instrumento

CAPTURA = Path(__file__).resolve().parent / "tests" / "capturas" / "metrica_vivo_2026-09-13.json"
CADENAS = instrumento.CADENAS
TOL = 0.02

_CACHE = {}


def _filas():
    """{(cadena, consulta, producto, precio): fila} — filas distintas de la captura."""
    if not _CACHE:
        cap = json.loads(CAPTURA.read_text(encoding="utf-8"))
        for cadena, por_consulta in instrumento.parsear(cap).items():
            for consulta, filas in por_consulta.items():
                for f in filas:
                    _CACHE.setdefault((cadena, consulta, f["of"].producto, f["of"].precio), f)
    return _CACHE


def _metrica(of):
    """La métrica como la calcula la ruta online en producción."""
    return _precio_por_100u(of, main._extraer_cantidades_desc, main.normalizar)


def _cerca(a, b):
    return abs(a - b) <= TOL * b


def _es_nxm(ref):
    return ref.get("n", 1) > 1 and bool(ref.get("m"))


def _por_titulo(cadena, titulo):
    filas = [f for (c, _q, t, _p), f in _filas().items() if c == cadena and t == titulo]
    assert filas, f"{cadena}: {titulo!r} no está en la captura — la lista del test está mal escrita"
    return filas


def _txt(pu):
    return (pu["tipo"], pu["cantidad_base"]) if pu else None


# ── (a) El papel: el total del paquete ───────────────────────────
def test_el_papel_nxm_da_el_total_del_paquete():
    """Los 135 papeles "N u. × M" disponibles: N rollos de M metros son N×M metros."""
    fallos, total = [], Counter()
    for (c, q, t, _p), f in _filas().items():
        if q != "Papel higienico" or not f["of"].disponible or not _es_nxm(f["ref"]):
            continue
        total[c] += 1
        pu = _metrica(f["of"])
        if not (pu and pu["tipo"] == "longitud" and _cerca(pu["cantidad_base"], f["ref"]["base"])):
            fallos.append((c, t, f["ref"]["base"], _txt(pu)))
    assert sum(total.values()) == 135, f"la captura tiene que dar 135 papeles N×M: {dict(total)}"
    assert not fallos, (
        f"{len(fallos)} de 135 papeles N×M no dan el total del paquete. Fallos por cadena "
        f"{dict(Counter(c for c, *_ in fallos))} sobre {dict(total)}:\n   "
        + "\n   ".join(f"{c}: {t!r} → {m} (esperado {e:g} m)" for c, t, e, m in fallos[:15]))


# ── (b) Los que ya estaban bien no se mueven ─────────────────────
# Los 29 papeles que el parser anterior (a71c83d) ya medía bien, escritos a mano para no depender
# del código viejo. Es el guard de no-regresión del fix: "4 u. x 80 m." y "4x30mts" son las formas
# que ya andaban, y un arreglo que lea el título entero puede romperlas.
PAPEL_BIEN_ANTES_DEL_FIX = {
    ("Carrefour", "Papel higiénico Elegante 4 u. x 80 m."): 320,
    ("Carrefour", "Papel higiénico Elegante doble hoja  4x 20 mt."): 80,
    ("Carrefour", "Papel higiénico Felpita blanquísimo hoja simple 4 x 80 m."): 320,
    ("Carrefour", "Papel higiénico Felpita hoja simple 12 x 30 m."): 360,
    ("Carrefour", "Papel higiénico Felpita hoja simple 4 x 30 m."): 120,
    ("Carrefour", "Papel higiénico doble hoja Carrefour Essential 4 x 30 mts"): 120,
    ("Carrefour", "Papel higiénico doble hoja Elegante 4 x 30 m."): 120,
    ("Carrefour", "Papel higiénico doble textura Felpita 4 u. x 20 m."): 80,
    ("Carrefour", "Papel higiénico ecológico Campanita 4 u. x 30 m."): 120,
    ("Carrefour", "Papel higiénico hoja simple Campanita soft 4 x 80 m."): 320,
    ("Carrefour", "Papel higiénico hoja simple Carrefour Essential 4 x 30 mts"): 120,
    ("Carrefour", "Papel higiénico hoja simple Carrefour Essential 4 x 80 m."): 320,
    ("Carrefour", "Papel higiénico hoja simple Elegante 4 x 30 m."): 120,
    ("Carrefour", "Papel higiénico hoja simple Elegante 4 x 80 m."): 320,
    ("Carrefour", "Papel higiénico hoja simple Elegante aloe vera 6 x 30 m."): 180,
    ("Carrefour", "Papel higiénico hoja simple aloe vera Elegante 24 x 30 m."): 720,
    ("Chango Más", "Papel Higiénico Elegante 4x30mts"): 120,
    ("Chango Más", "Papel Higiénico Elegante Aloe Vera 6x30mts"): 180,
    ("Chango Más", "Papel Higiénico Elegante Blanco 24 Rollos X 30mts"): 720,
    ("Chango Más", "Papel Higiénico Elegante Doble Hoja 4x30mts"): 120,
    ("Coto", "Papel Higiénico Boral Doble Hoja 4 rollos x 30 metros"): 120,
    ("Coto", "Papel Higiénico Felpita Superpack Blanco 24 rollos x 30m"): 720,
    ("Vea", "Papel Higienico Doble Hoja 4x30 M Family Care"): 120,
    ("Vea", "Papel Higienico Elegante Simple Hoja 4x30m"): 120,
    ("Vea", "Papel Higienico Elegante Simple Hoja Aloe Vera 12x30m"): 360,
    ("Vea", "Papel Higienico Elegante Simple Hoja Aloe Vera 6x30m"): 180,
    ("Vea", "Papel Higienico Simple Hoja 4 X 80 M Elegante"): 320,
    ("Vea", "Papel Higienico Simple Hoja 4x30 M Family Care"): 120,
    ("Vea", "Papel Higienico Simple Hoja 6x30 M Family Care."): 180,
}


def test_los_papeles_que_ya_estaban_bien_no_se_mueven():
    fallos = []
    for (c, t), metros in PAPEL_BIEN_ANTES_DEL_FIX.items():
        for f in _por_titulo(c, t):
            pu = _metrica(f["of"])
            if not (pu and pu["tipo"] == "longitud" and _cerca(pu["cantidad_base"], metros)):
                fallos.append(f"{c}: {t!r} → {_txt(pu)} (esperado {metros} m)")
    assert not fallos, "papeles que ya estaban bien y dejaron de estarlo:\n   " + "\n   ".join(fallos)


# ── (c) Peso y volumen: donde la forma no decide, no hay métrica ─
# El "N x M" pelado —la x entre los dos números, sin palabra de unidad— no se contradice en
# ningún dato visto: 6 de 6 en esta captura, y ~460 descripciones del SEPA (04/05) cuya muestra es
# toda por unidad ("3 X 90 GRS", "4 X 250 CC"). Decidido el 13/09: sigue multiplicando.
NXM_PELADO = {
    ("Coto", "Combo de Gaseosas Coca-Cola y Fanta Original y Naranja Dúo 2 x 1.75 litros"): ("volumen", 3500),
    ("Coto", "Gaseosa Coca-Cola Combo Dúo Original y Zero 2x1.75L"): ("volumen", 3500),
    ("Coto", "Refrescos Combo Dúo Coca-Cola y Sprite Sabor Original y Lima-Limón 2x1.75L"): ("volumen", 3500),
    ("Vea", "Postre Danette 2 X 95 Gr"): ("peso", 190),
    ("Vea", "Postre Danette Dulce De Leche 4x115 Gr"): ("peso", 460),
    ("Vea", "Postre Danette Vainilla 2x95 Gr"): ("peso", 190),
}


def test_nxm_de_peso_y_volumen_queda_sin_metrica():
    """
    Las 38 filas disponibles de peso o volumen con forma N×M, menos los 3 Dúo pelados. Entran las
    11 donde M ya es el total ("Pack x 2 190 Gr.", "X4 380g", "pack 6L", "6u 540g"): hoy aciertan
    por casualidad, son indistinguibles de las 22 que están mal, y publicar unas junto a las otras
    no es aceptable.
    """
    con_metrica, total = [], Counter()
    for (c, _q, t, _p), f in _filas().items():
        ref = f["ref"]
        if not (f["of"].disponible and ref.get("tipo") in ("peso", "volumen") and _es_nxm(ref)):
            continue
        if (c, t) in NXM_PELADO:
            continue
        total[c] += 1
        pu = _metrica(f["of"])
        if pu:
            con_metrica.append(f"{c}: {t!r} → {_txt(pu)}")
    assert sum(total.values()) == 35, f"la captura tiene que dar 35 filas (38 − 3 Dúo): {dict(total)}"
    assert not con_metrica, (
        f"{len(con_metrica)} de 35 filas N×M de peso/volumen publican una métrica que la forma del "
        f"título no permite decidir:\n   " + "\n   ".join(con_metrica))


def test_el_nxm_pelado_sigue_multiplicando():
    fallos = []
    for (c, t), (tipo, total) in NXM_PELADO.items():
        for f in _por_titulo(c, t):
            pu = _metrica(f["of"])
            if not (pu and pu["tipo"] == tipo and _cerca(pu["cantidad_base"], total)):
                fallos.append(f"{c}: {t!r} → {_txt(pu)} (esperado {tipo} {total})")
    assert not fallos, "el N x M pelado dejó de multiplicar:\n   " + "\n   ".join(fallos)


# ── (d) Alcance: una sola medida no es ambigua ───────────────────
# Filas disponibles cuya referencia es "solo medida" (una medida, ningún contable: "Coca Cola
# 2,25 L") y donde el backend coincide con ella. Medido sobre la captura ANTES del fix (a71c83d).
# La regla de ambigüedad no tiene ningún motivo para tocarlas: si este número baja, se aplicó más
# ancha de lo decidido. La que no coincide (de 278) no es de este bug: "Cerveza Lata BRAHMA 350
# Cmq" de Coto, porque `cmq` no está en el vocabulario.
# ✏️ Tarea 25 (14/09): salen del conjunto las filas cuya fuente declara una unidad de medida con
# contenido (los pesables). Ahí el título no dice lo que cotiza el precio: "Pollo Entero Fresco 3 Kg"
# cuesta $3.989 EL KILO y su métrica cubre 1 kg, no 3. Contra el título eran 3 filas de Chango Más
# (los dos pollos "3 Kg" y "Carne Picada Magra 800 G", kg/0.65): antes 2 "coincidían" con la métrica
# 3× abaratada y la carne no; ahora las tres tienen el $/kg correcto. No perdieron métrica: la
# referencia de este test era el supuesto desmentido. Los pesables se prueban contra el carrito en
# test_pesables.py. Chango Más baja de 76 a 74 por eso, no porque se haya caído nada.
SOLO_MEDIDA_COINCIDE_MIN = {"Carrefour": 82, "Día": 53, "Vea": 34, "Chango Más": 74, "Coto": 34}


def test_una_sola_medida_conserva_su_metrica():
    coincide, total = Counter(), Counter()
    for (c, _q, _t, _p), f in _filas().items():
        ref = f["ref"]
        if not f["of"].disponible or ref.get("motivo") != "solo medida":
            continue
        if (f["of"].unidad_medida or "").strip().lower() not in _UNIDAD_SIN_CONTENIDO | {""}:
            continue            # pesable: el título no es la referencia (ver arriba)
        total[c] += 1
        pu = _metrica(f["of"])
        coincide[c] += bool(pu and pu["tipo"] == ref["tipo"] and _cerca(pu["cantidad_base"], ref["base"]))
    bajas = {c: f"{coincide[c]} < {minimo}" for c, minimo in SOLO_MEDIDA_COINCIDE_MIN.items()
             if coincide[c] < minimo}
    assert not bajas, (f"títulos de UNA sola medida perdieron su métrica: {bajas} "
                       f"(coinciden {dict(coincide)} de {dict(total)})")


# ── (e) m²: no se convierte ──────────────────────────────────────
# Coto trae "Paq 8 M2". Pasarlo a metros exige suponer rollos de 10 cm, y eso no tiene ficha
# técnica que lo respalde: solo coincide con los títulos. Queda pendiente para la Tarea 22. Las
# filas que hoy dan $/rollo lo conservan (decidido el 13/09): no entran al %, porque la ficha las
# marca `unidad_distinta` frente a los metros. Con metros explícitos se mide por los metros.
M2_COTO = {
    "P.Higienico 80 Mts Campanita X 4 32 M2": ("longitud", 320),
    "P.Higienico D/H Soft X 30  Campanita Bol 12 M2": None,
    "P.Higienico D/H X 4 Rollos Felpita Paq 8 M2": ("count", 4),
    "P.Higienico Infinity X4 Felpita Paq 5 M2": None,
    "P.Higienico Plus X4 Campanita Paq 20 M2": None,
    "P.Higienico Pura Caricia X Felpita Paq 18 M2": None,
    "P.Higienico S/H X 12 Rollo Felpita Paq 36 M2": ("count", 12),
    "P.Higienico X12 Rollos Campanita Paq 36 M2": ("count", 12),
    "P.Higienico X4 Rollo 30 M2 Campanita Paq 12 M2": ("count", 4),
}


def test_los_m2_no_se_convierten_a_metros():
    en_captura = {t for (c, _q, t, _p) in _filas() if c == "Coto" and " M2" in t.upper()}
    assert en_captura == set(M2_COTO), f"la lista no cubre los m² de la captura: {en_captura ^ set(M2_COTO)}"
    fallos = []
    for t, esperado in M2_COTO.items():
        for f in _por_titulo("Coto", t):
            pu = _metrica(f["of"])
            ok = (pu is None if esperado is None else
                  bool(pu) and pu["tipo"] == esperado[0] and _cerca(pu["cantidad_base"], esperado[1]))
            if not ok:
                fallos.append(f"{t!r} → {_txt(pu)} (esperado {esperado})")
    assert not fallos, "m²:\n   " + "\n   ".join(fallos)


# ── Una medida "A x B cm" no cuenta envases ──────────────────────
# Descripciones REALES del SEPA (parquet del 04/05/2026, columna `_desc_norm`), no de la captura.
# Las encontró la re-medición del fix sobre la ruta SEPA: leer "30x30cm" como 30 unidades
# convertía un rollo de 30 m en 900 m, y "20x21x6 cm" dejaba sin métrica un hermético de 1,25 L.
def test_una_medida_a_x_b_cm_no_cuenta_envases():
    assert main._extraer_cantidades_desc(
        "papel p envolver soul life 30x30cm x 30mts") == [(30.0, "longitud")]
    hermetico = "hermetico duo360 con tapa cuad 1.25 l 20x21x6 cm"
    assert not main._forma_ambigua(hermetico)
    assert main._extraer_cantidades_desc(hermetico)[0] == (1250.0, "volumen")


def test_un_punto_de_abreviatura_no_es_un_decimal():
    """
    SEPA, 04/05. "simp.4x90m" son 4 rollos de 90 m (36 m², coincide). Rechazar un número detrás de
    un punto como si fuera decimal dejaba 90 m: estaba bien antes del fix y el fix lo rompía.
    """
    assert main._extraer_cantidades_desc(
        "papel hig.max hoja simp.4x90m higienol paq 36 m2")[0] == (360.0, "longitud")


def test_un_codigo_de_modelo_no_cuenta_envases():
    """
    SEPA, 04/05. "mx6x3am" es un código: el cable mide 1 m, no 6. Pero la frontera es fina:
    "x4x30mt" son 4 rollos de 30 m, y excluir todo número pegado a una letra lo rompía (120 → 30).
    """
    assert main._extraer_cantidades_desc(
        "cargador celular apple magsafe 1m blanco mx6x3am a")[0] == (1.0, "longitud")
    assert main._extraer_cantidades_desc(
        "papel higienico sol mayor x4x30mt rll-30-mt.")[0] == (120.0, "longitud")


# ── Guards por cadena: piso y brecha, como test_unidades.py ──────
def _acierto_papel():
    ok, tot = Counter(), Counter()
    for (c, q, _t, _p), f in _filas().items():
        if q != "Papel higienico" or not f["of"].disponible or not _es_nxm(f["ref"]):
            continue
        tot[c] += 1
        pu = _metrica(f["of"])
        ok[c] += bool(pu and pu["tipo"] == "longitud" and _cerca(pu["cantidad_base"], f["ref"]["base"]))
    return {c: round(100 * ok[c] / tot[c], 1) for c in tot}


def _cobertura_captura():
    con, n = Counter(), Counter()
    for (c, _q, _t, _p), f in _filas().items():
        if f["of"].disponible:
            n[c] += 1
            con[c] += bool(_metrica(f["of"]))
    return {c: round(100 * con[c] / n[c], 1) for c in n}


# Medido el 14/09 con el fix cableado: 100 en las 5 cadenas. En a71c83d: Carrefour 33,3 · Día 0 ·
# Vea 35 · Chango Más 8,7 · Coto 40. Día no tenía un solo papel bien y el agregado (21%) no lo decía.
PISO_ACIERTO_PAPEL = 95.0
# ⚠️ Lección de la Tarea 20, que es del proyecto y no de este test: si una brecha se pone en rojo,
# antes de tocar la constante hay que mirar si alguna cadena BAJÓ. Una brecha también sube cuando una
# sola cadena MEJORA —en la Tarea 20 pasó de 40,1 a 42,1 con un arreglo correcto—, y el número
# agregado no distingue un caso del otro. El detalle por cadena del mensaje, sí.
BRECHA_ACIERTO_PAPEL_MAX = 5.0

# Filas disponibles con métrica, captura del 13/09. Medido el 14/09 con la regla cableada:
# Carrefour 93,7 · Día 92,3 · Vea 88,1 · Chango Más 94,7 · Coto 57,7. En a71c83d, sin la regla:
# 97,2 · 100 · 95,5 · 98,5 · 71,1; la diferencia es el costo aceptado de la regla (5 · 6 · 5 · 5 · 13
# filas). Coto es el piso por motivos propios (pollo "X Kg" sin campos de API, papel en m²), así que
# cada cadena tiene su piso: lo medido menos 3 puntos, no un número común calibrado mirando a Coto.
PISO_COBERTURA_METRICA = {"Carrefour": 90.0, "Día": 89.0, "Vea": 85.0, "Chango Más": 91.0, "Coto": 54.0}
# ⚠️ La misma lección de la Tarea 20: rojo acá → mirar primero si alguna cadena BAJÓ. Medida: 37,0.
BRECHA_COBERTURA_MAX = 40.0


def test_ninguna_cadena_queda_abajo_del_piso_de_acierto_del_papel():
    acierto = _acierto_papel()
    bajas = {c: p for c, p in acierto.items() if p < PISO_ACIERTO_PAPEL}
    assert not bajas, f"acierto del papel por debajo de {PISO_ACIERTO_PAPEL}%: {bajas} (todas: {acierto})"


def test_la_brecha_de_acierto_del_papel_no_se_abre():
    acierto = _acierto_papel()
    brecha = max(acierto.values()) - min(acierto.values())
    assert brecha <= BRECHA_ACIERTO_PAPEL_MAX, (
        f"{brecha:.1f} puntos de acierto del papel entre la mejor y la peor cadena "
        f"(máx {BRECHA_ACIERTO_PAPEL_MAX}): {acierto}. Mirá si alguna cadena BAJÓ antes de tocar esto.")


def test_ninguna_cadena_queda_abajo_de_su_piso_de_cobertura():
    cob = _cobertura_captura()
    bajas = {c: f"{cob[c]} < {piso}" for c, piso in PISO_COBERTURA_METRICA.items() if cob[c] < piso}
    assert not bajas, f"cobertura de métrica por debajo del piso: {bajas} (todas: {cob})"


def test_la_brecha_de_cobertura_no_se_abre():
    cob = _cobertura_captura()
    brecha = max(cob.values()) - min(cob.values())
    assert brecha <= BRECHA_COBERTURA_MAX, (
        f"{brecha:.1f} puntos de cobertura entre la mejor y la peor cadena (máx {BRECHA_COBERTURA_MAX}): "
        f"{cob}. Mirá si alguna cadena BAJÓ antes de tocar esto.")


# ── Contables falsos en la multiplicación de metros ──────────────
# En la regla, un contable falso deja un hueco. En los metros, multiplica: el número queda mal y no se
# cae nada. Estos tres guards existen para ese caso (mutación M8 de la Tarea 19).
def test_todo_papel_con_referencia_de_longitud_coincide():
    """
    Las 138 filas de papel de la captura —disponibles o no, N×M o una sola medida— donde la
    referencia lee una longitud. Un contable falso junto a uno real hace caer la longitud o la
    multiplica mal; cualquiera de las dos cosas se pone en rojo acá.
    """
    fallos, total = [], 0
    for (c, q, t, _p), f in _filas().items():
        if q != "Papel higienico" or f["ref"].get("tipo") != "longitud":
            continue
        total += 1
        pu = _metrica(f["of"])
        if not (pu and pu["tipo"] == "longitud" and _cerca(pu["cantidad_base"], f["ref"]["base"])):
            fallos.append(f"{c}: {t!r} → {_txt(pu)} (referencia {f['ref']['base']:g} m)")
    assert total == 138, f"la captura tiene que dar 138 papeles con referencia de longitud, da {total}"
    assert not fallos, f"{len(fallos)} papeles no coinciden con la referencia:\n   " + "\n   ".join(fallos[:12])


# El paquete de papel más largo de la captura son 1.200 m ("Elegante Premium 24u X 50 M"). Un código
# de modelo leído como contable multiplica por decenas o cientos y pasa este techo aunque la referencia
# no pueda leer el título.
MAX_METROS_PAQUETE_PAPEL = 1500.0


def test_ningun_papel_pasa_el_techo_de_metros():
    altos = []
    for (c, q, t, _p), f in _filas().items():
        pu = _metrica(f["of"]) if q == "Papel higienico" else None
        if pu and pu["tipo"] == "longitud" and pu["cantidad_base"] > MAX_METROS_PAQUETE_PAPEL:
            altos.append(f"{c}: {t!r} → {pu['cantidad_base']:g} m")
    assert not altos, f"papel por encima de {MAX_METROS_PAQUETE_PAPEL:g} m:\n   " + "\n   ".join(altos)


def test_un_rollo_suelto_no_se_multiplica():
    """
    SEPA, 04/05. Rollos sin contable y con un número que no es un envase: el ancho ("x30 cm") o la
    presentación ("caja x25mts"). Acá un contable falso sería el ÚNICO del título y multiplicaría sin
    que nada se caiga: el caso que el guard de la referencia no puede ver.
    """
    for desc, metros in [("rollo film adherente rolopac 200 mts x30 cm", 200.0),
                         ("rollo film adherente separata 30 cm x 100 mts", 100.0),
                         ("papel higienico megamax elegante x 120 mts", 120.0),
                         ("rollo aluminio glow caja x25mts", 25.0)]:
        assert main._extraer_cantidades_desc(desc)[:1] == [(metros, "longitud")], desc


# ── Contables falsos en la regla de ambigüedad ───────────────────
# Censo del 14/09 sobre el SEPA local (91.453 descripciones) y las dos capturas en vivo. En la
# regla, un contable falso no inventa un número: deja un hueco. Solo se corrigen las formas donde
# sacarlo deja, en TODOS los casos vistos, la medida del artículo — si no, el hueco es más seguro.
def test_una_palabra_de_envase_en_singular_no_cuenta_envases():
    """
    "308 paq", "1882 botella", "etapa 3 lata": el número es del nombre y la palabra describe UN
    envase. 11 de 11 en el censo, y en las 11 la medida que queda es la del artículo. Los fideos
    son de Coto (captura del 14/09); el resto, del SEPA del 04/05.
    """
    for desc, esperado in [
        ("fideos de semolin con huevo n4 308 paq 500 grm", (500.0, "peso")),
        ("fernet 1882 botella x 750 ml", (750.0, "volumen")),
        ("leche polvo infantil vital etapa 3 lata x 800 g", (800.0, "peso")),
        ("patagonia ipa 24 7 lata 473cc", (473.0, "volumen")),
    ]:
        assert not main._forma_ambigua(desc), desc
        assert main._extraer_cantidades_desc(desc)[:1] == [esperado], desc
    # En plural sí cuenta: seis latas de 473 ml, y el título no dice si 473 es de una o del total.
    assert main._forma_ambigua("cerveza stella artois pack latas 473 ml 6 unidades")


def test_un_porcentaje_no_cuenta_envases():
    """SEPA, 04/05: "vegetalex 100%" — la x es la última letra de la marca y 100 es un porcentaje."""
    desc = "burger vegetalex 100% vegetal x226g cja-220-g."
    assert not main._forma_ambigua(desc)
    assert main._extraer_cantidades_desc(desc)[:1] == [(226.0, "peso")]


# ── (f) Fracciones: "1/2 Kg" es medio kilo (Tarea 26, paso a) ────
# `_RE_CANT_DESC` no conoce la barra: en "1/2 Kg" matcheaba "2 Kg" y el azúcar de Chango Más salía
# de 2 kg. Con el subtotal nuevo de la Tarea 26 la métrica fija el precio: un paquete de medio kilo
# "cubriría" los 2 kg pedidos y la fila subcobraría sin que se note.
# Pero "N/M unidad" NO siempre es una fracción. SEPA del 04/05, todas de Coto: "160/112g" (atún,
# bruto/escurrido), "9.5/6kg" y "10/6kg" (lavasecarropas, lava/seca), "4/ 64g" (tablet). Por eso
# solo se divide la fracción propia de denominador 2 o 4, que es la única forma vista como fracción.
CAPTURA_CANASTA = Path(__file__).resolve().parent / "tests" / "capturas" / "canasta_default_2026-09-14.json"
# Filas cuya referencia no puede dar `leer_titulo` (lee "1/2 Kg" como "número suelto [1]" y no da
# número). Leídas a mano del título crudo. Es la única fracción de las capturas y fixtures en disco.
LEIDO_A_MANO = {("Chango Más", "Azúcar Rubio Azucel Orgánica 1/2 Kg"): ("peso", 500.0)}
# Caché propio: _filas decide con `if not _CACHE` y compartirlo le metía estas filas adentro.
_CACHE_CANASTA = {}


def _filas_canasta():
    """{(cadena, producto, precio): fila} — filas distintas de la captura de la canasta, sin pesables."""
    if not _CACHE_CANASTA:
        cap = json.loads(CAPTURA_CANASTA.read_text(encoding="utf-8"))
        for cadena, por_consulta in instrumento.parsear(cap).items():
            for filas in por_consulta.values():
                for f in filas:
                    of = f["of"]
                    um = (of.unidad_medida or "").strip().lower()
                    if of.contenido and um and um not in _UNIDAD_SIN_CONTENIDO:
                        continue        # pesable: el título no dice lo que cotiza (test_pesables.py)
                    _CACHE_CANASTA.setdefault((cadena, of.producto, of.precio), f)
    return _CACHE_CANASTA


def test_la_fraccion_divide():
    """Antes del fix: 2000 g en las cinco (se quedaba con el denominador)."""
    f = next(f for (c, t, _p), f in _filas_canasta().items()
             if c == "Chango Más" and t == "Azúcar Rubio Azucel Orgánica 1/2 Kg")
    assert _txt(_metrica(f["of"])) == ("peso", 500.0), _txt(_metrica(f["of"]))
    # Jumbo, SEPA del 04/05 (`_desc_norm`). Otra grafía que la de Chango Más: pegada a la unidad.
    for desc in ["yerba mate taragui 4flex 1/2kg paq-500-g.",
                 "yerba mate union 4flex 1/2kg paq-500-g.",
                 "yerba mate mananita 4flex 1/2kg paq-500-g.",
                 "ravioles bolsa 1/2 kg di pascualle bsa-500-g."]:
        assert main._extraer_cantidades_desc(desc) == [(500.0, "peso")], desc


def test_una_barra_que_no_es_fraccion_no_se_mueve():
    """Coto, SEPA del 04/05. Dividir siempre daba 1,43 g de atún. Quedan como estaban antes del fix."""
    for desc, esperado in [
        ("atun lomo al natural ciudad del lago lat 160/112g", (112.0, "peso")),
        ("lavasecarropas cf 9.5/6kg blanco . . .", (6000.0, "peso")),
        ("lavasecarropas samsung 9.5/6kg wd95t504dbeubg", (6000.0, "peso")),
        ("lavasecarropas bgh 10/6kg inv bwdn10w25ar", (6000.0, "peso")),
        ("tab 10 gen2 tcl memoria 4/ 64g . . .", (64.0, "peso")),
    ]:
        assert main._extraer_cantidades_desc(desc) == [esperado], desc
    # Número mixto: no aparece en ningún dato visto. Documenta la decisión (hueco antes que leer
    # 500 g o 2 kg por 1,5 kg); NO es cobertura.
    assert main._extraer_cantidades_desc("queso 1 1/2 kg") == []


def _sin_numero_equivocado():
    """{cadena: (% de filas con referencia cuya métrica no es un número distinto, n con referencia)}."""
    cnt = {c: Counter() for c in CADENAS}
    for (c, t, _p), f in _filas_canasta().items():
        ref = f["ref"]
        if (c, t) in LEIDO_A_MANO:
            tipo, base = LEIDO_A_MANO[(c, t)]
            ref = {"tipo": tipo, "base": base, "n": 1, "m": base}
        clase = instrumento.comparar(_metrica(f["of"]), ref)
        if clase == "sin referencia":
            continue
        cnt[c]["ref"] += 1
        if clase in ("cantidad distinta", "tomó UNA unidad") or clase.startswith("tipo distinto"):
            cnt[c]["mal"] += 1
    return {c: ((n["ref"] - n["mal"]) / n["ref"] * 100, n["ref"]) for c, n in cnt.items()}


# Medido el 18/09 con el fix de la fracción (paso a), captura de la canasta del 14/09 (fija: el
# número es exacto, no una estimación). Antes de ese fix Chango Más daba 99,83 (596 de 597: el
# azúcar "1/2 Kg" leído como 2 kg). Lo que no era 100 ahí y NO era de ese bug (quedó reportado sin
# tocar): Vea 97,53 (19 "Six Pack"/"Four Pack"/"Eight Pack"/"Sixpack" donde la métrica tomaba UNA
# botella) y Coto 99,44/99,45 (dos gaseosas cuyo título escribe mal la unidad, "1,75 Ml" y "2.25cc").
# ✏️ 18/09, paso (a2): las dos se arreglaron — ver sección (g). Vea sube a 99,87 (18 de 19 filas se
# arreglan; la 19ª, "Pack 6" pelado, queda igual — PARÉ, sin decisión, ver (g)) y Coto a 100 (2/2).
PISO_SIN_NUMERO_EQUIVOCADO = {"Carrefour": 100.0, "Día": 100.0, "Vea": 99.87, "Chango Más": 100.0,
                              "Coto": 100.0}
# Medida: 0,13 (Vea 99,87 contra el resto en 100). Antes de este paso era 2,47. ⚠️ Esta brecha no
# detecta ninguno de los dos bugs de este paso —Vea y Coto no eran, juntas, el extremo en los dos
# sentidos—, los detecta el piso de cada cadena. Existe para que una regresión en otra cadena no se
# esconda.
# ⚠️ Lección de la Tarea 20: si se pone en rojo, antes de tocar la constante mirá si alguna cadena
# BAJÓ. Una brecha también sube cuando una sola cadena MEJORA —en la Tarea 20 pasó de 40,1 a 42,1 con
# un arreglo correcto, y acá esta misma brecha bajó de 2,47 a 0,13 por la misma razón—, y el número
# agregado no distingue un caso del otro. El detalle por cadena, sí.
BRECHA_SIN_NUMERO_EQUIVOCADO_MAX = 0.2


def test_ninguna_cadena_queda_abajo_de_su_piso_sin_numero_equivocado():
    m = _sin_numero_equivocado()
    bajo = {c: f"{p:.2f}% de {n}" for c, (p, n) in m.items() if p < PISO_SIN_NUMERO_EQUIVOCADO[c]}
    assert not bajo, f"bajo el piso: {bajo} — todas: { {c: round(p, 2) for c, (p, _n) in m.items()} }"


def test_la_brecha_sin_numero_equivocado_no_se_abre():
    m = {c: p for c, (p, _n) in _sin_numero_equivocado().items()}
    brecha = max(m.values()) - min(m.values())
    assert brecha <= BRECHA_SIN_NUMERO_EQUIVOCADO_MAX, \
        f"brecha {brecha:.2f} > {BRECHA_SIN_NUMERO_EQUIVOCADO_MAX} — por cadena: { {c: round(p, 2) for c, p in m.items()} }"


# ── (g) Vocabulario contable inglés y ml/cc mal escrito (Tarea 26, paso a2) ──────
# Las encontró el paso (a) y las dejó reportadas sin tocar. Captura de la canasta del 14/09.
#
# Vea, "Six Pack"/"Four Pack"/"Eight Pack"/"Sixpack": es vocabulario contable que faltaba —nada
# más—, y con eso la regla que YA EXISTE hace el resto: peso o volumen junto a un contable es
# ambiguo y NO publica métrica (decisión del 13/09, la de `_forma_ambigua`). Antes del fix
# "Gaseosa Sprite Six Pack 375 Ml" daba 375 ml (la botella suelta) por el precio de las seis; el
# resultado correcto es HUECO, no 2.250 ml — no se multiplica.
#
# Coto, "1,75 Ml" / "2.25cc": el título está mal escrito. Leerlos como 1,75 L / 2,25 L sería
# inventar un dato que la primera regla invariable de CLAUDE.md prohíbe. El resultado correcto
# también es HUECO. Lo que había que resolver es que publicaban 1,75 ml / 2,25 ml: un número
# equivocado por 1000×, no una fracción.
#
# Censo de las 22 filas reales de "pack" + peso/volumen que la captura de la canasta tiene en Vea y
# Coto (`tests/medir_unidad_pedida.py`, §MEDIR PRIMERO del checkpoint). 21 caen en las dos decisiones
# de arriba y quedan abajo, en rojo antes del fix con el número que daba al lado. De esas, el piso
# "sin número equivocado" (arriba) solo mide 19: a 3 ("7 Up ... Four Pack") el lector de referencia
# `leer_titulo` les lee el "7" de la marca como "número suelto" y las excluye de esa medición —ni
# suman ni restan ahí—, pero en producción el fix las corrige igual: se prueban acá, no arriba.
#
# La 22ª —"Agua Mineral Con Gas Eco De Los Andes 1,5 L - Pack 6"— es una TERCERA forma que ninguna
# de las dos decisiones cubre: el "6" va después de "Pack" sin una "x" y sin palabra de envase
# detrás ("6 pack" o "pack x 6" ya se reconocían; "pack 6" pelado, no). Ninguna de las dos decisiones
# de la Tarea 26 la nombra — PARÉ, queda sin tocar y documentada, no decidida acá.
ANTES_DEL_FIX = {
    ("Vea", "Cerveza Schneider Lata 710cc Four Pack"): ("volumen", 710.0),
    ("Vea", "Cerveza Heineken Lata 710cc Four Pack"): ("volumen", 710.0),
    ("Vea", "Agua Mineral Villavicencio S/g X 2 Lt. Six Pack."): ("volumen", 2000.0),
    ("Vea", "Agua Mineral Kin C/g  X 1.5  Lt. Four Pack."): ("volumen", 1500.0),
    ("Vea", "Agua Mineral Bell´s Con Gas Six Pack  Pet 1.5 Lt."): ("volumen", 1500.0),
    ("Vea", "Agua Mineral Eco De Los Andes S/g X 2 Lt. Six Pack."): ("volumen", 2000.0),
    ("Vea", "Agua Mineral Villa Del Sur S/g  X 1.5 Lt. Six Pack."): ("volumen", 1500.0),
    ("Vea", "Gaseosa Sprite Six Pack 375 Ml"): ("volumen", 375.0),
    ("Vea", "Gaseosa Fanta Six Pack 375 Ml"): ("volumen", 375.0),
    ("Vea", "Eight Pack De Gaseosa Coca Cola X 2.25 Lt."): ("volumen", 2250.0),
    ("Vea", "Gaseosa Pepsi Cola Six Pack  Lat 354 Cc."): ("volumen", 354.0),
    ("Vea", "Gaseosa Quatro Pom  X 354 Cc. Six Pack."): ("volumen", 354.0),
    ("Vea", "Gaseosa Coca Cola Vidrio Six Pack X 237 Cc"): ("volumen", 237.0),
    ("Vea", "Gaseosa Coca Cola Sin Azúcares Pet 600cc Six Pack"): ("volumen", 600.0),
    ("Vea", "Gaseosa Coca Cola Light Vidrio 237 Cc Six Pack"): ("volumen", 237.0),
    ("Vea", "Gaseosa Coca Cola Sin Azúcares Lata 354cc Six Pack"): ("volumen", 354.0),
    ("Vea", "Gaseosa Zero Sixpack De 237 Ml C/u Coca Cola"): ("volumen", 237.0),
    ("Vea", "Gaseosa Manzana 375ml Sixpack Fanta"): ("volumen", 375.0),
    ("Vea", "Gaseosa 7 Up Lima Limon 1.5 Lt Four Pack"): ("volumen", 1500.0),
    ("Vea", "Gaseosa 7 Up Lima Limon Botella 2 Lt Four Pack"): ("volumen", 2000.0),
    ("Vea", "Gaseosa 7 Up Light Lima Limon Botella 1.5 Lt Four Pack"): ("volumen", 1500.0),
    ("Coto", "Coca-Cola Sabor Liviano 1,75 Ml"): ("volumen", 1.75),
    ("Coto", "Gaseosa Sabor Cola CUNNINGTON Suave 2.25cc"): ("volumen", 2.25),
}
# La tercera forma ("pack N" pelado), sin decisión: se documenta con la métrica de HOY, que sigue
# siendo la de antes. Si este test se cae, alguien decidió esa forma — no lo cambies sin decisión.
PACK_PELADO_SIN_DECISION = {
    ("Vea", "Agua Mineral Con Gas Eco De Los Andes 1,5 L - Pack 6"): ("volumen", 1500.0),
}


def _por_titulo_canasta(cadena, titulo):
    filas = [f for (c, t, _p), f in _filas_canasta().items() if c == cadena and t == titulo]
    assert filas, f"{cadena}: {titulo!r} no está en la captura de la canasta — la lista está mal escrita"
    return filas


def test_censo_de_la_captura_es_22_filas():
    """Si la captura cambia, este test avisa antes que los de abajo."""
    import re as _re
    PACK_O_ML_RE = _re.compile(
        r"\b(two|four|six|eight|twelve)\s*pack\b|\bsixpack\b|\bpack\s*[-x]?\s*\d+\b"
        r"|\d[.,]\d+\s*(ml|cc)\b", _re.IGNORECASE)
    en_vea_coto = {(c, t) for (c, t, _p) in _filas_canasta()
                   if c in ("Vea", "Coto") and PACK_O_ML_RE.search(t)}
    esperadas = set(ANTES_DEL_FIX) | set(PACK_PELADO_SIN_DECISION)
    assert esperadas <= en_vea_coto, f"faltan en la captura: {esperadas - en_vea_coto}"


def test_los_packs_en_ingles_y_el_ml_mal_escrito_quedan_sin_metrica():
    """Las 19 de Vea + 2 de Coto: antes del fix daban el número de ANTES_DEL_FIX; ahora, hueco."""
    fallos = []
    for (c, t), antes in ANTES_DEL_FIX.items():
        for f in _por_titulo_canasta(c, t):
            pu = _metrica(f["of"])
            if pu is not None:
                fallos.append(f"{c}: {t!r} → {_txt(pu)} (antes {antes}, esperado sin métrica)")
    assert not fallos, (f"{len(fallos)} de {len(ANTES_DEL_FIX)} siguen publicando un número:\n   "
                        + "\n   ".join(fallos))


def test_el_pack_pelado_sin_decision_no_se_tocó():
    """
    "Pack 6" sin "x" y sin palabra de envase detrás es una tercera forma que ninguna decisión de
    la Tarea 26 cubre — PARÉ, no se resuelve acá. Este test documenta el estado actual (sigue mal),
    no lo pide: si se cae, alguien decidió la forma y hay que actualizar el comentario y esta tabla.
    """
    fallos = []
    for (c, t), (tipo, base) in PACK_PELADO_SIN_DECISION.items():
        for f in _por_titulo_canasta(c, t):
            pu = _metrica(f["of"])
            if not (pu and pu["tipo"] == tipo and _cerca(pu["cantidad_base"], base)):
                fallos.append(f"{c}: {t!r} → {_txt(pu)} (se esperaba sin cambios: {tipo} {base})")
    assert not fallos, "cambió sin que nadie decidiera la forma 'pack N' pelada:\n   " + "\n   ".join(fallos)


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in list(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn(); print(f"  PASS  {nombre}")
            except Exception as e:
                fallos += 1; print(f"  FAIL  {nombre}: {type(e).__name__}: {e}")
    print(f"\n{'TODO OK' if not fallos else str(fallos)+' FALLOS'}")
    raise SystemExit(1 if fallos else 0)
