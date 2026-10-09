"""``/host``: host controls (sends the host to ``/login`` first when a login is configured)."""

import runtime
import views

views.host_page(runtime.get_registry(), runtime.get_settings(), runtime.get_host_sessions())
