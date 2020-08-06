import pytest
import time
import sys
import random

from ptf_runner import ptf_runner

# vars
g_vars          = {}
test_vlan_id    = 2345
test_mtu        = 8000
pkt_num         = 2000
socket_size     = 16384

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

    g_vars["dut_ports"] = mg_facts["minigraph_vlans"].values()[0]["members"]
    g_vars["dut_test_ports"] = random.sample(g_vars["dut_ports"], 4)
    g_vars["vlan_id"] = mg_facts['minigraph_vlans'].values()[0]['vlanid']

    # init dut port status, change test port vlan, aviod packets statistics mismatch.
    duthost.shell("config vlan add {}".format(test_vlan_id))
    duthost.shell("ip addr flush Vlan{}".format(test_vlan_id))
    for port in g_vars["dut_test_ports"]:
        duthost.shell("config vlan member del {} {}".format(g_vars["vlan_id"], port))
        duthost.shell("config vlan member add {} {} -u".format(test_vlan_id, port))
    time.sleep(5)

    # copy ptftest script
    ptfhost.copy(src="ptftests", dest="/root")
    ptfhost.shell("mkdir -p /tmp/l2_port")
    ptfhost.script("scripts/remove_ip.sh")
    ptfhost.script("scripts/change_mac.sh")

    yield

    # recover dut port status
    for port in g_vars["dut_test_ports"]:
        duthost.shell("config vlan member del {} {}".format(test_vlan_id, port))
        duthost.shell("config vlan member add {} {} -u".format(g_vars["vlan_id"], port))
    duthost.shell("config vlan del {}".format(test_vlan_id))
    time.sleep(5)

@pytest.fixture(scope="function")
def traffic_check(request, mg_facts, ptfhost):
    traffic_reverse = request.param.get("traffic_reverse", False)
    except_receive = request.param.get("except_receive", True)
    socket_recv_size = request.param.get("socket_recv_size", None)
    pkt_num = request.param.get("pkt_num", 1)
    pkt_len = request.param.get("pkt_len", 100)
    for port in g_vars["dut_test_ports"][:-1]:
        params = {
                    "host": ptfhost,
                    "testdir": "ptftests",
                    "testname": "l2_port.L2PortTest",
                    "platform_dir": "ptftests",
                    "params": {
                            "src_port": mg_facts["minigraph_port_indices"][port] if not traffic_reverse else mg_facts["minigraph_port_indices"][g_vars["dut_test_ports"][-1]],
                            "dst_port": mg_facts["minigraph_port_indices"][g_vars["dut_test_ports"][-1]] if not traffic_reverse else mg_facts["minigraph_port_indices"][port],
                            "except_receive": except_receive,
                            "pkt_num": pkt_num,
                            "pkt_len": pkt_len
                    },
                    "socket_recv_size": socket_recv_size,
                    "log_file": "/tmp/l2_port/[{}]_[{}]_{}_[reverse_{}].log".format(request.instance.__class__.__name__, sys._getframe().f_code.co_name, port, traffic_reverse)
        }
        if not socket_recv_size:
            params.pop("socket_recv_size")
        ptf_runner(**params)

@pytest.fixture(scope="function")
def updown_port_by_json(request, duthost):
    admin_status = request.param["admin_status"]
    for port in g_vars["dut_test_ports"][:-1]:
        # setup port on dut
        dut_extra_vars = {
            "port"          : port,
            "admin_status"  : admin_status
        }
        duthost.host.options["variable_manager"].extra_vars = dut_extra_vars
        duthost.template(src="l2_port/l2_port_admin_updown_config.j2", dest="/tmp/{}_{}.conf".format(port, admin_status))
        duthost.shell("config load -y /tmp/{}_{}.conf".format(port, admin_status))
        time.sleep(5)

@pytest.fixture(scope="function")
def updown_port_by_cli(request, duthost):
    admin_status = request.param["admin_status"]
    for port in g_vars["dut_test_ports"][:-1]:
        duthost.shell("config interface {} {}".format(admin_status, port))
        time.sleep(5)

@pytest.fixture(scope="function")
def updown_port_on_fanout(request, fanouthost):
    pass
    # # fanouthost not support on pytest
    # admin_status = request.param["admin_status"]
    # for port in g_vars["dut_test_ports"][:-1]:
    #     fanouthost.shell("config interface {} {}".format(admin_status, port))
    #     time.sleep(5)

@pytest.fixture(scope="class")
def setup_port_mtu(duthost):
    ports_default_mtu       = {}
    for port in g_vars["dut_test_ports"][:-1]:
        # get default aging_time
        port_default_mtu = duthost.shell("redis-cli -n 0 hget PORT_TABLE:{} mtu".format(port))['stdout']
        ports_default_mtu.update({port: port_default_mtu})
        # setup port on dut
        dut_extra_vars = {
            "port"          : port,
            "mtu"           : test_mtu
        }
        duthost.host.options["variable_manager"].extra_vars = dut_extra_vars
        duthost.template(src="l2_port/l2_port_mtu_config.j2", dest="/tmp/{}_{}.conf".format(port, test_mtu))
        duthost.shell("config load -y /tmp/{}_{}.conf".format(port, test_mtu))
        time.sleep(5)
    
    yield

    for port in g_vars["dut_test_ports"][:-1]:
        # setup port on dut
        dut_extra_vars = {
            "port"          : port,
            "mtu"           : ports_default_mtu[port]
        }
        duthost.host.options["variable_manager"].extra_vars = dut_extra_vars
        duthost.template(src="l2_port/l2_port_mtu_config.j2", dest="/tmp/{}_{}.conf".format(port, ports_default_mtu[port]))
        duthost.shell("config load -y /tmp/{}_{}.conf".format(port, ports_default_mtu[port]))
        time.sleep(5)

@pytest.fixture(scope="class", autouse=True)
def clear_mac(duthost):
    # setup 
    duthost.shell("sonic-clear fdb all")
    time.sleep(2)

    yield

    # clear mac-table after every case finished
    duthost.shell("sonic-clear fdb all")
    time.sleep(2)

@pytest.fixture(scope="class")
def clear_statistics(duthost):
    duthost.shell("portstat -c")
    time.sleep(2)

    yield

    duthost.shell("portstat -c")
    time.sleep(2)

# lldp packet will cause packet counter mismatch, so stop lldp
@pytest.fixture(scope="class")
def lldp_server_change(duthost):
    duthost.shell("service lldp stop")
    time.sleep(5)

    yield

    duthost.shell("service lldp start")
    time.sleep(5)

class TestCase1_AdminUpDownByJson():
    @pytest.mark.parametrize("updown_port_by_json", [{"admin_status": "down"}], indirect=True)
    def test_port_down(self, duthost, updown_port_by_json):
        for port in g_vars["dut_test_ports"][:-1]:
            Oper_status, Admin_status = duthost.shell("show interfaces status |grep '%s ' |awk '{print $7,$8}'"%port)["stdout"].split()
            assert Oper_status == Admin_status == "down", "Port Oper and Admin status should be down"

    @pytest.mark.parametrize("traffic_check", [{"except_receive": False}], indirect=True)
    def test_drop_input_traffic_after_port_down(self, traffic_check):
        pass

    @pytest.mark.parametrize("traffic_check", [{"traffic_reverse": True, "except_receive": False}], indirect=True)
    def test_drop_output_traffic_after_port_down(self, traffic_check):
        pass

    @pytest.mark.parametrize("updown_port_by_json", [{"admin_status": "up"}], indirect=True)
    def test_port_up(self, duthost, updown_port_by_json):
        for port in g_vars["dut_test_ports"][:-1]:
            Oper_status, Admin_status = duthost.shell("show interfaces status |grep '%s ' |awk '{print $7,$8}'"%port)["stdout"].split()
            assert Oper_status == Admin_status == "up", "Port Oper and Admin status should be up"

    @pytest.mark.parametrize("traffic_check", [{"except_receive": True}], indirect=True)
    def test_forward_input_traffic_after_port_up(self, traffic_check):
        pass

    @pytest.mark.parametrize("traffic_check", [{"traffic_reverse": True, "except_receive": True}], indirect=True)
    def test_forward_output_traffic_after_port_up(self, traffic_check):
        pass

class TestCase2_AdminUpDownByCli():
    @pytest.mark.parametrize("updown_port_by_cli", [{"admin_status": "shutdown"}], indirect=True)
    def test_port_down(self, duthost, updown_port_by_cli):
        for port in g_vars["dut_test_ports"][:-1]:
            Oper_status, Admin_status = duthost.shell("show interfaces status |grep '%s ' |awk '{print $7,$8}'"%port)["stdout"].split()
            assert Oper_status == Admin_status == "down", "Port Oper and Admin status should be down"

    @pytest.mark.parametrize("traffic_check", [{"except_receive": False}], indirect=True)
    def test_drop_input_traffic_after_port_down(self, traffic_check):
        pass

    @pytest.mark.parametrize("traffic_check", [{"traffic_reverse": True, "except_receive": False}], indirect=True)
    def test_drop_output_traffic_after_port_down(self, traffic_check):
        pass

    @pytest.mark.parametrize("updown_port_by_cli", [{"admin_status": "startup"}], indirect=True)
    def test_port_up(self, duthost, updown_port_by_cli):
        for port in g_vars["dut_test_ports"][:-1]:
            Oper_status, Admin_status = duthost.shell("show interfaces status |grep '%s ' |awk '{print $7,$8}'"%port)["stdout"].split()
            assert Oper_status == Admin_status == "up", "Port Oper and Admin status should be up"

    @pytest.mark.parametrize("traffic_check", [{"except_receive": True}], indirect=True)
    def test_forward_input_traffic_after_port_up(self, traffic_check):
        pass

    @pytest.mark.parametrize("traffic_check", [{"traffic_reverse": True, "except_receive": True}], indirect=True)
    def test_forward_output_traffic_after_port_up(self, traffic_check):
        pass

@pytest.mark.skip(reason="Not support fanout control on pytest")
class TestCase3_OperUpDown():
    @pytest.mark.parametrize("updown_port_on_fanout", [{"admin_status": "shutdown"}], indirect=True)
    def test_port_down(self, duthost, updown_port_on_fanout):
        for port in g_vars["dut_test_ports"][:-1]:
            Oper_status, Admin_status = duthost.shell("show interfaces status |grep '%s ' |awk '{print $7,$8}'"%port)["stdout"].split()
            assert Oper_status == "down", "Port Oper status should be down"
            assert Admin_status == "up", "Port Admin status should be up"

    @pytest.mark.parametrize("traffic_check", [{"except_receive": False}], indirect=True)
    def test_drop_input_traffic_after_port_down(self, traffic_check):
        pass

    @pytest.mark.parametrize("traffic_check", [{"traffic_reverse": True, "except_receive": False}], indirect=True)
    def test_drop_output_traffic_after_port_down(self, traffic_check):
        pass

    @pytest.mark.parametrize("updown_port_on_fanout", [{"admin_status": "no shutdown"}], indirect=True)
    def test_port_up(self, duthost, updown_port_on_fanout):
        for port in g_vars["dut_test_ports"][:-1]:
            Oper_status, Admin_status = duthost.shell("show interfaces status |grep '%s ' |awk '{print $7,$8}'"%port)["stdout"].split()
            assert Oper_status == "up", "Port Oper status should be up"
            assert Admin_status == "up", "Port Admin status should be up"

    @pytest.mark.parametrize("traffic_check", [{"except_receive": True}], indirect=True)
    def test_forward_input_traffic_after_port_up(self, traffic_check):
        pass

    @pytest.mark.parametrize("traffic_check", [{"traffic_reverse": True, "except_receive": True}], indirect=True)
    def test_forward_output_traffic_after_port_up(self, traffic_check):
        pass

@pytest.mark.usefixtures("setup_port_mtu")
class TestCase4_ConfigMtu():
    def test_port_mtu(self, duthost):
        for port in g_vars["dut_test_ports"][:-1]:
            port_mtu = duthost.shell("show interfaces status |grep '%s ' |awk '{print $4}'"%port)["stdout"]
            assert port_mtu == str(test_mtu), "Port mtu should be the configuration value {}".format(test_mtu)

    @pytest.mark.parametrize("traffic_check", [{"pkt_len": test_mtu+14, "socket_recv_size": socket_size}], indirect=True)
    def test_forward_input_packet_length_less_than_port_mtu(self, traffic_check):
        pass

    @pytest.mark.parametrize("traffic_check", [{"traffic_reverse": True, "pkt_len": test_mtu+14, "socket_recv_size": socket_size}], indirect=True)
    def test_forward_output_packet_length_less_than_port_mtu(self, traffic_check):
        pass

    @pytest.mark.parametrize("traffic_check", [{"except_receive": False, "pkt_len": test_mtu+20, "socket_recv_size": socket_size}], indirect=True)
    def test_drop_input_packet_length_greater_than_port_mtu(self, traffic_check):
        pass

    @pytest.mark.skip(reason="Taurus chip only log error, do not drop the packets")
    @pytest.mark.parametrize("traffic_check", [{"traffic_reverse": True, "except_receive": False, "pkt_len": test_mtu+20, "socket_recv_size": socket_size}], indirect=True)
    def test_drop_output_packet_length_greater_than_port_mtu(self, traffic_check):
        pass

@pytest.mark.usefixtures("clear_statistics")
class TestCase5_RXCounter():
    @pytest.mark.parametrize("traffic_check", [{"pkt_num": pkt_num}], indirect=True)
    def test_port_rx_counter(self, duthost, traffic_check):
        for port in g_vars["dut_test_ports"][:-1]:
            rx_counter = duthost.shell("show interfaces counter |sed 's;B/s;;' |grep '%s ' |awk '{print $3}'"%port)["stdout"]
            assert rx_counter == format(pkt_num, ','), "The RX counter does not equal the number of packets sent"

@pytest.mark.usefixtures("clear_statistics")
@pytest.mark.usefixtures("lldp_server_change")
class TestCase6_TXCounter():
    def test_traffic_forward(self, ptfhost, mg_facts):
        ptf_runner( 
                    ptfhost,
                    "ptftests",
                    "l2_port.L2PortTest",
                    platform_dir="ptftests",
                    params={
                        "src_port": mg_facts["minigraph_port_indices"][g_vars["dut_test_ports"][-1]],
                        "dst_port": mg_facts["minigraph_port_indices"][g_vars["dut_test_ports"][0]],
                        "except_receive": True,
                        "pkt_num": pkt_num,
                        "pkt_len": 100
                    },
                    log_file="/tmp/l2_port/[{}]_[{}].log".format(self.__class__.__name__, sys._getframe().f_code.co_name)
        )

    def test_port_tx_counter(self, duthost):
        for port in g_vars["dut_test_ports"][:-1]:
            tx_counter = duthost.shell("show interfaces counter |sed 's;B/s;;' |grep '%s ' |awk '{print $9}'"%port)["stdout"]
            assert tx_counter == format(pkt_num, ','), "The TX counter does not equal the number of packets sent"

@pytest.mark.usefixtures("clear_statistics")
@pytest.mark.usefixtures("setup_port_mtu")
class TestCase7_RXOversizeCounter():
    @pytest.mark.parametrize("traffic_check", [{"except_receive": False, "pkt_len": test_mtu+20, "pkt_num": pkt_num, "socket_recv_size": socket_size}], indirect=True)
    def test_port_rx_oversize_counter(self, duthost, traffic_check):
        for port in g_vars["dut_test_ports"][:-1]:
            rx_oversize_counter = duthost.shell("show interfaces counter |sed 's;B/s;;' |grep '%s ' |awk '{print $8}'"%port)["stdout"]
            assert rx_oversize_counter == format(pkt_num, ','), "The RX oversize counter does not equal the number of packets sent"
