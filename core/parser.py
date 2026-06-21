import pymupdf4llm
import fitz
import os
import time
import pandas as pd
from google import genai
from google.genai import types

def parse_page_range(range_str: str, max_pages: int) -> list:
    """
    Parsea un rango de páginas en formato string (ej: "1, 2-3, 5") y devuelve
    una lista de índices de página base 0.
    """
    if not range_str or range_str.strip().lower() == 'todas' or range_str.strip() == '':
        return list(range(max_pages))
    
    pages = set()
    parts = range_str.split(',')
    for part in parts:
        part = part.strip()
        if '-' in part:
            try:
                start, end = part.split('-')
                start_idx = max(1, int(start.strip())) - 1
                end_idx = min(max_pages, int(end.strip()))
                for p in range(start_idx, end_idx):
                    pages.add(p)
            except ValueError:
                continue
        else:
            try:
                p_idx = int(part) - 1
                if 0 <= p_idx < max_pages:
                    pages.add(p_idx)
            except ValueError:
                continue
    return sorted(list(pages)) if pages else list(range(max_pages))


def normalize_cell(val) -> str:
    """
    Normaliza el contenido de una celda de tabla para estandarizar el equipamiento:
    - Vacíos, guiones, n/a, no -> 'No disponible'
    - Puntos, checks, 'S', 'Si', 'Sí', 'Std' -> 'Equipado'
    - 'O', 'Opt', 'Opcional' -> 'Opcional'
    - Conserva las unidades (ej: 120 HP, 1.6 L, 1500 kg) tal como están.
    """
    if val is None:
        return "No disponible"
    
    val_str = str(val).strip()
    val_lower = val_str.lower()
    
    if val_str == "" or val_lower in ["-", "--", "—", "n/a", "no", "none", "empty"]:
        return "No disponible"
        
    if val_lower in ["•", "✔", "s", "si", "sí", "std", "x", "equipado", "standard", "estándar", "de serie", "o"]:
        if val_lower == "o":
            return "Opcional"
        return "Equipado"
        
    if val_lower in ["opt", "opc", "opcional"]:
        return "Opcional"
        
    return val_str


def clean_markdown_tables(markdown_text: str) -> str:
    """
    Limpia las tablas en el texto Markdown según las reglas del usuario.
    """
    if not markdown_text:
        return ""
        
    lines = markdown_text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        stripped_line = line.strip()
        # Comprobar si la línea es parte de una tabla Markdown
        if stripped_line.startswith('|') and stripped_line.endswith('|'):
            parts = [p.strip() for p in stripped_line.split('|')]
            content_parts = parts[1:-1]
            
            # Identificar si es la línea divisoria del encabezado (ej. |---|---| o | :--- |)
            is_separator = True
            for part in content_parts:
                if not part or not all(c in '-: ' for c in part):
                    is_separator = False
                    break
            
            if is_separator:
                cleaned_lines.append(line)
                continue
            
            # Reemplazar celdas vacías o con guiones en filas de datos/encabezados
            new_parts = []
            for i, part in enumerate(content_parts):
                if i == 0:
                    new_parts.append(part if part else "Especificación")
                else:
                    new_parts.append(normalize_cell(part))
            
            # Reconstruir la línea de la tabla
            cleaned_lines.append("| " + " | ".join(new_parts) + " |")
        else:
            cleaned_lines.append(line)
            
    return '\n'.join(cleaned_lines)


def make_headers_unique(headers: list) -> list:
    seen = {}
    unique_headers = []
    for h in headers:
        h_str = str(h or "").strip().replace('\n', ' ')
        if not h_str:
            h_str = "Columna"
        if h_str in seen:
            seen[h_str] += 1
            unique_headers.append(f"{h_str}_{seen[h_str]}")
        else:
            seen[h_str] = 0
            unique_headers.append(h_str)
    return unique_headers


def extract_pandas_tables(pdf_path: str, pages: list) -> list:
    """
    Extrae tablas de un PDF usando PyMuPDF y las retorna como lista de DataFrames limpios.
    """
    dfs = []
    try:
        doc = fitz.open(pdf_path)
        for page_idx in pages:
            page = doc[page_idx]
            tables = page.find_tables()
            for idx, table in enumerate(tables):
                raw_data = table.extract()
                if not raw_data or len(raw_data) < 2:
                    continue
                
                # Crear DataFrame y limpiar cabeceras
                headers = []
                for c_idx, h in enumerate(raw_data[0]):
                    h_str = str(h or "").strip().replace('\n', ' ')
                    if c_idx == 0 and not h_str:
                        headers.append("Especificación")
                    elif not h_str:
                        headers.append(f"Versión {c_idx}")
                    else:
                        headers.append(h_str)
                
                # Asegurar cabeceras únicas para evitar ValueError: Duplicate column names found
                headers = make_headers_unique(headers)
                
                # Normalizar filas
                rows = []
                for r in raw_data[1:]:
                    if len(r) < len(headers):
                        r = r + [None] * (len(headers) - len(r))
                    else:
                        r = r[:len(headers)]
                        
                    cleaned_row = []
                    for c_idx, val in enumerate(r):
                        if c_idx == 0:
                            cleaned_row.append(str(val or "").strip().replace('\n', ' '))
                        else:
                            cleaned_row.append(normalize_cell(val))
                    rows.append(cleaned_row)
                
                df = pd.DataFrame(rows, columns=headers)
                dfs.append({
                    "page": page_idx + 1,
                    "table_index": idx + 1,
                    "df": df
                })
        doc.close()
    except Exception as e:
        print(f"Error al extraer tablas con pandas/fitz: {e}")
    return dfs


def refine_specs_with_gemini(extracted_markdown: str, api_key: str) -> str:
    """
    Usa la API de Gemini para procesar el Markdown extraído y aplicar las tres reglas
    de negocio para las especificaciones técnicas por defecto.
    """
    client = genai.Client(api_key=api_key)
    
    prompt = (
        "Eres un experto en extracción y estructuración de datos técnicos.\n"
        "Se te proporcionará el texto extraído (en formato Markdown) de una ficha técnica o manual.\n"
        "Tu tarea es refinar, corregir y estructurar este Markdown de acuerdo con las siguientes reglas estrictas:\n\n"
        "1. Asocia cada especificación técnica estrictamente con la columna o versión correspondiente según el orden secuencial en el que aparecen en el texto (de izquierda a derecha).\n"
        "2. Mantén siempre las unidades de medida originales (ej. HP, lb-ft, kg, l/100km, km/l) junto a cada valor numérico extraído.\n"
        "3. Si una versión o unidad no tiene un dato explícito en la fila (viene en blanco o con un guion '-'), regístralo como 'No equipado' o 'No aplica'. NO asumas el dato de la celda anterior a menos que sea evidente que forma parte de una celda combinada que aplica a todas las versiones.\n"
        "4. Celdas combinadas: Si una celda está combinada horizontal o verticalmente en la tabla original, asegúrate de repetir o propagar el valor correspondiente para cada una de las versiones/columnas afectadas, de modo que cada columna de versión muestre de forma clara y explícita el equipamiento que le corresponde, sin dejar celdas vacías.\n"
        "5. Marcadores de página: Conserva e inserta siempre marcadores de página muy visibles con el formato exacto `--- PÁGINA X ---` (donde X es el número de página original que viene en el texto como '--- INICIO DE PÁGINA X ---') al principio del contenido de cada página, para saber exactamente dónde se encuentra cada dato.\n\n"
        "Mantén el formato Markdown limpio, legible y estructurado. Devuelve únicamente el Markdown de la tabla o texto resultante estructurado, sin comentarios explicativos adicionales ni bloques de código de marcado (como ```markdown o ```)."
    )
    
    model_name = 'gemini-2.5-flash'
    last_error = None
    
    for attempt in range(5):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[prompt, extracted_markdown]
            )
            res = response.text.strip()
            
            if res.startswith("```markdown"):
                res = res[len("```markdown"):].strip()
            if res.startswith("```") and res.endswith("```"):
                res = res[3:-3].strip()
            elif res.endswith("```"):
                res = res[:-3].strip()
                
            tokens_dict = {
                "prompt": response.usage_metadata.prompt_token_count if response.usage_metadata else 0,
                "candidates": response.usage_metadata.candidates_token_count if response.usage_metadata else 0,
                "total": response.usage_metadata.total_token_count if response.usage_metadata else 0
            }
            return res.strip(), tokens_dict
        except Exception as e:
            last_error = e
            error_str = str(e)
            if any(x in error_str.lower() for x in ["503", "429", "quota", "overloaded", "unavailable", "resource_exhausted"]):
                time.sleep(2 ** attempt)
                continue
            else:
                raise e
                
    raise RuntimeError(f"El servicio de Gemini está temporalmente saturado (503/429). Detalles: {last_error}")


def refine_specs_with_gemini_vision(pdf_path: str, api_key: str, pages: list) -> str:
    """
    Usa el modelo de visión de Gemini para extraer las tablas directamente de imágenes
    de las páginas del PDF, lo cual mantiene una mayor precisión en la asociación de columnas.
    """
    client = genai.Client(api_key=api_key)
    doc = fitz.open(pdf_path)
    contents = []
    
    prompt = (
        "Eres un experto en extracción y estructuración de datos técnicos y análisis visual de documentos.\n"
        "Analiza las siguientes imágenes de páginas de una ficha técnica o manual y extrae toda la información técnica en formato Markdown.\n"
        "Sigue estas reglas estrictas en tu extracción:\n\n"
        "1. Asocia cada especificación técnica estrictamente con la columna o versión correspondiente según el orden secuencial en el que aparecen en el documento de izquierda a derecha.\n"
        "2. Mantén siempre las unidades de medida originales (ej. HP, lb-ft, kg, l/100km, km/l) junto a cada valor numérico extraído.\n"
        "3. Si una versión o unidad no tiene un dato explícito en la fila (viene en blanco o con un guion '-'), regístralo como 'No equipado' o 'No aplica'. NO asumas el dato de la celda anterior a menos que sea evidente que forma parte de una celda combinada que aplica a todas las versiones.\n"
        "4. Celdas combinadas: Si una celda está combinada horizontal o verticalmente en la tabla original, asegúrate de repetir o propagar el valor correspondiente para cada una de las versiones/columnas afectadas, de modo que cada columna de versión muestre de forma clara y explícita el equipamiento que le corresponde, sin dejar celdas vacías.\n"
        "5. Marcadores de página: Es MANDATORIO que insertes un marcador visible con el formato exacto `--- PÁGINA X ---` (donde X es el número de página real, empezando por la página correspondiente) al inicio de la información extraída de cada una de las imágenes de página provistas.\n\n"
        "Devuelve únicamente el Markdown estructurado y limpio, sin comentarios explicativos adicionales ni bloques de código de marcado (como ```markdown o ```)."
    )
    
    contents.append(prompt)
    
    for page_idx in pages:
        page = doc[page_idx]
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        contents.append(
            types.Part.from_bytes(
                data=img_bytes,
                mime_type="image/png"
            )
        )
    
    doc.close()
    
    model_name = 'gemini-2.5-flash'
    last_error = None
    
    for attempt in range(5):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents
            )
            res = response.text.strip()
            
            if res.startswith("```markdown"):
                res = res[len("```markdown"):].strip()
            if res.startswith("```") and res.endswith("```"):
                res = res[3:-3].strip()
            elif res.endswith("```"):
                res = res[:-3].strip()
                
            tokens_dict = {
                "prompt": response.usage_metadata.prompt_token_count if response.usage_metadata else 0,
                "candidates": response.usage_metadata.candidates_token_count if response.usage_metadata else 0,
                "total": response.usage_metadata.total_token_count if response.usage_metadata else 0
            }
            return res.strip(), tokens_dict
        except Exception as e:
            last_error = e
            error_str = str(e)
            if any(x in error_str.lower() for x in ["503", "429", "quota", "overloaded", "unavailable", "resource_exhausted"]):
                time.sleep(2 ** attempt)
                continue
            else:
                raise e
                
    raise RuntimeError(f"El servicio de Gemini Vision está temporalmente saturado (503/429). Detalles: {last_error}")


def convert_pdf_to_markdown_structured(pdf_path: str, api_key: str = "", use_ai: bool = False, use_vision: bool = False, page_range_str: str = "Todas") -> dict:
    """
    Lee un archivo PDF y retorna un diccionario con:
    - 'markdown': El texto en Markdown limpio
    - 'tables': Una lista de diccionarios conteniendo DataFrames de las tablas encontradas y limpias.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"El archivo no existe en: {pdf_path}")

    # Obtener el número total de páginas del PDF
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    doc.close()

    # Obtener la lista de páginas a procesar
    pages_to_process = parse_page_range(page_range_str, total_pages)

    if not pages_to_process:
        raise ValueError("El rango de páginas especificado no es válido o está fuera de rango.")

    # Extraer tablas como pandas DataFrames para visualización y descarga
    pandas_tables = extract_pandas_tables(pdf_path, pages_to_process)

    # Obtener el Markdown
    full_markdown = ""
    tokens_info = {"prompt": 0, "candidates": 0, "total": 0}
    
    if use_ai and use_vision and api_key and api_key.strip():
        try:
            full_markdown, tokens_info = refine_specs_with_gemini_vision(pdf_path, api_key, pages_to_process)
        except Exception as vision_error:
            print(f"Advertencia: Falló la extracción con visión, cayendo en extracción local. {vision_error}")
            use_vision = False

    # Si no se usó visión (o falló), realizamos la extracción basada en texto
    if not use_vision or not use_ai:
        try:
            for page_idx in pages_to_process:
                page_data = pymupdf4llm.to_markdown(pdf_path, pages=[page_idx])
                page_number = page_idx + 1
                full_markdown += f"\n\n--- INICIO DE PÁGINA {page_number} ---\n"
                full_markdown += page_data
                full_markdown += f"\n--- FIN DE PÁGINA {page_number} ---\n"
        except Exception as e:
            try:
                doc = fitz.open(pdf_path)
                full_markdown = ""
                for page_idx in pages_to_process:
                    page = doc[page_idx]
                    page_number = page_idx + 1
                    page_text = page.get_text("text")
                    full_markdown += f"\n\n--- INICIO DE PÁGINA {page_number} ---\n"
                    full_markdown += page_text
                    full_markdown += f"\n--- FIN DE PÁGINA {page_number} ---\n"
                doc.close()
            except Exception as en:
                raise RuntimeError(f"Error crítico al extraer texto del PDF: {str(en)}")

        # Aplicar el refinamiento de texto de IA si corresponde
        if use_ai and api_key and api_key.strip():
            try:
                full_markdown, tokens_info = refine_specs_with_gemini(full_markdown, api_key)
            except Exception as ai_error:
                print(f"Advertencia: Falló el refinamiento con IA, usando limpieza local. {ai_error}")
                full_markdown = clean_markdown_tables(full_markdown)
        else:
            full_markdown = clean_markdown_tables(full_markdown)

    return {
        "markdown": full_markdown,
        "tables": pandas_tables,
        "tokens": tokens_info
    }


def ask_gemini_about_document(document_text: str, question: str, api_key: str) -> tuple:
    """
    Permite hacer preguntas específicas sobre el contenido de un documento utilizando Gemini.
    Retorna una tupla (respuesta, tokens_dict).
    """
    if not api_key or not api_key.strip():
        raise ValueError("Se requiere una API Key de Gemini para usar el chat con el documento.")
        
    client = genai.Client(api_key=api_key)
    
    prompt = (
        f"Eres un asistente analítico experto en analizar fichas técnicas y manuales extensos.\n"
        f"A continuación se te proporciona el contenido extraído del documento en formato Markdown.\n"
        f"Usa este contenido para responder la pregunta del usuario de forma precisa, detallada y referenciando las especificaciones, páginas o secciones cuando sea posible.\n\n"
        f"--- INICIO DEL DOCUMENTO ---\n"
        f"{document_text}\n"
        f"--- FIN DEL DOCUMENTO ---\n\n"
        f"Pregunta del usuario: {question}\n"
        f"Respuesta:"
    )
    
    model_name = 'gemini-2.5-flash'
    last_error = None
    
    for attempt in range(5):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            tokens_dict = {
                "prompt": response.usage_metadata.prompt_token_count if response.usage_metadata else 0,
                "candidates": response.usage_metadata.candidates_token_count if response.usage_metadata else 0,
                "total": response.usage_metadata.total_token_count if response.usage_metadata else 0
            }
            return response.text.strip(), tokens_dict
        except Exception as e:
            last_error = e
            error_str = str(e)
            if any(x in error_str.lower() for x in ["503", "429", "quota", "overloaded", "unavailable", "resource_exhausted"]):
                time.sleep(2 ** attempt)
                continue
            else:
                raise e
                
    raise RuntimeError(f"El servicio de Gemini está temporalmente saturado (503/429). Detalles: {last_error}")


def audit_legal_document(markdown_text: str, api_key: str) -> tuple:
    """
    Analiza el texto de un acta constitutiva o contrato y extrae información legal clave usando Gemini.
    Retorna una tupla (respuesta, tokens_dict).
    """
    if not api_key or not api_key.strip():
        raise ValueError("Se requiere una API Key de Gemini para auditar el documento.")
        
    client = genai.Client(api_key=api_key)
    
    prompt = (
        "Eres un abogado experto en auditoría corporativa y revisión de contratos.\n"
        "Analiza el siguiente documento legal en formato Markdown (que contiene marcadores del tipo '--- PÁGINA X ---' o '--- INICIO DE PÁGINA X ---') y extrae la información clave en español.\n\n"
        "Por favor, estructura tu respuesta en las siguientes secciones claras utilizando Markdown, y asegúrate de CITAR SIEMPRE la página o páginas exactas del documento original de donde extraes cada dato (ej: 'Administrador Único: Juan Pérez (Pág. 4)'):\n"
        "1. **Información General de la Sociedad/Contrato** (Nombre/Razón Social, Fecha de constitución, Objeto social principal, Duración, Nacionalidad, Folio Mercantil/Datos de registro).\n"
        "2. **Socios / Accionistas** (Estructura accionaria, capital social mínimo y variable, número de acciones, participación de cada socio en tabla).\n"
        "3. **Administración y Representación** (Tipo de administración: Consejo de Administración o Administrador Único, nombres de los cargos, comisarios).\n"
        "4. **Poderes Otorgados** (Poderes para actos de dominio, administración, pleitos y cobranzas, títulos de crédito, facultades individuales o mancomunadas, limitaciones).\n"
        "5. **Puntos Críticos / Alertas** (Cualquier cláusula inusual, falta de datos, vigencias o detalles que requieran atención).\n\n"
        "--- INICIO DEL DOCUMENTO ---\n"
        f"{markdown_text}\n"
        "--- FIN DEL DOCUMENTO ---\n"
    )
    
    model_name = 'gemini-2.5-flash'
    last_error = None
    
    for attempt in range(5):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            tokens_dict = {
                "prompt": response.usage_metadata.prompt_token_count if response.usage_metadata else 0,
                "candidates": response.usage_metadata.candidates_token_count if response.usage_metadata else 0,
                "total": response.usage_metadata.total_token_count if response.usage_metadata else 0
            }
            return response.text.strip(), tokens_dict
        except Exception as e:
            last_error = e
            error_str = str(e)
            if any(x in error_str.lower() for x in ["503", "429", "quota", "overloaded", "unavailable", "resource_exhausted"]):
                time.sleep(2 ** attempt)
                continue
            else:
                raise e
                
    raise RuntimeError(f"El servicio de Gemini está temporalmente saturado (503/429). Detalles: {last_error}")



