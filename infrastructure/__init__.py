"""AWS CDK infrastructure for isolated Token Budget Platform environments."""

from .config import EnvironmentConfig, InfrastructureConfigurationError

__all__ = [
    "EnvironmentConfig",
    "InfrastructureConfigurationError",
    "TokenBudgetPlatformStack",
]


def __getattr__(name: str):
    if name == "TokenBudgetPlatformStack":
        from .stacks import TokenBudgetPlatformStack

        return TokenBudgetPlatformStack
    raise AttributeError(name)
