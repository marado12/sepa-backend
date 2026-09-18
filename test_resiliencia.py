"""
Tests de los ítems 0.2 y 0.3 del roadmap.
Corre sin red: _descargar_y_procesar se mockea para simular el portal caído.
"""
import os, sys, shutil, tempfile
from datetime import datetime, timedelta

CACHE = tempfile.mkdtemp(prefix="sepa_test_")
os.environ["CACHE_DIR"] = CACHE
os.environ["SEPA_MAX_REINTENTOS"] = "3"
os.environ["SEPA_BACKOFF_BASE_S"] = "0"      # sin esperas reales en el test
os.environ["SEPA_FALLBACK_MAX_DIAS"] = "7"

import pandas as pd, pyarrow as pa, pyarrow.parquet as pq
import main

FALLOS = []
def check(cond, msg):
    print(("  OK   " if cond else "  FALLA ") + msg)
    if not cond: FALLOS.append(msg)

def crear_parquets(dia, fecha, n_suc=3):
    """Genera un set de parquets válidos como los que produce _descargar_y_procesar."""
    base = os.path.join(CACHE, f"sepa_dia{dia}_{fecha}")
    df_suc = pd.DataFrame({
        "id_comercio": [1]*n_suc, "id_sucursal": list(range(n_suc)),
        "comercio_razon_social": ["COTO CICSA"]*n_suc,
        "comercio_bandera_nombre": ["Coto"]*n_suc,
        "sucursales_provincia": ["AR-B"]*n_suc,
        "sucursales_latitud": [-34.58]*n_suc, "sucursales_longitud": [-60.93]*n_suc,
    })
    df_suc.to_parquet(base + "_suc.parquet")
    df_prod = pd.DataFrame({
        "productos_descripcion": ["LECHE ENTERA 1L"], "productos_precio_lista": [1500.0],
        "_cadena": ["Coto"], "_precio": [1500.0], "_desc_norm": ["leche entera 1 l"],
    })
    df_prod.to_parquet(base + "_prod_Coto.parquet")
    return base

def reset():
    main._cache.clear(); main._degradado.clear(); main._estado_descarga.clear()
    main._descarga_en_progreso.clear()

HOY   = datetime.now().strftime("%Y-%m-%d")
AYER  = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
VIEJO = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
DIA_HOY = datetime.now().weekday()
OTRO_DIA = (DIA_HOY + 1) % 7

# ── 1. Backoff: reintenta N veces antes de rendirse ──────────────────────────
print("\n[1] _bg_descargar reintenta con backoff")
reset()
llamadas, esperas = [], []
main._descargar_y_procesar = lambda d: (_ for _ in ()).throw(
    ConnectionError("Connection to datos.produccion.gob.ar timed out")) if llamadas.append(d) is None else None
main.time.sleep = lambda s: esperas.append(s)
main._bg_descargar(DIA_HOY)
check(len(llamadas) == 3, f"hizo 3 intentos (hizo {len(llamadas)})")
check(main._estado_descarga[DIA_HOY]["intentos_fallidos"] == 3,
      f"intentos_fallidos == 3 (es {main._estado_descarga[DIA_HOY].get('intentos_fallidos')})")
check("timed out" in (main._estado_descarga[DIA_HOY].get("ultimo_error") or ""),
      "ultimo_error guarda el mensaje real")
check(DIA_HOY not in main._descarga_en_progreso, "_descarga_en_progreso queda limpio")

# backoff exponencial real (con base 30 daría 30, 60)
os.environ["X"] = ""
main.SEPA_BACKOFF_BASE_S = 30
reset(); llamadas.clear(); esperas.clear()
main._bg_descargar(DIA_HOY)
check(esperas == [30, 60], f"backoff exponencial 30,60 (fue {esperas})")
main.SEPA_BACKOFF_BASE_S = 0

# ── 2. Sin fallback disponible: la app queda sin datos pero con error visible ─
print("\n[2] Sin parquet viejo: /api/status explica por qué")
st = main.get_status()
check(st["listo"] is False, "listo=False")
check(st["en_progreso"] is False, "en_progreso=False")
check(st["ultimo_error"] is not None, "ultimo_error presente (antes era None y no se sabía por qué)")
check(st["ultimo_intento"] is not None, "ultimo_intento presente")
check(st["intentos_fallidos"] == 3, "intentos_fallidos expuesto")
check(st["datos_degradados"] is False, "datos_degradados=False")
check(st["origen"] is None, "origen=None")

# ── 3. Fallback al parquet de ayer, mismo weekday ────────────────────────────
print("\n[3] Fallback: usa el parquet de ayer y lo marca como viejo")
reset()
crear_parquets(DIA_HOY, AYER)
main._bg_descargar(DIA_HOY)
check(DIA_HOY in main._cache, "el día quedó cacheado (la app NO queda muerta)")
check(main._degradado.get(DIA_HOY) == AYER, f"_degradado marca la fecha {AYER}")
st = main.get_status()
check(st["listo"] is True, "listo=True: la app funciona con datos viejos")
check(st["datos_degradados"] is True, "datos_degradados=True")
check(st["fecha_datos"] == AYER, f"fecha_datos={AYER}")
check(st["origen"] == "cache_viejo", "origen=cache_viejo")
check("de ayer" in (st["aviso_datos"] or ""), f"aviso_datos legible: {st['aviso_datos']!r}")
check(st["ultimo_error"] is not None, "sigue mostrando el error que causó el fallback")

# ── 4. El caché degradado se reutiliza (no re-descarga en cada request) ──────
print("\n[4] El caché degradado se reutiliza")
llamadas.clear()
df_suc, prod_path = main._cargar_o_descargar(DIA_HOY)
check(len(llamadas) == 0, "no volvió a intentar descargar")
check(len(df_suc) == 3, "devolvió las sucursales del parquet viejo")
# y sin la marca de degradado, ese mismo caché se rechaza
_, _, fecha_cache = main._cache[DIA_HOY]
check(main._cache_utilizable(DIA_HOY, fecha_cache, prod_path) is True, "utilizable mientras esté degradado")
main._degradado.pop(DIA_HOY)
check(main._cache_utilizable(DIA_HOY, fecha_cache, prod_path) is False,
      "NO utilizable si no está marcado degradado (datos viejos no se cuelan solos)")

# ── 5. Fallback cross-día ────────────────────────────────────────────────────
print("\n[5] Fallback cross-día (decisión 1)")
reset(); shutil.rmtree(CACHE); os.makedirs(CACHE)
crear_parquets(OTRO_DIA, AYER)
fb = main._buscar_parquet_fallback(DIA_HOY)
check(fb is not None, "encuentra el parquet de otro weekday")
check(fb[2] == AYER if fb else False, "con la fecha correcta")

# ── 6. Descarta parquets más viejos que SEPA_FALLBACK_MAX_DIAS ──────────────
print("\n[6] Corte por antigüedad")
reset(); shutil.rmtree(CACHE); os.makedirs(CACHE)
crear_parquets(DIA_HOY, VIEJO)
check(main._buscar_parquet_fallback(DIA_HOY) is None, "ignora un parquet de hace 20 días")

# ── 7. Éxito posterior: sale del modo degradado ─────────────────────────────
print("\n[7] Cuando el SEPA vuelve, sale del modo degradado")
reset(); shutil.rmtree(CACHE); os.makedirs(CACHE)
crear_parquets(DIA_HOY, AYER)
main._bg_descargar(DIA_HOY)                      # falla → degradado
check(DIA_HOY in main._degradado, "entró en degradado")
base_hoy = crear_parquets(DIA_HOY, HOY)
import json as _json
main._descargar_y_procesar = lambda d: (
    pd.read_parquet(base_hoy + "_suc.parquet"),
    _json.dumps({"Coto": base_hoy + "_prod_Coto.parquet"}),
)
main._cache.pop(DIA_HOY, None); main._degradado.pop(DIA_HOY, None)
main._bg_descargar(DIA_HOY)
check(DIA_HOY not in main._degradado, "salió del modo degradado")
st = main.get_status()
check(st["ultimo_error"] is None, "ultimo_error se limpia tras el éxito")
check(st["ultimo_exito"] is not None, "ultimo_exito registrado")
check(st["origen"] == "sepa_hoy", "origen=sepa_hoy")
check(st["intentos_fallidos"] == 0, "contador de fallos reseteado")

# ── 8. /api/status expone qué commit tiene desplegado Render (Tarea 27) ─────
print("\n[8] /api/status expone render_git_commit y render_git_branch")
st = main.get_status()
check("render_git_commit" in st, "la clave render_git_commit existe")
check("render_git_branch" in st, "la clave render_git_branch existe")
check(st.get("render_git_commit") is None, "en local, sin la env var de Render, da None")
check(st.get("render_git_branch") is None, "en local, sin la env var de Render, da None")
os.environ["RENDER_GIT_COMMIT"] = "abc1234"
os.environ["RENDER_GIT_BRANCH"] = "main"
st = main.get_status()
check(st["render_git_commit"] == "abc1234", "con la env var seteada, la refleja")
check(st["render_git_branch"] == "main", "con la env var seteada, la refleja")
os.environ.pop("RENDER_GIT_COMMIT"); os.environ.pop("RENDER_GIT_BRANCH")

shutil.rmtree(CACHE, ignore_errors=True)
print("\n" + ("="*60))
print(f"RESULTADO: {'TODO OK' if not FALLOS else str(len(FALLOS)) + ' FALLAS'}")
for f in FALLOS: print("  - " + f)
sys.exit(1 if FALLOS else 0)
