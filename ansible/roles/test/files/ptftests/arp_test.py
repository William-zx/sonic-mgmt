'''
Description:    This file contains the arp test for SONiC testbed

                Implemented according to the <SONiC_ARP_TestPlan.md>

Usage:          Examples of how to use:
                ptf --test-dir ptftests arp_test.ArpTest -t 'router_mac="00:02:03:04:05:00";verbose=True;src_port=0;dst_port=1;dst_ip_addr_list=["1.1.1.1","2.2.2.2"]'
'''

#---------------------------------------------------------------------
# Global imports
#---------------------------------------------------------------------
import logging
import ast
import ptf
from ptf.base_tests import BaseTest
import ptf.testutils as testutils
import ipaddress
import pprint

class ArpTest(BaseTest):
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
        logging.info("### Start Arp extend test ###")
        self.dataplane = ptf.dataplane_instance
        self.router_mac = self.test_params['router_mac']
        self.src_port = self.test_params['src_port']
        self.dst_port = self.test_params['dst_port']
        self.expected_dst_mac = self.test_params.get('expected_dst_mac', None)
        self.dst_ip_addr_list = self.test_params['dst_ip_addr_list']
        self.unexpected_ip_addr_list = self.test_params.get('unexpected_ip_addr_list', '[]')

    #---------------------------------------------------------------------

    def checkPacketSendReceive(self, src_port, dst_port, dst_ip_addr, expect_passed=True, count=1):
        src_port_mac = self.dataplane.get_mac(0, src_port)
        dst_port_mac = self.expected_dst_mac or self.dataplane.get_mac(0, dst_port)
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

        for dst_ip_addr in self.dst_ip_addr_list:
            expect_passed = True if dst_ip_addr not in self.unexpected_ip_addr_list else False
            logging.info("Expect received? %s" % str(expect_passed))
            res = self.checkPacketSendReceive(self.src_port, self.dst_port, dst_ip_addr, expect_passed)            
            if res:
                tests_passed +=1
        logging.info("Total tests: {}, Faild: {}".format(tests_total, tests_total - tests_passed))
        assert(tests_passed == tests_total)
