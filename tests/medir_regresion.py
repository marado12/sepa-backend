"""
Conteo agregado sobre las 5 fixtures reales de tests/fixtures/: reproduce la
medición de claude/PRUEBA-MATCHING-REAL.md (Vea 83/83 ofertas con descuento falso,
Carrefour 0 Teasers leídos, Coto 0 productos) para confirmar que el commit 4523893
la corrigió, sin tocar el matcher ni el scoring — solo `FuenteVTEX._a_oferta` y
`FuenteCoto._parsear`, que son los mismos que corren en producción.

Sin red. Correr como módulo desde sepa_backend/:
    venv\\Scripts\\python.exe -m tests.medir_regresion
"""
import json
import statistics

from fuentes import FuenteVTEX
from tests.evaluar_matching import FIX, a_producto_vtex, ofertas_coto_de_filas

FIXTURES = {
    "Carrefour": "carrefour.json",
    "Día": "dia.json",
    "Vea": "vea.json",
    "Chango Más": "changomas.json",
    "Coto": "coto_estructura_real.json",
}


def ofertas_de_fixture(cadena, archivo):
    p = FIX / archivo
    if not p.exists():
        print(f"  ⚠ falta {archivo}: {cadena} queda fuera de la medición")
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for q, filas in data.items():
        if q.startswith("__"):
            continue
        if cadena == "Coto":
            out.extend(ofertas_coto_de_filas(filas))
        else:
            for fila in filas:
                of = FuenteVTEX._a_oferta(cadena, a_producto_vtex(fila))
                if of:
                    out.append(of)
    return out


def medir(cadena, ofertas):
    if ofertas is None:
        return None
    total = len(ofertas)
    con_desc = [o for o in ofertas if o.tiene_oferta]
    con_promo = [o for o in ofertas if o.promos]
    mediana = statistics.median(o.descuento_pct for o in con_desc) if con_desc else None
    return {
        "cadena": cadena, "ofertas": total, "con_descuento": len(con_desc),
        "descuento_mediano": mediana, "con_promo": len(con_promo),
    }


if __name__ == "__main__":
    print("=" * 88)
    print("MEDICIÓN AGREGADA sobre fixtures reales — claude/PRUEBA-MATCHING-REAL.md")
    print("=" * 88)
    for cadena, archivo in FIXTURES.items():
        r = medir(cadena, ofertas_de_fixture(cadena, archivo))
        if not r:
            continue
        med = f"{r['descuento_mediano']:.1f}%" if r["descuento_mediano"] is not None else "-"
        print(f"{r['cadena']:<12} ofertas={r['ofertas']:<4} "
              f"con_descuento={r['con_descuento']:<4} descuento_mediano={med:<7} "
              f"con_promo={r['con_promo']}")
