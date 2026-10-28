// =====================================================================
// HerbarioGraph · Consultas Cypher para la demo y las capturas del avance
// Ejecutar en Neo4j Browser / Aura Query
// =====================================================================

// 1) PREGUNTA DEMO (múltiples saltos):
//    ¿Qué especies de Polylepis se colectaron sobre 4000 m en Áncash?
//    Género <- Especie aceptada <- [sinónimo] <- nombre registrado <- Espécimen
//    -> Colecta (altitud) -> Localidad -> Región; y cada espécimen -> Fuente
MATCH (g:Genero {nombre: 'Polylepis'})<-[:PERTENECE_A]-(e:Especie)
MATCH (s:Especimen)-[:IDENTIFICADO_COMO]->(:Especie)-[:ES_SINONIMO_DE*0..1]->(e)
MATCH (s)-[:PROVIENE_DE]->(c:Colecta)-[:COLECTADO_EN]->(:Localidad)-[:UBICADA_EN]->(:Region {nombre: 'Áncash'})
WHERE c.altitudMetros > 4000
MATCH (s)-[:EXTRAIDO_DE]->(f:Fuente)
RETURN e.nombre AS especie, count(DISTINCT s) AS especimenes,
       max(c.altitudMetros) AS altitud_maxima, collect(DISTINCT f.url)[..3] AS fuentes
ORDER BY especimenes DESC;

// 2) Visualizar el camino completo (para la captura del grafo)
MATCH p = (:Region {nombre: 'Áncash'})<-[:UBICADA_EN]-(:Localidad)<-[:COLECTADO_EN]-(c:Colecta)
          <-[:PROVIENE_DE]-(s:Especimen)-[:IDENTIFICADO_COMO]->(:Especie)-[:PERTENECE_A]->(:Genero {nombre: 'Polylepis'})
WHERE c.altitudMetros > 4000
OPTIONAL MATCH q = (s)-[:EXTRAIDO_DE]->(:Fuente)
RETURN p, q LIMIT 15;

// 3) SINONIMIAS: especímenes registrados con un nombre antiguo
//    (un buscador por palabras NO los encuentra con el nombre aceptado)
MATCH (s:Especimen)-[:IDENTIFICADO_COMO]->(sin:Especie)-[:ES_SINONIMO_DE]->(acep:Especie)
RETURN sin.nombre AS nombre_registrado, acep.nombre AS nombre_aceptado, count(s) AS especimenes
ORDER BY especimenes DESC LIMIT 10;

// 4) Especímenes del Herbario San Marcos (USM)
MATCH (s:Especimen)-[:DEPOSITADO_EN]->(:Herbario {codigo: 'USM'})
MATCH (s)-[:IDENTIFICADO_COMO]->(e:Especie)
RETURN e.nombre AS especie, count(s) AS especimenes ORDER BY especimenes DESC;

// 5) Otra pregunta de múltiples saltos: ¿quién colectó Polylepis en Áncash
//    y qué especies colectó cada uno?
MATCH (g:Genero {nombre: 'Polylepis'})<-[:PERTENECE_A]-(e:Especie)
MATCH (s:Especimen)-[:IDENTIFICADO_COMO]->(:Especie)-[:ES_SINONIMO_DE*0..1]->(e)
MATCH (s)-[:PROVIENE_DE]->(c:Colecta)-[:COLECTADO_EN]->(:Localidad)-[:UBICADA_EN]->(:Region {nombre: 'Áncash'})
MATCH (c)-[:COLECTADO_POR]->(p:Colector)
RETURN p.nombre AS colector, collect(DISTINCT e.nombre) AS especies, count(s) AS especimenes
ORDER BY especimenes DESC LIMIT 10;

// 6) Hechos que vienen de artículos (PLN) y no de GBIF
MATCH (s:Especimen)-[:EXTRAIDO_DE]->(f:Fuente {tipo: 'PLN'})
MATCH (s)-[:IDENTIFICADO_COMO]->(e:Especie)
RETURN e.nombre AS especie, f.url AS fuente, left(f.fragmentoTexto, 150) AS fragmento LIMIT 10;

// 7) Conteos para la diapositiva del grafo
MATCH (n) RETURN labels(n)[0] AS tipo, count(*) AS total ORDER BY total DESC;
