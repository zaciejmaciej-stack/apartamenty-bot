import streamlit as st
import asyncio
# --- AUTO-INSTALACJA PRZEGLĄDARKI (Fix dla Chmury) ---
import os
import subprocess
import sys

# Sprawdzamy, czy przeglądarka jest zainstalowana, jeśli nie - instalujemy ją
# To kluczowy fragment, który naprawia błąd w Streamlit Cloud
try:
    from playwright.async_api import async_playwright
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])

# Wymuszamy instalację silnika Chromium przy każdym starcie w chmurze
if not os.path.exists("playwright_installed.flag"):
    print("🚑 Instaluję przeglądarkę Chromium dla bota...")
    subprocess.run(["playwright", "install", "chromium"])
    # Tworzymy pusty plik, żeby nie instalować przy każdym odświeżeniu strony, tylko przy restarcie serwera
    with open("playwright_installed.flag", "w") as f:
        f.write("installed")

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
    [data-testid="stImage"] img {
        max-height: 600px;
        object-fit: cover; 
        border-radius: 15px;
    }
</style>
""", unsafe_allow_html=True)

# --- FUNKCJE ---

def pobierz_twoje_zdjecia():
    folder = "moje_zdjecia"
    if not os.path.exists(folder): return []
    return [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]

async def parse_card_content(card):
    info = {}
    try:
        full_text = await card.inner_text()
        info["text"] = full_text.lower()
        
        price_el = await card.query_selector('[data-testid="price-and-discounted-price"]')
        if price_el:
            price_txt = await price_el.inner_text()
            info["price"] = float(re.sub(r'[^\d]', '', price_txt))
        else:
            return None 
            
        title_el = await card.query_selector('[data-testid="title"]')
        info["name"] = await title_el.inner_text() if title_el else "Obiekt"
        
        link_el = await card.query_selector('a[data-testid="title-link"]')
        info["link"] = link_el.get_attribute("href").split('?')[0] if link_el else "#"
        
        distance_el = await card.query_selector('[data-testid="distance"]')
        info["dist_val"] = 0.0
        if distance_el:
            dist_txt = await distance_el.inner_text()
            nums = re.findall(r"(\d+[.,]?\d*)", dist_txt)
            if nums:
                val = float(nums[0].replace(',', '.'))
                if "km" in dist_txt: info["dist_val"] = val
                elif "m" in dist_txt: info["dist_val"] = val / 1000.0

        return info
    except:
        return None

async def run_autopilot(address, radius, start_date, end_date, filters, progress_bar, status_text, image_spot, list_placeholder):
    twoje_fotki = pobierz_twoje_zdjecia()
    days = (end_date - start_date).days + 1
    daily_data = []
    unique_competitors = {} 
    
    async with async_playwright() as p:
        # --- KONFIGURACJA DLA CHMURY ---
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        status_text.info(f"🚀 Analizuję adres: {address}...")

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
                   f"&order=distance_from_search")

            try:
                await page.goto(url, timeout=60000)
                
                try: await page.click('#onetrust-accept-btn-handler', timeout=2000)
                except: pass

                await page.evaluate("window.scrollTo(0, 2000)")
                await page.wait_for_timeout(2000)

                cards = await page.query_selector_all('[data-testid="property-card"]')
                
                valid_prices = []
                
                for c in cards[:40]:
                    data = await parse_card_content(c)
                    if data:
                        if data["dist_val"] > radius: continue
                        if filters["parking"] and "parking" not in data["text"]: continue
                        if filters["sniadanie"] and not any(x in data["text"] for x in ["śniadanie", "breakfast", "wliczone"]): continue
                        if filters["klima"] and not any(x in data["text"] for x in ["klimatyzacja", "klimatyzowany", "ac"]): continue
                        
                        valid_prices.append(data["price"])
                        
                        if data["link"] not in unique_competitors:
                            unique_competitors[data["link"]] = {
                                "Nazwa": data["name"],
                                "Link": f"https://www.booking.com{data['link']}" if not data['link'].startswith('http') else data['link'],
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
                print(f"Błąd: {e}")

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
    radius = st.number_input("Promień (km):", 0.1, 5.0, 0.5, 0.1)
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
            status.success("Gotowe!")
            
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
            status.error("Brak danych.")
