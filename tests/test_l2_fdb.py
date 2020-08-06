import pytest
import time
import sys

from ptf_runner import ptf_runner

# functions

# global vars
g_vars = {}
g_vars['mac1']               = "00:01:00:00:00:01"
g_vars['mac2']               = "00:01:00:00:00:02"
g_vars['broadcast_mac']      = "ff:ff:ff:ff:ff:ff"
g_vars['multicast_mac']      = "01:00:5e:00:00:01"
g_vars['unkown_unicast_mac'] = "00:01:02:03:04:05"

g_vars['lag_id']     = 100
g_vars['member_num'] = 2


# fixtures
@pytest.fixture(scope="module")
def mg_facts(duthost, testbed):
    hostname = testbed['dut']
    return duthost.minigraph_facts(host=hostname)['ansible_facts']

@pytest.fixture(scope="module", autouse=True)
def setup_init(mg_facts, ptfhost):
    # vars
    global g_vars

    g_vars['dut_ports'] = mg_facts['minigraph_vlans'].values()[0]['members']
    g_vars['ptf_ports'] = [mg_facts['minigraph_port_indices'][port] for port in g_vars['dut_ports']]

    g_vars.update({'port1': {'port_name': g_vars['dut_ports'][0], 'ptf_peer': mg_facts['minigraph_port_indices'][g_vars['dut_ports'][0]]}})
    g_vars.update({'port2': {'port_name': g_vars['dut_ports'][1], 'ptf_peer': mg_facts['minigraph_port_indices'][g_vars['dut_ports'][1]]}})
    g_vars.update({'port3': {'port_name': g_vars['dut_ports'][-1], 'ptf_peer': mg_facts['minigraph_port_indices'][g_vars['dut_ports'][-1]]}})
    g_vars['vlan_id']   = mg_facts['minigraph_vlans'].values()[0]['vlanid']

    # copy ptftest script
    ptfhost.copy(src="ptftests", dest="/root")
    ptfhost.script("scripts/remove_ip.sh")
    ptfhost.script("scripts/change_mac.sh")

@pytest.fixture(scope="class")
def setup_lag(duthost, ptfhost):
    # setup lag on ptf
    ptf_extra_vars = {  
        'lag_id'   : g_vars['lag_id'],
        'members'  : g_vars['ptf_ports'][0:g_vars['member_num']]
    }

    ptfhost.host.options['variable_manager'].extra_vars = ptf_extra_vars

    ptfhost.template(src="l2_fdb/l2_fdb_PortChannel.conf.j2", dest="/tmp/PortChannel{}.conf".format(g_vars['lag_id']))
    # start teamd with g_vars['lag_id']
    ptfhost.script("l2_fdb/l2_fdb_teamd.sh start {} \"{}\"".format(g_vars['lag_id'], ' '.join([str(port) for port in g_vars['ptf_ports'][0:g_vars['member_num']]])))

    # setup lag on dut
    # creat lag 
    duthost.shell("config portchannel add PortChannel{}".format(g_vars['lag_id']))
    # remove port from origin vlan and add to lag
    for port in g_vars['dut_ports'][0:g_vars['member_num']]:
        duthost.shell("config interface shutdown {}".format(port))
        duthost.shell("sonic-clear fdb all")
        duthost.shell("config vlan member del {} {}".format(g_vars['vlan_id'], port))
        duthost.shell("ip link set {} nomaster".format(port), module_ignore_errors=True) # workaround for https://github.com/Azure/sonic-swss/pull/1001
        duthost.shell("config interface startup {}".format(port))
        duthost.shell("config portchannel member add PortChannel{} {}".format(g_vars['lag_id'], port))
    # add lag to vlan
    duthost.shell("config vlan member add {} PortChannel{} --untagged".format(g_vars['vlan_id'], g_vars['lag_id']))

    yield
    # teardown
    # stop teamd on ptf
    ptfhost.script("l2_fdb/l2_fdb_teamd.sh stop {} \"{}\"".format(g_vars['lag_id'], ' '.join([str(port) for port in g_vars['ptf_ports'][0:g_vars['member_num']]])))
    # restore configuration on dut
    duthost.shell("config interface shutdown PortChannel{}".format(g_vars['lag_id']))
    duthost.shell("sonic-clear fdb all")
    duthost.shell("config vlan member del {} PortChannel{}".format(g_vars['vlan_id'], g_vars['lag_id']))
    duthost.shell("config interface startup PortChannel{}".format(g_vars['lag_id']))
    for port in g_vars['dut_ports'][0:g_vars['member_num']]:
        duthost.shell("config portchannel member del PortChannel{} {}".format(g_vars['lag_id'], port))
        duthost.shell("config vlan member add {} {} --untagged".format(g_vars['vlan_id'], port))
    duthost.shell("config portchannel del PortChannel{}".format(g_vars['lag_id']))

@pytest.fixture(scope="class")
def send_learn_frame(ptfhost, mg_facts):
    ptf_runner( 
                ptfhost,
                "ptftests",
                "l2_fdb.L2FdbTest",
                platform_dir="ptftests",
                params={"src_mac": g_vars['mac1'],
                        "dst_mac": g_vars['broadcast_mac'],
                        "src_port": g_vars['port1']['ptf_peer'],
                        "dst_port_list": [port for port in g_vars['ptf_ports'] if port not in [g_vars['port1']['ptf_peer']]]
                    },
                log_file="/tmp/l2_fdb_[{}].log".format(sys._getframe().f_code.co_name)
    )
    time.sleep(5)

@pytest.fixture(scope="class", autouse=True)
def clear_mac(duthost):
    # setup 
    duthost.shell("sonic-clear fdb all")
    yield
    # clear mac-table after every case finished
    duthost.shell('sonic-clear fdb all')

@pytest.mark.usefixtures("send_learn_frame")
class TestCase1_MacLearn():
    def test_mac_learn(self, duthost):
        # verify mac1 learned on port1
        res = duthost.shell("show mac|grep {}".format(g_vars['port1']['port_name']), module_ignore_errors=True)
        assert g_vars['mac1'] in res['stdout'], "{} should learned on {}".format(g_vars['mac1'], g_vars['port1']['port_name'])

    def test_fwd_by_mac(self, ptfhost, mg_facts):
        ptf_runner( 
                    ptfhost,
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac2'],
                            "dst_mac": g_vars['mac1'],
                            "src_port": g_vars['port2']['ptf_peer'],
                            "dst_port_list": [g_vars['port1']['ptf_peer']]
                           },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

class TestCase2_PktFwd():
    def test_broadcast(self, ptfhost, mg_facts):
        ptf_runner( 
                    ptfhost,
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac1'],
                            "dst_mac": g_vars['broadcast_mac'],
                            "src_port": g_vars['port1']['ptf_peer'],
                            "dst_port_list": [port for port in g_vars['ptf_ports'] if port not in [g_vars['port1']['ptf_peer']]]
                           },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    def test_multicast(self, ptfhost, mg_facts):
        ptf_runner( 
                    ptfhost,
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac1'],
                            "dst_mac": g_vars['multicast_mac'],
                            "src_port": g_vars['port1']['ptf_peer'],
                            "dst_port_list": [port for port in g_vars['ptf_ports'] if port not in [g_vars['port1']['ptf_peer']]]
                           },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    def test_unknown_unicast(self, ptfhost, mg_facts):
        ptf_runner( 
                    ptfhost,
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac1'],
                            "dst_mac": g_vars['unkown_unicast_mac'],
                            "src_port": g_vars['port1']['ptf_peer'],
                            "dst_port_list": [port for port in g_vars['ptf_ports'] if port not in [g_vars['port1']['ptf_peer']]]
                           },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

@pytest.mark.usefixtures("send_learn_frame")
class TestCase3_MacAging():
    aging_time         = 200
    interval           = 10

    @pytest.fixture(scope="class", autouse=True)
    def setup_agingtime(self, duthost):
        # get default aging_time
        default_aging_time = duthost.shell("redis-cli -n 0 hget SWITCH_TABLE:switch fdb_aging_time")['stdout']

        dut_extra_vars = {'config_aging_time': self.aging_time}
        duthost.host.options['variable_manager'].extra_vars = dut_extra_vars

        duthost.template(src="l2_fdb/l2_fdb_config_aging.j2", dest="/tmp/l2_fdb_config_aging.json")
        duthost.shell("docker cp /tmp/l2_fdb_config_aging.json swss:/etc/swss/config.d/l2_fdb_config_aging.json")
        duthost.shell("docker exec -i swss swssconfig /etc/swss/config.d/l2_fdb_config_aging.json")

        yield
        dut_extra_vars = {'config_aging_time': default_aging_time}
        duthost.host.options['variable_manager'].extra_vars = dut_extra_vars

        duthost.template(src="l2_fdb/l2_fdb_config_aging.j2", dest="/tmp/l2_fdb_config_aging.json")
        duthost.shell("docker cp /tmp/l2_fdb_config_aging.json swss:/etc/swss/config.d/l2_fdb_config_aging.json")
        duthost.shell("docker exec -i swss swssconfig /etc/swss/config.d/l2_fdb_config_aging.json")

    def test_mac_learn(self, duthost):
        # verify mac1 learned on port1
        res = duthost.shell("show mac|grep {}".format(g_vars['port1']['port_name']), module_ignore_errors=True)
        assert g_vars['mac1'] in res['stdout'], "{} should learned on {}".format(g_vars['mac1'], g_vars['port1']['port_name'])

    def test_mac_aging(self, duthost, ptfhost, mg_facts):
        # max aging time = 2 * aging_time
        time.sleep(self.aging_time)
        res2 = False
        for i in range(0, self.aging_time/self.interval):
            res = duthost.shell("show mac|grep {}".format(g_vars['port1']['port_name']), module_ignore_errors=True)
            res2 = True if g_vars['mac1'] not in res['stdout'] else False
            if res2:
                break
            else:
                time.sleep(self.interval)
        assert res2, "{} should aged during 2*{} secs".format(g_vars['mac1'], self.aging_time)

        ptf_runner( 
                    ptfhost, 
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac2'],
                            "dst_mac": g_vars['mac1'],
                            "src_port": g_vars['port2']['ptf_peer'],
                            "dst_port_list": [port for port in g_vars['ptf_ports'] if port not in [g_vars['port2']['ptf_peer']]]
                            },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

@pytest.mark.skip(reason="Not support on Sonic")
@pytest.mark.usefixtures("send_learn_frame")
class TestCase4_MacDel_by_PortDown():
    @pytest.fixture(scope='function')
    def setup_shutdown_port(self, duthost):
        # setup 
        duthost.shell("config interface shutdown {}".format(g_vars['port1']['port_name']))
        time.sleep(2)

        yield
        # teardown
        duthost.shell("config interface startup {}".format(g_vars['port1']['port_name']))

    def test_mac_learn(self, duthost):
        # verify mac1 learned on port1
        res = duthost.shell("show mac|grep {}".format(g_vars['port1']['port_name']), module_ignore_errors=True)
        assert g_vars['mac1'] in res['stdout'], "{} should learned on {}".format(g_vars['mac1'], g_vars['port1']['port_name'])

    @pytest.mark.usefixtures("setup_shutdown_port")
    def test_shutdown_port(self, duthost, ptfhost, mg_facts):
        # verify mac1 deleted from port1
        res = duthost.shell("show mac|grep {}".format(g_vars['port1']['port_name']), module_ignore_errors=True)
        assert g_vars['mac1'] not in res['stdout'], "{} should be deleted from {} by shutdown port".format(g_vars['mac1'], g_vars['port1']['port_name'])

        ptf_runner( 
                    ptfhost, 
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac2'],
                            "dst_mac": g_vars['mac1'],
                            "src_port": g_vars['port2']['ptf_peer'],
                            "dst_port_list": [port for port in g_vars['ptf_ports'] if port not in [g_vars['port1']['ptf_peer'], g_vars['port2']['ptf_peer']]]
                            },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

@pytest.mark.skip(reason="Not support on Sonic")
@pytest.mark.usefixtures("send_learn_frame")
class TestCase5_MacDel_by_VlanChange():
    @pytest.fixture(scope='function')
    def setup_vlan_member(self, duthost):
        # setup
        duthost.shell("config vlan member del {} {}".format(g_vars['vlan_id'], g_vars['port1']['port_name']))
        time.sleep(2)

        yield
        # teardown
        duthost.shell("config vlan member add {} {} --untagged".format(g_vars['vlan_id'], g_vars['port1']['port_name']))

    def test_mac_learn(self, duthost):
        # verify mac1 learned on port1
        res = duthost.shell("show mac|grep {}".format(g_vars['port1']['port_name']), module_ignore_errors=True)
        assert g_vars['mac1'] in res['stdout'], "{} should learned on {}".format(g_vars['mac1'], g_vars['port1']['port_name'])

    @pytest.mark.usefixtures("setup_vlan_member")
    def test_vlan_change(self, duthost, ptfhost, mg_facts):
        # verify mac1 deleted from port1
        res = duthost.shell("show mac|grep {}".format(g_vars['port1']['port_name']), module_ignore_errors=True)
        assert g_vars['mac1'] not in res['stdout'], "{} should be deleted from {} by remove port from vlan".format(g_vars['mac1'], g_vars['port1']['port_name'])

        ptf_runner( 
                    ptfhost,
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac2'],
                            "dst_mac": g_vars['mac1'],
                            "src_port": g_vars['port2']['ptf_peer'],
                            "dst_port_list": [port for port in g_vars['ptf_ports'] if port not in [g_vars['port1']['ptf_peer'], g_vars['port2']['ptf_peer']]]
                            },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

@pytest.mark.skip(reason="Not support on Sonic")
@pytest.mark.usefixtures("send_learn_frame")
class TestCase6_MacDel_by_VlanDel():
    @pytest.fixture(scope="function")
    def setup_vlan(self, duthost, mg_facts):
        ip = '{}/{}'.format(mg_facts['minigraph_vlan_interfaces'][0]['addr'], mg_facts['minigraph_vlan_interfaces'][0]['prefixlen'])
        # setup
        duthost.shell("config interface ip remove Vlan{} {}".format(g_vars['vlan_id'], ip))
        duthost.shell("config vlan del {}".format(g_vars['vlan_id']))
        time.sleep(2)

        yield
        # teardown
        duthost.shell("config vlan add {}".format(g_vars['vlan_id']))
        for port in g_vars['dut_ports']:
            duthost.shell("config vlan member add {} {} --untagged".format(g_vars['vlan_id'], port))
        duthost.shell("config interface ip add Vlan{} {}".format(g_vars['vlan_id'], ip))

    def test_mac_learn(self, duthost):
        # verify mac1 learned on port1
        res = duthost.shell("show mac|grep {}".format(g_vars['port1']['port_name']), module_ignore_errors=True)
        assert g_vars['mac1'] in res['stdout'], "{} should learned on {}".format(g_vars['mac1'], g_vars['port1']['port_name'])

    @pytest.mark.usefixtures("setup_vlan")
    def test_del_vlan(self, duthost):
        # verify mac1 deleted from port1
        res = duthost.shell("show mac|grep {}".format(g_vars['port1']['port_name']), module_ignore_errors=True)
        assert g_vars['mac1'] not in res['stdout'], "{} should be deleted from {} by remove vlan".format(g_vars['mac1'], g_vars['port1']['port_name'])

@pytest.mark.usefixtures("send_learn_frame")
class TestCase7_MacDel_by_Clear():
    def test_mac_learn(self, duthost):
        # verify mac1 learned on port1
        res = duthost.shell("show mac|grep {}".format(g_vars['port1']['port_name']), module_ignore_errors=True)
        assert g_vars['mac1'] in res['stdout'], "{} should learned on {}".format(g_vars['mac1'], g_vars['port1']['port_name'])

    def test_clear_fdb(self, duthost, ptfhost, mg_facts):
        duthost.shell("sonic-clear fdb all")
        time.sleep(2)
        # verify mac1 deleted from port1
        res = duthost.shell("show mac|grep {}".format(g_vars['port1']['port_name']), module_ignore_errors=True)
        assert g_vars['mac1'] not in res['stdout'], "{} should be deleted from {} by clear command".format(g_vars['mac1'], g_vars['port1']['port_name'])

        ptf_runner( 
                    ptfhost,
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac2'],
                            "dst_mac": g_vars['mac1'],
                            "src_port": g_vars['port2']['ptf_peer'],
                            "dst_port_list": [port for port in g_vars['ptf_ports'] if port not in [g_vars['port2']['ptf_peer']]]
                            },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

class TestCase8_StaticFDB():
    @pytest.fixture(scope="class", autouse=True)
    def setup_static_fdb(self, duthost):
        # setup
        dut_extra_vars = {
            'vlan_id': g_vars['vlan_id'],
            'port'   : g_vars['port1']['port_name'],
            'mac'    : g_vars['mac1'].replace(":", "-")
        }
        cfg_name = 'l2_fdb_config_static_fdb'

        dut_extra_vars['op'] = "SET"
        duthost.host.options['variable_manager'].extra_vars = dut_extra_vars

        duthost.template(src="l2_fdb/{}.j2".format(cfg_name), dest="/tmp/{}.json".format(cfg_name))
        duthost.shell("docker cp /tmp/{}.json swss:/etc/swss/config.d/{}.json".format(cfg_name, cfg_name))
        duthost.shell("docker exec -i swss swssconfig /etc/swss/config.d/{}.json".format(cfg_name))

        yield
        # teardown

    def test_add_static_mac(self, duthost):
        # verify mac1 added on port1
        res = duthost.shell("show mac|grep {}".format(g_vars['port1']['port_name']), module_ignore_errors=True)
        assert g_vars['mac1'] in res['stdout'], "Static fdb {} should be added to {}".format(g_vars['mac1'], g_vars['port1']['port_name'])

    def test_verify_static_mac_add_by_traffic(self, ptfhost, mg_facts):
        ptf_runner( 
                    ptfhost,
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac2'],
                            "dst_mac": g_vars['mac1'],
                            "src_port": g_vars['port2']['ptf_peer'],
                            "dst_port_list": [g_vars['port1']['ptf_peer']]
                            },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    def test_del_static_mac(self, duthost, ptfhost, mg_facts):
        dut_extra_vars = {
            'vlan_id': g_vars['vlan_id'],
            'port'   : g_vars['port1']['port_name'],
            'mac'    : g_vars['mac1'].replace(":", "-")
        }
        cfg_name = 'l2_fdb_config_static_fdb'

        dut_extra_vars['op'] = "DEL"
        duthost.host.options['variable_manager'].extra_vars = dut_extra_vars

        duthost.template(src="l2_fdb/{}.j2".format(cfg_name), dest="/tmp/{}.json".format(cfg_name))
        duthost.shell("docker cp /tmp/{}.json swss:/etc/swss/config.d/{}.json".format(cfg_name, cfg_name))
        duthost.shell("docker exec -i swss swssconfig /etc/swss/config.d/{}.json".format(cfg_name))

        # verify mac1 deleted from port1
        res = duthost.shell("show mac|grep {}".format(g_vars['port1']['port_name']), module_ignore_errors=True)
        assert g_vars['mac1'] not in res['stdout'], "Static fdb {} should be deleted from {}".format(g_vars['mac1'], g_vars['port1']['port_name'])

        ptf_runner( ptfhost, \
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac2'],
                            "dst_mac": g_vars['mac1'],
                            "src_port": g_vars['port2']['ptf_peer'],
                            "dst_port_list": [port for port in g_vars['ptf_ports'] if port not in [g_vars['port2']['ptf_peer']]]
                            },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

@pytest.mark.usefixtures("send_learn_frame")
class TestCase9_MacFlapping():
    def test_mac_learn_on_port1(self, duthost):
        # verify mac1 learned on port1
        res = duthost.shell("show mac|grep {}".format(g_vars['port1']['port_name']), module_ignore_errors=True)
        assert g_vars['mac1'] in res['stdout'], "{} should learned on {}".format(g_vars['mac1'], g_vars['port1']['port_name'])

    def test_mac_on_port1_by_traffic(self, ptfhost, mg_facts):
        ptf_runner( 
                    ptfhost,
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac2'],
                            "dst_mac": g_vars['mac1'],
                            "src_port": g_vars['port3']['ptf_peer'],
                            "dst_port_list": [g_vars['port1']['ptf_peer']]
                            },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    def test_mac_flapping(self, duthost, ptfhost, mg_facts):
        ptf_runner( 
                    ptfhost,
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac1'],
                            "dst_mac": g_vars['broadcast_mac'],
                            "src_port": g_vars['port2']['ptf_peer'],
                            "dst_port_list": [port for port in g_vars['ptf_ports'] if port not in [g_vars['port2']['ptf_peer']]]
                            },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

        time.sleep(10)

        # verify mac1 deleted from port1
        res = duthost.shell("show mac|grep {}".format(g_vars['port1']['port_name']), module_ignore_errors=True)
        assert g_vars['mac1'] not in res['stdout'], "{} should be deleted from {}".format(g_vars['mac1'], g_vars['port1']['port_name'])

        # verify mac1 learned on port2
        res = duthost.shell("show mac|grep {}".format(g_vars['port2']['port_name']), module_ignore_errors=True)
        assert g_vars['mac1'] in res['stdout'], "{} should learned on {}".format(g_vars['mac1'], g_vars['port2']['port_name'])

        ptf_runner( 
                    ptfhost,
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac2'],
                            "dst_mac": g_vars['mac1'],
                            "src_port": g_vars['port3']['ptf_peer'],
                            "dst_port_list": [g_vars['port2']['ptf_peer']]
                            },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

@pytest.mark.usefixtures("setup_lag")
class TestCase10_MacLearnOnLag():
    def test_check_lag_status(self, duthost):
        # check lag status
        res = False
        for i in range(0, 12):
            status = duthost.shell("redis-cli -n 0 hget LAG_TABLE:PortChannel{} oper_status".format(g_vars['lag_id']))['stdout']
            if status == 'up':
                res = True
                break
            else:
                time.sleep(5)
        assert res, "PortChannel{} status should be up".format(g_vars['lag_id'])

    def test_mac_learn_on_member1(self, duthost, ptfhost, mg_facts):
        ptf_runner( 
                    ptfhost,
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac1'],
                            "dst_mac": g_vars['broadcast_mac'],
                            "src_port": g_vars['port1']['ptf_peer'],
                            "dst_port_list": [port for port in g_vars['ptf_ports'] if port not in [g_vars['port1']['ptf_peer'], g_vars['port2']['ptf_peer']]]
                            },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

        time.sleep(5)
        # verify mac1 learned on lag
        res = duthost.shell("show mac|grep PortChannel{}".format(g_vars['lag_id']), module_ignore_errors=True)
        assert g_vars['mac1'] in res['stdout'], "Mac {} should be learned on PortChannel{}".format(g_vars['mac1'], g_vars['lag_id'])

        # verify mac learn by traffic
        ptf_runner( 
                    ptfhost,
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac2'],
                            "dst_mac": g_vars['mac1'],
                            "src_port": g_vars['port3']['ptf_peer'],
                            "dst_port_list": [g_vars['port1']['ptf_peer'], g_vars['port2']['ptf_peer']],
                            "dst_port_lag" : True
                            },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    def test_flush_fdb_on_lag(self, duthost):
        duthost.shell("sonic-clear fdb all")
        res = duthost.shell("show mac|grep PortChannel{}".format(g_vars['lag_id']), module_ignore_errors=True)
        assert g_vars['mac1'] not in res['stdout'], "Mac {} should be deleted from PortChannel{}".format(g_vars['mac1'], g_vars['lag_id'])

    def test_mac_learn_on_member2(self, duthost, ptfhost, mg_facts):
        ptf_runner( 
                    ptfhost,
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac1'],
                            "dst_mac": g_vars['broadcast_mac'],
                            "src_port": g_vars['port2']['ptf_peer'],
                            "dst_port_list": [port for port in g_vars['ptf_ports'] if port not in [g_vars['port1']['ptf_peer'], g_vars['port2']['ptf_peer']]]
                            },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

        time.sleep(5)
        # verify mac1 learned on lag
        res = duthost.shell("show mac|grep PortChannel{}".format(g_vars['lag_id']), module_ignore_errors=True)
        assert g_vars['mac1'] in res['stdout'], "Mac {} should be learned on PortChannel{}".format(g_vars['mac1'], g_vars['lag_id'])

        # verify mac learn by traffic
        ptf_runner( 
                    ptfhost,
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac2'],
                            "dst_mac": g_vars['mac1'],
                            "src_port": g_vars['port3']['ptf_peer'],
                            "dst_port_list": [g_vars['port1']['ptf_peer'], g_vars['port2']['ptf_peer']],
                            "dst_port_lag" : True,
                            },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

@pytest.mark.usefixtures("setup_lag")
class TestCase11_MacFlapingOnLagMember():
    @pytest.fixture(scope="function")
    def setup_shutdown_member1(self, duthost):
        duthost.shell("config interface shutdown {}".format(g_vars['port1']['port_name']))
        yield
        duthost.shell("config interface startup {}".format(g_vars['port1']['port_name']))

    @pytest.fixture(scope="function")
    def setup_shutdown_member2(self, duthost):
        duthost.shell("config interface shutdown {}".format(g_vars['port2']['port_name']))
        yield
        duthost.shell("config interface startup {}".format(g_vars['port2']['port_name']))

    def test_check_lag_status(self, duthost):
        # check lag status
        res = False
        for i in range(0, 12):
            status = duthost.shell("redis-cli -n 0 hget LAG_TABLE:PortChannel{} oper_status".format(g_vars['lag_id']))['stdout']
            if status == 'up':
                res = True
                break
            else:
                time.sleep(5)
        assert res, "PortChannel{} status should be up".format(g_vars['lag_id'])

    def test_mac_learn_on_member1(self, duthost, ptfhost, mg_facts):
        ptf_runner( ptfhost, \
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac1'],
                            "dst_mac": g_vars['broadcast_mac'],
                            "src_port": g_vars['port1']['ptf_peer'],
                            "dst_port_list": [port for port in g_vars['ptf_ports'] if port not in [g_vars['port1']['ptf_peer'], g_vars['port2']['ptf_peer']]]
                            },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

        time.sleep(5)
        # verify mac1 learned on lag
        res = duthost.shell("show mac|grep PortChannel{}".format(g_vars['lag_id']), module_ignore_errors=True)
        assert g_vars['mac1'] in res['stdout'], "Mac {} should be learned on PortChannel{}".format(g_vars['mac1'], g_vars['lag_id'])

        # verify mac learn by traffic
        ptf_runner( ptfhost, \
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac2'],
                            "dst_mac": g_vars['mac1'],
                            "src_port": g_vars['port3']['ptf_peer'],
                            "dst_port_list": [g_vars['port1']['ptf_peer'], g_vars['port2']['ptf_peer']],
                            "dst_port_lag" : True
                            },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name))

    def test_shutdown_member1(self, duthost, ptfhost, mg_facts, setup_shutdown_member1):
        # verify mac1 still exist on lag
        res = duthost.shell("show mac|grep PortChannel{}".format(g_vars['lag_id']), module_ignore_errors=True)
        assert g_vars['mac1'] in res['stdout']

        # verify mac1 exist on lag by traffic
        ptf_runner( 
                    ptfhost, 
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac2'],
                            "dst_mac": g_vars['mac1'],
                            "src_port": g_vars['port3']['ptf_peer'],
                            "dst_port_list": [g_vars['port2']['ptf_peer']],
                            },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    def test_check_member1_status(self, duthost):
        res = False
        for i in range(0, 12):
            status = duthost.shell("teamdctl PortChannel{} state item get ports.{}.runner.selected".format(g_vars['lag_id'], g_vars['port1']['port_name']))['stdout']
            if status == 'true':
                res = True
                break
            else:
                time.sleep(5)
        assert res, "{} should be selected after startup".format(g_vars['port1']['port_name'])

    def test_shutdown_member2(self, duthost, ptfhost, mg_facts, setup_shutdown_member2):
        # shutdown port2
        duthost.shell("config interface shutdown {}".format(g_vars['port2']['port_name']))

        # verify mac1 still exist on lag
        res = duthost.shell("show mac|grep PortChannel{}".format(g_vars['lag_id']), module_ignore_errors=True)
        assert g_vars['mac1'] in res['stdout']

        # verify mac1 exist on lag by traffic
        ptf_runner( 
                    ptfhost, 
                    "ptftests",
                    "l2_fdb.L2FdbTest",
                    platform_dir="ptftests",
                    params={"src_mac": g_vars['mac2'],
                            "dst_mac": g_vars['mac1'],
                            "src_port": g_vars['port3']['ptf_peer'],
                            "dst_port_list": [g_vars['port1']['ptf_peer']],
                            },
                    log_file="/tmp/l2_fdb_[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    def test_check_member2_status(self, duthost):
        res = False
        for i in range(0, 12):
            status = duthost.shell("teamdctl PortChannel{} state item get ports.{}.runner.selected".format(g_vars['lag_id'], g_vars['port2']['port_name']))['stdout']
            if status == 'true':
                res = True
                break
            else:
                time.sleep(5)
        assert res, "{} should be selected after startup".format(g_vars['port2']['port_name'])

