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
from precios_vtex import precio_unitario
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
