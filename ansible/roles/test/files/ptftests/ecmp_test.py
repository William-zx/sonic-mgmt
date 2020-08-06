'''
Description:    This file contains the ECMP test for SONIC

                Design is available in https://github.com/Azure/SONiC/wiki/ECMP-Scale-Test-Plan

Usage:          Examples of how to use log analyzer
                ptf --test-dir ecmp ecmp_test.EcmpTest  --platform remote -t 'router_mac="00:02:03:04:05:00";route_info="ecmp/route_info.txt";testbed_type=t1'
'''

#---------------------------------------------------------------------
# Global imports
#---------------------------------------------------------------------
import logging
import random
import socket

import ptf
import ptf.packet as scapy
import ptf.dataplane as dataplane

from ptf.base_tests import BaseTest
from ptf.mask import Mask
from ptf.testutils import *
from ipaddress import ip_network

class EcmpTest(BaseTest):
    '''
    @summary: Router if tests on testbed topo: t0
    '''

    #---------------------------------------------------------------------
    # Class variables
    #---------------------------------------------------------------------
    DEFAULT_BALANCING_RANGE = 0.50
    BALANCING_TEST_TIMES = 1000
    # The next header field of an ipv6 packet will be dropped when populated with the following values. Who knows!!!
    proto_v6_exclude = [0, 43, 51, 60]

    def __init__(self):
        '''
        @summary: constructor
        '''
        BaseTest.__init__(self)
        self.test_params = test_params_get()

    #---------------------------------------------------------------------
    def setUp(self):
        '''
        @summary: Setup for the test
        '''

        logging.info("### Start ecmp test ###")

        self.dataplane = ptf.dataplane_instance     
        self.router_mac = self.test_params['router_mac']
        self.src_port = self.test_params['src_port']
        self.hash_keys = self.test_params.get('hash_keys', ["src-ip"])
        self.dst_route_list = self.test_params['dst_route_list']
        self.dst_port_list = self.test_params['dst_port_list']
        self.balancing_range = float(self.test_params.get('balancing_range', self.DEFAULT_BALANCING_RANGE))

    #---------------------------------------------------------------------
    def check_ip_range(self, hash_key, src_port, dst_route, dst_port_list):
        # Test traffic balancing across ECMP/LAG members
        if len(dst_port_list) > 1:
            logging.info("Check Port range balancing, hash_key: " + hash_key)
            hit_count_map = {}
            for _ in xrange(self.BALANCING_TEST_TIMES):
                matched_port, _ = self.check_ip_route(hash_key, src_port, dst_route, dst_port_list)
                hit_count_map[matched_port] = hit_count_map.get(matched_port, 0) + 1
            self.check_balancing(dst_port_list, hit_count_map)
        else:
            self.check_ip_route(hash_key, src_port, dst_route, dst_port_list)

    #---------------------------------------------------------------------
    def check_ip_route(self, hash_key, src_port, dst_route, dst_port_list):
        if ip_network(unicode(dst_route)).version == 4:
            (matched_index, received) = self.check_ipv4_route(hash_key, src_port, dst_route, dst_port_list)
        else:
            (matched_index, received) = self.check_ipv6_route(hash_key, src_port, dst_route, dst_port_list)
        
        matched_port = dst_port_list[matched_index]
        logging.info("Received packet at " + str(matched_port))

        return (matched_port, received)

    #---------------------------------------------------------------------
    def check_ipv4_route(self, hash_key, src_port, dst_route, dst_port_list):
        '''
        @summary: Check IPv4 route works.
        @param src_port: index of port to use for sending packet to switch
        @param dest_ip_addr: destination IP to build packet with.
        @param dst_port_list: list of ports on which to expect packet to come back from the switch
        '''

        src_port_mac = self.dataplane.get_mac(0, src_port)
        eth_src = (src_port_mac[:-2] + "%02x" % random.randint(1, 100)) if hash_key == "src-mac" else src_port_mac
        sport = random.randint(0, 65535) if hash_key == "src-port" else 1234
        dport = random.randint(0, 65535) if hash_key == "dst-port" else 80
        ip_src = "10.0.0.{}".format(random.randint(1, 100)) if hash_key == "src-ip" else "10.0.0.1"
        ip_dst = str(ip_network(unicode(dst_route))[1] + (random.randint(0, ip_network(unicode(dst_route)).num_addresses-3) if hash_key == "dst-ip" else 0))
        inner_src_ip = "20.0.0.{}".format(random.randint(1, 100)) if hash_key == "inner-src-ip" else "20.0.0.1"
        inner_dst_ip = "20.1.0.{}".format(random.randint(1, 100)) if hash_key == "inner-dst-ip" else "20.1.0.1"
        ip_proto = random.randint(0, 255) if hash_key == "ip-proto" else None

        if hash_key == "inner-src-ip" or hash_key == "inner-dst-ip":
            inner_pkt = simple_tcp_packet(ip_src=inner_src_ip,
                                        ip_dst=inner_dst_ip,
                                        ip_tos=100,
                                        ip_ttl=64) # get only the IP layer
            pkt = simple_ipv4ip_packet(
                                    eth_dst=self.router_mac,
                                    eth_src=eth_src,
                                    ip_src=ip_src,
                                    ip_dst=ip_dst,
                                    ip_tos=100,
                                    ip_ttl=64,
                                    inner_frame=inner_pkt)

            exp_pkt = simple_ipv4ip_packet(                                
                                    eth_src=self.router_mac,
                                    ip_src=ip_src,
                                    ip_dst=ip_dst,
                                    ip_tos=100,
                                    ip_ttl=63,
                                    inner_frame=inner_pkt)
        else:
            pkt = simple_tcp_packet(
                            eth_dst=self.router_mac,
                            eth_src=eth_src,
                            ip_src=ip_src,
                            ip_dst=ip_dst,
                            tcp_sport=sport,
                            tcp_dport=dport,
                            ip_ttl=64)

            exp_pkt = simple_tcp_packet(
                            eth_src=self.router_mac,
                            ip_src=ip_src,
                            ip_dst=ip_dst,
                            tcp_sport=sport,
                            tcp_dport=dport,
                            ip_ttl=63)

        if hash_key == "ip-proto":
            pkt['IP'].proto = ip_proto
            exp_pkt['IP'].proto = ip_proto

        masked_exp_pkt = Mask(exp_pkt)
        masked_exp_pkt.set_do_not_care_scapy(scapy.Ether, "dst")

        send_packet(self, src_port, pkt)
        logging.info("Sending packet from port " + str(src_port) + " to " + ip_dst)
        # logging.info("ip_src: " + str(ip_src))
        # logging.info("ip_dst: " + str(ip_dst))
        # logging.info("inner_src_ip: " + str(inner_src_ip))
        # logging.info("inner_dst_ip: " + str(inner_dst_ip))
        # logging.info("sport: " + str(sport))
        # logging.info("dport: " + str(dport))
        # logging.info("ip_proto: " + str(ip_proto))

        return verify_packet_any_port(self, masked_exp_pkt, dst_port_list)

    #---------------------------------------------------------------------
    def check_ipv6_route(self, hash_key, src_port, dst_route, dst_port_list):
        '''
        @summary: Check IPv6 route works.
        @param source_port_index: index of port to use for sending packet to switch
        @param dest_ip_addr: destination IP to build packet with.
        @param dst_port_list: list of ports on which to expect packet to come back from the switch
        @return Boolean
        '''

        src_port_mac = self.dataplane.get_mac(0, src_port)
        eth_src = (src_port_mac[:-2] + "%02x" % random.randint(1, 100)) if hash_key == "src-mac" else src_port_mac
        sport = random.randint(0, 65535) if hash_key == "src-port" else 1234
        dport = random.randint(0, 65535) if hash_key == "dst-port" else 80
        ip_src = "2000::{}".format(random.randint(1, 100)) if hash_key == "src-ip" else '2000::1'
        ip_dst = str(ip_network(unicode(dst_route))[1] + (random.randint(0, ip_network(unicode(dst_route)).num_addresses-3) if hash_key == "dst-ip" else 0))
        inner_src_ip = "2000::{}".format(random.randint(1, 100)) if hash_key == "inner-src-ip" else "2000::1"
        inner_dst_ip = "2010::{}".format(random.randint(1, 100)) if hash_key == "inner-dst-ip" else "2010::1"
        ip_proto = None
        if hash_key == "ip-proto":
            while True:
                ip_proto = random.randint(0, 255)
                if ip_proto not in self.proto_v6_exclude:
                    break

        if hash_key == "inner-src-ip" or hash_key == "inner-dst-ip":
            inner_pkt = simple_tcpv6_packet(ipv6_src=inner_src_ip,
                                            ipv6_dst=inner_dst_ip,
                                            ipv6_hlim=64) # get only the IP layer
            pkt = simple_ipv6ip_packet(
                                    eth_dst=self.router_mac,
                                    eth_src=eth_src,
                                    ipv6_src=ip_src,
                                    ipv6_dst=ip_dst,
                                    ipv6_hlim=64,
                                    inner_frame=inner_pkt)

            exp_pkt = simple_ipv6ip_packet(                                
                                    eth_src=self.router_mac,
                                    ipv6_src=ip_src,
                                    ipv6_dst=ip_dst,
                                    ipv6_hlim=63,
                                    inner_frame=inner_pkt)
        else:
            pkt = simple_tcpv6_packet(
                                eth_dst=self.router_mac,
                                eth_src=eth_src,
                                ipv6_dst=ip_dst,
                                ipv6_src=ip_src,
                                tcp_sport=sport,
                                tcp_dport=dport,
                                ipv6_hlim=64)

            exp_pkt = simple_tcpv6_packet(
                                eth_src=self.router_mac,
                                ipv6_dst=ip_dst,
                                ipv6_src=ip_src,
                                tcp_sport=sport,
                                tcp_dport=dport,
                                ipv6_hlim=63)

        if hash_key == "ip-proto":
            pkt['IPv6'].nh = ip_proto
            exp_pkt['IPv6'].nh = ip_proto

        masked_exp_pkt = Mask(exp_pkt)
        masked_exp_pkt.set_do_not_care_scapy(scapy.Ether, "dst")

        send_packet(self, src_port, pkt)
        logging.info("Sending packet from port " + str(src_port) + " to " + ip_dst)
        # logging.info("ip_src: " + str(ip_src))
        # logging.info("ip_dst: " + str(ip_dst))
        # logging.info("inner_src_ip: " + str(inner_src_ip))
        # logging.info("inner_dst_ip: " + str(inner_dst_ip))
        # logging.info("sport: " + str(sport))
        # logging.info("dport: " + str(dport))
        logging.info("ip_proto: " + str(ip_proto))

        return verify_packet_any_port(self, masked_exp_pkt, dst_port_list)

    #---------------------------------------------------------------------
    def check_within_expected_range(self, actual, expected):
        '''
        @summary: Check if the actual number is within the accepted range of the expected number
        @param actual : acutal number of recieved packets
        @param expected : expected number of recieved packets
        @return (percentage, bool)
        '''
        percentage = (actual - expected) / float(expected)
        return (percentage, abs(percentage) <= self.balancing_range)

    #---------------------------------------------------------------------
    def check_balancing(self, dest_port_list, port_hit_cnt):
        '''
        @summary: Check if the traffic is balanced across the ECMP groups and the LAG members
        @param dest_port_list : a list of ECMP entries and in each ECMP entry a list of ports
        @param port_hit_cnt : a dict that records the number of packets each port received
        @return bool
        '''

        logging.info("%-10s \t %-10s \t %10s \t %10s \t %10s" % ("type", "port(s)", "exp_cnt", "act_cnt", "diff(%)"))
        result = True

        total_hit_cnt = sum(port_hit_cnt.values())        
        for member in dest_port_list:
            (p, r) = self.check_within_expected_range(port_hit_cnt.get(member, 0), float(total_hit_cnt)/len(dest_port_list))
            logging.info("%-10s \t %-10s \t %10d \t %10d \t %10s"
                          % ("LAG", str(member), total_hit_cnt/len(dest_port_list), port_hit_cnt.get(member, 0), str(round(p, 4)*100) + '%'))
            result &= r

        assert result

    #---------------------------------------------------------------------

    def runTest(self):
        """
        @summary: Send packet for each range of both IPv4 and IPv6 spaces and
        expect the packet to be received from one of the expected ports
        """

        for dst_route in self.dst_route_list:
            for hash_key in self.hash_keys:
                self.check_ip_range(hash_key, self.src_port, dst_route, self.dst_port_list)
