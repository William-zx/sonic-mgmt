'''
Description:    This file contains the router interface test for SONiC testbed

                Implemented according to the <SONiC_RIF_TestPlan.md>

Usage:          Examples of how to use:
                ptf --test-dir ptftests router_if_test.RifTest -t 'router_mac="00:02:03:04:05:00";verbose=True;src_port=0;dst_port=1;dst_ip_addr_list=["1.1.1.1","2.2.2.2"]'
'''

#---------------------------------------------------------------------
# Global imports
#---------------------------------------------------------------------
import logging
from ptf.testutils import *
import ast
import ptf
from ptf.base_tests import BaseTest
import ptf.testutils as testutils
import ipaddress
import pprint
import time

class RifTest(BaseTest):
    '''
    @summary: Router if tests on testbed topo: t0
    '''

    #---------------------------------------------------------------------
    # Class variables
    #---------------------------------------------------------------------

    def __init__(self):
        '''
        @summary: constructor
        '''
        BaseTest.__init__(self)
        self.test_params = testutils.test_params_get()
    #---------------------------------------------------------------------

    def setUp(self):
        '''
        @summary: Setup for the test
        '''
        ptf.open_logfile(str(self))
        logging.info("### Start Router interface test ###")
        self.dataplane = ptf.dataplane_instance
        self.router_mac = self.test_params['router_mac']
        self.src_port = self.test_params['src_port']
        self.dst_port = self.test_params['dst_port']
        self.dst_ip_addr_list = self.test_params['dst_ip_addr_list']
        self.unexpected_ip_addr_list = self.test_params.get('unexpected_ip_addr_list', '[]')
        self.mac_neigh_learn = self.test_params.get('mac_neigh_learn', True)
    #---------------------------------------------------------------------

    def mac_arp_learn(self, port, ip_addr):
        """
        @param port: index of port to use for sending packet to switch.
        @param ip_addr: destination IP to build packet with.
        The MAC address will learn from this packet, and the device will send an arp packet requesting the IP address
        """
        ip_src = ip_addr
        ip_dst = ip_addr
        src_mac = self.dataplane.get_mac(0, port)

        if ipaddress.ip_address(unicode(ip_addr)).version == 4:  # is ipv4
            pkt = simple_icmp_packet(
                                eth_dst=self.router_mac,
                                eth_src=src_mac,
                                ip_src=ip_src,
                                ip_dst=ip_dst)
        else:
            pkt = simple_icmpv6_packet(
                eth_dst=self.router_mac,
                eth_src=src_mac,
                ipv6_src=ip_src,
                ipv6_dst=ip_dst)

        send_packet(self, port, pkt)
        logging.info("Sending learning packet from port " + str(port) + " to " + ip_addr)

    def checkPacketSendReceive(self, src_port, dst_port, dst_ip_addr, expect_passed=True, count=1):
        src_port_mac = self.dataplane.get_mac(0, src_port)
        dst_port_mac = self.dataplane.get_mac(0, dst_port)
        if ipaddress.ip_address(unicode(dst_ip_addr)).version == 4: # is ipv4
            pkt,exp_pkt = self.create_pkt(src_port_mac, dst_port_mac, dst_ip_addr)
        else:
            pkt,exp_pkt = self.create_pkt6(src_port_mac, dst_port_mac, dst_ip_addr)
        testutils.send_packet(self, src_port, pkt, count=count)
        logging.info("Sent {} pkts from port{}: DIP-{}".format(count, src_port, dst_ip_addr))
        rcv_cnt = testutils.count_matched_packets(self, exp_pkt, dst_port, timeout=1)
        logging.info("Received {} expected pkts from port {}".format(rcv_cnt, dst_port))
        test_result = True
        if rcv_cnt != count:
            test_result = False
        return test_result if expect_passed else not test_result
    #---------------------------------------------------------------------

    def create_pkt(self, src_port_mac, dst_port_mac, dst_ip):
        pkt = testutils.simple_icmp_packet(
                                eth_dst = self.router_mac,
                                eth_src = src_port_mac,
                                ip_src = "10.0.0.1",
                                ip_dst = dst_ip,
                                icmp_type=8,
                                icmp_code=0,
                                ip_ttl = 64
                            )
        exp_pkt = testutils.simple_icmp_packet(
                                eth_dst = dst_port_mac,
                                eth_src = self.router_mac,
                                ip_src = "10.0.0.1",
                                ip_dst = dst_ip,
                                icmp_type=8,
                                icmp_code=0,
                                ip_ttl = 63
                            )
        return (pkt,exp_pkt)
    
    def create_pkt6(self, src_port_mac, dst_port_mac, dst_ip):
        pkt = testutils.simple_tcpv6_packet(
                                eth_dst = self.router_mac,
                                eth_src = src_port_mac,
                                ipv6_src = "3ffe:1::1",
                                ipv6_dst = dst_ip,
                                ipv6_hlim=64
                            )
        exp_pkt = testutils.simple_tcpv6_packet(
                                eth_dst = dst_port_mac,
                                eth_src = self.router_mac,
                                ipv6_src = "3ffe:1::1",
                                ipv6_dst = dst_ip,
                                ipv6_hlim=63
                            )
        return (pkt,exp_pkt)
    #---------------------------------------------------------------------

    def runTest(self):
        """
        @summary: Create and send packet to verify each IP address
        """

        tests_passed = 0
        tests_total = len(self.dst_ip_addr_list)

        # Skip the MAC learning process and do not verify software forwarding
        if self.mac_neigh_learn:
            for dst_ip_addr in self.dst_ip_addr_list:
                dst_ip_addr = dst_ip_addr.split("/")[0]
                self.mac_arp_learn(self.dst_port, dst_ip_addr)
            time.sleep(2)

        for dst_ip_addr in self.dst_ip_addr_list:
            dst_ip_addr = dst_ip_addr.split("/")[0]
            expect_passed = True if dst_ip_addr not in self.unexpected_ip_addr_list else False
            logging.info("Expect received? %s" % str(expect_passed))
            res = self.checkPacketSendReceive(self.src_port, self.dst_port, dst_ip_addr, expect_passed)            
            if res:
                tests_passed +=1
        logging.info("Total tests: {}, Faild: {}".format(tests_total, tests_total - tests_passed))
        assert(tests_passed == tests_total)
