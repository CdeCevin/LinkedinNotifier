# LinkedIn Job Scraper & Gemini Evaluator

Este proyecto es un scraper automatizado diseñado para buscar ofertas de empleo en LinkedIn, analizarlas mediante inteligencia artificial (Google Gemini) en base a un perfil profesional en formato JSON, y enviar alertas por correo electrónico cuando se encuentra un "Match" ideal.

## 🚀 Características
- **Búsqueda automática**: Consulta las últimas ofertas de empleo en LinkedIn utilizando palabras clave específicas (Junior, Trainee, Backend, Developer, etc.) para Chile.
- **Filtros estáticos**: Descarte rápido de ofertas no aptas (ej. cargos Senior o exigencia de inglés avanzado fluido) antes de consumir la API de IA.
- **Evaluación Inteligente**: Uso del modelo `gemini-2.5-flash` para comparar la descripción de la oferta directamente con el currículum en `cv.json`.
- **Alertas por Correo**: Envío automático de notificaciones a tu Gmail cuando hay un match positivo.
- **Control de Duplicados**: Almacenamiento local e incremental de ofertas evaluadas en `revisado.json` para evitar analizar la misma oferta en ejecuciones posteriores, optimizando recursos y llamadas a la API.

---

## 🛠️ Configuración Local

### 1. Requisitos Previos
- Python 3.8 o superior instalado.
- Cuenta de Google Cloud con API Key de Gemini habilitada (o desde Google AI Studio).
- Una cuenta de Gmail emisora con una "Contraseña de Aplicación" configurada (requiere activar la verificación en dos pasos en Gmail).

### 2. Instalación
1. Clona este repositorio o descarga los archivos.
2. Instala las dependencias necesarias:
   ```bash
   pip install -r requirements.txt
   ```

### 3. Configuración del Entorno (.env)
Crea un archivo llamado `.env` en la raíz del proyecto (este archivo está excluido en el `.gitignore` y **nunca** se subirá a GitHub para proteger tus credenciales):
```env
GEMINI_API_KEY=tu_api_key_de_gemini
EMAIL_REMITENTE=tu_correo_emisor@gmail.com
EMAIL_DESTINATARIO=tu_correo_receptor@gmail.com
EMAIL_PASSWORD=tu_contrasena_de_aplicacion_gmail
```

### 4. Personalización del Currículum
Edita el archivo [cv.json](cv.json) con tus datos personales, experiencia, habilidades y educación. El scraper enviará este archivo JSON exacto al modelo de Gemini para realizar la evaluación.

### 5. Ejecución
Para iniciar el scraper de forma manual:
```bash
python hunter.py
```

---

## 🤖 Automatización con GitHub Actions

El proyecto incluye un flujo de trabajo automatizado mediante GitHub Actions ubicado en `.github/workflows/scrape.yml`. 

### ¿Cómo funciona?
1. **Frecuencia**: Se ejecuta automáticamente todos los días a las **13:00 UTC** (aproximadamente a las 09:00 AM hora de Chile) y también puede ejecutarse manualmente desde la pestaña "Actions" en GitHub.
2. **Secretos**: Utiliza los secretos del repositorio para configurar de forma segura las variables de entorno sin exponerlas en el código.
3. **Persistencia**: Tras analizar las ofertas, el pipeline realiza un commit y push automático del archivo `revisado.json` actualizado de regreso al repositorio Git, de modo que las ejecuciones diarias siguientes sepan qué ofertas ya fueron evaluadas.

### Configuración en tu Repositorio de GitHub
Para que el flujo de trabajo funcione correctamente, debes agregar las credenciales en la configuración de tu repositorio en GitHub:

1. Ve a tu repositorio en GitHub.
2. Entra a **Settings** > **Secrets and variables** > **Actions**.
3. Haz clic en **New repository secret** y añade las siguientes variables:
   - `GEMINI_API_KEY`: Tu API key de Gemini.
   - `EMAIL_REMITENTE`: El correo de Gmail que envía las alertas.
   - `EMAIL_DESTINATARIO`: El correo que recibe las alertas.
   - `EMAIL_PASSWORD`: La contraseña de aplicación de Gmail (16 caracteres).

4. **Permisos de Escritura del Token**:
   - Ve a **Settings** > **Actions** > **General**.
   - Desplázate hasta **Workflow permissions**.
   - Selecciona **Read and write permissions** y haz clic en **Save** (esto es crucial para permitir que la acción actualice el archivo `revisado.json`).
