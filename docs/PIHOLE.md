# Pi-hole

Continues from [ARGOCD.md](ARGOCD.md) (Cilium hands out the LAN IP). Runs three
Pi-hole instances behind one DNS address, `10.0.40.203`, and one dashboard to manage
all three, at `10.0.40.204`.

## How it works

```
LAN device -> 10.0.40.203:53 (Cilium LB, UDP+TCP) -> one of 3 Pi-hole pods (one per worker)
                                                          |
                                           blocked? answer 0.0.0.0 : forward to 1.1.1.1

browser -> 10.0.40.204 (Cilium LB) -> Dobbelt dashboard (nginx) -> pihole-1-web / pihole-2-web / pihole-3-web
```

- [apps/pihole-1.yaml](../apps/pihole-1.yaml), `-2`, `-3`: the `pihole` chart from
  <https://mojo2600.github.io/pihole-kubernetes/>, version pinned, **one Helm release
  per Pi-hole**, all using [infrastructure/pihole/values.yaml](../infrastructure/pihole/values.yaml)
  (commented). Separate releases give each Pi-hole its own Service name, which the
  dashboard needs: its nginx looks each address up once at start, and the pods of a
  single Deployment have no stable name or IP.
- [apps/pihole-config.yaml](../apps/pihole-config.yaml) applies
  [infrastructure/pihole/manifests/](../infrastructure/pihole/manifests/):
  - `dns-service.yaml`: one `LoadBalancer` Service for DNS with the IP pinned by
    `lbipam.cilium.io/ips`. It selects `app: pihole`, the label on all three pods, so
    queries are spread across them. Cilium answers ARP for it from a worker that runs
    a Pi-hole pod (`externalTrafficPolicy: Local`).
  - `dashboard.yaml`: [Dobbelt-Pi-hole](https://github.com/tursoe/Dobbelt-Pi-hole)
    (image pinned) and its Service on `10.0.40.204`.
- The three Pi-holes do not talk to each other. They are identical only because they
  are configured from the same file. The dashboard does not sync them either; it
  shows where they differ and lets you change one or all.

## Trade-offs to know about

- **No persistent volume.** The query log and anything changed in the web UI or
  dashboard reset when a pod restarts. Block lists, upstream DNS and so on belong in
  the values file. This is what lets three pods run on three nodes without sharing a
  volume.
- **Query logs are per Pi-hole.** A query only shows up on the Pi-hole that served
  it. The dashboard's combined stats and query log merge the three views.
- **The dashboard controls everything.** Anyone who can log in has admin on all three
  Pi-holes. It is on the LAN only; do not put it on the Cloudflare Tunnel without
  Cloudflare Access in front.
- **The dashboard checks addresses at start.** If it is crash-looping after a first
  sync, the Pi-holes were not up yet; it retries by itself.
- **DNS is a dependency of the cluster.** Argo CD fetches Helm charts and images by
  name. Don't point the *nodes'* DNS at this IP; keep the router/Talos resolvers
  independent so a Pi-hole outage cannot stop you fixing Pi-hole.

## Deploy

### 1. Create the admin Secret by hand

Not in git (same approach as the Grafana password). The pods stay in
`CreateContainerConfigError` until it exists:

```bash
kubectl create namespace pihole   # Argo would create it, but the Secret needs it first
read -s -p "Pi-hole admin password: " PASS; echo
kubectl -n pihole create secret generic pihole-admin \
  --from-literal=password="$PASS"
unset PASS
```

### 2. Push and sync

Merge to `main`; Argo CD creates `pihole-1`, `pihole-2`, `pihole-3` and `pihole-config`. Then check:

```bash
kubectl -n pihole get pods -o wide        # 3 Pi-holes + the dashboard, each Pi-hole on a different node
kubectl -n pihole get svc                 # pihole-dns 10.0.40.203, pihole-dashboard 10.0.40.204
```

**Migrating from the old single `pihole` app** (one Deployment, 3 replicas): the merge
removes it and creates the new ones. Both want `10.0.40.203`, so DNS is down from the
old Service being deleted until the new one gets the address (usually a minute or two;
if the new Service sits on `<pending>`, check the old one is gone). Merge at a quiet
time, and make sure the router's secondary DNS is something that is not Pi-hole.
Query logs and per-pod UI changes reset, as on any restart.

### 3. Test it

```bash
dig @10.0.40.203 example.com              # resolves
dig @10.0.40.203 doubleclick.net          # 0.0.0.0 = blocked
```

Failover: `kubectl -n pihole delete pod <one pod>`; the `dig` keeps working.

### 4. Point the LAN at it

In the router's DHCP settings, set the DNS server to `10.0.40.203`. Keep a secondary
that is not Pi-hole (e.g. `1.1.1.1`) if you want browsing to survive a full outage,
at the cost of some devices bypassing the blocking.

### 5. Reach it over Tailscale (away from home)

[infrastructure/tailscale/config/pihole.yaml](../infrastructure/tailscale/config/pihole.yaml)
adds a second Service, handled by the Tailscale operator ([TAILSCALE.md](TAILSCALE.md)).
After Argo syncs, a `pihole` device appears in the Tailscale machine list:

```bash
kubectl -n pihole get svc pihole-dns-tailscale   # EXTERNAL-IP becomes the tailnet IP
dig @pihole.<tailnet>.ts.net example.com         # from any tailnet device
```

To use it automatically: Tailscale admin console > DNS > Nameservers > add the
`pihole` device's tailnet IP, and enable **Override DNS servers** if you want all
tailnet traffic to use it. Needs the ACL to allow your devices to reach `tag:k8s` on
`53` (the allow-all rule in [TAILSCALE.md](TAILSCALE.md) already does). Tailscale
queries show in Pi-hole as one client (the proxy pod).

## Dashboard

Open <http://10.0.40.204>. It lists three Pi-holes and asks for a login for each: use
the password from step 1 (the same one for all three). The login is kept in the
browser tab only. From there you can see combined stats and the query log, compare
domains, local DNS/CNAME records, groups and adlists across the three, change one or
all, run Gravity updates one after another, and download Teleporter backups. Remember
that anything changed here is lost when that pod restarts: keep what should last
(block lists, upstream DNS) in the values file.

The classic Pi-hole admin pages are no longer on a LAN IP. To open one Pi-hole's own
UI:

```bash
kubectl -n pihole get pods
kubectl -n pihole port-forward svc/pihole-1-web 8080:80   # or pihole-2-web / pihole-3-web
```

Then open <http://localhost:8080/admin>.

## Metrics

Pi-hole has no Prometheus endpoint itself, so each pod runs a second container,
`exporter` ([ekofr/pihole-exporter](https://github.com/eko/pihole-exporter)), that
reads that pod's Pi-hole API and serves it on port 9617. A `PodMonitor` made by the
chart makes Prometheus scrape it every 30s ([MONITORING.md](MONITORING.md)). Check
[http://localhost:9090/targets](http://localhost:9090/targets) (port-forward in
MONITORING.md) for the three `podMonitor/pihole/pihole-N-prometheus-exporter` entries
(one per release), each with 1 target up.

Useful series (all have a `pod` label; sum or `avg by` across replicas):

| Metric | Meaning |
| --- | --- |
| `pihole_dns_queries_today` | queries handled |
| `pihole_ads_blocked_today` | queries blocked |
| `pihole_ads_percentage_today` | percent blocked |
| `pihole_domains_being_blocked` | size of the loaded block lists |
| `pihole_unique_clients` | distinct clients seen |
| `pihole_status` | 1 = blocking enabled |
| `pihole_queries_forwarded` / `pihole_queries_cached` | forwarded upstream vs answered from cache |

Exact names vary a little by exporter version; browse them in Prometheus by typing
`pihole_`. Because pods have no volume, the "today" counters reset when a pod
restarts. For rates use `rate()` over them rather than the raw value.

Two useful alerts-to-be: `sum(pihole_status) < 3` (a replica not blocking or down) and
`up{namespace="pihole"} == 0`. There is no Alertmanager yet, so these
are only for graphs for now.

The shared DNS Service only carries port 53, so the exporters are reachable inside
the cluster only. Each exporter logs in to its Pi-hole every scrape, which shows in
its session list.

## Changing the admin password

Update the Secret, then restart the three Deployments:
`kubectl -n pihole rollout restart deployment pihole-1 pihole-2 pihole-3`. The
exporters read the same Secret, so they pick up the new password on restart too.
Log in to the dashboard again afterwards.

## Troubleshooting

- **EXTERNAL-IP stuck on `<pending>`**: the IP is outside the pool or already used
  (`kubectl get ciliumloadbalancerippool`), or the annotation is mistyped.
- **A Pi-hole pod is Pending**: the spread rule needs 3 schedulable workers. If one
  is down, the third pod waits until it returns (the other two still serve DNS).
- **Dashboard pod in CrashLoopBackOff**: `kubectl -n pihole logs deploy/pihole-dashboard`.
  Usually a `pihole-N-web` Service does not exist yet (a Pi-hole app has not synced);
  it fixes itself once they do.
- **Timeouts from some LAN devices only**: with `externalTrafficPolicy: Local`, the
  announcing node must have a pod. Check
  `kubectl get ciliuml2announcementpolicy` and that the pods are on workers.
