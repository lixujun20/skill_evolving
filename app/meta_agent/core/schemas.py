# app/meta_agent/schemas.py
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator
from app.config import config
from datetime import datetime
import uuid
from app.meta_agent.workflows.workflow import Workflow
from enum import Enum
import json
import os

class ToolType(str, Enum):
    LLM_TOOL = 'llm_tool'
    # PYTHON_TOOL = 'python_tool'

class ToolSchema(BaseModel):
    # tool_code_path: str = Field(default=None)
    tool_type: ToolType = Field()
    name: str = Field()
    description: str = Field()
    parameters: str = Field()
    provider_id: str = Field(default=None)
    output_schema: str = Field(default=None)

    @classmethod
    @field_validator("parameters")
    def parameters_is_dict(cls, parameters):
        try:
            json.loads(parameters)
        except:
            raise ValueError("parameters must be a dict.")
        return parameters
    
    @classmethod
    @field_validator("output_schema")
    def output_schema_is_dict(cls, output_schema):
        try:
            json.loads(output_schema)
        except:
            raise ValueError("output_schema must be a dict.")
        return output_schema

    # @classmethod
    # @field_validator("tool_code_path")
    # def name_path_consistent(cls, tool_code_path, values):
    #     # tool_name = values.data.get('name')
    #     # if f'{tool_name}.py' != tool_code_path.split('/')[-1]:
    #     #     raise ValueError("Tool name and tool_code_path must be consistent. Got name={tool_name} and tool_code_path={tool_code_path}")
    #     # return tool_code_path
    #     if not tool_code_path.endswith('.py'):
    #         raise ValueError("Tool code path must end with .py")
    #     if not os.path.exists(tool_code_path):
    #         raise ValueError("Tool code path does not exist.")
    #     return tool_code_path


class NodeData(BaseModel):
    pass

class ToolData(NodeData):
    tool: ToolSchema

# class WorkflowSchema(BaseModel):
#     name: str
#     description: str
#     workflow_code_path: str = Field(default=None)
#     dependencies: List[Dict[str, str]] = Field(default_factory=list)

class WorkflowData(NodeData):
    workflow: Workflow

class HierarchicalNode(BaseModel):
    name: str
    path: str
    is_folder: bool
    description: str
    labels: List[str] = Field(default_factory=list)
    children: Dict[str, Union["HierarchicalNode", str]] = Field(default_factory=dict)
    parent: Optional[Union["HierarchicalNode", str]] = None
    data: Optional[NodeData] = None
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()))
    valid: bool = Field(default=True)
    last_modified_time: float = Field(default_factory=datetime.now().timestamp)

    @model_validator(mode="after")
    def validate_is_folder_for_root(cls, instance):
        # print('VALUES:{}'.format(values))
        path = instance.path
        if path == "/" and not instance.is_folder:
            raise ValueError("If path is '/', is_folder must be True.")
        return instance
    
    @model_validator(mode="after")
    def validate_data_accordance_with_is_folder(cls, instance):
        # print('INSTANCE:{}'.format(instance))
        is_folder = instance.is_folder
        data = instance.data
        if is_folder and data is not None:
            raise ValueError("If is_folder is True, data must be None.")
        if not is_folder and data is None:
            raise ValueError("If is_folder is False, data must be provided.")
        return instance
    
    def __repr__(self):
        return f"""
[{self.name}{'/' if self.is_folder else ''}]
{' |'.join(['#' + x for x in self.labels])}
{self.description}
"""
    
    def serialize(self):
        return {
            'name': self.name,
            'path': self.path,
            'is_folder': self.is_folder,
            'description': self.description,
            'labels': self.labels,
            'children': {k: v.id for k, v in self.children.items()},
            'parent': self.parent.id if self.parent else None,
            'data': self.data,
            'id': self.id,
            'last_modified_time': self.last_modified_time
        }
    
    @classmethod
    def deserialize(cls, data: Dict[str, Any]):
        return cls(
            name = data['name'],
            path = data['path'],
            is_folder = data['is_folder'],
            description = data['description'],
            labels = data['labels'],
            children = data['children'],
            parent = data['parent'],
            data = data['data'],
            id = data['id'],
            last_modified_time = data['last_modified_time']
        )

    def detach(self):
        return HierarchicalNode.deserialize(self.serialize())

    def value_eq(self, other):
        if not isinstance(other, HierarchicalNode):
            return False
        s1 = self.serialize()
        s2 = other.serialize()
        s1.pop('id')
        s1.pop('parent')
        s1.pop('children')
        s1.pop('valid')
        s1.pop('last_modified_time')
        s2.pop('id')
        s2.pop('parent')
        s2.pop('children')
        s2.pop('valid')
        s2.pop('last_modified_time')
        return s1 == s2
    
class WorkflowHistoryItem(BaseModel):
    query: str = None
    guideline: str = None
    executions: List[dict] = Field(default_factory=list)
    dify_dsl: dict = Field(default_factory=dict)
    # @model_validator(mode='after')
    # def validate_workflow_history_item(self):
    #     try:
    #         import ast
    #         ast.parse(self.guideline)
    #     except SyntaxError as e:
    #         raise ValueError(f"Workflow code has syntax errors: {e}")
        
    #     for execution in self.executions:
    #         assert set(execution.keys()) == {'toolcall', 'result'}, "executions has invalid format: should has key \"toolcall\" and \"result\". Got {}".format(execution)

    #     return self