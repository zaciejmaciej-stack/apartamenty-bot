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

st.set_page_config(page_title="Autopilot Szpieg", page_icon="🕵️", layout="wide")

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

async def scrape_stealth(page, radius, filters):
    results = []
    
    # Próba znalezienia czegokolwiek z ceną
    price_elements = await page.query_selector_all(':text-matches("PLN|zł")')
    
    seen_links = set()

    for el in price_elements:
        try:
            # Szukamy rodzica elementu ceny
            card = await el.evaluate_handle('el => el.closest("div[data-testid=\'property-card\']") || el.closest("div[role=\'listitem\']") || el.parentElement.parentElement.parentElement')
            if not card: continue
            
            full_text = await card.inner_text()
            text_lower = full_text.lower()
            
            # Cena
            price_val = 0.0
            matches = re.findall(r'(?:PLN|zł)\s*([\d\s]+)|([\d\s]+)\s*(?:PLN|zł)', full_text, re.IGNORECASE)
            for m in matches:
                val_str = m[0] if m[0] else m[1]
                clean = re.sub(r'\s+', '', val_str)
                if clean.isdigit():
                    v = float(clean)
                    if v > 30: price_val = v; break
            
            if price_val == 0: continue

            # Link
            link_el = await card.query_selector('a')
            link = "#"
            if link_el:
                href = await link_el.get_attribute("href")
                if href: link = href.split('?')[0]
            
            if link in seen_links: continue
            seen_links.add(link)
            
            # Nazwa
            name = "Oferta"
            title_el = await card.query_selector('[data-testid="title"], h3')
            if title_el: name = await title_el.inner_text()

            # Dystans
            dist_val = 999.0
            dist_match = re.search(r'(\d+[.,]?\d*)\s*(km|m)\s', text_lower)
            if dist_match:
                d_val = float(dist_match.group(1).replace(',', '.'))
                unit = dist_match.group(2)
                if unit == "km": dist_val = d_val
                elif unit == "m": dist_val = d_val / 1000.0

            # Filtry
            if dist_val != 999.0 and dist_val > radius: continue
            if filters["parking"] and "parking" not in text_lower: continue
            if filters["sniadanie"] and not any(x in text_lower for x in ["śniadanie", "breakfast"]): continue
            if filters["klima"] and not any(x in text_lower for x in ["klimatyzacja", "ac", "klimatyzowany"]): continue

            if link.startswith('http'): full_link = link
            else: full_link = f"https://www.booking.com{link}"

            results.append({
                "name": name,
                "price": price_val,
                "dist": dist_val,
                "link": full_link
            })
        except: continue
        
    return results

async def run_autopilot(address, radius, start_date, end_date, filters, progress_bar, status_text, image_spot, list_placeholder, debug_area):
    twoje_fotki = pobierz_twoje_zdjecia()
    days = (end_date - start_date).days + 1
    daily_data = []
    
    async with async_playwright() as p:
        # --- MASKOWANIE POZIOM EKSPERT ---
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", 
                "--disable-blink-features=AutomationControlled", # Ukrywa flagę robota
                "--disable-infobars",
                "--window-size=1920,1080"
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="pl-PL"
        )
        
        # SKRYPT JS: Usuwa ślady Playwrighta ze strony
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        page = await context.new_page()

        status_text.info(f"🕵️ Rozpoczynam misję dla: {address}")

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

            # URL
            url = (f"https://www.booking.com/searchresults.pl.html?ss={address}"
                   f"&checkin={s1}&checkout={s2}&group_adults=2&selected_currency=PLN"
                   f"&order=distance_from_search&lang=pl")

            try:
                await page.goto(url, timeout=90000)
                
                # Zamykanie popupów
                try: await page.click('#onetrust-accept-btn-handler', timeout=3000)
                except: pass

                # Przewijanie
                await page.evaluate("window.scrollTo(0, 1000)")
                await page.wait_for_timeout(2000)
                
                # --- POBIERANIE ---
                offers = await scrape_stealth(page, radius, filters)
                
                # --- DIAGNOSTYKA TEKSTOWA ---
                # Jeśli 0 wyników, pokażemy użytkownikowi kawałek tekstu strony
                if not offers:
                    body_text = await page.inner_text('body')
                    # Czyścimy tekst z pustych linii
                    clean_text = "\n".join([line for line in body_text.split('\n') if line.strip()][:20])
                    debug_area.error(f"⚠️ Dzień {s1}: Brak ofert. Oto co widzi bot na początku strony:")
                    debug_area.code(clean_text)

                valid_prices = [o["price"] for o in offers]

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
        return daily_data

# --- UI START ---
st.title("🕵️ Asystent Szpieg")
st.markdown("---")

col1, col2 = st.columns([1, 3])

with col1:
    st.subheader("📍 Ustawienia")
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
    btn = st.button("🚀 URUCHOM ANALIZĘ", type="primary")
    
    st.markdown("---")
    debug_area = st.empty() # Miejsce na komunikaty błędu

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
        
        # Czyścimy debug area
        debug_area.empty()
        
        dane_dni = asyncio.run(run_autopilot(address, radius, dates[0], dates[1], filters, progress, status, img_spot, st.empty(), debug_area))
        progress.progress(100)
        
        if dane_dni:
            df = pd.DataFrame(dane_dni)
            status.success("Gotowe!")
            
            st.subheader("Wykres")
            fig = px.line(df, x="Data", y=["Średnia Rynkowa", "Twoja Cena"], markers=True, color_discrete_map={"Średnia Rynkowa": "blue", "Twoja Cena": "red"})
            st.plotly_chart(fig, use_container_width=True)
            
            st.subheader("Tabela Cen")
            st.dataframe(df, use_container_width=True)
            
            buffer = io.BytesIO()
            if file_format == "Excel (.xlsx)":
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df.to_excel(writer, index=False)
                st.download_button("💾 Pobierz Excel", buffer, "RAPORT.xlsx", "application/vnd.ms-excel")
            else:
                st.download_button("💾 Pobierz CSV", df.to_csv(index=False, sep=';').encode('utf-8-sig'), "RAPORT.csv", "text/csv")
