import pytest
import time
import sys
import random

from ptf_runner import ptf_runner
from netaddr import IPNetwork


# global vars
mac1               = "00:01:00:00:00:01"
mac2               = "00:02:00:00:00:01"
broadcast_mac      = "ff:ff:ff:ff:ff:ff"

dut_ports    = []
ptf_ports    = []
vlan_id      = None
DEFAULT_VLAN = 1
lag_id       = 100

MEMBERS = 2           # num of lag members

dut_ip = IPNetwork("192.168.10.100/24")
ptf_ip = IPNetwork("{}/{}".format(dut_ip.ip+1, dut_ip.prefixlen))
dut_ipv6   = IPNetwork("2000:1::1/64")
ptf_ipv6   = IPNetwork("{}/{}".format(dut_ipv6.ip+1, dut_ipv6.prefixlen))


# fixtures
@pytest.fixture(scope="module")
def host_facts(duthost):
    return duthost.setup()['ansible_facts']

@pytest.fixture(scope="module")
def mg_facts(duthost, testbed):
    hostname = testbed['dut']
    return duthost.minigraph_facts(host=hostname)['ansible_facts']

@pytest.fixture(scope="module", autouse=True)
def setup_init(mg_facts, ptfhost):
    # vars
    global dut_ports, ptf_ports
    global vlan_id

    dut_ports = mg_facts['minigraph_vlans'].values()[0]['members']
    ptf_ports = [mg_facts['minigraph_port_indices'][port] for port in dut_ports]

    vlan_id   = mg_facts['minigraph_vlans'].values()[0]['vlanid']

    # copy ptftest script
    ptfhost.copy(src="ptftests", dest="/root")
    ptfhost.script("scripts/remove_ip.sh")
    ptfhost.script("scripts/change_mac.sh")

@pytest.fixture(scope="class")
def setup_vlan_peer_intf(duthost, ptfhost, mg_facts):
    duthost.shell("config interface ip add Vlan{} {}".format(vlan_id, dut_ip))
    duthost.shell("config interface ip add Vlan{} {}".format(vlan_id, dut_ipv6))

    # disable ptf dad detection
    dad = ptfhost.shell("sysctl net.ipv6.conf.eth{}.accept_dad".format(ptf_ports[0]))['stdout'].split(" = ")[-1]
    ptfhost.shell("sysctl net.ipv6.conf.eth{}.accept_dad=0".format(ptf_ports[0]))

    ptfhost.shell("ip address add {} dev eth{}".format(ptf_ip, ptf_ports[0]))
    ptfhost.shell("ip address add {} dev eth{}".format(ptf_ipv6, ptf_ports[0]))

    yield
    duthost.shell("config interface ip remove Vlan{} {}".format(vlan_id, dut_ip))
    duthost.shell("config interface ip remove Vlan{} {}".format(vlan_id, dut_ipv6))
    ptfhost.shell("ip address flush dev eth{}".format(ptf_ports[0]))

    # restore dad detection
    ptfhost.shell("sysctl net.ipv6.conf.eth{}.accept_dad={}".format(ptf_ports[0], dad))

@pytest.fixture(scope="class")
def setup_lag(duthost, ptfhost):
    # start teamd on PTF
    ptf_extra_vars = {  
        'lag_id'   : lag_id,
        'members'  : ptf_ports[:MEMBERS]
    }

    ptfhost.host.options['variable_manager'].extra_vars = ptf_extra_vars

    ptfhost.template(src="l2_vlan/l2_vlan_PortChannel.conf.j2", dest="/tmp/PortChannel{}.conf".format(lag_id))
    ptfhost.copy(src="l2_vlan/l2_vlan_teamd.sh", dest="/tmp/l2_vlan_teamd.sh", mode="0755")

    ptfhost.script("l2_vlan/l2_vlan_teamd.sh start {} \"{}\"".format(lag_id, ' '.join([str(port) for port in ptf_ports[:MEMBERS]])))

    # start teamd on DUT
    duthost.shell("config portchannel add PortChannel{}".format(lag_id))
    for port in dut_ports[:MEMBERS]:
        duthost.shell("config interface shutdown {}".format(port))
        duthost.shell("sonic-clear fdb all")
        duthost.shell("config vlan member del {} {}".format(vlan_id, port))
        duthost.shell("ip link set {} nomaster".format(port), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
        duthost.shell("config interface startup {}".format(port))

        duthost.shell("config portchannel member add PortChannel{} {}".format(lag_id, port))

    duthost.shell("config vlan member add {} PortChannel{} --untagged".format(vlan_id, lag_id))
    time.sleep(30)

    yield
    # stop teamd on PTF
    ptfhost.script("l2_vlan/l2_vlan_teamd.sh stop {} \"{}\"".format(lag_id, ' '.join([str(port) for port in ptf_ports[0:MEMBERS]])))

    # restore configuration on dut
    duthost.shell("config interface shutdown PortChannel{}".format(lag_id))
    duthost.shell("sonic-clear arp")
    duthost.shell("sonic-clear ndp")
    duthost.shell("sonic-clear fdb all")
    duthost.shell("config vlan member del {} PortChannel{}".format(vlan_id, lag_id))
    duthost.shell("ip link set PortChannel{} nomaster".format(lag_id), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
    duthost.shell("config interface startup PortChannel{}".format(lag_id))

    for port in dut_ports[:MEMBERS]:
        duthost.shell("config portchannel member del PortChannel{} {}".format(lag_id, port))
        duthost.shell("config vlan member add {} {} --untagged".format(vlan_id, port))
    duthost.shell("config portchannel del PortChannel{}".format(lag_id))

@pytest.fixture(scope="function", autouse=True)
def flush_fdb(duthost):
    duthost.shell("sonic-clear fdb all")
    yield
    duthost.shell("sonic-clear fdb all")

class TestCase1_VlanMember():
    num         = 10
    vlan_list   = [DEFAULT_VLAN]
    random_vlan = []
    @pytest.fixture(scope="class", autouse=True)
    def setup_vlan(self, duthost):
        for i in range(0, self.num-1):
            self.vlan_list.append(random.randint(2, 4094))
        
        # create vlans
        for vlan in self.vlan_list:
            duthost.shell("config vlan add {}".format(vlan))

        # add members to DEFAULT_VLAN
        for port in dut_ports[:2]:
            duthost.shell("config interface shutdown {}".format(port))
            duthost.shell("sonic-clear fdb all")
            duthost.shell("config vlan member del {} {}".format(vlan_id, port))
            duthost.shell("ip link set {} nomaster".format(port), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
            duthost.shell("config interface startup {}".format(port))

            duthost.shell("config vlan member add {} {} --untagged".format(DEFAULT_VLAN, port))

        # add members to random vlan
        self.random_vlan.append(random.choice(self.vlan_list))

        for port in dut_ports[-2:]:
            duthost.shell("config interface shutdown {}".format(port))
            duthost.shell("sonic-clear fdb all")
            duthost.shell("config vlan member del {} {}".format(vlan_id, port))
            duthost.shell("ip link set {} nomaster".format(port), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
            duthost.shell("config interface startup {}".format(port))

            duthost.shell("config vlan member add {} {} --untagged".format(self.random_vlan[0], port))

        yield
        # remove members from DEFAULT_VLAN
        for port in dut_ports[:2]:
            duthost.shell("config interface shutdown {}".format(port))
            duthost.shell("sonic-clear fdb all")
            duthost.shell("config vlan member del {} {}".format(DEFAULT_VLAN, port))
            duthost.shell("ip link set {} nomaster".format(port), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
            duthost.shell("config interface startup {}".format(port))

            duthost.shell("config vlan member add {} {} --untagged".format(vlan_id, port))

        # remove members from random vlan
        for port in dut_ports[-2:]:
            duthost.shell("config interface shutdown {}".format(port))
            duthost.shell("sonic-clear fdb all")
            duthost.shell("config vlan member del {} {}".format(self.random_vlan[0], port))
            duthost.shell("ip link set {} nomaster".format(port), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
            duthost.shell("config interface startup {}".format(port))

            duthost.shell("config vlan member add {} {} --untagged".format(vlan_id, port))

        # remove vlans
        for vlan in self.vlan_list:
            duthost.shell("config vlan del {}".format(vlan))

        # check vlan removed successfully
        for vlan in self.vlan_list:
            res = duthost.shell("redis-cli -n 0 exists VLAN_TABLE:Vlan{}".format(vlan))['stdout'].split(' ')[-1]
            assert res == '0', "Vlan{} should be removed successfully".format(vlan)


    @pytest.fixture(scope="function")
    def remove_member_from_default_vlan(self, duthost):
        port = dut_ports[1]
        duthost.shell("config interface shutdown {}".format(port))
        duthost.shell("sonic-clear fdb all")
        duthost.shell("config vlan member del {} {}".format(DEFAULT_VLAN, port))
        duthost.shell("ip link set {} nomaster".format(port), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
        duthost.shell("config interface startup {}".format(port))

        yield
        duthost.shell("config vlan member add {} {}".format(DEFAULT_VLAN, port))


    @pytest.fixture(scope="function")
    def remove_member_from_random_vlan(self, duthost):
        port = dut_ports[-1]
        duthost.shell("config interface shutdown {}".format(port))
        duthost.shell("sonic-clear fdb all")
        duthost.shell("config vlan member del {} {}".format(self.random_vlan[0], port))
        duthost.shell("ip link set {} nomaster".format(port), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
        duthost.shell("config interface startup {}".format(port))

        yield
        duthost.shell("config vlan member add {} {}".format(self.random_vlan[0], port))

    def test_vlan_create(self, duthost):
        for vlan in self.vlan_list:
            res = duthost.shell("redis-cli -n 0 exists VLAN_TABLE:Vlan{}".format(vlan))['stdout'].split(' ')[-1]
            assert res == '1', "Vlan{} should be created successfully".format(vlan)

    def test_default_vlan(self, duthost, ptfhost):
        for port in dut_ports[:2]:
            tagging_mode = duthost.shell("redis-cli -n 0 hget VLAN_MEMBER_TABLE:Vlan{}:{} tagging_mode".format(DEFAULT_VLAN, port))['stdout']
            assert tagging_mode == "untagged", "Port {} should joined Vlan{} with tagging_mode untagged".format(port, DEFAULT_VLAN)

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[0],
                        "dst_ports": [ptf_ports[1]]
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}]_flood_in_vlan{}.log".format(self.__class__.__name__, sys._getframe().f_code.co_name, DEFAULT_VLAN)
        )

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[0],
                        "dst_ports": ptf_ports[-2:],
                        "pkt_action": "drop"
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}]_not_flood_in_vlan{}.log".format(self.__class__.__name__, sys._getframe().f_code.co_name, self.random_vlan[0])
        )

    def test_random_vlan(self, duthost, ptfhost):
        for port in dut_ports[-2:]:
            tagging_mode = duthost.shell("redis-cli -n 0 hget VLAN_MEMBER_TABLE:Vlan{}:{} tagging_mode".format(self.random_vlan[0], port))['stdout']
            assert tagging_mode == "untagged", "Port {} should joined Vlan{} with tagging_mode untagged".format(port, self.random_vlan[0])

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[-2],
                        "dst_ports": [ptf_ports[-1]]
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}]_flood_in_vlan{}.log".format(self.__class__.__name__, sys._getframe().f_code.co_name, self.random_vlan[0])
        )

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[-2],
                        "dst_ports": ptf_ports[:2],
                        "pkt_action": "drop"
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}]_not_flood_in_vlan{}.log".format(self.__class__.__name__, sys._getframe().f_code.co_name, DEFAULT_VLAN)
        )

    @pytest.mark.usefixtures("remove_member_from_default_vlan")
    def test_remove_member_from_default_vlan(self, duthost, ptfhost):
        res = duthost.shell("redis-cli -n 0 exists VLAN_MEMBER_TABLE:Vlan{}:{}".format(DEFAULT_VLAN, dut_ports[1]))['stdout'].split(' ')[-1]
        assert res == '0', "Port {} should be removed from Vlan{}".format(dut_ports[1], DEFAULT_VLAN)

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[0],
                        "dst_ports": [ptf_ports[1]],
                        "pkt_action": "drop"
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    @pytest.mark.usefixtures("remove_member_from_random_vlan")
    def test_remove_member_from_random_vlan(self, duthost, ptfhost):
        res = duthost.shell("redis-cli -n 0 exists VLAN_MEMBER_TABLE:Vlan{}:{}".format(self.random_vlan[0], dut_ports[1]))['stdout'].split(' ')[-1]
        assert res == '0', "Port {} should be removed from Vlan{}".format(dut_ports[1], self.random_vlan[0])

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[-2],
                        "dst_ports": [ptf_ports[-1]],
                        "pkt_action": "drop"
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

class TestCase2_PortUntaggedMode():
    def test_untagged_pkt(self, duthost, ptfhost):
        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[0],
                        "dst_ports": ptf_ports[1:]
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )
        time.sleep(5)

        res = duthost.shell("show mac|grep {}".format(dut_ports[0]), module_ignore_errors=True)['stdout']
        assert mac1 in res, "Mac {} should be learned on port {}".format(mac1, dut_ports[0])

    def test_tagged_pkt_matched_with_pvid(self, duthost, ptfhost):
        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[0],
                        "dst_ports": ptf_ports[1:],
                        "vlan": vlan_id,
                        "strip_vlan": True
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )
        time.sleep(5)

        res = duthost.shell("show mac|grep {}".format(dut_ports[0]), module_ignore_errors=True)['stdout']
        assert mac1 in res, "Mac {} should be learned on untagged port {} with tag matched with pvid".format(mac1, dut_ports[0])

    def test_tagged_pkt_diff_from_pvid(self, duthost, ptfhost):
        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[0],
                        "dst_ports": ptf_ports[1:],
                        "vlan": int(vlan_id)+1,
                        "pkt_action": "drop"
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )
        time.sleep(5)

        res = duthost.shell("show mac|grep {}".format(dut_ports[0]), module_ignore_errors=True)['stdout']
        assert mac1 not in res, "Mac {} should not be learned on untagged port {} with tag not matched with pvid".format(mac1, dut_ports[0])

class TestCase3_PortTaggedMode():
    vlan_count   = 5
    vlan_base    = 2000
    vlan_invalid = vlan_base + vlan_count

    @pytest.fixture(scope="class", autouse=True)
    def setup_taggedport(self, duthost):
        duthost.shell("config vlan add {}".format(self.vlan_invalid))
        for i in range(0, self.vlan_count):
            vid = self.vlan_base + i
            duthost.shell("config vlan add {}".format(vid))
            duthost.shell("config vlan member add {} {}".format(vid, dut_ports[0]))
            duthost.shell("config vlan member add {} {}".format(vid, dut_ports[-1]))

        yield
        # teardown
        duthost.shell("config vlan del {}".format(self.vlan_invalid))
        duthost.shell("config interface shutdown {}".format(dut_ports[0]))
        duthost.shell("config interface shutdown {}".format(dut_ports[-1]))
        duthost.shell("sonic-clear fdb all")
        for i in range(0, self.vlan_count):
            vid = self.vlan_base + i
            duthost.shell("config vlan member del {} {}".format(vid, dut_ports[0]))
            duthost.shell("config vlan member del {} {}".format(vid, dut_ports[-1]))
            duthost.shell("config vlan del {}".format(vid))
        duthost.shell("config interface startup {}".format(dut_ports[0]))
        duthost.shell("config interface startup {}".format(dut_ports[-1]))

    def test_valid_tagged_pkt(self, duthost, ptfhost):
        vlans = []
        for i in range(0, self.vlan_count):
            vid = self.vlan_base + i
            vlans.append(str(vid))
            ptf_runner(
                        ptfhost, 
                        "ptftests", 
                        "l2_vlan.VlanTest",
                        platform_dir="ptftests",
                        params={
                            "src_mac": mac1,
                            "dst_mac": broadcast_mac,
                            "vlan": vid,
                            "src_port": ptf_ports[0],
                            "dst_ports": [ptf_ports[-1]]
                        },
                        log_file="/tmp/l2_vlan_[{}]_[{}]_Vlan{}.log".format(self.__class__.__name__, sys._getframe().f_code.co_name, vid)
            )
        time.sleep(5)

        mac_vlan_res = duthost.shell("show mac -p %s|grep %s|awk '{print $2}'" % (dut_ports[0], mac1), module_ignore_errors=True)['stdout_lines']
        assert mac_vlan_res==vlans, "{} learned on {} should be exists on vlans {}".format(mac1, dut_ports[0], vlans)

    def test_invalid_tagged_pkt(self, duthost, ptfhost):
        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "vlan": self.vlan_invalid,
                        "src_port": ptf_ports[0],
                        "dst_ports": ptf_ports[1:],
                        "pkt_action": "drop"
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

@pytest.mark.usefixtures("setup_lag")
class TestCase4_LagUntaggedMode():
    def test_untagged_pkt(self, duthost, ptfhost):
        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[0],
                        "dst_ports": ptf_ports[MEMBERS:]
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}]_receive.log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )
        time.sleep(5)

        res = duthost.shell("show mac|grep PortChannel{}".format(lag_id), module_ignore_errors=True)['stdout']
        assert mac1 in res, "Mac {} should be learned on PortChannel{}".format(mac1, lag_id)

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac2,
                        "dst_mac": mac1,
                        "src_port": ptf_ports[-1],
                        "dst_ports": ptf_ports[:MEMBERS]
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}]_send.log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    def test_tagged_pkt_matched_with_pvid(self, duthost, ptfhost):
        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[0],
                        "dst_ports": ptf_ports[MEMBERS:],
                        "vlan": vlan_id,
                        "strip_vlan": True
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}]_receive.log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )
        time.sleep(5)

        res = duthost.shell("show mac|grep PortChannel{}".format(lag_id), module_ignore_errors=True)['stdout']
        assert mac1 in res, "Mac {} should be learned on untagged PortChannel{} with tag matched with pvid".format(mac1, lag_id)

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac2,
                        "dst_mac": mac1,
                        "src_port": ptf_ports[-1],
                        "dst_ports": ptf_ports[:MEMBERS],
                        "vlan": vlan_id,
                        "strip_vlan": True
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}]_send.log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    def test_tagged_pkt_diff_from_pvid(self, duthost, ptfhost):
        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[0],
                        "dst_ports": ptf_ports[MEMBERS:],
                        "vlan": int(vlan_id)+1,
                        "pkt_action": "drop"
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )
        time.sleep(5)

        res = duthost.shell("show mac|grep PortChannel{}".format(lag_id), module_ignore_errors=True)['stdout']
        assert mac1 not in res, "Mac {} should not be learned on untagged PortChannel{} with tag not matched with pvid".format(mac1, lag_id)


class TestCase5_LagtaggedMode():
    vlan_count = 5
    vlan_base  = 2000
    vlan_invalid = vlan_base + vlan_count

    @pytest.fixture(scope="class")
    def setup_taggedlag(self, duthost):
        duthost.shell("config vlan add {}".format(self.vlan_invalid))
        for i in range(0, self.vlan_count):
            vid = self.vlan_base + i
            duthost.shell("config vlan add {}".format(vid))
            duthost.shell("config vlan member add {} PortChannel{}".format(vid, lag_id))
            duthost.shell("config vlan member add {} {}".format(vid, dut_ports[-1]))

        yield
        duthost.shell("config vlan del {}".format(self.vlan_invalid))
        duthost.shell("config interface shutdown PortChannel{}".format(lag_id))
        duthost.shell("config interface shutdown {}".format(dut_ports[-1]))
        duthost.shell("sonic-clear fdb all")
        for i in range(0, self.vlan_count):
            vid = self.vlan_base + i
            duthost.shell("config vlan member del {} PortChannel{}".format(vid, lag_id))
            duthost.shell("config vlan member del {} {}".format(vid, dut_ports[-1]))
            duthost.shell("config vlan del {}".format(vid))
        duthost.shell("config interface startup PortChannel{}".format(lag_id))
        duthost.shell("config interface startup {}".format(dut_ports[-1]))

    @pytest.mark.usefixtures("flush_fdb")
    @pytest.mark.usefixtures("setup_taggedlag")
    @pytest.mark.usefixtures("setup_lag")
    def test_valid_tagged_pkt(self, duthost, ptfhost):
        vlans = []
        for i in range(0, self.vlan_count):
            vid = self.vlan_base + i
            vlans.append(str(vid))
            ptf_runner(
                        ptfhost, 
                        "ptftests", 
                        "l2_vlan.VlanTest",
                        platform_dir="ptftests",
                        params={
                            "src_mac": mac1,
                            "dst_mac": broadcast_mac,
                            "vlan": vid,
                            "src_port": ptf_ports[0],
                            "dst_ports": [ptf_ports[-1]]
                        },
                        log_file="/tmp/l2_vlan_[{}]_[{}]_Vlan{}_receive.log".format(self.__class__.__name__, sys._getframe().f_code.co_name, vid)
            )
        time.sleep(5)

        mac_vlan_res = duthost.shell("show mac -p PortChannel%s|grep %s|awk '{print $2}'" % (lag_id, mac1), module_ignore_errors=True)['stdout_lines']
        assert mac_vlan_res==vlans, "{} learned on PortChannel{} should be exists on vlans {}".format(mac1, lag_id, vlans)

        for i in range(0, self.vlan_count):
            vid = self.vlan_base + i
            ptf_runner(
                        ptfhost, 
                        "ptftests", 
                        "l2_vlan.VlanTest",
                        platform_dir="ptftests",
                        params={
                            "src_mac": mac2,
                            "dst_mac": mac1,
                            "vlan": vid,
                            "src_port": ptf_ports[-1],
                            "dst_ports": ptf_ports[:MEMBERS]
                        },
                        log_file="/tmp/l2_vlan_[{}]_[{}]_Vlan{}_send.log".format(self.__class__.__name__, sys._getframe().f_code.co_name, vid)
            )

    @pytest.mark.usefixtures("flush_fdb")
    @pytest.mark.usefixtures("setup_taggedlag")
    @pytest.mark.usefixtures("setup_lag")
    def test_invalid_tagged_pkt(self, duthost, ptfhost):
        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "vlan": self.vlan_invalid,
                        "src_port": ptf_ports[0],
                        "dst_ports": [ptf_ports[-1]],
                        "pkt_action": "drop"
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    
@pytest.mark.usefixtures("setup_vlan_peer_intf")
class TestCase6_VlanIntf():
    def test_ipv4_vlan_intf(self, duthost, ptfhost, host_facts, mg_facts):
        ptfhost.shell("ping {} -c 3 -f -W 2 -I {}".format(dut_ip.ip, ptf_ip.ip))

        # upstream test, pkt come from vlan intf and outgoing from PortChannel0001
        pc_member = mg_facts['minigraph_portchannels']['PortChannel0001']['members'][0]
        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": host_facts['ansible_Ethernet0']['macaddress'],
                        "dst_ip": mg_facts['minigraph_portchannel_interfaces'][0]['peer_addr'],
                        "src_port": ptf_ports[0],
                        "dst_ports": [mg_facts['minigraph_port_indices'][pc_member]]
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}]_receive.log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

        # downstream test, pkt come from PortChannel0001 and outgong from vlan intf
        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac2,
                        "dst_mac": host_facts['ansible_Ethernet0']['macaddress'],
                        "dst_ip": str(ptf_ip.ip),
                        "src_port": mg_facts['minigraph_port_indices'][pc_member],
                        "dst_ports": [ptf_ports[0]]
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}]_send.log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    def test_ipv6_vlan_intf(self, duthost, ptfhost, host_facts, mg_facts):
        ptfhost.shell("ping6 {} -c 3 -f -W 2 -I {}".format(dut_ipv6.ip, ptf_ipv6.ip))

        # upstream test, pkt come from vlan intf and outgoing from PortChannel0001
        pc_member = mg_facts['minigraph_portchannels']['PortChannel0001']['members'][0]
        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": host_facts['ansible_Ethernet0']['macaddress'],
                        "dst_ip": mg_facts['minigraph_portchannel_interfaces'][1]['peer_addr'],
                        "src_port": ptf_ports[0],
                        "dst_ports": [mg_facts['minigraph_port_indices'][pc_member]]
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}]_receive.log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

        # downstream test, pkt come from PortChannel0001 and outgong from vlan intf
        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_vlan.VlanTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac2,
                        "dst_mac": host_facts['ansible_Ethernet0']['macaddress'],
                        "dst_ip": str(ptf_ipv6.ip),
                        "src_port": mg_facts['minigraph_port_indices'][pc_member],
                        "dst_ports": [ptf_ports[0]]
                    },
                    log_file="/tmp/l2_vlan_[{}]_[{}]_send.log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )
