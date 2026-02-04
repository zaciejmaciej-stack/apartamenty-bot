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

async def parse_card_content(card):
    info = {}
    try:
        full_text = await card.inner_text()
        info["text"] = full_text.lower()
        
        # --- PANCERNE SZUKANIE CENY ---
        price_val = None
        
        # Metoda 1: Standardowy selektor
        price_el = await card.query_selector('[data-testid="price-and-discounted-price"]')
        if price_el:
            price_txt = await price_el.inner_text()
            # Czyścimy wszystko co nie jest cyfrą
            price_val = float(re.sub(r'[^\d]', '', price_txt))
        
        # Metoda 2: Jeśli Metoda 1 zawiodła, szukamy w całym tekście karty
        if not price_val:
            # Szukamy wzorców: "200 zł", "PLN 200", "200 PLN"
            # Ignorujemy spacje w liczbach (np. 1 200)
            matches = re.findall(r'(?:PLN|zł)\s*([\d\s]+)|([\d\s]+)\s*(?:PLN|zł)', full_text, re.IGNORECASE)
            for m in matches:
                # m to krotka np. ('', '1 200') lub ('200', '')
                txt_val = m[0] if m[0] else m[1]
                # Usuwamy spacje i sprawdzamy czy to sensowna liczba
                clean_val = re.sub(r'\s+', '', txt_val)
                if clean_val.isdigit():
                    val = float(clean_val)
                    if val > 10: # Ignorujemy małe liczby (np. ocena 9.0, dystans 2.5)
                        price_val = val
                        break

        if price_val:
            info["price"] = price_val
        else:
            return None # Bez ceny oferta jest bezużyteczna
            
        # Nazwa
        title_el = await card.query_selector('[data-testid="title"]')
        if not title_el: title_el = await card.query_selector('h3') 
        info["name"] = await title_el.inner_text() if title_el else "Obiekt"
        
        # Link
        link_el = await card.query_selector('a[data-testid="title-link"]')
        if not link_el: link_el = await card.query_selector('a')
        
        if link_el:
            href = await link_el.get_attribute("href")
            info["link"] = href.split('?')[0] if href else "#"
        else:
            info["link"] = "#"
        
        # Dystans
        info["dist_val"] = 0.0
        distance_el = await card.query_selector('[data-testid="distance"]')
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
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", 
                "--disable-dev-shm-usage", 
                "--disable-blink-features=AutomationControlled"
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="pl-PL"
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
                   f"&order=distance_from_search&lang=pl")

            try:
                await page.goto(url, timeout=90000) # Wydłużony timeout
                
                # Zamykanie popupów
                try: 
                    # Szukamy przycisków zawierających słowa kluczowe
                    await page.click('button:has-text("Akceptuj")', timeout=2000)
                    await page.click('button:has-text("Accept")', timeout=500)
                except: pass
                
                # KLUCZOWE: Czekamy na "ciszę w sieci" (aż strona przestanie ładować dane)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except: pass # Jeśli timeout, idziemy dalej

                await page.evaluate("window.scrollTo(0, 2000)")
                await page.wait_for_timeout(2000)

                # --- STRATEGIA ZBIERANIA KART (Potrójna) ---
                cards = []
                # 1. Standardowy ID
                cards = await page.query_selector_all('[data-testid="property-card"]')
                
                # 2. Jeśli pusto -> szukaj po roli
                if not cards:
                    cards = await page.query_selector_all('div[role="listitem"]')
                
                # 3. Jeśli nadal pusto -> szukaj kontenerów z cenami
                if not cards:
                    # Szukamy elementów, które mają w sobie cenę, i bierzemy ich rodzica (kartę)
                    # To jest ryzykowne, ale tonący brzytwy się chwyta
                    cards = await page.query_selector_all('.sr_item') # Stary selektor

                # DIAGNOSTYKA BŁĘDU (Zrzut ekranu jeśli 0 kart)
                if not cards:
                    status_text.warning(f"⚠️ Dzień {s1}: Nadal 0 kart. Robię zdjęcie do weryfikacji.")
                    await page.screenshot(path="debug_error.png")
                    with image_spot.container():
                        st.image("debug_error.png", caption="Błąd: Brak widocznych ofert", use_container_width=True)
                
                valid_prices = []
                
                # Analizujemy znalezione karty
                for c in cards[:50]: # Zwiększyłem limit do 50
                    data = await parse_card_content(c)
                    if data:
                        # Filtr dystansu
                        if data["dist_val"] > radius: continue
                        
                        # Filtry tekstowe
                        if filters["parking"] and "parking" not in data["text"]: continue
                        if filters["sniadanie"] and not any(x in data["text"] for x in ["śniadanie", "breakfast", "wliczone"]): continue
                        if filters["klima"] and not any(x in data["text"] for x in ["klimatyzacja", "klimatyzowany", "ac"]): continue
                        
                        valid_prices.append(data["price"])
                        
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
    # ZMIANA: Domyślna wartość 3.0
    radius = st.number_input("Promień (km):", 0.1, 10.0, 3.0, 0.1)
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
