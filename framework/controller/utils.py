from fastapi import  HTTPException
from kubernetes import client

from models import AgentDTO, Resources
from rms_config import rms_config
import json
rms_namespace = rms_config.get("rms_namespace", "resource-management")


def create_agent_crd(agent):
    # Create an API client
    api_instance = client.CustomObjectsApi()

    # Trim the metadata name to remove any leading or trailing spaces
    agent["metadata"]["name"] = agent["metadata"]["name"].strip()
    agent["spec"]["nodeName"] = agent["spec"]["nodeName"].strip()
    print(agent)
    try:
        api_instance.get_namespaced_custom_object(
            group="resourcemanagement-v.io",
            version="v1",
            namespace=agent["spec"]["namespace"],
            plural="agents",
            name=agent["metadata"]["name"],
        )
        api_instance.patch_namespaced_custom_object(
            group="resourcemanagement-v.io",
            version="v1",
            namespace=agent["spec"]["namespace"],
            plural="agents",
            name=agent["metadata"]["name"],
            body=agent,
        )
        print("Resource limits updated successfully")
    except client.exceptions.ApiException as e:
        if e.status == 404:
            api_instance.create_namespaced_custom_object(
                group="resourcemanagement-v.io",
                version="v1",
                namespace=agent["spec"]["namespace"],
                plural="agents",
                body=agent,
            )
            print("Resource agent created successfully")
        else:
            print(e)
            raise


def prepare_agent(agent: AgentDTO):
    # Create an API client
    orderName = ""
    if agent.scope == "node":
        orderName = f"{agent.scope}-{agent.node_name}"
    elif agent.scope == "namespace":
        orderName = f"{agent.scope}-{agent.namespace}"
    elif agent.scope == "deployment":
        orderName = f"{agent.scope}-{agent.namespace}-{agent.deployment_name}"
    agent.name = orderName

    # Define the custom resource
    custom_resource = {
        "apiVersion": "resourcemanagement-v.io/v1",
        "kind": "Agent",
        "metadata": {"name": agent.name},
        "spec": {
            "status": "pending",
            "scope": agent.scope,
            "nodeName": agent.node_name,
            "namespace": agent.namespace,
            "deploymentName": agent.deployment_name,
            "resources": {
                "resourceQuota": {
                    "cpu": (
                        agent.resources.resourceQuota.cpu
                        if agent.resources and agent.resources.resourceQuota
                        else 1.0
                    ),
                    "memory": (
                        agent.resources.resourceQuota.memory
                        if agent.resources and agent.resources.resourceQuota
                        else 1.0
                    ),
                },
            },
            "restarts": agent.restarts,
        },
    }
    return agent, custom_resource


### LLM


def get_llm_response(prompt, openai_client):
    # Replace with actual LLM API call
    print(
        ">>>>>>>>>>>>>>>>>>>>>>>>>>>>EXECUTING OPENAI LLM<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<"
    )
    completion = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "Convert user input into a JSON dictionary with this structure: \n\n"
                '"spec": {\n'
                '    "scope": string (specified scope),\n'
                '    "nodeName": string (if specified) or empty string "",\n'
                ",\n"
                '    "namespace": string (specified namespace) or empty string "",\n'
                '    "deploymentName": string (if specified) or empty string "",\n'
                ",\n"
                '    "resources": {\n'
                '        "resourceQuota": {\n'
                '            "cpu": float (if specified) or 1.0,\n'
                "            \"memory\": float (if specified) or '1.0'\n"
                "        }\n"
                "    }\n"
                "}\n\n"
                "Ensure to parse the input command and extract details into the dictionary accurately. Use empty string "
                " instead of None for values that are not specified.",
            },
            {"role": "user", "content": prompt},
        ],
    )
    print(completion)
    return completion


def extract_prompt(data: dict) -> str:
    prompt = data.get("prompt")
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")
    return prompt

def parse_and_validate_llm_response(llm_response) -> dict:
    content = llm_response.choices[0].message.content
    json_string = content.strip("```json").strip("```").strip()
    order = json.loads(json_string)
    specs = order.get("spec", {})

    scope = specs.get("scope")
    namespace = specs.get("namespace", rms_namespace)
    node = specs.get("nodeName", "")
    deployment = specs.get("deploymentName", "")

    if not namespace and scope == "namespace":
        raise HTTPException(status_code=422, detail="Namespace required for 'namespace' scope.")
    if not namespace and scope in ["node", "deployment"]:
        print("No namespace provided; using default")
        namespace = rms_namespace
    if not node and scope == "node":
        raise HTTPException(status_code=422, detail="Node name required for 'node' scope.")
    if not deployment and scope == "deployment":
        raise HTTPException(status_code=422, detail="Deployment name required for 'deployment' scope.")

    specs["namespace"] = namespace  
    return specs

def build_agent_from_specs(specs: dict) -> AgentDTO:
    return AgentDTO(
        scope=specs.get("scope", ""),
        node_name=specs.get("nodeName", ""),
        namespace=specs.get("namespace", ""),
        deployment_name=specs.get("deploymentName", ""),
        resources=Resources(
            resourceQuota=specs.get("resources", {}).get("resourceQuota", {})
        ),
        restarts=specs.get("restarts", 0),
    )
