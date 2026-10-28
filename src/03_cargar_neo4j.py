"""
Paso 3 · Flujo documento -> grafo (Neo4j).

Carga en Neo4j:
  - los registros de GBIF (data/gbif_registros.csv)
  - las colectas extraídas con PLN (data/tripletas.jsonl)
  - los fragmentos de texto con su embedding (data/fragmentos.jsonl), para la
    búsqueda semántica del agente

Cada espécimen queda unido a su Fuente (URL de GBIF o documento + página), así
que todo hecho del grafo es trazable.

Uso:
    python src/03_cargar_neo4j.py            # carga todo
    python src/03_cargar_neo4j.py --reset    # borra el grafo antes de cargar
"""
import argparse
import json
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

MODELO_EMBEDDINGS = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"  # 384 dim

RESTRICCIONES = [
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Genero) REQUIRE n.nombre IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Especie) REQUIRE n.nombre IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Especimen) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Colecta) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Localidad) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Region) REQUIRE n.nombre IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Colector) REQUIRE n.nombre IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Herbario) REQUIRE n.codigo IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Publicacion) REQUIRE n.titulo IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Fuente) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Fragmento) REQUIRE n.id IS UNIQUE",
]

# Un espécimen completo, con todas sus relaciones y su fuente.
# Los campos opcionales (localidad, colector, herbario, altitud) se omiten si vienen vacíos.
CARGA_ESPECIMEN = """
UNWIND $filas AS f
MERGE (g:Genero {nombre: f.genero})
MERGE (ea:Especie {nombre: f.nombre_aceptado})
MERGE (ea)-[:PERTENECE_A]->(g)
MERGE (er:Especie {nombre: f.nombre_registrado})
MERGE (gr:Genero {nombre: split(f.nombre_registrado, ' ')[0]})
MERGE (er)-[:PERTENECE_A]->(gr)
FOREACH (_ IN CASE WHEN f.nombre_registrado <> f.nombre_aceptado THEN [1] ELSE [] END |
    MERGE (er)-[:ES_SINONIMO_DE]->(ea))

MERGE (s:Especimen {id: f.id})
SET s.codigoCatalogo = f.catalogo
MERGE (s)-[:IDENTIFICADO_COMO]->(er)

MERGE (c:Colecta {id: f.id})
SET c.altitudMetros = f.altitud, c.fechaColecta = f.fecha
MERGE (s)-[:PROVIENE_DE]->(c)

MERGE (fu:Fuente {id: f.fuente_id})
SET fu.url = f.fuente_url, fu.tipo = f.tipo, fu.fragmentoTexto = f.fragmento
MERGE (s)-[:EXTRAIDO_DE]->(fu)

FOREACH (_ IN CASE WHEN f.region IS NOT NULL THEN [1] ELSE [] END |
    MERGE (r:Region {nombre: f.region})
    MERGE (l:Localidad {id: f.region + '|' + coalesce(f.localidad, 'sin localidad')})
    SET l.nombre = coalesce(f.localidad, 'sin localidad')
    MERGE (l)-[:UBICADA_EN]->(r)
    MERGE (c)-[:COLECTADO_EN]->(l))

FOREACH (_ IN CASE WHEN f.colector IS NOT NULL THEN [1] ELSE [] END |
    MERGE (p:Colector {nombre: f.colector})
    MERGE (c)-[:COLECTADO_POR]->(p))

FOREACH (_ IN CASE WHEN f.herbario IS NOT NULL THEN [1] ELSE [] END |
    MERGE (h:Herbario {codigo: f.herbario})
    MERGE (s)-[:DEPOSITADO_EN]->(h))

FOREACH (_ IN CASE WHEN f.publicacion IS NOT NULL THEN [1] ELSE [] END |
    MERGE (pub:Publicacion {titulo: f.publicacion})
    MERGE (s)-[:CITADO_EN]->(pub))
"""

CARGA_FRAGMENTO = """
UNWIND $filas AS f
MERGE (fr:Fragmento {id: f.id})
SET fr.texto = f.texto, fr.documento = f.documento, fr.pagina = f.pagina, fr.embedding = f.embedding
WITH fr, f
MERGE (pub:Publicacion {titulo: f.documento})
MERGE (fr)-[:EXTRAIDO_DE]->(pub)
"""


def limpio(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    v = str(v).strip()
    return v or None


def filas_gbif(ruta: Path) -> list[dict]:
    df = pd.read_csv(ruta, dtype={c: str for c in ["catalogo", "fecha", "localidad", "colector", "herbario", "region"]})
    df = df[df["nombre_aceptado"].notna() & df["nombre_registrado"].notna() & df["genero"].notna()]
    # Si el nombre registrado también es una especie aceptada (sinonimia "pro parte", p. ej. un registro
    # 'P. besseri' -> 'P. crista-galli'), la relación ES_SINONIMO_DE haría que TODOS los P. besseri
    # cuenten como P. crista-galli. En ese caso el espécimen se enlaza directo a su nombre aceptado.
    aceptados = set(df["nombre_aceptado"])
    filas = []
    for _, r in df.iterrows():
        alt = pd.to_numeric(r["altitud"], errors="coerce")
        registrado = r["nombre_aceptado"] if r["nombre_registrado"] in aceptados else r["nombre_registrado"]
        filas.append({
            "id": f"gbif:{int(r['gbif_id'])}",
            "genero": r["genero"],
            "nombre_aceptado": r["nombre_aceptado"],
            "nombre_registrado": registrado,
            "catalogo": limpio(r["catalogo"]),
            "altitud": None if pd.isna(alt) else float(alt),
            "fecha": limpio(r["fecha"]),
            "region": limpio(r["region"]),
            "localidad": limpio(r["localidad"]),
            "colector": limpio(r["colector"]),
            "herbario": limpio(r["herbario"]),
            "publicacion": None,
            "fuente_id": f"gbif:{int(r['gbif_id'])}",
            "fuente_url": r["fuente_url"],
            "tipo": "GBIF",
            "fragmento": None,
        })
    return filas


def filas_pln(ruta: Path) -> list[dict]:
    filas = []
    for i, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines()):
        r = json.loads(linea)
        especie = r["especie"]
        filas.append({
            "id": f"pln:{r['documento']}:{r['pagina']}:{i}",
            "genero": especie.split()[0],
            "nombre_aceptado": especie,   # el nombre se valida luego contra GBIF
            "nombre_registrado": especie,
            "catalogo": r.get("numero_colecta"),
            "altitud": r.get("altitud"),
            "fecha": r.get("fecha"),
            "region": r.get("region"),
            "localidad": r.get("localidad"),
            "colector": r.get("colector"),
            "herbario": r.get("herbario"),
            "publicacion": r["documento"],
            "fuente_id": f"pdf:{r['documento']}:p{r['pagina']}:{i}",  # una Fuente por colecta: guarda su cita exacta
            "fuente_url": f"{r['documento']} (página {r['pagina']})",
            "tipo": "PLN",
            "fragmento": r["fragmento"],
        })
    return filas


def en_lotes(datos, n=500):
    for i in range(0, len(datos), n):
        yield datos[i: i + n]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    driver = GraphDatabase.driver(os.environ["NEO4J_URI"],
                                  auth=((os.getenv("NEO4J_USER") or os.environ["NEO4J_USERNAME"]), os.environ["NEO4J_PASSWORD"]))
    with driver.session() as s:
        if args.reset:
            s.run("MATCH (n) DETACH DELETE n")
            print("Grafo borrado.")
        for q in RESTRICCIONES:
            s.run(q)

        for nombre, ruta, lector in [("GBIF", Path("data/gbif_registros.csv"), filas_gbif),
                                     ("PLN", Path("data/tripletas.jsonl"), filas_pln)]:
            if not ruta.exists():
                print(f"(sin {ruta}, se omite {nombre})")
                continue
            filas = lector(ruta)
            for lote in en_lotes(filas):
                s.run(CARGA_ESPECIMEN, filas=lote)
            print(f"{nombre}: {len(filas)} especímenes cargados")

        ruta_frag = Path("data/fragmentos.jsonl")
        if ruta_frag.exists():
            from sentence_transformers import SentenceTransformer
            modelo = SentenceTransformer(MODELO_EMBEDDINGS)
            frags = [json.loads(l) for l in ruta_frag.read_text(encoding="utf-8").splitlines()]
            vectores = modelo.encode([f["texto"] for f in frags], show_progress_bar=True)
            filas = [{**f, "id": f"{f['documento']}:p{f['pagina']}", "embedding": v.tolist()}
                     for f, v in zip(frags, vectores)]
            for lote in en_lotes(filas, 200):
                s.run(CARGA_FRAGMENTO, filas=lote)
            s.run("""CREATE VECTOR INDEX fragmentos IF NOT EXISTS
                     FOR (f:Fragmento) ON f.embedding
                     OPTIONS {indexConfig: {`vector.dimensions`: 384,
                                            `vector.similarity_function`: 'cosine'}}""")
            print(f"Fragmentos con embedding: {len(filas)}")

        print("\n=== RESUMEN DEL GRAFO (anótalo para la presentación) ===")
        for r in s.run("MATCH (n) RETURN labels(n)[0] AS tipo, count(*) AS total ORDER BY total DESC"):
            print(f"  {r['tipo']}: {r['total']}")
        rel = s.run("MATCH ()-[r]->() RETURN count(r) AS total").single()["total"]
        print(f"  Relaciones: {rel}")
    driver.close()


if __name__ == "__main__":
    main()
