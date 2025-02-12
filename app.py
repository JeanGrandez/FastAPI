import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from bs4 import BeautifulSoup
import requests
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager

# ----------------------------------
# Configuración de MongoDB
# ----------------------------------
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DATABASE_NAME = "dolar"

client = MongoClient(MONGO_URI)
db = client[DATABASE_NAME]

casas_collection = db["casas"]         # Datos en vivo de casas de cambio
historial_collection = db["historial"]   # Historial de cambios
dolar_peru_collection = db["DolarPeru"]    # Datos SUNAT/Paralelo

# ----------------------------------
# Funciones de scraping (simplificadas)
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
        print(f"[{datetime.now()}] Error extrayendo Paralelo: {err}")
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
        print(f"[{datetime.now()}] Error extrayendo Sunat: {err}")
        return None, None

def scrape_and_update():
    url = "https://cuantoestaeldolar.pe/"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"[{datetime.now()}] Error al obtener la página: {e}")
        return

    soup = BeautifulSoup(response.text, "html.parser")
    
    # Procesar cada casa de cambio encontrada
    casas = soup.find_all("div", class_="ExchangeHouseItem_item__FLx1C")
    print(f"[{datetime.now()}] Se encontraron {len(casas)} casas de cambio.")

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
            print(f"[{datetime.now()}] No se pudieron extraer los valores para {name}.")
            continue

        try:
            new_buy = float(buy_value)
            new_sell = float(sell_value)
        except Exception as e:
            print(f"[{datetime.now()}] Error al convertir los valores para {name}: {e}")
            continue

        current_timestamp = datetime.now()
        current_date_str = current_timestamp.strftime("%Y-%m-%d")
        
        # Verificar si ya existe registro
        existing_record = casas_collection.find_one({"name": name})
        if existing_record:
            try:
                old_buy = float(existing_record.get("buy", 0))
                old_sell = float(existing_record.get("sell", 0))
            except Exception as e:
                print(f"[{datetime.now()}] Error al convertir valores antiguos para {name}: {e}")
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
                print(f"[{datetime.now()}] Actualizado {name}: compra {old_buy} -> {new_buy}, venta {old_sell} -> {new_sell}")

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
                print(f"[{datetime.now()}] No hubo cambio para {name}.")
        else:
            document = {
                "name": name,
                "buy": new_buy,
                "sell": new_sell,
                "date": current_date_str,
                "last_updated": current_timestamp
            }
            casas_collection.insert_one(document)
            print(f"[{datetime.now()}] Insertado nuevo registro para {name}.")

    # Actualizar opcionalmente los datos de Sunat/Paralelo
    paralelo_buy, paralelo_sell = get_paralelo_data(soup)
    sunat_buy, sunat_sell = get_sunat_data(soup)
    if (paralelo_buy is not None and paralelo_sell is not None and
        sunat_buy is not None and sunat_sell is not None):
        dolar_data = {
            "fecha": datetime.now(),
            "sunat": {"compra": sunat_buy, "venta": sunat_sell},
            "paralelo": {"compra": paralelo_buy, "venta": paralelo_sell}
        }
        dolar_peru_collection.update_one({}, {"$set": dolar_data}, upsert=True)
        print(f"[{datetime.now()}] Sunat/Paralelo actualizados en DolarPeru: {dolar_data}")
    else:
        print(f"[{datetime.now()}] No se pudo obtener correctamente Sunat o Paralelo.")

# ----------------------------------
# Configurar Scheduler para el scraping
# ----------------------------------

# ----------------------------------
# Definir lifespan event handler
# ----------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):

    scheduler = BackgroundScheduler()
    scheduler.add_job(scrape_and_update, 'interval', minutes=5)
    scheduler.start()
    
    # Código de startup: se puede ejecutar un scraping inicial
    scrape_and_update()
    yield
    # Código de shutdown: cerrar el scheduler
    scheduler.shutdown()

# Crear la aplicación usando el lifespan handler
app = FastAPI(lifespan=lifespan, title="Scraping de Casas de Cambio", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------
# Endpoint para exponer la información de "casas"
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