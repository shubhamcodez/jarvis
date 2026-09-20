"""Agents: supervisor, desktop, coding, shell, finance, and router graph (LangGraph)."""

__all__ = [
    "run_desktop_agent",
    "run_finance_agent",
    "create_router_graph",
    "supervisor_decision",
    "compute_supervisor_decision",
]


def __getattr__(name: str):
    if name == "create_router_graph":
        from .router import create_router_graph

        return create_router_graph
    if name in ("supervisor_decision", "compute_supervisor_decision"):
        from . import supervisor

        return getattr(supervisor, name)
    if name == "run_desktop_agent":
        from .desktop_agent import run_desktop_agent

        return run_desktop_agent
    if name == "run_finance_agent":
        from .finance_agent import run_finance_agent

        return run_finance_agent
    raise AttributeError(name)
