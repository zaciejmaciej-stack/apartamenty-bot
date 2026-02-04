import streamlit as st
import asyncio
from playwright.async_api import async_playwright
from datetime import date, timedelta
import pandas as pd
import plotly.express as px
import io
import re
import os
import random

st.set_page_config(page_title="Autopilot Pro + Radius", page_icon="🎯", layout="wide")

# --- CSS DLA WIĘKSZYCH ZDJĘĆ ---
st.markdown("""
<style>
    [data-testid="stImage"] img {
        max-height: 600px;
        object-fit: cover; 
        border-radius: 15px;
    }
</style>
""", unsafe_allow_html=True)

# --- FUNKCJE POMOCNICZE ---

def pobierz_twoje_zdjecia():
    folder = "moje_zdjecia"
    if not os.path.exists(folder): return []
    return [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]

async def parse_card_content(card):
    """Pobiera dane z karty: cenę, tekst, link i DYSTANS"""
    info = {}
    try:
        full_text = await card.inner_text()
        info["text"] = full_text.lower()
        
        # Cena
        price_el = await card.query_selector('[data-testid="price-and-discounted-price"]')
        if price_el:
            price_txt = await price_el.inner_text()
            info["price"] = float(re.sub(r'[^\d]', '', price_txt))
        else:
            return None 
            
        # Nazwa
        title_el = await card.query_selector('[data-testid="title"]')
        info["name"] = await title_el.inner_text() if title_el else "Obiekt"
        
        # Link
        link_el = await card.query_selector('a[data-testid="title-link"]')
        info["link"] = link_el.get_attribute("href").split('?')[0] if link_el else "#"
        
        # Ocena
        rating_el = await card.query_selector('[data-testid="review-score"]')
        if rating_el:
            rt = await rating_el.inner_text()
            match = re.search(r'(\d+[.,]\d+)', rt)
            info["rating"] = match.group(1) if match else "-"
        else:
            info["rating"] = "-"

        # --- DYSTANS (Kluczowe dla promienia) ---
        distance_el = await card.query_selector('[data-testid="distance"]')
        info["dist_val"] = 0.0
        
        if distance_el:
            dist_txt = await distance_el.inner_text()
            # Szukamy liczby (np. 500 m, 2.3 km)
            nums = re.findall(r"(\d+[.,]?\d*)", dist_txt)
            if nums:
                val = float(nums[0].replace(',', '.'))
                if "km" in dist_txt:
                    info["dist_val"] = val
                elif "m" in dist_txt:
                    info["dist_val"] = val / 1000.0 # Zamiana metrów na km

        return info
    except:
        return None

async def run_autopilot(address, radius, start_date, end_date, filters, progress_bar, status_text, image_spot, list_placeholder):
    twoje_fotki = pobierz_twoje_zdjecia()
    days = (end_date - start_date).days + 1
    
    daily_data = []
    unique_competitors = {} 
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        status_text.info(f"🚀 Startuję analizę dla adresu: {address} (Promień: {radius} km)")

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

            # URL: ss=ADRES + order=distance_from_search (najbliższe najpierw)
            url = (f"https://www.booking.com/searchresults.pl.html?ss={address}"
                   f"&checkin={s1}&checkout={s2}&group_adults=2&selected_currency=PLN"
                   f"&order=distance_from_search")

            try:
                await page.goto(url, timeout=45000)
                
                try: await page.click('button[aria-label="Zamknij logowanie"]', timeout=1000)
                except: pass
                try: await page.click('#onetrust-accept-btn-handler', timeout=1000)
                except: pass

                await page.evaluate("window.scrollTo(0, 1500)")
                await page.wait_for_timeout(1500)

                cards = await page.query_selector_all('[data-testid="property-card"]')
                
                valid_prices = []
                
                # Skanujemy pierwsze 40 wyników (żeby mieć z czego wybierać blisko)
                for c in cards[:40]:
                    data = await parse_card_content(c)
                    if data:
                        # 1. FILTR DYSTANSU
                        # Booking sortuje od najbliższych, ale czasem wrzuca "Polecane" daleko.
                        # Bezwzględnie odrzucamy jeśli dystans > radius
                        if data["dist_val"] > radius:
                            continue

                        # 2. FILTRY UDOGODNIEŃ
                        if filters["parking"] and "parking" not in data["text"]: continue
                        if filters["sniadanie"] and not any(x in data["text"] for x in ["śniadanie", "breakfast", "wliczone"]): continue
                        if filters["klima"] and not any(x in data["text"] for x in ["klimatyzacja", "klimatyzowany", "ac"]): continue
                        
                        valid_prices.append(data["price"])
                        
                        if data["link"] not in unique_competitors:
                            unique_competitors[data["link"]] = {
                                "Nazwa": data["name"],
                                "Ocena": data["rating"],
                                "Dystans": f"{data['dist_val']:.2f} km",
                                "Link": data["link"]
                            }

                list_placeholder.caption(f"Znaleziono {len(unique_competitors)} konkurentów w promieniu {radius} km...")

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
                    daily_data.append({
                        "Data": s1, "Dzień": current_date.strftime("%A"),
                        "Liczba Ofert": 0, "Średnia Rynkowa": 0, "Twoja Cena": 0
                    })

            except Exception as e:
                print(f"Błąd: {e}")

        await browser.close()
        competitors_list = list(unique_competitors.values())
        return daily_data, competitors_list

# --- UI START ---
st.title("🎯 Autopilot + Promień")
st.markdown("---")

col1, col2 = st.columns([1, 3])

with col1:
    st.subheader("📍 Lokalizacja")
    address = st.text_input("Dokładny adres (Ulica, Miasto):", "Szeroka 10, Toruń")
    
    # SUWAK PROMIENIA WRÓCIŁ!
    radius = st.number_input("Szukaj w promieniu (km):", 0.1, 10.0, 0.5, 0.1)
    
    st.markdown("---")
    dates = st.date_input("Zakres dat:", (date.today(), date.today() + timedelta(days=7)))
    
    st.subheader("🛠️ Filtry")
    f_klima = st.checkbox("❄️ Klimatyzacja")
    f_parking = st.checkbox("🅿️ Parking")
    f_sniadanie = st.checkbox("🥐 Śniadanie")
    
    st.markdown("---")
    file_format = st.radio("Format pliku:", ["Excel (.xlsx)", "Numbers (.csv)"])
    
    st.markdown("---")
    btn = st.button("🚀 URUCHOM", type="primary")
    
    st.markdown("---")
    list_placeholder = st.empty()

with col2:
    status = st.empty()
    progress = st.empty()
    img_spot = st.empty()

if btn:
    if len(dates) != 2:
        st.error("Ustaw daty!")
    else:
        filters = {"klima": f_klima, "parking": f_parking, "sniadanie": f_sniadanie}
        
        progress.progress(0)
        dane_dni, dane_konkurencji = asyncio.run(
            run_autopilot(address, radius, dates[0], dates[1], filters, progress, status, img_spot, list_placeholder)
        )
        progress.progress(100)
        
        if dane_dni:
            df = pd.DataFrame(dane_dni)
            df_comp = pd.DataFrame(dane_konkurencji)
            
            status.success("Gotowe!")
            
            st.subheader("📊 Wykres Cen")
            fig = px.line(df, x="Data", y=["Średnia Rynkowa", "Twoja Cena"], markers=True,
                         color_discrete_map={"Średnia Rynkowa": "blue", "Twoja Cena": "red"})
            st.plotly_chart(fig, use_container_width=True)
            
            st.subheader("📅 Tabela")
            st.dataframe(df, use_container_width=True)
            
            if not df_comp.empty:
                st.subheader(f"🔗 Konkurencja w promieniu {radius} km")
                st.dataframe(
                    df_comp,
                    column_config={
                        "Link": st.column_config.LinkColumn("Link", display_text="Otwórz"),
                        "Dystans": st.column_config.TextColumn("Odległość")
                    },
                    use_container_width=True
                )
            
            buffer = io.BytesIO()
            filename = ""
            mime = ""
            
            if file_format == "Excel (.xlsx)":
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df.to_excel(writer, index=False, sheet_name='Kalendarz')
                    if not df_comp.empty:
                        df_comp.to_excel(writer, index=False, sheet_name='Konkurencja')
                filename = "RAPORT_RADIUS.xlsx"
                mime = "application/vnd.ms-excel"
                st.download_button(f"💾 Pobierz Raport ({filename})", buffer, filename, mime)
            else:
                col_d1, col_d2 = st.columns(2)
                csv_cal = df.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
                col_d1.download_button("💾 Kalendarz (.csv)", csv_cal, "KALENDARZ.csv", "text/csv")
                if not df_comp.empty:
                    csv_list = df_comp.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
                    col_d2.download_button("💾 Konkurencja (.csv)", csv_list, "KONKURENCJA.csv", "text/csv")
            
        else:
            status.error("Brak danych. Spróbuj zwiększyć promień.")
