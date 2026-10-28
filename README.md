# HerbarioGraph

Agente GraphRAG que responde preguntas sobre colecciones botánicas del Perú citando la fuente de cada dato.
Proyecto Enterprise Brain · Gestión del Conocimiento · UNMSM-FISI 2026-II · Grupo 9 · Caso 5 (Herbario San Marcos).

## Flujo

```
GBIF (registros estructurados) ──┐
                                 ├─► Neo4j (grafo + fuente de cada hecho) ─► Agente GraphRAG
Artículos PDF ─► PLN (GLiNER) ───┘          ▲                                  (Cypher + búsqueda semántica)
                                     Ontología OWL (Darwin Core)
```

| Paso | Script | Salida |
|---|---|---|
| 1. Fuente estructurada | `src/01_descargar_gbif.py` | `data/gbif_registros.csv` |
| 2. Captura con PLN | `src/02_extraer_pln.py` | `data/tripletas.jsonl`, `data/fragmentos.jsonl` |
| 3. Documento → grafo | `src/03_cargar_neo4j.py` | Grafo en Neo4j con índice vectorial |
| 4. GraphRAG | `src/04_agente_graphrag.py` | Respuesta con citas |
| Ontología | `ontologia/herbariograph.ttl` (`.owl` para Protégé) | 11 clases, 10 propiedades de objeto, 6 de datos |
| Consultas | `consultas/consultas_demo.cypher` | Consultas de múltiples saltos para la demo |

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env          # y completar las credenciales
```

Neo4j: crear una instancia gratis en **Neo4j AuraDB Free** (console.neo4j.io) y copiar URI, usuario y contraseña al `.env`.

LLM: `LLM_PROVIDER=ollama` (gratis y local, `ollama pull llama3.1`), `anthropic` u `openai` (requieren API key).
Para la demo se recomienda un modelo con API: los modelos locales pequeños generan Cypher con más errores.

## Ejecución

```bash
python src/01_descargar_gbif.py Polylepis
# Descargar artículos en PDF a data/pdfs/ (ver abajo)
python src/02_extraer_pln.py
python src/03_cargar_neo4j.py --reset
python src/04_agente_graphrag.py "¿Qué especies de Polylepis se colectaron sobre 4000 m en Áncash?"
python src/04_agente_graphrag.py --demo     # respaldo sin LLM
```

Cada script imprime un **RESUMEN** con los números para la presentación.

## Corpus de artículos (PLN)

Artículos de acceso abierto de la **Revista Peruana de Biología** (revistasinvestigacion.unmsm.edu.pe/index.php/rpb)
y de *Arnaldoa*, buscando "Polylepis", "material examinado" o "Áncash". Se priorizan artículos taxonómicos y
florísticos, porque citan colectas con localidad, altitud, colector y herbario.

## Ontología

Clases: Taxón, Género, Especie, Espécimen, Colecta, Localidad, Región, Colector, Herbario, Publicación, Fuente.
Propiedades de objeto: perteneceA, identificadoComo, provieneDe, colectadoPor, colectadoEn, ubicadaEn,
depositadoEn, esSinonimoDe, citadoEn, extraidoDe.
Alineación: las clases heredan de Darwin Core (`dwc:Taxon`, `dwc:Occurrence`, `dwc:Event`), Dublin Core,
FOAF y PROV-O; las propiedades se relacionan con términos Darwin Core mediante `skos:closeMatch`.

## Limitaciones conocidas (avance 1)

- La extracción con PLN es una línea base (GLiNER + reglas). En dos muestras al azar de 15 colectas revisadas a mano
  se encontraron y corrigieron errores de localidad, colector y departamento; falta una medición formal de precisión.
- Casi todas las colectas extraídas (753 de 757) vienen de la monografía de Boza Espinoza & Kessler (2022). Los artículos
  ecológicos no citan especímenes y aportan solo fragmentos para la búsqueda semántica. Simpson (1979) es un escaneo
  con OCR dañado y no se usa para extraer colectas.
- Los nombres de colectores no se normalizan entre fuentes ("Smith", "D.N. Smith", "David N. Smith").
- Los nombres extraídos de artículos aún no se validan contra el backbone taxonómico de GBIF.
- Tropicos requiere API key; por ahora las sinonimias vienen de GBIF.

## Declaración de uso de IA

Según la guía del curso, se declara el uso de herramientas de IA:

| Herramienta | Uso |
|---|---|
| Claude (Anthropic) | Borrador inicial de los scripts, de la ontología y de las consultas Cypher; revisión de la estructura del avance. |
| Claude Code (Anthropic), 2026-10-28 | Paso 1: `binomio()` ya no cuenta "Polylepis Ruiz & Pav." (solo género) como especie; el resumen no cuenta como sinónimo un registro sin nombre; el nombre registrado conserva la subespecie o variedad ("P. racemosa subsp. lanata"), porque al cortarlo el grafo decía que P. racemosa era sinónimo de P. lanata. |
| Claude Code (Anthropic), 2026-10-28 | Búsqueda y descarga de 22 PDF de acceso abierto (lista en `data/pdfs/FUENTES.md`). |
| Claude Code (Anthropic), 2026-10-28 | Paso 2: la extracción lee el documento completo (las listas de especímenes cruzan páginas), quita cabeceras y pies de figura, toma la especie del encabezado de la monografía, descarta colectas fuera de Perú, segmenta las citas por el paréntesis de herbarios, reconoce colectores tipo "Boza 3032" / "Arce s.n.", cualquier acrónimo de herbario y los ejemplares tipo; filtra párrafos que no son citas y localidades erróneas de GLiNER. Antes: 572 colectas con especies basura ("Peru aprox", "Andean insect"); después: 757. |
| Claude Code (Anthropic), 2026-10-28 | Paso 3: cada colecta de un PDF tiene su propio nodo Fuente (antes las de una misma página compartían uno y se sobrescribía la cita); una sinonimia "pro parte" (un nombre que también es especie aceptada) no se carga como ES_SINONIMO_DE. |
| Claude Code (Anthropic), 2026-10-28 | Agente con Ollama (llama3.1): el corrector de Cypher de langchain-neo4j 0.11 vaciaba consultas válidas, así que si devuelve vacío se usa la consulta original; la pregunta demo va al final de los ejemplos (si va primero, llama3.1 la copia mal); el prompt de respuesta prioriza los hechos del grafo y pide citar sus URL. |

El grupo revisó, ejecutó y adaptó el código, y es responsable de su funcionamiento.
