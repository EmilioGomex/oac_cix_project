# Analizador de Transparencia de Huella de Carbono (CIX)

Este proyecto es una aplicación web desarrollada con Streamlit que automatiza el procesamiento de informes de evaluación de huella de carbono, calcula una puntuación de Transparencia (CIX simplificado) y permite gestionar y visualizar los resultados de forma persistente utilizando Supabase.

## Características
- Subida y procesamiento automático de archivos de evaluación (Excel o CSV).
- Cálculo de puntuaciones individuales y totales de indicadores CIX.
- Almacenamiento seguro de archivos y resultados en Supabase (Storage y Base de Datos).
- Visualización de resultados con gráficos y tablas interactivas.
- Descarga de resultados consolidados en formato CSV.
- Gestión y eliminación de evaluaciones desde la interfaz.

## Requisitos
- Python 3.8+
- Streamlit
- pandas
- matplotlib
- seaborn
- supabase-py

Instala las dependencias con:

```
pip install -r requirements.txt
```

## Configuración
1. Crea un archivo `.streamlit/secrets.toml` con tus credenciales de Supabase:

```
SUPABASE_URL = "<tu_supabase_url>"
SUPABASE_KEY = "<tu_supabase_key>"
```

2. Asegúrate de tener un bucket en Supabase Storage llamado `evaluaciones-cix-files` y una tabla `evaluaciones` con las columnas necesarias.


## Uso

Puedes probar la aplicación desplegada aquí: [https://oac-cix-project.streamlit.app/](https://oac-cix-project.streamlit.app/)

O ejecuta la aplicación localmente con:

```
streamlit run app.py
```

Sigue las instrucciones en la interfaz para subir, visualizar y gestionar evaluaciones.

## Autor
Desarrollado para el **Observatorio de Acción Climática (OAC)** - Proyecto Segundo Parcial

Ingeniería Mecatrónica - ESPOL
