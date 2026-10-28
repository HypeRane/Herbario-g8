# CLAUDE.md · HerbarioGraph

Responde y escribe siempre en español. El usuario es Fabrizio, estudiante de Ingeniería de Sistemas (UNMSM-FISI),
trabaja en Windows con PowerShell, VS Code y un entorno virtual en `.venv`.

## Qué es el proyecto

Proyecto "Enterprise Brain" del curso Gestión del Conocimiento (UNMSM, 2026-II, Dr. José Alfredo Herrera Quispe).
Grupo 9, caso 5: Herbario San Marcos. Sistema **HerbarioGraph**: captura información de colectas botánicas con PLN,
la organiza en una ontología OWL y un grafo Neo4j con la fuente de cada hecho, y la consulta con un agente GraphRAG
que cita sus fuentes.

Pregunta demo (múltiples saltos): **¿Qué especies de Polylepis se colectaron sobre 4000 m en Áncash?**

## Entrega inmediata: primer avance (semana 10, GraphRAG)

Se presenta 10 minutos + 5 de preguntas. Rúbrica: contenido 8 pts (cumplir el hito de la semana), demostración
en vivo 6 pts (con fallos = 3), respuestas de todos los integrantes 6 pts. El alcance llega hasta el hito de la
semana 10. **No implementar todavía** despliegue en la nube, RAGAS, dashboard ni propuesta PROCIENCIA (semanas 11 a 14).

## Estructura

| Archivo | Función |
|---|---|
| `src/01_descargar_gbif.py` | Descarga especímenes de GBIF → `data/gbif_registros.csv` |
| `src/02_extraer_pln.py` | PLN sobre PDF en `data/pdfs/` (GLiNER + reglas) → `data/tripletas.jsonl`, `data/fragmentos.jsonl` |
| `src/03_cargar_neo4j.py` | Carga todo en Neo4j AuraDB con embeddings e índice vectorial `fragmentos` |
| `src/04_agente_graphrag.py` | Agente: pregunta → Cypher + búsqueda semántica → respuesta con citas. `--demo` = consulta fija sin LLM |
| `ontologia/herbariograph.ttl` / `.owl` | Ontología: 11 clases, 10 propiedades de objeto, 6 de datos, alineada a Darwin Core |
| `consultas/consultas_demo.cypher` | Consultas para la demo y las capturas |

Modelo del grafo: `(Especimen)-[:IDENTIFICADO_COMO]->(Especie)-[:PERTENECE_A]->(Genero)`,
`(Especie)-[:ES_SINONIMO_DE]->(Especie)`, `(Especimen)-[:PROVIENE_DE]->(Colecta)-[:COLECTADO_EN]->(Localidad)-[:UBICADA_EN]->(Region)`,
`(Colecta)-[:COLECTADO_POR]->(Colector)`, `(Especimen)-[:DEPOSITADO_EN]->(Herbario)`, `(Especimen)-[:EXTRAIDO_DE]->(Fuente)`,
`(Especimen)-[:CITADO_EN]->(Publicacion)`, `(Fragmento)-[:EXTRAIDO_DE]->(Publicacion)`.

## Estado actual

- [x] Entorno instalado, `.env` con credenciales de Neo4j AuraDB Free.
- [x] Paso 1 (2026-10-28): 2944 registros GBIF, 478 USM, 30 con nombre sinónimo, 190 registros sobre 4000 m en
      Áncash (6 especies). El nombre registrado conserva subsp./var.
- [x] Paso 2: 22 PDF en `data/pdfs/` (ver FUENTES.md), 604 páginas, 757 colectas.
- [x] Paso 3: grafo cargado: 13 346 nodos, 23 175 relaciones, 32 especies, 8 sinonimias.
- [x] Paso 4: consulta 1 devuelve 6 especies (245 especímenes, 57 de PDF) con fuentes; `--demo` funciona.
- [x] Agente con LLM: Ollama 0.40.12 + llama3.1 instalados (2026-10-28). La pregunta demo responde las 6 especies
      con URL de GBIF en ~35-40 s. En preguntas abiertas llama3.1 a veces resume solo la primera fila.
- [x] Presentación completa: integrantes (diap. 1), captura de Protégé (diap. 5, `ontologia/cap1.png`),
      captura de Neo4j (diap. 6, `consultas/consulta2.png`) y salida real del agente (diap. 7).

## Tareas, en orden

1. Volver a correr `python src/01_descargar_gbif.py Polylepis` y anotar el RESUMEN.
2. Conseguir 15 a 30 artículos PDF de acceso abierto que citen colectas de *Polylepis* en Perú, sobre todo con
   sección "Material examinado" / "Especímenes examinados". Fuentes: Revista Peruana de Biología
   (revistasinvestigacion.unmsm.edu.pe/index.php/rpb, plataforma OJS), Arnaldoa, SciELO Perú. Guardarlos en
   `data/pdfs/` con nombres descriptivos (`autor_anio_tema.pdf`). Usar solo PDF de acceso abierto; registrar en
   `data/pdfs/FUENTES.md` el título y la URL de cada uno.
3. Correr `python src/02_extraer_pln.py`. Revisar a mano una muestra de `data/tripletas.jsonl`: si las colectas
   extraídas son pocas o están mal, ajustar las expresiones regulares y la segmentación según cómo vienen escritas
   las citas en esos PDF. Mostrar al usuario antes/después.
4. Correr `python src/03_cargar_neo4j.py --reset` y anotar el RESUMEN del grafo.
5. Probar las consultas de `consultas/consultas_demo.cypher` (con el driver de Python si hace falta) y confirmar
   que la consulta 1 devuelve las especies con sus fuentes.
6. Correr el agente: `python src/04_agente_graphrag.py` (y `--demo` como respaldo). Si `GraphCypherQAChain` o
   `Neo4jGraph` cambiaron en la versión instalada de `langchain-neo4j`, adaptar el código sin cambiar el comportamiento.
7. Entregar al usuario una tabla con todos los números para reemplazar los `[N]` de la presentación
   (`HerbarioGraph_Avance1.pptx`): registros GBIF, PDF procesados, colectas extraídas, nodos, relaciones,
   especies y sinonimias, más un fragmento real de `tripletas.jsonl` con sus entidades.

## Reglas

- **Nunca inventar datos, números ni resultados.** Todo número de la presentación debe salir de correr los scripts.
- **Nunca mostrar, copiar ni subir el contenido de `.env`.** No hacer commit de `.env` ni de `.venv/`.
- Antes de instalar paquetes nuevos o borrar datos del grafo, avisar al usuario.
- Mantener los nombres de clases y relaciones de la ontología; si se agrega algo, actualizar también
  `ontologia/herbariograph.ttl`, regenerar el `.owl` y avisar.
- Cada cambio relevante va en la tabla "Declaración de uso de IA" del README (la guía del curso lo exige).
- Explicar cada cambio en lenguaje simple: los tres integrantes deben poder defender el código en la exposición.
