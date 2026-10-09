"""Entry point: ``streamlit run app.py``. Routes ``/``, ``/login`` and ``/host`` (see docs/architecture.md)."""

import streamlit as st

import views

st.set_page_config(page_title=views.TITLE, layout="wide", initial_sidebar_state="collapsed")
st.html(views.MOBILE_CSS)

# Hidden navigation: players never see a link to the host pages; the host goes to /login directly.
st.navigation(
    [
        st.Page(views.PLAYER_PAGE, title=views.TITLE, default=True),
        st.Page(views.LOGIN_PAGE, title=f"Host login · {views.TITLE}", url_path="login"),
        st.Page(views.HOST_PAGE, title=f"Host · {views.TITLE}", url_path="host"),
    ],
    position="hidden",
).run()
