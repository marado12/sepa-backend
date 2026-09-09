"""
Evalúa el matching real de /api/comparar contra respuestas reales de las cadenas.

No usa red: consume las fixtures capturadas en fixtures/*.json con el navegador.
El parseo (`FuenteVTEX._a_oferta`), el scoring (`precios_vtex.puntuar`) y el
pipeline (`buscar_precios_online`) son EXACTAMENTE los del backend.
"""
import json, sys
from pathlib import Path

from fuentes import FuenteVTEX, Oferta, Resultado
import precios_vtex
from precios_vtex import buscar_precios_online, puntuar
from helpers_main import normalizar, _extraer_cantidades_desc

FIX = Path(__file__).parent / "fixtures"

CAMPOS = ["productName", "brand", "ean", "Price", "ListPrice", "PriceWithoutDiscount",
          "IsAvailable", "AvailableQuantity", "TeasersReales", "TeasersLegacy"]

# Los 6 ítems, tal como salen de CANASTA_DEFAULT en main.py.
CANASTA = [
    {"nombre": "Leche entera",    "cantidad": 4, "unidad": "litro",  "categoria": "Lácteos"},
    {"nombre": "Harina 000",      "cantidad": 2, "unidad": "kg",     "categoria": "Secos"},
    {"nombre": "Pollo entero",    "cantidad": 2, "unidad": "kg",     "categoria": "Carnes"},
    {"nombre": "Carne picada",    "cantidad": 1, "unidad": "kg",     "categoria": "Carnes"},
    {"nombre": "Papel higienico", "cantidad": 1, "unidad": "pack",   "categoria": "Higiene"},
    {"nombre": "Gaseosa cola",    "cantidad": 3, "unidad": "litro",  "categoria": "Bebidas"},
]


def a_producto_vtex(fila, teasers_reales=False):
    """Rearma la forma VTEX cruda para que la parsee `_a_oferta` sin tocarla."""
    n, marca, ean, price, lista, pwd, disp, qty, t_real, t_legacy = fila
    # El código actual lee t["name"]; la respuesta real trae "<Name>k__BackingField".
    teasers = ([{"name": x} for x in t_real] if teasers_reales
               else [{"<Name>k__BackingField": x} for x in t_real])
    return {
        "productName": n, "brand": marca,
        "items": [{"ean": ean, "sellers": [{"commertialOffer": {
            "Price": price, "ListPrice": lista, "PriceWithoutDiscount": pwd,
            "IsAvailable": disp, "AvailableQuantity": qty, "Teasers": teasers}}]}],
    }


def cargar(cadenas, teasers_reales=False):
    """{cadena: {consulta: [Oferta]}} usando el parser real del backend."""
    out = {}
    for cadena, archivo in cadenas.items():
        p = FIX / archivo
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        porq = {}
        for q, filas in data.items():
            if q.startswith("__"):
                continue
            ofertas = []
            for fila in filas:
                of = FuenteVTEX._a_oferta(cadena, a_producto_vtex(fila, teasers_reales))
                if of:
                    ofertas.append(of)
            porq[q] = ofertas
        out[cadena] = porq
    return out


class FuenteFixture:
    """Implementa el protocolo Fuente leyendo de las fixtures."""
    nombre = "fixture"

    def __init__(self, datos):
        self.datos = datos

    def cadenas_soportadas(self):
        return sorted(self.datos)

    def buscar(self, query, cadenas=None):
        objetivo = [c for c in (cadenas or self.cadenas_soportadas()) if c in self.datos]
        res = Resultado(consultadas=objetivo)
        for c in objetivo:
            res.ofertas.extend(self.datos[c].get(query, []))
        return res


def informe(datos, cadenas, titulo):
    print("=" * 78)
    print(titulo, "|", ", ".join(cadenas))
    print("=" * 78)
    fuente = FuenteFixture(datos)
    precios, meta = buscar_precios_online(
        CANASTA, cadenas, fuente, normalizar, _extraer_cantidades_desc)

    for item in CANASTA:
        nom = item["nombre"]
        print(f"\n■ {nom}  (pide {item['cantidad']} {item['unidad']})")
        hubo = False
        for c in cadenas:
            key = (c, nom)
            ofs = datos.get(c, {}).get(nom, [])
            if key in precios:
                hubo = True
                d = precios[key]
                vivas = [o for o in ofs if o.disponible and o.precio > 0]
                elegido = max(
                    ((puntuar(item, o, normalizar, _extraer_cantidades_desc), o) for o in vivas),
                    key=lambda t: (t[0], -t[1].precio))[1]
                barata = min(vivas, key=lambda o: o.precio)
                if barata.precio < elegido.precio * 0.85:
                    d["_mas_barata"] = barata
                p100 = d.get("precio_por_100u")
                extra = f" | {p100['label']} {p100['valor']}" if p100 else ""
                print(f"   {c:<11} ${d['precio_min']:>9,.2f}  s={d['match_score']:.2f}  "
                      f"{elegido.producto[:52]}{extra}")
                if d.get("promos"):
                    print(f"   {'':11} promos leídas: {d['promos']}")
            else:
                cand = sorted(
                    ((puntuar(item, o, normalizar, _extraer_cantidades_desc), o)
                     for o in ofs if o.disponible and o.precio > 0),
                    key=lambda t: -t[0])[:1]
                if cand:
                    s, o = cand[0]
                    print(f"   {c:<11} SIN MATCH (mejor candidato s={s:.2f}: {o.producto[:45]})")
                elif ofs:
                    print(f"   {c:<11} SIN MATCH ({len(ofs)} ofertas, ninguna disponible)")
                else:
                    print(f"   {c:<11} SIN DATOS")
        if not hubo:
            print("   → el optimizador no recibe precio para este ítem en ninguna cadena")

    print(f"\n-- meta: {meta['productos_con_precio']}/{meta['productos_pedidos']} productos "
          f"con precio | cobertura cadenas {meta['cobertura_cadenas_pct']}%")
    if meta.get("aviso"):
        print(f"-- aviso al usuario: {meta['aviso']}")
    return precios, meta


def top_candidatos(datos, cadena, nombre_item, n=8):
    item = next(i for i in CANASTA if i["nombre"] == nombre_item)
    ofs = datos[cadena].get(nombre_item, [])
    filas = sorted(((puntuar(item, o, normalizar, _extraer_cantidades_desc), o) for o in ofs),
                   key=lambda t: -t[0])[:n]
    print(f"\n### Candidatos de '{nombre_item}' en {cadena} (umbral {precios_vtex.UMBRAL_MATCH})")
    for s, o in filas:
        marca = "OK " if s >= precios_vtex.UMBRAL_MATCH else "-- "
        print(f"  {marca} s={s:.3f}  ${o.precio:>9,.2f}  {o.producto[:60]}")


if __name__ == "__main__":
    CAD = {"Carrefour": "carrefour.json", "Día": "dia.json", "Vea": "vea.json",
           "Chango Más": "changomas.json", "Coto": "coto.json"}
    datos = cargar(CAD)
    disponibles = [c for c in CAD if c in datos]
    if len(sys.argv) > 1 and sys.argv[1] == "top":
        for it in ("Leche entera", "Papel higienico", "Gaseosa cola", "Carne picada"):
            top_candidatos(datos, "Carrefour", it)
    else:
        informe(datos, disponibles, "MATCHING REAL")
