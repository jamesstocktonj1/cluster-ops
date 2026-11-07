# Omada controller

Continues from [ARGOCD.md](ARGOCD.md) (Cilium hands out the LAN IP) and
[STORAGE.md](STORAGE.md) (Longhorn holds the data). Runs the TP-Link Omada
controller on one LAN address, `10.0.40.205`. The web UI is also reachable from
outside through the Cloudflare Tunnel, behind Cloudflare Access (see
"Web UI through Cloudflare"). Devices only ever talk to the LAN address.

## How it works

```
Omada devices / LAN browser -> 10.0.40.205 (Cilium LB) -> omada-0 (one pod)
Remote browser -> Cloudflare Access -> tunnel -> cloudflared -> Service (8043) -^
                                                         |-- Java controller + MongoDB
                                                         '-- Longhorn volumes: data, logs
```

- [apps/omada.yaml](../apps/omada.yaml): the `omada-controller-helm` chart from the
  [docker-omada-controller](https://github.com/mbentley/docker-omada-controller/tree/master/helm/omada-controller-helm)
  project, pulled from Docker Hub as an OCI artifact. The chart version also fixes the
  controller version (1.5.5 = 6.3.0.111).
- [infrastructure/omada/values.yaml](../infrastructure/omada/values.yaml): all
  settings, commented.
- A StatefulSet with one pod. One `LoadBalancer` Service carries every Omada port
  (web UI `8043`, device traffic `29810-29817`, `19810/udp`, ...) on the pinned IP.

## Trade-offs to know about

- **Single pod.** While it restarts (upgrade, node loss) the controller is down.
  Adopted devices keep working; you just cannot manage them or collect statistics
  until it is back. Longhorn re-attaches the volume on another worker.
- **No layer 2 discovery.** The controller used to sit on the same network as the
  devices and find them by broadcast. A pod cannot see those broadcasts, so devices
  must be told where the controller is (see "Pointing devices at it").
- **Source IPs.** The chart cannot set `externalTrafficPolicy`, so traffic through the
  LB can be rewritten to a node IP on its way to the pod. Omada identifies devices by
  MAC address so adoption should still work, but if the client list shows node IPs
  instead of real ones, that is why.

## Deploy

### 1. Take a backup of the Docker controller

In the old controller: **Settings > Maintenance > Backup & Restore > Back Up**, and
download the `.cfg` file. Check the controller version while you are there
(**Settings > Maintenance**). A restore only works into the **same or a newer**
version; the chart pins 6.3.0.111. If the Docker one is newer, pick a newer chart
version in `apps/omada.yaml` first.

### 2. Free the IP, push and sync

Nothing else may use `10.0.40.205`. Merge to `main`; Argo CD creates the `omada`
app. First start takes a few minutes (the startup probe allows five):

```bash
kubectl -n omada get pods -w            # omada-0 goes 0/1 -> 1/1
kubectl -n omada get svc                # EXTERNAL-IP 10.0.40.205
kubectl -n omada get pvc                # data and logs, Bound, storage class longhorn
```

### 3. Restore

Open <https://10.0.40.205:8043> (self-signed certificate, so the browser warns). On
the setup wizard choose **Restore Controller** and upload the `.cfg` file. All sites,
devices and settings come across.

### 4. Pointing devices at it

Adopted devices remember the old controller's address. The new controller has its own
IP, so devices have to be told about it. Pick one:

- **Controller hostname/IP setting:** in the old controller, **Settings > Controller
  Settings > Controller Hostname/IP**, set `10.0.40.205` *before* shutting it down.
  Devices pick it up on their next check-in.
- **Per device:** if a device is already orphaned, SSH to it and run
  `set-inform http://10.0.40.205:29811/inform`, or use the Omada Discovery Utility.

Leave the Docker controller running until all devices show **Connected** in the new
one, then stop it.

## Web UI through Cloudflare

Nothing to change in git: the tunnel is remotely managed ([CLOUDFLARE.md](CLOUDFLARE.md)),
so the route lives in the dashboard, and cloudflared can already reach the Service
over the cluster network. Only the web UI goes this way; adoption and device
management need raw TCP/UDP, which the tunnel does not carry.

**1. Add the Access policy first**, before the route exists, so the UI is never
reachable without a login. Zero Trust > Access > Applications > Add > Self-hosted:

| Field       | Value                                                       |
| ----------- | ----------------------------------------------------------- |
| Hostname    | `omada.<your-domain>`                                       |
| Policy      | Allow, Emails: your own address (or your SSO group)         |

**2. Add the public hostname** to the `stack` tunnel:

| Field     | Value                                              |
| --------- | -------------------------------------------------- |
| Subdomain | `omada`                                            |
| Domain    | your domain                                        |
| Type      | `HTTPS`                                            |
| URL       | `omada-omada-controller.omada.svc.cluster.local:8043` |

Under **Additional application settings > TLS**, turn on **No TLS Verify**. Omada
serves a self-signed certificate, which cloudflared would otherwise reject (you get a
502). Traffic from the browser to Cloudflare is still properly encrypted.

**3. Test** with <https://omada.your-domain>: you should get the Access login, then
the Omada login page. Omada has its own admin password too, so this is two layers.

Set a strong Omada admin password: this is a login to your whole network.

## Upgrades

Bump `targetRevision` in [apps/omada.yaml](../apps/omada.yaml) (read the chart's
release notes first), take a backup in the UI, then merge. Omada migrates its own
database on start. Downgrading is not supported.

## Troubleshooting

```bash
kubectl -n omada logs omada-0 -f        # controller log, including first-start progress
kubectl -n omada describe pod omada-0   # probe failures, OOMKilled (raise the memory limit)
```
