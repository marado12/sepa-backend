"""
outliers.py — Detección de precios anómalos adaptado al esquema SEPA real.
Columnas reales: _cadena, _desc_norm, _precio, productos_descripcion
"""

import os, json, logging, re
import pandas as pd
import pyarrow.parquet as pq


log = logging.getLogger(__name__)

PARQUET_DIR = os.environ.get("CACHE_DIR", "/tmp/sepa_cache")
THRESHOLD   = float(os.environ.get("OUTLIER_THRESHOLD", "3.0"))


def load_all_chains(parquet_dir: str = PARQUET_DIR) -> pd.DataFrame:
    """
    Carga los parquets de productos del día actual desde el cache en memoria.
    Usa la misma lógica de rutas que main.py: sepa_dia{dia}_{fecha}_prod_{cadena}.parquet
    Lee solo las columnas necesarias — nunca carga todo en RAM.
    """
    from datetime import datetime
    fecha_hoy = datetime.now().strftime("%Y-%m-%d")
    dia_hoy   = datetime.now().weekday()

    cache_base = os.path.join(parquet_dir, f"sepa_dia{dia_hoy}_{fecha_hoy}")

    CADENAS = ["Jumbo", "Disco", "Vea", "Coto", "Día", "Carrefour", "Chango Más"]
    COLS    = ["_cadena", "_desc_norm", "_precio", "productos_descripcion"]

    dfs = []
    for cadena in CADENAS:
        path = cache_base + f"_prod_{cadena}.parquet"
        if not os.path.exists(path):
            continue
        try:
            df = pd.read_parquet(path, columns=COLS)
            df = df[df["_precio"] > 0].copy()
            if not df.empty:
                dfs.append(df)
                log.info(f"outliers: {cadena} — {len(df):,} filas")
        except Exception as e:
            log.warning(f"outliers: error leyendo {cadena}: {e}")

    if not dfs:
        raise FileNotFoundError(
            f"No hay parquets disponibles en '{parquet_dir}' para el día de hoy. "
            f"Usá /api/precargar/{{dia}} primero."
        )

    combined = pd.concat(dfs, ignore_index=True)
    log.info(f"outliers: total {len(combined):,} filas de {len(dfs)} cadenas")
    return combined


def _asignar_grupos_canonicos(desc_series: pd.Series, umbral: float = 0.60) -> pd.Series:
    """
    Asigna un nombre de grupo canónico a cada descripción normalizada.
    Usa la misma lógica de tokens que _agrupar_en_canonicos en main.py.
    Umbral más bajo (0.60) que el de búsqueda (0.70) para ser más agresivo
    agrupando variantes del mismo bien (roast beef / roast beef churrasco).

    Retorna una Series con el mismo índice que desc_series, con el nombre
    canónico del grupo como valor.
    """

    STOPWORDS = {'en','al','de','la','el','lo','un','con','por','para','las','los','del','x'}

    def _tokens(texto: str) -> set:
        resultado = set()
        for t in str(texto).split():
            if len(t) <= 1 or t in STOPWORDS:
                continue
            # Preservar 000, 0000 (tipo de harina)
            if re.match(r'^0+$', t):
                resultado.add(t)
                continue
            # Filtrar cantidades con unidad y números puros
            if re.match(r'^\d+[.,]?\d*[lgmk]+$', t):
                continue
            if re.match(r'^\d+[.,]?\d*$', t):
                continue
            resultado.add(t)
        return resultado

    descripciones = desc_series.unique().tolist()
    tok_sets = [_tokens(d) for d in descripciones]

    grupos: list[list[int]] = []
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
            # Jaccard sobre el conjunto más pequeño
            overlap = len(a & b) / min(len(a), len(b))
            if overlap >= umbral:
                grupo.append(j)
                asignado[j] = True
        grupos.append(grupo)

    # Nombre canónico = tokens que aparecen en >= umbral de miembros del grupo
    desc_a_canonico: dict[str, str] = {}
    for grupo in grupos:
        n = len(grupo)
        conteo: dict[str, int] = {}
        for idx in grupo:
            for t in tok_sets[idx]:
                conteo[t] = conteo.get(t, 0) + 1
        comunes = {t for t, c in conteo.items() if c / n >= umbral}

        if comunes:
            primera_norm = descripciones[grupo[0]]
            nombre = " ".join(t for t in primera_norm.split() if t in comunes)
        else:
            nombre = min((descripciones[i] for i in grupo), key=len)

        nombre = nombre.strip() or descripciones[grupo[0]]
        for idx in grupo:
            desc_a_canonico[descripciones[idx]] = nombre

    return desc_series.map(desc_a_canonico)


def detect_outliers(
    df: pd.DataFrame | None = None,
    threshold: float = THRESHOLD,
    parquet_dir: str = PARQUET_DIR,
    umbral_agrupacion: float = 0.60,
) -> pd.DataFrame:
    """
    Detecta filas donde _precio > threshold × mediana del GRUPO CANÓNICO.
    Productos similares (roast beef, roast beef churrasco, roast beef novillito)
    se agrupan antes de calcular la mediana, así la comparación es justa.
    """
    if df is None:
        df = load_all_chains(parquet_dir)

    # Descartar precios inválidos antes de agrupar
    df = df.dropna(subset=["_precio"]).copy()
    df = df[df["_precio"] > 0]

    # Asignar grupo canónico a cada descripción
    log.info(f"outliers: agrupando {df['_desc_norm'].nunique():,} descripciones únicas...")
    df["grupo_canonico"] = _asignar_grupos_canonicos(df["_desc_norm"], umbral=umbral_agrupacion)

    # Mediana por grupo canónico (entre todas las cadenas)
    medianas = (
        df.groupby("grupo_canonico")["_precio"]
        .median()
        .rename("mediana_grupo")
        .reset_index()
    )

    merged = df.merge(medianas, on="grupo_canonico", how="left")
    merged = merged.dropna(subset=["mediana_grupo"])
    merged = merged[merged["mediana_grupo"] > 0]

    merged["ratio"]           = merged["_precio"] / merged["mediana_grupo"]
    merged["threshold_usado"] = threshold

    outliers = merged[merged["ratio"] > threshold].copy()
    return outliers.sort_values("ratio", ascending=False)


def outlier_summary(outliers: pd.DataFrame) -> dict:
    if outliers.empty:
        return {
            "total_outliers": 0,
            "productos_afectados": 0,
            "cadenas_afectadas": 0,
            "ratio_max": None,
            "producto_mas_anomalo": None,
        }
    return {
        "total_outliers": int(len(outliers)),
        "productos_afectados": int(outliers["grupo_canonico"].nunique()),
        "cadenas_afectadas": int(outliers["_cadena"].nunique()),
        "ratio_max": round(float(outliers["ratio"].max()), 2),
        "producto_mas_anomalo": str(
            outliers.iloc[0].get("productos_descripcion", outliers.iloc[0]["_desc_norm"])
        ),
    }