"""Entry point: ``streamlit run app.py``. Routes on the query string (see docs/architecture.md)."""

import streamlit as st

import views
from wallstreet.config import Settings
from wallstreet.room import RoomRegistry

st.set_page_config(page_title="Wall Street Game", layout="wide", initial_sidebar_state="collapsed")


@st.cache_resource
def get_settings() -> Settings:
    return Settings.from_env()


@st.cache_resource
def get_registry() -> RoomRegistry:
    """One registry per server process: every browser session shares the same games."""
    return RoomRegistry(rounds=get_settings().rounds)


settings, registry = get_settings(), get_registry()
params = st.query_params

st.sidebar.caption("Players open this page's URL. The host adds `?role=host` to it.")
st.sidebar.markdown("[Open the host page](?role=host)")

if params.get("role") == "host":
    views.host_view(registry, settings)
elif token := params.get("seat"):
    views.player_view(registry, settings, token)
else:
    views.join_view(registry)
