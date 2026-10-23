# wasmCloud v2

Continues from [ARGOCD.md](ARGOCD.md). Runs WebAssembly components on the cluster
using the wasmCloud v2 runtime-operator.

> v2 is a different project shape from v1. The old `wasmcloud-platform` chart,
> wadm and `WasmCloudHostConfig` do **not** apply. Ignore v1 guides.

## How it works

```
WorkloadDeployment (you) -> operator -> NATS -> wasmCloud host pods (wash) -> your .wasm
                                                      ^
                       Service + EndpointSlice -------+  (HTTP traffic goes straight to hosts)
```

- **Operator** watches the CRDs and schedules workloads onto hosts.
- **NATS + JetStream** is the control plane. Hosts never talk to the Kubernetes API;
  they register themselves by heartbeat.
- **Hosts** are pods running the `wash` image, grouped in a *host group* (`default`).
- **Gateway** (runtime-gateway) is deprecated since 2.0.3, so it is disabled.

One Helm chart, `runtime-operator`, installs all of it into the `wasmcloud` namespace.

## What changed in git

| File | Purpose |
| ---- | ------- |
| `apps/wasmcloud.yaml` | Argo CD Application (OCI chart, version pinned here) |
| `infrastructure/wasmcloud/values.yaml` | Gateway off, `default` host group with 3 hosts |

## Notes specific to this cluster

- **Pod security:** the chart's pods are non-root, read-only rootfs, all caps dropped,
  so they pass Talos' `baseline` profile. No `privileged` namespace label is needed
  (unlike Longhorn).
- **Cilium:** the chart ships NetworkPolicies (`networkPolicy.enabled: true`), which
  Cilium enforces. If traffic to a workload is blocked, look here first.
- **NATS storage is an emptyDir.** Restarting NATS loses JetStream state. Hosts
  re-register and the operator reconciles workloads from the CRDs in git/etcd, but
  anything you stored in NATS object storage is gone. Fine for experimenting.
- **No prune** on the Application, so a bad commit can't delete the CRDs (which would
  delete every workload).

## Steps

### 1. Check the chart version

Docs pinned `2.11.0` when this was written. Check for newer:

```bash
helm show chart oci://ghcr.io/wasmcloud/charts/runtime-operator | grep -E '^(version|appVersion)'
```

Edit `targetRevision` in `apps/wasmcloud.yaml` if needed.

### 2. (Optional) Preview what will be created

```bash
helm template wasmcloud oci://ghcr.io/wasmcloud/charts/runtime-operator \
  --version 2.11.0 -n wasmcloud -f infrastructure/wasmcloud/values.yaml | less
```

Confirm there is no gateway, and that the host group renders with 3 replicas. The
values file only sets `runtime.hostGroups[0]` fields I took from the chart defaults;
Helm replaces lists wholesale, so if the render shows a missing field, copy it from
the chart's
[values.yaml](https://github.com/wasmCloud/wasmCloud/blob/main/charts/runtime-operator/values.yaml).

### 3. Deploy

Commit and push `apps/wasmcloud.yaml` and `infrastructure/wasmcloud/`.

```bash
export KUBECONFIG=$PWD/talos/clusterconfig/kubeconfig
kubectl -n argocd get application wasmcloud
kubectl -n wasmcloud get pods -w
kubectl rollout status deploy -l app.kubernetes.io/instance=wasmcloud -n wasmcloud
```

Expect: operator, NATS, and 3 host pods. If Argo can't pull the OCI chart, check the
`repoURL` has no `oci://` prefix.

### 4. Confirm hosts registered

```bash
kubectl get crd | grep wasmcloud
kubectl get hosts.runtime.wasmcloud.dev -A
```

You should see 3 hosts, all in the `default` host group. (CRD group is
`runtime.wasmcloud.dev`.)

### 5. Deploy a test workload

```bash
kubectl apply -f - <<'YAML'
apiVersion: v1
kind: Service
metadata:
  name: hello-world
spec:
  type: ClusterIP
  ports:
    - port: 80
      targetPort: 8080      # the host group's http.port in values.yaml
      protocol: TCP
---
apiVersion: runtime.wasmcloud.dev/v1alpha1
kind: WorkloadDeployment
metadata:
  name: hello-world
spec:
  replicas: 1
  template:
    spec:
      hostSelector:
        hostgroup: default
      kubernetes:
        service:
          name: hello-world
      components:
        - name: hello-world
          image: ghcr.io/wasmcloud/components/hello-world:0.1.0
      hostInterfaces:
        - namespace: wasi
          package: http
          interfaces:
            - incoming-handler
          config:
            host: localhost
YAML
kubectl get workloaddeployments
kubectl port-forward svc/hello-world 8080:80
curl -i localhost:8080
```

You should get a greeting. If the Service has no endpoints, the operator hasn't
placed the workload yet: `kubectl describe workloaddeployment hello-world`.

Clean up: `kubectl delete workloaddeployment/hello-world service/hello-world`.

> The upstream docs set the host HTTP port to 80 (`values.local.yaml`) and use
> `targetPort: 80`. This repo keeps the chart default 9191 so hosts needn't bind a
> privileged port; the `targetPort` must match whichever you choose. If curl fails,
> try this first.

## Using it

Workloads are plain CRDs, so keep them in git like everything else: add a directory
such as `workloads/<name>/` with the Service and WorkloadDeployment, and an Argo
Application in `apps/` pointing at it.

To make one reachable from the LAN, set the Service to `type: LoadBalancer`; Cilium
hands out an IP from the pool in `infrastructure/cilium/config/ip-pool.yaml`. To make
it reachable from the internet, add a route in the Cloudflare tunnel to
`http://<service>.<namespace>.svc.cluster.local` (see [CLOUDFLARE.md](CLOUDFLARE.md)).
If a workload routes by hostname, set `config: {host: <hostname>}` on the `http`
`incoming-handler` host interface.

Building your own components: install `wash`, then `wash build` and
`wash oci push ghcr.io/<you>/<name>:<tag> <file>.wasm`, and reference that image in
`components[].image`.

## Troubleshooting

- **Helm/Argo error about an existing ClusterRole or CRD:** a previous install left
  cluster-scoped objects. Remove them (this deletes existing wasmCloud resources):
  `kubectl delete clusterrole,clusterrolebinding,crd -l app.kubernetes.io/instance=wasmcloud`.
- **No hosts listed:** hosts can't reach NATS. `kubectl -n wasmcloud logs` on a host
  pod and the NATS pod; check NetworkPolicies.
- **Workload stuck pending:** `hostSelector.hostgroup` doesn't match a host group
  name, or no host has capacity/the interface you asked for.
- **Component pull fails:** hosts pull the image from the registry directly. Private
  GHCR images need credentials; public ones work as-is.

## Upgrading

Bump `targetRevision` in `apps/wasmcloud.yaml`. Argo applies CRD changes through
ServerSideApply. Read the release notes first; the project is young and the CRDs are
`v1alpha1`.

## Sources

- [wasmCloud Operator docs](https://wasmcloud.com/docs/kubernetes-operator/)
- [Install wasmCloud](https://wasmcloud.com/docs/installation/)
- [Deploy a Wasm workload](https://wasmcloud.com/docs/quickstart/deploy-a-webassembly-workload/)
- [Chart values](https://github.com/wasmCloud/wasmCloud/blob/main/charts/runtime-operator/values.yaml)
