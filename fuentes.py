"""
Buscadores de ofertas por plataforma.

Cada función buscar_* devuelve una lista de diccionarios con el mismo formato:
    {"fuente", "titulo", "empresa", "ubicacion", "url", "descripcion"}
"descripcion" viene vacía cuando hay que pedirla aparte con obtener_descripcion().
"""
import re
import time
import random
import html
import urllib.parse
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}

# Términos de búsqueda: no solo junior, también cargos generales de desarrollo
TERMINOS = [
    "desarrollador", "developer", "programador", "ingeniero de software",
    "software engineer", "ingeniero informático", "informático",
]


def pausa(minimo=1.5, maximo=3.5):
    time.sleep(random.uniform(minimo, maximo))


def get(url, **kwargs):
    """GET con reintento ante 429 (demasiadas peticiones). Devuelve None si falla."""
    for _ in range(2):
        try:
            res = requests.get(url, headers=HEADERS, timeout=20, **kwargs)
        except requests.RequestException as e:
            print(f"[!] Error de red en {url[:80]}: {e}")
            return None
        if res.status_code == 200:
            return res
        if res.status_code == 429:
            print("[!] Limite de peticiones (429). Reintentando en breve...")
            pausa(4, 8)
            continue
        print(f"[-] Status {res.status_code} en {url[:80]}")
        return None
    return None


def primera_linea(texto):
    return texto.strip().splitlines()[0].strip() if texto and texto.strip() else ""


def html_a_texto(fragmento):
    return BeautifulSoup(fragmento or "", "html.parser").get_text("\n", strip=True)


# =====================================================================
# LINKEDIN (API guest, sin login)
# =====================================================================
LINKEDIN_PAGINAS_CHILE = 10   # 10 ofertas por página -> 100 más recientes
LINKEDIN_PAGINAS_CIUDAD = 2
# Búsquedas extra en ciudades cercanas: en "Chile" las de regiones quedan enterradas bajo Santiago
LINKEDIN_CIUDADES = ["Talca", "Curicó", "Rancagua"]


def _linkedin_busqueda(location, paginas):
    keywords = " OR ".join(f'"{t}"' if " " in t else t for t in TERMINOS)
    empleos = []
    # LinkedIn devuelve 10 ofertas por página, así que se avanza de 10 en 10
    for start in range(0, paginas * 10, 10):
        url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
               f"?keywords={urllib.parse.quote(keywords)}&location={urllib.parse.quote(location)}"
               f"&f_TPR=r604800&sortBy=DD&start={start}")
        res = get(url)
        if not res:
            break
        tarjetas = BeautifulSoup(res.text, "html.parser").find_all("li")
        if not tarjetas:
            break
        for tarjeta in tarjetas:
            link = tarjeta.find("a", class_="base-card__full-link")
            titulo = tarjeta.find("h3", class_="base-search-card__title")
            empresa = tarjeta.find("h4", class_="base-search-card__subtitle")
            if not (link and titulo and empresa):
                continue
            ubicacion = tarjeta.find("span", class_="job-search-card__location")
            empleos.append({
                "fuente": "LinkedIn",
                "titulo": primera_linea(titulo.text),
                "empresa": empresa.text.strip(),
                "ubicacion": ubicacion.text.strip() if ubicacion else "",
                "url": link["href"].split("?")[0],
                "descripcion": "",
            })
        pausa(2, 4)
    return empleos


def buscar_linkedin():
    empleos = _linkedin_busqueda("Chile", LINKEDIN_PAGINAS_CHILE)
    for ciudad in LINKEDIN_CIUDADES:
        empleos += _linkedin_busqueda(f"{ciudad}, Chile", LINKEDIN_PAGINAS_CIUDAD)
    return empleos


def _linkedin_descripcion(url):
    res = get(url)
    if not res:
        return None
    elem = BeautifulSoup(res.text, "html.parser").find("div", class_="show-more-less-html__markup")
    return elem.get_text("\n", strip=True) if elem else None


# =====================================================================
# GET ON BOARD (API pública, solo empleos tech)
# =====================================================================
GETONBOARD_SENIORITY_VALIDA = {1, 2, 3}  # 1 sin experiencia, 2 junior, 3 semi senior (4 senior, 5 expert)
GETONBOARD_MAX_DIAS = 14
GETONBOARD_MODALIDAD = {"fully_remote": "Remoto", "remote_local": "Remoto (solo residentes del país)",
                        "temporarily_remote": "Remoto temporal", "hybrid": "Híbrido", "no_remote": "Presencial"}


def buscar_getonboard():
    empleos = []
    limite = (datetime.now() - timedelta(days=GETONBOARD_MAX_DIAS)).timestamp()
    for termino in TERMINOS:
        res = get("https://www.getonbrd.com/api/v0/search/jobs",
                  params={"query": termino, "per_page": 50, "expand": '["company"]'})
        if not res:
            continue
        for oferta in res.json().get("data", []):
            a = oferta["attributes"]
            seniority = (a.get("seniority") or {}).get("data", {}).get("id")
            if seniority not in GETONBOARD_SENIORITY_VALIDA:
                continue
            if a.get("lang") == "lang_en" or (a.get("published_at") or 0) < limite:
                continue
            paises = a.get("countries") or []
            modalidad = GETONBOARD_MODALIDAD.get(a.get("remote_modality"), a.get("remote_modality") or "")
            # Presencial/híbrido en otro país no sirve; remoto desde cualquier país sí
            if a.get("remote_modality") in ("hybrid", "no_remote") and "Chile" not in paises:
                continue
            empresa = (a.get("company") or {}).get("data", {}).get("attributes", {}).get("name", "")
            secciones = [("", a.get("description")), (a.get("projects_headline") or "Proyecto", a.get("projects")),
                         (a.get("functions_headline") or "Funciones", a.get("functions")),
                         (a.get("desirable_headline") or "Deseable", a.get("desirable")),
                         (a.get("benefits_headline") or "Condiciones", a.get("benefits"))]
            descripcion = "\n\n".join(f"{t}\n{html_a_texto(c)}".strip() for t, c in secciones if c)
            empleos.append({
                "fuente": "Get on Board",
                "titulo": html.unescape(a.get("title", "")),
                "empresa": empresa,
                "ubicacion": f"{modalidad} - {', '.join(paises) or 'sin país'}",
                "url": oferta.get("links", {}).get("public_url", ""),
                "descripcion": f"Modalidad: {modalidad}\nPaíses: {', '.join(paises)}\n\n{descripcion}",
            })
        pausa(0.5, 1.5)
    return empleos


# =====================================================================
# COMPUTRABAJO
# =====================================================================
COMPUTRABAJO_PAGINAS = 2
COMPUTRABAJO_DIAS = 3  # pubdate: ofertas de los últimos N días


def _slug(texto):
    import unicodedata
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", texto.lower()).strip("-")


def buscar_computrabajo():
    empleos = []
    for termino in TERMINOS:
        for pagina in range(1, COMPUTRABAJO_PAGINAS + 1):
            url = f"https://cl.computrabajo.com/trabajo-de-{_slug(termino)}?pubdate={COMPUTRABAJO_DIAS}&p={pagina}"
            res = get(url)
            if not res:
                break
            tarjetas = BeautifulSoup(res.text, "html.parser").select("article.box_offer")
            if not tarjetas:
                break
            for t in tarjetas:
                link = t.select_one("h2 a")
                if not link:
                    continue
                empresa = t.select_one("p a[offer-grid-article-company-url]") or t.select_one("p.fs16 a")
                parrafos = t.select("p.fs16")
                ubicacion = parrafos[1].get_text(" ", strip=True) if len(parrafos) > 1 else ""
                empleos.append({
                    "fuente": "Computrabajo",
                    "titulo": link.get_text(strip=True),
                    "empresa": empresa.get_text(strip=True) if empresa else "Empresa confidencial",
                    "ubicacion": ubicacion,
                    "url": "https://cl.computrabajo.com" + link["href"].split("#")[0],
                    "descripcion": "",
                })
            pausa()
    return empleos


def _computrabajo_descripcion(url):
    res = get(url)
    if not res:
        return None
    elem = BeautifulSoup(res.text, "html.parser").select_one('div[div-link="oferta"]')
    return elem.get_text("\n", strip=True) if elem else None


# =====================================================================
# CHILETRABAJOS
# =====================================================================
def buscar_chiletrabajos():
    empleos = []
    for termino in TERMINOS:
        res = get(f"https://www.chiletrabajos.cl/encuentra-un-empleo?2={urllib.parse.quote_plus(termino)}")
        if not res:
            continue
        for item in BeautifulSoup(res.text, "html.parser").select("div.job-item"):
            link = item.select_one("h2 a")
            meta = item.select_one("h3.meta")
            if not (link and meta):
                continue
            ciudad = meta.find("a")
            empresa = meta.get_text(" ", strip=True)
            if ciudad:
                empresa = empresa.replace(ciudad.get_text(strip=True), "")
            empleos.append({
                "fuente": "Chiletrabajos",
                "titulo": link.get_text(strip=True).rstrip("…").strip(),
                "empresa": empresa.strip(" ,"),
                "ubicacion": ciudad.get_text(strip=True) if ciudad else "",
                "url": link["href"],
                "descripcion": "",
            })
        pausa()
    return empleos


def _chiletrabajos_descripcion(url):
    res = get(url)
    if not res:
        return None
    soup = BeautifulSoup(res.text, "html.parser")
    elem = soup.select_one("div.job-item.no-hover")
    if not elem:
        return None
    # El título en el listado viene truncado; aquí está completo
    return elem.get_text("\n", strip=True)


# =====================================================================
# DESPACHO
# =====================================================================
BUSCADORES = {
    "LinkedIn": buscar_linkedin,
    "Get on Board": buscar_getonboard,
    "Computrabajo": buscar_computrabajo,
    "Chiletrabajos": buscar_chiletrabajos,
}

_DESCRIPCIONES = {
    "LinkedIn": _linkedin_descripcion,
    "Computrabajo": _computrabajo_descripcion,
    "Chiletrabajos": _chiletrabajos_descripcion,
}


def buscar_todas():
    empleos, vistos = [], set()
    for nombre, buscador in BUSCADORES.items():
        print(f"[*] Buscando en {nombre}...")
        try:
            encontrados = buscador()
        except Exception as e:  # una fuente caída no debe botar las demás
            print(f"[!] Fallo la busqueda en {nombre}: {e}")
            continue
        nuevos = [e for e in encontrados if e["url"] and e["url"] not in vistos]
        vistos.update(e["url"] for e in nuevos)
        print(f"[*] {nombre}: {len(nuevos)} ofertas.")
        empleos += nuevos
    return empleos


def obtener_descripcion(empleo):
    if empleo.get("descripcion"):
        return empleo["descripcion"]
    funcion = _DESCRIPCIONES.get(empleo["fuente"])
    return funcion(empleo["url"]) if funcion else None
