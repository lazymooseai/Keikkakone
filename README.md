# 🎵 KEIKKAKONE v2.0

Keikkasetin hallintasovellus muusikoille. Lataa biisilista → hae sointulaput automaattisesti → keikalle!

## Työnkulku

1. **Lataa biisilista** (TXT tai PDF): `Biisin nimi - Artisti` per rivi
2. **Hae sointulaput** Google Drivestä tai laitteelta (PDF:t)
3. **Automaattinen fuzzy matching** yhdistää biisinimet PDF-tiedostoihin
4. **Korjaa tarvittaessa** käsin jos automaattinen arvaus on väärä
5. **Rakenna keikkasetti** → transponoi, järjestä, keikkamoodi!

## Ominaisuudet

- 📄 Biisilistan automaattinen parsinta (TXT/PDF)
- 🔍 Fuzzy matching — löytää PDF:n vaikka nimi ei täsmää (esim. "tammerkoski_chords.pdf" ↔ "Tammerkoski")
- ☁️ Google Drive -integraatio (valittava kansio)
- 📱 Paikallinen PDF-upload
- 🎹 Sointujen transponointi (ylennys/alennus)
- 🌙 Tumma/vaalea teema
- 🎤 Keikkamoodi (selkeä esitysnäkymä)
- 💾 Settien tallennus ja lataus

## Asennus Streamlit Cloudiin

1. Luo GitHub-repo ja pushaa nämä tiedostot
2. Mene [share.streamlit.io](https://share.streamlit.io)
3. New app → valitse repo → `app.py` → Deploy

### Google Drive -integraatio (valinnainen)

1. Luo projekti [Google Cloud Console](https://console.cloud.google.com)
2. Ota käyttöön Google Drive API
3. Luo OAuth 2.0 Client ID (Web application)
4. Lisää redirect URI: `https://your-app.streamlit.app`
5. Lisää Streamlit Cloud Settings → Secrets:

```toml
GOOGLE_CLIENT_ID = "your-client-id.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "your-client-secret"
GOOGLE_REDIRECT_URI = "https://your-app.streamlit.app"
```

## Paikallinen asennus

```bash
pip install -r requirements.txt
streamlit run app.py
```
