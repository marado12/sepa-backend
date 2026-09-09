"""
Capa de fuentes de precios.

Existe para que ninguna caída de un origen tumbe la app. El 09/09/2026 el SEPA
se cayó (verificado: ICMP responde, TCP :443 no, desde Argentina, Render y Azure)
y la app quedó inutilizable porque el SEPA era el único origen posible.

Contrato central — `Fuente.buscar()` NUNCA levanta excepción por una cadena caída.
Devuelve `Resultado`, que separa lo que se pudo traer de lo que falló. Quien llama
decide qué hacer con un resultado parcial; lo que no puede pasar es que Carrefour
timeouteando deje al usuario sin los precios de Día.

No agrega dependencias: usa `requests`, que ya está en requirements.txt.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Protocol

import requests

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
#  MODELO
# ─────────────────────────────────────────────────────────────────

@dataclass
class Oferta:
    """Un precio de un producto en una cadena, normalizado."""
    cadena: str
    producto: str
    precio: float
    precio_lista: Optional[float] = None   # si difiere de `precio`, hay oferta
    ean: Optional[str] = None              # matching por código de barras
    marca: Optional[str] = None
    promos: list[str] = field(default_factory=list)   # Teasers de VTEX → roadmap 4.1
    disponible: bool = True
    origen: str = ""                       # "vtex", "coto", "sepa", "manual"
    ts: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def tiene_oferta(self) -> bool:
        return (self.precio_lista is not None
                and self.precio_lista > 0
                and self.precio < self.precio_lista)

    @property
    def descuento_pct(self) -> Optional[float]:
        if not self.tiene_oferta:
            return None
        return round((1 - self.precio / self.precio_lista) * 100, 1)


@dataclass
class Resultado:
    """
    Lo que se pudo traer + lo que falló. Nunca una excepción.

    `parcial` es lo que el frontend necesita para avisarle al usuario que está
    viendo una comparación incompleta, en vez de mostrarle un óptimo mentiroso
    calculado sobre la mitad de las cadenas.
    """
    ofertas: list[Oferta] = field(default_factory=list)
    fallidas: dict[str, str] = field(default_factory=dict)   # cadena -> motivo
    consultadas: list[str] = field(default_factory=list)

    @property
    def parcial(self) -> bool:
        return bool(self.fallidas)

    @property
    def cobertura_pct(self) -> float:
        if not self.consultadas:
            return 0.0
        ok = len(self.consultadas) - len(self.fallidas)
        return round(ok / len(self.consultadas) * 100, 1)

    @property
    def aviso(self) -> Optional[str]:
        if not self.parcial:
            return None
        nombres = ", ".join(sorted(self.fallidas))
        return f"Sin datos de {nombres}. La comparación puede estar incompleta."


class Fuente(Protocol):
    """Interfaz que toda fuente de precios tiene que cumplir."""

    nombre: str

    def cadenas_soportadas(self) -> list[str]: ...

    def buscar(self, query: str, cadenas: Optional[list[str]] = None) -> Resultado: ...


# ─────────────────────────────────────────────────────────────────
#  VTEX
# ─────────────────────────────────────────────────────────────────

# Verificado el 09/09/2026: las 6 responden JSON sin token en
# /api/catalog_system/pub/products/search/
VTEX_BASES = {
    "Carrefour":  "https://www.carrefour.com.ar",
    "Jumbo":      "https://www.jumbo.com.ar",
    "Disco":      "https://www.disco.com.ar",
    "Vea":        "https://www.vea.com.ar",
    "Día":        "https://diaonline.supermercadosdia.com.ar",
    "Chango Más": "https://www.masonline.com.ar",
}

# MEDIDO el 09/09/2026 contra Carrefour: la ventana `_to - _from` no puede pasar de 50
# ("Parameter _to can't be greater than 50") y `_from` no puede pasar de 2500. O sea:
# 50 productos por request, 2500 por consulta como máximo.
VTEX_PAGINA = 50
VTEX_VENTANA_MAX = 50
VTEX_FROM_MAX = 2500

# Un "descuento" por encima de esto no es un descuento: es un ListPrice en otra escala.
# MEDIDO: Vea publica ListPrice ≈ Price × 82,6 en el 100% de su catálogo, lo que daba
# 98,8% OFF en todos sus productos. Los descuentos reales observados no pasan del 49%.
DESCUENTO_MAX_PCT = 90.0

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


def _bf(dic: dict, clave: str):
    """
    Lee un campo de VTEX tolerando la serialización .NET.

    El endpoint `catalog_system/pub` devuelve los Teasers con los nombres de campo
    internos de C#: `<Name>k__BackingField` en vez de `name`. MEDIDO el 09/09/2026:
    leyendo `name` salían 0 promos de 4 cadenas; con la clave real, 92.
    Se prueban las dos formas porque otros endpoints de VTEX sí devuelven camelCase.
    """
    if not isinstance(dic, dict):
        return None
    for k in (clave, clave[0].lower() + clave[1:], f"<{clave}>k__BackingField"):
        if k in dic:
            return dic[k]
    return None


def _nombres_teasers(teasers) -> list[str]:
    vistos: list[str] = []
    for t in (teasers or []):
        n = _bf(t, "Name")
        if n and n not in vistos:
            vistos.append(str(n))
    return vistos


def _precio_lista(oferta: dict, precio: float) -> Optional[float]:
    """
    El precio tachado, o None si no hay oferta real.

    No alcanza con `ListPrice or PriceWithoutDiscount`: en Vea el ListPrice está en
    otra escala y daba 98,8% de descuento en todo el catálogo; en Carrefour, en cambio,
    el ListPrice SÍ es el precio tachado correcto y el PriceWithoutDiscount es menor.
    Por eso se toma el mayor de los candidatos que produzca un descuento creíble.
    """
    candidatos = []
    for campo in ("ListPrice", "PriceWithoutDiscount", "FullSellingPrice"):
        v = oferta.get(campo)
        try:
            v = float(v) if v else 0.0
        except (TypeError, ValueError):
            continue
        if v > precio and (1 - precio / v) * 100 <= DESCUENTO_MAX_PCT:
            candidatos.append(v)
    return max(candidatos) if candidatos else None


class FuenteVTEX:
    """
    Adaptador de las tiendas online sobre VTEX.

    Da lo que el SEPA no da: el precio con la oferta web ya aplicada, y las
    promos bancarias con BIN de tarjeta en `Teasers`.
    No da lo que el SEPA sí daba: precio por sucursal física. Por eso el filtro
    geográfico se resuelve aparte, contra el catálogo estático de sucursales.
    """

    nombre = "vtex"

    def __init__(self, timeout: tuple[int, int] = (8, 15), max_reintentos: int = 2,
                 pagina: int = VTEX_PAGINA, bases: Optional[dict[str, str]] = None):
        self.timeout = timeout
        self.max_reintentos = max_reintentos
        self.pagina = pagina
        self.bases = bases if bases is not None else VTEX_BASES

    def cadenas_soportadas(self) -> list[str]:
        return sorted(self.bases)

    # ── HTTP ──────────────────────────────────────────────────────
    def _pedir(self, cadena: str, query: str) -> list[dict]:
        base = self.bases[cadena]
        url = (f"{base}/api/catalog_system/pub/products/search/"
               f"?ft={requests.utils.quote(query)}&_from=0&_to={self.pagina - 1}")

        ultimo = None
        for intento in range(1, self.max_reintentos + 1):
            try:
                r = requests.get(
                    url,
                    headers={"User-Agent": UA, "Accept": "application/json"},
                    timeout=self.timeout,
                )
                # 429: el server nos está pidiendo que bajemos el ritmo. Se respeta.
                if r.status_code == 429:
                    espera = int(r.headers.get("Retry-After", 2 ** intento))
                    log.warning("[vtex:%s] 429, esperando %ss", cadena, espera)
                    ultimo = RuntimeError(f"rate limited (429), retry-after={espera}")
                    if intento < self.max_reintentos:
                        time.sleep(min(espera, 10))
                    continue
                r.raise_for_status()
                data = r.json()
                if not isinstance(data, list):
                    raise ValueError(f"esperaba lista, vino {type(data).__name__}")
                return data
            except Exception as e:                    # noqa: BLE001 — se reporta, no se propaga
                ultimo = e
                if intento < self.max_reintentos:
                    time.sleep(0.5 * 2 ** (intento - 1))
        raise ultimo if ultimo else RuntimeError("fallo desconocido")

    # ── Parseo ────────────────────────────────────────────────────
    @staticmethod
    def _a_oferta(cadena: str, prod: dict) -> Optional[Oferta]:
        """
        Un producto VTEX -> Oferta. Devuelve None si le falta lo indispensable.
        Tolerante a propósito: la forma real varía entre cadenas y un KeyError
        acá tiraría abajo la cadena entera.
        """
        try:
            item = (prod.get("items") or [])[0]
            oferta = ((item.get("sellers") or [])[0]).get("commertialOffer") or {}
        except (IndexError, TypeError, AttributeError):
            return None

        precio = oferta.get("Price")
        if precio in (None, 0):
            return None

        precio = float(precio)
        lista = _precio_lista(oferta, precio)
        teasers = _nombres_teasers(oferta.get("Teasers"))

        return Oferta(
            cadena=cadena,
            producto=prod.get("productName") or "",
            precio=precio,
            precio_lista=lista,
            ean=item.get("ean") or None,
            marca=prod.get("brand") or None,
            promos=teasers,
            disponible=bool(oferta.get("IsAvailable", True))
                       and (oferta.get("AvailableQuantity") or 0) > 0,
            origen="vtex",
        )

    # ── API pública ───────────────────────────────────────────────
    def buscar(self, query: str, cadenas: Optional[list[str]] = None) -> Resultado:
        objetivo = [c for c in (cadenas or self.cadenas_soportadas()) if c in self.bases]
        res = Resultado(consultadas=objetivo)
        if not objetivo:
            return res

        # En paralelo: son 7 cadenas independientes. En serie, con el timeout de
        # 8+15s, una sola cadena lenta arrastraría a todas.
        with ThreadPoolExecutor(max_workers=min(len(objetivo), 8)) as pool:
            futuros = {pool.submit(self._pedir, c, query): c for c in objetivo}
            for fut in as_completed(futuros):
                cadena = futuros[fut]
                try:
                    productos = fut.result()
                except Exception as e:                # noqa: BLE001 — este es el punto
                    res.fallidas[cadena] = f"{type(e).__name__}: {str(e)[:120]}"
                    log.warning("[vtex:%s] falló: %s", cadena, e)
                    continue
                for p in productos:
                    of = self._a_oferta(cadena, p)
                    if of:
                        res.ofertas.append(of)

        log.info("[vtex] '%s': %d ofertas de %d/%d cadenas",
                 query, len(res.ofertas),
                 len(objetivo) - len(res.fallidas), len(objetivo))
        return res


# ─────────────────────────────────────────────────────────────────
#  COTO — no es VTEX (Oracle/Endeca), formato propio
# ─────────────────────────────────────────────────────────────────

class FuenteCoto:
    """
    Coto no corre VTEX. Endpoint verificado el 09/09/2026:
        https://www.coto.com.ar/sitios/cdigi/categoria?Ntt={q}&format=json
    (cotodigital.com.ar redirige 302 a coto.com.ar — hay que seguir el redirect.)
    """

    nombre = "coto"
    BASE = "https://www.coto.com.ar/sitios/cdigi/categoria"

    def __init__(self, timeout: tuple[int, int] = (8, 15)):
        self.timeout = timeout

    def cadenas_soportadas(self) -> list[str]:
        return ["Coto"]

    def buscar(self, query: str, cadenas: Optional[list[str]] = None) -> Resultado:
        res = Resultado(consultadas=["Coto"])
        if cadenas and "Coto" not in cadenas:
            res.consultadas = []
            return res
        try:
            r = requests.get(
                self.BASE,
                params={"Ntt": query, "format": "json"},
                headers={"User-Agent": UA, "Accept": "application/json"},
                timeout=self.timeout,
                allow_redirects=True,
            )
            r.raise_for_status()
            for of in self._parsear(r.json()):
                res.ofertas.append(of)
        except Exception as e:                        # noqa: BLE001
            res.fallidas["Coto"] = f"{type(e).__name__}: {str(e)[:120]}"
            log.warning("[coto] falló: %s", e)
        return res

    # Ruta real, VERIFICADA el 09/09/2026 contra la respuesta del sitio:
    #   contents[].Main[].contents[].records[].records[0].attributes
    # Los atributos llevan prefijo (`product.` / `sku.`) y CADA VALOR ES UN ARRAY DE UNO.
    # La versión anterior buscaba "activePrice"/"description" sin prefijo y con valores
    # escalares: contra la respuesta real capturaba CERO productos, y como no levantaba
    # excepción, Coto desaparecía de la comparación sin que nada lo avisara.

    @staticmethod
    def _uno(attrs: dict, clave: str):
        v = attrs.get(clave)
        if isinstance(v, list):
            return v[0] if v else None
        return v

    @staticmethod
    def _hallar_records(data) -> list:
        """El primer nodo con `records` cuyos elementos son a su vez agregados."""
        encontrado: list = []

        def recorrer(nodo):
            if encontrado:
                return
            if isinstance(nodo, dict):
                recs = nodo.get("records")
                if isinstance(recs, list) and recs and isinstance(recs[0], dict) \
                        and isinstance(recs[0].get("records"), list):
                    encontrado.extend(recs)
                    return
                for v in nodo.values():
                    recorrer(v)
            elif isinstance(nodo, list):
                for v in nodo:
                    recorrer(v)

        recorrer(data)
        return encontrado

    @classmethod
    def _parsear(cls, data) -> list[Oferta]:
        out: list[Oferta] = []
        for rec in cls._hallar_records(data):
            hijos = rec.get("records") or []
            attrs = (hijos[0] if hijos else {}).get("attributes") or {}
            nombre = cls._uno(attrs, "product.description") or cls._uno(attrs, "sku.displayName")
            crudo = cls._uno(attrs, "sku.activePrice")
            if not nombre or crudo in (None, ""):
                continue
            try:
                precio = float(crudo)
            except (TypeError, ValueError):
                continue
            if precio <= 0:
                continue
            # OJO: `sku.referencePrice` NO es el precio tachado, es el precio por unidad
            # de medida (papel higiénico: activePrice 7396,99 · referencePrice 184,93 el m²).
            # Usarlo como precio_lista repetiría el bug de Vea. Las promos vienen aparte.
            out.append(Oferta(
                cadena="Coto",
                producto=str(nombre).strip(),
                precio=precio,
                precio_lista=None,
                ean=cls._uno(attrs, "product.eanPrincipal"),
                marca=cls._uno(attrs, "product.brand"),
                promos=cls._promos(attrs),
                origen="coto",
            ))
        return out

    @classmethod
    def _promos(cls, attrs: dict) -> list[str]:
        """`product.dtoDescuentos` es un string con JSON adentro."""
        salida: list[str] = []
        for campo in ("product.dtoDescuentos", "product.dtoDescuentosMediosPago"):
            crudo = cls._uno(attrs, campo)
            if not crudo or crudo == "[]":
                continue
            try:
                items = json.loads(crudo) if isinstance(crudo, str) else crudo
            except (ValueError, TypeError):
                continue
            for d in (items or []):
                if not isinstance(d, dict):
                    continue
                texto = " ".join(str(d.get(k, "")).strip() for k in
                                 ("textoDescuento", "textoLlevando") if d.get(k)).strip()
                if texto and texto not in salida:
                    salida.append(texto)
        return salida


# ─────────────────────────────────────────────────────────────────
#  COMPOSICIÓN
# ─────────────────────────────────────────────────────────────────

class FuenteCompuesta:
    """
    Varias fuentes como una sola. Si una se cae, las demás siguen respondiendo:
    es el punto de todo este módulo.
    """

    nombre = "compuesta"

    def __init__(self, *fuentes: Fuente):
        self.fuentes = list(fuentes)

    def cadenas_soportadas(self) -> list[str]:
        vistas: list[str] = []
        for f in self.fuentes:
            for c in f.cadenas_soportadas():
                if c not in vistas:
                    vistas.append(c)
        return sorted(vistas)

    def buscar(self, query: str, cadenas: Optional[list[str]] = None) -> Resultado:
        total = Resultado()
        for f in self.fuentes:
            try:
                r = f.buscar(query, cadenas)
            except Exception as e:                    # noqa: BLE001 — cinturón y tiradores
                log.error("[compuesta] la fuente %s levantó excepción: %s", f.nombre, e)
                continue
            total.ofertas.extend(r.ofertas)
            total.fallidas.update(r.fallidas)
            for c in r.consultadas:
                if c not in total.consultadas:
                    total.consultadas.append(c)
        return total


def fuente_por_defecto() -> FuenteCompuesta:
    return FuenteCompuesta(FuenteVTEX(), FuenteCoto())
