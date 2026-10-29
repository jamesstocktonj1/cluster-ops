# Monitoring with Prometheus

Continues from [STORAGE.md](STORAGE.md) (Prometheus stores its data on a Longhorn
volume). Deploys the Prometheus Operator and a single Prometheus that scrapes pods.

## How it works

```
pod (label + "metrics" port) <-- PodMonitor <-- Prometheus Operator --> Prometheus
```

- [apps/monitoring.yaml](../apps/monitoring.yaml): the `kube-prometheus-stack`
  chart: operator, Prometheus (20Gi Longhorn volume, 15d retention), node-exporter
  and kube-state-metrics. Grafana and Alertmanager are off. Values are in
  [infrastructure/monitoring/values.yaml](../infrastructure/monitoring/values.yaml).
- [apps/monitoring-config.yaml](../apps/monitoring-config.yaml): one `PodMonitor`
  ([infrastructure/monitoring/config](../infrastructure/monitoring/config)) that
  selects pods across all namespaces.

## Getting a pod scraped

Add the label to the pod template and name the container port `metrics`:

```yaml
metadata:
  labels:
    prometheus.io/scrape: "true"
spec:
  containers:
    - ports:
        - name: metrics
          containerPort: 9090
```

Metrics are read from `/metrics` every 30s. For a different path or port name, add
your own `PodMonitor`; Prometheus picks up all of them.

## Envoy Gateway and wasmCloud

- **Envoy Gateway**: [envoy-gateway.yaml](../infrastructure/monitoring/config/envoy-gateway.yaml)
  scrapes the controller and the two Envoy proxy pods. Both expose metrics by default.
- **wasmCloud**: not scraped. The v2 `runtime-operator` chart exposes no Prometheus
  endpoint (the operator serves only health probes on 8082, hosts only `/livez` and
  `/readyz` on 8081). Revisit if a later chart adds one.

## Checking it

```bash
kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090
```

Open http://localhost:9090/targets.

## Notes

- Talos exposes etcd, the scheduler and the controller manager on localhost only,
  so those scrapes are disabled.
- Prometheus is not a backup of anything and loses history if its volume is deleted.
