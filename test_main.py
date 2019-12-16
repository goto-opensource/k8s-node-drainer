import unittest
import unittest.mock
import datetime

from kubernetes import client

import main


args = main.parse_args([])
now = datetime.datetime.utcnow().replace(microsecond=0)


class TestNodeReplacer(unittest.TestCase):
    def setUp(self) -> None:
        self.maxDiff = None

    def test_generate_action_plan(self):
        mock_input = {
            'all_nodes': [
                client.V1Node(metadata=client.V1ObjectMeta(
                    name='node-1',
                    creation_timestamp=(now - datetime.timedelta(days=30.1)),
                    annotations={}
                ), spec=client.V1NodeSpec(unschedulable=False)),

                client.V1Node(metadata=client.V1ObjectMeta(
                    name='node-2',
                    creation_timestamp=(now - datetime.timedelta(days=60)),
                    annotations={}
                ), spec=client.V1NodeSpec(unschedulable=False)),

                client.V1Node(metadata=client.V1ObjectMeta(
                    name='node-3',
                    creation_timestamp=(now - datetime.timedelta(days=30.2)),
                    annotations={}
                ), spec=client.V1NodeSpec(unschedulable=True)),

                client.V1Node(metadata=client.V1ObjectMeta(
                    name='node-4',
                    creation_timestamp=(now - datetime.timedelta(days=30.3)),
                    annotations={
                        main.annotation('cordoned'): '',
                    }
                ), spec=client.V1NodeSpec(unschedulable=False)),

                client.V1Node(metadata=client.V1ObjectMeta(
                    name='node-4',
                    creation_timestamp=(now - datetime.timedelta(days=30.4)),
                    annotations={
                        main.annotation('cordoned'): '',
                    }
                ), spec=client.V1NodeSpec(
                    unschedulable=True,
                    taints=[
                        client.V1Taint(
                            key='node.kubernetes.io/unschedulable',
                            effect='NoSchedule',
                            time_added=(now - datetime.timedelta(hours=1)),
                        )
                    ]
                )),

                client.V1Node(metadata=client.V1ObjectMeta(
                    name='node-5',
                    creation_timestamp=(now - datetime.timedelta(days=32.5)),
                    annotations={
                        main.annotation('cordoned'): '',
                    }
                ), spec=client.V1NodeSpec(
                    unschedulable=True,
                    taints=[
                        client.V1Taint(
                            key='node.kubernetes.io/unschedulable',
                            effect='NoSchedule',
                            time_added=(now - datetime.timedelta(days=1.2)),
                        )
                    ]
                )),

                client.V1Node(metadata=client.V1ObjectMeta(
                    name='node-6',
                    creation_timestamp=(now - datetime.timedelta(days=35)),
                    annotations={
                        main.annotation('cordoned'): '',
                    }
                ), spec=client.V1NodeSpec(
                    unschedulable=True,
                    taints=[
                        client.V1Taint(
                            key='node.kubernetes.io/unschedulable',
                            effect='NoSchedule',
                            time_added=(now - datetime.timedelta(days=2)),
                        )
                    ]
                )),

                client.V1Node(metadata=client.V1ObjectMeta(
                    name='node-7',
                    creation_timestamp=(now - datetime.timedelta(days=35)),
                    annotations={
                        main.annotation('cordoned'): '',
                        main.annotation('notifications-sent'): str(int((datetime.datetime.utcnow() - datetime.timedelta(days=2.5)).timestamp())),
                    }
                ), spec=client.V1NodeSpec(
                    unschedulable=True,
                    taints=[
                        client.V1Taint(
                            key='node.kubernetes.io/unschedulable',
                            effect='NoSchedule',
                            time_added=(now - datetime.timedelta(days=4)),
                        )
                    ]
                )),
            ],
            'all_namespaces': [
                client.V1Namespace(metadata=client.V1ObjectMeta(
                    name='ns-1',
                    annotations={
                        'annotation-1': 'bla',
                    })),
                client.V1Namespace(metadata=client.V1ObjectMeta(
                    name='ns-2',
                    annotations={
                        'annotation-2': 'blub',
                    }))
            ],
            'all_pods': [
                client.V1Pod(
                    metadata=client.V1ObjectMeta(namespace='ns-1', name='pod-1', annotations={
                        'annotation-3': '123',
                    }),
                    spec=client.V1PodSpec(node_name='node-5', containers=[])),
                client.V1Pod(
                    metadata=client.V1ObjectMeta(namespace='ns-2', name='pod-2', annotations={
                        'annotation-4': '456',
                    }),
                    spec=client.V1PodSpec(node_name='node-6', containers=[])),
                client.V1Pod(
                    metadata=client.V1ObjectMeta(namespace='ns-2', name='pod-3', annotations={
                        'annotation-5': '789',
                    }),
                    spec=client.V1PodSpec(node_name='node-7', containers=[])),
            ],
            'args': args,
        }
        expected_result = {
            'cordon': {
                'nodes': ['node-1', 'node-2', 'node-4'],
                'affected_pods': []
            },
            'notify': {
                'nodes': ['node-5', 'node-6'],
                'affected_pods': [
                    {
                        'namespace': 'ns-1',
                        'name': 'pod-1',
                        'annotations': {
                            'annotation-1': 'bla',
                            'annotation-3': '123',
                        },
                        'eviction_time': '2 days from now',
                    },
                    {
                        'namespace': 'ns-2',
                        'name': 'pod-2',
                        'annotations': {
                            'annotation-2': 'blub',
                            'annotation-4': '456',
                        },
                        'eviction_time': '2 days from now',
                    },
                ]
            },
            'drain': {
                'nodes': ['node-7'],
                'affected_pods': [
                    {
                        'namespace': 'ns-2',
                        'name': 'pod-3',
                        'annotations': {
                            'annotation-2': 'blub',
                            'annotation-5': '789',
                        },
                        'eviction_time': None,
                    },
                ]
            },
        }
        self.assertEqual(expected_result, main.generate_action_plan(**mock_input))

    def test_notify(self):
        with unittest.mock.patch('requests.post') as mock:
            main.notify(
                [
                    {
                        'namespace': 'ns-1',
                        'name': 'pod-1',
                        'annotations': {
                            'logmein.com/slack': '#some-team',
                        },
                        'eviction_time': 'a day from now',
                    },
                    {
                        'namespace': 'ns-2',
                        'name': 'pod-2',
                        'annotations': {
                            'logmein.com/slack': '@some.person',
                        },
                        'eviction_time': '21 hours from now',
                    },
                ],
                main.parse_args([
                    '--slack-webhook=https://slack-test.com/',
                    '--slack-target=#admin-notifications',
                    '--slack-admin-contact=#admin-team',
                    '--slack-target-annotation=logmein.com/slack',
                ]),
            )
            self.assertEqual(len(mock.mock_calls), 3)
            self.assertTrue(any(
                (
                    c[2]['json']['channel'] == '#some-team' and
                    len(c[2]['json']['attachments']) == 1 and
                    '#admin-team' in c[2]['json']['text'] and
                    'a day from now' in c[2]['json']['attachments'][0]['pretext'] and
                    'ns-1' in c[2]['json']['attachments'][0]['text'] and
                    'pod-1' in c[2]['json']['attachments'][0]['text']
                )
                for c in mock.mock_calls
            ))
            self.assertTrue(any(
                (
                    c[2]['json']['channel'] == '@some.person' and
                    len(c[2]['json']['attachments']) == 1 and
                    '#admin-team' in c[2]['json']['text'] and
                    '21 hours from now' in c[2]['json']['attachments'][0]['pretext'] and
                    'ns-2' in c[2]['json']['attachments'][0]['text'] and
                    'pod-2' in c[2]['json']['attachments'][0]['text']
                )
                for c in mock.mock_calls
            ))
            self.assertTrue(any(
                (
                    c[2]['json']['channel'] == '#admin-notifications' and
                    len(c[2]['json']['attachments']) == 2 and
                    '#admin-team' in c[2]['json']['text'] and
                    any(
                        (
                            'a day from now' in a['pretext'] and
                            'ns-1' in a['text'] and
                            'pod-1' in a['text']
                        ) for a in c[2]['json']['attachments']
                    ) and
                    any(
                        (
                            '21 hours from now' in a['pretext'] and
                            'ns-2' in a['text'] and
                            'pod-2' in a['text']
                        ) for a in c[2]['json']['attachments']
                    )

                )
                for c in mock.mock_calls
            ))


if __name__ == '__main__':
    unittest.main()
