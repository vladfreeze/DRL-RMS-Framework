import os
import subprocess
import warnings

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from kubernetes import client, config
from openai import OpenAI
from pydantic import ValidationError
from utils import create_agent_crd, get_llm_response, prepare_agent, extract_prompt, parse_and_validate_llm_response, build_agent_from_specs

from models import AgentDTO
from rms_config import rms_config

# Suppress the specific deprecation warning from urllib3
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    message="HTTPResponse.getheaders() is deprecated and will be removed in urllib3 v2.1.0. Instead access HTTPResponse.headers directly.",
)

app = FastAPI()
openai_client = OpenAI()

namespaces = rms_config.get("target_namespaces", ["sim-env"])

rms_namespace = rms_config.get("rms_namespace", "resource-management")
# Serve static files
frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "frontend"))
app.mount("/frontend", StaticFiles(directory=frontend_path, html=True), name="frontend")

# Load Kubernetes configuration
if os.path.exists(os.path.expanduser("~/.kube/config")):
    config.load_kube_config()
else:
    config.load_incluster_config()


@app.get("/kubectl-get-ao", response_class=PlainTextResponse)
async def kubectl_get_ao():
    try:
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "ao",
                "-A",
                "-o",
                "custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,STATUS:spec.status,SCOPE:.spec.scope,CPU_QUOTA:.spec.resources.resourceQuota.cpu,MEMORY_QUOTA:.spec.resources.resourceQuota.memory",
            ],
            capture_output=True,
            text=True,
        )
        return result.stdout
    except Exception as e:
        return str(e)


@app.post("/delete-ao")
async def delete_ao(request: Request):
    form_data = await request.form()
    namespace = form_data.get("namespace")
    name = form_data.get("name")
    try:
        result = subprocess.run(
            ["kubectl", "delete", "ao", name, "-n", namespace],
            capture_output=True,
            text=True,
        )
        return PlainTextResponse(result.stdout)
    except Exception as e:
        return PlainTextResponse(str(e), status_code=500)


@app.put("/set-limits")
async def set_resource_limits(agent: AgentDTO, request: Request):
    try:
        # Log the incoming request data
        print(f"Received request to set limits: {agent}")

        agent, custom_resource = prepare_agent(agent)
        create_agent_crd(custom_resource)

        return {"message": "Resource agent saved successfully"}
    except ValidationError as ve:
        print(f"Validation error: {ve.errors()}")
        raise HTTPException(status_code=422, detail=f"Validation error: {ve.errors()}")
    except client.exceptions.ApiException as e:
        print(f"API exception: {e}")
        print(f"API exception: {e.reason}")
        raise HTTPException(status_code=e.status, detail=f"API exception: {e.reason}")
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")




@app.post("/llm")
async def llm(request: Request):
    try:
        print("Received request LLM to generate an agent")
        data = await request.json()
        prompt = extract_prompt(data)
        llm_response = get_llm_response(prompt, openai_client)
        specs = parse_and_validate_llm_response(llm_response)
        agent = build_agent_from_specs(specs)
        agent, custom_resource = prepare_agent(agent)
        create_agent_crd(custom_resource)

        return JSONResponse(content={"message": "Agent created successfully"}, status_code=200)

    except ValidationError as ve:
        print(f"Validation error: {ve.errors()}")
        raise HTTPException(status_code=422, detail=f"Validation error: {ve.errors()}")
    except client.exceptions.ApiException as e:
        print(f"API exception: {e.reason}")
        raise HTTPException(status_code=e.status, detail=f"API exception: {e}")
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@app.get("/", response_class=HTMLResponse)
@app.get("/llm-form", response_class=HTMLResponse)
async def llm_form():
    with open(os.path.join(frontend_path, "llm.html")) as f:
        return HTMLResponse(content=f.read(), status_code=200)


@app.get("/get-deployments", response_class=PlainTextResponse)
async def get_deployments():
    try:

        all_results = []

        for namespace in namespaces:
            result = subprocess.run(
                [
                    "kubectl",
                    "get",
                    "deployments",
                    "-n",
                    namespace,
                    "-o",
                    "custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,READY:.status.readyReplicas,UP-TO-DATE:.status.updatedReplicas,AVAILABLE:.status.availableReplicas,AGE:.metadata.creationTimestamp,CONTAINERS:.spec.template.spec.containers[*].name,IMAGES:.spec.template.spec.containers[*].image,SELECTOR:.spec.selector.matchLabels",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                # Split the output into lines and append only the data rows
                lines = result.stdout.splitlines()
                if all_results:
                    # Skip the header row if results already exist
                    all_results.extend(lines[1:])
                else:
                    all_results.extend(lines)
            else:
                all_results.append(f"Error in namespace {namespace}: {result.stderr}")

        return "\n".join(all_results)
    except Exception as e:
        return str(e)


@app.get("/get-pods", response_class=PlainTextResponse)
async def get_pods():
    try:
        all_results = []

        for namespace in namespaces:
            result = subprocess.run(
                [
                    "kubectl",
                    "get",
                    "pods",
                    "-n",
                    namespace,
                    "-o",
                    "custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[*].ready,STATUS:.status.phase,RESTARTS:.status.containerStatuses[*].restartCount,AGE:.metadata.creationTimestamp,IP:.status.podIP,NODE:.spec.nodeName",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                # Split the output into lines and append only the data rows
                lines = result.stdout.splitlines()
                if all_results:
                    # Skip the header row if results already exist
                    all_results.extend(lines[1:])
                else:
                    all_results.extend(lines)
            else:
                all_results.append(f"Error in namespace {namespace}: {result.stderr}")

        return "\n".join(all_results)
    except Exception as e:
        return str(e)


all_logs = []


@app.get("/get-watcher-logs", response_class=PlainTextResponse)
async def get_watcher_logs():
    global all_logs
    try:
        # Get the list of watcher pods
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                rms_namespace,
                "-l",
                "app=watcher",
                "-o",
                "jsonpath={.items[0].metadata.name}",
            ],
            capture_output=True,
            text=True,
        )
        watcher_pod_name = result.stdout.strip()
        print(watcher_pod_name)
        if not watcher_pod_name:
            raise HTTPException(status_code=404, detail="Watcher pod not found")

        # Get the latest 5 logs of the watcher pod
        logs_result = subprocess.run(
            [
                "kubectl",
                "logs",
                "--tail=5",
                watcher_pod_name,
                "-n",
                rms_namespace,
            ],
            capture_output=True,
            text=True,
        )
        new_logs = logs_result.stdout.strip().split("\n")
        all_logs.extend(new_logs)  # Append new logs to the global logs list
        print(len(all_logs))
        if len(all_logs) > 5:  # Adjust the limit as needed
            all_logs = all_logs[-1000:]  # Keep only the last 1000 logs

        # Return all logs as a single response
        return PlainTextResponse("\n".join(all_logs))
    except Exception as e:
        return PlainTextResponse(str(e), status_code=500)


# Define a global variable to store deployments data
deployments_data = {}


@app.post("/post-deployments")
async def post_deployments(request: Request):
    global deployments_data
    try:
        deployments_data = await request.json()
        print("Received deployments data:", deployments_data)
        return {"message": "Deployments data received successfully"}
    except Exception as e:
        print(f"Error receiving deployments data: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error receiving deployments data: {str(e)}"
        )


@app.get("/get-deployments-data")
async def get_deployments_data():
    try:
        return deployments_data
    except Exception as e:
        print(f"Error retrieving deployments data: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error retrieving deployments data: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
