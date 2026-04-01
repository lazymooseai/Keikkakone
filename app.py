"""
KEIKKAKONE v3 — Keikkasetti muusikoille
Lataa biisilista → lataa PDF-laput → näe setti → napauta → lue lappu
"""

import streamlit as st
import json, re, os, io, logging
from datetime import datetime
from difflib import SequenceMatcher

# Suppress pdfplumber/pdfminer FontBBox warnings
logging.getLogger("pdfminer").setLevel(logging.ERROR)

try:
    import pdfplumber
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

# ──────────────────────────────────────────
#  PDF helpers
# ──────────────────────────────────────────

def pdf_to_text(file_bytes: bytes) -> str:
    if not HAS_PDF:
        return ""
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            return "\n\n".join(
                p.extract_text() or "" for p in pdf.pages
            ).strip()
    except Exception:
        return ""


# ──────────────────────────────────────────
#  Setlist parser
# ──────────────────────────────────────────

def parse_setlist(text: str) -> list[dict]:
    songs = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cleaned = re.sub(r'^\d+[\.\)\:\-]\s*', '', line).strip()
        if not cleaned:
            continue
        title, artist = cleaned, ""
        if " - " in cleaned:
            parts = cleaned.split(" - ", 1)
            title, artist = parts[0].strip(), parts[1].strip()
        elif " / " in cleaned:
            parts = cleaned.split(" / ", 1)
            title, artist = parts[0].strip(), parts[1].strip()
        songs.append({"title": title, "artist": artist})
    return songs


# ──────────────────────────────────────────
#  Fuzzy matching
# ──────────────────────────────────────────

def _norm(t: str) -> str:
    t = t.lower()
    t = re.sub(r'\.pdf$', '', t)
    for s in ['_chords','_soinnut','_chord','_tabs','_tab','_sheet',
              '_nuotti','_lappu',' chords',' soinnut',' chord',
              ' tabs',' sheet',' nuotti',' lappu','(chords)','(soinnut)']:
        t = t.replace(s, '')
    t = re.sub(r'[_\-\.\(\)\[\]]', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def _score(query: str, candidate: str) -> float:
    q, c = _norm(query), _norm(candidate)
    if q == c: return 1.0
    if q in c or c in q: return 0.9
    qw, cw = set(q.split()), set(c.split())
    if qw and qw.issubset(cw): return 0.85
    if qw and cw:
        ov = len(qw & cw) / max(len(qw), len(cw))
        if ov > 0.5: return 0.7 + ov * 0.2
    return SequenceMatcher(None, q, c).ratio()


def match_songs_to_pdfs(songs: list[dict], pdfs: list[dict]) -> list[dict]:
    """Match song list to PDF files. Returns list with 'match' key added."""
    pool = list(pdfs)
    results = []
    for song in songs:
        best, best_sc = None, 0
        for pdf in pool:
            sc = _score(song["title"], pdf["name"])
            if song["artist"]:
                sc = max(sc,
                    _score(f"{song['title']} {song['artist']}", pdf["name"]),
                    _score(f"{song['artist']} {song['title']}", pdf["name"])
                )
            if sc > best_sc:
                best_sc, best = sc, pdf
        if best and best_sc >= 0.4:
            pool = [p for p in pool if p["name"] != best["name"]]
            results.append({**song, "match": best, "score": best_sc})
        else:
            results.append({**song, "match": None, "score": 0})
    return results


# ──────────────────────────────────────────
#  Chord transposition
# ──────────────────────────────────────────

_SHARP = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
_FLAT  = ["C","Db","D","Eb","E","F","Gb","G","Ab","A","Bb","B"]

_CHORD_RE = re.compile(
    r'\b([A-G])(#|b)?'
    r'(m|min|maj|dim|aug|sus|add|dom)?'
    r'(2|4|5|6|7|9|11|13)?'
    r'((?:add|b|#|no|sus|maj|min|dim|aug|alt|\d)*)'
    r'(/([A-G])(#|b)?)?'
    r'(?=\s|$|[)\]\-|:])'
)

def _ni(r, a=""):
    n = r + (a or "")
    if n in _SHARP: return _SHARP.index(n)
    if n in _FLAT: return _FLAT.index(n)
    return {"Cb":11,"Fb":4,"E#":5,"B#":0}.get(n, -1)

def _in(i, flat=False):
    return (_FLAT if flat else _SHARP)[i % 12]

def _tc(m, semi, flat):
    r, a, q, e, mo, sl, br, ba = m.groups()
    i = _ni(r, a or "")
    if i == -1: return m.group(0)
    res = _in(i+semi, flat) + (q or "") + (e or "") + (mo or "")
    if br:
        bi = _ni(br, ba or "")
        if bi != -1: res += "/" + _in(bi+semi, flat)
    return res

def is_chord_line(line):
    t = line.strip()
    if not t: return False
    c = re.sub(r'[\s|/\-()\[\]:.,]', '', _CHORD_RE.sub("", t))
    return bool(_CHORD_RE.findall(t)) and len(c) < len(t) * 0.4

def transpose(text, semi, flat=False):
    if semi == 0: return text
    return "\n".join(
        _CHORD_RE.sub(lambda m: _tc(m, semi, flat), ln) if is_chord_line(ln) else ln
        for ln in text.split("\n")
    )


# ──────────────────────────────────────────
#  State & persistence
# ──────────────────────────────────────────

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_FILE = os.path.join(_DIR, "kk.json")

def _load():
    if os.path.exists(_FILE):
        try:
            with open(_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {}

def _save(d):
    os.makedirs(_DIR, exist_ok=True)
    with open(_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)

def _persist():
    _save({
        "setlist": st.session_state.get("setlist", []),
        "song_texts": st.session_state.get("song_texts", {}),
        "song_pdfs": st.session_state.get("song_pdfs", {}),
        "transpose_settings": st.session_state.get("transpose_settings", {}),
    })


# ──────────────────────────────────────────
#  Init
# ──────────────────────────────────────────

st.set_page_config(
    page_title="KEIKKAKONE",
    page_icon="♪",
    layout="centered",
    initial_sidebar_state="collapsed",
)

if "init" not in st.session_state:
    d = _load()
    st.session_state.setlist = d.get("setlist", [])
    st.session_state.song_texts = d.get("song_texts", {})
    st.session_state.song_pdfs = d.get("song_pdfs", {})  # title -> base64 pdf bytes won't work, store text
    st.session_state.transpose_settings = d.get("transpose_settings", {})
    st.session_state.open_song = None
    st.session_state.init = True


# ──────────────────────────────────────────
#  Theme
# ──────────────────────────────────────────

dark = st.sidebar.toggle("Tumma teema", value=True, key="dark")

if dark:
    BG = "#08080c"
    CARD = "#111118"
    CARD_HOVER = "#1a1a24"
    BORDER = "#222233"
    TEXT = "#e4e4ec"
    DIM = "#66667a"
    ACCENT = "#e8572a"
    CHORD_COLOR = "#f0a830"
    CHORD_BG = "rgba(240,168,48,0.08)"
else:
    BG = "#f7f5f0"
    CARD = "#ffffff"
    CARD_HOVER = "#faf8f4"
    BORDER = "#e0ddd4"
    TEXT = "#1a1a24"
    DIM = "#777768"
    ACCENT = "#c04420"
    CHORD_COLOR = "#7a4012"
    CHORD_BG = "rgba(122,64,18,0.06)"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600;700&display=swap');

.stApp {{
    background: {BG} !important;
}}

.stApp, .stApp p, .stApp span, .stApp div, .stApp label {{
    color: {TEXT};
}}

/* ── Header ── */
.kk-header {{
    text-align: center;
    padding: 2rem 0 0.5rem;
    user-select: none;
}}
.kk-header h1 {{
    font-family: 'DM Mono', monospace;
    font-size: 1.6rem;
    font-weight: 500;
    color: {ACCENT};
    letter-spacing: 4px;
    margin: 0;
}}
.kk-header .sub {{
    font-family: 'DM Sans', sans-serif;
    font-size: 0.8rem;
    color: {DIM};
    letter-spacing: 2px;
    margin-top: 4px;
}}

/* ── Song card ── */
.song-card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 14px;
    padding: 18px 22px;
    margin-bottom: 10px;
    cursor: pointer;
    transition: all 0.15s ease;
    display: flex;
    align-items: center;
    gap: 16px;
    user-select: none;
}}
.song-card:hover {{
    background: {CARD_HOVER};
    border-color: {ACCENT}44;
    transform: translateY(-1px);
    box-shadow: 0 4px 20px rgba(0,0,0,0.08);
}}
.song-card:active {{
    transform: translateY(0);
}}
.card-num {{
    font-family: 'DM Mono', monospace;
    font-size: 1.4rem;
    font-weight: 500;
    color: {ACCENT};
    min-width: 36px;
    text-align: center;
    flex-shrink: 0;
}}
.card-title {{
    font-family: 'DM Sans', sans-serif;
    font-size: 1.15rem;
    font-weight: 600;
    color: {TEXT};
    line-height: 1.3;
}}
.card-artist {{
    font-family: 'DM Sans', sans-serif;
    font-size: 0.82rem;
    color: {DIM};
    margin-top: 2px;
}}
.card-status {{
    flex-shrink: 0;
    margin-left: auto;
    font-size: 0.75rem;
    color: {DIM};
}}
.card-status.matched {{
    color: #4ade80;
}}
.card-status.missing {{
    color: {DIM};
}}

/* ── PDF viewer ── */
.pdf-viewer {{
    font-family: 'DM Mono', monospace;
    line-height: 1.75;
    white-space: pre-wrap;
    word-break: break-word;
    padding: 1rem 0;
}}
.pdf-viewer .chord {{
    color: {CHORD_COLOR};
    font-weight: 500;
    font-size: 1.1em;
    background: {CHORD_BG};
    padding: 0 2px;
    border-radius: 3px;
}}
.pdf-viewer .lyric {{
    color: {TEXT};
    font-size: 1em;
}}

/* ── Upload zone ── */
.upload-zone {{
    border: 2px dashed {BORDER};
    border-radius: 16px;
    padding: 2rem;
    text-align: center;
    margin: 1rem 0;
}}
.upload-zone .label {{
    font-family: 'DM Sans', sans-serif;
    font-size: 0.9rem;
    color: {DIM};
}}

/* ── Section divider ── */
.section-label {{
    font-family: 'DM Mono', monospace;
    font-size: 0.7rem;
    color: {DIM};
    letter-spacing: 3px;
    text-transform: uppercase;
    padding: 1.5rem 0 0.5rem;
    user-select: none;
}}

/* ── Transpose pill ── */
.transpose-pill {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 20px;
    padding: 4px 6px;
    font-family: 'DM Mono', monospace;
    font-size: 0.85rem;
}}
.transpose-pill .val {{
    color: {CHORD_COLOR};
    font-weight: 500;
    min-width: 28px;
    text-align: center;
}}

/* ── Back button ── */
.back-btn {{
    font-family: 'DM Sans', sans-serif;
    font-size: 0.9rem;
    color: {DIM};
    cursor: pointer;
    padding: 8px 0;
    user-select: none;
}}
.back-btn:hover {{ color: {ACCENT}; }}

/* ── Count badge ── */
.count-badge {{
    font-family: 'DM Mono', monospace;
    font-size: 0.75rem;
    color: {DIM};
    text-align: center;
    padding: 0.5rem 0;
}}

/* ── Match indicator ── */
.match-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 0;
    border-bottom: 1px solid {BORDER}22;
}}

/* ── Hide Streamlit defaults ── */
#MainMenu {{visibility: hidden;}}
footer {{visibility: hidden;}}
header {{visibility: hidden;}}
div[data-testid="stSidebar"] {{
    background: {BG} !important;
}}
div[data-testid="stSidebar"] * {{
    color: {TEXT};
}}
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────
#  Render helpers
# ──────────────────────────────────────────

def render_chord_sheet(text: str, semi: int = 0, flat: bool = False):
    t = transpose(text, semi, flat)
    parts = []
    for line in t.split("\n"):
        esc = line.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        if not esc.strip():
            parts.append("<br>")
        elif is_chord_line(line):
            parts.append(f'<div class="chord">{esc}</div>')
        else:
            parts.append(f'<div class="lyric">{esc}</div>')
    st.markdown(f'<div class="pdf-viewer">{"".join(parts)}</div>', unsafe_allow_html=True)


# ══════════════════════════════════════════
#  MAIN VIEW LOGIC
# ══════════════════════════════════════════

# ── Song detail view ──
if st.session_state.open_song is not None:
    idx = st.session_state.open_song
    setlist = st.session_state.setlist

    if idx >= len(setlist):
        st.session_state.open_song = None
        st.rerun()

    song = setlist[idx]
    title = song["title"]
    artist = song.get("artist", "")
    text = st.session_state.song_texts.get(title, "")
    ts = st.session_state.transpose_settings.get(title, {"semi": 0, "flat": False})

    # Back
    if st.button("← takaisin", key="back"):
        st.session_state.open_song = None
        st.rerun()

    # Title
    st.markdown(f"""
    <div style="padding:0.5rem 0 0;">
        <span class="card-num" style="font-size:1.2rem;">{idx+1}</span>
        <span style="font-family:'DM Sans',sans-serif; font-size:1.5rem; font-weight:700; color:{TEXT}; margin-left:12px;">{title}</span>
    </div>
    """, unsafe_allow_html=True)
    if artist:
        st.markdown(f'<div style="font-family:\'DM Sans\',sans-serif; font-size:0.9rem; color:{DIM}; padding-left:48px;">{artist}</div>', unsafe_allow_html=True)

    # Transpose controls
    st.markdown('<div class="section-label">TRANSPONOINTI</div>', unsafe_allow_html=True)
    tc1, tc2, tc3, tc4, tc5 = st.columns([1, 1, 1, 1, 2])
    with tc1:
        if st.button("−", key="t_down"):
            ts["semi"] = ts.get("semi", 0) - 1
            st.session_state.transpose_settings[title] = ts
            _persist()
            st.rerun()
    with tc2:
        semi = ts.get("semi", 0)
        label = f"+{semi}" if semi > 0 else str(semi)
        st.markdown(f'<div style="text-align:center; font-family:\'DM Mono\',monospace; font-size:1.3rem; color:{CHORD_COLOR}; font-weight:500; padding-top:6px;">{label}</div>', unsafe_allow_html=True)
    with tc3:
        if st.button("+", key="t_up"):
            ts["semi"] = ts.get("semi", 0) + 1
            st.session_state.transpose_settings[title] = ts
            _persist()
            st.rerun()
    with tc4:
        if st.button("♭/♯", key="t_flat"):
            ts["flat"] = not ts.get("flat", False)
            st.session_state.transpose_settings[title] = ts
            _persist()
            st.rerun()
    with tc5:
        if ts.get("semi", 0) != 0:
            if st.button("Nollaa", key="t_reset"):
                ts["semi"] = 0
                st.session_state.transpose_settings[title] = ts
                _persist()
                st.rerun()

    st.markdown("---")

    # Song content
    if text:
        render_chord_sheet(text, ts.get("semi", 0), ts.get("flat", False))
    else:
        st.markdown(f'<div style="text-align:center; color:{DIM}; padding:3rem 0; font-family:\'DM Sans\',sans-serif;">PDF-lappua ei ladattu tälle biisille</div>', unsafe_allow_html=True)

    # Prev / Next
    st.markdown("---")
    cp, cn = st.columns(2)
    with cp:
        if idx > 0:
            prev_title = setlist[idx-1]["title"]
            if st.button(f"← {prev_title}", key="prev", use_container_width=True):
                st.session_state.open_song = idx - 1
                st.rerun()
    with cn:
        if idx < len(setlist) - 1:
            next_title = setlist[idx+1]["title"]
            if st.button(f"{next_title} →", key="next", use_container_width=True):
                st.session_state.open_song = idx + 1
                st.rerun()

    st.stop()


# ══════════════════════════════════════════
#  MAIN: Setlist view
# ══════════════════════════════════════════

# Header
st.markdown("""
<div class="kk-header">
    <h1>KEIKKAKONE</h1>
    <div class="sub">SETTILISTA</div>
</div>
""", unsafe_allow_html=True)

# ── Upload section ──
st.markdown('<div class="section-label">LATAA BIISILISTA</div>', unsafe_allow_html=True)

setlist_file = st.file_uploader(
    "Biisilista (TXT tai PDF)",
    type=["txt", "pdf"],
    key="setlist_file",
    label_visibility="collapsed"
)

if setlist_file:
    if setlist_file.type == "application/pdf":
        raw_bytes = setlist_file.read()
        setlist_file.seek(0)
        raw_text = pdf_to_text(raw_bytes)
    else:
        raw_text = setlist_file.read().decode("utf-8", errors="replace")
    
    parsed = parse_setlist(raw_text)
    if parsed:
        st.session_state._pending_songs = parsed
        st.success(f"{len(parsed)} biisiä luettu")
    else:
        st.warning("Biisejä ei löytynyt. Muoto: `Biisin nimi - Artisti` per rivi.")

# ── PDF upload & matching ──
if st.session_state.get("_pending_songs"):
    songs = st.session_state._pending_songs
    
    st.markdown('<div class="section-label">LATAA PDF-SOINTULAPUT</div>', unsafe_allow_html=True)
    st.caption("Valitse kaikki keikan PDF-laput kerralla.")
    
    pdf_files = st.file_uploader(
        "PDF-laput",
        type=["pdf"],
        accept_multiple_files=True,
        key="pdf_upload",
        label_visibility="collapsed"
    )
    
    if pdf_files:
        available = []
        pdf_bytes_map = {}
        for f in pdf_files:
            b = f.read(); f.seek(0)
            available.append({"name": f.name})
            pdf_bytes_map[f.name] = b
        
        matched = match_songs_to_pdfs(songs, available)
        
        # Show matches
        st.markdown('<div class="section-label">YHDISTÄMINEN</div>', unsafe_allow_html=True)
        
        all_names = ["—"] + [p["name"] for p in available]
        final_matches = []
        
        for i, m in enumerate(matched):
            c1, c2 = st.columns([3, 3])
            with c1:
                st.markdown(f"**{i+1}. {m['title']}**")
                if m.get("artist"):
                    st.caption(m["artist"])
            with c2:
                default = 0
                if m["match"]:
                    try:
                        default = all_names.index(m["match"]["name"])
                    except ValueError:
                        default = 0
                
                sel = st.selectbox(
                    f"PDF #{i+1}",
                    all_names,
                    index=default,
                    key=f"sel_{i}",
                    label_visibility="collapsed"
                )
                final_matches.append(sel)
        
        # Build button
        st.markdown("---")
        matched_count = sum(1 for f in final_matches if f != "—")
        st.markdown(f'<div class="count-badge">{matched_count}/{len(songs)} yhdistetty</div>', unsafe_allow_html=True)
        
        if st.button("RAKENNA SETTI", type="primary", use_container_width=True):
            new_setlist = []
            new_texts = {}
            
            for i, song in enumerate(songs):
                pdf_name = final_matches[i] if i < len(final_matches) else "—"
                title = song["title"]
                new_setlist.append(song)
                
                if pdf_name != "—" and pdf_name in pdf_bytes_map:
                    text = pdf_to_text(pdf_bytes_map[pdf_name])
                    new_texts[title] = text if text.strip() else f"[Tekstiä ei saatu luettua: {pdf_name}]"
                else:
                    new_texts[title] = ""
            
            st.session_state.setlist = new_setlist
            st.session_state.song_texts = new_texts
            st.session_state._pending_songs = None
            _persist()
            st.rerun()

# ── Display current setlist ──
setlist = st.session_state.setlist

if setlist:
    st.markdown("---")
    st.markdown(f'<div class="count-badge">{len(setlist)} biisiä</div>', unsafe_allow_html=True)
    
    for i, song in enumerate(setlist):
        title = song["title"]
        artist = song.get("artist", "")
        has_text = bool(st.session_state.song_texts.get(title, "").strip())
        ts = st.session_state.transpose_settings.get(title, {})
        semi = ts.get("semi", 0)
        
        # Status indicator
        status_class = "matched" if has_text else "missing"
        status_icon = "●" if has_text else "○"
        
        # Transpose indicator
        t_indicator = ""
        if semi != 0:
            t_label = f"+{semi}" if semi > 0 else str(semi)
            t_indicator = f'<span style="font-family:\'DM Mono\',monospace; font-size:0.75rem; color:{CHORD_COLOR}; margin-left:8px;">{t_label}</span>'
        
        st.markdown(f"""
        <div class="song-card" id="card-{i}">
            <div class="card-num">{i+1}</div>
            <div style="flex:1; min-width:0;">
                <div class="card-title">{title}{t_indicator}</div>
                {"<div class='card-artist'>" + artist + "</div>" if artist else ""}
            </div>
            <div class="card-status {status_class}">{status_icon}</div>
        </div>
        """, unsafe_allow_html=True)
        
        # Streamlit button overlay (since HTML clicks don't trigger Streamlit)
        if st.button(f"Avaa: {title}", key=f"open_{i}", use_container_width=True,
                     type="secondary"):
            st.session_state.open_song = i
            st.rerun()

elif not st.session_state.get("_pending_songs"):
    st.markdown(f"""
    <div style="text-align:center; padding:4rem 1rem; color:{DIM}; font-family:'DM Sans',sans-serif;">
        <div style="font-size:2.5rem; margin-bottom:1rem;">♪</div>
        <div style="font-size:1rem;">Lataa biisilista yllä aloittaaksesi</div>
        <div style="font-size:0.8rem; margin-top:0.5rem; color:{DIM}88;">TXT tai PDF · Biisin nimi - Artisti · rivi per biisi</div>
    </div>
    """, unsafe_allow_html=True)
