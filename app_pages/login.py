"""``/login``: the host's username and password."""

import runtime
import views

views.login_page(runtime.get_settings(), runtime.get_host_sessions())
