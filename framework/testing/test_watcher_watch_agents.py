import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../watcher")))

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from models import (
    AgentDTO,
    ResourceQuota,
    Resources,
)
from watcher import Watcher, WatcherState



'''This test file is designed to test the functionality of the Watcher class in the highest iteration level, where it searches for agents.'''

@pytest.fixture
def agent_node():
    return AgentDTO(
        name="agent1",
        scope="node",
        node_name="node1",
        namespace="test",
        deployment_name="",
        resources=Resources(
            resourceQuota=ResourceQuota(cpu=0.7, memory=0.2),
        ),
        restarts=0,
        state="pending",
    )


@pytest.fixture
def watcher_state():
    # Mock initial state
    df = pd.DataFrame(
        {
            "deployment_name": ["deployment1", "deployment2"],
            "applied_agents": [["agent1"], ["agent2"]],
            "applied_memory_usage": [None, None],
            "applied_cpu_usage": [None, None],
            "action": [None, None],
        }
    )
    iteration_conflict_dict = {}
    json_data = {}
    return WatcherState(
        df=df, iteration_conflict_dict=iteration_conflict_dict, json_data=json_data
    )


@pytest.fixture
def watcher(watcher_state):
    controller_endpoint = "http://mock-controller-endpoint"
    return Watcher(state=watcher_state, controller_endpoint=controller_endpoint)


@patch("watcher.client.CustomObjectsApi")
@patch("watcher.client.AppsV1Api")
@patch("watcher.get_cluster_deployments")
@patch("watcher.create_json_data")
@patch("watcher.requests.post")
@patch("watcher.revert_deployment_to_initial")
@patch("watcher.create_agent_DTO")
@patch("watcher.apply_based_on_scope")
def test_watch_agents(
    mock_apply_based_on_scope,
    mock_create_agent_DTO,
    mock_revert_deployment_to_initial,
    mock_requests_post,
    mock_create_json_data,
    mock_get_cluster_deployments,
    mock_apps_v1_api,
    mock_custom_objects_api,
    agent_node,
    watcher,
    caplog,
):
    # Mock the Watcher's state
    mock_create_agent_DTO.side_effect = [agent_node]
    mock_get_cluster_deployments.return_value = watcher.state.df

    # Mock API responses
    mock_custom_objects_api.return_value.list_cluster_custom_object.return_value = {
        "items": [
            {
                "metadata": {"name": agent_node.name},
                "spec": {
                    "scope": agent_node.scope,
                    "node_name": agent_node.node_name,
                    "namespace": agent_node.namespace,
                    "deployment_name": agent_node.deployment_name,
                    "resources": {
                        "resourceQuota": {
                            "cpu": agent_node.resources.resourceQuota.cpu,
                            "memory": agent_node.resources.resourceQuota.memory,
                        },
                    },
                    "restarts": agent_node.restarts,
                    "state": agent_node.state,
                },
            },
        ]
    }

    # Mock JSON data creation
    mock_create_json_data.return_value = {}

    # Mock POST request
    mock_requests_post.return_value.status_code = 200

    # Run the function
    with caplog.at_level(logging.INFO):
        for _ in range(2):  # Run two iterations of the while loop
            watcher.watch_agents()

    # Assertions
    assert "Watching for agents" in caplog.text
    assert "Successfully sent total_input_dict" in caplog.text
    assert "Now checking Agent: agent1" in caplog.text
    mock_apply_based_on_scope.assert_called_with(
        agent_node,
        watcher.state.df,
        watcher.state.iteration_conflict_dict,
        watcher.state.json_data,
    )
    mock_revert_deployment_to_initial.assert_not_called()
