"""
KEIKKAKONE v4 — Keikkasetti muusikoille
- PDF näytetään iframe:ssa (ei tekstimuunnos)
- Transponointi toimii tekstiextraktion kautta rinnalla
- SessionStorage-muisti: tiedostot säilyvät selaimen välimuistissa
- Korjattu HTML-renderöintiongelmat
"""

import streamlit as st
import streamlit.components.v1 as components
import json, re, os, io, logging, base64
from difflib import SequenceMatcher

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

def bytes_to_b64(b: bytes) -> str:
    return base64.b64encode(b).decode("utf-8")

def b64_to_bytes(s: str) -> bytes:
    return base64.b64decode(s)


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
        for sep in [" - ", " – ", " / "]:
            if sep in cleaned:
                parts = cleaned.split(sep, 1)
                title, artist = parts[0].strip(), parts[1].strip()
                break
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

def transpose_text(text, semi, flat=False):
    if semi == 0: return text
    return "\n".join(
        _CHORD_RE.sub(lambda m: _tc(m, semi, flat), ln) if is_chord_line(ln) else ln
        for ln in text.split("\n")
    )


# ──────────────────────────────────────────
#  SessionStorage bridge (muisti selaimessa)
# ──────────────────────────────────────────

def inject_storage_bridge():
    """Injektoi JS-silta joka lataa/tallentaa datan sessionStorageen."""
    components.html("""
    <script>
    // Tarkista onko dataa sessionStoragessa ja lähetä Streamlitille
    const stored = sessionStorage.getItem('keikkakone_v4');
    if (stored) {
        // Streamlit ei suoraan ota JS-dataa, käytetään query param -temppua
        // Tallennamme flagin jotta Python tietää datan olevan saatavilla
        window.parent.postMessage({type: 'keikkakone_has_data', value: true}, '*');
    }
    </script>
    """, height=0)

def save_to_session_storage(data_b64: str):
    """Tallenna data sessionStorageen."""
    components.html(f"""
    <script>
    try {{
        sessionStorage.setItem('keikkakone_v4', {json.dumps(data_b64)});
    }} catch(e) {{
        console.warn('SessionStorage full:', e);
    }}
    </script>
    """, height=0)

def render_pdf_viewer(pdf_b64: str, height: int = 700):
    """Näytä PDF iframe:ssa blob URL:n kautta."""
    components.html(f"""
    <!DOCTYPE html>
    <html>
    <head>
    <style>
        body {{ margin: 0; padding: 0; background: #08080c; }}
        iframe {{
            width: 100%;
            height: {height}px;
            border: none;
            border-radius: 12px;
        }}
        #loading {{
            color: #e8572a;
            font-family: 'DM Mono', monospace;
            text-align: center;
            padding: 2rem;
            font-size: 0.9rem;
            letter-spacing: 2px;
        }}
    </style>
    </head>
    <body>
    <div id="loading">LADATAAN PDF...</div>
    <script>
    (function() {{
        const b64 = {json.dumps(pdf_b64)};
        const byteChars = atob(b64);
        const byteNumbers = new Uint8Array(byteChars.length);
        for (let i = 0; i < byteChars.length; i++) {{
            byteNumbers[i] = byteChars.charCodeAt(i);
        }}
        const blob = new Blob([byteNumbers], {{ type: 'application/pdf' }});
        const url = URL.createObjectURL(blob);
        document.getElementById('loading').style.display = 'none';
        const iframe = document.createElement('iframe');
        iframe.src = url;
        iframe.style.width = '100%';
        iframe.style.height = '{height}px';
        iframe.style.border = 'none';
        iframe.style.borderRadius = '12px';
        document.body.appendChild(iframe);
    }})();
    </script>
    </body>
    </html>
    """, height=height + 10)


# ──────────────────────────────────────────
#  Render helpers
# ──────────────────────────────────────────

def render_chord_sheet(text: str, semi: int = 0, flat: bool = False,
                       text_color: str = "#e4e4ec",
                       chord_color: str = "#f0a830",
                       chord_bg: str = "rgba(240,168,48,0.08)"):
    t = transpose_text(text, semi, flat)
    parts = []
    for line in t.split("\n"):
        esc = line.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        if not esc.strip():
            parts.append('<div style="height:0.6em;"></div>')
        elif is_chord_line(line):
            parts.append(
                f'<div style="color:{chord_color}; font-weight:500; '
                f'font-size:1.05em; background:{chord_bg}; '
                f'padding:1px 4px; border-radius:3px; '
                f'margin:2px 0;">{esc}</div>'
            )
        else:
            parts.append(f'<div style="color:{text_color}; font-size:1em;">{esc}</div>')

    html = f"""
    <div style="font-family:'DM Mono',monospace; line-height:1.75;
                white-space:pre-wrap; word-break:break-word; padding:1rem 0;">
        {"".join(parts)}
    </div>
    """
    components.html(html, height=600, scrolling=True)


# ──────────────────────────────────────────
#  Persistent state (tiedostojärjestelmä)
# ──────────────────────────────────────────

# Streamlit Cloud: käytä /tmp koska /data ei välttämättä kirjoitettavissa
_CACHE_DIR = "/tmp/keikkakone"
_CACHE_FILE = os.path.join(_CACHE_DIR, "session.json")

def _save_disk(data: dict):
    """Tallenna metadata levylle (ei PDF-bytejä, ne session_statessa)."""
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        # Tallennetaan vain metadata (ei binääridataa)
        meta = {
            "setlist": data.get("setlist", []),
            "song_texts": data.get("song_texts", {}),
            "transpose_settings": data.get("transpose_settings", {}),
            "pdf_names": data.get("pdf_names", {}),
        }
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    except Exception as e:
        pass  # Streamlit Cloud saattaa rajoittaa kirjoittamista

def _load_disk() -> dict:
    try:
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _persist():
    _save_disk({
        "setlist": st.session_state.get("setlist", []),
        "song_texts": st.session_state.get("song_texts", {}),
        "transpose_settings": st.session_state.get("transpose_settings", {}),
        "pdf_names": st.session_state.get("pdf_names", {}),
    })


# ══════════════════════════════════════════
#  Streamlit page config
# ══════════════════════════════════════════

st.set_page_config(
    page_title="KEIKKAKONE",
    page_icon="♪",
    layout="centered",
    initial_sidebar_bar="collapsed",
)

# ──────────────────────────────────────────
#  Init session state
# ──────────────────────────────────────────

if "init" not in st.session_state:
    d = _load_disk()
    st.session_state.setlist = d.get("setlist", [])
    st.session_state.song_texts = d.get("song_texts", {})
    st.session_state.song_pdfs_b64 = {}  # title -> base64 PDF bytes
    st.session_state.pdf_names = d.get("pdf_names", {})  # title -> original filename
    st.session_state.transpose_settings = d.get("transpose_settings", {})
    st.session_state.open_song = None
    st.session_state.view_mode = "pdf"  # "pdf" tai "text"
    st.session_state.init = True


# ──────────────────────────────────────────
#  Theme (sidebar)
# ──────────────────────────────────────────

dark = st.sidebar.toggle("Tumma teema", value=True, key="dark")
st.sidebar.markdown("---")
st.sidebar.markdown("**KEIKKAKONE v4**")
st.sidebar.caption("PDF-näkymä + muisti")

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


# ──────────────────────────────────────────
#  Global CSS
# ──────────────────────────────────────────

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600;700&display=swap');

.stApp {{ background: {BG} !important; }}
.stApp, .stApp p, .stApp span, .stApp div, .stApp label {{ color: {TEXT}; }}

.kk-header {{
    text-align: center;
    padding: 1.5rem 0 0.5rem;
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
    font-size: 0.75rem;
    color: {DIM};
    letter-spacing: 3px;
    margin-top: 4px;
}}
.section-label {{
    font-family: 'DM Mono', monospace;
    font-size: 0.68rem;
    color: {DIM};
    letter-spacing: 3px;
    text-transform: uppercase;
    padding: 1.2rem 0 0.4rem;
    user-select: none;
}}
.count-badge {{
    font-family: 'DM Mono', monospace;
    font-size: 0.75rem;
    color: {DIM};
    text-align: center;
    padding: 0.4rem 0;
}}

/* Song list item — pelkkä teksti, ei HTML-kortit */
.stButton > button {{
    width: 100%;
    text-align: left;
    background: {CARD} !important;
    border: 1px solid {BORDER} !important;
    border-radius: 14px !important;
    padding: 16px 20px !important;
    margin-bottom: 6px !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 1rem !important;
    color: {TEXT} !important;
    transition: all 0.15s ease !important;
    white-space: normal !important;
    height: auto !important;
    min-height: 60px !important;
}}
.stButton > button:hover {{
    background: {CARD_HOVER} !important;
    border-color: {ACCENT}55 !important;
    transform: translateY(-1px);
    box-shadow: 0 4px 16px rgba(0,0,0,0.12) !important;
}}

/* Primary button */
.stButton > button[kind="primary"] {{
    background: {ACCENT} !important;
    border-color: {ACCENT} !important;
    color: white !important;
    font-weight: 600 !important;
    letter-spacing: 1px !important;
    font-family: 'DM Mono', monospace !important;
}}

#MainMenu {{visibility: hidden;}}
footer {{visibility: hidden;}}
header {{visibility: hidden;}}
div[data-testid="stSidebar"] {{
    background: {BG} !important;
}}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════
#  SONG DETAIL VIEW
# ══════════════════════════════════════════

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
    pdf_b64 = st.session_state.song_pdfs_b64.get(title, "")
    pdf_name = st.session_state.pdf_names.get(title, "")
    ts = st.session_state.transpose_settings.get(title, {"semi": 0, "flat": False})

    # ── Back button
    if st.button("← takaisin", key="back"):
        st.session_state.open_song = None
        st.rerun()

    # ── Title
    num_str = f"{idx+1}."
    st.markdown(
        f'<div style="padding:0.3rem 0;">'
        f'<span style="font-family:\'DM Mono\',monospace; font-size:1.1rem; '
        f'color:{ACCENT};">{num_str}</span> '
        f'<span style="font-family:\'DM Sans\',sans-serif; font-size:1.4rem; '
        f'font-weight:700; color:{TEXT};">{title}</span>'
        f'</div>',
        unsafe_allow_html=True
    )
    if artist:
        st.markdown(
            f'<div style="font-family:\'DM Sans\',sans-serif; font-size:0.85rem; '
            f'color:{DIM}; padding-left:4px; margin-bottom:0.5rem;">{artist}</div>',
            unsafe_allow_html=True
        )

    # ── View mode toggle (PDF / Teksti+Transponointi)
    view_col1, view_col2 = st.columns(2)
    with view_col1:
        if st.button("📄 PDF-näkymä", key="vm_pdf",
                     type="primary" if st.session_state.view_mode == "pdf" else "secondary"):
            st.session_state.view_mode = "pdf"
            st.rerun()
    with view_col2:
        if st.button("🎵 Transponointi", key="vm_text",
                     type="primary" if st.session_state.view_mode == "text" else "secondary"):
            st.session_state.view_mode = "text"
            st.rerun()

    st.markdown("---")

    # ── PDF VIEW
    if st.session_state.view_mode == "pdf":
        if pdf_b64:
            render_pdf_viewer(pdf_b64, height=680)
        elif text:
            st.info("PDF ei saatavilla — näytetään tekstiversio")
            st.text(text)
        else:
            st.markdown(
                f'<div style="text-align:center; color:{DIM}; padding:3rem 0; '
                f'font-family:\'DM Sans\',sans-serif;">Ei PDF-lappua tälle biisille</div>',
                unsafe_allow_html=True
            )

    # ── TRANSPOSITION VIEW
    else:
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
            st.markdown(
                f'<div style="text-align:center; font-family:\'DM Mono\',monospace; '
                f'font-size:1.4rem; color:{CHORD_COLOR}; font-weight:500; '
                f'padding-top:4px;">{label}</div>',
                unsafe_allow_html=True
            )
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

        if text:
            render_chord_sheet(
                text, ts.get("semi", 0), ts.get("flat", False),
                text_color=TEXT, chord_color=CHORD_COLOR, chord_bg=CHORD_BG
            )
        else:
            st.markdown(
                f'<div style="text-align:center; color:{DIM}; padding:3rem 0; '
                f'font-family:\'DM Sans\',sans-serif;">'
                f'Tekstiä ei saatu luettua PDF:stä.<br>'
                f'<span style="font-size:0.8rem;">Kokeile PDF-näkymää.</span></div>',
                unsafe_allow_html=True
            )

    # ── Prev / Next
    st.markdown("---")
    cp, cn = st.columns(2)
    with cp:
        if idx > 0:
            prev_title = setlist[idx-1]["title"]
            short = prev_title[:20] + "…" if len(prev_title) > 20 else prev_title
            if st.button(f"← {short}", key="prev", use_container_width=True):
                st.session_state.open_song = idx - 1
                st.rerun()
    with cn:
        if idx < len(setlist) - 1:
            next_title = setlist[idx+1]["title"]
            short = next_title[:20] + "…" if len(next_title) > 20 else next_title
            if st.button(f"{short} →", key="next", use_container_width=True):
                st.session_state.open_song = idx + 1
                st.rerun()

    st.stop()


# ══════════════════════════════════════════
#  MAIN: Setlist view
# ══════════════════════════════════════════

st.markdown("""
<div class="kk-header">
    <h1>KEIKKAKONE</h1>
    <div class="sub">SETTILISTA</div>
</div>
""", unsafe_allow_html=True)

# ── Näytä muistin status
setlist = st.session_state.setlist
if setlist:
    n_pdf = sum(1 for s in setlist if st.session_state.song_pdfs_b64.get(s["title"]) or
                st.session_state.song_texts.get(s["title"]))
    cols_top = st.columns([3, 1])
    with cols_top[0]:
        st.markdown(
            f'<div style="font-family:\'DM Mono\',monospace; font-size:0.75rem; '
            f'color:{DIM}; padding:0.5rem 0;">'
            f'♪ {len(setlist)} biisiä muistissa · {n_pdf} lappua ladattu</div>',
            unsafe_allow_html=True
        )
    with cols_top[1]:
        if st.button("🗑 Tyhjennä", key="clear_all"):
            for key in ["setlist","song_texts","song_pdfs_b64","pdf_names",
                        "transpose_settings","_pending_songs"]:
                st.session_state[key] = [] if key in ["setlist"] else {}
            _persist()
            st.rerun()

st.markdown("---")

# ──────────────────────────────────────────
#  Upload: Biisilista
# ──────────────────────────────────────────

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
        raw_text = pdf_to_text(raw_bytes)
    else:
        raw_text = setlist_file.read().decode("utf-8", errors="replace")
    parsed = parse_setlist(raw_text)
    if parsed:
        st.session_state._pending_songs = parsed
        st.success(f"✓ {len(parsed)} biisiä luettu")
    else:
        st.warning("Biisejä ei löytynyt. Muoto: Biisin nimi - Artisti")

# ──────────────────────────────────────────
#  Upload: PDF-laput + Matching
# ──────────────────────────────────────────

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

        st.markdown("---")
        matched_count = sum(1 for f in final_matches if f != "—")
        st.markdown(
            f'<div class="count-badge">{matched_count}/{len(songs)} yhdistetty</div>',
            unsafe_allow_html=True
        )

        if st.button("RAKENNA SETTI", type="primary", use_container_width=True):
            new_setlist = []
            new_texts = {}
            new_pdfs_b64 = {}
            new_pdf_names = {}

            for i, song in enumerate(songs):
                pdf_name = final_matches[i] if i < len(final_matches) else "—"
                title = song["title"]
                new_setlist.append(song)

                if pdf_name != "—" and pdf_name in pdf_bytes_map:
                    raw = pdf_bytes_map[pdf_name]
                    # Tallenna PDF base64:na
                    new_pdfs_b64[title] = bytes_to_b64(raw)
                    new_pdf_names[title] = pdf_name
                    # Myös tekstiextrakti transponointia varten
                    extracted = pdf_to_text(raw)
                    new_texts[title] = extracted if extracted.strip() else ""
                else:
                    new_texts[title] = ""

            st.session_state.setlist = new_setlist
            st.session_state.song_texts = new_texts
            st.session_state.song_pdfs_b64 = new_pdfs_b64
            st.session_state.pdf_names = new_pdf_names
            st.session_state._pending_songs = None
            _persist()
            st.rerun()

# ──────────────────────────────────────────
#  Setlist display
# ──────────────────────────────────────────

setlist = st.session_state.setlist

if setlist:
    st.markdown("---")
    st.markdown(
        f'<div class="count-badge">{len(setlist)} biisiä</div>',
        unsafe_allow_html=True
    )

    for i, song in enumerate(setlist):
        title = song["title"]
        artist = song.get("artist", "")
        has_pdf = bool(st.session_state.song_pdfs_b64.get(title))
        has_text = bool(st.session_state.song_texts.get(title, "").strip())
        has_content = has_pdf or has_text
        ts = st.session_state.transpose_settings.get(title, {})
        semi = ts.get("semi", 0)

        # Numero + status + transponointi indicator
        t_ind = f" [{'+' if semi > 0 else ''}{semi}]" if semi != 0 else ""
        status = "●" if has_content else "○"
        artist_line = f"\n{artist}" if artist else ""

        btn_label = f"{i+1}.  {status}  {title}{t_ind}{artist_line}"

        if st.button(btn_label, key=f"open_{i}", use_container_width=True):
            st.session_state.open_song = i
            st.session_state.view_mode = "pdf" if has_pdf else "text"
            st.rerun()

elif not st.session_state.get("_pending_songs"):
    st.markdown(f"""
    <div style="text-align:center; padding:3rem 1rem; color:{DIM};
                font-family:'DM Sans',sans-serif;">
        <div style="font-size:2.5rem; margin-bottom:1rem;">♪</div>
        <div style="font-size:1rem;">Lataa biisilista yllä aloittaaksesi</div>
        <div style="font-size:0.8rem; margin-top:0.5rem; color:{DIM}88;">
            TXT tai PDF · Biisin nimi - Artisti · rivi per biisi
        </div>
    </div>
    """, unsafe_allow_html=True)

# ──────────────────────────────────────────
#  Footer: lisää yksittäinen PDF jälkeenpäin
# ──────────────────────────────────────────

if setlist:
    with st.expander("➕ Lisää / päivitä yksittäinen lappu"):
        st.caption("Valitse biisi ja lataa sille uusi PDF")
        song_names = [s["title"] for s in setlist]
        chosen = st.selectbox("Biisi", song_names, key="add_single_song")
        single_pdf = st.file_uploader(
            "PDF", type=["pdf"], key="single_pdf_upload",
            label_visibility="collapsed"
        )
        if single_pdf and chosen:
            raw = single_pdf.read()
            if st.button("Tallenna lappu", key="save_single"):
                st.session_state.song_pdfs_b64[chosen] = bytes_to_b64(raw)
                st.session_state.pdf_names[chosen] = single_pdf.name
                extracted = pdf_to_text(raw)
                st.session_state.song_texts[chosen] = extracted if extracted.strip() else ""
                _persist()
                st.success(f"✓ {single_pdf.name} tallennettu biisille '{chosen}'")
                st.rerun()
