import pytest
import time
import sys
import random
import logging
from ptf_runner import ptf_runner
from netaddr import IPNetwork


# vars
SLEEP_TIME = 2

g_vars          = {}
test_ip         = ["2020::1/64", "2030::1/64"]
test_route      = "2040::/64"
host_mac        = "00:00:00:22:22:22"

# fixtures
@pytest.fixture(scope="module")
def host_facts(duthost):
    return duthost.setup()["ansible_facts"]

@pytest.fixture(scope="module")
def mg_facts(duthost, testbed):
    hostname = testbed["dut"]
    return duthost.minigraph_facts(host=hostname)["ansible_facts"]

@pytest.fixture(scope="module", autouse=True)
def setup_init(mg_facts, duthost, ptfhost):
    # vars
    global g_vars

    g_vars["router_mac"] = duthost.shell("redis-cli -n 4 hget 'DEVICE_METADATA|localhost' mac")["stdout"]
    port_test_intf_name = mg_facts["minigraph_vlans"].values()[0]["members"][0]
    vlan_test_intf_name = mg_facts["minigraph_vlan_interfaces"][0]["attachto"]
    lag_test_intf_name = mg_facts["minigraph_portchannel_interfaces"][0]["attachto"]
    g_vars["vlan_id"] = mg_facts["minigraph_vlans"][vlan_test_intf_name]["vlanid"]
    # Choose the last lag interface for ip forwarding src_port.
    g_vars["src_port"] = mg_facts["minigraph_port_indices"][mg_facts["minigraph_portchannels"][mg_facts["minigraph_portchannel_interfaces"][-1]["attachto"]]["members"][0]]
    # Choose the first vlan member for port interface test, last vlan member for vlan interface test, first lag interface member for lag test.
    g_vars["test_port"] = {"name": port_test_intf_name, 
                            "member_port_name": port_test_intf_name, 
                            "ptf_peer": mg_facts["minigraph_port_indices"][port_test_intf_name]
                            }
    g_vars["test_vlan"] = {"name": vlan_test_intf_name, 
                            "member_port_name": mg_facts["minigraph_vlans"][vlan_test_intf_name]["members"][-1], 
                            "ptf_peer": mg_facts["minigraph_port_indices"][mg_facts["minigraph_vlans"][vlan_test_intf_name]["members"][-1]]
                            }
    g_vars["test_lag"] = {"name": lag_test_intf_name, 
                            "member_port_name": mg_facts["minigraph_portchannels"][lag_test_intf_name]["members"][0], 
                            "ptf_peer": mg_facts["minigraph_port_indices"][mg_facts["minigraph_portchannels"][lag_test_intf_name]["members"][0]]
                            }
    g_vars["vlan_member_port_2"] = mg_facts["minigraph_vlans"][vlan_test_intf_name]["members"][-2]
    g_vars["vlan_member_port_2_peer"] = mg_facts["minigraph_port_indices"][g_vars["vlan_member_port_2"]]

    # init dut
    duthost.shell("config vlan member del {} {}".format(g_vars["vlan_id"], g_vars["test_port"]["name"]))
    duthost.shell("ip link set dev {} nomaster".format(g_vars["test_port"]["name"]), module_ignore_errors=True) # workaround for remove port from vlan. Until rm34965 is fixed.
    duthost.shell("mkdir -p /tmp/neighbor_v6")

    # copy ptftest script
    ptfhost.copy(src="ptftests", dest="/root")
    ptfhost.shell("mkdir -p /tmp/neighbor_v6")
    ptfhost.script("scripts/remove_ip.sh")
    ptfhost.script("scripts/change_mac.sh")

    yield

    # recover dut configuration
    duthost.shell("config vlan member add {} {} -u".format(g_vars["vlan_id"], g_vars["test_port"]["name"]))

def clear_arp_table(duthost, ptfhost, dut_if):
    duthost.shell("ip link set arp off dev {0} && ip link set arp on dev {0}".format(dut_if))
    ptfhost.shell("ip -s -s neigh flush all")
    time.sleep(SLEEP_TIME)

def setup_sonic_ip(duthost, op, ip, port_name, ignore_errors=False):
    # op should be add or remove
    if ignore_errors:
        duthost.shell("config interface ip {} {} {}".format(op, port_name, ip), module_ignore_errors=True)
    else:
        duthost.shell("config interface ip {} {} {}".format(op, port_name, ip))
    time.sleep(SLEEP_TIME)

def setup_static_route(duthost, ip, nexthop, op="add", distance=None, ignore_errors=False):
    route_cmd = "no " if op == "del" else ""
    route_cmd += "ip route {} {}".format(ip, nexthop) if IPNetwork(ip).version == 4 else "ipv6 route {} {}".format(ip, nexthop)
    route_cmd += " {}".format(distance) if distance else ""

    duthost.shell("vtysh -c 'configure terminal' -c '{}'".format(route_cmd), module_ignore_errors=ignore_errors)
    time.sleep(SLEEP_TIME)

def setup_ptf_ip(ptfhost, op, port_if, ip=None, ignore_errors=False):
    # op should be add ,del or flush; if op is flush, ip is not necessary
    if ignore_errors:
        if op == "flush":
            ptfhost.shell("ip addr {} dev eth{}".format(op, port_if), module_ignore_errors=True)
        else:
            ptfhost.shell("ip addr {} {} dev eth{}".format(op, ip, port_if), module_ignore_errors=True)
    else:
        if op == "flush":
            ptfhost.shell("ip addr {} dev eth{}".format(op, port_if))
        else:
            ptfhost.shell("ip addr {} {} dev eth{}".format(op, ip, port_if))
    time.sleep(SLEEP_TIME)

def ptf_ping_ip(ptfhost, ip, port_index=None, expect=True):
    if IPNetwork(ip).version == 4:
        ping_cmd = "ping"
    else:
        ping_cmd = "ping6"
    if expect:
        if port_index:
            ptfhost.shell("{} {} -c 3 -f -W 2 -I eth{}".format(ping_cmd, ip, port_index))
        else:
            ptfhost.shell("{} {} -c 3 -f -W 2".format(ping_cmd, ip))
    else:
        if port_index:
            ptfhost.shell("! {} {} -c 3 -f -W 2 -I eth{}".format(ping_cmd, ip, port_index))
        else:
            ptfhost.shell("! {} {} -c 3 -f -W 2".format(ping_cmd, ip))

@pytest.fixture(scope="function")
def setup_intf(request, duthost, ptfhost):
    intf_mode = request.param["intf_mode"]
    setup_ptf = request.param.get("setup_ptf", True)
    global g_vars
    g_vars["dut_if"] = g_vars["test_{}".format(intf_mode)]["name"]
    g_vars["dut_if_member_port"] = g_vars["test_{}".format(intf_mode)]["member_port_name"]
    g_vars["ptf_peer"] = g_vars["test_{}".format(intf_mode)]["ptf_peer"]
    g_vars["host_ip_addr_list"] = map(lambda ip: "{}/{}".format(IPNetwork(ip).ip+1, IPNetwork(ip).prefixlen), test_ip)

    clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
    setup_ptf_ip(ptfhost, "flush", g_vars["ptf_peer"])

    for ip in test_ip:
        setup_sonic_ip(duthost, "add", ip, g_vars["dut_if"])

    if setup_ptf:
        for ip in g_vars["host_ip_addr_list"]:
            setup_ptf_ip(ptfhost, "add", g_vars["ptf_peer"], ip)

    yield

    for ip in test_ip:
        setup_sonic_ip(duthost, "remove", ip, g_vars["dut_if"], ignore_errors=True)

    if setup_ptf:
        for ip in g_vars["host_ip_addr_list"]:
            setup_ptf_ip(ptfhost, "del", g_vars["ptf_peer"], ip, ignore_errors=True)

    clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
    setup_ptf_ip(ptfhost, "flush", g_vars["ptf_peer"])

@pytest.fixture(scope="function")
def setup_route(request, duthost, ptfhost):
    intf_mode = request.param["intf_mode"]
    global g_vars
    g_vars["dut_if"] = g_vars["test_{}".format(intf_mode)]["name"]
    g_vars["dut_if_member_port"] = g_vars["test_{}".format(intf_mode)]["member_port_name"]
    g_vars["ptf_peer"] = g_vars["test_{}".format(intf_mode)]["ptf_peer"]
    g_vars["route_nexthop"] = IPNetwork(test_ip[0]).ip+1
    g_vars["host_ip_addr_list"] = map(lambda ip: "{}/{}".format(IPNetwork(ip).ip+1, IPNetwork(ip).prefixlen), test_ip)
    g_vars["route_ip_addr_list"] = [str(IPNetwork(test_route).ip+1)]
    g_vars["ptf_origin_mac"] = ptfhost.shell("ip link show eth%s |  grep -o 'link/ether [^ ]*' | awk '{print $2}'" % g_vars["ptf_peer"])["stdout"]

    clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
    setup_ptf_ip(ptfhost, "flush", g_vars["ptf_peer"])

    for ip in test_ip:
        setup_sonic_ip(duthost, "add", ip, g_vars["dut_if"])

    for ip in g_vars["host_ip_addr_list"]:
        setup_ptf_ip(ptfhost, "add", g_vars["ptf_peer"], ip)

    setup_static_route(duthost, test_route, g_vars["route_nexthop"])

    yield

    setup_static_route(duthost, test_route, g_vars["route_nexthop"], op="del", ignore_errors=True)

    for ip in test_ip:
        setup_sonic_ip(duthost, "remove", ip, g_vars["dut_if"], ignore_errors=True)

    for ip in g_vars["host_ip_addr_list"]:
        setup_ptf_ip(ptfhost, "del", g_vars["ptf_peer"], ip, ignore_errors=True)

    clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
    setup_ptf_ip(ptfhost, "flush", g_vars["ptf_peer"])

class TestCase1_DynamicV6Neighbor():
    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "port"}], indirect=True)
    def test_dynamic_arp_on_port_intf(self, ptfhost, duthost, setup_intf):
        for ip in test_ip:
            ptf_ip = IPNetwork(ip).ip+1
            ptf_ping_ip(ptfhost, IPNetwork(ip).ip)
            res = duthost.shell("show ndp {}".format(ptf_ip))["stdout"]
            assert str(ptf_ip) in res, "Dynamic ndp {} must in ndp table".format(ptf_ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"])
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[expect_true].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

        duthost.shell("sonic-clear ndp")
        ptfhost.shell("ip addr flush dev eth{}".format(g_vars["ptf_peer"]))

        for ip in test_ip:
            ptf_ip = IPNetwork(ip).ip+1
            ptf_ping_ip(ptfhost, IPNetwork(ip).ip, expect=False)
            res = duthost.shell("show ndp {}".format(ptf_ip))["stdout"]
            assert str(ptf_ip) not in res, "Dynamic ndp {} must not in ndp table".format(ptf_ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"]),
                        "unexpected_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"])
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[expect_false].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "vlan"}], indirect=True)
    def test_dynamic_arp_on_vlan_intf(self, ptfhost, duthost, setup_intf):
        for ip in test_ip:
            ptf_ip = IPNetwork(ip).ip+1
            ptf_ping_ip(ptfhost, IPNetwork(ip).ip)
            res = duthost.shell("show ndp {}".format(ptf_ip))["stdout"]
            assert str(ptf_ip) in res, "Dynamic ndp {} must in ndp table".format(ptf_ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"])
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[expect_true].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

        duthost.shell("sonic-clear ndp")
        ptfhost.shell("ip addr flush dev eth{}".format(g_vars["ptf_peer"]))

        for ip in test_ip:
            ptf_ip = IPNetwork(ip).ip+1
            ptf_ping_ip(ptfhost, IPNetwork(ip).ip, expect=False)
            res = duthost.shell("show ndp {}".format(ptf_ip))["stdout"]
            assert str(ptf_ip) not in res, "Dynamic ndp {} must not in ndp table".format(ptf_ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"]),
                        "unexpected_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"])
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[expect_false].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "lag"}], indirect=True)
    def test_dynamic_arp_on_lag_intf(self, ptfhost, duthost, setup_intf):
        for ip in test_ip:
            ptf_ip = IPNetwork(ip).ip+1
            ptf_ping_ip(ptfhost, IPNetwork(ip).ip)
            res = duthost.shell("show ndp {}".format(ptf_ip))["stdout"]
            assert str(ptf_ip) in res, "Dynamic ndp {} must in ndp table".format(ptf_ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"])
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[expect_true].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

        duthost.shell("sonic-clear ndp")
        ptfhost.shell("ip addr flush dev eth{}".format(g_vars["ptf_peer"]))

        for ip in test_ip:
            ptf_ip = IPNetwork(ip).ip+1
            ptf_ping_ip(ptfhost, IPNetwork(ip).ip, expect=False)
            res = duthost.shell("show ndp {}".format(ptf_ip))["stdout"]
            assert str(ptf_ip) not in res, "Dynamic ndp {} must not in ndp table".format(ptf_ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"]),
                        "unexpected_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"])
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[expect_false].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

class TestCase2_StaticV6Neighbor():
    @pytest.fixture(scope="function", autouse=True)
    def del_static_arp(self, duthost):
        yield

        for ip in test_ip:
            duthost.shell("bridge fdb del {} dev {} vlan {} master".format(host_mac, g_vars["dut_if_member_port"], g_vars["vlan_id"]), module_ignore_errors=True)
            duthost.shell("ip -s -s neigh del {} dev {}".format(IPNetwork(ip).ip+1, g_vars["dut_if"]), module_ignore_errors=True)

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "port", "setup_ptf": False}], indirect=True)
    def test_static_arp_on_port_intf(self, ptfhost, duthost, setup_intf):
        for ip in test_ip:
            ptf_ip = IPNetwork(ip).ip+1
            # Add static ndp
            duthost.shell("ip neigh replace {} lladdr {} dev {}".format(ptf_ip, host_mac, g_vars["dut_if"]))
            time.sleep(SLEEP_TIME)

            res = duthost.shell("show ndp {}".format(ptf_ip))["stdout"]
            assert str(ptf_ip) in res, "Static ndp {} must in ndp table".format(ptf_ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "expected_dst_mac": host_mac,
                        "dst_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"])
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[expect_true].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

        for ip in test_ip:
            ptf_ip = IPNetwork(ip).ip+1
            # Del static ndp
            duthost.shell("ip -s -s neigh del {} dev {}".format(ptf_ip, g_vars["dut_if"]))
            time.sleep(SLEEP_TIME)

            res = duthost.shell("show ndp {}".format(ptf_ip))["stdout"]
            assert str(ptf_ip) not in res, "Static ndp {} must not in ndp table".format(ptf_ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "expected_dst_mac": host_mac,
                        "dst_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"]),
                        "unexpected_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"])
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[expect_false].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "vlan", "setup_ptf": False}], indirect=True)
    def test_static_arp_on_vlan_intf(self, ptfhost, duthost, setup_intf):
        for ip in test_ip:
            ptf_ip = IPNetwork(ip).ip+1
            # Add static ndp
            duthost.shell("ip neigh replace {} lladdr {} dev {}".format(ptf_ip, host_mac, g_vars["dut_if"]))
            duthost.shell("bridge fdb replace {} dev {} vlan {} master static".format(host_mac, g_vars["dut_if_member_port"], g_vars["vlan_id"]))
            time.sleep(SLEEP_TIME)

            res = duthost.shell("show ndp {}".format(ptf_ip))["stdout"]
            assert str(ptf_ip) in res, "Static ndp {} must in ndp table".format(ptf_ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "expected_dst_mac": host_mac,
                        "dst_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"])
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[expect_true].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

        for ip in test_ip:
            ptf_ip = IPNetwork(ip).ip+1
            # Del static ndp
            duthost.shell("bridge fdb del {} dev {} vlan {} master".format(host_mac, g_vars["dut_if_member_port"], g_vars["vlan_id"]), module_ignore_errors=True)
            duthost.shell("ip -s -s neigh del {} dev {}".format(ptf_ip, g_vars["dut_if"]))
            time.sleep(SLEEP_TIME)

            res = duthost.shell("show ndp {}".format(ptf_ip))["stdout"]
            assert str(ptf_ip) not in res, "Static ndp {} must not in ndp table".format(ptf_ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "expected_dst_mac": host_mac,
                        "dst_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"]),
                        "unexpected_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"])
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[expect_false].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "lag", "setup_ptf": False}], indirect=True)
    def test_static_arp_on_lag_intf(self, ptfhost, duthost, setup_intf):
        for ip in test_ip:
            ptf_ip = IPNetwork(ip).ip+1
            # Add static ndp
            duthost.shell("ip neigh replace {} lladdr {} dev {}".format(ptf_ip, host_mac, g_vars["dut_if"]))
            time.sleep(SLEEP_TIME)

            res = duthost.shell("show ndp {}".format(ptf_ip))["stdout"]
            assert str(ptf_ip) in res, "Static ndp {} must in ndp table".format(ptf_ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "expected_dst_mac": host_mac,
                        "dst_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"])
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[expect_true].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

        for ip in test_ip:
            ptf_ip = IPNetwork(ip).ip+1
            # Del static ndp
            duthost.shell("ip -s -s neigh del {} dev {}".format(ptf_ip, g_vars["dut_if"]))
            time.sleep(SLEEP_TIME)

            res = duthost.shell("show ndp {}".format(ptf_ip))["stdout"]
            assert str(ptf_ip) not in res, "Static ndp {} must not in ndp table".format(ptf_ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "expected_dst_mac": host_mac,
                        "dst_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"]),
                        "unexpected_ip_addr_list": map(lambda ip: str(IPNetwork(ip).ip), g_vars["host_ip_addr_list"])
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[expect_false].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

class TestCase3_AddAndFlushV6NeighborEntryWhichIsRouteNexthop():
    @pytest.mark.parametrize("setup_route", [{"intf_mode": "port"}], indirect=True)
    def test_on_port_intf(self, duthost, ptfhost, setup_route):
        ptf_ping_ip(ptfhost, IPNetwork(test_ip[0]).ip)

        res = duthost.shell("show ipv6 route {}".format(test_route))["stdout"]
        assert test_route in res, "the route {} must in route table".format(test_route)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["route_ip_addr_list"]
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[nexthop_add].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

        duthost.shell("sonic-clear ndp")
        time.sleep(SLEEP_TIME)

        res = duthost.shell("show ndp {}".format(g_vars["route_nexthop"]))["stdout"]
        assert str(g_vars["route_nexthop"]) not in res, "the ndp {} must not in ndp table".format(g_vars["route_nexthop"])

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["route_ip_addr_list"]
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[nexthop_flush].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_route", [{"intf_mode": "vlan"}], indirect=True)
    def test_on_vlan_intf(self, duthost, ptfhost, setup_route):
        ptf_ping_ip(ptfhost, IPNetwork(test_ip[0]).ip)

        res = duthost.shell("show ipv6 route {}".format(test_route))["stdout"]
        assert test_route in res, "the route {} must in route table".format(test_route)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["route_ip_addr_list"]
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[nexthop_add].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

        duthost.shell("sonic-clear ndp")
        time.sleep(SLEEP_TIME)

        res = duthost.shell("show ndp {}".format(g_vars["route_nexthop"]))["stdout"]
        assert str(g_vars["route_nexthop"]) not in res, "the ndp {} must not in ndp table".format(g_vars["route_nexthop"])

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["route_ip_addr_list"]
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[nexthop_flush].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_route", [{"intf_mode": "lag"}], indirect=True)
    def test_on_lag_intf(self, duthost, ptfhost, setup_route):
        ptf_ping_ip(ptfhost, IPNetwork(test_ip[0]).ip)

        res = duthost.shell("show ipv6 route {}".format(test_route))["stdout"]
        assert test_route in res, "the route {} must in route table".format(test_route)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["route_ip_addr_list"]
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[nexthop_add].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

        duthost.shell("sonic-clear ndp")
        time.sleep(SLEEP_TIME)

        res = duthost.shell("show ndp {}".format(g_vars["route_nexthop"]))["stdout"]
        assert str(g_vars["route_nexthop"]) not in res, "the ndp {} must not in ndp table".format(g_vars["route_nexthop"])

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["route_ip_addr_list"]
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[nexthop_flush].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

class TestCase4_MacPortChangeOfV6Neighbor():
    @pytest.fixture(scope="function", autouse=True)
    def recover_mac(self, ptfhost):
        yield

        ptfhost.shell("ip link set dev eth{} address {}".format(g_vars["ptf_peer"], g_vars["ptf_origin_mac"]), module_ignore_errors=True)
        setup_ptf_ip(ptfhost, "flush", g_vars["vlan_member_port_2_peer"], ignore_errors=True)

    @pytest.mark.parametrize("setup_route", [{"intf_mode": "port"}], indirect=True)
    def test_on_port_intf(self, duthost, ptfhost, setup_route):
        ptf_ping_ip(ptfhost, IPNetwork(test_ip[0]).ip)

        res = duthost.shell("show ipv6 route {}".format(test_route))["stdout"]
        assert test_route in res, "the route {} must in route table".format(test_route)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["route_ip_addr_list"]
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[nexthop_add].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

        clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
        ptfhost.shell("ip link set dev eth{} address {}".format(g_vars["ptf_peer"], host_mac))
        time.sleep(SLEEP_TIME)
        ptf_ping_ip(ptfhost, IPNetwork(test_ip[0]).ip)

        res = duthost.shell("show ndp {}".format(g_vars["route_nexthop"]))["stdout"]
        assert str(g_vars["route_nexthop"]) in res and host_mac in res, "the ndp {} mac {} must in ndp table".format(g_vars["route_nexthop"], host_mac)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "expected_dst_mac": host_mac,
                        "dst_ip_addr_list": g_vars["route_ip_addr_list"]
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[nexthop_mac_change].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_route", [{"intf_mode": "vlan"}], indirect=True)
    def test_on_vlan_intf(self, duthost, ptfhost, setup_route):
        ptf_ping_ip(ptfhost, IPNetwork(test_ip[0]).ip)

        res = duthost.shell("show ipv6 route {}".format(test_route))["stdout"]
        assert test_route in res, "the route {} must in route table".format(test_route)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["route_ip_addr_list"]
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[nexthop_add].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

        clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
        ptfhost.shell("ip link set dev eth{} address {}".format(g_vars["ptf_peer"], host_mac))
        time.sleep(SLEEP_TIME)
        ptf_ping_ip(ptfhost, IPNetwork(test_ip[0]).ip)
        time.sleep(SLEEP_TIME)

        res = duthost.shell("show ndp {}".format(g_vars["route_nexthop"]))["stdout"]
        assert str(g_vars["route_nexthop"]) in res and host_mac in res, "the ndp {} mac {} must in ndp table".format(g_vars["route_nexthop"], host_mac)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "expected_dst_mac": host_mac,
                        "dst_ip_addr_list": g_vars["route_ip_addr_list"]
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[nexthop_mac_change].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

        clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
        setup_ptf_ip(ptfhost, "flush", g_vars["ptf_peer"])

        for ip in g_vars["host_ip_addr_list"]:
            setup_ptf_ip(ptfhost, "add", g_vars["vlan_member_port_2_peer"], ip)
        time.sleep(SLEEP_TIME)
        ptf_ping_ip(ptfhost, IPNetwork(test_ip[0]).ip)
        time.sleep(SLEEP_TIME)

        res = duthost.shell("show ndp {}".format(g_vars["route_nexthop"]))["stdout"]
        assert str(g_vars["route_nexthop"]) in res and g_vars["vlan_member_port_2"] in res, "the ndp {} port {} must in ndp table".format(g_vars["route_nexthop"], g_vars["vlan_member_port_2"])

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["vlan_member_port_2_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["route_ip_addr_list"]
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[nexthop_port_change].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_route", [{"intf_mode": "lag"}], indirect=True)
    def test_on_lag_intf(self, duthost, ptfhost, setup_route):
        ptf_ping_ip(ptfhost, IPNetwork(test_ip[0]).ip)

        res = duthost.shell("show ipv6 route {}".format(test_route))["stdout"]
        assert test_route in res, "the route {} must in route table".format(test_route)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["route_ip_addr_list"]
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[nexthop_add].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

        clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
        g_vars["ptf_origin_mac"] = ptfhost.shell("ip link show eth%s |  grep -o 'link/ether [^ ]*' | awk '{print $2}'" % g_vars["ptf_peer"])["stdout"]
        ptfhost.shell("ip link set dev eth{} address {}".format(g_vars["ptf_peer"], host_mac))
        time.sleep(SLEEP_TIME)
        ptf_ping_ip(ptfhost, IPNetwork(test_ip[0]).ip)

        res = duthost.shell("show ndp {}".format(g_vars["route_nexthop"]))["stdout"]
        assert str(g_vars["route_nexthop"]) in res and host_mac in res, "the ndp {} mac {} must in ndp table".format(g_vars["route_nexthop"], host_mac)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "arp_test.ArpTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "expected_dst_mac": host_mac,
                        "dst_ip_addr_list": g_vars["route_ip_addr_list"]
                            },
                    log_file="/tmp/neighbor_v6/[{}]_[{}]_[nexthop_mac_change].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))
