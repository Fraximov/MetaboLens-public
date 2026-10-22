"""Trusted, versioned adapters; job parameters never import executable code."""
from services.engines.base import EngineContext, EngineLaunch
from services.engines.masscube import prepare_masscube
from services.engines.msdial import prepare_msdial

# Keep old versioned entries available for jobs already queued against them.
ENGINE_ADAPTERS = {'msdial5-v1': prepare_msdial, 'masscube-v1': prepare_masscube}

__all__ = ['ENGINE_ADAPTERS', 'EngineContext', 'EngineLaunch']
