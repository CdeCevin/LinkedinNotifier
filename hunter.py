import os
import re
import json
import unicodedata
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
import time
import random
from dotenv import load_dotenv

# Importamos la NUEVA librería oficial de Google
from google import genai
from google.genai import types

from fuentes import buscar_todas, obtener_descripcion, primera_linea

# Cargar variables de entorno del archivo .env
load_dotenv()

# Instanciar el nuevo cliente de Gemini
client = genai.Client()

# Máximo de ofertas evaluadas con Gemini por ejecución, para no agotar la cuota diaria.
# Las que queden fuera no se guardan en revisado.json y se evalúan en la siguiente ejecución.
MAX_EVALUACIONES_GEMINI = 40


# =====================================================================
# FILTROS ESTÁTICOS (antes de gastar llamadas a Gemini)
# =====================================================================
def compilar_patron(palabras):
    return re.compile(r"\b(" + "|".join(re.escape(p) for p in palabras) + r")\b", re.IGNORECASE)

# 1) El título debe ser de un cargo de desarrollo/informática. Las búsquedas amplias
#    ("informático", Computrabajo, Chiletrabajos) traen también ventas, bodega, etc.
PATRON_TITULO_RELEVANTE = re.compile(
    r"desarroll|developer|\bdev\b|programad|programmer|software|inform[aá]tic|full[\s-]?stack|"
    r"back[\s-]?end|front[\s-]?end|\bweb\b|sistemas|computaci|devops|\bqa\b|tester|testing|automatizaci|"
    r"\bdata\b|\bdatos\b|python|java|javascript|typescript|node|react|angular|\.net|php|"
    r"\bti\b|\bit\b|engineer|ingenier[oa] (de |en )?(software|sistemas|desarrollo|computaci|inform|ti\b)|"
    r"aplicaciones|mobile|m[oó]vil|cloud|trainee|builder|salesforce|\bsap\b|abap|odoo|\berp\b|"
    r"business intelligence|\bbi\b|machine learning|\bml\b|\bia\b|\bai\b|integra|"
    r"dev\w*ops|mlops|visualizaci|tableau|power bi",
    re.IGNORECASE,
)

# 2) Palabras que descartan por título. Se buscan como palabras completas (\b).
#    Seniority se revisa SOLO en el título: las descripciones de ofertas junior
#    suelen mencionar "trabajarás con desarrolladores senior" o "tech lead".
PALABRAS_DESCARTE_TITULO = [
    "senior", "sr", "semi senior", "semi-senior", "semisenior", "ssr", "mid", "mid-level", "expert", "experto",
    "lead", "líder", "lider", "architect", "arquitecto", "principal", "staff", "head",
    "jefe", "jefa", "gerente", "subgerente", "manager", "director", "directora", "coordinador", "coordinadora",
    # Prácticas profesionales (solo para estudiantes, no aplica a titulados)
    "práctica", "practica", "prácticas", "practicas", "practicante", "practicantes",
    "pasantía", "pasantia", "pasante", "internship", "intern", "interns",
    "becario", "becaria", "memorista", "alumno en práctica", "estudiante en práctica",
    # Cargos que no son de desarrollo
    "soporte", "mesa de ayuda", "help desk", "helpdesk", "técnico en terreno", "tecnico en terreno",
    "vendedor", "vendedora", "ejecutivo comercial", "ejecutiva comercial", "docente", "profesor", "profesora",
]

# 3) Inglés: se revisa en la descripción completa
PALABRAS_DESCARTE_DESCRIPCION = [
    "bilingue", "bilingüe", "bilingual", "ingles avanzado", "inglés avanzado",
    "ingles fluido", "inglés fluido", "fluent english", "advanced english",
    "english fluency", "native english",
    "ingles conversacional", "inglés conversacional", "conversational english",
    "buen nivel de ingles", "buen nivel de inglés", "upper intermediate", "upper-intermediate",
    "english proficiency", "proficient in english", "strong english",
]

PATRON_TITULO = compilar_patron(PALABRAS_DESCARTE_TITULO)
PATRON_DESCRIPCION = compilar_patron(PALABRAS_DESCARTE_DESCRIPCION)

# Si la oferta está escrita en inglés, se asume que el trabajo es en inglés
PALABRAS_INGLES = {"the", "and", "with", "you", "our", "will", "for", "are", "your", "experience", "team", "we"}
PALABRAS_ESPANOL = {"el", "la", "los", "las", "y", "con", "para", "que", "de", "en", "experiencia", "equipo", "nuestro"}

def oferta_en_ingles(texto):
    palabras = re.findall(r"[a-záéíóúñ]+", texto.lower())
    ingles = sum(p in PALABRAS_INGLES for p in palabras)
    espanol = sum(p in PALABRAS_ESPANOL for p in palabras)
    return ingles > espanol

# 4) Ubicación. El candidato vive en Lontué: puede ir presencial a lo que esté a
#    ~1,5 h de viaje. Santiago solo sirve remoto o híbrido (máx. 2 días), eso lo decide Gemini.
ZONA_LOCAL = [
    "talca", "curicó", "curico", "molina", "lontué", "lontue", "sagrada familia", "teno", "romeral", "rauco",
    "san rafael", "pelarco", "san clemente", "maule", "linares", "san javier", "yerbas buenas",
    "chimbarongo", "san fernando", "placilla", "nancagua", "santa cruz", "rengo", "requínoa", "requinoa",
    "rancagua", "machalí", "machali", "graneros", "o'higgins", "o’higgins", "ohiggins",
]
PATRON_ZONA_LOCAL = compilar_patron(ZONA_LOCAL)
PATRON_REMOTO_O_HIBRIDO = re.compile(
    r"remot|teletrabajo|home\s?office|trabajo a distancia|h[ií]brid|remote|hybrid|work from home|wfh",
    re.IGNORECASE,
)
# Ubicaciones que no dicen nada (o solo el país): no se puede descartar por código
PATRON_UBICACION_GENERICA = re.compile(r"^\s*(chile|cl|remot\w*|remote|sin pa[ií]s|-|)\s*$", re.IGNORECASE)


def descarte_por_ubicacion(empleo, descripcion):
    """Devuelve un motivo si la oferta es claramente presencial fuera de la zona, o None."""
    ubicacion = empleo.get("ubicacion", "")
    if PATRON_ZONA_LOCAL.search(ubicacion):
        return None
    if PATRON_UBICACION_GENERICA.match(ubicacion):
        return None
    if PATRON_REMOTO_O_HIBRIDO.search(f"{empleo['titulo']} {ubicacion} {descripcion}"):
        return None  # Gemini revisa cuántos días presenciales pide
    return f"Presencial fuera de la zona ({ubicacion})"


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", texto.lower()).strip()


def clave_oferta(titulo, empresa):
    """Identifica la misma oferta publicada en varias plataformas."""
    return f"{normalizar(empresa)}|{normalizar(primera_linea(titulo))}"


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
def leer_revisados():
    if os.path.exists(REVISADO_PATH):
        try:
            with open(REVISADO_PATH, "r", encoding="utf-8") as f:
                contenido = f.read().strip()
                if contenido:
                    revisados = json.loads(contenido)
                    if isinstance(revisados, list):
                        return [r for r in revisados if isinstance(r, dict) and "url" in r]
        except Exception as e:
            print(f"[!] Error al leer revisado.json: {e}")
    return []


def cargar_revisados():
    """Devuelve (urls, claves) de las ofertas ya revisadas en ejecuciones anteriores"""
    revisados = leer_revisados()
    urls = {r["url"] for r in revisados}
    claves = {clave_oferta(r.get("titulo", ""), r.get("empresa", "")) for r in revisados}
    return urls, claves


def guardar_revisado(empleo, calza, motivo):
    """Guarda una oferta en el archivo revisado.json para no volver a evaluarla"""
    revisados = leer_revisados()
    if empleo["url"] in {r["url"] for r in revisados}:
        return
    revisados.append({
        "url": empleo["url"],
        "titulo": empleo["titulo"],
        "empresa": empleo["empresa"],
        "fuente": empleo.get("fuente", "LinkedIn"),
        "ubicacion": empleo.get("ubicacion", ""),
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "calza": calza,
        "motivo": motivo,
    })
    try:
        with open(REVISADO_PATH, "w", encoding="utf-8") as f:
            json.dump(revisados, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[!] Error al escribir en revisado.json: {e}")


# =====================================================================
# CONSULTAR A LA API DE GEMINI
# =====================================================================
def evaluar_con_gemini(empleo, descripcion_empleo):
    prompt = f"""
    Actua como un reclutador tecnico experto en TI. Evalua si el candidato del siguiente CV calza con la oferta de empleo.

    REGLAS CRITICAS (DE CUMPLIMIENTO OBLIGATORIO). Si la oferta incumple CUALQUIERA, "cumple_reglas" debe ser false:
    1. UBICACION Y MODALIDAD: El candidato vive en Lontue (Region del Maule) y puede viajar hasta ~1,5 horas.
       - 100% Remota (desde Chile o global): CUMPLE. Si el titulo o la ubicacion de LinkedIn dicen "Remote"/"Remoto", considerala remota salvo que la descripcion diga lo contrario.
       - Presencial o hibrida en la ZONA CERCANA, con cualquier cantidad de dias: CUMPLE. Zona cercana: Talca, Curico, Molina, Lontue, Sagrada Familia, Teno, Romeral, Rauco, San Rafael, Pelarco, San Clemente, Maule, Linares, San Javier, Chimbarongo, San Fernando, Nancagua, Santa Cruz, Rengo, Requinoa, Rancagua, Machali, Graneros.
       - Santiago / Region Metropolitana: CUMPLE solo si es 100% remota, o hibrida con MAXIMO 2 dias presenciales a la semana. Si es presencial, o hibrida con 3 o mas dias presenciales, INCUMPLE. Si es hibrida pero no dice cuantos dias, CUMPLE pero marcala como "cercano" (no "calza") para que el candidato lo confirme.
       - Presencial o hibrida en cualquier otra ciudad (Valparaiso, Vina del Mar, Concepcion, Chillan, Antofagasta, etc.): INCUMPLE.
       - Si la oferta NO indica la modalidad ni la ciudad, NO la rechaces por eso: CUMPLE, pero marcala como "cercano" (no "calza").
    2. INGLES: El candidato solo LEE documentacion tecnica en ingles; no habla ni escribe en ingles de forma profesional. Incumple la regla si la oferta:
       - pide ingles fluido, avanzado, conversacional, intermedio-alto, B2 o superior, o "buen nivel de ingles";
       - requiere usar ingles en el trabajo diario (reuniones, comunicacion escrita, chats, documentacion o colaboracion con equipos o clientes angloparlantes, por ejemplo un equipo en Estados Unidos);
       - tiene entrevistas en ingles, o la descripcion esta escrita en ingles (en ese caso se asume que el trabajo es en ingles).
       Solo cumple si no menciona ingles, o si lo pide solo como "deseable" o para leer documentacion.
    3. NIVEL: El candidato es titulado. Incumple la regla si es una practica profesional, pasantia, internship o cualquier cargo exclusivo para estudiantes. Los programas trainee o para recien titulados SI son validos.
    4. TIPO DE CARGO: Debe ser un cargo de desarrollo o ingenieria de software/informatica (desarrollador, ingeniero de software, analista programador, QA automatizado, DevOps, ingeniero de datos, integraciones, etc.). Incumple si es soporte tecnico, mesa de ayuda, tecnico en terreno, ventas, docencia u otro cargo que no sea de desarrollo.

    CRITERIOS FLEXIBLES (no son motivo de rechazo):
    - TECNOLOGIAS: El candidato aprende rapido y tiene facilidad para Frontend y Fullstack (HTML, CSS, TypeScript, Tailwind, React, Angular, etc.). Si la oferta pide lenguajes o frameworks que no estan en el CV, NO la descartes por eso.
    - EXPERIENCIA: Sus proyectos (Caudal Rio y UAPSCI, en produccion con usuarios reales desde 2025) equivalen a 1 a 1,5 años de experiencia real. Si la oferta pide hasta 2 años, considera que calza. Si pide 3 años, puede ser "cercano". Si pide 4 o mas años, no calza.

    CV del Candidato (JSON):
    {CV_TEXTO_JSON}

    Oferta de Empleo:
    - Titulo: {empleo['titulo']}
    - Empresa: {empleo['empresa']}
    - Plataforma: {empleo.get('fuente', 'LinkedIn')}
    - Ubicacion segun la plataforma: {empleo.get('ubicacion') or 'No indicada'}

    Descripcion:
    {descripcion_empleo}

    Responde ESTRICTAMENTE con un objeto JSON valido con esta estructura, sin textos extras:
    {{
        "cumple_reglas": true o false,
        "calza": true o false,
        "cercano": true o false,
        "motivo": "Explicacion breve de una frase del porque calza, es cercano, o se rechaza"
    }}

    Nota sobre 'cercano': Si cumple las reglas criticas pero no es un match perfecto (falta alguna herramienta, pide algo mas de experiencia o no indica modalidad o dias presenciales), pon "cercano": true para que lo revise manualmente.
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
# ENVIARTE LA ALERTA A TU GMAIL
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
    msg["Subject"] = limpiar_texto(f"{tipo_match} ({empleo.get('fuente', 'LinkedIn')}): {empleo['titulo']} en {empleo['empresa']}")

    cuerpo = f"""
    Hola Kevin,

    Gemini encontro una oferta {'cercana para revisar' if es_cercano else 'ideal para ti'}:

    - Puesto: {empleo['titulo']}
    - Empresa: {empleo['empresa']}
    - Ubicacion: {empleo.get('ubicacion') or 'No indicada'}
    - Plataforma: {empleo.get('fuente', 'LinkedIn')}
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
    ofertas_en_bruto = buscar_todas()
    print(f"[*] Se encontraron {len(ofertas_en_bruto)} ofertas en total.")

    # Cargar ofertas ya revisadas (por URL y por empresa+título, para duplicados entre plataformas)
    urls_revisadas, claves_revisadas = cargar_revisados()
    print(f"[*] Se cargaron {len(urls_revisadas)} ofertas previamente revisadas.")

    evaluaciones = 0
    pendientes = 0
    matches = 0
    for empleo in ofertas_en_bruto:
        nombre = f"[{empleo['fuente']}] {limpiar_texto(empleo['titulo'])} en {limpiar_texto(empleo['empresa'])}"
        clave = clave_oferta(empleo["titulo"], empleo["empresa"])

        # Evitar procesar lo ya revisado, aunque venga de otra plataforma
        if empleo["url"] in urls_revisadas or clave in claves_revisadas:
            continue
        urls_revisadas.add(empleo["url"])
        claves_revisadas.add(clave)

        # Filtros por título (no requieren descargar la descripción)
        if not PATRON_TITULO_RELEVANTE.search(empleo["titulo"]):
            print(f"[-] Descartado (Codigo): {nombre} - no es un cargo de desarrollo.")
            guardar_revisado(empleo, False, "Descartado: el titulo no corresponde a un cargo de desarrollo")
            continue
        coincidencia = PATRON_TITULO.search(empleo["titulo"])
        if coincidencia:
            palabra = coincidencia.group(0)
            print(f"[-] Descartado (Codigo): {nombre} - palabra clave '{limpiar_texto(palabra)}'.")
            guardar_revisado(empleo, False, f"Descartado por palabra clave estatica: '{palabra}'")
            continue

        if evaluaciones >= MAX_EVALUACIONES_GEMINI:
            # Se deja para la próxima ejecución sin descargar nada más
            pendientes += 1
            continue

        texto_descripcion = obtener_descripcion(empleo)
        if not texto_descripcion:
            print(f"[!] No se pudo obtener la descripcion de: {nombre}")
            continue

        # Filtros por descripción
        coincidencia = PATRON_DESCRIPCION.search(texto_descripcion)
        if coincidencia:
            palabra = coincidencia.group(0)
            print(f"[-] Descartado (Codigo): {nombre} - palabra clave '{limpiar_texto(palabra)}'.")
            guardar_revisado(empleo, False, f"Descartado por palabra clave estatica: '{palabra}'")
            continue

        if oferta_en_ingles(texto_descripcion):
            print(f"[-] Descartado (Codigo): {nombre} - oferta escrita en ingles.")
            guardar_revisado(empleo, False, "Descartado: la oferta esta escrita en ingles")
            continue

        motivo_ubicacion = descarte_por_ubicacion(empleo, texto_descripcion)
        if motivo_ubicacion:
            print(f"[-] Descartado (Codigo): {nombre} - {limpiar_texto(motivo_ubicacion)}.")
            guardar_revisado(empleo, False, f"Descartado: {motivo_ubicacion}")
            continue

        # Filtro con Inteligencia Artificial
        print(f"[~] Analizando con Gemini: {nombre}...")
        evaluaciones += 1
        veredicto = evaluar_con_gemini(empleo, texto_descripcion)

        if veredicto.get("error_api"):
            print("[!] API de Gemini no disponible. Se omitira esta oferta para mantenerla en cola.")
            urls_revisadas.discard(empleo["url"])
            continue # No se guarda en revisado.json, por lo que se reintentara en la proxima ejecucion

        # Las reglas criticas mandan: si no se cumplen, no hay match aunque Gemini marque "cercano"
        cumple_reglas = veredicto.get("cumple_reglas") is True
        calza = cumple_reglas and veredicto.get("calza") is True
        cercano = cumple_reglas and veredicto.get("cercano") is True
        motivo = veredicto.get("motivo", "Sin motivo")

        # Guardar en revisados
        guardar_revisado(empleo, calza or cercano, motivo)

        if calza or cercano:
            matches += 1
            nivel = "MATCH ENCONTRADO" if calza else "MATCH CERCANO"
            print(f"[+] {nivel}!: {limpiar_texto(motivo)}")
            enviar_alerta_correo(empleo, motivo, es_cercano=cercano and not calza)
        else:
            print(f"[-] Pasando: {limpiar_texto(motivo)}")

        # Pausa aleatoria antes de la siguiente oferta para evitar bloqueos
        time.sleep(random.uniform(1.5, 3.5))

    print(f"[*] Resumen: {evaluaciones} evaluadas con Gemini, {matches} matches, {pendientes} pendientes para la proxima ejecucion.")
