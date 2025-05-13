import logging
import os
import threading
import time

import colorlog
import numpy as np
import pandas as pd
import requests
from helpers import (
    apply_based_on_scope,
    create_agent_DTO,
    create_json_data,
    get_cluster_deployments,
    initialize_deployment_df,
    resolve_agent_specification_conflict,
    revert_deployment_to_initial,
)
from kubernetes import client, config

from rms_config import rms_config


# Define the WatcherState class
class WatcherState:
    def __init__(self, df, iteration_conflict_dict, json_data):
        self.df = df
        self.iteration_conflict_dict = iteration_conflict_dict
        self.json_data = json_data


# Configure colored logging
handler = colorlog.StreamHandler()
handler.setFormatter(
    colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s - %(levelname)s - %(message)s",
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "bold_yellow",
            "ERROR": "red",
            "CRITICAL": "bold_red",
        },
    )
)
logger = colorlog.getLogger()
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Remove any other handlers to avoid duplicate logs
if logger.hasHandlers():
    logger.handlers.clear()
logger.addHandler(handler)

# Load Kubernetes configuration
if os.path.exists(os.path.expanduser("~/.kube/config")):
    config.load_kube_config()
else:
    config.load_incluster_config()


class Watcher:
    def __init__(self, state, controller_endpoint):
        self.state = state
        self.controller_endpoint = controller_endpoint
        self.iteration_count = 1
        self.previous_agents = {}
        self.agentDTOs = []

    def update_cluster_state(self):
        self.state.df = get_cluster_deployments(self.state.df)
        self.state.json_data = create_json_data(self.state.df, self.state.json_data)
        try:
            response = requests.post(self.controller_endpoint, json=self.state.json_data)
            response.raise_for_status()
            logger.info("Successfully sent total_input_dict to %s", self.controller_endpoint)
        except requests.exceptions.RequestException as e:
            logger.error("Error sending total_input_dict to %s: %s", self.controller_endpoint, e)

    def fetch_current_agents(self, custom_api_instance):
        agents = custom_api_instance.list_cluster_custom_object(
            group="resourcemanagement-v.io", version="v1", plural="agents"
        )
        return {a["metadata"]["name"]: a for a in agents.get("items", [])}

    def handle_deleted_agents(self, current_agents, api_instance):
        for index, deployment in self.state.df.iterrows():
            applied_agents = deployment["applied_agents"]
            for order in applied_agents:
                if order not in current_agents:
                    revert_deployment_to_initial(deployment, api_instance)
                    self.state.df.loc[index, "action"] = None
                    self.state.df.loc[index, "applied_memory_usage"] = None
                    self.state.df.loc[index, "applied_cpu_usage"] = None
                    self.state.df.at[index, "applied_agents"] = [
                        o for o in self.state.df.at[index, "applied_agents"] if o != order
                    ]
                    if order in self.state.df.columns:
                        self.state.df.drop(columns=[order], inplace=True)
                    logger.info("Reverted deployment %s due to missing agent %s",
                                deployment["deployment_name"], order)

    def handle_available_agents(self, current_agents, custom_api_instance):
        for name, agentorder in current_agents.items():
            dto = create_agent_DTO(agentorder)
            self.update_agent_dtos(name, dto)
            agent = next(agent for agent in self.agentDTOs if agent.name == name)

            logger.warning("Now checking Agent: %s", agent.name, extra={"log_color": "yellow"})

            if agent.state == "conflicting":
                logger.warning("Agent %s is in conflicting state, solving specification conflict by removing trigger agent", agent.name)
                resolve_agent_specification_conflict(self.agentDTOs, custom_api_instance)
            elif agent.state == "disabled":
                logger.warning("Agent %s is in disabled state because it is conflicting with other agents. Please revise its configuration", agent.name)
                continue
            elif agent.state in ["pending", "applied"]:
                logger.info("Checking Agent %s", agent.name)
                apply_based_on_scope(agent, self.state)

            self.track_iteration_conflicts(agent)

        self.previous_agents = current_agents

    def update_agent_dtos(self, name, new_dto):
        for i, dto in enumerate(self.agentDTOs):
            if dto.name == name:
                self.agentDTOs[i] = new_dto
                return
        self.agentDTOs.append(new_dto)

    def track_iteration_conflicts(self, agent):
        if agent.name not in self.state.iteration_conflict_dict:
            self.state.iteration_conflict_dict[agent.name] = {}
        self.state.iteration_conflict_dict[agent.name][self.iteration_count] = agent.restarts

    def update_iteration_tracking(self):
        logger.info("Iteration %d: %s", self.iteration_count, self.state.iteration_conflict_dict)
        self.iteration_count += 1
        if self.iteration_count > 20:
            self.state.iteration_conflict_dict = {}
            self.iteration_count = 1

    def watch_agents(self):
        custom_api_instance = client.CustomObjectsApi()
        api_instance = client.AppsV1Api()
        while True:
            logger.info("Watching for agents")
            self.update_cluster_state()
            try:
                current_agents = self.fetch_current_agents(custom_api_instance)
                self.handle_deleted_agents(current_agents, api_instance)
                self.handle_available_agents(current_agents, custom_api_instance)
            except client.exceptions.ApiException as e:
                logger.error("API exception: %s", e.reason)
            except Exception as e:
                logger.error("Unexpected error: %s", str(e))

            self.update_iteration_tracking()
            watcher_iteration_frequency = rms_config.get("watcher", {}).get(
                "iteration_frequency", 10
            )
            time.sleep(watcher_iteration_frequency)


if __name__ == "__main__":

    controller_endpoint = os.getenv(
        "controller_endpoint", "default-controller-endpoint"
    )
    df = initialize_deployment_df()  # Initialize your DataFrame here
    # Initialize WatcherState
    state = WatcherState(df=df, iteration_conflict_dict={}, json_data={})

    # Initialize Watcher
    watcher = Watcher(state=state, controller_endpoint=controller_endpoint)

    # Start the watcher thread
    watcher_thread = threading.Thread(target=watcher.watch_agents)
    watcher_thread.start()
