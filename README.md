# Job Hunter Chile: Scraper multiplataforma + evaluador con Gemini

Busca ofertas de desarrollo en **LinkedIn, Get on Board, Computrabajo y Chiletrabajos**, las filtra, las evalúa con Google Gemini contra el currículum en `cv.json` y envía un correo cuando encuentra un match.

## 🚀 Características
- **Multiplataforma** (`fuentes.py`): LinkedIn (API guest), Get on Board (API pública), Computrabajo y Chiletrabajos. Términos amplios: desarrollador, developer, programador, ingeniero de software, ingeniero informático, informático.
- **Filtros estáticos** (antes de gastar cuota de IA): cargo de desarrollo, seniority (senior, ssr, lead, jefe…), prácticas profesionales, inglés exigido u oferta escrita en inglés, y ubicación presencial fuera de la zona.
- **Ubicación**: remoto; presencial/híbrido en la zona cercana a Lontué (Talca, Curicó, Molina, San Fernando, Rancagua, etc.); Santiago solo remoto o híbrido con máximo 2 días presenciales.
- **Gemini extrae, el código decide**: `gemini-2.5-flash` (temperatura 0) solo extrae datos de la oferta (años de experiencia, modalidad, días presenciales, zona, inglés, práctica, stack compatible). `decidir()` aplica reglas fijas: 4+ años o stack incompatible = descartado, 3 años = cercano, híbrido en Santiago sin días = match con aviso. Los avisos llegan en el correo. Máximo `MAX_EVALUACIONES_GEMINI` evaluaciones por ejecución; el resto queda en cola.
- **Sin duplicados**: `revisado.json` guarda lo ya evaluado, por URL y por empresa+título (la misma oferta en varias plataformas se evalúa una sola vez).

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
1. **Frecuencia**: Se ejecuta cada 2 horas, en el minuto 17 de las horas pares de Chile (GitHub puede atrasar o saltarse ejecuciones programadas), y también puede ejecutarse manualmente desde la pestaña "Actions" en GitHub.
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
