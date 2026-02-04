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

# --- CSS (Tylko dla Twoich zdjęć) ---
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

async def parse_page_aggressive(page, radius, filters):
    """
    Nowa strategia: Szukamy wszystkich linków '/hotel/', a potem analizujemy ich rodziców.
    """
    results = []
    seen_links = set()
    
    # 1. Pobierz wszystkie linki na stronie
    # Szukamy linków, które w adresie mają słowo "hotel" - to zawsze działa
    links = await page.query_selector_all('a[href*="/hotel/"]')
    
    print(f"Znaleziono {len(links)} potencjalnych linków do ofert.")
    
    for link_el in links:
        try:
            href = await link_el.get_attribute("href")
            if not href: continue
            
            # Czysty link bez parametrów śledzących
            clean_link = href.split('?')[0]
            if clean_link in seen_links: continue
            
            # Pobieramy tekst całego kontenera (rodzica), w którym jest link
            # Wspinamy się 3 poziomy w górę, żeby złapać cenę i nazwę
            # To jest ryzykowne, ale skuteczne w chmurze
            card_context = await link_el.evaluate_handle('el => el.closest("div[data-testid=\'property-card\']") || el.closest("div[role=\'listitem\']") || el.parentElement.parentElement.parentElement')
            
            if not card_context: continue
            
            full_text = await card_context.inner_text()
            text_lower = full_text.lower()
            
            # --- CENA ---
            price_val = 0.0
            # Szukamy liczb w sąsiedztwie "zł" lub "PLN"
            matches = re.findall(r'(?:PLN|zł)\s*([\d\s]+)|([\d\s]+)\s*(?:PLN|zł)', full_text, re.IGNORECASE)
            for m in matches:
                val_str = m[0] if m[0] else m[1]
                clean = re.sub(r'\s+', '', val_str)
                if clean.isdigit():
                    val = float(clean)
                    if val > 50: # Odrzucamy śmieci poniżej 50 zł
                        price_val = val
                        break # Bierzemy pierwszą napotkaną (zazwyczaj główną) cenę
            
            if price_val == 0: continue # Nie ma ceny = nie ma oferty
            
            # --- DYSTANS ---
            dist_val = 0.0
            dist_match = re.search(r'(\d+[.,]?\d*)\s*(km|m)\s', text_lower)
            if dist_match:
                d_val = float(dist_match.group(1).replace(',', '.'))
                unit = dist_match.group(2)
                if unit == "km": dist_val = d_val
                elif unit == "m": dist_val = d_val / 1000.0
            
            # --- FILTRY ---
            if dist_val > radius: continue
            if filters["parking"] and "parking" not in text_lower: continue
            if filters["sniadanie"] and not any(x in text_lower for x in ["śniadanie", "breakfast", "wliczone"]): continue
            if filters["klima"] and not any(x in text_lower for x in ["klimatyzacja", "klimatyzowany", "ac"]): continue

            # --- NAZWA ---
            # Próbujemy znaleźć nagłówek w tym samym kontenerze
            name = "Nieznany Obiekt"
            # Szukamy elementu z dużą czcionką lub h3/h4
            try:
                name_el = await card_context.query_selector('h3, [data-testid="title"]')
                if name_el: name = await name_el.inner_text()
            except: pass

            seen_links.add(clean_link)
            
            if clean_link.startswith('http'): full_link = clean_link
            else: full_link = f"https://www.booking.com{clean_link}"

            results.append({
                "price": price_val,
                "dist": dist_val,
                "name": name,
                "link": full_link,
                "text": text_lower # do debugu
            })
            
        except Exception as e:
            continue

    return results

async def run_autopilot(address, radius, start_date, end_date, filters, progress_bar, status_text, image_spot, list_placeholder):
    twoje_fotki = pobierz_twoje_zdjecia()
    days = (end_date - start_date).days + 1
    daily_data = []
    unique_competitors = {} 
    
    async with async_playwright() as p:
        # Ustawiamy dużą rozdzielczość (viewport), żeby Booking myślał, że to duży monitor
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", 
                "--disable-dev-shm-usage", 
                "--disable-blink-features=AutomationControlled",
                "--window-size=1920,1080"
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
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
                await page.goto(url, timeout=90000)
                
                # Zamykanie popupów
                try: await page.click('#onetrust-accept-btn-handler', timeout=3000)
                except: pass
                
                # Przewijanie (ważne!)
                for _ in range(3):
                    await page.evaluate("window.scrollBy(0, 1000)")
                    await page.wait_for_timeout(1000)

                # --- NOWA STRATEGIA AGRESYWNA ---
                offers = await parse_page_aggressive(page, radius, filters)
                
                valid_prices = []
                for o in offers:
                    valid_prices.append(o["price"])
                    if o["link"] not in unique_competitors:
                        unique_competitors[o["link"]] = {
                            "Nazwa": o["name"],
                            "Link": o["link"],
                            "Dystans": f"{o['dist']:.2f} km"
                        }

                list_placeholder.caption(f"Znaleziono {len(unique_competitors)} unikalnych ofert...")

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
    # Reset suwaka do 3.0 przez zmianę key/label
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
