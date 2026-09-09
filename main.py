"""
Backend FastAPI — Comparador de Supermercados SEPA
Deploy: Railway / Render / Fly.io
"""

import gc
import io, os, re, math, time, zipfile, logging, json
from datetime import datetime, timedelta
from typing import Optional
from contextlib import asynccontextmanager

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from outliers import detect_outliers, outlier_summary, load_all_chains, PARQUET_DIR

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)



# ─────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────

CACHE_DIR = os.environ.get("CACHE_DIR", "/tmp/sepa_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# Precios manuales: complementan el SEPA cuando una cadena no reporta un producto.
# Formato del archivo JSON:
# {
#   "Día": {
#     "Pollo entero":  { "precio_min": 4500.0, "nota": "precio por kg, actualizado 2024-05-01" },
#     "Jabón polvo":   { "precio_min": 1200.0, "nota": "Ariel 1kg" }
#   },
#   "Vea": {
#     "Carne picada":  { "precio_min": 3800.0, "nota": "precio por kg" }
#   }
# }
PRECIOS_MANUALES_PATH = os.environ.get(
    "PRECIOS_MANUALES_PATH", os.path.join(CACHE_DIR, "precios_manuales.json")
)


def _leer_precios_manuales() -> dict:
    """Lee el JSON de precios manuales. Devuelve {} si no existe o está corrupto."""
    try:
        with open(PRECIOS_MANUALES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning(f"Error leyendo precios_manuales.json: {e}")
        return {}


def _guardar_precios_manuales(data: dict) -> None:
    with open(PRECIOS_MANUALES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


SEPA_URLS = {
    0: "https://datos.produccion.gob.ar/dataset/6f47ec76-d1ce-4e34-a7e1-621fe9b1d0b5/resource/0a9069a9-06e8-4f98-874d-da5578693290/download/sepa_lunes.zip",
    1: "https://datos.produccion.gob.ar/dataset/6f47ec76-d1ce-4e34-a7e1-621fe9b1d0b5/resource/9dc06241-cc83-44f4-8e25-c9b1636b8bc8/download/sepa_martes.zip",
    2: "https://datos.produccion.gob.ar/dataset/6f47ec76-d1ce-4e34-a7e1-621fe9b1d0b5/resource/1e92cd42-4f94-4071-a165-62c4cb2ce23c/download/sepa_miercoles.zip",
    3: "https://datos.produccion.gob.ar/dataset/6f47ec76-d1ce-4e34-a7e1-621fe9b1d0b5/resource/d076720f-a7f0-4af8-b1d6-1b99d5a90c14/download/sepa_jueves.zip",
    4: "https://datos.produccion.gob.ar/dataset/6f47ec76-d1ce-4e34-a7e1-621fe9b1d0b5/resource/91bc072a-4726-44a1-85ec-4a8467aad27e/download/sepa_viernes.zip",
    5: "https://datos.produccion.gob.ar/dataset/6f47ec76-d1ce-4e34-a7e1-621fe9b1d0b5/resource/b3c3da5d-213d-41e7-8d74-f23fda0a3c30/download/sepa_sabado.zip",
    6: "https://datos.produccion.gob.ar/dataset/6f47ec76-d1ce-4e34-a7e1-621fe9b1d0b5/resource/f8e75128-515a-436e-bf8d-5c63a62f2005/download/sepa_domingo.zip",
}

# Keywords para razon_social (primer intento)
CADENAS_KEYWORDS = {
    "Jumbo":      ["Cencosud", "CENCOSUD"],
    "Disco":      ["Cencosud", "CENCOSUD"],
    "Vea":        ["Cencosud", "CENCOSUD"],
    "Coto":       ["COTO CENTRO INTEGRAL", "COTO CICSA"],
    "Día":        ["DIA Argentina", "Supermercados DIA"],
    "Carrefour":  ["DORINKA"],
    "Chango Más": ["INC S.A", "INC SA", "SuperChangomas", "Changomas"],
}

# Keywords para comercio_bandera_nombre — resuelve ambigüedades que la razon social no puede.
# Cencosud opera Jumbo, Disco y Vea con la MISMA razon social → hay que mirar la bandera.
# DORINKA opera Carrefour Y Chango Más → idem.
CADENAS_BANDERA_KEYWORDS = {
    "Jumbo":      ["Jumbo"],
    "Disco":      ["Disco"],
    "Vea":        ["Vea"],
    "Coto":       ["Coto"],
    "Día":        ["DIA", "Dia"],
    "Carrefour":  ["Carrefour"],
    "Chango Más": ["ChangoMas", "Changomas", "Chango Mas", "SuperChangomas"],
}

CANASTA_DEFAULT = [
    {"nombre": "Leche entera",     "cantidad": 4, "unidad": "litro",  "categoria": "Lácteos",    "palabras_clave": ["leche entera"]},
    {"nombre": "Pan lactal",       "cantidad": 2, "unidad": "unidad", "categoria": "Panadería",  "palabras_clave": ["pan lactal"]},
    {"nombre": "Arroz largo fino", "cantidad": 2, "unidad": "kg",     "categoria": "Secos",      "palabras_clave": ["arroz largo fino"]},
    {"nombre": "Fideos spaghetti", "cantidad": 2, "unidad": "kg",     "categoria": "Secos",      "palabras_clave": ["fideos spaghetti"]},
    {"nombre": "Aceite girasol",   "cantidad": 2, "unidad": "litro",  "categoria": "Aceites",    "palabras_clave": ["aceite girasol"]},
    {"nombre": "Azúcar",           "cantidad": 2, "unidad": "kg",     "categoria": "Secos",      "palabras_clave": ["azucar"]},
    {"nombre": "Yerba mate",       "cantidad": 1, "unidad": "kg",     "categoria": "Infusiones", "palabras_clave": ["yerba mate"]},
    {"nombre": "Café molido",      "cantidad": 1, "unidad": "unidad", "categoria": "Infusiones", "palabras_clave": ["cafe molido"]},
    {"nombre": "Harina 000",       "cantidad": 2, "unidad": "kg",     "categoria": "Secos",      "palabras_clave": ["harina 000"]},
    {"nombre": "Manteca",          "cantidad": 2, "unidad": "unidad", "categoria": "Lácteos",    "palabras_clave": ["manteca"]},
    {"nombre": "Huevos",           "cantidad": 2, "unidad": "pack",   "categoria": "Lácteos",    "palabras_clave": ["huevos"]},
    {"nombre": "Pollo entero",     "cantidad": 2, "unidad": "kg",     "categoria": "Carnes",     "palabras_clave": ["pollo entero", "pollo s/menudos", "pollo sin menudos", "pollo fresco"]},
    {"nombre": "Carne picada",     "cantidad": 1, "unidad": "kg",     "categoria": "Carnes",     "palabras_clave": ["carne picada", "picada comun", "picada especial", "picada vacuna", "vacuna picada"]},
    {"nombre": "Detergente",       "cantidad": 2, "unidad": "unidad", "categoria": "Limpieza",   "palabras_clave": ["detergente"]},
    {"nombre": "Jabón polvo",      "cantidad": 1, "unidad": "unidad", "categoria": "Limpieza",   "palabras_clave": ["jabon polvo", "jabon en polvo", "detergente polvo", "polvo lavar ropa"]},
    {"nombre": "Papel higienico",  "cantidad": 1, "unidad": "pack",   "categoria": "Higiene",    "palabras_clave": ["papel higienico"]},
    {"nombre": "Shampoo",          "cantidad": 1, "unidad": "unidad", "categoria": "Higiene",    "palabras_clave": ["shampoo"]},
    {"nombre": "Tomate perita lata","cantidad": 2, "unidad": "unidad", "categoria": "Conservas",  "palabras_clave": ["tomate perita lata", "tomate perita", "tomate pera lata", "tomate entero pelado", "tomate triturado lata"]},
    {"nombre": "Atún natural",     "cantidad": 3, "unidad": "unidad", "categoria": "Conservas",  "palabras_clave": ["atun"]},
    {"nombre": "Gaseosa cola",     "cantidad": 3, "unidad": "litro",  "categoria": "Bebidas",    "palabras_clave": ["gaseosa cola"]},
]

PROMOS_DEFAULT = [
    # ── Banco Nación (MODO / QR con tarjeta de crédito) ─────────────────────
    {"nombre": "BNA 30% Miércoles Carrefour",  "banco": "Banco Nación", "medio": "credito",
     "descuento_pct": 30.0, "tope_reintegro": 12000.0,
     "cadenas_aplica": ["Carrefour"],                     "dias_semana": [2]},
    {"nombre": "BNA 30% Miércoles Chango Más", "banco": "Banco Nación", "medio": "credito",
     "descuento_pct": 30.0, "tope_reintegro": 12000.0,
     "cadenas_aplica": ["Chango Más"],                    "dias_semana": [2]},
    {"nombre": "BNA 20% Lunes Chango Más",     "banco": "Banco Nación", "medio": "credito",
     "descuento_pct": 20.0, "tope_reintegro": 25000.0,
     "cadenas_aplica": ["Chango Más"],                    "dias_semana": [0]},
    {"nombre": "BNA 20% Martes Coto",          "banco": "Banco Nación", "medio": "credito",
     "descuento_pct": 20.0, "tope_reintegro": 25000.0,
     "cadenas_aplica": ["Coto"],                          "dias_semana": [1]},
    {"nombre": "BNA 20% Viernes-Sáb Disco/Vea/Jumbo", "banco": "Banco Nación", "medio": "credito",
     "descuento_pct": 20.0, "tope_reintegro": 25000.0,
     "cadenas_aplica": ["Disco", "Vea", "Jumbo"],         "dias_semana": [4, 5]},
    {"nombre": "BNA 20% Viernes-Sáb Día",      "banco": "Banco Nación", "medio": "credito",
     "descuento_pct": 20.0, "tope_reintegro": 20000.0,
     "cadenas_aplica": ["Día"],                           "dias_semana": [4, 5]},
    {"nombre": "BNA 5% Miércoles Día",         "banco": "Banco Nación", "medio": "credito",
     "descuento_pct": 5.0,  "tope_reintegro": 5000.0,
     "cadenas_aplica": ["Día"],                           "dias_semana": [2]},

    # ── Banco Provincia — Cuenta DNI (débito / saldo en cuenta) ─────────────
    {"nombre": "CuentaDNI 10% Lunes Día",       "banco": "Banco Provincia", "medio": "debito",
     "descuento_pct": 10.0, "tope_reintegro": 0.0,
     "cadenas_aplica": ["Día"],                           "dias_semana": [0]},
    {"nombre": "CuentaDNI 10% Miércoles Carrefour", "banco": "Banco Provincia", "medio": "debito",
     "descuento_pct": 10.0, "tope_reintegro": 0.0,
     "cadenas_aplica": ["Carrefour"],                     "dias_semana": [2]},
    {"nombre": "CuentaDNI 20% Jueves Chango Más",   "banco": "Banco Provincia", "medio": "debito",
     "descuento_pct": 20.0, "tope_reintegro": 0.0,
     "cadenas_aplica": ["Chango Más"],                    "dias_semana": [3]},
    {"nombre": "CuentaDNI 30% Jueves Coto (NFC)",   "banco": "Banco Provincia", "medio": "debito",
     "descuento_pct": 30.0, "tope_reintegro": 0.0,
     "cadenas_aplica": ["Coto"],                          "dias_semana": [3]},

    # ── Banco Supervielle ────────────────────────────────────────────────────
    {"nombre": "Supervielle 20% Martes Cencosud+ChangoMás", "banco": "Banco Supervielle", "medio": "debito",
     "descuento_pct": 20.0, "tope_reintegro": 25000.0,
     "cadenas_aplica": ["Jumbo", "Disco", "Vea", "Chango Más"], "dias_semana": [1]},

    # ── Banco Galicia ────────────────────────────────────────────────────────
    {"nombre": "Galicia 12 cuotas s/i Cencosud", "banco": "Banco Galicia", "medio": "credito",
     "descuento_pct": 0.0,  "tope_reintegro": 0.0,
     "cadenas_aplica": ["Jumbo", "Disco", "Vea"],         "dias_semana": [],
     "cuotas_sin_interes": 12},

    # ── Mercado Pago (QR) ────────────────────────────────────────────────────
    {"nombre": "MercadoPago 25% QR Carrefour/Coto/Día/Vea/Chango Más", "banco": "Mercado Pago", "medio": "billetera",
     "descuento_pct": 25.0, "tope_reintegro": 0.0,
     "cadenas_aplica": ["Carrefour", "Coto", "Día", "Vea", "Chango Más"], "dias_semana": []},

     # ── Banco Macro ──────────────────────────────────────────────────────────
    {"nombre": "Macro 25% Martes Carrefour", "banco": "Banco Macro", "medio": "credito",
     "descuento_pct": 25.0, "tope_reintegro": 15000.0,
     "cadenas_aplica": ["Carrefour"], "dias_semana": [1]},
    {"nombre": "Macro 20% Jueves Cencosud", "banco": "Banco Macro", "medio": "credito",
     "descuento_pct": 20.0, "tope_reintegro": 20000.0,
     "cadenas_aplica": ["Jumbo", "Disco", "Vea"], "dias_semana": [3]},

    # ── BBVA ─────────────────────────────────────────────────────────────────
    {"nombre": "BBVA 20% Viernes Carrefour", "banco": "BBVA", "medio": "credito",
     "descuento_pct": 20.0, "tope_reintegro": 15000.0,
     "cadenas_aplica": ["Carrefour"], "dias_semana": [4]},
    {"nombre": "BBVA 15% Miércoles Coto", "banco": "BBVA", "medio": "debito",
     "descuento_pct": 15.0, "tope_reintegro": 10000.0,
     "cadenas_aplica": ["Coto"], "dias_semana": [2]},

    # ── Banco Santander ───────────────────────────────────────────────────────
    {"nombre": "Santander 20% Lunes Carrefour", "banco": "Banco Santander", "medio": "credito",
     "descuento_pct": 20.0, "tope_reintegro": 12000.0,
     "cadenas_aplica": ["Carrefour"], "dias_semana": [0]},

    # ── Ualá ─────────────────────────────────────────────────────────────────
    {"nombre": "Ualá 20% QR supermercados", "banco": "Ualá", "medio": "billetera",
     "descuento_pct": 20.0, "tope_reintegro": 3000.0,
     "cadenas_aplica": ["Carrefour", "Día", "Chango Más"], "dias_semana": []},

    # ── Personal Pay ──────────────────────────────────────────────────────────
    {"nombre": "Personal Pay 15% QR", "banco": "Personal Pay", "medio": "billetera",
     "descuento_pct": 15.0, "tope_reintegro": 4000.0,
     "cadenas_aplica": ["Carrefour", "Jumbo", "Disco"], "dias_semana": []},

    # ── Naranja X ─────────────────────────────────────────────────────────────
    {"nombre": "Naranja X 30% Miércoles", "banco": "Naranja X", "medio": "billetera",
     "descuento_pct": 30.0, "tope_reintegro": 8000.0,
     "cadenas_aplica": ["Carrefour", "Coto", "Día"], "dias_semana": [2]},
    {"nombre": "Naranja X 15% resto de días", "banco": "Naranja X", "medio": "credito",
     "descuento_pct": 15.0, "tope_reintegro": 4000.0,
     "cadenas_aplica": ["Carrefour", "Coto", "Día"], "dias_semana": []},
]

# Catálogo de bancos/billeteras disponibles para la pantalla de selección del usuario.
# "medios" indica qué tipo de pago tienen las promos de ese banco:
#   "credito"  → tarjeta de crédito
#   "debito"   → tarjeta de débito o saldo en cuenta
#   "billetera" → billetera digital (Mercado Pago, etc.)
# "nota" es un texto corto que se muestra al usuario como aclaración.
# DESPUÉS
BANCOS_CATALOGO = [
    {
        "id": "Banco Nación",
        "label": "Banco Nación",
        "medios": ["credito"],
        "nota": "Requiere tarjeta de crédito y pago con QR MODO",
        "cadenas_principales": ["Carrefour", "Chango Más", "Coto", "Disco", "Vea", "Jumbo", "Día"],
    },
    {
        "id": "Banco Provincia",
        "label": "Banco Provincia / Cuenta DNI",
        "medios": ["debito"],
        "nota": "Cuenta DNI (débito/saldo). Solo válido en Provincia de Buenos Aires",
        "cadenas_principales": ["Carrefour", "Chango Más", "Coto", "Día"],
    },
    {
        "id": "Banco Galicia",
        "label": "Banco Galicia",
        "medios": ["credito"],
        "nota": "12 cuotas sin interés en Cencosud (Jumbo, Disco, Vea)",
        "cadenas_principales": ["Jumbo", "Disco", "Vea"],
    },
    {
        "id": "Banco Macro",
        "label": "Banco Macro",
        "medios": ["credito", "debito"],
        "nota": "Promos en supermercados seleccionados",
        "cadenas_principales": ["Carrefour", "Jumbo", "Disco", "Vea"],
    },
    {
        "id": "BBVA",
        "label": "BBVA",
        "medios": ["credito", "debito"],
        "nota": "Promos variables — verificar en la app BBVA",
        "cadenas_principales": ["Carrefour", "Coto", "Jumbo"],
    },
    {
        "id": "Banco Santander",
        "label": "Banco Santander",
        "medios": ["credito"],
        "nota": "Promos en cadenas seleccionadas",
        "cadenas_principales": ["Carrefour", "Día"],
    },
    {
        "id": "Banco Supervielle",
        "label": "Banco Supervielle",
        "medios": ["debito"],
        "nota": "Solo jubilados con haberes en Supervielle",
        "cadenas_principales": ["Jumbo", "Disco", "Vea", "Chango Más"],
    },
    # ── Billeteras virtuales ──────────────────────────────────────────────────
    {
        "id": "Mercado Pago",
        "label": "Mercado Pago",
        "medios": ["billetera"],
        "nota": "Promo variable — verificar en la app antes de ir",
        "cadenas_principales": ["Carrefour", "Coto", "Día", "Vea", "Chango Más"],
    },
    {
        "id": "Ualá",
        "label": "Ualá",
        "medios": ["billetera"],
        "nota": "Pagá con QR en el super. Promos variables según convenio",
        "cadenas_principales": ["Carrefour", "Día", "Chango Más"],
    },
    {
        "id": "Personal Pay",
        "label": "Personal Pay",
        "medios": ["billetera"],
        "nota": "Billetera virtual de Personal. Verificar promos vigentes en la app",
        "cadenas_principales": ["Carrefour", "Jumbo", "Disco"],
    },
    {
        "id": "Naranja X",
        "label": "Naranja X",
        "medios": ["billetera", "credito"],
        "nota": "Tarjeta y billetera. Promos frecuentes en supermercados",
        "cadenas_principales": ["Carrefour", "Coto", "Día"],
    },
]

# ─────────────────────────────────────────────
#  ESTADO GLOBAL (caché en memoria)
# ─────────────────────────────────────────────

_cache: dict = {}   # { dia: (df_suc, prod_path, fecha_str) }  — df_prod NO se guarda en RAM

# ── Concurrencia: un solo lock global para serializar descargas ──────────────
# Problema original: /precargar, /refresh y /comparar podían disparar
# _cargar_o_descargar en paralelo → doble descarga de 325 MB, doble
# ParquetWriter sobre los mismos archivos, OOM en Railway.
_descarga_lock = None          # se inicializa en el primer uso (dentro del event loop)
_descarga_en_progreso: set[int] = set()   # días cuya descarga está activa

# ── Resiliencia de la descarga (roadmap 0.2 / 0.3) ──────────────────────────
# Antes: si la descarga del SEPA fallaba, _bg_descargar logueaba el error y lo
# descartaba → el caché quedaba vacío, la app entera inutilizable y /api/status
# decía "listo: false, en_progreso: false" sin explicar por qué.
SEPA_MAX_REINTENTOS    = int(os.environ.get("SEPA_MAX_REINTENTOS", "3"))
SEPA_BACKOFF_BASE_S    = int(os.environ.get("SEPA_BACKOFF_BASE_S", "30"))
SEPA_FALLBACK_MAX_DIAS = int(os.environ.get("SEPA_FALLBACK_MAX_DIAS", "7"))

# Estado observable de la descarga, por día. Lo consume GET /api/status.
#   { dia: {ultimo_intento, ultimo_error, ultimo_error_ts,
#           intentos_fallidos, ultimo_exito, proximo_reintento} }
_estado_descarga: dict[int, dict] = {}

# Días que están sirviendo datos VIEJOS porque la descarga falló.
#   { dia: "YYYY-MM-DD" }  ← fecha real del parquet en uso
_degradado: dict[int, str] = {}


def _hoy() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _ahora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _registrar_intento(dia: int) -> None:
    _estado_descarga.setdefault(dia, {})["ultimo_intento"] = _ahora_iso()


def _registrar_error(dia: int, exc: Exception) -> None:
    e = _estado_descarga.setdefault(dia, {})
    e["ultimo_error"]      = f"{type(exc).__name__}: {exc}"[:500]
    e["ultimo_error_ts"]   = _ahora_iso()
    e["intentos_fallidos"] = e.get("intentos_fallidos", 0) + 1


def _registrar_exito(dia: int, n_suc: Optional[int] = None) -> None:
    e = _estado_descarga.setdefault(dia, {})
    e["ultimo_exito"]      = _ahora_iso()
    e["ultimo_error"]      = None
    e["intentos_fallidos"] = 0
    e.pop("proximo_reintento", None)
    if n_suc is not None:
        e["n_sucursales"] = n_suc


def _aviso_datos(dia: int) -> Optional[str]:
    """Texto listo para mostrar en el frontend cuando los datos no son de hoy."""
    fecha = _degradado.get(dia)
    if not fecha:
        return None
    try:
        d = datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        return f"Datos del {fecha}"
    dias = (datetime.strptime(_hoy(), "%Y-%m-%d") - d).days
    if dias <= 0:
        antig = ""
    elif dias == 1:
        antig = " (de ayer)"
    else:
        antig = f" (de hace {dias} días)"
    return f"El SEPA no respondió: mostrando precios del {d.strftime('%d/%m')}{antig}"

def _prod_path_valido(prod_path: str) -> bool:
    """prod_path es ahora un JSON dict {cadena: path}. Valida que al menos uno exista."""
    if not prod_path:
        return False
    try:
        import json as _j
        paths = _j.loads(prod_path)
        return any(os.path.exists(p) for p in paths.values())
    except Exception:
        return False


def _get_lock():
    """Crea el lock la primera vez que se llama (debe ser dentro del event loop)."""
    global _descarga_lock
    if _descarga_lock is None:
        import asyncio
        _descarga_lock = asyncio.Lock()
    return _descarga_lock


# ─────────────────────────────────────────────
#  FUNCIONES DE PROCESAMIENTO (portadas del CLI)
# ─────────────────────────────────────────────

def normalizar(texto: str) -> str:
    import unicodedata
    if not isinstance(texto, str): return ""
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = texto.lower().strip()
    texto = re.sub(r"(\d),(\d)", r"\1.\2", texto)
    for pat, rep in [("lts?","l"), ("grs?","g"), ("kgs?","kg"), ("mls?","ml"), (r"\bcc\b","ml")]:
        texto = re.sub(r"\b" + pat + r"\b", rep, texto)
    return re.sub(r"\s+", " ", texto).strip()


_STOPWORDS = {'en', 'al', 'de', 'la', 'el', 'lo', 'un', 'con', 'por', 'para', 'las', 'los', 'del'}

# Tokens de grado: solo ceros repetidos → tipo de harina (000, 0000)
# Se preservan aunque el regex de cantidades los capturaría
_TOKENS_GRADO = re.compile(r'^0+$')

def tokens(texto: str) -> set:
    result = set()
    for t in normalizar(texto).split():
        if len(t) <= 1:
            continue
        if t in _STOPWORDS:
            continue
        # Preservar 000, 0000 (tipos de harina)
        if _TOKENS_GRADO.match(t):
            result.add(t)
            continue
        # Filtrar cantidades con unidad: 1kg, 500ml, 2.5l
        if re.match(r'^\d+[.,]?\d*[lgmk]+$', t):
            continue
        # Filtrar números puros: 1, 250, 1.5
        if re.match(r'^\d+[.,]?\d*$', t):
            continue
        result.add(t)
    return result

# ─────────────────────────────────────────────
#  PARSEO DE CANTIDAD CON UNIDAD
# ─────────────────────────────────────────────

# Factores de conversión a unidad base (gramos o ml)
_UNIDAD_A_GRAMOS = {
    "g": 1, "gramos": 1, "gr": 1,
    "kg": 1000, "kilo": 1000, "kilos": 1000,
    "mg": 0.001,
}
_UNIDAD_A_ML = {
    "ml": 1, "cc": 1,
    "l": 1000, "litro": 1000, "litros": 1000, "lt": 1000, "lts": 1000,
}
# Unidades contables: huevos, rollos, etc. La "base" es la unidad individual.
_UNIDAD_COUNT = {
    "unidad", "unidades", "un", "ud", "uds",
    "huevo", "huevos",
    "rollo", "rollos",
    "paquete", "paquetes", "paq",
    "sobre", "sobres",
    "lata", "latas",
    "botella", "botellas",
    "pack",
}

def _a_base(valor: float, unidad: str) -> tuple[float, str] | None:
    """
    Convierte (valor, unidad) → (valor_base, tipo) donde tipo es 'peso', 'volumen' o 'count'.
    Retorna None si la unidad no es reconocida (ej: 'pack' genérico).
    """
    u = unidad.lower().strip()
    if u in _UNIDAD_A_GRAMOS:
        return (valor * _UNIDAD_A_GRAMOS[u], "peso")
    if u in _UNIDAD_A_ML:
        return (valor * _UNIDAD_A_ML[u], "volumen")
    if u in _UNIDAD_COUNT:
        return (valor, "count")
    return None


# Regex para extraer cantidades de la descripción del producto
# Captura: "500 G", "500gr", "1.5 KG", "0,5kg", "1L", "750ml", "200 CC", etc.
_RE_CANT_DESC = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"
    r"(kg|kilo(?:s)?|g(?:r(?:amos?)?)?|mg|l(?:itros?|ts?)?|ml|cc)\b",
    re.IGNORECASE,
)

# Regex para unidades contables: "x12 unidades", "12un", "x 6 huevos", "pack x30", etc.
_RE_CANT_COUNT = re.compile(
    r"[xX×]?\s*(\d+)\s*"
    r"(?:un(?:idades?|\.)?|huevos?|ud(?:s)?\.?|rollos?|paquetes?|paq\.?|sobres?|latas?|botellas?|pack)\b",
    re.IGNORECASE,
)

# Regex para doble medida: "6 x 500ml", "4 x 200g", "2x1l"
# El precio SEPA corresponde al pack completo → normalizamos al total.
_RE_CANT_PACK = re.compile(
    r"(\d+)\s*[xX×]\s*(\d+(?:[.,]\d+)?)\s*(kg|kilo(?:s)?|g(?:r(?:amos?)?)?|mg|l(?:itros?|ts?)?|ml|cc)\b",
    re.IGNORECASE,
)


def _extraer_cantidades_desc(desc_norm: str) -> list[tuple[float, str]]:
    """
    Extrae todas las cantidades con unidad de una descripción normalizada.
    Ej: "leche entera 1 l la serenisima" → [(1000.0, 'volumen')]
        "huevos x12 unidades" → [(12.0, 'count')]
        "agua 6 x 500ml" → [(3000.0, 'volumen')]  ← pack completo
    """
    resultados = []
    posiciones_usadas: set[int] = set()  # evita que _RE_CANT_DESC reparse lo ya capturado

    # Doble medida tiene prioridad: "6 x 500ml" → 3000ml (precio del pack completo)
    for m in _RE_CANT_PACK.finditer(desc_norm):
        try:
            n_pack = int(m.group(1))
            val_unit = float(m.group(2).replace(",", "."))
            unidad = m.group(3)
            base = _a_base(val_unit * n_pack, unidad)
            if base:
                resultados.append(base)
                posiciones_usadas.update(range(m.start(), m.end()))
        except ValueError:
            pass

    for m in _RE_CANT_DESC.finditer(desc_norm):
        if m.start() in posiciones_usadas:
            continue  # ya capturado como pack
        try:
            val = float(m.group(1).replace(",", "."))
            unidad = m.group(2)
            base = _a_base(val, unidad)
            if base:
                resultados.append(base)
        except ValueError:
            pass
    for m in _RE_CANT_COUNT.finditer(desc_norm):
        try:
            val = float(m.group(1))
            if val > 0:
                resultados.append((val, "count"))
        except ValueError:
            pass
    return resultados


def _score_cantidad(item_cant: float, item_unidad: str,
                    desc_norm_series: "pd.Series",
                    cache: dict | None = None,
                    cantidad_pack: int | None = None) -> "pd.Series":
    """
    Vectorizado sobre una Series de descripciones normalizadas.
    Retorna una Series float con valores:
      +0.25  → cantidad exacta del ítem presente en la descripción
      +0.10  → cantidad equivalente (ej: 500g cuando el ítem pide 0.5 kg)
       0.0   → no hay cantidad parseable en la descripción (sin penalización)
      -0.20  → hay cantidad en la descripción pero fuera del ±30% (penaliza)

    cantidad_pack: para ítems con unidad "pack", la cantidad interna esperada
                   (ej: 12 para "pack de 12 huevos"). Permite discriminar x6 vs x12 vs x30.
    """
    base_item = _a_base(item_cant, item_unidad)

    # ── Caso especial: pack con cantidad interna conocida ─────────────────────
    # Si el item es "pack" y se especificó cantidad_pack, scorear por la cantidad
    # interna del pack (ej: x6, x12, x30) en lugar del número de packs.
    if base_item is None and item_unidad.lower() in _UNIDAD_COUNT and cantidad_pack:
        val_item = float(cantidad_pack)
        scores = pd.Series(0.0, index=desc_norm_series.index)
        for idx, desc in desc_norm_series.items():
            cantidades = (cache.get(desc) if cache else None) or _extraer_cantidades_desc(desc)
            if not cantidades:
                continue
            mejor = None
            for val_desc, tipo_desc in cantidades:
                if tipo_desc != "count" or val_desc <= 0:
                    continue
                ratio = val_desc / val_item
                if abs(ratio - 1.0) < 0.02:
                    mejor = max(mejor, 0.25) if mejor is not None else 0.25
                elif abs(ratio - 1.0) < 0.30:
                    mejor = max(mejor, 0.10) if mejor is not None else 0.10
                else:
                    mejor = max(mejor, -0.20) if mejor is not None else -0.20
            scores.at[idx] = mejor if mejor is not None else 0.0
        return scores

    if base_item is None:
        # unidad no métrica sin cantidad_pack → sin scoring de cantidad
        return pd.Series(0.0, index=desc_norm_series.index)

    val_item, tipo_item = base_item
    if val_item <= 0:
        return pd.Series(0.0, index=desc_norm_series.index)

    scores = pd.Series(0.0, index=desc_norm_series.index)

    for idx, desc in desc_norm_series.items():
        cantidades = (cache.get(desc) if cache else None) or _extraer_cantidades_desc(desc)
        if not cantidades:
            continue  # sin info de cantidad → score neutro (0)

        mejor = None
        for val_desc, tipo_desc in cantidades:
            if tipo_desc != tipo_item:
                continue
            if val_desc <= 0:
                continue
            ratio = val_desc / val_item
            if abs(ratio - 1.0) < 0.02:
                mejor = max(mejor, 0.25) if mejor is not None else 0.25
            elif abs(ratio - 1.0) < 0.30:
                mejor = max(mejor, 0.10) if mejor is not None else 0.10
            else:
                mejor = max(mejor, -0.20) if mejor is not None else -0.20

        scores.at[idx] = mejor if mejor is not None else 0.0

    return scores


def _normalizar_col_vec(serie: pd.Series) -> pd.Series:
    s = serie.fillna("").astype(str).str.lower()
    s = s.str.normalize("NFD").str.encode("ascii", errors="ignore").str.decode("ascii")
    s = s.str.replace(r"(\d),(\d)", r"\1.\2", regex=True)
    for pat, rep in [("lts?","l"), ("grs?","g"), ("kgs?","kg"), ("mls?","ml"), (r"\bcc\b","ml")]:
        s = s.str.replace(pat, rep, regex=True)
    return s.str.replace(r"\s+", " ", regex=True).str.strip()


def _precio_a_float_vec(serie: pd.Series) -> pd.Series:
    s = serie.fillna("").astype(str).str.strip().str.replace(" ", "", regex=False)
    europeo = s.str.contains(r"\d\.\d{3},", regex=True)
    s = s.copy()
    s[europeo]  = s[europeo].str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    s[~europeo] = s[~europeo].str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


def asignar_cadena(razon: str, bandera: str = "") -> Optional[str]:
    """
    Asigna cadena comercial a partir de razon_social y/o bandera.
    Prioriza la bandera cuando está disponible porque resuelve ambigüedades:
    - Cencosud opera Jumbo, Disco y Vea con la misma razon social
    - DORINKA opera Carrefour y Chango Más con la misma razon social
    """
    # Primero intentar por bandera (más específica)
    if isinstance(bandera, str) and bandera.strip():
        up_b = bandera.upper()
        for cadena, kws in CADENAS_BANDERA_KEYWORDS.items():
            if any(k.upper() in up_b for k in kws):
                return cadena

    # Fallback a razon social (detecta la empresa pero puede ser ambigua)
    if isinstance(razon, str) and razon.strip():
        up_r = razon.upper()
        # Para Cencosud sin bandera identificable, defaultear a Vea
        # (la más común en el interior del país)
        if any(k.upper() in up_r for k in ["CENCOSUD"]):
            return "Vea"
        for cadena, kws in CADENAS_KEYWORDS.items():
            if cadena in ("Jumbo", "Disco", "Vea"):
                continue  # ya manejado arriba
            if any(k.upper() in up_r for k in kws):
                return cadena

    return None


def _leer_csv_desde_bytes(data: bytes) -> Optional[pd.DataFrame]:
    try:
        muestra = data[:4096].decode("utf-8", errors="ignore")
        sep = "|" if muestra.count("|") > muestra.count(",") else ","
        return pd.read_csv(io.BytesIO(data), sep=sep, dtype=str, low_memory=False,
                           encoding="utf-8", on_bad_lines="skip")
    except Exception:
        return None


def _detectar_sep(data: bytes) -> str:
    muestra = data[:4096].decode("utf-8", errors="ignore")
    return "|" if muestra.count("|") > muestra.count(",") else ","


def _procesar_productos_chunked(z_inner, csv_name: str, cadena: str,
                                writers_prod: dict, schema_prod) -> int:
    """
    Lee el CSV de productos en chunks y guarda SOLO el precio mínimo por
    descripción normalizada. Esto reduce 12M filas → ~50-100k filas únicas,
    que es todo lo que necesita _buscar_precios.

    Estrategia anti-OOM: min_precios se vacía (flush) al parquet cada
    FLUSH_CADA entradas. Así el dict nunca supera ~50k entradas en RAM,
    incluso para comercios con 500 sucursales × 25k productos.
    El parquet puede quedar con duplicados entre flushes, pero _buscar_precios
    ya toma el mínimo por cadena vía groupby, así que no afecta la correctitud.
    """
    FLUSH_CADA = 50_000  # máximo de entradas en min_precios antes de vaciar a disco

    def _flush(min_precios: dict, writer, n_total: int) -> int:
        if not min_precios:
            return n_total
        rows = [(do, pr, cadena, pr, dn) for dn, (pr, do) in min_precios.items()]
        df_flush = pd.DataFrame(rows, columns=[
            "productos_descripcion", "productos_precio_lista",
            "_cadena", "_precio", "_desc_norm"])
        writer.write_table(
            pa.Table.from_pandas(df_flush, schema=schema_prod, preserve_index=False))
        n = len(df_flush)
        del df_flush
        return n_total + n

    try:
        with z_inner.open(csv_name) as f_peek:
            muestra = f_peek.read(4096).decode("utf-8", errors="ignore")
        sep = "|" if muestra.count("|") > muestra.count(",") else ","

        col_desc = col_precio = None
        min_precios: dict[str, tuple[float, str]] = {}  # desc_norm -> (precio_min, desc_orig)
        writer = writers_prod[cadena]
        n_total = 0

        with z_inner.open(csv_name) as f_csv:
            for chunk in pd.read_csv(f_csv, sep=sep, dtype=str, low_memory=False,
                                     encoding="utf-8", on_bad_lines="skip",
                                     chunksize=10_000):
                chunk.columns = [c.strip().lower().replace(" ", "_") for c in chunk.columns]

                if col_desc is None:
                    col_desc   = next((c for c in chunk.columns if "descripcion" in c), None)
                    col_precio = next((c for c in chunk.columns if "precio_lista" in c), None)
                if not col_desc or not col_precio:
                    break

                chunk["_precio"] = _precio_a_float_vec(chunk[col_precio]).astype("float64")
                # Filtrar precios imposibles: < 10 (error de parseo) o > 500_000 (dato corrupto)
                chunk = chunk[(chunk["_precio"] >= 10) & (chunk["_precio"] <= 500_000)]
                if chunk.empty:
                    continue

                chunk["_desc_norm"] = _normalizar_col_vec(chunk[col_desc]).astype(str)
                chunk["_desc_orig"] = chunk[col_desc].fillna("").astype(str)

                for dn, do, pr in chunk[["_desc_norm", "_desc_orig", "_precio"]].itertuples(index=False, name=None):
                    existing = min_precios.get(dn)
                    if existing is None or pr < existing[0]:
                        min_precios[dn] = (pr, do)

                del chunk

                # Flush parcial si el dict creció demasiado
                if len(min_precios) >= FLUSH_CADA:
                    n_total = _flush(min_precios, writer, n_total)
                    min_precios.clear()
                    gc.collect()

        # Flush final con lo que quedó
        n_total = _flush(min_precios, writer, n_total)
        min_precios.clear()
        gc.collect()
        return n_total

    except Exception as e:
        log.warning(f"  Productos chunked {csv_name}: {e}")
        return 0


def _clasificar_csv(df: pd.DataFrame, nombre: str) -> str:
    cols = set(df.columns)
    if any(c in cols for c in ["comercio_razon_social","comercio_cuit","comercio_bandera_nombre"]):
        return "comercio"
    if any(c in cols for c in ["sucursales_nombre","sucursales_calle","sucursales_provincia"]):
        return "sucursal"
    if any(c in cols for c in ["productos_descripcion","productos_precio_lista","productos_ean"]):
        return "producto"
    n = nombre.lower()
    if "sepa_1" in n: return "comercio"
    if "sepa_2" in n: return "sucursal"
    if "sepa_3" in n: return "producto"
    return "desconocido"


def _descargar_y_procesar(dia: int) -> tuple:
    """
    Descarga el ZIP SEPA y lo procesa en modo streaming con PyArrow.

    Estrategia para usar <200MB de RAM (vs ~1.5GB en batch):
    - Procesa un comercio a la vez (un ZIP interno = un comercio)
    - Filtra inmediatamente los que no son cadenas objetivo
    - Escribe directo al Parquet sin acumular DataFrames en listas
    - Libera memoria de cada chunk antes de leer el siguiente
    """
    url      = SEPA_URLS[dia]
    zip_path = os.path.join(CACHE_DIR, f"sepa_dia{dia}_tmp.zip")
    cache_base = os.path.join(CACHE_DIR, f"sepa_dia{dia}_{datetime.now().strftime('%Y-%m-%d')}")
    path_suc  = cache_base + "_suc.parquet"
    path_prod = cache_base + "_prod.parquet"

    # Descargar — escribe a .tmp y solo renombra si el ZIP es válido
    zip_tmp = zip_path + ".tmp"
    log.info(f"Descargando SEPA dia {dia}: {url}")
    try:
        # timeout=(connect, read). Antes era un único 600 que aplicaba también al
        # connect: con el portal caído cada intento colgaba ~10 min y cualquier
        # backoff arriba de esto era inservible.
        resp = requests.get(url, stream=True, timeout=(15, 600))
        resp.raise_for_status()
        total_bytes = int(resp.headers.get("content-length", 0))
        descargado  = 0
        with open(zip_tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=2 * 1024 * 1024):
                if chunk:
                    f.write(chunk)
                    descargado += len(chunk)
                    if total_bytes and descargado % (20 * 1024 * 1024) < 2 * 1024 * 1024:
                        log.info(f"  Descargando {descargado/total_bytes*100:.0f}%")
        # Verificar integridad: tamaño y firma ZIP válida
        if total_bytes and descargado < total_bytes:
            raise ValueError(
                f"Descarga truncada: esperados {total_bytes:,} B, recibidos {descargado:,} B"
            )
        with open(zip_tmp, "rb") as f:
            magic = f.read(4)
        if magic[:2] != b"PK":
            raise ValueError(f"El archivo descargado no es un ZIP válido (magic={magic!r})")
        os.replace(zip_tmp, zip_path)  # atómico
        log.info(f"Descarga completa y verificada: {descargado/1024**2:.0f} MB")
    except Exception:
        # Limpiar .tmp para no dejar basura en disco
        try: os.remove(zip_tmp)
        except FileNotFoundError: pass
        raise

    # Schemas PyArrow para escritura incremental sin acumular en RAM
    schema_suc = pa.schema([
        pa.field("id_comercio",             pa.int32()),
        pa.field("id_sucursal",             pa.int32()),
        pa.field("comercio_razon_social",   pa.string()),
        pa.field("comercio_bandera_nombre", pa.string()),
        pa.field("sucursales_provincia",    pa.string()),
        pa.field("sucursales_latitud",      pa.float32()),
        pa.field("sucursales_longitud",     pa.float32()),
    ])
    schema_prod = pa.schema([
        pa.field("productos_descripcion",   pa.string()),
        pa.field("productos_precio_lista",  pa.float64()),
        pa.field("_cadena",                 pa.string()),
        pa.field("_precio",                 pa.float64()),
        pa.field("_desc_norm",              pa.string()),
    ])

    path_suc_tmp  = path_suc  + ".tmp"
    writer_suc  = pq.ParquetWriter(path_suc_tmp, schema_suc, compression="snappy")
    # Un parquet por cadena → PyArrow puede leer solo el archivo de la cadena relevante
    cadenas_nombres = list(CADENAS_KEYWORDS.keys())
    writers_prod: dict[str, pq.ParquetWriter] = {}
    paths_prod_tmp: dict[str, str] = {}
    for _cad in cadenas_nombres:
        _p = cache_base + f"_prod_{_cad}.parquet.tmp"
        paths_prod_tmp[_cad] = _p
        writers_prod[_cad] = pq.ParquetWriter(_p, schema_prod, compression="snappy")
    n_suc = n_prod = errores = 0
    n_prod_por_cadena: dict = {c: 0 for c in cadenas_nombres}
    n_zips_por_cadena: dict = {c: 0 for c in cadenas_nombres}
    n_zips_sin_cadena = 0

    # Abrir el ZIP outer con un fd persistente y borrarlo del directorio
    # antes de procesar. En Linux, os.remove() elimina el nombre del
    # directorio pero el inode (y el contenido) permanece mientras el fd
    # esté abierto. Así liberamos ~310MB de espacio en tmpfs/disco durante
    # el procesamiento sin perder acceso al contenido.
    _fz_outer = open(zip_path, "rb")
    try:
        os.remove(zip_path)
        log.info("ZIP outer removido del filesystem (fd sigue abierto) — ~310MB liberados")
    except Exception as e:
        log.warning(f"No se pudo remover ZIP outer: {e}")

    with zipfile.ZipFile(_fz_outer) as z_outer:
        inner_zips = [n for n in z_outer.namelist()
                      if n.lower().endswith(".zip") and not n.endswith("/")]
        log.info(f"ZIPs internos: {len(inner_zips)} — procesando en streaming")

        inner_tmp = os.path.join(CACHE_DIR, f"_inner_tmp_dia{dia}.zip")

        for i, inner_name in enumerate(inner_zips):
            try:
                # ── Extraer ZIP interno a disco en lugar de cargarlo en RAM ──
                # z_outer.read() cargaría el ZIP entero en un bytes() → pico de RAM.
                # Con open()+shutil.copyfileobj escribimos en chunks de 4 MB.
                import shutil
                with z_outer.open(inner_name) as src, open(inner_tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)

                # Verificar firma ZIP mínima
                with open(inner_tmp, "rb") as f:
                    magic = f.read(2)
                if magic != b"PK":
                    errores += 1
                    continue

                df_com = df_suc_c = None
                csv_producto = None
                try:
                    with zipfile.ZipFile(inner_tmp) as z_inner:
                        # Primer pass: clasificar CSVs leyendo solo la cabecera (2KB)
                        # para detectar cadena ANTES de leer los productos (pesados)
                        for csv_name in z_inner.namelist():
                            if not csv_name.lower().endswith(".csv"): continue
                            with z_inner.open(csv_name) as _fh:
                                raw_head = _fh.read(2048)  # solo 2KB, sin descomprimir el CSV entero
                            sep_h = _detectar_sep(raw_head)
                            try:
                                head_df = pd.read_csv(
                                    io.BytesIO(raw_head), sep=sep_h, dtype=str,
                                    nrows=1, on_bad_lines="skip")
                                head_df.columns = [c.strip().lower().replace(" ","_")
                                                   for c in head_df.columns]
                                t = _clasificar_csv(head_df, csv_name)
                            except Exception:
                                t = "desconocido"
                            del raw_head

                            if t == "producto":
                                csv_producto = csv_name  # leer después en chunks
                            elif t in ("comercio", "sucursal"):
                                with z_inner.open(csv_name) as _fc:
                                    df_tmp = _leer_csv_desde_bytes(_fc.read())
                                if df_tmp is None or df_tmp.empty: continue
                                df_tmp.columns = [c.strip().lower().replace(" ","_")
                                                  for c in df_tmp.columns]
                                if t == "comercio":   df_com   = df_tmp
                                elif t == "sucursal": df_suc_c = df_tmp

                        if csv_producto is None:
                            continue

                        # Detectar cadena antes de leer productos
                        razon   = str((df_com["comercio_razon_social"].iloc[0]
                                       if df_com is not None and "comercio_razon_social" in df_com.columns
                                       else "") or "")
                        bandera = str((df_com["comercio_bandera_nombre"].iloc[0]
                                       if df_com is not None and "comercio_bandera_nombre" in df_com.columns
                                       else "") or "")
                        cadena = asignar_cadena(razon, bandera)
                        if not cadena:
                            n_zips_sin_cadena += 1
                            log.warning(f"[ZIP {i+1}/{len(inner_zips)}] SKIP razon={razon[:50]!r} bandera={bandera[:30]!r}")
                            continue  # no es cadena objetivo — no leer productos

                        n_zips_por_cadena[cadena] = n_zips_por_cadena.get(cadena, 0) + 1
                        log.warning(f"[ZIP {i+1}/{len(inner_zips)}] MATCH cadena={cadena} razon={razon[:50]!r}")

                        # ── Sucursales ────────────────────────────────────────
                        if df_suc_c is not None:
                            try:
                                for col in ["id_comercio","id_sucursal"]:
                                    if col in df_suc_c.columns:
                                        df_suc_c[col] = pd.to_numeric(df_suc_c[col], errors="coerce").fillna(0).astype("int32")
                                    else:
                                        df_suc_c[col] = 0
                                for col in ["sucursales_latitud","sucursales_longitud"]:
                                    if col in df_suc_c.columns:
                                        df_suc_c[col] = pd.to_numeric(df_suc_c[col], errors="coerce").astype("float32")
                                    else:
                                        df_suc_c[col] = float("nan")
                                for col, val in [("comercio_razon_social", razon),
                                                 ("comercio_bandera_nombre", bandera),
                                                 ("sucursales_provincia", "")]:
                                    if col not in df_suc_c.columns:
                                        df_suc_c[col] = val
                                    df_suc_c[col] = df_suc_c[col].fillna("").astype(str)
                                cols_s = ["id_comercio","id_sucursal","comercio_razon_social",
                                          "comercio_bandera_nombre","sucursales_provincia",
                                          "sucursales_latitud","sucursales_longitud"]
                                df_suc_out = df_suc_c[cols_s].drop_duplicates(["id_comercio","id_sucursal"])
                                writer_suc.write_table(
                                    pa.Table.from_pandas(df_suc_out, schema=schema_suc, preserve_index=False))
                                n_suc += len(df_suc_out)
                            except Exception as e:
                                log.warning(f"  Suc {inner_name}: {e}")

                        # ── Productos en chunks — nunca carga el CSV completo ──
                        n_prod += _procesar_productos_chunked(
                            z_inner, csv_producto, cadena, writers_prod, schema_prod)

                finally:
                    try: os.remove(inner_tmp)
                    except FileNotFoundError: pass
                    del df_com, df_suc_c
                    gc.collect()

            except Exception as e:
                errores += 1
                log.warning(f"Error ZIP {inner_name}: {e}")

            if (i + 1) % 5 == 0:
                log.info(f"  {i+1}/{len(inner_zips)} — suc:{n_suc:,} prod:{n_prod:,}")

            # Liberar DataFrames del comercio procesado antes del siguiente
            gc.collect()

    _fz_outer.close()  # liberar el fd del ZIP outer

    writer_suc.close()
    for w in writers_prod.values():
        w.close()
    # zip_path ya fue removido del filesystem antes del procesamiento
    for p in [os.path.join(CACHE_DIR, f"_inner_tmp_dia{dia}.zip")]:
        try: os.remove(p)
        except Exception: pass

    log.warning(f"[DIAG] ZIPs sin cadena: {n_zips_sin_cadena}, con cadena: {n_zips_por_cadena}")
    log.warning(f"Streaming completo: {n_suc:,} suc, {n_prod:,} prod, {errores} errores")
    if n_prod == 0:
        for p in list(paths_prod_tmp.values()) + [path_suc_tmp]:
            try: os.remove(p)
            except FileNotFoundError: pass
        raise ValueError("No se encontraron productos válidos en el ZIP")

    # Validar y promover cada parquet por cadena
    paths_prod_final: dict[str, str] = {}
    for cad, tmp_p in paths_prod_tmp.items():
        final_p = cache_base + f"_prod_{cad}.parquet"
        try:
            pq.read_metadata(tmp_p)
            os.replace(tmp_p, final_p)
            paths_prod_final[cad] = final_p
        except Exception as e:
            log.warning(f"Parquet {cad} inválido, se descarta: {e}")
            try: os.remove(tmp_p)
            except FileNotFoundError: pass

    try:
        pq.read_metadata(path_suc_tmp)
        os.replace(path_suc_tmp, path_suc)
    except Exception as e:
        raise ValueError(f"Parquet sucursales inválido: {e}") from e

    if not paths_prod_final:
        raise ValueError("Ningún parquet de productos válido generado")

    df_suc = pd.read_parquet(path_suc)
    log.info(f"Sucursales cargadas en memoria: {len(df_suc):,}")
    # prod_path ahora es un dict serializado como JSON string
    import json
    return df_suc, json.dumps(paths_prod_final)


def _cache_utilizable(dia: int, fecha_cache: str, prod_path: str) -> bool:
    """
    ¿Sirve esta entrada de caché?

    Antes la condición era simplemente `fecha_cache == hoy`. Con el fallback del
    roadmap 0.2 también aceptamos datos viejos, pero SOLO si el día está marcado
    como degradado — así un parquet viejo no se cuela silenciosamente cuando la
    descarga del día sí funcionó. Sin esto, cachear datos viejos haría que cada
    request los descarte y vuelva a intentar la descarga.
    """
    if not _prod_path_valido(prod_path):
        return False
    if fecha_cache == _hoy():
        return True
    return _degradado.get(dia) == fecha_cache


def _buscar_parquet_fallback(dia: int) -> Optional[tuple]:
    """
    Roadmap 0.2 — Busca en CACHE_DIR el parquet utilizable más reciente para
    servir datos viejos cuando la descarga del SEPA falla.

    Orden de preferencia:
      1. Mismo día de la semana, fecha anterior (dataset equivalente).
      2. Cualquier otro día, la fecha más reciente. Los precios del SEPA no
         dependen del weekday — el `dia` solo selecciona promos en _analizar().
         En Render /tmp se borra al dormirse, así que sin este paso el fallback
         casi nunca encontraría nada.

    Descarta lo más viejo que SEPA_FALLBACK_MAX_DIAS.
    Devuelve (df_suc, prod_path_json, fecha) o None.
    """
    import glob, json as _json

    hoy    = _hoy()
    hoy_dt = datetime.strptime(hoy, "%Y-%m-%d")

    candidatos: list[tuple[str, int, str]] = []   # (fecha, dia_archivo, cache_base)
    for suc_path in glob.glob(os.path.join(CACHE_DIR, "sepa_dia*_*_suc.parquet")):
        m = re.match(r"sepa_dia(\d+)_(\d{4}-\d{2}-\d{2})_suc\.parquet$",
                     os.path.basename(suc_path))
        if not m:
            continue
        dia_arch, fecha = int(m.group(1)), m.group(2)
        if dia_arch == dia and fecha == hoy:
            continue          # es justo el que acaba de fallar
        try:
            antiguedad = (hoy_dt - datetime.strptime(fecha, "%Y-%m-%d")).days
        except ValueError:
            continue
        if antiguedad < 0 or antiguedad > SEPA_FALLBACK_MAX_DIAS:
            continue
        candidatos.append((fecha, dia_arch, suc_path[: -len("_suc.parquet")]))

    if not candidatos:
        log.warning(f"[FALLBACK] No hay parquets viejos utilizables en {CACHE_DIR}")
        return None

    # Mismo weekday primero; dentro de cada grupo, la fecha más reciente
    candidatos.sort(key=lambda c: (
        0 if c[1] == dia else 1,
        -datetime.strptime(c[0], "%Y-%m-%d").toordinal(),
    ))

    for fecha, dia_arch, cache_base in candidatos:
        suc_path   = cache_base + "_suc.parquet"
        paths_prod = {}
        for cad in CADENAS_KEYWORDS.keys():
            pp = cache_base + f"_prod_{cad}.parquet"
            if os.path.exists(pp):
                paths_prod[cad] = pp
        if not paths_prod:
            continue
        try:
            pq.read_metadata(suc_path)
            for pp in paths_prod.values():
                pq.read_metadata(pp)
            df_suc = pd.read_parquet(suc_path)
        except Exception as e:
            log.warning(f"[FALLBACK] Parquet {os.path.basename(cache_base)} inválido: {e}")
            continue
        log.warning(
            f"[FALLBACK] Sirviendo datos VIEJOS del {fecha} (archivo del día {dia_arch}) "
            f"para el día {dia}: {len(df_suc):,} sucursales, {len(paths_prod)} cadenas"
        )
        return df_suc, _json.dumps(paths_prod), fecha

    log.warning("[FALLBACK] Había candidatos pero ninguno resultó legible")
    return None


def _cargar_o_descargar(dia: int, permitir_fallback: bool = True) -> tuple:
    """
    Devuelve (df_suc, prod_path). df_prod NO se cachea en RAM.

    `permitir_fallback=False` lo usa _bg_descargar para que cada reintento sea
    una descarga real: el fallback recién se aplica cuando se agotan todos.
    """
    fecha_hoy = _hoy()

    # 1. Memoria (solo df_suc + path al parquet)
    if dia in _cache:
        df_suc, prod_path, fecha_cache = _cache[dia]
        if _cache_utilizable(dia, fecha_cache, prod_path):
            log.info(f"Cache en memoria dia {dia}" +
                     (f" (DEGRADADO, datos del {fecha_cache})" if dia in _degradado else ""))
            return df_suc, prod_path

    # 2. Disco (parquet del día de hoy)
    cache_base = os.path.join(CACHE_DIR, f"sepa_dia{dia}_{fecha_hoy}")
    suc_path  = cache_base + "_suc.parquet"
    import json as _json
    # Buscar parquets por cadena (nuevo formato)
    paths_prod_cand = {}
    for cad in CADENAS_KEYWORDS.keys():
        p = cache_base + f"_prod_{cad}.parquet"
        if os.path.exists(p):
            paths_prod_cand[cad] = p
    if os.path.exists(suc_path) and paths_prod_cand:
        try:
            pq.read_metadata(suc_path)
            for p in paths_prod_cand.values():
                pq.read_metadata(p)
        except Exception as e:
            log.warning(f"Parquet en disco dia {dia} inválido, se descargará de nuevo: {e}")
            for p in [suc_path] + list(paths_prod_cand.values()):
                try: os.remove(p)
                except FileNotFoundError: pass
        else:
            log.info(f"Cargando parquet suc dia {dia} desde disco")
            t0 = time.perf_counter()
            df_suc = pd.read_parquet(suc_path)
            log.info(f"Suc cargado en {time.perf_counter()-t0:.1f}s: {len(df_suc):,} filas")
            _cache.clear()
            prod_path = _json.dumps(paths_prod_cand)
            _cache[dia] = (df_suc, prod_path, fecha_hoy)
            _degradado.pop(dia, None)   # hay parquet de hoy: ya no estamos degradados
            # Sin esto, /api/status seguía mostrando el ultimo_error de una
            # descarga vieja aunque los datos de hoy ya estuvieran cargados.
            _registrar_exito(dia, len(df_suc))
            return df_suc, prod_path

    # 3. Descargar y procesar
    _registrar_intento(dia)
    try:
        df_suc, prod_path = _descargar_y_procesar(dia)
    except Exception as e:
        _registrar_error(dia, e)
        log.error(f"Descarga día {dia} falló: {type(e).__name__}: {e}")
        if not permitir_fallback:
            raise
        # 4. Fallback (roadmap 0.2): antes de dejar la app muerta, servir el
        #    parquet más reciente que haya en disco, marcando que es viejo.
        fb = _buscar_parquet_fallback(dia)
        if fb is None:
            raise
        df_suc, prod_path, fecha_fb = fb
        _cache.clear()
        _cache[dia] = (df_suc, prod_path, fecha_fb)
        _degradado[dia] = fecha_fb
        return df_suc, prod_path

    _registrar_exito(dia, len(df_suc))
    _degradado.pop(dia, None)
    _cache.clear()
    _cache[dia] = (df_suc, prod_path, fecha_hoy)
    return df_suc, prod_path


async def _obtener_datos(dia: int) -> tuple:
    """
    Punto único de acceso a los datos SEPA. Garantiza que solo una descarga
    corra a la vez para un mismo día, sin importar cuántos endpoints la pidan.

    Flujo:
      1. Fast-path: ya está en _cache → retorna inmediatamente sin tomar el lock.
      2. Toma el lock (serializa todas las llamadas concurrentes).
      3. Double-checked: otro task puede haber llenado el cache mientras esperábamos.
      4. Si el día ya está descargándose (raro con el lock, pero por las dudas) → espera.
      5. Marca en progreso → corre _cargar_o_descargar en threadpool → guarda en cache.
    """
    import asyncio

    # 1. Fast-path sin lock
    if dia in _cache:
        df_suc, prod_path, fecha_cache = _cache[dia]
        if _cache_utilizable(dia, fecha_cache, prod_path):
            return df_suc, prod_path

    lock = _get_lock()
    async with lock:
        # 3. Double-checked locking
        if dia in _cache:
            df_suc, prod_path, fecha_cache = _cache[dia]
            if _cache_utilizable(dia, fecha_cache, prod_path):
                return df_suc, prod_path

        if dia in _descarga_en_progreso:
            # No debería llegar acá con el lock adquirido, pero por seguridad
            raise HTTPException(503, f"Descarga del día {dia} en progreso, reintentá en unos segundos")

        _descarga_en_progreso.add(dia)
        try:
            loop = asyncio.get_event_loop()
            df_suc, prod_path = await loop.run_in_executor(None, _cargar_o_descargar, dia)
            return df_suc, prod_path
        except Exception:
            # Si falló, sacar del progreso para permitir reintentos
            _descarga_en_progreso.discard(dia)
            raise
        finally:
            _descarga_en_progreso.discard(dia)


def _filtrar_radio(df_suc: pd.DataFrame, lat: float, lon: float, radio_km: float) -> pd.DataFrame:
    """Filtra sucursales por radio Haversine (numpy puro)."""
    lat_arr = pd.to_numeric(df_suc["sucursales_latitud"],  errors="coerce").values.astype("float64")
    lon_arr = pd.to_numeric(df_suc["sucursales_longitud"], errors="coerce").values.astype("float64")
    valid = ~(np.isnan(lat_arr) | np.isnan(lon_arr))
    lv, lnv = lat_arr[valid], lon_arr[valid]
    dlat = np.radians(lv - lat); dlon = np.radians(lnv - lon)
    a    = np.sin(dlat/2)**2 + np.cos(np.radians(lat)) * np.cos(np.radians(lv)) * np.sin(dlon/2)**2
    dist = 6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    mask = np.zeros(len(df_suc), dtype=bool)
    mask[np.where(valid)[0]] = dist <= radio_km
    return df_suc[mask]


def _buscar_precios(df_suc, prod_path, canasta, lat=None, lon=None, radio_km=5.0, provincia=None):
    """Core de búsqueda de precios. Devuelve dict {(cadena, producto): precio_min}.
    
    Lee df_prod desde disco con pushdown filter por cadena — nunca carga el parquet
    completo en RAM. Esto es la principal defensa contra OOM en Railway.
    """
    t0 = time.perf_counter()

    log.info(f"_buscar_precios: total_suc={len(df_suc)}, lat={lat}, lon={lon}, radio_km={radio_km}, provincia={provincia}")

    # Filtro geográfico sobre sucursales únicas
    if lat is not None and lon is not None:
        df_suc_filtrado = _filtrar_radio(df_suc, lat, lon, radio_km)
        log.info(f"Sucursales en radio {radio_km}km: {len(df_suc_filtrado)}")
        if df_suc_filtrado.empty:
            n_con_coord = df_suc[
                df_suc["sucursales_latitud"].notna() & df_suc["sucursales_longitud"].notna()
            ].shape[0]
            log.warning(
                f"Radio {radio_km}km vacío — {n_con_coord}/{len(df_suc)} suc tienen coords. "
                f"No se expande el radio para no mostrar sucursales de otras ciudades."
            )
            return {}
        df_suc = df_suc_filtrado
    elif provincia:
        mask = df_suc["sucursales_provincia"].astype(str).str.contains(provincia, case=False, na=False)
        df_suc = df_suc[mask]
        log.info(f"Sucursales en provincia '{provincia}': {len(df_suc)}")

    if df_suc.empty:
        return {}

    # Asignar cadena sobre sucursales filtradas
    col_rs = "comercio_razon_social"
    vals = df_suc[col_rs].dropna().unique()
    # Construir mapa razon_social -> cadena usando bandera si está disponible
    col_ban = "comercio_bandera_nombre"
    mapa = {}
    for _, row in df_suc.drop_duplicates(col_rs).iterrows():
        razon_v = str(row.get(col_rs, "") or "")
        bandera_v = str(row.get(col_ban, "") or "") if col_ban in df_suc.columns else ""
        mapa[razon_v] = asignar_cadena(razon_v, bandera_v)
    df_suc = df_suc.copy()
    df_suc["_cadena"] = df_suc[col_rs].astype(str).map(mapa)
    df_suc_cad = df_suc[df_suc["_cadena"].notna()]
    if df_suc_cad.empty:
        return {}

    cadenas_validas = list(df_suc_cad["_cadena"].unique())
    log.info(f"Leyendo productos para cadenas: {cadenas_validas}")

    # prod_path es un JSON dict {cadena: path_parquet}
    # Leer SOLO los parquets de las cadenas relevantes — RAM mínima
    import json as _json
    try:
        paths_por_cadena = _json.loads(prod_path)
    except Exception:
        log.error("prod_path no es JSON válido")
        return {}

    t_read = time.perf_counter()
    dfs = []
    for cad in cadenas_validas:
        p = paths_por_cadena.get(cad)
        if p and os.path.exists(p):
            try:
                df_c = pd.read_parquet(p, columns=["_cadena", "_desc_norm", "_precio", "productos_descripcion"])
                df_c = df_c[df_c["_precio"] > 0]
                if not df_c.empty:
                    dfs.append(df_c)
                    log.info(f"  {cad}: {len(df_c):,} productos")
            except Exception as e:
                log.warning(f"Error leyendo parquet {cad}: {e}")
        else:
            log.warning(f"Sin parquet para cadena: {cad}")

    if not dfs:
        return {}
    df_cad = pd.concat(dfs, ignore_index=True)
    del dfs
    gc.collect()
    log.info(f"Productos cargados: {len(df_cad):,} ({time.perf_counter()-t_read:.1f}s)")

    # ── DIAGNÓSTICO TEMPORAL ──────────────────────────────────────────────────
    _PROD_DIAG = ["pollo", "tomate", "jabon", "picada"]
    for _term in _PROD_DIAG:
        _mask = df_cad["_desc_norm"].str.contains(_term, na=False)
        log.warning(f"[DIAG] '{_term}' — total filas: {_mask.sum()}")
        for _cad_u in df_cad.loc[_mask, "_cadena"].unique():
            _sub = df_cad.loc[_mask & (df_cad["_cadena"] == _cad_u), "_desc_norm"]
            log.warning(f"  {_cad_u}: {len(_sub)} filas — ejemplos: {_sub.head(3).tolist()}")
    # ─────────────────────────────────────────────────────────────────────────

    df_cad["_desc_norm"] = df_cad["_desc_norm"].astype(str)

    # Pre-calcular cantidades de todas las descripciones únicas una sola vez
    _cache_cantidades: dict[str, list] = {}
    for desc in df_cad["_desc_norm"].unique():
        _cache_cantidades[desc] = _extraer_cantidades_desc(desc)

    # Búsqueda por producto
    precios = {}
    for prod in canasta:
        nombre = prod["nombre"]
        kws    = prod.get("palabras_clave", [nombre])
        marca  = prod.get("marca", "")
        cant_t = prod.get("cantidad_texto", "")

        prod_toks = tokens(nombre)
        log.info(f"[MATCH] '{nombre}' → tokens={prod_toks}")
        if not prod_toks:
            continue

        # Nivel 1: AND de todos los tokens del nombre
        mask = pd.Series(True, index=df_cad.index)
        for tok in prod_toks:
            mask &= df_cad["_desc_norm"].str.contains(re.escape(tok), na=False)
        df_match = df_cad[mask]

        # Nivel 2: AND por cada palabra_clave completa (no OR de tokens sueltos)
        if df_match.empty:
            for kw in kws:
                kw_toks = tokens(kw)
                if not kw_toks:
                    continue
                m = pd.Series(True, index=df_cad.index)
                for tok in kw_toks:
                    m &= df_cad["_desc_norm"].str.contains(re.escape(tok), na=False)
                df_match = df_cad[m]
                if not df_match.empty:
                    break

        # Nivel 3 (último recurso): OR de tokens — solo si hay 2+ tokens
        if df_match.empty and len(prod_toks) >= 2:
            mask_or = pd.Series(False, index=df_cad.index)
            for tok in prod_toks:
                mask_or |= df_cad["_desc_norm"].str.contains(re.escape(tok), na=False)
            df_match = df_cad[mask_or]

        if df_match.empty:
            continue

        # ── Selección: más barato por cadena, sin scoring de cantidad ────────
        marca         = prod.get("marca", "")
        marcas_acepta = prod.get("marcas_aceptadas", [])
        marc_toks     = tokens(marca)
        df_match      = df_match.copy()

        # Si hay lista de marcas aceptadas, filtrar estrictamente
        if marcas_acepta:
            any_match = pd.Series(False, index=df_match.index)
            for m in marcas_acepta:
                for tok in tokens(m):
                    any_match |= df_match["_desc_norm"].str.contains(re.escape(tok), na=False)
            df_match = df_match[any_match]
            if df_match.empty:
                continue

        if df_match.empty:
            continue

        def _calcular_precio_unitario(df_top: pd.DataFrame, precio_min: float) -> dict | None:
            """
            Calcula precio por unidad base del match más barato.
            Devuelve dict con:
              - valor: precio normalizado ($/100g, $/100ml, o $/unidad)
              - tipo: "peso" | "volumen" | "count"
              - label: "$/100g" | "$/100ml" | "$/unidad (pack xN)"
              - desc_ganadora: descripción original del producto ganador
              - cantidad_base: gramos/ml/unidades contenidos en el producto
            """
            _LABEL = {"peso": "$/100g", "volumen": "$/100ml", "count": "$/unidad"}
            candidatos = df_top[df_top["_precio"] == precio_min].head(5)
            for _, row in candidatos.iterrows():
                desc = row["_desc_norm"]
                desc_orig = row.get("productos_descripcion", desc)
                cantidades = _cache_cantidades.get(desc) or _extraer_cantidades_desc(desc)
                for val_base, tipo in cantidades:
                    if val_base > 0 and tipo in ("peso", "volumen", "count"):
                        if tipo == "count":
                            valor = round(precio_min / val_base, 2)
                            label = f"$/unidad (pack x{int(val_base)})"
                        else:
                            valor = round(precio_min / val_base * 100, 2)
                            label = _LABEL[tipo]
                        return {
                            "valor": valor,
                            "tipo": tipo,
                            "label": label,
                            "desc_ganadora": str(desc_orig),
                            "cantidad_base": round(val_base, 1),
                        }
            return None

        def _mejor_por_cadena(grupo):
            """
            Devuelve la fila con el precio más barato del grupo (una cadena).
            Si hay marca preferida, prioriza productos que la contienen;
            si ninguno la tiene, usa todos. Filtra outliers por debajo del 40%
            de la mediana antes de elegir el mínimo.
            """
            if marc_toks:
                tiene_marca = pd.Series(False, index=grupo.index)
                for tok in marc_toks:
                    tiene_marca |= grupo["_desc_norm"].str.contains(re.escape(tok), na=False)
                candidatos = grupo[tiene_marca] if tiene_marca.any() else grupo
            else:
                candidatos = grupo

            mediana = candidatos["_precio"].median()
            candidatos = candidatos[candidatos["_precio"] >= mediana * 0.40]
            if candidatos.empty:
                return candidatos
            return candidatos.loc[[candidatos["_precio"].idxmin()]]

        top = df_match.groupby("_cadena", group_keys=False).apply(_mejor_por_cadena)

        for cadena, grp in top.groupby("_cadena"):
            if grp.empty:
                continue
            pmin = grp.iloc[0]["_precio"]
            if pd.notna(pmin) and pmin > 0:
                p_unitario = _calcular_precio_unitario(grp, pmin)
                precios[(str(cadena), nombre)] = {
                    "precio_min": float(pmin),
                    "precio_por_100u": p_unitario,
                }

    # ── Precios manuales: completan huecos que el SEPA no cubre ──────────────
    # Solo se aplican si la cadena esta en el radio (cadenas_validas) Y el
    # producto no fue encontrado en el SEPA (no sobreescribe datos reales).
    manuales = _leer_precios_manuales()
    for cadena_m, productos_m in manuales.items():
        if cadena_m not in cadenas_validas:
            continue  # cadena no esta cerca del usuario
        for nombre_m, datos_m in productos_m.items():
            key = (cadena_m, nombre_m)
            if key in precios:
                continue  # el SEPA ya tiene precio, no pisar
            precio_m = datos_m.get("precio_min", 0)
            if precio_m > 0:
                precios[key] = {
                    "precio_min": float(precio_m),
                    "precio_por_100u": None,
                    "fuente": "manual",
                    "nota": datos_m.get("nota", ""),
                }
                log.info(f"[MANUAL] {cadena_m} / {nombre_m}: ${precio_m}")
    # ─────────────────────────────────────────────────────────────────────────

    log.info(f"buscar_precios: {len(precios)} precios en {time.perf_counter()-t0:.1f}s")
    return precios


def _analizar(canasta, precios, promos, dia):
    """
    Calcula totales y mejor promo por cadena.

    Cada promo puede tener un campo 'medio' ("credito"|"debito"|"billetera"|"cualquiera").
    Solo se aplica si el medio coincide con lo que el usuario declaró tener,
    o si la promo no tiene restricción de medio ("cualquiera").

    Las promos con cuotas_sin_interes > 0 no generan reintegro directo pero
    se reportan como 'mejor_promo' si no hay descuento en pesos mayor.
    """
    resultado = {}
    for cadena in CADENAS_KEYWORDS:
        total, n = 0.0, 0
        detalle = []
        for prod in canasta:
            entry = precios.get((cadena, prod["nombre"]))
            if entry:
                p = entry["precio_min"]
                p100 = entry.get("precio_por_100u")
                sub = p * prod["cantidad"]
                total += sub; n += 1
                detalle.append({"producto": prod["nombre"], "precio_unit": p,
                                 "precio_por_100u": p100,
                                 "cantidad": prod["cantidad"], "subtotal": sub, "ok": True})
            else:
                detalle.append({"producto": prod["nombre"], "precio_unit": None,
                                 "precio_por_100u": None,
                                 "cantidad": prod["cantidad"], "subtotal": 0, "ok": False})

        mejor_promo, mejor_r, mejor_cuotas = None, 0.0, 0
        for pr in promos:
            if pr.get("dias_semana") and dia not in pr["dias_semana"]:
                continue
            if pr.get("cadenas_aplica") and cadena not in pr["cadenas_aplica"]:
                continue
            # Promos de solo cuotas (sin descuento en pesos): registrar como candidato
            # pero no compiten con reintegros reales.
            cuotas = pr.get("cuotas_sin_interes", 0)
            if cuotas > 0 and pr.get("descuento_pct", 0) == 0:
                if mejor_r == 0.0 and cuotas > mejor_cuotas:
                    mejor_cuotas = cuotas
                    mejor_promo = pr["nombre"]
                continue
            r = total * (pr["descuento_pct"] / 100)
            if pr.get("tope_reintegro", 0) > 0:
                r = min(r, pr["tope_reintegro"])
            if r > mejor_r:
                mejor_r, mejor_promo = r, pr["nombre"]
                mejor_cuotas = 0  # un reintegro real supera siempre a las cuotas

        if total > 0:
            resultado[cadena] = {
                "total_base": round(total, 2),
                "reintegro":  round(mejor_r, 2),
                "total_final": round(total - mejor_r, 2),
                "mejor_promo": mejor_promo,
                "mejor_cuotas_sin_interes": mejor_cuotas,
                "n_encontrados": n,
                "n_total": len(canasta),
                "detalle": detalle,
            }
    return resultado


def _canasta_optima(canasta, precios):
    """Estrategia de compra distribuida: cada producto en el super más barato."""
    items = []
    total_optimo = 0.0
    cadenas_usadas = {}
    for prod in canasta:
        mejor_cadena, mejor_precio, mejor_p100 = None, float("inf"), None
        precios_x_cadena = {}
        for cadena in CADENAS_KEYWORDS:
            entry = precios.get((cadena, prod["nombre"]))
            if entry:
                p = entry["precio_min"]
                precios_x_cadena[cadena] = p
                if p < mejor_precio:
                    mejor_precio, mejor_cadena = p, cadena
                    mejor_p100 = entry.get("precio_por_100u")
        if mejor_cadena:
            sub = mejor_precio * prod["cantidad"]
            total_optimo += sub
            precio_caro = max(precios_x_cadena.values())
            items.append({"producto": prod["nombre"], "cantidad": prod["cantidad"],
                           "cadena": mejor_cadena, "precio_unit": mejor_precio,
                           "precio_por_100u": mejor_p100,
                           "subtotal": sub, "ahorro": (precio_caro - mejor_precio) * prod["cantidad"],
                           "precios_por_cadena": precios_x_cadena, "ok": True})
            cadenas_usadas.setdefault(mejor_cadena, []).append(prod["nombre"])
        else:
            items.append({"producto": prod["nombre"], "cantidad": prod["cantidad"],
                           "cadena": None, "precio_unit": None, "precio_por_100u": None,
                           "subtotal": 0, "ahorro": 0, "precios_por_cadena": {}, "ok": False})
    return {"items": items, "total_optimo": round(total_optimo, 2), "cadenas_usadas": cadenas_usadas}


# ─────────────────────────────────────────────
#  MODELOS PYDANTIC
# ─────────────────────────────────────────────

class ProductoItem(BaseModel):
    nombre: str
    cantidad: float = 1.0
    unidad: str = "unidad"
    categoria: str = ""
    palabras_clave: list[str] = []
    marca: str = ""
    marcas_aceptadas: list[str] = []
    cantidad_texto: str = ""
    cantidad_pack: Optional[int] = None  # para packs: cuántas unidades internas (ej: 12 para "huevos x12")

class PromoItem(BaseModel):
    nombre: str
    banco: str
    medio: str = "cualquiera"   # "credito" | "debito" | "billetera" | "cualquiera"
    descuento_pct: float
    tope_reintegro: float = 0.0
    cadenas_aplica: list[str] = []
    dias_semana: list[int] = []
    cuotas_sin_interes: int = 0  # 0 = no aplica cuotas

class BancoSeleccionado(BaseModel):
    banco_id: str           # coincide con BANCOS_CATALOGO[*].id
    medios: list[str] = []  # ["credito"], ["debito"], ["credito","debito"], etc.

class ComparacionRequest(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None
    radio_km: float = 5.0
    provincia: Optional[str] = None
    dia: Optional[int] = None   # None = día actual
    canasta: list[ProductoItem] = []
    promos: list[PromoItem] = []
    # Nuevo: bancos seleccionados por el usuario en la pantalla de promos.
    # Si se envía esta lista, se ignora `promos` y se filtra PROMOS_DEFAULT
    # para incluir solo las promos del banco+medio declarado.
    # Si está vacío y `promos` también está vacío → sin promos (el usuario omitió el paso).
    bancos_seleccionados: list[BancoSeleccionado] = []


# ─────────────────────────────────────────────
#  LIFESPAN — precarga del día actual al arrancar
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Startup rapido sin precarga SEPA")
    yield


# ─────────────────────────────────────────────
#  APP
# ─────────────────────────────────────────────

app = FastAPI(title="Comparador SEPA", version="1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # En producción: poner tu dominio Vercel
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"status": "ok", "mensaje": "Comparador SEPA API"}


def _bg_descargar(dia: int):
    """
    Wrapper síncrono para BackgroundTasks de FastAPI.
    BackgroundTasks corre en un threadpool separado, no en el event loop,
    por lo que usamos _cargar_o_descargar (sync) directamente en lugar de
    asyncio.create_task (que se pierde silenciosamente si no hay event loop activo).
    """
    log.info(f"[BG] Iniciando descarga día {dia} "
             f"(hasta {SEPA_MAX_REINTENTOS} intentos, backoff base {SEPA_BACKOFF_BASE_S}s)")
    _descarga_en_progreso.add(dia)
    try:
        ultimo_exc: Optional[Exception] = None

        for intento in range(1, SEPA_MAX_REINTENTOS + 1):
            try:
                # permitir_fallback=False: cada intento tiene que ser una descarga
                # real, si no el primer fallo devolvería datos viejos y cortaría
                # el ciclo de reintentos.
                df_suc, prod_path = _cargar_o_descargar(dia, permitir_fallback=False)
                _cache[dia] = (df_suc, prod_path, _hoy())
                log.info(f"[BG] Descarga día {dia} OK en intento "
                         f"{intento}/{SEPA_MAX_REINTENTOS} — {len(df_suc):,} sucursales")
                return
            except Exception as e:
                ultimo_exc = e
                log.error(f"[BG] Intento {intento}/{SEPA_MAX_REINTENTOS} falló para día {dia}: "
                          f"{type(e).__name__}: {e}",
                          exc_info=(intento == SEPA_MAX_REINTENTOS))
                if intento < SEPA_MAX_REINTENTOS:
                    espera = SEPA_BACKOFF_BASE_S * (2 ** (intento - 1))   # 30, 60, 120...
                    _estado_descarga.setdefault(dia, {})["proximo_reintento"] = (
                        datetime.now() + timedelta(seconds=espera)
                    ).isoformat(timespec="seconds")
                    log.warning(f"[BG] Reintentando día {dia} en {espera}s")
                    # Seguro: BackgroundTasks corre en threadpool, no en el event loop.
                    time.sleep(espera)

        _estado_descarga.setdefault(dia, {}).pop("proximo_reintento", None)

        # Reintentos agotados → recién ahora, datos viejos (roadmap 0.2)
        log.error(f"[BG] Agotados {SEPA_MAX_REINTENTOS} intentos para día {dia}. "
                  f"Buscando parquet de fallback.")
        fb = _buscar_parquet_fallback(dia)
        if fb is None:
            log.error(f"[BG] Sin fallback disponible: el día {dia} queda SIN DATOS. "
                      f"Último error: {type(ultimo_exc).__name__}: {ultimo_exc}")
            return
        df_suc, prod_path, fecha_fb = fb
        _cache.clear()
        _cache[dia] = (df_suc, prod_path, fecha_fb)
        _degradado[dia] = fecha_fb
        log.warning(f"[BG] Día {dia} en MODO DEGRADADO con datos del {fecha_fb} — "
                    f"{len(df_suc):,} sucursales")
    finally:
        _descarga_en_progreso.discard(dia)

@app.post("/refresh")
async def refresh_sepa(background_tasks: BackgroundTasks):
    dia_hoy = datetime.now().weekday()

    # Si ya hay una descarga activa para hoy, no lanzar otra
    if dia_hoy in _descarga_en_progreso:
        return {"status": "en_progreso", "message": f"Descarga ya en curso para día {dia_hoy}"}

    # Invalidar cache en memoria
    _cache.pop(dia_hoy, None)
    # Un refresh manual fuerza descarga real: salimos del modo degradado para
    # que _cache_utilizable no siga aceptando el parquet viejo.
    _degradado.pop(dia_hoy, None)

    # Limpiar parquets del día de hoy en disco para forzar re-descarga limpia.
    # Esto elimina parquets viejos (ej: generados con float32) que podrían quedar
    # en /tmp de deployments anteriores y causar precios incorrectos.
    fecha_hoy = datetime.now().strftime("%Y-%m-%d")
    cache_base = os.path.join(CACHE_DIR, f"sepa_dia{dia_hoy}_{fecha_hoy}")
    archivos_a_borrar = [cache_base + "_suc.parquet"]
    for cad in CADENAS_KEYWORDS.keys():
        archivos_a_borrar.append(cache_base + f"_prod_{cad}.parquet")
    borrados = []
    for p in archivos_a_borrar:
        try:
            os.remove(p)
            borrados.append(os.path.basename(p))
        except FileNotFoundError:
            pass
    if borrados:
        log.info(f"/refresh: eliminados {len(borrados)} parquets del día {dia_hoy}: {borrados}")

    background_tasks.add_task(_bg_descargar, dia_hoy)

    return {
        "status": "ok",
        "message": f"Descarga iniciada para día {dia_hoy}. Monitoreá con GET /api/status cada 30s.",
        "parquets_eliminados": borrados,
    }


@app.get("/api/canasta/default")
def get_canasta_default():
    return {"canasta": CANASTA_DEFAULT, "promos": PROMOS_DEFAULT}



@app.get("/api/bancos")
def get_bancos(cadenas: str = ""):
    """
    Devuelve el catálogo de bancos/billeteras con promos activas en mayo 2026.
    Parámetro opcional `cadenas` (lista separada por coma): si se provee, filtra
    solo los bancos que tienen al menos una promo para alguna de esas cadenas.

    Ejemplo: /api/bancos?cadenas=Jumbo,Coto,Carrefour

    Cada banco incluye:
      - id, label, medios (lista: "credito"|"debito"|"billetera"), nota
      - cadenas_principales: cadenas donde aplica alguna promo
      - promos_hoy: promos del banco activas para el día actual (dia_semana actual)
    """
    dia_hoy = datetime.now().weekday()
    cadenas_filtro = {c.strip() for c in cadenas.split(",") if c.strip()} if cadenas else set()

    resultado = []
    for banco in BANCOS_CATALOGO:
        bid = banco["id"]

        # Promos activas hoy para este banco
        promos_hoy = [
            {
                "nombre": p["nombre"],
                "descuento_pct": p["descuento_pct"],
                "tope_reintegro": p.get("tope_reintegro", 0),
                "cadenas_aplica": p.get("cadenas_aplica", []),
                "medio": p.get("medio", "cualquiera"),
                "cuotas_sin_interes": p.get("cuotas_sin_interes", 0),
            }
            for p in PROMOS_DEFAULT
            if p["banco"] == bid
            and (not p.get("dias_semana") or dia_hoy in p["dias_semana"])
        ]

        # Todas las promos del banco (sin filtro de día) para mostrar el calendario
        todas_promos = [
            {
                "nombre": p["nombre"],
                "descuento_pct": p["descuento_pct"],
                "tope_reintegro": p.get("tope_reintegro", 0),
                "cadenas_aplica": p.get("cadenas_aplica", []),
                "dias_semana": p.get("dias_semana", []),
                "medio": p.get("medio", "cualquiera"),
                "cuotas_sin_interes": p.get("cuotas_sin_interes", 0),
            }
            for p in PROMOS_DEFAULT
            if p["banco"] == bid
        ]

        # Filtrar por cadenas si se solicitó
        if cadenas_filtro:
            cadenas_banco = set(banco.get("cadenas_principales", []))
            if not cadenas_banco.intersection(cadenas_filtro):
                continue

        resultado.append({
            **banco,
            "es_billetera": "billetera" in banco.get("medios", []),
            "promos_hoy": promos_hoy,
            "todas_promos": todas_promos,
            "tiene_promo_hoy": len(promos_hoy) > 0,
        })

    return {"bancos": resultado, "dia_hoy": dia_hoy}


@app.get("/api/cadenas")
def get_cadenas():
    return {"cadenas": list(CADENAS_KEYWORDS.keys())}


@app.get("/api/dias")
def get_dias():
    """Devuelve los próximos 7 días con nombre, fecha y número de día SEPA (0=lunes)."""
    from datetime import timedelta
    NOMBRES = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]
    hoy = datetime.now()
    dias = []
    for i in range(7):
        d = hoy + timedelta(days=i)
        dia_num = d.weekday()
        promos_dia = [p["nombre"] for p in PROMOS_DEFAULT
                      if not p.get("dias_semana") or dia_num in p["dias_semana"]]
        dias.append({
            "dia": dia_num,
            "label": ("Hoy · " if i == 0 else "Mañana · " if i == 1 else "") + NOMBRES[dia_num],
            "fecha": d.strftime("%d/%m"),
            "promos": promos_dia,
        })
    return {"dias": dias}


@app.get("/api/precios-manuales")
def get_precios_manuales():
    """Devuelve todos los precios manuales cargados."""
    return {"precios_manuales": _leer_precios_manuales(), "path": PRECIOS_MANUALES_PATH}


class PrecioManualItem(BaseModel):
    cadena: str
    producto: str
    precio_min: float
    nota: str = ""


@app.post("/api/precios-manuales")
def upsert_precio_manual(item: PrecioManualItem):
    """
    Agrega o actualiza un precio manual para una cadena/producto.
    El SEPA siempre tiene prioridad: el precio manual solo se usa
    cuando el SEPA no reporta ese producto para esa cadena.
    """
    if item.cadena not in CADENAS_KEYWORDS:
        raise HTTPException(400, f"Cadena desconocida: {item.cadena}. Validas: {list(CADENAS_KEYWORDS.keys())}")
    if item.precio_min <= 0:
        raise HTTPException(400, "precio_min debe ser mayor a 0")
    data = _leer_precios_manuales()
    data.setdefault(item.cadena, {})[item.producto] = {
        "precio_min": item.precio_min,
        "nota": item.nota,
        "actualizado": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _guardar_precios_manuales(data)
    log.info(f"[MANUAL] Guardado: {item.cadena} / {item.producto} = ${item.precio_min}")
    return {"ok": True, "guardado": {item.cadena: {item.producto: data[item.cadena][item.producto]}}}


@app.delete("/api/precios-manuales")
def delete_precio_manual(cadena: str, producto: str):
    """Elimina un precio manual especifico."""
    data = _leer_precios_manuales()
    if cadena not in data or producto not in data.get(cadena, {}):
        raise HTTPException(404, f"No existe precio manual para {cadena} / {producto}")
    del data[cadena][producto]
    if not data[cadena]:
        del data[cadena]
    _guardar_precios_manuales(data)
    return {"ok": True, "eliminado": f"{cadena} / {producto}"}


@app.get("/api/status")
def get_status():
    dia_hoy = datetime.now().weekday()
    dias_en_cache = []
    for dia, (_, prod_path, fecha) in _cache.items():
        dias_en_cache.append({
            "dia": dia,
            "fecha": fecha,
            "listo": _prod_path_valido(prod_path),
            "degradado": dia in _degradado,
        })
    listo_hoy   = any(d["dia"] == dia_hoy and d["listo"] for d in dias_en_cache)
    en_progreso = dia_hoy in _descarga_en_progreso

    est         = _estado_descarga.get(dia_hoy, {})
    degradado   = dia_hoy in _degradado
    fecha_datos = _cache.get(dia_hoy, (None, None, None))[2]

    if degradado:
        origen = "cache_viejo"
    elif listo_hoy:
        origen = "sepa_hoy"
    else:
        origen = None

    return {
        # ── claves existentes: el frontend las usa para el banner de caché ──
        "cache": dias_en_cache,
        "cache_dir": CACHE_DIR,
        "dia_hoy": dia_hoy,
        "listo": listo_hoy,
        "en_progreso": en_progreso,
        # ── roadmap 0.3: por qué NO está listo ──
        "ultimo_intento": est.get("ultimo_intento"),
        "ultimo_error": est.get("ultimo_error"),
        "ultimo_error_ts": est.get("ultimo_error_ts"),
        "intentos_fallidos": est.get("intentos_fallidos", 0),
        "ultimo_exito": est.get("ultimo_exito"),
        "proximo_reintento": est.get("proximo_reintento"),
        "max_reintentos": SEPA_MAX_REINTENTOS,
        # ── roadmap 0.2: qué datos estamos sirviendo ──
        "datos_degradados": degradado,
        "fecha_datos": fecha_datos,
        "origen": origen,
        "aviso_datos": _aviso_datos(dia_hoy),
    }


def _agrupar_en_canonicos(descripciones: list[str], umbral: float = 0.70) -> list[dict]:
    """
    Agrupa descripciones del SEPA por similitud de tokens y devuelve
    una lista de {"nombre": str, "variantes": int} ordenada por variantes desc.

    Algoritmo O(n²) liviano — solo se llama sobre los top ~200 resultados
    del scan, nunca sobre el dataset completo.

    Nombre canónico = tokens que aparecen en ≥ umbral de las descripciones
    del grupo, unidos con espacio. Si queda vacío, usa la descripción más corta.
    """
    # Pre-tokenizar (tokens semánticos, sin cantidades ni stopwords)
    tok_sets: list[set] = [tokens(d) for d in descripciones]

    grupos: list[list[int]] = []   # lista de índices por grupo
    asignado = [False] * len(descripciones)

    for i in range(len(descripciones)):
        if asignado[i]:
            continue
        grupo = [i]
        asignado[i] = True
        for j in range(i + 1, len(descripciones)):
            if asignado[j]:
                continue
            a, b = tok_sets[i], tok_sets[j]
            if not a or not b:
                continue
            overlap = len(a & b) / min(len(a), len(b))
            if overlap >= umbral:
                grupo.append(j)
                asignado[j] = True
        grupos.append(grupo)

    resultado = []
    for grupo in grupos:
        # Tokens que aparecen en ≥ umbral de los miembros del grupo
        n = len(grupo)
        conteo_tok: dict[str, int] = {}
        for idx in grupo:
            for t in tok_sets[idx]:
                conteo_tok[t] = conteo_tok.get(t, 0) + 1
        comunes = {t for t, c in conteo_tok.items() if c / n >= umbral}

        if comunes:
            # Reconstruir en el orden en que aparecen en la primera descripción
            primera_norm = normalizar(descripciones[grupo[0]])
            nombre = " ".join(t for t in primera_norm.split() if t in comunes)
        else:
            # Fallback: descripción normalizada más corta del grupo
            nombre = min((normalizar(descripciones[i]) for i in grupo), key=len)

        resultado.append({"nombre": nombre.strip(), "variantes": n})

    # Ordenar por variantes desc (grupos más grandes primero → más relevantes)
    resultado.sort(key=lambda x: -x["variantes"])
    return resultado


@app.get("/api/buscar-productos")
def buscar_productos(q: str = "", limite: int = 8):
    """
    Devuelve hasta `limite` nombres canónicos agrupados del SEPA que contengan `q`.
    Cada sugerencia tiene {"nombre": str, "variantes": int}.
    Lee el parquet row-group por row-group (max ~200k filas por parquet) — nunca
    carga el archivo completo en RAM.
    Devuelve lista vacía si el caché no está listo — graceful degradation.
    """
    q = q.strip()
    if len(q) < 2:
        return {"sugerencias": []}

    dia_hoy = datetime.now().weekday()
    if dia_hoy not in _cache:
        return {"sugerencias": []}

    _, prod_path, _ = _cache[dia_hoy]
    if not prod_path:
        return {"sugerencias": []}

    q_norm = normalizar(q)
    toks = [t for t in q_norm.split() if len(t) > 1]
    if not toks:
        return {"sugerencias": []}

    import json as _json
    try:
        paths_por_cadena = _json.loads(prod_path)
    except Exception:
        return {"sugerencias": []}

    parquet_paths = [p for p in paths_por_cadena.values() if os.path.exists(p)]
    if not parquet_paths:
        return {"sugerencias": []}

    try:
        # Fase 1: scan liviano — acumular descripciones únicas con frecuencia
        conteo: dict[str, int] = {}
        MAX_FILAS_SCAN = 200_000   # por parquet — nunca cargamos el archivo entero
        POOL_MAXIMO = limite * 30  # pool pre-agrupación; ~240 para limite=8

        for parquet_path in paths_por_cadena.values():
            if not os.path.exists(parquet_path):
                continue
            filas_vistas = 0
            try:
                pf = pq.ParquetFile(parquet_path)
                for rg_batch in pf.iter_batches(
                    batch_size=10_000,
                    columns=["_desc_norm", "productos_descripcion"],
                ):
                    if filas_vistas >= MAX_FILAS_SCAN:
                        break

                    chunk = rg_batch.to_pandas()
                    filas_vistas += len(chunk)

                    mask = pd.Series(True, index=chunk.index)
                    for tok in toks:
                        mask &= chunk["_desc_norm"].str.contains(re.escape(tok), na=False)

                    for desc in chunk.loc[mask, "productos_descripcion"]:
                        conteo[desc] = conteo.get(desc, 0) + 1

                    del chunk
                    if len(conteo) >= POOL_MAXIMO:
                        break
            except Exception as e:
                log.warning(f"buscar_productos parquet error ({parquet_path}): {e}")
                continue

        if not conteo:
            return {"sugerencias": []}

        # Fase 2: agrupar el pool y devolver nombres canónicos
        pool = sorted(conteo, key=lambda k: -conteo[k])[:POOL_MAXIMO]
        canonicos = _agrupar_en_canonicos(pool)[:limite]

        return {"sugerencias": canonicos}

    except Exception as e:
        log.warning(f"buscar_productos error: {e}")
        return {"sugerencias": []}


@app.post("/api/comparar")
async def comparar(req: ComparacionRequest):
    t0 = time.perf_counter()

    dia = req.dia if req.dia is not None else datetime.now().weekday()

    if req.lat is None and req.lon is None and req.provincia is None:
        raise HTTPException(400, "Requerido: lat+lon o provincia")

    try:
        df_suc, prod_path = await _obtener_datos(dia)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error cargando SEPA: {e}")
        raise HTTPException(503, f"Error cargando datos SEPA: {str(e)}")

    canasta = [p.model_dump() for p in req.canasta] if req.canasta else CANASTA_DEFAULT

    # Resolver promos:
    # 1. Si el cliente envía bancos_seleccionados → filtrar PROMOS_DEFAULT por banco+medio
    # 2. Si el cliente envía promos directamente → usarlas tal cual (modo avanzado / legacy)
    # 3. Si ninguna de las dos → sin promos (usuario omitió el paso)
    if req.bancos_seleccionados:
        medios_por_banco: dict[str, set] = {
            b.banco_id: set(b.medios) for b in req.bancos_seleccionados
        }
        promos = [
            p for p in PROMOS_DEFAULT
            if p["banco"] in medios_por_banco
            and (
                not medios_por_banco[p["banco"]]           # sin restricción de medio
                or p.get("medio", "cualquiera") == "cualquiera"
                or p.get("medio", "cualquiera") in medios_por_banco[p["banco"]]
            )
        ]
    elif req.promos:
        promos = [p.model_dump() for p in req.promos]
    else:
        promos = []

    precios = _buscar_precios(
        df_suc, prod_path, canasta,
        lat=req.lat, lon=req.lon, radio_km=req.radio_km,
        provincia=req.provincia
    )

    if not precios:
        raise HTTPException(
            404,
            f"No se encontraron sucursales de las cadenas conocidas dentro de {req.radio_km:.0f} km de tu ubicación. "
            f"El SEPA puede no tener cobertura en esta zona. "
            f"Probá aumentar el radio de búsqueda o consultá el endpoint /api/diagnostico-sucursales para ver qué hay disponible."
        )

    resultado    = _analizar(canasta, precios, promos, dia)
    optimo       = _canasta_optima(canasta, precios)

    # Ordenar cadenas por total_final
    ranking = sorted(
        [(cadena, d) for cadena, d in resultado.items() if d["total_base"] > 0],
        key=lambda x: x[1]["total_final"]
    )

    elapsed = time.perf_counter() - t0
    log.info(f"/comparar completado en {elapsed:.1f}s")

    fecha_datos = _cache.get(dia, (None, None, None))[2]   # "YYYY-MM-DD" o None

    return {
        "dia": dia,
        "elapsed_s": round(elapsed, 2),
        "ranking": [
            {
                "cadena": c,
                **d,
                "total_sin_promo": d["total_base"],          # alias explícito para el frontend
                "cobertura_pct": round(d["n_encontrados"] / d["n_total"] * 100, 1) if d["n_total"] else 0,
            }
            for c, d in ranking
        ],
        "optimo": optimo,
        "n_precios": len(precios),
        "fecha_datos": fecha_datos,   # cuándo se descargó el ZIP del SEPA
        # roadmap 0.2: True si estos precios NO son de hoy porque el SEPA falló
        "datos_degradados": dia in _degradado,
        "aviso_datos": _aviso_datos(dia),
    }


@app.post("/api/precargar/{dia}")
async def precargar(dia: int, background_tasks: BackgroundTasks):
    """Endpoint para precargar un día específico."""

    if dia not in range(7):
        raise HTTPException(400, "dia debe ser 0-6 (lunes=0, domingo=6)")

    # Si ya está descargando, no lanzar segunda descarga
    if dia in _descarga_en_progreso:
        return {"ok": True, "dia": dia, "status": "en_progreso",
                "message": f"Descarga ya en curso para día {dia}"}

    # Si ya está en cache válido, no hacer nada
    fecha_hoy = datetime.now().strftime("%Y-%m-%d")
    if dia in _cache:
        _, prod_path, fecha_cache = _cache[dia]
        if fecha_cache == fecha_hoy and _prod_path_valido(prod_path):
            return {"ok": True, "dia": dia, "status": "listo",
                    "message": f"Día {dia} ya en cache"}

    background_tasks.add_task(_bg_descargar, dia)

    return {
        "ok": True,
        "dia": dia,
        "status": "iniciado",
        "message": f"Precarga iniciada para dia {dia}. Monitoreá con GET /api/status"
    }



@app.get("/api/diagnostico-sucursales")
def diagnostico_sucursales(lat: float, lon: float, radio_km: float = 20.0):
    """
    Diagnóstico: qué sucursales del dataset existen cerca de las coordenadas dadas.
    Útil para verificar si el filtro geográfico funciona correctamente.
    Devuelve las sucursales más cercanas con su distancia real.
    """
    dia_hoy = datetime.now().weekday()
    if dia_hoy not in _cache:
        raise HTTPException(503, "Cache no disponible — esperá que termine la precarga")

    df_suc, _, _ = _cache[dia_hoy]

    lat_arr = pd.to_numeric(df_suc["sucursales_latitud"],  errors="coerce").values.astype("float64")
    lon_arr = pd.to_numeric(df_suc["sucursales_longitud"], errors="coerce").values.astype("float64")
    valid   = ~(np.isnan(lat_arr) | np.isnan(lon_arr))

    lv, lnv = lat_arr[valid], lon_arr[valid]
    dlat = np.radians(lv - lat); dlon = np.radians(lnv - lon)
    a    = np.sin(dlat/2)**2 + np.cos(np.radians(lat)) * np.cos(np.radians(lv)) * np.sin(dlon/2)**2
    dist_all = 6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))

    df_valid = df_suc[valid].copy()
    df_valid["_dist_km"] = dist_all
    df_radio  = df_valid[df_valid["_dist_km"] <= radio_km].sort_values("_dist_km")

    n_total   = len(df_suc)
    n_con_coord = int(valid.sum())
    n_en_radio  = len(df_radio)

    # Top 20 más cercanas
    cols_out = ["comercio_razon_social", "comercio_bandera_nombre",
                "sucursales_provincia", "sucursales_latitud", "sucursales_longitud", "_dist_km"]
    cols_out = [c for c in cols_out if c in df_radio.columns]
    top20 = df_radio[cols_out].head(20).to_dict(orient="records")

    for row in top20:
        row["_cadena_detectada"] = asignar_cadena(str(row.get("comercio_razon_social", "")), str(row.get("comercio_bandera_nombre", "")))
        row["_dist_km"] = round(row["_dist_km"], 2)

    return {
        "lat": lat, "lon": lon, "radio_km": radio_km,
        "total_sucursales_dataset": n_total,
        "con_coordenadas": n_con_coord,
        "en_radio": n_en_radio,
        "top20_mas_cercanas": top20,
    }


@app.get("/api/diagnostico-cencosud")
def diagnostico_cencosud():
    """
    Muestra TODAS las sucursales de Cencosud (Vea/Jumbo/Disco) en el dataset,
    incluyendo las que no tienen coordenadas.
    """
    try:
        dia_hoy = datetime.now().weekday()
        if dia_hoy not in _cache:
            raise HTTPException(503, "Cache no disponible — esperá que termine la precarga")

        cached = _cache[dia_hoy]
        df_suc = cached[0].copy()

        log.info(f"[diag-cencosud] columnas: {list(df_suc.columns)}")
        log.info(f"[diag-cencosud] total filas: {len(df_suc)}")

        col_rs  = next((c for c in df_suc.columns if "razon_social"   in c.lower()), None)
        col_ban = next((c for c in df_suc.columns if "bandera_nombre" in c.lower()), None)

        log.info(f"[diag-cencosud] col_rs={col_rs}, col_ban={col_ban}")

        if col_rs is None and col_ban is None:
            return {"error": "No se encontraron columnas razon_social ni bandera_nombre", "columnas": list(df_suc.columns)}

        mask = pd.Series([False] * len(df_suc), index=df_suc.index)
        if col_rs:
            mask = mask | df_suc[col_rs].astype(str).str.contains("Cencosud", case=False, na=False)
        if col_ban:
            mask = mask | df_suc[col_ban].astype(str).str.contains("Vea|Jumbo|Disco", case=False, na=False)

        cencosud = df_suc[mask].copy()
        log.info(f"[diag-cencosud] filas Cencosud: {len(cencosud)}")

        if cencosud.empty:
            return {
                "total_cencosud": 0,
                "con_coordenadas": 0,
                "sin_coordenadas": 0,
                "columnas_disponibles": list(df_suc.columns),
                "sucursales": [],
            }

        col_lat = next((c for c in cencosud.columns if "latitud"  in c.lower()), None)
        col_lon = next((c for c in cencosud.columns if "longitud" in c.lower()), None)
        if col_lat and col_lon:
            tiene_lat = pd.to_numeric(cencosud[col_lat], errors="coerce").notna()
            tiene_lon = pd.to_numeric(cencosud[col_lon], errors="coerce").notna()
            con_coords = int((tiene_lat & tiene_lon).sum())
        else:
            con_coords = 0

        cols_deseadas = [
            "sucursales_id", "sucursales_nombre", "sucursales_direccion",
            "sucursales_localidad", "sucursales_provincia",
            "sucursales_latitud", "sucursales_longitud",
            "comercio_bandera_nombre", "comercio_razon_social",
        ]
        cols = [c for c in cols_deseadas if c in cencosud.columns]

        records = []
        for row in cencosud[cols].itertuples(index=False):
            r = {}
            for c, v in zip(cols, row):
                r[c] = None if (isinstance(v, float) and math.isnan(v)) else v
            records.append(r)

        return {
            "total_cencosud": len(cencosud),
            "con_coordenadas": con_coords,
            "sin_coordenadas": len(cencosud) - con_coords,
            "sucursales": records,
        }

    except HTTPException:
        raise
    except Exception as e:
        log.exception("[diag-cencosud] ERROR")
        raise HTTPException(500, f"Error interno: {str(e)}")


@app.get("/api/diagnostico-zip")
def diagnostico_zip(dia: int = 1, n_inner: int = 3):
    """
    Endpoint de diagnóstico temporal. Descarga el ZIP SEPA de `dia`,
    inspecciona los primeros `n_inner` ZIPs internos y devuelve:
    - nombres de archivos dentro de cada inner ZIP
    - columnas de cada CSV (leyendo solo la cabecera, 2KB)
    - tipo detectado por _clasificar_csv
    - cadena detectada por asignar_cadena
    NO procesa productos, NO escribe nada a disco.
    Útil para entender por qué el filtro de cadena no está funcionando.
    """
    import shutil

    url = SEPA_URLS.get(dia)
    if not url:
        raise HTTPException(400, f"dia {dia} no válido")

    n_inner = max(1, min(n_inner, 5))  # techo de seguridad

    resultado = []
    zip_path = os.path.join(CACHE_DIR, f"_diag_dia{dia}.zip")

    try:
        log.info(f"[diag] Descargando ZIP dia {dia}...")
        resp = requests.get(url, stream=True, timeout=(15, 600))
        resp.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=4 * 1024 * 1024):
                if chunk:
                    f.write(chunk)
        log.info(f"[diag] Descarga completa")

        inner_tmp = os.path.join(CACHE_DIR, "_diag_inner.zip")

        with open(zip_path, "rb") as fz:
            with zipfile.ZipFile(fz) as z_outer:
                inner_zips = [n for n in z_outer.namelist()
                              if n.lower().endswith(".zip") and not n.endswith("/")]
                log.info(f"[diag] {len(inner_zips)} ZIPs internos — inspeccionando primeros {n_inner}")

                for inner_name in inner_zips[:n_inner]:
                    info_inner = {"inner_zip": inner_name, "csvs": [], "cadena_detectada": None, "razon": "", "bandera": ""}
                    try:
                        with z_outer.open(inner_name) as src, open(inner_tmp, "wb") as dst:
                            shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)

                        with zipfile.ZipFile(inner_tmp) as z_inner:
                            for csv_name in z_inner.namelist():
                                if not csv_name.lower().endswith(".csv"):
                                    continue
                                with z_inner.open(csv_name) as _fh:
                                    raw_head = _fh.read(2048)
                                sep_h = _detectar_sep(raw_head)
                                try:
                                    head_df = pd.read_csv(
                                        io.BytesIO(raw_head), sep=sep_h, dtype=str,
                                        nrows=2, on_bad_lines="skip")
                                    head_df.columns = [c.strip().lower().replace(" ", "_")
                                                       for c in head_df.columns]
                                    tipo = _clasificar_csv(head_df, csv_name)
                                    cols = list(head_df.columns)
                                    # muestra de valores de la primera fila para contexto
                                    fila0 = head_df.iloc[0].to_dict() if len(head_df) > 0 else {}
                                    # extraer razon/bandera si es CSV de comercio
                                    if tipo == "comercio":
                                        info_inner["razon"]   = str(fila0.get("comercio_razon_social", ""))
                                        info_inner["bandera"] = str(fila0.get("comercio_bandera_nombre", ""))
                                except Exception as e:
                                    tipo = f"error: {e}"
                                    cols = []
                                    fila0 = {}

                                info_inner["csvs"].append({
                                    "csv": csv_name,
                                    "sep": sep_h,
                                    "tipo": tipo,
                                    "columnas": cols,
                                    "fila0_muestra": {k: str(v)[:60] for k, v in list(fila0.items())[:6]},
                                })

                        cadena = asignar_cadena(info_inner["razon"], info_inner["bandera"])
                        info_inner["cadena_detectada"] = cadena

                    except Exception as e:
                        info_inner["error"] = str(e)
                    finally:
                        try: os.remove(inner_tmp)
                        except FileNotFoundError: pass

                    resultado.append(info_inner)

    finally:
        try: os.remove(zip_path)
        except FileNotFoundError: pass

    return {
        "dia": dia,
        "total_inner_zips_en_outer": len(inner_zips) if resultado else "?",
        "inspeccionados": len(resultado),
        "resultado": resultado,
    }
# ─────────────────────────────────────────────
#  DIAGNÓSTICO DE RED (roadmap 0.1 / fuentes alternativas)
# ─────────────────────────────────────────────

# Objetivos fijos y hardcodeados a propósito: el endpoint NO acepta URLs del
# usuario. Si aceptara, sería un SSRF de manual (T.1 del roadmap: la API es
# pública y sin auth). Para agregar un objetivo se toca esta lista y se deploya.
#
# `sonda`:
#   "range" → GET con Range: bytes=0-1023. Baja 1 KB, no 300 MB. Sirve para el
#             ZIP del SEPA sin quemar RAM ni tiempo.
#   "get"   → GET normal, se leen los primeros bytes y se corta.
_DIAG_TARGETS = [
    {
        "id": "sepa_zip",
        "desc": "El ZIP del SEPA que usa el backend (día lunes)",
        "url": SEPA_URLS[0],
        "sonda": "range",
        "critico": True,
    },
    {
        "id": "sepa_portal",
        "desc": "Portal de datos abiertos de Producción (host del SEPA)",
        "url": "https://datos.produccion.gob.ar/dataset/sepa-precios",
        "sonda": "get",
        "critico": True,
    },
    {
        "id": "precios_claros",
        "desc": "Precios Claros — mismo programa oficial, otra infraestructura",
        "url": "https://www.preciosclaros.gob.ar/",
        "sonda": "get",
        "critico": False,
    },
    {
        # Verificado 09/09/2026: datos.gob.ar ya no lista ningún dataset de
        # "produccion" — el portal nacional dejó de federarlo. No es un mirror.
        "id": "precios_claros_api",
        "desc": "Precios Claros — endpoint de datos que consume su propia SPA",
        "url": "https://www.preciosclaros.gob.ar/api/sucursales?limit=1",
        "sonda": "get",
        "critico": False,
    },
    {
        "id": "vtex_carrefour",
        "desc": "API de catálogo VTEX (Carrefour) — fuente alternativa candidata",
        "url": "https://www.carrefour.com.ar/api/catalog_system/pub/products/search/?ft=leche&_from=0&_to=0",
        "sonda": "get",
        "critico": False,
    },
    {
        "id": "control_github",
        "desc": "CONTROL: si esto falla, el problema es la salida a internet de Render",
        "url": "https://api.github.com/zen",
        "sonda": "get",
        "critico": False,
    },
]

# El backend hoy sale con el User-Agent por defecto de requests
# ("python-requests/2.32.3"), que es un disparador clásico de WAF. Probamos las
# dos variantes para saber si el bloqueo es por UA o por IP: es la diferencia
# entre "se arregla con una línea" y "hay que cambiar de origen".
_DIAG_UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

_DIAG_CONNECT_TIMEOUT = 8
_DIAG_READ_TIMEOUT    = 12


def _diag_capa_red(host: str) -> dict:
    """
    Separa DNS de TCP. Es la distinción que importa: el CONTEXTO dice que desde
    Render los paquetes ni llegan (timeout de conexión) mientras que desde otros
    orígenes el borde contesta. Sin medir esto por separado no se puede saber si
    el bloqueo es de red o de aplicación.
    """
    import socket

    out = {"host": host}

    t0 = time.perf_counter()
    try:
        ips = sorted(set(socket.gethostbyname_ex(host)[2]))
        out["dns_ok"] = True
        out["dns_ms"] = round((time.perf_counter() - t0) * 1000)
        out["ips"] = ips
    except Exception as e:
        out["dns_ok"] = False
        out["dns_ms"] = round((time.perf_counter() - t0) * 1000)
        out["error"] = f"{type(e).__name__}: {e}"
        return out

    t0 = time.perf_counter()
    try:
        s = socket.create_connection((host, 443), timeout=_DIAG_CONNECT_TIMEOUT)
        s.close()
        out["tcp_ok"] = True
        out["tcp_ms"] = round((time.perf_counter() - t0) * 1000)
    except Exception as e:
        out["tcp_ok"] = False
        out["tcp_ms"] = round((time.perf_counter() - t0) * 1000)
        out["error"] = f"{type(e).__name__}: {e}"

    return out


def _diag_sonda_http(url: str, sonda: str, ua: Optional[str]) -> dict:
    """Un intento HTTP. Nunca baja más de ~1 KB del cuerpo."""
    headers = {}
    if ua:
        headers["User-Agent"] = ua
    if sonda == "range":
        headers["Range"] = "bytes=0-1023"

    t0 = time.perf_counter()
    try:
        resp = requests.get(
            url,
            headers=headers,
            stream=True,
            allow_redirects=True,
            timeout=(_DIAG_CONNECT_TIMEOUT, _DIAG_READ_TIMEOUT),
        )
        try:
            primeros = next(resp.iter_content(chunk_size=1024), b"") or b""
        finally:
            resp.close()

        return {
            "ok": resp.status_code < 400,
            "status": resp.status_code,
            "ms": round((time.perf_counter() - t0) * 1000),
            "content_type": resp.headers.get("content-type"),
            "content_length": resp.headers.get("content-length"),
            "server": resp.headers.get("server"),
            # Delatan al WAF cuando devuelve 403 sin decir por qué.
            "cf_ray": resp.headers.get("cf-ray"),
            "redirigido_a": resp.url if resp.url != url else None,
            "bytes_leidos": len(primeros),
        }
    except Exception as e:
        return {
            "ok": False,
            "status": None,
            "ms": round((time.perf_counter() - t0) * 1000),
            "error": f"{type(e).__name__}: {str(e)[:300]}",
        }


def _diag_un_target(t: dict) -> dict:
    from urllib.parse import urlparse

    host = urlparse(t["url"]).hostname or ""
    red = _diag_capa_red(host)

    res = {
        "id": t["id"],
        "desc": t["desc"],
        "url": t["url"],
        "critico": t["critico"],
        "red": red,
    }

    # Si ni siquiera hay TCP, las sondas HTTP solo agregan 20s de timeout.
    if not red.get("tcp_ok"):
        res["http_requests_ua"] = {"ok": False, "saltado": "sin TCP"}
        res["http_browser_ua"]  = {"ok": False, "saltado": "sin TCP"}
        res["veredicto"] = "BLOQUEO DE RED — los paquetes no llegan al host"
        return res

    # En paralelo: en serie el peor caso era 2x(connect+read) = 40s por objetivo,
    # demasiado para abrirlo desde el navegador.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_req = pool.submit(_diag_sonda_http, t["url"], t["sonda"], None)
        f_brw = pool.submit(_diag_sonda_http, t["url"], t["sonda"], _DIAG_UA_BROWSER)
        res["http_requests_ua"] = f_req.result()
        res["http_browser_ua"]  = f_brw.result()

    a = res["http_requests_ua"]
    b = res["http_browser_ua"]

    if a["ok"] and b["ok"]:
        res["veredicto"] = "OK"
    elif b["ok"] and not a["ok"]:
        res["veredicto"] = ("BLOQUEO POR USER-AGENT — anda con UA de navegador. "
                            "Se arregla mandando el header.")
    elif a["ok"] and not b["ok"]:
        res["veredicto"] = "RARO — anda con UA de requests pero no de navegador"
    elif a.get("status") or b.get("status"):
        st = b.get("status") or a.get("status")
        if st in (401, 403, 429):
            res["veredicto"] = (f"BLOQUEO DE APLICACIÓN (HTTP {st}) — el host contesta "
                                f"pero rechaza. WAF, geo o reputación de IP.")
        elif st in (404, 410):
            # No es un bloqueo: el host nos atiende bien, el recurso no está ahí.
            res["veredicto"] = (f"RECURSO INEXISTENTE (HTTP {st}) — el host responde "
                                f"normal. La URL cambió o el dataset se dio de baja.")
        elif st >= 500:
            res["veredicto"] = f"ERROR DEL SERVIDOR (HTTP {st}) — problema del origen, no nuestro."
        else:
            res["veredicto"] = f"HTTP {st} inesperado"
    else:
        err = (b.get("error") or a.get("error") or "")
        if "Timeout" in err:
            res["veredicto"] = "TIMEOUT HTTP — hay TCP pero la request no completa"
        elif "SSL" in err or "Certificate" in err:
            res["veredicto"] = f"FALLA TLS — TCP abre pero el handshake muere: {err[:120]}"
        else:
            res["veredicto"] = f"ERROR DE CONEXIÓN — {err[:160]}"

    return res


@app.get("/api/diagnostico-red")
def diagnostico_red():
    """
    Prueba, DESDE DONDE CORRE ESTE BACKEND, cada origen de datos candidato.

    Existe porque el 09/09/2026 descubrimos que el mismo host responde distinto
    según desde dónde se lo consulte: desde Render los paquetes no llegan, desde
    otros orígenes el borde contesta 403. Verificar desde un navegador no sirve
    para decidir nada.

    Por cada objetivo mide, en capas:
      1. DNS        → ¿resuelve? ¿a qué IPs?
      2. TCP :443   → ¿llegan los paquetes?   (acá muere hoy el SEPA en Render)
      3. HTTP x2    → con UA de requests y con UA de navegador

    No descarga datasets: usa Range para leer 1 KB. No escribe nada a disco.
    No acepta URLs del usuario: la lista de objetivos es fija (evita SSRF).
    """
    from concurrent.futures import ThreadPoolExecutor

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(_DIAG_TARGETS)) as pool:
        resultados = list(pool.map(_diag_un_target, _DIAG_TARGETS))

    criticos_ok = [r for r in resultados if r["critico"] and r["veredicto"] == "OK"]
    control     = next((r for r in resultados if r["id"] == "control_github"), None)

    if control and control["veredicto"] != "OK":
        resumen = ("La salida a internet de este host está rota o filtrada: falló "
                   "hasta el objetivo de control. El resto de los resultados no "
                   "es concluyente.")
    elif criticos_ok:
        resumen = "El SEPA es alcanzable desde acá. El problema no es de red."
    else:
        alt = [r["id"] for r in resultados
               if not r["critico"] and r["id"] != "control_github" and r["veredicto"] == "OK"]
        resumen = ("El SEPA NO es alcanzable desde acá. "
                   + (f"Sí responden: {', '.join(alt)}." if alt
                      else "Ninguna fuente alternativa respondió tampoco."))

    return {
        "ts": _ahora_iso(),
        "corriendo_en": os.environ.get("RENDER_SERVICE_NAME") or os.environ.get("HOSTNAME"),
        "region": os.environ.get("RENDER_REGION"),
        "ua_por_defecto": requests.utils.default_user_agent(),
        "duracion_ms": round((time.perf_counter() - t0) * 1000),
        "resumen": resumen,
        "objetivos": resultados,
    }


@app.get("/outliers")
def get_outliers(
    threshold: float = Query(default=3.0, gt=0),
    cadena: Optional[str] = Query(default=None),
    descripcion: Optional[str] = Query(default=None, description="Filtro parcial en _desc_norm"),
    limit: int = Query(default=100, le=1000),
):
    try:
        df = load_all_chains(PARQUET_DIR)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if cadena:
        df = df[df["_cadena"] == cadena]
    if descripcion:
        df = df[df["_desc_norm"].str.contains(descripcion, case=False, na=False)]

    outliers = detect_outliers(df=df, threshold=threshold)

    registros = (
        outliers.head(limit)
        .rename(columns={"_cadena": "cadena", "_precio": "precio",
                         "_desc_norm": "desc_norm",
                         "productos_descripcion": "descripcion"})
        .where(pd.notna(outliers.head(limit)), None)
        .to_dict(orient="records")
    )

    return {
        "resumen": outlier_summary(outliers),
        "threshold": threshold,
        "outliers": registros,
    }


@app.get("/outliers/cadenas")
def list_cadenas_outliers():
    try:
        df = load_all_chains(PARQUET_DIR)
        return {"cadenas": sorted(df["_cadena"].unique().tolist())}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/panel/outliers", response_class=HTMLResponse)
def panel_outliers():
    with open("static/outliers.html") as f:
        return f.read()