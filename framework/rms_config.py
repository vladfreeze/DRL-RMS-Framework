rms_config = {
    "rms_namespace": "resource-management",  # Namespace where the management components are deployed.
    "target_namespaces": [
        "sim-env"
    ],  # Change to alter the namespaces that should be managed by the RMS.
    "drl-system": {
        "default_latency_threshold": 1.2,  # Change to alter the default latency threshold.
        "minimum_reward": -10,  # Change to alter the minimum reward for the feedback system.
        "endpoints": {
            "drl_system_endpoint": "http://drl-model.resource-management.svc.cluster.local:8000/predict/",  # Change to alter the RMS endpoint
            "controller_endpoint": "http://rms:5000",  # Change to alter the RMS endpoint
        },
        "metrics": {
            "latency_avg_timespan": "1m",  # Change to alter the default latency average timespan for the latest latency measurement. For faster reaction time, decrease. Syntax: 5m,1h,2h etc.
        },
        "watcher": {
            "iteration_frequency": 10,  # Time between watcher iterations in seconds.
        },
    },
}
