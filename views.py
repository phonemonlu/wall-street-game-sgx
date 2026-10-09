"""Streamlit render functions. Thin by design: game rules live in ``wallstreet``.

The pure helpers at the top (``results_frame``, ``totals_line``, ...) never touch streamlit and are
unit-tested directly; the ``render_*`` / ``*_view`` functions only draw what they return.
Everything that changes while people play is drawn inside ``st.fragment(run_every=...)`` so only
that part of the page refreshes.
"""

import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import pandas as pd
import streamlit as st

from wallstreet.auth import HostSessions
from wallstreet.config import Settings, check_login
from wallstreet.errors import GameError, UnknownGroup, UnknownSeat
from wallstreet.game import Phase
from wallstreet.room import START_BOT, RoomRegistry, RoomSnapshot, SeatView
from wallstreet.scoring import SEATS, Card, player_label
from wallstreet.strategies import STRATEGIES

TITLE = "Wall Street Game"
# Page scripts, relative to app.py (routed by st.navigation there).
PLAYER_PAGE = "app_pages/player.py"
LOGIN_PAGE = "app_pages/login.py"
HOST_PAGE = "app_pages/host.py"
NO_LOGIN_WARNING = (
    "No host login set: anyone with this URL can control the game. "
    "Set WSG_HOST_USERNAME and WSG_HOST_PASSWORD to lock it."
)
WRONG_LOGIN = "Wrong username or password."
WAITING_FOR_GROUPS = "Waiting for the host to create groups"
MAX_TABS = 8  # more groups than this: pick the group from a selectbox instead of one tab each
DEFAULT_TIMER_SEC = 120
JOIN_POLL_SEC = 2.0
START_HELP = f"Empty seats become {START_BOT} bots. A group nobody has joined is not started."
ALL_GROUPS_REFRESH_SEC = 3.0  # the All groups tab redraws every group's table, so refresh it less often
FLASH_SEC = 6.0

_BIG_BUTTONS_CSS = """<style>
.st-key-pick_X button, .st-key-pick_Y button { min-height: 6rem; }
.st-key-pick_X button p, .st-key-pick_Y button p { font-size: 2.6rem; font-weight: 700; }
</style>"""
# Phones only: 640px is the width below which Streamlit itself stacks columns. Desktop is unchanged.
MOBILE_CSS = """<style>
@media (max-width: 640px) {
  /* The header bar is 60px tall; Streamlit's default 96px top padding pushes the X/Y buttons off-screen. */
  [data-testid=stMainBlockContainer] { padding-top: 3.75rem; }
  [data-testid=stHeading] h1 { font-size: 1.75rem; line-height: 1.2; }
  [data-testid=stHeading] h2 { font-size: 1.4rem; }
  [data-testid=stHeading] h3 { font-size: 1.15rem; }
  /* iOS Safari zooms into any text field whose font is smaller than 16px. */
  [data-testid=stMain] input, [data-testid=stMain] textarea { font-size: 16px !important; }
  /* Apple's minimum touch target is 44px. */
  [role=radiogroup] label { min-height: 44px; padding-right: 0.75rem; }
}
</style>"""
# Markdown syntax a player could smuggle in through their name (links, images, badges, headings, ...).
_MD_SPECIAL = re.compile(r"([\\`*_\[\]!:$<>#~|])")
_OWN_COLUMN_STYLE = "background-color: rgba(255, 75, 75, 0.14)"
_BONUS_ROW_STYLE = "background-color: rgba(255, 170, 0, 0.10)"


# -- pure helpers (no streamlit) ------------------------------------------------------------------


def results_frame(snapshot: RoomSnapshot) -> pd.DataFrame:
    """One row per *revealed* round: ``RD | Total | P1..P4`` with cells like ``"X +10"``.

    Seat columns use the players' numbers across groups (Group 2: ``P5..P8``).
    """
    labels = {seat: player_label(snapshot.group_id, seat) for seat in SEATS}
    rows = [
        {
            "RD": record.round_no,
            "Total": record.group_total,
            **{label: f"{record.choices[seat]} {record.payoffs[seat]:+d}" for seat, label in labels.items()},
        }
        for record in snapshot.history
    ]
    return pd.DataFrame(rows, columns=["RD", "Total", *labels.values()])


def totals_line(snapshot: RoomSnapshot) -> str:
    """``"P1: 270 P2: -90 P3: -50 P4: -130"``."""
    return " ".join(f"{player_label(snapshot.group_id, seat)}: {snapshot.totals[seat]}" for seat in SEATS)


def round_label(snapshot: RoomSnapshot) -> str:
    """``"Round 5 of 10 · BONUS ×3"``; the lobby and the end of the game have their own labels."""
    if snapshot.phase is Phase.LOBBY:
        return "Lobby"
    if snapshot.phase is Phase.OVER:
        return "Game over"
    label = f"Round {snapshot.round_no} of {snapshot.rounds}"
    if snapshot.multiplier > 1:
        label += f" · BONUS ×{snapshot.multiplier}"
    return label


def phase_text(phase: Phase) -> str:
    return {
        Phase.LOBBY: "Waiting for the host to start the game.",
        Phase.OPEN: "Choose X or Y. You can change your mind until the host reveals.",
        Phase.REVEALED: "Round revealed. Waiting for the host to open the next round.",
        Phase.OVER: "All rounds have been played.",
    }[phase]


def bonus_hint(snapshot: RoomSnapshot) -> str | None:
    """``"Next round is a ×5 bonus"`` while there is a next round and it is a bonus round."""
    if snapshot.phase is Phase.OVER or snapshot.round_no >= snapshot.rounds:
        return None
    if snapshot.next_multiplier > 1:
        return f"Next round is a ×{snapshot.next_multiplier} bonus"
    return None


def seconds_left(timer_ends_at: float | None, now: float) -> int | None:
    """Whole seconds until the timer ends (never negative); None without a timer."""
    if timer_ends_at is None:
        return None
    return max(0, int(timer_ends_at - now + 0.999))


def countdown_text(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    if seconds == 0:
        return "⏱ Negotiation time is up"
    return f"⏱ Negotiation: {seconds // 60}:{seconds % 60:02d} left"


def pending_seats(snapshot: RoomSnapshot) -> list[str]:
    """Seats that still have to choose (only meaningful while a round is open)."""
    if snapshot.phase is not Phase.OPEN:
        return []
    return [player_label(snapshot.group_id, seat.label) for seat in snapshot.seats if not seat.submitted]


def reveal_help(snapshot: RoomSnapshot) -> str:
    """Tooltip for a group's Reveal button, for every phase."""
    if waiting := pending_seats(snapshot):
        return f"Waiting for {', '.join(waiting)}"
    return {
        Phase.LOBBY: "Start the game first.",
        Phase.OPEN: "Everyone has chosen.",
        Phase.REVEALED: "This round is revealed. Open the next round first.",
        Phase.OVER: "The game is over.",
    }[snapshot.phase]


def md_escape(text: str) -> str:
    """Backslash-escape markdown syntax so a player-chosen name renders as plain text."""
    return _MD_SPECIAL.sub(r"\\\1", text)


def seat_name(seat: SeatView) -> str:
    """The seat's name for display (markdown-escaped); placeholders for free or unnamed seats."""
    if seat.is_free:
        return "free"
    return md_escape(seat.name) if seat.name else "no name yet"


def seats_line(snapshot: RoomSnapshot) -> str:
    """``"P1 Ann · P2 Bot (Smart) · P3 free · P4 no name yet"``."""
    return " · ".join(f"{player_label(snapshot.group_id, seat.label)} {seat_name(seat)}" for seat in snapshot.seats)


def seat_status(seat: SeatView, phase: Phase) -> str:
    """``"Bot (Smart) · ✓ submitted"`` / ``"Ann · pending"`` / ``"free"``."""
    if seat.is_free:
        return "free"
    who = seat.name if seat.is_bot else f"{seat_name(seat)} (human)"
    if phase is not Phase.OPEN:
        return who
    return f"{who} · {'✓ submitted' if seat.submitted else '⏳ pending'}"


def skipped_text(verb: str, skipped: dict[int, str]) -> str:
    """Markdown for the groups a ``*_all`` action skipped."""
    if not skipped:
        return f"{verb}: done for every group."
    lines = "\n".join(f"- **Group {gid}**: {reason}" for gid, reason in sorted(skipped.items()))
    return f"{verb}: skipped {len(skipped)} group(s)\n{lines}"


def overview_frame(snapshots: Sequence[RoomSnapshot]) -> pd.DataFrame:
    rows = [
        {
            "Group": snap.group_id,
            "Status": round_label(snap),
            "Waiting for": ", ".join(pending_seats(snap)),
            "Free seats": ", ".join(player_label(snap.group_id, s.label) for s in snap.seats if s.is_free),
            "Group total": snap.group_total,
        }
        for snap in snapshots
    ]
    return pd.DataFrame(rows, columns=["Group", "Status", "Waiting for", "Free seats", "Group total"])


def leaderboard_frames(registry: RoomRegistry) -> tuple[pd.DataFrame, pd.DataFrame]:
    players = pd.DataFrame(registry.leaderboard(), columns=["rank", "group", "seat", "name", "bot", "points"])
    groups = pd.DataFrame(registry.group_leaderboard(), columns=["rank", "group", "points"])
    return players.rename(columns=str.title), groups.rename(columns=str.title)


# -- flash messages (survive fragment reruns for a few seconds) -----------------------------------


def _flash(slot: str, message: str, kind: str = "error", ttl: float = FLASH_SEC) -> None:
    st.session_state[f"_flash_{slot}"] = (kind, message, time.time() + ttl)


def _show_flash(slot: str) -> None:
    entry = st.session_state.get(f"_flash_{slot}")
    if entry and entry[2] > time.time():
        kind, message, _ = entry
        getattr(st, kind)(message)


@dataclass(frozen=True)
class _Widget:
    """Callback argument read from ``st.session_state`` when the callback fires, not when drawn.

    Avoids acting on a stale value when a widget changes in the same interaction as the click.
    """

    key: str
    default: object = None


def _act(slot: str, action: Callable[..., object], *args: object, done: Callable[[object], str] | None = None) -> None:
    """Widget callback: run a registry action, flash the GameError (or ``done(result)``) under ``slot``."""
    values = [st.session_state.get(a.key, a.default) if isinstance(a, _Widget) else a for a in args]
    try:
        result = action(*values)
    except (GameError, ValueError) as err:
        _flash(slot, str(err))
        return
    if done is not None:
        _flash(slot, done(result), "info", ttl=15.0)


def _live(func: Callable[..., None], settings: Settings) -> Callable[..., None]:
    return st.fragment(func, run_every=settings.refresh_sec)


# -- shared pieces --------------------------------------------------------------------------------


def render_banner(settings: Settings) -> None:
    if not settings.host_login_required:
        st.warning(NO_LOGIN_WARNING, icon="⚠️")


def render_round_status(snapshot: RoomSnapshot) -> None:
    """Round line with bonus badge, phase hint, next-bonus hint and negotiation countdown."""
    if snapshot.phase is not Phase.OVER:
        label = round_label(snapshot)
        if snapshot.multiplier > 1 and snapshot.phase is not Phase.LOBBY:
            label = label.replace(f"BONUS ×{snapshot.multiplier}", f":orange-badge[BONUS ×{snapshot.multiplier}]")
        st.markdown(f"#### {label}")
        st.caption(phase_text(snapshot.phase))
    if hint := bonus_hint(snapshot):
        st.markdown(f":orange-badge[{hint}]")
    if text := countdown_text(seconds_left(snapshot.timer_ends_at, time.time())):
        st.markdown(f"**{text}**")


def render_board(snapshot: RoomSnapshot, title_seat: str | None = None) -> None:
    """"Game over" heading, ``Group N | Group total T``, the results table and the totals footer."""
    if snapshot.phase is Phase.OVER:
        st.header("Game over", anchor=False)
    st.subheader(f"Group {snapshot.group_id} | Group total {snapshot.group_total}", anchor=False)
    render_results(snapshot, title_seat)


def render_results(snapshot: RoomSnapshot, title_seat: str | None = None) -> None:
    """The results table (bonus rows tinted, ``title_seat``'s column highlighted) and totals footer."""
    frame = results_frame(snapshot)
    if frame.empty:
        st.caption("No rounds revealed yet.")
    else:
        bonus_rows = {i for i, record in enumerate(snapshot.history) if record.multiplier > 1}
        styler = frame.style.apply(
            lambda row: [_BONUS_ROW_STYLE if row.name in bonus_rows else ""] * len(row), axis=1
        )
        if title_seat in SEATS:
            styler = styler.map(lambda _: _OWN_COLUMN_STYLE, subset=[player_label(snapshot.group_id, title_seat)])
        st.dataframe(
            styler,
            hide_index=True,
            column_config={
                # The narrowest widths that fit their contents, so a 375px phone shows every column
                # (30 + 48 + 4 x 62 = 326px). On wider screens the grid shares out the extra space equally.
                "RD": st.column_config.NumberColumn("RD", width=30),
                "Total": st.column_config.NumberColumn("Total", width=48),
                **{label: st.column_config.TextColumn(label, width=62) for label in frame.columns[2:]},
            },
        )
    st.caption(totals_line(snapshot))


# -- join -----------------------------------------------------------------------------------------


def player_page(registry: RoomRegistry, settings: Settings) -> None:
    """``/``: the join form, or the seat's game when the URL carries ``?seat=<token>``."""
    if st.query_params.get("role") == "host":  # old host links (``/?role=host``)
        st.switch_page(LOGIN_PAGE)
    if token := st.query_params.get("seat"):
        player_view(registry, settings, token)
    else:
        join_view(registry)


def join_view(registry: RoomRegistry) -> None:
    st.title(TITLE)
    if not registry.group_ids():
        st.fragment(_wait_for_groups, run_every=JOIN_POLL_SEC)(registry)
        return
    st.subheader("Pick a seat")
    st.caption("Tap a free seat to take it. You choose your name next.")
    st.fragment(seat_picker, run_every=JOIN_POLL_SEC)(registry, "join")


def seat_picker(registry: RoomRegistry, prefix: str) -> None:
    """Every group's seats as buttons: tapping a free one takes it at once (the name comes after).

    The new token goes into the page's ``?seat=`` query parameter. ``prefix`` keeps widget keys
    apart when the picker appears on more than one page.
    """
    snapshots = registry.snapshots()
    if not any(seat.is_free for snap in snapshots for seat in snap.seats):
        st.caption("No free seats: every group is full.")
    for snap in snapshots:
        st.markdown(f"**Group {snap.group_id}**")
        row = st.container(horizontal=True)
        for seat in snap.seats:
            shown = player_label(snap.group_id, seat.label)
            label = shown if seat.is_free else f"{shown} · {seat_name(seat)}"
            if row.button(label, key=f"{prefix}_{snap.group_id}_{seat.label}", disabled=not seat.is_free, width="stretch"):
                try:
                    token = registry.take_seat(snap.group_id, seat.label)
                except GameError as err:
                    st.error(f"Could not take {shown} in Group {snap.group_id}: {err}")
                else:
                    st.query_params["seat"] = token
                    st.rerun()


def _wait_for_groups(registry: RoomRegistry) -> None:
    if registry.group_ids():
        st.rerun()
    st.info(WAITING_FOR_GROUPS + " …", icon="⏳")


# -- player ---------------------------------------------------------------------------------------


def player_view(registry: RoomRegistry, settings: Settings, token: str) -> None:
    st.title(TITLE)
    try:
        group_id, seat = registry.locate(token)
        snapshot = registry.snapshot(group_id)
    except (UnknownSeat, UnknownGroup):
        st.error(
            "This seat link is no longer valid. The host may have reset the game "
            "or handed your seat to a bot."
        )
        if st.button("Back to join", key="back_to_join"):
            st.query_params.clear()
            st.rerun()
        return
    me = next(s for s in snapshot.seats if s.label == seat)
    st.header(f"{player_label(group_id, seat)} | Group {group_id}", anchor=False)
    if not me.name:
        _name_step(registry, token, snapshot.phase)
        return
    st.caption(f"Playing as **{md_escape(me.name)}**. Keep this URL: it is your seat.")
    st.html(_BIG_BUTTONS_CSS)
    if len(registry.group_ids()) > 1:
        mine, everyone = st.tabs(["My group", "All groups"])
        with mine:
            _live(_player_live, settings)(registry, token, group_id, seat)
        with everyone:
            refresh = max(settings.refresh_sec, ALL_GROUPS_REFRESH_SEC)
            st.fragment(_all_groups, run_every=refresh)(registry, group_id, seat)
    else:
        _live(_player_live, settings)(registry, token, group_id, seat)


def _name_step(registry: RoomRegistry, token: str, phase: Phase) -> None:
    st.success("This seat is yours. Choose the name the other players will see.")
    _name_form(registry, token, "")
    if phase is Phase.LOBBY and st.button("Pick another seat", key="change_seat"):
        try:
            registry.leave(token)
        except GameError as err:
            st.error(str(err))
        else:
            st.query_params.clear()
            st.rerun()


def _name_form(registry: RoomRegistry, token: str, current: str) -> None:
    with st.form("name_form", border=False):
        name = st.text_input("Your name", value=current, max_chars=30, key="name_input")
        saved = st.form_submit_button("Save name", type="primary")
    if saved:
        try:
            registry.rename(token, name)
        except GameError as err:
            st.error(str(err))
        else:
            st.rerun()


def _all_groups(registry: RoomRegistry, my_group: int, my_seat: str) -> None:
    """Every group's revealed rounds (never unrevealed cards: snapshots do not contain them)."""
    snapshots = registry.snapshots()
    st.dataframe(overview_frame(snapshots)[["Group", "Status", "Group total"]], hide_index=True)
    for snap in snapshots:
        mine = snap.group_id == my_group
        # A fixed label keeps each expander open or closed across refreshes.
        with st.expander(f"Group {snap.group_id}{' (your group)' if mine else ''}", expanded=len(snapshots) <= MAX_TABS):
            st.markdown(f"**{round_label(snap)} | Group total {snap.group_total}**")
            st.caption(seats_line(snap))
            render_results(snap, title_seat=my_seat if mine else None)


def _player_live(registry: RoomRegistry, token: str, group_id: int, seat: str) -> None:
    try:
        registry.locate(token)
        snapshot = registry.snapshot(group_id)
        choice = registry.my_choice(token)
    except (UnknownSeat, UnknownGroup):
        st.rerun()  # full rerun shows the "no longer valid" message

    if snapshot.phase is Phase.LOBBY:  # in the live part, so it disappears when the host starts
        me = next(s for s in snapshot.seats if s.label == seat)
        with st.expander("Change name"):
            _name_form(registry, token, me.name)
    render_round_status(snapshot)
    if snapshot.phase is not Phase.OVER:
        if waiting := pending_seats(snapshot):
            st.caption(f"Waiting for: {', '.join(waiting)}")
        elif snapshot.phase is Phase.OPEN:
            st.caption("Everyone has chosen. Waiting for the host to reveal.")
        is_open = snapshot.phase is Phase.OPEN
        # A horizontal container (unlike st.columns) keeps X and Y side by side on phones too.
        row = st.container(horizontal=True)
        for card in Card:
            row.button(
                card.value,
                key=f"pick_{card.value}",
                type="primary" if choice is card else "secondary",
                disabled=not is_open,
                width="stretch",
                on_click=_act,
                args=("pick", registry.submit, token, card),
            )
        if is_open:
            st.markdown(f"Your pick: **{choice}**" if choice else "You have not picked yet.")
        _show_flash("pick")
    render_board(snapshot, title_seat=seat)


# -- host -----------------------------------------------------------------------------------------


def login_page(settings: Settings, sessions: HostSessions) -> None:
    """``/login``: on success the host lands on ``/host?auth=<token>`` (valid for 12 hours)."""
    if not settings.host_login_required:
        st.switch_page(HOST_PAGE)
    if sessions.is_valid(token := st.session_state.get("host_auth")):
        st.switch_page(HOST_PAGE, query_params={"auth": token})
    st.title(TITLE)
    st.subheader("Host login")
    with st.form("host_login_form"):
        username = st.text_input("Username", key="host_username_input", autocomplete="username")
        password = st.text_input(
            "Password", type="password", key="host_password_input", autocomplete="current-password"
        )
        submitted = st.form_submit_button("Log in", type="primary")
    if submitted:
        if check_login(settings, username, password):
            token = sessions.issue()
            st.session_state["host_auth"] = token
            st.switch_page(HOST_PAGE, query_params={"auth": token})
        st.error(WRONG_LOGIN)


def host_page(registry: RoomRegistry, settings: Settings, sessions: HostSessions) -> None:
    """``/host``: controls for every group. Sends the host to ``/login`` unless logged in."""
    token = None
    if settings.host_login_required:
        token = _host_token(sessions)
        if token is None:
            st.switch_page(LOGIN_PAGE)
    title_col, logout_col = st.columns([4, 1], vertical_alignment="center")
    title_col.title(f"{TITLE} · Host", anchor=False)
    if token is not None and logout_col.button("Log out", key="host_logout"):
        sessions.revoke(token)
        st.session_state.pop("host_auth", None)
        st.switch_page(LOGIN_PAGE)
    host_view(registry, settings)


def _host_token(sessions: HostSessions) -> str | None:
    """The host's valid login token, from the URL (survives a refresh) or this session."""
    for token in (st.query_params.get("auth"), st.session_state.get("host_auth")):
        if sessions.is_valid(token):
            st.session_state["host_auth"] = token
            st.query_params["auth"] = token  # keeps the host logged in across a refresh
            return token
    st.session_state.pop("host_auth", None)
    return None


def host_view(registry: RoomRegistry, settings: Settings) -> None:
    render_banner(settings)
    group_ids = registry.group_ids()
    _setup(registry, group_ids)
    if group_ids:
        _global_controls(registry)
        _host_play(registry)
        _groups_area(registry, settings, group_ids)
    else:
        st.info("Create groups in **Setup** to begin. Players can then join from the main URL.")
    _reset_controls(registry)


def _host_play(registry: RoomRegistry) -> None:
    """Lets the host take a seat; they then play it on the normal player page."""
    token = st.query_params.get("seat")
    try:
        group_id, seat = registry.locate(token) if token else (None, None)
    except (UnknownSeat, UnknownGroup):
        group_id = seat = None
        st.query_params.pop("seat", None)  # reset, or handed to a bot
    if seat is not None:
        with st.container(border=True):
            st.markdown(f"**You play as {player_label(group_id, seat)} in Group {group_id}.**")
            st.link_button("Open my player page", f"./?seat={token}", type="primary", key="host_seat_link")
            st.caption("Opens in a new tab. You can also open that link on your phone.")
        return
    with st.expander("Play as a player"):
        st.caption("Take a seat here, then open your player page to choose your name and play.")
        seat_picker(registry, "host_join")


def _setup(registry: RoomRegistry, group_ids: list[int]) -> None:
    with st.expander("Setup", expanded=not group_ids):
        st.number_input("Number of groups", min_value=1, max_value=50, value=1, step=1, key="setup_groups")
        allowed = True
        if group_ids:
            allowed = st.checkbox(
                f"Replace the {len(group_ids)} existing group(s): all current games and results are lost",
                key="setup_replace",
            )
        st.button(
            "Create groups",
            type="primary",
            key="create_groups",
            disabled=not allowed,
            on_click=_create_groups,
            args=(registry,),
        )
        _show_flash("setup")


def _create_groups(registry: RoomRegistry) -> None:
    n = int(st.session_state.get("setup_groups", 1))
    _act("setup", registry.create_groups, n, done=lambda _: f"Created {n} group(s).")
    st.session_state["setup_replace"] = False


def _global_controls(registry: RoomRegistry) -> None:
    with st.container(border=True):
        st.markdown("**All groups**")
        cols = st.columns(3)
        for col, (label, action, help_text) in zip(
            cols,
            (("Start all", registry.start_all, START_HELP), ("Reveal all", registry.reveal_all, None),
             ("Next round all", registry.next_all, None)),
            strict=True,
        ):
            col.button(
                label,
                key=f"all_{label}",
                help=help_text,
                width="stretch",
                on_click=_act,
                args=("all", action),
                kwargs={"done": lambda skipped, label=label: skipped_text(label, skipped)},
            )
        secs_col, start_col, clear_col = st.columns([2, 1, 1], vertical_alignment="bottom")
        secs_col.number_input(
            "Negotiation timer (seconds)", min_value=10, max_value=3600, value=DEFAULT_TIMER_SEC, step=10,
            key="timer_all_secs",
        )
        start_col.button(
            "Start for all", key="timer_all_start", width="stretch",
            on_click=_act, args=("all", registry.start_timer, _Widget("timer_all_secs", DEFAULT_TIMER_SEC)),
        )
        clear_col.button("Clear", key="timer_all_clear", width="stretch", on_click=_act, args=("all", registry.clear_timer))
        _show_flash("all")


def _groups_area(registry: RoomRegistry, settings: Settings, group_ids: list[int]) -> None:
    if len(group_ids) <= MAX_TABS:
        tabs = st.tabs([f"Group {gid}" for gid in group_ids] + ["Leaderboard"])
        for tab, gid in zip(tabs, group_ids):
            with tab:
                _live(_group_panel, settings)(registry, gid)
    else:
        tabs = st.tabs(["Groups", "Leaderboard"])
        with tabs[0]:
            _live(_overview, settings)(registry)
            gid = st.selectbox("Group", group_ids, key="host_group", format_func=lambda g: f"Group {g}")
            _live(_group_panel, settings)(registry, gid)
    with tabs[-1]:
        _live(_leaderboard, settings)(registry)


def _overview(registry: RoomRegistry) -> None:
    st.dataframe(overview_frame(registry.snapshots()), hide_index=True, height=250)


def _group_panel(registry: RoomRegistry, gid: int) -> None:
    try:
        snap = registry.snapshot(gid)
    except UnknownGroup:
        st.rerun()  # groups were replaced from another host tab
    phase = snap.phase

    render_round_status(snap)
    for col, seat in zip(st.columns(4), snap.seats, strict=True):
        with col.container(border=True):
            st.markdown(f"**{player_label(gid, seat.label)}**{' 🤖' if seat.is_bot else ''}")
            st.caption(seat_status(seat, phase))

    waiting = pending_seats(snap)
    cols = st.columns(3)
    for col, (label, action, enabled, help_text) in zip(
        cols,
        (
            ("Start", registry.start, phase is Phase.LOBBY, START_HELP),
            # Reveal always has a tooltip: a button whose tooltip comes and goes is briefly drawn twice.
            ("Reveal", registry.reveal, phase is Phase.OPEN and not waiting, reveal_help(snap)),
            ("Next round", registry.next_round, phase is Phase.REVEALED, None),
        ),
        strict=True,
    ):
        col.button(
            label, key=f"{label}_{gid}", type="primary" if enabled else "secondary", disabled=not enabled,
            help=help_text, width="stretch", on_click=_act, args=(f"g{gid}", action, gid),
        )

    secs = _Widget(f"timer_secs_{gid}", DEFAULT_TIMER_SEC)
    # Hidden once a timer is running (the break has started); next_round cancels the timer.
    if phase is Phase.REVEALED and snap.next_multiplier > 1 and snap.timer_ends_at is None:
        with st.container(border=True):
            st.markdown(f"### Next round is a ×{snap.next_multiplier} bonus — start a negotiation break?")
            st.button(
                f"Start {st.session_state.get(secs.key, secs.default)}s negotiation break", key=f"break_{gid}", type="primary",
                on_click=_act, args=(f"g{gid}", registry.start_timer, secs, gid),
            )
    _show_flash(f"g{gid}")

    with st.expander("Bots and timer"):
        _bot_controls(registry, snap)
        secs_col, start_col, clear_col = st.columns([2, 1, 1], vertical_alignment="bottom")
        secs_col.number_input(
            "Timer (seconds)", min_value=10, max_value=3600, value=DEFAULT_TIMER_SEC, step=10,
            key=f"timer_secs_{gid}",
        )
        start_col.button(
            "Start timer", key=f"timer_start_{gid}", width="stretch",
            on_click=_act, args=(f"g{gid}", registry.start_timer, secs, gid),
        )
        clear_col.button(
            "Clear timer", key=f"timer_clear_{gid}", width="stretch",
            on_click=_act, args=(f"g{gid}", registry.clear_timer, gid),
        )

    render_board(snap)


def _bot_controls(registry: RoomRegistry, snap: RoomSnapshot) -> None:
    gid = snap.group_id
    strategies = list(STRATEGIES)
    free = [s.label for s in snap.seats if s.is_free]
    humans = [s.label for s in snap.seats if not s.is_free and not s.is_bot]

    fill_col, replace_col = st.columns(2)
    with fill_col:
        st.selectbox("Bot strategy for free seats", strategies, key=f"fill_strategy_{gid}")
        st.button(
            f"Fill free seats with bots ({len(free)})", key=f"fill_{gid}", disabled=not free,
            on_click=_act, args=(f"g{gid}", registry.fill_with_bots, gid, _Widget(f"fill_strategy_{gid}")),
        )
    with replace_col:
        seat_col, strat_col = st.columns(2)
        seat_col.selectbox(
            "Seat", humans or ["–"], key=f"replace_seat_{gid}", disabled=not humans,
            format_func=lambda label: player_label(gid, label) if label in SEATS else label,
        )
        strat_col.selectbox("Strategy", strategies, key=f"replace_strategy_{gid}")
        st.button(
            "Replace seat with bot", key=f"replace_{gid}", disabled=not humans,
            on_click=_act, args=(f"g{gid}", registry.replace_with_bot, gid, _Widget(f"replace_seat_{gid}"),
                  _Widget(f"replace_strategy_{gid}")),
        )


def _leaderboard(registry: RoomRegistry) -> None:
    players, groups = leaderboard_frames(registry)
    player_col, group_col = st.columns([3, 2])
    with player_col:
        st.subheader("Players")
        st.dataframe(players, hide_index=True)
    with group_col:
        st.subheader("Groups")
        st.dataframe(groups, hide_index=True)
    st.download_button(
        "Download results (JSON)",
        data=json.dumps(registry.export(), indent=2),
        file_name="wall-street-game-results.json",
        mime="application/json",
        key="download_results",
        on_click="ignore",
    )


def _reset_controls(registry: RoomRegistry) -> None:
    st.divider()
    confirmed = st.checkbox("Confirm reset", key="confirm_reset")
    st.button("Reset game", key="reset_game", disabled=not confirmed, on_click=_reset, args=(registry,))


def _reset(registry: RoomRegistry) -> None:
    registry.reset()
    st.session_state["confirm_reset"] = False
    st.session_state["setup_replace"] = False
