import streamlit as st
import asyncio
import os
import subprocess
import sys

# --- AUTO-INSTALACJA ---
try:
    from playwright.async_api import async_playwright
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])

if not os.path.exists("playwright_installed.flag"):
    print("🚑 Instaluję przeglądarkę Chromium...")
    subprocess.run(["playwright", "install", "chromium"])
    with open("playwright_installed.flag", "w") as f: f.write("installed")

from datetime import date, timedelta
import pandas as pd
import plotly.express as px
import io
import re
import random

st.set_page_config(page_title="Autopilot Pro", page_icon="✈️", layout="wide")

# --- CSS ---
st.markdown("""
<style>
    [data-testid="stImage"] img { max-height: 600px; object-fit: cover; border-radius: 15px; }
</style>
""", unsafe_allow_html=True)

# --- FUNKCJE ---

def pobierz_twoje_zdjecia():
    folder = "moje_zdjecia"
    if not os.path.exists(folder): return []
    return [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]

async def parse_element_content(element):
    """Uniwersalna funkcja parsująca dowolny element listy"""
    info = {}
    try:
        full_text = await element.inner_text()
        text_lower = full_text.lower()
        info["text"] = text_lower
        
        # 1. CENA (Szukamy PLN/zł w tekście)
        # Regex łapie: "200 zł", "PLN 200", "2 300 zł"
        prices = re.findall(r'(?:PLN|zł)\s*([\d\s]+)|([\d\s]+)\s*(?:PLN|zł)', full_text, re.IGNORECASE)
        found_price = 0
        for p in prices:
            val_str = p[0] if p[0] else p[1]
            clean_val = re.sub(r'\s+', '', val_str)
            if clean_val.isdigit():
                val = float(clean_val)
                # Odrzucamy liczby, które są rokiem (2024) lub małe (ocena 9)
                if 30 < val < 50000: 
                    found_price = val
                    break
        
        if found_price > 0:
            info["price"] = found_price
        else:
            return None # Bez ceny nie bierzemy

        # 2. DYSTANS
        info["dist_val"] = 0.0
        # Szukamy fraz typu "1,5 km od centrum", "500 m od plaży"
        dist_match = re.search(r'(\d+[.,]?\d*)\s*(km|m)\s', text_lower)
        if dist_match:
            val = float(dist_match.group(1).replace(',', '.'))
            unit = dist_match.group(2)
            if unit == "km": info["dist_val"] = val
            elif unit == "m": info["dist_val"] = val / 1000.0

        # 3. LINK i NAZWA
        # Szukamy linku wewnątrz elementu
        link_el = await element.query_selector('a')
        if link_el:
            href = await link_el.get_attribute("href")
            info["link"] = href.split('?')[0] if href else "#"
            
            # Próba pobrania nazwy z linku lub nagłówka
            heading = await element.query_selector('[data-testid="title"], h3, h4, .sr-hotel__name')
            if heading:
                info["name"] = await heading.inner_text()
            else:
                info["name"] = "Oferta Booking"
        else:
            info["link"] = "#"
            info["name"] = "Oferta bez linku"

        return info
    except:
        return None

async def run_autopilot(address, radius, start_date, end_date, filters, progress_bar, status_text, image_spot, list_placeholder):
    twoje_fotki = pobierz_twoje_zdjecia()
    days = (end_date - start_date).days + 1
    daily_data = []
    unique_competitors = {} 
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="pl-PL"
        )
        page = await context.new_page()

        status_text.info(f"🚀 Analizuję: {address}...")

        for i in range(days):
            progress_bar.progress((i + 1) / days)
            current_date = start_date + timedelta(days=i)
            next_date = current_date + timedelta(days=1)
            s1 = current_date.strftime("%Y-%m-%d")
            s2 = next_date.strftime("%Y-%m-%d")

            status_text.markdown(f"### 📅 Analiza: `{s1}`")

            if twoje_fotki:
                with image_spot.container():
                    fotka = random.choice(twoje_fotki)
                    st.image(fotka, caption=f"Twój Apartament - {s1}", use_container_width=True)

            url = (f"https://www.booking.com/searchresults.pl.html?ss={address}"
                   f"&checkin={s1}&checkout={s2}&group_adults=2&selected_currency=PLN"
                   f"&order=distance_from_search&lang=pl")

            try:
                await page.goto(url, timeout=60000)
                
                # Zamykanie popupów (cicha próba)
                try: await page.click('#onetrust-accept-btn-handler', timeout=2000)
                except: pass
                
                # Przewijanie
                await page.evaluate("window.scrollTo(0, 2000)")
                await page.wait_for_timeout(2000)

                # --- NOWA STRATEGIA: Pobierz wszystkie możliwe kontenery ---
                # Zamiast szukać konkretnych klas, szukamy wszystkich elementów listy wyników
                elements = await page.query_selector_all('[data-testid="property-card"]')
                
                # Jeśli standardowe karty nie działają, szukamy generycznych bloków
                if not elements:
                    elements = await page.query_selector_all('div[role="listitem"]')
                
                # Jeśli nadal nic, szukamy po prostu bloków z ceną (ostateczność)
                if not elements:
                    # To bardzo szeroki selektor, ale w chmurze może być jedynym ratunkiem
                    # Szukamy divów, które mają klasę zawierającą 'item' lub 'card'
                    elements = await page.query_selector_all("div[class*='item'], div[class*='card']")

                valid_prices = []
                
                # Skanujemy max 60 elementów (żeby nie muliło)
                for el in elements[:60]:
                    data = await parse_element_content(el)
                    if data:
                        # Filtr promienia
                        if data["dist_val"] > radius: continue
                        
                        # Filtry udogodnień
                        if filters["parking"] and "parking" not in data["text"]: continue
                        if filters["sniadanie"] and not any(x in data["text"] for x in ["śniadanie", "breakfast", "wliczone"]): continue
                        if filters["klima"] and not any(x in data["text"] for x in ["klimatyzacja", "klimatyzowany", "ac"]): continue
                        
                        valid_prices.append(data["price"])
                        
                        # Zapisujemy do listy konkurencji
                        if data["link"] not in unique_competitors:
                            link = data['link']
                            if link.startswith('http'): full_link = link
                            else: full_link = f"https://www.booking.com{link}"
                                
                            unique_competitors[data["link"]] = {
                                "Nazwa": data["name"],
                                "Link": full_link,
                                "Dystans": f"{data['dist_val']:.2f} km"
                            }

                list_placeholder.caption(f"Znaleziono {len(unique_competitors)} konkurentów...")

                if valid_prices:
                    avg = int(sum(valid_prices) / len(valid_prices))
                    multiplier = 1.15 if current_date.weekday() in [4, 5] else 1.0
                    suggested = int(avg * multiplier)
                    daily_data.append({
                        "Data": s1, "Dzień": current_date.strftime("%A"),
                        "Liczba Ofert": len(valid_prices),
                        "Średnia Rynkowa": avg, "Twoja Cena": suggested
                    })
                else:
                    daily_data.append({"Data": s1, "Dzień": current_date.strftime("%A"), "Liczba Ofert": 0, "Średnia Rynkowa": 0, "Twoja Cena": 0})

            except Exception as e:
                print(f"Błąd przetwarzania: {e}")

        await browser.close()
        competitors_list = list(unique_competitors.values())
        return daily_data, competitors_list

# --- UI START ---
st.title("🎯 Asystent Cenowy")
st.markdown("---")

col1, col2 = st.columns([1, 3])

with col1:
    st.subheader("📍 Ustawienia")
    address = st.text_input("Adres:", "Szeroka 10, Toruń")
    
    # ZMIANA: Zmieniony 'label' wymusi odświeżenie domyślnej wartości na 3.0
    radius = st.number_input("Promień szukania (km):", 0.1, 15.0, 3.0, 0.1)
    
    dates = st.date_input("Zakres dat:", (date.today(), date.today() + timedelta(days=7)))
    
    st.markdown("---")
    f_klima = st.checkbox("❄️ Klimatyzacja")
    f_parking = st.checkbox("🅿️ Parking")
    f_sniadanie = st.checkbox("🥐 Śniadanie")
    
    st.markdown("---")
    file_format = st.radio("Format pliku:", ["Excel (.xlsx)", "Numbers (.csv)"])
    
    st.markdown("---")
    btn = st.button("🚀 URUCHOM ANALIZĘ", type="primary")
    list_placeholder = st.empty()

with col2:
    status = st.empty()
    progress = st.empty()
    img_spot = st.empty()

if btn:
    if len(dates) != 2:
        st.error("Wybierz daty!")
    else:
        filters = {"klima": f_klima, "parking": f_parking, "sniadanie": f_sniadanie}
        progress.progress(0)
        dane_dni, dane_konkurencji = asyncio.run(run_autopilot(address, radius, dates[0], dates[1], filters, progress, status, img_spot, list_placeholder))
        progress.progress(100)
        
        if dane_dni:
            df = pd.DataFrame(dane_dni)
            df_comp = pd.DataFrame(dane_konkurencji)
            status.success("Analiza zakończona!")
            
            st.subheader("Wykres")
            fig = px.line(df, x="Data", y=["Średnia Rynkowa", "Twoja Cena"], markers=True, color_discrete_map={"Średnia Rynkowa": "blue", "Twoja Cena": "red"})
            st.plotly_chart(fig, use_container_width=True)
            
            st.subheader("Tabela")
            st.dataframe(df, use_container_width=True)
            
            if not df_comp.empty:
                st.subheader("Lista Konkurencji")
                st.dataframe(df_comp, column_config={"Link": st.column_config.LinkColumn("Link")}, use_container_width=True)
            
            buffer = io.BytesIO()
            if file_format == "Excel (.xlsx)":
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df.to_excel(writer, index=False, sheet_name='Kalendarz')
                    if not df_comp.empty: df_comp.to_excel(writer, index=False, sheet_name='Konkurencja')
                st.download_button("💾 Pobierz Raport (Excel)", buffer, "RAPORT.xlsx", "application/vnd.ms-excel")
            else:
                c1, c2 = st.columns(2)
                c1.download_button("💾 Kalendarz (CSV)", df.to_csv(index=False, sep=';').encode('utf-8-sig'), "KALENDARZ.csv", "text/csv")
                if not df_comp.empty: c2.download_button("💾 Lista (CSV)", df_comp.to_csv(index=False, sep=';').encode('utf-8-sig'), "LISTA.csv", "text/csv")
        else:
            status.error("Brak danych (lub Booking zablokował połączenie).")
