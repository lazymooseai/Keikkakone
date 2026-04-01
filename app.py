"""
KEIKKAKONE - Keikkasetin hallinta muusikoille
Streamlit Cloud + GitHub deployment
"""

import streamlit as st
import json
import re
import os
from datetime import datetime

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


def extract_text_from_pdf(uploaded_file) -> str:
    """Extract text from uploaded PDF file."""
    if PDF_ENGINE == "pdfplumber":
        with pdfplumber.open(uploaded_file) as pdf:
            pages = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
            return "\n\n".join(pages)
    elif PDF_ENGINE == "pypdf2":
        reader = PdfReader(uploaded_file)
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)
    else:
        return "[PDF-kirjastoa ei löydy. Asenna pdfplumber tai PyPDF2.]"


# ─── Chord Transposition Engine ───
NOTES_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NOTES_FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

CHORD_PATTERN = re.compile(
    r'\b([A-G])(#|b|♯|♭)?'
    r'(m|min|maj|dim|aug|sus|add|dom)?'
    r'(2|4|5|6|7|9|11|13)?'
    r'((?:add|b|#|no|sus|maj|min|dim|aug|alt|\d)*)'
    r'(/([A-G])(#|b|♯|♭)?)?'
    r'(?=\s|$|[)\]\-|:])'
)


def note_to_index(root: str, acc: str = "") -> int:
    acc = acc.replace("♯", "#").replace("♭", "b") if acc else ""
    name = root + acc
    if name in NOTES_SHARP:
        return NOTES_SHARP.index(name)
    if name in NOTES_FLAT:
        return NOTES_FLAT.index(name)
    special = {"Cb": 11, "Fb": 4, "E#": 5, "B#": 0}
    return special.get(name, -1)


def index_to_note(idx: int, use_flats: bool = False) -> str:
    i = idx % 12
    return NOTES_FLAT[i] if use_flats else NOTES_SHARP[i]


def transpose_chord_match(match, semitones: int, use_flats: bool):
    root, acc, qual, ext, mods, slash, bass_root, bass_acc = match.groups()
    idx = note_to_index(root, acc or "")
    if idx == -1:
        return match.group(0)
    new_root = index_to_note(idx + semitones, use_flats)
    result = new_root + (qual or "") + (ext or "") + (mods or "")
    if bass_root:
        bass_idx = note_to_index(bass_root, bass_acc or "")
        if bass_idx != -1:
            result += "/" + index_to_note(bass_idx + semitones, use_flats)
    return result


def is_chord_line(line: str) -> bool:
    trimmed = line.strip()
    if not trimmed:
        return False
    without_chords = CHORD_PATTERN.sub("", trimmed)
    clean = re.sub(r'[\s|/\-()\[\]:.,]', '', without_chords)
    matches = CHORD_PATTERN.findall(trimmed)
    return len(matches) > 0 and len(clean) < len(trimmed) * 0.4


def transpose_text(text: str, semitones: int, use_flats: bool = False) -> str:
    if semitones == 0:
        return text
    lines = text.split("\n")
    result = []
    for line in lines:
        if is_chord_line(line):
            new_line = CHORD_PATTERN.sub(
                lambda m: transpose_chord_match(m, semitones, use_flats),
                line
            )
            result.append(new_line)
        else:
            result.append(line)
    return "\n".join(result)


# ─── Persistent Storage (JSON file) ───
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DATA_FILE = os.path.join(DATA_DIR, "keikkakone_data.json")


def load_data() -> dict:
    """Load persistent data from JSON file."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"songs": {}, "setlists": {}, "current_setlist": None}


def save_data(data: dict):
    """Save data to JSON file."""
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except IOError as e:
        st.error(f"Tallennusvirhe: {e}")


# ─── Initialize Session State ───
def init_state():
    if "initialized" not in st.session_state:
        data = load_data()
        st.session_state.songs = data.get("songs", {})
        st.session_state.setlists = data.get("setlists", {})
        st.session_state.current_setlist_name = data.get("current_setlist", None)
        st.session_state.current_setlist = []
        if st.session_state.current_setlist_name and st.session_state.current_setlist_name in st.session_state.setlists:
            st.session_state.current_setlist = st.session_state.setlists[st.session_state.current_setlist_name]
        st.session_state.view = "library"
        st.session_state.perform_song_idx = 0
        st.session_state.initialized = True


def persist():
    """Save current state to disk."""
    data = {
        "songs": st.session_state.songs,
        "setlists": st.session_state.setlists,
        "current_setlist": st.session_state.current_setlist_name,
    }
    save_data(data)


# ─── Page Config ───
st.set_page_config(
    page_title="KEIKKAKONE ♪",
    page_icon="🎵",
    layout="wide",
    initial_sidebar_state="collapsed",
)

init_state()


# ─── Theme CSS ───
def apply_theme(dark: bool = True):
    if dark:
        css = """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700;800&display=swap');
        
        .stApp { background-color: #0a0a0f !important; }
        
        .stApp, .stApp * { 
            font-family: 'JetBrains Mono', monospace !important; 
            color: #e8e8f0;
        }
        
        .stMarkdown h1 { color: #ff6b35 !important; letter-spacing: 2px; }
        .stMarkdown h2 { color: #ff6b35 !important; letter-spacing: 1px; }
        .stMarkdown h3 { color: #ffb347 !important; }
        
        div[data-testid="stSidebar"] { background-color: #0f0f18 !important; }
        
        .song-card {
            background: #14141f;
            border: 1px solid #2a2a3e;
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 10px;
        }
        
        .setlist-item {
            background: #14141f;
            border: 1px solid #2a2a3e;
            border-left: 4px solid #ff6b35;
            border-radius: 10px;
            padding: 14px;
            margin-bottom: 8px;
        }
        
        .chord-line { color: #ffb347 !important; font-weight: 700; font-size: 1.2em; }
        .lyric-line { color: #e8e8f0; font-size: 1.05em; }
        
        .perform-title {
            color: #ff6b35;
            font-size: 1.8em;
            font-weight: 800;
            letter-spacing: 1px;
            margin-bottom: 4px;
        }
        
        .song-number {
            color: #ff6b35;
            font-weight: 800;
            font-size: 1.3em;
            min-width: 32px;
            display: inline-block;
        }
        
        .transpose-badge {
            background: rgba(255,179,71,0.15);
            color: #ffb347;
            padding: 2px 10px;
            border-radius: 6px;
            font-size: 0.85em;
            font-weight: 600;
        }
        
        div[data-testid="stExpander"] { 
            background: #14141f !important;
            border: 1px solid #2a2a3e !important;
            border-radius: 10px !important;
        }

        section[data-testid="stSidebar"] .stButton button {
            width: 100%;
        }

        .stButton button {
            font-family: 'JetBrains Mono', monospace !important;
            font-weight: 600 !important;
        }

        .big-perform-btn button {
            background: linear-gradient(135deg, #ff6b35, #ff6b35cc) !important;
            color: white !important;
            font-size: 1.2em !important;
            font-weight: 800 !important;
            letter-spacing: 2px !important;
            padding: 12px !important;
            border-radius: 12px !important;
            border: none !important;
            width: 100% !important;
        }
        
        .nav-info {
            background: #14141f;
            border: 1px solid #2a2a3e;
            border-radius: 8px;
            padding: 10px 16px;
            text-align: center;
            color: #8888a0;
            font-size: 0.85em;
        }
        </style>
        """
    else:
        css = """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700;800&display=swap');
        
        .stApp { background-color: #f5f0e8 !important; }
        
        .stApp, .stApp * { 
            font-family: 'JetBrains Mono', monospace !important; 
            color: #1a1a2e;
        }
        
        .stMarkdown h1 { color: #d35400 !important; letter-spacing: 2px; }
        .stMarkdown h2 { color: #d35400 !important; letter-spacing: 1px; }
        .stMarkdown h3 { color: #8b4513 !important; }
        
        div[data-testid="stSidebar"] { background-color: #ede8dc !important; }
        
        .song-card {
            background: #ffffff;
            border: 1px solid #d4cfc0;
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 10px;
        }
        
        .setlist-item {
            background: #ffffff;
            border: 1px solid #d4cfc0;
            border-left: 4px solid #d35400;
            border-radius: 10px;
            padding: 14px;
            margin-bottom: 8px;
        }
        
        .chord-line { color: #8b4513 !important; font-weight: 700; font-size: 1.2em; }
        .lyric-line { color: #1a1a2e; font-size: 1.05em; }
        
        .perform-title { color: #d35400; font-size: 1.8em; font-weight: 800; }
        .song-number { color: #d35400; font-weight: 800; font-size: 1.3em; }
        .transpose-badge { background: rgba(139,69,19,0.1); color: #8b4513; padding: 2px 10px; border-radius: 6px; font-size: 0.85em; }
        
        div[data-testid="stExpander"] { 
            background: #ffffff !important;
            border: 1px solid #d4cfc0 !important;
            border-radius: 10px !important;
        }

        .big-perform-btn button {
            background: linear-gradient(135deg, #d35400, #d35400cc) !important;
            color: white !important;
            font-size: 1.2em !important;
            font-weight: 800 !important;
            letter-spacing: 2px !important;
            padding: 12px !important;
            border-radius: 12px !important;
            border: none !important;
            width: 100% !important;
        }
        
        .nav-info {
            background: #ffffff;
            border: 1px solid #d4cfc0;
            border-radius: 8px;
            padding: 10px 16px;
            text-align: center;
            color: #666650;
            font-size: 0.85em;
        }
        </style>
        """
    st.markdown(css, unsafe_allow_html=True)


# ─── Sidebar: Settings & Theme ───
with st.sidebar:
    st.markdown("## ⚙️ Asetukset")
    
    dark_mode = st.toggle("🌙 Tumma teema", value=True, key="dark_mode")
    apply_theme(dark_mode)
    
    st.markdown("---")
    
    st.markdown("### 📂 Navigointi")
    if st.button("📚 Biisikirjasto", use_container_width=True):
        st.session_state.view = "library"
        st.rerun()
    if st.button("📋 Settilista", use_container_width=True):
        st.session_state.view = "setlist"
        st.rerun()
    if st.button("🎤 Keikkamoodi", use_container_width=True, disabled=len(st.session_state.current_setlist) == 0):
        st.session_state.view = "perform"
        st.session_state.perform_song_idx = 0
        st.rerun()
    
    st.markdown("---")
    st.markdown("### 💾 Tallennetut setit")
    
    if st.session_state.setlists:
        for name in st.session_state.setlists:
            col1, col2 = st.columns([3, 1])
            with col1:
                if st.button(f"📋 {name}", key=f"load_{name}", use_container_width=True):
                    st.session_state.current_setlist = list(st.session_state.setlists[name])
                    st.session_state.current_setlist_name = name
                    st.session_state.view = "setlist"
                    persist()
                    st.rerun()
            with col2:
                if st.button("🗑", key=f"del_setlist_{name}"):
                    del st.session_state.setlists[name]
                    if st.session_state.current_setlist_name == name:
                        st.session_state.current_setlist_name = None
                    persist()
                    st.rerun()
    else:
        st.caption("Ei tallennettuja settejä")
    
    st.markdown("---")
    st.markdown(
        "<div style='text-align:center; font-size:0.75em; color:#666;'>"
        "KEIKKAKONE v1.0<br>🎵 Muusikoille, muusikoilta</div>",
        unsafe_allow_html=True
    )


# ─── Render song text with chord highlighting ───
def render_song_text(text: str, transpose: int = 0, use_flats: bool = False):
    """Render song text with chord lines highlighted."""
    transposed = transpose_text(text, transpose, use_flats)
    lines = transposed.split("\n")
    html_parts = []
    for line in lines:
        escaped = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if not escaped.strip():
            html_parts.append("<br>")
        elif is_chord_line(line):
            html_parts.append(f'<div class="chord-line">{escaped}</div>')
        else:
            html_parts.append(f'<div class="lyric-line">{escaped}</div>')
    
    html = f"""
    <div style="font-family: 'JetBrains Mono', monospace; line-height: 1.7; white-space: pre-wrap;">
    {"".join(html_parts)}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


# ═══════════════════════════════════════════
#                  VIEWS
# ═══════════════════════════════════════════

# ─── LIBRARY VIEW ───
if st.session_state.view == "library":
    col_title, col_nav = st.columns([3, 1])
    with col_title:
        st.markdown("# ♪ KEIKKAKONE")
        st.caption(f"BIISIKIRJASTO · {len(st.session_state.songs)} biisiä")
    with col_nav:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("📋 SETTI →", use_container_width=True, type="primary"):
            st.session_state.view = "setlist"
            st.rerun()

    # Upload section
    st.markdown("### 📥 Lisää biisejä")
    
    tab_pdf, tab_manual = st.tabs(["📄 Lataa PDF", "✏️ Kirjoita käsin"])
    
    with tab_pdf:
        uploaded_files = st.file_uploader(
            "Valitse PDF-sointulaput",
            type=["pdf"],
            accept_multiple_files=True,
            key="pdf_uploader"
        )
        if uploaded_files:
            for uf in uploaded_files:
                name = uf.name.replace(".pdf", "").replace(".PDF", "")
                if name not in st.session_state.songs:
                    text = extract_text_from_pdf(uf)
                    if text.strip():
                        st.session_state.songs[name] = {
                            "text": text,
                            "added": datetime.now().isoformat()
                        }
                        st.success(f"✅ {name} lisätty!")
                    else:
                        st.warning(f"⚠️ {name}: PDF:stä ei löytynyt tekstiä")
                else:
                    st.info(f"ℹ️ {name} on jo kirjastossa")
            persist()
    
    with tab_manual:
        with st.form("manual_song"):
            song_name = st.text_input("Biisin nimi")
            song_text = st.text_area(
                "Sointulappu",
                height=250,
                placeholder="Am        G         F         C\nSanat tähän riville...\n\n[Kertosäe]\nF         G         Am\nKertosäkeen sanat..."
            )
            if st.form_submit_button("💾 Tallenna biisi"):
                if song_name and song_text:
                    st.session_state.songs[song_name] = {
                        "text": song_text,
                        "added": datetime.now().isoformat()
                    }
                    persist()
                    st.success(f"✅ {song_name} tallennettu!")
                    st.rerun()
                else:
                    st.warning("Anna biisin nimi ja sointulappu")

    # Song library
    st.markdown("---")
    st.markdown("### 📚 Kirjasto")
    
    search = st.text_input("🔍 Hae biisejä...", key="lib_search")
    
    song_names = sorted(st.session_state.songs.keys())
    if search:
        song_names = [n for n in song_names if search.lower() in n.lower()]
    
    if not song_names:
        st.info("Ei biisejä. Lataa PDF tai luo uusi biisi yllä.")
    
    for name in song_names:
        song = st.session_state.songs[name]
        in_setlist = any(e.get("song") == name for e in st.session_state.current_setlist)
        
        with st.container():
            st.markdown(f'<div class="song-card">', unsafe_allow_html=True)
            col1, col2, col3, col4 = st.columns([4, 1, 1, 1])
            
            with col1:
                status = "✅" if in_setlist else ""
                lines = song["text"].split("\n")
                st.markdown(f"**{name}** {status}")
                st.caption(f"{len(lines)} riviä")
            
            with col2:
                if st.button("👁", key=f"preview_{name}", help="Esikatsele"):
                    st.session_state[f"show_preview_{name}"] = not st.session_state.get(f"show_preview_{name}", False)
                    st.rerun()
            
            with col3:
                if not in_setlist:
                    if st.button("➕", key=f"add_{name}", help="Lisää settiin"):
                        st.session_state.current_setlist.append({
                            "song": name,
                            "transpose": 0,
                            "use_flats": False
                        })
                        persist()
                        st.rerun()
                else:
                    st.markdown("✓")
            
            with col4:
                if st.button("🗑", key=f"del_{name}", help="Poista"):
                    del st.session_state.songs[name]
                    st.session_state.current_setlist = [
                        e for e in st.session_state.current_setlist if e.get("song") != name
                    ]
                    persist()
                    st.rerun()
            
            st.markdown('</div>', unsafe_allow_html=True)
            
            # Preview
            if st.session_state.get(f"show_preview_{name}", False):
                with st.expander(f"📖 {name}", expanded=True):
                    render_song_text(song["text"])


# ─── SETLIST VIEW ───
elif st.session_state.view == "setlist":
    col_back, col_title, col_save = st.columns([1, 2, 1])
    
    with col_back:
        if st.button("← Kirjasto"):
            st.session_state.view = "library"
            st.rerun()
    
    with col_title:
        setlist_name = st.text_input(
            "Setin nimi",
            value=st.session_state.current_setlist_name or "Keikkasetti",
            key="setlist_name_input",
            label_visibility="collapsed"
        )
        st.session_state.current_setlist_name = setlist_name
    
    with col_save:
        if st.button("💾 Tallenna", type="primary"):
            if setlist_name:
                st.session_state.setlists[setlist_name] = list(st.session_state.current_setlist)
                persist()
                st.success(f"Setti '{setlist_name}' tallennettu!")
    
    st.caption(f"{len(st.session_state.current_setlist)} biisiä setissä")
    
    # Perform button
    if st.session_state.current_setlist:
        st.markdown('<div class="big-perform-btn">', unsafe_allow_html=True)
        if st.button("▶ KEIKKAMOODI", use_container_width=True, type="primary"):
            st.session_state.view = "perform"
            st.session_state.perform_song_idx = 0
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("---")
    
    if not st.session_state.current_setlist:
        st.info("Setti on tyhjä. Lisää biisejä kirjastosta.")
    
    # Setlist items
    items_to_remove = []
    new_order = list(st.session_state.current_setlist)
    
    for idx, entry in enumerate(st.session_state.current_setlist):
        song_name = entry.get("song", "")
        song_data = st.session_state.songs.get(song_name)
        
        if not song_data:
            continue
        
        st.markdown(f'<div class="setlist-item">', unsafe_allow_html=True)
        
        # Header row
        col_num, col_name, col_up, col_down, col_remove = st.columns([1, 5, 1, 1, 1])
        
        with col_num:
            st.markdown(f'<span class="song-number">{idx + 1}</span>', unsafe_allow_html=True)
        
        with col_name:
            st.markdown(f"**{song_name}**")
            if entry.get("transpose", 0) != 0:
                t_val = entry["transpose"]
                st.markdown(
                    f'<span class="transpose-badge">{("+" if t_val > 0 else "")}{t_val}</span>',
                    unsafe_allow_html=True
                )
        
        with col_up:
            if idx > 0:
                if st.button("▲", key=f"up_{idx}"):
                    new_order[idx], new_order[idx - 1] = new_order[idx - 1], new_order[idx]
                    st.session_state.current_setlist = new_order
                    persist()
                    st.rerun()
        
        with col_down:
            if idx < len(st.session_state.current_setlist) - 1:
                if st.button("▼", key=f"down_{idx}"):
                    new_order[idx], new_order[idx + 1] = new_order[idx + 1], new_order[idx]
                    st.session_state.current_setlist = new_order
                    persist()
                    st.rerun()
        
        with col_remove:
            if st.button("✕", key=f"rm_{idx}"):
                items_to_remove.append(idx)
        
        # Transpose controls
        col_label, col_minus, col_val, col_plus, col_flats = st.columns([2, 1, 1, 1, 2])
        
        with col_label:
            st.caption("Transponointi:")
        with col_minus:
            if st.button("−", key=f"tminus_{idx}"):
                st.session_state.current_setlist[idx]["transpose"] = entry.get("transpose", 0) - 1
                persist()
                st.rerun()
        with col_val:
            t = entry.get("transpose", 0)
            st.markdown(f"**{'+' if t > 0 else ''}{t}**")
        with col_plus:
            if st.button("+", key=f"tplus_{idx}"):
                st.session_state.current_setlist[idx]["transpose"] = entry.get("transpose", 0) + 1
                persist()
                st.rerun()
        with col_flats:
            use_flats = st.checkbox(
                "♭ Alennetut",
                value=entry.get("use_flats", False),
                key=f"flats_{idx}"
            )
            if use_flats != entry.get("use_flats", False):
                st.session_state.current_setlist[idx]["use_flats"] = use_flats
                persist()
        
        # Preview expander
        with st.expander("👁 Esikatsele"):
            render_song_text(
                song_data["text"],
                entry.get("transpose", 0),
                entry.get("use_flats", False)
            )
        
        st.markdown('</div>', unsafe_allow_html=True)
    
    # Process removals
    if items_to_remove:
        for idx in sorted(items_to_remove, reverse=True):
            st.session_state.current_setlist.pop(idx)
        persist()
        st.rerun()


# ─── PERFORM VIEW ───
elif st.session_state.view == "perform":
    setlist = st.session_state.current_setlist
    
    if not setlist:
        st.warning("Setti on tyhjä!")
        if st.button("← Takaisin"):
            st.session_state.view = "setlist"
            st.rerun()
    else:
        idx = st.session_state.perform_song_idx
        idx = max(0, min(idx, len(setlist) - 1))
        st.session_state.perform_song_idx = idx
        
        entry = setlist[idx]
        song_name = entry.get("song", "")
        song_data = st.session_state.songs.get(song_name)
        
        # Top navigation
        col_back, col_info, col_theme = st.columns([1, 3, 1])
        
        with col_back:
            if st.button("← Takaisin"):
                st.session_state.view = "setlist"
                st.rerun()
        
        with col_info:
            st.markdown(
                f'<div class="nav-info">{idx + 1} / {len(setlist)}</div>',
                unsafe_allow_html=True
            )
        
        # Song title
        st.markdown(f'<div class="perform-title">{song_name}</div>', unsafe_allow_html=True)
        
        if entry.get("transpose", 0) != 0:
            t = entry["transpose"]
            st.markdown(
                f'<span class="transpose-badge">Transponoitu {("+" if t > 0 else "")}{t}</span>',
                unsafe_allow_html=True
            )
        
        st.markdown("---")
        
        # Song content
        if song_data:
            render_song_text(
                song_data["text"],
                entry.get("transpose", 0),
                entry.get("use_flats", False)
            )
        else:
            st.error(f"Biisiä '{song_name}' ei löydy kirjastosta!")
        
        # Bottom navigation
        st.markdown("---")
        
        col_prev, col_scroll, col_next = st.columns([1, 2, 1])
        
        with col_prev:
            if st.button("◀ Edellinen", disabled=idx == 0, use_container_width=True):
                st.session_state.perform_song_idx = idx - 1
                st.rerun()
        
        with col_scroll:
            st.markdown(
                '<div style="text-align:center; font-size:0.8em; color:#888; padding:8px;">'
                'Vieritä ylös/alas nähdäksesi koko biisin</div>',
                unsafe_allow_html=True
            )
        
        with col_next:
            if st.button("Seuraava ▶", disabled=idx >= len(setlist) - 1, use_container_width=True, type="primary"):
                st.session_state.perform_song_idx = idx + 1
                st.rerun()
        
        # Quick song list
        st.markdown("---")
        st.markdown("##### 📋 Koko setti:")
        for i, e in enumerate(setlist):
            is_current = i == idx
            prefix = "▶ " if is_current else "  "
            style = "font-weight:700; color:#ff6b35;" if is_current else "color:#8888a0;"
            if st.button(
                f"{i + 1}. {e.get('song', '?')}",
                key=f"jump_{i}",
                use_container_width=True
            ):
                st.session_state.perform_song_idx = i
                st.rerun()
