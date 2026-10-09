"""Process-wide state shared by every page and every browser session."""

import streamlit as st

import wallstreet.room
from wallstreet.auth import HostSessions
from wallstreet.config import Settings
from wallstreet.room import RoomRegistry


def get_settings() -> Settings:
    # Not cached: reading env is cheap, and a cached instance would outlive a hot reload of
    # wallstreet/config.py, leaving new code to run against an object of the old Settings class.
    return Settings.from_env()


@st.cache_resource
def get_registry() -> RoomRegistry:
    """One registry per server process: every browser session shares the same games."""
    # Looked up on the module at call time, so tests can substitute a recording subclass.
    return wallstreet.room.RoomRegistry(rounds=get_settings().rounds)


@st.cache_resource
def get_host_sessions() -> HostSessions:
    """Host logins live as long as the games do: a server restart logs every host out."""
    return HostSessions()
