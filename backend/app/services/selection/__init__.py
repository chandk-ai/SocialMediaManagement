"""Selection plugins — eagerly imported so @register_plugin runs at startup."""
from . import freshness  # noqa: F401
from . import per_item    # noqa: F401
from . import roundrobin  # noqa: F401
