# k8s-node-drainer

**Warning:** We are still in the processing of publishing this project. There might still be some glitches from making it "nice" for open-sourcing and the docker image is not pushed yet. **Do not consider this project production-ready yet.** However, feel free to take a look, try it out and even contribute!

`CronJob` for Kubernetes that drains old (cloud-based) worker nodes so that they can be removed, e.g. by the Kubernetes [cluster-autoscaler](https://github.com/kubernetes/autoscaler/tree/master/cluster-autoscaler).

## Why?

We automated the regular draining of old worker nodes to have an automatic, continuous, hands-free approach for rolling out updates of the worker node base system (OS, kubelet, etc.); evicted pods will be recreated and scheduled on other or newly spawned nodes automatically, not causing any cluster or service downtime (assuming pod disruption budgets are correctly set).

## How?

It executes the following steps:
1. When a node is getting older than configured via `--cordon-nodes-after`, it is cordoned (made unschedulable).
2. After another delay set by `--notify-after`, notifications can be sent to the administrator of the cluster and/or to the teams owning the namespaces in which the pods (that are still running on this node) are located.
3. After yet another delay set by `--evict-after`, pods still running on the worker node will be evicted, respecting pod disruption budgets.

When there are no pods running anymore on the node (or the utilization decreases under the configured threshold), the Kubernetes cluster-autoscaler is supposed to take over and delete the worker node in the corresponding cloud provider.

## Trying it out

Run:
```bash
$ python3 main.py \
    --dry-run \
    --cordon-nodes-after=10d \
    --notify-after=2d \
    --evict-after=1d \
    --slack-webhook https://hooks.slack.com/services/... \
    --slack-target '@your.username'
```

**Warning:** even with `--dry-run`, the script will still add annotations to your nodes.

Run `python3 main.py --help` to see the full set of options.

## Deployment

The script can be installed as a `CronJob` in your cluster. The corresponding manifests can be found in the `deploy` folder. Please add `args` in the `CronJob` and possibly adjust the `schedule` to configure it to your need, then run:

```bash
$ kubectl apply -f deploy/
```

## Notifications

At the moment, only **Slack** is supported as a notification target. Feel free to create a pull request to add more notification target types.
