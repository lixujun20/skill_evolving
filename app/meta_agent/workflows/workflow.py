from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Callable
from enum import Enum
from dataclasses import dataclass, field
from uuid import uuid4
import asyncio
from pydantic import BaseModel, Field
from datetime import datetime
import json
from app.logger import logger
from app.utils.utils import get_workflow_path_from_name

# =============================================================================
# 1. Core Data Structures
# =============================================================================

class Workflow(BaseModel):
    """Container for workflow definition"""

    model_config = {"arbitrary_types_allowed": True}

    name: str = Field()
    description: str = Field()
    metadata: Dict[str, Any] = Field(default_factory=dict)
    dependencies: List[Dict] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize workflow to dictionary"""
        workflow_code_path = get_workflow_path_from_name(self.name)
        with open(workflow_code_path, 'r', encoding='utf8') as file:
            workflow_code = file.read()
        return {
            "name": self.name,
            "workflow_code_path": workflow_code_path,
            "workflow_code": workflow_code,
            "dependencies": self.dependencies,
            "metadata": self.metadata
        }

