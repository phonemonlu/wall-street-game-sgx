"""Entry point: ``streamlit run app.py``. Routes on the query string (see docs/architecture.md)."""

import streamlit as st

import views
from wallstreet.config import Settings
from wallstreet.room import RoomRegistry

st.set_page_config(page_title="Wall Street Game", layout="wide", initial_sidebar_state="collapsed")


def get_settings() -> Settings:
    # Not cached: reading env is cheap, and a cached instance would outlive a hot reload of
    # wallstreet/config.py, leaving new code to run against an object of the old Settings class.
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
