import os
import json
import urllib.parse
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
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

# Palabras clave para descartar ofertas basura antes de usar la IA
PALABRAS_DESCARTE = [
    "senior", "sr", "lead", "architect", "semi-senior", "ssr", 
    "bilingue", "bilingual", "ingles avanzado", "fluent english", "advanced english"
]

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
    keywords = '(Junior OR Trainee) AND ("Backend" OR "Desarrollador" OR "Developer" OR "Programador") AND ("Python" OR "Node" OR "TypeScript")'
    location = "Chile"
    
    keywords_encoded = urllib.parse.quote(keywords)
    location_encoded = urllib.parse.quote(location)
    
    empleos_totales = []
    
    # Recorrer las primeras 5 páginas (del 0 al 40, saltando de 10 en 10)
    for pagina in range(0, 50, 10):
        print(f"[*] Extrayendo resultados de LinkedIn (Iniciando en posicion {pagina})...")
        
        # Parámetro f_TPR=r2592000 equivale al último mes
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={keywords_encoded}&location={location_encoded}&f_TPR=r2592000&start={pagina}"
        
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
                
                if url_limpia not in [e["url"] for e in empleos_totales]:
                    empleos_totales.append({"titulo": titulo, "empresa": empresa, "url": url_limpia})
                    
    return empleos_totales


# =====================================================================
# FUNCIÓN 2: EXTRAER EL TEXTO COMPLETO DE UNA OFERTA
# =====================================================================
def obtener_descripcion_completa(url_empleo):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    res = requests.get(url_empleo, headers=headers)
    if res.status_code != 200:
        return None
        
    soup = BeautifulSoup(res.text, "html.parser")
    descripcion_elem = soup.find("div", class_="show-more-less-html__markup")
    
    return descripcion_elem.text.strip() if descripcion_elem else None


# =====================================================================
# FUNCIÓN 3: CONSULTAR A LA API DE GEMINI
# =====================================================================
def evaluar_con_gemini(descripcion_empleo):
    prompt = f"""
    Actua como un reclutador tecnico experto en TI. Evalua si el candidato del siguiente CV calza con la oferta de empleo.
    
    REGLAS CRITICAS:
    1. El candidato es estudiante de ultimo año de Ingenieria Civil Informatica. Puede trabajar jornada completa o parcial (remoto o hibrido/presencial cerca de su zona (TALCA)).
    2. El candidato NO habla ingles fluido (solo lee documentacion). Si la oferta exige explicitamente hablar ingles en el dia a dia o entrevistas en ingles, RECHAZALA.
    
    CV del Candidato (JSON):
    {CV_TEXTO_JSON}
    
    Descripcion de la Oferta de Empleo:
    {descripcion_empleo}
    
    Responde ESTRICTAMENTE con un objeto JSON valido con esta estructura, sin textos extras:
    {{
        "calza": true o false,
        "motivo": "Explicacion breve de una frase del porque calza o se rechaza"
    }}
    """
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
        print(f"[!] Error en API Gemini: {e}")
        return {"calza": False, "motivo": "Fallo en la consulta de IA"}


# =====================================================================
# FUNCIÓN 4: ENVIARTE LA ALERTA A TU GMAIL
# =====================================================================
def enviar_alerta_correo(empleo, motivo):
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
    msg["Subject"] = limpiar_texto(f"MATCH: {empleo['titulo']} en {empleo['empresa']}")
    
    cuerpo = f"""
    Hola Kevin,
    
    Gemini encontro una oferta ideal para ti:
    
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
            
        texto_lowercase = texto_descripcion.lower()
        
        # Filtro estático por código
        if any(palabra in texto_lowercase for palabra in PALABRAS_DESCARTE):
            print(f"[-] Descartado (Codigo): {limpiar_texto(empleo['titulo'])} - Senior o requiere Ingles.")
            guardar_revisado(empleo, False, "Descartado por palabras clave estaticas (Senior o Ingles)")
            continue
            
        # Filtro con Inteligencia Artificial
        print(f"[~] Analizando con Gemini: {limpiar_texto(empleo['titulo'])} en {limpiar_texto(empleo['empresa'])}...")
        veredicto = evaluar_con_gemini(texto_descripcion)
        
        calza = veredicto.get("calza") is True
        motivo = veredicto.get("motivo", "Sin motivo")
        
        # Guardar en revisados
        guardar_revisado(empleo, calza, motivo)
        
        if calza:
            print(f"[+] MATCH ENCONTRADO!: {limpiar_texto(motivo)}")
            # ACTIVADO: Enviará el correo
            enviar_alerta_correo(empleo, motivo) 
        else:
            print(f"[-] Pasando: {limpiar_texto(motivo)}")