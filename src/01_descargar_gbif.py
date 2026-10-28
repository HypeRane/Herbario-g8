"""
Paso 1 · Descarga de registros de colecta desde GBIF (fuente estructurada).

Descarga los especímenes de herbario de uno o más géneros en Perú y los guarda
en data/gbif_registros.csv. Cada registro conserva su URL de GBIF como fuente.

Uso:
    python src/01_descargar_gbif.py                  # Polylepis (por defecto)
    python src/01_descargar_gbif.py Polylepis Gentianella
"""
import sys
import time
import unicodedata
from pathlib import Path

import pandas as pd
import requests

API = "https://api.gbif.org/v1"
SALIDA = Path("data/gbif_registros.csv")
MAX_POR_GENERO = 3000  # límite de seguridad

DEPARTAMENTOS = [
    "Amazonas", "Áncash", "Apurímac", "Arequipa", "Ayacucho", "Cajamarca", "Callao",
    "Cusco", "Huancavelica", "Huánuco", "Ica", "Junín", "La Libertad", "Lambayeque",
    "Lima", "Loreto", "Madre de Dios", "Moquegua", "Pasco", "Piura", "Puno",
    "San Martín", "Tacna", "Tumbes", "Ucayali",
]


def sin_tildes(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in texto if not unicodedata.combining(c)).lower().strip()


CANON = {sin_tildes(d): d for d in DEPARTAMENTOS}
CANON.update({"cuzco": "Cusco", "ancash": "Áncash"})


def normalizar_region(valor) -> str | None:
    """GBIF trae 'Ancash', 'Áncash', 'ANCASH', 'Dpto. Ancash'... -> 'Áncash'."""
    if not isinstance(valor, str) or not valor.strip():
        return None
    v = sin_tildes(valor).replace("departamento de", "").replace("dpto.", "").strip()
    for clave, canon in sorted(CANON.items(), key=lambda kv: -len(kv[0])):  # "huancavelica" antes que "ica"
        if clave in v:
            return canon
    return None


def binomio(nombre) -> str | None:
    """'Polylepis weberbaueri Pilg.' -> 'Polylepis weberbaueri'.
    'Polylepis Ruiz & Pav.' (solo género + autor) -> None: el epíteto va en minúscula."""
    if not isinstance(nombre, str):
        return None
    partes = nombre.split()
    if len(partes) < 2 or not partes[1][:1].islower():
        return None
    return " ".join(partes[:2])


RANGOS_INFRA = {"subsp.", "ssp.", "var.", "f."}


def nombre_con_infraespecie(nombre) -> str | None:
    """Como binomio(), pero conserva la subespecie o variedad:
    'Polylepis racemosa subsp. lanata (Kuntze) M.Kessler' -> 'Polylepis racemosa subsp. lanata'.
    Si se cortara a 'Polylepis racemosa', el grafo diría que P. racemosa es sinónimo de P. lanata."""
    base = binomio(nombre)
    if not base:
        return None
    partes = nombre.split()
    # 'P. racemosa f. racemosa' es la forma típica (el mismo taxón): queda como 'P. racemosa'
    if len(partes) >= 4 and partes[2] in RANGOS_INFRA and partes[3][:1].islower() and partes[3] != partes[1]:
        return f"{base} {partes[2]} {partes[3]}"
    return base


def clave_genero(genero: str) -> int | None:
    """Busca la clave del género en GBIF. Prueba la API v1 y la v2 (GBIF cambió el formato)."""
    intentos = [
        (f"{API}/species/match", {"name": genero, "rank": "GENUS", "kingdom": "Plantae"}),
        (f"{API}/species/match", {"name": genero}),
        ("https://api.gbif.org/v2/species/match", {"scientificName": genero, "taxonRank": "GENUS"}),
    ]
    for url, params in intentos:
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code != 200:
                continue
            datos = r.json()
            key = datos.get("usageKey") or (datos.get("usage") or {}).get("key")
            if key:
                print(f"  Clave GBIF de {genero}: {key}")
                return int(key)
        except Exception:
            continue
    print(f"  No se encontró la clave de {genero}; se buscará por nombre científico.")
    return None


def descargar(genero: str) -> list[dict]:
    key = clave_genero(genero)
    filtro_taxon = {"taxonKey": key} if key else {"scientificName": genero}
    filas, offset = [], 0
    while offset < MAX_POR_GENERO:
        r = requests.get(
            f"{API}/occurrence/search",
            params={
                **filtro_taxon,
                "country": "PE",
                "basisOfRecord": "PRESERVED_SPECIMEN",
                "limit": 300,
                "offset": offset,
            },
            timeout=60,
        )
        r.raise_for_status()
        pagina = r.json()
        for o in pagina["results"]:
            filas.append({
                "gbif_id": o.get("key"),
                "genero": o.get("genus"),
                "nombre_registrado": nombre_con_infraespecie(o.get("scientificName")),
                "nombre_aceptado": o.get("species") or binomio(o.get("acceptedScientificName")),
                "estado_taxonomico": o.get("taxonomicStatus"),
                "region_original": o.get("stateProvince"),
                "region": normalizar_region(o.get("stateProvince")),
                "localidad": o.get("locality"),
                "altitud": o.get("elevation"),
                "fecha": o.get("eventDate"),
                "colector": o.get("recordedBy"),
                "herbario": o.get("institutionCode"),
                "catalogo": o.get("catalogNumber"),
                "dataset": o.get("datasetName") or o.get("datasetKey"),
                "fuente_url": f"https://www.gbif.org/occurrence/{o.get('key')}",
            })
        print(f"  {genero}: {len(filas)} registros...")
        if pagina.get("endOfRecords"):
            break
        offset += 300
        time.sleep(0.5)  # respetar la API
    return filas


def main():
    generos = sys.argv[1:] or ["Polylepis"]
    todas = []
    for g in generos:
        print(f"Descargando {g} (Perú, especímenes de herbario)...")
        todas += descargar(g)
    df = pd.DataFrame(todas).drop_duplicates("gbif_id")
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(SALIDA, index=False)

    # Resumen para la diapositiva de corpus
    print("\n=== RESUMEN (anótalo para la presentación) ===")
    print(f"Registros totales: {len(df)}")
    print(f"Con región reconocida: {df['region'].notna().sum()}")
    print(f"Con altitud: {df['altitud'].notna().sum()}")
    con_nombres = df["nombre_registrado"].notna() & df["nombre_aceptado"].notna()  # vacío != vacío no es sinonimia
    sinon = df[con_nombres & (df["nombre_registrado"] != df["nombre_aceptado"])]
    print(f"Registrados con un nombre sinónimo: {len(sinon)}")
    print(f"Especímenes del Herbario San Marcos (USM): {(df['herbario'] == 'USM').sum()}")
    demo = df[(df["region"] == "Áncash") & (pd.to_numeric(df["altitud"], errors="coerce") > 4000)]
    print(f"Pregunta demo (Áncash > 4000 m): {len(demo)} registros, "
          f"{demo['nombre_aceptado'].nunique()} especies -> {sorted(demo['nombre_aceptado'].dropna().unique())}")
    print(f"\nGuardado en {SALIDA}")


if __name__ == "__main__":
    main()
