import streamlit as st
import asyncio
import os
import subprocess
import sys
import random

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

st.set_page_config(page_title="Analizator Rynku", page_icon="📊", layout="wide")

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

async def scrape_brutal(page):
    """
    Metoda Brutalna: Pobiera surowy tekst i wyciąga ceny oraz nazwy.
    """
    results = []
    
    # Pobieramy bloki z ceną
    elements = await page.query_selector_all('div:has-text("zł"), div:has-text("PLN")')
    
    potential_cards = []
    for el in elements:
        try:
            text = await el.inner_text()
            # Filtr długości, żeby nie łapać śmieci
            if len(text) < 600 and len(text) > 15:
                potential_cards.append(text)
        except: pass
        
    unique_texts = list(set(potential_cards))
    
    for text in unique_texts:
        text_lower = text.lower()
        
        # 1. CENA
        price_val = 0.0
        matches = re.findall(r'(?:PLN|zł)\s*([\d\s]+)|([\d\s]+)\s*(?:PLN|zł)', text, re.IGNORECASE)
        for m in matches:
            val_str = m[0] if m[0] else m[1]
            clean = re.sub(r'\s+', '', val_str)
            if clean.isdigit():
                v = float(clean)
                if v > 40: price_val = v; break
        
        if price_val == 0: continue

        # 2. DYSTANS
        dist_val = 0.0
        dist_match = re.search(r'(\d+[.,]?\d*)\s*(km|m)\s', text_lower)
        if dist_match:
            d_val = float(dist_match.group(1).replace(',', '.'))
            unit = dist_match.group(2)
            if unit == "km": dist_val = d_val
            elif unit == "m": dist_val = d_val / 1000.0
            
        # 3. NAZWA (Pierwsza linia tekstu, czyszczona)
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        name = "Oferta Booking"
        for line in lines:
            # Szukamy linii, która nie jest ceną, oceną ani dystansem
            if len(line) > 3 and not re.search(r'\d', line) and "zł" not in line:
                name = line
                break
        
        if len(name) > 60: name = name[:60] + "..."

        # 4. LINK (Symulowany)
        full_link = f"https://www.booking.com/searchresults.pl.html?ss={name}"

        # 5. UDOGODNIENIA
        ac = any(x in text_lower for x in ["klimatyzacja", "ac", "klimatyzowany"])
        park = "parking" in text_lower
        bfast = any(x in text_lower for x in ["śniadanie", "breakfast", "wliczone"])

        results.append({
            "name": name,
            "price": price_val,
            "dist": dist_val,
            "link": full_link, 
            "ac": ac, "parking": park, "breakfast": bfast
        })

    return results

async def run_autopilot(address, radius, start_date, end_date, filters, progress_bar, status_text, image_spot, list_placeholder):
    twoje_fotki = pobierz_twoje_zdjecia()
    days = (end_date - start_date).days + 1
    daily_data = []
    all_found_offers = [] # Lista wszystkich hoteli do tabeli
    
    async with async_playwright() as p:
        iphone = p.devices['iPhone 13']
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(**iphone, locale="pl-PL")
        page = await context.new_page()

        status_text.info(f"📊 Analizuję rynek: {address}")

        for i in range(days):
            progress_bar.progress((i + 1) / days)
            current_date = start_date + timedelta(days=i)
            s1 = current_date.strftime("%Y-%m-%d")
            # Booking szuka na 1 noc
            next_date = current_date + timedelta(days=1)
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
                try: await page.click('button', timeout=1000)
                except: pass

                await page.evaluate("window.scrollTo(0, 4000)")
                await page.wait_for_timeout(2000)
                
                offers = await scrape_brutal(page)
                
                valid_prices = []
                day_offers_list = []

                for o in offers:
                    # Filtrowanie
                    if o["dist"] > 0 and o["dist"] > radius: continue
                    if filters["parking"] and not o["parking"]: continue
                    if filters["sniadanie"] and not o["breakfast"]: continue
                    if filters["klima"] and not o["ac"]: continue
                    
                    valid_prices.append(o["price"])
                    
                    # Zapisujemy do listy szczegółowej
                    day_offers_list.append({
                        "Data": s1,
                        "Nazwa": o["name"],
                        "Cena": o["price"],
                        "Odległość": f"{o['dist']:.2f} km" if o['dist'] > 0 else "-",
                        "Link (Szukaj)": o["link"]
                    })

                # Dodajemy oferty z tego dnia do głównej listy
                all_found_offers.extend(day_offers_list)

                count = len(valid_prices)
                list_placeholder.caption(f"Znaleziono {count} ofert.")

                if valid_prices:
                    avg = int(sum(valid_prices) / count)
                    daily_data.append({
                        "Data": s1, 
                        "Dzień": current_date.strftime("%A"),
                        "Liczba Ofert": count, 
                        "Średnia Rynkowa": avg
                    })
                else:
                    daily_data.append({
                        "Data": s1, "Dzień": current_date.strftime("%A"), 
                        "Liczba Ofert": 0, "Średnia Rynkowa": 0
                    })

            except Exception as e:
                print(f"Błąd: {e}")

        await browser.close()
        return daily_data, all_found_offers

# --- UI START ---
st.title("📊 Analizator Rynku")
st.markdown("---")

col1, col2 = st.columns([1, 3])

with col1:
    st.subheader("📍 Parametry")
    address = st.text_input("Adres:", "Szeroka 10, Toruń")
    radius = st.number_input("Promień (km):", 0.1, 15.0, 3.0, 0.1)
    dates = st.date_input("Zakres dat:", (date.today(), date.today() + timedelta(days=7)))
    
    st.markdown("---")
    f_klima = st.checkbox("❄️ Klimatyzacja")
    f_parking = st.checkbox("🅿️ Parking")
    f_sniadanie = st.checkbox("🥐 Śniadanie")
    
    st.markdown("---")
    file_format = st.radio("Format pliku:", ["Excel (.xlsx)", "Numbers (.csv)"])
    
    st.markdown("---")
    btn = st.button("🚀 URUCHOM", type="primary")
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
        
        dane_dni, lista_ofert = asyncio.run(run_autopilot(address, radius, dates[0], dates[1], filters, progress, status, img_spot, list_placeholder))
        progress.progress(100)
        
        if dane_dni:
            df = pd.DataFrame(dane_dni)
            status.success("Zakończono!")
            
            # --- 1. WYKRES (Poprawiony: Oś Y od zera) ---
            st.subheader("📈 Średnia Cena Rynkowa")
            if not df["Średnia Rynkowa"].eq(0).all():
                # Ustalamy zakres osi Y od 0 do max_ceny + margines
                max_val = df["Średnia Rynkowa"].max()
                fig = px.bar(df, x="Data", y="Średnia Rynkowa", text="Średnia Rynkowa")
                fig.update_traces(textposition='outside')
                fig.update_layout(yaxis=dict(range=[0, max_val * 1.2])) # Oś Y od 0
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("Brak danych cenowych do wykresu.")
            
            # --- 2. TABELA PODSUMOWUJĄCA ---
            st.subheader("📅 Podsumowanie Dnia")
            st.dataframe(df, use_container_width=True)
            
            # --- 3. LISTA KONKURENCJI (Nowość!) ---
            if lista_ofert:
                st.markdown("---")
                st.subheader("🏨 Lista Znalezionych Ofert (Konkurencja)")
                st.caption("Oto hotele, które zostały wliczone do średniej:")
                df_detale = pd.DataFrame(lista_ofert)
                st.dataframe(
                    df_detale,
                    column_config={
                        "Link (Szukaj)": st.column_config.LinkColumn("Link"),
                        "Cena": st.column_config.NumberColumn("Cena", format="%d zł")
                    },
                    use_container_width=True
                )
            
            # --- POBIERANIE ---
            buffer = io.BytesIO()
            if file_format == "Excel (.xlsx)":
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df.to_excel(writer, index=False, sheet_name='Srednie_Ceny')
                    if lista_ofert: pd.DataFrame(lista_ofert).to_excel(writer, index=False, sheet_name='Lista_Ofert')
                st.download_button("💾 Pobierz Excel", buffer, "RAPORT.xlsx", "application/vnd.ms-excel")
            else:
                # CSV - pakujemy tylko listę ofert, bo jest cenniejsza
                if lista_ofert:
                    st.download_button("💾 Pobierz Listę Ofert (CSV)", pd.DataFrame(lista_ofert).to_csv(index=False, sep=';').encode('utf-8-sig'), "LISTA_OFERT.csv", "text/csv")
                else:
                    st.download_button("💾 Pobierz Srednie (CSV)", df.to_csv(index=False, sep=';').encode('utf-8-sig'), "RAPORT.csv", "text/csv")
