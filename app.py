import streamlit as st
import os
import io
import pandas as pd
from dotenv import load_dotenv
from core.parser import convert_pdf_to_markdown_structured, ask_gemini_about_document, audit_legal_document

load_dotenv()

st.set_page_config(page_title="Parser & Auditor de Fichas Técnicas",
                   page_icon="🚗", layout="wide")

st.title("🚗 Parser & Auditor Inteligent de Fichas Técnicas")
st.markdown("Extrae y normaliza especificaciones y tablas de equipamiento de fichas técnicas y manuales en PDF.")

if "chat_history" not in st.session_state:
    st.session_state["chat_history"] = []

st.sidebar.header("🔑 Configuración")
default_key = os.getenv("GEMINI_API_KEY", "")
api_key = st.sidebar.text_input("Introduce tu Gemini API Key", value=default_key, type="password")

processing_mode = st.sidebar.selectbox(
    "Método de Extracción (Fichas/Actas):",
    [
        "Local (Rápido y Gratis)",
        "Refinamiento de Texto por IA (Uso bajo de tokens)",
        "Extracción por Visión por IA (Máxima precisión, Mayor uso de tokens)"
    ],
    index=0,
    help="Selecciona cómo procesar las tablas y el texto original del documento. El modo Local no consume tokens."
)

page_range_str = st.sidebar.text_input(
    "Rango de páginas a procesar",
    value="Todas",
    help="Especifica las páginas a procesar. Ej: 'Todas', '1', '2-4', '1,3,5'"
)

# Mapear selección de procesamiento a parámetros
use_ai = processing_mode != "Local (Rápido y Gratis)"
use_vision = processing_mode == "Extracción por Visión por IA (Máxima precisión, Mayor uso de tokens)"

tab1, tab2 = st.tabs(
    [
        "⚡ 1. Convertidor de Fichas Técnicas", 
        "📋 2. Auditor de Actas y Contratos"
    ]
)

# --- PESTAÑA 1: CONVERTIDOR DE FICHAS TÉCNICAS ---
with tab1:
    st.markdown("### 📝 Convertidor de Fichas Técnicas y Manuales")
    uploaded_universal = st.file_uploader(
        "Arrastra tu PDF de ficha técnica aquí", type=["pdf"], key="uni")

    if uploaded_universal is not None:
        # Guardamos temporalmente el archivo para que el motor pueda leerlo
        temp_path = f"temp_{uploaded_universal.name}"
        with open(temp_path, "wb") as f:
            f.write(uploaded_universal.getbuffer())

        st.success(f"📁 Archivo '{uploaded_universal.name}' listo en memoria.")

        # ¡EL BOTÓN MÁGICO DE ACCIÓN!
        if st.button("🚀 Procesar y Convertir Documento"):
            with st.spinner("Leyendo el PDF y estructurando tablas..."):
                try:
                    # Ejecutamos el motor estructurado de parser.py
                    result = convert_pdf_to_markdown_structured(
                        temp_path, 
                        api_key=api_key, 
                        use_ai=use_ai, 
                        use_vision=use_vision, 
                        page_range_str=page_range_str
                    )

                    resultado_md = result["markdown"]
                    pandas_tables = result["tables"]

                    # Guardar el resultado en session_state para el chat
                    st.session_state["extracted_markdown"] = resultado_md
                    st.session_state["extracted_tokens"] = result.get("tokens", {"prompt": 0, "candidates": 0, "total": 0})
                    st.session_state["chat_history"] = []  # Reiniciar chat anterior al cambiar documento

                    st.balloons()  # Animación de éxito
                    
                    if use_ai and api_key:
                        mode_text = "Visión de IA" if use_vision else "Refinamiento de Texto por IA"
                        st.success(f"✨ Documento procesado y refinado con éxito usando {mode_text}.")
                        t = st.session_state["extracted_tokens"]
                        st.info(f"📊 **Uso de Tokens (Gemini)**: Entrada: {t['prompt']} | Salida: {t['candidates']} | Total: {t['total']}")
                    else:
                        st.info("⚙️ Documento procesado localmente con el motor de extracción y limpieza estructurada. Consumo: 0 tokens.")

                    # Mostrar tablas interactivas de Pandas si se encontraron
                    if pandas_tables:
                        st.markdown("## 📊 Tablas Detectadas y Normalizadas")
                        for item in pandas_tables:
                            st.write(f"**Página {item['page']} - Tabla #{item['table_index']}**")
                            df = item['df']
                            st.dataframe(df, use_container_width=True)
                            
                            # Preparar descarga de Excel en memoria
                            buffer = io.BytesIO()
                            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                                df.to_excel(writer, index=False, sheet_name=f"Pag {item['page']}")
                            
                            st.download_button(
                                label=f"📥 Descargar Tabla #{item['table_index']} (Excel)",
                                data=buffer.getvalue(),
                                file_name=f"tabla_pag{item['page']}_{item['table_index']}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            )
                            st.markdown("---")

                    st.markdown("## 📝 Vista Previa del Documento en Markdown:")
                    st.code(resultado_md[:5000], language="markdown")

                    # Botón para descargar el archivo .md completo
                    st.download_button(
                        label="📥 Descargar Documento Completo (.md)",
                        data=resultado_md,
                        file_name=f"{uploaded_universal.name.replace('.pdf', '')}_PROCESADO.md",
                        mime="text/markdown"
                    )
                except Exception as e:
                    st.error(f"Hubo un error: {e}")
                finally:
                    # Borramos el archivo temporal por seguridad
                    if os.path.exists(temp_path):
                        os.remove(temp_path)

# --- PESTAÑA 2: AUDITOR DE ACTAS ---
with tab2:
    st.markdown("### ⚖️ Analizador de Actas Constitutivas y Poderes")
    st.write("Sube documentos legales (Actas, Contratos, etc.) para convertirlos a Markdown y extraer información clave.")
    
    st.info(
        "💡 **¿Cómo funciona la extracción sin tokens?**\n\n"
        "- Si configuras el **Método de Extracción** en la barra lateral izquierda como **'Local (Rápido y Gratis)'**, "
        "la conversión a Markdown se realiza de manera local en tu equipo usando la biblioteca `pymupdf4llm`. "
        "Esto consume **0 tokens** de Gemini y es 100% gratuito.\n"
        "- El botón **'1. Convertir a Markdown'** te permite ver y descargar este texto localmente sin costo alguno.\n"
        "- El botón **'2. Convertir y Auditar Documento'** realiza la extracción local y luego utiliza Gemini para analizar, resumir y auditar la información estructurada."
    )
    uploaded_legal = st.file_uploader(
        "Arrastra tu PDF legal", type=["pdf"], key="legal")

    if uploaded_legal is not None:
        temp_path_legal = f"temp_legal_{uploaded_legal.name}"
        with open(temp_path_legal, "wb") as f:
            f.write(uploaded_legal.getbuffer())

        st.success(f"📁 Archivo legal '{uploaded_legal.name}' listo en memoria.")

        col1, col2 = st.columns(2)
        with col1:
            process_btn = st.button("🚀 1. Convertir a Markdown", key="btn_md_legal")
        with col2:
            audit_btn = st.button("⚖️ 2. Convertir y Auditar Documento", key="btn_audit_legal")

        # Utilizaremos session state para persistir los resultados de esta pestaña
        if "legal_markdown" not in st.session_state:
            st.session_state["legal_markdown"] = ""
        if "legal_audit" not in st.session_state:
            st.session_state["legal_audit"] = ""
        if "legal_md_tokens" not in st.session_state:
            st.session_state["legal_md_tokens"] = None
        if "legal_audit_tokens" not in st.session_state:
            st.session_state["legal_audit_tokens"] = None

        if process_btn:
            with st.spinner("Convirtiendo PDF legal a Markdown..."):
                try:
                    result = convert_pdf_to_markdown_structured(
                        temp_path_legal, 
                        api_key=api_key, 
                        use_ai=use_ai, 
                        use_vision=use_vision, 
                        page_range_str=page_range_str
                    )
                    st.session_state["legal_markdown"] = result["markdown"]
                    st.session_state["legal_md_tokens"] = result["tokens"]
                    st.session_state["chat_history"] = []  # Reiniciar chat
                    st.toast("✅ Conversión a Markdown finalizada!")
                except Exception as e:
                    st.error(f"Hubo un error: {e}")
                finally:
                    if os.path.exists(temp_path_legal):
                        os.remove(temp_path_legal)

        if audit_btn:
            with st.spinner("Procesando y auditando documento legal..."):
                try:
                    # Primero convertimos a markdown si no existe
                    result = convert_pdf_to_markdown_structured(
                        temp_path_legal, 
                        api_key=api_key, 
                        use_ai=use_ai, 
                        use_vision=use_vision, 
                        page_range_str=page_range_str
                    )
                    st.session_state["legal_markdown"] = result["markdown"]
                    st.session_state["legal_md_tokens"] = result["tokens"]
                    
                    # Luego auditamos
                    audit_res, audit_tokens = audit_legal_document(st.session_state["legal_markdown"], api_key)
                    st.session_state["legal_audit"] = audit_res
                    st.session_state["legal_audit_tokens"] = audit_tokens
                    st.session_state["chat_history"] = []  # Reiniciar chat
                    st.balloons()
                except Exception as e:
                    st.error(f"Hubo un error: {e}")
                finally:
                    if os.path.exists(temp_path_legal):
                        os.remove(temp_path_legal)

        # Mostrar resultados si existen
        if st.session_state["legal_markdown"]:
            st.markdown("---")
            if use_ai and st.session_state["legal_md_tokens"]:
                t = st.session_state["legal_md_tokens"]
                st.info(f"📊 **Uso de Tokens (Extracción/Refinamiento)**: Entrada: {t['prompt']} | Salida: {t['candidates']} | Total: {t['total']}")
            st.markdown("### 📝 Vista Previa del Markdown Extraído:")
            st.code(st.session_state["legal_markdown"][:5000], language="markdown")
            
            st.download_button(
                label="📥 Descargar Texto en Markdown (.md)",
                data=st.session_state["legal_markdown"],
                file_name=f"{uploaded_legal.name.replace('.pdf', '')}_TEXTO.md",
                mime="text/markdown",
                key="dl_legal_md"
            )

        if st.session_state["legal_audit"]:
            st.markdown("---")
            if st.session_state["legal_audit_tokens"]:
                t = st.session_state["legal_audit_tokens"]
                st.info(f"📊 **Uso de Tokens (Auditoría Gemini)**: Entrada: {t['prompt']} | Salida: {t['candidates']} | Total: {t['total']}")
            st.markdown("### 📋 Resultados de la Auditoría Legal:")
            st.markdown(st.session_state["legal_audit"])
            
            st.download_button(
                label="📥 Descargar Auditoría Completa (.md)",
                data=st.session_state["legal_audit"],
                file_name=f"{uploaded_legal.name.replace('.pdf', '')}_AUDITORIA.md",
                mime="text/markdown",
                key="dl_legal_audit"
            )

# Fin de la aplicación
