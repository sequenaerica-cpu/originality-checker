"""
Originality Checker app.

Runs two ways:
  - On your laptop: double-click run.bat
  - Online for friends/phones: deploy to Streamlit Community Cloud (see README)

Online settings come from the app's Secrets (never typed into the page):
  BRAVE_KEY = "your Brave key"        # optional, shared web search for everyone
  APP_PASSWORD = "something"          # optional, only people with it can use the app
  MAX_SEARCHES = 150                  # optional, cap per check to protect the shared credits
"""

import hmac
import json
import os
import re

import streamlit as st
import streamlit.components.v1 as components

from checker import run_check
from report import build_report

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(HERE, "settings.json")


def secret(name, default=None):
    try:
        return st.secrets.get(name, default)
    except Exception:          # no secrets file = running on your laptop
        return default


# run.bat only exists on your laptop (it isn't uploaded to GitHub), so if it's missing we're online
ONLINE = (secret("ONLINE", None) is not None or secret("BRAVE_KEY") is not None
          or secret("APP_PASSWORD") is not None or not os.path.exists(os.path.join(HERE, "run.bat")))


def load_settings():
    if ONLINE:
        return {}
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ---------------------------------------------------------------- phone or desktop?
def detect_phone():
    try:
        ua = st.context.headers.get("User-Agent", "") or ""
    except Exception:
        ua = ""
    return bool(re.search(r"Mobi|Android|iPhone|iPod|Opera Mini|IEMobile", ua))


VIEW_CHOICES = ["Auto", "Desktop", "Phone"]
view = st.session_state.get("view", "Auto")
PHONE = detect_phone() if view == "Auto" else view == "Phone"

st.set_page_config(page_title="Originality Checker", page_icon="📝",
                   layout="centered" if PHONE else "wide")
st.markdown("""<style>
.block-container{padding-top:2rem;padding-bottom:3rem}
@media (max-width:640px){.block-container{padding-left:1rem;padding-right:1rem} h1{font-size:1.8rem!important}}
</style>""", unsafe_allow_html=True)


def view_switch():
    st.radio("View", VIEW_CHOICES, key="view", horizontal=True,
             help="Auto picks Phone or Desktop based on your device.",
             label_visibility="collapsed")
    if view == "Auto":
        st.caption(f"Auto: showing {'phone' if PHONE else 'desktop'} view")


# ---------------------------------------------------------------- password gate
pw = secret("APP_PASSWORD")
if pw and not st.session_state.get("ok"):
    st.title("Originality Checker")
    typed = st.text_input("Password", type="password")
    if st.button("Enter", type="primary"):
        if hmac.compare_digest(typed, str(pw)):
            st.session_state["ok"] = True
            st.rerun()
        else:
            st.error("Wrong password.")
    st.stop()

cfg = load_settings()
shared_key = secret("BRAVE_KEY")
max_searches = int(secret("MAX_SEARCHES", 300))

# ---------------------------------------------------------------- header
if PHONE:
    st.title("Originality Checker")
    view_switch()
else:
    t1, t2 = st.columns([3, 1])
    with t1:
        st.title("Originality Checker")
    with t2:
        st.write("")
        view_switch()
st.caption("Upload your paper and get a report like Google Classroom's: flagged passages, "
           "matching sources, and what's already cited.")

# ---------------------------------------------------------------- inputs
def options_panel(expanded):
    with st.expander("Options", expanded=expanded):
        default_budget = min(cfg.get("budget", 150), max_searches)
        budget = st.slider("Web searches per check", 20, max_searches, default_budget, 10,
                           help="About 4 per page is a full check. Fewer = faster, less thorough.")
        academic = st.checkbox("Also search academic papers (free)", value=cfg.get("academic", True))
        refs = st.file_uploader("Your reference PDFs / related studies (optional)",
                                type=["pdf", "docx", "txt"], accept_multiple_files=True)
        if shared_key:
            own_key = st.text_input("Your own Brave key (optional, uses your credits instead)", type="password")
        else:
            own_key = st.text_input("Brave Search API key (needed for web search)",
                                    value=cfg.get("brave_key", ""), type="password")
        library = ""
        if not ONLINE:
            library = st.text_input("Folder with your references (laptop only)", value=cfg.get("library", ""))
            if st.button("Remember these settings"):
                with open(SETTINGS, "w", encoding="utf-8") as f:
                    json.dump({"brave_key": own_key, "academic": academic,
                               "library": library, "budget": budget}, f, indent=2)
                st.success("Saved on this computer.")
    return budget, academic, refs, own_key, library


if PHONE:
    up = st.file_uploader("Your paper (.docx or .pdf)", type=["docx", "pdf", "txt"])
    budget, academic, refs, own_key, library = options_panel(expanded=False)
    go_col = st.container()
else:
    left, right = st.columns([1, 1], gap="large")
    with left:
        up = st.file_uploader("Your paper (.docx or .pdf)", type=["docx", "pdf", "txt"])
        go_col = st.container()
    with right:
        budget, academic, refs, own_key, library = options_panel(expanded=True)

key = (own_key or "").strip() or shared_key
with go_col:
    if not key:
        st.info("Web search is off until a Brave key is added in Options. "
                "Academic papers and reference files still get checked.")
    go = bool(up) and st.button("Check originality", type="primary", use_container_width=True)

# ---------------------------------------------------------------- run + report
if go:
    bar = st.progress(0.0, text="Starting")

    def prog(frac, msg):
        bar.progress(min(max(frac, 0.0), 1.0), text=msg)

    try:
        res = run_check(doc_bytes=up.getvalue(), filename=up.name,
                        brave_key=key or None, use_academic=academic,
                        library_folder=library.strip() or None,
                        library_files=[(f.name, f.getvalue()) for f in (refs or [])],
                        web_budget=budget, progress=prog)
    except Exception as ex:
        st.error(f"Check failed: {ex}")
        st.stop()

    page = build_report(res, up.name)
    base = re.sub(r"[^\w\-]+", "_", os.path.splitext(up.name)[0])
    name = f"{base}_report.html"
    if not ONLINE:
        os.makedirs(os.path.join(HERE, "reports"), exist_ok=True)
        with open(os.path.join(HERE, "reports", name), "w", encoding="utf-8") as f:
            f.write(page)
    st.session_state["report"] = (page, name)

if "report" in st.session_state:
    page, name = st.session_state["report"]
    st.download_button("Download report", page, file_name=name, mime="text/html",
                       use_container_width=PHONE)
    # phone: the report stacks into one long column, so give it more height
    components.html(page, height=1600 if PHONE else 1000, scrolling=True)
