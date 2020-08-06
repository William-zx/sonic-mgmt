#!/usr/bin/env python

import yaml
import os.path
import subprocess
import re
import shutil
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('-p', '--platform', action='store_true')
parser.add_argument('-t', '--tbcfgfile', action='store_true')
parser.add_argument('-d', '--debug', action='store_true')
parser.add_argument('-i', '--server-ip', help='server ip of which should be read from testbed.yml')
option = parser.parse_args()

script_dir = os.path.dirname(os.path.realpath(__file__))
MAIN_TESTBED = 'testbed.yml'
MGMT_PREFIX = '192.168.11.0/24'
MAIN_TESTBED_PATH = os.path.join(script_dir, MAIN_TESTBED)

if not os.path.exists(MAIN_TESTBED_PATH):
    raise Exception('Can not find {}, exit!!'.format(MAIN_TESTBED_PATH))
    

with open(MAIN_TESTBED_PATH, 'r') as fh:
    tb = yaml.safe_load(fh.read())

# if user not specified ip then retrieve ip from which interface to MGMT prefix
# ip route get 192.168.11.0/24
# 192.168.11.0 via 192.168.13.254 dev ens160  src 192.168.13.82 
#     cache
if option.server_ip==None:
    ip_route_get_cmd = 'ip route get {}'.format(MGMT_PREFIX)
    ip_route_get = subprocess.check_output(ip_route_get_cmd, shell=True)
    server_ip = re.search(r'src ([\d.]+)', ip_route_get).group(1)
else:
    server_ip = option.server_ip

if server_ip not in tb['testbed']:
    raise Exception('Can not find testbed configuration file for server_ip {}'.format(server_ip))

tb_cfg = tb['testbed'][server_ip]['tb_file']
platform = tb['testbed'][server_ip]['platform']

if option.platform:
    print platform

if option.tbcfgfile:
    print tb_cfg
