"""
Paso 2 · Captura con PLN desde artículos científicos (fuente NO estructurada).

Lee los PDF de data/pdfs/ (artículos de la Revista Peruana de Biología u otras),
busca los fragmentos donde se citan colectas ("Material examinado", "Especímenes
examinados", etc.) y extrae entidades y relaciones:

    especie, región, localidad, altitud, colector, fecha, herbario

Combina dos técnicas:
  - GLiNER (NER zero-shot multilingüe) para especie, localidad y colector.
  - Reglas (expresiones regulares) para altitud, fecha, región y acrónimo de herbario.

Salida:
  data/tripletas.jsonl  -> una colecta por línea, con documento, página y fragmento
  data/fragmentos.jsonl -> fragmentos de texto para la búsqueda semántica

Uso:
    python src/02_extraer_pln.py
    python src/02_extraer_pln.py --sin-gliner   # solo reglas (rápido, para probar)
"""
import argparse
import json
import re
from pathlib import Path

import fitz  # PyMuPDF

CARPETA_PDF = Path("data/pdfs")
SALIDA_TRIPLETAS = Path("data/tripletas.jsonl")
SALIDA_FRAGMENTOS = Path("data/fragmentos.jsonl")

DEPARTAMENTOS = [
    "Amazonas", "Áncash", "Ancash", "Apurímac", "Apurimac", "Arequipa", "Ayacucho",
    "Cajamarca", "Cusco", "Cuzco", "Huancavelica", "Huánuco", "Huanuco", "Junín", "Junin",
    "La Libertad", "Lambayeque", "Lima", "Moquegua", "Pasco", "Piura", "Puno", "Tacna",
]
CANON = {"Ancash": "Áncash", "Apurimac": "Apurímac", "Cuzco": "Cusco",
         "Huanuco": "Huánuco", "Junin": "Junín"}

RE_SECCION = re.compile(
    r"(material\s+examinado|especímenes\s+examinados|especimenes\s+examinados|"
    r"material\s+estudiado|specimens?\s+examined|material\s+examined|"
    r"(?:holo)?(?:type|tipo)\s*[.:](?=\s*(?:Peru|Perú|Bolivia|Ecuador|Colombia|Venezuela|Argentina|Chile)\b))",  # ejemplar tipo
    re.IGNORECASE,
)
RE_ALTITUD = re.compile(r"(\d{1,2}[\.,]?\d{3})\s*(?:-|–|a)?\s*(?:\d{1,2}[\.,]?\d{3})?\s*m(?:\b|snm|\.s\.n\.m)", re.IGNORECASE)
RE_FECHA = re.compile(
    r"\b(\d{1,2}\s+(?:ene|feb|mar|abr|may|jun|jul|ago|set|sep|oct|nov|dic|jan|apr|aug|dec)[a-z]*\.?\s+\d{4})\b",
    re.IGNORECASE,
)
# Acrónimo de herbario entre paréntesis: "(USM!)", "(USM!, Z!)", "(MO, F)" -> el primero;
RE_HERBARIO = re.compile(r"\((?:[A-Za-z]+:\s*)?([A-Z]{1,6})!?(?:\s*[,;][^)]*)?\)")  # también "(holotype: MO!; ...)"
RE_BINOMIO = re.compile(r"\b([A-Z][a-z]{3,})\s([a-z][a-z-]{3,})\b")
# Colector + número: "Boza 3032", "T.R. Dudley 11180", "Arce s.n.", "Smith & Pérez 45"
PALABRA = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñüåøæäöèç']+(?:-\s?[A-ZÁÉÍÓÚÑ]?[a-záéíóúñüåøæäöèç']+)?"  # también "Schmidt-Lebuhn"
APELLIDO = rf"(?:[A-Z]\.\s?){{0,3}}{PALABRA}(?:\s(?:de\s|del\s|de\sla\s)?{PALABRA})?"
MESES = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|Ene|Abr|Ago|Set|Dic)[a-z]*"
RE_COLECTOR = re.compile(
    rf"\b(?!{MESES}\b|Km\b|Dist\b|Prov\b)({APELLIDO}(?:\s(?:et\sal\.|&\s?{APELLIDO}))?)\s+(\d{{1,6}}|s\.\s?n\.)(?![\d.,]*\s*m\b)(?![\d°'])"
)
# Encabezado de tratamiento de especie en monografías: "8. Polylepis canoi W.Mend., ..."
RE_ENCABEZADO = re.compile(r"(?:^|\n)\s*\d{1,3}[a-z]?\.\s+([A-Z][a-z]+)\s([a-z][a-z-]+)\s+[A-Z(]")
# Cambio de país dentro de "Specimens examined. Peru. ... Bolivia. ..."
RE_PAIS = re.compile(r"[\s.:;,—–-]*(Peru|Perú|PERU|PERÚ|Bolivia|BOLIVIA|Ecuador|ECUADOR|Colombia|COLOMBIA|Venezuela|"
                     r"VENEZUELA|Argentina|ARGENTINA|Chile|CHILE)\s*[.:]")
# Dónde termina una sección de material examinado
RE_FIN_SECCION = re.compile(r"\b(?:Description|Descripción|Diagnosis)\b|(?:Distribution|Distribución|Notes|Notas|Conservation|Discusión|Discussion|"
                            r"Etymology|Etimología|Figure|Figura|Fig\.|Table|Tabla)\b[^.]{0,40}[.:]|"
                            r"\s(?:Sub)?[Ss]ection\s[A-Z][a-z]+|"
                            r"Acknowledgements|Acknowledgments|References|Literature\scited|Agradecimientos|"
                            r"Referencias|Literatura\scitada")
NO_EPITETOS = {"forest", "forests", "species", "tree", "trees", "woodland", "woodlands", "stands", "bosque",
               "bosques", "subsection", "section", "tetra", "spp", "trunks", "leaves", "individuals"}

ETIQUETAS_GLINER = ["especie de planta", "localidad", "persona colectora"]


def cargar_gliner(desactivado: bool):
    if desactivado:
        return None
    try:
        from gliner import GLiNER
        print("Cargando GLiNER (la primera vez descarga el modelo)...")
        return GLiNER.from_pretrained("urchade/gliner_multi-v2.1")
    except Exception as e:  # sin internet o sin la librería
        print(f"GLiNER no disponible ({e}); se usarán solo reglas.")
        return None


def a_metros(texto: str) -> float | None:
    m = RE_ALTITUD.search(texto)
    if not m:
        return None
    valor = float(m.group(1).replace(".", "").replace(",", ""))
    return valor if 500 <= valor <= 6800 else None  # rango plausible en Perú


def region_de(texto: str) -> str | None:
    """Devuelve el departamento que aparece primero en el texto."""
    encontrados = [(m.start(), d) for d in DEPARTAMENTOS for m in [re.search(rf"\b{d}\b", texto)] if m]
    if not encontrados:
        return None
    return CANON.get(min(encontrados)[1], min(encontrados)[1])


RE_INICIO_DPTO = "|".join(DEPARTAMENTOS + [d.upper() for d in DEPARTAMENTOS])


def localidad_invalida(texto: str) -> bool:
    """GLiNER a veces marca como 'localidad' el departamento ('Cusco', 'Ancash'), al colector con su
    número ('Cano 12412') o al acrónimo del herbario ('USM'): nada de eso es una localidad."""
    t = texto.strip(" ,.:")
    return (t.lower() in {d.lower() for d in DEPARTAMENTOS} | {"peru", "perú", "cuzco"}
            or bool(re.search(r"\d", t)) or re.fullmatch(r"[A-Z]{1,6}", t) is not None)
PALABRAS_NO_GENERO = {"Material", "Prov", "Provincia", "Distrito", "Quebrada", "Laguna", "Cerro",
                      "Nevado", "Parque", "Reserva", "Especímenes", "Especimenes", "Figura", "Tabla"}


def es_binomio_valido(genero: str, epiteto: str, generos: set[str]) -> bool:
    """Solo géneros conocidos (los de GBIF) y epítetos en minúscula que no sean palabras comunes
    ('Polylepis forest', 'Polylepis spp.', 'Polylepis Yauyinazo')."""
    return genero in generos and re.fullmatch(r"[a-z][a-z-]{2,}", epiteto) is not None and epiteto not in NO_EPITETOS


def especie_previa(texto: str, generos: set[str]) -> str | None:
    """Último binomio válido antes de la sección."""
    candidatos = [f"{g} {e}" for g, e in RE_BINOMIO.findall(texto)
                  if g not in PALABRAS_NO_GENERO and es_binomio_valido(g, e, generos)]
    return candidatos[-1] if candidatos else None


def especie_por_encabezado(encabezados: list[tuple[int, str]], posicion: int) -> str | None:
    """En monografías, la especie es la del último encabezado numerado ('8. Polylepis canoi ...')."""
    previos = [nombre for inicio, nombre in encabezados if inicio < posicion]
    return previos[-1] if previos else None


def segmentar_colectas(texto: str) -> list[str]:
    """Divide una sección de material examinado en citas individuales de colecta.
    Una cita termina en el paréntesis de herbarios '(USM!, Z!).' o en ';' seguido de texto
    (no de otro número del mismo colector: 'Boza 3032; 3082; 3083 (USM!)' es una sola cita)."""
    partes = re.split(rf"(?<=\))\s*[.;]\s+|;\s+(?=[^\d\s])|(?<!Peru)(?<!Perú)(?<!PERU)(?<!PERÚ)\.\s+(?=(?:{RE_INICIO_DPTO})\s*[:,.])",
                      texto)
    return [p.strip() for p in partes if len(p.strip()) > 25]


def secciones_de_colecta(texto: str, encabezados: list[tuple[int, str]]) -> list[tuple[int, str, bool]]:
    """Devuelve (posición, bloque, es_tipo) de cada sección 'Material examinado' o similar.
    El bloque sigue hasta el siguiente apartado (Distribución, Notas, otra especie...), aunque cambie de página.
    Las citas del ejemplar tipo ('Tipo:', 'Type.') son una sola cita que termina en el primer ')'."""
    bloques = []
    for m in RE_SECCION.finditer(texto):
        resto = texto[m.end(): m.end() + 30000]
        fin = RE_FIN_SECCION.search(resto)
        corte = fin.start() if fin else len(resto)
        siguiente = [ini - m.end() for ini, _ in encabezados if ini > m.end()]  # empieza otra especie
        if siguiente:
            corte = min(corte, siguiente[0])
        es_tipo = m.group(0)[:1].lower() in "th"  # "Tipo:" / "Type." / "Holotipo:"
        if es_tipo:
            cierre = resto.find(")")
            corte = min(corte, cierre + 1 if cierre >= 0 else 500)
        bloques.append((m.start(), resto[:corte], es_tipo))
    if not bloques and RE_ALTITUD.search(texto) and region_de(texto):
        # artículos sin sección explícita: usar párrafos que parezcan una cita de colecta
        # (altitud + región + colector con número, herbario o fecha), no cualquier mención
        bloques = [(texto.find(p), p, False) for p in texto.split("  ")
                   if RE_ALTITUD.search(p) and region_de(p)
                   and (RE_COLECTOR.search(p) or RE_HERBARIO.search(p) or RE_FECHA.search(p))]
    return bloques


# "Cusco: La Convención..." abre un departamento. La coma solo vale tras el país ("PERU. Ancash, ..."),
# porque sin país "Huanuco, road to..." puede ser una provincia o un lugar dentro de otro departamento.
RE_PREFIJO_DPTO = re.compile(
    rf"^[\s.;,]*(?:(?:(?:Peru|Perú|PERU|PERÚ)\s*[.:]\s*)(?:Dpto\.?\s*|Departamento\s+de\s+)?({RE_INICIO_DPTO})\s*[:.,]"
    rf"|(?:Dpto\.?\s*|Departamento\s+de\s+)?({RE_INICIO_DPTO})\s*[:.])")


def texto_documento(doc) -> tuple[str, list[int]]:
    """Une todas las páginas en un solo texto y devuelve también dónde empieza cada página.
    Quita las cabeceras repetidas ('Monograph of Polylepis', números de página) y los pies de
    figura o tabla, para que una lista de especímenes que cruza de página no quede cortada."""
    paginas = []
    for p in doc:
        bloques = [b[4] for b in p.get_text("blocks") if b[6] == 0
                   and not re.match(r"\s*(?:Figure|Fig\.|Figura|Table|Tabla)\s*\d+", b[4])]
        paginas.append(re.sub(r"-\n(?=[a-z])", "", "".join(bloques)))
    lineas_por_pagina = [set(l.strip() for l in p.splitlines() if l.strip()) for p in paginas]
    frecuencia = {}
    for lineas in lineas_por_pagina:
        for l in lineas:
            frecuencia[l] = frecuencia.get(l, 0) + 1
    repetidas = {l for l, n in frecuencia.items() if len(paginas) >= 4 and n >= 0.3 * len(paginas)}
    texto, inicios = "", []
    for p in paginas:
        inicios.append(len(texto))
        limpias = [l for l in p.splitlines() if l.strip() not in repetidas and not l.strip().isdigit()]
        texto += "\n".join(limpias) + "\n"
    return texto, inicios


def pagina_de(posicion: int, inicios: list[int]) -> int:
    return max(i for i, ini in enumerate(inicios, start=1) if ini <= posicion)


def generos_conocidos() -> set[str]:
    ruta = Path("data/gbif_registros.csv")
    if ruta.exists():
        import pandas as pd
        return set(pd.read_csv(ruta, usecols=["genero"])["genero"].dropna().unique())
    return {"Polylepis"}


def extraer(pdf: Path, modelo, generos: set[str]) -> tuple[list[dict], list[dict]]:
    doc = fitz.open(pdf)
    colectas, fragmentos = [], []
    for n_pag, pagina in enumerate(doc, start=1):
        texto_pag = re.sub(r"-\n(?=[a-z])", "", pagina.get_text("text")).replace("\n", " ")
        fragmentos.append({"documento": pdf.name, "pagina": n_pag, "texto": texto_pag[:2000]})

    # todo el documento como un solo texto: las listas de especímenes cruzan páginas
    texto, inicios = texto_documento(doc)
    encabezados = [(m.start(1), f"{m.group(1)} {m.group(2)}") for m in RE_ENCABEZADO.finditer(texto)
                   if es_binomio_valido(m.group(1), m.group(2), generos)]
    texto_plano = texto.replace("\n", " ")  # mismo largo: las posiciones siguen valiendo

    especie_actual = None
    for inicio, bloque, es_tipo in secciones_de_colecta(texto_plano, encabezados):
        # la especie es la del encabezado del tratamiento o, si no hay, el binomio justo antes
        especie_actual = (especie_por_encabezado(encabezados, inicio)
                          or especie_previa(texto_plano[max(0, inicio - 600): inicio], generos)
                          or especie_actual)
        region_actual, pais_actual = None, None
        cursor = inicio
        for cita in ([bloque.strip()] if es_tipo else segmentar_colectas(bloque)):
            posicion = texto_plano.find(cita, cursor)
            cursor = posicion if posicion >= 0 else cursor
            m_pais = RE_PAIS.match(cita)
            if m_pais:
                pais_actual = m_pais.group(1)
                region_actual = None
            if pais_actual and pais_actual.lower() not in ("peru", "perú"):
                continue  # solo nos interesan las colectas en Perú

            m_dpto = RE_PREFIJO_DPTO.match(cita)
            if m_dpto:  # "Cusco: La Convención, ..." abre un departamento nuevo
                dpto = (m_dpto.group(1) or m_dpto.group(2)).title()
                region_actual = CANON.get(dpto, dpto)
            elif not region_actual:
                region_actual = region_de(cita)
            # si no, la cita hereda el departamento de la anterior

            colectores = list(RE_COLECTOR.finditer(cita))
            m_col = colectores[-1] if colectores else None  # el colector va al final de la cita
            m_herb = RE_HERBARIO.search(cita)
            registro = {
                "documento": pdf.name,
                "pagina": pagina_de(cursor, inicios),
                "fragmento": cita[:500],
                "especie": especie_actual,
                "region": region_actual,
                "localidad": None,
                "altitud": a_metros(cita),
                "fecha": (RE_FECHA.search(cita) or [None])[0],
                "colector": m_col.group(1) if m_col else None,
                "numero_colecta": m_col.group(2) if m_col else None,
                "herbario": m_herb.group(1) if m_herb else None,
                "metodo": "reglas",
            }

            if modelo is not None:
                for ent in modelo.predict_entities(cita, ETIQUETAS_GLINER, threshold=0.45):
                    if (ent["label"] == "localidad" and not registro["localidad"]
                            and not localidad_invalida(ent["text"])):
                        registro["localidad"] = ent["text"]
                    elif ent["label"] == "persona colectora" and not registro["colector"]:
                        registro["colector"] = ent["text"]
                    elif ent["label"] == "especie de planta" and not registro["especie"]:  # el encabezado manda
                        partes = ent["text"].split()
                        if len(partes) >= 2 and es_binomio_valido(partes[0], partes[1], generos):
                            registro["especie"] = f"{partes[0]} {partes[1]}"
                registro["metodo"] = "gliner+reglas"

            if not registro["localidad"]:
                # respaldo de localidad: desde el inicio (sin país ni departamento) hasta coordenadas/altitud/fecha
                cuerpo = cita[m_dpto.end():] if m_dpto else re.sub(r"^[\s.;,]*(?:Peru|Perú|PERU|PERÚ)\s*[.:]", "", cita)
                m_loc = re.match(r"\s*(.+?)(?:,\s*\d|\s\d{1,2}[°º]|\s\d{1,2}[\.,]?\d{3}\s*m|$)", cuerpo)
                if m_loc and len(m_loc.group(1).strip(" ,.")) > 2 and not localidad_invalida(m_loc.group(1)):
                    registro["localidad"] = m_loc.group(1).strip(" ,.")[:120]

            # una colecta útil necesita especie + (región o altitud), y algo que la haga una cita de
            # colecta y no una mención suelta: colector con número, herbario o fecha
            # (una cita real mide menos de 400 caracteres; un párrafo largo es texto corrido)
            es_cita = (m_col or m_herb or registro["fecha"]) and len(cita) <= 400
            if registro["especie"] and (registro["region"] or registro["altitud"]) and es_cita:
                colectas.append(registro)
    return colectas, fragmentos


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sin-gliner", action="store_true")
    args = parser.parse_args()

    pdfs = sorted(CARPETA_PDF.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"No hay PDF en {CARPETA_PDF}/. Descarga artículos y vuelve a ejecutar.")

    modelo = cargar_gliner(args.sin_gliner)
    generos = generos_conocidos()
    total_c, total_f = [], []
    for pdf in pdfs:
        c, f = extraer(pdf, modelo, generos)
        print(f"{pdf.name}: {len(c)} colectas extraídas")
        total_c += c
        total_f += f

    with SALIDA_TRIPLETAS.open("w", encoding="utf-8") as fh:
        for r in total_c:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with SALIDA_FRAGMENTOS.open("w", encoding="utf-8") as fh:
        for r in total_f:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    print("\n=== RESUMEN (anótalo para la presentación) ===")
    print(f"Documentos procesados: {len(pdfs)}  ·  páginas: {len(total_f)}")
    print(f"Colectas extraídas: {len(total_c)}")
    print(f"Tripletas aprox.: {sum(sum(1 for k in ('region','localidad','altitud','colector','herbario','fecha') if r[k]) for r in total_c)}")
    if total_c:
        print("Ejemplo:", json.dumps(total_c[0], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
