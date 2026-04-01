# ♪ KEIKKAKONE

Settilista muusikoille. Lataa biisilista → lataa PDF-laput → keikalle.

## Käyttö

1. Lataa biisilista (TXT/PDF): `Biisin nimi - Artisti` per rivi
2. Lataa PDF-sointulaput
3. Sovellus yhdistää automaattisesti (fuzzy match)
4. Napauta korttia → avaa sointulappu
5. Transponoi tarvittaessa

## Asennus

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Cloud

1. Pushaa GitHubiin
2. share.streamlit.io → New app → valitse repo → `app.py`
3. Deploy
