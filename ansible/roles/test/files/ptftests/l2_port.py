import logging
import ptf
import ptf.dataplane as dataplane
from ptf.base_tests import BaseTest
from ptf.testutils import *

class L2PortTest(BaseTest):
    def __init__(self):
        BaseTest.__init__(self)
        self.test_params = test_params_get()

    #--------------------------------------------------------------------------
    def setUp(self):
        self.dataplane = ptf.dataplane_instance
        self.src_port = int(self.test_params["src_port"])
        self.dst_port = int(self.test_params["dst_port"])
        self.except_receive = self.test_params["except_receive"]
        self.pkt_num = int(self.test_params["pkt_num"])
        self.pkt_len = int(self.test_params["pkt_len"])

    # --------------------------------------------------------------------------
    def verify_packet_receive(self, src_port, dst_port):
        pkt = simple_ip_packet(pktlen=self.pkt_len,
                                eth_dst='ff:ff:ff:ff:ff:ff',
                                eth_src=self.dataplane.get_mac(0, src_port),
                                ip_src='10.0.0.1',
                                ip_dst='192.168.0.2'
                                )
        send_packet(self, src_port, pkt, count=self.pkt_num)

        if self.except_receive:
            return verify_packet(self, pkt, dst_port)
        else:
            return verify_no_packet(self, pkt, dst_port)

    #-----------------------------------------------------------------------------
    def runTest(self):
        logging.info("Except receive %s!" % self.except_receive)
        logging.info("Send broadcast packets from %s to %s" % (str(self.src_port), str(self.dst_port)))
        self.verify_packet_receive(self.src_port, self.dst_port)
