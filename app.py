"""Entry point: ``streamlit run app.py``. Routes ``/``, ``/login`` and ``/host`` (see docs/architecture.md)."""

import streamlit as st

import views

# Hidden navigation: players never see a link to the host pages; the host goes to /login directly.
page = st.navigation(
    [
        st.Page(views.PLAYER_PAGE, title=views.TITLE, default=True),
        st.Page(views.LOGIN_PAGE, title=f"Host login · {views.TITLE}", url_path="login"),
        st.Page(views.HOST_PAGE, title=f"Host · {views.TITLE}", url_path="host"),
    ],
    position="hidden",
)
# Players get a phone-width column even on a laptop; the host's group panels need the full width.
# Set once per run, here: a second call from inside the page makes the layout jump on every rerun.
layout = "wide" if page.url_path == "host" else "centered"
st.set_page_config(page_title=views.TITLE, layout=layout, initial_sidebar_state="collapsed")
st.html(views.MOBILE_CSS)
page.run()
