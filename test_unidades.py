"""
Vocabulario de unidades y cobertura de la métrica por cadena. Sin red.

POR QUÉ EXISTE ESTE ARCHIVO. Es la tercera vez que una lista de constantes se
arma mirando los datos de UNA cadena y falla en otra:

  1. `NO_SUPERMERCADO` con las categorías de Carrefour → dejó pasar 1.599
     categorías de Chango Más (mascotas, autos, indumentaria).
  2. El vocabulario de unidades con la forma de escribir de Carrefour y Día →
     no conocía "1 Ltr" ni "400 Grm" de Coto, ni los metros del papel higiénico.
     90 de 442 productos sin métrica, 54 de ellos papel higiénico — justo el
     producto del ítem 1.1.
  3. Las fixtures inventadas del bug de los Teasers: 73 tests en verde contra
     datos que nadie había visto.

El test de cobertura POR CADENA es lo que hace visible esa forma de falla: un
promedio global la esconde.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import main
from precios_vtex import UMBRAL_MATCH, precio_unitario, puntuar
from tests.evaluar_matching import CANASTA, cargar
from tests.medir_regresion import FIXTURES, ofertas_de_fixture

# Medido el 11/09/2026 sobre las 5 fixtures reales, después de cerrar el
# vocabulario: Carrefour 97.0 · Día 97.2 · Vea 92.8 · Chango Más 98.7 · Coto 82.0
PISO_POR_CADENA = 75.0      # una cadena sola no puede caerse por debajo de esto
PISO_GLOBAL = 90.0
BRECHA_MAX = 25.0           # entre la mejor y la peor cadena


def _cobertura() -> dict[str, float]:
    """% de productos con métrica de contenido, por cadena, sobre las fixtures."""
    out = {}
    for cadena, archivo in FIXTURES.items():
        n = con = 0
        for of in (ofertas_de_fixture(cadena, archivo) or []):
            desc = main.normalizar(of.producto or "")
            if not desc:
                continue
            n += 1
            if main._extraer_cantidades_desc(desc):
                con += 1
        if n:
            out[cadena] = round(100 * con / n, 1)
    return out


# ── Vocabulario: las formas que cada cadena usa de verdad ────────
def test_las_unidades_de_coto_se_entienden():
    """Coto escribe "1 Ltr" y "400 Grm"; Carrefour y Día, "1 l" y "400 gr"."""
    assert main._extraer_cantidades_desc("leche entera 3% casanto 1 ltr") == [(1000.0, "volumen")]
    assert main._extraer_cantidades_desc("bocaditos congelados coto uni 400 grm") == [(400.0, "peso")]


def test_el_papel_higienico_se_mide_en_metros():
    """
    El caso del ítem 1.1. Sin el tipo `longitud` este producto no tenía métrica
    de ninguna clase, así que el rediseño que compara por unidad de contenido
    no podía compararlo — justo al producto que lo motiva.
    """
    assert main._extraer_cantidades_desc(
        "papel higienico hoja simple carrefour essential 4 x 80 m.") == [(320.0, "longitud")]


def test_un_pack_no_se_lee_como_una_unidad_sola():
    """
    "4 u. x 80 m." son 320 m, no 80. Leerlo como 80 da un $/m CUATRO VECES más
    alto, y un precio equivocado el usuario no lo puede detectar.
    """
    cants = main._extraer_cantidades_desc("papel higienico elegante 4 u. x 80 m.")
    assert cants[0] == (320.0, "longitud"), cants


def test_el_contable_con_punto_se_entiende():
    """Carrefour escribe "18 u." y "4 u."."""
    assert main._extraer_cantidades_desc("albondigas union ganadera 18 u.") == [(18.0, "count")]


def test_una_u_suelta_no_inventa_una_cantidad():
    """Aceptar "u" sin punto haría matchear cualquier cosa."""
    assert main._extraer_cantidades_desc("pack u de algo") == []


# ── Presentación ─────────────────────────────────────────────────
def test_longitud_se_muestra_por_metro():
    """Decisión de producto (11/09): $/m, no $/100 m."""
    pu = precio_unitario(5899.0, 320.0, "longitud", "PH Elegante 4x80m")
    assert pu["label"] == "$/m"
    assert pu["valor"] == 18.43          # el número de la tabla de CONTEXTO.md
    assert pu["unidad_base"] == "m"


def test_el_dato_va_separado_de_como_se_muestra():
    """
    `precio_base` es con lo que se compara; `valor`/`label` son presentación.
    Cambiar la escala no puede cambiar lo que compara el optimizador.
    """
    pu = precio_unitario(2490.0, 1000.0, "volumen", "Leche 1 L")
    assert pu["precio_base"] == 2.49     # $/ml
    assert pu["valor"] == 249.0          # $/100ml, solo para mostrar


def test_sin_cantidad_no_se_inventa_metrica():
    assert precio_unitario(100.0, 0.0, "peso", "x") is None
    assert precio_unitario(100.0, 5.0, "tipo_raro", "x") is None


# ── Cobertura por cadena: el guard de la forma de falla ──────────
def test_ninguna_cadena_queda_abajo_del_piso():
    cob = _cobertura()
    bajas = {c: p for c, p in cob.items() if p < PISO_POR_CADENA}
    assert not bajas, f"cobertura de métrica por debajo de {PISO_POR_CADENA}%: {bajas} (todas: {cob})"


def test_la_cobertura_global_se_sostiene():
    cob = _cobertura()
    glob = round(sum(cob.values()) / len(cob), 1)
    assert glob >= PISO_GLOBAL, f"cobertura global {glob}% < {PISO_GLOBAL}% — {cob}"


def test_ninguna_cadena_queda_muy_lejos_de_la_mejor():
    """
    El guard que importa. Un vocabulario armado mirando una sola cadena no baja
    el promedio: hunde a UNA cadena y el promedio lo disimula.
    """
    cob = _cobertura()
    brecha = max(cob.values()) - min(cob.values())
    assert brecha <= BRECHA_MAX, (
        f"{brecha:.1f} puntos entre la mejor y la peor cadena (máx {BRECHA_MAX}): {cob}. "
        f"Suele significar que una constante se armó con los datos de una sola cadena.")


# ── La SEGUNDA métrica: el matching ──────────────────────────────
#
# Hallazgo 17. Un cambio en el vocabulario de extracción de cantidades mueve DOS
# cosas —la cobertura de métrica y el matching— y los guards de arriba sólo miden
# la primera. El paso 1 subió la cobertura de Carrefour de 72,3% a 97,0%, el CI
# lo firmó en verde, y al mismo tiempo los candidatos de papel higiénico que
# superaban el umbral caían de 24 a 4 en esa misma cadena, con el representante
# pasando a ser un portarrollos de plástico. Nadie lo miró.
#
# Por eso el guard va en DOS capas, y la segunda es la que importa:
#   capa 1 — tasa de aceptación por cadena (cuántos candidatos pasan el umbral)
#   capa 2 — SNAPSHOT del representante elegido por (ítem, cadena)
#
# ⚠️ La capa 1 sola NO habría cazado el bug de la Tarea 20: la brecha entre la
# mejor y la peor cadena era 40,1 puntos antes del arreglo y 42,1 después — o
# sea que *empeoró* al arreglar. El bug fue "cambió el que gana", no "bajaron
# los candidatos". La capa 1 queda para la caída catastrófica; la capa 2 es la
# que detecta que el matcher cambió de opinión.

PISO_ACEPTACION = 35.0          # una cadena sola no puede caer por debajo
BRECHA_ACEPTACION_MAX = 50.0

# Medido el 12/09 después del hotfix de 'pack': Carrefour 78,7 · Chango Más 85,3
# · Coto 43,2 · Día 83,3 · Vea 83,1. Coto es el piso por motivos propios, no por
# este bug — el umbral no se calibra mirando su caso.
_CACHE_MATCH = {}


def _estado_matching():
    """(tasa por cadena, ganador por (ítem, cadena)) sobre las 5 fixtures."""
    if not _CACHE_MATCH:
        datos = cargar(FIXTURES)
        tasa = {c: [0, 0] for c in datos}
        ganador = {}
        for item in CANASTA:
            nom = item["nombre"]
            for c in sorted(datos):
                ofs = [o for o in datos[c].get(nom, []) if o.disponible and o.precio > 0]
                arriba = []
                for o in ofs:
                    s = puntuar(item, o, main.normalizar, main._extraer_cantidades_desc)
                    tasa[c][1] += 1
                    if s >= UMBRAL_MATCH:
                        tasa[c][0] += 1
                        arriba.append((s, o))
                if arriba:
                    _s, o = max(arriba, key=lambda f: (f[0], -f[1].precio))
                    ganador[(nom, c)] = (round(o.precio, 2), o.producto)
                else:
                    ganador[(nom, c)] = None
        _CACHE_MATCH["tasa"] = {c: round(100.0 * a / t, 1) if t else 0.0
                                for c, (a, t) in tasa.items()}
        _CACHE_MATCH["ganador"] = ganador
    return _CACHE_MATCH["tasa"], _CACHE_MATCH["ganador"]


def test_ninguna_cadena_cae_por_debajo_del_piso_de_aceptacion():
    tasa, _ = _estado_matching()
    bajas = {c: p for c, p in tasa.items() if p < PISO_ACEPTACION}
    assert not bajas, (
        f"tasa de aceptación por debajo de {PISO_ACEPTACION}%: {bajas} (todas: {tasa})")


def test_la_brecha_de_aceptacion_entre_cadenas_no_se_dispara():
    """Capa 1. Guard de caída catastrófica, no del cambio de ganador."""
    tasa, _ = _estado_matching()
    brecha = max(tasa.values()) - min(tasa.values())
    assert brecha <= BRECHA_ACEPTACION_MAX, (
        f"{brecha:.1f} puntos entre la mejor y la peor cadena "
        f"(máx {BRECHA_ACEPTACION_MAX}): {tasa}")


# Capa 2. Snapshot de quién gana. Si un cambio mueve un representante, esto se
# pone en rojo y hay que actualizarlo A MANO, mirando si el nuevo ganador es
# mejor o peor. Esa fricción es el punto: es exactamente lo que faltó el 11/09.
GANADORES_ESPERADOS = {
    ("Leche entera", "Carrefour"): (2529.00, "Leche entera larga vida Ilolay 1 l."),
    ("Leche entera", "Chango Más"): (1949.00, "Leche Entera Casanto 1 L"),
    ("Leche entera", "Coto"): (1899.00, "Leche Entera COTO Sachet 1 L"),
    ("Leche entera", "Día"): (1790.00, "Leche Entera DIA Sachet 1 Lt."),
    ("Leche entera", "Vea"): (2390.00, "Leche Entera 1 Lts Cuisine y Co"),
    ("Harina 000", "Carrefour"): (749.00, "Harina de trigo Bulnez 000 1 kg"),
    ("Harina 000", "Chango Más"): (919.00, "Harina Check 000 1 Kg"),
    ("Harina 000", "Coto"): (919.00, "Harina De Trigo 000 Coto 1kg"),
    ("Harina 000", "Día"): (905.00, "Harina 000  Caserita 1 Kg."),
    ("Harina 000", "Vea"): (790.00, "Harina 000 1 Kg Maxima"),
    ("Pollo entero", "Carrefour"): (3979.00, "Pollo entero congelado x kg"),
    ("Pollo entero", "Chango Más"): (3189.00, "Pollo Entero Fresco 3 Kg"),
    ("Pollo entero", "Coto"): None,
    ("Pollo entero", "Día"): (4390.00, "Pollo Entero x Kg."),
    ("Pollo entero", "Vea"): None,
    ("Carne picada", "Carrefour"): (7990.00, "Carne picada Swift congelada 500 grs"),
    ("Carne picada", "Chango Más"): (6799.00, "Carne Picada Swift Congelada 500 G"),
    ("Carne picada", "Coto"): None,
    ("Carne picada", "Día"): (13000.00, "Carne Picada de Nalga x 500 Gr."),
    ("Carne picada", "Vea"): (21305.00, "Carne Vacuna Picada Magra"),
    # Los tres que arregló la Tarea 20. Antes ganaban, en orden: un portarrollos
    # de $3.500, un "Hoja Simple 4u" sin métrica, y un Felpita de $13.750,99.
    ("Papel higienico", "Carrefour"): (5899.00, "Papel higiénico Elegante 4 u. x 80 m."),
    ("Papel higienico", "Chango Más"): (6729.00, "Papel Higiénico Elegante 4x30mts"),
    ("Papel higienico", "Coto"): (2420.99, "Papel Higiénico Boral Simple Hoja 4 rollos de 30m"),
    ("Papel higienico", "Día"): (1555.00, "Papel Higiénico Simple Hoja 50 mts 2 Ud."),
    ("Papel higienico", "Vea"): (2347.00, "Papel Higiénico Campanita Hoja Simple 4 U"),
    ("Gaseosa cola", "Carrefour"): (6050.00, "Gaseosa cola Pepsi Black pet 3 lts"),
    ("Gaseosa cola", "Chango Más"): (3389.00, "Gaseosa Cunnington Cola 3 L"),
    ("Gaseosa cola", "Coto"): (4940.00, "Gaseosa Coca-Cola Sabor Original  3 Lt"),
    ("Gaseosa cola", "Día"): (4537.50, "Gaseosa Cola Regular Pepsi 3 Lt."),
    ("Gaseosa cola", "Vea"): (1890.00, "Gaseosa Secco Cola 2,25 Lt"),
}


def test_el_representante_elegido_no_cambio_sin_que_nadie_lo_note():
    """
    Capa 2, la que caza el bug de la Tarea 20. Rojo si el matcher cambia de
    opinión sobre qué producto representa a un ítem en una cadena.
    """
    _, ganador = _estado_matching()
    distintos = []
    for clave, esperado in GANADORES_ESPERADOS.items():
        actual = ganador.get(clave)
        if actual != esperado:
            distintos.append(f"{clave[0]}/{clave[1]}:\n"
                             f"      esperado {esperado}\n"
                             f"      actual   {actual}")
    assert not distintos, (
        f"el matcher cambió de representante en {len(distintos)} caso(s). Mirá si "
        f"el nuevo es mejor o peor ANTES de actualizar el snapshot:\n   "
        + "\n   ".join(distintos))


def test_el_snapshot_cubre_todas_las_combinaciones():
    """Un snapshot incompleto da verde sobre lo que no mira."""
    _, ganador = _estado_matching()
    assert set(GANADORES_ESPERADOS) == set(ganador), (
        f"faltan o sobran combinaciones en el snapshot: "
        f"{set(GANADORES_ESPERADOS) ^ set(ganador)}")


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
