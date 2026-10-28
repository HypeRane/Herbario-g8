"""
Paso 4 · Agente GraphRAG.

Para cada pregunta combina dos recuperaciones:
  1. Grafo: el LLM traduce la pregunta a Cypher (con ejemplos), la ejecuta en Neo4j
     y obtiene hechos estructurados con su fuente.
  2. Semántica: busca los fragmentos de artículos más parecidos a la pregunta
     (índice vectorial en Neo4j).
Luego el LLM redacta la respuesta usando SOLO ese contexto y citando las fuentes.

Uso:
    python src/04_agente_graphrag.py "¿Qué especies de Polylepis se colectaron sobre 4000 m en Áncash?"
    python src/04_agente_graphrag.py --demo     # respaldo: consulta fija, sin LLM
"""
import argparse
import json
import os

from dotenv import load_dotenv
from langchain_core.prompts import PromptTemplate
from langchain_neo4j import GraphCypherQAChain, Neo4jGraph
from langchain_neo4j.chains.graph_qa.cypher_utils import CypherQueryCorrector

load_dotenv()


class CorrectorTolerante(CypherQueryCorrector):
    """El corrector de langchain-neo4j 0.11 devuelve '' (consulta vacía) ante patrones válidos como
    (:Localidad)-[:UBICADA_EN]->(:Region {nombre: 'Áncash'}). Si eso pasa, se usa la consulta original;
    si la corrige (p. ej. una flecha al revés), se usa la corregida."""

    def __call__(self, query: str) -> str:
        return super().__call__(query) or query


# La pregunta demo va AL FINAL: con llama3.1, si va primero el modelo copia mal su consulta
# (pone Áncash como :Localidad). Más cerca de la pregunta, la copia bien.
EJEMPLOS_CYPHER = """
Pregunta: ¿Quién colectó Polylepis racemosa en Cusco?
Cypher:
MATCH (e:Especie {{nombre: 'Polylepis racemosa'}})
MATCH (s:Especimen)-[:IDENTIFICADO_COMO]->(:Especie)-[:ES_SINONIMO_DE*0..1]->(e)
MATCH (s)-[:PROVIENE_DE]->(c:Colecta)-[:COLECTADO_EN]->(:Localidad)-[:UBICADA_EN]->(:Region {{nombre: 'Cusco'}})
MATCH (c)-[:COLECTADO_POR]->(p:Colector)
MATCH (s)-[:EXTRAIDO_DE]->(f:Fuente)
RETURN p.nombre AS colector, count(s) AS especimenes, collect(DISTINCT f.url)[..3] AS fuentes

Pregunta: ¿Con qué otros nombres se ha registrado Polylepis incana?
Cypher:
MATCH (sin:Especie)-[:ES_SINONIMO_DE]->(:Especie {{nombre: 'Polylepis incana'}})
RETURN sin.nombre AS sinonimo

Pregunta: ¿Qué especies de Polylepis se colectaron sobre 4000 m en Áncash?
Cypher:
MATCH (g:Genero {{nombre: 'Polylepis'}})<-[:PERTENECE_A]-(e:Especie)
MATCH (s:Especimen)-[:IDENTIFICADO_COMO]->(:Especie)-[:ES_SINONIMO_DE*0..1]->(e)
MATCH (s)-[:PROVIENE_DE]->(c:Colecta)-[:COLECTADO_EN]->(:Localidad)-[:UBICADA_EN]->(:Region {{nombre: 'Áncash'}})
WHERE c.altitudMetros > 4000
MATCH (s)-[:EXTRAIDO_DE]->(f:Fuente)
RETURN e.nombre AS especie, count(DISTINCT s) AS especimenes, collect(DISTINCT f.url)[..3] AS fuentes
"""

PROMPT_CYPHER = PromptTemplate(
    input_variables=["schema", "question"],
    template="""Eres un experto en Neo4j. Escribe UNA consulta Cypher que responda la pregunta.
Reglas:
- Usa solo las etiquetas, relaciones y propiedades del esquema.
- Si la pregunta se parece a un ejemplo, copia esa consulta y cambia solo los valores.
- Los departamentos (Áncash, Cusco, Lima...) son nodos :Region; :Localidad es un lugar dentro de una región.
- Las regiones se escriben con tilde: 'Áncash', 'Junín', 'Huánuco', 'Apurímac'.
- Para buscar por especie o género incluye siempre los sinónimos con [:ES_SINONIMO_DE*0..1].
- Devuelve siempre la fuente (f.url) uniendo el Especimen con (:Fuente) por [:EXTRAIDO_DE].
- Responde solo con la consulta, sin explicación ni ```.

Esquema:
{schema}

Ejemplos:
""" + EJEMPLOS_CYPHER + """
Pregunta: {question}
Cypher:""",
)

PROMPT_RESPUESTA = """Responde en español la pregunta usando SOLO el contexto.
Los hechos del grafo son la respuesta principal: ya vienen filtrados para esta pregunta. Si hay hechos,
menciona cada fila (por ejemplo cada especie con su número de especímenes) y, al lado, copia entre
corchetes una o dos URL de su campo "fuentes". Los fragmentos de artículos solo complementan.
Cita la fuente de cada dato entre corchetes, por ejemplo [https://www.gbif.org/occurrence/123] o [articulo.pdf, página 4].
Solo si no hay hechos del grafo ni fragmentos útiles, di que el contexto no alcanza; nunca inventes datos.
Termina después de la lista, sin frases de cierre.

Pregunta: {pregunta}

Hechos del grafo (Cypher):
{hechos}

Fragmentos de artículos (búsqueda semántica):
{fragmentos}

Respuesta:"""


def obtener_llm():
    proveedor = os.getenv("LLM_PROVIDER", "ollama").lower()
    modelo = os.getenv("LLM_MODEL")
    if proveedor == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=modelo or "claude-sonnet-5-5", temperature=0)
    if proveedor == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=modelo or "gpt-4o-mini", temperature=0)
    from langchain_ollama import ChatOllama
    return ChatOllama(model=modelo or "llama3.1", temperature=0)


def conectar() -> Neo4jGraph:
    return Neo4jGraph(url=os.environ["NEO4J_URI"], username=(os.getenv("NEO4J_USER") or os.environ["NEO4J_USERNAME"]),
                      password=os.environ["NEO4J_PASSWORD"], enhanced_schema=False)


def buscar_fragmentos(grafo: Neo4jGraph, pregunta: str, k: int = 3) -> list[dict]:
    try:
        from sentence_transformers import SentenceTransformer
        modelo = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        vector = modelo.encode(pregunta).tolist()
        return grafo.query(
            """CALL db.index.vector.queryNodes('fragmentos', $k, $v) YIELD node, score
               RETURN node.documento AS documento, node.pagina AS pagina,
                      left(node.texto, 600) AS texto, round(score, 3) AS similitud""",
            {"k": k, "v": vector},
        )
    except Exception as e:
        print(f"(búsqueda semántica no disponible: {e})")
        return []


def responder(pregunta: str) -> None:
    grafo = conectar()
    llm = obtener_llm()

    cadena = GraphCypherQAChain.from_llm(
        llm, graph=grafo, cypher_prompt=PROMPT_CYPHER, validate_cypher=True,
        return_direct=True, return_intermediate_steps=True, top_k=25,
        allow_dangerous_requests=True,  # solo lectura en la práctica; usar un usuario read-only en la nube
    )
    cadena.cypher_query_corrector = CorrectorTolerante(cadena.cypher_query_corrector.schemas)
    salida = cadena.invoke({"query": pregunta})
    cypher = salida["intermediate_steps"][0]["query"]
    hechos = salida["result"]
    fragmentos = buscar_fragmentos(grafo, pregunta)

    print("\n--- Cypher generado ---\n" + cypher)
    print(f"\n--- Hechos del grafo: {len(hechos)} filas ---")
    print(f"--- Fragmentos semánticos: {len(fragmentos)} ---")

    texto_frag = "\n".join(f"[{f['documento']}, página {f['pagina']}] {f['texto']}" for f in fragmentos) or "(ninguno)"
    respuesta = llm.invoke(PROMPT_RESPUESTA.format(
        pregunta=pregunta,
        hechos=json.dumps(hechos, ensure_ascii=False, indent=1) if hechos else "(ninguno)",
        fragmentos=texto_frag,
    ))
    print("\n=== RESPUESTA ===\n" + respuesta.content)


def demo_sin_llm() -> None:
    """Respaldo para la exposición: si falla el LLM, la consulta fija muestra el mismo resultado."""
    grafo = conectar()
    demo = EJEMPLOS_CYPHER.split("Pregunta: ¿Qué especies de Polylepis se colectaron sobre 4000 m en Áncash?")[1]
    consulta = demo.split("Cypher:\n")[1].split("\n\nPregunta")[0].strip().replace("{{", "{").replace("}}", "}")
    print("Consulta:\n" + consulta)
    for fila in grafo.query(consulta):
        print(fila)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pregunta", nargs="?", default="¿Qué especies de Polylepis se colectaron sobre 4000 m en Áncash?")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    demo_sin_llm() if args.demo else responder(args.pregunta)
