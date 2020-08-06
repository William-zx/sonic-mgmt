#!/usr/bin/env python
"""
This script is used for running all(or given) available testcases.

Procedures:
1. parse options
2. update dut images before run
3. parse default testcases with testcases.yml and testcases_nephos.yml
4. parse user defined testcases
5. run user defined testcases or all available testcases per topology

FIXME:
Known issues:
- After add-topo, we need to wait for dut become stable (team/bgp/routes).
  It could take more than half an hour (t1/t1-lag), and do not clearly known what the reason is.
  a code review is need to figure out why dut need so long time to become stable.

- Now, we assume that all testbeds in testbed.csv use the same dut.
  And there should be only one testbed configuration for each topo.

TODO:
Need another test framework (eg. Robotframework) for supporting below features.
- Auto retry failed cases(max_retry_times could be given per case)
- Support timeout setting for each case(default 5m or some proper value)
- Support pause on error
- Auto generate faild cases list for regression.
"""

import yaml
import csv
import time
import re
import argparse
import logging
import logging.handlers
import sys
import os.path
import subprocess
import json
import jinja2
import shutil
import inspect

from collections import Counter
from functools import partial
from netaddr import IPAddress, AddrFormatError

import ansible.constants
from ansible.inventory import Inventory
from ansible.parsing.dataloader import DataLoader
from ansible.vars import VariableManager

from pytest_ansible.host_manager import get_host_manager
from pytest_ansible.results import ModuleResult
from pytest_ansible.errors import AnsibleConnectionFailure

# try import ansible_host from SONiC pytest framework
c_dir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
tests_dir = os.path.abspath(os.path.join(c_dir, '../tests'))
sys.path.insert(0, tests_dir)
try:
    from ansible_host import ansible_host as AnsibleHost
except ImportError:
    from ansible_host import AnsibleHost
from ansible_host import dump_ansible_results

MTU_MTK_SIZE = 9000
TESTBED_FILE = 'testbed.csv'
TESTCASE_FILE_SONIC = 'roles/test/vars/testcases.yml'
TESTCASE_FILE_NEPHOS = 'roles/test/vars/testcases_nephos.yml'
TESTCASE_FILE_LIST = [
    TESTCASE_FILE_SONIC,
#    TESTCASE_FILE_NEPHOS
]
LOGGING_LEVEL_MAP = {
    'debug': logging.DEBUG,
    'info':  logging.INFO,
    'warn':  logging.WARN,
    'crit':  logging.CRITICAL
}
DRY_RUN = False

CFG_CHECK_FAIL_DICT = {
    'config_db': list(),
    'bgp': list()
}

INVENTORY_FILE = 'veos.vtb'

ansible_hosts = {}

class MyLogger(object):
    LOG_FORMAT = "%(asctime)s  %(levelname)-8s: [%(lineno)-4s]:  %(message)s"
    LOG_ROOT = '/tmp/'
    MAIN_LOG_NAME = os.path.splitext(os.path.basename(__file__))[0]

    def __init__(self):
        self.logger = logging.getLogger(MyLogger.MAIN_LOG_NAME)
        self.logger.setLevel(logging.DEBUG)
        self.formatter = logging.Formatter(MyLogger.LOG_FORMAT)
        self.add_stdout_handler()
        self.log_dir = None
    
    def add_stdout_handler(self):
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        handler.setFormatter(self.formatter)
        self.logger.addHandler(handler)
        self.stdout_handler = handler

    def add_file_handler(self, f_name, logging_level):
        if os.path.exists(f_name):
            os.remove(f_name)

        d = os.path.dirname(f_name)
        if not os.path.exists(d):
            os.makedirs(d)

        handler=logging.FileHandler(f_name, mode='a+')
        handler.setFormatter(self.formatter)
        handler.setLevel(logging_level)
        self.logger.addHandler(handler)

    def create_main_log(self, options):
        if options.build_number:
            job_name = options.build_number
        else:
            job_name = time.strftime('%Y%m%d_%H%M%S')

        self.log_dir = os.path.join(MyLogger.LOG_ROOT, job_name)
        self.log_file = os.path.join(self.log_dir, MyLogger.MAIN_LOG_NAME)
        self.add_file_handler(self.log_file, logging.DEBUG)

    def get_case_log_file(self, topo, case):
        if self.log_dir is None:
            raise Exception("Main log has not created!")
        
        f = '{}_ansible.log'.format(case)
        d = os.path.join(self.log_dir, topo, case)
        if not os.path.exists(d):
            os.makedirs(d)
        return os.path.join(d, f)
    
    def get_fail_case_log_file(self, topo, case):
        if self.log_dir is None:
            raise Exception("Main log has not created!")

        f = '{}_ansible.log'.format(case)
        d = os.path.join(self.log_dir, 'fail', topo)
        if not os.path.exists(d):
            os.makedirs(d)
        return os.path.join(d, f)

    def __getattr__(self, item):
        return getattr(self.logger, item)

logger = MyLogger()

# Classes
class TestcaseInfo(object):
    def __init__(self, testcase_file_list):
        self.testcase_file_list = testcase_file_list
        self.topo_testcases_map = dict()
        self.tc_props = dict()

        self.parse_testcase_file()


    def parse_testcase_file(self):
        for testcase_filename in self.testcase_file_list:
            if not os.path.exists(testcase_filename):
                logger.warning("Not found testcase file " + testcase_filename)

            with open(testcase_filename) as f:
                content = yaml.safe_load(f)

            for k in content:

                if 'testcases' not in k:
                    logger.warning("Invalid testcases file format!")
                    exit(1)

                # add testcase to available testcases dict
                for tc, props in content[k].items():
                    if tc in self.tc_props:
                        logger.critical("Found duplicated testcases!! {}: {} ".format(testcase_filename, tc))
                        exit(1)
                    else:
                        self.tc_props[tc] = props

                    # add testcases to topo2testcases map
                    for topo in props['topologies']:
                        if topo not in self.topo_testcases_map:
                            self.topo_testcases_map[topo] = []
                        self.topo_testcases_map[topo].append(tc)


class TestbedInfo(object):
    '''
    Parse the CSV file used to describe whole testbed info
    Please refer to the example of the CSV file format
    CSV file first line is title
    The topology name in title is 'uniq-name' or 'conf-name'
    '''

    def __init__(self, testbed_file):
        self.testbed_file = testbed_file
        self.testbed_topo = dict()
        self.support_topo = set()
        self.read_testbed_file()

    def read_testbed_file(self):
        with open(self.testbed_file) as f:
            content = csv.DictReader(f)

            for line in content:

                tb_paras = {}

                for field in content.fieldnames:
                    stripped_field = field.strip('#').strip()
                    tb_paras[stripped_field] = line[field]

                if tb_paras['vm_base'] != '':
                    tb_paras['vm_base_ip'] = get_ansible_host_ip_by_name(host_pattern=tb_paras['vm_base'])
                else:
                    tb_paras['vm_base_ip'] = None

                if tb_paras['ptf_ip'] !='':
                    tb_paras['ptf_ip_addr'], tb_paras['ptf_ip_mask']= tb_paras['ptf_ip'].split('/')

                topo = tb_paras['topo']

                if topo not in self.support_topo:
                    self.support_topo.add(tb_paras['topo'])
                else:
                    # Now we have to make sure there is only 1 testbed configuration
                    # for a given topo within a testbed file.
                    logger.warning("Found duplicaed topo in testbed file!!")
                    exit(1)

                self.testbed_topo[tb_paras['conf-name']] = tb_paras

                logger.debug("Found testbed: {}\n{}".format(tb_paras["topo"], yaml.dump(tb_paras)))

    def get_testbed_by_name(self, testbed_name=None):
        if testbed_name is not None:
            return self.testbed_topo[testbed_name]
        else:
            return self.testbed_topo

    def is_valid_topo(self, topo):
        return topo in self.support_topo

    def get_testbed_by_topo(self, topo):
        if not self.is_valid_topo(topo):
            logger.debug("Not Found Topo: " + topo)
            return None
        else:
            for tb_paras in self.testbed_topo.itervalues():
                if topo == tb_paras['topo']:
                    return tb_paras


class UDFTest():
    def __init__(self, tc_info, tb_info, platform='dut'):
        self.filter={}
        self.vskvm_topo_cases={}
        self.dut_topo_cases={}
        self.available_topo_cases = tc_info.topo_testcases_map
        self.tc_props = tc_info.tc_props
        self.tb_info=tb_info
        self.platform=platform

    def parse_udf_testcases_file(self, testcases_file):
        if not os.path.exists(testcases_file):
            logger.error("File not found !! " + testcases_file)
            exit(1)

        with open(testcases_file, 'r') as f:
            self.content = yaml.safe_load(f)

        self.vskvm_topo_cases = self.content.get('vskvm', {})
        self.dut_topo_cases = self.content.get('dut', {})
        self.filter = self.content.get('filter', {})
        self.filter_topo = self.filter.get('topo', [])
        self.filter_cases = self.filter.get('cases', [])
        self.filter_topo_cases = self.filter.get('topo_cases', {})

    def is_filter_topo(self, topo_name):
        return topo_name in self.filter_topo

    def is_filter_case(self, testcase_name):
        return testcase_name in self.filter_cases

    def is_filter_case_on_topo(self, topo_name, testcase_name):
        return testcase_name in self.filter_topo_cases.get(topo_name, [])

    def is_case_valid_on_platform(self, testcase_name, platform):
        support_platform_list = ['dut']
        support_platform_list.extend(self.tc_props[testcase_name].get('vir_platform', []))
        return platform in support_platform_list

    def filter_by_user(self, topo_to_cases_map):
        # filter by topo
        for topo in topo_to_cases_map:
            if self.is_filter_topo(topo):
                logger.debug("Filtered topo %s by user" % topo)
                topo_to_cases_map.pop(topo)

        # filter case or filter case on topo
        for topo, caselist in topo_to_cases_map.iteritems():
            filter_case_list = [ case for case in caselist if self.is_filter_case(case) or self.is_filter_case_on_topo(topo, case)]
            for i in filter_case_list:
                logger.debug("Filtered case %s on topo %s by user" % (i, topo))
                caselist.remove(i)

    def filter_by_platform(self, topo_to_cases_map, platform):
        for topo, caselist in topo_to_cases_map.iteritems():
            tmp_list = caselist[:]
            for case in tmp_list:
                if self.is_case_valid_on_platform(case, platform):
                    logger.debug("Testcase '%s' on platform %s is filtered!"
                        % (case, platform))
                    caselist.remove(case)

    def select_by_platform(self, topo_to_cases_map, platform):
        for topo, caselist in topo_to_cases_map.iteritems():
            tmp_list = caselist[:]
            for case in tmp_list:
                if not self.is_case_valid_on_platform(case, platform):
                    logger.debug("Testcase '%s' on platform %s is not valid! Remove it!"
                        % (case, platform))
                    caselist.remove(case)

    def validation_topo_cases(self, topo_to_cases_map):
        unavailable_topo = []
        unavailable_cases = []

        for topo, caselist in topo_to_cases_map.iteritems():

            if not self.tb_info.is_valid_topo(topo):
                logger.warning("Topo '%s' does not support on this env! " % topo )
                unavailable_topo.append(topo)
                continue

            for case in caselist:
                if case not in self.tc_props:
                    logger.critical("Testcase '%s' does not support now!" % case )
                    unavailable_cases.append(case)
                elif topo not in self.tc_props[case]['topologies']:
                    logger.critical("Testcase '%s' could not run on topo '%s'!" % (case, topo))
                    unavailable_cases.append(case)

        if len(unavailable_topo) !=0 :
            logger.warning("Skip unsupport topo %s !!" % yaml.dump(unavailable_topo))
            for topo in unavailable_topo:
                topo_to_cases_map.pop(topo)

        if len(unavailable_cases) != 0:
            logger.debug("Unavailabe testcases %s" % yaml.dump(unavailable_cases))
            logger.critical("Exit for testcases validation FAILED!")
            exit(1)

    def get_to_run(self):
        platform = self.platform
        if platform == 'vskvm':
            to_run = self.vskvm_topo_cases.copy()
        elif platform == 'dut':
            to_run = self.dut_topo_cases.copy()

        # if user does not give topo_cases, then use pre-defined testscases instead.
        # On platform dut: run all applicable cases on topo [ptf32, t0, t1, t1-lag, t1-6, t1-9-lag]
        # On platform vskvm: run all vskvm cases on topo [ptf32, t0, t1, t1-lag, t1-6, t1-9-lag]
        if len(to_run) == 0:
            to_run = self.available_topo_cases.copy()

            # filter unsupported topo
            default_topos = ['ptf32', 't0', 't1', 't1-lag', 't1-6', 't1-9-lag']
            for topo in to_run.keys():
                if topo not in default_topos:
                    to_run.pop(topo)

            self.select_by_platform(to_run, platform)

        if len(self.filter):
            self.filter_by_user(to_run)

        self.validation_topo_cases(to_run)

        return to_run


class MyAnsibleHost(AnsibleHost):
    def __init__(self, hostname):
        ansible_cfg = {
            'inventory': INVENTORY_FILE,
            'host_pattern': 'all',
            'connection': ansible.constants.DEFAULT_TRANSPORT,
            'become_method': ansible.constants.DEFAULT_BECOME_METHOD,
            'become_user': ansible.constants.DEFAULT_BECOME_USER,
        }

        adhoc = partial(get_host_manager, **ansible_cfg)

        AnsibleHost.__init__(self, adhoc, hostname, is_local=hostname == 'localhost')

        self.host.options['inventory_manager'].clear_pattern_cache()

    def _run(self, *module_args, **complex_args):
        logger.debug(
            "Run ansible module:\n" + yaml.dump({
                "module_name": self.module_name,
                "module_args": module_args,
                "complex_args": complex_args
            })
        )

        if DRY_RUN:
            logger.debug("Dry run...")
            return ModuleResult(rc=0, stdout='', stderr='')
      
        res = AnsibleHost._run(self, *module_args, **complex_args)
        
        logger.debug("Module result\n{}".format(dump_ansible_results(res)))

        return res


class Timer(object):
    def __init__(self, func=time.time):
        self.elapsed = 0.0
        self._func = func
        self._start = None
    
    def start(self):
        if self.running:
            raise RuntimeError('Timer already started')
        self._start = self._func()
    
    def stop(self):
        if not self.running:
            raise RuntimeError('Timer has not started!')
        end = self._func()
        self.elapsed += end - self._start
        self._start = None
    
    def reset(self):
        self.elapsed = 0.0
    
    @property
    def running(self):
        return self._start is not None
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, *args):
        self.stop()


# helper functions
def get_ansible_host(host_pattern):
    host = ansible_hosts.get(host_pattern, None)

    if host is None:
        try:
            host = MyAnsibleHost(host_pattern)
            ansible_hosts[host_pattern] = host
        except KeyError:
            logger.critical("Not found ansible host for {}".format(host_pattern))
            exit(1)

    return host

def get_ansible_host_var(host_pattern, var):
    host = get_ansible_host(host_pattern)

    res = host.debug(var=var).get(var, '')

    if "NOT DEFINED!" in res:
        return None

    return res

def get_ansible_host_ip_by_name(host_pattern):
    try:
        host_ip = get_ansible_host_var(host_pattern, 'ansible_host')
        if not DRY_RUN:
            IPAddress(host_ip)
    except AddrFormatError:
        logger.critical("Invalid ip address string {} for host {}".format(host_ip, host_pattern))
        exit(1)

    return host_ip


def run_shell_cmd(cmd, return_rc=True, return_stdout=False, force_running=False):
    logger.debug("Run shell command: "+ cmd )

    if DRY_RUN and not force_running:
        logger.debug("Dry run...")
        rc, out_buffer = 0, ''
    else:
        out_buffer = ''

        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE , stderr=subprocess.STDOUT)      
        with p.stdout:
            for line in iter(p.stdout.readline, b''):
                if return_stdout:
                    out_buffer += line
                logger.debug(line.strip('\n'))       
        
        rc = p.wait() # 0 means success

        logger.debug("rc {}".format(rc))

    if return_rc and return_stdout:
        return rc, out_buffer
    elif return_rc:
        return rc
    elif return_stdout:
        return out_buffer

def my_sleep(wait_time, comment=''):
    logger.info("Sleep {}: {}".format(wait_time, comment))
    if DRY_RUN:
        logger.debug("Dry run...")
    else:
        time.sleep(wait_time)
    logger.debug('Sleep done!')

def my_copy(src, dst):
    if not os.path.exists(os.path.dirname(dst)):
        os.makedirs(os.path.dirname(dst))
    shutil.copy(src, dst)

def archive_logs(log_dir, tc_paras, tb_paras):
    archive_logs_flag = tc_paras.get('archive_logs_flag', True)
    if not archive_logs_flag:
        logger.info("Skip archived logs.")
    else:
        archive_logs = tc_paras.get('archive_logs', {})
        dut_common_log = [
            "/var/log/syslog",
            "/var/log/swss/sairedis.rec",
            "/var/log/swss/swss.rec"
        ]
        archive_helper_playbook = r'roles/test/files/tools/archive_logs.yml'
        cmd_tpl = 'ANSIBLE_STDOUT_CALLBACK=yaml ansible-playbook {playbook} -i lab -vvv -e "target={host}" -e "local_dir={local_dir}" -e "file_patterns={patterns}"'

        # archive logs from ptf
        logs = archive_logs.get('ptf', [])
        if logs and len(logs):
            ptf_host = 'ptf_'+tb_paras['group-name']
            cmd = cmd_tpl.format(
                    playbook=archive_helper_playbook,
                    host=ptf_host,
                    local_dir=log_dir,
                    patterns=','.join(logs)
                )
            run_shell_cmd(cmd)
            logger.debug("Archived logs from ptf '{}': {}".format(ptf_host, logs))

        # archive logs from dut
        dut_logs = dut_common_log + (archive_logs.get('dut') or [])
        if dut_logs and len(dut_logs):
            cmd = cmd_tpl.format(
                    playbook=archive_helper_playbook,
                    host=tb_paras['dut'],
                    local_dir=log_dir,
                    patterns=','.join(dut_logs)
                )
            run_shell_cmd(cmd)
            logger.debug("Archived logs from dut '{}': {}".format(tb_paras['dut'], dut_logs))

        logger.info("Archived logs.")

def remove_topo(testbed_name, ignore_errors=False):
    cmd = "./testbed-cli.sh -m {} remove-topo {} password.txt -e remove_sonic_vm=false -vvvv".format(INVENTORY_FILE, testbed_name)

    rc = run_shell_cmd(cmd)
    if rc != 0 and not ignore_errors:
        logger.critical("remove topo failed! exit!")
        exit(1)

    logger.info("Removed topo " + testbed_name)

def add_topo(testbed_name):
    cmd = "./testbed-cli.sh -m {} add-topo {} password.txt -vvvv".format(INVENTORY_FILE, testbed_name)

    rc = run_shell_cmd(cmd)
    if rc != 0:
        logger.critical("Add topo failed! exit!")
        exit(1)

    logger.info("Add topo " + testbed_name)

def deploy_dut(tb_paras):
    """
    deploy minigraph on dut
    ansible-playbook -i lab config_sonic_basedon_testbed.yml
                     -l str-msn2700-01
                     -vvv
                     -e vm_base="VM0100"
                     -e topo=t1
                     -e testbed_name=vms-t1
                     -e deploy=True
                     -e local_minigraph=True
                     -e save=True
    """

    rc = run_shell_cmd(' \
        ANSIBLE_STDOUT_CALLBACK=yaml \
        ansible-playbook \
        -i lab config_sonic_basedon_testbed.yml \
        -l {dut_name} \
        -vvv \
        -e vm_base="{vm_base}" \
        -e topo={topo} \
        -e testbed_name={tb_name} \
        -e deploy=True \
        -e local_minigraph=True \
        -e save=True'
        .format(
            dut_name=tb_paras['dut'],
            vm_base=tb_paras['vm_base'],
            topo=tb_paras['topo'],
            tb_name=tb_paras['conf-name']
            )
        )

    if rc != 0 :
        logger.critical("Deploy failed!! rc {}".format(rc))
        exit(1)

    logger.info("Deploied dut.")

def wait_for_ptf_stable(ptf_name, timeout=100, poll_interval=10, ignore_error=False):
    target = "ptf {}".format(ptf_name)

    logger.info("Wait for {} to be stable...(timeout: {}, interval:{})".format(target, timeout, poll_interval))

    start_time = time.time()

    while True:
        if check_ptf_status(ptf_name):
            logger.info("Ptf is stable!")
            return True

        used_time = time.time() - start_time
        if used_time > timeout:
            logger.critical("Wait for ptf stable timeout({}s)!!".format(timeout))
            if not ignore_error:
                exit(1)
            else:
                return False

        my_sleep(poll_interval, "Wait for next check..(Used {:.2f}s)".format(used_time))

def wait_for_dut_stable(dut_name, topo, timeout=1800, poll_interval=60, ignore_error=False):
    target = "topo {} dut {}".format(topo, dut_name)

    logger.info("Wait for {} to be stable...(timeout: {}, interval:{})".format(target, timeout, poll_interval))

    if topo == 'ptf32':
        my_sleep(30, "Wait for {} to be stable!".format(target))
        logger.info("Dut is stable!")
        return True
    else:
        start_time = time.time()
        reboot_flag = True

        while True:
            if check_dut_status(dut_name):
                logger.info("Dut is stable!")
                return True

            used_time = time.time() - start_time
            if used_time > timeout/2 and reboot_flag == True:
                logger.critical("Restart the device after half the aging time, Wait for dut stable timeout({}s)!!".format(timeout))
                reboot_dut(dut_name, ignore_error=True)
                reboot_flag = False
            if used_time > timeout:
                logger.critical("Wait for dut stable timeout({}s)!!".format(timeout))
                if not ignore_error:
                    exit(1)
                else:
                    return False

            my_sleep(poll_interval, "Wait for next check..(Used {:.2f}s)".format(used_time))

def check_ptf_status(ptf_name):
    ptfhost = get_ansible_host(ptf_name)
    try:
        res = ptfhost.ping()
        is_ok = not res.is_failed
    except Exception as ex:
        logger.warning("Check ptf status failed: {}".format(ex))
        is_ok = False

    if is_ok:
        logger.debug("Check for ptf status ok!!")
    else:
        logger.debug("Check for ptf status failed!!")

    return is_ok

def check_dut_status(dut_name):
    logger.debug('Check for dut [{}] status...'.format(dut_name))
    cmd=r"ANSIBLE_STDOUT_CALLBACK=yaml ansible-playbook -vvv -i lab -l {} ./roles/test/files/tools/check_dut_status.yml".format(dut_name)
    rc = run_shell_cmd(cmd)
    if rc !=0 :
        logger.debug("Check for dut status failed!!")
    return True if rc==0 else False

def get_dut_connected_topo(dut_name):
    duthost = get_ansible_host(dut_name)

    try:
        res = duthost.shell("cat /etc/sonic/topo_marker", module_ignore_errors=True)['stdout'].strip() or None
    except AnsibleConnectionFailure:
        res = None

    logger.debug("Get dut {} currently connected topo: {}".format(dut_name, res))

    return res

def get_dut_platform_name(dut_name):
    duthost = get_ansible_host(dut_name)

    res = duthost.shell("sonic-cfggen -d -v DEVICE_METADATA.localhost.platform")['stdout'].strip()

    if res == '':
        logger.critical("Can not get dut platform name!")
        exit(1)

    logger.debug("Get dut {} platform name: {}".format(dut_name, res))

    return res

def get_dut_hwsku(dut_name):
    duthost = get_ansible_host(dut_name)
    res = duthost.shell("sonic-cfggen -d -v DEVICE_METADATA.localhost.hwsku")['stdout'].strip()

    if res == '':
        logger.critical("Can not get dut hwsku!")
        exit(1)

    logger.debug("Get dut {} hwsku: {}".format(dut_name, res))

    return res

def set_dut_default_hwsku(dut_name, hwsku, hwrev):
    platform = get_dut_platform_name(dut_name)
    platform_dir = os.path.join("/usr/share/sonic/device/", platform)

    default_sku_file = os.path.join(platform_dir, 'default_sku')
    backup_default_sku_file = os.path.join(platform_dir, 'default_sku.origin')

    duthost = get_ansible_host(dut_name)

    # backup default sku
    if not duthost.stat(path=backup_default_sku_file)['stat']['exists']:
        duthost.shell("cp {} {}".format(default_sku_file, backup_default_sku_file))

    # modify default_hwsku
    duthost.replace(
        dest="/usr/share/sonic/device/{}/default_sku".format(platform),
        regexp="^[^ ]+",
        replace=hwsku
    )

    logger.info("Set dut hwsku successfully!")

    return True

def set_dut_breakout(dut_name, ports_attrs):
    BREAKOUT_TYPE = [2, 4]
    BREAKOUT_ACTION = ['disable', 'enable']

    duthost = get_ansible_host(dut_name)

    for p_range, attrs in ports_attrs.iteritems():
        b = attrs.get('Breakout')

        if b is None:
            continue

        if b in BREAKOUT_TYPE:
            # FIXME
            #duthost.shell("port-breakout -0 breakout {0} {1}".format(p_range, b))
            duthost.shell("port-breakout -0 breakout {0}".format(p_range), module_ignore_errors=True)
            msg = "Breakout: breakout port {0} to {1} sub-ports ".format(p_range, b)

        elif b in BREAKOUT_ACTION:
            # FIXME
            #duthost.shell("port-breakout -0 {1} {0}".format(p_range, b))
            duthost.shell("port-breakout -0 {1} {0}".format(p_range, b), module_ignore_errors=True)
            msg = "Breakout: {1} port {0} ".format(p_range, b)

        else:
            logger.warning("Invalid breakout value {1} for {0}".format(p_range, b))
            continue

        logger.info(msg)

def set_dut_media_type(dut_name, if_attrs):
    MEDIA_TYPE_LIST = ['opt', 'dac']

    duthost = get_ansible_host(dut_name)

    for if_name, attrs in if_attrs.iteritems():
        m = attrs.get('MediaType')

        if m is None:
            continue

        if m not in MEDIA_TYPE_LIST:
            logger.warning("Invalid interface media type {1} for {0}. Valid options: {2}".format(if_name, m, MEDIA_TYPE_LIST))
            continue

        duthost.shell("sfpdet config -p {} -m {}".format(if_name, m))

        msg = "Set interface {} transceiver type {} ".format(if_name, m)

        logger.info(msg)

def set_dut_port_config_ini_speed(dut_name, if_attrs):
    # get platform name of dut
    platform = get_dut_platform_name(dut_name)
    hwsku = get_dut_hwsku(dut_name)

    duthost = get_ansible_host(dut_name)

    for if_name, attrs in if_attrs.iteritems():
        s = attrs.get('Bandwidth')

        if s is None:
            continue

        ini_file = '/usr/share/sonic/device/{}/{}/port_config.ini'.format(platform, hwsku)

        duthost.lineinfile(
            dest=ini_file,
            regexp=r"^(?P<interface>{}\b)(?P<lanes>[ ]+[^ ]+)(?P<alias>[ ]+[^ ]+)(?P<index>[ ]+[^ ]+)(?P<speed_pre_spaces> +)(?P<speed>\d+)(?P<others>.*)$".format(if_name),
            line=r"\g<interface>\g<lanes>\g<alias>\g<index>\g<speed_pre_spaces>{}\g<others>".format(s),
            backrefs='yes'
        )

        msg = "Set interface {} speed {} ".format(if_name, s)

        logger.info(msg)


def reboot_dut(dut_name, ignore_error=False):
    logger.info("Reboot dut {}...".format(dut_name))

    # cmd = r'ansible-playbook -i lab -l {} test_sonic.yml -e "testcase_name=reboot testbed_name=vms-t1"'.format(dut_name)
    # could not use MyAnsibleHost here
    cmd = r"ansible -i lab {} -m include -a './roles/test/tasks/common_tasks/reboot_sonic.yml'".format(dut_name)

    rc = run_shell_cmd(cmd)
    if rc != 0 :
        logger.critical("Reboot dut failed!! rc {}. ".format(rc) + " Ignore errors..." if ignore_error else "")
        if not ignore_error:
            exit(1)
    return True if rc == 0 else False


def upgrade_dut(dut_name, url, ignore_error=False):
    logger.info("Upgrade dut with image {}".format(url))

    cmd=r'ANSIBLE_STDOUT_CALLBACK=yaml ansible-playbook upgrade_sonic.yml -i lab -l {} -vvv -e "upgrade_type=sonic" -e "image_url=\'{}\'"'.format(dut_name, url)
    rc = run_shell_cmd(cmd)

    # copy topo_marker from old_config to /etc/sonic
    # since update_graph does not do that.
    duthost = get_ansible_host(dut_name)
    old_topo_marker = "/etc/sonic/old_config/topo_marker"
    if duthost.stat(path=old_topo_marker)['stat']['exists']:
        duthost.shell("cp {} /etc/sonic/.".format(old_topo_marker))

    if rc != 0 :
        logger.critical("Upgrade dut failed!! rc {}".format(rc))
        if not ignore_error:
            exit(1)

    return True if rc == 0 else False


def setup_dut_after_install_new_image(dut_name, testbed_config):
    """
    setup dut after install new image. Almost args are retrieved from testbed_config file.(e.g. testbed-topo1.yaml)

    set dut default hwsku:
        devices -> dut_name -> hwsku                    
        devices -> dut_name -> hwrev

    breakout ports: 
        topology -> dut_name -> indices -> port_index_range_list -> Breakout -> 2/4/disable/enable

    reboot after breakout

    set port media-type
        topology -> dut_name -> Interfaces -> interface_name -> MediaType -> dac/opt

    modify speed of port_config.ini for generate correct minigraph when deploy dut:
        topology -> dut_name -> Interfaces -> interface_name -> Bandwidth

    """
    if not testbed_config or not os.path.exists(testbed_config):
        logger.critical("Not found yaml file for testbed processing! {}".format(testbed_config))
        exit(1)

    with open(testbed_config, 'r') as fh:
        tbcfg = yaml.safe_load(fh)

    hwsku = tbcfg['devices'][dut_name]['hwsku']
    hwrev = tbcfg['devices'][dut_name].get('hwrev')
    ports_attrs = tbcfg['topology'][dut_name].get('indices', {})
    if_attrs = tbcfg['topology'][dut_name].get('interfaces', {})

    duthost = get_ansible_host(dut_name)

    set_dut_default_hwsku(dut_name, hwsku, hwrev)

    # copy src_hwsku_dir to dst_hwsku_dir
    if (hwrev is not None) and (hwrev != ''):
        platform = get_dut_platform_name(dut_name)
        platform_dir = os.path.join("/usr/share/sonic/device/", platform)
        
        src_hwsku = "{}-{}".format(hwsku, hwrev)  # e.g. Accton-AS7116-54X-R0A
        src_hwsku_dir = os.path.join(platform_dir, src_hwsku)
        dst_hwsku_dir = os.path.join(platform_dir, hwsku)

        if not duthost.stat(path=src_hwsku_dir)['stat']['exists']:
            logger.critical("Not found hwsku dir for {}".format(src_hwsku_dir))
            exit(1)

        duthost.shell("rm -rf {}".format(dst_hwsku_dir), module_ignore_errors=True)
        duthost.shell("cp -r {} {}".format(src_hwsku_dir, dst_hwsku_dir))
        logger.info("Copy {} to {}".format(src_hwsku_dir, dst_hwsku_dir))

    set_dut_breakout(dut_name, ports_attrs)

    # remove first boot flag
    sonic_version = duthost.shell("sonic-cfggen -y /etc/sonic/sonic_version.yml -v build_version")['stdout']
    duthost.shell("rm '/host/image-{}/platform/firsttime'".format(sonic_version), module_ignore_errors=True)
    
    logger.info("Removed first boot flag!")

    # build preset config_db.json with eth0 and DEVICE_METADATA configuration except hwsku
    old_cfg_facts = json.loads(duthost.shell("sonic-cfggen -d --print-data")['stdout'])
    l2_cfg_facts = json.loads(duthost.shell("sonic-cfggen -k {} --preset l2".format(hwsku))['stdout'])

    l2_cfg_facts['MGMT_INTERFACE'] = old_cfg_facts['MGMT_INTERFACE']
    for k,v in old_cfg_facts['DEVICE_METADATA']['localhost'].iteritems():
        if k != 'hwsku':
            l2_cfg_facts['DEVICE_METADATA']['localhost'][k] = v

    duthost.copy(
        content=json.dumps(l2_cfg_facts),
        dest="/tmp/tmp_cfg"
    )

    duthost.shell("sonic-cfggen -j /tmp/tmp_cfg --print-data > /etc/sonic/config_db.json")  # verify and format the cfg with sonic-cfggen

    reboot_dut(dut_name, ignore_error=True)

    set_dut_media_type(dut_name, if_attrs)

    set_dut_port_config_ini_speed(dut_name, if_attrs)

def log_to_dut(dut_name, message, priority='INFO'):

    duthost = get_ansible_host(dut_name)

    duthost.shell('logger -p {} "{}"'.format(priority, message), module_ignore_errors=True)

    return True

def disable_logrotate(dut_name):

    duthost = get_ansible_host(dut_name)

    logrotate_conf = "/etc/cron.d/logrotate"
    res = duthost.replace(
              dest=logrotate_conf,
              regexp='^',
              replace='#'
          )

    if res.is_failed:
        logger.warning("Disable logrotate failed!! {}".format(dump_ansible_results(res)))
        return False

    # Wait for logrotate from previous cron task run to finish
    wait_cmd = """
        for i in `seq 1 5`;
        do
            if (ansible -i lab %s -b -m shell -a "! ps -aux| grep logrotate | grep -v grep"); then
                break
            fi
            sleep 1
        done """ % dut_name
    run_shell_cmd(wait_cmd)

    logger.debug("Disabled logrotate.")

    return True

def enable_logrotate(dut_name):
    duthost = get_ansible_host(dut_name)

    logrotate_conf = "/etc/cron.d/logrotate"
    res = duthost.replace(
              dest=logrotate_conf,
              regexp='^#',
              replace=''
          )

    if res.is_failed:
        logger.warning("Enable logrotate failed!! \n{}".format(dump_ansible_results(res)))
        return False

    logger.debug("Enabled logrotate")

    return True

def force_logrotate(dut_name):
    duthost = get_ansible_host(dut_name)

    duthost.shell("logrotate -f /etc/logrotate.conf", module_ignore_errors=True)

    return True

def deco_check_cfg_recover(task):
    def _check_cfg_recover(*args, **kwargs):
        global CFG_CHECK_FAIL_DICT

        tc_name = kwargs.get('testcase_name') or args[0]

        dut = kwargs.get('tb_paras', {}).get('dut')
        if not dut:
            dut = args[2].get('dut')
        if dut is None:
            logger.critical("Not Found dut!!")
            exit(2)

        duthost = get_ansible_host(dut)

        pre_config_db = '/tmp/pre_cfgdb'
        post_config_db = '/tmp/post_cfgdb'
        pre_bgp_cfg = '/tmp/pre_bgp_cfg'
        post_bgp_cfg = '/tmp/post_bgp_cfg'

        # save cfg before test
        duthost.shell("vtysh -c 'show run' > {}".format(pre_bgp_cfg), module_ignore_errors=True)
        duthost.shell("sonic-cfggen -d --print-data > {}".format(pre_config_db), module_ignore_errors=True)

        #execute test
        ret = task(*args, **kwargs)

        # save cfg after test
        duthost.shell("vtysh -c 'show run' > {}".format(post_bgp_cfg), module_ignore_errors=True)
        duthost.shell("sonic-cfggen -d --print-data > {}".format(post_config_db), module_ignore_errors=True)

        # diff cfgs
        cfgdb_res = duthost.shell("diff {} {}".format(pre_config_db, post_config_db), module_ignore_errors=True)
        bgp_res = duthost.shell("diff {} {}".format(pre_bgp_cfg, post_bgp_cfg), module_ignore_errors=True)

        if cfgdb_res.is_failed :
            CFG_CHECK_FAIL_DICT['config_db'].append(tc_name)
            logger.critical("Configdb mismatch after test {}!!".format(tc_name))

        if bgp_res.is_failed :
            CFG_CHECK_FAIL_DICT['bgp'].append(tc_name)
            logger.critical("BGP cfg mismatch after test {}!!".format(tc_name))

        return ret

    return _check_cfg_recover

@deco_check_cfg_recover
def run_case(testcase_name, tc_paras, tb_paras):
    logger.debug(
        "Run {} with parameters: \n {}"
        .format(
            testcase_name,
            yaml.dump({
                "Test parameters:": tc_paras,
                "Testbed parameters": tb_paras
            })
        )
    )
    # force logrotate then we could get exactly logs after test
    force_logrotate(tb_paras['dut'])

    topo = tb_paras['topo']
    start_msg = "{topo} - {case} start...".format(topo=topo, case=testcase_name)
    logger.info(start_msg)
    log_to_dut(tb_paras['dut'], start_msg)

    # create log dir
    ansible_log = logger.get_case_log_file(topo, testcase_name)

    # grenerate and run
    cmd_tmpl = r"""
        ANSIBLE_STDOUT_CALLBACK=yaml \
        ansible-playbook \
        -i lab \
        -vvv \
        --limit {{ tb_paras.dut }} \
        test_sonic.yml \
        -e "testcase_name={{ testcase_name }}" \
        -e "testbed_name={{ tb_paras["conf-name"] }}" \

        {%- if 'testbed_type' in tc_paras.required_vars  %}
        -e "testbed_type={{ tb_paras.topo }}" \
        {%- endif %}

        {%- if 'ptf_host' in tc_paras.required_vars  %}
        -e "ptf_host='{{ tb_paras.ptf_ip_addr }}'" \
        {%- endif %}

        {%- if 'mtu' in tc_paras.required_vars  %}
        -e "mtu={{ tc_paras.required_vars.mtu if tc_paras.required_vars.mtu else MTU_MTK_SIZE }}" \
        {%- endif %}

        {%- if 'dscp_mode' in tc_paras.required_vars  %}
        -e "dscp_mode=pipe ecn_mode=copy_from_outer" \
        {%- endif %}

        {%- if 'vm_hosts' in tc_paras.required_vars  %}
        -e "vm='{{ tb_paras.vm_base }}'" \
        {%- endif %}

        {%- if 'sudo' in tc_paras  %}
        -b \
        {%- endif %}

        {%- if 'extra_cmd_args' in tc_paras %}
        -e "{{ tc_paras.extra_cmd_args }}" \
        {%- endif %}
        > {{ log }}
    """

    cmd = jinja2.Template(cmd_tmpl).render(
        testcase_name=testcase_name,
        tc_paras=tc_paras,
        tb_paras=tb_paras,
        MTU_MTK_SIZE=MTU_MTK_SIZE,
        log=ansible_log
    )

    rc = run_shell_cmd(cmd)

    if rc != 0:
        logger.info( "{topo} - {case} failed!!".format(topo=topo, case=testcase_name))
        fail_ansible_log = logger.get_fail_case_log_file(topo, testcase_name)
        my_copy(ansible_log, fail_ansible_log)
    else:
        logger.info( "{topo} - {case} passed!!".format(topo=topo, case=testcase_name))

    # wait for dut recover due to reboot in some cases
    sleep_time = tc_paras.get('sleep_for_reboot', None)
    if sleep_time:
        my_sleep(sleep_time, "sleep for dut reboot")
    if tc_paras.get('reboot_after_test', False):
        logger.info( "%s need reboot after test!" % testcase_name)
        reboot_dut(tb_paras['dut'], ignore_error=True)

    end_msg = "{topo} - {case} end.".format(topo=topo, case=testcase_name)
    logger.info(end_msg)
    log_to_dut(tb_paras['dut'], end_msg)

    return True if rc==0 else False

def pre_case(testcase_name, tc_paras, tb_paras):
    pass

def post_case(testcase_name, tc_paras, tb_paras):
    case_log_dir = os.path.join(logger.log_dir, tb_paras['topo'], testcase_name)
    archive_logs(case_log_dir, tc_paras, tb_paras)

def print_tc_result(tc_result):
    for topo, cases in tc_result.iteritems():
        cases_res_list = [ tc_res['result'] for tc_res in cases.values() ]
        res_counter = Counter(cases_res_list)
        logger.info("Topo {} - Total: {} Passed:{} Failed: {}".format(
            topo,
            len(cases),
            res_counter['pass'],
            res_counter['fail']
        ))

        for case, tc_res in cases.iteritems():
            t1, t2, t3 = (tc_res['pre_time'], tc_res['run_time'], tc_res['post_time'])
            total_time = t1 + t2+ t3
            logger.debug("{} - {} elapsed time: "
                         "pre {:.2f}s, running {:.2f}s, post {:.2f}s, total {:.2f}s".format(
                             topo, case,
                             t1, t2, t3, total_time
                         ))

def print_cfg_chk_result(result_dict):
    logger.info("Config check result\n" + yaml.dump(result_dict))

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--topo2cases-file', help='Testcases file path')
    parser.add_argument('-s', '--sonic-image', help='Sonic image file path, http url or local file on dut')
    parser.add_argument('-c', '--testbed-config', help='a file for the testbed processing script. e.g. testbed-topo1.yaml')
    parser.add_argument('-t', '--testbed-file', help='Testbed file. Default testbed.csv', default=TESTBED_FILE)
    parser.add_argument('-l', '--logging-level', help='Logging level(default: INFO)', choices=LOGGING_LEVEL_MAP.keys())
    parser.add_argument('-b', '--build-number', help='Jenkins build number')
    parser.add_argument('--dry', action='store_true')
    parser.add_argument('-p', '--platform', help='vskvm or dut',
                        choices=['vskvm', 'dut'],
                        required=True,
                        default='dut')
    return parser.parse_args()

def main():
    # initilization
    options = parse_args()
    platform = options.platform
    global DRY_RUN
    DRY_RUN = options.dry

    # Init_logger
    logger.create_main_log(options)
    if options.logging_level is not None:
        logger.stdout_handler.setLevel(LOGGING_LEVEL_MAP[options.logging_level])

    # Gather testcaseinfo
    logger.info("Gather testcaseinfo")
    available_testcaseinfo = TestcaseInfo(TESTCASE_FILE_LIST)

    logger.info("Gather testbedinfo")
    testbedinfo = TestbedInfo(options.testbed_file)

    logger.info("Available test_topo: %s" % testbedinfo.support_topo)

    # Generate map of topo to testcases for execution
    udf_test = UDFTest(available_testcaseinfo, testbedinfo, platform)
    if options.topo2cases_file:
        udf_test.parse_udf_testcases_file(options.topo2cases_file)

    to_run_topo_cases_map = udf_test.get_to_run()

    logger.info("Topo and cases to be executed: \n" + yaml.dump(to_run_topo_cases_map))

    # Assume that all testbed use the same dut.
    # So we can get dut name from any testbed configurations.
    tmp_tb_paras = testbedinfo.get_testbed_by_topo(to_run_topo_cases_map.keys()[0])

    # if not found currently connected topo, restore by removing each topo
    # if found, leave it.
    cur_topo = get_dut_connected_topo(tmp_tb_paras['dut'])
    if cur_topo is None:
        logger.warning("Not found connected topo! Restore all topological environment...")
        for testbed_name in testbedinfo.testbed_topo.keys():
            remove_topo(testbed_name, ignore_errors=True)

    # Upgrade dut if need
    if options.sonic_image and platform != 'vskvm':
        upgrade_dut(tmp_tb_paras['dut'], options.sonic_image)

        setup_dut_after_install_new_image(tmp_tb_paras['dut'], options.testbed_config)

        # recover config after upgrade dut
        if cur_topo is not None:
            tmp_tb_paras = testbedinfo.get_testbed_by_topo(cur_topo)
            deploy_dut(tmp_tb_paras)

    # Start sonic test
    result_dict = {}
    for topo, caselist in to_run_topo_cases_map.iteritems():
        result_dict[topo] = {}
        total_count = len(caselist)
        tb_paras = testbedinfo.get_testbed_by_topo(topo)

        # add topo if need
        cur_topo = get_dut_connected_topo(tb_paras['dut'])

        if cur_topo != topo:
            # remove topo if already connected
            if cur_topo:
                logger.info("Need connect to topo {}, but currently connected to {}, remove it first!".format(topo, cur_topo))
                remove_topo(testbedinfo.get_testbed_by_topo(cur_topo)['conf-name'])

            add_topo(tb_paras['conf-name'])

            deploy_dut(tb_paras)

        
        disable_logrotate(tb_paras['dut'])

        wait_for_ptf_stable('ptf_{}'.format(tb_paras['group-name']))
        wait_for_dut_stable(tb_paras['dut'], tb_paras['topo'])

        # run test
        for index, testcase in enumerate(caselist, 1):
            logger.info('{} progress [{}/{}]'.format(topo, index, total_count))

            tc_paras = available_testcaseinfo.tc_props.get(testcase)

            with Timer() as pre_timer:
                pre_case(testcase, tc_paras, tb_paras)
            with Timer() as run_timer:
                res = run_case(testcase_name=testcase, tc_paras=tc_paras, tb_paras=tb_paras)
            with Timer() as post_timer:
                post_case(testcase, tc_paras, tb_paras)

            result_dict[topo][testcase] = {
                'result': 'pass' if res else 'fail',
                'pre_time': pre_timer.elapsed,
                'run_time': run_timer.elapsed,
                'post_time': post_timer.elapsed
            }

        # Not to remove topo after test, thus we don't need to add topo again if next test use the same topo.
        # remove_topo(tb_paras['conf-name'])

        logger.info( '*'*10 + topo + " topology test finished " + '*'*10)

    # log result
    print_tc_result(result_dict)
    print_cfg_chk_result(CFG_CHECK_FAIL_DICT)

    # exit with non-zero return code. Jenkins could fail the build by the rc.
    for topo, cases in result_dict.iteritems():
        for tc in cases:
            if cases[tc]['result'] != 'pass':
                exit(1)  # exit with non-zero return code. Jenkins could fail the build by the rc.

if __name__ == "__main__":
    main()
