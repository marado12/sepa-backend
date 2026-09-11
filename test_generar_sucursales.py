"""
Tests de `scripts/generar_sucursales.py`. Sin red, como el resto de la suite.

Lo que se protege acá es el archivo del que depende TODO el "cerca tuyo":
`data/sucursales.csv`. El script lo regenera y lo pisa. Si una corrida sale
degenerada —el parquet cambió de esquema, la clasificación de cadenas falla— el
resultado era un CSV casi vacío escrito encima del bueno, y recién lo cazaba el
CI, si alguien lo commiteaba. Mientras tanto la app se queda sin filtro
geográfico y le dice al usuario que agrande el radio.

Hallazgo 11 de claude/AUDITORIA-FALLAS-SILENCIOSAS.md.
"""
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import generar_sucursales as G  # noqa: E402

RAIZ = Path(__file__).resolve().parent


def _filas(n_por_cadena: dict) -> pd.DataFrame:
    """Parquet de entrada con la forma que tiene el del SEPA."""
    filas = []
    # razon social / bandera que asignar_cadena() sabe clasificar
    BANDERA = {"Día": "Supermercados DIA", "Carrefour": "Carrefour",
               "Vea": "Vea", "Coto": "Coto CICSA", "Chango Más": "SuperChangomas"}
    RAZON = {"Día": "DIA Argentina", "Carrefour": "DORINKA", "Vea": "Cencosud",
             "Coto": "COTO CICSA", "Chango Más": "INC S.A"}
    for cadena, n in n_por_cadena.items():
        for i in range(n):
            filas.append({
                "comercio_razon_social": RAZON[cadena],
                "comercio_bandera_nombre": BANDERA[cadena],
                "sucursales_provincia": "AR-B",
                "sucursales_latitud": -34.6 - i * 0.0001,
                "sucursales_longitud": -58.4 - i * 0.0001,
                "id_comercio": "1",
                "id_sucursal": str(i),
            })
    return pd.DataFrame(filas)


def _correr(df: pd.DataFrame, salida: Path):
    """Corre el script completo contra un parquet temporal y una salida temporal."""
    tmp_parquet = Path(tempfile.mkdtemp(prefix="gen_suc_")) / "suc.parquet"
    df.to_parquet(tmp_parquet)
    argv, salida_orig, raiz_orig = sys.argv, G.SALIDA, G.RAIZ
    sys.argv = ["generar_sucursales.py", str(tmp_parquet), "--main", str(RAIZ / "main.py")]
    # RAIZ solo se usa para mostrar la ruta relativa al final; sin esto el
    # script explota con ValueError al imprimir una salida fuera del repo.
    G.SALIDA, G.RAIZ = salida, salida.parent
    try:
        G.main()
        return 0
    except SystemExit as e:
        return e.code if e.code is not None else 0
    finally:
        sys.argv, G.SALIDA, G.RAIZ = argv, salida_orig, raiz_orig


def test_un_catalogo_degenerado_no_pisa_el_csv():
    """
    El caso que motiva el guard: la corrida sale con 3 sucursales en vez de 2.162.
    Tiene que abortar SIN escribir — el catálogo viejo es mejor que uno vacío.
    """
    salida = Path(tempfile.mkdtemp(prefix="gen_suc_out_")) / "sucursales.csv"
    code = _correr(_filas({"Día": 1, "Carrefour": 1, "Vea": 1}), salida)

    assert code != 0, "una corrida degenerada tiene que salir con código ≠ 0"
    assert not salida.exists(), "NO puede escribir el CSV con un resultado degenerado"


def test_si_falta_una_cadena_esperada_tampoco_escribe():
    """
    Suficientes filas pero sin Coto: es el síntoma de que la clasificación se
    rompió, no de que Coto haya cerrado todas sus sucursales.
    """
    salida = Path(tempfile.mkdtemp(prefix="gen_suc_out_")) / "sucursales.csv"
    code = _correr(_filas({"Día": 400, "Carrefour": 400, "Vea": 400,
                           "Chango Más": 400}), salida)

    assert code != 0
    assert not salida.exists()


def test_un_catalogo_sano_se_escribe_normal():
    """El guard no puede bloquear la corrida buena."""
    salida = Path(tempfile.mkdtemp(prefix="gen_suc_out_")) / "sucursales.csv"
    code = _correr(_filas({"Día": 400, "Carrefour": 400, "Vea": 400,
                           "Coto": 400, "Chango Más": 400}), salida)

    assert code == 0, f"la corrida sana no debería abortar (code={code})"
    assert salida.exists()
    escrito = pd.read_csv(salida)
    assert len(escrito) >= 1000
    assert set(escrito["cadena"]) >= {"Día", "Carrefour", "Vea", "Coto", "Chango Más"}


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
