# Metrics API

import datetime
import math

from prometheus_api_client import PrometheusConnect
from prometheus_api_client.utils import parse_datetime

from rms_config import rms_config

timespan = rms_config.get("metrics", {}).get(
    "latency_avg_timespan", "2m"
)  # Default to "2m" if not set


# Call the function
def calculate_average_values(duration, values):
    now = datetime.datetime.now()
    filtered_values = [
        value for timestamp, value in values if now - timestamp <= duration
    ]
    if filtered_values:
        return sum(filtered_values) / len(filtered_values)
    return 0


def query_memory_usage(pod_name):
    prom = PrometheusConnect(
        url="http://rancher-monitoring-prometheus.cattle-monitoring-system.svc.cluster.local:9090",
        disable_ssl=True,
    )

    start_time = parse_datetime("1d")
    end_time = parse_datetime("now")
    step = "1m"

    result = prom.custom_query_range(
        query=f'sum(container_memory_working_set_bytes{{pod="{pod_name}"}})',
        start_time=start_time,
        end_time=end_time,
        step=step,
    )

    if not result or "values" not in result[0]:
        print(f"No data returned for pod: {pod_name}")
        return 0, 0, 0, 0

    memory_values = [
        (datetime.datetime.fromtimestamp(entry[0]), float(entry[1]) / (1024**2))
        for entry in result[0]["values"]
    ]

    latest_memory_usage = memory_values[-1][1] if memory_values else 0
    avg_mem_30m = calculate_average_values(
        datetime.timedelta(minutes=30), memory_values
    )
    avg_mem_3h = calculate_average_values(datetime.timedelta(hours=3), memory_values)
    avg_mem_24h = calculate_average_values(datetime.timedelta(hours=24), memory_values)

    return latest_memory_usage, avg_mem_30m, avg_mem_3h, avg_mem_24h


def query_memory_workload_usage(workload_name, namespace, workload_type):
    try:
        prom = PrometheusConnect(
            url="http://rancher-monitoring-prometheus.cattle-monitoring-system.svc.cluster.local:9090",
            disable_ssl=True,
        )

        start_time = parse_datetime("1d")
        end_time = parse_datetime("now")
        step = "1m"

        query = (
            f'sum(container_memory_working_set_bytes{{namespace="{namespace}"}}'
            f" * on(namespace,pod) group_left(workload, workload_type) namespace_workload_pod:kube_pod_owner:relabel"
            f'{{namespace="{namespace}", workload="{workload_name}", workload_type=~"{workload_type}"}})'
        )

        result = prom.custom_query_range(
            query=query,
            start_time=start_time,
            end_time=end_time,
            step=step,
        )

        if not result:
            print("No data returned from the query.")
            return 0, 0, 0, 0

        memory_values = [
            (datetime.datetime.fromtimestamp(entry[0]), float(entry[1]) / (1024**2))
            for entry in result[0]["values"]
        ]

        latest_memory_usage = memory_values[-1][1] if memory_values else 0
        avg_mem_30m = calculate_average_values(
            datetime.timedelta(minutes=30), memory_values
        )
        avg_mem_3h = calculate_average_values(
            datetime.timedelta(hours=3), memory_values
        )
        avg_mem_24h = calculate_average_values(
            datetime.timedelta(hours=24), memory_values
        )

        if latest_memory_usage < 1:
            query = (
                f'sum(container_memory_working_set_bytes{{namespace="{namespace}"}}'
                f" * on(namespace,pod) group_left(workload, workload_type) namespace_workload_pod:kube_pod_owner:relabel"
                f'{{namespace="{namespace}", workload="{workload_name}", workload_type=~"{workload_type}"}})'
            )

            result = prom.custom_query_range(
                query=query,
                start_time=start_time,
                end_time=end_time,
                step=step,
            )

            if not result:
                print("No data returned from the query.")
                return 0, 0, 0, 0

            memory_values = [
                (datetime.datetime.fromtimestamp(entry[0]), float(entry[1]) / (1024**2))
                for entry in result[0]["values"]
            ]

            latest_memory_usage = memory_values[-1][1] if memory_values else 0
            avg_mem_30m = calculate_average_values(
                datetime.timedelta(minutes=30), memory_values
            )
            avg_mem_3h = calculate_average_values(
                datetime.timedelta(hours=3), memory_values
            )
            avg_mem_24h = calculate_average_values(
                datetime.timedelta(hours=24), memory_values
            )

        return latest_memory_usage, avg_mem_30m, avg_mem_3h, avg_mem_24h

    except Exception as e:
        print(f"An error occurred while querying memory workload usage: {e}")
        return 0, 0, 0, 0


def query_cpu_usage(pod_name):
    prom = PrometheusConnect(
        url="http://rancher-monitoring-prometheus.cattle-monitoring-system.svc.cluster.local:9090",
        disable_ssl=True,
    )

    start_time = parse_datetime("1d")
    end_time = parse_datetime("now")
    step = "1m"

    result = prom.custom_query_range(
        query=f'sum(rate(container_cpu_usage_seconds_total{{container!="", pod="{pod_name}"}}[1m]))',
        start_time=start_time,
        end_time=end_time,
        step=step,
    )

    # Check if result is empty or does not contain 'values'
    if not result or "values" not in result[0]:
        print(f"No data returned for pod: {pod_name}")
        return 0, 0, 0, 0

    cpu_values = [
        (datetime.datetime.fromtimestamp(entry[0]), float(entry[1]))
        for entry in result[0]["values"]
    ]

    latest_cpu_usage = cpu_values[-1][1] if cpu_values else 0
    avg_cpu_30m = calculate_average_values(datetime.timedelta(minutes=30), cpu_values)
    avg_cpu_3h = calculate_average_values(datetime.timedelta(hours=3), cpu_values)
    avg_cpu_24h = calculate_average_values(datetime.timedelta(hours=24), cpu_values)

    return latest_cpu_usage, avg_cpu_30m, avg_cpu_3h, avg_cpu_24h


def query_cpu_workload_usage(workload_name, namespace, workload_type):
    try:
        prom = PrometheusConnect(
            url="http://rancher-monitoring-prometheus.cattle-monitoring-system.svc.cluster.local:9090",
            disable_ssl=True,
        )

        start_time = parse_datetime("1d")
        end_time = parse_datetime("now")
        step = "1m"

        query = (
            f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}"}}[1m])'
            f" * on(namespace,pod) group_left(workload, workload_type) namespace_workload_pod:kube_pod_owner:relabel"
            f'{{namespace="{namespace}", workload="{workload_name}", workload_type=~"{workload_type}"}})'
        )

        result = prom.custom_query_range(
            query=query,
            start_time=start_time,
            end_time=end_time,
            step=step,
        )

        if not result:
            print("No data returned from the query.")
            return 0, 0, 0, 0

        cpu_values = [
            (datetime.datetime.fromtimestamp(entry[0]), float(entry[1]))
            for entry in result[0]["values"]
        ]
        latest_cpu_usage = cpu_values[-1][1] if cpu_values else 0
        avg_cpu_30m = calculate_average_values(
            datetime.timedelta(minutes=30), cpu_values
        )
        avg_cpu_3h = calculate_average_values(datetime.timedelta(hours=3), cpu_values)
        avg_cpu_24h = calculate_average_values(datetime.timedelta(hours=24), cpu_values)

        return latest_cpu_usage, avg_cpu_30m, avg_cpu_3h, avg_cpu_24h

    except Exception as e:
        print(f"An error occurred while querying CPU workload usage: {e}")
        return 0, 0, 0, 0


def query_avg_latency_latest(deployment_name):
    # Queries latency over the past 5 minutes.

    prom = PrometheusConnect(
        url="http://rancher-monitoring-prometheus.cattle-monitoring-system.svc.cluster.local:9090",
        disable_ssl=True,
    )

    query_avg = f'avg_over_time(model_training_latency_seconds{{deployment="{deployment_name}"}}[{timespan}])'
    result = prom.custom_query(query=query_avg)
    try:
        value = float(result[0]["value"][1])
    except Exception as e:
        print(e)
        value = -1
    return value


def query_avg_latency_1h(deployment_name):
    # Queries latency over the past 5 minutes.

    prom = PrometheusConnect(
        url="http://rancher-monitoring-prometheus.cattle-monitoring-system.svc.cluster.local:9090",
        disable_ssl=True,
    )

    query = f'avg_over_time(model_training_latency_seconds{{deployment="{deployment_name}"}}[60m])'
    result = prom.custom_query(query=query)
    try:
        value = float(result[0]["value"][1])
    except Exception as e:
        print(e)
        value = -1
    return value
