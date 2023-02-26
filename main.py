import sys
import argparse
import datetime
from operator import itemgetter, attrgetter
from itertools import groupby
from typing import List

from humanfriendly import parse_timespan, format_timespan
import requests
from kubernetes import client, config


def parse_args(argv=sys.argv[1:]):
    p = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter, description=(
        "Automatically cordon old worker nodes, send notifications about pending evictions and finally evict pods from "
        "cordoned old worker nodes"
    ))
    p.add_argument('--dry-run', default=False, action='store_true',
                   help="don't perform any changes, just print what would have been done")

    p.add_argument('--nodes', default=None, nargs='+', type=str, metavar='NODE',
                   help='act on the given nodes only')


    p.add_argument('--exclude-nodes-with-annotation', default=None, type=str,
                   help='exclude nodes which has this annotation')
    p.add_argument('--cordon-nodes-after', default='30d',
                   help='cordon nodes as old after they have been up for the given amount of time')
    p.add_argument('--notify-after', default='1d',
                   help=(
                       "send notifications about soon-to-be-drained nodes and the pods running on them after they have "
                       "been cordoned for the given amount of time; needs to be smaller than --evict-after)"))
    p.add_argument('--evict-after', default='2d',
                   help=(
                       "start evicting pods from nodes after they given amount of time after notifications have been "
                       "sent"))

    group = p.add_argument_group('slack notification settings')
    group.add_argument('--slack-webhook')
    group.add_argument('--slack-username', default=None)
    group.add_argument('--slack-icon', default=None)
    group.add_argument('--slack-default-message',
                       default=(
                           "*Heads up <!everyone>*\n"
                           "The node(s) running the following pods have been cordoned and will be gracefully drained soon. "
                           "The pods will be evicted at the given time. Make sure to define pod disruption budgets to prevent "
                           "service downtime."),
                       help='default message that will appear in slack notification')
    group.add_argument('--slack-additional-message', default=None,
                       help='will be added after the default message')
    group.add_argument('--slack-admin-contact',
                       help=(
                           "if given, this slack contact will be included in the notification messages to direct "
                           "people to the right team or person"))
    group.add_argument('--slack-target',
                       help='target for sending slack notifications about any actions performed by this program')
    group.add_argument('--slack-target-annotation',
                       help=(
                           "annotation key to look for on pods and the namespaces of pods that are running on nodes "
                           "that are to be drained soon; the value of the annotation is used as a target for sending "
                           "slack notifications about the actions performed by this program that affect the nodes that "
                           "the pods are running on"))

    args = p.parse_args(argv)

    args.cordon_nodes_after = datetime.timedelta(seconds=parse_timespan(args.cordon_nodes_after))
    args.notify_after = datetime.timedelta(seconds=parse_timespan(args.notify_after))
    args.evict_after = datetime.timedelta(seconds=parse_timespan(args.evict_after))

    if args.notify_after >= args.evict_after:
        print('--notify-after needs to be smaller than --evict-after')
        sys.exit(1)

    return args


def annotation(key: str):
    return 'node-drainer.k8s.logmein.com/' + key


def generate_action_plan(all_nodes: List[client.V1Node], all_namespaces: List[client.V1Namespace], all_pods, args):
    actions = {
        'cordon': {
            'nodes': [],
            'affected_pods': []
        },
        'notify': {
            'nodes': [],
            'affected_pods': []
        },
        'drain': {
            'nodes': [],
            'affected_pods': []
        },
    }

    namespace_annotations = {ns.metadata.name: (ns.metadata.annotations or {}) for ns in all_namespaces}
    now = datetime.datetime.utcnow().replace(microsecond=0)

    for node in all_nodes:
        if node.metadata.annotations.get(annotation('ignored')) is not None:
            continue

        cordoned = (args.dry_run or node.spec.unschedulable) and node.metadata.annotations.get(annotation('cordoned')) is not None
        notifications_sent_at = node.metadata.annotations.get(annotation('notifications-sent'))
        if notifications_sent_at is not None:
            try:
                notifications_sent_at = datetime.datetime.fromtimestamp(int(notifications_sent_at))
            except:
                print(f"Failed parsing timestamp of notifications-sent annotation")
                notifications_sent_at = now + args.notify_after

        action = None
        eviction_time = None
        cordon_at = node.metadata.creation_timestamp.replace(tzinfo=None) + args.cordon_nodes_after
        if notifications_sent_at is not None:
            if notifications_sent_at + args.evict_after < now:
                action = 'drain'
                print(
                    f"Node {node.metadata.name} was already cordoned and notifications had been sent. "
                    "It will be drained now.")
        elif cordoned:
            if args.dry_run:
                cordoned_at = cordon_at
            else:
                unschedulable_taint = next(filter(
                    lambda taint: taint.key == 'node.kubernetes.io/unschedulable',
                    node.spec.taints))
                cordoned_at = unschedulable_taint.time_added.replace(tzinfo=None)
            if cordoned_at + args.notify_after < now:
                action = 'notify'
                eviction_time = f"{format_timespan(args.evict_after)} from now"
                print(
                    f"Node {node.metadata.name} was already cordoned and will be drained {eviction_time}. "
                    "Notifications will be sent now.")
        elif not node.spec.unschedulable and cordon_at < now:
            action = 'cordon'
            print(
                f"Node {node.metadata.name} is older than {args.cordon_nodes_after} and will be cordoned now. "
                f"Notifications will be sent in {format_timespan(args.notify_after)}.")

        if action is None:
            continue

        pods = [pod for pod in all_pods if pod.spec.node_name == node.metadata.name]
        if len(pods) > 0:
            print(" Pods running on this instance:")

            # group pods by namespace
            keyfunc = attrgetter('metadata.namespace')
            pods = sorted(pods, key=keyfunc)
            pods_by_namespace = groupby(pods, key=keyfunc)

            for namespace, pods in pods_by_namespace:
                print(f"  Namespace: {namespace}")
                for pod in pods:
                    print(f"   {pod.metadata.name}")

                    actions[action]['affected_pods'].append({
                        'namespace': pod.metadata.namespace,
                        'name': pod.metadata.name,
                        'annotations': {
                            **namespace_annotations[pod.metadata.namespace],
                            **(pod.metadata.annotations or {})
                        },
                        'eviction_time': eviction_time
                    })

        actions[action]['nodes'].append(node.metadata.name)

    return actions


def notify(affected_pods, args):
    """
    Sends notification to the owners of the given list of affected pods.
    """
    if args.slack_webhook and (args.slack_target or args.slack_target_annotation):
        pods_by_slack_target = {}
        if args.slack_target_annotation:
            for pod in affected_pods:
                value = pod['annotations'].get(args.slack_target_annotation, None)
                if value is None:
                    continue
                targets = value.split(',')
                for target in targets:
                    if target not in pods_by_slack_target.keys():
                        pods_by_slack_target[target] = []
                    pods_by_slack_target[target].append(pod)
        if args.slack_target:
            pods_by_slack_target[args.slack_target] = affected_pods

        for target, pods_for_target in pods_by_slack_target.items():
            if len(pods_for_target) == 0:
                continue

            keyfunc = itemgetter('eviction_time')
            pods_by_eviction_time = groupby(sorted(pods_for_target, key=keyfunc), key=keyfunc)
            attachments = []
            for eviction_time, pods_for_eviction_time in pods_by_eviction_time:
                keyfunc = itemgetter('namespace')
                pods_by_namespace = groupby(sorted(pods_for_eviction_time, key=keyfunc), key=keyfunc)
                text = ''
                for namespace, pods in pods_by_namespace:
                    text += f"*Namespace:* {namespace}\n"
                    for pod in pods:
                        text += f"• `{pod['name']}`\n"
                attachments.append({
                    'mrkdwn_in': ['text', 'pretext'],
                    'pretext': f"*Eviction:* {eviction_time}",
                    'text': text
                })

            text = args.slack_default_message
            if args.slack_additional_message:
                text += "\n" + args.slack_additional_message
            if args.slack_admin_contact and args.slack_admin_contact != target:
                text += f"\nQuestions regarding this message? Contact <{args.slack_admin_contact}>"

            requests.post(args.slack_webhook, json={
                'username': args.slack_username,
                'icon_emoji': args.slack_icon,
                'channel': target,
                'text': text,
                'attachments': attachments,
            })


def drain_node(v1, node: client.V1Node, all_pods: List[client.V1Pod], args):
    print(f"Draining node {node}")
    pods = [pod for pod in all_pods if pod.spec.node_name == node]
    for pod in pods:
        if pod.metadata.deletion_timestamp is not None:
            continue

        print(f" Evicting pod {pod.metadata.name}")

        try:
            if not args.dry_run:
                v1.create_namespaced_pod_eviction(
                    pod.metadata.name,
                    pod.metadata.namespace,
                    {'metadata': {'name': pod.metadata.name}}
                )
        except client.rest.ApiException as exc:
            if exc.status == 429:
                print('Pod cannot be evicted right now due to PDB')
            elif exc.status == 404:
                pass
            elif exc.status == 500:
                print(f"Failed evicting pod {pod.metadata.name}; check PDB configuration")
            else:
                raise


def run():
    args = parse_args()
    

    try:
        config.load_kube_config()
    except:
        print("Failed loading kube config. Trying to use in-cluster config.")
        config.load_incluster_config()

    v1 = client.CoreV1Api()

    all_nodes = v1.list_node().items
    if args.nodes is not None:
        all_nodes = [node for node in all_nodes if node.metadata.name in args.nodes]
    if args.exclude_nodes_with_annotation is not None:
        all_nodes = [node for node in all_nodes if args.exclude_nodes_with_annotation not in node.metadata.annotations]

    all_namespaces = v1.list_namespace().items
    all_pods = [
        pod for pod in v1.list_pod_for_all_namespaces().items
        if not any(owner.kind == 'DaemonSet' for owner in (pod.metadata.owner_references or []))
    ]

    for action, info in generate_action_plan(all_nodes, all_namespaces, all_pods, args).items():
        nodes = info['nodes']
        pods = info['affected_pods']
        if action == 'notify':
            notify(pods, args)
            for node in nodes:
                print(f"Notifications sent for node {node}")
                v1.patch_node(node, {'metadata': {'annotations': {annotation('notifications-sent'): str(int(datetime.datetime.utcnow().timestamp()))}}})
        if action == 'cordon':
            for node in nodes:
                print(f"Cordoning node {node}")
                patch = {'metadata': {'annotations': {annotation('cordoned'): ''}}}
                if not args.dry_run:
                    patch = {**patch, 'spec': {'unschedulable': True}}
                v1.patch_node(node, patch)
        if action == 'drain':
            for node in nodes:
                drain_node(v1, node, all_pods, args)


if __name__ == "__main__":
    run()

