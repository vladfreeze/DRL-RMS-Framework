from pydantic import BaseModel


class ResourceQuota(BaseModel):
    cpu: float = 1.0
    memory: float = 1.0


class Resources(BaseModel):
    resourceQuota: ResourceQuota = ResourceQuota(cpu=1.0, memory=1.0)


class AgentDTO(BaseModel):
    scope: str
    name: str = ""
    state: str = ""
    node_name: str = ""
    namespace: str
    deployment_name: str = ""
    resources: Resources = Resources()
    restarts: int = 0
    age: float = 0
