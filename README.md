# 🎵 KEIKKAKONE

Keikkasetin hallintasovellus muusikoille. Hallitse sointulappuja, transponoi sävellajeja ja pyöritä keikkaa yhdestä paikasta.

## Ominaisuudet

- 📄 **PDF-sointulaput** — lataa PDF-tiedostoja ja poimi teksti automaattisesti
- ✏️ **Manuaalinen syöttö** — kirjoita sointulaput suoraan sovellukseen
- 🎹 **Transponointi** — siirrä sävellajia ylös/alas puolisävelaskelin, ylennys- tai alennusmerkein
- 🌙 **Tumma/vaalea teema** — keikalla tumma, treenissä vaalea
- 📋 **Settilistat** — tallenna useita keikkasettejä
- 🎤 **Keikkamoodi** — selkeä esitysnäkymä isolla fontilla

## Käyttöönotto

### Streamlit Cloud (suositeltu)
1. Forkkaa tai kloonaa tämä repo
2. Mene [share.streamlit.io](https://share.streamlit.io)
3. Luo uusi appi → valitse tämä repo → `app.py`
4. Deploy!

### Paikallinen asennus
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Tekniikka

- **Streamlit** — web-UI
- **pdfplumber** — PDF-tekstin purku
- **Oma transponointimoottori** — regex-pohjainen sointujen tunnistus ja kromaattinen transponointi
