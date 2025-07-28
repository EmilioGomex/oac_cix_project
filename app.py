import streamlit as st
import pandas as pd
import os
import matplotlib.pyplot as plt
import seaborn as sns
from supabase import create_client, Client # Importar la librería de Supabase
import io # Para manejar archivos en memoria
import tempfile # Para guardar archivos temporalmente (necesario para la subida a Supabase Storage)

# --- 0. Credenciales de Supabase ---
# ¡IMPORTANTE! Las claves se cargan de .streamlit/secrets.toml
# NUNCA subas tus claves directamente en el código a GitHub en un proyecto real sin st.secrets
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
except KeyError:
    st.error("Error: Las credenciales de Supabase no están configuradas en .streamlit/secrets.toml.")
    st.markdown("Por favor, crea el archivo `.streamlit/secrets.toml` con `SUPABASE_URL` y `SUPABASE_KEY`.")
    st.stop() # Detiene la ejecución si las claves no están

# Inicializa el cliente de Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- 1. Configuración de Variables Globales ---
COLUMNAS_CALIFICACION = {
    'N': 0,    # No cumple
    'P': 0.4,  # Cumple parcialmente
    'T': 0.8,  # Cumple satisfactoriamente
    'E': 1     # Cumple totalmente
}

INDICADORES_A_EVALUAR = [
    'Datos de actividad',
    'Factores de emisión',
    'Alcance 1',
    'Alcance 2',
    'Alcance 3',
    'Categorías excluidas',
    'Consolidación',
    'Auditoría',
    'Compromisos de reducción',
    'Evaluación de incertidumbre'
]

SUPABASE_BUCKET_NAME = "evaluaciones-cix-files" # Asegúrate de que este nombre coincida con tu bucket en Supabase

# --- 2. Función para procesar un solo archivo de evaluación ---
# st.cache_data cachea los resultados de esta función para mejorar el rendimiento
@st.cache_data
def procesar_evaluacion_empresa(file_bytes_obj, file_name):
    """
    Procesa un archivo Excel/CSV desde un objeto de bytes en memoria,
    extrae datos de evaluación y calcula la puntuación CIX.
    """
    st.info(f"Procesando archivo: {file_name}...")
    try:
        # Determinar el tipo de archivo y leer
        if file_name.lower().endswith('.csv'):
            df_raw = pd.read_csv(file_bytes_obj, header=None)
        elif file_name.lower().endswith('.xlsx'):
            df_raw = pd.read_excel(file_bytes_obj, header=None)
        else:
            st.error(f"Formato de archivo no soportado para {file_name}. Sube archivos .csv o .xlsx.")
            return None

        # --- Extracción de Metadatos ---
        # Basado en la plantilla de evaluación que proporcionaste
        # Usamos .iloc para acceder a celdas específicas por su índice (fila, columna)
        # y pd.isna para manejar celdas vacías
        nombre_organizacion = df_raw.iloc[5, 1] if not pd.isna(df_raw.iloc[5, 1]) else file_name.split('-')[0].replace(".xlsx", "").replace(".csv", "")
        periodo_informe = df_raw.iloc[6, 1] if not pd.isna(df_raw.iloc[6, 1]) else "Desconocido"
        enlace_documentacion = df_raw.iloc[7, 1] if not pd.isna(df_raw.iloc[7, 1]) else "N/A"

        # --- Extracción de Calificaciones de Indicadores ---
        puntuaciones_indicadores = {}
        for indicador_nombre in INDICADORES_A_EVALUAR:
            # Buscar la fila donde se encuentra el indicador (columna A o B en Excel, índice 0 o 1 en DataFrame)
            # Manejamos ambos casos porque en tus ejemplos el nombre a veces está en la columna 0 o 1
            fila_indicador = df_raw[(df_raw.iloc[:, 0] == indicador_nombre) | (df_raw.iloc[:, 1] == indicador_nombre)].index

            if not fila_indicador.empty:
                fila_idx = fila_indicador[0]
                # Columnas donde pueden estar las 'x' para N, P, T, E (E=4, F=5, G=6, H=7 en 0-indexed)
                calificacion_col_indices = [4, 5, 6, 7]
                puntuacion_obtenida = 0 # Valor por defecto si no se encuentra la 'x'

                # Iterar sobre las posibles columnas de calificación (N, P, T, E)
                for col_idx, key_calificacion in zip(calificacion_col_indices, COLUMNAS_CALIFICACION.keys()):
                    # Asegurarse de que el índice de columna no exceda los límites del DataFrame
                    if col_idx < df_raw.shape[1] and str(df_raw.iloc[fila_idx, col_idx]).strip().lower() == 'x':
                        puntuacion_obtenida = COLUMNAS_CALIFICACION[key_calificacion]
                        break # Si encontramos la 'x', salimos del bucle de columnas
                puntuaciones_indicadores[indicador_nombre] = puntuacion_obtenida
            else:
                # Si un indicador no se encuentra en el archivo, se le asigna 0 puntos
                puntuaciones_indicadores[indicador_nombre] = 0

        # Calcular CIX Total (promedio simple de las puntuaciones de los 10 indicadores)
        if puntuaciones_indicadores:
            cix_total = sum(puntuaciones_indicadores.values()) / len(INDICADORES_A_EVALUAR)
        else:
            cix_total = 0 # Si no se pudo extraer ningún indicador

        # Prepara el diccionario de datos para ser insertado en la base de datos de Supabase
        # Asegúrate de que los nombres de las claves aquí coincidan con los nombres de las columnas en tu tabla 'evaluaciones'
        db_data = {
            'organizacion_nombre': nombre_organizacion,
            'periodo_informe': periodo_informe,
            'enlace_original_documentacion': enlace_documentacion,
            'cix_total': cix_total
        }
        # Añade las puntuaciones de los indicadores individuales, convirtiendo el nombre a snake_case
        for key, value in puntuaciones_indicadores.items():
            db_data[key.lower().replace(" ", "_").replace("-", "_")] = value

        return db_data

    except Exception as e:
        st.error(f"Error al procesar el archivo '{file_name}': {e}. Por favor, verifica que el archivo siga la estructura de la plantilla.")
        return None

# --- 3. Funciones de Interacción con Supabase ---

def subir_archivo_a_supabase(uploaded_file):
    """Sube un archivo a Supabase Storage y retorna su URL pública."""
    try:
        if uploaded_file.name is None:
            st.error("No se detectó un nombre de archivo para subir.")
            return None

        file_name = uploaded_file.name
        file_bytes = uploaded_file.getvalue() # Obtiene los bytes del archivo subido

        # Intenta subir el archivo. Si ya existe, Supabase Storage lo sobrescribe por defecto.
        # Puedes añadir lógica para evitar duplicados si es necesario (ej. renombrar o verificar existencia).
        res = supabase.storage.from_(SUPABASE_BUCKET_NAME).upload(file_name, file_bytes, {"ContentType": uploaded_file.type})

        # Si hay un error, el resultado de Supabase tendrá un campo 'error'
        if res.data:
            # Construir la URL pública del archivo. El formato es fijo de Supabase.
            public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{file_name}"
            st.success(f"Archivo '{file_name}' subido a Supabase Storage.")
            return public_url
        elif res.error:
            st.error(f"Error al subir archivo a Supabase Storage: {res.error.message}")
            return None
    except Exception as e:
        st.error(f"Excepción al subir archivo a Supabase Storage: {e}")
        return None

def guardar_evaluacion_en_db(eval_data, file_url_supabase):
    """Guarda los datos de la evaluación en la tabla 'evaluaciones' de Supabase."""
    try:
        # Añade la URL del archivo al diccionario de datos antes de insertar
        eval_data['url_archivo_supabase'] = file_url_supabase
        
        # Inserta los datos en la tabla 'evaluaciones'
        response = supabase.table('evaluaciones').insert(eval_data).execute()
        
        # El objeto 'response' de Supabase contiene 'data' si fue exitoso o 'error' si falló
        if response.data:
            st.success(f"Evaluación de '{eval_data['organizacion_nombre']}' guardada en la base de datos.")
        else:
            st.error(f"Error al guardar en la base de datos: {response.error.message}")
    except Exception as e:
        st.error(f"Excepción al guardar la evaluación en Supabase DB: {e}")

@st.cache_data(ttl=600) # Cachea los datos de la DB por 10 minutos para evitar consultas repetidas
def obtener_evaluaciones_de_db():
    """Obtiene todas las evaluaciones de la tabla 'evaluaciones' de Supabase."""
    try:
        # Consulta todos los registros y los ordena por fecha de evaluación descendente
        response = supabase.table('evaluaciones').select('*').order('fecha_evaluacion', desc=True).execute()
        
        if response.data:
            return pd.DataFrame(response.data)
        else:
            st.warning(f"No hay evaluaciones guardadas en la base de datos o error al cargar: {response.error.message}")
            return pd.DataFrame() # Retorna un DataFrame vacío si no hay datos
    except Exception as e:
        st.error(f"Excepción al obtener evaluaciones de Supabase DB: {e}")
        return pd.DataFrame()

def eliminar_evaluacion_de_db(eval_id, file_name_in_storage):
    """
    Elimina una evaluación de la base de datos y su archivo asociado de Supabase Storage.
    Recibe el ID de la evaluación en la DB y el nombre del archivo en Storage.
    """
    try:
        # 1. Eliminar el archivo de Supabase Storage
        # El método remove espera una lista de nombres de archivos
        res_storage = supabase.storage.from_(SUPABASE_BUCKET_NAME).remove([file_name_in_storage])
        
        # La respuesta de remove puede ser None si no hay error pero no hay data, o tener 'error'
        if res_storage and res_storage.error:
            st.warning(f"Error al eliminar archivo '{file_name_in_storage}' de Storage (puede que ya no exista): {res_storage.error.message}")
        else:
            st.success(f"Archivo '{file_name_in_storage}' eliminado de Storage.")

        # 2. Eliminar el registro de la base de datos
        # .eq('id', eval_id) significa "donde la columna 'id' sea igual a 'eval_id'"
        response = supabase.table('evaluaciones').delete().eq('id', eval_id).execute()
        
        if response.data:
            st.success(f"Evaluación eliminada de la base de datos.")
        else:
            st.error(f"Error al eliminar de la base de datos: {response.error.message}")
    except Exception as e:
        st.error(f"Excepción al eliminar la evaluación: {e}")

# --- 4. Función para visualizar los resultados en Streamlit ---
def visualizar_resultados_streamlit(df_data):
    """
    Genera y muestra gráficos y tablas de los resultados de las evaluaciones en Streamlit.
    """
    if df_data.empty:
        st.warning("No hay datos para visualizar. Sube una evaluación o verifica que existan en la base de datos.")
        return

    # Ordenar por CIX_Total para una mejor visualización en el gráfico de barras
    df_data = df_data.sort_values(by='cix_total', ascending=False)

    st.subheader("📊 Comparación de Puntuación CIX Total por Organización")
    # Crear la figura y los ejes para el gráfico de Matplotlib/Seaborn
    fig, ax = plt.subplots(figsize=(10, max(6, len(df_data) * 0.7))) # Ajustar tamaño según el número de empresas
    sns.barplot(x='cix_total', y='organizacion_nombre', data=df_data, palette='viridis', ax=ax)
    ax.set_xlabel('Puntuación CIX Total (0-1)')
    ax.set_ylabel('Organización')
    ax.set_xlim(0, 1) # Asegura que el eje x vaya de 0 a 1
    ax.grid(axis='x', linestyle='--', alpha=0.7)
    plt.tight_layout() # Ajusta el diseño para que no se superpongan los elementos
    st.pyplot(fig) # Muestra el gráfico en la interfaz de Streamlit

    st.subheader("📋 Tabla de Resultados Detallada")
    # Selecciona las columnas clave para mostrar en la tabla principal
    columnas_a_mostrar = [
        'organizacion_nombre', 'periodo_informe', 'cix_total',
        'datos_actividad', 'factores_emision', 'alcance_1', 'alcance_2',
        'alcance_3', 'categorias_excluidas', 'consolidacion', 'auditoria',
        'compromisos_de_reduccion', 'evaluacion_de_incertidumbre',
        'fecha_evaluacion', 'url_archivo_supabase'
    ]
    # Muestra el DataFrame en Streamlit
    st.dataframe(df_data[columnas_a_mostrar])

    # Ofrecer descarga del CSV consolidado (del DataFrame actual en memoria)
    csv_descarga = df_data.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="Descargar Resultados Consolidados (CSV)",
        data=csv_descarga,
        file_name="resultados_cix_consolidados.csv",
        mime="text/csv",
    )

# --- 5. Lógica Principal de la Interfaz de Usuario con Streamlit ---
st.set_page_config(layout="wide", page_title="Analizador CIX OAC") # Configura la página

st.title("📊 Analizador de Transparencia de Huella de Carbono (CIX)")
st.markdown("""
Esta herramienta automatiza el procesamiento de informes de evaluación de huella de carbono,
calcula una puntuación de Transparencia (CIX simplificado) y permite gestionar y visualizar
los resultados de forma persistente utilizando Supabase.
""")

st.sidebar.header("Opciones de Navegación")
# Crea un menú de radio en la barra lateral para cambiar de sección
menu_selection = st.sidebar.radio(
    "Selecciona una sección:",
    ["Subir Nueva Evaluación", "Ver Evaluaciones Guardadas"]
)

# --- Sección: Subir Nueva Evaluación ---
if menu_selection == "Subir Nueva Evaluación":
    st.header("Sube un Nuevo Informe de Evaluación")
    st.markdown("Sube un archivo Excel o CSV de evaluación de huella de carbono para procesar y guardar en el sistema.")

    uploaded_file = st.file_uploader(
        "Selecciona el archivo de evaluación (Excel o CSV):",
        type=["csv", "xlsx"],
        accept_multiple_files=False # Para simplificar, permitimos solo un archivo a la vez para la subida inicial
    )

    if uploaded_file is not None:
        # Asegurarse de que el objeto de archivo esté al inicio antes de leerlo
        uploaded_file.seek(0)
        
        with st.spinner(f"Procesando y subiendo '{uploaded_file.name}'... Esto puede tomar unos segundos."):
            # 1. Procesar el archivo para extraer los datos y calcular el CIX
            # Pasamos el objeto BytesIO del archivo subido y su nombre
            eval_data_processed = procesar_evaluacion_empresa(io.BytesIO(uploaded_file.getvalue()), uploaded_file.name)

            if eval_data_processed:
                st.success(f"Archivo '{uploaded_file.name}' procesado correctamente.")
                st.markdown("---")
                st.subheader("Datos Extraídos y Puntuación CIX:")
                st.json(eval_data_processed) # Muestra los datos que se guardarán

                # 2. Subir el archivo original a Supabase Storage
                file_url_supabase = subir_archivo_a_supabase(uploaded_file)

                if file_url_supabase:
                    # 3. Guardar los datos de la evaluación en la base de datos de Supabase
                    guardar_evaluacion_en_db(eval_data_processed, file_url_supabase)
                    # Establece un estado para que al cambiar a la otra pestaña se refresquen los datos
                    st.session_state['refresh_data'] = True
                    st.success("Operación completada. Puedes ir a 'Ver Evaluaciones Guardadas' para ver el nuevo registro.")
                else:
                    st.error("No se pudo subir el archivo o guardar su URL en la base de datos. Por favor, revisa los logs.")
            else:
                st.error("No se pudo procesar el archivo. Revisa el formato o los mensajes de error.")

# --- Sección: Ver Evaluaciones Guardadas ---
elif menu_selection == "Ver Evaluaciones Guardadas":
    st.header("Evaluaciones Almacenadas en la Base de Datos")
    st.markdown("Revisa, analiza y gestiona todas las evaluaciones de huella de carbono guardadas.")

    # Mecanismo para refrescar los datos de la DB si se ha realizado una acción (subir/eliminar)
    if 'refresh_data' not in st.session_state:
        st.session_state['refresh_data'] = False

    if st.session_state['refresh_data']:
        st.cache_data.clear() # Limpia la caché para forzar una nueva lectura de la DB
        st.session_state['refresh_data'] = False # Restablece la bandera

    df_evaluaciones = obtener_evaluaciones_de_db() # Obtiene los datos más recientes

    if not df_evaluaciones.empty:
        st.write(f"📊 Total de evaluaciones cargadas: **{len(df_evaluaciones)}**")
        visualizar_resultados_streamlit(df_evaluaciones) # Muestra gráficos y tabla

        st.markdown("---")
        st.subheader("🗑️ Gestionar y Eliminar Evaluaciones")
        # Prepara las opciones para el selectbox de eliminación
        opciones_eliminar = [f"{row['organizacion_nombre']} - {row['periodo_informe']} (ID: {row['id']})"
                            for index, row in df_evaluaciones.iterrows()]
        
        # Agrega una opción vacía para que el usuario pueda seleccionar nada al inicio
        opciones_eliminar.insert(0, "--- Selecciona una evaluación ---")
        
        evaluacion_a_eliminar_str = st.selectbox(
            "Selecciona una evaluación para eliminar (se eliminará el registro y el archivo original):",
            options=opciones_eliminar
        )

        if evaluacion_a_eliminar_str != "--- Selecciona una evaluación ---":
            # Extraer el ID de la evaluación seleccionada de la cadena
            # Asumimos el formato "NOMBRE - PERIODO (ID: UUID)"
            eval_id_to_delete = evaluacion_a_eliminar_str.split('(ID: ')[1][:-1]
            
            # Buscar el nombre del archivo en Supabase Storage usando el ID de la DB
            # La URL completa está en la columna 'url_archivo_supabase'
            file_url_to_delete = df_evaluaciones[df_evaluaciones['id'] == eval_id_to_delete]['url_archivo_supabase'].iloc[0]
            # Extraer solo el nombre del archivo de la URL
            file_name_in_storage = file_url_to_delete.split('/')[-1]

            if st.button(f"Confirmar Eliminación de '{evaluacion_a_eliminar_str}'", type="secondary"):
                eliminar_evaluacion_de_db(eval_id_to_delete, file_name_in_storage)
                st.session_state['refresh_data'] = True # Activa el refresco de datos
                st.experimental_rerun() # Fuerza a Streamlit a volver a ejecutar el script para actualizar la UI
    else:
        st.info("No se han encontrado evaluaciones guardadas. Sube una nueva evaluación para empezar.")

st.markdown("---")
st.markdown("Desarrollado para el **Observatorio de Acción Climática (OAC)** - Proyecto Segundo Parcial")
st.caption("Ingeniería Mecatrónica")