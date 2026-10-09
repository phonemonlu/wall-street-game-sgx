"""``/``: join a group, then play from the seat link (``/?seat=<token>``)."""

import runtime
import views

views.player_page(runtime.get_registry(), runtime.get_settings())
