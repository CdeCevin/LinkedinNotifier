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


def guardar_revisado(empleo, calza, motivo, nivel=None, avisos=None):
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
        "nivel": nivel or ("match" if calza else "descartado"),
        "motivo": motivo,
        **({"avisos": avisos} if avisos else {}),
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
    """Gemini solo EXTRAE datos de la oferta. La decisión la toma decidir() con reglas fijas."""
    prompt = f"""
    Eres un reclutador tecnico experto en TI. Lee la oferta de empleo y EXTRAE datos objetivos.
    No decidas si el candidato calza: solo responde lo que dice la oferta, comparando tecnologias con el CV cuando se pida.

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
        "anos_experiencia_requeridos": numero entero o null,
        "modalidad": "remoto" | "hibrido" | "presencial" | "no_indicada",
        "dias_presenciales": numero entero o null,
        "ciudad": "texto" o null,
        "zona": "cercana" | "santiago" | "otra" | "no_indicada",
        "exige_ingles": true o false,
        "es_practica": true o false,
        "es_cargo_desarrollo": true o false,
        "stack_principal_compatible": true o false,
        "tecnologias_faltantes": ["..."],
        "resumen": "una frase sobre el cargo y por que le podria interesar al candidato"
    }}

    Como llenar cada campo:
    - anos_experiencia_requeridos: el MINIMO de años de experiencia que se exige (si dice "3 a 5 años" es 3; si dice "Senior" sin numero, pon 5). Si no pide experiencia o no lo dice, null.
    - modalidad: "remoto" solo si es 100% remoto (si el titulo o la ubicacion dicen "Remote"/"Remoto" y la descripcion no lo contradice). "hibrido" si combina oficina y casa.
    - dias_presenciales: dias de oficina por semana si es hibrido y lo dice; si no lo dice, null.
    - ciudad: ciudad o comuna de la oficina, si se menciona.
    - zona: "cercana" si la oficina esta en Talca, Curico, Molina, Lontue, Sagrada Familia, Teno, Romeral, Rauco, San Rafael, Pelarco, San Clemente, Maule, Linares, San Javier, Chimbarongo, San Fernando, Nancagua, Santa Cruz, Rengo, Requinoa, Rancagua, Machali o Graneros. "santiago" si esta en Santiago o cualquier comuna de la Region Metropolitana (Las Condes, Providencia, Huechuraba, etc.). "otra" si es otra ciudad. "no_indicada" si no se sabe.
    - exige_ingles: true si pide ingles fluido, avanzado, conversacional, intermedio-alto, B2 o superior, "buen nivel de ingles", entrevistas en ingles, o usar ingles en el trabajo diario (reuniones, comunicacion escrita, equipos o clientes angloparlantes). false si no lo menciona o es solo "deseable" o para leer documentacion.
    - es_practica: true si es practica profesional, pasantia, internship o un cargo exclusivo para estudiantes. Los programas trainee o para recien titulados son false.
    - es_cargo_desarrollo: true si es desarrollo o ingenieria de software/informatica (desarrollador, ingeniero de software, analista programador, QA, DevOps, ingeniero de datos, integraciones, etc.). false si es soporte, mesa de ayuda, tecnico en terreno, ventas, docencia u otro.
    - stack_principal_compatible: compara el lenguaje/plataforma CENTRAL del cargo con el CV. true si el candidato lo maneja o lo esta aprendiendo (campo "en_aprendizaje_actualmente"), o si es un lenguaje/framework muy cercano a lo que maneja. false si el cargo gira en torno a una tecnologia que el candidato no maneja en absoluto (por ejemplo ABAP, Android nativo con Kotlin, iOS con Swift, Golang, Salesforce Apex, Liferay) o si exige una certificacion excluyente que no tiene. Herramientas secundarias (Docker, AWS, Kubernetes, un framework puntual) NO hacen que sea false.
    - tecnologias_faltantes: tecnologias que la oferta pide y no estan en el CV (maximo 5).
    """
    for intento in range(3):
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0,
                )
            )
            datos = json.loads(response.text)
            if isinstance(datos, list) and datos:
                datos = datos[0]
            if isinstance(datos, dict):
                return datos
            raise ValueError(f"Respuesta inesperada: {response.text[:200]}")
        except Exception as e:
            print(f"[!] Error en API Gemini (Intento {intento+1}/3): {e}")
            if intento < 2:
                time.sleep(random.uniform(15, 30)) # Espera larga para evitar limites de cuota (429)

    # Si fallan todos los reintentos, devolvemos un flag de error
    return {"error_api": True}


# =====================================================================
# DECISIÓN DETERMINISTA (reglas fijas sobre los datos que extrajo Gemini)
# =====================================================================
MAX_DIAS_SANTIAGO = 2      # híbrido en Santiago: máximo de días presenciales aceptados
EXPERIENCIA_MATCH = 2      # hasta N años pedidos -> match
EXPERIENCIA_CERCANO = 3    # N años -> cercano; más que esto -> descartado


def _entero(valor):
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return None


def decidir(datos):
    """Devuelve (nivel, motivo, avisos). nivel: "match", "cercano" o "descartado"."""
    avisos = []
    cercano = False

    if datos.get("es_practica") is True:
        return "descartado", "Es una practica profesional o cargo para estudiantes", avisos
    if datos.get("exige_ingles") is True:
        return "descartado", "Exige ingles hablado o de uso diario", avisos
    if datos.get("es_cargo_desarrollo") is False:
        return "descartado", "No es un cargo de desarrollo", avisos
    if datos.get("stack_principal_compatible") is False:
        faltan = ", ".join(datos.get("tecnologias_faltantes") or []) or "stack principal"
        return "descartado", f"El stack principal no es compatible ({faltan})", avisos

    # Experiencia
    anos = _entero(datos.get("anos_experiencia_requeridos"))
    if anos is not None and anos > EXPERIENCIA_CERCANO:
        return "descartado", f"Pide {anos}+ años de experiencia", avisos
    if anos is not None and anos > EXPERIENCIA_MATCH:
        cercano = True
        avisos.append(f"Piden {anos} años de experiencia")

    # Ubicación y modalidad
    modalidad = datos.get("modalidad") or "no_indicada"
    zona = datos.get("zona") or "no_indicada"
    dias = _entero(datos.get("dias_presenciales"))
    ciudad = datos.get("ciudad") or "ciudad no indicada"

    if modalidad == "remoto" or zona == "cercana":
        pass
    elif zona == "santiago":
        if modalidad == "presencial":
            return "descartado", f"Presencial en Santiago ({ciudad})", avisos
        if modalidad == "hibrido":
            if dias is not None and dias > MAX_DIAS_SANTIAGO:
                return "descartado", f"Hibrido con {dias} dias presenciales en Santiago", avisos
            if dias is None:
                avisos.append(f"Hibrido en Santiago ({ciudad}): confirmar dias presenciales (maximo {MAX_DIAS_SANTIAGO})")
        else:
            cercano = True
            avisos.append(f"Oficina en Santiago ({ciudad}) y no indica modalidad: confirmar si es remoto o hibrido")
    elif zona == "otra":
        return "descartado", f"{modalidad.capitalize()} fuera de la zona ({ciudad})", avisos
    else:  # zona no indicada
        if modalidad == "hibrido":
            avisos.append("Hibrido sin ciudad indicada: confirmar ciudad y dias presenciales")
        else:
            cercano = True
            avisos.append("No indica ciudad ni modalidad: confirmar")

    faltan = datos.get("tecnologias_faltantes") or []
    if faltan:
        avisos.append("Tecnologias que no estan en tu CV: " + ", ".join(faltan[:5]))

    return ("cercano" if cercano else "match"), datos.get("resumen") or "Sin resumen", avisos


# =====================================================================
# ENVIARTE LA ALERTA A TU GMAIL
# =====================================================================
def enviar_alerta_correo(empleo, motivo, es_cercano=False, avisos=None):
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

    texto_avisos = "\n".join(f"    ! {a}" for a in (avisos or [])) or "    (ninguno)"
    cuerpo = f"""
    Hola Kevin,

    Gemini encontro una oferta {'cercana para revisar' if es_cercano else 'ideal para ti'}:

    - Puesto: {empleo['titulo']}
    - Empresa: {empleo['empresa']}
    - Ubicacion: {empleo.get('ubicacion') or 'No indicada'}
    - Plataforma: {empleo.get('fuente', 'LinkedIn')}
    - Resumen: {motivo}
    - Link directo: {empleo['url']}

    Para revisar antes de postular:
{texto_avisos}

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

        nivel, motivo, avisos = decidir(veredicto)
        es_match = nivel in ("match", "cercano")
        guardar_revisado(empleo, es_match, motivo, nivel, avisos)

        if es_match:
            matches += 1
            print(f"[+] {nivel.upper()}!: {limpiar_texto(motivo)}")
            for aviso in avisos:
                print(f"    ! {limpiar_texto(aviso)}")
            enviar_alerta_correo(empleo, motivo, es_cercano=(nivel == "cercano"), avisos=avisos)
        else:
            print(f"[-] Descartado (Gemini): {limpiar_texto(motivo)}")

        # Pausa aleatoria antes de la siguiente oferta para evitar bloqueos
        time.sleep(random.uniform(1.5, 3.5))

    print(f"[*] Resumen: {evaluaciones} evaluadas con Gemini, {matches} matches, {pendientes} pendientes para la proxima ejecucion.")
