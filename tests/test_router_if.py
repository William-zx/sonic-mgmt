import pytest
import time
import sys
import random
import logging
from ptf_runner import ptf_runner
from netaddr import IPNetwork


# vars
g_vars          = {}
ip_addr_info    = {"v4": ["192.168.96.1/30", "172.17.17.1/16", "172.16.1.1/16", "30.1.1.1/8"],
                    "v4-overlap": ["30.1.1.1/24", "30.1.1.1/8", "30.1.1.20/8"],
                    "v6-global": ["2011::1/64", "2012::1/96", "2013::1/126"],
                    "v6-unique": ["fc11::1/64", "fc12::1/96", "fc13::1/126"],
                    "v6-link": ["fe80::abcd:efff:fe00:1/64"],
                    "v6-overlap": ["2011::1/64", "2011::1/96", "2011::20/96"]
                    }

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

    # init dut
    duthost.shell("config vlan member del {} {}".format(g_vars["vlan_id"], g_vars["test_port"]["name"]))
    duthost.shell("ip link set dev {} nomaster".format(g_vars["test_port"]["name"]), module_ignore_errors=True) # workaround for remove port from vlan. Until rm34965 is fixed.
    duthost.shell("mkdir -p /tmp/router_if")

    # copy ptftest script
    ptfhost.copy(src="ptftests", dest="/root")
    ptfhost.shell("mkdir -p /tmp/router_if")
    ptfhost.script("scripts/remove_ip.sh")
    ptfhost.script("scripts/change_mac.sh")

    yield

    # recover dut configuration
    duthost.shell("config vlan member add {} {} -u".format(g_vars["vlan_id"], g_vars["test_port"]["name"]))

def clear_arp_table(duthost, ptfhost, dut_if):
    duthost.shell("ip link set arp off dev {0} && ip link set arp on dev {0}".format(dut_if))
    ptfhost.shell("ip -s -s neigh flush all")
    time.sleep(2)

def setup_ip_address_by_json(duthost, dut_if, ip_addrs, op_cmd="add"):
    dut_extra_vars = {  
        "op_cmd"   : op_cmd,
        "dut_if"   : dut_if,
        "ip_addrs"  : ip_addrs
    }

    duthost.host.options["variable_manager"].extra_vars = dut_extra_vars
    duthost.template(src="router_if/ip_addr.j2", dest="/tmp/router_if/{}_{}_ip_addr.json".format(dut_if, op_cmd))
    duthost.shell("config load -y /tmp/router_if/{}_{}_ip_addr.json".format(dut_if, op_cmd))
    time.sleep(2)

def setup_sonic_ip(duthost, op, ip, port_name, ignore_errors=False):
    # op should be add or remove
    if ignore_errors:
        duthost.shell("config interface ip {} {} {}".format(op, port_name, ip), module_ignore_errors=True)
    else:
        duthost.shell("config interface ip {} {} {}".format(op, port_name, ip))
    time.sleep(2)

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
    time.sleep(2)

def ptf_ping_ip(ptfhost, ip, port_index=None, expect=True):
    if IPNetwork(ip).version == 4:
        ping_cmd = "ping"
    else:
        ping_cmd = "ping6"
    if expect:
        if port_index:
            ptfhost.shell("{} {} -c 3 -f -W 2 -I eth{}".format(ping_cmd, IPNetwork(ip).ip, port_index))
        else:
            ptfhost.shell("{} {} -c 3 -f -W 2".format(ping_cmd, IPNetwork(ip).ip))
    else:
        if port_index:
            ptfhost.shell("! {} {} -c 3 -f -W 2 -I eth{}".format(ping_cmd, IPNetwork(ip).ip, port_index))
        else:
            ptfhost.shell("! {} {} -c 3 -f -W 2".format(ping_cmd, IPNetwork(ip).ip))

@pytest.fixture(scope="function")
def setup_intf(request, duthost, ptfhost):
    intf_mode = request.param["intf_mode"]
    ip_mode = request.param["ip_mode"]
    config_mode = request.param.get("config_mode", "CLI")
    global g_vars
    g_vars["dut_if"] = g_vars["test_{}".format(intf_mode)]["name"]
    g_vars["dut_if_member_port"] = g_vars["test_{}".format(intf_mode)]["member_port_name"]
    g_vars["ptf_peer"] = g_vars["test_{}".format(intf_mode)]["ptf_peer"]
    g_vars["ip_addr_list"] = ip_addr_info[ip_mode]
    g_vars["host_ip_addr_list"] = map(lambda ip: "{}/{}".format(IPNetwork(ip).ip+1, IPNetwork(ip).prefixlen), g_vars["ip_addr_list"])

    clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
    setup_ptf_ip(ptfhost, "flush", g_vars["ptf_peer"])

    if config_mode == "CLI":
        for ip in g_vars["ip_addr_list"]:
            setup_sonic_ip(duthost, "add", ip, g_vars["dut_if"])
    else:
        setup_ip_address_by_json(duthost, g_vars["dut_if"], g_vars["ip_addr_list"])

    for ip in g_vars["host_ip_addr_list"]:
        setup_ptf_ip(ptfhost, "add", g_vars["ptf_peer"], ip)

    yield

    for ip in g_vars["ip_addr_list"]:
        setup_sonic_ip(duthost, "remove", ip, g_vars["dut_if"], ignore_errors=True)

    for ip in g_vars["host_ip_addr_list"]:
        setup_ptf_ip(ptfhost, "del", g_vars["ptf_peer"], ip, ignore_errors=True)

    clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
    setup_ptf_ip(ptfhost, "flush", g_vars["ptf_peer"])

@pytest.fixture(scope="function")
def setup_overlap_intf(request, duthost, ptfhost):
    intf_mode = request.param["intf_mode"]
    ip_mode = request.param["ip_mode"]
    global g_vars
    g_vars["dut_if"] = g_vars["test_{}".format(intf_mode)]["name"]
    g_vars["dut_if_member_port"] = g_vars["test_{}".format(intf_mode)]["member_port_name"]
    g_vars["ptf_peer"] = g_vars["test_{}".format(intf_mode)]["ptf_peer"]
    g_vars["ip_addr_list"] = ip_addr_info[ip_mode]
    g_vars["host_ip_addr_list"] = map(lambda ip: "{}/{}".format(IPNetwork(ip).ip+1, IPNetwork(ip).prefixlen), g_vars["ip_addr_list"])

    clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
    setup_ptf_ip(ptfhost, "flush", g_vars["ptf_peer"])

    # Enable promote secondaries on dut_if for supporting ip overlap
    default_value = duthost.shell("sysctl net.ipv4.conf.{}.promote_secondaries".format(g_vars["dut_if"]))["stdout"].split("=").strip()[1]
    duthost.shell("sysctl -w net.ipv4.conf.{}.promote_secondaries=1".format(g_vars["dut_if"]), module_ignore_errors=True)

    yield

    duthost.shell("sysctl -w net.ipv4.conf.{}.promote_secondaries={}".format(g_vars["dut_if"], default_value), module_ignore_errors=True)

    clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
    setup_ptf_ip(ptfhost, "flush", g_vars["ptf_peer"])
  
class TestCase1_AddIpAddressWithDiffMask():
    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "port", "ip_mode": "v4", "config_mode": "json"}], indirect=True)
    def test_ping_and_traffic_forward_on_port_intf(self, ptfhost, setup_intf):
        for ip in g_vars["ip_addr_list"]:
            ptf_ping_ip(ptfhost, ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "router_if_test.RifTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["host_ip_addr_list"]
                            },
                    log_file="/tmp/router_if/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "vlan", "ip_mode": "v4", "config_mode": "json"}], indirect=True)
    def test_ping_and_traffic_forward_on_vlan_intf(self, ptfhost, setup_intf):
        for ip in g_vars["ip_addr_list"]:
            ptf_ping_ip(ptfhost, ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "router_if_test.RifTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["host_ip_addr_list"]
                            },
                    log_file="/tmp/router_if/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "lag", "ip_mode": "v4", "config_mode": "json"}], indirect=True)
    def test_ping_and_traffic_forward_on_lag_intf(self, ptfhost, setup_intf):
        for ip in g_vars["ip_addr_list"]:
            ptf_ping_ip(ptfhost, ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "router_if_test.RifTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["host_ip_addr_list"]
                            },
                    log_file="/tmp/router_if/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

class TestCase2_RemoveIpAddress():
    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "port", "ip_mode": "v4"}], indirect=True)
    def test_ping_and_traffic_forward_on_port_intf(self, duthost, ptfhost, setup_intf):
        unexpected_ip_addr_list = []
        for ip in g_vars["ip_addr_list"]:
            unexpected_ip_addr_list.append("{}".format(IPNetwork(ip).ip+1))
            setup_sonic_ip(duthost, "remove", ip, g_vars["dut_if"])
            clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
            ptf_ping_ip(ptfhost, ip, expect=False)

            ptf_runner( ptfhost, \
                        "ptftests",
                        "router_if_test.RifTest",
                        platform_dir="ptftests",
                        params={
                            "src_port": g_vars["src_port"],
                            "dst_port": g_vars["ptf_peer"],
                            "router_mac": g_vars["router_mac"],
                            "dst_ip_addr_list": g_vars["host_ip_addr_list"],
                            "unexpected_ip_addr_list": unexpected_ip_addr_list
                                },
                        log_file="/tmp/router_if/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "vlan", "ip_mode": "v4"}], indirect=True)
    def test_ping_and_traffic_forward_on_vlan_intf(self, duthost, ptfhost, setup_intf):
        unexpected_ip_addr_list = []
        for ip in g_vars["ip_addr_list"]:
            unexpected_ip_addr_list.append("{}".format(IPNetwork(ip).ip+1))
            setup_sonic_ip(duthost, "remove", ip, g_vars["dut_if"])
            clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
            ptf_ping_ip(ptfhost, ip, expect=False)

            ptf_runner( ptfhost, \
                        "ptftests",
                        "router_if_test.RifTest",
                        platform_dir="ptftests",
                        params={
                            "src_port": g_vars["src_port"],
                            "dst_port": g_vars["ptf_peer"],
                            "router_mac": g_vars["router_mac"],
                            "dst_ip_addr_list": g_vars["host_ip_addr_list"],
                            "unexpected_ip_addr_list": unexpected_ip_addr_list
                                },
                        log_file="/tmp/router_if/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "lag", "ip_mode": "v4"}], indirect=True)
    def test_ping_and_traffic_forward_on_lag_intf(self, duthost, ptfhost, setup_intf):
        unexpected_ip_addr_list = []
        for ip in g_vars["ip_addr_list"]:
            unexpected_ip_addr_list.append("{}".format(IPNetwork(ip).ip+1))
            setup_sonic_ip(duthost, "remove", ip, g_vars["dut_if"])
            clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
            ptf_ping_ip(ptfhost, ip, expect=False)

            ptf_runner( ptfhost, \
                        "ptftests",
                        "router_if_test.RifTest",
                        platform_dir="ptftests",
                        params={
                            "src_port": g_vars["src_port"],
                            "dst_port": g_vars["ptf_peer"],
                            "router_mac": g_vars["router_mac"],
                            "dst_ip_addr_list": g_vars["host_ip_addr_list"],
                            "unexpected_ip_addr_list": unexpected_ip_addr_list
                                },
                        log_file="/tmp/router_if/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

# The behaviors of overlap configuration between SONiC and linux kernel are not consistent.
# Hence, we do not test the overlap feature until a new requirement publish in the furture.
@pytest.mark.skip(reason="Not supported on SONiC")
class TestCase3_OverlapIpAddress():
    @pytest.mark.parametrize("setup_overlap_intf", [{"intf_mode": "port", "ip_mode": "v4-overlap"}], indirect=True)
    def test_overlap_ip_on_port_intf(self, duthost, ptfhost, setup_overlap_intf):
        for i in xrange(len(g_vars["ip_addr_list"])):
            if i != 0:
                setup_sonic_ip(duthost, "remove", g_vars["ip_addr_list"][i-1], g_vars["dut_if"])
            setup_sonic_ip(duthost, "add", g_vars["ip_addr_list"][i], g_vars["dut_if"])
            setup_ptf_ip(ptfhost, "flush", g_vars["ptf_peer"])
            setup_ptf_ip(ptfhost, "add", g_vars["ptf_peer"], g_vars["host_ip_addr_list"][i])
            ptf_ping_ip(ptfhost, g_vars["ip_addr_list"][i])

            if i != 0:
                res = duthost.shell("show ip interfaces")["stdout"]
                assert g_vars["ip_addr_list"][i-1] not in res, "the old ip {} should be removed".format(g_vars["ip_addr_list"][i-1])

    @pytest.mark.parametrize("setup_overlap_intf", [{"intf_mode": "vlan", "ip_mode": "v4-overlap"}], indirect=True)
    def test_overlap_ip_on_vlan_intf(self, duthost, ptfhost, setup_overlap_intf):
        for i in xrange(len(g_vars["ip_addr_list"])):
            if i != 0:
                setup_sonic_ip(duthost, "remove", g_vars["ip_addr_list"][i-1], g_vars["dut_if"])
            setup_sonic_ip(duthost, "add", g_vars["ip_addr_list"][i], g_vars["dut_if"])
            setup_ptf_ip(ptfhost, "flush", g_vars["ptf_peer"])
            setup_ptf_ip(ptfhost, "add", g_vars["ptf_peer"], g_vars["host_ip_addr_list"][i])
            ptf_ping_ip(ptfhost, g_vars["ip_addr_list"][i])

            if i != 0:
                res = duthost.shell("show ip interfaces")["stdout"]
                assert g_vars["ip_addr_list"][i-1] not in res, "the old ip {} should be removed".format(g_vars["ip_addr_list"][i-1])

    @pytest.mark.parametrize("setup_overlap_intf", [{"intf_mode": "lag", "ip_mode": "v4-overlap"}], indirect=True)
    def test_overlap_ip_on_lag_intf(self, duthost, ptfhost, setup_overlap_intf):
        for i in xrange(len(g_vars["ip_addr_list"])):
            if i != 0:
                setup_sonic_ip(duthost, "remove", g_vars["ip_addr_list"][i-1], g_vars["dut_if"])
            setup_sonic_ip(duthost, "add", g_vars["ip_addr_list"][i], g_vars["dut_if"])
            setup_ptf_ip(ptfhost, "flush", g_vars["ptf_peer"])
            setup_ptf_ip(ptfhost, "add", g_vars["ptf_peer"], g_vars["host_ip_addr_list"][i])
            ptf_ping_ip(ptfhost, g_vars["ip_addr_list"][i])

            if i != 0:
                res = duthost.shell("show ip interfaces")["stdout"]
                assert g_vars["ip_addr_list"][i-1] not in res, "the old ip {} should be removed".format(g_vars["ip_addr_list"][i-1])

class TestCase4_AddIpv6AddressWithDiffMask():
    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "port", "ip_mode": "v6-global", "config_mode": "json"}], indirect=True)
    def test_ping_and_traffic_forward_on_port_intf(self, ptfhost, setup_intf):
        for ip in g_vars["ip_addr_list"]:
            ptf_ping_ip(ptfhost, ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "router_if_test.RifTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["host_ip_addr_list"]
                            },
                    log_file="/tmp/router_if/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "vlan", "ip_mode": "v6-global", "config_mode": "json"}], indirect=True)
    def test_ping_and_traffic_forward_on_vlan_intf(self, ptfhost, setup_intf):
        for ip in g_vars["ip_addr_list"]:
            ptf_ping_ip(ptfhost, ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "router_if_test.RifTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["host_ip_addr_list"]
                            },
                    log_file="/tmp/router_if/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "lag", "ip_mode": "v6-global", "config_mode": "json"}], indirect=True)
    def test_ping_and_traffic_forward_on_lag_intf(self, ptfhost, setup_intf):
        for ip in g_vars["ip_addr_list"]:
            ptf_ping_ip(ptfhost, ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "router_if_test.RifTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["host_ip_addr_list"]
                            },
                    log_file="/tmp/router_if/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

class TestCase5_AddIpv6LinkLocalAndUniqueLocal():
    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "port", "ip_mode": "v6-link", "config_mode": "json"}], indirect=True)
    def test_v6_link_local_on_port_intf(self, ptfhost, setup_intf):
        for ip in g_vars["ip_addr_list"]:
            ptf_ping_ip(ptfhost, ip, port_index=g_vars["ptf_peer"])

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "vlan", "ip_mode": "v6-link", "config_mode": "json"}], indirect=True)
    def test_v6_link_local_on_vlan_intf(self, ptfhost, setup_intf):
        for ip in g_vars["ip_addr_list"]:
            ptf_ping_ip(ptfhost, ip, port_index=g_vars["ptf_peer"])

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "lag", "ip_mode": "v6-link", "config_mode": "json"}], indirect=True)
    def test_v6_link_local_on_lag_intf(self, ptfhost, setup_intf):
        for ip in g_vars["ip_addr_list"]:
            ptf_ping_ip(ptfhost, ip, port_index=g_vars["ptf_peer"])

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "port", "ip_mode": "v6-unique", "config_mode": "json"}], indirect=True)
    def test_v6_unique_on_port_intf(self, ptfhost, setup_intf):
        for ip in g_vars["ip_addr_list"]:
            ptf_ping_ip(ptfhost, ip)

        ptf_runner( ptfhost, \
                    "ptftests",
                    "router_if_test.RifTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["host_ip_addr_list"]
                            },
                    log_file="/tmp/router_if/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "vlan", "ip_mode": "v6-unique", "config_mode": "json"}], indirect=True)
    def test_v6_unique_on_vlan_intf(self, ptfhost, setup_intf):
        for ip in g_vars["ip_addr_list"]:
            ptf_ping_ip(ptfhost, ip)
            
        ptf_runner( ptfhost, \
                    "ptftests",
                    "router_if_test.RifTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["host_ip_addr_list"]
                            },
                    log_file="/tmp/router_if/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "lag", "ip_mode": "v6-unique", "config_mode": "json"}], indirect=True)
    def test_v6_unique_on_lag_intf(self, ptfhost, setup_intf):
        for ip in g_vars["ip_addr_list"]:
            ptf_ping_ip(ptfhost, ip)
            
        ptf_runner( ptfhost, \
                    "ptftests",
                    "router_if_test.RifTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": g_vars["src_port"],
                        "dst_port": g_vars["ptf_peer"],
                        "router_mac": g_vars["router_mac"],
                        "dst_ip_addr_list": g_vars["host_ip_addr_list"]
                            },
                    log_file="/tmp/router_if/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

class TestCase6_RemoveIpv6Address():
    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "port", "ip_mode": "v6-global"}], indirect=True)
    def test_ping_and_traffic_forward_on_port_intf(self, duthost, ptfhost, setup_intf):
        unexpected_ip_addr_list = []
        for ip in g_vars["ip_addr_list"]:
            unexpected_ip_addr_list.append("{}".format(IPNetwork(ip).ip+1))
            setup_sonic_ip(duthost, "remove", ip, g_vars["dut_if"])
            clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
            ptf_ping_ip(ptfhost, ip, expect=False)

            ptf_runner( ptfhost, \
                        "ptftests",
                        "router_if_test.RifTest",
                        platform_dir="ptftests",
                        params={
                            "src_port": g_vars["src_port"],
                            "dst_port": g_vars["ptf_peer"],
                            "router_mac": g_vars["router_mac"],
                            "dst_ip_addr_list": g_vars["host_ip_addr_list"],
                            "unexpected_ip_addr_list": unexpected_ip_addr_list
                                },
                        log_file="/tmp/router_if/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "vlan", "ip_mode": "v6-global"}], indirect=True)
    def test_ping_and_traffic_forward_on_vlan_intf(self, duthost, ptfhost, setup_intf):
        unexpected_ip_addr_list = []
        for ip in g_vars["ip_addr_list"]:
            unexpected_ip_addr_list.append("{}".format(IPNetwork(ip).ip+1))
            setup_sonic_ip(duthost, "remove", ip, g_vars["dut_if"])
            clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
            ptf_ping_ip(ptfhost, ip, expect=False)

            ptf_runner( ptfhost, \
                        "ptftests",
                        "router_if_test.RifTest",
                        platform_dir="ptftests",
                        params={
                            "src_port": g_vars["src_port"],
                            "dst_port": g_vars["ptf_peer"],
                            "router_mac": g_vars["router_mac"],
                            "dst_ip_addr_list": g_vars["host_ip_addr_list"],
                            "unexpected_ip_addr_list": unexpected_ip_addr_list
                                },
                        log_file="/tmp/router_if/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    @pytest.mark.parametrize("setup_intf", [{"intf_mode": "lag", "ip_mode": "v6-global"}], indirect=True)
    def test_ping_and_traffic_forward_on_lag_intf(self, duthost, ptfhost, setup_intf):
        unexpected_ip_addr_list = []
        for ip in g_vars["ip_addr_list"]:
            unexpected_ip_addr_list.append("{}".format(IPNetwork(ip).ip+1))
            setup_sonic_ip(duthost, "remove", ip, g_vars["dut_if"])
            clear_arp_table(duthost, ptfhost, g_vars["dut_if"])
            ptf_ping_ip(ptfhost, ip, expect=False)

            ptf_runner( ptfhost, \
                        "ptftests",
                        "router_if_test.RifTest",
                        platform_dir="ptftests",
                        params={
                            "src_port": g_vars["src_port"],
                            "dst_port": g_vars["ptf_peer"],
                            "router_mac": g_vars["router_mac"],
                            "dst_ip_addr_list": g_vars["host_ip_addr_list"],
                            "unexpected_ip_addr_list": unexpected_ip_addr_list
                                },
                        log_file="/tmp/router_if/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

# The behaviors of overlap configuration between SONiC and linux kernel are not consistent.
# Hence, we do not test the overlap feature until a new requirement publish in the furture.
@pytest.mark.skip(reason="Not supported on SONiC")
class TestCase7_OverlapIpv6Address():
    @pytest.mark.parametrize("setup_overlap_intf", [{"intf_mode": "port", "ip_mode": "v6-overlap"}], indirect=True)
    def test_overlap_ip_on_port_intf(self, duthost, ptfhost, setup_overlap_intf):
        for i in xrange(len(g_vars["ip_addr_list"])):
            if i != 0:
                setup_sonic_ip(duthost, "remove", g_vars["ip_addr_list"][i-1], g_vars["dut_if"])
            setup_sonic_ip(duthost, "add", g_vars["ip_addr_list"][i], g_vars["dut_if"])
            setup_ptf_ip(ptfhost, "flush", g_vars["ptf_peer"])
            setup_ptf_ip(ptfhost, "add", g_vars["ptf_peer"], g_vars["host_ip_addr_list"][i])
            ptf_ping_ip(ptfhost, g_vars["ip_addr_list"][i])

            if i != 0:
                res = duthost.shell("show ip interfaces")["stdout"]
                assert g_vars["ip_addr_list"][i-1] not in res, "the old ip {} should be removed".format(g_vars["ip_addr_list"][i-1])

    @pytest.mark.parametrize("setup_overlap_intf", [{"intf_mode": "vlan", "ip_mode": "v6-overlap"}], indirect=True)
    def test_overlap_ip_on_vlan_intf(self, duthost, ptfhost, setup_overlap_intf):
        for i in xrange(len(g_vars["ip_addr_list"])):
            if i != 0:
                setup_sonic_ip(duthost, "remove", g_vars["ip_addr_list"][i-1], g_vars["dut_if"])
            setup_sonic_ip(duthost, "add", g_vars["ip_addr_list"][i], g_vars["dut_if"])
            setup_ptf_ip(ptfhost, "flush", g_vars["ptf_peer"])
            setup_ptf_ip(ptfhost, "add", g_vars["ptf_peer"], g_vars["host_ip_addr_list"][i])
            ptf_ping_ip(ptfhost, g_vars["ip_addr_list"][i])

            if i != 0:
                res = duthost.shell("show ip interfaces")["stdout"]
                assert g_vars["ip_addr_list"][i-1] not in res, "the old ip {} should be removed".format(g_vars["ip_addr_list"][i-1])

    @pytest.mark.parametrize("setup_overlap_intf", [{"intf_mode": "lag", "ip_mode": "v6-overlap"}], indirect=True)
    def test_overlap_ip_on_lag_intf(self, duthost, ptfhost, setup_overlap_intf):
        for i in xrange(len(g_vars["ip_addr_list"])):
            if i != 0:
                setup_sonic_ip(duthost, "remove", g_vars["ip_addr_list"][i-1], g_vars["dut_if"])
            setup_sonic_ip(duthost, "add", g_vars["ip_addr_list"][i], g_vars["dut_if"])
            setup_ptf_ip(ptfhost, "flush", g_vars["ptf_peer"])
            setup_ptf_ip(ptfhost, "add", g_vars["ptf_peer"], g_vars["host_ip_addr_list"][i])
            ptf_ping_ip(ptfhost, g_vars["ip_addr_list"][i])

            if i != 0:
                res = duthost.shell("show ip interfaces")["stdout"]
                assert g_vars["ip_addr_list"][i-1] not in res, "the old ip {} should be removed".format(g_vars["ip_addr_list"][i-1])
