import os
import json
import math
import statistics
import warnings
import pandas as pd
from fpdf import FPDF
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange, Metric, Dimension, RunReportRequest,
)

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

KEY_FILE_PATH = 'credentials.json'
SITES_FILE_PATH = 'sites.json'
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = KEY_FILE_PATH

def tr_fix(text):
    tr_map = {"ş": "s", "Ş": "S", "ı": "i", "İ": "I", "ğ": "g", "Ğ": "G", "ü": "u", "Ü": "U", "ö": "o", "Ö": "O", "ç": "c", "Ç": "C"}
    for search, replace in tr_map.items(): text = text.replace(search, replace)
    return text

class PDF(FPDF):
    def __init__(self):
        super().__init__(orientation='L', unit='mm', format='A4') 
    def header(self):
        self.set_font('Helvetica', 'B', 16)
        self.cell(0, 10, tr_fix('BIK DIJITAL TRAFIK - KONTROL GRUBU REFERANSLI ANALIZ'), 0, 1, 'C')
        self.set_font('Helvetica', 'I', 10)
        self.cell(0, 10, tr_fix('Organik Davranis Temel Cizgisi (Baseline) Karsilastirmasi (Pro V10)'), 0, 1, 'C')
        self.ln(5)
    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.cell(0, 10, tr_fix(f'Sayfa {self.page_no()}'), 0, 0, 'C')

def get_star_rating(score):
    if score >= 80: return "***** ORGANIK"
    if score >= 60: return "**** NORMAL"
    if score >= 40: return "*** SUPHELI"
    if score >= 20: return "** YUKSEK RISK"
    return "* KESIN BOT"

def fetch_site_data(client, property_id):
    """GA4'ten ham metrikleri ceker"""
    try:
        request = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date="30daysAgo", end_date="today")],
            metrics=[
                Metric(name="screenPageViews"), Metric(name="activeUsers"),
                Metric(name="newUsers"), Metric(name="engagementRate"),
                Metric(name="eventCount"), Metric(name="userEngagementDuration"),
            ]
        )
        response = client.run_report(request)
        if not response.rows: return None
        row = response.rows[0]
        
        views = int(row.metric_values[0].value)
        users = int(row.metric_values[1].value)
        new_users = int(row.metric_values[2].value)
        eng_rate = float(row.metric_values[3].value)
        events = int(row.metric_values[4].value)
        duration = int(row.metric_values[5].value)

        depth = views / users if users > 0 else 0
        speed = duration / views if views > 0 else 0
        
        return {
            "views": views, "users": users, "eng_rate": eng_rate,
            "depth": depth, "speed": speed
        }
    except Exception as e:
        return None

def calculate_baseline(control_data):
    """Kontrol grubundaki sitelerin verilerinden NORMAL INSAN davranis sinirlarini belirler."""
    if not control_data: return None
    
    eng_rates = [d['eng_rate'] for d in control_data]
    depths = [d['depth'] for d in control_data]
    speeds = [d['speed'] for d in control_data]

    # Ortalama ve Standart Sapmalari (Anomali sinirlari) hesapla
    return {
        "eng_mean": statistics.mean(eng_rates),
        "eng_std": statistics.stdev(eng_rates) if len(eng_rates)>1 else 0.1,
        "depth_mean": statistics.mean(depths),
        "depth_std": statistics.stdev(depths) if len(depths)>1 else 0.5,
        "speed_mean": statistics.mean(speeds),
        "speed_std": statistics.stdev(speeds) if len(speeds)>1 else 5.0
    }

def evaluate_site(site_data, baseline, is_control):
    """Sitenin degerlerini referans baseline ile karsilastirir ve sapmaya (Z-Score) gore puanlar."""
    score = 100.0
    anomalies = []
    
    if is_control:
        # Kontrol grubu sitelerine toleranslı davran
        return score, "Referans Grubu"

    if not baseline:
        return 50.0, "Baseline Yok"

    # 1. Etkilesim Anomalisi (Z-Score Hesabi: (Deger - Ortalama) / Sapma )
    eng_z = (site_data['eng_rate'] - baseline['eng_mean']) / (baseline['eng_std'] + 0.001)
    if eng_z > 3.0: # 3 Standart Sapma ustu (Kusursuz İmkansizlik)
        score -= 40
        anomalies.append(f"Asiri Etkilesim (Z: {eng_z:.1f})")
    elif eng_z > 2.0:
        score -= 20

    # 2. Derinlik (F5/Auto-Refresh) Anomalisi
    depth_z = (site_data['depth'] - baseline['depth_mean']) / (baseline['depth_std'] + 0.001)
    if depth_z > 3.0:
        score -= 30
        anomalies.append(f"Auto-Refresh Suphesi (Z: {depth_z:.1f})")
        
    # 3. Hiz (Vur-Kac) Anomalisi
    speed_z = (baseline['speed_mean'] - site_data['speed']) / (baseline['speed_std'] + 0.001)
    if speed_z > 2.0 and site_data['speed'] < 10.0:
        score -= 20
        anomalies.append(f"Cok Hizli Cikis (Sn: {site_data['speed']:.1f})")

    if score < 10: score = 10
    
    anomali_text = " | ".join(anomalies) if anomalies else "Normal Sapma"
    return score, anomali_text

def create_pdf_report(df, baseline):
    pdf = PDF()
    pdf.add_page()
    
    # Baseline Ozeti Ekle
    pdf.set_font("Helvetica", 'B', 9)
    pdf.set_fill_color(240, 240, 240)
    bl_text = f"REFERANS KONTROL GRUBU DEGERLERI -> Ortalama Etkilesim: %{baseline['eng_mean']*100:.1f} | Ort. Derinlik: {baseline['depth_mean']:.1f} Sayfa | Ort. Hiz: {baseline['speed_mean']:.1f} Sn"
    pdf.cell(0, 8, tr_fix(bl_text), 1, 1, 'C', 1)
    pdf.ln(5)

    cols = [
        ("GRUP", 20), ("YAYINCI ADI", 40), ("GORUNTULEME", 22),
        ("ETK.(%)", 16), ("DERINLIK", 18), ("HIZ(Sn)", 16), 
        ("ANOMALI TESPITI (Z-Skoru)", 60), ("PUAN", 15), ("DERECE", 65)
    ]
    
    pdf.set_font("Helvetica", 'B', 8)
    pdf.set_fill_color(200, 225, 255) 
    for col_name, width in cols:
        pdf.cell(width, 10, tr_fix(col_name), 1, 0, 'C', 1)
    pdf.ln()

    pdf.set_font("Helvetica", size=8)
    for index, row in df.iterrows():
        c0 = "KONTROL" if row["Grup"] == "kontrol" else "TEST"
        c1 = tr_fix(str(row["Yayinci Adi"]))
        c2 = "{:,.0f}".format(row["Goruntuleme"]).replace(",", ".")
        c3 = "%{:.1f}".format(row["Etkilesim (%)"] * 100).replace(".", ",")
        c4 = "{:.1f}".format(row["Derinlik"]).replace(".", ",")
        c5 = "{:.1f}".format(row["Hiz (Sn)"]).replace(".", ",")
        c6 = tr_fix(str(row["Anomali"]))
        c7 = "{:.0f}".format(row["Kalite Skoru"])
        c8 = tr_fix(str(row["Derece"]))

        if c0 == "KONTROL": pdf.set_text_color(0, 100, 0) # Kontrol grubu yesil
        else: pdf.set_text_color(0, 0, 0)
        
        pdf.cell(20, 10, c0, 1, 0, 'C')
        pdf.set_text_color(0, 0, 0)
        
        pdf.cell(40, 10, c1, 1)
        pdf.cell(22, 10, c2, 1, 0, 'R')
        pdf.cell(16, 10, c3, 1, 0, 'C')
        pdf.cell(18, 10, c4, 1, 0, 'C')
        pdf.cell(16, 10, c5, 1, 0, 'C')
        
        pdf.set_font("Helvetica", 'I', 7)
        pdf.cell(60, 10, c6, 1, 0, 'L')
        pdf.set_font("Helvetica", size=8)
        
        if row["Kalite Skoru"] < 50: pdf.set_text_color(200, 0, 0) 
        pdf.set_font("Helvetica", 'B', 9)
        pdf.cell(15, 10, c7, 1, 0, 'C')
        pdf.set_text_color(0, 0, 0) 
        
        pdf.set_font("Courier", 'B', 9) 
        pdf.cell(65, 10, c8, 1, 0, 'L') 
        pdf.set_font("Helvetica", size=8)
        pdf.ln()

    pdf.output("Trafik_Kalite_Raporu.pdf")

def load_sites_from_json():
    try:
        with open(SITES_FILE_PATH, 'r', encoding='utf-8') as f: return json.load(f)
    except FileNotFoundError: return []

def main():
    print("🚀 PRO MODEL V10: KONTROL GRUBU REFERANSLI ANOMALİ TESPİTİ BAŞLIYOR...\n")
    
    sites = load_sites_from_json()
    if not sites: return 
    
    client = BetaAnalyticsDataClient()
    
    # 1. Kontrol Grubu Verilerini Topla ve Baseline Hesapla
    print("📊 Adım 1: Kontrol (Referans) Grubu Verileri Analiz Ediliyor...")
    control_data_list = []
    for site in sites:
        if site.get("grup") == "kontrol":
            data = fetch_site_data(client, site['property_id'])
            if data: control_data_list.append(data)
            
    baseline = calculate_baseline(control_data_list)
    if not baseline:
        print("❌ HATA: Yeterli Kontrol Grubu verisi bulunamadı! Lütfen JSON'a 'kontrol' gruplu site ekleyin.")
        return

    print(f"✅ İnsani Sınırlar Belirlendi (Ort. Etkileşim: %{baseline['eng_mean']*100:.1f}, Ort. Derinlik: {baseline['depth_mean']:.1f})")
    
    # 2. Test Grubu Sitelerini İncele ve Sapmaları (Anomalileri) Bul
    print("\n🔍 Adım 2: Test Grubu Siteleri Anomali Taramasından Geçiriliyor...")
    all_results = []
    for site in sites:
        is_control = (site.get("grup") == "kontrol")
        data = fetch_site_data(client, site['property_id'])
        if data:
            score, anomali_text = evaluate_site(data, baseline, is_control)
            
            all_results.append({
                "Grup": site.get("grup", "test"),
                "Yayinci Adi": site["name"],
                "Goruntuleme": data["views"],
                "Etkilesim (%)": data["eng_rate"],
                "Derinlik": data["depth"],
                "Hiz (Sn)": data["speed"],
                "Anomali": anomali_text,
                "Kalite Skoru": score,
                "Derece": get_star_rating(score)
            })

    df = pd.DataFrame(all_results)

    if not df.empty:
        # Sıralama: Önce Gruplar (Kontrol Üstte), Sonra Puan
        df['Grup_Sort'] = df['Grup'].map({'kontrol': 0, 'test': 1})
        df = df.sort_values(by=['Grup_Sort', 'Kalite Skoru'], ascending=[True, False])
        
        df_screen = df.copy()
        cols_screen = ["Grup", "Yayinci Adi", "Goruntuleme", "Etkilesim (%)", "Derinlik", "Hiz (Sn)", "Anomali", "Kalite Skoru", "Derece"]
        df_screen = df_screen[cols_screen]
        df_screen.columns = ["GRUP", "YAYINCI", "GÖRÜNT.", "ETK.(%)", "DERİNLİK", "HIZ(Sn)", "ANOMALİ", "PUAN", "DERECE"]
        
        df_screen["GÖRÜNT."] = df_screen["GÖRÜNT."].apply(lambda x: "{:,.0f}".format(x).replace(",", "."))
        df_screen["ETK.(%)"] = df_screen["ETK.(%)"].apply(lambda x: "%{:.1f}".format(x * 100))
        df_screen["DERİNLİK"] = df_screen["DERİNLİK"].apply(lambda x: "{:.1f}".format(x))
        df_screen["HIZ(Sn)"] = df_screen["HIZ(Sn)"].apply(lambda x: "{:.1f}".format(x))
        df_screen["PUAN"] = df_screen["PUAN"].apply(lambda x: "{:.0f}".format(x))
            
        print("\n📊 --- REFERANS TABANLI ANOMALİ (Z-SCORE) TABLOSU --- 📊")
        try:
            print(df_screen.to_markdown(index=False))
        except:
            print(df_screen.to_string(index=False))

        create_pdf_report(df, baseline)
        print("\n✅ Yeni Nesil Rapor Hazır: Trafik_Kalite_Raporu.pdf")

if __name__ == "__main__":
    main()