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

async def scrape_safe(page):
    """
    Funkcja "Odkurzacz" - bierze wszystko jak leci, bez filtrowania.
    """
    results = []
    
    # Pobieramy wszystkie elementy, które mają w sobie cenę (najbardziej niezawodny znacznik)
    # Szukamy po tekście "zł" lub "PLN" - to działa niezależnie od struktury HTML
    price_elements = await page.query_selector_all(':text-matches("PLN|zł")')
    
    print(f"Znaleziono {len(price_elements)} elementów z walutą.")
    
    seen_links = set()

    for el in price_elements:
        try:
            # Wspinamy się w górę, żeby znaleźć kontener karty
            card = await el.evaluate_handle('el => el.closest("div[data-testid=\'property-card\']") || el.closest("div[role=\'listitem\']") || el.parentElement.parentElement.parentElement')
            if not card: continue
            
            full_text = await card.inner_text()
            text_lower = full_text.lower()
            
            # --- CENA ---
            price_val = 0.0
            matches = re.findall(r'(?:PLN|zł)\s*([\d\s]+)|([\d\s]+)\s*(?:PLN|zł)', full_text, re.IGNORECASE)
            for m in matches:
                val_str = m[0] if m[0] else m[1]
                clean = re.sub(r'\s+', '', val_str)
                if clean.isdigit():
                    v = float(clean)
                    if v > 30: price_val = v; break
            
            if price_val == 0: continue

            # --- LINK ---
            link_el = await card.query_selector('a')
            link = "#"
            if link_el:
                href = await link_el.get_attribute("href")
                if href: link = href.split('?')[0]
            
            if link in seen_links: continue
            seen_links.add(link)
            
            # --- NAZWA ---
            name = "Oferta Booking"
            title_el = await card.query_selector('[data-testid="title"], h3')
            if title_el: name = await title_el.inner_text()

            # --- DYSTANS (Bezpieczny) ---
            dist_val = 999.0 # Domyślnie "daleko", jeśli nie uda się odczytać
            dist_txt = "Brak danych"
            
            dist_match = re.search(r'(\d+[.,]?\d*)\s*(km|m)\s', text_lower)
            if dist_match:
                d_val = float(dist_match.group(1).replace(',', '.'))
                unit = dist_match.group(2)
                if unit == "km": dist_val = d_val
                elif unit == "m": dist_val = d_val / 1000.0
                dist_txt = f"{dist_val:.2f} km"

            # --- UDOGODNIENIA (Do raportu) ---
            has_ac = any(x in text_lower for x in ["klimatyzacja", "klimatyzowany", "ac"])
            has_park = "parking" in text_lower
            has_bfast = any(x in text_lower for x in ["śniadanie", "breakfast"])

            if link.startswith('http'): full_link = link
            else: full_link = f"https://www.booking.com{link}"

            results.append({
                "name": name,
                "price": price_val,
                "dist_val": dist_val,
                "dist_txt": dist_txt,
                "link": full_link,
                "ac": has_ac,
                "parking": has_park,
                "breakfast": has_bfast
            })

        except: continue
        
    return results

async def run_autopilot(address, radius, start_date, end_date, filters, progress_bar, status_text, image_spot, list_placeholder):
    twoje_fotki = pobierz_twoje_zdjecia()
    days = (end_date - start_date).days + 1
    daily_data = []
    all_raw_offers = [] # Tu trzymamy wszystko co znaleźliśmy
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="pl-PL"
        )
        page = await context.new_page()

        status_text.info(f"🚀 Pobieram WSZYSTKIE oferty dla: {address}...")

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
                await page.goto(url, timeout=90000)
                try: await page.click('#onetrust-accept-btn-handler', timeout=3000)
                except: pass
                
                # Przewijanie
                for _ in range(3):
                    await page.evaluate("window.scrollBy(0, 1500)")
                    await page.wait_for_timeout(1000)

                # --- POBIERZ WSZYSTKO (BEZ FILTRÓW) ---
                offers = await scrape_safe(page)
                
                # Teraz filtrujemy w Pythonie (bezpieczniej)
                valid_prices = []
                for o in offers:
                    # Dodaj do ogólnej listy (nawet jeśli odrzucone, żebyś wiedział)
                    all_raw_offers.append(o)

                    # --- LOGIKA FILTRACJI ---
                    # Dystans: Jeśli nie udało się odczytać (999), to PRZEPUSZCZAMY (lepiej pokazać za dużo niż nic)
                    if o["dist_val"] != 999 and o["dist_val"] > radius: continue
                    
                    if filters["parking"] and not o["parking"]: continue
                    if filters["sniadanie"] and not o["breakfast"]: continue
                    if filters["klima"] and not o["ac"]: continue
                    
                    valid_prices.append(o["price"])

                count_found = len(valid_prices)
                list_placeholder.caption(f"Znaleziono {count_found} pasujących (z {len(offers)} pobranych).")

                if valid_prices:
                    avg = int(sum(valid_prices) / len(valid_prices))
                    multiplier = 1.15 if current_date.weekday() in [4, 5] else 1.0
                    suggested = int(avg * multiplier)
                    daily_data.append({
                        "Data": s1, "Dzień": current_date.strftime("%A"),
                        "Liczba Ofert": count_found, "Średnia Rynkowa": avg, "Twoja Cena": suggested
                    })
                else:
                    daily_data.append({"Data": s1, "Dzień": current_date.strftime("%A"), "Liczba Ofert": 0, "Średnia Rynkowa": 0, "Twoja Cena": 0})

            except Exception as e:
                print(f"Błąd: {e}")

        await browser.close()
        return daily_data, all_raw_offers

# --- UI START ---
st.title("🎯 Asystent Cenowy")
st.markdown("---")

col1, col2 = st.columns([1, 3])

with col1:
    st.subheader("📍 Ustawienia")
    address = st.text_input("Adres:", "Szeroka 10, Toruń")
    
    # SUWAK 3.0 KM - Odświeżony
    radius = st.number_input("Maks. Dystans (km):", 0.1, 15.0, 3.0, 0.1, key="new_radius_slider")
    
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
        dane_dni, all_raw = asyncio.run(run_autopilot(address, radius, dates[0], dates[1], filters, progress, status, img_spot, list_placeholder))
        progress.progress(100)
        
        if dane_dni:
            df = pd.DataFrame(dane_dni)
            status.success("Gotowe!")
            
            st.subheader("Wykres")
            fig = px.line(df, x="Data", y=["Średnia Rynkowa", "Twoja Cena"], markers=True, color_discrete_map={"Średnia Rynkowa": "blue", "Twoja Cena": "red"})
            st.plotly_chart(fig, use_container_width=True)
            
            st.subheader("Tabela Cen")
            st.dataframe(df, use_container_width=True)
            
            # --- TABELA WSZYSTKICH ZNALEZISK (Żebyś widział co bot pobiera) ---
            if all_raw:
                st.markdown("---")
                st.subheader("🔍 Wszystkie Znalezione Oferty (Surowe Dane)")
                st.caption("Jeśli ta lista jest pełna, a wykres pusty - poluzuj filtry.")
                df_raw = pd.DataFrame(all_raw).drop_duplicates(subset=["link"])
                st.dataframe(
                    df_raw,
                    column_config={
                        "link": st.column_config.LinkColumn("Link"),
                        "price": st.column_config.NumberColumn("Cena", format="%d zł"),
                        "dist_txt": "Odległość (zczytana)"
                    },
                    use_container_width=True
                )
            
            buffer = io.BytesIO()
            if file_format == "Excel (.xlsx)":
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df.to_excel(writer, index=False, sheet_name='Kalendarz')
                    if all_raw: pd.DataFrame(all_raw).drop_duplicates(subset=["link"]).to_excel(writer, index=False, sheet_name='Wszystkie_Oferty')
                st.download_button("💾 Pobierz Raport (Excel)", buffer, "RAPORT.xlsx", "application/vnd.ms-excel")
            else:
                c1, c2 = st.columns(2)
                c1.download_button("💾 Kalendarz (CSV)", df.to_csv(index=False, sep=';').encode('utf-8-sig'), "KALENDARZ.csv", "text/csv")
                if all_raw: c2.download_button("💾 Lista Ofert (CSV)", pd.DataFrame(all_raw).drop_duplicates(subset=["link"]).to_csv(index=False, sep=';').encode('utf-8-sig'), "LISTA.csv", "text/csv")
        else:
            status.error("Brak danych.")
