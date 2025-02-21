import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from bs4 import BeautifulSoup
import requests
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager

# --- IMPORTS ADICIONALES PARA SELENIUM ---
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time  # para time.sleep (simple) o puedes usar WebDriverWait

# ----------------------------------
# Configuración de MongoDB
# ----------------------------------
MONGO_URI = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
client = MongoClient(MONGO_URI)
DATABASE_NAME = "dolar"

db = client[DATABASE_NAME]

casas_collection = db["casas"]          
historial_collection = db["historial"]  
dolar_peru_collection = db["DolarPeru"]
mercado_cambio = db["mercadocambiario"]

# ----------------------------------
# Zona horaria GMT-5
# ----------------------------------
gmt_minus_5 = timezone(timedelta(hours=-5))

# ----------------------------------
# Funciones de scraping 
# ----------------------------------
def get_paralelo_data(soup):
    try:
        main_div = soup.find("div", class_="QuotacionValue_content__lHRji")
        if not main_div:
            return None, None
        buy_div = main_div.find("div", class_="ValueCurrency_content_buy__Z9pSf")
        sell_div = main_div.find("div", class_="ValueCurrency_content_sale__fdX_P")
        if not buy_div or not sell_div:
            return None, None
        buy_p = buy_div.find("p")
        sell_p = sell_div.find("p")
        if not buy_p or not sell_p:
            return None, None
        return float(buy_p.get_text(strip=True)), float(sell_p.get_text(strip=True))
    except Exception as err:
        print(f"[{datetime.now(gmt_minus_5)}] Error extrayendo Paralelo: {err}")
        return None, None

def get_sunat_data(soup):
    try:
        all_divs = soup.find_all("div", class_="QuotacionValue_content__lHRji")
        if len(all_divs) < 2:
            return None, None
        sunat_div = all_divs[1]
        buy_div = sunat_div.find("div", class_="ValueCurrency_content_buy__Z9pSf")
        sell_div = sunat_div.find("div", class_="ValueCurrency_content_sale__fdX_P")
        if not buy_div or not sell_div:
            return None, None
        buy_p = buy_div.find("p")
        sell_p = sell_div.find("p")
        if not buy_p or not sell_p:
            return None, None
        return float(buy_p.get_text(strip=True)), float(sell_p.get_text(strip=True))
    except Exception as err:
        print(f"[{datetime.now(gmt_minus_5)}] Error extrayendo Sunat: {err}")
        return None, None

def scrape_and_update():
    """
    Scraping de 'cuantoestaeldolar.pe' con requests + BeautifulSoup (HTML estático).
    """
    url = "https://cuantoestaeldolar.pe/"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
    except Exception as e:
        print(f"[{datetime.now(gmt_minus_5)}] Error al obtener la página: {e}")
        return

    soup = BeautifulSoup(response.text, "html.parser")
    
    # Procesar cada casa de cambio encontrada
    casas = soup.find_all("div", class_="ExchangeHouseItem_item__FLx1C")
    print(f"[{datetime.now(gmt_minus_5)}] Se encontraron {len(casas)} casas de cambio.")

    for casa in casas:
        # Extraer nombre
        img = casa.find("img")
        name = img["alt"].strip() if img and img.has_attr("alt") else "Desconocido"
        
        # Extraer precio de compra y venta
        buy_value = None
        sell_value = None
        buy_div = casa.find("div", class_="ValueCurrency_content_buy__Z9pSf")
        if buy_div:
            buy_p = buy_div.find("p")
            if buy_p:
                buy_value = buy_p.get_text(strip=True)
        sell_div = casa.find("div", class_="ValueCurrency_content_sale__fdX_P")
        if sell_div:
            sell_p = sell_div.find("p")
            if sell_p:
                sell_value = sell_p.get_text(strip=True)
        
        if buy_value is None or sell_value is None:
            print(f"[{datetime.now(gmt_minus_5)}] No se pudieron extraer los valores para {name}.")
            continue

        try:
            new_buy = float(buy_value)
            new_sell = float(sell_value)
        except Exception as e:
            print(f"[{datetime.now(gmt_minus_5)}] Error al convertir los valores para {name}: {e}")
            continue

        current_timestamp = datetime.now(gmt_minus_5)
        current_date_str = current_timestamp.strftime("%Y-%m-%d")
        
        # Verificar si ya existe registro
        existing_record = casas_collection.find_one({"name": name})
        if existing_record:
            try:
                old_buy = float(existing_record.get("buy", 0))
                old_sell = float(existing_record.get("sell", 0))
            except Exception as e:
                print(f"[{datetime.now(gmt_minus_5)}] Error al convertir valores antiguos para {name}: {e}")
                old_buy, old_sell = None, None

            if new_buy != old_buy or new_sell != old_sell:
                update_fields = {
                    "buy": new_buy,
                    "sell": new_sell,
                    "last_updated": current_timestamp
                }
                if existing_record.get("date") != current_date_str:
                    update_fields["date"] = current_date_str

                casas_collection.update_one({"_id": existing_record["_id"]}, {"$set": update_fields})
                print(f"[{datetime.now(gmt_minus_5)}] Actualizado {name}: compra {old_buy} -> {new_buy}, venta {old_sell} -> {new_sell}")

                log_entry = {
                    "name": name,
                    "old_buy": old_buy,
                    "new_buy": new_buy,
                    "old_sell": old_sell,
                    "new_sell": new_sell,
                    "timestamp": current_timestamp
                }
                historial_collection.insert_one(log_entry)
            else:
                print(f"[{datetime.now(gmt_minus_5)}] No hubo cambio para {name}.")
        else:
            document = {
                "name": name,
                "buy": new_buy,
                "sell": new_sell,
                "date": current_date_str,
                "last_updated": current_timestamp
            }
            casas_collection.insert_one(document)
            print(f"[{datetime.now(gmt_minus_5)}] Insertado nuevo registro para {name}.")

    # Actualizar opcionalmente los datos de Sunat/Paralelo
    paralelo_buy, paralelo_sell = get_paralelo_data(soup)
    sunat_buy, sunat_sell = get_sunat_data(soup)
    if (paralelo_buy is not None and paralelo_sell is not None and
        sunat_buy is not None and sunat_sell is not None):
        dolar_data = {
            "fecha": datetime.now(gmt_minus_5),
            "sunat": {"compra": sunat_buy, "venta": sunat_sell},
            "paralelo": {"compra": paralelo_buy, "venta": paralelo_sell}
        }
        dolar_peru_collection.update_one({}, {"$set": dolar_data}, upsert=True)
        print(f"[{datetime.now(gmt_minus_5)}] Sunat/Paralelo actualizados en DolarPeru: {dolar_data}")
    else:
        print(f"[{datetime.now(gmt_minus_5)}] No se pudo obtener correctamente Sunat o Paralelo.")


# ------- NUEVA VERSIÓN: Scraping Mercado Cambiario con Selenium Headless -------
def scrape_mercadocambiario():
    """
    Usa Selenium para cargar la página de https://www.mercadocambiario.pe/
    y obtener los spans que React inyecta dinámicamente.
    """
    url = "https://www.mercadocambiario.pe/"
    
    # Configuración de Chrome en modo headless
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")

    # Si usas webdriver_manager (opcional):
    # from webdriver_manager.chrome import ChromeDriverManager
    # driver = webdriver.Chrome(ChromeDriverManager().install(), options=options)

    # Si ya tienes ChromeDriver en PATH:
    driver = webdriver.Chrome(options=options)
    
    try:
        driver.get(url)
        
        # Esperar unos segundos a que React cargue el contenido
        time.sleep(5)
        
        # Obtener HTML renderizado
        page_source = driver.page_source
    except Exception as e:
        print(f"[{datetime.now(gmt_minus_5)}] Error con Selenium: {e}")
        driver.quit()
        return
    
    driver.quit()

    # Ahora sí usamos BeautifulSoup para parsear el HTML final
    soup = BeautifulSoup(page_source, "html.parser")
    
    # Buscar los spans con la clase "MuiTypography-root MuiTypography-body1 amount css-wrqirr"
    spans = soup.find_all("span", class_="MuiTypography-root MuiTypography-body1 amount css-wrqirr")

    if len(spans) < 2:
        print(f"[{datetime.now(gmt_minus_5)}] No se encontraron los valores de compra y venta en MercadoCambiario.pe.")
        return
    
    try:
        demanda = float(spans[0].get_text(strip=True).replace(',', '.'))
        oferta  = float(spans[1].get_text(strip=True).replace(',', '.'))
    except Exception as e:
        print(f"[{datetime.now(gmt_minus_5)}] Error al convertir los valores de compra/venta: {e}")
        return

    current_timestamp = datetime.now(gmt_minus_5)
    document = {
        "name": "Mercado Cambiario",
        "demanda": demanda,
        "oferta": oferta,
        "last_updated": current_timestamp
    }

    # Insertar o actualizar en MongoDB
    existing_record = mercado_cambio.find_one({"name": "Mercado Cambiario"})
    if existing_record:
        mercado_cambio.update_one({"_id": existing_record["_id"]}, {"$set": document})
        print(f"[{datetime.now(gmt_minus_5)}] Actualizado Mercado Cambiario: Compra {demanda}, Venta {oferta}")
    else:
        mercado_cambio.insert_one(document)
        print(f"[{datetime.now(gmt_minus_5)}] Insertado nuevo registro Mercado Cambiario: Compra {demanda}, Venta {oferta}")


# ----------------------------------
# Configurar Scheduler para el scraping
# ----------------------------------

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler()
    # Programa cada scraping con el intervalo que desees:
    scheduler.add_job(scrape_and_update, 'interval', minutes=5)
    scheduler.add_job(scrape_mercadocambiario, 'interval', minutes=5)
    scheduler.start()
    
    # (Opcional) hacer un primer scraping al iniciar
    scrape_and_update()
    scrape_mercadocambiario()
    
    yield
    # Al cerrar la app, detenemos el scheduler
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan, title="Scraping de Casas de Cambio", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------
# Rutas / Endpoints
# ----------------------------------
@app.get("/casas", summary="Obtiene información en vivo de las casas de cambio")
def get_casas():
    try:
        casas = list(casas_collection.find({}, {"_id": 0}))
        return {"casas": casas}
    except Exception as e:
        return {"error": str(e)}

@app.get("/historial", summary="Obtiene el historial de cambios")
def get_historial():
    try:
        historial = list(historial_collection.find({}, {"_id": 0}))
        return {"historial": historial}
    except Exception as e:
        return {"error": str(e)}

@app.get("/dolarperu", summary="Obtiene los datos de Sunat y Paralelo")
def get_dolarperu():
    try:
        data = dolar_peru_collection.find_one({}, {"_id": 0})
        return {"dolar_peru": data}
    except Exception as e:
        return {"error": str(e)}

@app.get("/mercadocambio", summary="Obtiene los datos del mercado cambiario")
def get_mercadocambio():    
    try:
        datos = list(mercado_cambio.find({}, {"_id": 0}))
        return {"mercado_cambio": datos}
    except Exception as e:
        return {"error": str(e)}
