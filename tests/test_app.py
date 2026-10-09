from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import views
import wallstreet.room
from wallstreet.errors import UnknownSeat
from wallstreet.game import Phase
from wallstreet.room import RoomRegistry
from wallstreet.scoring import SEATS, Card

pytestmark = pytest.mark.usefixtures("fixed_bots")  # deterministic "Always X" / "Always Y" bots

APP = str(Path(__file__).resolve().parent.parent / "app.py")
SCREENSHOT_GAME = ["XYXX", "YXYY", "YYXX", "XXXY", "XYYY", "YXXY", "XYYY", "YXXX", "XYYY", "XYYY"]


class RecordingRegistry(RoomRegistry):
    """The app's registry, captured so tests can set up game state without clicking through it."""

    instances: list["RecordingRegistry"] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        RecordingRegistry.instances.append(self)


@pytest.fixture(autouse=True)
def fresh_app(monkeypatch: pytest.MonkeyPatch):
    for key in ("WSG_HOST_USERNAME", "WSG_HOST_PASSWORD", "WSG_REFRESH_SEC", "WSG_ROUNDS"):
        monkeypatch.delenv(key, raising=False)
    RecordingRegistry.instances = []
    monkeypatch.setattr(wallstreet.room, "RoomRegistry", RecordingRegistry)
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


def run(**params: str) -> AppTest:
    """The player page (``/``)."""
    at = AppTest.from_file(APP, default_timeout=10)
    at.query_params.update(params)
    return at.run()


def run_page(page: str, **params: str) -> AppTest:
    """Open one of the app's pages (``views.LOGIN_PAGE`` / ``views.HOST_PAGE``) in a new session."""
    at = AppTest.from_file(APP, default_timeout=10)
    at.run()  # st.navigation registers the pages on the first run
    at.switch_page(page)
    at.query_params.update(params)
    return at.run()


def run_host(**params: str) -> AppTest:
    return run_page(views.HOST_PAGE, **params)


def on_login_page(at: AppTest) -> bool:
    return bool(at.text_input) and at.text_input[0].key == "host_username_input"


def app_registry() -> RoomRegistry:
    """The registry the app created (call after at least one run)."""
    assert len(RecordingRegistry.instances) == 1, "the registry must be a cached singleton"
    return RecordingRegistry.instances[0]


def button_keys(at: AppTest) -> list[str]:
    return [button.key for button in at.button]


def texts(elements) -> list[str]:
    return [element.value for element in elements]


def play(reg: RoomRegistry, tokens: dict[str, str], games: list[str], group_id: int = 1) -> None:
    """Start (if needed) and play ``games`` (one "XYXX" string per round) via player tokens."""
    if reg.snapshot(group_id).phase is Phase.LOBBY:
        reg.start(group_id)
    for i, cards in enumerate(games):
        if i:
            reg.next_round(group_id)
        for seat, card in zip(SEATS, cards, strict=True):
            reg.submit(tokens[seat], Card(card))
        reg.reveal(group_id)


def screenshot_registry() -> tuple[RoomRegistry, dict[str, str]]:
    reg = RoomRegistry()
    reg.create_groups(1)
    tokens = {seat: reg.join(1, seat, f"Player {seat}") for seat in SEATS}
    play(reg, tokens, SCREENSHOT_GAME)
    return reg, tokens


# -- pure helpers -------------------------------------------------------------------------------


def test_results_frame_and_totals_line_for_screenshot_game():
    reg, _ = screenshot_registry()
    snap = reg.snapshot(1)
    frame = views.results_frame(snap)
    assert list(frame.columns) == ["RD", "Total", "P1", "P2", "P3", "P4"]
    assert list(frame["RD"]) == list(range(1, 11))
    assert frame.iloc[0].to_dict() == {"RD": 1, "Total": 0, "P1": "X +10", "P2": "Y -30", "P3": "X +10", "P4": "X +10"}
    row8 = frame[frame["RD"] == 8].iloc[0]
    assert row8["P1"] == "Y -150" and row8["P2"] == "X +50" and row8["Total"] == 0
    row10 = frame[frame["RD"] == 10].iloc[0]
    assert row10["P1"] == "X +300" and row10["P4"] == "Y -100"
    assert list(frame["Total"]) == [record.group_total for record in snap.history]
    assert frame["Total"].sum() == snap.group_total == 0
    assert views.totals_line(snap) == "P1: 270 P2: -90 P3: -50 P4: -130"


def test_results_frame_only_covers_revealed_rounds():
    reg = RoomRegistry()
    reg.create_groups(1)
    tokens = {seat: reg.join(1, seat, seat) for seat in SEATS}
    snap = reg.snapshot(1)
    assert views.results_frame(snap).empty
    assert list(views.results_frame(snap).columns) == ["RD", "Total", *SEATS]
    assert views.totals_line(snap) == "P1: 0 P2: 0 P3: 0 P4: 0"
    play(reg, tokens, ["YYYY"])
    reg.next_round(1)
    for seat in ("P1", "P2", "P3"):  # round 2 open with cards in: still hidden
        reg.submit(tokens[seat], Card.X)
    frame = views.results_frame(reg.snapshot(1))
    assert frame.to_dict("records") == [{"RD": 1, "Total": 40, "P1": "Y +10", "P2": "Y +10", "P3": "Y +10", "P4": "Y +10"}]


def test_round_label_bonus_hint_and_countdown():
    reg = RoomRegistry()
    reg.create_groups(1)
    tokens = {seat: reg.join(1, seat, seat) for seat in SEATS}
    assert views.round_label(reg.snapshot(1)) == "Lobby"
    play(reg, tokens, ["YYYY"] * 4)
    snap = reg.snapshot(1)
    assert views.round_label(snap) == "Round 4 of 10"
    assert views.bonus_hint(snap) == "Next round is a ×3 bonus"
    reg.next_round(1)
    snap = reg.snapshot(1)
    assert views.round_label(snap) == "Round 5 of 10 · BONUS ×3"
    assert views.bonus_hint(snap) is None
    assert views.pending_seats(snap) == list(SEATS)
    assert views.seconds_left(None, 100.0) is None
    assert views.seconds_left(165.0, 100.0) == 65
    assert views.seconds_left(90.0, 100.0) == 0
    assert views.countdown_text(65) == "⏱ Negotiation: 1:05 left"
    assert views.countdown_text(0) == "⏱ Negotiation time is up"
    assert views.skipped_text("Start all", {}) == "Start all: done for every group."
    assert "- **Group 2**: nope" in views.skipped_text("Start all", {2: "nope"})


def test_player_names_cannot_inject_markdown():
    assert views.md_escape("Ann (B.)") == "Ann (B.)"
    assert views.md_escape("![x](https://evil.example/x.png)") == r"\!\[x\](https\://evil.example/x.png)"
    assert views.md_escape(":red[# big] **b**") == r"\:red\[\# big\] \*\*b\*\*"
    reg = RoomRegistry()
    reg.create_groups(1)
    reg.join(1, "P1", "[click](https://evil.example)")
    seat = reg.snapshot(1).seats[0]
    assert views.seat_status(seat, Phase.LOBBY) == r"\[click\](https\://evil.example) (human)"


def test_bonus_hint_respects_short_games():
    reg = RoomRegistry(rounds=4)
    reg.create_groups(1)
    tokens = {seat: reg.join(1, seat, seat) for seat in SEATS}
    play(reg, tokens, ["YYYY"] * 4)
    assert reg.snapshot(1).phase is Phase.OVER
    assert views.bonus_hint(reg.snapshot(1)) is None
    assert views.round_label(reg.snapshot(1)) == "Game over"


# -- join ---------------------------------------------------------------------------------------


def test_join_view_without_groups():
    at = run()
    assert not at.exception
    assert texts(at.title) == ["Wall Street Game"]
    assert any(views.WAITING_FOR_GROUPS in text for text in texts(at.info))
    assert not at.button


def test_host_creates_groups_and_join_view_lists_free_seats():
    host = run(role="host")
    host.number_input(key="setup_groups").set_value(2)
    host.button(key="create_groups").click().run()
    assert not host.exception
    assert app_registry().group_ids() == [1, 2]
    assert [tab.label for tab in host.tabs] == ["Group 1", "Group 2", "Leaderboard"]

    join = run()
    assert join.selectbox(key="join_group").options == ["Group 1 · 4 free seat(s)", "Group 2 · 4 free seat(s)"]
    assert join.radio(key="join_seat_1").options == list(SEATS)
    app_registry().join(1, "P1", "Ann")
    join = run()
    assert join.radio(key="join_seat_1").options == ["P1 (taken)", "P2", "P3", "P4"]
    assert join.radio(key="join_seat_1").value == "P2"  # first free seat preselected


def test_player_joins_and_token_lands_in_query_params():
    run().run()  # warm the cache
    app_registry().create_groups(1)
    at = run()
    at.radio(key="join_seat_1").set_value("P2")
    at.text_input(key="join_name").input("Ann")
    at.button(key="join_button").click().run()
    assert not at.exception
    token = at.query_params["seat"]
    token = token[-1] if isinstance(token, list) else token  # older AppTest returns a list
    assert app_registry().locate(token) == (1, "P2")
    assert texts(at.header) == ["P2 | Group 1"]
    assert "Ann" in at.caption[0].value


def test_join_errors_are_friendly():
    run()
    app_registry().create_groups(1)
    at = run()
    at.button(key="join_button").click().run()  # blank name
    assert texts(at.error) == ["Could not join: Please enter your name."]
    app_registry().join(1, "P1", "Bob")  # someone else grabs P1 meanwhile
    at.text_input(key="join_name").input("Ann")
    at.radio(key="join_seat_1").set_value("P1")
    at.button(key="join_button").click().run()
    assert any("already taken" in text for text in texts(at.error))
    assert "seat" not in at.query_params


# -- host ---------------------------------------------------------------------------------------


def test_host_page_warns_without_login():
    at = run_host()
    assert not at.exception
    assert texts(at.title) == ["Wall Street Game · Host"]
    assert texts(at.warning) == [views.NO_LOGIN_WARNING]
    assert at.button(key="create_groups")
    assert "host_logout" not in button_keys(at)  # nothing to log out of


def test_login_page_goes_straight_to_host_without_login():
    at = run_page(views.LOGIN_PAGE)
    assert not at.exception
    assert at.button(key="create_groups")


def test_player_page_has_no_host_link():
    at = run()
    assert not at.sidebar.markdown and not at.sidebar.caption
    assert all("host" not in md.value.lower() for md in at.markdown)


def test_old_host_link_goes_to_login(monkeypatch: pytest.MonkeyPatch):
    set_login(monkeypatch)
    assert on_login_page(run(role="host"))


def set_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WSG_HOST_USERNAME", "admin")
    monkeypatch.setenv("WSG_HOST_PASSWORD", "s3cret!")


def log_in(at: AppTest, username: str, password: str) -> AppTest:
    at.text_input(key="host_username_input").input(username)
    at.text_input(key="host_password_input").input(password)
    return at.button[0].click().run()  # the form's submit button


def test_host_page_sends_strangers_to_login(monkeypatch: pytest.MonkeyPatch):
    set_login(monkeypatch)
    for at in (run_host(), run_host(auth="made-up")):
        assert not at.exception
        assert on_login_page(at)
        assert "create_groups" not in button_keys(at)


def test_login_rejects_wrong_credentials(monkeypatch: pytest.MonkeyPatch):
    set_login(monkeypatch)
    at = run_page(views.LOGIN_PAGE)
    assert texts(at.subheader) == ["Host login"]
    for username, password in [("admin", "wrong"), ("someone", "s3cret!"), ("", "")]:
        log_in(at, username, password)
        assert texts(at.error) == [views.WRONG_LOGIN]
        assert "create_groups" not in button_keys(at)


def test_login_opens_host_page_and_survives_refresh(monkeypatch: pytest.MonkeyPatch):
    set_login(monkeypatch)
    at = log_in(run_page(views.LOGIN_PAGE), "admin", "s3cret!")
    assert not at.exception
    assert at.button(key="create_groups")
    assert not at.warning  # a login is set: no warning banner
    token = at.query_params["auth"]
    assert token
    at.run()
    assert at.button(key="create_groups")
    refreshed = run_host(auth=token)  # a browser refresh is a new session with the same URL
    assert refreshed.button(key="create_groups")
    assert refreshed.query_params["auth"] == token


def test_logout_ends_the_login(monkeypatch: pytest.MonkeyPatch):
    set_login(monkeypatch)
    token = log_in(run_page(views.LOGIN_PAGE), "admin", "s3cret!").query_params["auth"]
    # AppTest keeps running the page it was switched to by the test, not one the script switched
    # to, so open /host directly (as a refreshed browser would) before clicking Log out.
    at = run_host(auth=token)
    at.button(key="host_logout").click().run()
    assert not at.exception
    assert "host_auth" not in at.session_state
    assert on_login_page(run_host(auth=token))  # the old link no longer works


def test_host_takes_a_seat_and_gets_a_player_link():
    at = run_host()
    reg = app_registry()
    reg.create_groups(2)
    at.run()
    assert not at.get("link_button")
    at.button(key="host_join_2_P3").click().run()
    assert not at.exception
    token = at.query_params["seat"]
    assert reg.locate(token) == (2, "P3")
    assert any("You play as P7 in Group 2" in md.value for md in at.markdown)
    (link,) = at.get("link_button")
    assert link.proto.url == f"./?seat={token}"
    assert "host_join_2_P3" not in button_keys(at)

    player = run(seat=token)  # the link opens the normal player page for that seat
    assert texts(player.header) == ["P7 | Group 2"]
    assert player.text_input(key="name_input")  # the host names the seat there

    reg.reset()  # the seat is gone: the host can join again
    reg.create_groups(1)
    at.run()
    assert not at.get("link_button")
    assert "seat" not in at.query_params
    assert at.button(key="host_join_1_P1")


def test_reset_disabled_until_confirmed():
    at = run_host()
    app_registry().create_groups(2)
    at.run()
    assert at.button(key="reset_game").disabled
    at.checkbox(key="confirm_reset").check().run()
    assert not at.button(key="reset_game").disabled
    at.button(key="reset_game").click().run()
    assert app_registry().group_ids() == []
    assert not at.checkbox(key="confirm_reset").value
    assert at.button(key="reset_game").disabled


def test_recreating_groups_needs_confirmation():
    at = run_host()
    at.button(key="create_groups").click().run()
    assert app_registry().group_ids() == [1]
    assert at.button(key="create_groups").disabled
    at.number_input(key="setup_groups").set_value(3)
    at.checkbox(key="setup_replace").check().run()
    at.button(key="create_groups").click().run()
    assert app_registry().group_ids() == [1, 2, 3]


def test_host_group_controls_bots_and_global_actions():
    at = run_host()
    reg = app_registry()
    reg.create_groups(2)
    token = reg.join(1, "P1", "Ann")
    at.run()
    assert at.button(key="Reveal_1").disabled and at.button(key="Next round_1").disabled
    at.button(key="all_Start all").click().run()
    assert any("skipped 2 group(s)" in text for text in texts(at.info))

    assert at.selectbox(key="fill_strategy_1").options[:2] == ["Random", "Smart"]
    at.selectbox(key="fill_strategy_1").set_value("Smart")
    at.button(key="fill_1").click().run()
    assert [s.is_bot for s in reg.snapshot(1).seats] == [False, True, True, True]
    at.button(key="Start_1").click().run()
    assert reg.snapshot(1).phase is Phase.OPEN
    assert at.button(key="Reveal_1").disabled  # P1 has not chosen
    assert "Ann (human) · ⏳ pending" in texts(at.caption)

    at.selectbox(key="replace_strategy_1").set_value("Smart")
    at.button(key="replace_1").click().run()  # P1 is the only human seat
    with pytest.raises(UnknownSeat):
        reg.locate(token)
    assert not at.button(key="Reveal_1").disabled
    at.button(key="Reveal_1").click().run()
    assert reg.snapshot(1).phase is Phase.REVEALED
    assert at.dataframe  # the board
    assert "Group 1 | Group total 40" in texts(at.subheader)  # an all-smart group cooperates


def test_host_bonus_prompt_starts_group_timer():
    at = run_host()
    reg = app_registry()
    reg.create_groups(1)
    reg.fill_with_bots(1, "Always Y")
    reg.start(1)
    for i in range(4):
        if i:
            reg.next_round(1)
        reg.reveal(1)
    at.run()
    assert any("Next round is a ×3 bonus — start a negotiation break?" in md.value for md in at.markdown)
    at.button(key="break_1").click().run()
    assert reg.snapshot(1).timer_ends_at is not None
    assert any("Negotiation:" in md.value for md in at.markdown)
    at.button(key="timer_clear_1").click().run()
    assert reg.snapshot(1).timer_ends_at is None


def test_host_many_groups_use_a_selectbox_and_leaderboard_exports():
    at = run_host()
    app_registry().create_groups(9)
    at.run()
    assert [tab.label for tab in at.tabs] == ["Groups", "Leaderboard"]
    at.selectbox(key="host_group").set_value(7).run()
    assert "Group 7 | Group total 0" in texts(at.subheader)
    assert at.get("download_button")


# -- player -------------------------------------------------------------------------------------


def test_player_page_unknown_token():
    at = run(seat="not-a-token")
    assert not at.exception
    assert "no longer valid" in at.error[0].value
    at.button(key="back_to_join").click().run()
    assert at.query_params == {}
    assert any(views.WAITING_FOR_GROUPS in text for text in texts(at.info))


def test_player_picks_inside_fragment_and_sees_board_at_game_over():
    run()
    reg = app_registry()
    reg.create_groups(1)
    tokens = {seat: reg.join(1, seat, f"Player {seat}") for seat in SEATS}
    at = run(seat=tokens["P1"])
    assert at.button(key="pick_X").disabled  # lobby
    reg.start(1)
    at.run()
    at.button(key="pick_X").click().run()
    assert reg.my_choice(tokens["P1"]) is Card.X
    assert at.button(key="pick_X").proto.type == "primary"
    at.button(key="pick_Y").click().run()
    assert reg.my_choice(tokens["P1"]) is Card.Y

    for seat, card in zip(SEATS, SCREENSHOT_GAME[0], strict=True):
        reg.submit(tokens[seat], Card(card))
    reg.reveal(1)
    reg.next_round(1)
    play(reg, tokens, SCREENSHOT_GAME[1:])
    reg_snapshot = reg.snapshot(1)
    assert reg_snapshot.phase is Phase.OVER
    at.run()
    assert texts(at.header) == ["P1 | Group 1", "Game over"]
    assert texts(at.subheader) == ["Group 1 | Group total 0"]
    assert "P1: 270 P2: -90 P3: -50 P4: -130" in texts(at.caption)
    assert not at.button  # no X/Y buttons once the game is over
    assert len(at.dataframe[0].value) == 10


def test_player_seat_handed_to_bot_shows_error():
    run()
    reg = app_registry()
    reg.create_groups(1)
    token = reg.join(1, "P3", "Cy")
    at = run(seat=token)
    assert texts(at.header) == ["P3 | Group 1"]
    reg.replace_with_bot(1, "P3", "Random")
    at.run()
    assert "no longer valid" in at.error[0].value
