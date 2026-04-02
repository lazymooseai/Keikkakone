"""
KEIKKAKONE v5 — Keikkasetti muusikoille
- Isommat kortit ja fontit, yksinkertaisempi UI
- PDF näytetään iframe:ssa blob URL:n kautta (st.html)
- Transponointi tekstiextraktin kautta (PDF ei tue suoraan)
- st.html käytössä (ei components.v1)
"""

import streamlit as st
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
            return "\n\n".join(p.extract_text() or "" for p in pdf.pages).strip()
    except Exception:
        return ""

def bytes_to_b64(b: bytes) -> str:
    return base64.b64encode(b).decode("utf-8")


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
        for sep in [" - ", " \u2013 ", " / "]:
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

def match_songs_to_pdfs(songs, pdfs):
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
    r'\b([A-G])(#|b)?(m|min|maj|dim|aug|sus|add|dom)?(2|4|5|6|7|9|11|13)?'
    r'((?:add|b|#|no|sus|maj|min|dim|aug|alt|\d)*)(/([A-G])(#|b)?)?'
    r'(?=\s|$|[)\]\-|:])'
)

def _ni(r, a=""):
    n = r + (a or "")
    if n in _SHARP: return _SHARP.index(n)
    if n in _FLAT:  return _FLAT.index(n)
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
#  Persistent state (/tmp)
# ──────────────────────────────────────────

_CACHE_FILE = "/tmp/keikkakone/session.json"

def _save_disk():
    try:
        os.makedirs("/tmp/keikkakone", exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "setlist": st.session_state.get("setlist", []),
                "song_texts": st.session_state.get("song_texts", {}),
                "transpose_settings": st.session_state.get("transpose_settings", {}),
                "pdf_names": st.session_state.get("pdf_names", {}),
            }, f, ensure_ascii=False)
    except Exception:
        pass

def _load_disk() -> dict:
    try:
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


# ──────────────────────────────────────────
#  Page config & init
# ──────────────────────────────────────────

st.set_page_config(
    page_title="KEIKKAKONE",
    page_icon="\u266a",
    layout="centered",
    initial_sidebar_state="collapsed",
)

if "init" not in st.session_state:
    d = _load_disk()
    st.session_state.setlist            = d.get("setlist", [])
    st.session_state.song_texts         = d.get("song_texts", {})
    st.session_state.song_pdfs_b64      = {}
    st.session_state.pdf_names          = d.get("pdf_names", {})
    st.session_state.transpose_settings = d.get("transpose_settings", {})
    st.session_state.open_song          = None
    st.session_state.view_mode          = "pdf"
    st.session_state.init               = True


# ──────────────────────────────────────────
#  Theme
# ──────────────────────────────────────────

dark = st.sidebar.toggle("Tumma teema", value=True)
if dark:
    BG, CARD, BORDER = "#09090f", "#13131d", "#23233a"
    TEXT, DIM        = "#eeeef5", "#5a5a72"
    ACCENT           = "#e8572a"
    CHORD            = "#f0a830"
    CHORD_BG         = "rgba(240,168,48,0.10)"
else:
    BG, CARD, BORDER = "#f5f4ef", "#ffffff", "#dddbd0"
    TEXT, DIM        = "#18181f", "#888870"
    ACCENT           = "#c04420"
    CHORD            = "#7a4012"
    CHORD_BG         = "rgba(122,64,18,0.07)"


# ──────────────────────────────────────────
#  Global CSS
# ──────────────────────────────────────────

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:ital,wght@0,400;0,600;0,800;1,400&display=swap');

html, body, .stApp {{
    background: {BG} !important;
    font-family: 'DM Sans', sans-serif;
}}
.stApp * {{ color: {TEXT}; }}

.kk-title {{
    font-family: 'DM Mono', monospace;
    font-size: 2rem;
    font-weight: 500;
    color: {ACCENT};
    letter-spacing: 6px;
    text-align: center;
    padding: 1.8rem 0 0.2rem;
}}
.kk-sub {{
    font-family: 'DM Sans', sans-serif;
    font-size: 0.78rem;
    color: {DIM};
    letter-spacing: 4px;
    text-align: center;
    margin-bottom: 0.5rem;
}}

/* ── Isot kortit ── */
.stButton > button {{
    width: 100%;
    text-align: left !important;
    background: {CARD} !important;
    border: 1.5px solid {BORDER} !important;
    border-radius: 18px !important;
    padding: 22px 26px !important;
    margin-bottom: 10px !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 1.25rem !important;
    font-weight: 600 !important;
    color: {TEXT} !important;
    line-height: 1.5 !important;
    white-space: normal !important;
    height: auto !important;
    min-height: 80px !important;
    transition: all 0.13s ease !important;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08) !important;
}}
.stButton > button:hover {{
    border-color: {ACCENT}77 !important;
    box-shadow: 0 6px 20px rgba(0,0,0,0.14) !important;
    transform: translateY(-2px);
}}
.stButton > button[kind="primary"] {{
    background: {ACCENT} !important;
    border-color: {ACCENT} !important;
    color: #fff !important;
    font-family: 'DM Mono', monospace !important;
    letter-spacing: 2px !important;
    font-size: 1rem !important;
    min-height: 56px !important;
    font-weight: 700 !important;
}}
.stButton > button[kind="secondary"] {{
    min-height: 52px !important;
    font-size: 1.05rem !important;
}}

.sec {{
    font-family: 'DM Mono', monospace;
    font-size: 0.68rem;
    color: {DIM};
    letter-spacing: 3px;
    text-transform: uppercase;
    padding: 1rem 0 0.4rem;
}}
.info {{
    font-family: 'DM Mono', monospace;
    font-size: 0.8rem;
    color: {DIM};
    text-align: center;
    padding: 0.3rem 0 0.8rem;
}}
.num {{
    font-family: 'DM Mono', monospace;
    font-size: 1.1rem;
    color: {ACCENT};
    font-weight: 500;
}}

#MainMenu, footer, header {{ visibility: hidden; }}
div[data-testid="stSidebar"] {{ background: {BG} !important; }}
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────
#  PDF iframe via st.html
# ──────────────────────────────────────────

def show_pdf(pdf_b64: str, height: int = 700):
    # Split b64 into JS-safe chunks to avoid string literal limits
    chunk = 50000
    chunks = [pdf_b64[i:i+chunk] for i in range(0, len(pdf_b64), chunk)]
    chunks_js = "[" + ",".join(f'"{c}"' for c in chunks) + "]"
    html = f"""<!DOCTYPE html>
<html><head><style>
  body{{margin:0;background:{BG};}}
  iframe{{width:100%;height:{height}px;border:none;border-radius:12px;display:block;}}
  #msg{{color:{ACCENT};font-family:monospace;text-align:center;padding:2rem;
        font-size:0.9rem;letter-spacing:2px;}}
</style></head><body>
<div id="msg">LADATAAN...</div>
<script>
(function(){{
  var chunks={chunks_js};
  var b64=chunks.join("");
  var bin=atob(b64),arr=new Uint8Array(bin.length);
  for(var i=0;i<bin.length;i++) arr[i]=bin.charCodeAt(i);
  var url=URL.createObjectURL(new Blob([arr],{{type:"application/pdf"}}));
  document.getElementById("msg").style.display="none";
  var f=document.createElement("iframe");
  f.src=url;
  document.body.appendChild(f);
}})();
</script></body></html>"""
    st.html(html)


# ──────────────────────────────────────────
#  Chord sheet via st.html
# ──────────────────────────────────────────

def show_chords(text: str, semi: int = 0, flat: bool = False):
    t = transpose_text(text, semi, flat)
    rows = []
    for line in t.split("\n"):
        esc = line.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        if not esc.strip():
            rows.append('<div style="height:0.55em"></div>')
        elif is_chord_line(line):
            rows.append(
                f'<div style="color:{CHORD};font-weight:500;font-size:1.15em;'
                f'background:{CHORD_BG};padding:2px 6px;border-radius:4px;margin:3px 0;">'
                f'{esc}</div>'
            )
        else:
            rows.append(f'<div style="color:{TEXT};font-size:1.05em;">{esc}</div>')

    html = (
        f'<div style="font-family:\'DM Mono\',monospace;line-height:1.8;'
        f'white-space:pre-wrap;word-break:break-word;padding:0.5rem 0;">'
        + "".join(rows) + "</div>"
    )
    st.html(html)


# ══════════════════════════════════════════
#  BIISI-DETALJINÄKYMÄ
# ══════════════════════════════════════════

if st.session_state.open_song is not None:
    idx     = st.session_state.open_song
    setlist = st.session_state.setlist

    if idx >= len(setlist):
        st.session_state.open_song = None
        st.rerun()

    song    = setlist[idx]
    title   = song["title"]
    artist  = song.get("artist", "")
    text    = st.session_state.song_texts.get(title, "")
    pdf_b64 = st.session_state.song_pdfs_b64.get(title, "")
    ts      = st.session_state.transpose_settings.get(title, {"semi":0,"flat":False})

    # Takaisin
    if st.button("\u2190 Takaisin", key="back", type="secondary"):
        st.session_state.open_song = None
        st.rerun()

    # Otsikko
    st.markdown(
        f'<div style="margin:0.5rem 0 0.1rem;">'
        f'<span class="num">{idx+1}.</span> '
        f'<span style="font-size:1.6rem;font-weight:800;">{title}</span>'
        f'</div>',
        unsafe_allow_html=True
    )
    if artist:
        st.markdown(
            f'<div style="font-size:1rem;color:{DIM};margin-bottom:0.6rem;">'
            f'{artist}</div>',
            unsafe_allow_html=True
        )

    # View toggle
    v1, v2 = st.columns(2)
    with v1:
        if st.button(
            "\U0001f4c4 PDF", key="vm_pdf",
            type="primary" if st.session_state.view_mode == "pdf" else "secondary",
            use_container_width=True
        ):
            st.session_state.view_mode = "pdf"
            st.rerun()
    with v2:
        if st.button(
            "\U0001f3b5 Transponointi", key="vm_text",
            type="primary" if st.session_state.view_mode == "text" else "secondary",
            use_container_width=True
        ):
            st.session_state.view_mode = "text"
            st.rerun()

    st.markdown("---")

    # PDF-näkymä
    if st.session_state.view_mode == "pdf":
        if pdf_b64:
            show_pdf(pdf_b64, height=680)
        elif text:
            st.info("PDF ei saatavilla \u2014 n\u00e4ytet\u00e4\u00e4n teksti")
            show_chords(text, ts.get("semi",0), ts.get("flat",False))
        else:
            st.markdown(
                f'<div style="text-align:center;color:{DIM};padding:3rem 0;'
                f'font-size:1.1rem;">Ei lappua t\u00e4lle biisille</div>',
                unsafe_allow_html=True
            )

    # Transponointinäkymä
    else:
        semi = ts.get("semi", 0)
        label = f"+{semi}" if semi > 0 else str(semi)

        c_down, c_val, c_up, c_flat, c_reset = st.columns([1,1,1,1,2])
        with c_down:
            if st.button("\u2212", key="t_down", use_container_width=True):
                ts["semi"] = semi - 1
                st.session_state.transpose_settings[title] = ts
                _save_disk(); st.rerun()
        with c_val:
            st.markdown(
                f'<div style="text-align:center;font-family:\'DM Mono\',monospace;'
                f'font-size:1.8rem;color:{CHORD};font-weight:500;padding-top:6px;">'
                f'{label}</div>',
                unsafe_allow_html=True
            )
        with c_up:
            if st.button("+", key="t_up", use_container_width=True):
                ts["semi"] = semi + 1
                st.session_state.transpose_settings[title] = ts
                _save_disk(); st.rerun()
        with c_flat:
            flat_label = "\u266d" if ts.get("flat") else "\u266f"
            if st.button(flat_label, key="t_flat", use_container_width=True):
                ts["flat"] = not ts.get("flat", False)
                st.session_state.transpose_settings[title] = ts
                _save_disk(); st.rerun()
        with c_reset:
            if semi != 0:
                if st.button("Nollaa", key="t_reset", use_container_width=True):
                    ts["semi"] = 0
                    st.session_state.transpose_settings[title] = ts
                    _save_disk(); st.rerun()

        st.markdown("---")

        if text:
            show_chords(text, semi, ts.get("flat", False))
        else:
            st.markdown(
                f'<div style="text-align:center;color:{DIM};padding:2rem 0;">'
                f'Teksti\u00e4 ei saatu luettua PDF:st\u00e4.<br>'
                f'<span style="font-size:0.85rem;">Kokeile PDF-n\u00e4kym\u00e4\u00e4.</span></div>',
                unsafe_allow_html=True
            )

    # Edellinen / Seuraava
    st.markdown("---")
    cp, cn = st.columns(2)
    with cp:
        if idx > 0:
            prev = setlist[idx-1]["title"]
            lbl  = ("\u2190 " + prev)[:28]
            if st.button(lbl, key="prev", use_container_width=True, type="secondary"):
                st.session_state.open_song = idx - 1
                st.rerun()
    with cn:
        if idx < len(setlist) - 1:
            nxt = setlist[idx+1]["title"]
            lbl = (nxt + " \u2192")[:28]
            if st.button(lbl, key="next", use_container_width=True, type="secondary"):
                st.session_state.open_song = idx + 1
                st.rerun()

    st.stop()


# ══════════════════════════════════════════
#  PÄÄNÄKYMÄ — Setlista
# ══════════════════════════════════════════

st.markdown(f'<div class="kk-title">KEIKKAKONE</div>', unsafe_allow_html=True)
st.markdown(f'<div class="kk-sub">SETTILISTA</div>', unsafe_allow_html=True)

setlist = st.session_state.setlist

# Muississtatus
if setlist:
    n_pdf = sum(1 for s in setlist if
                st.session_state.song_pdfs_b64.get(s["title"]) or
                st.session_state.song_texts.get(s["title"]))
    hcol1, hcol2 = st.columns([4, 1])
    with hcol1:
        st.markdown(
            f'<div style="font-family:\'DM Mono\',monospace;font-size:0.78rem;'
            f'color:{DIM};padding:0.4rem 0;">'
            f'\u266a {len(setlist)} bii\u00e4 \u00b7 {n_pdf} lappua</div>',
            unsafe_allow_html=True
        )
    with hcol2:
        if st.button("\U0001f5d1", key="clear_all", help="Tyhjenn\u00e4 kaikki"):
            for k in ["setlist","song_texts","song_pdfs_b64","pdf_names","transpose_settings"]:
                st.session_state[k] = [] if k == "setlist" else {}
            st.session_state._pending_songs = None
            _save_disk(); st.rerun()

st.markdown("---")

# Lataa biisilista
st.markdown('<div class="sec">LATAA BIISILISTA</div>', unsafe_allow_html=True)

setlist_file = st.file_uploader(
    "Biisilista (TXT tai PDF)", type=["txt","pdf"],
    key="setlist_file", label_visibility="collapsed"
)

if setlist_file:
    raw = setlist_file.read()
    raw_text = (pdf_to_text(raw) if setlist_file.type == "application/pdf"
                else raw.decode("utf-8", errors="replace"))
    parsed = parse_setlist(raw_text)
    if parsed:
        st.session_state._pending_songs = parsed
        st.success(f"\u2713 {len(parsed)} biisi\u00e4 luettu")
    else:
        st.warning("Biisej\u00e4 ei l\u00f6ytynyt. Muoto: Biisin nimi - Artisti")

# Lataa PDF-laput
if st.session_state.get("_pending_songs"):
    songs = st.session_state._pending_songs
    st.markdown('<div class="sec">LATAA PDF-SOINTULAPUT</div>', unsafe_allow_html=True)

    pdf_files = st.file_uploader(
        "PDF-laput", type=["pdf"], accept_multiple_files=True,
        key="pdf_upload", label_visibility="collapsed"
    )

    if pdf_files:
        available, pdf_bytes_map = [], {}
        for f in pdf_files:
            b = f.read()
            available.append({"name": f.name})
            pdf_bytes_map[f.name] = b

        matched   = match_songs_to_pdfs(songs, available)
        all_names = ["\u2014"] + [p["name"] for p in available]
        final_matches = []

        st.markdown('<div class="sec">YHDIST\u00c4MINEN</div>', unsafe_allow_html=True)
        for i, m in enumerate(matched):
            c1, c2 = st.columns([3, 3])
            with c1:
                st.markdown(
                    f'<div style="font-size:1.05rem;font-weight:600;">'
                    f'{i+1}. {m["title"]}</div>'
                    + (f'<div style="font-size:0.85rem;color:{DIM};">'
                       f'{m["artist"]}</div>' if m.get("artist") else ""),
                    unsafe_allow_html=True
                )
            with c2:
                default = 0
                if m["match"]:
                    try: default = all_names.index(m["match"]["name"])
                    except ValueError: pass
                sel = st.selectbox("", all_names, index=default,
                                   key=f"sel_{i}", label_visibility="collapsed")
                final_matches.append(sel)

        st.markdown("---")
        mc = sum(1 for f in final_matches if f != "\u2014")
        st.markdown(
            f'<div class="info">{mc}/{len(songs)} yhdistetty</div>',
            unsafe_allow_html=True
        )

        if st.button("RAKENNA SETTI", type="primary", use_container_width=True):
            new_sl, new_texts, new_pdfs, new_names = [], {}, {}, {}
            for i, song in enumerate(songs):
                pname = final_matches[i] if i < len(final_matches) else "\u2014"
                t = song["title"]
                new_sl.append(song)
                if pname != "\u2014" and pname in pdf_bytes_map:
                    raw = pdf_bytes_map[pname]
                    new_pdfs[t]  = bytes_to_b64(raw)
                    new_names[t] = pname
                    ext = pdf_to_text(raw)
                    new_texts[t] = ext if ext.strip() else ""
                else:
                    new_texts[t] = ""
            st.session_state.setlist            = new_sl
            st.session_state.song_texts         = new_texts
            st.session_state.song_pdfs_b64      = new_pdfs
            st.session_state.pdf_names          = new_names
            st.session_state._pending_songs     = None
            _save_disk(); st.rerun()

# Setlista — isot kortit
setlist = st.session_state.setlist

if setlist:
    st.markdown("---")
    st.markdown(
        f'<div class="info">{len(setlist)} biisi\u00e4</div>',
        unsafe_allow_html=True
    )

    for i, song in enumerate(setlist):
        title       = song["title"]
        artist      = song.get("artist", "")
        has_content = bool(
            st.session_state.song_pdfs_b64.get(title) or
            st.session_state.song_texts.get(title, "").strip()
        )
        ts   = st.session_state.transpose_settings.get(title, {})
        semi = ts.get("semi", 0)
        t_ind = (f"  [{'+' if semi>0 else ''}{semi}]" if semi != 0 else "")
        dot   = "\u25cf" if has_content else "\u25cb"

        lbl = f"{i+1}.  {dot}  {title}{t_ind}"
        if artist:
            lbl += f"\n        {artist}"

        if st.button(lbl, key=f"open_{i}", use_container_width=True):
            st.session_state.open_song = i
            st.session_state.view_mode = (
                "pdf" if st.session_state.song_pdfs_b64.get(title) else "text"
            )
            st.rerun()

elif not st.session_state.get("_pending_songs"):
    st.markdown(f"""
    <div style="text-align:center;padding:3rem 1rem;color:{DIM};">
        <div style="font-size:3rem;margin-bottom:1rem;">\u266a</div>
        <div style="font-size:1.1rem;">Lataa biisilista yll\u00e4</div>
        <div style="font-size:0.85rem;margin-top:0.5rem;color:{DIM}88;">
            TXT tai PDF \u00b7 Biisin nimi - Artisti \u00b7 rivi per biisi
        </div>
    </div>
    """, unsafe_allow_html=True)

# Lisää yksittäinen lappu
if setlist:
    with st.expander("\u2795 Lis\u00e4\u00e4 / p\u00e4ivit\u00e4 yksitt\u00e4inen lappu"):
        names  = [s["title"] for s in setlist]
        chosen = st.selectbox("Biisi", names, key="add_single_song")
        spdf   = st.file_uploader("PDF", type=["pdf"], key="single_pdf",
                                  label_visibility="collapsed")
        if spdf and chosen:
            raw = spdf.read()
            if st.button("Tallenna", key="save_single"):
                st.session_state.song_pdfs_b64[chosen] = bytes_to_b64(raw)
                st.session_state.pdf_names[chosen]     = spdf.name
                ext = pdf_to_text(raw)
                st.session_state.song_texts[chosen]    = ext if ext.strip() else ""
                _save_disk()
                st.success(f"\u2713 Tallennettu: {spdf.name}")
                st.rerun()
