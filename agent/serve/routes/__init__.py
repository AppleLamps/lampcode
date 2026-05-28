"""HTTP route mixins for agent serve."""

from agent.serve.routes.admin_routes import AdminRoutesMixin
from agent.serve.routes.auth_routes import AuthRoutesMixin
from agent.serve.routes.ide_routes import IdeRoutesMixin
from agent.serve.routes.thread_routes import ThreadRoutesMixin

__all__ = [
    "AdminRoutesMixin",
    "AuthRoutesMixin",
    "IdeRoutesMixin",
    "ThreadRoutesMixin",
]
