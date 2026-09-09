#!/usr/bin/env python3
"""
Mide los límites reales de la API de catálogo VTEX antes de escribir el adaptador.

Responde las tres preguntas que quedaron abiertas:
  1. ¿Cuál es la ventana máxima de _from/_to por request?
  2. ¿Hay un techo total de resultados paginables?
  3. ¿`sc` (sales channel) cambia el precio? → ¿se puede recuperar algo de la lógica por zona?
  4. ¿Cuál es el rate limit real?

Usa solo stdlib. Corré:   python3 scripts/medir_vtex.py
Opcional:                 python3 scripts/medir_vtex.py --cadena jumbo

IMPORTANTE: hace unas ~40 requests en total, espaciadas. No lo corras en loop.
"""

import argparse
import json
import ssl
import sys
import time
import urllib.error
import urllib.request

BASES = {
    "carrefour": "https://www.carrefour.com.ar",
    "jumbo":     "https://www.jumbo.com.ar",
    "disco":     "https://www.disco.com.ar",
    "vea":       "https://www.vea.com.ar",
    "dia":       "https://diaonline.supermercadosdia.com.ar",
    "masonline": "https://www.masonline.com.ar",
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

CTX = ssl.create_default_context()


def pedir(url: str, timeout: int = 20) -> dict:
    """Una request. Devuelve status, headers relevantes y el body ya parseado."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            raw = r.read()
            ms = round((time.perf_counter() - t0) * 1000)
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                return {"status": r.status, "ms": ms, "error": "respuesta no es JSON",
                        "preview": raw[:200].decode("utf-8", "replace")}
            return {
                "status": r.status,
                "ms": ms,
                # VTEX devuelve el total en este header: "resources 0-49/1234"
                "content_range": r.headers.get("resources") or r.headers.get("Content-Range"),
                "n": len(body) if isinstance(body, list) else None,
                "body": body,
            }
    except urllib.error.HTTPError as e:
        cuerpo = e.read()[:300].decode("utf-8", "replace")
        return {"status": e.code, "ms": round((time.perf_counter() - t0) * 1000),
                "error": f"HTTP {e.code} {e.reason}", "preview": cuerpo}
    except Exception as e:
        return {"status": None, "ms": round((time.perf_counter() - t0) * 1000),
                "error": f"{type(e).__name__}: {e}"}


def busq(base: str, q: str, desde: int, hasta: int, extra: str = "") -> str:
    return (f"{base}/api/catalog_system/pub/products/search/"
            f"?ft={q}&_from={desde}&_to={hasta}{extra}")


def precio_de(prod: dict):
    """Extrae (nombre, ean, Price, ListPrice, teasers) del primer item/seller."""
    try:
        it = prod["items"][0]
        of = it["sellers"][0]["commertialOffer"]
        return (prod.get("productName"), it.get("ean"), of.get("Price"),
                of.get("ListPrice"), [t.get("name") for t in of.get("Teasers", [])])
    except (KeyError, IndexError, TypeError):
        return (prod.get("productName"), None, None, None, [])


def seccion(t):
    print(f"\n{'=' * 66}\n{t}\n{'=' * 66}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cadena", default="carrefour", choices=sorted(BASES))
    ap.add_argument("--q", default="aceite", help="término de búsqueda")
    args = ap.parse_args()
    base = BASES[args.cadena]
    print(f"Midiendo {args.cadena} ({base}) — query '{args.q}'")

    # ── 1. Ventana máxima por request ──────────────────────────────────
    seccion("1. VENTANA MAXIMA DE _from/_to")
    print(f"{'ventana':>10} {'pedidos':>8} {'status':>7} {'recibidos':>10} {'ms':>6}  content-range")
    maximo = None
    for hasta in (9, 24, 49, 50, 99):
        r = pedir(busq(base, args.q, 0, hasta))
        pedidos = hasta + 1
        n = r.get("n")
        print(f"{'0-'+str(hasta):>10} {pedidos:>8} {str(r['status']):>7} "
              f"{str(n if n is not None else r.get('error','?'))[:10]:>10} {r['ms']:>6}  "
              f"{r.get('content_range') or ''}")
        if n:
            maximo = max(maximo or 0, n)
        time.sleep(1.5)
    print(f"\n>>> Máximo de productos devueltos en una request: {maximo}")

    # ── 2. Techo total de paginación ───────────────────────────────────
    seccion("2. TECHO TOTAL DE PAGINACION")
    for desde in (100, 1000, 2450, 2500, 5000):
        r = pedir(busq(base, args.q, desde, desde + 9))
        n = r.get("n")
        estado = f"{n} productos" if n is not None else r.get("error", "?")
        print(f"  _from={desde:<6} status={str(r['status']):<6} {estado}")
        if r.get("preview"):
            print(f"           {r['preview'][:150]}")
        time.sleep(1.5)

    # ── 3. ¿El sales channel cambia el precio? ─────────────────────────
    seccion("3. REGIONALIZACION VIA sales channel (sc)")
    print("Si el precio cambia entre canales, se puede recuperar algo de la lógica por zona.")
    ref = None
    for sc in (None, 1, 2, 3, 4):
        extra = "" if sc is None else f"&sc={sc}"
        r = pedir(busq(base, args.q, 0, 0, extra))
        etiqueta = "default" if sc is None else f"sc={sc}"
        if r.get("n"):
            nombre, ean, price, lista, teasers = precio_de(r["body"][0])
            print(f"  {etiqueta:<8} status={r['status']}  {str(nombre)[:38]:<38} "
                  f"Price={price}  ListPrice={lista}")
            if teasers:
                print(f"           teasers: {teasers}")
            if ref is None:
                ref = price
            elif price != ref:
                print(f"           *** PRECIO DISTINTO AL DEFAULT ({ref}) — hay regionalización ***")
        else:
            print(f"  {etiqueta:<8} status={r['status']}  {r.get('error', 'sin productos')}")
        time.sleep(1.5)

    # ── 4. Rate limit ──────────────────────────────────────────────────
    seccion("4. RATE LIMIT")
    print("20 requests seguidas sin pausa. Buscamos 429 o degradación de latencia.")
    lat, bloqueos = [], 0
    for i in range(20):
        r = pedir(busq(base, args.q, 0, 0))
        lat.append(r["ms"])
        if r["status"] == 429:
            bloqueos += 1
            print(f"  #{i+1}: 429 Too Many Requests  (retry-after: {r.get('retry_after')})")
        elif r["status"] != 200 and r["status"] != 206:
            print(f"  #{i+1}: status={r['status']} {r.get('error','')}")
    if lat:
        print(f"\n  latencia  min={min(lat)}ms  mediana={sorted(lat)[len(lat)//2]}ms  max={max(lat)}ms")
        print(f"  primeras 5: {lat[:5]}")
        print(f"  últimas  5: {lat[-5:]}")
    print(f"\n>>> Respuestas 429: {bloqueos}/20")
    if bloqueos == 0:
        print(">>> Sin rate limit visible a este volumen. NO significa que no exista:")
        print(">>> el adaptador igual tiene que llevar backoff y respetar 429.")

    seccion("FIN")
    print("Pegale esta salida a Claude para cerrar el diseño del adaptador.")


if __name__ == "__main__":
    sys.exit(main())
