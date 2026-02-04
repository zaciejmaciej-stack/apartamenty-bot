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

st.set_page_config(page_title="Autopilot Brutalny", page_icon="🦖", layout="wide")

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
    Metoda Brutalna: Pobiera surowy tekst ze wszystkich elementów div
    i szuka wzorców cenowych, ignorując strukturę HTML.
    """
    results = []
    
    # Pobieramy wszystkie bloki tekstu, które mogą być ofertami
    # Szukamy elementów, które zawierają "zł" lub "PLN"
    elements = await page.query_selector_all('div:has-text("zł"), div:has-text("PLN")')
    
    # Odsiewamy te, które są zbyt duże (cała strona) lub zbyt małe
    potential_cards = []
    for el in elements:
        try:
            # Sprawdzamy czy to liść (nie ma zbyt wielu dzieci) lub mała karta
            text = await el.inner_text()
            if len(text) < 500 and len(text) > 20: # Rozsądna długość opisu oferty
                potential_cards.append(text)
        except: pass
        
    # Usuwamy duplikaty (bo div jest w divie)
    unique_texts = list(set(potential_cards))
    
    print(f"Znaleziono {len(unique_texts)} bloków tekstu z walutą.")

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
                if v > 50: price_val = v; break
        
        if price_val == 0: continue

        # 2. DYSTANS
        dist_val = 0.0
        dist_match = re.search(r'(\d+[.,]?\d*)\s*(km|m)\s', text_lower)
        if dist_match:
            d_val = float(dist_match.group(1).replace(',', '.'))
            unit = dist_match.group(2)
            if unit == "km": dist_val = d_val
            elif unit == "m": dist_val = d_val / 1000.0
            
        # 3. NAZWA (Bierzemy pierwszą linię tekstu jako nazwę)
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        name = lines[0] if lines else "Oferta"
        if len(name) > 50: name = name[:50] + "..."

        # 4. LINK (Tworzymy sztuczny, bo brutalny scraping gubi kontekst linku)
        full_link = f"https://www.booking.com/searchresults.pl.html?ss={name}"

        # 5. UDOGODNIENIA
        ac = any(x in text_lower for x in ["klimatyzacja", "ac", "klimatyzowany"])
        park = "parking" in text_lower
        bfast = any(x in text_lower for x in ["śniadanie", "breakfast"])

        results.append({
            "name": name,
            "price": price_val,
            "dist": dist_val,
            "link": full_link, # Link do wyszukiwania tej nazwy
            "ac": ac, "parking": park, "breakfast": bfast,
            "raw": text[:50] # debug
        })

    return results

async def run_autopilot(address, radius, start_date, end_date, filters, progress_bar, status_text, image_spot, list_placeholder, debug_area):
    twoje_fotki = pobierz_twoje_zdjecia()
    days = (end_date - start_date).days + 1
    daily_data = []
    
    async with async_playwright() as p:
        # EMULACJA IPHONE'A - To często omija blokady desktopowe!
        iphone = p.devices['iPhone 13']
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            **iphone,
            locale="pl-PL"
        )
        page = await context.new_page()

        status_text.info(f"🦖 Tryb Brutalny: {address}")

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
                
                # DIAGNOSTYKA: POKAŻ TYTUŁ STRONY
                page_title = await page.title()
                debug_area.info(f"🔍 Tytuł strony (Dzień {i+1}): {page_title}")
                
                # Zamykanie
                try: await page.click('button', timeout=1000) # Kliknij cokolwiek co jest przyciskiem (często zamyka popupy na mobile)
                except: pass

                # Scroll
                await page.evaluate("window.scrollTo(0, 5000)")
                await page.wait_for_timeout(2000)
                
                # --- POBIERANIE BRUTALNE ---
                offers = await scrape_brutal(page)
                
                if not offers:
                    # Jeśli nadal nic, zrzucamy źródło strony do pliku
                    html_content = await page.content()
                    with open(f"debug_source_{s1}.html", "w") as f:
                        f.write(html_content)
                    debug_area.error(f"⚠️ Nadal 0. Pobrano kod strony do analizy (debug_source_{s1}.html). Tytuł: {page_title}")

                valid_prices = []
                for o in offers:
                    # IGNORUJEMY FILTR DYSTANSU JEŚLI JEST 0 (żeby cokolwiek pokazać)
                    if o["dist"] > 0 and o["dist"] > radius: continue
                    
                    if filters["parking"] and not o["parking"]: continue
                    if filters["sniadanie"] and not o["breakfast"]: continue
                    if filters["klima"] and not o["ac"]: continue
                    
                    valid_prices.append(o["price"])

                count = len(valid_prices)
                list_placeholder.caption(f"Znaleziono {count} (z {len(offers)} surowych).")

                if valid_prices:
                    avg = int(sum(valid_prices) / count)
                    multiplier = 1.15 if current_date.weekday() in [4, 5] else 1.0
                    suggested = int(avg * multiplier)
                    daily_data.append({
                        "Data": s1, "Dzień": current_date.strftime("%A"),
                        "Liczba Ofert": count, "Średnia Rynkowa": avg, "Twoja Cena": suggested
                    })
                else:
                    daily_data.append({"Data": s1, "Dzień": current_date.strftime("%A"), "Liczba Ofert": 0, "Średnia Rynkowa": 0, "Twoja Cena": 0})

            except Exception as e:
                print(f"Błąd: {e}")

        await browser.close()
        return daily_data

# --- UI START ---
st.title("🦖 Asystent Brutalny")
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
    btn = st.button("🚀 URUCHOM", type="primary")
    debug_area = st.empty()

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
        
        dane_dni = asyncio.run(run_autopilot(address, radius, dates[0], dates[1], filters, progress, status, img_spot, st.empty(), debug_area))
        progress.progress(100)
        
        if dane_dni:
            df = pd.DataFrame(dane_dni)
            status.success("Zakończono!")
            
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
