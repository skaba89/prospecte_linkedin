"""P35 — Connecteurs job boards (framework extensible).

base : SearchCriteria, ConnectorResult, JobPostingData, JobConnector (ABC).
registry : CONNECTOR_REGISTRY (12 providers) + get_connector + list_connectors.
"""
from .base import ConnectorResult, JobConnector, JobPostingData, SearchCriteria
from .registry import CONNECTOR_REGISTRY, get_connector, list_connectors

__all__ = [
    "CONNECTOR_REGISTRY",
    "ConnectorResult",
    "JobConnector",
    "JobPostingData",
    "SearchCriteria",
    "get_connector",
    "list_connectors",
]
