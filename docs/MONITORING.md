# Monitoring with Prometheus

Continues from [STORAGE.md](STORAGE.md) (Prometheus stores its data on a Longhorn
volume). Deploys the Prometheus Operator, a single Prometheus that scrapes pods,
and Grafana for dashboards at https://k8smetrics.jamesstockton.com.

## How it works

```
pod (label + "metrics" port) <-- PodMonitor <-- Prometheus Operator --> Prometheus
                                                                            ^
browser -> Cloudflare -> tunnel -> cloudflared -> Grafana ------ queries ---+
```

- [apps/monitoring.yaml](../apps/monitoring.yaml): the `kube-prometheus-stack`
  chart: operator, Prometheus (20Gi Longhorn volume, 15d retention), node-exporter,
  kube-state-metrics and Grafana (5Gi Longhorn volume). Alertmanager is off.
  Values are in
  [infrastructure/monitoring/values.yaml](../infrastructure/monitoring/values.yaml).
- [apps/monitoring-config.yaml](../apps/monitoring-config.yaml): one `PodMonitor`
  ([infrastructure/monitoring/config](../infrastructure/monitoring/config)) that
  selects pods across all namespaces.

## Grafana

Grafana comes with Prometheus already added as a data source and the chart's
default Kubernetes dashboards (cluster, nodes, pods, namespaces, and so on).
Dashboards you build in the UI are saved in Grafana's database on its Longhorn
volume, so they survive restarts.

### LAN access

Grafana's Service is a `LoadBalancer`, so Cilium gives it a LAN IP (pinned to
`10.0.40.201` with the `lbipam.cilium.io/ips` annotation) and answers ARP for it.
From any machine on the LAN open http://10.0.40.201. After login Grafana redirects
to the public hostname, because `root_url` is set to it.

### 1. Create the admin Secret by hand

Like the Cloudflare token, the admin login is not in git. Grafana's pod stays in
`CreateContainerConfigError` until this exists:

```bash
read -s -p "Grafana admin password: " PASS; echo
kubectl -n monitoring create secret generic grafana-admin \
  --from-literal=admin-user=admin \
  --from-literal=admin-password="$PASS"
unset PASS
```

Then `kubectl -n monitoring get pods -l app.kubernetes.io/name=grafana` should show
it Running within a minute.

Changing the password later: update the Secret and restart Grafana
(`kubectl -n monitoring rollout restart statefulset monitoring-grafana`). Grafana
only reads it at startup, and only overwrites the stored password if it changed.

### 2. Add the public hostname in Cloudflare

In the `stack` tunnel's **Public Hostname** settings (see
[CLOUDFLARE.md](CLOUDFLARE.md)), add:

| Field     | Value                                             |
| --------- | ------------------------------------------------- |
| Subdomain | `k8smetrics`                                      |
| Domain    | `jamesstockton.com`                               |
| Type      | `HTTP`                                            |
| URL       | `monitoring-grafana.monitoring.svc.cluster.local:80` |

The hostname must match `grafana.ini.server.root_url` in the values file. If
you pick a different one, change both.

### 3. Put Cloudflare Access in front (recommended)

Grafana has its own login, but it is still an admin UI on the internet. Add a
**Cloudflare Access** application for `k8smetrics.jamesstockton.com` (Zero Trust >
Access > Applications, a self-hosted app with a policy that allows only your
email). Requests then never reach the cluster until you have signed in.

### Keeping dashboards in git

Dashboards made in the UI live only on the Grafana volume. To keep one in git,
export it as JSON (Share > Export) and put it in a ConfigMap labelled
`grafana_dashboard: "1"` in any namespace, for example in
`infrastructure/monitoring/config/`. Grafana's sidecar loads it automatically.
Dashboards loaded this way can't be saved from the UI; export the edited JSON
back into git instead.

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

Grafana without going through Cloudflare:

```bash
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80
```

## Notes

- Talos exposes etcd, the scheduler and the controller manager on localhost only,
  so those scrapes are disabled.
- Prometheus is not a backup of anything and loses history if its volume is deleted.
- The same goes for Grafana: dashboards that exist only in the UI are lost if its
  volume is deleted. Export anything you care about to git.
