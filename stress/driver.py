# Copyright 2011 Quanta Research Cambridge, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
"""The entry point for the execution of a workloadTo execute a workload.
Users pass in a description of the workload and a nova manager object
to the bash_openstack function call"""


import random
import datetime
import time


# local imports
from test_case import *
import utils.util
from config import StressConfig
from state import ClusterState, KeyPairState, FloatingIpState, VolumeState
from tempest.common.utils.data_utils import rand_name


# setup logging to file
logging.basicConfig(
    format='%(asctime)s %(name)-20s %(levelname)-8s %(message)s',
    datefmt='%m-%d %H:%M:%S',
    filename="stress.debug.log",
    filemode="w",
    level=logging.DEBUG,
    )

# define a Handler which writes INFO messages or higher to the sys.stdout
_console = logging.StreamHandler()
_console.setLevel(logging.INFO)
# set a format which is simpler for console use
_formatter = logging.Formatter('%(name)-20s: %(levelname)-8s %(message)s')
# tell the handler to use this format
_console.setFormatter(_formatter)
# add the handler to the root logger
logging.getLogger('').addHandler(_console)


def _create_cases(choice_spec):
    """
    Generate a workload of tests from workload description
    """
    cases = []
    count = 0
    for choice in choice_spec:
        p = choice.probability
        for i in range(p):
            cases.append(choice)
        i = i + p
        count = count + p
    assert(count == 100)
    return cases


def _get_compute_nodes(keypath, user, controller):
    """
    Returns a list of active compute nodes. List is generated by running
    nova-manage on the controller.
    """
    nodes = []
    if keypath is None or user is None:
        return nodes
    cmd = "nova-manage service list | grep ^nova-compute"
    lines = utils.util.ssh(keypath, user, controller, cmd).split('\n')
    # For example: nova-compute xg11eth0 nova enabled :-) 2011-10-31 18:57:46
    # This is fragile but there is, at present, no other way to get this info.
    for line in lines:
        words = line.split()
        if len(words) > 0 and words[4] == ":-)":
            nodes.append(words[1])
    return nodes


def _error_in_logs(keypath, logdir, user, nodes):
    """
    Detect errors in the nova log files on the controller and compute nodes.
    """
    grep = 'egrep "ERROR\|TRACE" %s/*.log' % logdir
    for node in nodes:
        errors = utils.util.ssh(keypath, user, node, grep, check=False)
        if len(errors) > 0:
            logging.error('%s: %s' % (node, errors))
            return True
    return False


def create_initial_vms(manager, state, count):
    image = manager.config.compute.image_ref
    flavor = manager.config.compute.flavor_ref
    servers = []
    logging.info('Creating %d vms' % count)
    for _ in xrange(count):
        name = rand_name('initial_vm-')
        _, server = manager.servers_client.create_server(name, image, flavor)
        servers.append(server)
    for server in servers:
        manager.servers_client.wait_for_server_status(server['id'], 'ACTIVE')
        logging.info('Server Name: %s Id: %s' % (name, server['id']))
        state.set_instance_state(server['id'], (server, 'ACTIVE'))


def create_initial_floating_ips(manager, state, count):
    logging.info('Creating %d floating ips' % count)
    for _ in xrange(count):
        _, ip = manager.floating_ips_client.create_floating_ip()
        logging.info('Ip: %s' % ip['ip'])
        state.add_floating_ip(FloatingIpState(ip))


def create_initial_keypairs(manager, state, count):
    logging.info('Creating %d keypairs' % count)
    for _ in xrange(count):
        name = rand_name('keypair-')
        _, keypair = manager.keypairs_client.create_keypair(name)
        logging.info('Keypair: %s' % name)
        state.add_keypair(KeyPairState(keypair))


def create_initial_volumes(manager, state, count):
    volumes = []
    logging.info('Creating %d volumes' % count)
    for _ in xrange(count):
        name = rand_name('volume-')
        _, volume = manager.volumes_client.create_volume(size=1,
                                                         display_name=name)
        volumes.append(volume)
    for volume in volumes:
        manager.volumes_client.wait_for_volume_status(volume['id'],
                                                      'available')
        logging.info('Volume Name: %s Id: %s' % (name, volume['id']))
        state.add_volume(VolumeState(volume))


def bash_openstack(manager,
                   choice_spec,
                   **kwargs):
    """
    Workload driver. Executes a workload as specified by the `choice_spec`
    parameter against a nova-cluster.

    `manager`  : Manager object
    `choice_spec` : list of BasherChoice actions to run on the cluster
    `kargs`       : keyword arguments to the constructor of `test_case`
                    `duration`   = how long this test should last (3 sec)
                    `sleep_time` = time to sleep between actions (in msec)
                    `test_name`  = human readable workload description
                                   (default: unnamed test)
                    `max_vms`    = maximum number of instances to launch
                                   (default: 32)
                    `seed`       = random seed (default: None)
    """
    stress_config = StressConfig(manager.config._conf)
    # get keyword arguments
    duration = kwargs.get('duration', datetime.timedelta(seconds=10))
    seed = kwargs.get('seed', None)
    sleep_time = float(kwargs.get('sleep_time', 3000)) / 1000
    max_vms = int(kwargs.get('max_vms', stress_config.max_instances))
    test_name = kwargs.get('test_name', 'unamed test')

    keypath = stress_config.host_private_key_path
    user = stress_config.host_admin_user
    logdir = stress_config.nova_logdir
    computes = _get_compute_nodes(keypath, user, manager.config.identity.host)
    utils.util.execute_on_all(keypath, user, computes,
                              "rm -f %s/*.log" % logdir)
    random.seed(seed)
    cases = _create_cases(choice_spec)
    state = ClusterState(max_vms=max_vms)
    create_initial_keypairs(manager, state,
                             int(kwargs.get('initial_keypairs', 0)))
    create_initial_vms(manager, state,
                       int(kwargs.get('initial_vms', 0)))
    create_initial_floating_ips(manager, state,
                                int(kwargs.get('initial_floating_ips', 0)))
    create_initial_volumes(manager, state,
                                int(kwargs.get('initial_volumes', 0)))
    test_end_time = time.time() + duration.seconds

    retry_list = []
    last_retry = time.time()
    cooldown = False
    logcheck_count = 0
    test_succeeded = True
    logging.debug('=== Test \"%s\" on %s ===' %
                  (test_name, time.asctime(time.localtime())))
    for kw in kwargs:
        logging.debug('\t%s = %s', kw, kwargs[kw])

    while True:
        if not cooldown:
            if time.time() < test_end_time:
                case = random.choice(cases)
                logging.debug('Chose %s' % case)
                retry = case.invoke(manager, state)
                if retry is not None:
                    retry_list.append(retry)
            else:
                logging.info('Cooling down...')
                cooldown = True
        if cooldown and len(retry_list) == 0:
            if _error_in_logs(keypath, logdir, user, computes):
                test_succeeded = False
            break
        # Retry verifications every 5 seconds.
        if time.time() - last_retry > 5:
            logging.debug('retry verifications for %d tasks', len(retry_list))
            new_retry_list = []
            for v in retry_list:
                v.check_timeout()
                if not v.retry():
                    new_retry_list.append(v)
            retry_list = new_retry_list
            last_retry = time.time()
        time.sleep(sleep_time)
        # Check error logs after 100 actions
        if logcheck_count > 100:
            if _error_in_logs(keypath, logdir, user, computes):
                test_succeeded = False
                break
            else:
                logcheck_count = 0
        else:
            logcheck_count = logcheck_count + 1
    # Cleanup
    logging.info('Cleaning up: terminating virtual machines...')
    vms = state.get_instances()
    active_vms = [v for _k, v in vms.iteritems()
                  if v and v[1] != 'TERMINATING']
    for target in active_vms:
        manager.servers_client.delete_server(target[0]['id'])
        # check to see that the server was actually killed
    for target in active_vms:
        kill_id = target[0]['id']
        i = 0
        while True:
            try:
                manager.servers_client.get_server(kill_id)
            except Exception:
                break
            i += 1
            if i > 60:
                _error_in_logs(keypath, logdir, user, computes)
                raise Exception("Cleanup timed out")
            time.sleep(1)
        logging.info('killed %s' % kill_id)
        state.delete_instance_state(kill_id)
    for floating_ip_state in state.get_floating_ips():
        manager.floating_ips_client.delete_floating_ip(
                                            floating_ip_state.resource_id)
    for keypair_state in state.get_keypairs():
        manager.keypairs_client.delete_keypair(keypair_state.name)
    for volume_state in state.get_volumes():
        manager.volumes_client.delete_volume(volume_state.resource_id)

    if test_succeeded:
        logging.info('*** Test succeeded ***')
    else:
        logging.info('*** Test had errors ***')
    return test_succeeded
