#!/usr/bin/env python3
"""
Snapshot diario del catálogo completo de cada cadena.

POR QUÉ EXISTE
--------------
Hoy `/api/comparar` consulta las tiendas EN VIVO: por cada ítem de la canasta
dispara una búsqueda full-text a cada cadena. Eso tiene cuatro problemas:

  1. Lento: 20 ítems × 5 cadenas = 100 requests HTTP mientras el usuario espera.
  2. Frágil: si una cadena timeoutea en ese momento, esa comparación sale coja.
  3. Ruidoso: la búsqueda full-text devuelve lo que el buscador del sitio quiera
     (buscando "leche entera" en Coto aparecen NESFIT y alimento para gatos).
  4. **No permite comparar por código de barras**, que es lo único que garantiza
     que dos cadenas están cotizando EL MISMO producto.

Con el catálogo completo bajado una vez por día, los cuatro desaparecen: el
matching pasa a ser un índice local, y el mismo EAN en dos cadenas es,
literalmente, el mismo producto.

LÍMITES DE LA API, MEDIDOS (09/09/2026, contra Carrefour)
---------------------------------------------------------
  * ventana `_to - _from` ≤ 50   → 50 productos por request
  * `_from` ≤ 2500               → 2500 productos por consulta como máximo,
                                   por eso se baja por SUBcategoría, no por categoría
  * header `resources: 0-49/1577` → total de resultados, permite paginar sin adivinar
  * 100% de los productos traen EAN
  * ~1,8 s por página de 50 · 1,25 MB crudo · 6 KB una vez reducido (121 B/producto)

USO
---
    python scripts/snapshot_catalogo.py --probe          # mide sin bajar nada
    python scripts/snapshot_catalogo.py --cadena Día     # una cadena
    python scripts/snapshot_catalogo.py                  # todas
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fuentes import UA, VTEX_BASES, FuenteVTEX, _nombres_teasers, _precio_lista  # noqa: E402

log = logging.getLogger("snapshot")

VENTANA = 50          # productos por request (techo duro de VTEX)
TOPE_CONSULTA = 2500  # `_from` máximo (techo duro de VTEX)
PAUSA = 0.4           # entre requests, para no castigar al sitio
SALIDA = Path(__file__).resolve().parent.parent / "data" / "snapshots"

# Categorías que no son de supermercado. Se saltean por defecto porque son la mitad
# del catálogo de Carrefour (electro 23k, juguetería 10k, hogar 19k) y ninguna sirve
# para comparar una canasta. Con --todo se bajan igual.
# Se compara por SUBCADENA sobre el nombre normalizado, no por igualdad: cada cadena
# nombra sus categorías distinto y la lista exacta sólo servía para Carrefour. En la
# primera corrida real de Día se colaron heladeras de $2.469.999 y microondas, porque
# sus categorías raíz no se llaman igual que las de Carrefour.
NO_SUPERMERCADO = (
    "electro", "tecnolog", "informatic", "celular", "telefon", "televis", "audio",
    "heladera", "lavarropa", "microonda", "aire acondicionado", "climatiza",
    "hogar", "mueble", "bazar", "decoracion", "blanco", "colchon",
    "indumentaria", "calzado", "moda", "textil",
    "juguet", "libreria", "libreria", "escolar",
    "automotor", "auto y moto", "neumatic", "ferreteria", "construccion", "pintureria",
    "aire libre", "ocio", "deporte", "camping", "pileta", "jardin",
    "gift card", "test category", "electrodomestic", "tiempo libre",
)


def _sin_acentos(texto: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", texto)
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower().strip()


def es_supermercado(nombre: str) -> bool:
    n = _sin_acentos(nombre)
    return not any(t in n for t in NO_SUPERMERCADO)


@dataclass
class Fila:
    cadena: str
    ean: Optional[str]
    nombre: str
    marca: Optional[str]
    categoria: str
    precio: float
    precio_lista: Optional[float]
    disponible: bool
    promos: list[str] = field(default_factory=list)


class Contador:
    def __init__(self):
        self.requests = 0
        self.errores = 0
        self.t0 = time.time()

    @property
    def seg(self) -> float:
        return round(time.time() - self.t0, 1)


def _sesion() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json"})
    return s


# ─────────────────────────────────────────────────────────────────
#  VTEX
# ─────────────────────────────────────────────────────────────────

def arbol_vtex(ses: requests.Session, base: str, cont: Contador) -> list[dict]:
    r = ses.get(f"{base}/api/catalog_system/pub/category/tree/3", timeout=(8, 30))
    cont.requests += 1
    r.raise_for_status()
    return r.json()


def _hojas(nodo: dict, camino: str = "") -> Iterator[tuple[str, str]]:
    """
    Devuelve (ruta_fq, nombre_legible) de cada categoría a consultar.

    Se baja al nivel más profundo disponible: cuanto más chica la categoría, menos
    probable que choque contra el techo de 2500 productos por consulta.
    """
    camino = f"{camino}/{nodo['id']}" if camino else f"/{nodo['id']}"
    hijos = nodo.get("children") or []
    if not hijos:
        yield camino, nodo["name"]
        return
    for h in hijos:
        yield from _hojas(h, camino)


def sales_channel(ses: requests.Session, base: str, fq: str,
                  cont: Contador) -> Optional[str]:
    """
    Descubre el canal de venta de la tienda leyendo el `PriceToken` de un producto.

    POR QUÉ HACE FALTA. Sin canal explícito, la API devuelve el catálogo del canal
    por defecto, donde en algunas tiendas casi nada tiene stock: la primera corrida
    real de Vea trajo 888 productos y sólo 66 con stock (7%), con precios viejos
    (mediana $400 contra $12.499 de los disponibles). Filtrando por el canal correcto
    la misma categoría devuelve 42 de 42 con stock. El navegador acierta porque manda
    cookies de sesión; `requests` no, así que hay que decirlo explícito.

    El `PriceToken` es un JWT cuyo payload trae `salesChannel` — se lee sin validar
    firma, es un dato de la respuesta, no una credencial.
    """
    import base64
    try:
        productos, _ = _pagina_vtex(ses, base, fq, 0, cont, pagina=5)
    except Exception as e:                        # noqa: BLE001
        log.warning("no se pudo detectar el canal: %s", e)
        return None
    for p in productos:
        for it in (p.get("items") or []):
            for sel in (it.get("sellers") or []):
                tok = (sel.get("commertialOffer") or {}).get("PriceToken")
                if not tok or tok.count(".") != 2:
                    continue
                cuerpo = tok.split(".")[1]
                cuerpo += "=" * (-len(cuerpo) % 4)
                try:
                    datos = json.loads(base64.urlsafe_b64decode(cuerpo)).get("data", {})
                except Exception:                 # noqa: BLE001
                    continue
                sc = datos.get("salesChannel")
                if sc:
                    return str(sc)
    return None


def _pagina_vtex(ses, base, fq, desde, cont, sc: Optional[str] = None,
                 pagina: int = VENTANA) -> tuple[list[dict], Optional[int]]:
    url = f"{base}/api/catalog_system/pub/products/search"
    params = [("fq", f"C:{fq}/"), ("_from", desde), ("_to", desde + pagina - 1)]
    if sc:
        # Filtra EN EL ORIGEN por disponibilidad en ese canal: en Aceites Especiales
        # de Vea baja de 579 declarados a los 42 que realmente se pueden comprar.
        # No es sólo ahorro de tiempo — sin esto el snapshot guarda precios que no
        # corresponden al canal donde el usuario compra.
        params.append(("fq", f"isAvailablePerSalesChannel_{sc}:1"))
    r = ses.get(url, params=params, timeout=(8, 40))
    cont.requests += 1
    if r.status_code == 429:
        espera = int(r.headers.get("Retry-After", 5))
        log.warning("429 — esperando %ss", espera)
        time.sleep(min(espera, 30))
        return [], None
    r.raise_for_status()
    total = None
    if (res := r.headers.get("resources")) and "/" in res:
        try:
            total = int(res.rsplit("/", 1)[1])
        except ValueError:
            pass
    data = r.json()
    return (data if isinstance(data, list) else []), total


def bajar_vtex(cadena: str, base: str, cont: Contador, todo: bool,
               limite_cat: Optional[int] = None,
               sin_stock: bool = False) -> list[Fila]:
    ses = _sesion()
    filas: list[Fila] = []
    vistos: set[str] = set()
    descartados = [0]

    raiz = arbol_vtex(ses, base, cont)
    if not todo:
        fuera = [c["name"] for c in raiz if not es_supermercado(c["name"])]
        raiz = [c for c in raiz if es_supermercado(c["name"])]
        if fuera:
            log.info("[%s] fuera del snapshot: %s", cadena, ", ".join(fuera))

    categorias = [h for c in raiz for h in _hojas(c)]
    if not todo:
        # También por hoja: hay cadenas que cuelgan "Heladeras" de una raíz que sí
        # es de supermercado, y ahí se colaban productos de $2,4 millones.
        antes = len(categorias)
        categorias = [(fq, n) for fq, n in categorias if es_supermercado(n)]
        if antes != len(categorias):
            log.info("[%s] %d hojas descartadas por nombre", cadena, antes - len(categorias))
    log.info("[%s] %d categorías raíz · %d hojas a recorrer", cadena, len(raiz), len(categorias))
    if limite_cat:
        categorias = categorias[:limite_cat]

    sc = None if sin_stock else sales_channel(ses, base, categorias[0][0], cont) \
        if categorias else None
    log.info("[%s] canal de venta: %s", cadena, sc or "no detectado (sin filtro de stock)")

    for i, (fq, nombre) in enumerate(categorias, 1):
        desde, total = 0, None
        while desde < TOPE_CONSULTA:
            try:
                productos, t = _pagina_vtex(ses, base, fq, desde, cont, sc)
            except Exception as e:                       # noqa: BLE001
                cont.errores += 1
                log.warning("[%s] %s @%d: %s", cadena, nombre, desde, e)
                break
            if total is None:
                total = t
            if not productos:
                break
            for p in productos:
                of = FuenteVTEX._a_oferta(cadena, p)
                if not of:
                    continue
                # Navegar por categoría devuelve mucho producto discontinuado que la
                # búsqueda no muestra: en la primera corrida de Vea, 822 de 888 filas
                # eran sin stock, con precios viejos (mediana $400 contra $12.499 de
                # los disponibles). Guardarlos es peso muerto y riesgo de mostrar un
                # precio que no existe.
                if not sin_stock and not of.disponible:
                    descartados[0] += 1
                    continue
                clave = of.ean or f"~{of.producto}"
                if clave in vistos:
                    continue
                vistos.add(clave)
                filas.append(Fila(cadena, of.ean, of.producto, of.marca, nombre,
                                  of.precio, of.precio_lista, of.disponible, of.promos))
            desde += VENTANA
            if total is not None and desde >= total:
                break
            time.sleep(PAUSA)
        if i % 25 == 0:
            log.info("[%s] %d/%d categorías · %d productos · %ss",
                     cadena, i, len(categorias), len(filas), cont.seg)
    if descartados[0]:
        log.info("[%s] %d descartados por falta de stock", cadena, descartados[0])
    return filas


# ─────────────────────────────────────────────────────────────────
#  COTO (Oracle/Endeca — paginación con No/Nrpp)
# ─────────────────────────────────────────────────────────────────

def bajar_coto(cont: Contador, paginas_max: int = 200) -> list[Fila]:
    from fuentes import FuenteCoto

    ses = _sesion()
    filas: list[Fila] = []
    vistos: set[str] = set()
    # Coto no expone árbol JSON como VTEX: se recorre el buscador por letra/término.
    # Es menos prolijo que VTEX y por eso queda documentado como pendiente de mejora.
    for termino in ("almacen", "lacteos", "bebidas", "limpieza", "perfumeria",
                    "carnes", "verduleria", "panaderia", "congelados", "desayuno"):
        for pagina in range(paginas_max):
            try:
                r = ses.get("https://www.coto.com.ar/sitios/cdigi/categoria",
                            params={"Ntt": termino, "format": "json",
                                    "No": pagina * 24, "Nrpp": 24},
                            timeout=(8, 40), allow_redirects=True)
                cont.requests += 1
                r.raise_for_status()
                ofertas = FuenteCoto._parsear(r.json())
            except Exception as e:                       # noqa: BLE001
                cont.errores += 1
                log.warning("[Coto] %s p%d: %s", termino, pagina, e)
                break
            if not ofertas:
                break
            nuevas = 0
            for of in ofertas:
                clave = of.ean or f"~{of.producto}"
                if clave in vistos:
                    continue
                vistos.add(clave)
                nuevas += 1
                filas.append(Fila("Coto", of.ean, of.producto, of.marca, termino,
                                  of.precio, of.precio_lista, of.disponible, of.promos))
            if nuevas == 0:
                break
            time.sleep(PAUSA)
        log.info("[Coto] %s → %d productos acumulados", termino, len(filas))
    return filas


# ─────────────────────────────────────────────────────────────────
#  PROBE — mide sin bajar
# ─────────────────────────────────────────────────────────────────

def probe() -> int:
    """Responde: ¿desde acá se llega? ¿cuántos productos hay? ¿cuánto tardaría?"""
    print(f"{'cadena':<12}{'HTTP':>6}{'ms':>7}{'categorías':>12}{'comprables':>11}"
          f"{'requests':>10}{'estimado':>10}{'canal':>7}")
    print("-" * 75)
    total_general = 0
    for cadena, base in VTEX_BASES.items():
        cont = Contador()
        ses = _sesion()
        try:
            t0 = time.time()
            raiz = arbol_vtex(ses, base, cont)
            ms = int((time.time() - t0) * 1000)
            raiz = [c for c in raiz if es_supermercado(c["name"])]
            hojas = [h for c in raiz for h in _hojas(c) if es_supermercado(h[1])]
            # Muestra AL AZAR: con las primeras 40 hojas la estimación de Vea dio
            # 313.707 productos (las primeras eran las más densas). Con muestreo
            # aleatorio y semilla fija el número es comparable entre corridas.
            import random
            muestra = random.Random(20260909).sample(hojas, min(25, len(hojas)))
            sc = sales_channel(ses, base, hojas[0][0], cont) if hojas else None
            total = 0
            for fq, _ in muestra:
                try:
                    _, t = _pagina_vtex(ses, base, fq, 0, cont, sc)
                    total += t or 0
                except Exception:             # noqa: BLE001
                    cont.errores += 1
                time.sleep(0.15)
            estimado = int(total / max(len(muestra), 1) * len(hojas))
            reqs = estimado // VENTANA + len(hojas)
            total_general += estimado
            print(f"{cadena:<12}{'200':>6}{ms:>7}{len(hojas):>12}{estimado:>11,}"
                  f"{reqs:>10,}{reqs * (PAUSA + 1.0) / 60:>9.0f}m{(sc or '?'):>7}")
        except Exception as e:                # noqa: BLE001
            print(f"{cadena:<12}{'ERR':>6}  {type(e).__name__}: {str(e)[:38]}")
    print("-" * 75)
    print(f"{'TOTAL':<12}{'':>6}{'':>7}{'':>12}{total_general:>11,}")
    print(f"\nPeso estimado del snapshot: ~{total_general * 121 / 1e6:.1f} MB en JSON "
          f"reducido (bastante menos en parquet).")
    return 0


# ─────────────────────────────────────────────────────────────────

def guardar(filas: list[Fila], cadena: str) -> Path:
    SALIDA.mkdir(parents=True, exist_ok=True)
    hoy = date.today().isoformat()
    destino = SALIDA / f"{hoy}_{cadena.replace(' ', '_').lower()}.parquet"
    try:
        import pandas as pd
        df = pd.DataFrame([asdict(f) for f in filas])
        df["promos"] = df["promos"].apply(lambda x: "|".join(x) if x else "")
        df.to_parquet(destino, index=False, compression="zstd")
    except ImportError:
        destino = destino.with_suffix(".jsonl")
        with destino.open("w", encoding="utf-8") as fh:
            for f in filas:
                fh.write(json.dumps(asdict(f), ensure_ascii=False) + "\n")
    return destino


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", action="store_true",
                    help="mide alcance y volumen sin bajar el catálogo")
    ap.add_argument("--cadena", help="bajar sólo esta cadena")
    ap.add_argument("--todo", action="store_true",
                    help="incluir categorías que no son de supermercado")
    ap.add_argument("--limite-categorias", type=int,
                    help="cortar después de N categorías (para probar)")
    ap.add_argument("--sin-stock", action="store_true",
                    help="guardar también los productos sin stock (por defecto se descartan)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    if args.probe:
        return probe()

    objetivo = ({args.cadena: VTEX_BASES[args.cadena]} if args.cadena in VTEX_BASES
                else {} if args.cadena else dict(VTEX_BASES))
    resumen = []
    for cadena, base in objetivo.items():
        cont = Contador()
        log.info("=== %s ===", cadena)
        try:
            filas = bajar_vtex(cadena, base, cont, args.todo, args.limite_categorias,
                               args.sin_stock)
        except Exception as e:                # noqa: BLE001
            log.error("[%s] falló entera: %s", cadena, e)
            resumen.append((cadena, 0, cont.requests, cont.errores, cont.seg, "—"))
            continue
        destino = guardar(filas, cadena) if filas else None
        ean = sum(1 for f in filas if f.ean)
        log.info("[%s] %d productos · %d con EAN (%.1f%%)", cadena, len(filas), ean,
                 ean / len(filas) * 100 if filas else 0)
        resumen.append((cadena, len(filas), cont.requests, cont.errores, cont.seg,
                        destino.name if destino else "—"))

    if not args.cadena or args.cadena == "Coto":
        cont = Contador()
        log.info("=== Coto ===")
        filas = bajar_coto(cont)
        destino = guardar(filas, "Coto") if filas else None
        resumen.append(("Coto", len(filas), cont.requests, cont.errores, cont.seg,
                        destino.name if destino else "—"))

    print(f"\n{'cadena':<12}{'productos':>11}{'requests':>10}{'errores':>9}"
          f"{'seg':>8}  archivo")
    for r in resumen:
        print(f"{r[0]:<12}{r[1]:>11,}{r[2]:>10,}{r[3]:>9}{r[4]:>8.0f}  {r[5]}")
    print(f"\nSnapshot: {datetime.now().isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
