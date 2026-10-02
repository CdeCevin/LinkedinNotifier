import os
import re
import json
import urllib.parse
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
import time
import random
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Importamos la NUEVA librería oficial de Google
from google import genai
from google.genai import types

# Cargar variables de entorno del archivo .env
load_dotenv()

# Instanciar el nuevo cliente de Gemini
client = genai.Client()

# Palabras clave para descartar ofertas basura antes de usar la IA.
# Se buscan como palabras completas (\b), no como trozos de texto: así "sr" no
# calza con cualquier palabra que contenga esas letras, ni "lead" con "leading".
# Seniority se revisa SOLO en el título: las descripciones de ofertas junior
# suelen mencionar "trabajarás con desarrolladores senior" o "tech lead".
PALABRAS_DESCARTE_TITULO = [
    "senior", "sr", "semi senior", "semi-senior", "semisenior", "ssr",
    "lead", "líder", "lider", "architect", "arquitecto", "principal", "staff", "head",
    # Prácticas profesionales (solo para estudiantes, no aplica a titulados)
    "práctica", "practica", "prácticas", "practicas", "practicante", "practicantes",
    "pasantía", "pasantia", "pasante", "internship", "intern", "interns",
    "becario", "becaria", "memorista", "alumno en práctica", "estudiante en práctica",
]
# Inglés se revisa en la descripción completa
PALABRAS_DESCARTE_DESCRIPCION = [
    "bilingue", "bilingüe", "bilingual", "ingles avanzado", "inglés avanzado",
    "ingles fluido", "inglés fluido", "fluent english", "advanced english",
    "english fluency", "native english",
    "ingles conversacional", "inglés conversacional", "conversational english",
    "buen nivel de ingles", "buen nivel de inglés", "upper intermediate", "upper-intermediate",
    "english proficiency", "proficient in english", "strong english",
]

# Si la oferta está escrita en inglés, se asume que el trabajo es en inglés
PALABRAS_INGLES = {"the", "and", "with", "you", "our", "will", "for", "are", "your", "experience", "team", "we"}
PALABRAS_ESPANOL = {"el", "la", "los", "las", "y", "con", "para", "que", "de", "en", "experiencia", "equipo", "nuestro"}

def oferta_en_ingles(texto):
    palabras = re.findall(r"[a-záéíóúñ]+", texto.lower())
    ingles = sum(p in PALABRAS_INGLES for p in palabras)
    espanol = sum(p in PALABRAS_ESPANOL for p in palabras)
    return ingles > espanol

def compilar_patron(palabras):
    return re.compile(r"\b(" + "|".join(re.escape(p) for p in palabras) + r")\b", re.IGNORECASE)

PATRON_TITULO = compilar_patron(PALABRAS_DESCARTE_TITULO)
PATRON_DESCRIPCION = compilar_patron(PALABRAS_DESCARTE_DESCRIPCION)

# Ruta absoluta automática para cv.json y revisado.json al lado del script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CV_PATH = os.path.join(BASE_DIR, "cv.json")
REVISADO_PATH = os.path.join(BASE_DIR, "revisado.json")

with open(CV_PATH, "r", encoding="utf-8") as f:
    CV_TEXTO_JSON = f.read()

# =====================================================================
# FUNCIÓN UTILIDAD: LIMPIAR TEXTO PARA CONSOLA WINDOWS
# =====================================================================
def limpiar_texto(texto):
    """Elimina caracteres como emojis que la consola antigua de Windows no puede codificar"""
    if not texto:
        return ""
    return texto.encode('ascii', 'ignore').decode('ascii')


# =====================================================================
# FUNCIONES DE PERSISTENCIA: CONTROL DE OFERTAS REVISADAS
# =====================================================================
def cargar_urls_revisadas():
    """Carga las URLs de ofertas que ya han sido revisadas en ejecuciones anteriores"""
    if os.path.exists(REVISADO_PATH):
        try:
            with open(REVISADO_PATH, "r", encoding="utf-8") as f:
                contenido = f.read().strip()
                if not contenido:
                    return set()
                revisados = json.loads(contenido)
                if isinstance(revisados, list):
                    return {r["url"] for r in revisados if isinstance(r, dict) and "url" in r}
        except Exception as e:
            print(f"[!] Error al cargar revisado.json: {e}")
    return set()


def guardar_revisado(empleo, calza, motivo):
    """Guarda una oferta en el archivo revisado.json para no volver a evaluarla"""
    revisados = []
    if os.path.exists(REVISADO_PATH):
        try:
            with open(REVISADO_PATH, "r", encoding="utf-8") as f:
                contenido = f.read().strip()
                if contenido:
                    revisados = json.loads(contenido)
                    if not isinstance(revisados, list):
                        revisados = []
        except Exception as e:
            print(f"[!] Error al leer revisado.json para guardar: {e}")

    # Evitar duplicados por seguridad
    urls = {r["url"] for r in revisados if isinstance(r, dict) and "url" in r}
    if empleo["url"] not in urls:
        nuevo_registro = {
            "url": empleo["url"],
            "titulo": empleo["titulo"],
            "empresa": empleo["empresa"],
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "calza": calza,
            "motivo": motivo
        }
        revisados.append(nuevo_registro)
        try:
            with open(REVISADO_PATH, "w", encoding="utf-8") as f:
                json.dump(revisados, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[!] Error al escribir en revisado.json: {e}")


# =====================================================================
# FUNCIÓN 1: IR A BUSCAR LAS OFERTAS A LINKEDIN (GUEST API)
# =====================================================================
def buscar_ofertas_linkedin():
    keywords = '(Junior OR Trainee) AND ("Backend" OR "Frontend" OR "Fullstack" OR "Desarrollador" OR "Developer" OR "Programador")'
    location = "Chile"
    
    keywords_encoded = urllib.parse.quote(keywords)
    location_encoded = urllib.parse.quote(location)
    
    empleos_totales = []
    
    # Recorrer las primeras 3 páginas (posiciones 0, 25 y 50)
    for pagina in range(0, 75, 25):
        print(f"[*] Extrayendo resultados de LinkedIn (Iniciando en posicion {pagina})...")

        # f_TPR=r604800 = última semana; sortBy=DD = más recientes primero
        # (sin sortBy LinkedIn ordena por relevancia y las ofertas nuevas pueden quedar fuera de las 3 páginas)
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={keywords_encoded}&location={location_encoded}&f_TPR=r604800&sortBy=DD&start={pagina}"
        
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            print(f"[-] No se pudieron obtener mas paginas (Status: {response.status_code})")
            break
            
        soup = BeautifulSoup(response.text, "html.parser")
        tarjetas = soup.find_all("li")
        
        if not tarjetas:
            break
            
        for tarjeta in tarjetas:
            link_elem = tarjeta.find("a", class_="base-card__full-link")
            if link_elem:
                url_limpia = link_elem["href"].split("?")[0]
                titulo = tarjeta.find("h3", class_="base-search-card__title").text.strip()
                empresa = tarjeta.find("h4", class_="base-search-card__subtitle").text.strip()
                ubicacion_elem = tarjeta.find("span", class_="job-search-card__location")
                ubicacion = ubicacion_elem.text.strip() if ubicacion_elem else ""

                if url_limpia not in [e["url"] for e in empleos_totales]:
                    empleos_totales.append({"titulo": titulo, "empresa": empresa, "ubicacion": ubicacion, "url": url_limpia})
                    
        # Pausa aleatoria para no saturar al buscar paginación
        time.sleep(random.uniform(2, 4))
                    
    return empleos_totales


# =====================================================================
# FUNCIÓN 2: EXTRAER EL TEXTO COMPLETO DE UNA OFERTA
# =====================================================================
def obtener_descripcion_completa(url_empleo, reintentos=2):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    for intento in range(reintentos):
        res = requests.get(url_empleo, headers=headers)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            descripcion_elem = soup.find("div", class_="show-more-less-html__markup")
            return descripcion_elem.text.strip() if descripcion_elem else None
            
        if res.status_code == 429:
            print(f"[!] Limite de peticiones alcanzado al obtener descripcion (429). Reintentando en breve...")
            time.sleep(random.uniform(3, 6))
        else:
            break
            
    return None


# =====================================================================
# FUNCIÓN 3: CONSULTAR A LA API DE GEMINI
# =====================================================================
def evaluar_con_gemini(empleo, descripcion_empleo):
    prompt = f"""
    Actua como un reclutador tecnico experto en TI. Evalua si el candidato del siguiente CV calza con la oferta de empleo.
    
    REGLAS CRITICAS (DE CUMPLIMIENTO OBLIGATORIO). Si la oferta incumple CUALQUIERA, "cumple_reglas" debe ser false:
    1. UBICACION: La oferta DEBE ser:
       - 100% Remota (desde cualquier parte de Chile o global). Si el titulo o la ubicacion de LinkedIn dicen "Remote"/"Remoto", considerala remota salvo que la descripcion diga lo contrario.
       - O presencial/hibrida EXCLUSIVAMENTE en Talca, la Region del Maule en general, o comunas aledañas.
       Si exige presencialidad o modalidad hibrida en Santiago, Viña del Mar, Concepcion o cualquier otra region fuera del Maule, incumple la regla.
       Si la oferta NO indica la modalidad ni la ciudad, NO la rechaces por eso: cumple la regla, pero marcala como "cercano" (no "calza") para que el candidato confirme la modalidad.
    2. INGLES: El candidato solo LEE documentacion tecnica en ingles; no habla ni escribe en ingles de forma profesional. Incumple la regla si la oferta:
       - pide ingles fluido, avanzado, conversacional, intermedio-alto, B2 o superior, o "buen nivel de ingles";
       - requiere usar ingles en el trabajo diario (reuniones, comunicacion escrita, chats, documentacion o colaboracion con equipos o clientes angloparlantes, por ejemplo un equipo en Estados Unidos);
       - tiene entrevistas en ingles, o la descripcion esta escrita en ingles (en ese caso se asume que el trabajo es en ingles).
       Solo cumple si no menciona ingles, o si lo pide solo como "deseable" o para leer documentacion.
    3. NIVEL: El candidato es titulado. Incumple la regla si es una practica profesional, pasantia, internship o cualquier cargo exclusivo para estudiantes. Los programas trainee o para recien titulados SI son validos.
    
    CRITERIOS FLEXIBLES (no son motivo de rechazo):
    - TECNOLOGIAS: El candidato aprende rapido y tiene facilidad para Frontend y Fullstack (HTML, CSS, TypeScript, Tailwind, React, Angular, etc.). Si la oferta pide lenguajes o frameworks que no estan en el CV, NO la descartes por eso.
    - EXPERIENCIA: Sus proyectos (Caudal Rio y UAPSCI, desarrollados en produccion desde 2025) equivalen a 1 a 1,5 años de experiencia real. Si la oferta pide hasta 2 años, considera que calza. Si pide 3 años, puede ser "cercano". Si pide 4 o mas años, no calza.
    
    CV del Candidato (JSON):
    {CV_TEXTO_JSON}
    
    Oferta de Empleo:
    - Titulo: {empleo['titulo']}
    - Empresa: {empleo['empresa']}
    - Ubicacion segun LinkedIn: {empleo.get('ubicacion') or 'No indicada'}
    
    Descripcion:
    {descripcion_empleo}
    
    Responde ESTRICTAMENTE con un objeto JSON valido con esta estructura, sin textos extras:
    {{
        "cumple_reglas": true o false,
        "calza": true o false,
        "cercano": true o false,
        "motivo": "Explicacion breve de una frase del porque calza, es cercano, o se rechaza"
    }}
    
    Nota sobre 'cercano': Si cumple las reglas criticas pero no es un match perfecto (falta alguna herramienta, pide algo mas de experiencia o no indica modalidad), pon "cercano": true para que lo revise manualmente.
    """
    for intento in range(3):
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"[!] Error en API Gemini (Intento {intento+1}/3): {e}")
            if intento < 2:
                time.sleep(random.uniform(15, 30)) # Espera larga para evitar limites de cuota (429)

    # Si fallan todos los reintentos, devolvemos un flag de error
    return {"error_api": True, "motivo": "Fallo total en la consulta de IA"}


# =====================================================================
# FUNCIÓN 4: ENVIARTE LA ALERTA A TU GMAIL
# =====================================================================
def enviar_alerta_correo(empleo, motivo, es_cercano=False):
    remitente = os.environ.get("EMAIL_REMITENTE")
    destinatario = os.environ.get("EMAIL_DESTINATARIO")
    password_aplicacion = os.environ.get("EMAIL_PASSWORD")
    
    if not all([remitente, destinatario, password_aplicacion]):
        print("[!] Faltan credenciales de correo en el archivo .env")
        return

    msg = MIMEMultipart()
    msg["From"] = remitente
    msg["To"] = destinatario
    # Limpiamos el asunto por si el título trae emojis
    tipo_match = "MATCH CERCANO" if es_cercano else "MATCH"
    msg["Subject"] = limpiar_texto(f"{tipo_match}: {empleo['titulo']} en {empleo['empresa']}")
    
    cuerpo = f"""
    Hola Kevin,
    
    Gemini encontro una oferta {'cercana para revisar' if es_cercano else 'ideal para ti'}:
    
    - Puesto: {empleo['titulo']}
    - Empresa: {empleo['empresa']}
    - Motivo de la IA: {motivo}
    - Link directo: {empleo['url']}
    
    Mucho exito en la postulacion!
    """
    msg.attach(MIMEText(cuerpo, "plain"))
    
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(remitente, password_aplicacion)
        server.send_message(msg)
        server.quit()
        print("[+] Notificacion enviada con exito a tu correo.")
    except Exception as e:
        print(f"[!] No se pudo enviar el correo: {e}")


# =====================================================================
# FLUJO PRINCIPAL (ORQUESTADOR)
# =====================================================================
if __name__ == "__main__":
    print("[*] Buscando ofertas publicadas en el ultimo mes...")
    ofertas_en_bruto = buscar_ofertas_linkedin()
    print(f"[*] Se encontraron {len(ofertas_en_bruto)} ofertas preliminares.")
    
    # Cargar URLs ya revisadas
    urls_revisadas = cargar_urls_revisadas()
    print(f"[*] Se cargaron {len(urls_revisadas)} URLs previamente revisadas.")
    
    for empleo in ofertas_en_bruto:
        # Evitar procesar lo ya revisado
        if empleo["url"] in urls_revisadas:
            print(f"[-] Omitiendo (Ya revisado): {limpiar_texto(empleo['titulo'])} en {limpiar_texto(empleo['empresa'])}")
            continue
            
        texto_descripcion = obtener_descripcion_completa(empleo["url"])
        if not texto_descripcion:
            print(f"[!] No se pudo obtener la descripcion de: {limpiar_texto(empleo['titulo'])}")
            continue
            
        # Filtro estático por código
        coincidencia = PATRON_TITULO.search(empleo["titulo"]) or PATRON_DESCRIPCION.search(texto_descripcion)
        if coincidencia:
            palabra = coincidencia.group(0)
            print(f"[-] Descartado (Codigo): {limpiar_texto(empleo['titulo'])} - palabra clave '{limpiar_texto(palabra)}'.")
            guardar_revisado(empleo, False, f"Descartado por palabra clave estatica: '{palabra}'")
            continue

        if oferta_en_ingles(texto_descripcion):
            print(f"[-] Descartado (Codigo): {limpiar_texto(empleo['titulo'])} - oferta escrita en ingles.")
            guardar_revisado(empleo, False, "Descartado: la oferta esta escrita en ingles")
            continue
            
        # Filtro con Inteligencia Artificial
        print(f"[~] Analizando con Gemini: {limpiar_texto(empleo['titulo'])} en {limpiar_texto(empleo['empresa'])}...")
        veredicto = evaluar_con_gemini(empleo, texto_descripcion)
        
        if veredicto.get("error_api"):
            print("[!] API de Gemini no disponible. Se omitira esta oferta para mantenerla en cola.")
            continue # No se guarda en revisado.json, por lo que se reintentara en la proxima ejecucion
            
        # Las reglas criticas mandan: si no se cumplen, no hay match aunque Gemini marque "cercano"
        cumple_reglas = veredicto.get("cumple_reglas") is True
        calza = cumple_reglas and veredicto.get("calza") is True
        cercano = cumple_reglas and veredicto.get("cercano") is True
        motivo = veredicto.get("motivo", "Sin motivo")
        
        # Guardar en revisados
        guardar_revisado(empleo, calza or cercano, motivo)
        
        if calza or cercano:
            nivel = "MATCH ENCONTRADO" if calza else "MATCH CERCANO"
            print(f"[+] {nivel}!: {limpiar_texto(motivo)}")
            # ACTIVADO: Enviará el correo
            enviar_alerta_correo(empleo, motivo, es_cercano=cercano and not calza) 
        else:
            print(f"[-] Pasando: {limpiar_texto(motivo)}")
            
        # Pausa aleatoria antes de la siguiente oferta para evitar bloqueos
        time.sleep(random.uniform(1.5, 3.5))