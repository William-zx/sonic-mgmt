import pytest
import time
import sys

from ptf_runner import ptf_runner
from netaddr import IPNetwork




# vars
mac1               = "00:01:00:00:00:01"
mac2               = "00:02:00:00:00:01"
broadcast_mac      = "ff:ff:ff:ff:ff:ff"
multicast_mac      = "01:00:5e:00:00:01"
unkown_unicast_mac = "00:01:02:03:04:05"

dut_ports   = []
ptf_ports   = []
port1       = None
port2       = None
port3       = None
vlan_id     = None
lag_id      = 100

MEMBER_INIT = 2           # init num of lag members
MEMBER_MAX  = 8           # max num of lag members
LAG_MAX     = 16          # max num of lags


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
def setup_lag(duthost, ptfhost, mg_facts):
    # vars
    global dut_ports, ptf_ports
    global port1, port2, port3, vlan_id

    dut_ports = mg_facts['minigraph_vlans'].values()[0]['members']
    for port in dut_ports:
        ptf_ports.append(mg_facts['minigraph_port_indices'][port])

    port1     = dut_ports[0]
    port2     = dut_ports[1]
    port3     = dut_ports[-1]
    vlan_id   = mg_facts['minigraph_vlans'].values()[0]['vlanid']

    # copy ptftest script
    ptfhost.copy(src="ptftests", dest="/root")
    ptfhost.script("scripts/remove_ip.sh")
    ptfhost.script("scripts/change_mac.sh")

    # start teamd on PTF
    ptf_extra_vars = {  
        'lag_id'   : lag_id,
        'members'  : ptf_ports[:MEMBER_MAX]
    }

    ptfhost.host.options['variable_manager'].extra_vars = ptf_extra_vars

    ptfhost.template(src="l2_lag/l2_lag_PortChannel.conf.j2", dest="/tmp/PortChannel{}.conf".format(lag_id))
    ptfhost.copy(src="l2_lag/l2_lag_teamd.sh", dest="/tmp/l2_lag_teamd.sh", mode="0755")

    ptfhost.script("l2_lag/l2_lag_teamd.sh start {} \"{}\"".format(lag_id, ' '.join([str(port) for port in ptf_ports[:MEMBER_MAX]])))

    # start teamd on DUT
    duthost.shell("config portchannel add PortChannel{}".format(lag_id))
    for port in dut_ports[:MEMBER_INIT]:
        duthost.shell("config interface shutdown {}".format(port))
        duthost.shell("sonic-clear fdb all")
        duthost.shell("config vlan member del {} {}".format(vlan_id, port))
        duthost.shell("ip link set {} nomaster".format(port), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
        duthost.shell("config interface startup {}".format(port))

        duthost.shell("config portchannel member add PortChannel{} {}".format(lag_id, port))

    duthost.shell("config vlan member add {} PortChannel{} --untagged".format(vlan_id, lag_id))

    # shutdown backup lag members
    for port in dut_ports[MEMBER_INIT:MEMBER_MAX]:
        duthost.shell("config interface shutdown {}".format(port))

    yield
    # stop teamd on PTF
    ptfhost.script("l2_lag/l2_lag_teamd.sh stop {} \"{}\"".format(lag_id, ' '.join([str(port) for port in ptf_ports[0:MEMBER_MAX]])))

    # restore configuration on dut
    duthost.shell("config interface shutdown PortChannel{}".format(lag_id))
    duthost.shell("sonic-clear arp")
    duthost.shell("sonic-clear ndp")
    duthost.shell("sonic-clear fdb all")
    duthost.shell("config vlan member del {} PortChannel{}".format(vlan_id, lag_id))
    duthost.shell("ip link set PortChannel{} nomaster".format(lag_id), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
    duthost.shell("config interface startup PortChannel{}".format(lag_id))

    for port in dut_ports[:MEMBER_INIT]:
        duthost.shell("config portchannel member del PortChannel{} {}".format(lag_id, port))
        duthost.shell("config vlan member add {} {} --untagged".format(vlan_id, port))
    duthost.shell("config portchannel del PortChannel{}".format(lag_id))

    # restore port to admin up
    for port in dut_ports[MEMBER_INIT:MEMBER_MAX]:
        duthost.shell("config interface startup {}".format(port))

@pytest.fixture(scope="class")
def setup_lag_rif(duthost, ptfhost):
    duthost.shell("config interface shutdown PortChannel{}".format(lag_id))
    duthost.shell("sonic-clear fdb all")
    duthost.shell("config vlan member del {} PortChannel{}".format(vlan_id, lag_id))
    duthost.shell("ip link set PortChannel{} nomaster".format(lag_id), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
    duthost.shell("config interface startup PortChannel{}".format(lag_id))

    duthost.shell("config interface ip add PortChannel{} {}".format(lag_id, dut_ip))
    duthost.shell("config interface ip add PortChannel{} {}".format(lag_id, dut_ipv6))
    time.sleep(30)

    # disable dad detection on lag
    dad = ptfhost.shell("sysctl net.ipv6.conf.PortChannel{}.accept_dad".format(lag_id))['stdout'].split(" = ")[-1]
    ptfhost.shell("sysctl net.ipv6.conf.PortChannel{}.accept_dad=0".format(lag_id))

    ptfhost.shell("ip address add {} dev PortChannel{}".format(ptf_ip, lag_id))
    ptfhost.shell("ip address add {} dev PortChannel{}".format(ptf_ipv6, lag_id))


    
    yield
    # teardown
    duthost.shell("sonic-clear arp")
    duthost.shell("sonic-clear ndp")
    duthost.shell("config interface ip remove PortChannel{} {}".format(lag_id, dut_ip))
    duthost.shell("config interface ip remove PortChannel{} {}".format(lag_id, dut_ipv6))
    duthost.shell("config vlan member add {} PortChannel{} --untagged".format(vlan_id, lag_id))

    ptfhost.shell("ip address flush dev PortChannel{}".format(lag_id))
 
    # restore dad detection on lag
    ptfhost.shell("sysctl net.ipv6.conf.PortChannel{}.accept_dad={}".format(lag_id, dad))

@pytest.fixture(scope="class", autouse=True)
def check_lag_status(duthost):
    '''
    verify lag status is up before every test case
    check inteval 5s
    check times 12
    '''
    res = False
    for i in range(0, 12):
        status = duthost.shell("redis-cli -n 0 hget LAG_TABLE:PortChannel{} oper_status".format(lag_id))['stdout']
        if status == 'up':
            res = True
            break
        else:
            time.sleep(5)
    assert res, "PortChannel{} status shoule be up".format(lag_id)

@pytest.fixture(scope="class", autouse=True)
def clear_fwd_info(duthost):
    # clear before every case run
    duthost.shell("sonic-clear arp")
    duthost.shell("sonic-clear ndp")
    duthost.shell("sonic-clear fdb all")

    yield
    # clear after every case finished
    duthost.shell("sonic-clear arp")
    duthost.shell("sonic-clear ndp")
    duthost.shell("sonic-clear fdb all")


class TestCase1_MaxLagMembers():
    @pytest.fixture(scope="function")
    def setup_max_members(self, duthost, ptfhost):
        for port in dut_ports[MEMBER_INIT:MEMBER_MAX]:
            duthost.shell("config interface shutdown {}".format(port))
            duthost.shell("sonic-clear fdb all")
            duthost.shell("config vlan member del {} {}".format(vlan_id, port))
            duthost.shell("ip link set {} nomaster".format(port), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
            duthost.shell("config interface startup {}".format(port))

            duthost.shell("config portchannel member add PortChannel{} {}".format(lag_id, port))

        yield
        # teardown
        for port in dut_ports[MEMBER_INIT:MEMBER_MAX]:
            duthost.shell("config interface shutdown {}".format(port))
            duthost.shell("config portchannel member del PortChannel{} {}".format(lag_id, port))
            duthost.shell("config vlan member add {} {} --untagged".format(vlan_id, port))

    def test_two_members(self, duthost, ptfhost):
        '''
        During init configuration, 2 members is added to lags
        '''
        # verify members status to be selected
        for p in dut_ports[:MEMBER_INIT]:
            res = False
            for i in range(0, 12):
                status = duthost.shell("teamdctl PortChannel{} state item get ports.{}.runner.selected".format(lag_id, p))['stdout']
                if status == 'true':
                    res = True
                    break
                else:
                    time.sleep(5)
            assert res, "Port {} status should be selected".format(p)

        # verify members by traffic
        ptf_runner( 
                    ptfhost,
                    "ptftests",
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[0],
                        "dst_ports": ptf_ports[MEMBER_INIT:]
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}]_send_learn_frame.log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac2,
                        "dst_mac": mac1,
                        "src_port": ptf_ports[-1],
                        "dst_ports": ptf_ports[:MEMBER_INIT]
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    @pytest.mark.usefixtures("setup_max_members")
    def test_max_member(self, duthost, ptfhost):
        # verify members status to be selected
        for p in dut_ports[:MEMBER_MAX]:
            res = False
            for i in range(0, 12):
                status = duthost.shell("teamdctl PortChannel{} state item get ports.{}.runner.selected".format(lag_id, p))['stdout']
                if status == 'true':
                    res = True
                    break
                else:
                    time.sleep(5)
            assert res, "Port {} status should be selected".format(p)

        ptf_runner( 
                    ptfhost,
                    "ptftests",
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[0],
                        "dst_ports": ptf_ports[MEMBER_MAX:]
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}]_send_learn_frame.log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac2,
                        "dst_mac": mac1,
                        "src_port": ptf_ports[-1],
                        "dst_ports": ptf_ports[:MEMBER_MAX]
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

class TestCase2_MultiLags():
    lag_ids = range(lag_id+1, lag_id+LAG_MAX+1)

    @pytest.fixture(scope='class', autouse=True)
    def setup_multilags(self, duthost, ptfhost):
        # stop init teamd on PTF
        ptfhost.script("l2_lag/l2_lag_teamd.sh stop {} \"{}\"".format(lag_id, ' '.join([str(port) for port in ptf_ports[0:MEMBER_MAX]])))

        # stop init teamd on DUT
        duthost.shell("config interface shutdown PortChannel{}".format(lag_id))
        duthost.shell("sonic-clear fdb all")
        duthost.shell("config vlan member del {} PortChannel{}".format(vlan_id, lag_id))
        duthost.shell("ip link set PortChannel{} nomaster".format(lag_id), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
        duthost.shell("config interface startup PortChannel{}".format(lag_id))

        for port in dut_ports[:MEMBER_INIT]:
            duthost.shell("config portchannel member del PortChannel{} {}".format(lag_id, port))
            duthost.shell("config vlan member add {} {} --untagged".format(vlan_id, port))
        duthost.shell("config portchannel del PortChannel{}".format(lag_id))

        for lag in self.lag_ids:
            # start teamd on PTF
            ptf_extra_vars = {  
                'lag_id'   : lag,
                'members'  : [ptf_ports[self.lag_ids.index(lag)]]
            }
            ptfhost.host.options['variable_manager'].extra_vars = ptf_extra_vars
            ptfhost.template(src="l2_lag/l2_lag_PortChannel.conf.j2", dest="/tmp/PortChannel{}.conf".format(lag))
            ptfhost.script("l2_lag/l2_lag_teamd.sh start {} \"{}\"".format(lag, ptf_ports[self.lag_ids.index(lag)]))

            # start teamd on DUT
            duthost.shell("config portchannel add PortChannel{}".format(lag))
            port = dut_ports[self.lag_ids.index(lag)]
            duthost.shell("config interface shutdown {}".format(port))
            duthost.shell("sonic-clear fdb all")
            duthost.shell("config vlan member del {} {}".format(vlan_id, port))
            duthost.shell("ip link set {} nomaster".format(port), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
            duthost.shell("config interface startup {}".format(port))

            duthost.shell("config portchannel member add PortChannel{} {}".format(lag, port))
            duthost.shell("config vlan member add {} PortChannel{} --untagged".format(vlan_id, lag))

        yield
        # teamdown
        for lag in self.lag_ids:
            # stop teamd on PTF
            ptfhost.script("l2_lag/l2_lag_teamd.sh stop {} \"{}\"".format(lag, ptf_ports[self.lag_ids.index(lag)]))

            # stop teamd on DUT
            duthost.shell("config interface shutdown PortChannel{}".format(lag))
            duthost.shell("sonic-clear fdb all")
            duthost.shell("config vlan member del {} PortChannel{}".format(vlan_id, lag))
            duthost.shell("ip link set PortChannel{} nomaster".format(lag), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
            duthost.shell("config interface startup PortChannel{}".format(lag))

            port = dut_ports[self.lag_ids.index(lag)]
            duthost.shell("config portchannel member del PortChannel{} {}".format(lag, port))
            duthost.shell("config vlan member add {} {} --untagged".format(vlan_id, port))
            duthost.shell("config portchannel del PortChannel{}".format(lag))

        # start init teamd on PTF
        ptfhost.script("l2_lag/l2_lag_teamd.sh start {} \"{}\"".format(lag_id, ' '.join([str(port) for port in ptf_ports[0:MEMBER_MAX]])))
        
        # start init teamd on DUT
        duthost.shell("config portchannel add PortChannel{}".format(lag_id))
        for port in dut_ports[:MEMBER_INIT]:
            duthost.shell("config interface shutdown {}".format(port))
            duthost.shell("sonic-clear fdb all")
            duthost.shell("config vlan member del {} {}".format(vlan_id, port))
            duthost.shell("ip link set {} nomaster".format(port), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
            duthost.shell("config interface startup {}".format(port))

            duthost.shell("config portchannel member add PortChannel{} {}".format(lag_id, port))

        duthost.shell("config vlan member add {} PortChannel{} --untagged".format(vlan_id, lag_id))

        # shutdown backup lag members
        for port in dut_ports[MEMBER_INIT:MEMBER_MAX]:
            duthost.shell("config interface shutdown {}".format(port))

    def test_verify_multilags_status(self, duthost):
        for lag in self.lag_ids:
            res = False
            for i in range(0, 12):
                status = duthost.shell("redis-cli -n 0 hget LAG_TABLE:PortChannel{} oper_status".format(lag))['stdout']
                if status == 'up':
                    res = True
                    break
                else:
                    time.sleep(5)
            assert res, "PortChannel{} status should be up".format(lag)

    def test_send_pkts_to_multilags(self, ptfhost):
        for lag in self.lag_ids:
            ptf_runner(
                        ptfhost, 
                        "ptftests", 
                        "l2_lag.LagTest",
                        platform_dir="ptftests",
                        params={
                            "src_mac": mac1,
                            "dst_mac": broadcast_mac,
                            "src_port": ptf_ports[self.lag_ids.index(lag)],
                            "dst_ports": [port for port in ptf_ports if port not in [ptf_ports[self.lag_ids.index(lag)]]]
                        },
                        log_file="/tmp/l2_lag_[{}]_[{}]_lag{}_learn_mac.log".format(self.__class__.__name__, sys._getframe().f_code.co_name, lag)
            )
            ptf_runner(
                        ptfhost, 
                        "ptftests", 
                        "l2_lag.LagTest",
                        platform_dir="ptftests",
                        params={
                            "src_mac": mac2,
                            "dst_mac": mac1,
                            "src_port": ptf_ports[-1],
                            "dst_ports": [ptf_ports[self.lag_ids.index(lag)]]
                        },
                        log_file="/tmp/l2_lag_[{}]_[{}]_lag{}_verify_unicast.log".format(self.__class__.__name__, sys._getframe().f_code.co_name, lag)
            )

class TestCase3_LagAdminStatus():
    @pytest.fixture(scope='function')
    def shutdown_lag(self, duthost):
        duthost.shell("config interface shutdown PortChannel{}".format(lag_id))
        yield
        duthost.shell("config interface startup PortChannel{}".format(lag_id))

    @pytest.mark.usefixtures("shutdown_lag")
    def test_shutdown_lag(self, duthost, ptfhost):
        status = duthost.shell("redis-cli -n 0 hget LAG_TABLE:PortChannel{} oper_status".format(lag_id))['stdout']
        res    = True if status == 'down' else False
        assert res, "PortChannel{} status should be down after admin down".format(lag_id)

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[0],
                        "dst_ports": [ptf_ports[-1]],
                        "pkt_action": "drop"
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac2,
                        "dst_mac": mac1,
                        "src_port": ptf_ports[-1],
                        "dst_ports": ptf_ports[:MEMBER_INIT],
                        "pkt_action": "drop"
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    def test_startup_lag(self, duthost, ptfhost):
        res = False
        for i in range(0, 12):
            status = duthost.shell("redis-cli -n 0 hget LAG_TABLE:PortChannel{} oper_status".format(lag_id))['stdout']
            if status == 'up':
                res = True
                break
            else:
                time.sleep(5)
        assert res, "PortChannel{} status should be up after admin up".format(lag_id)

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[0],
                        "dst_ports": ptf_ports[MEMBER_INIT:]
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac2,
                        "dst_mac": mac1,
                        "src_port": ptf_ports[-1],
                        "dst_ports": ptf_ports[:MEMBER_INIT]
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

class TestCase4_MemberAdminStatus():
    @pytest.fixture(scope="function")
    def setup_shutdown_member(self, duthost):
        duthost.shell("config interface shutdown {}".format(port1))
        yield
        duthost.shell("config interface startup {}".format(port1))

    @pytest.fixture(scope="function")
    def setup_shutdown_all_member(self, duthost):
        for port in dut_ports[:MEMBER_INIT]:
            duthost.shell("config interface shutdown {}".format(port))
        yield
        for port in dut_ports[:MEMBER_INIT]:
            duthost.shell("config interface startup {}".format(port))

    @pytest.mark.usefixtures("setup_shutdown_member")
    def test_shutdown_member(self, duthost, ptfhost):
        # verify member not selected after shutdown
        member_status = duthost.shell("teamdctl PortChannel{} state item get ports.{}.runner.selected".format(lag_id, port1))['stdout']
        assert member_status == 'false', "Member status should not be selected after shutdown"
        
        # verify lag status is 'up'
        lag_status = duthost.shell("redis-cli -n 0 hget LAG_TABLE:PortChannel{} oper_status".format(lag_id))['stdout']
        assert lag_status == 'up', "Lag status should be up after shutdown one of the members"

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[1],
                        "dst_ports": ptf_ports[MEMBER_INIT:]
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac2,
                        "dst_mac": mac1,
                        "src_port": ptf_ports[-1],
                        "dst_ports": ptf_ports[:MEMBER_INIT]
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    @pytest.mark.usefixtures("setup_shutdown_all_member")
    def test_shutdown_all(self, duthost, ptfhost):
        # shutdown all member and verify member_status
        for port in dut_ports[:MEMBER_INIT]:
            member_status = duthost.shell("teamdctl PortChannel{} state item get ports.{}.runner.selected".format(lag_id, port))['stdout']
            assert member_status == 'false', "Member {} status should be not selected after shutdown".format(port)

        # verify lag status
        lag_status = duthost.shell("redis-cli -n 0 hget LAG_TABLE:PortChannel{} oper_status".format(lag_id))['stdout']
        assert lag_status == 'down', "Lag status should be down after shutdown all members"

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[1],
                        "dst_ports": ptf_ports[MEMBER_INIT:],
                        "pkt_action": "drop"
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac2,
                        "dst_mac": mac1,
                        "src_port": ptf_ports[-1],
                        "dst_ports": ptf_ports[:MEMBER_INIT],
                        "pkt_action": "drop"
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    @pytest.mark.usefixtures("setup_shutdown_all_member")
    def test_startup_member(self, duthost, ptfhost):
        duthost.shell("config interface startup {}".format(port1))
        # verify member selected after startup
        member_status = 'false'
        for i in range(0, 12):
            member_status = duthost.shell("teamdctl PortChannel{} state item get ports.{}.runner.selected".format(lag_id, port1))['stdout']
            if member_status == 'true':
                break
            else:
                time.sleep(5)
        assert member_status == 'true', "Member status should be selected after startup"
        
        # verify lag status is 'up'
        lag_status = duthost.shell("redis-cli -n 0 hget LAG_TABLE:PortChannel{} oper_status".format(lag_id))['stdout']
        assert lag_status == 'up', "Lag status should be up after member selected"

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[0],
                        "dst_ports": ptf_ports[MEMBER_INIT:]
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac2,
                        "dst_mac": mac1,
                        "src_port": ptf_ports[-1],
                        "dst_ports": ptf_ports[:MEMBER_INIT]
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    @pytest.mark.usefixtures("setup_shutdown_all_member")
    def test_startup_all(self, duthost, ptfhost):
        # startup all members and verify member_status
        for port in dut_ports[:MEMBER_INIT]:
            duthost.shell("config interface startup {}".format(port))
            member_status = 'false'
            for i in range(0, 12):
                member_status = duthost.shell("teamdctl PortChannel{} state item get ports.{}.runner.selected".format(lag_id, port1))['stdout']
                if member_status == 'true':
                    break
                else:
                    time.sleep(5)
            assert member_status == 'true', "Member {} status not selected after startup".format(port)
        
        # verify lag status is 'up'
        lag_status = duthost.shell("redis-cli -n 0 hget LAG_TABLE:PortChannel{} oper_status".format(lag_id))['stdout']
        assert lag_status == 'up', "Lag status should be up after all member selected"

        time.sleep(10)

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[0],
                        "dst_ports": ptf_ports[MEMBER_INIT:]
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac2,
                        "dst_mac": mac1,
                        "src_port": ptf_ports[-1],
                        "dst_ports": ptf_ports[:MEMBER_INIT],
                        "balance": "mac"
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

@pytest.mark.skip(reason="Not supported on SONiC")
class TestCase5_DelLagAttachedToVlan():
    pass

@pytest.mark.usefixtures("setup_lag_rif")
class TestCase6_DelLagAttachedToRIF():
    @pytest.fixture(scope="function")
    def setup_dellag(self, duthost):
        duthost.shell("sonic-clear arp")
        duthost.shell("sonic-clear ndp")
        time.sleep(5)
        duthost.shell("config portchannel del PortChannel{}".format(lag_id))

        yield
        # resume lag rif configuration
        duthost.shell("config portchannel add PortChannel{}".format(lag_id))
        for port in dut_ports[:MEMBER_INIT]:
            duthost.shell("config portchannel member add PortChannel{} {}".format(lag_id, port))
        duthost.shell("config interface ip add PortChannel{} {}".format(lag_id, dut_ip))
        duthost.shell("config interface ip add PortChannel{} {}".format(lag_id, dut_ipv6))

    @pytest.fixture(scope="function")
    def setup_joinvlan(self, duthost):
        for port in dut_ports[:MEMBER_INIT]:
            duthost.shell("config vlan member add {} {} --untagged".format(vlan_id, port))

        yield
        for port in dut_ports[:MEMBER_INIT]:
            duthost.shell("config interface shutdown {}".format(port))
            duthost.shell("sonic-clear fdb all")
            duthost.shell("config vlan member del {} {}".format(vlan_id, port))
            duthost.shell("ip link set {} nomaster".format(port), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
            duthost.shell("config interface startup {}".format(port))


    def test_ping_lag_rif(self, duthost, ptfhost):
        ptfhost.shell("ping {} -c 3 -f -W 2 -I {}".format(dut_ip.ip, ptf_ip.ip))
        ptfhost.shell("ping6 {} -c 3 -f -W 2 -I {}".format(dut_ipv6.ip, ptf_ipv6.ip))

        for ip_addr in [ptf_ip, ptf_ipv6]:
            res = duthost.shell("ip neigh show dev PortChannel{}".format(lag_id))['stdout']
            assert str(ip_addr.ip) in res, "{} should learn on PortChannel{}".format(ip_addr.ip, lag_id)

    @pytest.mark.usefixtures("setup_dellag")
    def test_lag_del(self, duthost, ptfhost):
        res = duthost.shell("redis-cli -n 0 exists \"LAG_TABLE:PortChannel{}\"".format(lag_id))['stdout'].split(' ')[-1]
        assert res == '0', "PortChannel{} should be removed from config_db after delete".format(lag_id)

        for ip_addr in [dut_ip, dut_ipv6]:
            ping_cmd = 'ping6' if ip_addr.version == 6 else 'ping'
            ping = ptfhost.shell("{} {} -c 3 -f -W 2".format(ping_cmd, ip_addr.ip), module_ignore_errors=True)['rc']
            assert ping != 0, "Ping {} should failed after lag delete".format(ip_addr.ip)

            res1 = duthost.shell("ip neigh show dev PortChannel{}".format(lag_id), module_ignore_errors=True)['stdout']
            assert str(ip_addr.ip) not in res1, "{} on PortChannel{] should be flushed".format(ip_addr.ip, lag_id)

    @pytest.mark.usefixtures("setup_joinvlan")
    @pytest.mark.usefixtures("setup_dellag")
    def test_member_join_vlan_after_lag_del(self, ptfhost, mg_facts):
        for port in ptf_ports[:MEMBER_INIT]:
            ptf_runner(
                        ptfhost, 
                        "ptftests", 
                        "l2_lag.LagTest",
                        platform_dir="ptftests",
                        params={
                            "src_mac": mac1,
                            "dst_mac": broadcast_mac,
                            "src_port": port,
                            "dst_ports": [p for p in ptf_ports if p != port]
                        },
                        log_file="/tmp/l2_lag_[{}]_[{}]_member_{}_recv.log".format(self.__class__.__name__, sys._getframe().f_code.co_name, port)
            )

            ptf_runner(
                        ptfhost, 
                        "ptftests", 
                        "l2_lag.LagTest",
                        platform_dir="ptftests",
                        params={
                            "src_mac": mac2,
                            "dst_mac": mac1,
                            "src_port": ptf_ports[-1],
                            "dst_ports": [port]
                        },
                        log_file="/tmp/l2_lag_[{}]_[{}]_member_{}_send.log".format(self.__class__.__name__, sys._getframe().f_code.co_name, port)
            )

class TestCase7_LoadBalance():
    def test_smac_balance(self, ptfhost):
        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[0],
                        "dst_ports": ptf_ports[MEMBER_INIT:]
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac2,
                        "dst_mac": mac1,
                        "src_port": ptf_ports[-1],
                        "dst_ports": ptf_ports[:MEMBER_INIT],
                        "balance": "mac"
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    def test_sip_balance(self, ptfhost):
        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "src_port": ptf_ports[0],
                        "dst_ports": ptf_ports[MEMBER_INIT:]
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac2,
                        "dst_mac": mac1,
                        "src_port": ptf_ports[-1],
                        "dst_ports": ptf_ports[:MEMBER_INIT],
                        "balance": "ip"
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

class TestCase8_NeighLearnOnLagPort():
    @pytest.fixture(scope="class", autouse=True)
    def setup_vlan_peer_addr(self, duthost, ptfhost):
        duthost.shell("config interface ip add Vlan{} {}".format(vlan_id, dut_ip))
        duthost.shell("config interface ip add Vlan{} {}".format(vlan_id, dut_ipv6))

        # disable dad detection on lag
        dad = ptfhost.shell("sysctl net.ipv6.conf.PortChannel{}.accept_dad".format(lag_id))['stdout'].split(" = ")[-1]
        ptfhost.shell("sysctl net.ipv6.conf.PortChannel{}.accept_dad=0".format(lag_id))

        ptfhost.shell("ip address add {} dev PortChannel{}".format(ptf_ip, lag_id))
        ptfhost.shell("ip address add {} dev PortChannel{}".format(ptf_ipv6, lag_id))

        yield
        duthost.shell("config interface ip remove Vlan{} {}".format(vlan_id, dut_ip))
        duthost.shell("config interface ip remove Vlan{} {}".format(vlan_id, dut_ipv6))
        ptfhost.shell("ip address flush dev PortChannel{}".format(lag_id))

        # restore dad detection on lag
        ptfhost.shell("sysctl net.ipv6.conf.PortChannel{}.accept_dad={}".format(lag_id, dad))

    def test_ipv4_neigh(self, duthost, ptfhost):
        # flush fdb
        duthost.shell("sonic-clear fdb all")

        # verify ping
        ptfhost.shell("ping {} -c 3 -f -W 2".format(dut_ip.ip))
        time.sleep(5)

        # verify neighbor
        neigh_res = duthost.shell("ip neigh show dev Vlan{}".format(vlan_id))['stdout']
        assert str(ptf_ip.ip) in neigh_res, "Neighbor {} should exists on Vlan{}".format(ptf_ip.ip, vlan_id)

        # verify mac
        mac = duthost.shell("ip neigh show dev Vlan%s|awk '$1==\"%s\" {print $3}'" % (vlan_id, ptf_ip.ip))['stdout']
        mac_res = duthost.shell("show mac|grep PortChannel{}".format(lag_id), module_ignore_errors=True)['stdout']
        assert mac.upper() in mac_res, "Mac {} should exists on PortChannel{}".format(mac, lag_id)

    def test_ipv6_neigh(self, duthost, ptfhost):
        # flush fdb
        duthost.shell("sonic-clear fdb all")

        # verify ping
        ptfhost.shell("ping6 {} -c 3 -f -W 2 -I {}".format(dut_ipv6.ip, ptf_ipv6.ip))
        time.sleep(5)

        # verify neighbor
        neigh_res = duthost.shell("ip neigh show dev Vlan{}".format(vlan_id))['stdout']
        assert str(ptf_ipv6.ip) in neigh_res, "Neighbor {} should exists on Vlan{}".format(ptf_ipv6.ip, vlan_id)

        # verify mac
        mac = duthost.shell("ip neigh show dev Vlan%s|awk '$1==\"%s\" {print $3}'" % (vlan_id, ptf_ipv6.ip))['stdout']
        mac_res = duthost.shell("show mac|grep PortChannel{}".format(lag_id), module_ignore_errors=True)['stdout']
        assert mac.upper() in mac_res, "Mac {} should exists on PortChannel{}".format(mac, lag_id)

@pytest.mark.usefixtures("setup_lag_rif")
class TestCase9_NeighLearnOnLagRIF():
    def test_ipv4_neigh(self, duthost, ptfhost):
        ptfhost.shell("ping {} -c 3 -f -W 2 -I {}".format(dut_ip.ip, ptf_ip.ip))
        neigh_res = duthost.shell("ip neigh show dev PortChannel{}".format(lag_id))['stdout']
        assert str(ptf_ip.ip) in neigh_res, "Neighbor {} should exists on PortChannel{}".format(ptf_ip.ip, lag_id)

    def test_ipv6_neigh(self, duthost, ptfhost):
        ptfhost.shell("ping6 {} -c 3 -f -W 2 -I {}".format(dut_ipv6.ip, ptf_ipv6.ip))
        neigh_res = duthost.shell("ip neigh show dev PortChannel{}".format(lag_id))['stdout']
        assert str(ptf_ipv6.ip) in neigh_res, "Neighbor {} should exists on PortChannel{}".format(ptf_ipv6.ip, lag_id)

class TestCase10_RouteViaLag():
    target  = IPNetwork("200.1.1.1/32")
    target6 = IPNetwork("3000:1::1/128")

    @pytest.fixture(scope="class")
    def setup_static_route(self, duthost):
        duthost.shell("vtysh -c \"config terminal\" -c \"ip route {} {}\"".format(self.target, ptf_ip.ip))
        duthost.shell("vtysh -c \"config terminal\" -c \"ipv6 route {} {}\"".format(self.target6, ptf_ipv6.ip))

        yield
        duthost.shell("vtysh -c \"config terminal\" -c \"no ip route {} {}\"".format(self.target, ptf_ip.ip))
        duthost.shell("vtysh -c \"config terminal\" -c \"no ipv6 route {} {}\"".format(self.target6, ptf_ipv6.ip))

    @pytest.mark.usefixtures("setup_static_route")
    @pytest.mark.usefixtures("setup_lag_rif")
    def test_route_via_lag(self, duthost, ptfhost, host_facts):
        for dst in [self.target, self.target6]:
            (ping_cmd, ping_target, src_ip) = ("ping6", dut_ipv6.ip, ptf_ipv6.ip) if dst.version == 6 else ("ping", dut_ip.ip, ptf_ip.ip)
            ptfhost.shell("{} {} -c 3 -f -W 2 -I {}".format(ping_cmd, ping_target, src_ip))

            route_info = duthost.shell("vtysh -c \"show ip route {}\"".format(dst))['stdout_lines']
            for line in route_info:
                if str(src_ip) in line:
                    out_if = line.split(' ')[-1]
            assert out_if == "PortChannel{}".format(lag_id), "Route {} out_if should be PortChannel{}".format(dst, lag_id)

            ptf_runner(
                        ptfhost, 
                        "ptftests", 
                        "l2_lag.LagTest",
                        platform_dir="ptftests",
                        params={
                            "src_mac": mac2,
                            "dst_mac": host_facts['ansible_Ethernet0']['macaddress'],
                            "dst_ip": str(dst.ip),
                            "src_port": ptf_ports[-1],
                            "dst_ports": ptf_ports[:MEMBER_INIT]
                        },
                        log_file="/tmp/l2_lag_[{}]_[{}]_V{}.log".format(self.__class__.__name__, sys._getframe().f_code.co_name, dst.version)
            )


class TestCase11_VlanOnLag():
    vlan_count   = 5
    vlan_base    = 2000
    vlan_invalid = vlan_base + vlan_count

    @pytest.fixture(scope="class", autouse=True)
    def setup_vlan_mode(self, duthost):
        for i in range(0, self.vlan_count):
            vid = self.vlan_base + i
            duthost.shell("config vlan add {}".format(vid))
            duthost.shell("config vlan member add {} PortChannel{}".format(vid, lag_id))
            duthost.shell("config vlan member add {} {}".format(vid, port3))

        yield
        # teardown
        duthost.shell("config interface shutdown PortChannel{}".format(lag_id))
        duthost.shell("config interface shutdown {}".format(port3))
        duthost.shell("sonic-clear fdb all")

        for i in range(0, self.vlan_count):
            vid = self.vlan_base + i
            duthost.shell("config vlan member del {} PortChannel{}".format(vid, lag_id))
            duthost.shell("config vlan member del {} {}".format(vid, port3))
            duthost.shell("config vlan del {}".format(vid))

        duthost.shell("config interface startup {}".format(port3))
        duthost.shell("config interface startup PortChannel{}".format(lag_id))

    def test_check_vlan_member(self, duthost):
        for i in range(0, self.vlan_count):
            vid = self.vlan_base + i
            tagging_mode = duthost.shell("redis-cli -n 0 hget \"VLAN_MEMBER_TABLE:Vlan{}:PortChannel{}\" \"tagging_mode\"".format(vid, lag_id))['stdout']
            assert tagging_mode == 'tagged', "PortChannel{} should join Vlan{} with tagging_mode tagged".format(lag_id, vid)

    def test_send_valid_vlan_pkt(self, duthost, ptfhost):
        vlans = []
        for i in range(0, self.vlan_count):
            vid = self.vlan_base + i
            vlans.append(str(vid))
            ptf_runner(
                        ptfhost, 
                        "ptftests", 
                        "l2_lag.LagTest",
                        platform_dir="ptftests",
                        params={
                            "src_mac": mac1,
                            "dst_mac": broadcast_mac,
                            "vlan": vid,
                            "src_port": ptf_ports[0],
                            "dst_ports": [ptf_ports[-1]]
                        },
                        log_file="/tmp/l2_lag_[{}]_[{}]_Vlan{}.log".format(self.__class__.__name__, sys._getframe().f_code.co_name, vid)
            )

        time.sleep(10)
        mac_vlan_res = duthost.shell("show mac -p PortChannel%s|grep %s|awk '{print $2}'" % (lag_id, mac1), module_ignore_errors=True)['stdout_lines']
        assert mac_vlan_res==vlans, "{} learned on PortChannel{} should be exists on vlans {}".format(mac1, lag_id, vlans)

    def test_send_invalid_vlan_pkt(self, duthost, ptfhost):
        ptf_runner(
                    ptfhost, 
                    "ptftests", 
                    "l2_lag.LagTest",
                    platform_dir="ptftests",
                    params={
                        "src_mac": mac1,
                        "dst_mac": broadcast_mac,
                        "vlan": self.vlan_invalid,
                        "src_port": ptf_ports[0],
                        "dst_ports": ptf_ports[MEMBER_INIT:],
                        "pkt_action": "drop"
                    },
                    log_file="/tmp/l2_lag_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    def test_send_vlan_pkt_to_lag(self, ptfhost):
        for i in range(0, self.vlan_count):
            vid = self.vlan_base + i
            ptf_runner(
                        ptfhost, 
                        "ptftests", 
                        "l2_lag.LagTest",
                        platform_dir="ptftests",
                        params={
                            "src_mac": mac2,
                            "dst_mac": mac1,
                            "vlan": vid,
                            "src_port": ptf_ports[-1],
                            "dst_ports": ptf_ports[:MEMBER_INIT]
                        },
                        log_file="/tmp/l2_lag_[{}]_[{}]_Vlan{}.log".format(self.__class__.__name__, sys._getframe().f_code.co_name, vid)
            )
