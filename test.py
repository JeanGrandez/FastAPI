from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import time

def scrape_mercadocambiario_headless():
    url = "https://www.mercadocambiario.pe/"
    
    # Configurar el navegador en modo headless (sin interfaz)
    options = Options()
    options.add_argument("--headless")
    # Otras opciones recomendables:
    # options.add_argument("--no-sandbox")
    # options.add_argument("--disable-dev-shm-usage")

    # Iniciar el navegador
    driver = webdriver.Chrome(options=options)
    
    # Navegar a la página
    driver.get(url)
    
    # Esperar unos segundos a que cargue el contenido dinámico
    time.sleep(10)
    
    # Obtener el HTML final (tras ejecutar JS)
    page_source = driver.page_source
    
    # Cerrar el navegador
    driver.quit()
    
    # Parsear con BeautifulSoup
    soup = BeautifulSoup(page_source, "html.parser")
    
    # Luego ya buscas tus spans
    spans = soup.find_all("span", class_="MuiTypography-root MuiTypography-body1 amount css-wrqirr")

    if len(spans) < 2:
        print("No se encontraron los valores de compra y venta.")
        return
    
    demanda = float(spans[0].get_text(strip=True).replace(',', '.'))
    oferta  = float(spans[1].get_text(strip=True).replace(',', '.'))
    print("Compra:", demanda, " - Venta:", oferta)
    
    # Aquí seguirías el mismo proceso de insertar/actualizar en la base de datos
    # ...

# Llamada de prueba
scrape_mercadocambiario_headless()
