import pytest
import time
import sys
import random
import logging
import threading
import Queue
from ptf_runner import ptf_runner
from netaddr import IPNetwork
from natsort import natsorted


# vars
SLEEP_TIME = 2
MAX_TIMES = 100

g_vars                          = {}
route_info                      = {
                                    "v4": ["172.16.1.0/24"],
                                    "v6": ["2100::/64"]
                                }
# only test src-ip, dst-ip, ip-proto, src-port, dst-port
# inner-src-ip, inner-dst-ip, vlan-id, eth-type, src-mac, dst-mac, ingress-port do not test
# hash_keys                       = ["src-ip", "dst-ip", "inner-src-ip", "inner-dst-ip", "vlan-id", "ip-proto", "eth-type",
#                                 "src-port", "dst-port", "src-mac", "dst-mac", "ingress-port"]
test_hash_keys                  = ["src-ip", "dst-ip", "ip-proto", "src-port", "dst-port"]

# fixtures
@pytest.fixture(scope="module")
def host_facts(duthost):
    return duthost.setup()["ansible_facts"]

@pytest.fixture(scope="module")
def mg_facts(duthost, testbed):
    hostname = testbed["dut"]
    return duthost.minigraph_facts(host=hostname)["ansible_facts"]

def get_route_nexthop_info(duthost, ip):
    if IPNetwork(ip).version == 4:
        res = duthost.shell("show ip route {} |grep '*'".format(ip))["stdout"]
    else:
        res = duthost.shell("show ipv6 route {} |grep '*'".format(ip))["stdout"]
    
    out_port_list = []
    nexthop_info = {}
    if res:
        for line in res.split('\n'):
            if "directly connected" in line:
                out_port = line.split(",")[-1].split()[-1]
                nexthop_info[out_port] = "directly connected"
            else:
                out_port = line.split(",")[-1].split()[-1]
                nexthop = line.split(",")[0].split()[-1]
                out_port_list.append(out_port)
                nexthop_info[out_port] = nexthop
    return natsorted(out_port_list), nexthop_info

@pytest.fixture(scope="module", autouse=True)
def setup_init(mg_facts, duthost, ptfhost):
    # vars
    global g_vars

    g_vars["router_mac"] = duthost.shell("redis-cli -n 4 hget 'DEVICE_METADATA|localhost' mac")["stdout"]
    g_vars["vlan_id"] = mg_facts["minigraph_vlans"].values()[0]["vlanid"]
    # Choose the last vlan interface member port for ip forwarding src_port.
    g_vars["src_port_name"] = mg_facts["minigraph_vlans"].values()[0]["members"][-1]
    g_vars["src_port"] = mg_facts["minigraph_port_indices"][g_vars["src_port_name"]]

    g_vars["default_out_port_list"], g_vars["default_ipv4_nexthop_info"] = get_route_nexthop_info(duthost, "0.0.0.0/0")
    g_vars["default_member_port_list"] = map(lambda port: mg_facts["minigraph_portchannels"][port]["members"][0], g_vars["default_out_port_list"])
    g_vars["default_ptf_port_list"] = map(lambda port: mg_facts["minigraph_port_indices"][port], g_vars["default_member_port_list"])

    _, g_vars["default_ipv6_nexthop_info"] = get_route_nexthop_info(duthost, "::/0")

    g_vars["dst_route_list"] = route_info["v4"]
    g_vars["dst_route_list_v6"] = route_info["v6"]

    # init dut
    duthost.shell("mkdir -p /tmp/ecmp")
    duthost.shell("docker exec -i bgp supervisorctl stop bgpd")
    time.sleep(10)

    for port in g_vars["default_out_port_list"]:
        for route in route_info["v4"]:
            setup_static_route(duthost, route, g_vars["default_ipv4_nexthop_info"][port])
        for route in route_info["v6"]:
            setup_static_route(duthost, route, g_vars["default_ipv6_nexthop_info"][port])

    # copy ptftest script
    ptfhost.copy(src="ptftests", dest="/root")
    ptfhost.shell("mkdir -p /tmp/ecmp")
    ptfhost.script("scripts/remove_ip.sh")
    ptfhost.script("scripts/change_mac.sh")

    yield

    for port in g_vars["default_out_port_list"]:
        for route in route_info["v4"]:
            setup_static_route(duthost, route, g_vars["default_ipv4_nexthop_info"][port], op="del", ignore_errors=True)
        for route in route_info["v6"]:
            setup_static_route(duthost, route, g_vars["default_ipv6_nexthop_info"][port], op="del", ignore_errors=True)

    duthost.shell("docker exec -i bgp supervisorctl start bgpd")
    time.sleep(10)
    
def clear_arp_table(duthost, ptfhost, dut_if):
    duthost.shell("ip link set arp off dev {0} && ip link set arp on dev {0}".format(dut_if))
    ptfhost.shell("ip -s -s neigh flush all")
    time.sleep(SLEEP_TIME)

def setup_static_route(duthost, route, nexthop, op="add", distance=None, ignore_errors=False):
    route_cmd = "no " if op == "del" else ""
    route_cmd += "ip route {} {}".format(route, nexthop) if IPNetwork(route).version == 4 else "ipv6 route {} {}".format(route, nexthop)
    route_cmd += " {}".format(distance) if distance else ""

    duthost.shell("vtysh -c 'configure terminal' -c '{}'".format(route_cmd), module_ignore_errors=ignore_errors)
    time.sleep(SLEEP_TIME)

def updown_port_by_cli(duthost, op, port, ignore_errors=False):
    # op should be shutdown or startup
    duthost.shell("config interface {} {}".format(op, port), module_ignore_errors=ignore_errors)
    time.sleep(SLEEP_TIME)

class TestCase1_EcmpGroupAndGroupMemberChange():
    @pytest.fixture(scope="class", autouse=True)
    def recover_port_status(self, duthost):

        yield

        for port in g_vars["default_out_port_list"]:
            updown_port_by_cli(duthost, "startup", port, ignore_errors=True)

    def test_ipv4_load_balance_by_src_ip(self, duthost, ptfhost):
        for route in route_info["v4"]:
            res = duthost.shell("show ip route {}".format(route))["stdout"]
            assert route in res, "the route {} must in route table".format(route)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "ecmp_test.EcmpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port_list": g_vars["default_ptf_port_list"],
                        "router_mac": g_vars["router_mac"],
                        "dst_route_list": g_vars["dst_route_list"]
                            },
                    log_file="/tmp/ecmp/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    def test_ipv4_load_balance_by_src_ip_after_one_nexthop_down(self, duthost, ptfhost):
        port = g_vars["default_out_port_list"][0]
        updown_port_by_cli(duthost, "shutdown", port)
        for route in route_info["v4"]:
            res = duthost.shell("show ip route {}".format(route))["stdout"]
            assert g_vars["default_ipv4_nexthop_info"][port] not in res, "the nexthop {} must not in route table".format(g_vars["default_ipv4_nexthop_info"][port])

        ptf_runner( ptfhost, \
                    "ptftests",
                    "ecmp_test.EcmpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port_list": g_vars["default_ptf_port_list"][1:],
                        "router_mac": g_vars["router_mac"],
                        "dst_route_list": g_vars["dst_route_list"]
                            },
                    log_file="/tmp/ecmp/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    def test_ipv4_load_balance_by_src_ip_only_last_nexthop_up(self, duthost, ptfhost):
        for port in g_vars["default_out_port_list"][1:-1]:
            updown_port_by_cli(duthost, "shutdown", port)
            for route in route_info["v4"]:
                res = duthost.shell("show ip route {}".format(route))["stdout"]
                assert g_vars["default_ipv4_nexthop_info"][port] not in res, "the nexthop {} must not in route table".format(g_vars["default_ipv4_nexthop_info"][port])

        ptf_runner( ptfhost, \
                    "ptftests",
                    "ecmp_test.EcmpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port_list": g_vars["default_ptf_port_list"][-1:],
                        "router_mac": g_vars["router_mac"],
                        "dst_route_list": g_vars["dst_route_list"]
                            },
                    log_file="/tmp/ecmp/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    def test_ipv4_load_balance_by_src_ip_when_all_nexthop_up(self, duthost, ptfhost):
        for port in g_vars["default_out_port_list"][:-1]:
            updown_port_by_cli(duthost, "startup", port)
            for route in route_info["v4"]:
                res = duthost.shell("show ip route {}".format(route))["stdout"]
                assert g_vars["default_ipv4_nexthop_info"][port] in res, "the nexthop {} must in route table".format(g_vars["default_ipv4_nexthop_info"][port])

        ptf_runner( ptfhost, \
                    "ptftests",
                    "ecmp_test.EcmpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port_list": g_vars["default_ptf_port_list"],
                        "router_mac": g_vars["router_mac"],
                        "dst_route_list": g_vars["dst_route_list"]
                            },
                    log_file="/tmp/ecmp/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    def test_ipv6_load_balance_by_src_ip(self, duthost, ptfhost):
        for route in route_info["v6"]:
            res = duthost.shell("show ipv6 route {}".format(route))["stdout"]
            assert route in res, "the route {} must in route table".format(route)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "ecmp_test.EcmpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port_list": g_vars["default_ptf_port_list"],
                        "router_mac": g_vars["router_mac"],
                        "dst_route_list": g_vars["dst_route_list_v6"]
                            },
                    log_file="/tmp/ecmp/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    def test_ipv6_load_balance_by_src_ip_after_one_nexthop_down(self, duthost, ptfhost):
        port = g_vars["default_out_port_list"][0]
        updown_port_by_cli(duthost, "shutdown", port)
        for route in route_info["v6"]:
            res = duthost.shell("show ipv6 route {}".format(route))["stdout"]
            assert g_vars["default_ipv6_nexthop_info"][port] not in res, "the nexthop {} must not in route table".format(g_vars["default_ipv6_nexthop_info"][port])

        ptf_runner( ptfhost, \
                    "ptftests",
                    "ecmp_test.EcmpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port_list": g_vars["default_ptf_port_list"][1:],
                        "router_mac": g_vars["router_mac"],
                        "dst_route_list": g_vars["dst_route_list_v6"]
                            },
                    log_file="/tmp/ecmp/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    def test_ipv6_load_balance_by_src_ip_only_last_nexthop_up(self, duthost, ptfhost):
        for port in g_vars["default_out_port_list"][1:-1]:
            updown_port_by_cli(duthost, "shutdown", port)
            for route in route_info["v6"]:
                res = duthost.shell("show ipv6 route {}".format(route))["stdout"]
                assert g_vars["default_ipv6_nexthop_info"][port] not in res, "the nexthop {} must not in route table".format(g_vars["default_ipv6_nexthop_info"][port])

        ptf_runner( ptfhost, \
                    "ptftests",
                    "ecmp_test.EcmpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port_list": g_vars["default_ptf_port_list"][-1:],
                        "router_mac": g_vars["router_mac"],
                        "dst_route_list": g_vars["dst_route_list_v6"]
                            },
                    log_file="/tmp/ecmp/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    def test_ipv6_load_balance_by_src_ip_when_all_nexthop_up(self, duthost, ptfhost):
        for port in g_vars["default_out_port_list"][:-1]:
            updown_port_by_cli(duthost, "startup", port)
            for route in route_info["v6"]:
                res = duthost.shell("show ipv6 route {}".format(route))["stdout"]
                assert g_vars["default_ipv6_nexthop_info"][port] in res, "the nexthop {} must in route table".format(g_vars["default_ipv6_nexthop_info"][port])

        ptf_runner( ptfhost, \
                    "ptftests",
                    "ecmp_test.EcmpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port_list": g_vars["default_ptf_port_list"],
                        "router_mac": g_vars["router_mac"],
                        "dst_route_list": g_vars["dst_route_list_v6"]
                            },
                    log_file="/tmp/ecmp/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

class TestCase2_LoadBalanceAfterNexthopContinousLinkFlap():
    @pytest.fixture(scope="class", autouse=True)
    def setup_continous_link_flap(self, duthost, ptfhost):
        for _ in xrange(MAX_TIMES):
            index = random.randint(0, len(g_vars["default_out_port_list"])-1)
            port = g_vars["default_out_port_list"][index]
            updown_port_by_cli(duthost, "shutdown", port)
            updown_port_by_cli(duthost, "startup", port)
        
        time.sleep(10)

        yield

        for port in g_vars["default_out_port_list"]:
            updown_port_by_cli(duthost, "startup", port, ignore_errors=True)

    def test_ipv4(self, ptfhost):
        ptf_runner( ptfhost, \
                    "ptftests",
                    "ecmp_test.EcmpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port_list": g_vars["default_ptf_port_list"],
                        "router_mac": g_vars["router_mac"],
                        "dst_route_list": g_vars["dst_route_list"]
                            },
                    log_file="/tmp/ecmp/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    def test_ipv6(self, ptfhost):
        ptf_runner( ptfhost, \
                    "ptftests",
                    "ecmp_test.EcmpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port_list": g_vars["default_ptf_port_list"],
                        "router_mac": g_vars["router_mac"],
                        "dst_route_list": g_vars["dst_route_list_v6"]
                            },
                    log_file="/tmp/ecmp/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

class TestCase3_EcmpHashKey():
    def test_ipv4_hash_key(self, duthost, ptfhost):
        for route in route_info["v4"]:
            res = duthost.shell("show ip route {}".format(route))["stdout"]
            assert route in res, "the route {} must in route table".format(route)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "ecmp_test.EcmpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port_list": g_vars["default_ptf_port_list"],
                        "router_mac": g_vars["router_mac"],
                        "hash_keys": test_hash_keys,
                        "dst_route_list": g_vars["dst_route_list"]
                            },
                    log_file="/tmp/ecmp/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    def test_ipv6_hash_key(self, duthost, ptfhost):
        for route in route_info["v6"]:
            res = duthost.shell("show ipv6 route {}".format(route))["stdout"]
            assert route in res, "the route {} must in route table".format(route)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "ecmp_test.EcmpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port_list": g_vars["default_ptf_port_list"],
                        "router_mac": g_vars["router_mac"],
                        "hash_keys": test_hash_keys,
                        "dst_route_list": g_vars["dst_route_list_v6"]
                            },
                    log_file="/tmp/ecmp/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))