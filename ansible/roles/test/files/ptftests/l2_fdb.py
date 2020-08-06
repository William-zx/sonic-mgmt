import ast
import json
import logging
import subprocess

from collections import defaultdict
from ipaddress import ip_address, ip_network

import ptf
import ptf.packet as scapy
import ptf.dataplane as dataplane

from ptf import config
from ptf.base_tests import BaseTest
from ptf.testutils import *
from ptf.mask import Mask

class L2FdbTest(BaseTest):
    def __init__(self):
        BaseTest.__init__(self)
        self.test_params = test_params_get()

    #--------------------------------------------------------------------------
    def setUp(self):
        self.dataplane = ptf.dataplane_instance
        self.src_mac = self.test_params["src_mac"]
        self.dst_mac = self.test_params["dst_mac"]
        self.src_port = self.test_params["src_port"]
        self.dst_port_list = self.test_params["dst_port_list"]
        self.lag = self.test_params.get("dst_port_lag", None)
        self.pkt_num = self.test_params.get("pkt_num", 1)

    # --------------------------------------------------------------------------
    def verify_packet_receive(self, src_port, dst_port_list):
        src_mac = self.src_mac
        dst_mac = self.dst_mac
        pkt = simple_ip_packet(eth_dst=dst_mac,
                                eth_src=src_mac,
                                ip_src='10.0.0.1',
                                ip_dst='192.168.0.2'
                                )
        send_packet(self, src_port, pkt, count=self.pkt_num)

        if self.lag:
            return verify_packets_any(self, pkt, dst_port_list)
        else:
            return verify_packets(self, pkt, dst_port_list)

    #-----------------------------------------------------------------------------
    def runTest(self):
        logging.info("src_mac: {}".format(self.src_mac))
        logging.info("dst_mac: {}".format(self.dst_mac))
        logging.info("Send packets from {} to {}".format(self.src_port, str(self.dst_port_list)))
        self.verify_packet_receive(self.src_port, self.dst_port_list)
