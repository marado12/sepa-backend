"""
El caché de `main` en un temporal propio, para los tests que lo importan.

`main.py:38-39` lee `CACHE_DIR` del entorno y hace `makedirs` AL IMPORTARSE, y `:52-54` lee
`PRECIOS_MANUALES_PATH` en el mismo momento. Sin fijarlos, un test corre contra el default
`/tmp/sepa_cache` (en Windows, `C:\\tmp\\sepa_cache`, que en la PC tiene parquets de mayo).

    from tests import cache_temporal
    cache_temporal.fijar()          # antes de `import main` y de todo lo que lo importe
    import main

y, en el archivo, un test que llame a `cache_temporal.afirmar_aislado(main)`.
"""
import os
import tempfile

# Los defaults de main.py:38 y :53, escritos a mano a propósito: si se leyeran de `main`,
# un main que ya apunta al default se compararía contra sí mismo.
DEFAULT_CACHE_DIR = "/tmp/sepa_cache"
DEFAULT_PRECIOS = os.path.join(DEFAULT_CACHE_DIR, "precios_manuales.json")


def fijar() -> str:
    """Apunta CACHE_DIR y PRECIOS_MANUALES_PATH a un mkdtemp nuevo. Pisa lo que traiga la shell."""
    tmp = tempfile.mkdtemp(prefix="sepa_test_")
    os.environ["CACHE_DIR"] = tmp
    os.environ["PRECIOS_MANUALES_PATH"] = os.path.join(tmp, "precios_manuales.json")
    return tmp


def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(p))


def _bajo(p: str, raiz: str) -> bool:
    p, raiz = _norm(p), _norm(raiz)
    try:
        return p != raiz and os.path.commonpath([p, raiz]) == raiz
    except ValueError:          # Windows: otra unidad
        return False


def afirmar_aislado(main) -> None:
    """
    Las dos rutas que usa `main` están bajo el temporal Y no son el default. Hacen falta las
    dos condiciones: en la CI (Linux) `gettempdir()` es `/tmp` y el default `/tmp/sepa_cache`
    también está debajo, así que "bajo el temporal" solo daría verde en falso.
    """
    tmp = tempfile.gettempdir()
    assert _bajo(main.CACHE_DIR, tmp), (
        f"[bajo el temporal] main.CACHE_DIR={main.CACHE_DIR!r} no está bajo "
        f"tempfile.gettempdir()={tmp!r}")
    assert _bajo(main.PRECIOS_MANUALES_PATH, tmp), (
        f"[bajo el temporal] main.PRECIOS_MANUALES_PATH={main.PRECIOS_MANUALES_PATH!r} no está "
        f"bajo tempfile.gettempdir()={tmp!r}")
    assert _norm(main.CACHE_DIR) != _norm(DEFAULT_CACHE_DIR), (
        f"[no es el default] main.CACHE_DIR={main.CACHE_DIR!r} es el default "
        f"{DEFAULT_CACHE_DIR!r} ({_norm(DEFAULT_CACHE_DIR)})")
    assert _norm(main.PRECIOS_MANUALES_PATH) != _norm(DEFAULT_PRECIOS), (
        f"[no es el default] main.PRECIOS_MANUALES_PATH={main.PRECIOS_MANUALES_PATH!r} es el "
        f"default {DEFAULT_PRECIOS!r} ({_norm(DEFAULT_PRECIOS)})")
