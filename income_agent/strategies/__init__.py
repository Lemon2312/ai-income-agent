from income_agent.strategies.base import Context, Strategy
from income_agent.strategies.content_publisher import ContentPublisher
from income_agent.strategies.freelance_scout import FreelanceScout

REGISTRY: dict[str, type[Strategy]] = {
    FreelanceScout.name: FreelanceScout,
    ContentPublisher.name: ContentPublisher,
}

__all__ = ["REGISTRY", "Context", "Strategy", "ContentPublisher", "FreelanceScout"]
