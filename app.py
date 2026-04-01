"""
KEIKKAKONE v2.0 - Keikkasetin hallinta muusikoille
- Lataa setlisti (txt/PDF) → automaattinen PDF-lappujen haku
- Google Drive -integraatio (valittava kansio)
- Paikallinen PDF-lataus
- Fuzzy matching sekalaisille tiedostonimille
- Sointujen transponointi
- Tumma/vaalea keikkateema
"""

import streamlit as st
import json
import re
import os
import io
from datetime import datetime
from difflib import SequenceMatcher

# ─── PDF text extraction ───
try:
    import pdfplumber
    PDF_ENGINE = "pdfplumber"
except ImportError:
    try:
        from PyPDF2 import PdfReader
        PDF_ENGINE = "pypdf2"
    except ImportError:
        PDF_ENGINE = None


def extract_text_from_pdf_bytes(file_bytes) -> str:
    if PDF_ENGINE == "pdfplumber":
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            pages = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
            return "\n\n".join(pages)
    elif PDF_ENGINE == "pypdf2":
        reader = PdfReader(io.BytesIO(file_bytes))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)
    return ""


def extract_text_from_pdf(uploaded_file) -> str:
    file_bytes = uploaded_file.read()
    uploaded_file.seek(0)
    return extract_text_from_pdf_bytes(file_bytes)


# ─── Google Drive Integration ───
GDRIVE_AVAILABLE = False
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    GDRIVE_AVAILABLE = True
except ImportError:
    pass


def get_gdrive_auth_url():
    client_config = {
        "web": {
            "client_id": st.secrets.get("GOOGLE_CLIENT_ID", ""),
            "client_secret": st.secrets.get("GOOGLE_CLIENT_SECRET", ""),
            "redirect_uris": [st.secrets.get("GOOGLE_REDIRECT_URI", "http://localhost:8501")],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
        redirect_uri=client_config["web"]["redirect_uris"][0]
    )
    auth_url, state = flow.authorization_url(prompt="consent", access_type="offline")
    st.session_state.gdrive_state = state
    st.session_state.gdrive_flow = flow
    return auth_url


def complete_gdrive_auth(code):
    flow = st.session_state.get("gdrive_flow")
    if flow:
        flow.fetch_token(code=code)
        st.session_state.gdrive_creds = flow.credentials
        return True
    return False


def get_drive_service():
    creds = st.session_state.get("gdrive_creds")
    if creds:
        return build("drive", "v3", credentials=creds)
    return None


def list_drive_folders(service):
    results = service.files().list(
        q="mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id, name)",
        orderBy="name",
        pageSize=100
    ).execute()
    return results.get("files", [])


def list_pdfs_in_folder(service, folder_id):
    pdfs = []
    query = f"'{folder_id}' in parents and trashed=false"
    results = service.files().list(
        q=query, fields="files(id, name, mimeType)", pageSize=500
    ).execute()
    for f in results.get("files", []):
        if f["mimeType"] == "application/pdf":
            pdfs.append({"id": f["id"], "name": f["name"]})
        elif f["mimeType"] == "application/vnd.google-apps.folder":
            pdfs.extend(list_pdfs_in_folder(service, f["id"]))
    return pdfs


def download_pdf_from_drive(service, file_id):
    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


# ─── Setlist Parsing ───
def parse_setlist_text(text):
    lines = text.strip().split("\n")
    songs = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        cleaned = re.sub(r'^\d+[\.\)\:\-]\s*', '', line).strip()
        if not cleaned:
            continue
        title = cleaned
        artist = ""
        if " - " in cleaned:
            parts = cleaned.split(" - ", 1)
            title = parts[0].strip()
            artist = parts[1].strip()
        elif " / " in cleaned:
            parts = cleaned.split(" / ", 1)
            title = parts[0].strip()
            artist = parts[1].strip()
        if title:
            songs.append({"title": title, "artist": artist, "original_line": line})
    return songs


# ─── Fuzzy Matching Engine ───
def normalize_for_matching(text):
    text = text.lower()
    text = re.sub(r'\.pdf$', '', text)
    for suffix in ['_chords', '_soinnut', '_chord', '_tabs', '_tab',
                   '_sheet', '_nuotti', '_lappu', ' chords', ' soinnut',
                   ' chord', ' tabs', ' sheet', ' nuotti', ' lappu',
                   '(chords)', '(soinnut)', ' pdf']:
        text = text.replace(suffix, '')
    text = re.sub(r'[_\-\.\(\)\[\]]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def fuzzy_score(query, candidate):
    q = normalize_for_matching(query)
    c = normalize_for_matching(candidate)
    if q == c:
        return 1.0
    if q in c or c in q:
        return 0.9
    q_words = set(q.split())
    c_words = set(c.split())
    if q_words and q_words.issubset(c_words):
        return 0.85
    if q_words and c_words:
        overlap = len(q_words & c_words)
        word_score = overlap / max(len(q_words), len(c_words))
        if word_score > 0.5:
            return 0.7 + (word_score * 0.2)
    return SequenceMatcher(None, q, c).ratio()


def find_best_match(song_title, artist, available_pdfs, threshold=0.45):
    best_match = None
    best_score = 0
    for pdf in available_pdfs:
        score_title = fuzzy_score(song_title, pdf["name"])
        score_combined = 0
        if artist:
            score_combined = max(
                fuzzy_score(f"{song_title} {artist}", pdf["name"]),
                fuzzy_score(f"{artist} {song_title}", pdf["name"])
            )
        score = max(score_title, score_combined)
        if score > best_score:
            best_score = score
            best_match = {**pdf, "score": score}
    if best_match and best_score >= threshold:
        return best_match
    return None


# ─── Chord Transposition Engine ───
NOTES_SHARP = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
NOTES_FLAT = ["C","Db","D","Eb","E","F","Gb","G","Ab","A","Bb","B"]

CHORD_PATTERN = re.compile(
    r'\b([A-G])(#|b|♯|♭)?'
    r'(m|min|maj|dim|aug|sus|add|dom)?'
    r'(2|4|5|6|7|9|11|13)?'
    r'((?:add|b|#|no|sus|maj|min|dim|aug|alt|\d)*)'
    r'(/([A-G])(#|b|♯|♭)?)?'
    r'(?=\s|$|[)\]\-|:])'
)


def note_to_index(root, acc=""):
    acc = acc.replace("\u266f","#").replace("\u266d","b") if acc else ""
    name = root + acc
    if name in NOTES_SHARP: return NOTES_SHARP.index(name)
    if name in NOTES_FLAT: return NOTES_FLAT.index(name)
    return {"Cb":11,"Fb":4,"E#":5,"B#":0}.get(name, -1)


def index_to_note(idx, use_flats=False):
    return (NOTES_FLAT if use_flats else NOTES_SHARP)[idx % 12]


def transpose_chord_match(match, semitones, use_flats):
    root, acc, qual, ext, mods, slash, bass_root, bass_acc = match.groups()
    idx = note_to_index(root, acc or "")
    if idx == -1: return match.group(0)
    result = index_to_note(idx + semitones, use_flats) + (qual or "") + (ext or "") + (mods or "")
    if bass_root:
        bi = note_to_index(bass_root, bass_acc or "")
        if bi != -1: result += "/" + index_to_note(bi + semitones, use_flats)
    return result


def is_chord_line(line):
    trimmed = line.strip()
    if not trimmed: return False
    clean = re.sub(r'[\s|/\-()\[\]:.,]', '', CHORD_PATTERN.sub("", trimmed))
    return bool(CHORD_PATTERN.findall(trimmed)) and len(clean) < len(trimmed) * 0.4


def transpose_text(text, semitones, use_flats=False):
    if semitones == 0: return text
    return "\n".join(
        CHORD_PATTERN.sub(lambda m: transpose_chord_match(m, semitones, use_flats), line)
        if is_chord_line(line) else line
        for line in text.split("\n")
    )


# ─── Persistent Storage ───
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DATA_FILE = os.path.join(DATA_DIR, "keikkakone_data.json")

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception: pass
    return {"songs": {}, "setlists": {}, "current_setlist": None, "current_setlist_songs": []}

def save_data(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def persist():
    save_data({
        "songs": st.session_state.songs,
        "setlists": st.session_state.setlists,
        "current_setlist": st.session_state.current_setlist_name,
        "current_setlist_songs": st.session_state.current_setlist,
    })


# ─── Init ───
def init_state():
    if "initialized" not in st.session_state:
        data = load_data()
        st.session_state.songs = data.get("songs", {})
        st.session_state.setlists = data.get("setlists", {})
        st.session_state.current_setlist_name = data.get("current_setlist")
        st.session_state.current_setlist = data.get("current_setlist_songs", [])
        st.session_state.view = "home"
        st.session_state.perform_song_idx = 0
        st.session_state.initialized = True


st.set_page_config(page_title="KEIKKAKONE", page_icon="\U0001f3b5", layout="wide", initial_sidebar_state="collapsed")
init_state()


# ─── Theme ───
def apply_theme(dark=True):
    if dark:
        st.markdown("""<style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700;800&display=swap');
        .stApp{background:#0a0a0f!important}
        .stApp,.stApp *{font-family:'JetBrains Mono',monospace!important;color:#e8e8f0}
        .stMarkdown h1{color:#ff6b35!important;letter-spacing:2px}
        .stMarkdown h2{color:#ff6b35!important}
        .stMarkdown h3{color:#ffb347!important}
        div[data-testid="stSidebar"]{background:#0f0f18!important}
        .chord-line{color:#ffb347!important;font-weight:700;font-size:1.15em}
        .lyric-line{color:#e8e8f0}
        .match-good{color:#4ade80} .match-ok{color:#ffb347} .match-bad{color:#f87171}
        .setlist-num{color:#ff6b35;font-weight:800;font-size:1.3em}
        .perform-title{color:#ff6b35;font-size:1.8em;font-weight:800;letter-spacing:1px}
        .transpose-badge{background:rgba(255,179,71,0.15);color:#ffb347;padding:2px 10px;border-radius:6px;font-size:0.85em;font-weight:600;display:inline-block}
        .status-card{background:#14141f;border:1px solid #2a2a3e;border-radius:10px;padding:14px 16px;margin-bottom:8px}
        .nav-info{background:#14141f;border:1px solid #2a2a3e;border-radius:8px;padding:8px 16px;text-align:center;color:#8888a0;font-size:0.85em}
        div[data-testid="stExpander"]{background:#14141f!important;border:1px solid #2a2a3e!important;border-radius:10px!important}
        </style>""", unsafe_allow_html=True)
    else:
        st.markdown("""<style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700;800&display=swap');
        .stApp{background:#f5f0e8!important}
        .stApp,.stApp *{font-family:'JetBrains Mono',monospace!important;color:#1a1a2e}
        .stMarkdown h1{color:#d35400!important;letter-spacing:2px}
        .stMarkdown h2{color:#d35400!important}
        .stMarkdown h3{color:#8b4513!important}
        div[data-testid="stSidebar"]{background:#ede8dc!important}
        .chord-line{color:#8b4513!important;font-weight:700;font-size:1.15em}
        .lyric-line{color:#1a1a2e}
        .match-good{color:#16a34a} .match-ok{color:#b45309} .match-bad{color:#dc2626}
        .setlist-num{color:#d35400;font-weight:800;font-size:1.3em}
        .perform-title{color:#d35400;font-size:1.8em;font-weight:800}
        .transpose-badge{background:rgba(139,69,19,0.1);color:#8b4513;padding:2px 10px;border-radius:6px;font-size:0.85em}
        .status-card{background:#fff;border:1px solid #d4cfc0;border-radius:10px;padding:14px 16px;margin-bottom:8px}
        .nav-info{background:#fff;border:1px solid #d4cfc0;border-radius:8px;padding:8px 16px;text-align:center;color:#666650;font-size:0.85em}
        div[data-testid="stExpander"]{background:#fff!important;border:1px solid #d4cfc0!important;border-radius:10px!important}
        </style>""", unsafe_allow_html=True)


# ─── Sidebar ───
with st.sidebar:
    st.markdown("## \u2699\ufe0f Asetukset")
    dark_mode = st.toggle("\U0001f319 Tumma teema", value=True, key="dark_mode")
    apply_theme(dark_mode)
    st.markdown("---")
    st.markdown("### \U0001f4c2 Navigointi")
    for label, view_name in [("\U0001f3e0 Etusivu","home"),("\U0001f4da Kirjasto","library"),("\U0001f4cb Setti","setlist")]:
        if st.button(label, use_container_width=True, key=f"nav_{view_name}"):
            st.session_state.view = view_name
            st.rerun()
    if st.button("\U0001f3a4 Keikkamoodi", use_container_width=True, disabled=not st.session_state.current_setlist):
        st.session_state.view = "perform"
        st.session_state.perform_song_idx = 0
        st.rerun()
    st.markdown("---")
    st.markdown("### \U0001f4be Setit")
    for name in list(st.session_state.setlists.keys()):
        c1, c2 = st.columns([3,1])
        with c1:
            if st.button(f"\U0001f4cb {name}", key=f"ld_{name}", use_container_width=True):
                st.session_state.current_setlist = list(st.session_state.setlists[name])
                st.session_state.current_setlist_name = name
                st.session_state.view = "setlist"
                persist(); st.rerun()
        with c2:
            if st.button("\U0001f5d1", key=f"ds_{name}"):
                del st.session_state.setlists[name]; persist(); st.rerun()
    if not st.session_state.setlists:
        st.caption("Ei tallennettuja")
    st.markdown("---")
    st.caption("KEIKKAKONE v2.0 \U0001f3b5")


# ─── Render helpers ───
def render_song_text(text, transpose=0, use_flats=False):
    transposed = transpose_text(text, transpose, use_flats)
    html = []
    for line in transposed.split("\n"):
        esc = line.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        if not esc.strip():
            html.append("<br>")
        elif is_chord_line(line):
            html.append(f'<div class="chord-line">{esc}</div>')
        else:
            html.append(f'<div class="lyric-line">{esc}</div>')
    st.markdown(f'<div style="font-family:\'JetBrains Mono\',monospace;line-height:1.7;white-space:pre-wrap;">{"".join(html)}</div>', unsafe_allow_html=True)


def match_quality_label(score):
    if score >= 0.8: return ("\u2705 Erinomainen","match-good")
    if score >= 0.6: return ("\U0001f7e1 Hyv\u00e4","match-ok")
    if score >= 0.45: return ("\U0001f7e0 Arvaus","match-ok")
    return ("\u274c Ei l\u00f6ydy","match-bad")


# ═══════════════════════════════════════════
#                HOME VIEW
# ═══════════════════════════════════════════
if st.session_state.view == "home":
    st.markdown("# \u266a KEIKKAKONE")
    st.markdown("##### Lataa biisilista \u2192 hae laput \u2192 keikalle!")
    st.markdown("---")

    # Step 1: Setlist
    st.markdown("## 1\ufe0f\u20e3 Lataa biisilista")
    st.caption("Muoto: `Biisin nimi - Artisti` riveitt\u00e4in. Numerointi vapaaehtoinen.")

    tab_file, tab_paste = st.tabs(["\U0001f4c4 Tiedostosta","✏\ufe0f Liit\u00e4 teksti"])
    setlist_songs = []

    with tab_file:
        sf = st.file_uploader("Valitse setlisti", type=["txt","pdf","text"], key="sl_up")
        if sf:
            raw = extract_text_from_pdf(sf) if sf.type == "application/pdf" else sf.read().decode("utf-8", errors="replace")
            setlist_songs = parse_setlist_text(raw)
            if setlist_songs:
                st.success(f"\u2705 {len(setlist_songs)} biisi\u00e4 l\u00f6ydetty!")
                for i, s in enumerate(setlist_songs):
                    st.markdown(f"{i+1}. **{s['title']}**" + (f" \u2014 {s['artist']}" if s['artist'] else ""))
            else:
                st.warning("Listasta ei l\u00f6ytynyt biisej\u00e4.")

    with tab_paste:
        pasted = st.text_area("Liit\u00e4 biisilista", height=200, placeholder="1. Tammerkoski - Eppu Normaali\n2. Miss\u00e4 se v\u00e4yl\u00e4 on - Popeda")
        if pasted.strip():
            setlist_songs = parse_setlist_text(pasted)
            if setlist_songs:
                st.success(f"\u2705 {len(setlist_songs)} biisi\u00e4!")

    if not setlist_songs:
        st.stop()

    st.markdown("---")

    # Step 2: PDF source
    st.markdown("## 2\ufe0f\u20e3 Hae sointulaput")
    available_pdfs = []

    tab_local, tab_drive = st.tabs(["\U0001f4f1 Laitteelta","\u2601\ufe0f Google Drive"])

    with tab_local:
        st.caption("Valitse kaikki PDF-sointulaput kerralla. Sovellus yhdist\u00e4\u00e4 ne automaattisesti.")
        ups = st.file_uploader("Valitse PDF:t", type=["pdf"], accept_multiple_files=True, key="pdf_batch")
        if ups:
            for uf in ups:
                b = uf.read(); uf.seek(0)
                available_pdfs.append({"name": uf.name, "source": "local", "bytes": b})
            st.success(f"\U0001f4c4 {len(ups)} PDF:\u00e4\u00e4 ladattu")

    with tab_drive:
        has_secrets = False
        try:
            has_secrets = st.secrets.get("GOOGLE_CLIENT_ID", "") != ""
        except Exception:
            pass

        if not GDRIVE_AVAILABLE:
            st.info("☁\ufe0f Google Drive vaatii lis\u00e4kirjastot.\n\nLis\u00e4\u00e4 `requirements.txt`:\n```\ngoogle-auth-oauthlib\ngoogle-api-python-client\n```\n\nJa aseta `secrets.toml`:\n```\nGOOGLE_CLIENT_ID = \"...\"\nGOOGLE_CLIENT_SECRET = \"...\"\nGOOGLE_REDIRECT_URI = \"https://your-app.streamlit.app\"\n```")
        elif not has_secrets:
            st.info("☁\ufe0f Lis\u00e4\u00e4 Google OAuth -asetukset `.streamlit/secrets.toml` tai Streamlit Cloud Settings.")
        else:
            creds = st.session_state.get("gdrive_creds")
            if not creds:
                auth_url = get_gdrive_auth_url()
                st.markdown(f"[\U0001f510 Kirjaudu Google-tilill\u00e4]({auth_url})")
                code = st.text_input("Liit\u00e4 koodi:")
                if code and complete_gdrive_auth(code):
                    st.success("\u2705 Yhdistetty!"); st.rerun()
            else:
                service = get_drive_service()
                if service:
                    folders = list_drive_folders(service)
                    fmap = {f["name"]: f["id"] for f in folders}
                    sel = st.selectbox("\U0001f4c1 Kansio", list(fmap.keys()))
                    if sel and st.button("\U0001f50d Hae PDF:t"):
                        with st.spinner("Haetaan..."):
                            dpdfs = list_pdfs_in_folder(service, fmap[sel])
                        st.success(f"\U0001f4c4 {len(dpdfs)} PDF:\u00e4\u00e4 kansiosta '{sel}'")
                        prog = st.progress(0)
                        for i, pi in enumerate(dpdfs):
                            b = download_pdf_from_drive(service, pi["id"])
                            available_pdfs.append({"name": pi["name"], "source": "drive", "bytes": b})
                            prog.progress((i+1)/len(dpdfs))
                        prog.empty()

    if not available_pdfs:
        st.info("\u2b06\ufe0f Lataa PDF-laput yll\u00e4.")
        st.stop()

    st.markdown("---")

    # Step 3: Matching
    st.markdown("## 3\ufe0f\u20e3 Yhdist\u00e4 biisit lappuihin")
    st.caption("Automaattinen fuzzy-haku. Voit korjata k\u00e4sin.")

    matches = []
    pool = list(available_pdfs)
    for song in setlist_songs:
        best = find_best_match(song["title"], song["artist"], pool)
        if best:
            pool = [p for p in pool if p["name"] != best["name"]]
            matches.append({"song": song, "pdf": best, "score": best["score"]})
        else:
            matches.append({"song": song, "pdf": None, "score": 0})

    all_pdf_names = [p["name"] for p in available_pdfs]
    confirmed = []

    for i, m in enumerate(matches):
        song = m["song"]
        st.markdown(f'<div class="status-card">', unsafe_allow_html=True)
        c1, c2, c3 = st.columns([1, 4, 3])
        with c1:
            st.markdown(f'<span class="setlist-num">{i+1}</span>', unsafe_allow_html=True)
        with c2:
            st.markdown(f"**{song['title']}**" + (f" \u2014 {song['artist']}" if song['artist'] else ""))
            if m["pdf"]:
                lbl, cls = match_quality_label(m["score"])
                st.markdown(f'<span class="{cls}">{lbl}</span> \u2192 `{m["pdf"]["name"]}`', unsafe_allow_html=True)
            else:
                st.markdown('<span class="match-bad">\u274c Ei l\u00f6ytynyt</span>', unsafe_allow_html=True)
        with c3:
            opts = ["-- Valitse k\u00e4sin --"] + all_pdf_names
            default = 0
            if m["pdf"]:
                try: default = opts.index(m["pdf"]["name"])
                except: default = 0
            sel = st.selectbox("PDF", opts, index=default, key=f"mo_{i}", label_visibility="collapsed")

            if sel != "-- Valitse k\u00e4sin --":
                confirmed.append({"song": song, "pdf": next((p for p in available_pdfs if p["name"]==sel), None)})
            elif m["pdf"] and m["score"] >= 0.45:
                confirmed.append({"song": song, "pdf": next((p for p in available_pdfs if p["name"]==m["pdf"]["name"]), None)})
            else:
                confirmed.append({"song": song, "pdf": None})
        st.markdown('</div>', unsafe_allow_html=True)

    mc = sum(1 for c in confirmed if c["pdf"])
    st.markdown("---")
    st.markdown(f"### Yhteenveto: {mc}/{len(confirmed)} yhdistetty")

    if mc == 0:
        st.warning("Valitse PDF:t k\u00e4sin yll\u00e4.")
        st.stop()

    sn = st.text_input("Setin nimi", "Keikkasetti", key="nsn")

    if st.button("\U0001f680 RAKENNA KEIKKASETTI", type="primary", use_container_width=True):
        new_sl = []
        for cm in confirmed:
            song = cm["song"]
            pdf = cm["pdf"]
            key = song["title"]
            if pdf:
                text = extract_text_from_pdf_bytes(pdf["bytes"])
                if not text.strip():
                    text = f"[PDF '{pdf['name']}' \u2013 teksti\u00e4 ei voitu lukea]\n\nMuokkaa k\u00e4sin."
                st.session_state.songs[key] = {"text": text, "pdf_name": pdf["name"], "artist": song.get("artist",""), "added": datetime.now().isoformat()}
            elif key not in st.session_state.songs:
                st.session_state.songs[key] = {"text": f"[Ei lappua]\n\n{key}\n\nLis\u00e4\u00e4 sointulappu.", "artist": song.get("artist",""), "added": datetime.now().isoformat()}
            new_sl.append({"song": key, "transpose": 0, "use_flats": False})

        st.session_state.current_setlist = new_sl
        st.session_state.current_setlist_name = sn
        st.session_state.setlists[sn] = list(new_sl)
        persist()
        st.success(f"\u2705 '{sn}' valmis! ({mc} biisi\u00e4)")
        st.session_state.view = "setlist"
        st.rerun()


# ═══════════════════════════════════════════
#              LIBRARY VIEW
# ═══════════════════════════════════════════
elif st.session_state.view == "library":
    c1, c2 = st.columns([3,1])
    with c1:
        st.markdown("# \u266a BIISIKIRJASTO")
        st.caption(f"{len(st.session_state.songs)} biisi\u00e4")
    with c2:
        if st.button("\U0001f4cb SETTI \u2192", type="primary"):
            st.session_state.view = "setlist"; st.rerun()

    with st.expander("\u2795 Lis\u00e4\u00e4 biisi k\u00e4sin"):
        with st.form("mf"):
            nm = st.text_input("Nimi")
            ar = st.text_input("Artisti")
            tx = st.text_area("Sointulappu", height=200)
            up = st.file_uploader("Tai PDF", type=["pdf"], key="lpu")
            if st.form_submit_button("\U0001f4be Tallenna"):
                if nm:
                    if up: tx = extract_text_from_pdf(up)
                    st.session_state.songs[nm] = {"text": tx or "[Tyhj\u00e4]", "artist": ar, "added": datetime.now().isoformat()}
                    persist(); st.success(f"\u2705 {nm}!"); st.rerun()

    search = st.text_input("\U0001f50d Hae...", key="ls")
    names = sorted(st.session_state.songs.keys())
    if search: names = [n for n in names if search.lower() in n.lower()]

    for name in names:
        song = st.session_state.songs[name]
        ins = any(e.get("song")==name for e in st.session_state.current_setlist)
        c1,c2,c3,c4 = st.columns([5,1,1,1])
        with c1:
            st.markdown(f"**{name}** {'\u2705' if ins else ''}")
            st.caption(f"{song.get('artist','')+' \u00b7 ' if song.get('artist') else ''}{len(song['text'].split(chr(10)))} rivi\u00e4")
        with c2:
            if st.button("\U0001f441", key=f"pv_{name}"):
                st.session_state[f"sh_{name}"] = not st.session_state.get(f"sh_{name}",False); st.rerun()
        with c3:
            if not ins and st.button("\u2795", key=f"a_{name}"):
                st.session_state.current_setlist.append({"song":name,"transpose":0,"use_flats":False}); persist(); st.rerun()
        with c4:
            if st.button("\U0001f5d1", key=f"d_{name}"):
                del st.session_state.songs[name]
                st.session_state.current_setlist = [e for e in st.session_state.current_setlist if e.get("song")!=name]
                persist(); st.rerun()
        if st.session_state.get(f"sh_{name}",False):
            with st.expander(f"\U0001f4d6 {name}", expanded=True):
                render_song_text(song["text"])


# ═══════════════════════════════════════════
#              SETLIST VIEW
# ═══════════════════════════════════════════
elif st.session_state.view == "setlist":
    cb, ct, cs = st.columns([1,2,1])
    with cb:
        if st.button("\u2190 Etusivu"):
            st.session_state.view = "home"; st.rerun()
    with ct:
        sn = st.text_input("Nimi", value=st.session_state.current_setlist_name or "Keikkasetti", label_visibility="collapsed", key="sln")
        st.session_state.current_setlist_name = sn
    with cs:
        if st.button("\U0001f4be Tallenna", type="primary"):
            if sn: st.session_state.setlists[sn] = list(st.session_state.current_setlist); persist(); st.success(f"'{sn}' tallennettu!")

    st.caption(f"{len(st.session_state.current_setlist)} biisi\u00e4")

    if st.session_state.current_setlist:
        if st.button("\u25b6  K E I K K A M O O D I", type="primary", use_container_width=True):
            st.session_state.view = "perform"; st.session_state.perform_song_idx = 0; st.rerun()

    st.markdown("---")
    if not st.session_state.current_setlist:
        st.info("Setti tyhj\u00e4. Lataa biisilista etusivulta.")

    to_rm = []
    order = list(st.session_state.current_setlist)

    for idx, entry in enumerate(st.session_state.current_setlist):
        sname = entry.get("song","")
        sdata = st.session_state.songs.get(sname)
        if not sdata: continue

        cn,cna,cu,cd,cr = st.columns([1,5,1,1,1])
        with cn: st.markdown(f'<span class="setlist-num">{idx+1}</span>', unsafe_allow_html=True)
        with cna:
            st.markdown(f"**{sname}**")
            if entry.get("transpose",0)!=0:
                tv=entry["transpose"]; st.markdown(f'<span class="transpose-badge">{("+" if tv>0 else "")}{tv}</span>', unsafe_allow_html=True)
        with cu:
            if idx>0 and st.button("\u25b2",key=f"u{idx}"):
                order[idx],order[idx-1]=order[idx-1],order[idx]; st.session_state.current_setlist=order; persist(); st.rerun()
        with cd:
            if idx<len(st.session_state.current_setlist)-1 and st.button("\u25bc",key=f"d{idx}"):
                order[idx],order[idx+1]=order[idx+1],order[idx]; st.session_state.current_setlist=order; persist(); st.rerun()
        with cr:
            if st.button("\u2715",key=f"r{idx}"): to_rm.append(idx)

        c1,c2,c3,c4,c5 = st.columns([2,1,1,1,2])
        with c1: st.caption("Transponointi:")
        with c2:
            if st.button("\u2212",key=f"tm{idx}"):
                st.session_state.current_setlist[idx]["transpose"]=entry.get("transpose",0)-1; persist(); st.rerun()
        with c3:
            tv=entry.get("transpose",0); st.markdown(f"**{'+' if tv>0 else ''}{tv}**")
        with c4:
            if st.button("+",key=f"tp{idx}"):
                st.session_state.current_setlist[idx]["transpose"]=entry.get("transpose",0)+1; persist(); st.rerun()
        with c5:
            uf=st.checkbox("\u266d",value=entry.get("use_flats",False),key=f"fl{idx}")
            if uf!=entry.get("use_flats",False): st.session_state.current_setlist[idx]["use_flats"]=uf; persist()

        with st.expander("\U0001f441 Esikatsele / \u270f\ufe0f Muokkaa"):
            render_song_text(sdata["text"], entry.get("transpose",0), entry.get("use_flats",False))
            nt = st.text_area("Muokkaa:", value=sdata["text"], height=200, key=f"ed{idx}")
            if nt != sdata["text"] and st.button("\U0001f4be Tallenna",key=f"se{idx}"):
                st.session_state.songs[sname]["text"]=nt; persist(); st.success("OK!"); st.rerun()
        st.markdown("---")

    if to_rm:
        for i in sorted(to_rm, reverse=True): st.session_state.current_setlist.pop(i)
        persist(); st.rerun()


# ═══════════════════════════════════════════
#             PERFORM VIEW
# ═══════════════════════════════════════════
elif st.session_state.view == "perform":
    sl = st.session_state.current_setlist
    if not sl:
        st.warning("Tyhj\u00e4!")
        if st.button("\u2190"): st.session_state.view="setlist"; st.rerun()
        st.stop()

    idx = max(0,min(st.session_state.perform_song_idx,len(sl)-1))
    st.session_state.perform_song_idx = idx
    entry = sl[idx]
    sname = entry.get("song","")
    sdata = st.session_state.songs.get(sname)

    c1,c2,c3 = st.columns([1,3,1])
    with c1:
        if st.button("\u2190 Takaisin"): st.session_state.view="setlist"; st.rerun()
    with c2:
        st.markdown(f'<div class="nav-info">{idx+1} / {len(sl)}</div>', unsafe_allow_html=True)

    st.markdown(f'<div class="perform-title">{sname}</div>', unsafe_allow_html=True)
    if entry.get("transpose",0)!=0:
        tv=entry["transpose"]; st.markdown(f'<span class="transpose-badge">Transponoitu {("+" if tv>0 else "")}{tv}</span>', unsafe_allow_html=True)
    ar = st.session_state.songs.get(sname,{}).get("artist","")
    if ar: st.caption(ar)

    st.markdown("---")
    if sdata: render_song_text(sdata["text"],entry.get("transpose",0),entry.get("use_flats",False))
    else: st.error(f"'{sname}' ei l\u00f6ydy!")

    st.markdown("---")
    cp,cm,cn = st.columns([1,2,1])
    with cp:
        if st.button("\u25c0 Edellinen",disabled=idx==0,use_container_width=True):
            st.session_state.perform_song_idx=idx-1; st.rerun()
    with cm:
        st.markdown('<div class="nav-info">Vierit\u00e4 yl\u00f6s/alas</div>', unsafe_allow_html=True)
    with cn:
        if st.button("Seuraava \u25b6",disabled=idx>=len(sl)-1,use_container_width=True,type="primary"):
            st.session_state.perform_song_idx=idx+1; st.rerun()

    st.markdown("---")
    st.markdown("##### \U0001f4cb Setti:")
    for i, e in enumerate(sl):
        if st.button(f"{'▶ ' if i==idx else '   '}{i+1}. {e.get('song','?')}", key=f"j{i}", use_container_width=True, type="primary" if i==idx else "secondary"):
            st.session_state.perform_song_idx=i; st.rerun()
